# [[OpenAI Codex | GPT-6 | 2026-09-09 | 20:40 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:07 UTC | BEGIN]]
"""Phase 2 of human-out-of-the-loop operation: record format and audit ledger.

Every case here asserts that something stays closed or stays checkable. Phase 2
executes no decision, so the properties worth testing are that a record cannot
be malformed into something a later phase would misread, that silence can never
be mistaken for consent, that the ledger cannot be edited after the fact, and
that this module's own code path reaches nothing that acts.
"""

from __future__ import annotations

import ast
import json
import sqlite3
import sys
import tempfile
import unittest
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from unittest.mock import Mock

from director_orchestrator import delegation_ledger
from director_orchestrator.delegation import GRANT_SCHEMA_VERSION, Tier1Class
from director_orchestrator.delegation_ledger import (
    AUDIT_SCHEMA_VERSION,
    DelegationAuditError,
    DelegationAuditRecord,
    GateResult,
    GateStatus,
    RecordedOutcome,
    VerdictEntry,
    VerdictOutcome,
    parse_record,
)
from director_orchestrator.store import (
    DelegationAuditConflict,
    DelegationAuditAnchorUnavailable,
    DelegationAuditLedgerCorrupt,
    TaskStore,
)

NOW = datetime(2026, 9, 8, 20, 0, tzinfo=timezone.utc)
SHA = "a" * 40
GRANT = json.dumps({
    "schema_version": GRANT_SCHEMA_VERSION, "grant_id": "grant-001",
    "repository": "example/project", "branch_pattern": "feature/*",
    "classes": [Tier1Class.MARK_PULL_REQUEST_READY.value],
    "providers": ["openai", "cursor"], "expires_at": (NOW + timedelta(days=1)).isoformat(),
    "max_exercises": 10, "revoked": False,
})


def verdict(
    provider: str = "openai",
    outcome: VerdictOutcome = VerdictOutcome.ACCEPT,
    run_id: str | None = "run-1",
) -> VerdictEntry:
    return VerdictEntry(
        provider=provider,
        run_id=run_id,
        outcome=outcome,
        observed_at=NOW - timedelta(minutes=5),
    )


def gate(name: str = "ci_green_at_head", status: GateStatus = GateStatus.PASS) -> GateResult:
    return GateResult(gate=name, status=status, evaluated_at=NOW - timedelta(minutes=9))


def record(**overrides) -> DelegationAuditRecord:
    payload = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "outcome": RecordedOutcome.REFUSED,
        "grant_id": "grant-001",
        "grant_snapshot": GRANT,
        "decision_class": Tier1Class.MARK_PULL_REQUEST_READY,
        "candidate_sha": SHA,
        "acting_agent": "claude-code",
        "verdicts": (verdict(), verdict("cursor", run_id="run-2")),
        "gate_results": (gate(), gate("security_evidence_at_head")),
        "recorded_at": NOW,
    }
    payload.update(overrides)
    if payload["grant_id"] is None and "grant_snapshot" not in overrides:
        payload["grant_snapshot"] = None
    return DelegationAuditRecord(**payload)


class ProviderIdentityRegressionTests(unittest.TestCase):
    def test_wire_payload_aliases_cannot_bypass_uniqueness(self):
        payload = record().to_payload()
        payload["verdicts"][0]["provider"] = "ChatGPT"
        payload["verdicts"][1]["provider"] = "chatgpt-codex-connector"
        with self.assertRaises(DelegationAuditError):
            parse_record(json.dumps(payload))

    def test_openai_chatgpt_codex_aliases_are_one_stored_provider(self):
        aliases = (
            "openai", "OpenAI", "openai_director", "openai-director", "ChatGPT",
            "chatgpt-codex-connector", "Codex", "openai-codex", "openai_codex",
            "chatgpt_codex_connector", "chatgpt-codex", "chatgpt_codex",
        )
        for alias in aliases:
            with self.subTest(alias=alias):
                entry = verdict(alias)
                self.assertEqual(entry.provider, "openai")
                self.assertEqual(VerdictEntry.from_payload(entry.to_payload()).provider, "openai")

    def test_aliases_cannot_supply_two_independent_verdicts(self):
        for left, right in (
            ("openai", "OpenAI"), ("OpenAI", "Codex"),
            ("ChatGPT", "chatgpt-codex-connector"), ("codex", "openai_director"),
            ("Claude", "claude-code"), ("Cursor", "cursor-agent"),
            ("Gemini", "google"), ("Perplexity", "pplx"),
        ):
            with self.subTest(left=left, right=right), self.assertRaises(DelegationAuditError):
                record(verdicts=(verdict(left), verdict(right, run_id="run-2")))

    def test_canonical_independent_providers_still_round_trip(self):
        original = record(verdicts=(verdict("Codex"), verdict("Claude-Code")))
        self.assertEqual([v.provider for v in original.verdicts], ["openai", "claude"])
        self.assertEqual(parse_record(original.canonical_json()), original)


