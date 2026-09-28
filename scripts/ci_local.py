# [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 09:20 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 10:42 UTC | BEGIN]]
"""One attribution preflight for local candidates and the CI pull_request event.

Review-loop lifecycle (issue #299). Two pull-request labels are visible state:

- ``review-lock``: an exact-head review is in progress. The head must not
  change while the label is present; any head change is refused.
- ``frozen``: the pull request is closed to ordinary churn. After the lock is
  released, a head change is admitted only with a one-use ``Review-exception``
  inside the envelope of the single commit the push introduces::

      Review-exception: P0 | P1 | required-gate
      Previous-head: <the actual previous remote head, full SHA>
      Evidence: https://... | receipt:<id>
      Reason: <one line, at most 280 characters>

An unchanged head, and a newly opened pull request that already carries either
label, are not transitions and are never violations.

Modes:

- ``--pr N`` reproduces the pull_request event from
  ``gh api repos/<repository>/pulls/N`` for the pushed head.
- ``--pr N --candidate REV`` builds the synchronize event a push of the local
  candidate would produce, with the current remote head as ``before``.
- ``--ci [--github-event PATH]`` reads the real ``GITHUB_EVENT_PATH``.

Every mode checks the transition first, then runs the existing
``check_contribution_tags.py`` with ``--base`` set to the merge base of the
event base and the head, so a newer unrelated main commit is not read as a
reverse change by the pull request. The event file keeps GitHub's original
base SHA. Attribution is parsed only by that checker. A main push keeps its
existing exemption: the checker receives the push range and event unchanged.

Local modes are advisory: a contributor can skip them or push with plain Git.
The enforcing form is the CI mode once wired into the required
``Contribution attribution`` job; even then it fails a required check rather
than preventing the push, and it is stateless per event, so exact-head review
of the whole diff remains the merge control.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check_contribution_tags.py"
REPOSITORY = "edwardjones3976/director-orchestrator"
FROZEN_LABEL = "frozen"
REVIEW_LOCK_LABEL = "review-lock"
EXCEPTION_SEVERITIES = frozenset({"P0", "P1", "required-gate"})
REASON_MAX = 280
_SHA = re.compile(r"[0-9a-f]{40}")
_EXCEPTION_KEYS = ("Review-exception", "Previous-head", "Evidence", "Reason")
_EXCEPTION_LINE = re.compile(
    r"^(?P<key>Review-exception|Previous-head|Evidence|Reason):[ \t]*(?P<value>.*?)[ \t]*$"
)
_EVIDENCE = re.compile(
    r"^(?:https://[A-Za-z0-9.-]+\.[A-Za-z]{2,}/\S+|receipt:[A-Za-z0-9][A-Za-z0-9._:/-]{5,})$"
)
_GITHUB_REMOTE = re.compile(
    r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"(?P<name>[^/\s]+/[^/\s]+?)(?:\.git)?/?$"
)


class PreflightError(RuntimeError):
    """The preflight cannot establish a trustworthy event or range."""


_checker_module = None


def _checker():
    """Load check_contribution_tags.py, the repository's only attribution parser."""

    global _checker_module
    if _checker_module is None:
        source = str(ROOT / "src")
        if source not in sys.path:
            sys.path.insert(0, source)
        spec = importlib.util.spec_from_file_location("check_contribution_tags", CHECKER)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _checker_module = module
    return _checker_module


