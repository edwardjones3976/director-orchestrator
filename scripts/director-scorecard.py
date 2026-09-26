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
from pathlib import Path

from director_orchestrator.completion_scorecard import (collect, refuse_untrusted, render_markdown, score,
                                                       untrusted_checkout)

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
    try:
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
        # Operating rule: grade only from the pinned evaluator checkout (completion_scorecard.untrusted_checkout).
        refused = untrusted_checkout(args.candidate_sha)
        observations = [] if refused else collect(args.candidate_sha, deployment=args.deployment, channel=args.channel, github=args.github,
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