class MemoryAnchor:
    """Test double only: production must supply durable, separately protected storage."""

    def __init__(self):
        self.head = (0, "0" * 64)

    def read_head(self):
        return self.head

    def compare_and_set(self, expected, updated):
        if self.head != expected:
            raise RuntimeError("anchor changed")
        self.head = updated


class ExternalAnchorRegressionTests(unittest.TestCase):
    def test_binary_corrupt_rows_use_the_ledger_corruption_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "audit.sqlite3"), delegation_audit_anchor=MemoryAnchor())
            store.append_delegation_audit_record(record())
            with store._connect() as connection:
                connection.execute("DROP TRIGGER delegated_decision_audit_no_update")
                connection.execute("UPDATE delegated_decision_audit SET record_json = ?", (b"corrupt",))
            with self.assertRaises(DelegationAuditLedgerCorrupt):
                store.delegation_audit_records()

    def test_anchor_errors_are_fail_closed_without_rows(self):
        for anchor in (
            Mock(read_head=Mock(side_effect=OSError("unavailable"))),
            Mock(read_head=Mock(return_value=(True, "0" * 64))),
            Mock(read_head=Mock(return_value=(0, "invalid"))),
            Mock(read_head=Mock(return_value=None)),
            Mock(read_head=Mock(return_value=(0, "0" * 64)),
                 compare_and_set=Mock(side_effect=OSError("failed"))),
            Mock(read_head=Mock(return_value=(0, "0" * 64))),
        ):
            with self.subTest(anchor=anchor), tempfile.TemporaryDirectory() as directory:
                store = TaskStore(str(Path(directory) / "audit.sqlite3"), delegation_audit_anchor=anchor)
                with self.assertRaises(DelegationAuditAnchorUnavailable):
                    store.append_delegation_audit_record(record())
                with store._connect() as connection:
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM delegated_decision_audit").fetchone()[0], 0)

    def test_ambiguous_anchor_advance_is_not_retried_or_rebased(self):
        anchor = MemoryAnchor()
        calls = []

        def advance_then_fail(expected, updated):
            calls.append(updated)
            anchor.head = updated
            raise OSError("acknowledgment lost")

        anchor.compare_and_set = advance_then_fail
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "audit.sqlite3")
            store = TaskStore(path, delegation_audit_anchor=anchor)
            with self.assertRaises(DelegationAuditAnchorUnavailable):
                store.append_delegation_audit_record(record())
            reopened = TaskStore(path, delegation_audit_anchor=anchor)
            with self.assertRaises(DelegationAuditLedgerCorrupt):
                reopened.delegation_audit_records()
            with self.assertRaises(DelegationAuditLedgerCorrupt):
                reopened.append_delegation_audit_record(record())
            self.assertEqual(len(calls), 1)

    def test_two_store_instances_serialize_checkpoint_advancement(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "audit.sqlite3")
            anchor = MemoryAnchor()
            stores = [TaskStore(path, delegation_audit_anchor=anchor) for _ in range(2)]
            originals = [record(recorded_at=NOW + timedelta(seconds=i)) for i in range(6)]
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(stores[i % 2].append_delegation_audit_record, item)
                           for i, item in enumerate(originals)]
                self.assertEqual({future.result() for future in futures}, {item.digest for item in originals})
            self.assertEqual({item.digest for item in stores[0].delegation_audit_records()},
                             {item.digest for item in originals})
            self.assertEqual(anchor.head[0], 6)

    def test_rewriting_both_record_and_digest_cannot_forge_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "audit.sqlite3"), delegation_audit_anchor=MemoryAnchor())
            store.append_delegation_audit_record(record())
            forged = record(acting_agent="forged-agent")
            with store._connect() as connection:
                connection.execute("DROP TRIGGER delegated_decision_audit_no_update")
                connection.execute(
                    "UPDATE delegated_decision_audit SET record_json = ?, record_digest = ?",
                    (forged.canonical_json(), forged.digest),
                )
            with self.assertRaises(DelegationAuditLedgerCorrupt):
                store.delegation_audit_records()
            with self.assertRaises(DelegationAuditLedgerCorrupt):
                store.append_delegation_audit_record(record(recorded_at=NOW + timedelta(seconds=1)))

    def test_deleting_any_or_all_records_is_detected_after_reopening(self):
        for predicate in ("ledger_id = 1", "ledger_id = 2", "1 = 1"):
            with self.subTest(predicate=predicate), tempfile.TemporaryDirectory() as directory:
                path = str(Path(directory) / "audit.sqlite3")
                anchor = MemoryAnchor()
                store = TaskStore(path, delegation_audit_anchor=anchor)
                store.append_delegation_audit_record(record())
                store.append_delegation_audit_record(record(recorded_at=NOW + timedelta(seconds=1)))
                with store._connect() as connection:
                    connection.execute("DROP TRIGGER delegated_decision_audit_no_delete")
                    connection.execute("DELETE FROM delegated_decision_audit WHERE " + predicate)
                reopened = TaskStore(path, delegation_audit_anchor=anchor)
                with self.assertRaises(DelegationAuditLedgerCorrupt):
                    reopened.delegation_audit_records()

    def test_an_unanchored_ledger_cannot_claim_verifiable_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "audit.sqlite3"))
            with self.assertRaises(DelegationAuditAnchorUnavailable):
                store.append_delegation_audit_record(record())
            with self.assertRaises(DelegationAuditAnchorUnavailable):
                store.delegation_audit_records()


