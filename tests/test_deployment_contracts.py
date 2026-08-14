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
        request_node = next(node for node in workflow["nodes"] if node["name"] == "Create Director Task")
        headers = request_node["parameters"]["headerParameters"]["parameters"]
        authorization = next(item["value"] for item in headers if item["name"] == "Authorization")

        self.assertEqual(authorization, "={{ $json.headers.authorization }}")
        self.assertEqual(request_node["parameters"]["url"], "http://orchestrator:8080/v1/intake")
        self.assertNotIn("ORCHESTRATOR_SHARED_SECRET", json.dumps(workflow))

    def test_compose_does_not_expose_the_orchestrator_secret_to_n8n(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn("env_file: ${DIRECTOR_ENV_FILE:-.env}", compose)
        self.assertNotIn("ORCHESTRATOR_SHARED_SECRET: ${ORCHESTRATOR_SHARED_SECRET}", compose)
        self.assertIn('N8N_BLOCK_ENV_ACCESS_IN_NODE: "true"', compose)
        self.assertIn("postgres:17-alpine@sha256:", compose)
        self.assertIn("n8n:latest@sha256:", compose)

        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("FROM python:3.12-slim@sha256:", dockerfile)

    def test_pull_requests_have_cross_platform_ci(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

        self.assertIn("pull_request:", workflow)
        self.assertIn("ubuntu-latest", workflow)
        self.assertIn("windows-latest", workflow)
        self.assertIn("python -m unittest discover -s tests -v", workflow)
        self.assertIn("docker compose --env-file .env.example config --quiet", workflow)


if __name__ == "__main__":
    unittest.main()
