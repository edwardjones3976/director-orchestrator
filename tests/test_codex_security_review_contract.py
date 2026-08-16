from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "codex-security-review.yml"


class CodexSecurityReviewContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_review_is_label_and_main_branch_scoped(self):
        self.assertIn("pull_request_target:", self.workflow)
        self.assertIn("types: [labeled]", self.workflow)
        self.assertIn("github.event.label.name == 'codex-security-review'", self.workflow)
        self.assertIn("github.event.pull_request.base.ref == 'main'", self.workflow)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository",
            self.workflow,
        )

    def test_untrusted_pr_checkout_cannot_persist_credentials(self):
        self.assertIn(
            "ref: refs/pull/${{ github.event.pull_request.number }}/merge",
            self.workflow,
        )
        self.assertIn("persist-credentials: false", self.workflow)

    def test_codex_runs_in_a_read_only_bounded_sandbox(self):
        self.assertRegex(self.workflow, r"openai/codex-action@[0-9a-f]{40}")
        self.assertIn("sandbox: read-only", self.workflow)
        self.assertIn("safety-strategy: drop-sudo", self.workflow)
        self.assertIn('allow-users: "edwardjones3976"', self.workflow)
        self.assertIn("EDGE_ORACLE_OPENAI_API_KEY", self.workflow)
        self.assertIn("Model output is advisory and never authorizes merge", self.workflow)

    def test_only_publisher_receives_comment_permissions(self):
        review_prefix, publish_suffix = self.workflow.split("\n  publish:\n", 1)
        self.assertNotIn("issues: write", review_prefix)
        self.assertNotIn("pull-requests: write", review_prefix)
        self.assertIn("issues: write", publish_suffix)
        self.assertIn("pull-requests: write", publish_suffix)
        self.assertIn(".replaceAll('@', '@\\u200b')", publish_suffix)


if __name__ == "__main__":
    unittest.main()
