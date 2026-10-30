from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from director_orchestrator.models import UsageCheckRunResponse, UsageCheckRunStatus
import director_orchestrator.main as main


class UsageCheckRunApiTests(unittest.TestCase):
    def _request(self, plan: UsageCheckRunResponse, *, run_once: AsyncMock | None = None):
        run_once = run_once or AsyncMock(return_value=[])
        test_settings = replace(
            main.settings,
            usage_check_enabled=True,
            allow_paid_runs=True,
            shared_secret="test-secret",
        )
        with (
            patch.object(main, "settings", test_settings),
            patch.object(
                main.usage_monitor,
                "reserve_manual_cycle",
                new=AsyncMock(return_value=plan),
            ),
            patch.object(main.usage_monitor, "run_once", run_once),
            patch.object(main.usage_monitor, "run_forever", new=AsyncMock(return_value=None)),
            patch.object(main.engine, "run", new=AsyncMock(return_value=None)),
            patch.object(main.store, "list_recoverable", return_value=[]),
        ):
            with TestClient(main.app) as client:
                response = client.post(
                    "/v1/usage-checks/run",
                    headers={"Authorization": "Bearer test-secret"},
                )
        return response, run_once

    def test_accepted_response_includes_scheduled_providers_and_returns_202(self):
        plan = UsageCheckRunResponse(
            status=UsageCheckRunStatus.ACCEPTED,
            cycle_id="USAGE-ABCDEF1234",
            scheduled_providers=["cursor", "openai"],
            cooling_down_providers=["claude"],
            unsupported_providers=[],
            minimum_interval_minutes=15,
        )
        response, _ = self._request(plan)
        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(payload["status"], "accepted")
        self.assertEqual(payload["cycle_id"], "USAGE-ABCDEF1234")
        self.assertEqual(payload["scheduled_providers"], ["cursor", "openai"])
        self.assertEqual(payload["cooling_down_providers"], ["claude"])

    def test_not_due_response_is_200_and_does_not_schedule_run_once(self):
        plan = UsageCheckRunResponse(
            status=UsageCheckRunStatus.NOT_DUE,
            cycle_id=None,
            scheduled_providers=[],
            cooling_down_providers=["cursor", "openai", "claude"],
            unsupported_providers=[],
            minimum_interval_minutes=15,
            reason="All configured supported providers are within the 15-minute minimum interval",
        )
        response, run_once = self._request(plan)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "not_due")
        self.assertIsNone(payload["cycle_id"])
        self.assertEqual(payload["scheduled_providers"], [])
        self.assertEqual(payload["cooling_down_providers"], ["cursor", "openai", "claude"])
        run_once.assert_not_awaited()

    def test_busy_response_is_409(self):
        plan = UsageCheckRunResponse(
            status=UsageCheckRunStatus.BUSY,
            scheduled_providers=[],
            cooling_down_providers=["cursor"],
            unsupported_providers=[],
            minimum_interval_minutes=15,
            reason="A usage-check cycle is already running or reserved",
        )
        response, _ = self._request(plan)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["status"], "busy")


class ContactObservationsApiTests(unittest.TestCase):
    """Additive WO-20261029-005 cases: the read-only contact-observations route."""

    SIX_CONTACTS = (
        "zai",
        "github-copilot",
        "firecrawl",
        "antigravity-subscription-cli",
        "grok-bot",
        "zcode",
    )

    def _get(self, headers: dict | None, receipts_root):
        test_settings = replace(main.settings, shared_secret="test-secret")
        with (
            patch.object(main, "settings", test_settings),
            patch.object(
                main.usage_monitor, "run_forever", new=AsyncMock(return_value=None)
            ),
            patch.object(main.engine, "run", new=AsyncMock(return_value=None)),
            patch.object(main.store, "list_recoverable", return_value=[]),
            patch(
                "director_orchestrator.contact_observations.DEFAULT_RECEIPTS_ROOT",
                receipts_root,
            ),
        ):
            with TestClient(main.app) as client:
                return client.get(
                    "/v1/usage-checks/contact-observations", headers=headers
                )

    def test_authorized_get_returns_all_contacts_from_receipts_only(self):
        with tempfile.TemporaryDirectory() as directory:
            empty_root = Path(directory) / "receipts"
            response = self._get(
                {"Authorization": "Bearer test-secret"}, empty_root
            )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["freshness_window_days"], 7)
            self.assertFalse(payload["receipts_root_present"])
            records = {record["contact_id"]: record for record in payload["contacts"]}
            self.assertEqual(tuple(records), self.SIX_CONTACTS)
            for contact_id in self.SIX_CONTACTS:
                with self.subTest(contact_id=contact_id):
                    self.assertFalse(records[contact_id]["ready"])
                    self.assertEqual(records[contact_id]["staleness"], "not-observed")
                    self.assertEqual(records[contact_id]["task_result_state"], "not-observed")
            # Observation-only Copilot: issues-only scope, nothing else claimed.
            self.assertEqual(
                records["github-copilot"]["permissions_state"], "recorded-issues-only"
            )

    def test_get_requires_authentication(self):
        with tempfile.TemporaryDirectory() as directory:
            empty_root = Path(directory) / "receipts"
            self.assertEqual(
                self._get(None, empty_root).status_code, 401
            )
            self.assertEqual(
                self._get({"Authorization": "Bearer wrong-secret"}, empty_root).status_code,
                401,
            )


if __name__ == "__main__":
    unittest.main()
