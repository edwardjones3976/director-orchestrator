"""Construct target-free, construction-only KEP inputs. No model or held-out access."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile

if "--count-only" in sys.argv:
    sys.dont_write_bytecode = True

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "train"))
import prompt_template

REPOSITORY = "kubernetes/enhancements"
COMMIT = "f7be055669b365e9b4ab3905bda6ef385e0c84ca"
PINS = {
    "archive": "da037ba8c8e68aa7033d256beff5da7cf3605b83678d46b0f6c403947c574618",
    "license": "b40930bbcf80744c86c46a12bc9da056641d722716c378f5659b9e555ef833e1",
    "tree": "91f07eb949f3a66ea8dd48022179a60bf8df3fe7cc529ee0fa53a663b46d3f9b",
    "census": "df00d70bc30e77bc2b77c1c147e45f046aff3c9a1cc286dce16aa8acd1736bca",
}
SUPPORT_SECTIONS = ("Non-Goals", "Notes/Constraints/Caveats", "Alpha Graduation Criteria", "Test Plan")
CONTEXT_SECTIONS = ("Summary", "Motivation", "Goals") + SUPPORT_SECTIONS
ORDER = "sig-round-robin/numeric-id-path/v1"
MAX_ARTIFACT = 2 * 1024 * 1024
MAX_SOURCE = 1024 * 1024
MAX_SOURCE_TOTAL = 64 * 1024 * 1024
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_MEMBERS = 20000
MAX_EXCLUDED_PATHS = 256
SECRET_MARKERS = ("_KEY=", "_TOKEN=", "_SECRET=", "DSN=", "PASSWORD=", "xoxb-", "xapp-")
RECORD_KEYS = frozenset({
    "protocol", "disposition", "source_group", "kep_number", "selection_rank", "source_ref",
    "source_sha256", "source_git_blob", "policy_sha256", "qualified_selection_sha256",
    "source_captured_at", "generated_at", "input", "title_span", "objective_span", "context_spans",
})

# -- Rule v3 (length-budgeted, two-alias) constants. The v2 path above is untouched;
# every name below is new and only reached when a policy's protocol is v3. --
V3_AUTHORITY = "issue299/comment6004258885"         # allocation amendment; supersedes the fixed-cap authority 6000111441
V3_SECTION_ALIASES = {"Notes/Constraints/Caveats": ["Risks and Mitigations"], "Alpha Graduation Criteria": ["Graduation Criteria"]}
# Reverse lookup used only to recognize an alias HEADING; the original heading
# text and wording are always kept verbatim, never renamed to the role name.
V3_ALIAS_HEADING_ROLE = {"risks and mitigations": "Notes/Constraints/Caveats", "graduation criteria": "Alpha Graduation Criteria"}
V3_ALIAS_PRECEDENCE = "canonical-nonempty-before-alias; duplicate-refuses; no-post-trim-fallback"
TEMPLATE_PATH = "keps/NNNN-kep-template/README.md"
TEMPLATE_SHA256 = "38ed9f6e5c99bf72398fdb7c048515783bf20c1a4a1d691dc1d82e921eb63db7"
TEMPLATE_GIT_BLOB = "d7d230223518a9982a6d8f717b285d3e76a2c45b"
V3_TEMPLATE = {"path": TEMPLATE_PATH, "sha256": TEMPLATE_SHA256, "git_blob": TEMPLATE_GIT_BLOB}
# Shared allocation (addendum ecf5d343...a0f5): the five mandatory minima (objective plus each support role's retained heading/prefix and first
# qualifying whole paragraph) are reserved first inside one 2,100-character pool; support roles then fill in order from what remains; Summary and
# Motivation keep separate 200-character budgets that never transfer. Costs are Unicode code points of the exact emitted text.
V3_MANDATORY_BUDGET = 2100
V3_TOTAL_BUDGET = 2500
V3_OPTIONAL_BUDGETS = {"Summary": 200, "Motivation": 200}
V3_ALLOCATION = {"mode": "shared-mandatory-minima-then-ordered-prefix/v1", "mandatory_character_budget": V3_MANDATORY_BUDGET,
                 "total_character_budget": V3_TOTAL_BUDGET, "mandatory_reservation_order": ["objective", *SUPPORT_SECTIONS],
                 "support_fill_order": list(SUPPORT_SECTIONS), "objective_selection": "first-qualifying-whole-contiguous-paragraph-only",
                 "nonfitting_paragraph": "stop-current-role; continue-next-role; no-skip-within-role",
                 "optional_character_budgets": V3_OPTIONAL_BUDGETS, "optional_budget_transfer": False,
                 "character_accounting": "unicode-codepoints-of-exact-emitted-span-text; retained-separators-included; no-inserted-join-separators"}
V3_TRIM = {"line_match": "exact-unicode-excluding-newline",
           "paragraphs": "source-order-whole-prefix; no-oversized-first-paragraph-skip",
           "preserve_original_spans": True, "duplicate_goals_context": False,
           "allocation": V3_ALLOCATION}
V3_TOKEN_GATE = {"maximum_input_prompt_tokens": 1400, "target_median": 1000, "minimum_usable_inputs": 200,
                 "implementation": "separate-pinned-offline-tokenizer; over-limit-refused-not-retrimmed"}
V3_POLICY_KEYS = frozenset({
    "protocol", "authority", "repository", "commit", "statuses", "creation_date_filter", "order", "frozen_at",
    "required_sections", "section_aliases", "alias_precedence", "template", "trim", "postconstruction_token_gate",
})


class Refusal(ValueError):
    """Only a fixed reason code is exposed to the operator."""
    def __init__(self, reason, summary=None):
        super().__init__(reason)
        self.summary = summary


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise Refusal("DUPLICATE_JSON_MEMBER")
            result[key] = value
        return result
    try:
        def invalid_constant(_value):
            raise Refusal("INVALID_JSON")
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (UnicodeError, json.JSONDecodeError, TypeError):
        raise Refusal("INVALID_JSON") from None


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def utc(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value):
        raise Refusal("INVALID_TIMESTAMP")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise Refusal("INVALID_TIMESTAMP") from None


def next_clock(clock, after):
    value = clock()
    if not utc(after) <= utc(value) <= datetime.now(timezone.utc):
        raise Refusal("INVALID_CHRONOLOGY")
    return value


def guard_path(path, reason="PROTECTED_PATH"):
    """Refuse protected components and ambiguous Win32 spellings before access."""
    for part in Path(path).parts:
        if part.endswith((".", " ")) or part.casefold() == "heldout":
            raise Refusal(reason)


def pinned_file(path, pin, limit=MAX_ARTIFACT):
    path = Path(path).absolute()
    guard_path(path)
    if not isinstance(pin, str) or re.fullmatch(r"[0-9a-f]{64}", pin) is None:
        raise Refusal("INVALID_PIN")
    try:
        raw = _read_artifact(path, limit)
    except OSError:
        raise Refusal("UNREADABLE_ARTIFACT") from None
    if len(raw) > limit or digest(raw) != pin:
        raise Refusal("ARTIFACT_PIN_MISMATCH")
    return raw


def safe_archive_name(name):
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        return False
    return not name.startswith("/") and all(part not in ("", ".", "..") for part in name.rstrip("/").split("/"))


def visible_lines(text):
    """Mask comments at original offsets; fence literals cannot open comments."""
    offset, in_comment, fence = 0, False, None
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not marker[2].strip():
                fence = None
            yield offset, line, True
            offset += len(line)
            continue
        if not in_comment and marker and not (marker[1][0] == "`" and "`" in marker[2]):
            fence = (marker[1][0], len(marker[1]))
            yield offset, line, True
            offset += len(line)
            continue
        visible = list(line)
        index = 0
        while index < len(line):
            if not in_comment and line.startswith("<!--", index):
                in_comment = True
            if in_comment:
                if line.startswith("-->", index):
                    for pos in range(index, min(index + 3, len(line))):
                        visible[pos] = " "
                    index += 3
                    in_comment = False
                    continue
                if visible[index] not in "\r\n":
                    visible[index] = " "
            index += 1
        clean = "".join(visible).rstrip("\r\n")
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", clean)
        if marker and not (marker[1][0] == "`" and "`" in marker[2]):
            fence = (marker[1][0], len(marker[1]))
            yield offset, "".join(visible), True
        else:
            yield offset, "".join(visible), False
        offset += len(line)


def heading_name(name):
    return re.sub(r"\s+\(optional\)$", "", name.casefold())


def source_sections(text):
    """Original Unicode offsets; comments and fenced-code headings are ignored."""
    headings = []
    for offset, visible, fenced in visible_lines(text):
        if not fenced:
            clean = visible.rstrip("\r\n")
            match = re.match(r"^ {0,3}(#{1,6})[ \t]+(.*)$", clean)
            if match:
                name = re.sub(r"[ \t]+#+[ \t]*$", "", match[2]).strip(" \t")
                if not name:
                    continue
                start = match.start(2) + len(match[2]) - len(match[2].lstrip(" \t"))
                end = start + len(name)
                # A title containing removed comment syntax is ambiguous, not rewritten.
                headings.append((len(match[1]), name, offset, offset + len(visible),
                                 {"start": offset + start, "end": offset + end}))
    sections = {}
    title = None
    mapping = {name.casefold(): name for name in CONTEXT_SECTIONS}
    for index, (level, name, start, body, title_span) in enumerate(headings):
        if level == 1 and title is None:
            if text[title_span["start"]:title_span["end"]] != name:
                raise Refusal("AMBIGUOUS_INPUT_TITLE")
            title = title_span
            continue  # The document title is not also an input section.
        key_name = heading_name(name)
        key = mapping.get(key_name)
        if key_name == "alpha":
            parent = next((head for head in reversed(headings[:index]) if head[0] < level), None)
            if parent and heading_name(parent[1]) == "graduation criteria":
                # A single original span cannot include the parent role without also
                # including an earlier sibling. Refuse instead of citing Beta as Alpha.
                if any(head[2] > parent[2] for head in headings[:index]):
                    raise Refusal("ALPHA_PRECEDING_SIBLING")
                key = "Alpha Graduation Criteria"
        if key:
            if key in sections:
                raise Refusal("AMBIGUOUS_SECTION")
            end = next((head[2] for head in headings[index + 1:] if head[0] <= level), len(text))
            # Context preserves the source role, including Alpha's parent role.
            context_start = parent[2] if key == "Alpha Graduation Criteria" and key_name == "alpha" else start
            sections[key] = {"start": context_start, "body_start": body, "end": end}
    return title, sections


def visibly_nonempty(text):
    fence = None
    for _, visible, fenced in visible_lines(text):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", visible.rstrip("\r\n"))
        if fenced and marker:
            if fence is None:
                fence = (marker[1][0], len(marker[1]))
                continue  # The info string is a delimiter, not section content.
            if marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not marker[2].strip():
                fence = None
                continue
        if any(char.isalnum() for char in visible):
            return True
    return False


def validate_v3_policy(rule):
    """Closed-key, fixed-constant check for a kep-input-selection/v3 policy.

    Every field except ``frozen_at`` is a pinned constant (deep-equality against
    the accepted specification); ``frozen_at`` is validated by the caller the
    same way v2 validates it, via utc(). This never runs for a v2 policy and
    never changes the v2 validation above."""
    if not isinstance(rule, dict) or set(rule) != V3_POLICY_KEYS:
        raise Refusal("INVALID_POLICY")
    fixed = {
        "protocol": "kep-input-selection/v3", "authority": V3_AUTHORITY, "repository": REPOSITORY,
        "commit": COMMIT, "statuses": ["implemented", "implementable"], "creation_date_filter": None,
        "order": ORDER, "required_sections": list(SUPPORT_SECTIONS), "section_aliases": V3_SECTION_ALIASES,
        "alias_precedence": V3_ALIAS_PRECEDENCE, "template": V3_TEMPLATE, "trim": V3_TRIM,
        "postconstruction_token_gate": V3_TOKEN_GATE,
    }
    for key, value in fixed.items():
        if not _typed_equal(rule.get(key), value):
            raise Refusal("INVALID_POLICY")


def v4_policy(frozen_at):
    """The construction contract; approval of a trust root remains external."""
    from kep_constraints_v4 import SOURCE_SHA256
    return {
        "protocol": "kep-input-selection/v4", "authority": "WO-C1/WO-C2",
        "repository": REPOSITORY, "commit": COMMIT,
        "statuses": ["implemented", "implementable"], "creation_date_filter": None,
        "order": ORDER, "frozen_at": frozen_at, "required_sections": list(SUPPORT_SECTIONS),
        "section_aliases": {"Alpha Graduation Criteria": ["Graduation Criteria"]},
        "alias_precedence": V3_ALIAS_PRECEDENCE, "template": V3_TEMPLATE,
        "trim": V3_TRIM, "constraints_selector": {"rule": "A2", "source_sha256": SOURCE_SHA256},
        "postconstruction_token_gate": {key: value for key, value in V3_TOKEN_GATE.items()
                                        if key != "minimum_usable_inputs"},
    }


def _typed_equal(got, want):
    """Deep equality that also requires identical JSON types at every level: a Boolean never stands for an integer or the reverse, a float never
    for an integer, and every object must carry exactly the expected keys (unknown, missing or old fixed-cap keys therefore refuse)."""
    if type(got) is not type(want):
        return False
    if isinstance(want, dict):
        return set(got) == set(want) and all(_typed_equal(got[k], want[k]) for k in want)
    if isinstance(want, list):
        return len(got) == len(want) and all(_typed_equal(g, w) for g, w in zip(got, want))
    return got == want


def source_sections_v3(text):
    """Canonical detection is an unmodified copy of source_sections (v2): same
    title rule, same nested-Alpha form, same AMBIGUOUS_SECTION duplicate check,
    in the same order, so the v2 function above is never touched or reused in a
    way that could change its behavior.

    This additionally records the two configured alias headings ("Risks and
    Mitigations" for Notes/Constraints/Caveats, a plain "Graduation Criteria"
    for Alpha Graduation Criteria) in a SEPARATE map, with its own
    AMBIGUOUS_ALIAS duplicate check. A heading already consumed as the title or
    as a canonical section (including the "Graduation Criteria" parent of a
    nested canonical Alpha heading) is never also treated as an alias source."""
    headings = []
    for offset, visible, fenced in visible_lines(text):
        if not fenced:
            clean = visible.rstrip("\r\n")
            match = re.match(r"^ {0,3}(#{1,6})[ \t]+(.*)$", clean)
            if match:
                name = re.sub(r"[ \t]+#+[ \t]*$", "", match[2]).strip(" \t")
                if not name:
                    continue
                start = match.start(2) + len(match[2]) - len(match[2].lstrip(" \t"))
                end = start + len(name)
                headings.append((len(match[1]), name, offset, offset + len(visible),
                                 {"start": offset + start, "end": offset + end}))
    sections = {}
    title = None
    mapping = {name.casefold(): name for name in CONTEXT_SECTIONS}
    consumed = set()
    for index, (level, name, start, body, title_span) in enumerate(headings):
        if level == 1 and title is None:
            if text[title_span["start"]:title_span["end"]] != name:
                raise Refusal("AMBIGUOUS_INPUT_TITLE")
            title = title_span
            consumed.add(index)
            continue
        key_name = heading_name(name)
        key = mapping.get(key_name)
        parent = None
        if key_name == "alpha":
            parent = next((head for head in reversed(headings[:index]) if head[0] < level), None)
            if parent and heading_name(parent[1]) == "graduation criteria":
                if any(head[2] > parent[2] for head in headings[:index]):
                    raise Refusal("ALPHA_PRECEDING_SIBLING")
                key = "Alpha Graduation Criteria"
                for parent_index, head in enumerate(headings[:index]):
                    if head[2] == parent[2]:
                        consumed.add(parent_index)
        if key:
            if key in sections:
                raise Refusal("AMBIGUOUS_SECTION")
            end = next((head[2] for head in headings[index + 1:] if head[0] <= level), len(text))
            context_start = parent[2] if key == "Alpha Graduation Criteria" and key_name == "alpha" else start
            sections[key] = {"start": context_start, "body_start": body, "end": end}
            consumed.add(index)
    alias_sections = {}
    for index, (level, name, start, body, title_span) in enumerate(headings):
        if index in consumed:
            continue
        role = V3_ALIAS_HEADING_ROLE.get(heading_name(name))
        if role is None:
            continue
        if role in alias_sections:
            raise Refusal("AMBIGUOUS_ALIAS")
        end = next((head[2] for head in headings[index + 1:] if head[0] <= level), len(text))
        alias_sections[role] = {"start": start, "body_start": body, "end": end}
    return title, sections, alias_sections


def _physical_lines(text):
    """Every physical line of text, segmented the same way visible_lines
    segments it (str.splitlines), yielded as (start, end-with-terminator,
    content-with-the-terminator-removed)."""
    offset = 0
    for line in text.splitlines(keepends=True):
        split = line.splitlines()
        yield offset, offset + len(line), split[0] if split else ""
        offset += len(line)


def _document_lines(text):
    """One record per physical line: original offsets, raw content (for exact
    template-line matching) and masked/visible content plus fence state (for
    blank-line and visibility checks), built from the same two generators
    source_sections_v3 and visibly_nonempty already rely on."""
    lines = []
    for (start, end, raw), (_, visible, fenced) in zip(_physical_lines(text), visible_lines(text)):
        split = visible.splitlines()
        lines.append({"start": start, "end": end, "raw": raw, "visible": split[0] if split else "", "fenced": fenced})
    return lines


def _template_line_set(template_text):
    """Exact-Unicode, newline-excluded template body lines used for removal.

    Blank (whitespace-only) lines are deliberately excluded: a template
    necessarily contains many blank lines, and matching on them would strip
    every blank-line paragraph separator from every KEP body, destroying
    paragraph structure everywhere rather than removing boilerplate text."""
    return frozenset(content for _, _, content in _physical_lines(template_text) if content.strip())


def _surviving_spans(lines, removed_flags, start, end):
    """Contiguous original spans inside [start, end) after dropping any line
    flagged removed; a removed line always starts a new, separate span."""
    spans, current = [], None
    for line, removed in zip(lines, removed_flags):
        if line["start"] < start or line["start"] >= end:
            continue
        if removed:
            if current is not None:
                spans.append(current)
                current = None
            continue
        if current is None:
            current = {"start": line["start"], "end": line["end"]}
        else:
            current["end"] = line["end"]
    if current is not None:
        spans.append(current)
    return spans


def _source_paragraphs(lines, start, end):
    """Whole-paragraph spans inside [start, end) in source order, computed
    BEFORE template-line removal: a blank visible line ends a paragraph unless
    a fence is open, in which case an internal blank line never splits it."""
    paragraphs, current = [], None
    for line in lines:
        if line["start"] < start or line["start"] >= end:
            continue
        blank = not line["fenced"] and line["visible"].strip() == ""
        if blank:
            if current is not None:
                paragraphs.append(current)
                current = None
            continue
        if current is None:
            current = {"start": line["start"], "end": line["end"]}
        else:
            current["end"] = line["end"]
    if current is not None:
        paragraphs.append(current)
    return paragraphs


def _span_cost(text, span):
    """Unicode code points of the exact emitted text of one span (CR, LF and every other retained character count; no separator is added)."""
    return len(text[span["start"]:span["end"]])


def _spans_overlap(spans):
    """True when any two half-open source spans [start, end) share a character; spans that only touch (one ends where the next starts) do not."""
    ordered = sorted((span["start"], span["end"]) for span in spans)
    return any(later[0] < earlier[1] for earlier, later in zip(ordered, ordered[1:]))


def _qualifying_paragraph_runs(text, lines, removed_flags, source):
    """For each whole source paragraph of a section body, in source order, its surviving visible runs after template-line removal; a paragraph
    with no surviving visible content is omitted (it costs nothing and never blocks a later paragraph)."""
    out = []
    for paragraph in _source_paragraphs(lines, source["body_start"], source["end"]):
        runs = [run for run in _surviving_spans(lines, removed_flags, paragraph["start"], paragraph["end"])
                if visibly_nonempty(text[run["start"]:run["end"]])]
        if runs:
            out.append(runs)
    return out


def _select_budgeted_items(text, lines, removed_flags, source, budget):
    """A heading item plus a source-order whole-paragraph prefix of body items.

    The retained heading's own character length counts against the budget,
    same as retained paragraph text (only renderer metadata is excluded, and
    none of these helpers ever emit any). A source paragraph that template
    removal empties entirely contributes no content and no cost: it is not
    "an oversized first content paragraph" (it has no content at all), so it
    never blocks a later, real paragraph -- it is skipped for free. The
    prefix instead stops (without skipping past it) at the first paragraph
    that still has non-empty surviving content but would push the running
    total over budget."""
    heading = {"start": source["start"], "end": source["body_start"]}
    used = heading["end"] - heading["start"]
    if used > budget:
        return []
    paragraphs = _source_paragraphs(lines, source["body_start"], source["end"])
    accepted = []
    for paragraph in paragraphs:
        runs = [run for run in _surviving_spans(lines, removed_flags, paragraph["start"], paragraph["end"])
                if visibly_nonempty(text[run["start"]:run["end"]])]
        if not runs:
            continue  # Wholly template boilerplate; never blocks a later paragraph.
        length = sum(run["end"] - run["start"] for run in runs)
        if used + length > budget:
            break
        used += length
        accepted.extend(runs)
    if not accepted:
        return []
    return [heading] + accepted


class _WindowsDirectories:
    """Retained no-delete-sharing handles anchor every Windows path component."""
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes = ctypes
        class FileId(ctypes.Structure):
            _fields_ = [("volume", ctypes.c_ulonglong), ("identifier", ctypes.c_ubyte * 16)]
        class Attributes(ctypes.Structure):
            _fields_ = [("attributes", wintypes.DWORD), ("reparse_tag", wintypes.DWORD)]
        class UnicodeString(ctypes.Structure):
            _fields_ = [("length", wintypes.USHORT), ("maximum_length", wintypes.USHORT),
                        ("buffer", wintypes.LPWSTR)]
        class ObjectAttributes(ctypes.Structure):
            _fields_ = [("length", wintypes.ULONG), ("root", wintypes.HANDLE),
                        ("name", ctypes.POINTER(UnicodeString)), ("attributes", wintypes.ULONG),
                        ("security_descriptor", ctypes.c_void_p), ("security_qos", ctypes.c_void_p)]
        class StatusUnion(ctypes.Union):
            _fields_ = [("status", wintypes.LONG), ("pointer", ctypes.c_void_p)]
        class IoStatus(ctypes.Structure):
            _fields_ = [("status", StatusUnion), ("information", ctypes.c_size_t)]
        self.FileId, self.Attributes = FileId, Attributes
        self.UnicodeString, self.ObjectAttributes, self.IoStatus = UnicodeString, ObjectAttributes, IoStatus
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.create = kernel.CreateFileW
        self.create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        self.create.restype = wintypes.HANDLE
        self.info = kernel.GetFileInformationByHandleEx
        self.info.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.info.restype = wintypes.BOOL
        self.final_name = kernel.GetFinalPathNameByHandleW
        self.final_name.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        self.final_name.restype = wintypes.DWORD
        self.close = kernel.CloseHandle
        self.close.argtypes, self.close.restype = [wintypes.HANDLE], wintypes.BOOL
        self.disposition = kernel.SetFileInformationByHandle
        self.disposition.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.disposition.restype = wintypes.BOOL
        native = ctypes.WinDLL("ntdll")
        self.native_create = native.NtCreateFile
        self.native_create.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG,
            ctypes.POINTER(ObjectAttributes), ctypes.POINTER(IoStatus), ctypes.c_void_p,
            wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG]
        self.native_create.restype = wintypes.LONG
        self.native_error = native.RtlNtStatusToDosError
        self.native_error.argtypes, self.native_error.restype = [wintypes.LONG], wintypes.ULONG
        self.Handle = wintypes.HANDLE

    def open(self, path, *, sharing=3, access=0x80000000, creation=3, flags=0x02200000):
        guard_path(path)
        handle = self.create(str(path), access, sharing, None, creation, flags, None)
        if handle == self.ctypes.c_void_p(-1).value:
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        try:
            self.guard_handle_path(handle)
            return handle
        except BaseException:
            self.close(handle)
            raise

    def guard_handle_path(self, handle):
        """Check the normalized name of THIS acquired object, before content access.

        FILE_NAME_NORMALIZED | VOLUME_NAME_DOS (flags 0) expands short names.
        Microsoft documents a too-small result as the required size including NUL.
        No pathname resolution or reopened object is used as a weaker fallback.
        """
        size = 1024
        while size <= 32768:
            buffer = self.ctypes.create_unicode_buffer(size)
            count = self.final_name(handle, buffer, size, 0)
            if count == 0:
                raise self.ctypes.WinError(self.ctypes.get_last_error())
            if count < size:
                guard_path(buffer.value)
                return
            size = count + 1
        raise Refusal("PROTECTED_PATH")

    def identity(self, handle):
        file_id, attributes = self.FileId(), self.Attributes()
        if not self.info(handle, 18, self.ctypes.byref(file_id), self.ctypes.sizeof(file_id)) or not self.info(
                handle, 9, self.ctypes.byref(attributes), self.ctypes.sizeof(attributes)):
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        if not attributes.attributes & 0x10 or attributes.attributes & 0x400:
            raise Refusal("PROTECTED_OUTPUT_PATH")
        return file_id.volume, bytes(file_id.identifier)

    def snapshot(self, path):
        handle = self.open(path, sharing=7)
        try:
            return self.identity(handle)
        finally:
            self.close(handle)

    def open_relative(self, parent, name, *, directory):
        """Open one child of an acquired object; never follow its reparse point."""
        if not name or name in (".", "..") or any(char in name for char in "/\\:\0"):
            raise Refusal("PROTECTED_PATH")
        guard_path(name)
        buffer = self.ctypes.create_unicode_buffer(name)
        length = len(name.encode("utf-16-le"))
        if length + 2 > 65535:
            raise Refusal("PROTECTED_PATH")
        string = self.UnicodeString(length, length + 2, self.ctypes.cast(buffer, self.ctypes.c_wchar_p))
        attributes = self.ObjectAttributes(self.ctypes.sizeof(self.ObjectAttributes), parent,
            self.ctypes.pointer(string), 0x40, None, None)
        handle, status = self.Handle(), self.IoStatus()
        access = 0x00100081 if directory else 0x80100080
        options = 0x00200021 if directory else 0x00200060
        code = self.native_create(self.ctypes.byref(handle), access, self.ctypes.byref(attributes),
            self.ctypes.byref(status), None, 0, 3 if directory else 1, 1, options, None, 0)
        if code != 0:
            if handle.value:
                self.close(handle.value)
            raise self.ctypes.WinError(self.native_error(code))
        try:
            info = self.Attributes()
            if not self.info(handle.value, 9, self.ctypes.byref(info), self.ctypes.sizeof(info)):
                raise self.ctypes.WinError(self.ctypes.get_last_error())
            if info.attributes & 0x400 or bool(info.attributes & 0x10) != directory:
                raise Refusal("ARTIFACT_REPARSE")
            self.guard_handle_path(handle.value)
            return handle.value
        except BaseException:
            self.close(handle.value)
            raise

    def delete_owned(self, handle):
        # FILE_DISPOSITION_INFO contains a one-byte BOOLEAN, not a Win32 BOOL.
        value = self.ctypes.c_ubyte(1)
        if not self.disposition(handle, 4, self.ctypes.byref(value), self.ctypes.sizeof(value)):
            raise self.ctypes.WinError(self.ctypes.get_last_error())

    def create_directory(self, parent, name):
        """FILE_CREATE returns the birth handle; never mkdir then authenticate a pathname."""
        if not parent or not name or name in (".", "..") or any(char in name for char in "\\/:\0"):
            raise Refusal("PROTECTED_OUTPUT_PATH")
        guard_path(name, "PROTECTED_OUTPUT_PATH")
        buffer = self.ctypes.create_unicode_buffer(name)
        length = len(name.encode("utf-16-le"))
        if length + 2 > 65535:
            raise Refusal("PROTECTED_OUTPUT_PATH")
        string = self.UnicodeString(length, length + 2, self.ctypes.cast(buffer, self.ctypes.c_wchar_p))
        attributes = self.ObjectAttributes(self.ctypes.sizeof(self.ObjectAttributes), parent,
            self.ctypes.pointer(string), 0x40, None, None)  # OBJ_CASE_INSENSITIVE, relative RootDirectory.
        handle, status = self.Handle(), self.IoStatus()
        # SYNCHRONIZE | FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES, share read/write only;
        # FILE_CREATE, DIRECTORY_FILE | SYNCHRONOUS_IO_NONALERT | OPEN_REPARSE_POINT.
        code = self.native_create(self.ctypes.byref(handle), 0x00100081, self.ctypes.byref(attributes),
            self.ctypes.byref(status), None, 0x10, 3, 2, 0x00200021, None, 0)
        if code != 0:
            if handle.value:
                self.close(handle.value)
            raise self.ctypes.WinError(self.native_error(code))
        if not handle.value or status.information != 2:  # FILE_CREATED, not FILE_OPENED.
            if handle.value:
                self.close(handle.value)
            raise Refusal("OUTPUT_BIRTH_NOT_CREATED")
        try:
            self.guard_handle_path(handle.value)
            return handle.value
        except BaseException:
            self.close(handle.value)
            raise


def _read_artifact(path, limit):
    """Read through held no-follow objects; hashes authenticate the resulting bytes."""
    if os.name != "nt":
        raise Refusal("ARTIFACT_PLATFORM_UNQUALIFIED")
    import msvcrt
    windows, directories, leaf, fd = _WindowsDirectories(), [], None, None
    try:
        parent = windows.open(Path(path.anchor))
        directories.append(parent)
        windows.identity(parent)
        for name in path.parts[1:-1]:
            parent = windows.open_relative(parent, name, directory=True)
            directories.append(parent)
        leaf = windows.open_relative(parent, path.name, directory=False)
        fd = msvcrt.open_osfhandle(leaf, os.O_RDONLY | os.O_BINARY)
        leaf = None  # fd owns this same held leaf object until after the read.
        chunks, remaining = [], limit + 1
        while remaining:
            chunk = os.read(fd, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    finally:
        if fd is not None:
            os.close(fd)
        if leaf is not None:
            windows.close(leaf)
        for handle in reversed(directories):
            windows.close(handle)


class _Publication:
    """Publish through acquired directory objects, never a mutable output pathname."""
    def __init__(self, out):
        self.out = out
        self.directories, self.files = [], []
        self.windows = _WindowsDirectories() if os.name == "nt" else None
        self.output_handle = None

    def _snapshot(self, path, parent=None):
        return self.windows.snapshot(path)

    def _take_directory(self, path, parent, expected):
        handle = None
        try:
            handle = self.windows.open(path)  # FILE_SHARE_DELETE deliberately absent.
            identity = self.windows.identity(handle)
            if identity != expected:
                raise Refusal("OUTPUT_PATH_RACE")
            self.directories.append(handle)
            return handle
        except BaseException:
            if handle is not None:
                self.windows.close(handle)
            raise

    def _create_directory(self, path, parent):
        handle = self.windows.create_directory(parent, path.name)
        try:
            self.windows.identity(handle)  # Validate the returned birth object, not an output-path snapshot.
            self.directories.append(handle)
            return handle
        except BaseException:
            self.windows.close(handle)
            raise

    def __enter__(self):
        try:
            if self.windows is None:
                raise Refusal("OUTPUT_PLATFORM_UNQUALIFIED")
            guard_path(self.out, "PROTECTED_OUTPUT_PATH")
            chain = list(reversed(self.out.parent.parents)) + [self.out.parent]
            snapshots = {}
            for path in chain:
                try:
                    snapshots[path] = self._snapshot(path)
                except FileNotFoundError:
                    snapshots[path] = None
            parent = None
            for path in chain:
                expected = snapshots[path]
                if expected is None:
                    try:
                        parent = self._create_directory(path, parent)
                    except FileExistsError:
                        raise Refusal("OUTPUT_PATH_RACE") from None
                else:
                    parent = self._take_directory(path, parent, expected)
            try:
                self.output_handle = self._create_directory(self.out, parent)
            except FileExistsError:
                raise Refusal("OUTPUT_EXISTS") from None
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _write_temp(self, raw):
        name = ".kep-" + os.urandom(16).hex()
        import msvcrt
        handle = self.windows.open(self.out / name, sharing=1, access=0x40010000, creation=1, flags=0x00200080)
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
        except BaseException:
            self.windows.close(handle)
            raise
        self.files.append((name, fd))
        remaining = memoryview(raw)
        while remaining:
            count = os.write(fd, remaining)
            if count <= 0:
                raise OSError("short publication write")
            remaining = remaining[count:]
        os.fsync(fd)
        return name

    def publish(self, data, manifest):
        for final, raw in (("inputs.jsonl", data), ("manifest.json", manifest)):
            staged = self._write_temp(raw)
            # All ancestor/destination and staging-file objects deny rename/delete.
            os.link(self.out / staged, self.out / final)
            # The complete manifest is linked last, never exposed while being written.

    def __exit__(self, *_exc):
        for name, fd in reversed(self.files):
            try:
                import msvcrt
                self.windows.delete_owned(msvcrt.get_osfhandle(fd))
            except OSError:
                pass  # Preserve the owned temporary; never delete by a mutable pathname.
            finally:
                os.close(fd)
        self.files.clear()
        for handle in reversed(self.directories):
            self.windows.close(handle)
        self.directories.clear()


def metadata_identity(raw):
    """Inspect YAML nodes only; no constructors, implicit dates or merge keys."""
    try:
        node = yaml.compose(raw.decode("utf-8"), Loader=yaml.BaseLoader)
        if not isinstance(node, yaml.MappingNode):
            raise Refusal("INVALID_METADATA")
        fields = {}
        for key, value in node.value:
            if not isinstance(key, yaml.ScalarNode) or key.value in fields or key.value == "<<":
                raise Refusal("INVALID_METADATA")
            fields[key.value] = value
        number, status = fields.get("kep-number"), fields.get("status")
        if any(not isinstance(value, yaml.ScalarNode) or value.style in ("|", ">") for value in (number, status)):
            raise Refusal("INVALID_METADATA")
        if re.fullmatch(r"[0-9]+", number.value) is None:
            raise Refusal("INVALID_METADATA")
        return int(number.value), status.value
    except (UnicodeError, yaml.YAMLError, RecursionError):
        raise Refusal("INVALID_METADATA") from None


def trim_span(text, span):
    raw = text[span["start"]:span["end"]]
    leading = len(raw) - len(raw.lstrip())
    trailing = len(raw) - len(raw.rstrip())
    return {"start": span["start"] + leading, "end": span["end"] - trailing}


def validate_record(record, text):
    if set(record) != RECORD_KEYS or record["protocol"] != "kep-input/v1" or record["disposition"] != "construction_only":
        raise Refusal("INVALID_INPUT_RECORD")
    if type(record["kep_number"]) is not int or record["kep_number"] < 1 or type(record["selection_rank"]) is not int or record["selection_rank"] < 0 or record["source_group"] != f"licensed/{REPOSITORY}/kep-{record['kep_number']}":
        raise Refusal("INVALID_INPUT_IDENTITY")
    for key in ("source_sha256", "policy_sha256", "qualified_selection_sha256"):
        if not isinstance(record[key], str) or re.fullmatch(r"[0-9a-f]{64}", record[key]) is None:
            raise Refusal("INVALID_INPUT_IDENTITY")
    raw = text.encode("utf-8")
    blob = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
    if not safe_archive_name(record["source_ref"]) or record["source_sha256"] != digest(raw) or record["source_git_blob"] != blob:
        raise Refusal("SOURCE_PIN_MISMATCH")
    item = record["input"]
    if not isinstance(item, dict) or set(item) != {"objective", "repository", "title", "context"}:
        raise Refusal("INVALID_INPUT_SHAPE")
    if prompt_template.input_contract(item) != prompt_template.INPUT_CONTRACT_CONTEXT or prompt_template.context_shape_errors(item["context"]):
        raise Refusal("INVALID_INPUT_SHAPE")
    if item["repository"] != REPOSITORY or any(not isinstance(item[key], str) or not item[key].strip() for key in ("objective", "title")):
        raise Refusal("INVALID_INPUT_SHAPE")
    def excerpt(span):
        if not isinstance(span, dict) or set(span) != {"start", "end"} or any(type(span[key]) is not int for key in span):
            raise Refusal("INVALID_SPAN")
        if not 0 <= span["start"] < span["end"] <= len(text):
            raise Refusal("INVALID_SPAN")
        return text[span["start"]:span["end"]]
    if item["title"] != excerpt(record["title_span"]) or item["objective"] != excerpt(record["objective_span"]):
        raise Refusal("INPUT_SPAN_MISMATCH")
    spans = record["context_spans"]
    if len(spans) != len(item["context"]):
        raise Refusal("CONTEXT_SPAN_MISMATCH")
    for block, span in zip(item["context"], spans):
        if block["kind"] != "assignment_packet" or block["source_ref"] != record["source_ref"] or block["source_sha256"] != record["source_sha256"] or block["text"] != excerpt(span):
            raise Refusal("CONTEXT_SPAN_MISMATCH")
    if not utc(record["source_captured_at"]) <= utc(record["generated_at"]) <= datetime.now(timezone.utc):
        raise Refusal("INVALID_CHRONOLOGY")
    if any(marker in json.dumps(record, ensure_ascii=False) for marker in SECRET_MARKERS):
        raise Refusal("SECRET_PATTERN")


def prepare(*, archive, tree, census, license_file, policy=None, policy_sha256=None, out=None, dry_run=False,
            clock=utc_now, _pins=None, rule_version=None, count_only=False):
    """_pins injects only synthetic fixture identities; the CLI uses frozen PINS."""
    pins = PINS if _pins is None else _pins
    if count_only:
        if rule_version != "v4" or any(value is not None for value in (out, policy, policy_sha256)) or dry_run:
            raise Refusal("INVALID_COUNT_ONLY_ARGUMENTS")
    else:
        if any(value is None for value in (out, policy, policy_sha256)):
            raise Refusal("MISSING_CONSTRUCTION_ARGUMENTS")
        out = Path(out).absolute()
        guard_path(out, "PROTECTED_OUTPUT_PATH")
        if any(parent.is_symlink() or parent.is_junction() for parent in (out, *out.parents)):
            raise Refusal("PROTECTED_OUTPUT_PATH")
        if not dry_run and (out.exists() or out.is_symlink()):
            raise Refusal("OUTPUT_EXISTS")
    raw_archive = pinned_file(archive, pins["archive"], MAX_ARCHIVE)
    raw_tree = pinned_file(tree, pins["tree"])
    raw_census = pinned_file(census, pins["census"])
    pinned_file(license_file, pins["license"])
    # A count-only snapshot is not a frozen publication policy or approval receipt.
    raw_policy = json_bytes(v4_policy(clock())) if count_only else pinned_file(policy, policy_sha256, 65536)
    if count_only:
        policy_sha256 = digest(raw_policy)
    rule = strict_json(raw_policy)
    if not isinstance(rule, dict):
        raise Refusal("INVALID_POLICY")
    protocol = rule.get("protocol")
    if rule_version is not None and (rule_version not in ("v2", "v3", "v4") or protocol != "kep-input-selection/" + rule_version):
        raise Refusal("RULE_POLICY_MISMATCH")
    if protocol == "kep-input-selection/v4":
        if not _typed_equal(rule, v4_policy(rule.get("frozen_at"))):
            raise Refusal("INVALID_POLICY")
    elif protocol == "kep-input-selection/v3":
        # v3 is additive only; the v2 branch below is untouched and a v2
        # policy is validated exactly as it always was.
        validate_v3_policy(rule)
    else:
        policy_keys = {"protocol", "repository", "commit", "statuses", "creation_date_filter", "required_sections", "order", "frozen_at", "authority"}
        if set(rule) != policy_keys or rule.get("authority") != "issue299/comment5973258630" or rule.get("protocol") != "kep-input-selection/v2" or rule.get("repository") != REPOSITORY or rule.get("commit") != COMMIT or rule.get("statuses") != ["implemented", "implementable"] or rule.get("creation_date_filter") is not None or rule.get("required_sections") != list(SUPPORT_SECTIONS) or rule.get("order") != ORDER:
            raise Refusal("INVALID_POLICY")
    frozen_at = rule.get("frozen_at")
    utc(frozen_at)
    started_at = next_clock(clock, frozen_at)
    if not count_only and utc(started_at) <= utc(frozen_at):
        raise Refusal("INVALID_CHRONOLOGY")
    metadata, upstream = strict_json(raw_census), strict_json(raw_tree)
    if not isinstance(metadata, dict) or metadata.get("repo") != REPOSITORY or metadata.get("commit") != COMMIT or metadata.get("archive_sha256") != pins["archive"] or not isinstance(metadata.get("items"), list):
        raise Refusal("INVALID_CENSUS")
    if not isinstance(upstream, dict) or upstream.get("truncated") is not False or not isinstance(upstream.get("tree"), list):
        raise Refusal("INVALID_TREE")
    blobs = {}
    for entry in upstream["tree"]:
        if not isinstance(entry, dict) or not safe_archive_name(entry.get("path")):
            raise Refusal("INVALID_TREE")
        if entry.get("type") == "blob":
            if entry["path"] in blobs or re.fullmatch(r"[0-9a-f]{40}", str(entry.get("sha"))) is None:
                raise Refusal("INVALID_TREE")
            blobs[entry["path"]] = entry["sha"]
    decisions, eligible, seen_paths, seen_ids = [], [], set(), set()
    for entry in metadata["items"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("status"), str):
            raise Refusal("INVALID_CENSUS")
        path, sig = entry.get("path"), entry.get("sig")
        # The legacy census field "sig" stores the original namespace, including
        # historical provider-* groups; retain it as the round-robin bucket.
        if not isinstance(path, str) or not safe_archive_name(path) or re.fullmatch(r"keps/(?:sig|provider)-[a-z0-9-]+/[A-Za-z0-9._-]+/kep\.yaml", path) is None or sig != path.split("/")[1] or path in seen_paths:
            raise Refusal("INVALID_CENSUS")
        seen_paths.add(path)
        decision = {"path": path, "sig": sig, "status": entry.get("status"), "kep_number": None,
                    "selection_rank": None, "section_qualified": False, "disposition": "refused", "reason": None}
        decisions.append(decision)
        number = entry.get("kep_id")
        if type(number) is int:
            number = str(number)
        directory_number = re.match(r"([0-9]+)(?:-|$)", path.split("/")[2])
        if not isinstance(number, str) or not number.isascii() or not number.isdigit():
            raise Refusal("CENSUS_IDENTITY_MISMATCH")
        number = int(number)
        decision["kep_number"] = number  # Retained for every authenticated census entry.
        if re.fullmatch(r"[0-9a-f]{64}", str(entry.get("metadata_sha256"))) is None:
            raise Refusal("INVALID_CENSUS")
        # Census/YAML identity is authoritative. Directory disagreements are
        # counted refusals, not identities to repair or a batch-wide veto.
        if entry.get("status") in ("implemented", "implementable") and number > 0:
            if number in seen_ids:
                raise Refusal("DUPLICATE_KEP_ID")
            seen_ids.add(number)
        if directory_number is None or int(directory_number[1]) != number:
            decision["kep_number"] = number
            decision["reason"] = "DIRECTORY_ID_MISMATCH"
            continue
        if entry.get("status") not in ("implemented", "implementable"):
            decision["reason"] = "INELIGIBLE_STATUS"
            continue
        if number < 1:
            decision["reason"] = "NONPOSITIVE_ID"
            continue
        decision["kep_number"] = number
        eligible.append((entry, decision))
    buckets = defaultdict(list)
    for pair in eligible:
        buckets[pair[0]["sig"]].append(pair)
    for bucket in buckets.values():
        bucket.sort(key=lambda pair: (pair[1]["kep_number"], pair[0]["path"]))
    ordered = [buckets[sig][index] for index in range(max((len(v) for v in buckets.values()), default=0))
               for sig in sorted(buckets) if index < len(buckets[sig])]
    for rank, (_, decision) in enumerate(ordered):
        decision["selection_rank"] = rank
    order_hash = digest(json_bytes([{key: decision[key] for key in ("path", "sig", "kep_number", "selection_rank")} for _, decision in ordered]))
    prepared, source_total = [], 0
    v4_stages = Counter()
    stage_before = stage_after = stage_paragraph = 0  # v3 funnel counts only; unused and unreported for v2.
    template_lines = frozenset()
    prefix = "enhancements-" + COMMIT + "/"
    last_time = started_at
    with tarfile.open(fileobj=io.BytesIO(raw_archive), mode="r:gz") as tar:
        members = {}
        excluded_links = set()
        for member in tar:
            if len(members) + len(excluded_links) >= MAX_MEMBERS or not safe_archive_name(member.name) or not (member.name == prefix.rstrip("/") or member.name.startswith(prefix)) or member.name in members or member.name in excluded_links or member.islnk():
                raise Refusal("INVALID_ARCHIVE_MEMBER")
            if member.issym():
                relative = member.name[len(prefix):] if member.name.startswith(prefix) else None
                if not safe_archive_name(relative):
                    raise Refusal("INVALID_ARCHIVE_MEMBER")
                excluded_links.add(member.name)
            else:
                members[member.name] = member
        def read_member(path):
            if prefix + path in excluded_links:
                raise Refusal("SYMLINK_MEMBER")
            member = members.get(prefix + path)
            if member is None or not member.isfile() or not 0 <= member.size <= MAX_SOURCE:
                raise Refusal("NO_BOUNDED_REGULAR_SOURCE")
            handle = tar.extractfile(member)
            if handle is None:
                raise Refusal("NO_BOUNDED_REGULAR_SOURCE")
            with handle:
                raw = handle.read(MAX_SOURCE + 1)
            if len(raw) != member.size or len(raw) > MAX_SOURCE:
                raise Refusal("NO_BOUNDED_REGULAR_SOURCE")
            blob = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
            if blobs.get(path) != blob:
                raise Refusal("SOURCE_GIT_BLOB_MISMATCH")
            return raw, blob
        archive_license, _ = read_member("LICENSE")
        if digest(archive_license) != pins["license"]:
            raise Refusal("LICENSE_MISMATCH")
        if protocol in ("kep-input-selection/v3", "kep-input-selection/v4"):
            raw_template, template_blob = read_member(rule["template"]["path"])
            if digest(raw_template) != rule["template"]["sha256"] or template_blob != rule["template"]["git_blob"]:
                raise Refusal("TEMPLATE_PIN_MISMATCH")
            template_lines = _template_line_set(raw_template.decode("utf-8"))
        # Authenticate census semantics for every entry before reading bodies;
        # a false ineligible status must not silently remove a pinned source.
        for entry in metadata["items"]:
            metadata_raw, _ = read_member(entry["path"])
            if digest(metadata_raw) != entry["metadata_sha256"]:
                raise Refusal("METADATA_PIN_MISMATCH")
            number, status = metadata_identity(metadata_raw)
            if number != int(entry["kep_id"]) or status != entry["status"]:
                raise Refusal("CENSUS_METADATA_MISMATCH")
        for entry, decision in ordered:
            try:
                if protocol == "kep-input-selection/v4":
                    decision["role"] = "source"
                path = str(PurePosixPath(entry["path"]).parent / "README.md")
                raw, blob = read_member(path)
                source_total += len(raw)
                if source_total > MAX_SOURCE_TOTAL:
                    raise Refusal("CORPUS_SIZE_BOUND")
                text = raw.decode("utf-8")
                captured = next_clock(clock, last_time)
                last_time = captured
                if protocol == "kep-input-selection/v4":
                    from kep_constraints_v4 import analyse
                    analysis = analyse(text, template_lines)
                    v4_stages[analysis["stage"]] += 1
                    decision["stage"] = analysis["stage"]
                    decision["section_qualified"] = analysis["stage"] == "B"
                    if analysis["stage"] != "B":
                        decision["role"] = analysis["role"]
                        raise Refusal(analysis["reason"])
                    title_span, objective_span, spans = (analysis[key] for key in ("title_span", "objective_span", "context_spans"))
                    source_sha = digest(raw)
                    item = {"objective": text[objective_span["start"]:objective_span["end"]],
                            "title": text[title_span["start"]:title_span["end"]], "repository": REPOSITORY,
                            "context": [{"kind": "assignment_packet", "source_ref": path, "source_sha256": source_sha,
                                         "text": text[span["start"]:span["end"]]} for span in spans]}
                elif protocol == "kep-input-selection/v3":
                    # -- v3: alias-aware selection, then template-line removal,
                    # then a paragraph-budget trim. The v2 branch below (the
                    # `else`) is the original, untouched code. --
                    title_span, sections, alias_sections = source_sections_v3(text)
                    chosen = {}
                    for role in SUPPORT_SECTIONS:
                        canonical = sections.get(role)
                        if canonical is not None and visibly_nonempty(text[canonical["body_start"]:canonical["end"]]):
                            chosen[role] = canonical
                            continue
                        alias = alias_sections.get(role)
                        if alias is not None and visibly_nonempty(text[alias["body_start"]:alias["end"]]):
                            chosen[role] = alias
                            continue
                        raise Refusal("MISSING_OR_EMPTY_SUPPORT_SECTION")
                    decision["section_qualified"] = True  # Matches v2's meaning: the 4 roles alone, independent of title/Goals.
                    if title_span is None:
                        raise Refusal("INPUT_TITLE_MISSING")
                    if "Goals" not in sections or not visibly_nonempty(text[sections["Goals"]["body_start"]:sections["Goals"]["end"]]):
                        raise Refusal("INPUT_GOALS_MISSING")
                    stage_before += 1  # Title + Goals + all 4 chosen roles, per the v3 stage-count definition.

                    doc_lines = _document_lines(text)
                    removed_flags = [bool(rec["raw"].strip()) and rec["raw"] in template_lines for rec in doc_lines]

                    required_bodies = {"Goals": {"start": sections["Goals"]["body_start"], "end": sections["Goals"]["end"]}}
                    for role in SUPPORT_SECTIONS:
                        required_bodies[role] = {"start": chosen[role]["body_start"], "end": chosen[role]["end"]}
                    for body_span in required_bodies.values():
                        runs = _surviving_spans(doc_lines, removed_flags, body_span["start"], body_span["end"])
                        if not any(visibly_nonempty(text[run["start"]:run["end"]]) for run in runs):
                            raise Refusal("EMPTY_AFTER_TEMPLATE_REMOVAL")
                    stage_after += 1

                    goals_paragraphs = _source_paragraphs(doc_lines, required_bodies["Goals"]["start"], required_bodies["Goals"]["end"])
                    objective_paragraph = None
                    for paragraph in goals_paragraphs:
                        runs = [run for run in _surviving_spans(doc_lines, removed_flags, paragraph["start"], paragraph["end"])
                                if visibly_nonempty(text[run["start"]:run["end"]])]
                        if not runs:
                            continue  # Wholly template boilerplate; the real objective paragraph may follow it.
                        objective_paragraph = (paragraph, runs)
                        break
                    if objective_paragraph is None:
                        raise Refusal("NO_WHOLE_PARAGRAPH_WITHIN_BUDGET")
                    first_goals_paragraph, goals_runs = objective_paragraph
                    if len(goals_runs) > 1 or goals_runs[0]["start"] != first_goals_paragraph["start"] or goals_runs[0]["end"] != first_goals_paragraph["end"]:
                        raise Refusal("OBJECTIVE_SPAN_NOT_CONTIGUOUS")
                    objective_span = trim_span(text, goals_runs[0])

                    # Reserve every mandatory minimum BEFORE any fill: the objective and, per support role, its retained heading/prefix once plus all
                    # surviving runs of its first qualifying whole paragraph (never skipped, whatever its size).
                    minima, rests = {}, {}
                    for role in SUPPORT_SECTIONS:
                        qualifying = _qualifying_paragraph_runs(text, doc_lines, removed_flags, chosen[role])
                        if not qualifying:
                            raise Refusal("EMPTY_AFTER_TEMPLATE_REMOVAL")
                        minima[role] = [{"start": chosen[role]["start"], "end": chosen[role]["body_start"]}] + qualifying[0]
                        rests[role] = qualifying[1:]
                    used = _span_cost(text, objective_span) + sum(_span_cost(text, item) for role in SUPPORT_SECTIONS for item in minima[role])
                    if used > V3_MANDATORY_BUDGET:
                        raise Refusal("MANDATORY_MINIMA_OVER_BUDGET")
                    # Fill support roles only, in the fixed order, with each next whole paragraph that fits the remaining pool; the first one that does
                    # not fit stops that role (no skip within a role) and the next role continues. The objective never receives more.
                    selected = {role: list(minima[role]) for role in SUPPORT_SECTIONS}
                    for role in SUPPORT_SECTIONS:
                        for runs in rests[role]:
                            cost = sum(_span_cost(text, run) for run in runs)
                            if used + cost > V3_MANDATORY_BUDGET:
                                break
                            used += cost
                            selected[role].extend(runs)
                    context_items = [item for role in SUPPORT_SECTIONS for item in selected[role]]
                    optional_used = {}
                    for role in ("Summary", "Motivation"):
                        source = sections.get(role)
                        if source is None or not visibly_nonempty(text[source["body_start"]:source["end"]]):
                            continue
                        items = _select_budgeted_items(text, doc_lines, removed_flags, source, V3_OPTIONAL_BUDGETS[role])
                        optional_used[role] = sum(_span_cost(text, item) for item in items)
                        context_items.extend(items)
                    # The objective and every emitted context span must be pairwise disjoint source spans (touching is allowed). A recognized heading
                    # nested inside another section can otherwise put the same source characters in two items; refuse rather than clip or reallocate.
                    if _spans_overlap([objective_span] + context_items):
                        raise Refusal("CONTEXT_SPAN_OVERLAP")
                    # Independent reconciliation against the exact emitted strings.
                    mandatory_total = len(text[objective_span["start"]:objective_span["end"]]) + sum(
                        len(text[item["start"]:item["end"]]) for role in SUPPORT_SECTIONS for item in selected[role])
                    if (mandatory_total != used or mandatory_total > V3_MANDATORY_BUDGET
                            or any(optional_used[role] > V3_OPTIONAL_BUDGETS[role] for role in optional_used)
                            or mandatory_total + sum(optional_used.values()) > V3_TOTAL_BUDGET
                            or len({(item["start"], item["end"]) for item in context_items}) != len(context_items)):
                        raise Refusal("ALLOCATION_ACCOUNTING_MISMATCH")
                    stage_paragraph += 1

                    spans = sorted(context_items, key=lambda span: span["start"])
                    source_sha = digest(raw)
                    item = {"objective": text[objective_span["start"]:objective_span["end"]], "title": text[title_span["start"]:title_span["end"]], "repository": REPOSITORY,
                            "context": [{"kind": "assignment_packet", "source_ref": path, "source_sha256": source_sha, "text": text[span["start"]:span["end"]]} for span in spans]}
                else:
                    title_span, sections = source_sections(text)
                    if any(name not in sections or not visibly_nonempty(text[sections[name]["body_start"]:sections[name]["end"]]) for name in SUPPORT_SECTIONS):
                        raise Refusal("MISSING_OR_EMPTY_SUPPORT_SECTION")
                    decision["section_qualified"] = True
                    if title_span is None:
                        raise Refusal("INPUT_TITLE_MISSING")
                    if "Goals" not in sections or not visibly_nonempty(text[sections["Goals"]["body_start"]:sections["Goals"]["end"]]):
                        raise Refusal("INPUT_GOALS_MISSING")
                    objective_span = trim_span(text, {"start": sections["Goals"]["body_start"], "end": sections["Goals"]["end"]})
                    spans = sorted(({"start": sections[name]["start"], "end": sections[name]["end"]} for name in CONTEXT_SECTIONS if name in sections and visibly_nonempty(text[sections[name]["body_start"]:sections[name]["end"]])), key=lambda span: span["start"])
                    source_sha = digest(raw)
                    item = {"objective": text[objective_span["start"]:objective_span["end"]], "title": text[title_span["start"]:title_span["end"]], "repository": REPOSITORY,
                            "context": [{"kind": "assignment_packet", "source_ref": path, "source_sha256": source_sha, "text": text[span["start"]:span["end"]]} for span in spans]}
                if any(marker in json.dumps(item, ensure_ascii=False) for marker in SECRET_MARKERS):
                    raise Refusal("SECRET_PATTERN")
                prepared.append({"decision": decision, "text": text, "item": item, "spans": spans, "title_span": title_span, "objective_span": objective_span, "captured": captured, "source_ref": path, "source_sha": source_sha, "blob": blob})
                decision["disposition"], decision["reason"] = "construction_only", None
            except UnicodeError:
                decision["reason"] = "INVALID_UTF8"
            except Refusal as exc:
                if str(exc) in {"SOURCE_GIT_BLOB_MISMATCH", "METADATA_PIN_MISMATCH", "CORPUS_SIZE_BOUND", "INVALID_CHRONOLOGY"}:
                    raise
                decision["reason"] = str(exc)
    excluded_symlink_paths = sorted(name[len(prefix):] for name in excluded_links)[:MAX_EXCLUDED_PATHS]
    decisions.sort(key=lambda item: item["path"])
    v4_report = {}
    if protocol == "kep-input-selection/v4":
        v4_report = {
            "stage_counts": {"metadata_candidates": len(decisions), "eligible_groups": len(ordered),
                             "analysed_groups": sum(v4_stages.values()),
                             "lexical": sum(v4_stages[s] for s in ("L_ONLY", "W_ONLY", "B")),
                             "whole_source_bound": v4_stages["W_ONLY"] + v4_stages["B"],
                             "allocation_valid": v4_stages["B"], "usable_inputs": len(prepared),
                             "tokens": "NOT_MEASURED"},
            "refusals": [{"group_id": f"licensed/{REPOSITORY}/kep-{d['kep_number']}",
                          "role": d.get("role", "metadata"), "reason": d["reason"]}
                         for d in decisions if d["reason"]],
        }
        if count_only:
            kept = sorted((digest(f"licensed/{REPOSITORY}/kep-{s['decision']['kep_number']}".encode("utf-8")),
                           f"licensed/{REPOSITORY}/kep-{s['decision']['kep_number']}") for s in prepared)
            return {"protocol": "kep-input-counts/v4", "count_only": True, **v4_report,
                    "kept_groups": [{"group_id": group, "sha256": sha, "sha256_rank": rank}
                                    for rank, (sha, group) in enumerate(kept)]}
    qualification = {"protocol": "kep-input-qualification/v2", "policy_sha256": policy_sha256, "order_sha256": order_hash,
                     "qualified_at": next_clock(clock, last_time), "census": decisions, "excluded_symlink_members": len(excluded_links),
                     "excluded_symlink_member_paths": excluded_symlink_paths}
    qualification_sha = digest(json_bytes(qualification))
    last_time = qualification["qualified_at"]
    records = []
    for source in prepared:
        decision = source["decision"]
        generated = next_clock(clock, last_time)
        last_time = generated
        record = {"protocol": "kep-input/v1", "disposition": "construction_only", "source_group": f"licensed/{REPOSITORY}/kep-{decision['kep_number']}",
                  "kep_number": decision["kep_number"], "selection_rank": decision["selection_rank"], "source_ref": source["source_ref"],
                  "source_sha256": source["source_sha"], "source_git_blob": source["blob"], "policy_sha256": policy_sha256,
                  "qualified_selection_sha256": qualification_sha, "source_captured_at": source["captured"], "generated_at": generated,
                  "input": source["item"], "title_span": source["title_span"], "objective_span": source["objective_span"], "context_spans": source["spans"]}
        validate_record(record, source["text"])
        records.append(record)
    summary = {"metadata_candidates": len(decisions), "status_candidates": sum(item["status"] in ("implemented", "implementable") for item in decisions),
               "positive_unique_ids": len(ordered), "section_qualified": sum(item["section_qualified"] for item in decisions),
               "usable_inputs": len(records), "refused": len(decisions) - len(records), "refused_by_reason": dict(sorted(Counter(item["reason"] for item in decisions if item["reason"]).items())),
               "excluded_symlink_members": len(excluded_links),
               "excluded_symlink_member_paths": excluded_symlink_paths}
    stage_counts = None
    if protocol == "kep-input-selection/v3":
        stage_counts = {"status_candidates": summary["status_candidates"], "qualified_before_template_removal": stage_before,
                         "qualified_after_template_removal": stage_after, "paragraph_qualified": stage_paragraph,
                         "usable_inputs": summary["usable_inputs"]}
    elif protocol == "kep-input-selection/v4":
        stage_counts = v4_report["stage_counts"]
        summary["refusals"] = v4_report["refusals"]
    if not records:
        # v3 refusals carry the same qualification funnel as successful results (#463); v2 summaries are unchanged.
        raise Refusal("NO_USABLE_INPUTS", summary if stage_counts is None else dict(summary, stage_counts=stage_counts))
    payloads = [json_bytes(record) + b"\n" for record in records]
    data = b"".join(payloads)
    manifest = {"protocol": "kep-input-manifest/v1", "disposition": "construction_only", "repository": REPOSITORY, "commit": COMMIT,
                "pins": dict(pins), "policy_sha256": policy_sha256, "policy": rule, "policy_frozen_at": frozen_at,
                "construction_started_at": started_at, "recorded_at": next_clock(clock, last_time), "order_sha256": order_hash,
                "qualified_selection_sha256": qualification_sha, "qualification": qualification, "summary": summary,
                "inputs_sha256": digest(data), "records": [{"source_group": record["source_group"], "selection_rank": record["selection_rank"], "sha256": digest(payload)} for record, payload in zip(records, payloads)]}
    if stage_counts is not None:
        manifest["stage_counts"] = stage_counts
    if dry_run:
        return {"completion_status": "not_completed", "dry_run": True, **summary, **({"stage_counts": stage_counts} if stage_counts is not None else {})}
    manifest_data = json_bytes(manifest) + b"\n"
    with _Publication(out) as publication:
        publication.publish(data, manifest_data)
    # These hashes bind the bytes supplied to successful publication. Authenticity
    # comes from the external host receipt, never this caller-visible dictionary.
    return {"protocol": "kep-input-completion/v1", "completion_status": "completed",
            "dry_run": False, "inputs_sha256": digest(data),
            "manifest_sha256": digest(manifest_data), "policy_sha256": policy_sha256, **summary,
            **({"stage_counts": stage_counts} if stage_counts is not None else {})}


def main(argv=None):
    class Parser(argparse.ArgumentParser):
        def error(self, _message):
            self.exit(2, '{"refused": "USAGE_ERROR"}\n')
    parser = Parser(description=__doc__)
    for name in ("archive", "tree", "census", "license"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("policy", "out"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--policy-sha256")
    parser.add_argument("--rule", dest="rule_version", choices=("v2", "v3", "v4"))
    parser.add_argument("--count-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = vars(parser.parse_args(argv))
    args["license_file"] = args.pop("license")
    try:
        result = prepare(**args)
    except Refusal as exc:
        for refusal in (exc.summary or {}).get("refusals", []):
            print(json.dumps(refusal, sort_keys=True), file=sys.stderr)
        print(json.dumps({"refused": str(exc), **({"summary": exc.summary} if exc.summary is not None else {})}), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, AttributeError, tarfile.TarError):
        print(json.dumps({"refused": "INVALID_CONSTRUCTION_EVIDENCE"}), file=sys.stderr)
        return 1
    for refusal in result.pop("refusals", []):
        print(json.dumps(refusal, sort_keys=True), file=sys.stderr)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
