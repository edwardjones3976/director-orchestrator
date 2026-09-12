# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:30 UTC | BEGIN]]
import unittest

import httpx

from director_orchestrator.oracle_merge import GitHubOracleGateway


class OracleBranchProtectionTests(unittest.TestCase):
    def gateway(self, *, protected=False, rules=None, status=200, rules_status=200, malformed=False, drift=False):
        self.requests = []
        def handler(request):
            self.requests.append(request)
            path = request.url.path
            if path.endswith("/pulls/1"):
                return httpx.Response(200, json={"head": {"sha": "b" * 40},
                    "base": {"sha": "a" * 40, "ref": "project/base"}})
            if "/rules/branches/" in path:
                return httpx.Response(rules_status, json=[] if rules is None else rules)
            if "/branches/" in path:
                return httpx.Response(status, json={} if malformed else {
                    "name": "project/base", "commit": {"sha": ("c" if drift else "a") * 40},
                    "protected": protected})
            if path == "/user":
                return httpx.Response(200, json={"login": "test-reviewer"})
            if request.method == "PUT":
                return httpx.Response(200, json={"merged": True, "sha": "d" * 40})
            raise AssertionError(path)
        return GitHubOracleGateway("test-token", transport=httpx.MockTransport(handler))

    def test_unprotected_branch_requires_empty_active_rules(self):
        gateway = self.gateway()
        self.assertFalse(gateway._base_protected("owner/repo", "project/base", "a" * 40))
        self.assertEqual(len(self.requests), 2)
        self.assertIn(b"project%2Fbase", self.requests[0].url.raw_path)

    def test_protected_non_trunk_and_organization_rules_refuse_merge(self):
        for protected, rules in ((True, []), (False, [{"type": "pull_request", "ruleset_source_type": "Organization"}])):
            with self.subTest(protected=protected, rules=rules):
                gateway = self.gateway(protected=protected, rules=rules)
                with self.assertRaisesRegex(RuntimeError, "protected base"):
                    gateway.merge("owner/repo", 1, "b" * 40, "a" * 40)
                self.assertFalse(any(request.method == "PUT" for request in self.requests))

    def test_unknown_protection_never_reaches_mutation(self):
        for options in ({"status": 401}, {"status": 403}, {"status": 404},
                        {"status": 500}, {"rules_status": 403}, {"rules_status": 404}, {"malformed": True}, {"drift": True},
                        {"protected": "false"}, {"rules": {"message": "unknown"}}):
            with self.subTest(options=options):
                gateway = self.gateway(**options)
                with self.assertRaises((RuntimeError, ValueError)):
                    gateway.merge("owner/repo", 1, "b" * 40, "a" * 40)
                self.assertFalse(any(request.method == "PUT" for request in self.requests))

    def test_gateway_rechecks_before_successful_merge(self):
        gateway = self.gateway()
        self.assertTrue(gateway.merge("owner/repo", 1, "b" * 40, "a" * 40).merged)
        paths = [request.url.path for request in self.requests]
        self.assertLess(next(i for i,p in enumerate(paths) if "/rules/branches/" in p),
                        next(i for i,r in enumerate(self.requests) if r.method == "PUT"))
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:30 UTC | END]]
