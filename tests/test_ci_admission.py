"""Shared-host admission contract; fixtures never dispatch Actions or Docker."""

from collections import Counter, deque
from itertools import product
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROUTING = (
    "vars.DIRECTOR_CI_RUNNER == 'self-hosted' && "
    "(github.event_name != 'pull_request' || "
    "github.event.pull_request.head.repo.full_name == github.repository)"
)


class CiAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8'))
        cls.jobs = cls.workflow['jobs']

    def expanded_jobs(self):
        for job_id, job in self.jobs.items():
            for os_name in job.get('strategy', {}).get('matrix', {}).get('os', ['none']):
                yield job_id, job, os_name

    def group(self, job_id, job, os_name, runner='self-hosted', event='push',
              same_repo=True, run_id=1, attempt=1):
        # Interpret only the prescribed expression, failing closed on any drift.
        expression = job['concurrency']['group']
        prefix = '${{ ' + ROUTING + " && 'director-blackbox-ci' || format('"
        suffix = "', github.run_id, github.run_attempt, matrix.os || 'none') }}"
        self.assertTrue(expression.startswith(prefix), expression)
        self.assertTrue(expression.endswith(suffix), expression)
        template = expression[len(prefix):-len(suffix)]
        self.assertEqual(template, 'director-hosted-ci-{0}-{1}-' + job_id + '-{2}')
        if runner == 'self-hosted' and (event != 'pull_request' or same_repo):
            return 'director-blackbox-ci'
        return template.format(run_id, attempt, os_name)

    def test_all_expanded_jobs_share_one_queue_only_when_self_hosted(self):
        for runner, event, same_repo in product(['self-hosted', ''], ['push', 'pull_request'], [True, False]):
            groups = []
            for job_id, job, os_name in self.expanded_jobs():
                with self.subTest(job=job_id, os=os_name, runner=runner, event=event, same_repo=same_repo):
                    self.assertTrue(job['runs-on'].startswith('${{ ' + ROUTING + ' && fromJSON('))
                    self.assertIs(job['concurrency']['cancel-in-progress'], False)
                    self.assertEqual(job['concurrency']['queue'], 'max')
                    self.assertEqual(job['env']['DIRECTOR_TEST_LANE_MAX_CONCURRENCY'], '4')
                    groups.append(self.group(job_id, job, os_name, runner, event, same_repo))
            if runner == 'self-hosted' and (event != 'pull_request' or same_repo):
                self.assertEqual(set(groups), {'director-blackbox-ci'})
            else:
                self.assertEqual(len(set(groups)), 8)
                self.assertNotIn('director-blackbox-ci', groups)

    def test_hosted_keys_are_unique_across_runs_attempts_jobs_and_matrix(self):
        groups = [
            self.group(job_id, job, os_name, runner='', run_id=run_id, attempt=attempt)
            for run_id, attempt in product([1, 2], [1, 2])
            for job_id, job, os_name in self.expanded_jobs()
        ]
        self.assertEqual(len(set(groups)), 32)

    def test_required_names_dependencies_supersession_and_cleanup_are_preserved(self):
        names = [job['name'].replace('${{ matrix.os }}', os_name)
                 for _, job, os_name in self.expanded_jobs()]
        self.assertEqual(len(names), 8)
        self.assertEqual(Counter(names), Counter([
            'CI scope', 'Documentation changes', 'Contribution attribution',
            'Secret hygiene', 'Python 3.12 (ubuntu-latest)',
            'Python 3.12 (windows-latest)', 'Compose and container build',
            'Cross-process recovery probe',
        ]))
        self.assertEqual(self.workflow['concurrency'], {
            'group': 'ci-${{ github.event.pull_request.number || github.run_id }}',
            'cancel-in-progress': "${{ github.event_name == 'pull_request' }}",
        })
        self.assertEqual({key: job.get('needs') for key, job in self.jobs.items()}, {
            'scope': None, 'changes': None, 'contribution-attribution': None,
            'python': 'scope', 'containers': ['changes', 'scope'],
            'secret-hygiene': None, 'recovery-probe': ['changes', 'scope'],
        })
        for job_id in ('python', 'containers', 'recovery-probe'):
            self.assertEqual(self.jobs[job_id]['if'], '${{ always() && !cancelled() }}')
        python = self.jobs['python']
        self.assertIs(python['strategy']['fail-fast'], False)
        self.assertEqual([(row['os'], row['shards']) for row in python['strategy']['matrix']['include']],
                         [('ubuntu-latest', 8), ('windows-latest', 4)])
        stops = {step['name']: step for step in python['steps']
                 if step.get('name', '').startswith('Stop disposable PostgreSQL')}
        self.assertEqual(len(stops), 4)
        for os_name, shell in [('Linux', 'pwsh'), ('Windows', 'powershell')]:
            step = stops[f'Stop disposable PostgreSQL (self-hosted {os_name}, test lane)']
            self.assertEqual(step['if'], "always() && runner.environment == 'self-hosted' && "
                             f"runner.os == '{os_name}' && env.DIRECTOR_CI_PG_LANE != ''")
            self.assertEqual(step['shell'], shell)
            self.assertIn('./scripts/windows-postgres-test-lane.ps1 -Action Stop', step['run'])
            hosted = stops[f'Stop disposable PostgreSQL ({os_name})']
            self.assertEqual(hosted['if'], "always() && runner.environment == 'github-hosted' && "
                             f"runner.os == '{os_name}'")

    def test_five_contenders_wait_without_displacement(self):
        # Assumption: GitHub enforces one running entry per repository group,
        # with max retaining up to 100 pending entries. This models admission,
        # not live Actions scheduling or the independent outer PR cancellation.
        # https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency
        contenders = list(self.expanded_jobs())[:5]  # Includes both OS lanes.
        active, pending, events, displaced = {}, {}, [], []
        maximum = 0
        for number, (job_id, job, os_name) in enumerate(contenders):
            identity = f'{number}:{job_id}:{os_name}'
            group = self.group(job_id, job, os_name, run_id=number + 1)
            queue = pending.setdefault(group, deque())
            config = job['concurrency']
            if config['cancel-in-progress'] and group in active:
                displaced.append(active.pop(group))
            if group not in active:
                active[group] = identity
                events.append(('start', identity))
            else:
                if config.get('queue', 'single') == 'single':
                    displaced.extend(queue)
                    queue.clear()
                if len(queue) < (100 if config.get('queue') == 'max' else 1):
                    queue.append(identity)
                    events.append(('queued', identity))
                else:
                    displaced.append(identity)
            maximum = max(maximum, len(active))
        self.assertEqual(sum(map(len, pending.values())), 4, events)
        while active:
            for group in list(active):
                events.append(('end', active.pop(group)))
                if pending[group]:
                    active[group] = pending[group].popleft()
                    events.append(('start', active[group]))
        self.assertEqual(maximum, 1, events)
        self.assertEqual(displaced, [], events)
        expected = Counter(f'{i}:{job_id}:{os_name}' for i, (job_id, _, os_name) in enumerate(contenders))
        for action in ('start', 'end'):
            self.assertEqual(Counter(identity for kind, identity in events if kind == action), expected)
        running = 0
        for kind, _ in events:
            running += {'start': 1, 'end': -1, 'queued': 0}[kind]
            self.assertIn(running, (0, 1), events)
        self.assertEqual(running, 0)


if __name__ == '__main__':
    unittest.main()
