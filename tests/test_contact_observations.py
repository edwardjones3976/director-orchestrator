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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from director_orchestrator.contact_observations import (
    CONTACT_OBSERVATION_FRESHNESS,
    DEFAULT_RECEIPTS_ROOT,
    contact_observation,
    contact_observations,
    contact_observations_report,
)
from director_orchestrator.contact_families import provider_family_for
from director_orchestrator.models import (
    ContactAuthState,
    ContactObservationStaleness,
    ContactPermissionState,
    ContactQuotaState,
    ContactTaskResultState,
)

NOW = datetime(2026, 10, 30, 12, 0, 0, tzinfo=timezone.utc)
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
        # Acceptance (D): labels match contact_families. ZCode carries the
        # Foreman-ruled label (2026-10-29) equal to the zai lane's label
        # because the contact_families table itself is unchanged here.
        expected = {
            "zai": provider_family_for("zai"),
            "github-copilot": provider_family_for("github-copilot"),
            "firecrawl": provider_family_for("firecrawl"),
            "antigravity-subscription-cli": provider_family_for("antigravity"),
            "grok-bot": provider_family_for("grok-bot"),
            "zcode": provider_family_for("zai"),
        }
        with tempfile.TemporaryDirectory() as directory:
            records = contact_observations(receipts_root=Path(directory), now=NOW)
            for contact_id, family in expected.items():
                self.assertEqual(records[contact_id].provider_family, family)

    def test_unknown_contact_id_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(KeyError):
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

    def test_fresh_firecrawl_search_receipt_yields_observed_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            moment = NOW - timedelta(hours=2)
            path = _write_text(
                root, "firecrawl", f"{_stamp(moment)}-fixture.search.txt",
                "=== Web Results ===\nRAW-CONTENT-NEVER-COPIED\n",
            )
            record = contact_observation("firecrawl", receipts_root=root, now=NOW)
            self.assertEqual(record.source, str(path))
            self.assertEqual(record.observed_at_utc, moment)
            self.assertIn("firecrawl search", record.transport)
            self.assertEqual(record.task_result_state, ContactTaskResultState.OBSERVED_SUCCESS)
            self.assertTrue(record.ready)
            # Raw receipt content is never copied into the record.
            self.assertNotIn("RAW-CONTENT-NEVER-COPIED", json.dumps(record.model_dump(mode="json")))

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
                root, "github-copilot", "fixture03.json",
                _json_receipt(
                    "github-copilot", finished=NOW - timedelta(hours=1), status="ERROR",
                    exit_code=1, is_error=True, error="HTTP 401 unauthorized",
                ),
            )
            record = contact_observation("github-copilot", receipts_root=root, now=NOW)
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
        for contact_id, lane_directory in (
            ("zai", "zai"),
            ("github-copilot", "github-copilot"),
            ("antigravity-subscription-cli", "antigravity"),
            ("grok-bot", "grok-bot"),
            ("zcode", "zcode"),
        ):
            with self.subTest(contact_id=contact_id), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _write_json(
                    root, lane_directory, "catalog.json",
                    {"provider": contact_id, "finished_at": _stamp(NOW - timedelta(hours=1)), "route": "present"},
                )
                record = contact_observation(contact_id, receipts_root=root, now=NOW)
                self.assertFalse(record.ready)
                self.assertEqual(record.task_result_state, ContactTaskResultState.NOT_OBSERVED)
                self.assertEqual(record.staleness, ContactObservationStaleness.FRESH)

    def test_undated_successful_receipt_is_ignored(self):
        # Readiness requires a DATED observed task result.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_json(root, "zai", "undated.json", _json_receipt("zai"))
            record = contact_observation("zai", receipts_root=root, now=NOW)
            self.assertFalse(record.ready)
            self.assertIsNone(record.source)
            self.assertEqual(record.staleness, ContactObservationStaleness.NOT_OBSERVED)

    def test_empty_firecrawl_receipt_states_no_task_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_text(root, "firecrawl", f"{_stamp(NOW - timedelta(hours=1))}-empty.search.txt", "")
            record = contact_observation("firecrawl", receipts_root=root, now=NOW)
            self.assertFalse(record.ready)
            self.assertEqual(record.task_result_state, ContactTaskResultState.NOT_OBSERVED)


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
                root, "zcode", "mislabeled.json",
                _json_receipt("zai", finished=NOW - timedelta(hours=1)),
            )
            record = contact_observation("zcode", receipts_root=root, now=NOW)
            self.assertFalse(record.ready)
            self.assertIsNone(record.source)
            self.assertEqual(record.task_result_state, ContactTaskResultState.NOT_OBSERVED)

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
            _write_text(root, "firecrawl", f"{_stamp(NOW - timedelta(hours=1))}-x.search.txt", "results")
            with (
                patch.object(socket, "socket", refuse),
                patch.object(socket, "create_connection", refuse),
                patch.object(subprocess, "Popen", refuse),
                patch.object(subprocess, "run", refuse),
            ):
                records = contact_observations(receipts_root=root, now=NOW)
            self.assertTrue(records["zai"].ready)
            self.assertTrue(records["firecrawl"].ready)

    def test_output_contains_no_environment_value(self):
        sentinels = {
            "CONTACT_OBSERVATIONS_SECRET_A": "ak-SENTINEL-VALUE-ONE-4815162342",
            "CONTACT_OBSERVATIONS_SECRET_B": "SENTINEL-TOKEN-tgt-9931-ffeeddcc",
        }
        environment = dict(os.environ)
        environment.update(sentinels)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_json(
                root, "zai", "secretscan.json",
                _json_receipt("zai", finished=NOW - timedelta(hours=1)),
            )
            with patch.object(os, "environ", environment):
                report = contact_observations_report(receipts_root=root, now=NOW)
                dumped = json.dumps(report.model_dump(mode="json"), default=str)
            for value in sentinels.values():
                self.assertNotIn(value, dumped)
            root_text = str(root).lower()
            for key, value in os.environ.items():
                if len(value) < 12 or value in sentinels.values():
                    continue
                # A real environment value that is merely a path prefix of the
                # fixture root (TEMP, USERPROFILE on Windows) is not a leak.
                if root_text.startswith(value.lower()):
                    continue
                self.assertNotIn(value, dumped, f"environment value of {key} leaked")


if __name__ == "__main__":
    unittest.main()
