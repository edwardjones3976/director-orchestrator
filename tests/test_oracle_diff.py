# [[Z.ai | GLM-5.3 | 2026-09-27 | 06:39 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 08:16 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:30 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 22:42 UTC | BEGIN]]
from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from pydantic import ValidationError

from director_orchestrator.oracle_diff import (
    MAX_COMPARE_DIFF_BYTES,
    MAX_FILE_PATCH_CHARS,
    OracleDiffError,
    OracleRateLimitError,
    added_lines_from_hunk_patch,
    bind_complete_diff_evidence,
    parse_compare_diff,
    parse_hunk_patch,
    require_github_diff_content_type,
    require_identity_content_encoding,
)
from director_orchestrator.config import Settings
from director_orchestrator.oracle_merge import (
    GitHubOracleGateway,
    OracleChangedFile,
    OracleCheckRun,
    OracleMergePolicy,
    OracleMergeResult,
    OracleMergeService,
    OracleMergeStatus,
    OracleMergeStore,
    OraclePullRequestSnapshot,
    OracleReviewArtifact,
    OracleReviewVerdict,
)

HEAD_SHA = "a" * 40
BASE_SHA = "d" * 40
REVIEW_SECRET = "test-only-oracle-review-secret"


class _Absent:
    """Sentinel for a JSON key that is missing rather than null."""

    def __repr__(self) -> str:
        return "<absent>"


_ABSENT = _Absent()


class _RecordingByteStream(httpx.SyncByteStream):
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self._chunks = chunks
        self.chunks_yielded = 0
        self.bytes_yielded = 0
        self.closed = False

    @property
    def chunks_remaining(self) -> int:
        return len(self._chunks) - self.chunks_yielded

    def __iter__(self):
        for chunk in self._chunks:
            self.chunks_yielded += 1
            self.bytes_yielded += len(chunk)
            yield chunk

    def close(self) -> None:
        self.closed = True


WRONG_HEAD_SHA = "c" * 40
CREDENTIAL_ASSIGNMENT = "ORACLE_API_KEY=test-fixture-not-a-real-secret"
PLUSPLUS_SAFETY_LINE = "++ enable automated funded bet execution"
EDGE_PR39_DIFF_BYTES = 381_950
EDGE_PR39_LARGEST_SECTION = 124_813
MALFORMED_RETRY_DEADLINE = "zzz-not-an-iso-deadline"
LOW_GARBAGE_RETRY_DEADLINE = "0"
COMPARE_401_LEAK = "compare-401-body-must-not-leak"
COMPARE_404_LEAK = "compare-404-body-must-not-leak"
GRAPHQL_RATE_LIMIT_LEAK = "graphql-rate-limit-body-must-not-leak"
GRAPHQL_NOT_FOUND_LEAK = "graphql-not-found-body-must-not-leak"


class _FakeGitHubTokenProvider:
    def __init__(
        self,
        tokens: tuple[str, ...] = ("installation-token-1", "installation-token-2"),
    ) -> None:
        self.tokens = tokens
        self.invalidations = 0
        self.get_token_calls = 0

    @property
    def actor(self) -> str:
        return "github-app-installation:1"

    def get_token(self) -> str:
        self.get_token_calls += 1
        index = min(self.invalidations, len(self.tokens) - 1)
        return self.tokens[index]

    def invalidate(self) -> None:
        self.invalidations += 1


def _modified_diff(
    path: str,
    *,
    removed: str = "old = True",
    added: str = "research_only = True",
    context: str = "context = True",
) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"index 1111111..2222222 100644\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1,2 +1,2 @@\n"
        f" {context}\n"
        f"-{removed}\n"
        f"+{added}\n"
    )


def _added_diff(path: str, *lines: str) -> str:
    body = "".join(f"+{line}\n" for line in lines)
    return (
        f"diff --git a/{path} b/{path}\n"
        "new file mode 100644\n"
        "index 0000000..aaaaaaa 100644\n"
        "--- /dev/null\n"
        f"+++ b/{path}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n"
        f"{body}"
    )


def _removed_diff(path: str, *lines: str) -> str:
    body = "".join(f"-{line}\n" for line in lines)
    return (
        f"diff --git a/{path} b/{path}\n"
        "deleted file mode 100644\n"
        "index bbbbbbb..0000000 100644\n"
        f"--- a/{path}\n"
        "+++ /dev/null\n"
        f"@@ -1,{len(lines)} +0,0 @@\n"
        f"{body}"
    )


def _edited_rename_diff(old: str, new: str) -> str:
    return (
        f"diff --git a/{old} b/{new}\n"
        "similarity index 90%\n"
        f"rename from {old}\n"
        f"rename to {new}\n"
        "index 1111111..2222222 100644\n"
        f"--- a/{old}\n"
        f"+++ b/{new}\n"
        "@@ -1,2 +1,2 @@\n"
        " context = True\n"
        "-old = True\n"
        "+research_only = True\n"
    )


def _pure_rename_diff(old: str, new: str) -> str:
    return (
        f"diff --git a/{old} b/{new}\n"
        "similarity index 100%\n"
        f"rename from {old}\n"
        f"rename to {new}\n"
    )


class AddedLineExtractionTests(unittest.TestCase):
    def test_strips_exactly_one_hunk_marker(self) -> None:
        patch = "@@ -0,0 +1,2 @@\n+research_only = True\n+++ enable automated funded bet execution"
        self.assertEqual(
            added_lines_from_hunk_patch(patch),
            ("research_only = True", "++ enable automated funded bet execution"),
        )

    def test_file_headers_are_not_added_source(self) -> None:
        patch = "--- a/oracle/a.py\n+++ b/oracle/a.py\n@@ -0,0 +1 @@\n+research_only = True"
        self.assertEqual(added_lines_from_hunk_patch(patch), ("research_only = True",))


class RestHunkCompletenessTests(unittest.TestCase):
    def test_incomplete_hunk_body_is_rejected(self) -> None:
        with self.assertRaisesRegex(OracleDiffError, "truncated"):
            parse_hunk_patch("@@ -0,0 +1,2 @@\n+only_one")
        with self.assertRaisesRegex(ValidationError, "truncated"):
            OracleChangedFile(
                filename="oracle/a.py",
                status="modified",
                additions=2,
                deletions=0,
                patch="@@ -0,0 +1,2 @@\n+only_one",
            )

    def test_complete_patch_with_disagreeing_rest_counts_is_rejected(self) -> None:
        patch = "@@ -1 +1 @@\n-old\n+safe"
        parsed = parse_hunk_patch(patch)
        self.assertEqual(parsed.additions, 1)
        self.assertEqual(parsed.deletions, 1)
        self.assertEqual(parsed.added_lines, ("safe",))
        with self.assertRaisesRegex(ValidationError, "do not match REST"):
            OracleChangedFile(
                filename="oracle/a.py",
                status="modified",
                additions=2,
                deletions=1,
                patch=patch,
            )

    def test_added_lines_cap_stays_dominated_by_the_patch_char_cap(self) -> None:
        """The added_lines item cap can never bind before the patch char cap.

        added_lines is capped at MAX_FILE_PATCH_CHARS items so a complete,
        fully scanned diff is not spent as a terminal OracleDiffError. That
        item cap is a backstop, not the real bound: every added line costs at
        least a "+" and a newline inside the patch, so the densest patch the
        character cap admits still carries at most half the item cap.
        """
        self.assertEqual(
            OracleChangedFile.model_fields["added_lines"].metadata,
            OracleChangedFile.model_fields["patch"].metadata,
        )
        densest = MAX_FILE_PATCH_CHARS // 2 - 10_000
        newline = chr(10)
        patch = f"@@ -0,0 +1,{densest} @@{newline}" + f"+{newline}" * densest
        patch = patch.rstrip(newline)
        self.assertLessEqual(len(patch), MAX_FILE_PATCH_CHARS)
        changed = OracleChangedFile(
            filename="oracle/dense.py",
            status="added",
            additions=densest,
            deletions=0,
            patch=patch,
        )
        self.assertEqual(len(changed.added_lines or ()), densest)
        self.assertLess(len(changed.added_lines or ()), MAX_FILE_PATCH_CHARS)
        self.assertLessEqual(
            len(changed.added_lines or ()), (MAX_FILE_PATCH_CHARS + 1) // 2
        )

        with self.assertRaises(ValidationError):
            OracleChangedFile(
                filename="oracle/dense.py",
                status="added",
                additions=1,
                deletions=0,
                patch="+" * (MAX_FILE_PATCH_CHARS + 1),
            )

    def test_valid_multi_hunk_rest_patch_binds_exact_totals(self) -> None:
        patch = (
            "@@ -1,2 +1,2 @@\n"
            " context = True\n"
            "-old = True\n"
            "+safe = True\n"
            "@@ -10,1 +10,2 @@\n"
            " keep = True\n"
            "+also_safe = True"
        )
        parsed = parse_hunk_patch(patch)
        self.assertEqual(parsed.additions, 2)
        self.assertEqual(parsed.deletions, 1)
        self.assertEqual(parsed.added_lines, ("safe = True", "also_safe = True"))
        bound = OracleChangedFile(
            filename="oracle/a.py",
            status="modified",
            additions=2,
            deletions=1,
            patch=patch,
        )
        self.assertEqual(bound.added_lines, ["safe = True", "also_safe = True"])

    def test_header_only_rest_patch_is_rejected(self) -> None:
        patch = "--- a/oracle/a.py\n+++ b/oracle/a.py"
        with self.assertRaisesRegex(OracleDiffError, "does not prove a content change"):
            parse_hunk_patch(patch)
        with self.assertRaisesRegex(ValidationError, "does not prove a content change"):
            OracleChangedFile(
                filename="oracle/a.py",
                status="modified",
                additions=0,
                deletions=0,
                patch=patch,
            )

    def test_zero_change_hunk_rest_patch_is_rejected(self) -> None:
        patch = "@@ -0,0 +0,0 @@"
        with self.assertRaisesRegex(OracleDiffError, "does not prove a content change"):
            parse_hunk_patch(patch)
        with self.assertRaisesRegex(ValidationError, "does not prove a content change"):
            OracleChangedFile(
                filename="oracle/a.py",
                status="modified",
                additions=0,
                deletions=0,
                patch=patch,
            )


