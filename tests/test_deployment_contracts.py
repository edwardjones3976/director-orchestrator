# [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 23:10 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 00:20 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:18 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:06 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-23 | 20:19 UTC | BEGIN]]
from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeploymentContractTests(unittest.TestCase):
    def test_default_primary_postgres_is_dedicated_healthy_and_default_off(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        environment = (ROOT / ".env.example").read_text(encoding="utf-8")
        primary_db = compose.split("  langgraph-primary-db:\n", 1)[1].split(
            "\n  slackbot:", 1
        )[0]
        orchestrator = compose.split("  orchestrator:\n", 1)[1].split(
            "\n  langgraph-primary-db:", 1
        )[0]
        n8n_db = compose.split("  n8n-db:\n", 1)[1].split("\n  n8n:", 1)[0]

        self.assertIn("postgres:17-alpine@sha256:", primary_db)
        self.assertIn("POSTGRES_USER: director_primary", primary_db)
        self.assertIn("POSTGRES_DB: director_primary", primary_db)
        self.assertIn(
            "POSTGRES_PASSWORD: ${LANGGRAPH_PRIMARY_POSTGRES_PASSWORD}", primary_db
        )
        self.assertIn(
            "langgraph-primary-db-data:/var/lib/postgresql/data", primary_db
        )
        self.assertIn("pg_isready -U director_primary -d director_primary", primary_db)
        self.assertIn("langgraph-primary-db:\n        condition: service_healthy", orchestrator)
        self.assertIn("LANGGRAPH_PRIMARY_POSTGRES_DSN:", orchestrator)
        self.assertIn("?sslmode=disable", orchestrator)
        self.assertNotIn("n8n-db-data", primary_db)
        self.assertNotIn("langgraph-primary-db-data", n8n_db)

        self.assertIn("LANGGRAPH_PRIMARY_ENABLED=false", environment)
        self.assertIn("LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED=false", environment)
        self.assertIn("ALLOW_PAID_RUNS=false", environment)
        self.assertIn(
            "LANGGRAPH_PRIMARY_POSTGRES_SCHEMA=director_primary_v1", environment
        )
        self.assertIn(
            "LANGGRAPH_PRIMARY_CHECKPOINT_NAMESPACE=default-director-primary-v1",
            environment,
        )
        self.assertIn("LANGGRAPH_PRIMARY_POSTGRES_DSN=\n", environment)
        self.assertIn(
            "LANGGRAPH_PRIMARY_OWNER_TOKEN=REPLACE_WITH_DISTINCT_RANDOM_SECRET",
            environment,
        )
        self.assertIn(
            "LANGGRAPH_PRIMARY_POSTGRES_PASSWORD=REPLACE_WITH_DISTINCT_RANDOM_SECRET",
            environment,
        )
        self.assertIn(
            "LANGGRAPH_PRIMARY_ALLOW_INSECURE_LOCAL_POSTGRES=false", environment
        )

    def test_default_primary_backup_and_restore_are_bounded_to_its_service(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        section = readme.split(
            "### Default LangGraph-primary database (inactive by default)", 1
        )[1].split("\n## Slack app setup", 1)[0]

        self.assertIn("LANGGRAPH_PRIMARY_ENABLED=false", section)
        self.assertIn("LANGGRAPH_PRIMARY_NEW_INTAKE_ENABLED=false", section)
        self.assertIn("fail-closed staging and backup surface", section)
        self.assertIn("sslmode=verify-full", section)
        self.assertIn("pg_dump", section)
        self.assertIn("pg_restore", section)
        self.assertIn("--clean --if-exists", section)
        self.assertIn("--exit-on-error --single-transaction", section)
        self.assertIn("stop orchestrator slackbot", section)
        self.assertIn("langgraph-primary-db:/tmp/$backupName", section)
        self.assertNotIn("n8n-db:/tmp/$backupName", section)
        self.assertIn("never commit them", section)

    def test_readme_local_postgres_exception_matches_shipped_compose(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        environment = (ROOT / ".env.example").read_text(encoding="utf-8")
        orchestrator = compose.split("  orchestrator:\n", 1)[1].split(
            "\n  langgraph-primary-db:", 1
        )[0]
        section = readme.split(
            "### Default LangGraph-primary database (inactive by default)", 1
        )[1].split("\n## Slack app setup", 1)[0]

        # The shipped defaults are the contract the prose must describe: the
        # closed .env value plus the orchestrator-only Compose override for the
        # exact local service hostname.
        self.assertIn(
            'LANGGRAPH_PRIMARY_ALLOW_INSECURE_LOCAL_POSTGRES: "true"', orchestrator
        )
        self.assertIn(
            "LANGGRAPH_PRIMARY_ALLOW_INSECURE_LOCAL_POSTGRES=false", environment
        )
        self.assertIn(
            'overrides it to `"true"` for the orchestrator service', section
        )
        self.assertIn("LANGGRAPH_PRIMARY_ALLOW_INSECURE_LOCAL_POSTGRES=false", section)
        self.assertIn("langgraph-primary-db", section)
        self.assertIn("container-only exception", section)
        self.assertNotIn(
            "unencrypted container-network transport\nwhile "
            "`LANGGRAPH_PRIMARY_ALLOW_INSECURE_LOCAL_POSTGRES=false`",
            section,
        )

    def test_n8n_forwards_the_authenticated_caller_header(self):
        workflow = json.loads(
            (ROOT / "n8n" / "workflows" / "director-intake.json").read_text(encoding="utf-8")
        )
        self.assertEqual(workflow["id"], "directorIntake")
        request_node = next(node for node in workflow["nodes"] if node["name"] == "Create Director Task")
        headers = request_node["parameters"]["headerParameters"]["parameters"]
        authorization = next(item["value"] for item in headers if item["name"] == "Authorization")
        attestation_headers = {
            item["name"]: item["value"]
            for item in headers
            if item["name"].startswith("X-Director-Intake-")
        }

        self.assertEqual(authorization, "={{ $json.headers.authorization }}")
        self.assertEqual(
            attestation_headers,
            {
                "X-Director-Intake-Issued-At": (
                    "={{ $json.headers['x-director-intake-issued-at'] }}"
                ),
                "X-Director-Intake-Nonce": (
                    "={{ $json.headers['x-director-intake-nonce'] }}"
                ),
                "X-Director-Intake-Signature": (
                    "={{ $json.headers['x-director-intake-signature'] }}"
                ),
            },
        )
        self.assertEqual(request_node["parameters"]["url"], "http://orchestrator:8080/v1/intake")
        self.assertNotIn("ORCHESTRATOR_SHARED_SECRET", json.dumps(workflow))

    def test_n8n_intake_limits_completed_execution_retention(self):
        workflow = json.loads(
            (ROOT / "n8n" / "workflows" / "director-intake.json").read_text(encoding="utf-8")
        )
        # n8n can persist incoming headers/body initially and defer physical
        # pruning. These settings control completion cleanup, not zero storage.
        settings = workflow["settings"]
        self.assertEqual(settings["saveDataErrorExecution"], "none")
        self.assertEqual(settings["saveDataSuccessExecution"], "none")
        self.assertIs(settings["saveManualExecutions"], False)
        self.assertIs(settings["saveExecutionProgress"], False)

    def _intake_workflow(self):
        return json.loads(
            (ROOT / "n8n" / "workflows" / "director-intake.json").read_text(encoding="utf-8")
        )

    def _intake_node(self, name):
        return next(node for node in self._intake_workflow()["nodes"] if node["name"] == name)

    def test_n8n_preserves_intake_body_and_refuses_redirects(self):
        request = self._intake_node("Create Director Task")
        self.assertEqual(request["parameters"]["method"], "POST")
        self.assertEqual(
            request["parameters"]["body"], "={{ JSON.stringify($json.body ?? $json) }}"
        )
        self.assertFalse(request.get("retryOnFail", False))
        self.assertFalse(request.get("continueOnFail", False))
        # A redirect would resend the caller's Authorization header to another host.
        self.assertIs(
            request["parameters"]["options"]["redirect"]["redirect"]["followRedirects"],
            False,
        )

    def test_n8n_relay_decides_before_its_caller_stops_waiting(self):
        # slack_bot._forward posts with httpx timeout=20. A relay that waits
        # longer than its caller cannot classify the outcome for anyone: the
        # gateway has already given up and recorded an unconfirmed delivery.
        request = self._intake_node("Create Director Task")
        self.assertLess(request["parameters"]["options"]["timeout"], 20_000)

    def test_n8n_accepts_only_an_exact_202_from_the_orchestrator(self):
        # The intake contract is 202. Any other 2xx is a contract violation and
        # must not be relayed to the gateway as an acceptance.
        request = self._intake_node("Create Director Task")
        self.assertIs(
            request["parameters"]["options"]["response"]["response"]["fullResponse"], True
        )
        gate = self._intake_node("Exactly 202")
        self.assertEqual(gate["type"], "n8n-nodes-base.if")
        self.assertIn(
            "$json.statusCode === 202",
            json.dumps(gate["parameters"]["conditions"]),
        )
        accepted = self._intake_node("Accepted")
        self.assertEqual(accepted["parameters"]["options"]["responseCode"], 202)
        # Body only. fullResponse also carries the upstream response headers.
        self.assertEqual(
            accepted["parameters"]["responseBody"], "={{ JSON.stringify($json.body) }}"
        )
        unexpected = self._intake_node("Unexpected Upstream Success")
        self.assertEqual(unexpected["parameters"]["options"]["responseCode"], 502)

    def test_n8n_reports_a_definite_refusal_and_stays_silent_on_ambiguity(self):
        # The invariant this replaces asserted onError == "stopWorkflow", which
        # stopped on *every* failure. That is right for an ambiguous delivery and
        # wrong for a definite refusal: a 401 or 422 means no task was created and
        # the gateway can be told so, whereas a timeout or dropped socket means the
        # task may exist and no claim about it is safe. The workflow now separates
        # the two rather than treating every failure as unknowable.
        workflow = self._intake_workflow()
        request = self._intake_node("Create Director Task")
        self.assertEqual(request.get("onError"), "continueErrorOutput")

        classifier = self._intake_node("Definite Upstream Refusal")
        condition = json.dumps(classifier["parameters"]["conditions"])
        self.assertIn("$json.error?.statusCode", condition)

        failed = self._intake_node("Intake Failed")
        self.assertEqual(failed["parameters"]["options"]["responseCode"], 502)
        self.assertIn("upstream_status", failed["parameters"]["responseBody"])

        # The ambiguous branch must terminate without a Respond node, so the
        # webhook answers 200 empty and the gateway records an unconfirmed
        # delivery rather than a fabricated success or failure.
        stop = self._intake_node("Ambiguous Delivery - Stop")
        self.assertEqual(stop["type"], "n8n-nodes-base.noOp")
        self.assertNotIn("Ambiguous Delivery - Stop", workflow["connections"])

    def test_n8n_intake_relays_no_response_body_on_failure(self):
        # Only the upstream status code may cross back through the webhook.
        for name in ("Intake Failed", "Unexpected Upstream Success"):
            body = self._intake_node(name)["parameters"]["responseBody"]
            self.assertNotIn("$json.body", body)
            self.assertNotIn("headers", body)

    def test_compose_does_not_expose_the_orchestrator_secret_to_n8n(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        n8n_section = compose.split("  n8n:\n", 1)[1].split("\nvolumes:", 1)[0]

        self.assertIn("env_file: ${DIRECTOR_ENV_FILE:-.env}", compose)
        self.assertNotIn("ORCHESTRATOR_SHARED_SECRET", n8n_section)
        self.assertIn('N8N_BLOCK_ENV_ACCESS_IN_NODE: "true"', compose)
        self.assertIn("postgres:17-alpine@sha256:", compose)
        self.assertIn("n8n:latest@sha256:", compose)

        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("FROM python:3.12-slim@sha256:", dockerfile)

    # [[Codex | GPT-5.6 | 2026-08-23 08:31 UTC | BEGIN]]
    def test_slack_bridge_keeps_provider_key_in_orchestrator_only(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        slackbot_section = compose.split("  slackbot:\n", 1)[1].split(
            "\n  n8n-db:", 1
        )[0]

        self.assertIn("CHATGPT_BRIDGE_ENABLED", slackbot_section)
        self.assertIn("CHATGPT_BRIDGE_CHANNEL_IDS", slackbot_section)
        self.assertNotIn("OPENAI_API_KEY", slackbot_section)

    def test_isolated_launcher_clears_every_slack_bridge_interpolation(self):
        launcher = (
            ROOT / "scripts" / "windows-isolated-live-validation.ps1"
        ).read_text(encoding="utf-8")
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        slackbot_section = compose.split("  slackbot:\n", 1)[1].split(
            "\n  n8n-db:", 1
        )[0]
        names = {
            line.split(":", 1)[0].strip()
            for line in slackbot_section.splitlines()
            if "CHATGPT_BRIDGE_" in line and ":" in line
        }

        self.assertEqual(
            names,
            {
                "CHATGPT_BRIDGE_ENABLED",
                "CHATGPT_BRIDGE_SLACK_USER_ID",
                "CHATGPT_BRIDGE_CHANNEL_IDS",
                "CHATGPT_BRIDGE_MAX_PROMPT_CHARS",
            },
        )
        for name in names:
            self.assertIn(f'"{name}"', launcher)
    # [[Codex | GPT-5.6 | 2026-08-23 08:31 UTC | END]]

    def test_pull_requests_have_cross_platform_ci(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        recovery = (ROOT / ".github" / "workflows" / "recovery-soak.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("pull_request:", workflow)
        self.assertIn("ubuntu-latest", workflow)
        self.assertIn("windows-latest", workflow)
        self.assertIn("python -m unittest discover -s tests -v", workflow)
        self.assertIn("docker compose --env-file .env.example config --quiet", workflow)
        self.assertNotIn(
            "PYTHONPATH: src\n      SQLITE_PATH: ${{ runner.temp }}/recovery-bootstrap.sqlite3",
            workflow,
        )
        self.assertNotIn(
            "PYTHONPATH: src\n      SQLITE_PATH: ${{ runner.temp }}/recovery-bootstrap.sqlite3",
            recovery,
        )


if __name__ == "__main__":
    unittest.main()
# [[Codex | GPT-5.6 | 2026-08-23 | 20:19 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:06 UTC | END]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:18 UTC | END]]
# [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 00:20 UTC | END]]
# [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 23:10 UTC | END]]
