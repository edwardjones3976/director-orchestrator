# [[OpenAI Codex | GPT-6 | 2026-09-27 | 14:10 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 02:11 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 19:58 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 19:06 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 18:00 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 16:36 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 15:16 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 13:51 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 11:20 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 09:18 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:27 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:15 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:29 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:57 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 12:32 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5 (1M context) | 2026-09-12 | 12:22 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:38 UTC | BEGIN]]
"""Render the read-only twenty-check Director scorecard."""
import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

# Trusted reports come only from the evaluator tree that the owner-installed launcher (scripts/scorecard-launcher.py,
# installed at ~/.director-scorecard/launcher.py) extracted from the pinned evaluator_sha with git archive; a working
# checkout's copy of this file is never trusted to check itself (review 4112412081). The check below is a second
# line against a direct run, standard library only, before any repository module is imported.
ROOT = Path(__file__).resolve().parents[1]
# The owner-installed release descriptor, read here with json alone. Its location is this process's own
# OS-resolved account home (never HOME/USERPROFILE, and never DIRECTOR_SCORECARD_OWNER_HOME merely because a
# descriptor digest is also present -- #287 item 34): a caller-controlled environment must not relocate the owner
# trust root. Its exact bytes must hash to the digest the launcher pinned (#287 item 33). A run the launcher did
# not start has no descriptor.
OWNER_HOME_ENV = "DIRECTOR_SCORECARD_OWNER_HOME"
DESCRIPTOR_ENV = "DIRECTOR_SCORECARD_DESCRIPTOR_SHA256"
DESCRIPTOR_VERSION = "director-cli-release-descriptor/v1"
TREE_ENV, REPO_ENV = "DIRECTOR_SCORECARD_TREE", "DIRECTOR_SCORECARD_REPO"  # set by the launcher
SHA_ENV = "DIRECTOR_SCORECARD_EVALUATOR_SHA"  # the exact pin the launcher extracted the tree from
ROTATED = "the release descriptor no longer pins the evaluator revision the launcher extracted"


def account_home() -> Path:
    """The account's profile directory from the operating system, never HOME or USERPROFILE (#287 item 34): the
    identical lookup scripts/scorecard-launcher.py's account_home() performs. Standalone (no import of that file,
    or of any repository module) so this still runs before any repository module is imported."""
    if os.name == "nt":
        import ctypes
        import uuid
        folder = (ctypes.c_byte * 16).from_buffer_copy(
            uuid.UUID("5E6C858F-0E22-4760-9AFE-EA3317B67173").bytes_le)  # FOLDERID_Profile
        path = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(folder), 0, None, ctypes.byref(path)) != 0:
            raise OSError("the account profile folder could not be resolved")
        try:
            return Path(path.value)
        finally:
            ctypes.windll.ole32.CoTaskMemFree(path)
    import pwd
    return Path(pwd.getpwuid(os.geteuid()).pw_dir)


def release_descriptor() -> Path | None:
    """The OS-resolved owner root's descriptor (#287 item 34): a caller-controlled DIRECTOR_SCORECARD_OWNER_HOME is
    never the source of this path, merely because a descriptor digest is also present. DESCRIPTOR_ENV still gates
    this to launcher-started runs; a forwarded owner-home claim that disagrees with the OS-resolved account is
    treated the same as a run the launcher never started."""
    if not os.environ.get(DESCRIPTOR_ENV):
        return None
    try:
        home = account_home()
    except OSError:
        return None
    forwarded = os.environ.get(OWNER_HOME_ENV)
    if forwarded is not None and Path(forwarded).resolve() != home.resolve():
        return None
    return home / ".director-scorecard" / "release-descriptor.json"


def pinned_evaluator(descriptor: Path | None = None) -> str | None:
    """The descriptor's evaluator_sha (40 lowercase hex), or None: absent, a link, oversized, malformed, another
    version, no pin, or bytes other than the ones the launcher pinned."""
    descriptor = descriptor or release_descriptor()
    if descriptor is None:
        return None
    try:
        if descriptor.is_symlink() or not descriptor.is_file() or descriptor.stat().st_size > 1_048_576:
            return None
        raw = descriptor.read_bytes()
        if hashlib.sha256(raw).hexdigest() != os.environ.get(DESCRIPTOR_ENV):
            return None
        value = json.loads(raw)
    except (OSError, ValueError):
        return None
    pin = value.get("evaluator_sha") if isinstance(value, dict) and value.get("version") == DESCRIPTOR_VERSION else None
    return pin if isinstance(pin, str) and len(pin) == 40 and all(c in "0123456789abcdef" for c in pin) else None


