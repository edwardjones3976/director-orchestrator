# [[OpenAI Codex | GPT-6 | 2026-09-08 | 21:00 UTC | BEGIN]]
"""PR 140 scratch-only adversarial review; never edits tracked files."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / '.review-pr140' / 'case'
DEPS = Path('C:/Users/edwar/director-orchestrator/.venv-win/Lib/site-packages')
CASE.mkdir(exist_ok=True)
shutil.copytree(ROOT / 'src', CASE / 'src', dirs_exist_ok=True,
                ignore=shutil.ignore_patterns('__pycache__'))
(CASE / 'scripts').mkdir(exist_ok=True)
(CASE / 'tests').mkdir(exist_ok=True)
(CASE / 'tests' / '__init__.py').write_text('', encoding='utf-8')
TEST = 'tests.test_live_dispatch_preflight'
shutil.copyfile(ROOT / 'tests/test_live_dispatch_preflight.py', CASE / 'tests/test_live_dispatch_preflight.py')
SCRIPT = CASE / 'scripts/live-dispatch-preflight.py'
CONFIG = CASE / 'src/director_orchestrator/config.py'
original_script = (ROOT / 'scripts/live-dispatch-preflight.py').read_text(encoding='utf-8')
original_config = CONFIG.read_text(encoding='utf-8')
env = {k: v for k, v in os.environ.items() if k.upper() in {
    'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATH', 'TEMP', 'TMP', 'PATHEXT', 'SYSTEMDRIVE'}}
env['PYTHONPATH'] = str(CASE / 'src') + os.pathsep + str(DEPS)
env['PYTHONDONTWRITEBYTECODE'] = '1'

def run(name, args, extra_env=None):
    result = subprocess.run([sys.executable, '-B', *args], cwd=CASE,
        env={**env, **(extra_env or {})}, text=True, capture_output=True, timeout=45)
    record = {'name': name, 'exit': result.returncode,
              'stdout': result.stdout, 'stderr': result.stderr}
    with (ROOT / '.review-pr140/results.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(record) + '\n')
    print(json.dumps({'name': name, 'exit': result.returncode,
        'summary': (result.stdout + result.stderr)[-700:]}), flush=True)
    return record

def reset():
    SCRIPT.write_text(original_script, encoding='utf-8')
    CONFIG.write_text(original_config, encoding='utf-8')

mutants = [
 ('UnsetEnvironmentTests.test_every_prerequisite_is_reported_at_once', 'script',
  '        ("workload policy", target.validate_workload_policy_configuration),\n', ''),
 ('UnsetEnvironmentTests.test_exit_status_is_nonzero', 'script', 'return 1 if problems else 0', 'return 0'),
 ('UnsetEnvironmentTests.test_every_reported_problem_names_its_variables', 'script',
  '    named = tuple(dict.fromkeys(VARIABLE_NAME.findall(problem)))', '    return ()\n    named = tuple(dict.fromkeys(VARIABLE_NAME.findall(problem)))'),
 ('CompleteEnvironmentTests.test_nothing_is_outstanding', 'script', '    return problems',
  '    return problems + [("mutation", "EXTRA_SETTING must be configured")]'),
 ('CompleteEnvironmentTests.test_exit_status_is_zero', 'script', 'return 1 if problems else 0', 'return 1'),
 ('CompleteEnvironmentTests.test_report_states_that_configuration_is_not_authority', 'script',
  'acceptance, evidence, and owner approval are separate gates.', 'configuration authorizes dispatch'),
 ('CouplingTests.test_paid_runs_alone_is_reported_as_the_crash_inducing_state', 'script',
  '        settings.langgraph_primary_enabled\n', '        False\n'),
 ('CouplingTests.test_paid_runs_off_reports_no_coupling_failure', 'script',
  '        and settings.allow_paid_runs\n', ''),
 ('CouplingTests.test_offline_simulation_with_live_dispatch_is_mutually_exclusive', 'config',
  '            self.langgraph_primary_offline_simulation_enabled\n            and self.langgraph_primary_live_provider_dispatch_enabled',
  '            False\n            and self.langgraph_primary_live_provider_dispatch_enabled'),
 ('SecretDisclosureTests.test_no_configured_value_ever_appears_in_the_output', 'script',
  '    lines.append("")', '    lines.append(settings.shared_secret)\n    lines.append("")'),
 ('SecretDisclosureTests.test_a_validator_that_leaked_a_value_would_be_redacted', 'script',
  'text = text.replace(item, "<redacted>")', 'text = text'),
 ('SecretDisclosureTests.test_the_report_prints_switch_names_with_booleans_only', 'script',
  '{str(getattr(settings, attribute)).lower()}', '{getattr(settings, attribute)}'),
 ('ReadOnlyTests.test_it_imports_nothing_that_can_write_connect_or_dispatch', 'script',
  'import dataclasses', 'import socket\nimport dataclasses'),
 ('ReadOnlyTests.test_it_calls_nothing_that_opens_a_file_or_a_process', 'script',
  '    lines.append("")', '    open("mutation-write.txt", "w").close()\n    lines.append("")'),
 ('ReadOnlyTests.test_it_never_assigns_to_the_environment_or_to_settings', 'script',
  '    lines.append("")', '    sys.modules["os"].environ["PREFLIGHT_MUTATION_FLAG"] = "1"\n    lines.append("")'),
]

reset()
run('baseline', ['-m', 'unittest', TEST, '-q'])
for test, which, old, new in mutants:
    reset()
    source = original_script if which == 'script' else original_config
    assert source.count(old) == 1, (test, source.count(old))
    (SCRIPT if which == 'script' else CONFIG).write_text(source.replace(old, new), encoding='utf-8')
    run('targeted mutation: ' + test, ['-m', 'unittest', TEST + '.' + test, '-q'])

reset()
SCRIPT.write_text(original_script.replace('{redact(problem, settings)}', '{problem}'), encoding='utf-8')
run('all tests with report redaction disconnected', ['-m', 'unittest', TEST, '-q'])

reset()
SCRIPT.write_text(original_script.replace('    lines.append("")',
    '    sys.modules["io"].open("mutation-write-bypass.txt", "w").close()\n    sys.modules["os"].environ.update(PREFLIGHT_MUTATION_FLAG="1")\n    lines.append("")'), encoding='utf-8')
run('all tests with file write and environment mutation', ['-m', 'unittest', TEST, '-q'])
print(json.dumps({'mutation_file_created': (CASE / 'mutation-write-bypass.txt').is_file()}), flush=True)

reset()
CONFIG.write_text(original_config.replace(
    '    def execution_profile_readiness(self) -> dict[str, bool]:\n        return {',
    '    def execution_profile_readiness(self) -> dict[str, bool]:\n        return {\n            "REVIEW_REQUIRED_ENV": bool(os.getenv("REVIEW_REQUIRED_ENV")),'), encoding='utf-8')
run('all tests with new unmet real readiness prerequisite', ['-m', 'unittest', TEST, '-q'])
run('new prerequisite false green', ['-c', '''
from tests.test_live_dispatch_preflight import PREFLIGHT, _complete
from unittest.mock import patch
s = _complete()
print('real_execution_profile_all_ready=', all(s.execution_profile_readiness().values()))
print('new_prerequisite=', s.execution_profile_readiness()['REVIEW_REQUIRED_ENV'])
with patch.object(PREFLIGHT, 'Settings', lambda: s):
    print('preflight_exit=', PREFLIGHT.main())
'''])

reset()
CONFIG.write_text(original_config.replace(
    '    def validate_workload_policy_configuration(self) -> None:\n',
    '    def validate_workload_policy_configuration(self) -> None:\n        if not os.getenv("REVIEW_REQUIRED_ENV"):\n            raise ValueError("REVIEW_REQUIRED_ENV must be configured")\n'), encoding='utf-8')
run('new prerequisite in invoked validator is detected', ['-c', '''
from tests.test_live_dispatch_preflight import PREFLIGHT, _complete
from unittest.mock import patch
s = _complete()
with patch.object(PREFLIGHT, 'Settings', lambda: s):
    print('preflight_exit=', PREFLIGHT.main())
'''])

reset()
run('existing incomplete configuration and aggregation', ['-c', '''
from tests.test_live_dispatch_preflight import PREFLIGHT, _complete, _bare
from unittest.mock import patch
from dataclasses import replace
s = _complete(completion_gates_enabled=True, completion_gate_store_path='')
try:
    s.validate_completion_gate_configuration()
except ValueError as exc:
    print('real_validator=', str(exc))
with patch.object(PREFLIGHT, 'Settings', lambda: s):
    print('preflight_exit=', PREFLIGHT.main())
s = _complete(workload_policy_mode='bad', workload_policy_window_assignments=19,
              workload_policy_shadow_assignments=4)
print('multiple_workload_defects=', PREFLIGHT.unmet(s))
s = replace(_bare(), langgraph_primary_snapshot_max_files=0)
print('snapshot_and_missing_dsn=', PREFLIGHT.unmet(s))
'''])

run('constructor secret echo', ['scripts/live-dispatch-preflight.py'], {
    'LANGGRAPH_PRIMARY_SNAPSHOT_MAX_FILES': 'SYNTHETIC_SECRET_VALUE',
    'ORCHESTRATOR_SHARED_SECRET': 'SYNTHETIC_SECRET_VALUE'})
run('import-time secret echo', ['scripts/live-dispatch-preflight.py'], {
    'MAX_AGENT_HOPS': 'SYNTHETIC_SECRET_VALUE',
    'ORCHESTRATOR_SHARED_SECRET': 'SYNTHETIC_SECRET_VALUE'})
run('report secret paths and missing variable mapping', ['-c', '''
from tests.test_live_dispatch_preflight import PREFLIGHT, _complete
from dataclasses import replace
s = _complete(langgraph_primary_owner_token='SYNTHETIC_SECRET_VALUE_12345678901234567890')
problem = 'LANGGRAPH_PRIMARY_OWNER_TOKEN rejected ' + s.langgraph_primary_owner_token
print('uppercase_secret_report=', '\\n'.join(PREFLIGHT.report(s, [('test', problem)])))
s = replace(s, claude_callback_secret='s3crt')
print('short_secret_report=', '\\n'.join(PREFLIGHT.report(s, [('test', 'CLAUDE_CALLBACK_SECRET rejected s3crt')])))
s = _complete(langgraph_parallel_external_dispatch_enabled=True, langgraph_parallel_cursor_cap=0)
print('bad_cap_report=', '\\n'.join(PREFLIGHT.report(s, PREFLIGHT.unmet(s))))
'''])
reset()
# [[OpenAI Codex | GPT-6 | 2026-09-08 | 21:00 UTC | END]]
