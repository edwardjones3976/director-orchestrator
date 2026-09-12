# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:44 UTC | BEGIN]]
from __future__ import annotations

import base64
import json
import unittest
from datetime import datetime, timedelta, timezone

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from director_orchestrator.config import Settings
from director_orchestrator.oracle_merge import (
    GitHubAppInstallationTokenProvider,
    GitHubOracleGateway,
)


HEAD_SHA = "a" * 40
BASE_SHA = "b" * 40


class GitHubAppAuthenticationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.private_key_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        cls.private_key_b64 = base64.b64encode(cls.private_key_pem).decode("ascii")

    def test_installation_tokens_are_short_lived_and_cached(self):
        fixed = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
        requests: list[httpx.Request] = []

        def auth_handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            app_jwt = request.headers["Authorization"].removeprefix("Bearer ")
            claims = jwt.decode(app_jwt, options={"verify_signature": False})
            self.assertEqual(claims["iss"], "1234")
            self.assertLessEqual(claims["exp"] - claims["iat"], 600)
            return httpx.Response(
                201,
                json={
                    "token": "installation-token",
                    "expires_at": (fixed + timedelta(hours=1)).isoformat().replace(
                        "+00:00", "Z"
                    ),
                },
            )

        provider = GitHubAppInstallationTokenProvider(
            "1234",
            "5678",
            self.private_key_pem,
            transport=httpx.MockTransport(auth_handler),
            now=lambda: fixed,
        )

        self.assertEqual(provider.get_token(), "installation-token")
        self.assertEqual(provider.get_token(), "installation-token")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url.path, "/app/installations/5678/access_tokens")

    def test_gateway_uses_app_token_for_snapshot_and_exact_sha_merge(self):
        fixed = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
        auth_calls = 0
        api_requests: list[httpx.Request] = []

        def auth_handler(_: httpx.Request) -> httpx.Response:
            nonlocal auth_calls
            auth_calls += 1
            return httpx.Response(
                201,
                json={
                    "token": "installation-token",
                    "expires_at": (fixed + timedelta(hours=1)).isoformat().replace(
                        "+00:00", "Z"
                    ),
                },
            )

        pull = {
            "title": "Bounded Oracle change",
            "body": "Only Oracle paths change.",
            "state": "open",
            "draft": False,
            "mergeable": True,
            "merged": False,
            "changed_files": 0,
            "base": {"ref": "main", "sha": BASE_SHA},
            "head": {
                "ref": "oracle/test",
                "sha": HEAD_SHA,
                "repo": {"full_name": "edwardjones3976/edge_engine"},
            },
            "user": {"login": "cursor-agent"},
        }

        def api_handler(request: httpx.Request) -> httpx.Response:
            api_requests.append(request)
            self.assertEqual(request.headers["Authorization"], "Bearer installation-token")
            path = request.url.path
            if request.method == "GET" and path.endswith("/branches/main"):
                if "/rules/" in path:
                    return httpx.Response(200, json=[])
                return httpx.Response(200, json={"name": "main", "commit": {"sha": BASE_SHA}, "protected": False})
            if request.method == "GET" and path.endswith("/pulls/23"):
                return httpx.Response(200, json=pull)
            if request.method == "GET" and path.endswith("/pulls/23/files"):
                return httpx.Response(200, json=[])
            if request.method == "GET" and "/compare/" in path:
                return httpx.Response(
                    200,
                    content=b"",
                    headers={"Content-Type": "application/vnd.github.diff"},
                )
            if request.method == "GET" and path.endswith("/check-runs"):
                return httpx.Response(
                    200,
                    json={
                        "total_count": 1,
                        "check_runs": [
                            {
                                "id": 10,
                                "name": "test",
                                "status": "completed",
                                "conclusion": "success",
                                "head_sha": HEAD_SHA,
                                "app": {"slug": "github-actions"},
                                "check_suite": {"id": 11},
                            }
                        ],
                    },
                )
            if request.method == "POST" and path == "/graphql":
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "repository": {
                                "pullRequest": {
                                    "reviewDecision": "APPROVED",
                                    "reviewThreads": {
                                        "nodes": [],
                                        "pageInfo": {"hasNextPage": False},
                                    },
                                }
                            }
                        }
                    },
                )
            if request.method == "PUT" and path.endswith("/pulls/23/merge"):
                payload = json.loads(request.content)
                self.assertEqual(payload["sha"], HEAD_SHA)
                return httpx.Response(200, json={"merged": True, "sha": "c" * 40})
            return httpx.Response(404)

        settings = Settings(
            oracle_automerge_github_token="",
            oracle_automerge_github_app_id="1234",
            oracle_automerge_github_installation_id="5678",
            oracle_automerge_github_app_private_key_b64=self.private_key_b64,
        )
        gateway = GitHubOracleGateway.from_settings(
            settings,
            transport=httpx.MockTransport(api_handler),
            auth_transport=httpx.MockTransport(auth_handler),
            now=lambda: fixed,
        )

        snapshot = gateway.snapshot("edwardjones3976/edge_engine", 23)
        merged = gateway.merge(
            "edwardjones3976/edge_engine", 23, HEAD_SHA, BASE_SHA
        )

        self.assertEqual(snapshot.head_sha, HEAD_SHA)
        self.assertEqual(snapshot.checks[0].app_slug, "github-actions")
        self.assertTrue(merged.merged)
        self.assertEqual(merged.actor, "github-app-installation:5678")
        self.assertEqual(auth_calls, 1)
        # Protection is checked at snapshot time and immediately before merge,
        # using the same installation-token authenticated request path.
        self.assertEqual(sum(request.url.path.endswith("/branches/main") and "/rules/" not in request.url.path for request in api_requests), 2)
        self.assertEqual(sum(request.url.path.endswith("/rules/branches/main") for request in api_requests), 2)
        self.assertFalse(any(request.url.path == "/user" for request in api_requests))

    def test_partial_or_ambiguous_app_configuration_fails_closed(self):
        partial = Settings(
            oracle_automerge_github_token="",
            oracle_automerge_github_app_id="1234",
            oracle_automerge_github_installation_id="",
            oracle_automerge_github_app_private_key_b64="",
        )
        ambiguous = Settings(
            oracle_automerge_github_token="legacy-token",
            oracle_automerge_github_app_id="1234",
            oracle_automerge_github_installation_id="5678",
            oracle_automerge_github_app_private_key_b64=self.private_key_b64,
        )

        self.assertFalse(partial.oracle_github_credential_configured())
        self.assertFalse(ambiguous.oracle_github_credential_configured())
        with self.assertRaises(ValueError):
            GitHubOracleGateway.from_settings(partial)
        with self.assertRaises(ValueError):
            GitHubOracleGateway.from_settings(ambiguous)

    def test_gateway_marks_only_the_expected_exact_sha_draft_ready(self):
        requests: list[httpx.Request] = []
        pull = {
            "node_id": "PR_node_23",
            "draft": True,
            "base": {"sha": BASE_SHA},
            "head": {"sha": HEAD_SHA},
        }

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "GET" and request.url.path.endswith("/pulls/23"):
                return httpx.Response(200, json=pull)
            if request.method == "POST" and request.url.path == "/graphql":
                payload = json.loads(request.content)
                self.assertEqual(payload["variables"]["pullRequestId"], "PR_node_23")
                self.assertIn("markPullRequestReadyForReview", payload["query"])
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "markPullRequestReadyForReview": {
                                "pullRequest": {"isDraft": False}
                            }
                        }
                    },
                )
            return httpx.Response(404)

        gateway = GitHubOracleGateway(
            "repo-scoped-token", transport=httpx.MockTransport(handler)
        )

        self.assertTrue(
            gateway.mark_ready(
                "edwardjones3976/edge_engine", 23, HEAD_SHA, BASE_SHA
            )
        )
        self.assertEqual([request.method for request in requests], ["GET", "POST"])

        with self.assertRaisesRegex(RuntimeError, "changed"):
            gateway.mark_ready(
                "edwardjones3976/edge_engine", 23, "f" * 40, BASE_SHA
            )


if __name__ == "__main__":
    unittest.main()

# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:44 UTC | END]]