class GateHistoryRegressionTests(unittest.TestCase):
    def test_repeated_gate_evaluations_survive_a_refusal_round_trip(self):
        before = gate()
        after = replace(before, status=GateStatus.FAIL, evaluated_at=NOW)
        original = record(gate_results=(before, after))
        self.assertEqual(parse_record(original.canonical_json()).gate_results, (before, after))


class RemainingFindingsRegressionTests(unittest.TestCase):
    def test_named_grants_require_matching_valid_snapshots(self):
        for snapshot in (None, "", "{}", GRANT.replace("grant-001", "grant-002")):
            with self.subTest(snapshot=snapshot), self.assertRaises(DelegationAuditError):
                record(grant_snapshot=snapshot)
        with self.assertRaises(DelegationAuditError):
            record(grant_id=None, grant_snapshot=GRANT)

    def test_changed_grant_contents_change_the_record_digest_and_remain_auditable(self):
        changed = json.loads(GRANT)
        changed["max_exercises"] = 2
        original = record()
        revised = record(grant_snapshot=json.dumps(changed))
        self.assertNotEqual(original.digest, revised.digest)
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "audit.sqlite3"), delegation_audit_anchor=MemoryAnchor())
            store.append_delegation_audit_record(original)
            store.append_delegation_audit_record(revised)
            self.assertEqual([json.loads(r.grant_snapshot)["max_exercises"]
                              for r in store.delegation_audit_records()], [10, 2])

    def test_refusal_class_names_never_become_delegable(self):
        for outcome in (RecordedOutcome.DELEGATED, RecordedOutcome.WOULD_DELEGATE):
            for attempted in ("deployment", "merge_into_protected_branch", "unrecognized_act"):
                with self.subTest(outcome=outcome, attempted=attempted), self.assertRaises(DelegationAuditError):
                    record(outcome=outcome, decision_class=attempted)
        for attempted in ("", "x" * 129, "unbounded class text"):
            with self.subTest(attempted=attempted), self.assertRaises(DelegationAuditError):
                record(decision_class=attempted)

    def test_gate_history_cannot_hide_a_failure_behind_a_later_pass(self):
        history = (gate(status=GateStatus.FAIL), replace(gate(), evaluated_at=NOW))
        for outcome in (RecordedOutcome.DELEGATED, RecordedOutcome.WOULD_DELEGATE):
            with self.subTest(outcome=outcome), self.assertRaises(DelegationAuditError):
                record(outcome=outcome, gate_results=history)
        self.assertEqual(record(gate_results=history).gate_results, history)

    def test_evidence_at_record_time_and_equivalent_offsets_is_valid(self):
        offset_time = NOW.astimezone(timezone(timedelta(hours=-7)))
        original = record(verdicts=(replace(verdict(), observed_at=offset_time),),
                          gate_results=(replace(gate(), evaluated_at=NOW),))
        self.assertEqual(parse_record(original.canonical_json()), original)
        self.assertEqual(original.verdicts[0].observed_at.tzinfo, timezone.utc)

    def test_nested_corrupt_row_uses_the_store_corruption_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "audit.sqlite3"), delegation_audit_anchor=MemoryAnchor())
            store.append_delegation_audit_record(record())
            with store._connect() as connection:
                connection.execute("DROP TRIGGER delegated_decision_audit_no_update")
                connection.execute("UPDATE delegated_decision_audit SET record_json = ?",
                                   ("[" * 2000 + "0" + "]" * 2000,))
            with self.assertRaises(DelegationAuditLedgerCorrupt):
                store.delegation_audit_records()

    def test_pagination_rejects_unknown_cursors_and_allows_later_appends(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "audit.sqlite3"), delegation_audit_anchor=MemoryAnchor())
            first = record()
            store.append_delegation_audit_record(first)
            for cursor in (True, 3, "short", "g" * 64, "f" * 64):
                with self.subTest(cursor=cursor), self.assertRaises(ValueError):
                    store.delegation_audit_records(after_digest=cursor)
            second = record(recorded_at=NOW + timedelta(seconds=1))
            store.append_delegation_audit_record(second)
            self.assertEqual(store.delegation_audit_records(after_digest=first.digest), [second])

    def test_delegated_and_would_delegate_require_a_grant(self):
        for outcome in (RecordedOutcome.DELEGATED, RecordedOutcome.WOULD_DELEGATE):
            with self.subTest(outcome=outcome), self.assertRaises(DelegationAuditError):
                record(outcome=outcome, grant_id=None)

    def test_delegation_claims_require_concurring_verdicts_and_passing_gates(self):
        cases = [
            {"verdicts": ()}, {"verdicts": (verdict(),)}, {"gate_results": ()},
            *({"verdicts": (verdict(), verdict("cursor", outcome))}
              for outcome in VerdictOutcome if outcome != VerdictOutcome.ACCEPT),
            *({"gate_results": (gate(status=status),)}
              for status in GateStatus if status != GateStatus.PASS),
        ]
        for outcome in (RecordedOutcome.DELEGATED, RecordedOutcome.WOULD_DELEGATE):
            for case in cases:
                with self.subTest(outcome=outcome, case=case), self.assertRaises(DelegationAuditError):
                    record(outcome=outcome, **case)

    def test_a_tzinfo_without_an_offset_or_with_invalid_behavior_is_refused(self):
        class NoOffset(tzinfo):
            def utcoffset(self, dt):
                return None

        class BrokenOffset(tzinfo):
            def utcoffset(self, dt):
                raise ValueError("broken timezone")

        for zone in (NoOffset(), BrokenOffset()):
            stamp = NOW.replace(tzinfo=zone)
            for build in (
                lambda: record(recorded_at=stamp),
                lambda: replace(verdict(), observed_at=stamp),
                lambda: replace(gate(), evaluated_at=stamp),
            ):
                with self.subTest(zone=zone, build=build), self.assertRaises(DelegationAuditError):
                    build()

    def test_refusals_preserve_the_exact_tier_zero_or_unknown_class(self):
        for outcome in (RecordedOutcome.REFUSED, RecordedOutcome.WOULD_STOP):
            for attempted in ("merge_into_protected_branch", "deployment", "unrecognized_act"):
                with self.subTest(outcome=outcome, attempted=attempted):
                    payload = record(outcome=outcome).to_payload()
                    payload["decision_class"] = attempted
                    stored = DelegationAuditRecord.from_payload(payload)
                    self.assertEqual(parse_record(stored.canonical_json()).decision_class, attempted)

    def test_excessively_nested_json_is_an_audit_error(self):
        previous_limit = sys.getrecursionlimit()
        try:
            # Other imported libraries can raise the process-wide limit. Fix
            # it for this probe so real decoding exceeds it within 65,536 bytes.
            sys.setrecursionlimit(1000)
            with self.assertRaises(DelegationAuditError):
                parse_record("[" * 4096 + "0" + "]" * 4096)
        finally:
            sys.setrecursionlimit(previous_limit)

    def test_future_evidence_cannot_be_recorded(self):
        future = NOW + timedelta(microseconds=1)
        for case in (
            {"verdicts": (replace(verdict(), observed_at=future),)},
            {"gate_results": (replace(gate(), evaluated_at=future),)},
        ):
            with self.subTest(case=case), self.assertRaises(DelegationAuditError):
                record(**case)

    def test_a_grant_identifier_alone_is_not_a_durable_grant_binding(self):
        payload = record().to_payload()
        payload.pop("grant_snapshot", None)
        with self.assertRaises(DelegationAuditError):
            DelegationAuditRecord.from_payload(payload)

    def test_read_api_can_traverse_past_one_thousand_records(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "audit.sqlite3"), delegation_audit_anchor=MemoryAnchor())
            originals = [record(recorded_at=NOW + timedelta(seconds=i)) for i in range(1001)]
            for item in originals:
                store.append_delegation_audit_record(item)
            first = store.delegation_audit_records(limit=1000)
            second = store.delegation_audit_records(limit=1000, after_digest=first[-1].digest)
            self.assertEqual(first + second, originals)
            self.assertEqual(store.delegation_audit_records(after_digest=second[-1].digest), [])


