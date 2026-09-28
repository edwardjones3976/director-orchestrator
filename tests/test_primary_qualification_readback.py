# [[OpenAI Codex | GPT-6 | 2026-09-28 | 00:48 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-28 | 00:34 UTC | BEGIN]]
import unittest
from unittest.mock import patch

from psycopg.conninfo import conninfo_to_dict
from director_orchestrator import langgraph_postgres as pg


class QualificationReadOnlyFactoryTests(unittest.TestCase):
    dsn = "host=localhost port=55463 dbname=director_test user=director_ci sslmode=verify-full sslrootcert=fixture-ca.pem"
    schema = "director_primary_qualification_v1"
    instance = "1328fe1c-1b22-49a2-8818-7f0acf21c0d0"

    def arguments(self, **changes):
        parsed = conninfo_to_dict(self.dsn)
        return {"schema": self.schema,
                "accepted_storage_identity": pg._storage_identity(parsed, schema=self.schema),
                "accepted_instance_id": self.instance, **changes}

    def test_refuses_untrusted_transport_target_and_instance_before_open(self):
        cases = [(self.dsn.replace("verify-full", mode), self.arguments())
                 for mode in ("disable", "require", "verify-ca")]
        cases += [(self.dsn, self.arguments(accepted_storage_identity="wrong")),
                  (self.dsn, self.arguments(accepted_instance_id="wrong")),
                  (self.dsn, self.arguments(wall_seconds=True)),
                  (self.dsn, self.arguments(wall_seconds=float("nan")))]
        for dsn, arguments in cases:
            with self.subTest(dsn=dsn, arguments=arguments), \
                    patch("psycopg_pool.ConnectionPool") as pool, self.assertRaises(ValueError):
                pg.read_postgres_qualification_evidence(dsn, "DIR-LG-AB12", **arguments)
            pool.assert_not_called()

    def test_returns_only_snapshot_through_real_factory_provenance_and_readonly_pool(self):
        snapshot = {"complete": True, "snapshot": {"read_only": True}}
        def read(store, task, *, wall_seconds):
            self.assertTrue(store.is_factory_bound_control_plane())
            self.assertEqual(task, "DIR-LG-AB12")
            self.assertGreater(wall_seconds, 0)
            self.assertLessEqual(wall_seconds, 5)
            return snapshot
        with patch("psycopg_pool.ConnectionPool") as pool, \
                patch.object(pg.PostgresConditionalClaimStore, "read_primary_qualification_evidence",
                             autospec=True, side_effect=read) as reader, \
                patch.object(pg, "_setup_control_plane", side_effect=AssertionError("DDL forbidden")), \
                patch.object(pg, "open_postgres_control_plane", side_effect=AssertionError("runtime forbidden")):
            result = pg.read_postgres_qualification_evidence(self.dsn, "DIR-LG-AB12", **self.arguments())
        self.assertIs(result, snapshot)
        reader.assert_called_once()
        self.assertIn("default_transaction_read_only=on", pool.call_args.kwargs["kwargs"]["options"])
        pool.return_value.open.assert_called_once_with(wait=False)
        pool.return_value.close.assert_called_once()

    def test_connection_preparation_and_cleanup_cannot_reset_deadline(self):
        for ticks, calls in (([0, 4.95], 0), ([0, 1, 6], 1)):
            with self.subTest(ticks=ticks), patch.object(pg, "monotonic", side_effect=ticks), \
                    patch("psycopg_pool.ConnectionPool") as pool, \
                    patch.object(pg.PostgresConditionalClaimStore, "read_primary_qualification_evidence",
                                 return_value={}) as reader, self.assertRaises(pg.DeadlineExpired):
                pg.read_postgres_qualification_evidence(self.dsn, "DIR-LG-AB12", **self.arguments())
            self.assertEqual(reader.call_count, calls)
            pool.return_value.close.assert_called_once()


class TrustedTaskConsumerTests(unittest.TestCase):
    def reader(self):
        import httpx
        from datetime import datetime, timedelta
        from tests import test_completion_scorecard as fixture
        from tests import test_collect_offline_slack_trials as trial
        from director_orchestrator import completion_scorecard as sc

        task, _, _, _ = trial.sample()
        task["status"] = "DONE"
        for outcome in task["assignment_outcomes"]:
            outcome.update(assignment_sequence=1, provider="claude", review_summary="fixture review")
        evidence = trial.readback_fixture()
        evidence["projection"] = pg.PrimaryTaskProjection(task["task_id"], "langgraph", "a" * 64,
            1, evidence["projection_version"], evidence["projection_digest"],
            {"terminal_classification": "SUCCEEDED", "application": {"task_record": task}})
        captured = datetime.fromisoformat(evidence["captured_at"])
        runtime = fixture.runtime_fixture(collected_at=captured - timedelta(seconds=1),
                                          collection_end=lambda: captured + timedelta(seconds=1))
        def transport(request):
            self.assertEqual(str(request.url), "https://slack.com/api/auth.test")
            return httpx.Response(200, json={"ok": True, "user_id": fixture.BOT, "bot_id": "BFIXTURE"})
        client = httpx.Client(transport=httpx.MockTransport(transport))
        self.addCleanup(client.close)
        reader = sc._SlackThreads(runtime, fixture.CHANNEL_FIXTURE, fixture.OWNER, client=client)
        self.addCleanup(reader.close)
        return reader, task["task_id"], evidence

    def test_task_and_deliveries_share_one_snapshot_without_director_http(self):
        from director_orchestrator import completion_scorecard as sc
        reader, task, evidence = self.reader()
        with patch.object(sc, "_trusted_collection", return_value=({task: evidence}, None)) as collect:
            body, headers = reader.task(task)
            deliveries = reader.deliveries(task)
            self.assertEqual(reader.task(task), (body, headers))
        collect.assert_called_once_with(reader.runtime, [task], qualification_only=True)
        self.assertEqual(body["task_id"], task)
        self.assertEqual(headers["x-primary-projection-digest"], evidence["projection_digest"])
        self.assertTrue(deliveries)
        self.assertTrue(all(row["task_id"] == task for row in deliveries))
        self.assertEqual([row["client_msg_id"] for row in deliveries],
                         [row["client_msg_id"] for row in body["qualification_evidence"]["slack_deliveries"]])

    def test_failed_incomplete_stale_or_foreign_snapshot_never_falls_back_or_retries(self):
        import copy
        from director_orchestrator import completion_scorecard as sc
        for change in (None, {"complete": False}, {"captured_at": "2020-01-01T00:00:00+00:00"},
                       {"task_id": "DIR-LG-DEAD"}):
            reader, task, evidence = self.reader()
            if change is None:
                options = {"side_effect": ValueError("store unavailable")}
            else:
                evidence = {**copy.deepcopy(evidence), **change}
                options = {"return_value": ({task: evidence}, None)}
            with self.subTest(change=change), patch.object(sc, "_trusted_collection", **options) as collect:
                with self.assertRaises(ValueError):
                    reader.task(task)
                with self.assertRaises(ValueError):
                    reader.deliveries(task)
            collect.assert_called_once()


if __name__ == "__main__":
    unittest.main()
# [[OpenAI Codex | GPT-6 | 2026-09-28 | 00:34 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-28 | 00:48 UTC | END]]