class GithubDiffContentTypeTests(unittest.TestCase):
    def test_accepts_github_diff_media_type_with_utf8_or_no_charset(self) -> None:
        require_github_diff_content_type("application/vnd.github.diff")
        require_github_diff_content_type("application/vnd.github.diff; charset=utf-8")
        require_github_diff_content_type("Application/VND.GitHub.diff; Charset=UTF-8")
        require_github_diff_content_type('application/vnd.github.diff; charset="utf-8"')
        require_github_diff_content_type('application/vnd.github.diff; charset="UTF-8"')

    def test_rejects_json_plain_text_and_unknown_parameters(self) -> None:
        for value in (
            None,
            "",
            "text/plain",
            "application/json",
            "application/vnd.github+json",
            "application/vnd.github.diff; boundary=x",
            "application/vnd.github.diff; charset=",
            "application/vnd.github.diff; charset=iso-8859-1",
            "application/vnd.github.diff; charset=utf-8; charset=utf-8",
            "application/vnd.github.diff; charset=utf-8; charset=iso-8859-1",
            "application/vnd.github.diff; charset='utf-8'",
            "application/vnd.github.diff; charset='utf-8\"",
            "application/vnd.github.diff; charset=\"utf-8'",
            "application/vnd.github.diff; charset=\"utf-8",
            "application/vnd.github.diff; charset=utf-8\"",
            r'application/vnd.github.diff; charset="utf\-8"',
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(OracleDiffError, "malformed"):
                    require_github_diff_content_type(value)


class IdentityContentEncodingTests(unittest.TestCase):
    def test_accepts_missing_or_identity_encoding(self) -> None:
        require_identity_content_encoding(None)
        require_identity_content_encoding("")
        require_identity_content_encoding("identity")
        require_identity_content_encoding("Identity")

    def test_rejects_gzip_and_mixed_encodings(self) -> None:
        for value in ("gzip", "x-gzip", "identity, gzip", "br"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(OracleDiffError, "malformed"):
                    require_identity_content_encoding(value)


class CompareDiffParserTests(unittest.TestCase):
    def test_modified_file_has_structured_source_lines(self) -> None:
        parsed = parse_compare_diff(_modified_diff("oracle/large.py"))
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].filename, "oracle/large.py")
        self.assertIsNone(parsed[0].previous_filename)
        self.assertEqual(parsed[0].status, "modified")
        self.assertEqual(parsed[0].additions, 1)
        self.assertEqual(parsed[0].deletions, 1)
        self.assertEqual(parsed[0].added_lines, ("research_only = True",))

    def test_added_modified_deleted_and_edited_rename(self) -> None:
        text = (
            _added_diff("oracle/new.py", "alpha", "beta")
            + _modified_diff("oracle/mid.py")
            + _removed_diff("oracle/gone.py", "old_one", "old_two")
            + _edited_rename_diff("oracle/old.py", "oracle/renamed.py")
        )
        parsed = parse_compare_diff(text)
        self.assertEqual([item.status for item in parsed], ["added", "modified", "removed", "renamed"])
        self.assertEqual(parsed[0].added_lines, ("alpha", "beta"))
        self.assertEqual(parsed[2].added_lines, ())
        self.assertEqual(parsed[2].deletions, 2)
        self.assertEqual(parsed[3].previous_filename, "oracle/old.py")
        self.assertEqual(parsed[3].filename, "oracle/renamed.py")

    def test_pure_100_percent_rename_has_empty_added_lines(self) -> None:
        parsed = parse_compare_diff(_pure_rename_diff("oracle/old.py", "oracle/new.py"))
        self.assertEqual(parsed[0].status, "renamed")
        self.assertEqual(parsed[0].additions, 0)
        self.assertEqual(parsed[0].deletions, 0)
        self.assertEqual(parsed[0].added_lines, ())
        self.assertEqual(parsed[0].patch, "")

    def test_deletion_only_is_complete_without_added_lines(self) -> None:
        parsed = parse_compare_diff(_removed_diff("oracle/gone.py", "old_one"))
        self.assertEqual(parsed[0].status, "removed")
        self.assertEqual(parsed[0].added_lines, ())
        self.assertEqual(parsed[0].additions, 0)

    def test_million_line_start_offset_with_small_hunk_is_complete(self) -> None:
        text = (
            "diff --git a/oracle/a.py b/oracle/a.py\n"
            "index 1111111..2222222 100644\n"
            "--- a/oracle/a.py\n"
            "+++ b/oracle/a.py\n"
            "@@ -1000000,1 +1000000,1 @@\n"
            "-old\n"
            "+x\n"
        )
        parsed = parse_compare_diff(text)
        self.assertEqual(parsed[0].added_lines, ("x",))
        self.assertEqual(parsed[0].additions, 1)
        self.assertEqual(parsed[0].deletions, 1)
        patch = parse_hunk_patch("@@ -1000000 +1000000 @@\n-old\n+x")
        self.assertEqual(patch.added_lines, ("x",))
        self.assertEqual(patch.additions, 1)
        self.assertEqual(patch.deletions, 1)

    def test_5000_digit_hunk_count_is_malformed_without_raw_digits(self) -> None:
        huge = "1" * 5_000
        seven_digit_count = "1000000"
        text = (
            "diff --git a/oracle/a.py b/oracle/a.py\n"
            "index 1111111..2222222 100644\n"
            "--- a/oracle/a.py\n"
            "+++ b/oracle/a.py\n"
            f"@@ -1 +1,{huge} @@\n"
            "+x\n"
        )
        with self.assertRaisesRegex(OracleDiffError, "malformed") as raised:
            parse_compare_diff(text)
        self.assertNotIn(huge[:32], str(raised.exception))
        with self.assertRaisesRegex(OracleDiffError, "malformed") as raised:
            parse_hunk_patch(f"@@ -1 +1,{huge} @@\n+x")
        self.assertNotIn(huge[:32], str(raised.exception))
        with self.assertRaisesRegex(OracleDiffError, "malformed"):
            parse_hunk_patch(f"@@ -1 +1,{seven_digit_count} @@\n+x")

    def test_pathological_hunk_start_is_malformed_without_raw_digits(self) -> None:
        huge = "1" * 5_000
        text = (
            "diff --git a/oracle/a.py b/oracle/a.py\n"
            "index 1111111..2222222 100644\n"
            "--- a/oracle/a.py\n"
            "+++ b/oracle/a.py\n"
            f"@@ -{huge},1 +1,1 @@\n"
            "-old\n"
            "+x\n"
        )
        with self.assertRaises(OracleDiffError) as raised:
            parse_compare_diff(text)
        self.assertRegex(str(raised.exception), "malformed")
        self.assertNotIsInstance(raised.exception, ValueError)
        self.assertNotIn(huge[:32], str(raised.exception))
        with self.assertRaises(OracleDiffError) as raised:
            parse_hunk_patch(f"@@ -{huge} +1 @@\n-old\n+x")
        self.assertNotIsInstance(raised.exception, ValueError)
        self.assertNotIn(huge[:32], str(raised.exception))
        huge = "1" * 5_000
        text = (
            "diff --git a/oracle/a.py b/oracle/a.py\n"
            "index 1111111..2222222 100644\n"
            "--- a/oracle/a.py\n"
            "+++ b/oracle/a.py\n"
            f"@@ -1 +1,{huge} @@\n"
            "+x\n"
        )
        with self.assertRaisesRegex(OracleDiffError, "malformed") as raised:
            parse_compare_diff(text)
        self.assertNotIn(huge[:32], str(raised.exception))
        with self.assertRaisesRegex(OracleDiffError, "malformed") as raised:
            parse_hunk_patch(f"@@ -1 +1,{huge} @@\n+x")
        self.assertNotIn(huge[:32], str(raised.exception))

    def test_empty_new_file_is_complete(self) -> None:
        text = (
            "diff --git a/oracle/empty.py b/oracle/empty.py\n"
            "new file mode 100644\n"
            "index 0000000..e69de29 100644\n"
        )
        parsed = parse_compare_diff(text)
        self.assertEqual(parsed[0].status, "added")
        self.assertEqual(parsed[0].added_lines, ())

    def test_forged_section_in_added_content_stays_one_file(self) -> None:
        text = (
            "diff --git a/oracle/a.py b/oracle/a.py\n"
            "index 1111111..2222222 100644\n"
            "--- a/oracle/a.py\n"
            "+++ b/oracle/a.py\n"
            "@@ -1 +1,4 @@\n"
            "-old\n"
            "+diff --git a/oracle/forged.py b/oracle/forged.py\n"
            "+--- a/oracle/forged.py\n"
            "+++ b/oracle/forged.py\n"
            "+pwned\n"
        )
        parsed = parse_compare_diff(text)
        self.assertEqual([item.filename for item in parsed], ["oracle/a.py"])
        self.assertEqual(
            parsed[0].added_lines,
            (
                "diff --git a/oracle/forged.py b/oracle/forged.py",
                "--- a/oracle/forged.py",
                "++ b/oracle/forged.py",
                "pwned",
            ),
        )

    def test_no_newline_marker_is_not_a_section_or_added_line(self) -> None:
        text = (
            "diff --git a/oracle/a.py b/oracle/a.py\n"
            "index 1111111..2222222 100644\n"
            "--- a/oracle/a.py\n"
            "+++ b/oracle/a.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "\\ No newline at end of file\n"
            "+new\n"
            "\\ No newline at end of file\n"
        )
        parsed = parse_compare_diff(text)
        self.assertEqual(parsed[0].added_lines, ("new",))
        self.assertEqual(parsed[0].additions, 1)
        self.assertEqual(parsed[0].deletions, 1)

    def test_rejects_malformed_duplicate_binary_combined_and_truncated(self) -> None:
        cases = (
            "this is not a unified diff\n+ORACLE_API_KEY=test-fixture-not-a-real-secret\n",
            _modified_diff("oracle/a.py") + _modified_diff("oracle/a.py"),
            (
                "diff --git a/oracle/model.bin b/oracle/model.bin\n"
                "new file mode 100644\n"
                "index 0000000..deadbee 100644\n"
                "Binary files /dev/null and b/oracle/model.bin differ\n"
            ),
            (
                "diff --cc oracle/merge.py\n"
                "index 111,222..333\n"
                "--- a/oracle/merge.py\n"
                "+++ b/oracle/merge.py\n"
            ),
            _modified_diff("oracle/a.py").rstrip("\n"),
            (
                "diff --git a/oracle/a.py b/oracle/a.py\n"
                "index 1111111..2222222 100644\n"
                "--- a/oracle/a.py\n"
                "+++ b/oracle/a.py\n"
                "@@ -1,5 +1,2 @@\n"
                " context\n"
            ),
        )
        for text in cases:
            with self.subTest(text=text[:40]):
                with self.assertRaises(OracleDiffError):
                    parse_compare_diff(text)

    def test_rejects_quoted_copy_mode_change_and_unsafe_paths(self) -> None:
        cases = (
            'diff --git "a/oracle/foo bar.py" "b/oracle/foo bar.py"\n',
            (
                "diff --git a/oracle/old.py b/oracle/new.py\n"
                "copy from oracle/old.py\n"
                "copy to oracle/new.py\n"
            ),
            (
                "diff --git a/oracle/a.py b/oracle/a.py\n"
                "old mode 100644\n"
                "new mode 100755\n"
            ),
            (
                "diff --git a/oracle/link b/oracle/link\n"
                "new file mode 120000\n"
                "index 0000000..aaaaaaa\n"
            ),
            (
                "diff --git a/oracle/../scheduler.py b/oracle/../scheduler.py\n"
                "index 1111111..2222222 100644\n"
                "--- a/oracle/../scheduler.py\n"
                "+++ b/oracle/../scheduler.py\n"
                "@@ -1 +1 @@\n"
                "-old\n"
                "+new\n"
            ),
            (
                "diff --git a/oracle/old.py b/oracle/new.py\n"
                "--- a/oracle/old.py\n"
                "+++ b/oracle/new.py\n"
                "@@ -1 +1 @@\n"
                "-old\n"
                "+new\n"
            ),
        )
        for text in cases:
            with self.subTest(text=text[:50]):
                with self.assertRaises(OracleDiffError):
                    parse_compare_diff(text)

    def test_error_messages_do_not_include_file_content(self) -> None:
        with self.assertRaises(OracleDiffError) as raised:
            parse_compare_diff(
                "this is not a unified diff\n+ORACLE_API_KEY=test-fixture-not-a-real-secret\n"
            )
        self.assertNotIn(CREDENTIAL_ASSIGNMENT, str(raised.exception))

    def test_production_bounds_cover_edge_pr_39(self) -> None:
        self.assertGreater(MAX_COMPARE_DIFF_BYTES, EDGE_PR39_DIFF_BYTES)
        self.assertGreater(MAX_FILE_PATCH_CHARS, EDGE_PR39_LARGEST_SECTION)

    def test_explicit_limit_rejects_without_truncation(self) -> None:
        text = _modified_diff("oracle/a.py")
        with self.assertRaisesRegex(OracleDiffError, "bounded evidence limit"):
            parse_compare_diff(text, max_bytes=len(text.encode("utf-8")) - 1)

    def test_content_bearing_section_without_index_is_rejected(self) -> None:
        text = (
            "diff --git a/oracle/a.py b/oracle/a.py\n"
            "--- a/oracle/a.py\n"
            "+++ b/oracle/a.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        with self.assertRaisesRegex(OracleDiffError, "malformed"):
            parse_compare_diff(text)

    def test_malformed_index_object_id_is_rejected(self) -> None:
        cases = (
            "index NOTHEX!!..2222222 100644\n",
            "index DEADBEE..2222222 100644\n",
            "index abc..2222222 100644\n",
        )
        for index_lines in cases:
            text = (
                "diff --git a/oracle/a.py b/oracle/a.py\n"
                f"{index_lines}"
                "--- a/oracle/a.py\n"
                "+++ b/oracle/a.py\n"
                "@@ -1 +1 @@\n"
                "-old\n"
                "+new\n"
            )
            with self.subTest(index_lines=index_lines):
                with self.assertRaisesRegex(OracleDiffError, "malformed"):
                    parse_compare_diff(text)

    def test_duplicate_index_metadata_is_rejected(self) -> None:
        text = (
            "diff --git a/oracle/a.py b/oracle/a.py\n"
            "index 1111111..2222222 100644\n"
            "index aaaa111..bbbb222 100644\n"
            "--- a/oracle/a.py\n"
            "+++ b/oracle/a.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        with self.assertRaisesRegex(OracleDiffError, "malformed"):
            parse_compare_diff(text)

    def test_edited_rename_without_similarity_is_rejected(self) -> None:
        text = (
            "diff --git a/oracle/old.py b/oracle/new.py\n"
            "rename from oracle/old.py\n"
            "rename to oracle/new.py\n"
            "index 1111111..2222222 100644\n"
            "--- a/oracle/old.py\n"
            "+++ b/oracle/new.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        with self.assertRaisesRegex(OracleDiffError, "unsupported rename"):
            parse_compare_diff(text)

    def _index_section(self, index_line: str) -> str:
        return (
            "diff --git a/oracle/a.py b/oracle/a.py\n"
            f"{index_line}"
            "--- a/oracle/a.py\n"
            "+++ b/oracle/a.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )

    def test_abbreviated_sha1_index_pair_is_accepted(self) -> None:
        parsed = parse_compare_diff(self._index_section("index 11111111..22222222 100644\n"))
        self.assertEqual(parsed[0].added_lines, ("new",))

    def test_full_sha1_index_pair_is_accepted(self) -> None:
        parsed = parse_compare_diff(
            self._index_section(f"index {'a' * 40}..{'b' * 40} 100644\n")
        )
        self.assertEqual(parsed[0].added_lines, ("new",))

    def test_64_hex_index_object_id_is_rejected(self) -> None:
        with self.assertRaisesRegex(OracleDiffError, "malformed"):
            parse_compare_diff(
                self._index_section(f"index {'a' * 64}..{'b' * 64} 100644\n")
            )

    def test_mixed_sha1_and_64_hex_index_pair_is_rejected(self) -> None:
        with self.assertRaisesRegex(OracleDiffError, "malformed"):
            parse_compare_diff(
                self._index_section(f"index {'a' * 40}..{'b' * 64} 100644\n")
            )


class BindCompleteDiffTests(unittest.TestCase):
    def test_exact_rest_agreement_binds_every_file_from_diff(self) -> None:
        rest = [
            {
                "filename": "oracle/small.py",
                "status": "modified",
                "additions": 1,
                "deletions": 1,
                "patch": "@@ -1,2 +1,2 @@\n context = True\n-old = True\n+REST_ONLY = True",
            },
            {
                "filename": "oracle/large.py",
                "status": "modified",
                "additions": 1,
                "deletions": 1,
                "patch": None,
            },
        ]
        parsed = parse_compare_diff(
            _modified_diff("oracle/small.py", added="from_diff = True")
            + _modified_diff("oracle/large.py", added="fallback_only = True")
        )
        bound = bind_complete_diff_evidence(rest, parsed)
        self.assertEqual(bound[0].added_lines, ("from_diff = True",))
        self.assertEqual(bound[1].added_lines, ("fallback_only = True",))
        self.assertNotIn("REST_ONLY = True", bound[0].added_lines)

    def test_path_status_and_count_mismatches_reject(self) -> None:
        parsed = parse_compare_diff(_modified_diff("oracle/a.py"))
        cases = (
            [{"filename": "oracle/b.py", "status": "modified", "additions": 1, "deletions": 1}],
            [{"filename": "oracle/a.py", "status": "added", "additions": 1, "deletions": 1}],
            [{"filename": "oracle/a.py", "status": "modified", "additions": 5, "deletions": 1}],
            [
                {"filename": "oracle/a.py", "status": "modified", "additions": 1, "deletions": 1},
                {"filename": "oracle/b.py", "status": "modified", "additions": 1, "deletions": 1},
            ],
        )
        for rest in cases:
            with self.subTest(rest=rest):
                with self.assertRaises(OracleDiffError):
                    bind_complete_diff_evidence(rest, parsed)

    def test_rest_additions_and_deletions_must_be_present_non_bool_integers(self) -> None:
        parsed = parse_compare_diff(_modified_diff("oracle/a.py"))
        cases = (
            {"filename": "oracle/a.py", "status": "modified", "deletions": 1},
            {"filename": "oracle/a.py", "status": "modified", "additions": 1},
            {
                "filename": "oracle/a.py",
                "status": "modified",
                "additions": True,
                "deletions": 1,
            },
            {
                "filename": "oracle/a.py",
                "status": "modified",
                "additions": 1,
                "deletions": False,
            },
            {
                "filename": "oracle/a.py",
                "status": "modified",
                "additions": "1",
                "deletions": 1,
            },
            {
                "filename": "oracle/a.py",
                "status": "modified",
                "additions": 1.9,
                "deletions": 1,
            },
            {
                "filename": "oracle/a.py",
                "status": "modified",
                "additions": -1,
                "deletions": 1,
            },
        )
        for rest in cases:
            with self.subTest(rest=rest):
                with self.assertRaisesRegex(OracleDiffError, "malformed"):
                    bind_complete_diff_evidence([rest], parsed)

    def test_scalar_rest_file_list_is_malformed_binding(self) -> None:
        parsed = parse_compare_diff(_modified_diff("oracle/a.py"))
        with self.assertRaisesRegex(OracleDiffError, "malformed"):
            bind_complete_diff_evidence(["oracle/a.py"], parsed)


class GitHubCompleteDiffGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.requests: list[httpx.Request] = []
        self.pull = {
            "title": "Bounded Oracle change",
            "body": "Only Oracle paths change.",
            "state": "open",
            "draft": False,
            "mergeable": True,
            "merged": False,
            "changed_files": 1,
            "base": {"ref": "edge-oracle/base", "sha": BASE_SHA},
            "head": {
                "ref": "oracle/test",
                "sha": HEAD_SHA,
                "repo": {"full_name": "edwardjones3976/edge_engine"},
            },
            "user": {"login": "cursor-agent"},
        }
        self.files = [
            {
                "filename": "oracle/large.py",
                "status": "modified",
                "additions": 1,
                "deletions": 1,
                "patch": None,
            }
        ]
        self.compare_diff = _modified_diff("oracle/large.py", added="research_only = True")
        self.wrong_head_diff = _modified_diff("oracle/large.py", added="wrong_head_only = True")
        self.pull_reads = 0
        self.file_reads = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        accept = request.headers.get("accept", "")
        if "/rules/branches/" in path:
            return httpx.Response(200, json=[])
        if "/branches/" in path:
            return httpx.Response(200, json={"name": self.pull["base"]["ref"],
                "commit": {"sha": self.pull["base"]["sha"]}, "protected": False})
        if request.method == "GET" and path.endswith("/pulls/23") and "diff" in accept:
            return httpx.Response(200, text=self.wrong_head_diff)
        if request.method == "GET" and path.endswith("/pulls/23"):
            self.pull_reads += 1
            return httpx.Response(200, json=self.pull)
        if request.method == "GET" and path.endswith("/pulls/23/files"):
            self.file_reads += 1
            return httpx.Response(200, json=self.files)
        if request.method == "GET" and "/compare/" in path:
            self.assertEqual(accept, "application/vnd.github.diff")
            self.assertEqual(request.headers.get("accept-encoding"), "identity")
            return httpx.Response(
                200,
                text=self.compare_diff,
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
        return httpx.Response(404)

    def gateway(self, **kwargs) -> GitHubOracleGateway:
        return GitHubOracleGateway(
            "repo-scoped-token",
            transport=httpx.MockTransport(self.handler),
            **kwargs,
        )

    def test_missing_rest_patch_uses_compare_diff_for_complete_evidence(self) -> None:
        snapshot = self.gateway().snapshot("edwardjones3976/edge_engine", 23)
        self.assertEqual(snapshot.changed_files[0].patch is None, False)
        self.assertEqual(snapshot.changed_files[0].added_lines, ["research_only = True"])
        compare_requests = [item for item in self.requests if "/compare/" in item.url.path]
        self.assertEqual(len(compare_requests), 1)
        self.assertEqual(
            compare_requests[0].url.path,
            f"/repos/edwardjones3976/edge_engine/compare/{BASE_SHA}...{HEAD_SHA}",
        )
        self.assertEqual(self.pull_reads, 2)
        self.assertEqual(self.file_reads, 2)

    def test_number_addressed_diff_from_another_head_cannot_pass(self) -> None:
        snapshot = self.gateway().snapshot("edwardjones3976/edge_engine", 23)
        self.assertEqual(snapshot.changed_files[0].added_lines, ["research_only = True"])
        self.assertNotIn("wrong_head_only = True", snapshot.changed_files[0].added_lines or [])
        diff_pulls = [
            item
            for item in self.requests
            if item.url.path.endswith("/pulls/23") and "diff" in item.headers.get("accept", "")
        ]
        self.assertEqual(diff_pulls, [])

    def test_binds_every_rest_file_from_compare_diff(self) -> None:
        self.pull["changed_files"] = 2
        self.files = [
            {
                "filename": "oracle/small.py",
                "status": "modified",
                "additions": 1,
                "deletions": 1,
                "patch": "@@ -1,2 +1,2 @@\n context = True\n-old = True\n+REST_ONLY = True",
            },
            {
                "filename": "oracle/large.py",
                "status": "modified",
                "additions": 1,
                "deletions": 1,
                "patch": None,
            },
        ]
        self.compare_diff = _modified_diff("oracle/small.py", added="from_diff = True") + _modified_diff(
            "oracle/large.py", added="fallback_only = True"
        )
        snapshot = self.gateway().snapshot("edwardjones3976/edge_engine", 23)
        self.assertEqual(snapshot.changed_files[0].added_lines, ["from_diff = True"])
        self.assertEqual(snapshot.changed_files[1].added_lines, ["fallback_only = True"])

    def test_incomplete_rest_hunk_still_binds_exact_sha_compare(self) -> None:
        self.files = [
            {
                "filename": "oracle/large.py",
                "status": "modified",
                "additions": 1,
                "deletions": 1,
                "patch": "@@ -0,0 +1,2 @@\n+only_one",
            }
        ]
        snapshot = self.gateway().snapshot("edwardjones3976/edge_engine", 23)
        self.assertEqual(snapshot.changed_files[0].added_lines, ["research_only = True"])
        self.assertTrue(any("/compare/" in item.url.path for item in self.requests))

    def test_complete_rest_patch_still_binds_exact_sha_compare(self) -> None:
        self.files[0]["patch"] = (
            "@@ -1,2 +1,2 @@\n context = True\n-old = True\n+REST_ONLY = True"
        )
        snapshot = self.gateway().snapshot("edwardjones3976/edge_engine", 23)
        self.assertEqual(snapshot.changed_files[0].added_lines, ["research_only = True"])
        self.assertNotIn("REST_ONLY = True", snapshot.changed_files[0].added_lines or [])
        compare_requests = [item for item in self.requests if "/compare/" in item.url.path]
        self.assertEqual(len(compare_requests), 1)
        self.assertEqual(
            compare_requests[0].url.path,
            f"/repos/edwardjones3976/edge_engine/compare/{BASE_SHA}...{HEAD_SHA}",
        )

    def test_rest_counts_that_disagree_with_compare_fail_closed(self) -> None:
        self.files = [
            {
                "filename": "oracle/large.py",
                "status": "modified",
                "additions": 2,
                "deletions": 1,
                "patch": "@@ -1 +1 @@\n-old\n+safe",
            }
        ]
        with self.assertRaisesRegex(OracleDiffError, "does not match the complete compare diff"):
            self.gateway().snapshot("edwardjones3976/edge_engine", 23)
        self.assertTrue(any("/compare/" in item.url.path for item in self.requests))

    def test_valid_multi_hunk_compare_binds_exact_totals(self) -> None:
        self.files = [
            {
                "filename": "oracle/a.py",
                "status": "modified",
                "additions": 2,
                "deletions": 1,
                "patch": (
                    "@@ -1,2 +1,2 @@\n"
                    " context = True\n"
                    "-old = True\n"
                    "+safe = True\n"
                    "@@ -10,1 +10,2 @@\n"
                    " keep = True\n"
                    "+also_safe = True"
                ),
            }
        ]
        self.compare_diff = (
            "diff --git a/oracle/a.py b/oracle/a.py\n"
            "index 1111111..2222222 100644\n"
            "--- a/oracle/a.py\n"
            "+++ b/oracle/a.py\n"
            "@@ -1,2 +1,2 @@\n"
            " context = True\n"
            "-old = True\n"
            "+safe = True\n"
            "@@ -10,1 +10,2 @@\n"
            " keep = True\n"
            "+also_safe = True\n"
        )
        snapshot = self.gateway().snapshot("edwardjones3976/edge_engine", 23)
        self.assertEqual(snapshot.changed_files[0].added_lines, ["safe = True", "also_safe = True"])
        self.assertTrue(any("/compare/" in item.url.path for item in self.requests))

    def test_million_line_start_offset_binds_exact_sha_compare(self) -> None:
        self.files = [
            {
                "filename": "oracle/a.py",
                "status": "modified",
                "additions": 1,
                "deletions": 1,
                "patch": None,
            }
        ]
        self.compare_diff = (
            "diff --git a/oracle/a.py b/oracle/a.py\n"
            "index 1111111..2222222 100644\n"
            "--- a/oracle/a.py\n"
            "+++ b/oracle/a.py\n"
            "@@ -1000000,1 +1000000,1 @@\n"
            "-old\n"
            "+x\n"
        )
        snapshot = self.gateway().snapshot("edwardjones3976/edge_engine", 23)
        self.assertEqual(snapshot.changed_files[0].added_lines, ["x"])
        self.assertTrue(any("/compare/" in item.url.path for item in self.requests))

    def test_header_only_rest_patch_uses_exact_sha_compare_fallback(self) -> None:
        self.files[0]["patch"] = "--- a/oracle/large.py\n+++ b/oracle/large.py"
        snapshot = self.gateway().snapshot("edwardjones3976/edge_engine", 23)
        self.assertEqual(snapshot.changed_files[0].added_lines, ["research_only = True"])
        self.assertTrue(any("/compare/" in item.url.path for item in self.requests))

    def test_zero_change_hunk_rest_patch_uses_exact_sha_compare_fallback(self) -> None:
        self.files[0]["patch"] = "@@ -0,0 +0,0 @@"
        snapshot = self.gateway().snapshot("edwardjones3976/edge_engine", 23)
        self.assertEqual(snapshot.changed_files[0].added_lines, ["research_only = True"])
        self.assertTrue(any("/compare/" in item.url.path for item in self.requests))

    def test_dense_short_added_lines_under_patch_cap_bind(self) -> None:
        added = ["x"] * 20_001
        self.files = [
            {
                "filename": "oracle/dense.py",
                "status": "added",
                "additions": len(added),
                "deletions": 0,
                "patch": None,
            }
        ]
        self.compare_diff = _added_diff("oracle/dense.py", *added)
        snapshot = self.gateway().snapshot("edwardjones3976/edge_engine", 23)
        self.assertEqual(snapshot.changed_files[0].added_lines, added)
        self.assertLessEqual(len(snapshot.changed_files[0].patch or ""), MAX_FILE_PATCH_CHARS)
        self.assertTrue(any("/compare/" in item.url.path for item in self.requests))

    def test_changed_file_validation_failure_is_terminal_evidence_failure(self) -> None:
        """A snapshot-binding ValidationError stays terminal, not retryable.

        Aligning the added_lines cap with the per-file patch cap removed the
        only case that reached this mapping, so hold it with a different
        bounded field that the compare parser and REST binding both accept:
        a path longer than OracleChangedFile allows.
        """
        long_path = "oracle/" + "d" * 2_000 + ".py"
        self.files = [
            {
                "filename": long_path,
                "status": "added",
                "additions": 1,
                "deletions": 0,
                "patch": None,
            }
        ]
        self.compare_diff = _added_diff(long_path, "research_only = True")
        with self.assertRaisesRegex(OracleDiffError, "incomplete or disagrees"):
            self.gateway().snapshot("edwardjones3976/edge_engine", 23)
        self.assertTrue(any("/compare/" in item.url.path for item in self.requests))

    def test_unavailable_malformed_and_oversized_compare_diffs_reject(self) -> None:
        def not_found(_: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"message": COMPARE_404_LEAK})

        with self.assertRaises(RuntimeError) as raised:
            GitHubOracleGateway(
                "repo-scoped-token", transport=httpx.MockTransport(not_found)
            )._fetch_compare_diff("edwardjones3976/edge_engine", BASE_SHA, HEAD_SHA)
        self.assertNotIsInstance(raised.exception, OracleDiffError)
        self.assertIn("404", str(raised.exception))
        self.assertNotIn(COMPARE_404_LEAK, str(raised.exception))

        original = self.compare_diff
        self.compare_diff = '{"message":"not a diff"}'
        with self.assertRaisesRegex(OracleDiffError, "malformed"):
            self.gateway().snapshot("edwardjones3976/edge_engine", 23)

        self.compare_diff = original
        with self.assertRaisesRegex(OracleDiffError, "bounded evidence limit"):
            self.gateway(max_compare_diff_bytes=16).snapshot(
                "edwardjones3976/edge_engine", 23
            )

    def test_head_and_base_drift_after_compare_reject(self) -> None:
        drifted = dict(self.pull)
        drifted["head"] = {
            "ref": "oracle/test",
            "sha": WRONG_HEAD_SHA,
            "repo": {"full_name": "edwardjones3976/edge_engine"},
        }

        def drifting(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path.endswith("/pulls/23"):
                payload = drifted if self.pull_reads else self.pull
                self.pull_reads += 1
                return httpx.Response(200, json=payload)
            return self.handler(request)

        with self.assertRaisesRegex(RuntimeError, "identity changed"):
            GitHubOracleGateway(
                "repo-scoped-token", transport=httpx.MockTransport(drifting)
            ).snapshot("edwardjones3976/edge_engine", 23)

        self.pull_reads = 0
        drifted = dict(self.pull)
        drifted["base"] = {"ref": "main", "sha": WRONG_HEAD_SHA}

        def base_drift(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path.endswith("/pulls/23"):
                payload = drifted if self.pull_reads else self.pull
                self.pull_reads += 1
                return httpx.Response(200, json=payload)
            return self.handler(request)

        with self.assertRaisesRegex(RuntimeError, "identity changed"):
            GitHubOracleGateway(
                "repo-scoped-token", transport=httpx.MockTransport(base_drift)
            ).snapshot("edwardjones3976/edge_engine", 23)

    def test_title_or_file_set_drift_rejects(self) -> None:
        drifted = dict(self.pull)
        drifted["title"] = "changed after compare"

        def title_drift(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path.endswith("/pulls/23"):
                payload = drifted if self.pull_reads else self.pull
                self.pull_reads += 1
                return httpx.Response(200, json=payload)
            return self.handler(request)

        with self.assertRaisesRegex(RuntimeError, "identity changed"):
            GitHubOracleGateway(
                "repo-scoped-token", transport=httpx.MockTransport(title_drift)
            ).snapshot("edwardjones3976/edge_engine", 23)

    def test_empty_rest_patch_with_additions_fetches_compare(self) -> None:
        self.files[0]["patch"] = ""
        snapshot = self.gateway().snapshot("edwardjones3976/edge_engine", 23)
        self.assertEqual(snapshot.changed_files[0].added_lines, ["research_only = True"])
        self.assertTrue(any("/compare/" in item.url.path for item in self.requests))

    def test_deletion_only_empty_patch_fetches_compare(self) -> None:
        self.files = [
            {
                "filename": "oracle/gone.py",
                "status": "removed",
                "additions": 0,
                "deletions": 2,
                "patch": "",
            }
        ]
        self.compare_diff = _removed_diff("oracle/gone.py", "old_one", "old_two")
        snapshot = self.gateway().snapshot("edwardjones3976/edge_engine", 23)
        changed = snapshot.changed_files[0]
        self.assertEqual(changed.status, "removed")
        self.assertEqual(changed.added_lines, [])
        self.assertIsNotNone(changed.patch)
        self.assertTrue(any("/compare/" in item.url.path for item in self.requests))

    def test_zero_count_empty_patch_rename_is_proven_by_compare_diff(self) -> None:
        self.files = [
            {
                "filename": "oracle/new.py",
                "previous_filename": "oracle/old.py",
                "status": "renamed",
                "additions": 0,
                "deletions": 0,
                "patch": "",
            }
        ]
        self.compare_diff = _pure_rename_diff("oracle/old.py", "oracle/new.py")
        snapshot = self.gateway().snapshot("edwardjones3976/edge_engine", 23)
        changed = snapshot.changed_files[0]
        self.assertEqual(changed.status, "renamed")
        self.assertEqual(changed.previous_filename, "oracle/old.py")
        self.assertEqual(changed.added_lines, [])
        self.assertEqual(changed.patch, "")

    def test_zero_count_empty_patch_mode_only_diff_rejects(self) -> None:
        self.files = [
            {
                "filename": "oracle/a.py",
                "status": "modified",
                "additions": 0,
                "deletions": 0,
                "patch": "",
            }
        ]
        self.compare_diff = (
            "diff --git a/oracle/a.py b/oracle/a.py\n"
            "old mode 100644\n"
            "new mode 100755\n"
        )
        with self.assertRaisesRegex(OracleDiffError, "unsupported metadata"):
            self.gateway().snapshot("edwardjones3976/edge_engine", 23)

    def test_oversized_stream_stops_at_the_cap_without_consuming_the_rest(self) -> None:
        chunk = b"01234567"
        leftover_chunks = 8
        stream = _RecordingByteStream((chunk,) * (3 + leftover_chunks))

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers.get("accept"), "application/vnd.github.diff")
            self.assertEqual(request.headers.get("accept-encoding"), "identity")
            return httpx.Response(
                200,
                headers={"content-type": "application/vnd.github.diff"},
                stream=stream,
            )

        gateway = GitHubOracleGateway(
            "repo-scoped-token",
            transport=httpx.MockTransport(handler),
            max_compare_diff_bytes=16,
        )
        with self.assertRaisesRegex(OracleDiffError, "bounded evidence limit") as raised:
            gateway._request_bounded_bytes(
                "GET",
                f"/repos/edwardjones3976/edge_engine/compare/{BASE_SHA}...{HEAD_SHA}",
                max_bytes=16,
                headers={"Accept": "application/vnd.github.diff"},
            )
        self.assertNotIn(chunk.decode("ascii"), str(raised.exception))
        self.assertTrue(stream.closed)
        self.assertEqual(stream.chunks_yielded, 3)
        self.assertEqual(stream.bytes_yielded, 24)
        self.assertEqual(stream.chunks_remaining, leftover_chunks)

    def test_one_oversized_chunk_is_rejected_before_copying_past_the_cap(self) -> None:
        stream = _RecordingByteStream((b"0123456789abcdef01234567", b"leftover-not-read"))

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "application/vnd.github.diff"},
                stream=stream,
            )

        with self.assertRaisesRegex(OracleDiffError, "bounded evidence limit") as raised:
            GitHubOracleGateway(
                "repo-scoped-token",
                transport=httpx.MockTransport(handler),
                max_compare_diff_bytes=16,
            )._request_bounded_bytes(
                "GET",
                f"/repos/edwardjones3976/edge_engine/compare/{BASE_SHA}...{HEAD_SHA}",
                max_bytes=16,
                headers={"Accept": "application/vnd.github.diff"},
            )
        self.assertNotIn("0123456789abcdef", str(raised.exception))
        self.assertTrue(stream.closed)
        self.assertEqual(stream.chunks_yielded, 1)
        self.assertEqual(stream.chunks_remaining, 1)

    def test_stream_of_exactly_max_bytes_is_accepted(self) -> None:
        max_bytes = 16
        body = b"0123456789abcdef"
        self.assertEqual(len(body), max_bytes)
        stream = _RecordingByteStream((body,))

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers.get("accept"), "application/vnd.github.diff")
            self.assertEqual(request.headers.get("accept-encoding"), "identity")
            return httpx.Response(
                200,
                headers={
                    "content-type": "application/vnd.github.diff",
                    "content-length": str(max_bytes),
                },
                stream=stream,
            )

        stream = _RecordingByteStream((body,))
        collected = GitHubOracleGateway(
            "repo-scoped-token",
            transport=httpx.MockTransport(handler),
            max_compare_diff_bytes=max_bytes,
        )._request_bounded_bytes(
            "GET",
            f"/repos/edwardjones3976/edge_engine/compare/{BASE_SHA}...{HEAD_SHA}",
            max_bytes=max_bytes,
            headers={"Accept": "application/vnd.github.diff"},
        )
        self.assertEqual(collected, body)
        self.assertEqual(len(collected), max_bytes)
        self.assertTrue(stream.closed)
        self.assertEqual(stream.chunks_yielded, 1)
        self.assertEqual(stream.bytes_yielded, max_bytes)
        self.assertEqual(stream.chunks_remaining, 0)

    def test_stream_of_exactly_max_bytes_plus_one_is_rejected_before_retention(self) -> None:
        max_bytes = 16
        overflow = b"0123456789abcdefX"
        leftover = b"excess-must-not-be-read"
        self.assertEqual(len(overflow), max_bytes + 1)

        stream = _RecordingByteStream((overflow, leftover))

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "application/vnd.github.diff"},
                stream=stream,
            )

        with self.assertRaisesRegex(OracleDiffError, "bounded evidence limit") as raised:
            GitHubOracleGateway(
                "repo-scoped-token",
                transport=httpx.MockTransport(handler),
                max_compare_diff_bytes=max_bytes,
            )._request_bounded_bytes(
                "GET",
                f"/repos/edwardjones3976/edge_engine/compare/{BASE_SHA}...{HEAD_SHA}",
                max_bytes=max_bytes,
                headers={"Accept": "application/vnd.github.diff"},
            )
        self.assertNotIn(overflow.decode("ascii"), str(raised.exception))
        self.assertNotIn("excess-must-not-be-read", str(raised.exception))
        self.assertTrue(stream.closed)
        self.assertEqual(stream.chunks_yielded, 1)
        self.assertEqual(stream.bytes_yielded, max_bytes + 1)
        self.assertEqual(stream.chunks_remaining, 1)

        declared = _RecordingByteStream((overflow, leftover))

        def declared_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={
                    "content-type": "application/vnd.github.diff",
                    "content-length": str(max_bytes + 1),
                },
                stream=declared,
            )

        with self.assertRaisesRegex(OracleDiffError, "bounded evidence limit") as declared_raised:
            GitHubOracleGateway(
                "repo-scoped-token",
                transport=httpx.MockTransport(declared_handler),
                max_compare_diff_bytes=max_bytes,
            )._request_bounded_bytes(
                "GET",
                f"/repos/edwardjones3976/edge_engine/compare/{BASE_SHA}...{HEAD_SHA}",
                max_bytes=max_bytes,
                headers={"Accept": "application/vnd.github.diff"},
            )
        self.assertNotIn(overflow.decode("ascii"), str(declared_raised.exception))
        self.assertNotIn("excess-must-not-be-read", str(declared_raised.exception))
        self.assertTrue(declared.closed)
        self.assertEqual(declared.chunks_yielded, 0)
        self.assertEqual(declared.bytes_yielded, 0)
        self.assertEqual(declared.chunks_remaining, 2)

    def test_http_206_prefix_is_terminal_incomplete_evidence(self) -> None:
        prefix = (
            "diff --git a/oracle/empty.py b/oracle/empty.py\n"
            "new file mode 100644\n"
            "index 0000000..deadbee"
        )

        def partial(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(
                206,
                text=prefix + "\n",
                headers={"content-type": "application/vnd.github.diff"},
            )

        with self.assertRaisesRegex(OracleDiffError, "incomplete") as raised:
            GitHubOracleGateway(
                "repo-scoped-token", transport=httpx.MockTransport(partial)
            )._fetch_compare_diff("edwardjones3976/edge_engine", BASE_SHA, HEAD_SHA)
        self.assertNotIn("deadbee", str(raised.exception))
        self.assertNotIn(prefix, str(raised.exception))

        self.requests.clear()

        def snapshot_partial(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and "/compare/" in request.url.path:
                return httpx.Response(
                    206,
                    text=prefix + "\n",
                    headers={"content-type": "application/vnd.github.diff"},
                )
            return self.handler(request)

        with self.assertRaisesRegex(OracleDiffError, "incomplete"):
            GitHubOracleGateway(
                "repo-scoped-token", transport=httpx.MockTransport(snapshot_partial)
            ).snapshot("edwardjones3976/edge_engine", 23)
        parsed = parse_compare_diff(prefix + "\n")
        self.assertEqual(parsed[0].status, "added")
        self.assertEqual(parsed[0].added_lines, ())

    def test_changed_files_must_be_a_non_bool_integer_count(self) -> None:
        cases = (
            {"omit": True},
            {"changed_files": True},
            {"changed_files": False},
            {"changed_files": "1"},
            {"changed_files": -1},
            {"changed_files": 2},
        )
        for case in cases:
            with self.subTest(case=case):
                self.requests.clear()
                pull = dict(self.pull)
                if case.get("omit"):
                    pull.pop("changed_files")
                else:
                    pull["changed_files"] = case["changed_files"]
                self.pull = pull
                with self.assertRaisesRegex(OracleDiffError, "file enumeration is incomplete"):
                    self.gateway().snapshot("edwardjones3976/edge_engine", 23)
                self.assertFalse(any("/compare/" in item.url.path for item in self.requests))
        self.pull = {
            **self.pull,
            "changed_files": 1,
            "title": "Bounded Oracle change",
        }

    def test_missing_rest_filename_or_status_is_malformed_evidence(self) -> None:
        cases = (
            {"status": "modified", "additions": 1, "deletions": 1, "patch": None},
            {"filename": "oracle/large.py", "additions": 1, "deletions": 1, "patch": None},
        )
        for item in cases:
            with self.subTest(item=item):
                self.requests.clear()
                self.files = [item]
                with self.assertRaisesRegex(OracleDiffError, "malformed"):
                    self.gateway().snapshot("edwardjones3976/edge_engine", 23)
                self.assertFalse(any("/compare/" in item.url.path for item in self.requests))

    def test_scalar_rest_file_list_is_malformed_evidence(self) -> None:
        self.files = ["oracle/a.py"]
        with self.assertRaisesRegex(OracleDiffError, "malformed") as raised:
            self.gateway().snapshot("edwardjones3976/edge_engine", 23)
        self.assertNotIsInstance(raised.exception, AttributeError)
        self.assertNotIn("oracle/a.py", str(raised.exception))
        self.assertFalse(any("/compare/" in item.url.path for item in self.requests))

    def test_compare_content_type_must_be_github_diff_media_type(self) -> None:
        def plain_text(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text=_modified_diff("oracle/a.py"),
                headers={"content-type": "text/plain"},
            )

        with self.assertRaisesRegex(OracleDiffError, "malformed"):
            GitHubOracleGateway(
                "repo-scoped-token",
                transport=httpx.MockTransport(plain_text),
            )._request_bounded_bytes(
                "GET",
                f"/repos/edwardjones3976/edge_engine/compare/{BASE_SHA}...{HEAD_SHA}",
                max_bytes=MAX_COMPARE_DIFF_BYTES,
                headers={"Accept": "application/vnd.github.diff"},
            )

        def charset_ok(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=b"diff --git a/oracle/a.py b/oracle/a.py\n",
                headers={"content-type": "application/vnd.github.diff; charset=utf-8"},
            )

        body = GitHubOracleGateway(
            "repo-scoped-token",
            transport=httpx.MockTransport(charset_ok),
        )._request_bounded_bytes(
            "GET",
            f"/repos/edwardjones3976/edge_engine/compare/{BASE_SHA}...{HEAD_SHA}",
            max_bytes=MAX_COMPARE_DIFF_BYTES,
            headers={"Accept": "application/vnd.github.diff"},
        )
        self.assertTrue(body.startswith(b"diff --git "))

        def latin1(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=_modified_diff("oracle/a.py").encode("utf-8"),
                headers={"content-type": "application/vnd.github.diff; charset=iso-8859-1"},
            )

        with self.assertRaisesRegex(OracleDiffError, "malformed"):
            GitHubOracleGateway(
                "repo-scoped-token",
                transport=httpx.MockTransport(latin1),
            )._request_bounded_bytes(
                "GET",
                f"/repos/edwardjones3976/edge_engine/compare/{BASE_SHA}...{HEAD_SHA}",
                max_bytes=MAX_COMPARE_DIFF_BYTES,
                headers={"Accept": "application/vnd.github.diff"},
            )

    def test_gzip_compare_body_is_rejected_without_reading_and_sends_identity(self) -> None:
        stream = _RecordingByteStream((b"\x1f\x8bnot-a-real-gzip-payload",))

        def gzip_response(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers.get("accept-encoding"), "identity")
            return httpx.Response(
                200,
                headers={
                    "content-type": "application/vnd.github.diff",
                    "content-encoding": "gzip",
                    "content-length": "8",
                },
                stream=stream,
            )

        with self.assertRaisesRegex(OracleDiffError, "malformed") as raised:
            GitHubOracleGateway(
                "repo-scoped-token",
                transport=httpx.MockTransport(gzip_response),
            )._request_bounded_bytes(
                "GET",
                f"/repos/edwardjones3976/edge_engine/compare/{BASE_SHA}...{HEAD_SHA}",
                max_bytes=MAX_COMPARE_DIFF_BYTES,
                headers={"Accept": "application/vnd.github.diff"},
            )
        self.assertNotIn("not-a-real-gzip-payload", str(raised.exception))
        self.assertEqual(stream.chunks_yielded, 0)
        self.assertTrue(stream.closed)

    def test_compare_rate_limit_is_dedicated_and_does_not_reread(self) -> None:
        cases = (
            (429, {}),
            (403, {"retry-after": "1"}),
            (403, {"x-ratelimit-remaining": "0"}),
        )
        for status, headers in cases:
            with self.subTest(status=status, headers=headers):
                self.requests.clear()

                def throttled(request: httpx.Request, status=status, headers=headers) -> httpx.Response:
                    if request.method == "GET" and "/compare/" in request.url.path:
                        self.requests.append(request)
                        return httpx.Response(status, headers=headers)
                    return self.handler(request)

                with self.assertRaises(OracleRateLimitError) as raised:
                    GitHubOracleGateway(
                        "repo-scoped-token", transport=httpx.MockTransport(throttled)
                    ).snapshot("edwardjones3976/edge_engine", 23)
                self.assertNotIsInstance(raised.exception, OracleDiffError)
                self.assertEqual(
                    sum(1 for item in self.requests if "/compare/" in item.url.path),
                    1,
                )

    def test_compare_408_is_ordinary_transport_failure(self) -> None:
        def timed_out(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and "/compare/" in request.url.path:
                return httpx.Response(408)
            return self.handler(request)

        with self.assertRaises(RuntimeError) as raised:
            GitHubOracleGateway(
                "repo-scoped-token", transport=httpx.MockTransport(timed_out)
            ).snapshot("edwardjones3976/edge_engine", 23)
        self.assertNotIsInstance(raised.exception, OracleRateLimitError)
        self.assertNotIsInstance(raised.exception, OracleDiffError)
        self.assertIn("408", str(raised.exception))

    def test_static_token_compare_401_is_authentication_failure(self) -> None:
        def unauthorized(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and "/compare/" in request.url.path:
                self.requests.append(request)
                return httpx.Response(401, json={"message": COMPARE_401_LEAK})
            return self.handler(request)

        with self.assertRaisesRegex(RuntimeError, "authentication failed") as raised:
            GitHubOracleGateway(
                "repo-scoped-token", transport=httpx.MockTransport(unauthorized)
            ).snapshot("edwardjones3976/edge_engine", 23)
        self.assertNotIsInstance(raised.exception, OracleDiffError)
        self.assertNotIn(COMPARE_401_LEAK, str(raised.exception))
        self.assertEqual(
            sum(1 for item in self.requests if "/compare/" in item.url.path),
            1,
        )

    def test_app_compare_401_refreshes_once_then_authentication_fails(self) -> None:
        provider = _FakeGitHubTokenProvider()

        def unauthorized(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and "/compare/" in request.url.path:
                self.requests.append(request)
                return httpx.Response(401, json={"message": COMPARE_401_LEAK})
            return self.handler(request)

        with self.assertRaisesRegex(RuntimeError, "authentication failed") as raised:
            GitHubOracleGateway(
                token_provider=provider,
                transport=httpx.MockTransport(unauthorized),
            ).snapshot("edwardjones3976/edge_engine", 23)
        self.assertNotIsInstance(raised.exception, OracleDiffError)
        self.assertEqual(provider.invalidations, 1)
        self.assertEqual(provider.get_token_calls, 4)
        self.assertEqual(
            sum(1 for item in self.requests if "/compare/" in item.url.path),
            2,
        )
        self.assertNotIn(COMPARE_401_LEAK, str(raised.exception))
        self.assertEqual(
            [
                item.headers.get("authorization")
                for item in self.requests
                if "/compare/" in item.url.path
            ],
            ["Bearer installation-token-1", "Bearer installation-token-2"],
        )

    def test_compare_404_is_ordinary_transport_failure(self) -> None:
        def not_found(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and "/compare/" in request.url.path:
                return httpx.Response(404, json={"message": COMPARE_404_LEAK})
            return self.handler(request)

        with self.assertRaises(RuntimeError) as raised:
            GitHubOracleGateway(
                "repo-scoped-token", transport=httpx.MockTransport(not_found)
            ).snapshot("edwardjones3976/edge_engine", 23)
        self.assertNotIsInstance(raised.exception, OracleDiffError)
        self.assertIn("404", str(raised.exception))
        self.assertNotIn(COMPARE_404_LEAK, str(raised.exception))

    def test_ordinary_permission_403_is_deterministic_evidence_failure(self) -> None:
        def forbidden(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and "/compare/" in request.url.path:
                return httpx.Response(403)
            return self.handler(request)

        with self.assertRaisesRegex(OracleDiffError, "unavailable"):
            GitHubOracleGateway(
                "repo-scoped-token", transport=httpx.MockTransport(forbidden)
            ).snapshot("edwardjones3976/edge_engine", 23)

    def test_pull_rate_limit_is_oracle_rate_limit_error(self) -> None:
        def throttled(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path.endswith("/pulls/23"):
                return httpx.Response(429, headers={"retry-after": "3600"})
            return self.handler(request)

        with self.assertRaises(OracleRateLimitError) as raised:
            GitHubOracleGateway(
                "repo-scoped-token", transport=httpx.MockTransport(throttled)
            ).snapshot("edwardjones3976/edge_engine", 23)
        self.assertNotIsInstance(raised.exception, OracleDiffError)
        self.assertFalse(any("/compare/" in item.url.path for item in self.requests))
        self.assertNotIn("3600", str(raised.exception))

    def test_post_compare_check_run_rate_limit_is_oracle_rate_limit_error(self) -> None:
        def throttled(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path.endswith("/check-runs"):
                return httpx.Response(
                    403, headers={"x-ratelimit-remaining": "0"}
                )
            return self.handler(request)

        with self.assertRaises(OracleRateLimitError) as raised:
            GitHubOracleGateway(
                "repo-scoped-token", transport=httpx.MockTransport(throttled)
            ).snapshot("edwardjones3976/edge_engine", 23)
        self.assertNotIsInstance(raised.exception, OracleDiffError)
        self.assertEqual(
            sum(1 for item in self.requests if "/compare/" in item.url.path),
            1,
        )

    def _review_thread_graphql(self, request: httpx.Request) -> bool:
        return (
            request.method == "POST"
            and request.url.path.endswith("/graphql")
            and b"markPullRequestReadyForReview" not in request.content
        )

    def test_graphql_http_200_rate_limit_types_raise_oracle_rate_limit_error(self) -> None:
        reset = str(int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()))
        cases = (
            "RATE_LIMITED",
            "RATE_LIMIT",
        )
        for error_type in cases:
            with self.subTest(error_type=error_type):
                self.requests.clear()

                def throttled(
                    request: httpx.Request, error_type=error_type
                ) -> httpx.Response:
                    if self._review_thread_graphql(request):
                        self.requests.append(request)
                        return httpx.Response(
                            200,
                            json={
                                "errors": [
                                    {
                                        "type": error_type,
                                        "message": GRAPHQL_RATE_LIMIT_LEAK,
                                    }
                                ]
                            },
                            headers={
                                "x-ratelimit-remaining": "0",
                                "x-ratelimit-reset": reset,
                            },
                        )
                    return self.handler(request)

                with self.assertRaises(OracleRateLimitError) as raised:
                    GitHubOracleGateway(
                        "repo-scoped-token", transport=httpx.MockTransport(throttled)
                    ).snapshot("edwardjones3976/edge_engine", 23)
                self.assertNotIsInstance(raised.exception, OracleDiffError)
                self.assertNotIn(GRAPHQL_RATE_LIMIT_LEAK, str(raised.exception))
                self.assertNotIn(reset, str(raised.exception))
                self.assertIsNotNone(raised.exception.retry_not_before)
                self.assertEqual(
                    sum(1 for item in self.requests if self._review_thread_graphql(item)),
                    1,
                )

    def test_graphql_http_200_exhausted_remaining_is_oracle_rate_limit_error(self) -> None:
        def throttled(request: httpx.Request) -> httpx.Response:
            if self._review_thread_graphql(request):
                self.requests.append(request)
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
                    headers={"x-ratelimit-remaining": "0", "retry-after": "3600"},
                )
            return self.handler(request)

        with self.assertRaises(OracleRateLimitError) as raised:
            GitHubOracleGateway(
                "repo-scoped-token", transport=httpx.MockTransport(throttled)
            ).snapshot("edwardjones3976/edge_engine", 23)
        self.assertNotIsInstance(raised.exception, OracleDiffError)
        self.assertNotIn("3600", str(raised.exception))
        self.assertEqual(
            sum(1 for item in self.requests if self._review_thread_graphql(item)),
            1,
        )

    def test_graphql_non_rate_limit_errors_fail_closed_without_payload_leak(self) -> None:
        def incomplete(request: httpx.Request) -> httpx.Response:
            if self._review_thread_graphql(request):
                self.requests.append(request)
                return httpx.Response(
                    200,
                    json={
                        "errors": [
                            {
                                "type": "NOT_FOUND",
                                "message": GRAPHQL_NOT_FOUND_LEAK,
                            }
                        ]
                    },
                )
            return self.handler(request)

        with self.assertRaisesRegex(
            OracleDiffError, "GitHub review-thread evidence is incomplete"
        ) as raised:
            GitHubOracleGateway(
                "repo-scoped-token", transport=httpx.MockTransport(incomplete)
            ).snapshot("edwardjones3976/edge_engine", 23)
        self.assertNotIsInstance(raised.exception, OracleRateLimitError)
        self.assertNotIn(GRAPHQL_NOT_FOUND_LEAK, str(raised.exception))
        self.assertEqual(
            sum(1 for item in self.requests if self._review_thread_graphql(item)),
            1,
        )

    def test_bool_string_fractional_and_negative_rest_counts_are_malformed(self) -> None:
        cases = (
            {"additions": True, "deletions": 1},
            {"additions": 1, "deletions": False},
            {"additions": "1", "deletions": 1},
            {"additions": 1.9, "deletions": 1},
            {"additions": -1, "deletions": 1},
            {"deletions": 1},
            {"additions": 1},
        )
        for extra in cases:
            with self.subTest(extra=extra):
                self.requests.clear()
                item = {
                    "filename": "oracle/large.py",
                    "status": "modified",
                    "patch": None,
                    **extra,
                }
                self.files = [item]
                with self.assertRaisesRegex(OracleDiffError, "malformed"):
                    self.gateway().snapshot("edwardjones3976/edge_engine", 23)
                self.assertFalse(any("/compare/" in item.url.path for item in self.requests))

    def test_compare_http_5xx_is_transport_failure_not_evidence_error(self) -> None:
        def server_error(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and "/compare/" in request.url.path:
                return httpx.Response(503)
            return self.handler(request)

        with self.assertRaises(RuntimeError) as raised:
            GitHubOracleGateway(
                "repo-scoped-token", transport=httpx.MockTransport(server_error)
            ).snapshot("edwardjones3976/edge_engine", 23)
        self.assertNotIsInstance(raised.exception, OracleDiffError)
        self.assertIn("503", str(raised.exception))

    def test_incomplete_rest_enumeration_does_not_fetch_compare(self) -> None:
        self.pull["changed_files"] = 2
        with self.assertRaisesRegex(OracleDiffError, "file enumeration is incomplete"):
            self.gateway().snapshot("edwardjones3976/edge_engine", 23)
        self.assertFalse(any("/compare/" in item.url.path for item in self.requests))

    def test_non_hex_pull_sha_is_deterministic_evidence_failure(self) -> None:
        self.pull["head"]["sha"] = "not-a-40-hex-digest"
        with self.assertRaisesRegex(OracleDiffError, "pinned 40-hex digest"):
            self.gateway().snapshot("edwardjones3976/edge_engine", 23)
        self.assertFalse(any("/compare/" in item.url.path for item in self.requests))

    def test_error_messages_do_not_include_credential_text(self) -> None:
        self.compare_diff = "not a diff\n+" + CREDENTIAL_ASSIGNMENT + "\n"
        with self.assertRaises(OracleDiffError) as raised:
            self.gateway().snapshot("edwardjones3976/edge_engine", 23)
        self.assertNotIn(CREDENTIAL_ASSIGNMENT, str(raised.exception))


class PolicyCompleteDiffScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            oracle_automerge_enabled=True,
            oracle_automerge_kill_switch=False,
            oracle_automerge_project="edge-oracle",
            oracle_automerge_repository="edwardjones3976/edge_engine",
            oracle_automerge_base_ref="edge-oracle/base",
            oracle_automerge_branch_prefix="oracle/",
            oracle_automerge_allowed_paths=("oracle/**", "docs/oracle/**", "tests/oracle/**"),
            oracle_automerge_required_checks=("ci", "security"),
            oracle_automerge_required_check_app="github-actions",
            oracle_automerge_review_secret=REVIEW_SECRET,
            oracle_automerge_claude_actors=("claude-agent",),
            oracle_automerge_cursor_actors=("cursor-agent",),
            oracle_automerge_gemini_actors=("gemini-agent",),
        )

    def artifact(self, **changes) -> OracleReviewArtifact:
        values = {
            "artifact_type": "edge_oracle_exact_sha_review/v1",
            "artifact_id": "review-artifact-1",
            "assignment_id": "assignment-1",
            "project": "edge-oracle",
            "repository": "edwardjones3976/edge_engine",
            "pull_number": 101,
            "base_ref": "edge-oracle/base",
            "base_sha": BASE_SHA,
            "head_ref": "oracle/revival-phase-1",
            "head_sha": HEAD_SHA,
            "implementation_provider": "cursor",
            "implementation_actor": "cursor-agent",
            "reviewer_provider": "claude",
            "review_run_id": "claude-review-1",
            "verdict": OracleReviewVerdict.ACCEPT,
            "summary": "Exact-SHA review accepted the bounded Oracle change.",
            "evidence": ["Tests and changed paths reviewed at the exact SHA."],
            "acceptance_checks": ["ci", "security"],
            "rollback_instructions": "Revert the merge commit and restore the prior main ref.",
            "signature": "0" * 64,
        }
        if "artifact_id" in changes and "review_run_id" not in changes:
            values["review_run_id"] = f"review-for-{changes['artifact_id']}"
        if "artifact_id" in changes and "assignment_id" not in changes:
            values["assignment_id"] = f"assignment-for-{changes['artifact_id']}"
        values.update(changes)
        return OracleReviewArtifact.model_validate(values).sign(REVIEW_SECRET)

    def snapshot(self, **changes) -> OraclePullRequestSnapshot:
        values = {
            "repository": "edwardjones3976/edge_engine",
            "pull_number": 101,
            "title": "Oracle research scaffold",
            "body": "Adds a research-only calibration component.",
            "state": "open",
            "draft": False,
            "mergeable": True,
            "merged": False,
            "base_ref": "edge-oracle/base",
            "base_protected": False,
            "base_sha": BASE_SHA,
            "head_ref": "oracle/revival-phase-1",
            "head_sha": HEAD_SHA,
            "head_repository": "edwardjones3976/edge_engine",
            "author_login": "cursor-agent",
            "changed_files": [
                OracleChangedFile(
                    filename="oracle/calibration.py",
                    status="added",
                    additions=2,
                    patch="@@ -0,0 +1,2 @@\n+research_only = True\n+funded_execution = False",
                )
            ],
            "checks": [
                OracleCheckRun(
                    run_id=1,
                    name="ci",
                    status="completed",
                    conclusion="success",
                    head_sha=HEAD_SHA,
                    app_slug="github-actions",
                ),
                OracleCheckRun(
                    run_id=2,
                    name="security",
                    status="completed",
                    conclusion="success",
                    head_sha=HEAD_SHA,
                    app_slug="github-actions",
                ),
            ],
            "blocking_review_threads": 0,
            "review_threads_complete": True,
        }
        values.update(changes)
        return OraclePullRequestSnapshot.model_validate(values)

    def assert_rejected(self, *, artifact=None, snapshot=None, reason: str) -> None:
        result = OracleMergePolicy(self.settings).evaluate(
            artifact or self.artifact(), snapshot or self.snapshot()
        )
        self.assertNotEqual(result.status, OracleMergeStatus.ELIGIBLE)
        self.assertTrue(
            any(reason in item for item in result.reasons + result.serious_safety_reasons),
            result,
        )

    def test_missing_patch_snapshot_still_rejects_without_complete_evidence(self) -> None:
        self.assert_rejected(
            snapshot=self.snapshot(
                changed_files=[
                    OracleChangedFile(
                        filename="oracle/model.bin",
                        status="added",
                        additions=1,
                        patch=None,
                    )
                ]
            ),
            reason="incomplete",
        )

    def test_complete_fallback_evidence_is_not_incomplete(self) -> None:
        result = OracleMergePolicy(self.settings).evaluate(
            self.artifact(),
            self.snapshot(
                changed_files=[
                    OracleChangedFile(
                        filename="oracle/large.py",
                        status="modified",
                        additions=1,
                        deletions=1,
                        patch="@@ -1,2 +1,2 @@\n context = True\n-old = True\n+research_only = True",
                        added_lines=["research_only = True"],
                    )
                ]
            ),
        )
        self.assertNotIn("incomplete", " ".join(result.reasons))
        self.assertEqual(result.status, OracleMergeStatus.ELIGIBLE)

    def test_credential_assignment_only_in_fallback_added_lines_is_detected(self) -> None:
        result = OracleMergePolicy(self.settings).evaluate(
            self.artifact(artifact_id="fallback-secret"),
            self.snapshot(
                changed_files=[
                    OracleChangedFile(
                        filename="oracle/large.py",
                        status="modified",
                        additions=1,
                        deletions=0,
                        patch="@@ -0,0 +1 @@\n+" + CREDENTIAL_ASSIGNMENT,
                        added_lines=[CREDENTIAL_ASSIGNMENT],
                    )
                ]
            ),
        )
        self.assertEqual(result.status, OracleMergeStatus.SERIOUS_SAFETY_STOP)
        self.assertTrue(any("credential" in item for item in result.serious_safety_reasons))
        self.assertNotIn(CREDENTIAL_ASSIGNMENT, json.dumps(result.model_dump(mode="json")))

    def test_plusplus_source_line_reaches_serious_safety_regex(self) -> None:
        result = OracleMergePolicy(self.settings).evaluate(
            self.artifact(artifact_id="plusplus-safety"),
            self.snapshot(
                changed_files=[
                    OracleChangedFile(
                        filename="oracle/large.py",
                        status="modified",
                        additions=1,
                        deletions=0,
                        patch="@@ -0,0 +1 @@\n+" + PLUSPLUS_SAFETY_LINE,
                        added_lines=[PLUSPLUS_SAFETY_LINE],
                    )
                ]
            ),
        )
        self.assertEqual(result.status, OracleMergeStatus.SERIOUS_SAFETY_STOP)
        self.assertTrue(any("funded wagering" in item for item in result.serious_safety_reasons))

    def test_deletion_only_complete_evidence_is_not_an_added_line_false_negative(self) -> None:
        result = OracleMergePolicy(self.settings).evaluate(
            self.artifact(artifact_id="deletion-complete"),
            self.snapshot(
                changed_files=[
                    OracleChangedFile(
                        filename="oracle/gone.py",
                        status="removed",
                        additions=0,
                        deletions=1,
                        patch="@@ -1 +0,0 @@\n-old_one",
                        added_lines=[],
                    )
                ]
            ),
        )
        self.assertNotIn("incomplete", " ".join(result.reasons))
        self.assertEqual(result.status, OracleMergeStatus.SERIOUS_SAFETY_STOP)
        self.assertIn("material deletion", result.serious_safety_reasons)

    def test_policy_never_reparses_patch_prefixes(self) -> None:
        hidden = "enable automated funded bet execution"

        class _File:
            added_lines = ["research_only = True"]
            patch = "@@ -0,0 +1 @@\n+" + hidden

        class _Snapshot:
            changed_files = [_File()]

        self.assertEqual(
            OracleMergePolicy._added_patch_text(_Snapshot()),
            "research_only = True",
        )


class FileEnumerationContractTests(unittest.TestCase):
    def test_changed_files_rejects_missing_bool_string_negative_and_mismatch(self) -> None:
        files = [{"filename": "oracle/a.py", "status": "added"}]
        cases = (None, True, False, "1", -1, 2)
        for raw in cases:
            with self.subTest(changed_files=raw):
                pull = {} if raw is None else {"changed_files": raw}
                with self.assertRaisesRegex(OracleDiffError, "file enumeration is incomplete"):
                    GitHubOracleGateway._require_complete_file_enumeration(pull, files)
        GitHubOracleGateway._require_complete_file_enumeration({"changed_files": 1}, files)
        GitHubOracleGateway._require_complete_file_enumeration({"changed_files": 0}, [])


class DeterministicEvidenceFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.settings = Settings(
            sqlite_path=str(Path(self.temporary.name) / "oracle.sqlite3"),
            oracle_automerge_enabled=True,
            oracle_automerge_kill_switch=False,
            oracle_automerge_project="edge-oracle",
            oracle_automerge_repository="edwardjones3976/edge_engine",
            oracle_automerge_base_ref="edge-oracle/base",
            oracle_automerge_branch_prefix="oracle/",
            oracle_automerge_allowed_paths=("oracle/**", "docs/oracle/**", "tests/oracle/**"),
            oracle_automerge_required_checks=("ci", "security"),
            oracle_automerge_required_check_app="github-actions",
            oracle_automerge_review_secret=REVIEW_SECRET,
            oracle_automerge_claude_actors=("claude-agent",),
            oracle_automerge_cursor_actors=("cursor-agent",),
            oracle_automerge_gemini_actors=("gemini-agent",),
        )
        self.artifact = OracleReviewArtifact.model_validate(
            {
                "artifact_type": "edge_oracle_exact_sha_review/v1",
                "artifact_id": "review-artifact-evidence",
                "assignment_id": "assignment-evidence",
                "project": "edge-oracle",
                "repository": "edwardjones3976/edge_engine",
                "pull_number": 101,
                "base_ref": "edge-oracle/base",
                "base_sha": BASE_SHA,
                "head_ref": "oracle/revival-phase-1",
                "head_sha": HEAD_SHA,
                "implementation_provider": "cursor",
                "implementation_actor": "cursor-agent",
                "reviewer_provider": "claude",
                "review_run_id": "claude-review-evidence",
                "verdict": OracleReviewVerdict.ACCEPT,
                "summary": "Exact-SHA review accepted the bounded Oracle change.",
                "evidence": ["Tests and changed paths reviewed at the exact SHA."],
                "acceptance_checks": ["ci", "security"],
                "rollback_instructions": "Revert the merge commit and restore the prior main ref.",
                "signature": "0" * 64,
            }
        ).sign(REVIEW_SECRET)

    def test_deterministic_evidence_failure_is_not_a_transient_read(self) -> None:
        class _GitHub:
            def __init__(self) -> None:
                self.snapshot_calls = 0

            def snapshot(self, repository: str, pull_number: int):
                self.snapshot_calls += 1
                raise OracleDiffError("complete compare diff contains a binary file")

            def merge(self, *args, **kwargs):
                raise AssertionError("merge must not run after evidence failure")

            def mark_ready(self, *args, **kwargs):
                raise AssertionError("ready transition must not run after evidence failure")

        github = _GitHub()
        result = OracleMergeService(
            OracleMergePolicy(self.settings),
            OracleMergeStore(self.settings.sqlite_path),
            github,
            sleep=lambda _: None,
        ).process(self.artifact)
        self.assertEqual(result.status, OracleMergeStatus.ERROR)
        self.assertEqual(github.snapshot_calls, 1)
        self.assertTrue(any("complete compare-diff evidence failed closed" in item for item in result.reasons))
        self.assertFalse(any("transient" in item for item in result.reasons))
        self.assertNotEqual(result.status, OracleMergeStatus.ELIGIBLE)

    def test_transport_timeout_stays_on_the_bounded_transient_path(self) -> None:
        class _GitHub:
            def __init__(self) -> None:
                self.snapshot_calls = 0

            def snapshot(self, repository: str, pull_number: int):
                self.snapshot_calls += 1
                raise TimeoutError("transient 504")

            def merge(self, *args, **kwargs):
                raise AssertionError("merge must not run after transport failure")

            def mark_ready(self, *args, **kwargs):
                raise AssertionError("ready transition must not run after transport failure")

        github = _GitHub()
        result = OracleMergeService(
            OracleMergePolicy(self.settings),
            OracleMergeStore(self.settings.sqlite_path),
            github,
            sleep=lambda _: None,
        ).process(self.artifact)
        self.assertEqual(result.status, OracleMergeStatus.ERROR)
        self.assertEqual(github.snapshot_calls, 2)
        self.assertTrue(any("transient read failures" in item for item in result.reasons))
        self.assertTrue(any("remains retryable" in item for item in result.reasons))

    def _stub_github(self, exc: Exception):
        class _GitHub:
            def __init__(self) -> None:
                self.snapshot_calls = 0
                self.merge_calls = 0
                self.ready_calls = 0

            def snapshot(self, repository: str, pull_number: int):
                self.snapshot_calls += 1
                raise exc

            def merge(self, *args, **kwargs):
                self.merge_calls += 1
                raise AssertionError("merge must not run after snapshot failure")

            def mark_ready(self, *args, **kwargs):
                self.ready_calls += 1
                raise AssertionError("ready transition must not run after snapshot failure")

        return _GitHub()

    def _process(self, github) -> OracleMergeResult:
        return OracleMergeService(
            OracleMergePolicy(self.settings),
            OracleMergeStore(self.settings.sqlite_path),
            github,
            sleep=lambda _: None,
        ).process(self.artifact)

    def _attempt_row(self) -> tuple[str, str | None]:
        connection = sqlite3.connect(self.settings.sqlite_path)
        try:
            return connection.execute(
                "SELECT status, result_json FROM oracle_merge_attempts WHERE artifact_id = ?",
                (self.artifact.artifact_id,),
            ).fetchone()
        finally:
            connection.close()

    def test_rate_limited_compare_releases_after_one_snapshot_and_never_eligible(self) -> None:
        github = self._stub_github(OracleRateLimitError("GitHub API GET /compare is rate limited"))
        result = self._process(github)
        self.assertEqual(result.status, OracleMergeStatus.ERROR)
        self.assertEqual(github.snapshot_calls, 1)
        self.assertEqual(github.merge_calls, 0)
        self.assertEqual(github.ready_calls, 0)
        self.assertTrue(any("rate limit" in item for item in result.reasons))
        self.assertTrue(any("remains retryable" in item for item in result.reasons))
        self.assertFalse(any("transient read failures" in item for item in result.reasons))
        self.assertNotEqual(result.status, OracleMergeStatus.ELIGIBLE)
        self.assertEqual(self._attempt_row(), ("RECOVERABLE", None))

    def test_compare_408_stays_on_the_bounded_read_retry_path(self) -> None:
        github = self._stub_github(
            RuntimeError("GitHub API GET /compare failed with status 408")
        )
        result = self._process(github)
        self.assertEqual(result.status, OracleMergeStatus.ERROR)
        self.assertEqual(github.snapshot_calls, 2)
        self.assertEqual(github.merge_calls, 0)
        self.assertEqual(github.ready_calls, 0)
        self.assertTrue(any("transient read failures" in item for item in result.reasons))
        self.assertTrue(any("remains retryable" in item for item in result.reasons))
        self.assertNotEqual(result.status, OracleMergeStatus.ELIGIBLE)
        self.assertEqual(self._attempt_row(), ("RECOVERABLE", None))

    def test_permission_403_consumes_evidence_and_is_never_eligible(self) -> None:
        github = self._stub_github(OracleDiffError("complete compare diff is unavailable"))
        result = self._process(github)
        self.assertEqual(result.status, OracleMergeStatus.ERROR)
        self.assertEqual(github.snapshot_calls, 1)
        self.assertEqual(github.merge_calls, 0)
        self.assertEqual(github.ready_calls, 0)
        self.assertTrue(any("complete compare-diff evidence failed closed" in item for item in result.reasons))
        self.assertFalse(any("transient" in item for item in result.reasons))
        self.assertNotEqual(result.status, OracleMergeStatus.ELIGIBLE)
        status, payload = self._attempt_row()
        self.assertNotEqual(status, "RECOVERABLE")
        self.assertIsNotNone(payload)

    def test_incomplete_enumeration_and_bad_sha_are_terminal_evidence_failures(self) -> None:
        cases = (
            OracleDiffError("GitHub file enumeration is incomplete"),
            OracleDiffError("GitHub pull request SHA is not a pinned 40-hex digest"),
        )
        for index, exc in enumerate(cases):
            with self.subTest(error=str(exc)):
                self.artifact = self.artifact.model_copy(
                    update={
                        "artifact_id": f"review-artifact-shape-{index}",
                        "assignment_id": f"assignment-shape-{index}",
                        "review_run_id": f"claude-review-shape-{index}",
                    }
                ).sign(REVIEW_SECRET)
                github = self._stub_github(exc)
                result = self._process(github)
                self.assertEqual(result.status, OracleMergeStatus.ERROR)
                self.assertEqual(github.snapshot_calls, 1)
                self.assertTrue(
                    any("complete compare-diff evidence failed closed" in item for item in result.reasons)
                )
                self.assertFalse(any("transient" in item for item in result.reasons))
                self.assertNotEqual(result.status, OracleMergeStatus.ELIGIBLE)
                status, payload = self._attempt_row()
                self.assertNotEqual(status, "RECOVERABLE")
                self.assertIsNotNone(payload)


class CompareRateLimitServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = GitHubCompleteDiffGatewayTests()
        self.harness.setUp()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.settings = Settings(
            sqlite_path=str(Path(self.temporary.name) / "oracle.sqlite3"),
            oracle_automerge_enabled=True,
            oracle_automerge_kill_switch=False,
            oracle_automerge_project="edge-oracle",
            oracle_automerge_repository="edwardjones3976/edge_engine",
            oracle_automerge_base_ref="edge-oracle/base",
            oracle_automerge_branch_prefix="oracle/",
            oracle_automerge_allowed_paths=("oracle/**", "docs/oracle/**", "tests/oracle/**"),
            oracle_automerge_required_checks=("ci", "security"),
            oracle_automerge_required_check_app="github-actions",
            oracle_automerge_review_secret=REVIEW_SECRET,
            oracle_automerge_claude_actors=("claude-agent",),
            oracle_automerge_cursor_actors=("cursor-agent",),
            oracle_automerge_gemini_actors=("gemini-agent",),
        )

    def _artifact(self, suffix: str) -> OracleReviewArtifact:
        return OracleReviewArtifact.model_validate(
            {
                "artifact_type": "edge_oracle_exact_sha_review/v1",
                "artifact_id": f"review-artifact-compare-{suffix}",
                "assignment_id": f"assignment-compare-{suffix}",
                "project": "edge-oracle",
                "repository": "edwardjones3976/edge_engine",
                "pull_number": 23,
                "base_ref": "edge-oracle/base",
                "base_sha": BASE_SHA,
                "head_ref": "oracle/test",
                "head_sha": HEAD_SHA,
                "implementation_provider": "cursor",
                "implementation_actor": "cursor-agent",
                "reviewer_provider": "claude",
                "review_run_id": f"claude-review-compare-{suffix}",
                "verdict": OracleReviewVerdict.ACCEPT,
                "summary": "Exact-SHA review accepted the bounded Oracle change.",
                "evidence": ["Tests and changed paths reviewed at the exact SHA."],
                "acceptance_checks": ["ci", "security"],
                "rollback_instructions": "Revert the merge commit and restore the prior main ref.",
                "signature": "0" * 64,
            }
        ).sign(REVIEW_SECRET)

    def _process_compare_status(self, status: int, headers: dict[str, str] | None = None) -> OracleMergeResult:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and "/compare/" in request.url.path:
                self.harness.requests.append(request)
                return httpx.Response(status, headers=headers or {})
            return self.harness.handler(request)

        return OracleMergeService(
            OracleMergePolicy(self.settings),
            OracleMergeStore(self.settings.sqlite_path),
            GitHubOracleGateway(
                "repo-scoped-token", transport=httpx.MockTransport(handler)
            ),
            sleep=lambda _: None,
        ).process(self._artifact(f"{status}-{sorted((headers or {}).items())}"))

    def _compare_calls(self) -> int:
        return sum(1 for item in self.harness.requests if "/compare/" in item.url.path)

    def _merge_or_ready_calls(self) -> int:
        return sum(
            1
            for item in self.harness.requests
            if (item.method == "PUT" and item.url.path.endswith("/merge"))
            or (
                item.method == "POST"
                and item.url.path.endswith("/graphql")
                and b"markPullRequestReadyForReview" in item.content
            )
        )

    def test_429_and_rate_limited_403_make_one_compare_call_and_stay_retryable(self) -> None:
        cases = (
            (429, {}),
            (403, {"retry-after": "1"}),
            (403, {"x-ratelimit-remaining": "0"}),
        )
        for status, headers in cases:
            with self.subTest(status=status, headers=headers):
                self.harness.requests.clear()
                result = self._process_compare_status(status, headers)
                self.assertEqual(result.status, OracleMergeStatus.ERROR)
                self.assertEqual(self._compare_calls(), 1)
                self.assertEqual(self._merge_or_ready_calls(), 0)
                self.assertTrue(any("rate limit" in item for item in result.reasons))
                self.assertTrue(any("remains retryable" in item for item in result.reasons))
                self.assertNotEqual(result.status, OracleMergeStatus.ELIGIBLE)
                connection = sqlite3.connect(self.settings.sqlite_path)
                try:
                    row = connection.execute(
                        "SELECT status, result_json FROM oracle_merge_attempts "
                        "WHERE artifact_id = ?",
                        (f"review-artifact-compare-{status}-{sorted(headers.items())}",),
                    ).fetchone()
                finally:
                    connection.close()
                self.assertEqual(row, ("RECOVERABLE", None))

    def test_permission_403_is_terminal_after_one_compare_call(self) -> None:
        result = self._process_compare_status(403)
        self.assertEqual(result.status, OracleMergeStatus.ERROR)
        self.assertEqual(self._compare_calls(), 1)
        self.assertEqual(self._merge_or_ready_calls(), 0)
        self.assertTrue(any("complete compare-diff evidence failed closed" in item for item in result.reasons))
        self.assertFalse(any("retryable" in item for item in result.reasons))
        self.assertNotEqual(result.status, OracleMergeStatus.ELIGIBLE)

    def _process_compare_json_status(
        self,
        status: int,
        payload: dict,
        *,
        suffix: str,
        token_provider: _FakeGitHubTokenProvider | None = None,
    ) -> OracleMergeResult:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and "/compare/" in request.url.path:
                self.harness.requests.append(request)
                return httpx.Response(status, json=payload)
            return self.harness.handler(request)

        if token_provider is None:
            gateway = GitHubOracleGateway(
                "repo-scoped-token", transport=httpx.MockTransport(handler)
            )
        else:
            gateway = GitHubOracleGateway(
                token_provider=token_provider,
                transport=httpx.MockTransport(handler),
            )
        return OracleMergeService(
            OracleMergePolicy(self.settings),
            OracleMergeStore(self.settings.sqlite_path),
            gateway,
            sleep=lambda _: None,
        ).process(self._artifact(suffix))

    def _assert_recoverable_transient(
        self, result: OracleMergeResult, leak: str, artifact_id: str
    ) -> None:
        self.assertEqual(result.status, OracleMergeStatus.ERROR)
        self.assertEqual(self._merge_or_ready_calls(), 0)
        self.assertTrue(any("transient read failures" in item for item in result.reasons))
        self.assertTrue(any("remains retryable" in item for item in result.reasons))
        self.assertNotEqual(result.status, OracleMergeStatus.ELIGIBLE)
        self.assertNotIn(leak, " ".join(result.reasons))
        connection = sqlite3.connect(self.settings.sqlite_path)
        try:
            row = connection.execute(
                "SELECT status, result_json FROM oracle_merge_attempts "
                "WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row, ("RECOVERABLE", None))

    def test_static_token_compare_401_releases_without_terminal_result(self) -> None:
        result = self._process_compare_json_status(
            401,
            {"message": COMPARE_401_LEAK},
            suffix="static-401",
        )
        self._assert_recoverable_transient(
            result,
            COMPARE_401_LEAK,
            "review-artifact-compare-static-401",
        )
        self.assertEqual(self._compare_calls(), 2)

    def test_app_refresh_then_401_releases_without_terminal_result(self) -> None:
        provider = _FakeGitHubTokenProvider()
        result = self._process_compare_json_status(
            401,
            {"message": COMPARE_401_LEAK},
            suffix="app-401",
            token_provider=provider,
        )
        self._assert_recoverable_transient(
            result,
            COMPARE_401_LEAK,
            "review-artifact-compare-app-401",
        )
        self.assertGreaterEqual(provider.invalidations, 1)
        self.assertEqual(self._compare_calls(), 4)
        self.assertEqual(self._merge_or_ready_calls(), 0)

    def test_compare_404_releases_without_terminal_result(self) -> None:
        result = self._process_compare_json_status(
            404,
            {"message": COMPARE_404_LEAK},
            suffix="compare-404",
        )
        self._assert_recoverable_transient(
            result,
            COMPARE_404_LEAK,
            "review-artifact-compare-compare-404",
        )
        self.assertEqual(self._compare_calls(), 2)

    def test_rate_limit_window_blocks_immediate_duplicate_process(self) -> None:
        reset = str(int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()))
        cases = (
            (429, {"retry-after": "3600"}),
            (429, {}),
            (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset}),
        )
        for status, headers in cases:
            with self.subTest(status=status, headers=headers):
                self.harness.requests.clear()
                artifact = self._artifact(f"hold-{status}-{sorted(headers.items())}")
                store = OracleMergeStore(self.settings.sqlite_path)

                def handler(request: httpx.Request) -> httpx.Response:
                    if request.method == "GET" and "/compare/" in request.url.path:
                        self.harness.requests.append(request)
                        return httpx.Response(status, headers=headers)
                    return self.harness.handler(request)

                service = OracleMergeService(
                    OracleMergePolicy(self.settings),
                    store,
                    GitHubOracleGateway(
                        "repo-scoped-token", transport=httpx.MockTransport(handler)
                    ),
                    sleep=lambda _: None,
                )
                first = service.process(artifact)
                second = service.process(artifact)
                self.assertEqual(first.status, OracleMergeStatus.ERROR)
                self.assertEqual(second.status, OracleMergeStatus.ERROR)
                self.assertEqual(self._compare_calls(), 1)
                self.assertEqual(self._merge_or_ready_calls(), 0)
                self.assertTrue(any("rate limit" in item for item in first.reasons))
                self.assertTrue(any("remains retryable" in item for item in first.reasons))
                self.assertTrue(any("rate limit" in item for item in second.reasons))
                self.assertTrue(any("deferred" in item for item in second.reasons))
                self.assertTrue(any("remains retryable" in item for item in second.reasons))
                joined = " ".join([*first.reasons, *second.reasons])
                self.assertNotIn("3600", joined)
                self.assertNotIn(reset, joined)
                connection = sqlite3.connect(self.settings.sqlite_path)
                try:
                    row = connection.execute(
                        "SELECT status, result_json, retry_not_before "
                        "FROM oracle_merge_attempts WHERE artifact_id = ?",
                        (artifact.artifact_id,),
                    ).fetchone()
                    connection.execute(
                        "UPDATE oracle_merge_attempts SET retry_not_before = ? "
                        "WHERE artifact_id = ?",
                        (
                            (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
                            artifact.artifact_id,
                        ),
                    )
                    connection.commit()
                finally:
                    connection.close()
                self.assertEqual(row[0], "RECOVERABLE")
                self.assertIsNone(row[1])
                self.assertIsNotNone(row[2])
                deadline = datetime.fromisoformat(row[2])
                self.assertIsNotNone(deadline.tzinfo)
                third = service.process(artifact)
                self.assertEqual(third.status, OracleMergeStatus.ERROR)
                self.assertEqual(self._compare_calls(), 2)
                self.assertEqual(self._merge_or_ready_calls(), 0)

    def test_post_compare_rate_limit_persists_and_blocks_immediate_retry(self) -> None:
        cases = (
            (429, {"retry-after": "3600"}),
            (403, {"retry-after": "3600"}),
            (403, {"x-ratelimit-remaining": "0"}),
        )
        for status, headers in cases:
            with self.subTest(status=status, headers=headers):
                self.harness.requests.clear()
                self.harness.pull_reads = 0
                self.harness.file_reads = 0
                artifact = self._artifact(
                    f"post-compare-{status}-{sorted(headers.items())}"
                )
                store = OracleMergeStore(self.settings.sqlite_path)

                def handler(
                    request: httpx.Request, status=status, headers=headers
                ) -> httpx.Response:
                    if request.method == "GET" and request.url.path.endswith("/check-runs"):
                        self.harness.requests.append(request)
                        return httpx.Response(status, headers=headers)
                    return self.harness.handler(request)

                service = OracleMergeService(
                    OracleMergePolicy(self.settings),
                    store,
                    GitHubOracleGateway(
                        "repo-scoped-token", transport=httpx.MockTransport(handler)
                    ),
                    sleep=lambda _: None,
                )
                first = service.process(artifact)
                first_calls = len(self.harness.requests)
                first_compares = self._compare_calls()
                self.assertEqual(first.status, OracleMergeStatus.ERROR)
                self.assertEqual(first_compares, 1)
                self.assertEqual(self._merge_or_ready_calls(), 0)
                self.assertTrue(any("rate limit" in item for item in first.reasons))
                self.assertTrue(any("remains retryable" in item for item in first.reasons))
                self.assertFalse(any("transient read failures" in item for item in first.reasons))
                self.assertNotIn("3600", " ".join(first.reasons))
                second = service.process(artifact)
                self.assertEqual(second.status, OracleMergeStatus.ERROR)
                self.assertEqual(len(self.harness.requests), first_calls)
                self.assertEqual(self._compare_calls(), 1)
                self.assertEqual(self._merge_or_ready_calls(), 0)
                self.assertTrue(any("deferred" in item for item in second.reasons))
                connection = sqlite3.connect(self.settings.sqlite_path)
                try:
                    row = connection.execute(
                        "SELECT status, result_json, retry_not_before "
                        "FROM oracle_merge_attempts WHERE artifact_id = ?",
                        (artifact.artifact_id,),
                    ).fetchone()
                    connection.execute(
                        "UPDATE oracle_merge_attempts SET retry_not_before = ? "
                        "WHERE artifact_id = ?",
                        (
                            (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
                            artifact.artifact_id,
                        ),
                    )
                    connection.commit()
                finally:
                    connection.close()
                self.assertEqual(row[0], "RECOVERABLE")
                self.assertIsNone(row[1])
                self.assertIsNotNone(row[2])
                third = service.process(artifact)
                self.assertEqual(third.status, OracleMergeStatus.ERROR)
                self.assertEqual(self._compare_calls(), 2)
                self.assertEqual(self._merge_or_ready_calls(), 0)


class GraphQLReviewThreadRateLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = GitHubCompleteDiffGatewayTests()
        self.harness.setUp()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.settings = Settings(
            sqlite_path=str(Path(self.temporary.name) / "oracle.sqlite3"),
            oracle_automerge_enabled=True,
            oracle_automerge_kill_switch=False,
            oracle_automerge_project="edge-oracle",
            oracle_automerge_repository="edwardjones3976/edge_engine",
            oracle_automerge_base_ref="edge-oracle/base",
            oracle_automerge_branch_prefix="oracle/",
            oracle_automerge_allowed_paths=("oracle/**", "docs/oracle/**", "tests/oracle/**"),
            oracle_automerge_required_checks=("ci", "security"),
            oracle_automerge_required_check_app="github-actions",
            oracle_automerge_review_secret=REVIEW_SECRET,
            oracle_automerge_claude_actors=("claude-agent",),
            oracle_automerge_cursor_actors=("cursor-agent",),
            oracle_automerge_gemini_actors=("gemini-agent",),
        )

    def _artifact(self, suffix: str) -> OracleReviewArtifact:
        return OracleReviewArtifact.model_validate(
            {
                "artifact_type": "edge_oracle_exact_sha_review/v1",
                "artifact_id": f"review-artifact-graphql-{suffix}",
                "assignment_id": f"assignment-graphql-{suffix}",
                "project": "edge-oracle",
                "repository": "edwardjones3976/edge_engine",
                "pull_number": 23,
                "base_ref": "edge-oracle/base",
                "base_sha": BASE_SHA,
                "head_ref": "oracle/test",
                "head_sha": HEAD_SHA,
                "implementation_provider": "cursor",
                "implementation_actor": "cursor-agent",
                "reviewer_provider": "claude",
                "review_run_id": f"claude-review-graphql-{suffix}",
                "verdict": OracleReviewVerdict.ACCEPT,
                "summary": "Exact-SHA review accepted the bounded Oracle change.",
                "evidence": ["Tests and changed paths reviewed at the exact SHA."],
                "acceptance_checks": ["ci", "security"],
                "rollback_instructions": "Revert the merge commit and restore the prior main ref.",
                "signature": "0" * 64,
            }
        ).sign(REVIEW_SECRET)

    def _review_thread_graphql(self, request: httpx.Request) -> bool:
        return (
            request.method == "POST"
            and request.url.path.endswith("/graphql")
            and b"markPullRequestReadyForReview" not in request.content
        )

    def _compare_calls(self) -> int:
        return sum(1 for item in self.harness.requests if "/compare/" in item.url.path)

    def _graphql_calls(self) -> int:
        return sum(1 for item in self.harness.requests if self._review_thread_graphql(item))

    def _process_review_thread_graphql(
        self,
        artifact: OracleReviewArtifact,
        payload: dict,
        headers: dict[str, str] | None = None,
    ) -> OracleMergeResult:
        def handler(request: httpx.Request) -> httpx.Response:
            if self._review_thread_graphql(request):
                self.harness.requests.append(request)
                return httpx.Response(200, json=payload, headers=headers or {})
            return self.harness.handler(request)

        return OracleMergeService(
            OracleMergePolicy(self.settings),
            OracleMergeStore(self.settings.sqlite_path),
            GitHubOracleGateway(
                "repo-scoped-token", transport=httpx.MockTransport(handler)
            ),
            sleep=lambda _: None,
        ).process(artifact)

    def test_http_200_rate_limit_holds_and_duplicate_makes_zero_github_calls(self) -> None:
        reset = str(int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()))
        cases = (
            ("RATE_LIMITED", {"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset}),
            ("RATE_LIMIT", {"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset}),
        )
        for error_type, headers in cases:
            with self.subTest(error_type=error_type):
                self.harness.requests.clear()
                artifact = self._artifact(error_type.lower())
                store = OracleMergeStore(self.settings.sqlite_path)

                def handler(
                    request: httpx.Request, error_type=error_type, headers=headers
                ) -> httpx.Response:
                    if self._review_thread_graphql(request):
                        self.harness.requests.append(request)
                        return httpx.Response(
                            200,
                            json={
                                "errors": [
                                    {
                                        "type": error_type,
                                        "message": GRAPHQL_RATE_LIMIT_LEAK,
                                    }
                                ]
                            },
                            headers=headers,
                        )
                    return self.harness.handler(request)

                service = OracleMergeService(
                    OracleMergePolicy(self.settings),
                    store,
                    GitHubOracleGateway(
                        "repo-scoped-token", transport=httpx.MockTransport(handler)
                    ),
                    sleep=lambda _: None,
                )
                first = service.process(artifact)
                first_calls = len(self.harness.requests)
                first_compares = self._compare_calls()
                first_graphql = self._graphql_calls()
                self.assertEqual(first.status, OracleMergeStatus.ERROR)
                self.assertEqual(first_compares, 1)
                self.assertEqual(first_graphql, 1)
                self.assertTrue(any("rate limit" in item for item in first.reasons))
                self.assertTrue(any("remains retryable" in item for item in first.reasons))
                self.assertFalse(any("transient read failures" in item for item in first.reasons))
                joined = " ".join(first.reasons)
                self.assertNotIn(GRAPHQL_RATE_LIMIT_LEAK, joined)
                self.assertNotIn(reset, joined)
                second = service.process(artifact)
                self.assertEqual(second.status, OracleMergeStatus.ERROR)
                self.assertEqual(len(self.harness.requests), first_calls)
                self.assertEqual(self._compare_calls(), 1)
                self.assertEqual(self._graphql_calls(), 1)
                self.assertTrue(any("deferred" in item for item in second.reasons))
                self.assertTrue(any("remains retryable" in item for item in second.reasons))
                connection = sqlite3.connect(self.settings.sqlite_path)
                try:
                    row = connection.execute(
                        "SELECT status, result_json, retry_not_before "
                        "FROM oracle_merge_attempts WHERE artifact_id = ?",
                        (artifact.artifact_id,),
                    ).fetchone()
                finally:
                    connection.close()
                self.assertEqual(row[0], "RECOVERABLE")
                self.assertIsNone(row[1])
                self.assertIsNotNone(row[2])

    def test_non_rate_limit_graphql_errors_are_terminal_without_payload_leak(self) -> None:
        artifact = self._artifact("not-found")
        result = self._process_review_thread_graphql(
            artifact,
            {
                "errors": [
                    {"type": "NOT_FOUND", "message": GRAPHQL_NOT_FOUND_LEAK},
                ]
            },
        )
        self.assertEqual(result.status, OracleMergeStatus.ERROR)
        self.assertTrue(
            any("complete compare-diff evidence failed closed" in item for item in result.reasons)
        )
        self.assertTrue(
            any("review-thread evidence is incomplete" in item for item in result.reasons)
        )
        self.assertFalse(any("retryable" in item for item in result.reasons))
        self.assertNotEqual(result.status, OracleMergeStatus.ELIGIBLE)
        self.assertNotIn(GRAPHQL_NOT_FOUND_LEAK, " ".join(result.reasons))
        connection = sqlite3.connect(self.settings.sqlite_path)
        try:
            row = connection.execute(
                "SELECT status, result_json FROM oracle_merge_attempts "
                "WHERE artifact_id = ?",
                (artifact.artifact_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertNotEqual(row[0], "RECOVERABLE")
        self.assertIsNotNone(row[1])
        first_calls = len(self.harness.requests)
        duplicate = OracleMergeService(
            OracleMergePolicy(self.settings),
            OracleMergeStore(self.settings.sqlite_path),
            GitHubOracleGateway(
                "repo-scoped-token",
                transport=httpx.MockTransport(self.harness.handler),
            ),
            sleep=lambda _: None,
        ).process(artifact)
        self.assertEqual(duplicate, result)
        self.assertEqual(len(self.harness.requests), first_calls)


class WriteMutationRateLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = GitHubCompleteDiffGatewayTests()
        self.harness.setUp()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        # Issue #306 removed the legacy no-boundary merge, so the write
        # mutations under test travel through a working standing boundary.
        # The phase 3 harness supplies it (throwaway in-memory Ed25519 keys);
        # the merge transport stays this suite's own gateway.
        from tests.test_oracle_merge import OracleMergeGateTests

        self.fixtures = OracleMergeGateTests("setUp")
        self.fixtures.setUp()
        self.addCleanup(self.fixtures.temporary.cleanup)
        self.settings = Settings(
            sqlite_path=str(Path(self.temporary.name) / "oracle.sqlite3"),
            oracle_automerge_enabled=True,
            oracle_automerge_kill_switch=False,
            oracle_automerge_project="edge-oracle",
            oracle_automerge_repository="edwardjones3976/edge_engine",
            oracle_automerge_base_ref="edge-oracle/base",
            oracle_automerge_branch_prefix="oracle/",
            oracle_automerge_allowed_paths=("oracle/**", "docs/oracle/**", "tests/oracle/**"),
            oracle_automerge_required_checks=("test",),
            oracle_automerge_required_check_app="github-actions",
            oracle_automerge_review_secret=REVIEW_SECRET,
            oracle_automerge_claude_actors=("claude-agent",),
            oracle_automerge_cursor_actors=("cursor-agent",),
            oracle_automerge_gemini_actors=("gemini-agent",),
        )

    def _artifact(self, suffix: str) -> OracleReviewArtifact:
        return OracleReviewArtifact.model_validate(
            {
                "artifact_type": "edge_oracle_exact_sha_review/v1",
                "artifact_id": f"review-artifact-write-{suffix}",
                "assignment_id": f"assignment-write-{suffix}",
                "project": "edge-oracle",
                "repository": "edwardjones3976/edge_engine",
                "pull_number": 23,
                "base_ref": "edge-oracle/base",
                "base_sha": BASE_SHA,
                "head_ref": "oracle/test",
                "head_sha": HEAD_SHA,
                "implementation_provider": "cursor",
                "implementation_actor": "cursor-agent",
                "reviewer_provider": "claude",
                "review_run_id": f"claude-review-write-{suffix}",
                "verdict": OracleReviewVerdict.ACCEPT,
                "summary": "Exact-SHA review accepted the bounded Oracle change.",
                "evidence": ["Tests and changed paths reviewed at the exact SHA."],
                "acceptance_checks": ["test"],
                "rollback_instructions": "Revert the merge commit and restore the prior main ref.",
                "signature": "0" * 64,
            }
        ).sign(REVIEW_SECRET)

    def test_merge_mutation_429_is_terminal_not_a_read_hold(self) -> None:
        artifact = self._artifact("merge-429")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path == "/user":
                self.harness.requests.append(request)
                return httpx.Response(200, json={"login": "cursor-agent"})
            if request.method == "PUT" and request.url.path.endswith("/merge"):
                self.harness.requests.append(request)
                return httpx.Response(429, headers={"retry-after": "3600"})
            return self.harness.handler(request)

        store = OracleMergeStore(self.settings.sqlite_path)
        # The same gateway now merges only through the standing boundary; a
        # 429 on the mutation is held by the boundary as an ambiguous outcome.
        service = self.fixtures.service(
            GitHubOracleGateway(
                "repo-scoped-token", transport=httpx.MockTransport(handler)
            ),
            settings=self.settings,
            store=store,
        )
        first = service.process(artifact)
        self.assertEqual(first.status, OracleMergeStatus.REJECTED)
        self.assertTrue(any("merge boundary refused or result ambiguous" in item for item in first.reasons))
        self.assertFalse(any("retryable" in item for item in first.reasons))
        self.assertEqual(
            sum(1 for item in self.harness.requests if item.url.path.endswith("/merge")),
            1,
        )
        first_calls = len(self.harness.requests)
        second = service.process(artifact)
        self.assertEqual(second, first)
        self.assertEqual(len(self.harness.requests), first_calls)
        connection = sqlite3.connect(self.settings.sqlite_path)
        try:
            row = connection.execute(
                "SELECT status, result_json, retry_not_before "
                "FROM oracle_merge_attempts WHERE artifact_id = ?",
                (artifact.artifact_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertNotEqual(row[0], "RECOVERABLE")
        self.assertIsNotNone(row[1])
        self.assertIsNone(row[2])
        self.assertNotIn("3600", " ".join(first.reasons))

    def test_merge_preflight_get_429_holds_terminal_without_mutating(self) -> None:
        artifact = self._artifact("merge-preflight-429")

        def handler(request: httpx.Request) -> httpx.Response:
            if (
                request.method == "GET"
                and request.url.path.endswith("/pulls/23")
                and self.harness.pull_reads >= 4
            ):
                self.harness.requests.append(request)
                return httpx.Response(429, headers={"retry-after": "3600"})
            return self.harness.handler(request)

        # The rate limit now surfaces inside the boundary, which holds every
        # transport failure as an ambiguous outcome: terminal, never retried,
        # and still no mutation is attempted.
        service = self.fixtures.service(
            GitHubOracleGateway(
                "repo-scoped-token", transport=httpx.MockTransport(handler)
            ),
            settings=self.settings,
            store=OracleMergeStore(self.settings.sqlite_path),
        )
        result = service.process(artifact)
        self.assertEqual(result.status, OracleMergeStatus.REJECTED)
        self.assertTrue(any("merge boundary refused or result ambiguous" in item for item in result.reasons))
        self.assertEqual(
            sum(1 for item in self.harness.requests if item.url.path.endswith("/merge")),
            0,
        )
        self.assertFalse(
            any(
                item.method == "POST"
                and item.url.path.endswith("/graphql")
                and b"markPullRequestReadyForReview" in item.content
                for item in self.harness.requests
            )
        )
        connection = sqlite3.connect(self.settings.sqlite_path)
        try:
            row = connection.execute(
                "SELECT status, result_json FROM oracle_merge_attempts "
                "WHERE artifact_id = ?",
                (artifact.artifact_id,),
            ).fetchone()
        finally:
            connection.close()
        # Held means terminal: the attempt records its verdict instead of
        # releasing the artifact for a retry.
        self.assertNotEqual(row[0], "RECOVERABLE")
        self.assertIsNotNone(row[1])


class OracleMergeStoreSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = str(Path(self.temporary.name) / "legacy-oracle.sqlite3")
        self.settings = Settings(
            sqlite_path=self.path,
            oracle_automerge_enabled=True,
            oracle_automerge_kill_switch=False,
            oracle_automerge_project="edge-oracle",
            oracle_automerge_repository="edwardjones3976/edge_engine",
            oracle_automerge_base_ref="edge-oracle/base",
            oracle_automerge_branch_prefix="oracle/",
            oracle_automerge_allowed_paths=("oracle/**", "docs/oracle/**", "tests/oracle/**"),
            oracle_automerge_required_checks=("ci", "security"),
            oracle_automerge_required_check_app="github-actions",
            oracle_automerge_review_secret=REVIEW_SECRET,
            oracle_automerge_claude_actors=("claude-agent",),
            oracle_automerge_cursor_actors=("cursor-agent",),
            oracle_automerge_gemini_actors=("gemini-agent",),
        )
        self.artifact = OracleReviewArtifact.model_validate(
            {
                "artifact_type": "edge_oracle_exact_sha_review/v1",
                "artifact_id": "review-artifact-schema-migration",
                "assignment_id": "assignment-schema-migration",
                "project": "edge-oracle",
                "repository": "edwardjones3976/edge_engine",
                "pull_number": 23,
                "base_ref": "edge-oracle/base",
                "base_sha": BASE_SHA,
                "head_ref": "oracle/test",
                "head_sha": HEAD_SHA,
                "implementation_provider": "cursor",
                "implementation_actor": "cursor-agent",
                "reviewer_provider": "claude",
                "review_run_id": "claude-review-schema-migration",
                "verdict": OracleReviewVerdict.ACCEPT,
                "summary": "Exact-SHA review accepted the bounded Oracle change.",
                "evidence": ["Tests and changed paths reviewed at the exact SHA."],
                "acceptance_checks": ["ci", "security"],
                "rollback_instructions": "Revert the merge commit and restore the prior main ref.",
                "signature": "0" * 64,
            }
        ).sign(REVIEW_SECRET)

    def _columns(self) -> set[str]:
        connection = sqlite3.connect(self.path)
        try:
            return {
                row[1]
                for row in connection.execute("PRAGMA table_info(oracle_merge_attempts)")
            }
        finally:
            connection.close()

    def test_existing_attempts_schema_gains_retry_not_before(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                """
                CREATE TABLE oracle_merge_attempts (
                    artifact_id TEXT PRIMARY KEY,
                    artifact_digest TEXT NOT NULL,
                    assignment_id TEXT NOT NULL UNIQUE,
                    review_run_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO oracle_merge_attempts VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
                (
                    self.artifact.artifact_id,
                    self.artifact.digest(),
                    self.artifact.assignment_id,
                    self.artifact.review_run_id,
                    "EVALUATING",
                    now,
                    now,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        self.assertNotIn("retry_not_before", self._columns())

        store = OracleMergeStore(self.path)
        self.assertIn("retry_not_before", self._columns())
        OracleMergeStore(self.path)
        self.assertIn("retry_not_before", self._columns())

        later = datetime.now(timezone.utc) + timedelta(hours=2)
        earlier = datetime.now(timezone.utc) + timedelta(minutes=5)
        result = OracleMergeResult(
            artifact_id=self.artifact.artifact_id,
            artifact_digest=self.artifact.digest(),
            status=OracleMergeStatus.ERROR,
            repository=self.settings.oracle_automerge_repository,
            pull_number=self.artifact.pull_number,
            reviewed_sha=self.artifact.head_sha,
            reasons=["GitHub compare-diff rate limit requires a later retry and remains retryable"],
        )
        store.release(result, retry_not_before=later)
        store.release(result, retry_not_before=earlier)
        store.release(result)
        hold = store.reserve(self.artifact)
        self.assertIsNotNone(hold)
        self.assertEqual(hold.status, OracleMergeStatus.ERROR)
        self.assertTrue(any("deferred" in item for item in hold.reasons))
        self.assertTrue(any("remains retryable" in item for item in hold.reasons))
        connection = sqlite3.connect(self.path)
        try:
            row = connection.execute(
                "SELECT status, result_json, retry_not_before "
                "FROM oracle_merge_attempts WHERE artifact_id = ?",
                (self.artifact.artifact_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row[0], "RECOVERABLE")
        self.assertIsNone(row[1])
        stored = datetime.fromisoformat(row[2])
        self.assertGreaterEqual(stored, later - timedelta(seconds=1))
        self.assertGreater(stored, earlier)

    def test_release_later_deadline_wins_across_store_instances(self) -> None:
        store = OracleMergeStore(self.path)
        now = datetime.now(timezone.utc).isoformat()
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                "INSERT INTO oracle_merge_attempts("
                "artifact_id, artifact_digest, assignment_id, review_run_id, "
                "status, result_json, created_at, updated_at, retry_not_before"
                ") VALUES (?, ?, ?, ?, ?, NULL, ?, ?, NULL)",
                (
                    self.artifact.artifact_id,
                    self.artifact.digest(),
                    self.artifact.assignment_id,
                    self.artifact.review_run_id,
                    "EVALUATING",
                    now,
                    now,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        later = datetime.now(timezone.utc) + timedelta(hours=2)
        earlier = datetime.now(timezone.utc) + timedelta(minutes=5)
        result = OracleMergeResult(
            artifact_id=self.artifact.artifact_id,
            artifact_digest=self.artifact.digest(),
            status=OracleMergeStatus.ERROR,
            repository=self.settings.oracle_automerge_repository,
            pull_number=self.artifact.pull_number,
            reviewed_sha=self.artifact.head_sha,
            reasons=["GitHub rate limit requires a later retry and remains retryable"],
        )
        started = threading.Barrier(2)
        errors: list[Exception] = []

        def release_at(deadline: datetime) -> None:
            try:
                started.wait(timeout=5)
                OracleMergeStore(self.path).release(result, retry_not_before=deadline)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=release_at, args=(later,)),
            threading.Thread(target=release_at, args=(earlier,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertTrue(all(not thread.is_alive() for thread in threads), "workers did not finish within 30s per thread")
        self.assertEqual(errors, [])
        connection = sqlite3.connect(self.path)
        try:
            row = connection.execute(
                "SELECT status, result_json, retry_not_before "
                "FROM oracle_merge_attempts WHERE artifact_id = ?",
                (self.artifact.artifact_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row[0], "RECOVERABLE")
        self.assertIsNone(row[1])
        stored = datetime.fromisoformat(row[2])
        self.assertGreaterEqual(stored, later - timedelta(seconds=1))
        self.assertGreater(stored, earlier)

    def test_concurrent_initializers_add_retry_not_before_once(self) -> None:
        self._create_legacy_attempts_table()
        barrier = threading.Barrier(2)
        errors: list[Exception] = []

        class RacingStore(OracleMergeStore):
            def _ensure_retry_not_before_column(self, connection):
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(oracle_merge_attempts)"
                    )
                }
                if "retry_not_before" not in columns:
                    barrier.wait(timeout=5)
                OracleMergeStore._ensure_retry_not_before_column(connection)

        def construct() -> None:
            try:
                RacingStore(self.path)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=construct) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertTrue(all(not thread.is_alive() for thread in threads), "workers did not finish within 30s per thread")
        self.assertEqual(errors, [])
        self.assertIn("retry_not_before", self._columns())

    def _create_legacy_attempts_table(self) -> None:
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                """
                CREATE TABLE oracle_merge_attempts (
                    artifact_id TEXT PRIMARY KEY,
                    artifact_digest TEXT NOT NULL,
                    assignment_id TEXT NOT NULL UNIQUE,
                    review_run_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.commit()
        finally:
            connection.close()


class MalformedEvidenceProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = GitHubCompleteDiffGatewayTests()
        self.harness.setUp()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.settings = Settings(
            sqlite_path=str(Path(self.temporary.name) / "oracle.sqlite3"),
            oracle_automerge_enabled=True,
            oracle_automerge_kill_switch=False,
            oracle_automerge_project="edge-oracle",
            oracle_automerge_repository="edwardjones3976/edge_engine",
            oracle_automerge_base_ref="edge-oracle/base",
            oracle_automerge_branch_prefix="oracle/",
            oracle_automerge_allowed_paths=("oracle/**", "docs/oracle/**", "tests/oracle/**"),
            oracle_automerge_required_checks=("ci", "security"),
            oracle_automerge_required_check_app="github-actions",
            oracle_automerge_review_secret=REVIEW_SECRET,
            oracle_automerge_claude_actors=("claude-agent",),
            oracle_automerge_cursor_actors=("cursor-agent",),
            oracle_automerge_gemini_actors=("gemini-agent",),
        )

    def _artifact(self, suffix: str) -> OracleReviewArtifact:
        return OracleReviewArtifact.model_validate(
            {
                "artifact_type": "edge_oracle_exact_sha_review/v1",
                "artifact_id": f"review-artifact-malformed-{suffix}",
                "assignment_id": f"assignment-malformed-{suffix}",
                "project": "edge-oracle",
                "repository": "edwardjones3976/edge_engine",
                "pull_number": 23,
                "base_ref": "edge-oracle/base",
                "base_sha": BASE_SHA,
                "head_ref": "oracle/test",
                "head_sha": HEAD_SHA,
                "implementation_provider": "cursor",
                "implementation_actor": "cursor-agent",
                "reviewer_provider": "claude",
                "review_run_id": f"claude-review-malformed-{suffix}",
                "verdict": OracleReviewVerdict.ACCEPT,
                "summary": "Exact-SHA review accepted the bounded Oracle change.",
                "evidence": ["Tests and changed paths reviewed at the exact SHA."],
                "acceptance_checks": ["ci", "security"],
                "rollback_instructions": "Revert the merge commit and restore the prior main ref.",
                "signature": "0" * 64,
            }
        ).sign(REVIEW_SECRET)

    def _process(self, artifact: OracleReviewArtifact) -> tuple[OracleMergeResult, int]:
        inner = self.harness.gateway()

        class _CountingGateway:
            def __init__(self) -> None:
                self.snapshot_calls = 0
                self.merge_calls = 0
                self.ready_calls = 0

            def snapshot(self, repository: str, pull_number: int):
                self.snapshot_calls += 1
                return inner.snapshot(repository, pull_number)

            def merge(self, *args, **kwargs):
                self.merge_calls += 1
                return inner.merge(*args, **kwargs)

            def mark_ready(self, *args, **kwargs):
                self.ready_calls += 1
                return inner.mark_ready(*args, **kwargs)

        github = _CountingGateway()
        result = OracleMergeService(
            OracleMergePolicy(self.settings),
            OracleMergeStore(self.settings.sqlite_path),
            github,
            sleep=lambda _: None,
        ).process(artifact)
        self.assertEqual(github.snapshot_calls, 1)
        self.assertEqual(github.merge_calls, 0)
        self.assertEqual(github.ready_calls, 0)
        return result, github.snapshot_calls

    def _assert_terminal_one_snapshot(self, result: OracleMergeResult) -> None:
        self.assertEqual(result.status, OracleMergeStatus.ERROR)
        self.assertTrue(
            any("complete compare-diff evidence failed closed" in item for item in result.reasons)
        )
        self.assertFalse(any("transient" in item for item in result.reasons))
        self.assertNotEqual(result.status, OracleMergeStatus.ELIGIBLE)
        connection = sqlite3.connect(self.settings.sqlite_path)
        try:
            row = connection.execute(
                "SELECT status, result_json FROM oracle_merge_attempts WHERE artifact_id = ?",
                (result.artifact_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertNotEqual(row[0], "RECOVERABLE")
        self.assertIsNotNone(row[1])

    def test_missing_rest_status_is_one_terminal_snapshot(self) -> None:
        self.harness.files = [
            {"filename": "oracle/large.py", "additions": 1, "deletions": 1, "patch": None}
        ]
        result, _ = self._process(self._artifact("missing-status"))
        self._assert_terminal_one_snapshot(result)
        self.assertEqual(
            sum(1 for item in self.harness.requests if item.url.path.endswith("/pulls/23")),
            1,
        )
        self.assertFalse(any("/compare/" in item.url.path for item in self.harness.requests))
        self.assertNotIn("oracle/large.py", " ".join(result.reasons))

    def test_missing_rest_filename_is_one_terminal_snapshot(self) -> None:
        self.harness.files = [
            {"status": "modified", "additions": 1, "deletions": 1, "patch": None}
        ]
        result, _ = self._process(self._artifact("missing-filename"))
        self._assert_terminal_one_snapshot(result)
        self.assertEqual(
            sum(1 for item in self.harness.requests if item.url.path.endswith("/pulls/23")),
            1,
        )
        self.assertFalse(any("/compare/" in item.url.path for item in self.harness.requests))

    def test_5000_digit_hunk_count_is_one_terminal_snapshot(self) -> None:
        huge = "1" * 5_000
        self.harness.compare_diff = (
            "diff --git a/oracle/large.py b/oracle/large.py\n"
            "index 1111111..2222222 100644\n"
            "--- a/oracle/large.py\n"
            "+++ b/oracle/large.py\n"
            f"@@ -1 +1,{huge} @@\n"
            "+x\n"
        )
        result, _ = self._process(self._artifact("huge-hunk"))
        self._assert_terminal_one_snapshot(result)
        self.assertEqual(
            sum(1 for item in self.harness.requests if "/compare/" in item.url.path),
            1,
        )
        self.assertNotIn(huge[:32], " ".join(result.reasons))

    def test_scalar_rest_file_list_is_one_terminal_snapshot(self) -> None:
        self.harness.files = ["oracle/a.py"]
        result, _ = self._process(self._artifact("scalar-files"))
        self._assert_terminal_one_snapshot(result)
        self.assertNotIn("oracle/a.py", " ".join(result.reasons))
        self.assertFalse(any("/compare/" in item.url.path for item in self.harness.requests))

    def test_bool_rest_additions_are_one_terminal_snapshot(self) -> None:
        self.harness.files = [
            {
                "filename": "oracle/large.py",
                "status": "modified",
                "additions": True,
                "deletions": 1,
                "patch": None,
            }
        ]
        result, _ = self._process(self._artifact("bool-additions"))
        self._assert_terminal_one_snapshot(result)
        self.assertFalse(any("/compare/" in item.url.path for item in self.harness.requests))
        self.assertEqual(result.status, OracleMergeStatus.ERROR)
        self.assertNotEqual(result.status, OracleMergeStatus.ELIGIBLE)


class MalformedRetryDeadlineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = str(Path(self.temporary.name) / "legacy-oracle.sqlite3")
        self.settings = Settings(
            sqlite_path=self.path,
            oracle_automerge_enabled=True,
            oracle_automerge_kill_switch=False,
            oracle_automerge_project="edge-oracle",
            oracle_automerge_repository="edwardjones3976/edge_engine",
            oracle_automerge_base_ref="edge-oracle/base",
            oracle_automerge_branch_prefix="oracle/",
            oracle_automerge_allowed_paths=("oracle/**", "docs/oracle/**", "tests/oracle/**"),
            oracle_automerge_required_checks=("ci", "security"),
            oracle_automerge_required_check_app="github-actions",
            oracle_automerge_review_secret=REVIEW_SECRET,
            oracle_automerge_claude_actors=("claude-agent",),
            oracle_automerge_cursor_actors=("cursor-agent",),
            oracle_automerge_gemini_actors=("gemini-agent",),
        )

    def _artifact(self, suffix: str) -> OracleReviewArtifact:
        return OracleReviewArtifact.model_validate(
            {
                "artifact_type": "edge_oracle_exact_sha_review/v1",
                "artifact_id": f"review-artifact-deadline-{suffix}",
                "assignment_id": f"assignment-deadline-{suffix}",
                "project": "edge-oracle",
                "repository": "edwardjones3976/edge_engine",
                "pull_number": 23,
                "base_ref": "edge-oracle/base",
                "base_sha": BASE_SHA,
                "head_ref": "oracle/test",
                "head_sha": HEAD_SHA,
                "implementation_provider": "cursor",
                "implementation_actor": "cursor-agent",
                "reviewer_provider": "claude",
                "review_run_id": f"claude-review-deadline-{suffix}",
                "verdict": OracleReviewVerdict.ACCEPT,
                "summary": "Exact-SHA review accepted the bounded Oracle change.",
                "evidence": ["Tests and changed paths reviewed at the exact SHA."],
                "acceptance_checks": ["ci", "security"],
                "rollback_instructions": "Revert the merge commit and restore the prior main ref.",
                "signature": "0" * 64,
            }
        ).sign(REVIEW_SECRET)

    def _create_legacy_attempts_table(self) -> None:
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                """
                CREATE TABLE oracle_merge_attempts (
                    artifact_id TEXT PRIMARY KEY,
                    artifact_digest TEXT NOT NULL,
                    assignment_id TEXT NOT NULL UNIQUE,
                    review_run_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

    def _insert_recoverable(
        self, artifact: OracleReviewArtifact, retry_not_before: str | None
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                "INSERT INTO oracle_merge_attempts("
                "artifact_id, artifact_digest, assignment_id, review_run_id, "
                "status, result_json, created_at, updated_at, retry_not_before"
                ") VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)",
                (
                    artifact.artifact_id,
                    artifact.digest(),
                    artifact.assignment_id,
                    artifact.review_run_id,
                    "RECOVERABLE",
                    now,
                    now,
                    retry_not_before,
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def _attempt_row(self, artifact_id: str) -> tuple:
        connection = sqlite3.connect(self.path)
        try:
            return connection.execute(
                "SELECT status, result_json, retry_not_before "
                "FROM oracle_merge_attempts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        finally:
            connection.close()

    def _set_deadline(self, artifact_id: str, retry_not_before: str) -> None:
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                "UPDATE oracle_merge_attempts SET retry_not_before = ? "
                "WHERE artifact_id = ?",
                (retry_not_before, artifact_id),
            )
            connection.commit()
        finally:
            connection.close()

    def _assert_deferred_hold(self, hold: OracleMergeResult, raw: str) -> None:
        self.assertEqual(hold.status, OracleMergeStatus.ERROR)
        self.assertTrue(any("deferred" in item for item in hold.reasons))
        self.assertTrue(any("remains retryable" in item for item in hold.reasons))
        self.assertNotIn(raw, " ".join(hold.reasons))
        self.assertNotEqual(hold.status, OracleMergeStatus.ELIGIBLE)

    def test_existing_schema_malformed_deadline_is_repaired(self) -> None:
        self._create_legacy_attempts_table()
        artifact = self._artifact("legacy-malformed")
        store = OracleMergeStore(self.path)
        self._insert_recoverable(artifact, MALFORMED_RETRY_DEADLINE)

        hold = store.reserve(artifact)
        self.assertIsNotNone(hold)
        self._assert_deferred_hold(hold, MALFORMED_RETRY_DEADLINE)
        status, payload, stored = self._attempt_row(artifact.artifact_id)
        self.assertEqual(status, "RECOVERABLE")
        self.assertIsNone(payload)
        deadline = datetime.fromisoformat(stored)
        self.assertIsNotNone(deadline.tzinfo)
        self.assertGreater(deadline, datetime.now(timezone.utc))
        self.assertLessEqual(
            deadline,
            datetime.now(timezone.utc) + timedelta(seconds=90),
        )
        self.assertNotEqual(stored, MALFORMED_RETRY_DEADLINE)

    def test_low_garbage_deadline_does_not_rearm_before_repair(self) -> None:
        self._create_legacy_attempts_table()
        artifact = self._artifact("low-garbage")
        store = OracleMergeStore(self.path)
        self._insert_recoverable(artifact, LOW_GARBAGE_RETRY_DEADLINE)

        class _GitHub:
            def __init__(self) -> None:
                self.snapshot_calls = 0
                self.merge_calls = 0
                self.ready_calls = 0

            def snapshot(self, repository: str, pull_number: int):
                self.snapshot_calls += 1
                raise RuntimeError("GitHub must not run while the repaired deadline is held")

            def merge(self, *args, **kwargs):
                self.merge_calls += 1
                raise AssertionError("merge must not run while the repaired deadline is held")

            def mark_ready(self, *args, **kwargs):
                self.ready_calls += 1
                raise AssertionError("ready transition must not run while the repaired deadline is held")

        github = _GitHub()
        service = OracleMergeService(
            OracleMergePolicy(self.settings),
            store,
            github,
            sleep=lambda _: None,
        )
        first = service.process(artifact)
        second = service.process(artifact)
        self._assert_deferred_hold(first, LOW_GARBAGE_RETRY_DEADLINE)
        self._assert_deferred_hold(second, LOW_GARBAGE_RETRY_DEADLINE)
        self.assertEqual(github.snapshot_calls, 0)
        self.assertEqual(github.merge_calls, 0)
        self.assertEqual(github.ready_calls, 0)
        status, payload, stored = self._attempt_row(artifact.artifact_id)
        self.assertEqual(status, "RECOVERABLE")
        self.assertIsNone(payload)
        self.assertNotEqual(stored, LOW_GARBAGE_RETRY_DEADLINE)
        datetime.fromisoformat(stored)
        self._set_deadline(
            artifact.artifact_id,
            (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        )
        third = service.process(artifact)
        self.assertEqual(github.snapshot_calls, 2)
        self.assertEqual(github.merge_calls, 0)
        self.assertEqual(github.ready_calls, 0)
        self.assertTrue(any("transient read failures" in item for item in third.reasons))
        store = OracleMergeStore(self.path)
        artifact = self._artifact("immediate-repeat")
        self._insert_recoverable(artifact, MALFORMED_RETRY_DEADLINE)
        first = store.reserve(artifact)
        stored_after_repair = self._attempt_row(artifact.artifact_id)[2]
        second = store.reserve(artifact)
        self._assert_deferred_hold(first, MALFORMED_RETRY_DEADLINE)
        self._assert_deferred_hold(second, MALFORMED_RETRY_DEADLINE)
        status, payload, stored = self._attempt_row(artifact.artifact_id)
        self.assertEqual(status, "RECOVERABLE")
        self.assertIsNone(payload)
        self.assertEqual(stored, stored_after_repair)
        self.assertEqual(
            [
                event["event_type"]
                for event in store.audit_events(artifact.artifact_id)
            ].count("retry_not_before_repaired"),
            1,
        )

    def test_malformed_deadline_due_rearms_exactly_once(self) -> None:
        store = OracleMergeStore(self.path)
        artifact = self._artifact("due-rearm")
        self._insert_recoverable(artifact, "!!!")
        hold = store.reserve(artifact)
        self._assert_deferred_hold(hold, "!!!")
        self._set_deadline(
            artifact.artifact_id,
            (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        )
        self.assertIsNone(store.reserve(artifact))
        status, payload, stored = self._attempt_row(artifact.artifact_id)
        self.assertEqual(status, "EVALUATING")
        self.assertIsNone(payload)
        self.assertIsNone(stored)
        with self.assertRaisesRegex(RuntimeError, "already being evaluated"):
            store.reserve(artifact)
        self.assertEqual(
            [
                event["event_type"]
                for event in store.audit_events(artifact.artifact_id)
            ].count("review_artifact_retried"),
            1,
        )

    def test_malformed_deadline_audit_omits_raw_value(self) -> None:
        store = OracleMergeStore(self.path)
        artifact = self._artifact("audit")
        self._insert_recoverable(artifact, MALFORMED_RETRY_DEADLINE)
        store.reserve(artifact)
        events = [
            event
            for event in store.audit_events(artifact.artifact_id)
            if event["event_type"] == "retry_not_before_repaired"
        ]
        self.assertEqual(len(events), 1)
        payload = json.dumps(events[0]["payload"])
        self.assertNotIn(MALFORMED_RETRY_DEADLINE, payload)
        self.assertNotIn("zzz", payload)
        self.assertIn("unparseable nonempty", events[0]["payload"]["reason"])

    def test_valid_future_deadline_is_not_rewritten(self) -> None:
        store = OracleMergeStore(self.path)
        artifact = self._artifact("valid-future")
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        self._insert_recoverable(artifact, future)
        hold = store.reserve(artifact)
        self._assert_deferred_hold(hold, future)
        status, payload, stored = self._attempt_row(artifact.artifact_id)
        self.assertEqual(status, "RECOVERABLE")
        self.assertIsNone(payload)
        self.assertEqual(stored, future)
        self.assertEqual(
            [
                event["event_type"]
                for event in store.audit_events(artifact.artifact_id)
            ].count("retry_not_before_repaired"),
            0,
        )

    def test_malformed_deadline_process_makes_no_github_request_until_due(self) -> None:
        store = OracleMergeStore(self.path)
        artifact = self._artifact("process-hold")
        self._insert_recoverable(artifact, MALFORMED_RETRY_DEADLINE)

        class _GitHub:
            def __init__(self) -> None:
                self.snapshot_calls = 0
                self.merge_calls = 0
                self.ready_calls = 0

            def snapshot(self, repository: str, pull_number: int):
                self.snapshot_calls += 1
                raise RuntimeError("GitHub must not run while the deadline is held")

            def merge(self, *args, **kwargs):
                self.merge_calls += 1
                raise AssertionError("merge must not run while the deadline is held")

            def mark_ready(self, *args, **kwargs):
                self.ready_calls += 1
                raise AssertionError("ready transition must not run while the deadline is held")

        github = _GitHub()
        service = OracleMergeService(
            OracleMergePolicy(self.settings),
            store,
            github,
            sleep=lambda _: None,
        )
        first = service.process(artifact)
        second = service.process(artifact)
        self._assert_deferred_hold(first, MALFORMED_RETRY_DEADLINE)
        self._assert_deferred_hold(second, MALFORMED_RETRY_DEADLINE)
        self.assertEqual(github.snapshot_calls, 0)
        self.assertEqual(github.merge_calls, 0)
        self.assertEqual(github.ready_calls, 0)
        self._set_deadline(
            artifact.artifact_id,
            (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        )
        third = service.process(artifact)
        self.assertEqual(third.status, OracleMergeStatus.ERROR)
        self.assertEqual(github.snapshot_calls, 2)
        self.assertEqual(github.merge_calls, 0)
        self.assertEqual(github.ready_calls, 0)
        self.assertTrue(any("transient read failures" in item for item in third.reasons))
        self.assertTrue(any("remains retryable" in item for item in third.reasons))
        status, payload, _stored = self._attempt_row(artifact.artifact_id)
        self.assertEqual(status, "RECOVERABLE")
        self.assertIsNone(payload)

    def test_malformed_deadline_repair_is_exclusive_across_store_instances(self) -> None:
        store = OracleMergeStore(self.path)
        artifact = self._artifact("repair-race")
        self._insert_recoverable(artifact, MALFORMED_RETRY_DEADLINE)
        barrier = threading.Barrier(2)

        class BarrierCursor:
            def __init__(self, cursor):
                self.cursor = cursor

            def fetchone(self):
                row = self.cursor.fetchone()
                if row is not None and row["retry_not_before"] == MALFORMED_RETRY_DEADLINE:
                    barrier.wait(timeout=5)
                return row

            def __getattr__(self, name):
                return getattr(self.cursor, name)

        class BarrierConnection:
            def __init__(self, connection):
                self.connection = connection

            def execute(self, sql, parameters=()):
                cursor = self.connection.execute(sql, parameters)
                if "SELECT artifact_digest, status, result_json, retry_not_before" in sql:
                    return BarrierCursor(cursor)
                return cursor

            def __getattr__(self, name):
                return getattr(self.connection, name)

        class BarrierStore(OracleMergeStore):
            @contextmanager
            def _connect(self, *, immediate: bool = False):
                with super()._connect(immediate=immediate) as connection:
                    yield BarrierConnection(connection)

        outcomes: list[OracleMergeResult | None] = []
        errors: list[Exception] = []

        def reserve_once(candidate_store: OracleMergeStore) -> None:
            try:
                outcomes.append(candidate_store.reserve(artifact))
            except Exception as exc:
                errors.append(exc)

        contenders = [BarrierStore(self.path), BarrierStore(self.path)]
        threads = [
            threading.Thread(target=reserve_once, args=(candidate,))
            for candidate in contenders
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertTrue(all(not thread.is_alive() for thread in threads), "workers did not finish within 30s per thread")
        self.assertEqual(errors, [])
        self.assertEqual(len(outcomes), 2)
        for hold in outcomes:
            self.assertIsNotNone(hold)
            self._assert_deferred_hold(hold, MALFORMED_RETRY_DEADLINE)
        status, payload, stored = self._attempt_row(artifact.artifact_id)
        self.assertEqual(status, "RECOVERABLE")
        self.assertIsNone(payload)
        datetime.fromisoformat(stored)
        self.assertNotEqual(stored, MALFORMED_RETRY_DEADLINE)
        self.assertEqual(
            [
                event["event_type"]
                for event in store.audit_events(artifact.artifact_id)
            ].count("retry_not_before_repaired"),
            1,
        )


CHECK_RUN_LEAK = "check-run-body-must-not-leak"
REVIEW_THREAD_LEAK = "review-thread-body-must-not-leak"
PULL_EVIDENCE_LEAK = "pull-evidence-body-must-not-leak"
GOOD_CHECK_RUN = {
    "id": 10,
    "name": "test",
    "status": "completed",
    "conclusion": "success",
    "head_sha": HEAD_SHA,
    "app": {"slug": "github-actions"},
    "check_suite": {"id": 11},
}


def _check_run(**overrides) -> dict:
    run = dict(GOOD_CHECK_RUN)
    for key, value in overrides.items():
        if value is _ABSENT:
            run.pop(key, None)
        else:
            run[key] = value
    return run


def _checks_page(*runs, total_count=None) -> dict:
    return {
        "total_count": len(runs) if total_count is None else total_count,
        "check_runs": list(runs),
    }


def _graphql_page(
    *,
    nodes=(),
    page_info=_ABSENT,
    review_decision="APPROVED",
    threads=_ABSENT,
    pull_request=_ABSENT,
    repository=_ABSENT,
    data=_ABSENT,
) -> dict:
    if threads is _ABSENT:
        threads = {
            "nodes": list(nodes),
            "pageInfo": {"hasNextPage": False} if page_info is _ABSENT else page_info,
        }
    if pull_request is _ABSENT:
        pull_request = {"reviewDecision": review_decision, "reviewThreads": threads}
    if repository is _ABSENT:
        repository = {"pullRequest": pull_request}
    if data is _ABSENT:
        data = {"repository": repository}
    return {"data": data}


class MalformedGitHubEvidenceBoundaryTests(unittest.TestCase):
    """Malformed GitHub evidence must be terminal, never a transient re-read.

    Every case here is durable: re-reading returns the same bytes, so classifying
    it as a transient read failure would spend the bounded read allowance on a
    fresh full pull/files/compare cycle each time and then release the reviewed
    artifact for recoverable redelivery that can never succeed. Each case must
    raise ``OracleDiffError`` inside the terminal-evidence boundary, and no case
    may echo an untrusted payload value.
    """

    def setUp(self) -> None:
        self.harness = GitHubCompleteDiffGatewayTests()
        self.harness.setUp()

    def _gateway(
        self,
        *,
        pull=_ABSENT,
        checks=_ABSENT,
        checks_text=None,
        graphql=_ABSENT,
        graphql_text=None,
    ) -> GitHubOracleGateway:
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            is_diff = "diff" in request.headers.get("accept", "")
            if pull is not _ABSENT and request.method == "GET" and path.endswith("/pulls/23") and not is_diff:
                return httpx.Response(200, json=pull)
            if request.method == "GET" and path.endswith("/check-runs"):
                if checks_text is not None:
                    return httpx.Response(
                        200, text=checks_text, headers={"Content-Type": "application/json"}
                    )
                if checks is not _ABSENT:
                    return httpx.Response(200, json=checks)
            if request.method == "POST" and path == "/graphql":
                if graphql_text is not None:
                    return httpx.Response(
                        200, text=graphql_text, headers={"Content-Type": "application/json"}
                    )
                if graphql is not _ABSENT:
                    return httpx.Response(200, json=graphql)
            return self.harness.handler(request)

        return GitHubOracleGateway(
            "repo-scoped-token", transport=httpx.MockTransport(handler)
        )

    def _assert_terminal(self, expected: str, **kwargs) -> None:
        with self.assertRaises(OracleDiffError) as caught:
            self._gateway(**kwargs).snapshot("edwardjones3976/edge_engine", 23)
        message = str(caught.exception)
        self.assertEqual(message, expected)
        for leak in (CHECK_RUN_LEAK, REVIEW_THREAD_LEAK, PULL_EVIDENCE_LEAK):
            self.assertNotIn(leak, message)

    def _assert_check_evidence_malformed(self, payload=_ABSENT, text=None) -> None:
        self._assert_terminal(
            "GitHub check-run evidence is malformed", checks=payload, checks_text=text
        )

    def _assert_review_evidence_incomplete(self, payload=_ABSENT, text=None) -> None:
        self._assert_terminal(
            "GitHub review-thread evidence is incomplete",
            graphql=payload,
            graphql_text=text,
        )

    def _assert_pull_evidence_malformed(self, pull) -> None:
        self._assert_terminal("GitHub pull request evidence is malformed", pull=pull)

    # --- check-run evidence: body shape -------------------------------------

    def test_non_object_check_run_body_is_terminal(self) -> None:
        for payload in (
            [GOOD_CHECK_RUN],
            CHECK_RUN_LEAK,
            17,
            1.5,
            True,
            None,
        ):
            with self.subTest(payload=payload):
                self._assert_check_evidence_malformed(payload)

    def test_unparseable_check_run_body_is_terminal(self) -> None:
        for text in ("{{{", "", "not json at all", '{"total_count": 1,'):
            with self.subTest(text=text):
                self._assert_check_evidence_malformed(text=text)

    def test_non_list_check_runs_is_terminal(self) -> None:
        for runs in (GOOD_CHECK_RUN, CHECK_RUN_LEAK, 1, True, None):
            with self.subTest(runs=runs):
                self._assert_check_evidence_malformed(
                    {"total_count": 1, "check_runs": runs}
                )

    def test_missing_check_runs_key_is_terminal(self) -> None:
        self._assert_check_evidence_malformed({"total_count": 0})

    def test_check_run_page_beyond_the_requested_size_is_terminal(self) -> None:
        runs = [_check_run(id=index + 1) for index in range(101)]
        self._assert_check_evidence_malformed(_checks_page(*runs))

    # --- check-run evidence: enumeration ------------------------------------

    def test_non_integer_total_count_is_terminal(self) -> None:
        for total in ("1", True, False, 1.0, [1], {"n": 1}, None, -1):
            with self.subTest(total=total):
                self._assert_check_evidence_malformed(
                    {"total_count": total, "check_runs": [GOOD_CHECK_RUN]}
                )

    def test_missing_total_count_is_terminal_incomplete_enumeration(self) -> None:
        self._assert_terminal(
            "GitHub check-run enumeration is incomplete",
            checks={"check_runs": [GOOD_CHECK_RUN]},
        )

    def test_total_count_disagreeing_with_the_page_is_terminal(self) -> None:
        for total in (0, 2, 5, 100):
            with self.subTest(total=total):
                self._assert_terminal(
                    "GitHub check-run enumeration is incomplete",
                    checks=_checks_page(GOOD_CHECK_RUN, total_count=total),
                )

    # --- check-run evidence: per-run fields ---------------------------------

    def test_check_run_that_is_not_an_object_is_terminal(self) -> None:
        for run in (CHECK_RUN_LEAK, 10, [GOOD_CHECK_RUN], None, True):
            with self.subTest(run=run):
                self._assert_check_evidence_malformed(_checks_page(run))

    def test_missing_or_wrongly_typed_run_identifier_is_terminal(self) -> None:
        for identifier in (_ABSENT, None, "10", True, 1.0, 0, -1, [10], {"id": 10}):
            with self.subTest(identifier=identifier):
                self._assert_check_evidence_malformed(
                    _checks_page(_check_run(id=identifier))
                )

    def test_missing_or_wrongly_typed_run_name_is_terminal(self) -> None:
        for name in (_ABSENT, None, "", 10, True, ["test"], "n" * 256):
            with self.subTest(name=name):
                self._assert_check_evidence_malformed(
                    _checks_page(_check_run(name=name))
                )

    def test_missing_or_wrongly_typed_run_status_is_terminal(self) -> None:
        for status in (_ABSENT, None, "", 10, True, ["completed"], "s" * 65):
            with self.subTest(status=status):
                self._assert_check_evidence_malformed(
                    _checks_page(_check_run(status=status))
                )

    def test_wrongly_typed_run_conclusion_is_terminal(self) -> None:
        for conclusion in (10, True, ["success"], {"c": 1}, "c" * 65):
            with self.subTest(conclusion=conclusion):
                self._assert_check_evidence_malformed(
                    _checks_page(_check_run(conclusion=conclusion))
                )

    def test_absent_or_null_conclusion_stays_non_green_rather_than_malformed(self) -> None:
        for conclusion in (None, ""):
            with self.subTest(conclusion=conclusion):
                snapshot = self._gateway(
                    checks=_checks_page(_check_run(conclusion=conclusion))
                ).snapshot("edwardjones3976/edge_engine", 23)
                self.assertEqual(snapshot.checks[0].conclusion, conclusion)

    # --- check-run evidence: head SHA, app, and check suite -----------------

    def test_invalid_run_head_sha_is_terminal(self) -> None:
        for head_sha in (
            _ABSENT,
            None,
            "",
            CHECK_RUN_LEAK,
            "a" * 39,
            "a" * 41,
            "A" * 40,
            "g" * 40,
            10,
            True,
            [HEAD_SHA],
        ):
            with self.subTest(head_sha=head_sha):
                self._assert_check_evidence_malformed(
                    _checks_page(_check_run(head_sha=head_sha))
                )

    def test_invalid_run_app_is_terminal(self) -> None:
        for app in (CHECK_RUN_LEAK, ["github-actions"], 10, True, {"slug": 10}, {"slug": ["a"]}, {"slug": "s" * 256}):
            with self.subTest(app=app):
                self._assert_check_evidence_malformed(
                    _checks_page(_check_run(app=app))
                )

    def test_unproven_app_producer_stays_unknown_and_never_matches_a_trusted_app(self) -> None:
        for app in (_ABSENT, None, {}, {"slug": None}, {"slug": ""}):
            with self.subTest(app=app):
                snapshot = self._gateway(
                    checks=_checks_page(_check_run(app=app))
                ).snapshot("edwardjones3976/edge_engine", 23)
                self.assertEqual(snapshot.checks[0].app_slug, "unknown")
                self.assertNotEqual(snapshot.checks[0].app_slug, "github-actions")

    def test_invalid_run_check_suite_is_terminal(self) -> None:
        for suite in (
            CHECK_RUN_LEAK,
            [11],
            11,
            True,
            {"id": "11"},
            {"id": 0},
            {"id": -1},
            {"id": True},
            {"id": 1.0},
        ):
            with self.subTest(suite=suite):
                self._assert_check_evidence_malformed(
                    _checks_page(_check_run(check_suite=suite))
                )

    def test_absent_check_suite_identifier_stays_absent(self) -> None:
        for suite in (_ABSENT, None, {}, {"id": None}):
            with self.subTest(suite=suite):
                snapshot = self._gateway(
                    checks=_checks_page(_check_run(check_suite=suite))
                ).snapshot("edwardjones3976/edge_engine", 23)
                self.assertIsNone(snapshot.checks[0].suite_id)

    # --- GraphQL review-thread evidence -------------------------------------

    def test_non_object_graphql_body_is_terminal(self) -> None:
        for payload in ([{"data": {}}], REVIEW_THREAD_LEAK, 17, True, None):
            with self.subTest(payload=payload):
                self._assert_review_evidence_incomplete(payload)

    def test_unparseable_graphql_body_is_terminal(self) -> None:
        for text in ("{{{", "", "not json at all"):
            with self.subTest(text=text):
                self._assert_review_evidence_incomplete(text=text)

    def test_malformed_graphql_envelope_is_terminal(self) -> None:
        cases = (
            {},
            {"data": None},
            {"data": REVIEW_THREAD_LEAK},
            {"data": []},
            _graphql_page(repository=None),
            _graphql_page(repository=[]),
            _graphql_page(pull_request=None),
            _graphql_page(pull_request=REVIEW_THREAD_LEAK),
            _graphql_page(threads=None),
            _graphql_page(threads=[]),
            _graphql_page(threads={"pageInfo": {"hasNextPage": False}}),
            _graphql_page(threads={"nodes": []}),
        )
        for payload in cases:
            with self.subTest(payload=payload):
                self._assert_review_evidence_incomplete(payload)

    def test_malformed_review_thread_nodes_are_terminal(self) -> None:
        for nodes in (
            {"isResolved": True},
            REVIEW_THREAD_LEAK,
            17,
            None,
            [REVIEW_THREAD_LEAK],
            [None],
            [{"isOutdated": False}],
            [{"isResolved": True}],
            [{"isResolved": "false", "isOutdated": False}],
            [{"isResolved": False, "isOutdated": "false"}],
            [{"isResolved": 0, "isOutdated": 0}],
            [{"isResolved": 1, "isOutdated": 1}],
            [{"isResolved": None, "isOutdated": None}],
            [{"isResolved": False, "isOutdated": False}] * 101,
        ):
            with self.subTest(nodes=nodes):
                self._assert_review_evidence_incomplete(
                    _graphql_page(threads={"nodes": nodes, "pageInfo": {"hasNextPage": False}})
                )

    def test_malformed_page_info_is_terminal(self) -> None:
        for page_info in (
            {},
            None,
            [],
            REVIEW_THREAD_LEAK,
            {"hasNextPage": None},
            {"hasNextPage": "false"},
            {"hasNextPage": "true"},
            {"hasNextPage": 0},
            {"hasNextPage": 1},
            {"hasNextPage": []},
        ):
            with self.subTest(page_info=page_info):
                self._assert_review_evidence_incomplete(_graphql_page(page_info=page_info))

    def test_malformed_review_decision_is_terminal(self) -> None:
        for decision in (10, True, ["APPROVED"], {"d": 1}, "d" * 65):
            with self.subTest(decision=decision):
                self._assert_review_evidence_incomplete(
                    _graphql_page(review_decision=decision)
                )

    def test_string_flags_can_no_longer_hide_a_blocking_review_thread(self) -> None:
        """A truthiness read of ``"false"`` would have dropped this blocking thread."""
        self._assert_review_evidence_incomplete(
            _graphql_page(nodes=[{"isResolved": "false", "isOutdated": "false"}])
        )
        snapshot = self._gateway(
            graphql=_graphql_page(nodes=[{"isResolved": False, "isOutdated": False}])
        ).snapshot("edwardjones3976/edge_engine", 23)
        self.assertEqual(snapshot.blocking_review_threads, 1)

    # --- final snapshot construction ----------------------------------------

    def test_non_object_pull_payload_is_terminal(self) -> None:
        for payload in ([{"state": "open"}], PULL_EVIDENCE_LEAK, 17, True, None):
            with self.subTest(payload=payload):
                self._assert_pull_evidence_malformed(payload)

    def test_repository_without_owner_and_name_is_terminal_before_any_read(self) -> None:
        for repository in ("edge_engine", "/edge_engine", "edwardjones3976/", "/", ""):
            with self.subTest(repository=repository):
                with self.assertRaises(OracleDiffError) as caught:
                    self._gateway().snapshot(repository, 23)
                self.assertEqual(
                    str(caught.exception), "GitHub pull request evidence is malformed"
                )
                self.assertEqual(self.harness.requests, [])

    def _poisoned_pull(self, **overrides) -> dict:
        pull = dict(self.harness.pull)
        for key, value in overrides.items():
            if value is _ABSENT:
                pull.pop(key, None)
            else:
                pull[key] = value
        return pull

    def test_non_boolean_mergeability_can_never_be_coerced_into_a_merge(self) -> None:
        """Lax coercion would have read every one of these as a proven ``True``."""
        for mergeable in ("yes", "true", "on", "1", 1, 1.0, "no", "false", 0, [], {}):
            with self.subTest(mergeable=mergeable):
                self._assert_pull_evidence_malformed(
                    self._poisoned_pull(mergeable=mergeable)
                )

    def test_absent_or_null_mergeability_stays_unresolved(self) -> None:
        for mergeable in (_ABSENT, None):
            with self.subTest(mergeable=mergeable):
                snapshot = self._gateway(
                    pull=self._poisoned_pull(mergeable=mergeable)
                ).snapshot("edwardjones3976/edge_engine", 23)
                self.assertIsNone(snapshot.mergeable)

    def test_non_boolean_draft_and_merged_flags_are_terminal(self) -> None:
        for field, value in (
            ("draft", "false"),
            ("draft", "true"),
            ("draft", 0),
            ("draft", 1),
            ("draft", None),
            ("draft", _ABSENT),
            ("merged", "false"),
            ("merged", 1),
            ("merged", []),
        ):
            with self.subTest(field=field, value=value):
                self._assert_pull_evidence_malformed(self._poisoned_pull(**{field: value}))

    def test_missing_or_wrongly_typed_pull_fields_are_terminal(self) -> None:
        for field, value in (
            ("state", _ABSENT),
            ("state", None),
            ("state", ""),
            ("state", 10),
            ("state", ["open"]),
            ("title", 10),
            ("title", ["t"]),
            ("title", "t" * 2_001),
            ("body", 10),
            ("body", "b" * 100_001),
            ("user", {"login": ""}),
            ("user", {"login": 10}),
            ("user", {"login": None}),
            ("user", {}),
            ("user", None),
            ("base", {"ref": "main"}),
            ("base", {"ref": "", "sha": BASE_SHA}),
            ("base", {"ref": 10, "sha": BASE_SHA}),
            ("base", None),
        ):
            with self.subTest(field=field, value=value):
                self._assert_pull_evidence_malformed(self._poisoned_pull(**{field: value}))

    def test_non_integer_changed_file_count_is_terminal_incomplete_enumeration(self) -> None:
        for value in ("1", True, None, 1.0, -1, [1], _ABSENT):
            with self.subTest(value=value):
                self._assert_terminal(
                    "GitHub file enumeration is incomplete",
                    pull=self._poisoned_pull(changed_files=value),
                )

    def test_healthy_evidence_still_produces_a_complete_snapshot(self) -> None:
        snapshot = self._gateway().snapshot("edwardjones3976/edge_engine", 23)
        self.assertEqual(snapshot.head_sha, HEAD_SHA)
        self.assertEqual(snapshot.base_sha, BASE_SHA)
        self.assertIs(snapshot.mergeable, True)
        self.assertIs(snapshot.draft, False)
        self.assertIs(snapshot.merged, False)
        self.assertEqual(len(snapshot.checks), 1)
        self.assertEqual(snapshot.checks[0].run_id, 10)
        self.assertEqual(snapshot.checks[0].suite_id, 11)
        self.assertEqual(snapshot.checks[0].app_slug, "github-actions")
        self.assertEqual(snapshot.checks[0].head_sha, HEAD_SHA)
        self.assertEqual(snapshot.blocking_review_threads, 0)
        self.assertIs(snapshot.review_threads_complete, True)
        self.assertEqual(snapshot.review_decision, "APPROVED")
        self.assertEqual(snapshot.changed_files[0].added_lines, ["research_only = True"])


class MalformedEvidenceTerminalDeliveryTests(unittest.TestCase):
    """Malformed evidence is consumed once; rate limits and transport are not.

    Terminal evidence must spend exactly one bounded snapshot cycle, record a
    completed non-merge verdict, and make zero GitHub calls on redelivery. A rate
    limit or a real transport failure must stay recoverable so the same reviewed
    artifact can be delivered again.
    """

    def setUp(self) -> None:
        self.harness = GitHubCompleteDiffGatewayTests()
        self.harness.setUp()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.settings = Settings(
            sqlite_path=str(Path(self.temporary.name) / "oracle.sqlite3"),
            oracle_automerge_enabled=True,
            oracle_automerge_kill_switch=False,
            oracle_automerge_project="edge-oracle",
            oracle_automerge_repository="edwardjones3976/edge_engine",
            oracle_automerge_base_ref="edge-oracle/base",
            oracle_automerge_branch_prefix="oracle/",
            oracle_automerge_allowed_paths=("oracle/**", "docs/oracle/**", "tests/oracle/**"),
            oracle_automerge_required_checks=("ci", "security"),
            oracle_automerge_required_check_app="github-actions",
            oracle_automerge_review_secret=REVIEW_SECRET,
            oracle_automerge_claude_actors=("claude-agent",),
            oracle_automerge_cursor_actors=("cursor-agent",),
            oracle_automerge_gemini_actors=("gemini-agent",),
        )

    def _artifact(self, suffix: str) -> OracleReviewArtifact:
        return OracleReviewArtifact.model_validate(
            {
                "artifact_type": "edge_oracle_exact_sha_review/v1",
                "artifact_id": f"review-artifact-evidence-{suffix}",
                "assignment_id": f"assignment-evidence-{suffix}",
                "project": "edge-oracle",
                "repository": "edwardjones3976/edge_engine",
                "pull_number": 23,
                "base_ref": "edge-oracle/base",
                "base_sha": BASE_SHA,
                "head_ref": "oracle/test",
                "head_sha": HEAD_SHA,
                "implementation_provider": "cursor",
                "implementation_actor": "cursor-agent",
                "reviewer_provider": "claude",
                "review_run_id": f"claude-review-evidence-{suffix}",
                "verdict": OracleReviewVerdict.ACCEPT,
                "summary": "Exact-SHA review accepted the bounded Oracle change.",
                "evidence": ["Tests and changed paths reviewed at the exact SHA."],
                "acceptance_checks": ["ci", "security"],
                "rollback_instructions": "Revert the merge commit and restore the prior main ref.",
                "signature": "0" * 64,
            }
        ).sign(REVIEW_SECRET)

    def _compare_calls(self) -> int:
        return sum(1 for item in self.harness.requests if "/compare/" in item.url.path)

    def _check_run_calls(self) -> int:
        return sum(
            1 for item in self.harness.requests if item.url.path.endswith("/check-runs")
        )

    def _merge_or_ready_calls(self) -> int:
        return sum(
            1
            for item in self.harness.requests
            if (item.method == "PUT" and item.url.path.endswith("/merge"))
            or (
                item.method == "POST"
                and item.url.path.endswith("/graphql")
                and b"markPullRequestReadyForReview" in item.content
            )
        )

    def _attempt_row(self, artifact_id: str) -> tuple:
        connection = sqlite3.connect(self.settings.sqlite_path)
        try:
            return connection.execute(
                "SELECT status, result_json, retry_not_before "
                "FROM oracle_merge_attempts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        finally:
            connection.close()

    def _service(self, handler) -> OracleMergeService:
        return OracleMergeService(
            OracleMergePolicy(self.settings),
            OracleMergeStore(self.settings.sqlite_path),
            GitHubOracleGateway(
                "repo-scoped-token", transport=httpx.MockTransport(handler)
            ),
            sleep=lambda _: None,
        )

    def _handler(self, *, checks=None, graphql=None, pull=None):
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            is_diff = "diff" in request.headers.get("accept", "")
            if pull is not None and request.method == "GET" and path.endswith("/pulls/23") and not is_diff:
                self.harness.requests.append(request)
                return httpx.Response(200, json=pull)
            if checks is not None and request.method == "GET" and path.endswith("/check-runs"):
                self.harness.requests.append(request)
                return checks()
            if (
                graphql is not None
                and request.method == "POST"
                and path == "/graphql"
                and b"markPullRequestReadyForReview" not in request.content
            ):
                self.harness.requests.append(request)
                return graphql()
            return self.harness.handler(request)

        return handler

    def _pull_calls(self) -> int:
        return sum(
            1
            for item in self.harness.requests
            if item.method == "GET"
            and item.url.path.endswith("/pulls/23")
            and "diff" not in item.headers.get("accept", "")
        )

    def _assert_terminal_single_delivery(
        self, suffix: str, *, expected_compare_calls: int = 1, **handler_kwargs
    ) -> None:
        self.harness.requests.clear()
        artifact = self._artifact(suffix)
        service = self._service(self._handler(**handler_kwargs))

        first = service.process(artifact)
        self.assertEqual(first.status, OracleMergeStatus.ERROR)
        self.assertNotEqual(first.status, OracleMergeStatus.MERGED)
        self.assertNotEqual(first.status, OracleMergeStatus.ELIGIBLE)
        self.assertTrue(
            any("evidence failed closed" in item for item in first.reasons), first.reasons
        )
        # Terminal evidence is consumed, not released: no retryable release reason
        # and no repeated pull/files/compare cycle.
        self.assertFalse(any("retryable" in item for item in first.reasons), first.reasons)
        self.assertFalse(
            any("transient read failures" in item for item in first.reasons), first.reasons
        )
        self.assertEqual(self._compare_calls(), expected_compare_calls)
        self.assertLessEqual(self._check_run_calls(), 1)
        # One snapshot cycle brackets the compare fetch with two pull reads. More
        # than two proves the read allowance was spent re-reading durable evidence.
        self.assertLessEqual(self._pull_calls(), 2)
        self.assertEqual(self._merge_or_ready_calls(), 0)
        self.assertIsNone(first.merge_commit_sha)
        joined = " ".join(first.reasons)
        for leak in (CHECK_RUN_LEAK, REVIEW_THREAD_LEAK, PULL_EVIDENCE_LEAK):
            self.assertNotIn(leak, joined)

        status, payload, retry_not_before = self._attempt_row(artifact.artifact_id)
        self.assertEqual(status, "ERROR")
        self.assertIsNotNone(payload)
        self.assertIsNone(retry_not_before)

        calls = len(self.harness.requests)
        second = service.process(artifact)
        self.assertEqual(second.status, OracleMergeStatus.ERROR)
        self.assertEqual(len(self.harness.requests), calls)
        self.assertEqual(self._compare_calls(), expected_compare_calls)
        self.assertEqual(self._merge_or_ready_calls(), 0)

    def test_malformed_check_run_evidence_is_one_delivery_and_no_merge(self) -> None:
        cases = {
            "non-object-body": lambda: httpx.Response(
                200, text=f'"{CHECK_RUN_LEAK}"', headers={"Content-Type": "application/json"}
            ),
            "unparseable-body": lambda: httpx.Response(
                200, text="{{{", headers={"Content-Type": "application/json"}
            ),
            "non-list-runs": lambda: httpx.Response(
                200, json={"total_count": 1, "check_runs": {"id": 10}}
            ),
            "string-total-count": lambda: httpx.Response(
                200, json={"total_count": "1", "check_runs": [GOOD_CHECK_RUN]}
            ),
            "bool-total-count": lambda: httpx.Response(
                200, json={"total_count": True, "check_runs": [GOOD_CHECK_RUN]}
            ),
            "count-disagrees": lambda: httpx.Response(
                200, json={"total_count": 7, "check_runs": [GOOD_CHECK_RUN]}
            ),
            "missing-run-id": lambda: httpx.Response(
                200, json=_checks_page(_check_run(id=_ABSENT))
            ),
            "invalid-run-head-sha": lambda: httpx.Response(
                200, json=_checks_page(_check_run(head_sha=CHECK_RUN_LEAK))
            ),
            "invalid-run-app": lambda: httpx.Response(
                200, json=_checks_page(_check_run(app=[CHECK_RUN_LEAK]))
            ),
            "invalid-check-suite": lambda: httpx.Response(
                200, json=_checks_page(_check_run(check_suite=[11]))
            ),
        }
        for suffix, checks in cases.items():
            with self.subTest(case=suffix):
                self._assert_terminal_single_delivery(suffix, checks=checks)

    def test_malformed_review_thread_evidence_is_one_delivery_and_no_merge(self) -> None:
        cases = {
            "graphql-non-object": lambda: httpx.Response(
                200, text=f'"{REVIEW_THREAD_LEAK}"', headers={"Content-Type": "application/json"}
            ),
            "graphql-unparseable": lambda: httpx.Response(
                200, text="{{{", headers={"Content-Type": "application/json"}
            ),
            "graphql-null-data": lambda: httpx.Response(200, json={"data": None}),
            "graphql-null-pull-request": lambda: httpx.Response(
                200, json=_graphql_page(pull_request=None)
            ),
            "graphql-non-list-nodes": lambda: httpx.Response(
                200,
                json=_graphql_page(
                    threads={"nodes": REVIEW_THREAD_LEAK, "pageInfo": {"hasNextPage": False}}
                ),
            ),
            "graphql-string-node-flags": lambda: httpx.Response(
                200, json=_graphql_page(nodes=[{"isResolved": "false", "isOutdated": "false"}])
            ),
            "graphql-missing-page-info": lambda: httpx.Response(
                200, json=_graphql_page(page_info={})
            ),
            "graphql-string-has-next-page": lambda: httpx.Response(
                200, json=_graphql_page(page_info={"hasNextPage": "false"})
            ),
            "graphql-non-string-decision": lambda: httpx.Response(
                200, json=_graphql_page(review_decision=[REVIEW_THREAD_LEAK])
            ),
        }
        for suffix, graphql in cases.items():
            with self.subTest(case=suffix):
                self._assert_terminal_single_delivery(suffix, graphql=graphql)

    def test_malformed_final_snapshot_construction_is_one_delivery_and_no_merge(self) -> None:
        """``mergeable`` is proven only at construction, after the compare fetch."""
        for suffix, mergeable in (
            ("coercible-mergeable-yes", "yes"),
            ("coercible-mergeable-true", "true"),
            ("coercible-mergeable-one", 1),
            ("coercible-mergeable-float", 1.0),
        ):
            with self.subTest(case=suffix):
                self._assert_terminal_single_delivery(
                    suffix, pull=dict(self.harness.pull, mergeable=mergeable)
                )

    def test_malformed_pull_evidence_is_terminal_before_any_compare_fetch(self) -> None:
        """Identity-bound fields fail closed before the bounded compare fetch runs."""
        cases = {
            "pull-non-object": [PULL_EVIDENCE_LEAK],
            "string-draft": dict(self.harness.pull, draft="false"),
            "numeric-merged": dict(self.harness.pull, merged=1),
            "empty-author": dict(self.harness.pull, user={"login": ""}),
            "non-string-state": dict(self.harness.pull, state=[PULL_EVIDENCE_LEAK]),
            "oversized-title": dict(self.harness.pull, title="t" * 2_001),
        }
        for suffix, pull in cases.items():
            with self.subTest(case=suffix):
                self._assert_terminal_single_delivery(
                    suffix, pull=pull, expected_compare_calls=0
                )

    def test_check_run_rate_limit_stays_recoverable_for_a_later_retry(self) -> None:
        reset = str(int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()))
        cases = (
            (429, {"retry-after": "3600"}),
            (403, {"retry-after": "3600"}),
            (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset}),
        )
        for status, headers in cases:
            with self.subTest(status=status, headers=headers):
                self.harness.requests.clear()
                artifact = self._artifact(f"rate-limit-{status}-{sorted(headers.items())}")
                service = self._service(
                    self._handler(
                        checks=lambda status=status, headers=headers: httpx.Response(
                            status, json={"total_count": 0, "check_runs": []}, headers=headers
                        )
                    )
                )
                result = service.process(artifact)
                self.assertEqual(result.status, OracleMergeStatus.ERROR)
                self.assertTrue(any("rate limit" in item for item in result.reasons))
                self.assertTrue(any("remains retryable" in item for item in result.reasons))
                self.assertFalse(
                    any("evidence failed closed" in item for item in result.reasons)
                )
                self.assertEqual(self._compare_calls(), 1)
                self.assertEqual(self._merge_or_ready_calls(), 0)
                self.assertNotIn("3600", " ".join(result.reasons))
                row_status, payload, retry_not_before = self._attempt_row(
                    artifact.artifact_id
                )
                self.assertEqual(row_status, "RECOVERABLE")
                self.assertIsNone(payload)
                self.assertIsNotNone(retry_not_before)

    def test_check_run_transport_failure_stays_recoverable_after_bounded_reads(self) -> None:
        for status in (500, 502, 503, 504):
            with self.subTest(status=status):
                self.harness.requests.clear()
                artifact = self._artifact(f"transport-{status}")
                service = self._service(
                    self._handler(
                        checks=lambda status=status: httpx.Response(status, json={})
                    )
                )
                result = service.process(artifact)
                self.assertEqual(result.status, OracleMergeStatus.ERROR)
                self.assertTrue(
                    any("transient read failures" in item for item in result.reasons)
                )
                self.assertTrue(any("remains retryable" in item for item in result.reasons))
                self.assertFalse(
                    any("evidence failed closed" in item for item in result.reasons)
                )
                # The bounded read allowance is spent, and only then released.
                self.assertEqual(self._compare_calls(), 2)
                self.assertEqual(self._merge_or_ready_calls(), 0)
                row_status, payload, _ = self._attempt_row(artifact.artifact_id)
                self.assertEqual(row_status, "RECOVERABLE")
                self.assertIsNone(payload)

    def test_transport_exception_stays_recoverable(self) -> None:
        self.harness.requests.clear()
        artifact = self._artifact("connect-error")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET" and request.url.path.endswith("/check-runs"):
                self.harness.requests.append(request)
                raise httpx.ConnectError("transport is unavailable")
            return self.harness.handler(request)

        result = self._service(handler).process(artifact)
        self.assertEqual(result.status, OracleMergeStatus.ERROR)
        self.assertTrue(any("transient read failures" in item for item in result.reasons))
        self.assertTrue(any("remains retryable" in item for item in result.reasons))
        self.assertFalse(any("evidence failed closed" in item for item in result.reasons))
        self.assertEqual(self._merge_or_ready_calls(), 0)
        row_status, payload, _ = self._attempt_row(artifact.artifact_id)
        self.assertEqual(row_status, "RECOVERABLE")
        self.assertIsNone(payload)

    def test_graphql_rate_limit_stays_recoverable_while_malformed_graphql_is_terminal(self) -> None:
        self.harness.requests.clear()
        artifact = self._artifact("graphql-rate-limit")
        service = self._service(
            self._handler(
                graphql=lambda: httpx.Response(
                    200,
                    json={"errors": [{"type": "RATE_LIMITED", "message": GRAPHQL_RATE_LIMIT_LEAK}]},
                    headers={"retry-after": "3600"},
                )
            )
        )
        result = service.process(artifact)
        self.assertEqual(result.status, OracleMergeStatus.ERROR)
        self.assertTrue(any("rate limit" in item for item in result.reasons))
        self.assertTrue(any("remains retryable" in item for item in result.reasons))
        self.assertNotIn(GRAPHQL_RATE_LIMIT_LEAK, " ".join(result.reasons))
        row_status, payload, retry_not_before = self._attempt_row(artifact.artifact_id)
        self.assertEqual(row_status, "RECOVERABLE")
        self.assertIsNone(payload)
        self.assertIsNotNone(retry_not_before)



if __name__ == "__main__":
    unittest.main()
# [[Claude Code | Claude Opus 5 | 2026-09-08 | 22:42 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-12 | 08:30 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 08:16 UTC | END]]
# [[Z.ai | GLM-5.3 | 2026-09-27 | 06:39 UTC | END]]
