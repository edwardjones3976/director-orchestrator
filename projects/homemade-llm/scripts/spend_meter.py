# [[OpenAI Codex | GPT-6 | 2026-09-27 | 21:15 UTC | BEGIN]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 20:39 UTC | BEGIN]]
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
no valid label are split into inferred and unknown buckets. Unknown costs count
against the milestone's conservative USD 25 upper bound, including with --project.
--unpriced-bound applies a positive USD cap to each receipt with no recorded cost;
without it, the upper bound is unknown and the budget check stops.

    python spend_meter.py                  # last 7 days, every project
    python spend_meter.py --days 28 --json
    python spend_meter.py --purpose-prefix 'homemade-llm M1' --unpriced-bound 1.00 --json
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
from datetime import datetime, timezone
import json
import math
import os
import re
import sys
import time
from pathlib import Path

RECEIPTS = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".claude/skills/provider-dispatch/receipts"
PROJECT_DIRS = ("homemade-llm", "hl-")  # receipt folders named after this project's worktrees
PURPOSE = re.compile(r"^(?P<project>[a-z0-9][a-z0-9-]*) (?P<milestone>M\d+|-) (?P<packet>[A-Za-z0-9][A-Za-z0-9._-]*):")
PREFIX = re.compile(r"^(?P<project>[a-z0-9][a-z0-9-]*) (?P<milestone>M\d+|-)$")
STAMP = re.compile(r"20\d{6}T\d{6}Z")


def utc(text: str) -> float:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


# project-profile.yaml created_at; decision-log.md D-018 (#280 pre-gate M1 spend)
# and O-031 (M0 closed/M1 entered), recorded on PR #293. M1's conservative
# budget window starts at project creation because unlabelled pre-gate calls
# cannot be ruled out. Overlapping M0/M1 time is ambiguous for inference.
# PLAN.md's 10-08 M1 date is a target, not a closing gate (O-022).
MILESTONE_WINDOWS = {
    ("homemade-llm", "M0"): (utc("2026-09-24T06:05:00Z"), utc("2026-09-26T03:15:00Z")),
    ("homemade-llm", "M1"): (utc("2026-09-24T06:05:00Z"), None),
}

# Ordered legacy inference rules. A rule identifies a project; a missing
# milestone is resolved only when exactly one documented time window fits.
RULES = (
    ("folder-m1-run", "folder", re.compile(r"^m1-run.*$", re.I), "homemade-llm", "M1"),  # M1 run worktree
    ("folder-homemade-window", "folder", re.compile(r"^(?:hl-.*|heldout.*|homemade-llm.*)$", re.I), "homemade-llm", None),  # project worktree + gate window
    ("folder-platform", "folder", re.compile(r"^(?:shard-cleanup|blob-reap|win-shards|par-cleanup|parallel-ci|mg.*|rv.*|merge-law.*|site-.*|review-.*)$", re.I), "director-orchestrator", "-"),  # platform CI/review worktrees
    ("folder-edge", "folder", re.compile(r"^edge.*$", re.I), "edge-engine", "-"),  # Edge Engine worktrees
    ("text-platform-pr", "text", re.compile(r"\b(?:RV|MX)-(?:333|335|314|323)\b", re.I), "director-orchestrator", "-"),  # identified platform PR packets
    ("text-homemade-m1", "text", re.compile(r"\b(?:O-044|M1)\b|\bhomemade-llm\b.{0,80}\b(?:probe|grad(?:e|ing)|scor(?:e|ing))\b", re.I), "homemade-llm", "M1"),  # legacy M1 task text with project context
)


