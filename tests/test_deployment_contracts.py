from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeploymentContractTests(unittest.TestCase):
    def test_n8n_forwards_the_authenticated_caller_header(self):
        workflow = json.loads(
            (ROOT / "n8n" / "workflows" / "director-intake.json").read_text(encoding="utf-8")
        )
        self.assertEqual(workflow["id"], "directorIntake")
        request_node = next(node for node in workflow["nodes"] if node["name"] == "Create Director Task")
        headers = request_node["parameters"]["headerParameters"]["parameters"]
        authorization = next(item["value"] for item in headers if item["name"] == "Authorization")

        self.assertEqual(authorization, "={{ $json.headers.authorization }}")
        self.assertEqual(request_node["parameters"]["url"], "http://orchestrator:8080/v1/intake")
        self.assertNotIn("ORCHESTRATOR_SHARED_SECRET", json.dumps(workflow))

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