def _is_sha(value: object) -> bool:
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def _git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except (OSError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", b"") or b""
        detail = detail.decode("utf-8", errors="replace").strip().replace("\n", "; ")
        raise PreflightError(f"git {args[0]} failed{': ' + detail if detail else ''}") from error
    return result.stdout.decode("utf-8", errors="replace")


def require_commit(sha: object, role: str) -> str:
    if not _is_sha(sha):
        raise PreflightError(f"{role} is not a full 40-character commit SHA")
    try:
        _git("cat-file", "-e", f"{sha}^{{commit}}")
    except PreflightError as error:
        raise PreflightError(
            f"{role} {sha} is not present locally; fetch it before the preflight"
        ) from error
    return str(sha)


# [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 09:20 UTC | BEGIN]]
def merge_base(base: str, head: str) -> str:
    # ``--all`` lists every best common ancestor; a criss-cross history has
    # more than one, and attribution against an arbitrary one is unsound.
    values = _git("merge-base", "--all", base, head).split()
    if len(values) != 1 or not _is_sha(values[0]):
        raise PreflightError(
            f"no single merge base for {base} and {head} (found {len(values)}); "
            "resolve the criss-cross history before the preflight"
        )
    return values[0]
# [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 09:20 UTC | END]]


def _is_ancestor(ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode not in (0, 1):
        raise PreflightError(f"could not compare {ancestor} with {descendant}")
    return result.returncode == 0


def _field(payload: object, *path: str) -> object:
    value = payload
    for key in path:
        if not isinstance(value, dict) or key not in value:
            raise PreflightError(f"pull request payload lacks {'.'.join(path)}")
        value = value[key]
    return value


def label_names(pull: dict) -> frozenset[str]:
    labels = _field(pull, "labels")
    if not isinstance(labels, list) or not all(
        isinstance(label, dict) and isinstance(label.get("name"), str) for label in labels
    ):
        raise PreflightError("pull request labels are not a list of named labels")
    return frozenset(label["name"] for label in labels)


def validate_pull(pull: object, number: int) -> dict:
    """Fail closed unless the API payload identifies this repository's open PR."""

    if not isinstance(pull, dict):
        raise PreflightError("pull request payload is not a JSON object")
    if _field(pull, "number") != number:
        raise PreflightError(f"pull request payload is not PR #{number}")
    if _field(pull, "state") != "open":
        raise PreflightError(f"PR #{number} is not open")
    for side in ("base", "head"):
        if not _is_sha(_field(pull, side, "sha")):
            raise PreflightError(f"pull request {side}.sha is not a full commit SHA")
        if not isinstance(_field(pull, side, "ref"), str):
            raise PreflightError(f"pull request payload lacks {side}.ref")
        full_name = _field(pull, side, "repo", "full_name")
        if not isinstance(full_name, str) or full_name.lower() != REPOSITORY.lower():
            raise PreflightError(
                f"pull request {side} repository {full_name!r} is not {REPOSITORY}"
            )
    body = _field(pull, "body")
    if body is not None and not isinstance(body, str):
        raise PreflightError("pull request body is neither text nor null")
    label_names(pull)
    return pull


def matching_remote(remote: str | None = None) -> str:
    """Return a local remote whose URL is this GitHub repository."""

    names = _git("remote").split()
    for name in [remote] if remote else names:
        if name not in names:
            raise PreflightError(f"remote {name!r} does not exist")
        match = _GITHUB_REMOTE.match(_git("remote", "get-url", name).strip())
        if match and match.group("name").lower() == REPOSITORY.lower():
            return name
    raise PreflightError(
        f"remote {remote!r} is not {REPOSITORY}"
        if remote
        else f"no local remote points at github.com/{REPOSITORY}"
    )


def fetch_pull(number: int, runner=subprocess.run) -> object:
    command = ["gh", "api", f"repos/{REPOSITORY}/pulls/{number}"]
    try:
        result = runner(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    except OSError as error:
        raise PreflightError(f"could not run gh api: {error}") from error
    if result.returncode != 0:
        raise PreflightError(f"gh api exited with status {result.returncode}")
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise PreflightError("gh api returned invalid JSON") from error


def resolve_candidate(revision: str) -> str:
    if not revision or revision.startswith("-"):
        raise PreflightError("candidate revision must name a commit")
    resolved = _git("rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}").strip()
    return require_commit(resolved, "candidate")


def build_event(pull: dict, *, before: str, after: str) -> dict:
    """Return the synchronize event a push from ``before`` to ``after`` produces.

    Only the head SHA is replaced. ``pull_request.base.sha`` stays GitHub's
    value; the merge base is a checker argument, never an event rewrite.
    """

    event_pull = copy.deepcopy(pull)
    event_pull["head"]["sha"] = after
    return {
        "action": "synchronize",
        "number": pull["number"],
        "before": before,
        "after": after,
        "pull_request": event_pull,
        "repository": {"full_name": REPOSITORY},
    }


def review_exception_errors(message: str, previous_head: str) -> list[str]:
    """Validate the structured Review-exception inside one commit envelope."""

    checker = _checker()
    text = checker._without_trailing_git_trailers(message)
    envelope_errors = checker._validate_submission_envelope(
        text, require_outer=True, require_markdown_boundary=False
    )
    if envelope_errors:
        return [f"review exception commit: {error}" for error in envelope_errors]
    # The checker has established that the first and last nonblank lines are the
    # outer tags, so everything between them is the envelope body.
    inner = [line for line in text.splitlines() if line.strip()][1:-1]
    fields: dict[str, str] = {}
    for line in inner:
        match = _EXCEPTION_LINE.match(line.strip())
        if match is None:
            continue
        if match.group("key") in fields:
            return [f"review exception repeats {match.group('key')}"]
        fields[match.group("key")] = match.group("value")
    missing = [key for key in _EXCEPTION_KEYS if not fields.get(key)]
    if missing:
        return [f"frozen head change lacks a Review-exception field: {', '.join(missing)}"]
    errors = []
    if fields["Review-exception"] not in EXCEPTION_SEVERITIES:
        errors.append("Review-exception must be P0, P1, or required-gate")
    if fields["Previous-head"] != previous_head:
        errors.append(
            f"Review-exception Previous-head {fields['Previous-head']} is not the "
            f"previous remote head {previous_head}"
        )
    if _EVIDENCE.fullmatch(fields["Evidence"]) is None:
        errors.append("Review-exception Evidence must be an https URL with a path or receipt:<id>")
    if len(fields["Reason"]) > REASON_MAX or len(fields["Reason"].split()) < 3:
        errors.append(f"Review-exception Reason must be 3 words to {REASON_MAX} characters")
    return errors


def frozen_transition_errors(before: str, after: str) -> list[str]:
    try:
        require_commit(before, "previous remote head")
        require_commit(after, "new head")
        if not _is_ancestor(before, after):
            return [f"frozen PR history was rewritten: {before} is not an ancestor of {after}"]
        introduced = _git("rev-list", f"{before}..{after}").split()
        # One commit per exception keeps a multi-commit push from carrying an
        # unexplained change next to the commit that holds the exception.
        if len(introduced) != 1:
            return [
                "a frozen head change must introduce exactly one commit carrying the "
                f"Review-exception; this push introduces {len(introduced)}"
            ]
        message = _git("show", "-s", "--format=%B", after, "--")
    except PreflightError as error:
        return [str(error)]
    return review_exception_errors(message, before)


def transition_errors(event: dict) -> list[str]:
    """Refuse a pull_request head change the review-loop labels do not allow."""

    pull = event.get("pull_request")
    if not isinstance(pull, dict) or event.get("action") != "synchronize":
        return []
    before, after = event.get("before"), event.get("after")
    if not (_is_sha(before) and _is_sha(after)):
        return ["synchronize event lacks full before and after SHAs"]
    head = pull.get("head")
    if not isinstance(head, dict) or head.get("sha") != after:
        return ["synchronize event after SHA is not pull_request.head.sha"]
    if before == after:
        return []
    try:
        labels = label_names(pull)
    except PreflightError as error:
        return [str(error)]
    if REVIEW_LOCK_LABEL in labels:
        return [
            f"head changed from {before} to {after} while {REVIEW_LOCK_LABEL} is present; "
            "wait for the reviewer to release the lock"
        ]
    if FROZEN_LABEL not in labels:
        return []
    return frozen_transition_errors(before, after)


def run_checker(base: str, head: str, event_path: Path) -> int:
    environment = dict(os.environ)
    source = str(ROOT / "src")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = source + (os.pathsep + existing if existing else "")
    command = [
        sys.executable, str(CHECKER),
        "--base", base, "--head", head, "--github-event", str(event_path),
    ]
    result = subprocess.run(
        command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    sys.stdout.write(result.stdout.decode("utf-8", errors="replace"))
    sys.stderr.write(result.stderr.decode("utf-8", errors="replace"))
    return result.returncode


def _report(**values: object) -> None:
    for key, value in values.items():
        print(f"{key.replace('_', '-')}: {value}")


def check_pull_request_event(event: dict, event_path: Path, *, mode: str) -> int:
    pull = event["pull_request"]
    head = pull.get("head") if isinstance(pull.get("head"), dict) else {}
    base = pull.get("base") if isinstance(pull.get("base"), dict) else {}
    try:
        labels = sorted(label_names(pull))
    except PreflightError:
        labels = ["<invalid>"]
    before = event.get("before", "<none>")
    _report(
        mode=mode,
        action=event.get("action", "<none>"),
        event_base=base.get("sha", "<missing>"),
        before=before,
        head=head.get("sha", "<missing>"),
# [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 09:20 UTC | BEGIN]]
        head_change=(
            "not-validated (pushed head; previous head unknown)" if mode == "pushed-pr"
            else "changed" if event.get("action") == "synchronize" and before != head.get("sha")
            else "none"
        ),
# [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 09:20 UTC | END]]
        labels=",".join(labels) or "<none>",
    )
    errors = transition_errors(event)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    try:
        event_base = require_commit(base.get("sha"), "event base")
        head_sha = require_commit(head.get("sha"), "pull request head")
        attribution_base = merge_base(event_base, head_sha)
    except PreflightError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    _report(merge_base=attribution_base)
    return run_checker(attribution_base, head_sha, event_path)


def ci_main(event_path: Path) -> int:
    try:
        event = json.loads(event_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"ERROR: could not read GitHub event {event_path}: {error}", file=sys.stderr)
        return 1
    if not isinstance(event, dict):
        print("ERROR: GitHub event is not a JSON object", file=sys.stderr)
        return 1
    if isinstance(event.get("pull_request"), dict):
        return check_pull_request_event(event, event_path, mode="ci-pull-request")
    if event.get("ref") == "refs/heads/main" and _is_sha(event.get("after")):
        # Main-push exemption: the checker keeps its existing push range and
        # squash-merge envelope handling; no merge base is substituted.
        before = event.get("before") if _is_sha(event.get("before")) else _checker().ZERO_SHA
        _report(mode="ci-main-push", before=before, head=event["after"])
        return run_checker(before, event["after"], event_path)
    print("ERROR: event is neither a pull_request nor a main push", file=sys.stderr)
    return 1


def local_main(number: int, candidate: str | None, remote: str | None, event_out: Path | None) -> int:
    try:
        pull = validate_pull(fetch_pull(number), number)
        remote_name = matching_remote(remote)
        remote_head = require_commit(pull["head"]["sha"], "remote PR head")
        after = remote_head if candidate is None else resolve_candidate(candidate)
    except PreflightError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    _report(
        pr=number,
        remote=remote_name,
        remote_head=remote_head,
        candidate=after if candidate is not None else "<pushed head>",
    )
    event = build_event(pull, before=remote_head, after=after)
    mode = "pushed-pr" if candidate is None else "pre-push-candidate"
    if event_out is not None:
        event_out.write_text(json.dumps(event, indent=2), encoding="utf-8")
        return check_pull_request_event(event, event_out, mode=mode)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "pull_request.json"
        path.write_text(json.dumps(event, indent=2), encoding="utf-8")
        return check_pull_request_event(event, path, mode=mode)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--pr", type=int, help="reproduce this pull request's event")
    target.add_argument("--ci", action="store_true", help="check the actual GitHub event")
    parser.add_argument("--candidate", help="local revision proposed as the new PR head")
    parser.add_argument("--remote", help="local remote that must be the GitHub repository")
    parser.add_argument("--event-out", type=Path, help="keep the reproduced event here")
    parser.add_argument("--github-event", type=Path, help="event file (default GITHUB_EVENT_PATH)")
    args = parser.parse_args(argv)
    if args.ci:
        if args.candidate or args.remote or args.event_out:
            parser.error("--ci reads only --github-event")
        event_path = args.github_event or (
            Path(os.environ["GITHUB_EVENT_PATH"]) if os.environ.get("GITHUB_EVENT_PATH") else None
        )
        if event_path is None:
            parser.error("--ci needs --github-event or GITHUB_EVENT_PATH")
        return ci_main(event_path)
    if args.github_event:
        parser.error("--github-event is only for --ci")
    return local_main(args.pr, args.candidate, args.remote, args.event_out)


if __name__ == "__main__":
    raise SystemExit(main())
# [[Claude Code | Claude Opus 5.5 | 2026-09-27 | 10:42 UTC | END]]
# [[Claude Code | Claude Fable 5.1 | 2026-09-28 | 09:20 UTC | END]]