def _git_environment() -> dict[str, str]:
    # This gate must run before importing any repository module.
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR"}}
    env.update(GIT_NO_REPLACE_OBJECTS="1", GIT_GRAFT_FILE=os.devnull,
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return env


def _git(repo: Path, *arguments: str) -> bytes:
    return subprocess.run(["git", "--no-replace-objects", "-C", str(repo), *arguments],
                          env=_git_environment(), capture_output=True, timeout=30, check=True,
                          stdin=subprocess.DEVNULL).stdout


def _reject_git_metadata(repo: Path) -> None:
    if _git(repo, "for-each-ref", "--format=%(refname)", "refs/replace/").strip():
        raise ValueError("replace refs are not permitted")
    common = os.fsdecode(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")).strip()
    if not common or not Path(common).is_absolute():
        raise ValueError("Git common directory could not be verified")
    try:
        (Path(common) / "info" / "grafts").lstat()
    except FileNotFoundError:
        return
    raise ValueError("grafts metadata is not permitted")


def launch_refusal(root: Path = ROOT, descriptor: Path | None = None) -> str | None:
    """None when this file runs from the launcher's archived evaluator tree and the pin is on github/main."""
    pinned = pinned_evaluator(descriptor)
    if pinned is None:
        return "evaluator revision not pinned"
    tree, repo = os.environ.get(TREE_ENV), os.environ.get(REPO_ENV)
    if not tree or not repo or os.path.normcase(str(Path(tree).resolve())) != os.path.normcase(str(root)):
        return "not started by the owner-installed launcher from the archived evaluator tree"
    if os.environ.get(SHA_ENV) != pinned:  # review 4112533825: the tree is bound to the pin read at extraction
        return ROTATED
    try:
        _reject_git_metadata(Path(repo))
    except ValueError as error:
        return str(error)
    except (OSError, subprocess.SubprocessError):
        return "Git replace/graft metadata could not be verified"
    try:
        _git(Path(repo), "merge-base", "--is-ancestor", pinned, "github/main")
    except (OSError, subprocess.SubprocessError):
        return "the pinned evaluator revision is not on github/main"
    return None


_LIVE_ONLY = ("--deployment, --channel, --github, --ledger, --security-scan, --drill-thread, --approval-task, --trial, "
              "--rubric, --paid-thread, --paid-rubric, --ci-replica-receipt and --gateway require --live")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", "--candidate-sha", dest="candidate_sha", required=True,
                        help="Exact candidate commit, graded as data from the pinned evaluator checkout")
    parser.add_argument("--evidence-dir", type=Path, help="Explicit directory of unverified reference files; not recursively scanned")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--live", action="store_true", help="Inspect the selected --deployment once; there is no default stack")
    parser.add_argument("--deployment", help="Exact Compose project name of the deployment to inspect; required by --live")
    parser.add_argument("--owner", help="Exact Slack owner user ID (evaluator configuration, never the candidate's); "
                        "the deployment must use it; needed by Slack-derived checks")
    parser.add_argument("--channel", help="Exact Slack channel ID the deployment must use; never committed; needed by Slack-derived checks")
    parser.add_argument("--gateway", help="Exact running container name of the expected Slack gateway; requires --live and is needed for checks 01 and 02")
    parser.add_argument("--github", action="store_true", help="Also verify CI through authenticated gh; no provider calls")
    parser.add_argument("--ledger", type=Path, help="Reopen the actual workload journal read-only; requires --live")
    parser.add_argument("--security-scan", help="Verify this scan ID through the native Security workbench index; requires --live")
    parser.add_argument("--security-base", help="Reviewed base commit for check 18 when the release descriptor pins none")
    parser.add_argument("--drill-thread", action="append", default=[], metavar="KIND=THREAD_TS",
                        help="Drill thread for checks 03/09: refusal-4xx, reserved-5xx, transport-failure, duplicate-input or "
                             "ambiguous-post; repeat per kind")
    parser.add_argument("--approval-task", help="DIR-LG task that resumed after approval across a restart (checks 06/08)")
    parser.add_argument("--trial", action="append", default=[], metavar="THREAD_TS", help="Offline trial thread for check 10; repeat for ten consecutive owner posts")
    parser.add_argument("--rubric", type=Path, help="Structural rubric the ten --trial inputs committed to")
    parser.add_argument("--paid-thread", metavar="THREAD_TS", help="The real paid Slack run for checks 11, 15 and 20")
    parser.add_argument("--paid-rubric", type=Path, help="Paid answer rubric the --paid-thread input committed to")
    parser.add_argument("--ci-replica-receipt", type=Path, help="Owner-signed local-replica receipt for check 16; requires --github")
    args = parser.parse_args()
    selected = (args.deployment, args.channel, args.github, args.ledger, args.security_scan, args.drill_thread,
                args.approval_task, args.trial, args.rubric, args.paid_thread, args.paid_rubric,
                args.ci_replica_receipt, args.gateway)
    if any(selected) and not args.live:
        parser.error(_LIVE_ONLY)
    if args.live and not args.deployment:
        parser.error("--live requires --deployment <compose project>")
    drills = {}
    for value in args.drill_thread:
        kind, separator, thread = value.partition("=")
        if not separator or kind in drills:
            parser.error("--drill-thread takes one KIND=THREAD_TS value per drill kind")
        drills[kind] = thread
    # The whole trust check, standard library only, before any repository module is imported.
    refusal = launch_refusal()
    if refusal is not None:
        parser.error("scorecard not run from the pinned evaluator checkout: " + refusal)
    from director_orchestrator.completion_scorecard import (collect, refuse_untrusted, render_markdown, score,
                                                           untrusted_checkout, _launcher)
    try:
        launcher = _launcher()
        attestation = launcher.run_attestation(ROOT)
        # Consistency checks and candidate ancestry; only the owner-local receipt proves origin.
        refused = untrusted_checkout(args.candidate_sha)
        if attestation is None:
            refused = refused or launcher.UNATTESTED
        observations = [] if refused else collect(args.candidate_sha, deployment=args.deployment, channel=args.channel,
                                                  owner=args.owner, github=args.github,
                               ledger_path=args.ledger, gateway=args.gateway, security_scan_id=args.security_scan,
                               security_base=args.security_base,
                               drill_threads=drills, approval_task=args.approval_task, trials=args.trial or None,
                               rubric_path=args.rubric, paid_thread=args.paid_thread, paid_rubric_path=args.paid_rubric,
                               ci_replica_receipt=args.ci_replica_receipt) if args.live else []
        runtime = next((item for item in observations if item.runtime_sha and item.image_id), None)
        report = score(args.candidate_sha, observations, evidence_dir=args.evidence_dir,
                       runtime_sha=runtime.runtime_sha if runtime else None, image_id=runtime.image_id if runtime else None)
        # The descriptor is read again once the collection is done: a pin rotated mid-run refuses the whole run.
        refused = untrusted_checkout(args.candidate_sha) or refused
        if refused:
            report = refuse_untrusted(report, refused)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        json_path = args.output_dir / "completion-scorecard.json"
        md_path = args.output_dir / "completion-scorecard.md"
        json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        md_path.write_text(render_markdown(report, _pending_launch=True), encoding="utf-8")
        if report["attested"] is True:
            Path(os.environ["DIRECTOR_SCORECARD_RESULT"]).write_text(json.dumps(report), encoding="utf-8")
        else:
            print(launcher.UNATTESTED)
        print(json.dumps({"score": f"{report['passed']}/20" if report["attested"] else "UNATTESTED",
                          "attested": report["attested"], "json": str(json_path.resolve()),
                          "markdown": str(md_path.resolve()), "activation_authorized": False}))
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:38 UTC | END]]
# [[Claude Code | Claude Opus 5 (1M context) | 2026-09-12 | 12:22 UTC | END]]
# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 12:32 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 06:57 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-23 | 16:29 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:15 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 18:27 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 09:18 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 11:20 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 13:51 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 15:16 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 16:36 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 18:00 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 19:06 UTC | END]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-26 | 19:58 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 02:11 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 14:10 UTC | END]]
