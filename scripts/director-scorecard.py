# [[OpenAI Codex | GPT-6 Astra | 2026-09-12 | 08:38 UTC | BEGIN]]
"""Render the read-only twenty-check Director scorecard."""
import argparse
import json
from pathlib import Path

from director_orchestrator.completion_scorecard import collect, render_markdown, score


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-sha", required=True, help="Exact candidate; distinct from the running image")
    parser.add_argument("--evidence-dir", type=Path, help="Explicit directory of unverified reference files; not recursively scanned")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--live", action="store_true", help="Read the named Director container and localhost:8080 health once")
    parser.add_argument("--github", action="store_true", help="Also verify CI through authenticated gh; no provider calls")
    parser.add_argument("--ledger", type=Path, help="Reopen the actual workload journal read-only; requires --live")
    parser.add_argument("--security-scan", help="Verify this scan ID through the native Security workbench index; requires --live")
    args = parser.parse_args()
    try:
        if (args.github or args.ledger or args.security_scan) and not args.live:
            parser.error("--github, --ledger and --security-scan require --live")
        observations = collect(args.candidate_sha, github=args.github, ledger_path=args.ledger,
                               security_scan_id=args.security_scan) if args.live else []
        runtime = next((item for item in observations if item.runtime_sha and item.image_id), None)
        report = score(args.candidate_sha, observations, evidence_dir=args.evidence_dir,
                       runtime_sha=runtime.runtime_sha if runtime else None, image_id=runtime.image_id if runtime else None)
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
