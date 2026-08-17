from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from director_orchestrator.models import UsageCheckRunResponse, UsageCheckRunStatus


def _patched_app_client(plan: UsageCheckRunResponse, *, run_once: AsyncMock | None = None):
    import director_orchestrator.main as main

    run_once = run_once or AsyncMock(return_value=[])
    return (
        patch.object(main.settings, "usage_check_enabled", True),
        patch.object(main.settings, "allow_paid_runs", True),
        patch.object(main.settings, "shared_secret", "test-secret"),
        patch.object(
            main.usage_monitor,
            "reserve_manual_cycle",
            new=AsyncMock(return_value=plan),
        ),
        patch.object(main.usage_monitor, "run_once", run_once),
        patch.object(main.usage_monitor, "run_forever", new=AsyncMock(return_value=None)),
        patch.object(main.engine, "run", new=AsyncMock(return_value=None)),
        patch.object(main.store, "list_recoverable", return_value=[]),
        main,
        run_once,
    )


class UsageCheckRunApiTests(unittest.TestCase):
    def test_accepted_response_includes_scheduled_providers_and_returns_202(self):
        plan = UsageCheckRunResponse(
            status=UsageCheckRunStatus.ACCEPTED,
            cycle_id="USAGE-ABCDEF1234",
            scheduled_providers=["cursor", "openai"],
            cooling_down_providers=["claude"],
            unsupported_providers=[],
            minimum_interval_minutes=15,
        )
        patches = _patched_app_client(plan)
        main = patches[-2]
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
            with TestClient(main.app) as client:
                response = client.post(
                    "/v1/usage-checks/run",
                    headers={"Authorization": "Bearer test-secret"},
                )
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
        run_once = AsyncMock(return_value=[])
        patches = _patched_app_client(plan, run_once=run_once)
        main = patches[-2]
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
            with TestClient(main.app) as client:
                response = client.post(
                    "/v1/usage-checks/run",
                    headers={"Authorization": "Bearer test-secret"},
                )
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
        patches = _patched_app_client(plan)
        main = patches[-2]
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
            with TestClient(main.app) as client:
                response = client.post(
                    "/v1/usage-checks/run",
                    headers={"Authorization": "Bearer test-secret"},
                )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["status"], "busy")


if __name__ == "__main__":
    unittest.main()