def receipt_timestamp(path: Path) -> float:
    match = STAMP.search(path.name)
    if match:
        try:
            return datetime.strptime(match.group(), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            pass
    return path.stat().st_mtime  # legacy receipt with no timestamp in its name: conservative fallback


def milestone_at(project: str, stamp: float) -> str | None:
    matches = [milestone for (name, milestone), (start, end) in MILESTONE_WINDOWS.items()
               if name == project and start <= stamp and (end is None or stamp < end)]
    return matches[0] if len(matches) == 1 else None


def receipt_identity(path: Path, data: dict, stamp: float) -> tuple[str, str | None, str | None, str | None]:
    purpose = data.get("purpose")
    match = PURPOSE.match(purpose) if isinstance(purpose, str) else None
    if match:
        return "labelled", match["project"], match["milestone"], None
    if data.get("purpose_source") in ("explicit", "derived"):
        project, milestone = data.get("project"), data.get("milestone")
        if isinstance(project, str) and isinstance(milestone, str) and PREFIX.fullmatch(f"{project} {milestone}"):
            return "labelled", project, milestone, None
    text = " ".join(value for value in (purpose, data.get("packet_id")) if isinstance(value, str))
    for rule_id, source, pattern, project, milestone in RULES:
        candidate = path.parent.name if source == "folder" else text
        if pattern.search(candidate):
            resolved = milestone or milestone_at(project, stamp)
            if resolved is not None:
                return "inferred", project, resolved, rule_id
    return "unknown", None, None, None


def receipt_cost(path: Path, data: dict) -> tuple[str, float | None] | None:
    """Return (provider, usd or None) for a paid-call receipt, or None when it is not one."""
    def safe_usd(value: object) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        try:
            amount = float(value)
        except OverflowError:
            return None
        return amount if math.isfinite(amount) and amount >= 0 else None

    parent = path.parent.name
    if parent == "xai":
        usage = data.get("usage")
        ticks = usage.get("cost_in_usd_ticks") if isinstance(usage, dict) else None
        amount = safe_usd(ticks)
        return "xai", amount / 1e10 if amount is not None else None
    if parent == "perplexity":
        response = data.get("response")
        usage = response.get("usage") if isinstance(response, dict) else None
        cost = usage.get("cost") if isinstance(usage, dict) else None
        return "perplexity", safe_usd(cost.get("total_cost") if isinstance(cost, dict) else None)
    if path.name.startswith("grok-") and path.name.endswith(".out.json"):
        return "grok-cli", safe_usd(data.get("total_cost_usd"))
    return None


UNATTRIBUTED = ("xai", "perplexity")  # provider-level receipt folders with no project field


def meter(root: Path, since: float, project_only: bool, purpose_prefix: str | None = None,
          budget: float = 25.0, unpriced_bound: float | None = None) -> dict:
    if unpriced_bound is not None and (not math.isfinite(unpriced_bound) or unpriced_bound <= 0):
        raise ValueError("--unpriced-bound must be positive and finite")
    target = PREFIX.fullmatch(purpose_prefix) if purpose_prefix is not None else None
    if purpose_prefix is not None and target is None:
        raise ValueError("--purpose-prefix must be '<project> <M-number|->'")
    selected = (target["project"], target["milestone"]) if target else None
    window = MILESTONE_WINDOWS.get(selected) if selected else None
    totals: dict[str, dict] = {}
    unattributed: dict[str, dict] = {}
    buckets: dict[str, dict[str, dict]] = {name: {} for name in ("labelled", "inferred", "unknown")}
    rule_counts: dict[str, int] = {}
    inferred_receipts: list[dict[str, str]] = []
    unlabelled = 0
    for path in root.rglob("*.json"):
        stamp = receipt_timestamp(path) if selected else path.stat().st_mtime
        if stamp < since:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            if not selected:
                continue
            data = {}  # an unreadable paid receipt cannot lower the budget bound
        if selected and not isinstance(data, dict):
            data = {}
        found = receipt_cost(path, data) if isinstance(data, dict) else None
        if not found:
            continue
        if selected:
            category, project, milestone, rule_id = receipt_identity(path, data, stamp)
            if category == "unknown":
                if window and not (window[0] <= stamp and (window[1] is None or stamp < window[1])):
                    continue
            elif (project, milestone) != selected:
                continue
            purpose = data.get("purpose")
            unlabelled += not isinstance(purpose, str) or not purpose
            bucket = buckets[category]
            if rule_id:
                rule_counts[rule_id] = rule_counts.get(rule_id, 0) + 1
        else:
            bucket = totals
            if project_only and not path.parent.name.startswith(PROJECT_DIRS):
                if path.parent.name not in UNATTRIBUTED:
                    continue
                bucket = unattributed
        provider, usd = found
        if selected and rule_id:
            inferred_receipts.append({"receipt": f"{path.parent.name}/{path.name}", "provider": provider, "rule_id": rule_id})
        row = bucket.setdefault(provider, {"calls": 0, "usd": 0.0, "uncosted": 0})
        row["calls"] += 1
        if usd is None:
            row["uncosted"] += 1
        else:
            row["usd"] += usd
    rounded = lambda rows: {k: {**v, "usd": round(v["usd"], 4)} for k, v in sorted(rows.items())}
    if selected:
        known = sum(row["usd"] for name in ("labelled", "inferred") for row in buckets[name].values())
        unknown = sum(row["usd"] for row in buckets["unknown"].values())
        uncosted = sum(row["uncosted"] for rows in buckets.values() for row in rows.values())
        upper = round(known + unknown + uncosted * (unpriced_bound or 0), 4) if unpriced_bound is not None or not uncosted else None
        reasons = []
        if window is None:
            reasons.append("milestone window is not recorded")
        elif since > window[0]:
            reasons.append("--days excludes part of the milestone window")
        if uncosted and unpriced_bound is None:
            reasons.append("uncosted receipts make the upper bound unknown")
        if upper is not None and upper >= budget:
            reasons.append("upper bound meets or exceeds budget")
        return {
            "since_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(since)),
            "scope": purpose_prefix,
            "project_only": project_only,
            "purpose_prefix": purpose_prefix,
            "window_start_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(window[0])) if window else None,
            "window_end_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(window[1])) if window and window[1] else None,
            "buckets": {name: rounded(rows) for name, rows in buckets.items()},
            "inference_rules": dict(sorted(rule_counts.items())),
            "inferred_receipts": sorted(inferred_receipts, key=lambda row: row["receipt"]),
            "matched_receipts": sum(row["calls"] for name in ("labelled", "inferred") for row in buckets[name].values()),
            "lower_bound_usd": round(known, 4),
            "unknown_usd": round(unknown, 4),
            "upper_known_usd": round(known + unknown, 4),
            "upper_bound_usd": upper,
            "unlabelled": unlabelled,
            "uncosted_receipts": uncosted,
            "unpriced_bound_usd": unpriced_bound,
            "budget_usd": budget,
            "budget_status": "STOP" if reasons else "OK",
            "stop_reasons": reasons,
        }
    report = {
        "since_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(since)),
        "scope": "homemade-llm" if project_only else "all projects",
        "providers": rounded(totals),
        "total_usd": round(sum(v["usd"] for v in totals.values()), 4),
    }
    if project_only:
        report["unattributed"] = rounded(unattributed)
    return report


def self_test() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel, body in {
            "xai/20260927T110000Z.json": {"purpose": "homemade-llm M1 packet-a: grading", "usage": {"cost_in_usd_ticks": 515680000}},
            "xai/20260927T140000Z.json": {"purpose": "review O-044", "usage": {"cost_in_usd_ticks": 400000000}},
            "xai/20260927T150000Z.json": {"usage": {"cost_in_usd_ticks": 200000000}},
            "xai/20260927T160000Z.json": {"purpose": "RV-333 review", "usage": {"cost_in_usd_ticks": 300000000}},
            "perplexity/20260927T170000Z.json": {"purpose": "director-orchestrator - RV-333: review", "response": {"usage": {"cost": {"total_cost": 0.02}}}},
            "perplexity/20260927T180000Z.json": {"response": {"usage": {"cost": {"total_cost": 0.02}}}},
            "homemade-llm/grok-20260927T120000Z.out.json": {"total_cost_usd": 0.33},
            "m1-run/grok-20260927T130000Z.out.json": {"total_cost_usd": 0.1},
            "rv333/grok-20260927T190000Z.out.json": {"total_cost_usd": 1.0},
            "other/codex-1.json": {"note": "subscription, not paid"},
        }.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(body), encoding="utf-8")
        everything = meter(root, 0, project_only=False)
        assert everything["providers"]["xai"] == {"calls": 4, "usd": 0.1416, "uncosted": 0}, everything
        assert everything["providers"]["perplexity"] == {"calls": 2, "usd": 0.04, "uncosted": 0}, everything
        assert everything["providers"]["grok-cli"]["usd"] == 1.43, everything
        assert everything["total_usd"] == 1.6116, everything
        project = meter(root, 0, project_only=True)
        assert project["providers"] == {"grok-cli": {"calls": 1, "usd": 0.33, "uncosted": 0}}, project
        assert project["unattributed"]["xai"]["calls"] == 4, project
        assert project["unattributed"]["perplexity"] == {"calls": 2, "usd": 0.04, "uncosted": 0}, project
        assert "unattributed" not in everything, everything
        filtered = meter(root, 0, project_only=False, purpose_prefix="homemade-llm M1")
        assert filtered["buckets"]["labelled"] == {"xai": {"calls": 1, "usd": 0.0516, "uncosted": 0}}, filtered
        assert filtered["buckets"]["inferred"] == {
            "grok-cli": {"calls": 2, "usd": 0.43, "uncosted": 0},
            "xai": {"calls": 1, "usd": 0.04, "uncosted": 0},
        }, filtered
        assert filtered["buckets"]["unknown"] == {
            "perplexity": {"calls": 1, "usd": 0.02, "uncosted": 0},
            "xai": {"calls": 1, "usd": 0.02, "uncosted": 0},
        }, filtered
        assert filtered["inference_rules"] == {"folder-homemade-window": 1, "folder-m1-run": 1, "text-homemade-m1": 1}, filtered
        assert {row["rule_id"] for row in filtered["inferred_receipts"]} == set(filtered["inference_rules"]), filtered
        assert (filtered["matched_receipts"], filtered["lower_bound_usd"], filtered["upper_bound_usd"], filtered["unlabelled"]) == (4, 0.5216, 0.5616, 4), filtered
        assert filtered["budget_status"] == "OK", filtered
        project_filtered = meter(root, 0, project_only=True, purpose_prefix="homemade-llm M1")
        assert project_filtered["buckets"] == filtered["buckets"], project_filtered
        stopped = meter(root, 0, project_only=False, purpose_prefix="homemade-llm M1", budget=0.55)
        assert stopped["lower_bound_usd"] < stopped["budget_usd"] <= stopped["upper_bound_usd"], stopped
        assert stopped["budget_status"] == "STOP" and stopped["stop_reasons"] == ["upper bound meets or exceeds budget"], stopped
        platform = meter(root, 0, project_only=False, purpose_prefix="director-orchestrator -")
        assert platform["inference_rules"] == {"folder-platform": 1, "text-platform-pr": 1}, platform
        assert receipt_identity(root / "xai/20260927T200000Z.json", {"purpose": "legacy", "purpose_source": "derived", "project": "homemade-llm", "milestone": "M1"}, time.time())[:3] == ("labelled", "homemade-llm", "M1")
        assert receipt_identity(root / "xai/20260927T200001Z.json", {"purpose": "unrelated score"}, time.time())[0] == "unknown"
        assert receipt_cost(root / "xai/20260927T200002Z.json", {"usage": {"cost_in_usd_ticks": -1}}) == ("xai", None)
        bad = root / "xai/20260927T200003Z.json"
        bad.write_text("{", encoding="utf-8")
        default = meter(root, 0, project_only=False, purpose_prefix="homemade-llm M1")
        assert default["budget_status"] == "STOP" and default["upper_bound_usd"] is None, default
        bounded = meter(root, 0, project_only=False, purpose_prefix="homemade-llm M1", unpriced_bound=1.0)
        assert bounded["uncosted_receipts"] == 1 and bounded["unpriced_bound_usd"] == 1.0, bounded
        assert bounded["upper_bound_usd"] == round(bounded["upper_known_usd"] + 1.0, 4) and bounded["budget_status"] == "OK", bounded
        assert meter(root, 0, False, "homemade-llm M1", budget=bounded["upper_bound_usd"], unpriced_bound=1.0)["budget_status"] == "STOP"
        assert meter(root, time.time() + 60, project_only=False)["providers"] == {}
        assert meter(root, time.time() + 60, project_only=False, purpose_prefix="homemade-llm M1")["matched_receipts"] == 0
    print("spend_meter self-test: ok")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=float, default=7)
    parser.add_argument("--project", action="store_true", help="only this project's worktree receipts")
    parser.add_argument("--purpose-prefix", help="project and milestone, e.g. 'homemade-llm M1'")
    parser.add_argument("--budget", type=float, default=25.0, help="milestone USD stop threshold (default: 25)")
    parser.add_argument("--unpriced-bound", type=float, metavar="USD", help="positive USD cap per receipt with no recorded cost")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--receipts", type=Path, default=RECEIPTS)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if not math.isfinite(args.days) or args.days < 0 or not math.isfinite(args.budget) or args.budget <= 0:
        parser.error("--days must be nonnegative and --budget must be positive; both must be finite")
    if args.unpriced_bound is not None and (not math.isfinite(args.unpriced_bound) or args.unpriced_bound <= 0 or args.purpose_prefix is None):
        parser.error("--unpriced-bound requires --purpose-prefix and a positive finite USD amount")
    if args.self_test:
        self_test()
        return 0
    if not args.receipts.is_dir():
        print(f"receipts directory not found: {args.receipts}", file=sys.stderr)
        return 1
    try:
        report = meter(args.receipts, time.time() - args.days * 86400, args.project, args.purpose_prefix, args.budget, args.unpriced_bound)
    except ValueError as error:
        parser.error(str(error))
    if args.json:
        print(json.dumps(report, indent=2))
        return 2 if report.get("budget_status") == "STOP" else 0
    print(f"paid spend since {report['since_utc']} ({report['scope']})")
    if args.purpose_prefix is not None:
        for name, rows in report["buckets"].items():
            print(f"  {name}:")
            for provider, row in rows.items():
                print(f"    {provider}: {row['calls']} receipts, USD {row['usd']:.4f}, uncosted {row['uncosted']}")
        for rule_id, count in report["inference_rules"].items():
            print(f"  inference {rule_id}: {count} receipts")
        upper = report["upper_bound_usd"]
        print(f"  lower USD {report['lower_bound_usd']:.4f}; upper USD {upper if upper is not None else 'UNKNOWN'} (known subtotal USD {report['upper_known_usd']:.4f}); budget USD {report['budget_usd']:.4f}")
        if args.unpriced_bound is not None:
            print(f"  --unpriced-bound USD {args.unpriced_bound:.2f} x {report['uncosted_receipts']} unpriced receipts")
        print(f"  unlabelled {report['unlabelled']}; budget {report['budget_status']}")
        if report["stop_reasons"]:
            print("  stop reasons: " + "; ".join(report["stop_reasons"]))
        print("  All unknown receipts count toward the upper bound. Only verified labels can lower that allocation; unpriced costs force STOP unless bounded.")
        return 2 if report["budget_status"] == "STOP" else 0
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
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 20:39 UTC | END]]
# [[OpenAI Codex | GPT-6 | 2026-09-27 | 21:15 UTC | END]]