class RecordFormatTests(unittest.TestCase):
    """The document's "Audit record" field set, and nothing beside it."""

    def test_well_formed_record_round_trips(self):
        original = record()
        rebuilt = DelegationAuditRecord.from_payload(original.to_payload())
        self.assertEqual(original, rebuilt)
        self.assertEqual(original.digest, rebuilt.digest)
        self.assertEqual(rebuilt, parse_record(original.canonical_json()))

    def test_payload_names_exactly_the_documented_fields(self):
        self.assertEqual(
            set(record().to_payload()),
            {
                "schema_version", "outcome", "grant_id", "decision_class",
                "candidate_sha", "acting_agent", "verdicts", "gate_results",
                "recorded_at", "grant_snapshot",
            },
        )

    def test_digest_is_content_derived_and_changes_with_content(self):
        first = record()
        # A different acting agent is a different record, and a reader must be
        # able to tell without trusting whoever assigned the identifier.
        second = record(acting_agent="cursor-agent")
        self.assertEqual(first.digest, record().digest)
        self.assertNotEqual(first.digest, second.digest)
        self.assertEqual(len(first.digest), 64)

    def test_a_refusal_may_name_no_grant_and_no_evidence(self):
        # The case the document warns hardest about: nothing answered, nothing
        # was reachable.  The record has to be able to say so plainly.
        empty = record(grant_id=None, verdicts=(), gate_results=())
        self.assertIsNone(empty.grant_id)
        self.assertEqual(DelegationAuditRecord.from_payload(empty.to_payload()), empty)

    def test_shadow_outcomes_exist_but_nothing_here_produces_them(self):
        # Phase 3 vocabulary, recorded as format only.
        for outcome in (RecordedOutcome.WOULD_DELEGATE, RecordedOutcome.WOULD_STOP):
            self.assertEqual(record(outcome=outcome).outcome, outcome)


