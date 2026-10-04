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
import tempfile

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
SECRET_MARKERS = ("_KEY=", "_TOKEN=", "_SECRET=", "DSN=", "PASSWORD=", "xoxb-", "xapp-")
RECORD_KEYS = frozenset({
    "protocol", "disposition", "source_group", "kep_number", "selection_rank", "source_ref",
    "source_sha256", "source_git_blob", "policy_sha256", "qualified_selection_sha256",
    "source_captured_at", "generated_at", "input", "title_span", "objective_span", "context_spans",
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


def pinned_file(path, pin, limit=MAX_ARTIFACT):
    path = Path(path).absolute()
    if "heldout" in {part.casefold() for part in path.parts}:
        raise Refusal("PROTECTED_PATH")
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise Refusal("ARTIFACT_SYMLINK")
    if not isinstance(pin, str) or re.fullmatch(r"[0-9a-f]{64}", pin) is None:
        raise Refusal("INVALID_PIN")
    try:
        if not path.is_file():
            raise Refusal("MISSING_ARTIFACT")
        with path.open("rb") as handle:
            raw = handle.read(limit + 1)
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
            if text[title_span["start"]:title_span["end"]] == name:
                title = title_span
                continue  # The document title is not also an input section.
        key_name = heading_name(name)
        key = mapping.get(key_name)
        if key_name == "alpha":
            parent = next((head for head in reversed(headings[:index]) if head[0] < level), None)
            if parent and heading_name(parent[1]) == "graduation criteria":
                key = "Alpha Graduation Criteria"
        if key:
            if key in sections:
                raise Refusal("AMBIGUOUS_SECTION")
            end = next((head[2] for head in headings[index + 1:] if head[0] <= level), len(text))
            sections[key] = {"start": body, "end": end}
    return title, sections


def visibly_nonempty(text):
    return any(char.isalnum() for _, visible, _ in visible_lines(text) for char in visible)


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


def prepare(*, archive, tree, census, license_file, policy, policy_sha256, out, dry_run=False,
            clock=utc_now, _pins=None):
    """_pins injects only synthetic fixture identities; the CLI uses frozen PINS."""
    pins = PINS if _pins is None else _pins
    out = Path(out).absolute()
    if "heldout" in {part.casefold() for part in out.parts} or any(parent.is_symlink() for parent in (out, *out.parents)):
        raise Refusal("PROTECTED_OUTPUT_PATH")
    if out.exists() or out.is_symlink():
        raise Refusal("OUTPUT_EXISTS")
    raw_archive = pinned_file(archive, pins["archive"], MAX_ARCHIVE)
    raw_tree = pinned_file(tree, pins["tree"])
    raw_census = pinned_file(census, pins["census"])
    pinned_file(license_file, pins["license"])
    raw_policy = pinned_file(policy, policy_sha256, 65536)
    rule = strict_json(raw_policy)
    policy_keys = {"protocol", "repository", "commit", "statuses", "creation_date_filter", "required_sections", "order", "frozen_at", "authority"}
    if not isinstance(rule, dict) or set(rule) != policy_keys or rule.get("authority") != "issue299/comment5973258630" or rule.get("protocol") != "kep-input-selection/v2" or rule.get("repository") != REPOSITORY or rule.get("commit") != COMMIT or rule.get("statuses") != ["implemented", "implementable"] or rule.get("creation_date_filter") is not None or rule.get("required_sections") != list(SUPPORT_SECTIONS) or rule.get("order") != ORDER:
        raise Refusal("INVALID_POLICY")
    frozen_at = rule.get("frozen_at")
    utc(frozen_at)
    started_at = next_clock(clock, frozen_at)
    if utc(started_at) <= utc(frozen_at):
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
    prefix = "enhancements-" + COMMIT + "/"
    last_time = started_at
    with tarfile.open(fileobj=io.BytesIO(raw_archive), mode="r:gz") as tar:
        members = {}
        for member in tar:
            if len(members) >= MAX_MEMBERS or not safe_archive_name(member.name) or not (member.name == prefix.rstrip("/") or member.name.startswith(prefix)) or member.name in members or member.issym() or member.islnk():
                raise Refusal("INVALID_ARCHIVE_MEMBER")
            members[member.name] = member
        def read_member(path):
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
                path = str(PurePosixPath(entry["path"]).parent / "README.md")
                raw, blob = read_member(path)
                source_total += len(raw)
                if source_total > MAX_SOURCE_TOTAL:
                    raise Refusal("CORPUS_SIZE_BOUND")
                text = raw.decode("utf-8")
                captured = next_clock(clock, last_time)
                last_time = captured
                title_span, sections = source_sections(text)
                if any(name not in sections or not visibly_nonempty(text[sections[name]["start"]:sections[name]["end"]]) for name in SUPPORT_SECTIONS):
                    raise Refusal("MISSING_OR_EMPTY_SUPPORT_SECTION")
                decision["section_qualified"] = True
                if title_span is None:
                    raise Refusal("INPUT_TITLE_MISSING")
                if "Goals" not in sections or not visibly_nonempty(text[sections["Goals"]["start"]:sections["Goals"]["end"]]):
                    raise Refusal("INPUT_GOALS_MISSING")
                objective_span = trim_span(text, sections["Goals"])
                spans = sorted((sections[name] for name in CONTEXT_SECTIONS if name in sections and visibly_nonempty(text[sections[name]["start"]:sections[name]["end"]])), key=lambda span: span["start"])
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
    decisions.sort(key=lambda item: item["path"])
    qualification = {"protocol": "kep-input-qualification/v2", "policy_sha256": policy_sha256, "order_sha256": order_hash,
                     "qualified_at": next_clock(clock, last_time), "census": decisions}
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
               "usable_inputs": len(records), "refused": len(decisions) - len(records), "refused_by_reason": dict(sorted(Counter(item["reason"] for item in decisions if item["reason"]).items()))}
    if not records:
        raise Refusal("NO_USABLE_INPUTS", summary)
    payloads = [json_bytes(record) + b"\n" for record in records]
    data = b"".join(payloads)
    manifest = {"protocol": "kep-input-manifest/v1", "disposition": "construction_only", "repository": REPOSITORY, "commit": COMMIT,
                "pins": dict(pins), "policy_sha256": policy_sha256, "policy": rule, "policy_frozen_at": frozen_at,
                "construction_started_at": started_at, "recorded_at": next_clock(clock, last_time), "order_sha256": order_hash,
                "qualified_selection_sha256": qualification_sha, "qualification": qualification, "summary": summary,
                "inputs_sha256": digest(data), "records": [{"source_group": record["source_group"], "selection_rank": record["selection_rank"], "sha256": digest(payload)} for record, payload in zip(records, payloads)]}
    if dry_run:
        return {"dry_run": True, **summary}
    out.parent.mkdir(parents=True, exist_ok=True)
    # Directory reservation is atomic/no-clobber. The manifest is installed last
    # as the commit marker; readers must ignore an interrupted directory lacking it.
    with tempfile.TemporaryDirectory(prefix=".kep-inputs-", dir=out.parent) as folder:
        staging = Path(folder)
        for name, raw in (("inputs.jsonl", data), ("manifest.json", json_bytes(manifest) + b"\n")):
            with (staging / name).open("xb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
        try:
            out.mkdir()
        except FileExistsError:
            raise Refusal("OUTPUT_EXISTS") from None
        os.link(staging / "inputs.jsonl", out / "inputs.jsonl")
        os.link(staging / "manifest.json", out / "manifest.json")
    return {"dry_run": False, **summary}


def main(argv=None):
    class Parser(argparse.ArgumentParser):
        def error(self, _message):
            self.exit(2, '{"refused": "USAGE_ERROR"}\n')
    parser = Parser(description=__doc__)
    for name in ("archive", "tree", "census", "license", "policy", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--policy-sha256", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = vars(parser.parse_args(argv))
    args["license_file"] = args.pop("license")
    try:
        result = prepare(**args)
    except Refusal as exc:
        print(json.dumps({"refused": str(exc), **({"summary": exc.summary} if exc.summary is not None else {})}), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, AttributeError, tarfile.TarError):
        print(json.dumps({"refused": "INVALID_CONSTRUCTION_EVIDENCE"}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
