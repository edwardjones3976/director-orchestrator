# [[Cursor | Auto | 2026-10-01 | 16:54 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 01:36 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:05 UTC | BEGIN]]
"""Display open work and detect exact repository-relative path conflicts."""

import argparse
from collections import defaultdict
import json
from pathlib import PurePosixPath
import re
import subprocess
import sys


REPO = "edwardjones3976/director-orchestrator"
API = f"repos/{REPO}"
MARKER = "<!-- site-board -->"
MAX_PRS = 1000
MAX_PAGES = 10
COMMENT_LIMIT = 65536
_CONTROLS = re.compile(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")
_MARKDOWN = re.compile(r"([\\`*_{}\[\]()<>#!|])")


class BoardError(Exception):
    pass


class GitHub:
    def __init__(self):
        self.cache = {}

    def call(self, *args, body=None):
        if body is None and args in self.cache:
            return self.cache[args]
        command = ["gh", *args]
        if body is not None:
            command += ["--input", "-"]
        try:
            result = subprocess.run(
                command, input=json.dumps({"body": body}) if body is not None else None,
                capture_output=True, text=True, encoding="utf-8", timeout=60,
                check=True,
            )
            data = json.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            # Do not echo gh output: it may contain private authentication details.
            raise BoardError("gh failed or returned invalid JSON; no retry performed") from exc
        if body is None:
            self.cache[args] = data
        return data

    def pages(self, endpoint, key=None):
        items = []
        separator = "&" if "?" in endpoint else "?"
        for page in range(1, MAX_PAGES + 1):
            data = self.call("api", f"{endpoint}{separator}per_page=100&page={page}")
            batch = data[key] if key else data
            items.extend(batch)
            if len(batch) < 100:
                return items
        raise BoardError("pagination bound reached; refusing an incomplete snapshot")


def open_prs(gh):
    prs = gh.call(
        "pr", "list", "--repo", REPO, "--state", "open", "--limit", str(MAX_PRS + 1),
        "--json", "number,title,isDraft,headRefName,headRefOid,body",
    )
    if len(prs) > MAX_PRS:
        raise BoardError("open PR bound reached; refusing an incomplete snapshot")
    for pr in prs:
        detail = gh.call(
            "pr", "view", str(pr["number"]), "--repo", REPO,
            "--json", "files,changedFiles,headRefOid,state",
        )
        if detail["state"] != "OPEN" or detail["headRefOid"] != pr["headRefOid"]:
            raise BoardError("PR changed during collection; run again for a fresh snapshot")
        pr["files"] = sorted({item["path"] for item in detail["files"]})
        if len(pr["files"]) != detail["changedFiles"]:
            raise BoardError("PR file list is incomplete; refusing a false clear")
        renamed = {item["path"] for item in detail["files"] if item.get("changeType") == "RENAMED"}
        if renamed:
            files = gh.pages(f"{API}/pulls/{pr['number']}/files")
            if len(files) != detail["changedFiles"] or {item["filename"] for item in files} != set(pr["files"]):
                raise BoardError("PR rename file list is incomplete or changed; refusing a false clear")
            previous = {item["filename"]: item.get("previous_filename")
                        for item in files if item["status"] == "renamed"}
            if set(previous) != renamed or not all(isinstance(path, str) and path for path in previous.values()):
                raise BoardError("PR rename sources are incomplete; refusing a false clear")
            current = gh.call("api", f"{API}/pulls/{pr['number']}")
            if current["state"] != "open" or current["head"]["sha"] != pr["headRefOid"]:
                raise BoardError("PR changed during collection; run again for a fresh snapshot")
            pr["files"] = sorted(set(pr["files"]) | set(previous.values()))
    return sorted(prs, key=lambda pr: pr["number"])


def file_index(prs):
    index = defaultdict(list)
    for pr in prs:
        for path in pr["files"]:
            index[path].append(pr["number"])
    return index


def neutralize(value):
    """Replace terminal and bidi controls without changing the remaining characters."""
    return _CONTROLS.sub("\ufffd", str(value))


def inline(value):
    """Keep remote prose inert in terminal output and Markdown comments."""
    return _MARKDOWN.sub(r"\\\1", " ".join(neutralize(value).split())).replace("@", "@\u200d")


def inline_path(value):
    """Show a repository path without collapsing significant whitespace."""
    visible = neutralize(value).replace(" ", "\u2423")
    return _MARKDOWN.sub(r"\\\1", visible).replace("@", "@\u200d")


def render_board(gh, prs):
    queued = gh.pages(f"{API}/actions/runs?status=queued", "workflow_runs") if prs else []
    queued.sort(key=lambda run: (run["created_at"], run["id"]))
    positions = {run["id"]: position for position, run in enumerate(queued, 1)}
    lines = ["# Work board", "", "Queue positions: observed repository queued runs, oldest first; not a scheduling guarantee.", ""]
    for pr in prs:
        runs = gh.call("api", f"{API}/actions/runs?head_sha={pr['headRefOid']}&per_page=1")["workflow_runs"]
        run = runs[0] if runs else None
        ci = f"{run['status']} / {run['conclusion'] or 'none'}" if run else "unknown / unknown"
        position = str(positions.get(run["id"], "unknown")) if run and run["status"] == "queued" else "n/a"
        session = re.search(r"^Session:[ \t]*(\S[^\r\n]*)", pr["body"] or "", re.MULTILINE)
        lines.extend([
            f"## #{pr['number']} {inline(pr['title'])} (draft: {str(pr['isDraft']).lower()})",
            f"- Head: {inline(pr['headRefName'])} @ {pr['headRefOid'][:8]}",
            f"- Session: {inline(session.group(1).strip()) if session else 'unknown'}",
            f"- Latest head CI: {ci}; queue position: {position}",
            "- Files:",
            *[f"  - {inline_path(path)}" for path in pr["files"]],
            "",
        ])
    if not prs:
        lines.extend(["No open PRs.", ""])
    lines.extend(["## OVERLAPS", ""])
    overlaps = [(path, numbers) for path, numbers in sorted(file_index(prs).items()) if len(numbers) > 1]
    lines.extend(f"- {inline_path(path)}: " + ", ".join(f"#{number}" for number in numbers) for path, numbers in overlaps)
    if not overlaps:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def post_board(gh, issue, board):
    body = MARKER + "\n" + board
    if len(body) > COMMENT_LIMIT:
        raise BoardError(
            f"board comment is {len(body)} characters; GitHub's limit is {COMMENT_LIMIT}; refusing to post"
        )
    login = gh.call("api", "user")["login"]
    if not isinstance(login, str) or not login:
        raise BoardError("authenticated identity is missing; refusing to post")
    comments = gh.pages(f"{API}/issues/{issue}/comments")
    matches = [comment for comment in comments
               if (comment.get("user") or {}).get("login") == login
               and MARKER in (comment.get("body") or "").splitlines()]
    if len(matches) > 1:
        raise BoardError("multiple marker comments found; resolve duplicates before posting")
    if matches:
        gh.call("api", "-X", "PATCH", f"{API}/issues/comments/{matches[0]['id']}", body=body)
    else:
        gh.call("api", "-X", "POST", f"{API}/issues/{issue}/comments", body=body)


def positive_issue(value):
    if not value.isdecimal() or int(value) < 1:
        raise argparse.ArgumentTypeError("issue must be a positive number")
    return int(value)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", nargs="+", metavar="PATH")
    mode.add_argument("--post", type=positive_issue, metavar="ISSUE")
    args = parser.parse_args(argv)
    gh = GitHub()
    try:
        prs = open_prs(gh)
        if args.check:
            index = file_index(prs)
            conflict = False
            for path in args.check:
                normalized = path.replace("\\", "/")
                while normalized.startswith("./"):
                    normalized = normalized[2:]
                if not normalized or normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized) or ".." in normalized.split("/"):
                    raise BoardError("check paths must be repository-relative without '..'")
                normalized = str(PurePosixPath(normalized))
                numbers = sorted(set(index.get(path, [])) | set(index.get(normalized, [])))
                print(f"{neutralize(path)}: " + (", ".join(f"#{number}" for number in numbers) if numbers else "clear"))
                conflict |= bool(numbers)
            return int(conflict)
        board = render_board(gh, prs)
        if args.post:
            post_board(gh, args.post, board)
        print(board, end="")
        return 0
    except (BoardError, KeyError, TypeError) as exc:
        message = str(exc) if isinstance(exc, BoardError) else "unexpected gh response shape"
        print(f"site-board: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
# [[OpenAI Codex | GPT-6 | 2026-09-26 | 22:05 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 01:36 UTC | END]]
# [[Cursor | Auto | 2026-10-01 | 16:54 UTC | END]]