class MalformedRecordTests(unittest.TestCase):
    """Hostile or malformed input is refused, never repaired."""

    def test_missing_field_is_refused(self):
        payload = record().to_payload()
        del payload["acting_agent"]
        with self.assertRaises(DelegationAuditError):
            DelegationAuditRecord.from_payload(payload)

    def test_extra_field_is_refused(self):
        payload = record().to_payload()
        payload["owner_approved"] = True
        with self.assertRaises(DelegationAuditError):
            DelegationAuditRecord.from_payload(payload)

    def test_wrong_types_are_refused_rather_than_coerced(self):
        cases = {
            "outcome": 1,
            "grant_id": 7,
            "decision_class": ["merge"],
            "candidate_sha": None,
            "acting_agent": {"name": "claude"},
            "verdicts": {"openai": "ACCEPT"},
            "gate_results": "all green",
            "recorded_at": 1757366400,
        }
        for field, value in cases.items():
            with self.subTest(field=field):
                payload = record().to_payload()
                payload[field] = value
                with self.assertRaises(DelegationAuditError):
                    DelegationAuditRecord.from_payload(payload)

    def test_naive_datetimes_are_refused_not_assumed_utc(self):
        naive = NOW.replace(tzinfo=None)
        with self.assertRaises(DelegationAuditError):
            record(recorded_at=naive)
        payload = record().to_payload()
        payload["recorded_at"] = naive.isoformat()
        with self.assertRaises(DelegationAuditError):
            DelegationAuditRecord.from_payload(payload)
        payload = record().to_payload()
        payload["verdicts"][0]["observed_at"] = naive.isoformat()
        with self.assertRaises(DelegationAuditError):
            DelegationAuditRecord.from_payload(payload)
        payload = record().to_payload()
        payload["gate_results"][0]["evaluated_at"] = naive.isoformat()
        with self.assertRaises(DelegationAuditError):
            DelegationAuditRecord.from_payload(payload)

    def test_unknown_decision_class_is_refused_not_mapped(self):
        payload = record(outcome=RecordedOutcome.DELEGATED).to_payload()
        payload["decision_class"] = "deploy_to_production"
        with self.assertRaises(DelegationAuditError):
            DelegationAuditRecord.from_payload(payload)

    def test_unknown_verdict_is_refused_not_mapped(self):
        payload = record().to_payload()
        payload["verdicts"][0]["outcome"] = "LGTM"
        with self.assertRaises(DelegationAuditError):
            DelegationAuditRecord.from_payload(payload)

    def test_unknown_gate_status_and_recorded_outcome_are_refused(self):
        payload = record().to_payload()
        payload["gate_results"][0]["status"] = "PROBABLY_FINE"
        with self.assertRaises(DelegationAuditError):
            DelegationAuditRecord.from_payload(payload)
        payload = record().to_payload()
        payload["outcome"] = "APPROVED_BY_OWNER"
        with self.assertRaises(DelegationAuditError):
            DelegationAuditRecord.from_payload(payload)

    def test_unsupported_schema_version_is_refused(self):
        with self.assertRaises(DelegationAuditError):
            record(schema_version="director-delegation-audit/v2")
        payload = record().to_payload()
        payload["schema_version"] = "director-delegation-audit/v2"
        with self.assertRaises(DelegationAuditError):
            DelegationAuditRecord.from_payload(payload)

    def test_candidate_sha_must_be_a_full_commit(self):
        for value in ("", "a" * 39, "a" * 41, "A" * 40, "z" * 40, SHA + "\n"):
            with self.subTest(value=value), self.assertRaises(DelegationAuditError):
                record(candidate_sha=value)

    def test_identifiers_are_bounded(self):
        with self.assertRaises(DelegationAuditError):
            record(acting_agent="x" * 129)
        with self.assertRaises(DelegationAuditError):
            record(acting_agent="claude code")
        with self.assertRaises(DelegationAuditError):
            record(grant_id="grant 001")

    def test_a_verdict_must_carry_its_run_identifier(self):
        # "each verdict with its provider and run identifier".  An ACCEPT no
        # reader can trace back to a run is not independently checkable.
        for outcome in (
            VerdictOutcome.ACCEPT,
            VerdictOutcome.CHANGES_REQUIRED,
            VerdictOutcome.SERIOUS_SAFETY_STOP,
        ):
            with self.subTest(outcome=outcome), self.assertRaises(DelegationAuditError):
                verdict(outcome=outcome, run_id=None)

    def test_a_non_verdict_needs_no_run_identifier(self):
        # A provider that never ran has no run to name.
        for outcome in (
            VerdictOutcome.SILENT,
            VerdictOutcome.ABSENT,
            VerdictOutcome.QUOTA_EXHAUSTED,
            VerdictOutcome.UNAUTHENTICATED,
            VerdictOutcome.AMBIGUOUS,
        ):
            with self.subTest(outcome=outcome):
                self.assertIsNone(verdict(outcome=outcome, run_id=None).run_id)

    def test_a_provider_may_appear_only_once(self):
        # Two runs of one provider are not two verdicts.
        with self.assertRaises(DelegationAuditError):
            record(verdicts=(verdict(run_id="run-1"), verdict(run_id="run-2")))

    def test_collections_are_bounded(self):
        with self.assertRaises(DelegationAuditError):
            record(verdicts=tuple(verdict(f"p{i}") for i in range(17)))
        with self.assertRaises(DelegationAuditError):
            record(gate_results=tuple(gate(f"g{i}") for i in range(65)))
        with self.assertRaises(DelegationAuditError):
            record(verdicts=[verdict()])

    def test_oversize_or_unparsable_documents_are_refused(self):
        with self.assertRaises(DelegationAuditError):
            parse_record("x" * 65_537)
        with self.assertRaises(DelegationAuditError):
            parse_record("{not json")
        with self.assertRaises(DelegationAuditError):
            parse_record(json.dumps([record().to_payload()]))


