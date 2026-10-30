"""Evidence-based contact observations (WO-20261029-005).

Every test uses local fixture receipts only: no provider call, no network,
no subprocess, no credential value. Fakes and tmp directories only.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from director_orchestrator.contact_families import provider_family_for
from director_orchestrator.contact_observations import (
    CONTACT_OBSERVATION_FRESHNESS,
    CONTACT_OBSERVATION_MAX_CLOCK_SKEW,
    contact_observation,
    contact_observations,
    contact_observations_report,
)
from director_orchestrator.models import (
    ContactAuthState,
    ContactObservationStaleness,
    ContactPermissionState,
    ContactQuotaState,
    ContactTaskResultState,
)

NOW = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)
SIX_CONTACTS = (
    "zai",
    "github-copilot",
    "firecrawl",
    "antigravity-subscription-cli",
    "grok-bot",
    "zcode",
)


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y%m%dT%H%M%SZ")


def _json_receipt(provider: str, *, finished: datetime | None = None, **extra) -> dict:
    payload: dict = {
        "provider": provider,
        "status": "COMPLETED",
        "recovery": False,
        "purpose": "fixture receipt",
        "exit_code": 0,
        "native_subtype": "success",
        "is_error": False,
    }
    if finished is not None:
        payload["finished_at"] = _stamp(finished)
    payload.update(extra)
    return payload


def _write_json(root: Path, directory: str, name: str, payload: dict) -> Path:
    lane = root / directory
    lane.mkdir(parents=True, exist_ok=True)
    path = lane / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_text(root: Path, directory: str, name: str, content: str) -> Path:
    lane = root / directory
    lane.mkdir(parents=True, exist_ok=True)
    path = lane / name
    path.write_text(content, encoding="utf-8")
    return path


# Fixtures below copy the SHAPES (names and field names, never content) of the
# installed provider-dispatch dispatcher's real receipt files.
WORKTREE = "director-fixture-worktree"


def _dispatch_stamp(moment: datetime) -> str:
    """dispatch.py utc_stamp(): YYYYMMDDTHHMMSSZ-<microseconds>-<16 hex>."""

    return f"{_stamp(moment)}-{moment:%f}-0123456789abcdef"


def _write_zai_run(root: Path, moment: datetime, **extra) -> Path:
    """receipts/zai/<stamp>.json written by provider_receipt for `zai run`."""

    stamp = _dispatch_stamp(moment)
    payload = {
        "provider": "zai", "action": "run", "role": "review", "mode": "default",
        "requested_model": "glm-fixture", "models": ["glm-fixture"], "started_at": stamp,
        "production_credit": True, "status": "COMPLETED", "timeout_seconds": 60,
        "finished_at": stamp, "exit_code": 0, "native_subtype": "success", "is_error": False,
        "terminal_reason": None, "api_error_status": None, "worktree": "fixture",
    }
    payload.update(extra)
    return _write_json(root, "zai", f"{stamp}.json", payload)


def _write_firecrawl(root: Path, moment: datetime, action: str = "search", exit_code: int = 0) -> tuple[Path, Path]:
    """receipts/firecrawl/<stamp>.json plus <stamp>.<action>.txt (combined stdout+stderr)."""

    stamp = _dispatch_stamp(moment)
    receipt = _write_json(
        root, "firecrawl", f"{stamp}.json",
        {"provider": "firecrawl", "action": action, "exit_code": exit_code, "worktree": "fixture"},
    )
    output = _write_text(root, "firecrawl", f"{stamp}.{action}.txt", "RAW-CONTENT-NEVER-COPIED\n")
    return receipt, output


def _write_agy(root: Path, moment: datetime, **extra) -> Path:
    """receipts/<worktree>/agy-<stamp>.receipt.json: no provider field."""

    stamp = _dispatch_stamp(moment)
    payload = {
        "exit_code": 0, "shell_profile": "none", "allowed_commands": [],
        "conversation_id": "fixture-conversation", "denied_commands": [],
        "conversation_db": None, "denial_diagnostic": None,
    }
    payload.update(extra)
    _write_text(root, WORKTREE, f"agy-{stamp}.packet.txt", "fixture packet")
    _write_text(root, WORKTREE, f"agy-{stamp}.out.json", json.dumps({"status": "fixture"}))
    return _write_json(root, WORKTREE, f"agy-{stamp}.receipt.json", payload)


def _write_copilot(root: Path, moment: datetime, text: str = "fixture answer") -> Path:
    """receipts/<worktree>/copilot-<stamp>.out.txt plus .log and .packet.txt."""

    stamp = _dispatch_stamp(moment)
    _write_text(root, WORKTREE, f"copilot-{stamp}.packet.txt", "fixture packet")
    _write_text(root, WORKTREE, f"copilot-{stamp}.log", "")
    return _write_text(root, WORKTREE, f"copilot-{stamp}.out.txt", text)


class ContactObservationFieldTests(unittest.TestCase):
    """Acceptance (A): one record per contact with every field separate."""

    def test_every_contact_has_a_record_with_separate_state_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            records = contact_observations(receipts_root=Path(directory), now=NOW)
            self.assertEqual(tuple(records), SIX_CONTACTS)
            for contact_id in SIX_CONTACTS:
                with self.subTest(contact_id=contact_id):
                    record = records[contact_id]
                    model = record.model_dump(mode="json")
                    for field in (
                        "contact_id",
                        "provider_family",
                        "source",
                        "observed_at_utc",
                        "transport",
                        "account_scope",
                        "auth_state",
                        "quota_state",
                        "permissions_state",
                        "task_result_state",
                        "staleness",
                        "ready",
                        "note",
                    ):
                        self.assertIn(field, model)
                    # Separate fields, not one merged verdict.
                    self.assertIsInstance(model["auth_state"], str)
                    self.assertIsInstance(model["quota_state"], str)
                    self.assertIsInstance(model["permissions_state"], str)
                    self.assertIsInstance(model["task_result_state"], str)

    def test_provider_family_labels_match_contact_families(self):
        # Acceptance (D): labels come from the contact_families table itself,
        # ZCode included (zcode lane, Foreman ruling 2026-10-29 10:25 PDT).
        expected = {
            "zai": provider_family_for("zai"),
            "github-copilot": provider_family_for("github-copilot"),
            "firecrawl": provider_family_for("firecrawl"),
            "antigravity-subscription-cli": provider_family_for("antigravity"),
            "grok-bot": provider_family_for("grok-bot"),
            "zcode": provider_family_for("zcode"),
        }
        self.assertEqual(expected["zcode"], "Z.ai")
        with tempfile.TemporaryDirectory() as directory:
            records = contact_observations(receipts_root=Path(directory), now=NOW)
            for contact_id, family in expected.items():
                self.assertEqual(records[contact_id].provider_family, family)

    def test_unknown_contact_id_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(KeyError):
            contact_observation("not-a-contact", receipts_root=Path(directory), now=NOW)


class PositiveEvidenceTests(unittest.TestCase):
    """A fresh dated receipt yields observed fields — and only what it states."""

    def test_fresh_successful_zai_receipt_yields_observed_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            finished = NOW - timedelta(hours=1)
            path = _write_json(
                root, "zai", "fixture01.json",
                _json_receipt("zai", finished=finished, started_at=_stamp(finished - timedelta(minutes=2))),
            )
            record = contact_observation("zai", receipts_root=root, now=NOW)
            self.assertEqual(record.source, str(path))
            self.assertEqual(record.observed_at_utc, finished)
            self.assertEqual(record.transport, "provider-dispatch receipt (zai)")
            self.assertEqual(record.staleness, ContactObservationStaleness.FRESH)
            self.assertEqual(record.auth_state, ContactAuthState.OBSERVED_AUTHENTICATED)
            self.assertEqual(record.task_result_state, ContactTaskResultState.OBSERVED_SUCCESS)
            # Quota is never inferred from a successful task.
            self.assertEqual(record.quota_state, ContactQuotaState.NOT_OBSERVED)
            self.assertTrue(record.ready)

    def test_fresh_firecrawl_structured_receipt_yields_observed_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            moment = (NOW - timedelta(hours=2)).replace(microsecond=0)
            receipt, _ = _write_firecrawl(root, moment)
            record = contact_observation("firecrawl", receipts_root=root, now=NOW)
            # The structured receipt wins the same-stamp tie over its text output.
            self.assertEqual(record.source, str(receipt))
            self.assertEqual(record.observed_at_utc, moment)
            self.assertEqual(record.task_result_state, ContactTaskResultState.OBSERVED_SUCCESS)
            self.assertTrue(record.ready)
            # Raw receipt content is never copied into the record.
            self.assertNotIn("RAW-CONTENT-NEVER-COPIED", json.dumps(record.model_dump(mode="json")))

    def test_firecrawl_failed_exit_code_is_failure_even_with_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_firecrawl(root, NOW - timedelta(hours=2), action="scrape", exit_code=1)
            record = contact_observation("firecrawl", receipts_root=root, now=NOW)
            self.assertEqual(record.task_result_state, ContactTaskResultState.OBSERVED_FAILURE)
            self.assertFalse(record.ready)

    def test_real_zai_run_receipt_with_microsecond_stamp_is_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            moment = (NOW - timedelta(hours=1)).replace(microsecond=0)
            path = _write_zai_run(root, moment)
            record = contact_observation("zai", receipts_root=root, now=NOW)
            self.assertEqual(record.source, str(path))
            self.assertEqual(record.observed_at_utc, moment)
            self.assertTrue(record.ready)

    def test_real_agy_failure_receipt_without_provider_field_is_observed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = _write_agy(root, NOW - timedelta(hours=1), exit_code=1)
            record = contact_observation("antigravity-subscription-cli", receipts_root=root, now=NOW)
            self.assertEqual(record.source, str(path))
            self.assertEqual(record.staleness, ContactObservationStaleness.FRESH)
            self.assertEqual(record.task_result_state, ContactTaskResultState.OBSERVED_FAILURE)
            self.assertFalse(record.ready)

    def test_quota_limited_receipt_states_quota_and_no_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_json(
                root, "zai", "fixture02.json",
                _json_receipt(
                    "zai", finished=NOW - timedelta(hours=1), status="ERROR",
                    exit_code=1, is_error=True, error="quota exceeded after retry (429)",
                ),
            )
            record = contact_observation("zai", receipts_root=root, now=NOW)
            self.assertEqual(record.task_result_state, ContactTaskResultState.OBSERVED_FAILURE)
            self.assertEqual(record.quota_state, ContactQuotaState.OBSERVED_LIMITED)
            self.assertEqual(record.auth_state, ContactAuthState.NOT_OBSERVED)
            self.assertFalse(record.ready)

    def test_authentication_failure_receipt_states_auth_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_json(
                root, "zai", "fixture03.json",
                _json_receipt(
                    "zai", finished=NOW - timedelta(hours=1), status="FAILED_OR_AMBIGUOUS",
                    exit_code=1, is_error=True, api_error_status="HTTP 401 unauthorized",
                ),
            )
            record = contact_observation("zai", receipts_root=root, now=NOW)
            self.assertEqual(record.auth_state, ContactAuthState.OBSERVED_AUTHENTICATION_FAILED)
            self.assertEqual(record.task_result_state, ContactTaskResultState.OBSERVED_FAILURE)
            self.assertFalse(record.ready)

    def test_newest_receipt_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            older = _write_json(
                root, "zai", "older.json",
                _json_receipt("zai", finished=NOW - timedelta(days=2), exit_code=1, is_error=True, status="ERROR"),
            )
            newer = _write_json(
                root, "zai", "newer.json",
                _json_receipt("zai", finished=NOW - timedelta(hours=3)),
            )
            record = contact_observation("zai", receipts_root=root, now=NOW)
            self.assertEqual(record.source, str(newer))
            self.assertNotEqual(record.source, str(older))
            self.assertTrue(record.ready)


class NegativeEvidenceTests(unittest.TestCase):
    """Acceptance (B): existence of an executable, route, catalog or key name
    never yields ready, for every contact."""

    def test_executable_route_and_catalog_present_never_yield_ready(self):
        for contact_id, executable in (
            ("zai", "zai-cli.exe"),
            ("github-copilot", "copilot-cli.exe"),
            ("firecrawl", "firecrawl-mcp.exe"),
            ("antigravity-subscription-cli", "agy.exe"),
            ("grok-bot", "grokbot.exe"),
            ("zcode", "zcode.exe"),
        ):
            with self.subTest(contact_id=contact_id), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                lane = root / {"antigravity-subscription-cli": "agy"}.get(contact_id, contact_id)
                lane.mkdir(parents=True, exist_ok=True)
                (lane / executable).write_bytes(b"MZ fixture executable")
                (lane / "routes.catalog.json").write_text(
                    json.dumps({"provider": contact_id, "route": "configured", "catalog": True}),
                    encoding="utf-8",
                )
                (lane / "key-name.env.placeholder").write_text("ZAI_API_KEY=<placeholder>\n", encoding="utf-8")
                record = contact_observation(contact_id, receipts_root=root, now=NOW)
                self.assertFalse(record.ready)
                self.assertIsNone(record.source)
                self.assertEqual(record.staleness, ContactObservationStaleness.NOT_OBSERVED)
                self.assertIsNone(record.observed_at_utc)
                self.assertEqual(record.task_result_state, ContactTaskResultState.NOT_OBSERVED)
                self.assertEqual(record.auth_state, ContactAuthState.NOT_OBSERVED)

    def test_dated_receipt_without_task_result_never_yields_ready(self):
        # A dated receipt that states no task outcome certifies no task result.
        moment = NOW - timedelta(hours=1)
        for contact_id, directory, name, payload in (
            ("zai", "zai", "catalog.json", {"provider": "zai", "finished_at": _stamp(moment), "route": "present"}),
            (
                "antigravity-subscription-cli", WORKTREE, f"agy-{_dispatch_stamp(moment)}.receipt.json",
                {"conversation_id": None, "denied_commands": []},
            ),
        ):
            with self.subTest(contact_id=contact_id), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _write_json(root, directory, name, payload)
                record = contact_observation(contact_id, receipts_root=root, now=NOW)
                self.assertFalse(record.ready)
                self.assertEqual(record.task_result_state, ContactTaskResultState.NOT_OBSERVED)
                self.assertEqual(record.staleness, ContactObservationStaleness.FRESH)

    def test_contacts_without_a_receipt_route_ignore_invented_lane_directories(self):
        # Grok Bot and ZCode have no receipt-writing route, and Copilot writes
        # no JSON receipt: an invented receipts/<contact>/ JSON is never read.
        for contact_id in ("github-copilot", "grok-bot", "zcode"):
            with self.subTest(contact_id=contact_id), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _write_json(
                    root, contact_id, f"{_stamp(NOW - timedelta(hours=1))}.json",
                    _json_receipt(contact_id, finished=NOW - timedelta(hours=1)),
                )
                record = contact_observation(contact_id, receipts_root=root, now=NOW)
                self.assertFalse(record.ready)
                self.assertIsNone(record.source)
                self.assertEqual(record.staleness, ContactObservationStaleness.NOT_OBSERVED)

    def test_undated_successful_receipt_is_ignored(self):
        # Readiness requires a DATED observed task result.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_json(root, "zai", "undated.json", _json_receipt("zai"))
            record = contact_observation("zai", receipts_root=root, now=NOW)
            self.assertFalse(record.ready)
            self.assertIsNone(record.source)
            self.assertEqual(record.staleness, ContactObservationStaleness.NOT_OBSERVED)

    def test_text_only_output_is_unconfirmed_never_ready(self):
        # Codex review finding 3: the dispatcher writes combined stdout and
        # stderr to text files regardless of exit status, so nonempty text
        # (here an error message) certifies no outcome.
        moment = NOW - timedelta(hours=1)
        stamp = _dispatch_stamp(moment)
        for contact_id, directory, name in (
            ("firecrawl", "firecrawl", f"{stamp}.search.txt"),
            ("firecrawl", "firecrawl", f"{stamp}.scrape.txt"),
            ("github-copilot", WORKTREE, f"copilot-{stamp}.out.txt"),
        ):
            for content in ("Error: 401 Unauthorized\n", "fixture answer\n", ""):
                with self.subTest(name=name, content=content), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    path = _write_text(root, directory, name, content)
                    record = contact_observation(contact_id, receipts_root=root, now=NOW)
                    self.assertEqual(record.source, str(path))
                    self.assertEqual(record.staleness, ContactObservationStaleness.FRESH)
                    self.assertEqual(record.task_result_state, ContactTaskResultState.OBSERVED_UNCONFIRMED)
                    self.assertEqual(record.auth_state, ContactAuthState.NOT_OBSERVED)
                    self.assertFalse(record.ready)

    def test_real_copilot_output_is_discovered_but_unconfirmed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = _write_copilot(root, NOW - timedelta(hours=1))
            record = contact_observation("github-copilot", receipts_root=root, now=NOW)
            self.assertEqual(record.source, str(path))
            self.assertEqual(record.task_result_state, ContactTaskResultState.OBSERVED_UNCONFIRMED)
            self.assertFalse(record.ready)

    def test_agy_zero_exit_receipt_is_unconfirmed_never_ready(self):
        # The agy receipt omits the producer's other success conditions
        # (non-empty response, no denied actions), so exit 0 is unconfirmed.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = _write_agy(root, NOW - timedelta(hours=1))
            record = contact_observation("antigravity-subscription-cli", receipts_root=root, now=NOW)
            self.assertEqual(record.source, str(path))
            self.assertEqual(record.task_result_state, ContactTaskResultState.OBSERVED_UNCONFIRMED)
            self.assertFalse(record.ready)

    def test_agy_denials_are_failures(self):
        for extra in ({"denied_commands": ["git push"]}, {"denial_diagnostic": "denial unresolved"}):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                _write_agy(root, NOW - timedelta(hours=1), **extra)
                record = contact_observation("antigravity-subscription-cli", receipts_root=root, now=NOW)
                self.assertEqual(record.task_result_state, ContactTaskResultState.OBSERVED_FAILURE)
                self.assertFalse(record.ready)

    def test_explicit_non_success_status_outranks_zero_exit_code(self):
        # Codex review finding 2: FAILED_OR_AMBIGUOUS (and every other
        # non-success status) wins over exit_code 0 with is_error absent.
        for status in ("FAILED_OR_AMBIGUOUS", "error", "failed", "ATTEMPT_RESERVED", "timed out"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                _write_zai_run(root, NOW - timedelta(hours=1), status=status, is_error=None)
                record = contact_observation("zai", receipts_root=root, now=NOW)
                self.assertEqual(record.task_result_state, ContactTaskResultState.OBSERVED_FAILURE)
                self.assertNotEqual(record.auth_state, ContactAuthState.OBSERVED_AUTHENTICATED)
                self.assertFalse(record.ready)


class MissingAndStaleTests(unittest.TestCase):
    """Acceptance (C): missing evidence is not-observed; expired receipts are stale."""

    def test_missing_receipts_root_yields_all_not_observed(self):
        with tempfile.TemporaryDirectory() as directory:
            absent = Path(directory) / "does-not-exist"
            records = contact_observations(receipts_root=absent, now=NOW)
            for contact_id in SIX_CONTACTS:
                with self.subTest(contact_id=contact_id):
                    record = records[contact_id]
                    self.assertFalse(record.ready)
                    self.assertIsNone(record.source)
                    self.assertIsNone(record.observed_at_utc)
                    self.assertIsNone(record.transport)
                    self.assertIsNone(record.account_scope)
                    self.assertEqual(record.staleness, ContactObservationStaleness.NOT_OBSERVED)
                    self.assertEqual(record.auth_state, ContactAuthState.NOT_OBSERVED)
                    self.assertEqual(record.quota_state, ContactQuotaState.NOT_OBSERVED)
                    self.assertEqual(record.task_result_state, ContactTaskResultState.NOT_OBSERVED)
            self.assertEqual(
                records["github-copilot"].permissions_state,
                ContactPermissionState.RECORDED_ISSUES_ONLY,
            )
            report = contact_observations_report(receipts_root=absent, now=NOW)
            self.assertFalse(report.receipts_root_present)
            self.assertEqual(report.freshness_window_days, CONTACT_OBSERVATION_FRESHNESS.days)

    def test_expired_receipt_is_stale_and_not_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expired = NOW - CONTACT_OBSERVATION_FRESHNESS - timedelta(seconds=1)
            _write_json(root, "zai", "old.json", _json_receipt("zai", finished=expired))
            record = contact_observation("zai", receipts_root=root, now=NOW)
            self.assertEqual(record.staleness, ContactObservationStaleness.STALE)
            self.assertEqual(record.task_result_state, ContactTaskResultState.STALE)
            self.assertEqual(record.auth_state, ContactAuthState.STALE)
            self.assertFalse(record.ready)
            # The expired evidence is still named, never silently dropped.
            self.assertIsNotNone(record.source)

    def test_receipt_within_clock_skew_allowance_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ahead = NOW + CONTACT_OBSERVATION_MAX_CLOCK_SKEW
            _write_json(root, "zai", "skew.json", _json_receipt("zai", finished=ahead))
            record = contact_observation("zai", receipts_root=root, now=NOW)
            self.assertEqual(record.observed_at_utc, ahead)
            self.assertTrue(record.ready)

    def test_receipt_beyond_clock_skew_is_rejected_as_evidence(self):
        # Codex review finding 5: a future stamp beyond the allowance is
        # never fresh and never selected.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            future = NOW + CONTACT_OBSERVATION_MAX_CLOCK_SKEW + timedelta(seconds=1)
            _write_json(root, "zai", "future.json", _json_receipt("zai", finished=future))
            record = contact_observation("zai", receipts_root=root, now=NOW)
            self.assertIsNone(record.source)
            self.assertEqual(record.staleness, ContactObservationStaleness.NOT_OBSERVED)
            self.assertFalse(record.ready)

    def test_future_receipt_never_masks_a_later_genuine_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_json(
                root, "zai", "future.json",
                _json_receipt("zai", finished=NOW + timedelta(days=30)),
            )
            failure = _write_json(
                root, "zai", "genuine.json",
                _json_receipt(
                    "zai", finished=NOW - timedelta(minutes=10),
                    status="FAILED_OR_AMBIGUOUS", exit_code=0,
                ),
            )
            record = contact_observation("zai", receipts_root=root, now=NOW)
            self.assertEqual(record.source, str(failure))
            self.assertEqual(record.task_result_state, ContactTaskResultState.OBSERVED_FAILURE)
            self.assertFalse(record.ready)

    def test_receipt_exactly_at_the_window_boundary_is_fresh(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            boundary = NOW - CONTACT_OBSERVATION_FRESHNESS
            _write_json(root, "zai", "boundary.json", _json_receipt("zai", finished=boundary))
            record = contact_observation("zai", receipts_root=root, now=NOW)
            self.assertEqual(record.staleness, ContactObservationStaleness.FRESH)
            self.assertTrue(record.ready)


class ContactIsolationTests(unittest.TestCase):
    """Acceptance (D): no contact reads another contact's receipts."""

    def _foreign(self, directory: str, provider: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_json(
                root, directory, f"{_stamp(NOW - timedelta(hours=1))}-foreign.json",
                _json_receipt(provider, finished=NOW - timedelta(hours=1)),
            )
            self.assertFalse(contact_observation("grok-bot", receipts_root=root, now=NOW).ready)
            self.assertFalse(contact_observation("zcode", receipts_root=root, now=NOW).ready)
            self.assertFalse(
                contact_observation("antigravity-subscription-cli", receipts_root=root, now=NOW).ready
            )

    def test_grok_bot_never_reads_direct_grok_or_xai_receipts(self):
        self._foreign("grok", "grok")
        self._foreign("xai", "xai")

    def test_zcode_never_reads_zai_api_receipts(self):
        self._foreign("zai", "zai")

    def test_antigravity_subscription_cli_never_reads_gemini_api_receipts(self):
        self._foreign("gemini", "gemini")

    def test_receipt_of_another_provider_inside_own_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_json(
                root, "zai", "mislabeled.json",
                _json_receipt("xai", finished=NOW - timedelta(hours=1)),
            )
            record = contact_observation("zai", receipts_root=root, now=NOW)
            self.assertFalse(record.ready)
            self.assertIsNone(record.source)
            self.assertEqual(record.task_result_state, ContactTaskResultState.NOT_OBSERVED)

    def test_agy_prefixed_file_naming_another_provider_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_agy(root, NOW - timedelta(hours=1), provider="gemini")
            record = contact_observation("antigravity-subscription-cli", receipts_root=root, now=NOW)
            self.assertIsNone(record.source)
            self.assertEqual(record.staleness, ContactObservationStaleness.NOT_OBSERVED)

    def test_providerless_json_without_contact_prefix_is_rejected(self):
        # Raw output dumps (for example zai-*.out.json or cursor-*.out.json in
        # worktree directories) carry no provider field and are not receipts.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stamp = _dispatch_stamp(NOW - timedelta(hours=1))
            _write_json(root, WORKTREE, f"cursor-{stamp}.out.json", {"exit_code": 0})
            _write_json(root, WORKTREE, f"zai-{stamp}.out.json", {"exit_code": 0})
            records = contact_observations(receipts_root=root, now=NOW)
            for contact_id in SIX_CONTACTS:
                with self.subTest(contact_id=contact_id):
                    self.assertIsNone(records[contact_id].source)

    def test_isolation_holds_with_every_lane_populated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            moment = NOW - timedelta(hours=1)
            for lane, provider in (
                ("zai", "zai"), ("grok", "grok"), ("xai", "xai"), ("gemini", "gemini"),
                ("firecrawl", "firecrawl"), ("openai", "openai"), ("perplexity", "perplexity"),
            ):
                _write_json(
                    root, lane, f"{_stamp(moment)}-receipt.json",
                    _json_receipt(provider, finished=moment),
                )
            records = contact_observations(receipts_root=root, now=NOW)
            self.assertTrue(records["zai"].ready)
            self.assertFalse(records["grok-bot"].ready)
            self.assertFalse(records["zcode"].ready)
            self.assertFalse(records["antigravity-subscription-cli"].ready)
            self.assertFalse(records["github-copilot"].ready)


class CopilotObservationOnlyTests(unittest.TestCase):
    """Acceptance (E): Copilot is observation only, issues-only scope."""

    def test_copilot_record_is_observation_only_with_issues_only_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = contact_observation("github-copilot", receipts_root=root, now=NOW)
            self.assertEqual(record.permissions_state, ContactPermissionState.RECORDED_ISSUES_ONLY)
            self.assertIn("issues-only", record.note or "")
            self.assertIn("Observation only", record.note or "")
            self.assertFalse(record.ready)
            self.assertEqual(record.quota_state, ContactQuotaState.NOT_OBSERVED)
            self.assertEqual(record.auth_state, ContactAuthState.NOT_OBSERVED)
            self.assertEqual(record.task_result_state, ContactTaskResultState.NOT_OBSERVED)


class NoNetworkNoSecretTests(unittest.TestCase):
    """Acceptance (F): no network, no subprocess, no credential value."""

    def test_records_are_built_with_socket_and_subprocess_disabled(self):
        def refuse(*args, **kwargs):
            raise AssertionError("network/subprocess use in the observation path")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_json(
                root, "zai", "netfree.json",
                _json_receipt("zai", finished=NOW - timedelta(hours=1)),
            )
            _write_firecrawl(root, NOW - timedelta(hours=1))
            _write_agy(root, NOW - timedelta(hours=1))
            _write_copilot(root, NOW - timedelta(hours=1))
            with (
                patch.object(socket, "socket", refuse),
                patch.object(socket, "create_connection", refuse),
                patch.object(subprocess, "Popen", refuse),
                patch.object(subprocess, "run", refuse),
            ):
                records = contact_observations(receipts_root=root, now=NOW)
            self.assertTrue(records["zai"].ready)
            self.assertTrue(records["firecrawl"].ready)
            self.assertFalse(records["antigravity-subscription-cli"].ready)
            self.assertFalse(records["github-copilot"].ready)

    def test_output_contains_no_environment_value(self):
        # Codex review finding 6: an isolated synthetic environment holding
        # only fixture values; the live environment is never read or copied.
        sentinels = {
            "CONTACT_OBSERVATIONS_SECRET_A": "ak-SENTINEL-VALUE-ONE-4815162342",
            "CONTACT_OBSERVATIONS_SECRET_B": "SENTINEL-TOKEN-tgt-9931-ffeeddcc",
            "ZAI_API_KEY": "SENTINEL-zai-fixture-key-77aa",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_zai_run(root, NOW - timedelta(hours=1))
            _write_firecrawl(root, NOW - timedelta(hours=1))
            _write_agy(root, NOW - timedelta(hours=1))
            _write_copilot(root, NOW - timedelta(hours=1))
            # patch.object swaps the mapping by reference: no live value is copied.
            with patch.object(os, "environ", dict(sentinels)):
                report = contact_observations_report(receipts_root=root, now=NOW)
                dumped = json.dumps(report.model_dump(mode="json"), default=str)
            for name, value in sentinels.items():
                # The failure message names the fixture variable, never a value.
                self.assertFalse(value in dumped, f"fixture value of {name} leaked")


if __name__ == "__main__":
    unittest.main()
