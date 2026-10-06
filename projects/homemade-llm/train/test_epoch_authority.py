"""Synthetic admission/launch regressions; no model or production custody reads."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import test_train_adapter as fixtures
import epoch_authority as authority

trainer = fixtures.train_adapter
real_authority_loader = trainer._epoch_authority


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True), encoding='utf-8')


def refresh_stage(fx):
    """Attacker can rewrite only stage and its binding, not admin files."""
    binding = fx['binding']
    profile = json.loads((fx['rows_dir'] / 'profile.json').read_bytes())
    report = json.loads((fx['rows_dir'] / 'report.json').read_bytes())
    profile['accepted_jsonl_sha256'] = authority.digest((fx['rows_dir'] / 'accepted.jsonl').read_bytes())
    report['accepted_jsonl_sha256'] = profile['accepted_jsonl_sha256']
    write_json(fx['rows_dir'] / 'report.json', report)
    profile['validation_report_sha256'] = authority.digest((fx['rows_dir'] / 'report.json').read_bytes())
    write_json(fx['rows_dir'] / 'profile.json', profile)
    for item in binding['files']:
        directory = fx[{'rows': 'rows_dir', 'train': 'code_dir', 'eval': 'eval_dir'}[item['dir']]]
        data = (directory / item['name']).read_bytes()
        item.update(sha256=authority.digest(data), bytes=len(data))
        key = trainer.PUBLICATION_PAYLOAD_FILES[(item['dir'], item['name'])]
        if key:
            binding['candidate'][key] = item['sha256']
    for field, name in [('accepted_jsonl_sha256', 'accepted.jsonl'), ('profile_sha256', 'profile.json'), ('validation_report_sha256', 'report.json')]:
        binding['resolution'][field] = authority.digest((fx['rows_dir'] / name).read_bytes())
    write_json(fx['binding_path'], binding)


def admit_fixture(fx):
    """Explicit synthetic administrator step, separate from attacker updates."""
    pin, root_path, pub_path = fx['authority_paths']
    root = json.loads(root_path.read_bytes())
    root['adapter_consumption']['candidate'] = dict(fx['binding']['candidate'])
    write_json(root_path, root)
    root_sha = authority.digest(root_path.read_bytes())
    pin.write_text(f"ROOT_FILE = 'route-r-trust-root-v2r4.json'\nROOT_FORMAT = 'route-r-trust-root/v2r4'\nAPPROVED_TRUST_ROOT_SHA256 = '{root_sha}'\n")
    fx['binding']['root_sha256'] = fx['binding']['resolution']['trust_root_sha256'] = root_sha
    profile = json.loads((fx['rows_dir'] / 'profile.json').read_bytes())
    profile['trust_root_sha256'] = root_sha
    write_json(fx['rows_dir'] / 'profile.json', profile)
    refresh_stage(fx)
    publication = json.loads(pub_path.read_bytes())
    publication.update(publication_profile_sha256=fx['binding']['resolution']['profile_sha256'],
                       accepted_jsonl_sha256=fx['binding']['resolution']['accepted_jsonl_sha256'])
    write_json(pub_path, publication)
    fx['binding']['resolution']['publication_sha256'] = authority.digest(pub_path.read_bytes())
    write_json(fx['binding_path'], fx['binding'])


class EpochAuthorityTests(fixtures.AuthorityFixtureCase):
    def fixture(self, tmp):
        return fixtures._build_publication_fixture(Path(tmp), rows=fixtures.slice3_train_rows(9) + fixtures.slice3_dev_rows())

    def verify(self, fx):
        return trainer.verify_publication_binding(fx['binding_path'], fx['rows_dir'], fx['code_dir'], fx['eval_dir'])

    def test_genuine_chain_and_real_root_fabrication(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = self.fixture(tmp)
            self.verify(fx)
            approved = fx['binding']['root_sha256']
            rows = [json.loads(line) for line in (fx['rows_dir'] / 'accepted.jsonl').read_text().splitlines()]
            rows[0]['input']['title'] = 'attacker substituted input'
            (fx['rows_dir'] / 'accepted.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
            refresh_stage(fx)
            self.assertEqual(fx['binding']['root_sha256'], approved)
            with self.assertRaisesRegex(SystemExit, 'protected publication'):
                self.verify(fx)

    def test_fake_root_candidate_batch_and_schema_refuse(self):
        for defect in ('root', 'candidate', 'batch', 'schema'):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as tmp:
                fx = self.fixture(tmp)
                if defect == 'root':
                    fx['binding']['root_sha256'] = fx['binding']['resolution']['trust_root_sha256'] = '0' * 64
                    profile = json.loads((fx['rows_dir'] / 'profile.json').read_bytes())
                    profile['trust_root_sha256'] = '0' * 64
                    write_json(fx['rows_dir'] / 'profile.json', profile)
                elif defect == 'candidate':
                    fx['binding']['candidate']['commit'] = '0' * 40
                elif defect == 'batch':
                    pub = json.loads(fx['authority_paths'][2].read_bytes())
                    pub['batch'] = 'wrong-batch'
                    write_json(fx['authority_paths'][2], pub)
                else:
                    profile = json.loads((fx['rows_dir'] / 'profile.json').read_bytes())
                    profile['row_schema_sha256'] = '0' * 64
                    write_json(fx['rows_dir'] / 'profile.json', profile)
                    refresh_stage(fx)
                    # Even a protected publication of the wrong schema must fail
                    # against the independently approved schema contract.
                    admit_fixture(fx)
                refresh_stage(fx)
                with self.assertRaises(SystemExit):
                    self.verify(fx)

    def test_missing_duplicate_and_unqualified_authority_refuse(self):
        for defect in ('missing', 'duplicate_pin', 'duplicate_root', 'duplicate_publication', 'unqualified'):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as tmp:
                fx = self.fixture(tmp)
                pin, root_path, pub = fx['authority_paths']
                if defect == 'missing':
                    pin.unlink()
                elif defect == 'duplicate_pin':
                    pin.write_text(pin.read_text() + "ROOT_FILE = 'route-r-trust-root-v2r4.json'\n")
                elif defect == 'duplicate_publication':
                    pub.write_text('{"format":"x",' + pub.read_text()[1:])
                else:
                    root = json.loads(root_path.read_bytes())
                    if defect == 'unqualified':
                        root.pop('adapter_consumption')
                        write_json(root_path, root)
                    else:
                        root_path.write_text('{"format":"x",' + root_path.read_text()[1:])
                    pin.write_text(pin.read_text().replace(fx['binding']['root_sha256'], authority.digest(root_path.read_bytes())))
                with self.assertRaises(SystemExit):
                    self.verify(fx)

    def test_publication_and_profile_require_approved_administrator(self):
        for defect in ('publisher', 'builder', None, [], 'codex', ['codex', 'codex'], [''], [{}], [' codex']):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as tmp:
                fx = self.fixture(tmp)
                if defect == 'publisher':
                    pub = json.loads(fx['authority_paths'][2].read_bytes())
                    pub['published_by'] = 'other-seat'
                    write_json(fx['authority_paths'][2], pub)
                    fx['binding']['resolution']['publication_sha256'] = authority.digest(fx['authority_paths'][2].read_bytes())
                    write_json(fx['binding_path'], fx['binding'])
                    reason = 'invalid protected publication'
                elif defect == 'builder':
                    profile = json.loads((fx['rows_dir'] / 'profile.json').read_bytes())
                    profile['built_by'] = 'other-seat'
                    write_json(fx['rows_dir'] / 'profile.json', profile)
                    admit_fixture(fx)
                    reason = 'builder is not an approved administrator'
                else:
                    root = json.loads(fx['authority_paths'][1].read_bytes())
                    root['custody']['admins'] = defect
                    write_json(fx['authority_paths'][1], root)
                    admit_fixture(fx)
                    reason = 'unqualified custody administrators'
                with self.assertRaisesRegex(SystemExit, reason):
                    self.verify(fx)

    def test_direct_epoch_and_check_only_refuse_before_model_and_outputs(self):
        for check_only in (False, True):
            with self.subTest(check_only=check_only), tempfile.TemporaryDirectory() as tmp:
                fx = self.fixture(tmp)
                fx['authority_paths'][0].unlink()
                report = Path(tmp) / 'output' / 'report.json'
                models = Path(tmp) / 'models'
                with mock.patch.object(trainer, '__file__', str(fx['code_dir'] / 'train_adapter.py')), \
                        mock.patch.dict(os.environ, {'ADAPTER_MODELS_DIR': str(models)}), \
                        mock.patch.object(trainer, '_prompt_template', side_effect=AssertionError('renderer before authority')) as renderer, \
                        mock.patch.object(trainer, 'FastLanguageModel') as loader:
                    with self.assertRaises(SystemExit):
                        trainer.main(['--rows', str(fx['rows_dir'] / 'accepted.jsonl'), '--report', str(report),
                            '--epochs', '1', '--base', trainer.WINNER_BASE, '--revision', trainer.WINNER_REVISION,
                            '--publication-binding', str(fx['binding_path']), *(['--check-only'] if check_only else [])])
                    self.assertEqual(loader.mock_calls, [])
                    renderer.assert_not_called()
                self.assertFalse(report.parent.exists())
                self.assertFalse(models.exists())

    def test_authority_source_substitution_and_read_once_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            authority_path = root / 'epoch_authority.py'
            source = Path(authority.__file__).read_bytes().replace(b'\r\n', b'\n')
            authority_path.write_bytes(source)
            actual_read = Path.read_bytes
            def substitute_after_read(path):
                data = actual_read(path)
                if path == authority_path:
                    authority_path.write_text("raise RuntimeError('substituted authority executed')")
                return data
            with mock.patch.object(trainer, '__file__', str(root / 'train_adapter.py')), mock.patch.object(Path, 'read_bytes', substitute_after_read):
                verified = real_authority_loader()
                self.assertTrue(callable(verified.authenticate))
            with mock.patch.object(trainer, '__file__', str(root / 'train_adapter.py')):
                with self.assertRaisesRegex(SystemExit, 'source differs'):
                    real_authority_loader()

    def test_bootstrap_checks_replaced_trainer_and_executes_verified_buffer(self):
        for malicious in (False, True):
            with self.subTest(malicious=malicious), tempfile.TemporaryDirectory() as tmp:
                fx = self.fixture(tmp)
                sentinel = Path(tmp) / 'escaped'
                evil = f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('bad')\n".encode()
                (fx['code_dir'] / 'train_adapter.py').write_bytes(b'VERIFIED_CONTROL = True\n')
                # Real renderer plus pinned synthetic downstream modules. The
                # actual predictor loader is exercised separately below; it
                # expects Linux /train paths in the production container.
                (fx['eval_dir'] / 'predict_dev.py').write_bytes(b'import prompt_template\nassert isinstance(_EPOCH_TEMPLATE_BYTES, bytes)\n')
                (fx['eval_dir'] / 'dev_selection.py').write_bytes(b'VERIFIED_SCORER = True\n')
                refresh_stage(fx)
                admit_fixture(fx)
                if malicious:
                    (fx['code_dir'] / 'train_adapter.py').write_bytes(evil)
                    refresh_stage(fx)
                original_read = authority.read
                def mapped_read(path):
                    paths = {'/workspace/publication-binding.json': fx['binding_path']}
                    for prefix, directory in (('/workspace/rows/', fx['rows_dir']), ('/train/', fx['code_dir']), ('/eval/', fx['eval_dir'])):
                        if str(path).startswith(prefix):
                            paths[str(path)] = directory / str(path)[len(prefix):]
                    result = original_read(paths.get(str(path), path))
                    if str(path) == '/train/train_adapter.py' and not malicious:
                        (fx['code_dir'] / 'train_adapter.py').write_bytes(evil)
                    return result
                # Bootstrap paths are fixed in production. Patch only filesystem
                # lookup for this in-process synthetic launch, never CLI options.
                with mock.patch.object(authority, 'read', side_effect=mapped_read), mock.patch.dict(sys.modules):
                    if malicious:
                        with self.assertRaises(SystemExit):
                            authority.bootstrap()
                    else:
                        authority.bootstrap()
                        self.assertTrue(sys.modules['__main__'].VERIFIED_CONTROL)
                self.assertFalse(sentinel.exists())

    def test_predictor_rejects_untrusted_template_buffer(self):
        import predict_dev
        good = fixtures._publication_admitted_template_bytes()
        with mock.patch.dict(predict_dev.__dict__, {'_EPOCH_TEMPLATE_BYTES': good}), \
                mock.patch.object(Path, 'read_bytes', side_effect=AssertionError('must execute checked buffer')):
            module, source = predict_dev._load_prompt_template()
            self.assertEqual(source, good)
            self.assertTrue(callable(module.render_messages))
        with mock.patch.dict(predict_dev.__dict__, {'_EPOCH_TEMPLATE_BYTES': b"raise RuntimeError('bad template')"}):
            with self.assertRaisesRegex(predict_dev.UnsupportedRuntime, 'not the admitted'):
                predict_dev._load_prompt_template()

    def test_authority_symlink_ancestry_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'real').mkdir()
            (root / 'real' / 'record').write_text('{}')
            if os.name == 'nt':
                result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(root / 'alias'), str(root / 'real')], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            else:
                (root / 'alias').symlink_to(root / 'real', target_is_directory=True)
            try:
                with self.assertRaisesRegex(SystemExit, 'reparse'):
                    authority.read(root / 'alias' / 'record')
            finally:
                (root / 'alias').rmdir() if os.name == 'nt' else (root / 'alias').unlink()

    def test_mount_destinations_and_authority_reparse_refuse(self):
        pwsh = shutil.which('pwsh')
        if os.name != 'nt' or pwsh is None:
            self.skipTest('Windows PowerShell host mount validation')
        helper = Path(__file__).parent.parent / 'scripts' / 'train-adapter-stage.ps1'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fx = self.fixture(tmp)
            pin, root_path, pub = fx['authority_paths']
            (root / 'authority' / 'publication').mkdir()
            shutil.copyfile(root_path, root / 'authority' / 'route-r-trust-root-v2r4.json')
            shutil.copyfile(pub, root / 'authority' / 'publication' / 'batch-0001.publication.json')
            script = f"""