class AbsenceIsNeverConsentTests(unittest.TestCase):
    """Declined, silent, out of quota and approved stay four distinct things."""

    def test_every_non_accept_outcome_is_distinguishable(self):
        outcomes = [
            VerdictOutcome.ACCEPT,
            VerdictOutcome.CHANGES_REQUIRED,
            VerdictOutcome.SERIOUS_SAFETY_STOP,
            VerdictOutcome.AMBIGUOUS,
            VerdictOutcome.UNAUTHENTICATED,
            VerdictOutcome.QUOTA_EXHAUSTED,
            VerdictOutcome.SILENT,
            VerdictOutcome.ABSENT,
        ]
        self.assertEqual(len(set(outcomes)), len(outcomes))
        self.assertEqual(set(VerdictOutcome), set(outcomes))
        values = [item.value for item in outcomes]
        self.assertEqual(len(set(values)), len(values))

    def test_only_accept_reads_as_approval(self):
        for outcome in VerdictOutcome:
            with self.subTest(outcome=outcome):
                self.assertEqual(
                    outcome.is_approval, outcome is VerdictOutcome.ACCEPT
                )

    def test_silence_and_quota_are_not_even_verdicts(self):
        for outcome in (
            VerdictOutcome.SILENT,
            VerdictOutcome.ABSENT,
            VerdictOutcome.QUOTA_EXHAUSTED,
            VerdictOutcome.UNAUTHENTICATED,
            VerdictOutcome.AMBIGUOUS,
        ):
            with self.subTest(outcome=outcome):
                self.assertFalse(outcome.is_verdict)
                self.assertFalse(outcome.is_approval)

    def test_a_record_of_four_providers_keeps_them_apart_through_storage(self):
        stored = record(
            verdicts=(
                verdict("openai", VerdictOutcome.ACCEPT, "run-a"),
                verdict("cursor", VerdictOutcome.CHANGES_REQUIRED, "run-b"),
                verdict("grok", VerdictOutcome.SILENT, None),
                verdict("copilot", VerdictOutcome.QUOTA_EXHAUSTED, None),
            )
        )
        rebuilt = parse_record(stored.canonical_json())
        self.assertEqual(
            {item.provider: item.outcome for item in rebuilt.verdicts},
            {
                "openai": VerdictOutcome.ACCEPT,
                "cursor": VerdictOutcome.CHANGES_REQUIRED,
                "grok": VerdictOutcome.SILENT,
                "copilot": VerdictOutcome.QUOTA_EXHAUSTED,
            },
        )
        self.assertEqual(
            [item.provider for item in rebuilt.verdicts if item.outcome.is_approval],
            ["openai"],
        )

    def test_only_pass_reads_as_a_passing_gate(self):
        for status in GateStatus:
            with self.subTest(status=status):
                self.assertEqual(status.is_pass, status is GateStatus.PASS)
        # Every condition the evidence bar names is representable.
        self.assertLessEqual(
            {
                GateStatus.MISSING, GateStatus.STALE, GateStatus.AMBIGUOUS,
                GateStatus.UNAVAILABLE, GateStatus.UNREADABLE,
            },
            set(GateStatus),
        )


