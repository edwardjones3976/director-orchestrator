# [[OpenAI Codex | GPT-6 | 2026-09-08 | 21:00 UTC | BEGIN]]
"""Exercise the unchanged preflight CLI with synthetic fixtures."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / '.review-pr140/case'
sys.path[:0] = [str(CASE), str(CASE / 'src'),
    'C:/Users/edwar/director-orchestrator/.venv-win/Lib/site-packages']
from tests.test_live_dispatch_preflight import _complete, SECRETS

s = _complete()
keys = [
    'allow_paid_runs', 'langgraph_primary_enabled',
    'langgraph_primary_new_intake_enabled',
    'langgraph_primary_live_provider_dispatch_enabled',
    'trusted_provider_cost_ranks_json',
    'langgraph_primary_provider_activation_bindings_json',
    'langgraph_primary_completion_evidence_actor_allowlist_json',
    'project_allowlist', 'repository_allowlist', 'allowed_starting_refs', *SECRETS]
rename = {'shared_secret': 'ORCHESTRATOR_SHARED_SECRET',
          'trusted_provider_cost_ranks_json': 'TRUSTED_PROVIDER_COST_RANKS'}
env = {k: v for k, v in os.environ.items() if k.upper() in {
    'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATH', 'TEMP', 'TMP', 'PATHEXT', 'SYSTEMDRIVE'}}
env.update(PYTHONPATH=os.pathsep.join(sys.path[:3]), PYTHONDONTWRITEBYTECODE='1')
for key in keys:
    value = getattr(s, key)
    env[rename.get(key, key.upper())] = ','.join(value) if isinstance(value, tuple) else str(value)
env.update(COMPLETION_GATES_ENABLED='true', COMPLETION_GATE_STORE_PATH='')
result = subprocess.run([sys.executable, '-B', str(CASE / 'scripts/live-dispatch-preflight.py')],
    cwd=CASE, env=env, text=True, capture_output=True, timeout=45)
record = {'name': 'unchanged CLI with missing completion gate store',
          'exit': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
with (ROOT / '.review-pr140/results.jsonl').open('a', encoding='utf-8') as stream:
    stream.write(json.dumps(record) + '\n')
print(json.dumps(record))
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 21:00 UTC | END]]
