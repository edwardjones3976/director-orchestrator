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
import json
import os
import subprocess
from pathlib import Path

# This file runs only from the evaluator tree that the owner-installed launcher (scripts/scorecard-launcher.py,
# installed at ~/.director-scorecard/launcher.py) extracted from the pinned evaluator_sha with git archive; a working
# checkout's copy of this file is never trusted to check itself (review 4112412081). The check below is a second
# line against a direct run, standard library only, before any repository module is imported.
ROOT = Path(__file__).resolve().parents[1]
# The owner-installed release descriptor (primary_scorecard_evidence.RELEASE_DESCRIPTOR), read here with json alone.
RELEASE_DESCRIPTOR = Path.home() / ".director-scorecard" / "release-descriptor.json"
DESCRIPTOR_VERSION = "director-cli-release-descriptor/v1"
TREE_ENV, REPO_ENV = "DIRECTOR_SCORECARD_TREE", "DIRECTOR_SCORECARD_REPO"  # set by the launcher


def pinned_evaluator(descriptor: Path = RELEASE_DESCRIPTOR) -> str | None:
    """The descriptor's evaluator_sha (40 lowercase hex), or None: absent, a link, oversized, malformed, another
    version or no pin."""
    try:
        if descriptor.is_symlink() or not descriptor.is_file() or descriptor.stat().st_size > 1_048_576:
            return None
        value = json.loads(descriptor.read_bytes())
    except (OSError, ValueError):
        return None
    pin = value.get("evaluator_sha") if isinstance(value, dict) and value.get("version") == DESCRIPTOR_VERSION else None
    return pin if isinstance(pin, str) and len(pin) == 40 and all(c in "0123456789abcdef" for c in pin) else None


def launch_refusal(root: Path = ROOT, descriptor: Path = RELEASE_DESCRIPTOR) -> str | None:
    """None when this file runs from the launcher's archived evaluator tree and the pin is on github/main."""
    pinned = pinned_evaluator(descriptor)
    if pinned is None:
        return "evaluator revision not pinned"
    tree, repo = os.environ.get(TREE_ENV), os.environ.get(REPO_ENV)
    if not tree or not repo or os.path.normcase(str(Path(tree).resolve())) != os.path.normcase(str(root)):
        return "not started by the owner-installed launcher from the archived evaluator tree"
    try:
        subprocess.run(["git", "-C", repo, "merge-base", "--is-ancestor", pinned, "github/main"],
                       capture_output=True, timeout=30, check=True, stdin=subprocess.DEVNULL)
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
                                                           untrusted_checkout)
    try:
        # Second line, now that the evaluator is trusted: the same rule, and the candidate's ancestry.
        refused = untrusted_checkout(args.candidate_sha)
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
        if refused:
            report = refuse_untrusted(report, refused)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        json_path = args.output_dir / "completion-scorecard.json"
        md_path = args.output_dir / "completion-scorecard.md"
        json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        md_path.write_text(render_markdown(report), encoding="utf-8")
        print(json.dumps({"score": f"{report['passed']}/20", "json": str(json_path.resolve()),
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