class AppendOnlyLedgerTests(unittest.TestCase):
    """The ledger is evidence, so it has no update path and no delete path."""

    def _store(self, directory: str) -> TaskStore:
        return TaskStore(str(Path(directory) / "director.sqlite3"), delegation_audit_anchor=MemoryAnchor())

    def test_append_and_read_round_trip_in_order(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            first = record()
            second = record(recorded_at=NOW + timedelta(minutes=1))
            self.assertEqual(store.append_delegation_audit_record(first), first.digest)
            store.append_delegation_audit_record(second)
            self.assertEqual(
                store.delegation_audit_records(), [first, second]
            )

    def test_the_store_exposes_no_update_or_delete_for_the_ledger(self):
        surface = [
            name
            for name in dir(TaskStore)
            if "delegation_audit" in name or "delegated_decision" in name
        ]
        self.assertEqual(
            sorted(surface),
            ["append_delegation_audit_record", "delegation_audit_records"],
        )

    def test_an_identical_record_cannot_be_appended_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            store.append_delegation_audit_record(record())
            with self.assertRaises(DelegationAuditConflict):
                store.append_delegation_audit_record(record())
            self.assertEqual(len(store.delegation_audit_records()), 1)

    def test_a_correction_is_a_new_record_not_a_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            original = record(
                gate_results=(gate(status=GateStatus.UNREADABLE),)
            )
            store.append_delegation_audit_record(original)
            corrected = replace(
                original,
                gate_results=(gate(status=GateStatus.FAIL),),
                recorded_at=NOW + timedelta(minutes=2),
            )
            store.append_delegation_audit_record(corrected)
            ledger = store.delegation_audit_records()
            self.assertEqual(ledger, [original, corrected])
            # The superseded record is still there.  That is the point.
            self.assertEqual(
                ledger[0].gate_results[0].status, GateStatus.UNREADABLE
            )

    def test_update_and_delete_are_refused_by_the_storage_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            store.append_delegation_audit_record(record())
            connection = sqlite3.connect(store.path)
            try:
                for statement in (
                    "UPDATE delegated_decision_audit SET record_json = '{}'",
                    "DELETE FROM delegated_decision_audit",
                    "UPDATE delegated_decision_audit SET recorded_at = 'x'",
                ):
                    with self.subTest(statement=statement):
                        with self.assertRaises(sqlite3.IntegrityError):
                            with connection:
                                connection.execute(statement)
            finally:
                connection.close()
            self.assertEqual(len(store.delegation_audit_records()), 1)

    def test_insert_or_replace_cannot_overwrite_a_record(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            stored = record()
            store.append_delegation_audit_record(stored)
            connection = sqlite3.connect(store.path)
            try:
                with self.assertRaises(sqlite3.IntegrityError):
                    with connection:
                        connection.execute(
                            """INSERT OR REPLACE INTO delegated_decision_audit(
                                record_digest, record_json, recorded_at
                            ) VALUES (?, ?, ?)""",
                            (stored.digest, "{}", NOW.isoformat()),
                        )
            finally:
                connection.close()
            self.assertEqual(store.delegation_audit_records(), [stored])

    def test_a_tampered_row_is_reported_not_returned(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            stored = record()
            store.append_delegation_audit_record(stored)
            forged = replace(stored, outcome=RecordedOutcome.DELEGATED)
            # Reach past the triggers the way a compromised operator would, by
            # rewriting the file rather than issuing an UPDATE.
            connection = sqlite3.connect(store.path)
            try:
                with connection:
                    connection.execute(
                        "DROP TRIGGER delegated_decision_audit_no_update"
                    )
                    connection.execute(
                        "UPDATE delegated_decision_audit SET record_json = ?",
                        (forged.canonical_json(),),
                    )
            finally:
                connection.close()
            with self.assertRaises(DelegationAuditLedgerCorrupt):
                store.delegation_audit_records()

    def test_unvalidated_input_is_not_accepted_for_append(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            with self.assertRaises(TypeError):
                store.append_delegation_audit_record(record().to_payload())
            for limit in (0, -1, 1_001, True, "10"):
                with self.subTest(limit=limit), self.assertRaises((ValueError, TypeError)):
                    store.delegation_audit_records(limit=limit)


class ExecutesNoDecisionTests(unittest.TestCase):
    """Phase 2 records decisions. It does not make them.

    A module that could reach a merge, a Slack post, or the evidence bar would
    have started phase 3 or 4 early, and the document's whole safety argument
    rests on those phases being separately built, reviewed and accepted.
    """

    MODULE = Path(delegation_ledger.__file__)

    def _imports(self) -> set[str]:
        tree = ast.parse(self.MODULE.read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add(node.module or "")
        return names

    def test_it_imports_nothing_that_can_act(self):
        forbidden = {
            "oracle_merge", "merge_eligibility", "providers", "slack_bot",
            "engine", "packet_transports", "primary_effect_gateways",
            "approvals", "policy", "store", "subprocess",
            "httpx", "requests", "urllib", "urllib.request",
        }
        self.assertEqual(self._imports() & forbidden, set())

    def test_its_whole_import_set_is_a_record_format_and_a_digest(self):
        # Anything that could reach a gate, a provider, or an external effect
        # has to be imported first, so pinning the set is the real guard.
        self.assertEqual(
            self._imports(),
            {
                "__future__", "json", "re", "dataclasses", "datetime", "enum",
                "typing", "delegation", "interactions",
            },
        )

    def test_no_public_name_resolves_or_executes_anything(self):
        module = delegation_ledger
        acting = ("merge", "post", "approve", "execute", "decide", "resolve", "dispatch")
        offenders = [
            name
            for name in dir(module)
            if not name.startswith("_") and any(verb in name.lower() for verb in acting)
        ]
        self.assertEqual(offenders, [])

    def test_no_public_function_grants_eligibility(self):
        # Structural record invariants do not evaluate live evidence or grant
        # eligibility. The public surface still exposes only record operations.
        module = delegation_ledger
        defined_here = sorted(
            name
            for name, value in vars(module).items()
            if not name.startswith("_")
            and getattr(value, "__module__", None) == module.__name__
        )
        self.assertEqual(
            defined_here,
            [
                "DelegationAuditError",
                "DelegationAuditRecord",
                "GateResult",
                "GateStatus",
                "RecordedOutcome",
                "VerdictEntry",
                "VerdictOutcome",
                "parse_record",
            ],
        )


if __name__ == "__main__":
    unittest.main()
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 20:07 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-09 | 20:40 UTC | END]]
