# [[Claude Code | Claude Opus 5 | 2026-09-09 | 03:13 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-06 | 07:55 UTC | BEGIN]]
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from director_orchestrator.primary_activation_evidence import (
    ActivationEvidenceError, ActivationEvidenceStore, canonical_facts_digest,
)

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "a" * 40
TREE = "b" * 40
CHECKS = ("Contribution attribution", "Python 3.12 (ubuntu-latest)",
          "Python 3.12 (windows-latest)", "Compose and container build",
          "Secret hygiene", "Cross-process recovery probe")


def review(actor, kind):
    body = dict(actor=actor, head_sha=COMMIT, kind=kind,
                schema_version="director-activation-review/v1", verdict="ACCEPT")
    return dict(author=dict(login=actor), state="APPROVED", authorAssociation="COLLABORATOR",
                body=json.dumps(body, separators=(",", ":")))


def pull():
    return dict(headRefOid=COMMIT, author=dict(login="producer"),
                reviews=[review("security-reviewer", "security"),
                         review("independent-reviewer", "independent_review")],
                statusCheckRollup=[dict(name=name, status="COMPLETED", conclusion="SUCCESS",
                    detailsUrl="https://github.com/example/repo/actions/runs/123/job/456")
                    for name in CHECKS])


@unittest.skipUnless(shutil.which("pwsh"), "PowerShell is required")
class AcceptanceRequestToolTests(unittest.TestCase):
    def run_tool(self, evidence=None, extra="", commit=None):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "pull.json").write_text(json.dumps(evidence or pull()), encoding="utf-8")
            (root / "commit.json").write_text(json.dumps(commit or dict(sha=COMMIT, tree=dict(sha=TREE))), encoding="utf-8")
            script = ROOT / "scripts/windows-langgraph-primary-control.ps1"
            # Intercept the actual external boundary, never offer fixture facts to production.
            harness = """
$ErrorActionPreference = 'Stop'
function global:docker { throw 'FORBIDDEN_DOCKER' }
function global:git { throw 'FORBIDDEN_GIT' }
function global:gh {
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'api') {
        if ($args[2] -cne 'github.com') { throw 'WRONG_HOST' }
        Get-Content -LiteralPath '__COMMIT__' -Raw
    } elseif ($args[0] -eq 'pr') {
        if ($args[4] -cne 'https://github.com/example/repo') { throw 'WRONG_REPOSITORY' }
        Get-Content -LiteralPath '__PULL__' -Raw
    } else { throw 'FORBIDDEN_COMMAND' }
}
try {
    & '__SCRIPT__' -Action PrepareAcceptance -CandidateCommit '__SHA__' -GitHubRepository example/repo -PullRequestNumber 98 -SecurityReviewerAllowlistJson '["security-reviewer"]' -IndependentReviewerAllowlistJson '["independent-reviewer"]' __EXTRA__
} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }
"""
            for key, value in dict(COMMIT=root / "commit.json", PULL=root / "pull.json",
                                   SCRIPT=script, SHA=COMMIT, EXTRA=extra).items():
                harness = harness.replace("__" + key + "__", str(value).replace("'", "''") if key != "EXTRA" else value)
            result = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-Command", harness],
                                    capture_output=True, text=True, timeout=300)
            return result

    def test_unsigned_request_matches_existing_canonical_contract_without_runtime_inputs(self):
        result = self.run_tool()
        self.assertEqual(0, result.returncode, result.stderr)
        unsigned = json.loads(result.stdout)
        self.assertEqual({"facts", "facts_digest", "pull_request_number", "repository", "version"}, set(unsigned))
        self.assertEqual(TREE, unsigned["facts"]["candidate_tree"])
        self.assertEqual(COMMIT, unsigned["facts"]["candidate_commit"])
        self.assertEqual(canonical_facts_digest(unsigned["facts"]), unsigned["facts_digest"])
        self.assertEqual([123], unsigned["facts"]["ci_run_ids"])
        with self.assertRaises(ActivationEvidenceError):
            ActivationEvidenceStore._validate_acceptance_artifact_contract(unsigned)

    def test_invalid_gitHub_evidence_never_emits_a_request(self):
        cases = []
        item = pull(); item["headRefOid"] = "c" * 40; cases.append(item)
        item = pull(); item["statusCheckRollup"][0]["conclusion"] = "FAILURE"; cases.append(item)
        item = pull(); item["reviews"][0]["state"] = "COMMENTED"; cases.append(item)
        item = pull(); item["reviews"][0]["authorAssociation"] = "NONE"; cases.append(item)
        item = pull(); item["reviews"][0]["body"] += " extra prose"; cases.append(item)
        item = pull(); item["author"]["login"] = "security-reviewer"; cases.append(item)
        for item in cases:
            with self.subTest(evidence=item):
                result = self.run_tool(item)
                self.assertNotEqual(0, result.returncode)
                self.assertEqual("", result.stdout.strip())

    def test_caller_cannot_supply_runtime_environment_or_fake_tree(self):
        result = self.run_tool(extra="-EnvironmentFile 'does-not-exist.env'")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("accepts no runtime", result.stderr)
        self.assertEqual("", result.stdout.strip())
        result = self.run_tool(commit=dict(sha="c" * 40, tree=dict(sha=TREE)))
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("", result.stdout.strip())


if __name__ == "__main__":
    unittest.main()

# [[OpenAI Codex | GPT-6 | 2026-09-06 | 07:55 UTC | END]]
# [[Claude Code | Claude Opus 5 | 2026-09-09 | 03:13 UTC | END]]
