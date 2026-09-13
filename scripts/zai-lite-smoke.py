# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 01:00 UTC | BEGIN]]
"""Owner-invoked one-shot Lite trial. No tools, deployment or general API calls."""
import argparse
import ast
import json
import subprocess
import time
from pathlib import Path

from director_orchestrator.langgraph_runtime import ProviderAdapterContext
from director_orchestrator.packet_contracts import document_digest
from director_orchestrator.zai_trial import build_zai_trial_adapter


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--executable", required=True, type=Path)
    parser.add_argument("--evidence-dir", required=True, type=Path)
    parser.add_argument("--lite-entitlement-verified", action="store_true", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    blob = subprocess.check_output(["git", "rev-parse", "HEAD:AGENTS.md"], cwd=root, text=True).strip()
    work = json.loads((root / "docs/schemas/model-work-packet-v1.example.json").read_text())
    work.update(task_id="DIR-ZAI-LITE-ONBOARDING", assignment_id="DIR-ZAI-LITE-ONBOARDING-1",
                project="director-orchestrator", repository="edwardjones3976/director-orchestrator",
                starting_ref=sha, objective='Return outcome containing only Python source for def add(a, b): return a + b. Do not run tools. Set status SUCCEEDED.',
                non_goals=["No tools, file access, external effects, or general API calls."],
                allowed_tools=[], allowed_external_effects=[],
                acceptance_checks=["Return a pure two-argument addition function; local harness checks it."],
                evidence_requirements=["Do not claim local execution."],
                idempotency_key="director:zai:lite:onboarding:1")
    work["authority_snapshot"] = [{"path": "AGENTS.md", "commit_sha": sha, "blob_sha": blob}]
    work["authority_snapshot_digest"] = document_digest(work["authority_snapshot"])
    work["correlation"].update(correlation_id="zai-lite-onboarding-1", source_event_id="owner-zai-onboarding")
    work["limits"].update(budget_usd=0, timeout_seconds=60, retry_limit=1)
    context = ProviderAdapterContext(
        task_id=work["task_id"], assignment_id=work["assignment_id"], idempotency_key=work["idempotency_key"],
        work_packet_digest=document_digest(work), writer_token_digest="sha256:" + "0" * 64,
        timeout_seconds=60, budget_usd=0, max_hops=1, max_revisions=1,
        allowed_tools=(), allowed_external_effects=())
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    report = {"provider": "zai", "source_sha": sha, "production_enabled": False,
              "billing_cost_usd": None, "subscription_entitlement": "owner-observed Lite valid",
              "test": "Director PacketBoundaryAdapter tools-disabled addition fixture"}
    start = time.monotonic()
    try:
        adapter = build_zai_trial_adapter(executable=args.executable, env_file=args.env_file,
                    journal=args.evidence_dir / "attempts", model="glm-5.3-flash", lite_verified=True)
        result = adapter.dispatch(work, context=context)
        # Validate source shape before executing the fixed, tiny arithmetic fixture.
        tree = ast.parse(result["outcome"])
        expected = ast.parse("def add(a, b): return a + b")
        if ast.dump(tree) != ast.dump(expected):
            raise ValueError("Returned code does not match the safe addition fixture")
        namespace = {}
        exec(compile(tree, "<zai-addition-fixture>", "exec"), {"__builtins__": {}}, namespace)
        for a, b in ((2, 3), (-2, 2), (0, 0), (1.5, 2.25)):
            assert namespace["add"](a, b) == a + b
        report.update(status="PASS", cases_passed=4, run_reference=result["run_reference"],
                      packet_validated=True, result=result)
    except Exception as exc:
        # Exceptions may contain provider output; report the type only.
        report.update(status="FAIL", error_type=type(exc).__name__,
                      detail="Attempt retained. No automatic retry or general-API fallback.")
    report["elapsed_seconds"] = round(time.monotonic() - start, 3)
    (args.evidence_dir / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "result"}, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
# [[OpenAI Codex | GPT-6 Astra | 2026-09-13 | 01:00 UTC | END]]