$ErrorActionPreference = 'Stop'
. '{helper.as_posix()}'
$locations = @{{ PinFile = '{pin.as_posix()}'; CustodyRoot = '{(root / 'authority').as_posix()}' }}
$stage = @{{ Train = '{fx['code_dir'].as_posix()}'; Eval = '{fx['eval_dir'].as_posix()}'; Input = '{fx['rows_dir'].as_posix()}' }}
function Make-Args([string[]]$mounts) {{
    New-TrainerDockerArgs -Image 'unsloth/unsloth@sha256:1644d635bc7c5b57ed64cabbab1ae00647dfb583c2135fdfb6a461aeeba52739' -Name 'test' -CommonMounts $mounts -Stage $stage `
        -BindingPath '{fx['binding_path'].as_posix()}' -Base 'synthetic' -Revision 'synthetic' `
        -ReportContainerPath '/workspace/reports/test.json' -Epochs 1 -AuthoritySource '# verified synthetic code' `
        -Batch 'batch-0001' -RootFile 'route-r-trust-root-v2r4.json' -Locations $locations
}}
$good = @(Make-Args @('-v', '{root.as_posix()}:/safe:ro'))
if ($good -notcontains '-I' -or $good -notcontains '-c' -or $good -notcontains "$($locations.PinFile):/run/route-r-authority/root-pin.py:ro") {{ throw 'valid control failed' }}
$count = 0
foreach ($dest in '/run', '/run/route-r-authority/root.json', '/run/route-r-authority/root.json/nested', '/workspace', '/train', '/') {{
    $denied = $false
    try {{ Make-Args @('-v', "{root.as_posix()}:$($dest):ro") | Out-Null }} catch {{ $denied = $_.Exception.Message -match 'shadows reserved|unexpected trainer common mount syntax' }}
    if (-not $denied) {{ throw "collision admitted: $dest" }}
    $count++
}}
$denied = $false
try {{ Make-Args @('-v', '{root.as_posix()}:/safe:ro', '-v', '{root.as_posix()}:/safe/sub:ro') | Out-Null }} catch {{ $denied = $_.Exception.Message -match 'overlapping trainer mount' }}
if (-not $denied) {{ throw 'common mount collision admitted' }}
'PASS positive + ' + $count + ' authority/stage collisions + common collision'
"""
            result = subprocess.run([pwsh, '-NoProfile', '-NonInteractive', '-Command', script], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('PASS positive + 6', result.stdout)


if __name__ == '__main__':
    unittest.main()
