"""The CI credential scan must not treat a hyphenated id as a secret key."""

import re
import unittest
from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
# The shipped Set A id that failed CI on #294. It contains "sk-" inside "task-".
HYPHENATED_ID = (
    "WO-work-order-packets-wed-1-wo-02-codex-task-evidence-blocker-audit-due-14-00-"
)


def credential_pattern() -> re.Pattern[str]:
    text = WORKFLOW.read_text(encoding="utf-8")
    match = re.search(r"pattern='([^']+)'", text)
    if match is None:
        raise AssertionError("secret-hygiene pattern is missing from ci.yml")
    return re.compile(match.group(1), re.MULTILINE)


class SecretHygienePatternTests(unittest.TestCase):
    def test_hyphenated_task_id_is_not_a_credential(self) -> None:
        self.assertIsNone(credential_pattern().search(HYPHENATED_ID))
        self.assertIsNone(credential_pattern().search("prefix " + HYPHENATED_ID))

    def test_shaped_keys_still_match(self) -> None:
        pattern = credential_pattern()
        openai_key = "sk-" + ("a" * 30)
        anthropic_key = "sk-ant-" + ("b" * 20)
        github_token = "ghp_" + ("c" * 30)
        for sample in (
            openai_key,
            "token=" + openai_key,
            anthropic_key,
            " " + github_token,
        ):
            with self.subTest(sample=sample[:12]):
                self.assertIsNotNone(pattern.search(sample))

    def test_key_buried_in_a_hyphenated_word_does_not_match(self) -> None:
        buried = "xxsk-" + ("d" * 30)
        self.assertIsNone(credential_pattern().search(buried))
