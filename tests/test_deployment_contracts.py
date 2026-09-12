# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-12 | 14:05 UTC | BEGIN]]
# [[Gemini Antigravity | Gemini 3.1 Pro (High) | 2026-09-12 | 12:24 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-10 | 18:04 UTC | BEGIN]]
# [[Claude Cowork | Claude Opus 5 | 2026-09-10 | 03:25 UTC | BEGIN]]
# [[Claude Cowork | Claude Opus 5 | 2026-09-09 | 23:10 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 00:20 UTC | BEGIN]]
# [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:18 UTC | BEGIN]]
# [[OpenAI Codex | GPT-5.6 | 2026-08-25 | 10:06 UTC | BEGIN]]
# [[Codex | GPT-5.6 | 2026-08-23 | 20:19 UTC | BEGIN]]
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeploymentContractTests(unittest.TestCase):
    def _run_status_sequence(self, responses):
        workflow = json.loads(
            (ROOT / "n8n/workflows/director-status.json").read_text(encoding="utf-8")
        )
        code = next(node for node in workflow["nodes"] if node["name"] == "Diff Readiness")
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required to execute the stored n8n status code")
        result = subprocess.run(
            [node, "-e", """
const fs = require('node:fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const run = new Function('items', '$getWorkflowStaticData', input.code);
const store = {};
const outputs = input.responses.map(json => run([{json}], () => store));
process.stdout.write(JSON.stringify({outputs, store}));
"""],
            input=json.dumps({"code": code["parameters"]["jsCode"], "responses": responses}),
            text=True, encoding="utf-8", capture_output=True, timeout=10, check=True,
        )
        return json.loads(result.stdout)

    def _status_response(self, **providers):
        return {"statusCode": 200, "body": {
            "providers": {name: providers.get(name, True) for name in (
                "slack", "openai_director", "cursor", "claude", "gemini", "grok", "perplexity"
            )},
            "parallel_dispatch": {"enabled": False, "kill_switch_open": True},
            "langgraph_primary": {"ambiguous_effect_count": 0},
        }}

    def test_status_executes_provider_changes_and_suppresses_unchanged_health(self):
        healthy = self._status_response()
        down = self._status_response(gemini=False)
        result = self._run_status_sequence([healthy, healthy, down, down, healthy])
        initial, quiet, changed, still_down, recovered = result["outputs"]
        self.assertEqual(len(initial), 1)
        self.assertEqual(quiet, [])
        self.assertEqual(still_down, [])
        self.assertEqual(changed[0]["json"]["changed"], ["provider_gemini_ready"])
        self.assertIn("true → false", changed[0]["json"]["text"])
        self.assertIn("false → true", recovered[0]["json"]["text"])
        watched = json.loads(result["store"]["lastWatched"])
        self.assertEqual(sum(key.startswith("provider_") for key in watched), 7)

    def test_status_executes_transport_http_and_repeated_ambiguity_alarms(self):
        ambiguous = self._status_response()
        ambiguous["body"]["langgraph_primary"]["ambiguous_effect_count"] = 1
        stopped = self._status_response()
        stopped["body"]["parallel_dispatch"]["kill_switch_open"] = False
        stopped["body"]["parallel_dispatch"]["enabled"] = True
        responses = [{"error": "connection refused"}, {"statusCode": 503},
                     ambiguous, ambiguous, stopped]
        result = self._run_status_sequence(responses)
        for index, output in enumerate(result["outputs"]):
            with self.subTest(response=index):
                self.assertEqual(len(output), 1)
                self.assertIs(output[0]["json"]["alarm"], True)
        self.assertIn("unreachable", result["outputs"][0][0]["json"]["text"])
        self.assertIn("ambiguous effects", result["outputs"][3][0]["json"]["text"])
        self.assertIn("kill switch engaged while parallel dispatch is enabled", result["outputs"][4][0]["json"]["text"])

    def test_status_treats_engaged_kill_switch_as_expected_under_offline_simulation(self):
        healthy = self._status_response()
        engaged = self._status_response()
        engaged["body"]["parallel_dispatch"]["kill_switch_open"] = False
        engaged_enabled = self._status_response()
        engaged_enabled["body"]["parallel_dispatch"]["kill_switch_open"] = False
        engaged_enabled["body"]["parallel_dispatch"]["enabled"] = True

        result = self._run_status_sequence([healthy, engaged, engaged, engaged, engaged_enabled])
        initial, changed, quiet1, quiet2, alarmed = result["outputs"]

        self.assertEqual(len(initial), 1)
        self.assertEqual(len(changed), 1)
        self.assertIs(changed[0]["json"]["alarm"], False)
        self.assertEqual(changed[0]["json"]["changed"], ["parallel_dispatch_kill_switch_open"])
        self.assertEqual(quiet1, [])
        self.assertEqual(quiet2, [])
        self.assertEqual(len(alarmed), 1)
        self.assertIs(alarmed[0]["json"]["alarm"], True)
        self.assertIn("kill switch engaged while parallel dispatch is enabled", alarmed[0]["json"]["text"])

    def test_status_only_emits_fixed_boolean_provider_fields(self):
        response = self._status_response(gemini="sensitive-test-value", grok={"key": "sensitive-test-value"})
        response["body"]["providers"]["api_key"] = "sensitive-test-value"
        response["body"]["credentials"] = {"token": "sensitive-test-value"}
        result = self._run_status_sequence([response])
        self.assertNotIn("sensitive-test-value", json.dumps(result))
        self.assertNotIn("api_key", json.dumps(result))
        watched = json.loads(result["store"]["lastWatched"])
        self.assertIsNone(watched["provider_gemini_ready"])
        self.assertIsNone(watched["provider_grok_ready"])
        self.assertIs(watched["provider_openai_director_ready"], True)

    def test_status_connects_schedule_health_diff_and_slack_without_other_routes(self):
        workflow = json.loads(
            (ROOT / "n8n/workflows/director-status.json").read_text(encoding="utf-8")
        )
        names = ["Every 5 minutes", "Read Health", "Diff Readiness", "Post Status"]
        self.assertEqual({node["name"] for node in workflow["nodes"]}, set(names))
        self.assertEqual(workflow["connections"], {
            source: {"main": [[{"node": target, "type": "main", "index": 0}]]}
            for source, target in zip(names, names[1:])
        })
        health = next(node for node in workflow["nodes"] if node["name"] == "Read Health")
        self.assertEqual(health["parameters"]["url"], "http://orchestrator:8080/health")
        self.assertEqual(health["onError"], "continueRegularOutput")
        self.assertTrue(health["parameters"]["options"]["response"]["response"]["fullResponse"])

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
        # webhook answers n8n's standard 200 message -- body text undocumented,
        # so only the absence of a Respond node is asserted -- and the gateway
        # records an unconfirmed
        # delivery rather than a fabricated success or failure.
        stop = self._intake_node("Ambiguous Delivery - Stop")
        self.assertEqual(stop["type"], "n8n-nodes-base.noOp")
        self.assertNotIn("Ambiguous Delivery - Stop", workflow["connections"])

    def test_n8n_treats_a_5xx_as_unproven_because_intake_is_reserved_first(self):
        # A-19'' P1. The relay once classified every 400-599 as a definite
        # refusal. That is false for 5xx: primary intake is reserved before
        # later operations can fail (primary_coordinator.reserve path), and
        # main.py wraps those exceptions as 503 -- so a task can already exist
        # behind one. Reporting "no task was created" there loses a live task.
        # This asserts the boundary numerically, not by matching prose.
        classifier = self._intake_node("Definite Upstream Refusal")
        condition = json.dumps(classifier["parameters"]["conditions"])

        bounds = [int(n) for n in re.findall(r"statusCode\s*<=\s*(\d{3})", condition)]
        self.assertEqual(len(bounds), 1, "expected exactly one upper bound")
        self.assertLess(
            bounds[0],
            500,
            "5xx must not be classified as a definite refusal; a task may exist",
        )
        self.assertNotIn("599", condition)

        # And the operator-facing text must not contradict the classifier.
        notes = json.dumps(self._intake_node("Intake Failed").get("notes", ""))
        self.assertNotIn("503", notes)

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
# [[Claude Cowork | Claude Opus 5 | 2026-09-10 | 03:25 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-10 | 18:04 UTC | END]]
# [[Gemini Antigravity | Gemini 3.1 Pro (High) | 2026-09-12 | 12:24 UTC | END]]
# [[Gemini Antigravity | gemini-3.1-pro-high | 2026-09-12 | 14:05 UTC | END]]
