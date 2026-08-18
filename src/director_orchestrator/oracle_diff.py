"""Bounded structural parser for GitHub compare unified diffs.

This module supplies complete per-file scan evidence when the pull-request
files REST response omits ``patch`` bodies. It accepts only a strict subset of
ordinary UTF-8 textual git diffs and fails closed on anything it cannot prove.
Error messages never include file content or matched credential text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


# Edge Oracle PR #39's complete compare/PR diffs were 381,950 bytes with 13
# sections; the two largest textual sections were 124,813 and 115,828 bytes.
MAX_COMPARE_DIFF_BYTES = 1_048_576
MAX_FILE_PATCH_CHARS = 200_000
GITHUB_DIFF_MEDIA_TYPE = "application/vnd.github.diff"
NO_NEWLINE_MARKER = "\\ No newline at end of file"
_REGULAR_FILE_MODES = frozenset({"100644", "100755"})
_HUNK_HEADER_PREFIX = "@@ "
_DIFF_GIT_PREFIX = "diff --git a/"
_GIT_OID = re.compile(r"[0-9a-f]{4,64}")


class OracleDiffError(RuntimeError):
    """Fail-closed complete-diff error that must not embed file content."""


class OracleRateLimitError(RuntimeError):
    """GitHub rate limit that must release for a later retry, not an immediate re-read."""


@dataclass(frozen=True)
class ParsedDiffFile:
    filename: str
    previous_filename: str | None
    status: str
    additions: int
    deletions: int
    patch: str
    added_lines: tuple[str, ...]


@dataclass(frozen=True)
class BoundFileEvidence:
    filename: str
    previous_filename: str | None
    status: str
    additions: int
    deletions: int
    patch: str
    added_lines: tuple[str, ...]


@dataclass(frozen=True)
class ParsedHunkPatch:
    added_lines: tuple[str, ...]
    additions: int
    deletions: int


def parse_hunk_patch(patch: str) -> ParsedHunkPatch:
    """Parse REST-style hunks and prove every hunk body is complete.

    File headers are recognized only before the first hunk. Once a hunk is
    open, a line beginning with ``+`` is always added source content, even if
    the source itself begins with ``++``. Old/new hunk counts are enforced, and
    the returned totals are the counted added and deleted body lines.
    """
    if patch == "":
        return ParsedHunkPatch(added_lines=(), additions=0, deletions=0)
    if "\x00" in patch:
        raise OracleDiffError("changed-file patch evidence is malformed")
    added: list[str] = []
    additions = 0
    deletions = 0
    in_hunk = False
    hunk_complete = True
    old_remaining = 0
    new_remaining = 0
    for line in patch.splitlines():
        if line.startswith("@@"):
            if in_hunk and not hunk_complete:
                raise OracleDiffError("changed-file patch evidence is truncated")
            old_count, new_count = _parse_hunk_header(line)
            in_hunk = True
            old_remaining = old_count
            new_remaining = new_count
            hunk_complete = old_count == 0 and new_count == 0
            continue
        if not in_hunk:
            if line.startswith("---") or line.startswith("+++"):
                continue
            raise OracleDiffError("changed-file patch evidence is malformed")
        if hunk_complete:
            if line == NO_NEWLINE_MARKER:
                continue
            raise OracleDiffError("changed-file patch evidence is malformed")
        if line == NO_NEWLINE_MARKER:
            continue
        if line.startswith("+"):
            if new_remaining <= 0:
                raise OracleDiffError("changed-file patch evidence is malformed")
            added.append(line[1:])
            additions += 1
            new_remaining -= 1
            hunk_complete = old_remaining == 0 and new_remaining == 0
            continue
        if line.startswith("-"):
            if old_remaining <= 0:
                raise OracleDiffError("changed-file patch evidence is malformed")
            deletions += 1
            old_remaining -= 1
            hunk_complete = old_remaining == 0 and new_remaining == 0
            continue
        if line.startswith(" "):
            if old_remaining <= 0 or new_remaining <= 0:
                raise OracleDiffError("changed-file patch evidence is malformed")
            old_remaining -= 1
            new_remaining -= 1
            hunk_complete = old_remaining == 0 and new_remaining == 0
            continue
        raise OracleDiffError("changed-file patch evidence is malformed")
    if in_hunk and not hunk_complete:
        raise OracleDiffError("changed-file patch evidence is truncated")
    return ParsedHunkPatch(
        added_lines=tuple(added),
        additions=additions,
        deletions=deletions,
    )


def added_lines_from_hunk_patch(patch: str) -> tuple[str, ...]:
    return parse_hunk_patch(patch).added_lines


def require_github_diff_content_type(content_type: str | None) -> None:
    raw = (content_type or "").strip()
    if not raw:
        raise OracleDiffError("complete compare diff is malformed")
    parts = [part.strip() for part in raw.split(";")]
    if parts[0].lower() != GITHUB_DIFF_MEDIA_TYPE:
        raise OracleDiffError("complete compare diff is malformed")
    for param in parts[1:]:
        name, separator, value = param.partition("=")
        if separator != "=" or name.strip().lower() != "charset" or not value.strip():
            raise OracleDiffError("complete compare diff is malformed")


def require_identity_content_encoding(content_encoding: str | None) -> None:
    raw = (content_encoding or "").strip()
    if not raw:
        return
    parts = [part.strip().lower() for part in raw.split(",") if part.strip()]
    if parts != ["identity"]:
        raise OracleDiffError("complete compare diff is malformed")


def parse_compare_diff(
    text: str,
    *,
    max_bytes: int = MAX_COMPARE_DIFF_BYTES,
) -> tuple[ParsedDiffFile, ...]:
    if len(text.encode("utf-8")) > max_bytes:
        raise OracleDiffError("complete compare diff exceeds the bounded evidence limit")
    if text == "":
        return ()
    if text.startswith("\ufeff"):
        raise OracleDiffError("complete compare diff is malformed")
    if "\x00" in text:
        raise OracleDiffError("complete compare diff is malformed")
    if not text.endswith("\n"):
        raise OracleDiffError("complete compare diff is truncated")
    parser = _CompareDiffParser()
    parser.feed(text.splitlines())
    return parser.finish()


def bind_complete_diff_evidence(
    rest_files: Sequence[Mapping[str, Any]],
    parsed: Sequence[ParsedDiffFile],
) -> tuple[BoundFileEvidence, ...]:
    rest_names = [str(item["filename"]) for item in rest_files]
    if len(set(rest_names)) != len(rest_names):
        raise OracleDiffError("REST file enumeration contains duplicate paths")
    if len(rest_files) != len(parsed):
        raise OracleDiffError("REST file enumeration does not match the complete compare diff")

    parsed_by_name: dict[str, ParsedDiffFile] = {}
    for section in parsed:
        if section.filename in parsed_by_name:
            raise OracleDiffError("complete compare diff has duplicate file sections")
        parsed_by_name[section.filename] = section
    if set(rest_names) != set(parsed_by_name):
        raise OracleDiffError("REST file enumeration does not match the complete compare diff")

    bound: list[BoundFileEvidence] = []
    for item in rest_files:
        section = parsed_by_name[str(item["filename"])]
        previous = item.get("previous_filename")
        if previous != section.previous_filename:
            raise OracleDiffError("REST file enumeration does not match the complete compare diff")
        if str(item["status"]) != section.status:
            raise OracleDiffError("REST file enumeration does not match the complete compare diff")
        if int(item.get("additions", 0)) != section.additions:
            raise OracleDiffError("REST file enumeration does not match the complete compare diff")
        if int(item.get("deletions", 0)) != section.deletions:
            raise OracleDiffError("REST file enumeration does not match the complete compare diff")
        if len(section.patch) > MAX_FILE_PATCH_CHARS:
            raise OracleDiffError("complete compare diff exceeds the bounded per-file evidence limit")
        bound.append(
            BoundFileEvidence(
                filename=section.filename,
                previous_filename=section.previous_filename,
                status=section.status,
                additions=section.additions,
                deletions=section.deletions,
                patch=section.patch,
                added_lines=section.added_lines,
            )
        )
    return tuple(bound)


def _parse_hunk_header(line: str) -> tuple[int, int]:
    if not line.startswith("@@"):
        raise OracleDiffError("complete compare diff is malformed")
    if line.startswith("@@@"):
        raise OracleDiffError("complete compare diff contains a combined diff")
    if not line.startswith(_HUNK_HEADER_PREFIX):
        raise OracleDiffError("complete compare diff is malformed")
    rest = line[len(_HUNK_HEADER_PREFIX) :]
    at = rest.find(" @@")
    if at < 0:
        raise OracleDiffError("complete compare diff is malformed")
    spec = rest[:at]
    parts = spec.split(" ")
    if len(parts) != 2 or not parts[0].startswith("-") or not parts[1].startswith("+"):
        raise OracleDiffError("complete compare diff is malformed")
    return _hunk_count(parts[0][1:]), _hunk_count(parts[1][1:])


def _hunk_count(spec: str) -> int:
    if "," in spec:
        start, count = spec.split(",", 1)
        if not start.isdigit() or not count.isdigit():
            raise OracleDiffError("complete compare diff is malformed")
        return int(count)
    if not spec.isdigit():
        raise OracleDiffError("complete compare diff is malformed")
    return 1


def _safe_repo_path(path: str) -> str:
    if not path or path.startswith("/") or path.endswith("/"):
        raise OracleDiffError("complete compare diff has an unsafe path")
    if any(ord(char) < 32 or char in '"\'\\\t*?`[]$;&|<>' for char in path):
        raise OracleDiffError("complete compare diff has an unsupported path")
    parts = path.split("/")
    if any(part in {"", ".", "..", ".git"} for part in parts):
        raise OracleDiffError("complete compare diff has an unsafe path")
    if not all(part.isascii() and part.isprintable() for part in parts):
        raise OracleDiffError("complete compare diff has an unsupported path")
    return path


def _parse_identical_diff_git(line: str) -> str:
    if not line.startswith(_DIFF_GIT_PREFIX):
        raise OracleDiffError("complete compare diff is malformed")
    rest = line[len(_DIFF_GIT_PREFIX) :]
    if len(rest) < 3 or (len(rest) - 3) % 2 != 0:
        raise OracleDiffError("complete compare diff has an ambiguous a/ b/ path")
    size = (len(rest) - 3) // 2
    path = rest[:size]
    if rest != f"{path} b/{path}":
        raise OracleDiffError("complete compare diff has an ambiguous a/ b/ path")
    return _safe_repo_path(path)


class _CompareDiffParser:
    def __init__(self) -> None:
        self._sections: list[ParsedDiffFile] = []
        self._seen: set[tuple[str, str | None]] = set()
        self._current: _FileSection | None = None

    def feed(self, lines: Sequence[str]) -> None:
        for line in lines:
            if line.startswith("diff --cc ") or line.startswith("diff --combined "):
                raise OracleDiffError("complete compare diff contains a combined diff")
            if self._current is None:
                self._start_file(line)
                continue
            if self._current.in_hunk:
                self._current.consume_hunk_line(line)
                if self._current.hunk_complete and line.startswith(_DIFF_GIT_PREFIX):
                    self._commit_current()
                    self._start_file(line)
                continue
            if line.startswith(_DIFF_GIT_PREFIX):
                self._commit_current()
                self._start_file(line)
                continue
            self._current.consume_meta_line(line)

    def finish(self) -> tuple[ParsedDiffFile, ...]:
        if self._current is not None:
            if self._current.in_hunk and not self._current.hunk_complete:
                raise OracleDiffError("complete compare diff is truncated")
            self._commit_current()
        return tuple(self._sections)

    def _start_file(self, line: str) -> None:
        if not line.startswith("diff --git "):
            raise OracleDiffError("complete compare diff is malformed")
        if '"' in line or "\\" in line:
            raise OracleDiffError("complete compare diff has an unsupported path")
        self._current = _FileSection(line)

    def _commit_current(self) -> None:
        assert self._current is not None
        parsed = self._current.finish()
        identity = (parsed.filename, parsed.previous_filename)
        if identity in self._seen or any(item.filename == parsed.filename for item in self._sections):
            raise OracleDiffError("complete compare diff has duplicate file sections")
        self._seen.add(identity)
        self._sections.append(parsed)
        self._current = None


class _FileSection:
    def __init__(self, git_line: str) -> None:
        self.git_line = git_line
        self.identical_path: str | None
        try:
            self.identical_path = _parse_identical_diff_git(git_line)
        except OracleDiffError:
            self.identical_path = None
        self.new_file_mode: str | None = None
        self.deleted_file_mode: str | None = None
        self.rename_from: str | None = None
        self.rename_to: str | None = None
        self.similarity: int | None = None
        self.old_path: str | None = None
        self.new_path: str | None = None
        self.old_is_dev_null = False
        self.new_is_dev_null = False
        self.saw_index = False
        self.hunk_lines: list[str] = []
        self.added_lines: list[str] = []
        self.additions = 0
        self.deletions = 0
        self.in_hunk = False
        self.hunk_complete = True
        self._old_remaining = 0
        self._new_remaining = 0

    def consume_meta_line(self, line: str) -> None:
        if line.startswith("@@@") or line.startswith("diff --cc ") or line.startswith("diff --combined "):
            raise OracleDiffError("complete compare diff contains a combined diff")
        if line.startswith("Binary files ") or line == "GIT binary patch":
            raise OracleDiffError("complete compare diff contains a binary file")
        if line.startswith("copy from ") or line.startswith("copy to "):
            raise OracleDiffError("complete compare diff has an unsupported rename")
        if line.startswith("dissimilarity index "):
            raise OracleDiffError("complete compare diff has unsupported metadata")
        if line.startswith("old mode ") or line.startswith("new mode "):
            raise OracleDiffError("complete compare diff has unsupported metadata")
        if line.startswith("new file mode "):
            self.new_file_mode = _require_regular_mode(line[len("new file mode ") :])
            return
        if line.startswith("deleted file mode "):
            self.deleted_file_mode = _require_regular_mode(line[len("deleted file mode ") :])
            return
        if line.startswith("rename from "):
            self.rename_from = _safe_repo_path(line[len("rename from ") :])
            return
        if line.startswith("rename to "):
            self.rename_to = _safe_repo_path(line[len("rename to ") :])
            return
        if line.startswith("similarity index "):
            if self.similarity is not None:
                raise OracleDiffError("complete compare diff is malformed")
            self.similarity = _parse_similarity(line)
            return
        if line.startswith("index "):
            self._parse_index(line)
            return
        if line.startswith("--- "):
            self._parse_old_header(line)
            return
        if line.startswith("+++ "):
            self._parse_new_header(line)
            return
        if line.startswith(_HUNK_HEADER_PREFIX) or line.startswith("@@"):
            self._start_hunk(line)
            return
        raise OracleDiffError("complete compare diff has unsupported metadata")

    def consume_hunk_line(self, line: str) -> None:
        if self.hunk_complete:
            if line == NO_NEWLINE_MARKER:
                self.hunk_lines.append(line)
                return
            if line.startswith(_HUNK_HEADER_PREFIX) or line.startswith("@@"):
                self._start_hunk(line)
                return
            if line.startswith(_DIFF_GIT_PREFIX):
                return
            raise OracleDiffError("complete compare diff is malformed")
        if line.startswith(_DIFF_GIT_PREFIX):
            raise OracleDiffError("complete compare diff is malformed")
        if line.startswith("@@@"):
            raise OracleDiffError("complete compare diff contains a combined diff")
        if line == NO_NEWLINE_MARKER:
            self.hunk_lines.append(line)
            return
        if line.startswith("+"):
            if self._new_remaining <= 0:
                raise OracleDiffError("complete compare diff is malformed")
            self.hunk_lines.append(line)
            self.added_lines.append(line[1:])
            self.additions += 1
            self._new_remaining -= 1
            self._mark_hunk_progress()
            return
        if line.startswith("-"):
            if self._old_remaining <= 0:
                raise OracleDiffError("complete compare diff is malformed")
            self.hunk_lines.append(line)
            self.deletions += 1
            self._old_remaining -= 1
            self._mark_hunk_progress()
            return
        if line.startswith(" "):
            if self._old_remaining <= 0 or self._new_remaining <= 0:
                raise OracleDiffError("complete compare diff is malformed")
            self.hunk_lines.append(line)
            self._old_remaining -= 1
            self._new_remaining -= 1
            self._mark_hunk_progress()
            return
        raise OracleDiffError("complete compare diff is malformed")

    def finish(self) -> ParsedDiffFile:
        if self.in_hunk and not self.hunk_complete:
            raise OracleDiffError("complete compare diff is truncated")
        filename, previous, status = self._identity()
        self._require_index_metadata(status)
        if len("\n".join(self.hunk_lines)) > MAX_FILE_PATCH_CHARS:
            raise OracleDiffError("complete compare diff exceeds the bounded per-file evidence limit")
        return ParsedDiffFile(
            filename=filename,
            previous_filename=previous,
            status=status,
            additions=self.additions,
            deletions=self.deletions,
            patch="\n".join(self.hunk_lines),
            added_lines=tuple(self.added_lines),
        )

    def _start_hunk(self, line: str) -> None:
        old_count, new_count = _parse_hunk_header(line)
        self.in_hunk = True
        self.hunk_complete = old_count == 0 and new_count == 0
        self._old_remaining = old_count
        self._new_remaining = new_count
        self.hunk_lines.append(line)

    def _mark_hunk_progress(self) -> None:
        if self._old_remaining == 0 and self._new_remaining == 0:
            self.hunk_complete = True

    def _parse_index(self, line: str) -> None:
        if self.saw_index:
            raise OracleDiffError("complete compare diff is malformed")
        payload = line[len("index ") :]
        if "," in payload.split(" ", 1)[0]:
            raise OracleDiffError("complete compare diff contains a combined diff")
        parts = payload.split(" ")
        if len(parts) == 2:
            _require_regular_mode(parts[1])
        elif len(parts) != 1:
            raise OracleDiffError("complete compare diff is malformed")
        shas = parts[0].split("..")
        if len(shas) != 2 or not all(_GIT_OID.fullmatch(item) for item in shas):
            raise OracleDiffError("complete compare diff is malformed")
        self.saw_index = True

    def _require_index_metadata(self, status: str) -> None:
        pure_rename = (
            status == "renamed" and not self.hunk_lines and self.similarity == 100
        )
        if pure_rename:
            return
        if not self.saw_index:
            raise OracleDiffError("complete compare diff is malformed")

    def _parse_old_header(self, line: str) -> None:
        if self.old_path is not None or self.old_is_dev_null:
            raise OracleDiffError("complete compare diff is malformed")
        path = line[len("--- ") :]
        if path == "/dev/null":
            self.old_is_dev_null = True
            return
        if not path.startswith("a/"):
            raise OracleDiffError("complete compare diff has an ambiguous a/ b/ path")
        self.old_path = _safe_repo_path(path[2:])

    def _parse_new_header(self, line: str) -> None:
        if self.new_path is not None or self.new_is_dev_null:
            raise OracleDiffError("complete compare diff is malformed")
        path = line[len("+++ ") :]
        if path == "/dev/null":
            self.new_is_dev_null = True
            return
        if not path.startswith("b/"):
            raise OracleDiffError("complete compare diff has an ambiguous a/ b/ path")
        self.new_path = _safe_repo_path(path[2:])

    def _identity(self) -> tuple[str, str | None, str]:
        renamed = self.rename_from is not None or self.rename_to is not None
        if renamed:
            return self._rename_identity()
        if self.new_file_mode and self.deleted_file_mode:
            raise OracleDiffError("complete compare diff is malformed")
        if self.identical_path is None:
            raise OracleDiffError("complete compare diff has an ambiguous a/ b/ path")
        path = self.identical_path
        if self.new_file_mode:
            return self._added_identity(path)
        if self.deleted_file_mode:
            return self._removed_identity(path)
        if self.hunk_lines:
            self._require_modify_headers(path)
            return path, None, "modified"
        raise OracleDiffError("complete compare diff has unsupported metadata")

    def _rename_identity(self) -> tuple[str, str | None, str]:
        if self.rename_from is None or self.rename_to is None:
            raise OracleDiffError("complete compare diff has an unsupported rename")
        if self.new_file_mode or self.deleted_file_mode:
            raise OracleDiffError("complete compare diff has an unsupported rename")
        expected = f"diff --git a/{self.rename_from} b/{self.rename_to}"
        if self.git_line != expected:
            raise OracleDiffError("complete compare diff has an unsupported rename")
        if self.hunk_lines:
            if self.similarity is None or self.similarity == 100:
                raise OracleDiffError("complete compare diff has an unsupported rename")
            if self.old_is_dev_null or self.new_is_dev_null:
                raise OracleDiffError("complete compare diff has an unsupported rename")
            if self.old_path != self.rename_from or self.new_path != self.rename_to:
                raise OracleDiffError("complete compare diff has an unsupported rename")
            return self.rename_to, self.rename_from, "renamed"
        if self.similarity != 100 or self.additions != 0 or self.deletions != 0:
            raise OracleDiffError("complete compare diff has an unsupported rename")
        if self.old_path is not None or self.new_path is not None:
            raise OracleDiffError("complete compare diff has an unsupported rename")
        return self.rename_to, self.rename_from, "renamed"

    def _added_identity(self, path: str) -> tuple[str, str | None, str]:
        if self.old_path is not None or self.new_is_dev_null:
            raise OracleDiffError("complete compare diff is malformed")
        if self.hunk_lines:
            if not self.old_is_dev_null or self.new_path != path:
                raise OracleDiffError("complete compare diff is malformed")
        elif self.old_is_dev_null or (self.new_path is not None and self.new_path != path):
            raise OracleDiffError("complete compare diff is malformed")
        return path, None, "added"

    def _removed_identity(self, path: str) -> tuple[str, str | None, str]:
        if self.new_path is not None or self.old_is_dev_null:
            raise OracleDiffError("complete compare diff is malformed")
        if self.hunk_lines:
            if not self.new_is_dev_null or self.old_path != path:
                raise OracleDiffError("complete compare diff is malformed")
        elif self.new_is_dev_null or (self.old_path is not None and self.old_path != path):
            raise OracleDiffError("complete compare diff is malformed")
        return path, None, "removed"

    def _require_modify_headers(self, path: str) -> None:
        if self.old_is_dev_null or self.new_is_dev_null:
            raise OracleDiffError("complete compare diff is malformed")
        if self.old_path != path or self.new_path != path:
            raise OracleDiffError("complete compare diff is malformed")


def _require_regular_mode(mode: str) -> str:
    if mode not in _REGULAR_FILE_MODES:
        raise OracleDiffError("complete compare diff has unsupported metadata")
    return mode


def _parse_similarity(line: str) -> int:
    payload = line[len("similarity index ") :]
    if not payload.endswith("%"):
        raise OracleDiffError("complete compare diff is malformed")
    value = payload[:-1]
    if not value.isdigit():
        raise OracleDiffError("complete compare diff is malformed")
    percent = int(value)
    if percent > 100:
        raise OracleDiffError("complete compare diff is malformed")
    return percent
