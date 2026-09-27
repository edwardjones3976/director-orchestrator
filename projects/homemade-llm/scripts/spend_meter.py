# [[OpenAI Codex | GPT-6 | 2026-09-27 | 20:20 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 20:18 UTC | BEGIN]]
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 15:07 UTC | BEGIN]]
"""Paid-provider spend meter for homemade-llm (M0.10). Reporting is read-only.

Totals the costs that the provider-dispatch wrapper already records in its receipts
(~/.claude/skills/provider-dispatch/receipts). The receipts are the ledger; a report
only reads them. (--self-test writes fixtures to a temporary directory it deletes.)

--project counts only receipts filed under this project's worktree folders (Grok CLI).
xAI and Perplexity receipts carry no project, so in --project mode they are reported
under "unattributed" rather than silently dropped or wrongly claimed.
--purpose-prefix filters paid receipts by their top-level purpose. Receipts with
no recorded purpose are excluded and counted as "unlabelled".

    python spend_meter.py                  # last 7 days, every project
    python spend_meter.py --days 28 --json
    python spend_meter.py --purpose-prefix 'homemade-llm M1' --json
    python spend_meter.py --self-test

Costs recorded in receipts:
    Grok CLI        <worktree>/grok-*.out.json   total_cost_usd
    xAI API         xai/*.json                   usage.cost_in_usd_ticks / 1e10
    Perplexity ask  perplexity/*.json            response.usage.cost.total_cost
Calls with no recorded cost (Perplexity search, a failed ask) are counted under
"uncosted" so the baseline never pretends they were free.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

RECEIPTS = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".claude/skills/provider-dispatch/receipts"
PROJECT_DIRS = ("homemade-llm", "hl-")  # receipt folders named after this project's worktrees


def receipt_cost(path: Path, data: dict) -> tuple[str, float | None] | None:
    """Return (provider, usd or None) for a paid-call receipt, or None when it is not one."""
    parent = path.parent.name
    if parent == "xai":
        ticks = (data.get("usage") or {}).get("cost_in_usd_ticks")
        return "xai", ticks / 1e10 if isinstance(ticks, int) else None
    if parent == "perplexity":
        response = data.get("response")
        usage = response.get("usage") if isinstance(response, dict) else None
        cost = ((usage or {}).get("cost") or {}).get("total_cost")
        return "perplexity", float(cost) if isinstance(cost, (int, float)) else None
    if path.name.startswith("grok-") and path.name.endswith(".out.json"):
        cost = data.get("total_cost_usd")
        return "grok-cli", float(cost) if isinstance(cost, (int, float)) else None
    return None


UNATTRIBUTED = ("xai", "perplexity")  # provider-level receipt folders with no project field


def meter(root: Path, since: float, project_only: bool, purpose_prefix: str | None = None) -> dict:
    totals: dict[str, dict] = {}
    unattributed: dict[str, dict] = {}
    unlabelled = 0
    for path in root.rglob("*.json"):
        if path.stat().st_mtime < since:
            continue
        bucket = totals
        if project_only and not path.parent.name.startswith(PROJECT_DIRS):
            if path.parent.name not in UNATTRIBUTED:
                continue
            bucket = unattributed
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        found = receipt_cost(path, data) if isinstance(data, dict) else None
        if not found:
            continue
        if purpose_prefix is not None:
            purpose = data.get("purpose")
            if not isinstance(purpose, str) or not purpose:
                unlabelled += 1
                continue
            if not purpose.startswith(purpose_prefix):
                continue
        provider, usd = found
        row = bucket.setdefault(provider, {"calls": 0, "usd": 0.0, "uncosted": 0})
        row["calls"] += 1
        if usd is None:
            row["uncosted"] += 1
        else:
            row["usd"] += usd
    rounded = lambda rows: {k: {**v, "usd": round(v["usd"], 4)} for k, v in sorted(rows.items())}
    report = {
        "since_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(since)),
        "scope": "homemade-llm" if project_only else "all projects",
        "providers": rounded(totals),
        "total_usd": round(sum(v["usd"] for v in totals.values()), 4),
    }
    if project_only:
        report["unattributed"] = rounded(unattributed)
    if purpose_prefix is not None:
        report["purpose_prefix"] = purpose_prefix
        report["matched_receipts"] = sum(v["calls"] for rows in (totals, unattributed) for v in rows.values())
        report["matched_usd"] = round(sum(v["usd"] for rows in (totals, unattributed) for v in rows.values()), 4)
        report["unlabelled"] = unlabelled
    return report


def self_test() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel, body in {
            "xai/1.json": {"purpose": "homemade-llm M1 grading", "usage": {"cost_in_usd_ticks": 515680000}},
            "perplexity/2.json": {"purpose": "other project", "action": "ask", "response": {"usage": {"cost": {"total_cost": 0.02}}}},
            "perplexity/3.json": {"action": "search", "response": {}},
            "homemade-llm/grok-1.out.json": {"purpose": "homemade-llm M1 review", "total_cost_usd": 0.33},
            "other/grok-2.out.json": {"purpose": "other project", "total_cost_usd": 1.0},
            "other/codex-1.json": {"note": "subscription, not paid"},
        }.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(body), encoding="utf-8")
        everything = meter(root, 0, project_only=False)
        assert everything["providers"]["xai"] == {"calls": 1, "usd": 0.0516, "uncosted": 0}, everything
        assert everything["providers"]["perplexity"] == {"calls": 2, "usd": 0.02, "uncosted": 1}, everything
        assert everything["providers"]["grok-cli"]["usd"] == 1.33, everything
        assert everything["total_usd"] == 1.4016, everything
        project = meter(root, 0, project_only=True)
        assert project["providers"] == {"grok-cli": {"calls": 1, "usd": 0.33, "uncosted": 0}}, project
        assert project["unattributed"]["xai"]["calls"] == 1, project
        assert project["unattributed"]["perplexity"] == {"calls": 2, "usd": 0.02, "uncosted": 1}, project
        assert "unattributed" not in everything, everything
        filtered = meter(root, 0, project_only=False, purpose_prefix="homemade-llm M1")
        assert filtered["providers"] == {
            "grok-cli": {"calls": 1, "usd": 0.33, "uncosted": 0},
            "xai": {"calls": 1, "usd": 0.0516, "uncosted": 0},
        }, filtered
        assert (filtered["matched_receipts"], filtered["matched_usd"], filtered["unlabelled"]) == (2, 0.3816, 1), filtered
        project_filtered = meter(root, 0, project_only=True, purpose_prefix="homemade-llm M1")
        assert project_filtered["providers"] == {"grok-cli": {"calls": 1, "usd": 0.33, "uncosted": 0}}, project_filtered
        assert project_filtered["unattributed"] == {"xai": {"calls": 1, "usd": 0.0516, "uncosted": 0}}, project_filtered
        assert (project_filtered["matched_receipts"], project_filtered["matched_usd"], project_filtered["unlabelled"]) == (2, 0.3816, 1), project_filtered
        assert meter(root, time.time() + 60, project_only=False)["providers"] == {}
        assert meter(root, time.time() + 60, project_only=False, purpose_prefix="homemade-llm M1")["matched_receipts"] == 0
    print("spend_meter self-test: ok")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=float, default=7)
    parser.add_argument("--project", action="store_true", help="only this project's worktree receipts")
    parser.add_argument("--purpose-prefix", help="include only paid receipts whose recorded purpose starts with PREFIX")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--receipts", type=Path, default=RECEIPTS)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not args.receipts.is_dir():
        print(f"receipts directory not found: {args.receipts}", file=sys.stderr)
        return 1
    report = meter(args.receipts, time.time() - args.days * 86400, args.project, args.purpose_prefix)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(f"paid spend since {report['since_utc']} ({report['scope']})")
    if args.purpose_prefix is not None:
        print(f"purpose prefix {args.purpose_prefix!r}: {report['matched_receipts']} matched receipts, USD {report['matched_usd']:.4f}; {report['unlabelled']} unlabelled excluded")
    for name, row in report["providers"].items():
        print(f"  {name:<11} {row['calls']:>4} calls  USD {row['usd']:>8.4f}  uncosted {row['uncosted']}")
    print(f"  {'total':<11} {'':>10}  USD {report['total_usd']:>8.4f}")
    for name, row in report.get("unattributed", {}).items():
        print(f"  unattributed {name}: {row['calls']} calls, USD {row['usd']:.4f} (receipts carry no project)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
# [[Claude Code | Claude Opus 5.5 | 2026-09-24 | 15:07 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 20:18 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 20:20 UTC | END]]
