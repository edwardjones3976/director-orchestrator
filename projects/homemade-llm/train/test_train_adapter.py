# [[Cursor | Auto | 2026-09-29 | 04:18 UTC | BEGIN]]
# [[Cursor | Auto | 2026-09-29 | 04:03 UTC | BEGIN]]
# [[Cursor | Auto | 2026-09-29 | 01:12 UTC | BEGIN]]
# [[Cursor | Auto | 2026-09-29 | 01:10 UTC | BEGIN]]
"""Unit tests for the M2-T1 trainer's non-GPU contract.

    python projects/homemade-llm/train/test_train_adapter.py
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


class OfflineTrainerCallback:
    """No-op lifecycle from the public Transformers TrainerCallback contract.

    Heavy runtime imports stay stubbed for synthetic tests. Event dispatch below
    uses the handler's attribute lookup and control return rules, with no fallback
    for missing methods, so a plain epoch-only callback cannot pass.
    """

    def on_init_end(self, args, state, control, **kwargs):
        pass

    def on_train_begin(self, args, state, control, **kwargs):
        pass

    def on_train_end(self, args, state, control, **kwargs):
        pass

    def on_epoch_begin(self, args, state, control, **kwargs):
        pass

    def on_epoch_end(self, args, state, control, **kwargs):
        pass

    def on_step_begin(self, args, state, control, **kwargs):
        pass

    def on_pre_optimizer_step(self, args, state, control, **kwargs):
        pass

    def on_optimizer_step(self, args, state, control, **kwargs):
        pass

    def on_substep_end(self, args, state, control, **kwargs):
        pass

    def on_step_end(self, args, state, control, **kwargs):
        pass

    def on_evaluate(self, args, state, control, **kwargs):
        pass

    def on_predict(self, args, state, control, **kwargs):
        pass

    def on_save(self, args, state, control, **kwargs):
        pass

    def on_log(self, args, state, control, **kwargs):
        pass

    def on_prediction_step(self, args, state, control, **kwargs):
        pass


class OfflineCallbackHandler:
    def __init__(self, callbacks, model):
        self.callbacks = callbacks
        self.model = model

    def call_event(self, event, args, state, control, **kwargs):
        for callback in self.callbacks:
            result = getattr(callback, event)(
                args, state, control, model=self.model, processing_class=None,
                optimizer=None, lr_scheduler=None, train_dataloader=None,
                eval_dataloader=None, **kwargs,
            )
            if result is not None:
                control = result
        return control


for name, attrs in {
    "torch": {},
    "unsloth": {"FastLanguageModel": object},
    "datasets": {"Dataset": object},
    "transformers": {"set_seed": lambda *_a, **_k: None, "TrainerCallback": OfflineTrainerCallback},
    "huggingface_hub": {"snapshot_download": lambda *_a, **_k: ""},
    "trl": {"SFTConfig": object, "SFTTrainer": object},
}.items():
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules.setdefault(name, module)

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))
import train_adapter  # noqa: E402


def row(split: str = "train") -> dict:
    return {
        "split": split,
        "heldout_check": {
            "set_a_manifest": "a" * 64,
            "set_b_manifest": "b" * 64,
            "source_lineage": "clear",
            "near_duplicate": {
                "status": "clear", "max_overlap": 0.0, "threshold": 0.5, "quarantine_band": 0.2,
            },
            "title_objective_match": False,
            "overlap": "none",
        },
        "input": {
            "id": "row-1",
            "objective": "Add a bounded retry.",
            "repository": "example/repo",
            "title": "Retry the client",
            "context": [{
                "kind": "assignment_packet",
                "text": "Retry three times.",
                "source_ref": "packet/row-1",
                "source_sha256": "ab" * 32,
            }],
        },
        "target": {
            "acceptance_checks": ["the client stops at three attempts"],
            "constraints": ["no unbounded loop"],
            "evidence_requirements": ["the test names the cap"],
            "non_goals": ["no new provider"],
        },
    }


def write_accepted(directory: Path, rows: list[dict]) -> Path:
    path = directory / "accepted.jsonl"
    path.write_text("".join(json.dumps(item) + "\n" for item in rows), encoding="utf-8")
    counts = {"train": sum(item["split"] == "train" for item in rows), "dev": sum(item["split"] == "dev" for item in rows)}
    (directory / "report.json").write_text(json.dumps({
        "n_accepted": len(rows), "rows_per_split": counts,
    }), encoding="utf-8")
    return path


def make_reparse_link(link: Path, target: Path) -> bool:
    """Create a symlink, or on Windows a directory junction when symlinks are
    unavailable. Returns False when the host permits neither."""
    try:
        link.symlink_to(target)
        return True
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt" and target.is_dir():
        try:
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
            return os.path.isjunction(link) or link.is_symlink()
        except (OSError, NotImplementedError, ImportError):
            return False
    return False


def _wrapper_mount_construction(source: str) -> str:
    """Load the real helper and extract only the wrapper's mount call."""
    helper = Path(__file__).resolve().parents[1] / "scripts" / "train-adapter-stage.ps1"
    # Both launch modes use these same real argument statements; the wrapper
    # executes them at different admission points (covered separately below).
    call = re.search(r"\$mounts = New-TrainerCommonMounts[^\n]+\n[^\n]+", source).group()
    probe_start = source.index("if (-not [string]::IsNullOrWhiteSpace($ProbeContextInputs)) {", source.index("# Smoke/probe output directories"))
    probe_end = source.index("\n        }", probe_start) + len("\n        }")
    bounded = helper.with_name("invoke-bounded.ps1")
    return (". '" + bounded.as_posix().replace("'", "''") + "'\n. '"
            + helper.as_posix().replace("'", "''") + "'\n" + call + "\n" + source[probe_start:probe_end] + "\n")


def synthetic_kep_input(item):
    # Mirrors prepare_kep_inputs.RECORD_KEYS without importing its custody tooling.
    return {"protocol": "kep-input/v1", "disposition": "construction_only",
        "source_group": "licensed/kubernetes/enhancements/kep-1", "kep_number": 1, "selection_rank": 0,
        "source_ref": "keps/synthetic/README.md", "source_sha256": "a" * 64,
        "source_git_blob": "b" * 40, "policy_sha256": "c" * 64, "qualified_selection_sha256": "d" * 64,
        "source_captured_at": "2026-10-05T10:00:00Z", "generated_at": "2026-10-05T10:00:01Z",
        "input": {key: value for key, value in item.items() if key != "id"},
        "title_span": {"start": 0, "end": 2}, "objective_span": {"start": 3, "end": 5},
        "context_spans": [{"start": 6, "end": 8}]}


@contextlib.contextmanager
def admitted_probe_template():
    # Production requires exact admitted LF bytes; fixture copies may normalize
    # checkout CRLF, but the runtime verifier never normalizes source.
    data = Path(train_adapter.__file__).with_name("prompt_template.py").read_bytes().replace(b"\r\n", b"\n")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "prompt_template.py"
        path.write_bytes(data)
        with mock.patch.object(train_adapter, "__file__", str(Path(tmp) / "train_adapter.py")):
            yield path


class ContextTokenizer:
    def __init__(self, prompt=2, full=3, truncate=False):
        self.prompt, self.full, self.truncate = prompt, full, truncate
    def apply_chat_template(self, messages, **kwargs):
        length = self.prompt if kwargs["add_generation_prompt"] else self.full
        if self.truncate and "truncation" not in kwargs:
            length -= 1
        return list(range(length))


class VerifiedSourceLoaderTests(unittest.TestCase):
    def test_epoch_loader_retains_exact_pin_and_same_buffer_execution(self):
        data = Path(train_adapter.__file__).with_name('epoch_authority.py').read_bytes().replace(b'\r\n', b'\n')
        self.assertEqual(hashlib.sha256(data).hexdigest(), train_adapter.EPOCH_AUTHORITY_SHA256)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'epoch_authority.py'
            sentinel = Path(tmp) / 'executed'
            replacement = f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\n".encode()
            path.write_bytes(data)
            original_read = Path.read_bytes

            def read_then_replace(source):
                result = original_read(source)
                if source == path:
                    source.write_bytes(replacement)
                return result

            with mock.patch.object(train_adapter, '__file__', str(Path(tmp) / 'train_adapter.py')):
                with mock.patch.object(Path, 'read_bytes', read_then_replace):
                    authority = train_adapter._epoch_authority()
                self.assertTrue(callable(authority.load))
                self.assertTrue(callable(authority.bootstrap))
                self.assertFalse(sentinel.exists())
                with self.assertRaisesRegex(SystemExit, 'epoch authority source differs from trainer pin'):
                    train_adapter._epoch_authority()
                self.assertFalse(sentinel.exists())
                with mock.patch.dict(train_adapter.__dict__, {'_BOOTSTRAP_AUTHORITY': authority}):
                    with mock.patch.object(Path, 'read_bytes', side_effect=AssertionError('bootstrap must retain its buffer')):
                        self.assertIs(train_adapter._epoch_authority(), authority)


class AuthorityFixtureCase(unittest.TestCase):
    def setUp(self):
        import epoch_authority
        self._authority_patch = mock.patch.object(train_adapter, '_epoch_authority', return_value=epoch_authority)
        self._authority_patch.start()
        self.addCleanup(self._authority_patch.stop)
        paths = mock.patch.object(epoch_authority, 'production_paths', side_effect=lambda *args: _TEST_AUTHORITY_PATHS)
        paths.start()
        self.addCleanup(paths.stop)


class TrainAdapterTests(AuthorityFixtureCase):
    def test_smoke_probe_admission_precedes_mount_directory_creation(self):
        pwsh = shutil.which('pwsh')
        if pwsh is None:
            self.skipTest('PowerShell required for wrapper admission statements')
        scripts = Path(__file__).resolve().parents[1] / 'scripts'
        source = (scripts / 'train-adapter.ps1').read_text(encoding='utf-8')
        fragment = source[source.index("$runId = 'm2-'"):source.index('    if (Get-Command lms')]
        # Exercise the actual control flow; substitute only the machine-wide
        # mutex, external preflight and recursive mount scan. All writes are real
        # writes under a fresh temporary directory, never the production marker.
        fragment = fragment.replace("[System.Threading.Mutex]::new($false, 'Global\\homemade-llm-gpu')", '$fakeLock')
        cleanup = source[source.rindex('} finally {'):source.index('\n# [[Cursor', source.rindex('} finally {'))]
        with tempfile.TemporaryDirectory() as tmp:
            command = f". '{(scripts / 'train-adapter-stage.ps1').as_posix()}'\n" + r'''
$ErrorActionPreference = 'Stop'
$PSScriptRoot = '__SCRIPTS__'
$fakeLock = [pscustomobject]@{}
$fakeLock | Add-Member ScriptMethod WaitOne { param($wait) $script:seen.Add('mutex'); return $script:case -cne 'busy' }
$fakeLock | Add-Member ScriptMethod ReleaseMutex { $script:seen.Add('release') }
$fakeLock | Add-Member ScriptMethod Dispose { }
function Invoke-Bounded($Exe, $A, $Seconds) {
    if ($Exe -cne 'pwsh' -or $Seconds -ne 180 -or ($A -join '|') -cne "-NoProfile|-File|$(Join-Path '__SCRIPTS__' 'preflight.ps1')|-HotRoot|$HotRoot") { throw 'unexpected external call' }
    $script:seen.Add('preflight')
    switch ($script:case) { 'preflight' { '__EXIT__1' }; 'timeout' { '__EXIT__timeout' }; default { '__EXIT__0' } }
}
function Get-ProductionLocations { @{ PinFile = '__ROOT__/pin'; CustodyRoot = '__ROOT__/custody' } }
function Assert-TrainerMountIsolation { $script:seen.Add('mounts-created') }
foreach ($Probe in $false, $true) {
    foreach ($script:case in 'busy', 'poison', 'preflight', 'timeout', 'success') {
        $HotRoot = '__ROOT__/' + $Probe + '-' + $script:case
        $Rows = "$HotRoot/accepted.jsonl"; $GpuMarker = "$HotRoot/guard/marker"
        $Epochs = 0; $ExpectedDigests = ''; $TrustedDigestRoot = ''; $ProbeContextInputs = ''
        $markerWritten = $false; $containerGone = $null; $BoundedUnconfirmed = $false; $keepLock = $false
        $script:seen = [Collections.Generic.List[string]]::new()
        if ($script:case -ceq 'poison') {
            New-Item -ItemType Directory -Force (Split-Path $GpuMarker) | Out-Null
            Set-Content -LiteralPath $GpuMarker -Value 'synthetic poison'
        }
        $reason = ''
        try {
__FRAGMENT__
            throw 'SUCCESS-STOP-BEFORE-LMS-OR-DOCKER'
__CLEANUP__
        } catch { $reason = $_.Exception.Message }
        $made = Test-Path -LiteralPath "$HotRoot/hf"
        if ($script:case -ceq 'success') {
            if (-not $made -or $reason -cne 'SUCCESS-STOP-BEFORE-LMS-OR-DOCKER' -or ($script:seen -join ',') -cne 'mutex,preflight,mounts-created,release') { throw "success order: $reason; $script:seen" }
            if (-not (Test-Path -LiteralPath "$HotRoot/models/m2/$runId") -or -not (Test-Path -LiteralPath "$HotRoot/reports/adapter-output/$runId")) { throw 'valid mounts absent' }
        } else {
            if ($made -or (Test-Path -LiteralPath "$HotRoot/models") -or (Test-Path -LiteralPath "$HotRoot/reports")) { throw "refusal created mount directories: $script:case" }
            $expected = switch ($script:case) { 'busy' { 'another GPU job' }; 'poison' { 'GPU is poisoned' }; default { 'preflight did not pass' } }
            if (-not $reason.Contains($expected)) { throw "wrong refusal: $reason" }
        }
        if ($script:case -ceq 'poison') {
            if ((Get-Content -LiteralPath $GpuMarker -Raw).Trim() -cne 'synthetic poison') { throw 'poison overwritten' }
        } elseif (Test-Path -LiteralPath $GpuMarker) { throw 'synthetic marker survived cleanup' }
    }
}
'PASS smoke/probe busy, poison, failed/timed-out preflight, valid mount order'
'''
            command = command.replace('__ROOT__', Path(tmp).as_posix()).replace('__SCRIPTS__', scripts.as_posix())
            command = command.replace('__FRAGMENT__', fragment).replace('__CLEANUP__', cleanup)
            path = Path(tmp) / 'admission.ps1'
            path.write_text(command, encoding='utf-8')
            result = subprocess.run([pwsh, '-NoProfile', '-NonInteractive', '-File', str(path)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PASS smoke/probe', result.stdout)

    @unittest.skipUnless(os.name == 'nt' and shutil.which('pwsh'), 'Windows bounded job required')
    def test_caller_hash_byte_edges_and_authenticated_custody_buffers(self):
        scripts = Path(__file__).resolve().parents[1] / 'scripts'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _build_publication_fixture(root)
            custody = root / 'custody'
            (custody / 'accepted').mkdir(parents=True)
            (custody / 'publication').mkdir()
            for leaf, target in [('accepted.jsonl', 'accepted/batch-0001.accepted.jsonl'),
                                 ('report.json', 'accepted/batch-0001.validation-report.json'),
                                 ('profile.json', 'publication/batch-0001.profile.json')]:
                shutil.copyfile(fixture['rows_dir'] / leaf, custody / target)
            for size in (0, 1, 65536, 65537):
                (root / f'bytes-{size}').write_bytes(bytes((i % 256 for i in range(size))))
            command = f". '{(scripts / 'invoke-bounded.ps1').as_posix()}'\n. '{(scripts / 'train-adapter-stage.ps1').as_posix()}'\n" + r'''
$ErrorActionPreference = 'Stop'
$GpuMarker = '__ROOT__/synthetic-marker'
foreach ($size in 0, 1, 65536, 65537) {
    $path = "__ROOT__/bytes-$size"
    $wanted = Get-SHA256HexOfFile $path
    foreach ($limit in $size, ($size + 1)) {
        if ((Get-BoundedCallerFileSHA256 -Path $path -MaxBytes $limit) -cne $wanted) { throw 'valid byte edge digest changed' }
    }
    if ($size -gt 0) {
        $refused = $false
        try { Get-BoundedCallerFileSHA256 -Path $path -MaxBytes ($size - 1) | Out-Null }
        catch { $refused = $_.Exception.Message.Contains('byte limit') }
        if (-not $refused) { throw 'over-limit byte edge accepted' }
    }
}
$binding = ConvertFrom-StrictJson ([IO.File]::ReadAllText('__BINDING__'))
$args_ = @{ CustodyRoot = '__ROOT__/custody'; Batch = 'batch-0001'; Resolution = $binding['resolution']; Rows = '__ROWS__/accepted.jsonl' }
$records = Read-PublicationRecords @args_
foreach ($pair in @(@('accepted.jsonl','accepted_jsonl_sha256'), @('report.json','validation_report_sha256'))) {
    $path = '__ROWS__/' + $pair[0]
    $original = [IO.File]::ReadAllBytes($path)
    [IO.File]::WriteAllBytes($path, [byte[]]::new(0))
    $refused = $false
    try { Read-PublicationRecords @args_ | Out-Null } catch { $refused = $_.Exception.Message.Contains('does not match the verified custody') }
    if (-not $refused) { throw 'short caller file accepted' }
    [IO.File]::WriteAllBytes($path, $original)
    # Sparse-sized small fixture, not a multi-GB input: rejection must happen
    # at the authenticated byte ceiling, regardless of caller-reported length.
    $file = [IO.File]::OpenWrite($path); try { $file.SetLength(16777216) } finally { $file.Dispose() }
    $refused = $false
    try { Read-PublicationRecords @args_ | Out-Null } catch { $refused = $_.Exception.Message.Contains('byte limit') }
    if (-not $refused) { throw 'oversized caller file accepted' }
    [IO.File]::WriteAllBytes($path, $original)
    $original[0] = $original[0] -bxor 1
    [IO.File]::WriteAllBytes($path, $original)
    $refused = $false
    try { Read-PublicationRecords @args_ | Out-Null } catch { $refused = $_.Exception.Message.Contains('does not match the verified custody') }
    if (-not $refused) { throw 'equal-length digest substitution accepted' }
    $original[0] = $original[0] -bxor 1
    [IO.File]::WriteAllBytes($path, $original)
}
# Returned payloads remain the authenticated custody buffers, regardless of
# a subsequent caller rewrite; staging never reopens those caller paths.
[IO.File]::WriteAllText('__ROWS__/accepted.jsonl', 'caller changed after return')
[IO.File]::WriteAllText('__ROWS__/report.json', 'caller changed after return')
if ((Get-SHA256Hex $records.AcceptedBytes) -cne $binding['resolution']['accepted_jsonl_sha256'] -or
    (Get-SHA256Hex $records.ReportBytes) -cne $binding['resolution']['validation_report_sha256']) { throw 'custody buffer changed' }
if ($BoundedUnconfirmed -or (Test-Path -LiteralPath $GpuMarker)) { throw 'uncertain cleanup' }
'PASS byte edges, oversized accepted/report, equal-length mutations, retained custody buffers'
'''
            command = command.replace('__ROOT__', root.as_posix()).replace('__ROWS__', fixture['rows_dir'].as_posix()).replace('__BINDING__', fixture['binding_path'].as_posix())
            result = subprocess.run([shutil.which('pwsh'), '-NoProfile', '-NonInteractive', '-Command', command], capture_output=True, text=True, timeout=90,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PASS byte edges', result.stdout)

    @unittest.skipUnless(os.name == 'nt' and shutil.which('pwsh'), 'Windows bounded job required')
    def test_caller_hash_timeout_cancellation_reparse_and_incomplete_receipts(self):
        scripts = Path(__file__).resolve().parents[1] / 'scripts'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'payload').write_bytes(b'fixed synthetic payload\x00\xff\r\n')
            linked = make_reparse_link(root / 'alias', root)
            setup = f". '{(scripts / 'invoke-bounded.ps1').as_posix()}'\n. '{(scripts / 'train-adapter-stage.ps1').as_posix()}'\n" + r'''
$ErrorActionPreference = 'Stop'
$GpuMarker = '__ROOT__/synthetic-marker'
$realBounded = ${function:Invoke-Bounded}
'''
            setup = setup.replace('__ROOT__', root.as_posix())
            fault = r'''
function Invoke-Bounded([string]$Exe, [string[]]$A, [int]$Seconds) {
    $reader = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($A[-1]))
    if (-not $reader.Contains('$buffer = [byte[]]::new(65536)') -or $reader.Contains('ReadAllBytes')) { throw 'caller-sized allocation returned' }
    # Delay the actual read site inside the owned child (not a clock check
    # around a blocking filesystem operation in the wrapper process).
    $site = '$count = $stream.Read($buffer, 0, $wanted)'
    $reader = $reader.Replace($site, 'Set-Content -LiteralPath "__PID__" -Value $PID; Start-Sleep -Seconds 30; ' + $site)
    $A[-1] = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($reader))
    & $realBounded $Exe $A $Seconds
}
'''
            cancel = setup + fault.replace('__PID__', (root / 'cancel-pid').as_posix())
            cancel += f"\nGet-BoundedCallerFileSHA256 -Path '{root.as_posix()}/payload' -MaxBytes 100 -TimeoutSeconds 60\n"
            (root / 'cancel.ps1').write_text(cancel, encoding='utf-8')
            command = setup + fault.replace('__PID__', (root / 'timeout-pid').as_posix()) + r'''
$clock = [Diagnostics.Stopwatch]::StartNew()
$refused = $false
try { Get-BoundedCallerFileSHA256 -Path '__ROOT__/payload' -MaxBytes 100 -TimeoutSeconds 2 | Out-Null }
catch { $refused = $_.Exception.Message.Contains('__EXIT__timeout') }
if (-not $refused -or $clock.Elapsed.TotalSeconds -gt 15) { throw 'read deadline failed' }
$childPid = [int](Get-Content -LiteralPath '__ROOT__/timeout-pid')
if (Get-Process -Id $childPid -ErrorAction SilentlyContinue) { throw 'timed-out reader survived' }
Set-Item Function:Invoke-Bounded $realBounded
foreach ($fake in 'empty', 'nonzero', 'extra') {
    $script:receipt = $fake
    function Invoke-Bounded {
        switch ($script:receipt) {
            'empty' { @() }; 'nonzero' { ('a' * 64); '__EXIT__9' }; 'extra' { ('a' * 64); 'extra'; '__EXIT__0' }
        }
    }
    $refused = $false
    try { Get-BoundedCallerFileSHA256 -Path '__ROOT__/payload' -MaxBytes 100 | Out-Null }
    catch { $refused = $_.Exception.Message.Contains('bounded caller file hash failed') }
    if (-not $refused) { throw 'incomplete receipt accepted' }
}
function Invoke-Bounded { throw 'synthetic cleanup unconfirmed' }
$refused = $false
try { Get-BoundedCallerFileSHA256 -Path '__ROOT__/payload' -MaxBytes 100 | Out-Null }
catch { $refused = $_.Exception.Message.Contains('synthetic cleanup unconfirmed') }
if (-not $refused) { throw 'cleanup uncertainty swallowed' }
Set-Item Function:Invoke-Bounded $realBounded
$refused = $false
try { Get-BoundedCallerFileSHA256 -Path '__ROOT__/absent' -MaxBytes 100 | Out-Null }
catch { $refused = $_.Exception.Message.Contains('__EXIT__1') }
if (-not $refused) { throw 'native error accepted' }
__REPARSE__
# PowerShell.Stop exercises the bounded helper's cancellation/finally cleanup.
# Stop only this synthetic reader runspace, never another provider or GPU job.
$ps = [PowerShell]::Create()
try {
    $null = $ps.AddScript([IO.File]::ReadAllText('__ROOT__/cancel.ps1'))
    $async = $ps.BeginInvoke()
    $until = [DateTime]::UtcNow.AddSeconds(15)
    while (-not (Test-Path -LiteralPath '__ROOT__/cancel-pid') -and [DateTime]::UtcNow -lt $until -and -not $async.IsCompleted) { Start-Sleep -Milliseconds 50 }
    if (-not (Test-Path -LiteralPath '__ROOT__/cancel-pid')) { throw 'cancellation reader did not enter read' }
    $clock.Restart()
    $ps.Stop()
    try { $null = $ps.EndInvoke($async) } catch [Management.Automation.PipelineStoppedException] { }
    if ($clock.Elapsed.TotalSeconds -gt 15) { throw 'cancellation cleanup exceeded bound' }
    $childPid = [int](Get-Content -LiteralPath '__ROOT__/cancel-pid')
    if (Get-Process -Id $childPid -ErrorAction SilentlyContinue) { throw 'cancelled reader survived' }
} finally { $ps.Dispose() }
if ($BoundedUnconfirmed -or (Test-Path -LiteralPath $GpuMarker)) { throw 'unconfirmed cleanup; stop' }
'PASS deadline + cancellation killed readers; receipts/native errors/uncertain cleanup refused'
'''
            reparse = r'''
$refused = $false
try { Get-BoundedCallerFileSHA256 -Path '__ROOT__/alias/payload' -MaxBytes 100 | Out-Null }
catch { $refused = $_.Exception.Message.Contains('junction or symbolic link') }
if (-not $refused) { throw 'reparse ancestry accepted' }
'''
            command = command.replace('__REPARSE__', reparse if linked else '').replace('__ROOT__', root.as_posix())
            path = root / 'bounds.ps1'
            path.write_text(command, encoding='utf-8')
            try:
                result = subprocess.run([shutil.which('pwsh'), '-NoProfile', '-NonInteractive', '-File', str(path)], capture_output=True, text=True, timeout=60,
                                        creationflags=subprocess.CREATE_NO_WINDOW)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('PASS deadline + cancellation', result.stdout)
            finally:
                if linked:
                    (root / 'alias').rmdir() if os.name == 'nt' else (root / 'alias').unlink()

    def test_epoch_dev_preflight_exact_boundaries_and_no_truncation(self):
        rows = [row("dev")]
        train_adapter.preflight_epoch_dev(rows, ContextTokenizer(prompt=2, full=2048), 2050)
        for tokenizer, limit, message in [
            (ContextTokenizer(prompt=3), 2050, "max_new_tokens"),
            (ContextTokenizer(full=2049), 4096, "reference"),
            (ContextTokenizer(truncate=True), 4096, "truncates"),
            (ContextTokenizer(), None, "explicit context"),
            (ContextTokenizer(), True, "explicit context"),
        ]:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                train_adapter.preflight_epoch_dev(rows, tokenizer, limit)

    def test_epoch_row_id_recomputed_for_input_and_target_on_both_splits(self):
        for split in ("train", "dev"):
            for field in ("input", "target"):
                with self.subTest(split=split, field=field), tempfile.TemporaryDirectory() as tmp:
                    rows = slice3_train_rows(2) + slice3_dev_rows()[:1]
                    target = next(item for item in rows if item["split"] == split)
                    if field == "input":
                        target["input"]["id"] += "-substituted"
                    else:
                        target["target"]["constraints"][0] = "substituted"
                    fx = _build_publication_fixture(Path(tmp), rows=rows)
                    with self.assertRaisesRegex(ValueError, "noncanonical row_id"):
                        train_adapter.load_epoch_rows(fx["binding_path"], fx["rows_dir"], fx["code_dir"], fx["eval_dir"])
        # Pure algorithm parity, including Unicode and key order.
        value = {"input": {"z": "\u00e9", "a": 1}, "target": {"b": "x"}}
        expected = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()[:16]
        self.assertEqual(train_adapter.canonical_row_id(value["input"], value["target"]), expected)

    @unittest.skipUnless(os.name == "nt" and shutil.which("pwsh"), "Windows JobTree fixture")
    def test_bounded_raw_bytes_exit_and_pipe_holder_cleanup(self):
        helper = Path(__file__).resolve().parents[1] / "scripts" / "invoke-bounded.ps1"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = root / "raw_child.py"
            child.write_text("import sys,os,time,subprocess\n"
                "if sys.argv[1]=='bytes': os.write(1,bytes([0,255,128,13,10,65])); sys.exit(7)\n"
                "if sys.argv[1]=='empty': sys.exit(0)\n"
                "if sys.argv[1]=='hold':\n"
                " p=subprocess.Popen([sys.executable,__file__,'sleep',sys.argv[2]],close_fds=False)\n"
                " open(sys.argv[2],'w').write(str(p.pid))\n"
                "if sys.argv[1]=='sleep': time.sleep(20)\n", encoding="utf-8")
            repo = root / "repo"
            repo.mkdir()
            payload = bytes([0, 255, 128, 13, 10, 65])
            (repo / "payload.bin").write_bytes(payload)
            (repo / "empty.bin").write_bytes(b"")
            for git_args in (["init", "-q"], ["add", "."], ["-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid", "commit", "-qm", "binary fixture"]):
                subprocess.run(["git", *git_args], cwd=repo, check=True, capture_output=True, timeout=10)
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, timeout=10).decode().strip()
            stage_helper = helper.with_name("train-adapter-stage.ps1")
            command = f"""
$ErrorActionPreference='Stop'
. '{helper.as_posix()}'
$GpuMarker='{root.as_posix()}/marker'
. '{stage_helper.as_posix()}'
$b=Get-GitBlobBytes -RepoRoot '{repo.as_posix()}' -Commit '{commit}' -Path payload.bin -ExpectedSha256 '{hashlib.sha256(bytes([0,255,128,13,10,65])).hexdigest()}'
if ($b -isnot [byte[]] -or [Convert]::ToHexString($b) -cne '00FF800D0A41') {{ throw 'real Git binary payload changed' }}
$b=Get-GitBlobBytes -RepoRoot '{repo.as_posix()}' -Commit '{commit}' -Path empty.bin -ExpectedSha256 '{hashlib.sha256(b'').hexdigest()}'
if ($b -isnot [byte[]] -or $b.Length -ne 0) {{ throw 'real Git empty payload changed' }}
$r=Invoke-Bounded '{sys.executable}' @('{child.as_posix()}', 'bytes') 10 -RawBytes
if ($r.ExitCode -ne 7 -or [Convert]::ToHexString($r.Bytes) -cne '00FF800D0A41') {{ throw 'raw byte/nonzero result mismatch' }}
$r=Invoke-Bounded '{sys.executable}' @('{child.as_posix()}', 'empty') 10 -RawBytes
if ($r.ExitCode -ne 0 -or $r.Bytes.Length -ne 0) {{ throw 'empty raw stdout mismatch' }}
$r=@(Invoke-Bounded 'cmd' @('/c','echo text-default') 10)
if (($r -join '|') -cne 'text-default|__EXIT__0') {{ throw 'text default changed' }}
$r=@(Invoke-Bounded '{sys.executable}' @('{child.as_posix()}', 'hold', '{root.as_posix()}/pid') 2 -RawBytes)
if (($r -join '|') -cne '__EXIT__timeout') {{ throw 'pipe holder did not time out' }}
$childPid=[int](Get-Content -LiteralPath '{root.as_posix()}/pid')
if (Get-Process -Id $childPid -ErrorAction SilentlyContinue) {{ throw 'pipe holder survived cleanup' }}
if ($BoundedUnconfirmed -or (Test-Path -LiteralPath $GpuMarker)) {{ throw 'unconfirmed cleanup; stop' }}
Write-Output 'binary, nonzero, empty, default text, pipe-holder cleanup: PASS'
"""
            result = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True, text=True, timeout=40, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("cleanup: PASS", result.stdout)

    @admitted_probe_template()
    def test_context_probe_longest_real_input_full_generation_and_refusals(self):
        from contextlib import nullcontext
        class Tokenizer(ContextTokenizer):
            model_max_length = 8192
            def apply_chat_template(self, messages, **kwargs):
                self.prompt = 4 if "longest" in messages[1]["content"] else 2
                return super().apply_chat_template(messages, **kwargs)
        for kind in ("pass", "constructor_cli", "short", "changed_prefix", "memory", "over_limit", "generate_error"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                short, longest = dict(row()["input"]), dict(row()["input"], objective="longest real input")
                records = [short, longest] if kind != "constructor_cli" else [synthetic_kep_input(short), synthetic_kep_input(longest)]
                raw = ("\n".join(json.dumps(item) for item in records) + "\n").encode()
                inputs, report_path = root / "inputs.jsonl", root / "report.json"
                inputs.write_bytes(raw)
                tokenizer = Tokenizer()
                model = mock.Mock(device="cpu", max_seq_length=2052,
                    config=types.SimpleNamespace(_commit_hash=train_adapter.SMOKE_REVISION, max_position_embeddings=8192))
                output = list(range(4)) + [9] * (2047 if kind == "short" else 2048)
                if kind == "changed_prefix": output[0] = 999
                model.generate.return_value = [output]
                if kind == "generate_error": model.generate.side_effect = RuntimeError("synthetic")
                cuda = mock.Mock(max_memory_reserved=mock.Mock(return_value=(11 if kind == "memory" else 2) * 2**30))
                with mock.patch.object(train_adapter, "digest_verification_before_load", return_value=None), \
                     mock.patch.object(train_adapter, "FastLanguageModel") as flm, \
                     mock.patch.object(train_adapter, "set_seed"), \
                     mock.patch.object(train_adapter.torch, "cuda", cuda, create=True), \
                     mock.patch.object(train_adapter.torch, "tensor", side_effect=lambda ids, **kw: ids, create=True), \
                     mock.patch.object(train_adapter.torch, "ones_like", return_value=[[1] * 4], create=True), \
                     mock.patch.object(train_adapter.torch, "long", 0, create=True), \
                     mock.patch.object(train_adapter.torch, "inference_mode", side_effect=nullcontext, create=True):
                    flm.from_pretrained.return_value = model, tokenizer
                    args = (inputs, train_adapter.SMOKE_BASE, train_adapter.SMOKE_REVISION, report_path)
                    if kind in ("pass", "constructor_cli"):
                        if kind == "constructor_cli":
                            with contextlib.redirect_stdout(io.StringIO()):
                                train_adapter.main(["--rows", "unused/accepted.jsonl", "--report", str(report_path),
                                    "--probe", "--probe-context-inputs", str(inputs), "--probe-context-limit", "2052"])
                            result = json.loads(report_path.read_text())
                        else:
                            result = train_adapter.run_context_probe(*args, context_limit=2052)
                        self.assertTrue(result["context_pass"])
                        self.assertFalse(result["qualified"])
                        self.assertEqual(result["inputs_sha256"], hashlib.sha256(raw).hexdigest())
                        self.assertEqual(result["template_sha256"], train_adapter.ADMITTED_TEMPLATE_SHA256)
                        self.assertEqual(result["record_position"], 2)
                        self.assertEqual(result["prompt_max"], 4)
                        self.assertEqual(result["total_positions"], 2052)
                        self.assertEqual(result["new_tokens"], 2048)
                        self.assertEqual(result["tokenizer_model_max_length"], 8192)
                        self.assertEqual(model.generate.call_args.kwargs["input_ids"], [list(range(4))])
                        self.assertEqual(model.generate.call_args.kwargs["min_new_tokens"], 2048)
                        self.assertEqual(model.generate.call_args.kwargs["max_new_tokens"], 2048)
                        self.assertNotIn("9, 9, 9", report_path.read_text())
                    else:
                        with self.assertRaises((ValueError, RuntimeError)):
                            train_adapter.run_context_probe(*args, context_limit=2051 if kind == "over_limit" else 2052)
                        result = json.loads(report_path.read_text())
                        self.assertFalse(result["context_pass"])
                    if kind == "over_limit":
                        model.generate.assert_not_called()
                    else:
                        model.generate.assert_called_once()
                        flm.for_training.assert_called_once_with(model, use_gradient_checkpointing="unsloth")

    def test_context_input_stream_enforces_exact_and_exceeded_limits(self):
        with tempfile.TemporaryDirectory() as tmp, admitted_probe_template() as template_path:
            template = train_adapter._verified_source_module(template_path.read_bytes(), template_path,
                train_adapter.ADMITTED_TEMPLATE_SHA256, 'synthetic_context_template', 'mismatch')
            path = Path(tmp) / 'inputs.jsonl'
            raw = json.dumps(row()['input'], ensure_ascii=False).encode('utf-8')
            line_limit = train_adapter.CONTEXT_INPUT_MAX_LINE_BYTES
            byte_limit = train_adapter.CONTEXT_INPUT_MAX_BYTES
            record_limit = train_adapter.CONTEXT_INPUT_MAX_RECORDS
            line = raw + b' ' * (line_limit - len(raw) - 2) + b'\r\n'
            # Actual production byte and line boundaries, streamed fixture writes.
            expected_hash = hashlib.sha256()
            with path.open('wb') as handle:
                for _ in range(byte_limit // line_limit):
                    handle.write(line)
                    expected_hash.update(line)
            records, digest = train_adapter._read_context_probe_inputs(path, template)
            self.assertEqual(len(records), byte_limit // line_limit)
            self.assertEqual(digest, expected_hash.hexdigest())
            with path.open('ab') as handle:
                handle.write(b' ')
            with self.assertRaisesRegex(ValueError, 'input byte limit'):
                train_adapter._read_context_probe_inputs(path, template)
            path.write_bytes(line[:-2] + b' \r\n')
            with mock.patch.object(train_adapter, '_load_strict_json') as parse:
                with self.assertRaisesRegex(ValueError, 'record byte limit'):
                    train_adapter._read_context_probe_inputs(path, template)
                parse.assert_not_called()
            # Enforce record count BEFORE parsing/materializing record 1,025.
            bounded_line = raw + b'\n'
            path.write_bytes(bounded_line * record_limit)
            records, digest = train_adapter._read_context_probe_inputs(path, template)
            self.assertEqual(len(records), record_limit)
            self.assertEqual(digest, hashlib.sha256(bounded_line * record_limit).hexdigest())
            with path.open('ab') as handle:
                handle.write(bounded_line)
            with mock.patch.object(train_adapter, '_load_strict_json', wraps=train_adapter._load_strict_json) as parse:
                with self.assertRaisesRegex(ValueError, 'record count limit'):
                    train_adapter._read_context_probe_inputs(path, template)
                self.assertEqual(parse.call_count, record_limit)
            # Hash exact bytes, including blank lines, Unicode and no final LF.
            exact = b'\r\n \n' + json.dumps(dict(row()['input'], title='unicode é'), ensure_ascii=False).encode() + b' \r\n' + raw
            path.write_bytes(exact)
            records, digest = train_adapter._read_context_probe_inputs(path, template)
            self.assertEqual(len(records), 2)
            self.assertEqual(digest, hashlib.sha256(exact).hexdigest())
            # A single oversized physical line cannot request an unbounded read.
            class ReadSpy(io.BytesIO):
                def readline(self, size=-1):
                    self.assert_size(size)
                    return super().readline(size)
                def assert_size(self, size):
                    if not 0 < size <= line_limit + 1:
                        raise AssertionError('unbounded input read')
            with mock.patch.object(Path, 'open', return_value=ReadSpy(b'x' * (line_limit + 2))):
                with self.assertRaisesRegex(ValueError, 'record byte limit'):
                    train_adapter._read_context_probe_inputs(path, template)

    @admitted_probe_template()
    def test_context_probe_persists_all_early_failures_without_input_leaks(self):
        good = json.dumps(row()['input']).encode()
        cases = [('missing', None, FileNotFoundError), ('utf8', b'\xff', UnicodeDecodeError),
                 ('json', b'{SECRET_INPUT_SENTINEL', ValueError), ('blank', b' \r\n', ValueError),
                 ('not-object', b'[]\n', ValueError), ('contract', b'{"private":"SECRET_INPUT_SENTINEL"}', ValueError),
                 ('later-invalid', good + b'\n{SECRET_INPUT_SENTINEL', ValueError),
                 ('bytes', good, ValueError), ('records', good + b'\n' + good, ValueError)]
        for label, raw, expected in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp, \
                 mock.patch.object(train_adapter, 'FastLanguageModel') as flm, \
                 mock.patch.object(train_adapter, 'digest_verification_before_load') as digests, \
                 mock.patch.object(train_adapter, 'set_seed') as seed:
                inputs = Path(tmp) / 'inputs.jsonl'
                report = Path(tmp) / 'reports' / 'probe.json'
                if raw is not None:
                    inputs.write_bytes(raw)
                with contextlib.ExitStack() as stack:
                    if label == 'bytes':
                        stack.enter_context(mock.patch.object(train_adapter, 'CONTEXT_INPUT_MAX_BYTES', 1))
                    if label == 'records':
                        stack.enter_context(mock.patch.object(train_adapter, 'CONTEXT_INPUT_MAX_RECORDS', 1))
                    with self.assertRaises(expected):
                        train_adapter.run_context_probe(inputs, train_adapter.SMOKE_BASE, train_adapter.SMOKE_REVISION,
                                                        report, context_limit=4096)
                result = json.loads(report.read_text())
                self.assertFalse(result['context_pass'])
                self.assertFalse(result['generation_executed'])
                self.assertIsNone(result['inputs_sha256'])
                self.assertIsNone(result['input_count'])
                self.assertEqual(result['template_sha256'], train_adapter.ADMITTED_TEMPLATE_SHA256)
                self.assertIn('error_type', result)
                self.assertNotIn('SECRET_INPUT_SENTINEL', report.read_text())
                self.assertFalse(report.with_name(report.name + '.tmp').exists())
                flm.from_pretrained.assert_not_called()
                digests.assert_not_called()
                seed.assert_not_called()
        original = ValueError('synthetic original error')
        with mock.patch.object(train_adapter, '_read_context_probe_inputs', side_effect=original), \
             mock.patch.object(train_adapter, 'write_report', side_effect=OSError('report destination unavailable')):
            with self.assertRaises(ValueError) as caught:
                train_adapter.run_context_probe(Path('synthetic'), train_adapter.SMOKE_BASE, train_adapter.SMOKE_REVISION,
                                                Path('unwritable'), context_limit=4096)
            self.assertIs(caught.exception, original)
            self.assertIn('failure report could not be persisted: OSError', original.__notes__[0])

    def test_context_probe_authenticates_same_template_buffer_before_render_or_load(self):
        with tempfile.TemporaryDirectory() as tmp, admitted_probe_template() as template_path:
            root = Path(tmp)
            inputs, report = root / 'inputs.jsonl', root / 'report.json'
            inputs.write_text(json.dumps(row()['input']))
            admitted = template_path.read_bytes()
            sentinel = root / 'executed'
            bad = ("from pathlib import Path\nPath(" + repr(str(sentinel)) + ").write_text('unexpected')\n").encode()
            template_path.write_bytes(bad)
            with mock.patch.object(train_adapter, 'FastLanguageModel') as flm, \
                 mock.patch.object(train_adapter, 'context_probe_input') as contract:
                with self.assertRaisesRegex(SystemExit, 'template digest mismatch'):
                    train_adapter.run_context_probe(inputs, train_adapter.SMOKE_BASE, train_adapter.SMOKE_REVISION,
                                                    report, context_limit=4096)
                flm.from_pretrained.assert_not_called()
                contract.assert_not_called()
            self.assertFalse(sentinel.exists())
            failed = json.loads(report.read_text())
            self.assertFalse(failed['context_pass'])
            self.assertIsNone(failed['template_sha256'])
            self.assertIsNone(failed['inputs_sha256'])
            # Rewrite source after the read and poison the cached module. Neither
            # later path bytes nor its callable may supply the probe's renderer.
            class SwapAfterRead(io.BytesIO):
                def read(self, size=-1):
                    result = super().read(size)
                    template_path.write_bytes(bad)
                    return result
            real_open = Path.open
            def guarded_open(path, *args, **kwargs):
                if path == template_path and args == ('rb',):
                    return SwapAfterRead(admitted)
                return real_open(path, *args, **kwargs)
            cached = types.ModuleType('prompt_template')
            cached.render_messages = mock.Mock(side_effect=AssertionError('cached renderer used'))
            before_model = RuntimeError('synthetic stop after admission')
            with mock.patch.object(Path, 'open', guarded_open), mock.patch.dict(sys.modules, {'prompt_template': cached}), \
                 mock.patch.object(train_adapter, 'digest_verification_before_load', side_effect=before_model), \
                 mock.patch.object(train_adapter, 'FastLanguageModel') as flm:
                with self.assertRaises(RuntimeError) as caught:
                    train_adapter.run_context_probe(inputs, train_adapter.SMOKE_BASE, train_adapter.SMOKE_REVISION,
                                                    report, context_limit=4096)
                self.assertIs(caught.exception, before_model)
                cached.render_messages.assert_not_called()
                flm.from_pretrained.assert_not_called()
            result = json.loads(report.read_text())
            self.assertEqual(result['template_sha256'], hashlib.sha256(admitted).hexdigest())
            self.assertEqual(result['input_count'], 1)
            self.assertEqual(result['inputs_sha256'], hashlib.sha256(inputs.read_bytes()).hexdigest())
            self.assertFalse(sentinel.exists())

    @admitted_probe_template()
    def test_context_probe_rejects_malformed_envelopes_before_model_load(self):
        import copy
        good = synthetic_kep_input(row()["input"])
        cases = []
        for field, value in (("protocol", "kep-extraction/v1"), ("disposition", "accepted"), ("input", [])):
            bad = copy.deepcopy(good)
            bad[field] = value
            cases.append(bad)
        for where in ("envelope", "input", "bare"):
            bad = copy.deepcopy(row()["input"] if where == "bare" else good)
            (bad["input"] if where == "input" else bad)["target"] = row()["target"]
            cases.append(bad)
        bad = copy.deepcopy(good)
        del bad["source_git_blob"]
        cases.append(bad)
        bad = copy.deepcopy(good)
        bad["input"]["context"][0]["source_sha256"] = "invalid"
        cases.append(bad)
        bad = copy.deepcopy(good)
        bad["input"]["objective"] = 17
        cases.append(bad)
        for index, bad in enumerate(cases):
            with self.subTest(case=index), tempfile.TemporaryDirectory() as tmp, \
                 mock.patch.object(train_adapter, "FastLanguageModel") as flm, \
                 mock.patch.object(train_adapter, "digest_verification_before_load") as digests, \
                 mock.patch.object(train_adapter, "set_seed") as seed:
                inputs = Path(tmp) / "inputs.jsonl"
                # A valid earlier record must not cause a load before later validation.
                inputs.write_text(json.dumps(good) + "\n" + json.dumps(bad) + "\n")
                with self.assertRaises(ValueError):
                    train_adapter.main(["--rows", "unused/accepted.jsonl", "--report", str(Path(tmp) / "report.json"),
                        "--probe", "--probe-context-inputs", str(inputs), "--probe-context-limit", "4096"])
                flm.from_pretrained.assert_not_called()
                digests.assert_not_called()
                seed.assert_not_called()

    def test_epoch_bad_dev_context_refuses_before_first_trainer_step(self):
        for tokenizer in (ContextTokenizer(prompt=2049), ContextTokenizer(full=2049), ContextTokenizer(truncate=True)):
            with self.subTest(tokenizer=tokenizer), tempfile.TemporaryDirectory() as tmp:
                trainer_type = mock.Mock()
                with self.assertRaisesRegex(ValueError, "max_new_tokens|reference|truncates"):
                    self._maintenance_epoch_run(Path(tmp), trainer_type, epochs=1, context_tokenizer=tokenizer)
                trainer_type.assert_not_called()
                report = json.loads((Path(tmp) / "reports" / "maintenance.json").read_text())
                self.assertEqual(report["state"], "failed")

    def test_context_probe_cli_dispatches_without_loading_training_rows(self):
        with mock.patch.object(train_adapter, "run_context_probe", return_value={"context_pass": True}) as probe, \
             mock.patch.object(train_adapter, "load_training_rows") as rows, \
             contextlib.redirect_stdout(io.StringIO()):
            train_adapter.main(["--rows", "unused/accepted.jsonl", "--report", "out.json", "--probe",
                "--probe-context-inputs", "inputs.jsonl", "--probe-context-limit", "15009"])
            rows.assert_not_called()
            probe.assert_called_once()
            self.assertEqual(probe.call_args.kwargs["context_limit"], 15009)
        for extra in (["--probe-context-inputs", "inputs.jsonl"], ["--probe-context-limit", "15009"]):
            with self.subTest(extra=extra), mock.patch.object(train_adapter, "run_context_probe") as probe:
                with self.assertRaises(SystemExit):
                    train_adapter.main(["--rows", "unused/accepted.jsonl", "--report", "out.json", "--probe", *extra])
                probe.assert_not_called()

    def test_response_mask_ignores_the_prompt_and_covers_the_answer(self):
        labels = train_adapter.labels_for_token_ids([4, 5, 6, 7], [4, 5, 6, 7, 8, 9, 10])
        self.assertEqual(labels, [-100, -100, -100, -100, 8, 9, 10])
        self.assertAlmostEqual(train_adapter.require_response_only(labels, 4), 3 / 7)

    def test_empty_response_and_prompt_labels_fail_closed(self):
        with self.assertRaises(SystemExit):
            train_adapter.labels_for_token_ids([4, 5], [4, 5])
        with self.assertRaises(SystemExit):
            train_adapter.labels_for_token_ids([4, 5], [9, 5, 6])
        with self.assertRaises(SystemExit):
            train_adapter.require_response_only([-100, 1, 1], 2)

    def test_encode_rows_labels_are_the_response_ids(self):
        def tokenize(messages, add_generation_prompt):
            if messages and messages[-1]["role"] == "assistant" and not add_generation_prompt:
                return tokenize(messages[:-1], True) + [60, 61, 62]
            if add_generation_prompt:
                return tokenize(messages, False) + [50, 51]
            return [10 + len(messages)]

        encoded = train_adapter.encode_rows([row()], tokenize)
        self.assertEqual(encoded[0]["labels"][-3:], [60, 61, 62])
        self.assertTrue(all(label == -100 for label in encoded[0]["labels"][:-3]))
        self.assertEqual(len(encoded[0]["attention_mask"]), len(encoded[0]["input_ids"]))
        self.assertEqual(encoded[0]["input_ids"][-3:], [60, 61, 62])

    def test_only_the_validator_accepted_file_reaches_the_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "rows.jsonl"
            raw.write_text(json.dumps(row()) + "\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                train_adapter.load_training_rows(raw)
            quarantined = row("quarantine")
            write_accepted(root, [quarantined])
            with self.assertRaises(SystemExit):
                train_adapter.load_training_rows(root / "accepted.jsonl")
            dev = row("dev")
            dev["input"] = dict(dev["input"], id="dev-1")
            accepted = write_accepted(root, [row(), dev])
            loaded = train_adapter.load_training_rows(accepted)
            self.assertEqual([item["input"]["id"] for item in loaded], ["row-1"])

    def test_a_train_row_renders_through_the_shared_template(self):
        messages = train_adapter.messages_for(row())
        self.assertEqual([item["role"] for item in messages], ["system", "user", "assistant"])
        self.assertIn("context", messages[1]["content"])
        self.assertIn("acceptance_checks", messages[2]["content"])

    def test_a_validator_row_without_an_input_id_still_renders(self):
        item = row()
        del item["input"]["id"]
        item["input"]["project"] = "director"
        item["row_id"] = "cafebabe" * 2
        messages = train_adapter.messages_for(item)
        self.assertNotIn(item["row_id"], messages[1]["content"])
        self.assertNotIn("director", messages[1]["content"])
        self.assertIn("Add a bounded retry.", messages[1]["content"])

    def test_real_validator_accepted_file_trains_only_the_train_row(self):
        repo = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hl = root / "hl"
            for rel in (
                "eval/build_inputs.py",
                "eval/test_build_inputs.py",
                "eval/validate_rows.py",
                "eval/test_validate_rows.py",
                "train/prompt_template.py",
                "schemas/director-data-v1-row.schema.json",
            ):
                dest = hl / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(repo / "projects" / "homemade-llm" / rel, dest)
            out = root / "validated"
            script = root / "run_validator.py"
            script.write_text(
                "import json, sys\n"
                "from pathlib import Path\n"
                "root = Path(sys.argv[1])\n"
                "sys.path.insert(0, str(root / 'hl' / 'eval'))\n"
                "import test_validate_rows as fixture\n"
                "import validate_rows\n"
                "set_a, set_b = fixture.write_sets(root / 'sets')\n"
                "train = fixture.make_row(set_a, set_b)\n"
                "dev = fixture.make_row(set_a, set_b, source_group='seed-002', split='dev',\n"
                "    input={'objective': 'Add a retry budget to the n8n intake webhook.'},\n"
                "    target={'non_goals': ['no change to the Slack copy']})\n"
                "(root / 'rows.jsonl').write_bytes(''.join(json.dumps(item) + '\\n' for item in (train, dev)).encode('utf-8'))\n"
                "code = validate_rows.validate(['--rows', str(root / 'rows.jsonl'), '--set-a', str(set_a),\n"
                "    '--set-b', str(set_b), '--out', str(root / 'validated')])\n"
                "raise SystemExit(code)\n",
                encoding="utf-8",
                newline="\n",
            )
            completed = subprocess.run([sys.executable, str(script), str(root)], cwd=repo, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            accepted = [json.loads(line) for line in (out / "accepted.jsonl").read_text(encoding="utf-8").splitlines()]
            report = json.loads((out / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["n_accepted"], 2)
            self.assertEqual(report["n_rejected"], 0)
            self.assertEqual(report["n_quarantined"], 0)
            self.assertEqual(report["rows_per_split"], {"train": 1, "dev": 1})
            self.assertEqual({item["source_group"] for item in accepted}, {"seed-001", "seed-002"})
            loaded = train_adapter.load_training_rows(out / "accepted.jsonl")
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["split"], "train")
            self.assertEqual(loaded[0]["row_id"], next(item["row_id"] for item in accepted if item["split"] == "train"))
            self.assertNotIn("id", loaded[0]["input"])
            messages = train_adapter.messages_for(loaded[0])
            self.assertIn("Webhook secret rotation", messages[1]["content"])
            self.assertNotIn("n8n intake webhook", messages[1]["content"])
            self.assertIn("acceptance_checks", messages[2]["content"])

    def test_the_trainer_keeps_the_attention_mask_column(self):
        args = train_adapter.sft_arguments("out", 20)
        self.assertIs(args["remove_unused_columns"], False)
        self.assertEqual(args["dataset_kwargs"], {"skip_prepare_dataset": True})
        self.assertIs(args["packing"], False)

    def test_sft_arguments_matches_probe_gradient_accumulation(self):
        args = train_adapter.sft_arguments("out", 20)
        self.assertEqual(args["gradient_accumulation_steps"], train_adapter.PROBE_GRAD_ACCUM)
        self.assertEqual(train_adapter.PROBE_GRAD_ACCUM, 8)
        self.assertEqual(train_adapter.TRAIN_GRAD_ACCUM, 8)

    def test_smoke_arguments_still_use_steps_without_epoch_saving(self):
        args = train_adapter.sft_arguments("out", 20)
        self.assertEqual(args["max_steps"], 20)
        self.assertEqual(args["save_strategy"], "no")
        self.assertNotIn("num_train_epochs", args)
        self.assertEqual(args["learning_rate"], 2e-4)

    def test_epoch_arguments_use_epochs_per_epoch_saving_and_unset_max_steps(self):
        args = train_adapter.sft_arguments("out", None, epochs=4)
        self.assertEqual(args["num_train_epochs"], 4)
        self.assertEqual(args["max_steps"], -1)
        self.assertEqual(args["save_strategy"], "epoch")
        self.assertEqual(args["learning_rate"], train_adapter.EPOCH_LEARNING_RATE)
        self.assertEqual(args["learning_rate"], 1e-4)
        self.assertEqual(args["lr_scheduler_type"], "cosine")
        self.assertEqual(args["gradient_accumulation_steps"], 8)
        self.assertNotIn("save_total_limit", args)

    def test_pinned_revision_must_match_the_loaded_commit(self):
        pin = "a" * 40
        self.assertEqual(train_adapter.resolve_commit(pin, "org/model", pin), pin)
        with self.assertRaises(SystemExit):
            train_adapter.resolve_commit("b" * 40, "org/model", pin)

    def test_snapshot_resolution_stays_on_hf_root_after_cache_redirect(self):
        # Importing unsloth moves HF_HOME/HF_HUB_CACHE to /tmp when the mounted cache is read-only; resolution must not follow it.
        calls, pin = [], "a" * 40
        hub = types.ModuleType("huggingface_hub")
        hub.snapshot_download = lambda **kw: calls.append(kw) or str(Path(kw["cache_dir"], "models--org--m", "snapshots", pin))
        with mock.patch.dict(sys.modules, {"huggingface_hub": hub}), \
                mock.patch.object(train_adapter, "HF_ROOT", Path("/workspace/hf")), \
                mock.patch.dict(os.environ, {"HF_HOME": "/tmp/huggingface_0", "HF_HUB_CACHE": "/tmp/huggingface_0/hub"}):
            snapshot = train_adapter.local_snapshot_dir("org/m", pin)
        self.assertEqual(calls, [{"repo_id": "org/m", "revision": pin, "cache_dir": Path("/workspace/hf") / "hub",
                                  "local_files_only": True}])
        self.assertEqual(snapshot.name, pin)

    def test_missing_cached_snapshot_refuses_before_any_load(self):
        hub = types.ModuleType("huggingface_hub")

        def missing(**_kw):
            raise FileNotFoundError("snapshot not in the local cache")
        hub.snapshot_download = missing
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(sys.modules, {"huggingface_hub": hub}), \
                mock.patch.object(train_adapter, "FastLanguageModel") as flm:
            with self.assertRaises(FileNotFoundError):
                train_adapter.run_probe([row()], train_adapter.SMOKE_BASE, train_adapter.SMOKE_REVISION, Path(tmp, "probe.json"),
                                        probe_rows_total=None, probe_seq_len=train_adapter.SEQ)
            flm.from_pretrained.assert_not_called()

    def test_commit_falls_back_to_the_loaded_snapshot_and_pins_still_match(self):
        pin = "a" * 40
        self.assertEqual(train_adapter.resolve_commit(None, "org/m", pin, Path("hub", "snapshots", pin)), pin)
        self.assertEqual(train_adapter.resolve_commit(None, "org/m", "main", Path("hub", "snapshots", "c" * 40)), "c" * 40)
        with self.assertRaises(SystemExit):
            train_adapter.resolve_commit(None, "org/m", pin, Path("hub", "snapshots", "b" * 40))
        with self.assertRaises(SystemExit):
            train_adapter.resolve_commit("b" * 40, "org/m", pin, Path("hub", "snapshots", pin))

    def test_digest_verification_hands_back_the_snapshot_it_verified(self):
        snapshot = Path("hub", "models--org--m", "snapshots", "a" * 40)
        resolved = []
        with mock.patch.object(train_adapter, "local_snapshot_dir", return_value=snapshot) as resolver, \
                mock.patch.object(train_adapter, "load_expected_digests", return_value=[]), \
                mock.patch.object(train_adapter, "verify_expected_digests", return_value={"files": 0}) as verify:
            train_adapter.digest_verification_before_load("org/m", "a" * 40, Path("digests.json"), resolved=resolved)
        resolver.assert_called_once_with("org/m", "a" * 40)
        self.assertIs(verify.call_args.args[0], snapshot)
        self.assertEqual(resolved, [snapshot])

    def test_every_mode_loads_the_verified_snapshot_by_path_without_revision(self):
        snapshot = Path("hub", "models--org--m", "snapshots", train_adapter.SMOKE_REVISION)

        def verified(*_a, resolved=None, **_k):  # admission and verification pass, handing back the verified snapshot
            resolved.append(snapshot)
            return {"files": 1, "reads": 2, "bytes": 1, "weight_files": 1, "weight_map_checked": False}
        for mode in ("train", "context", "probe"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                flm = mock.MagicMock()
                flm.from_pretrained = mock.MagicMock(side_effect=SystemExit("stop after load"))
                inputs = Path(tmp, "inputs.jsonl")
                short, longest = dict(row()["input"]), dict(row()["input"], objective="longest real input")
                inputs.write_text("\n".join(json.dumps(item) for item in (short, longest)) + "\n", encoding="utf-8")
                with mock.patch.object(train_adapter, "local_snapshot_dir") as resolver, \
                        mock.patch.object(train_adapter, "digest_verification_before_load", side_effect=verified), \
                        mock.patch.object(train_adapter, "FastLanguageModel", flm), \
                        mock.patch.object(train_adapter, "set_seed"), \
                        mock.patch.object(train_adapter.torch, "cuda", mock.MagicMock(), create=True):
                    try:
                        if mode == "train":
                            train_adapter.train([row()], [], train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION, 20,
                                                Path(tmp, "out.json"), winner=True, expected_digests_path=Path(tmp, "d.json"))
                        elif mode == "context":
                            with admitted_probe_template():
                                train_adapter.run_context_probe(inputs, train_adapter.SMOKE_BASE, train_adapter.SMOKE_REVISION,
                                                                Path(tmp, "ctx.json"), context_limit=2052)
                        else:
                            train_adapter.run_probe([row()], train_adapter.SMOKE_BASE, train_adapter.SMOKE_REVISION,
                                                    Path(tmp, "probe.json"), probe_rows_total=None, probe_seq_len=train_adapter.SEQ)
                    except BaseException:  # the stub stops right after the load call; reports may wrap it
                        pass
                resolver.assert_not_called()  # no second lookup after verification
                self.assertTrue(flm.from_pretrained.called, f"{mode} did not reach the load")
                args, kwargs = flm.from_pretrained.call_args
                self.assertEqual(args, (str(snapshot),))
                self.assertNotIn("revision", kwargs)
                self.assertTrue(kwargs["local_files_only"])
                self.assertTrue(kwargs["use_exact_model_name"])

    def test_unverified_mode_resolves_once_and_loads_that_path(self):
        snapshot = Path("hub", "models--org--m", "snapshots", train_adapter.SMOKE_REVISION)
        flm = mock.MagicMock()
        flm.from_pretrained = mock.MagicMock(side_effect=SystemExit("stop after load"))
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(train_adapter, "local_snapshot_dir", return_value=snapshot) as resolver, \
                mock.patch.object(train_adapter, "digest_verification_before_load", return_value=None), \
                mock.patch.object(train_adapter, "FastLanguageModel", flm), mock.patch.object(train_adapter, "set_seed"):
            with self.assertRaises(SystemExit):
                train_adapter.run_probe([row()], train_adapter.SMOKE_BASE, train_adapter.SMOKE_REVISION, Path(tmp, "probe.json"),
                                        probe_rows_total=None, probe_seq_len=train_adapter.SEQ)
        resolver.assert_called_once_with(train_adapter.SMOKE_BASE, train_adapter.SMOKE_REVISION)
        self.assertEqual(flm.from_pretrained.call_args.args, (str(snapshot),))

    def test_winner_probe_stops_above_the_card_and_the_eleven_gib_ceiling(self):
        train_adapter.require_vram(10.9, winner=True)
        with self.assertRaises(SystemExit):
            train_adapter.require_vram(11.1, winner=True)
        with self.assertRaises(SystemExit):
            train_adapter.require_vram(12.1, winner=False)

    def test_report_records_the_probe_fields_and_keeps_the_previous_file(self):
        report = train_adapter.build_report(
            run="probe", base=train_adapter.WINNER_BASE, revision=train_adapter.WINNER_REVISION,
            rows=2, steps=20, mask_coverage=0.25, peak_reserved_gib=9.5, loss=1.2,
            wall_seconds=3, winner=True, resolved_commit=train_adapter.SMOKE_REVISION,
        )
        self.assertEqual(report["gguf_sha256"], None)
        self.assertEqual(report["thinking"], "disabled")
        self.assertEqual(report["base_resolved_commit"], train_adapter.SMOKE_REVISION)
        self.assertEqual(report["peak_reserved_vram_gib"], 9.5)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "probe.json")
            train_adapter.write_report(path, {"stage": "first"})
            with mock.patch.object(Path, "write_text", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    train_adapter.write_report(path, {"stage": "second"})
            self.assertEqual(json.loads(path.read_text()), {"stage": "first"})

    def test_check_only_validates_rows_without_a_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_accepted(Path(tmp), [row()])
            train_adapter.main(["--rows", str(path), "--report", str(Path(tmp, "missing.json")), "--check-only"])
            self.assertFalse(Path(tmp, "missing.json").exists())

    def test_training_run_rejects_conflicting_or_missing_mode_flags_before_model_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_accepted(Path(tmp), [row()])
            cases = [
                (["--steps", "20", "--epochs", "3"], "exactly one of --steps or --epochs, not both"),
                (["--epochs", "0"], "--epochs must be 1 to 6"),
                (["--epochs", "7"], "--epochs must be 1 to 6"),
                (["--epochs", "x"], None),
                ([], "needs exactly one of --steps or --epochs"),
                (["--epochs", "3", "--probe"], "--epochs cannot be combined with --probe"),
            ]
            for extra, message in cases:
                with self.subTest(extra=extra):
                    with mock.patch.object(train_adapter, "FastLanguageModel") as model:
                        with self.assertRaises(SystemExit) as raised:
                            train_adapter.main([
                                "--rows", str(path), "--report", str(Path(tmp, "out.json")),
                                "--base", train_adapter.SMOKE_BASE, "--revision", train_adapter.SMOKE_REVISION,
                                *extra,
                            ])
                        if message is not None:
                            self.assertIn(message, str(raised.exception))
                    model.from_pretrained.assert_not_called()

    def test_winner_flag_rejects_a_different_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_accepted(Path(tmp), [row()])
            with self.assertRaises(SystemExit):
                train_adapter.main([
                    "--rows", str(path), "--report", str(Path(tmp, "out.json")),
                    "--winner", "--probe", "--base", train_adapter.SMOKE_BASE, "--revision", train_adapter.SMOKE_REVISION,
                ])

    def test_winner_cli_requires_probe_before_rows_or_training(self):
        for mode in (["--steps", "20"], ["--epochs", "1"], ["--check-only"]):
            with self.subTest(mode=mode), mock.patch.object(train_adapter, "load_training_rows") as rows, \
                 mock.patch.object(train_adapter, "train") as training:
                with self.assertRaisesRegex(SystemExit, "--winner is only valid together with --probe"):
                    train_adapter.main(["--rows", "synthetic/accepted.jsonl", "--report", "synthetic/report.json",
                                        "--base", train_adapter.WINNER_BASE, "--revision", train_adapter.WINNER_REVISION,
                                        "--winner", *mode])
                rows.assert_not_called()
                training.assert_not_called()

    def test_epoch_cli_requires_explicit_production_pin(self):
        cases = (
            [],
            ["--base", train_adapter.WINNER_BASE],
            ["--revision", train_adapter.WINNER_REVISION],
            ["--base", train_adapter.SMOKE_BASE, "--revision", train_adapter.SMOKE_REVISION],
            ["--base", train_adapter.WINNER_BASE, "--revision", "a" * 40],
            ["--base", train_adapter.FALLBACK_BASE, "--revision", train_adapter.WINNER_REVISION],
            ["--base", train_adapter.WINNER_BASE, "--revision", train_adapter.FALLBACK_REVISION],
            ["--base", "Other/Approved-Model", "--revision", train_adapter.FALLBACK_REVISION],
        )
        for pin in cases:
            with self.subTest(pin=pin), \
                 mock.patch.dict(os.environ, {"ADAPTER_BASE": train_adapter.WINNER_BASE,
                                              "ADAPTER_REVISION": train_adapter.WINNER_REVISION}), \
                 mock.patch.object(train_adapter, "load_training_rows") as rows:
                with self.assertRaisesRegex(SystemExit, "epoch mode requires explicit --base"):
                    train_adapter.main(["--rows", "synthetic/accepted.jsonl", "--report", "synthetic/report.json",
                                        "--epochs", "1", *pin])
                rows.assert_not_called()
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(train_adapter, "load_training_rows", return_value=slice3_train_rows()), \
             mock.patch.object(train_adapter, "load_dev_rows", return_value=slice3_dev_rows()), \
             mock.patch.object(train_adapter, "train", return_value={}) as training, \
             contextlib.redirect_stdout(io.StringIO()):
            fx = _build_publication_fixture(Path(tmp), rows=slice3_train_rows() + slice3_dev_rows())
            for base, revision in ((train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION),
                                   (train_adapter.FALLBACK_BASE, train_adapter.FALLBACK_REVISION)):
                train_adapter.main(["--rows", str(fx["rows_dir"] / "accepted.jsonl"), "--report", str(Path(tmp) / "out.json"),
                                    "--epochs", "1", "--base", base, "--revision", revision,
                                    "--epoch-context-limit", "4096", "--publication-binding", str(fx["binding_path"])])
                self.assertEqual(training.call_args.args[2:4], (base, revision))
            self.assertEqual(training.call_args.kwargs["epochs"], 1)
            self.assertEqual(training.call_args.kwargs["publication_binding_path"], fx["binding_path"])
            self.assertEqual(training.call_args.kwargs["publication_rows_dir"], fx["rows_dir"])

    def test_wrapper_carries_the_same_winner_pin(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "train-adapter.ps1"
        text = script.read_text(encoding="utf-8")
        self.assertIn(train_adapter.WINNER_REVISION, text)
        self.assertIn(train_adapter.WINNER_BASE, text)
        self.assertIn("Global\\homemade-llm-gpu", text)
        self.assertIn("--probe", text)
        self.assertNotIn("-Probe requires -Winner", text)
        self.assertIn("--expected-digests", text)
        self.assertIn("ExpectedDigests", text)
        self.assertIn("m2-expected-qwen3-14b-v2.json", text)
        self.assertIn("HF_HUB_OFFLINE", text)

    def _context_wrapper_run(self, tmp: Path, code_dir: Path, override: str = ""):
        """Run the REAL wrapper mount/staging block and argument construction for a context probe; return (completed, args)."""
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            self.skipTest("PowerShell is required to evaluate the context-probe renderer staging")
        script = Path(__file__).resolve().parents[1] / "scripts" / "train-adapter.ps1"
        source = script.read_text(encoding="utf-8")
        construction = source[source.index("    $dockerArgs = "):source.index("    function Get-AdapterState")]
        setup = _wrapper_mount_construction(source)
        marker = "$mounts = New-TrainerCommonMounts"
        setup = setup.replace(marker, override + "\n" + marker, 1)
        q = lambda p: Path(p).as_posix().replace("'", "''")
        command = (
            "$ErrorActionPreference = 'Stop'\n"
            f"$name = 'dummy-container'; $code = '{q(code_dir)}'; $evalCode = 'dummy-eval'; $Image = 'dummy-image'\n"
            "$Base = 'dummy-base'; $Revision = 'dummy-revision'; $Steps = 20; $Epochs = 0; $runId = 'm2-0123abcd'\n"
            "$Winner = $false; $Probe = $true; $ProbeSeqLen = 2048; $ProbeRowsTotal = 96; $PSBoundParameters = @{}\n"
            "$ProbeContextInputs = 'C:/synthetic/inputs.jsonl'; $ProbeContextLimit = 15009\n"
            f"$repoRoot = '{q(Path(__file__).resolve().parents[3])}'\n"
            f"$HotRoot = '{q(tmp)}'\n$Rows = '{q(tmp)}/accepted.jsonl'\n$ExpectedDigests = ''\n$TrustedDigestRoot = ''\n"
            + setup + construction + "\nConvertTo-Json -InputObject $dockerArgs -Compress")
        completed = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", command],
                                   capture_output=True, text=True, timeout=60)
        args = json.loads(completed.stdout) if completed.returncode == 0 else None
        return completed, args

    def test_context_probe_wrapper_binds_the_admitted_lf_renderer_over_a_crlf_checkout(self):
        # The Windows working tree is CRLF; the probe must still see the exact admitted LF renderer (#472 P1 4193399786).
        admitted = "6ee4833fbce2b0f38f37b5cd51f5424cd154a78cbfc9d9ae3643359a709908ea"
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            code_dir = tmp / "checkout-train"
            code_dir.mkdir()
            lf = Path(train_adapter.__file__).with_name("prompt_template.py").read_bytes().replace(b"\r\n", b"\n")
            (code_dir / "prompt_template.py").write_bytes(lf.replace(b"\n", b"\r\n"))
            self.assertNotEqual(hashlib.sha256((code_dir / "prompt_template.py").read_bytes()).hexdigest(), admitted)
            completed, args = self._context_wrapper_run(tmp, code_dir)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            image = args.index("dummy-image")
            options = args[:image]
            renderer_mounts = [options[i + 1] for i in range(len(options) - 1)
                               if options[i] == "-v" and options[i + 1].endswith(":/code/prompt_template.py:ro")]
            self.assertEqual(len(renderer_mounts), 1, options)
            source = Path(renderer_mounts[0][:-len(":/code/prompt_template.py:ro")])
            self.assertEqual(source, tmp / "runs" / "stage" / "m2-0123abcd" / "Train" / "prompt_template.py")
            staged = source.read_bytes()
            self.assertNotIn(b"\r\n", staged)
            self.assertEqual(hashlib.sha256(staged).hexdigest(), admitted)
            self.assertIn(f"{code_dir.as_posix()}:/code:ro", options)
            self.assertLess(options.index(f"{code_dir.as_posix()}:/code:ro"), options.index(renderer_mounts[0]))
            self.assertIn("C:/synthetic/inputs.jsonl:/workspace/context-inputs.jsonl:ro", options)

    def test_context_probe_wrapper_refuses_tampered_renderer_bytes_before_any_container(self):
        override = ("function Get-GitBlobBytes { param($RepoRoot, $Commit, $Path, $ExpectedOid, $ExpectedSha256) "
                    "return [Text.Encoding]::UTF8.GetBytes('tampered renderer') }")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            code_dir = tmp / "checkout-train"
            code_dir.mkdir()
            completed, args = self._context_wrapper_run(tmp, code_dir, override)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIsNone(args)
            self.assertIn("do not match the expected SHA-256", completed.stderr)
            self.assertFalse((tmp / "runs" / "stage" / "m2-0123abcd" / "Train" / "prompt_template.py").exists())

    def test_context_probe_renderer_staging_follows_gpu_admission_and_preflight(self):
        source = (Path(__file__).resolve().parents[1] / "scripts" / "train-adapter.ps1").read_text(encoding="utf-8")
        preflight = source.index("preflight did not pass ($($pf[-1]")
        stage = source.index("$rendererStage = New-StageTree -HotRoot $HotRoot -RunId $runId")
        mutex = source.index("$gpuLock.WaitOne(0)")
        self.assertLess(mutex, preflight)
        self.assertLess(preflight, stage)
        self.assertLess(stage, source.index("    $dockerArgs = "))

    def test_wrapper_serialized_docker_arguments_keep_offline_flags_before_image(self):
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            self.skipTest("PowerShell is required to evaluate Docker argument construction")
        script = Path(__file__).resolve().parents[1] / "scripts" / "train-adapter.ps1"
        source = script.read_text(encoding="utf-8")
        # Evaluate only the argument-building block, never the wrapper or its setup.
        start = source.index("    $dockerArgs = ")
        end = source.index("    function Get-AdapterState", start)
        construction = source[start:end]
        for digests, context_file in (("", False), ("dummy-digests.json", True)):
            with self.subTest(expected_digests=bool(digests)), tempfile.TemporaryDirectory() as tmp:
                hot_root = Path(tmp).as_posix().replace("'", "''")
                command = """
$ErrorActionPreference = 'Stop'
$name = 'dummy-container'; $code = 'dummy-code'; $evalCode = 'dummy-eval'; $Image = 'dummy-image'
$Base = 'dummy-base'; $Revision = 'dummy-revision'; $Steps = 20
$Epochs = 0; $runId = 'm2-01234567'
$Winner = $true; $Probe = $true; $ProbeSeqLen = 2048; $ProbeRowsTotal = 96
$PSBoundParameters = @{ ProbeRowsTotal = 96 }
""" + f"$repoRoot = '{Path(__file__).resolve().parents[3].as_posix()}'\n" + ("$ProbeContextInputs = 'C:/synthetic/inputs.jsonl'; $ProbeContextLimit = 15009\n" if context_file else "") + f"$HotRoot = '{hot_root}'\n$Rows = '{hot_root}/accepted.jsonl'\n" + f"$ExpectedDigests = '{digests}'\n$TrustedDigestRoot = '{'dummy-root.json' if digests else ''}'\n" + _wrapper_mount_construction(source) + construction + "\nConvertTo-Json -InputObject $dockerArgs -Compress"
                completed = subprocess.run(
                    [pwsh, "-NoProfile", "-NonInteractive", "-Command", command],
                    capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                args = json.loads(completed.stdout)
                image = args.index("dummy-image")
                self.assertEqual(args[image - 2:image], ["--entrypoint", "python"])
                self.assertEqual(args[image + 1], "/code/train_adapter.py")
                options, trainer = args[:image], args[image + 2:]
                self._assert_common_mounts(options, Path(tmp), epoch_mode=False, digests=bool(digests), context_probe=context_file)
                self.assertIn("dummy-code:/code:ro", options)
                if context_file:
                    self.assertNotIn("dummy-eval:/eval:ro", options)
                    self.assertFalse((Path(tmp) / "models").exists())
                else:
                    self.assertIn("dummy-eval:/eval:ro", options)
                self.assertNotIn("-e", trainer)
                for flag in ("HF_HUB_OFFLINE=1", "TRANSFORMERS_OFFLINE=1"):
                    if digests:
                        self.assertEqual(args.count(flag), 1)
                        self.assertEqual(options[options.index(flag) - 1], "-e")
                    else:
                        self.assertNotIn(flag, args)
                if context_file:
                    self.assertIn("C:/synthetic/inputs.jsonl:/workspace/context-inputs.jsonl:ro", options)
                    self.assertEqual(trainer[trainer.index("--probe-context-limit") + 1], "15009")
                    self.assertEqual(trainer[trainer.index("--probe-context-inputs") + 1], "/workspace/context-inputs.jsonl")
                if digests:
                    index = trainer.index("--expected-digests")
                    self.assertEqual(trainer[index + 1], "/workspace/expected-digests.json")
                    self.assertNotIn("--expected-digests", options)
                    index = trainer.index("--trusted-digest-root")
                    self.assertEqual(trainer[index + 1], "/workspace/trusted-digest-root.json")
                    self.assertNotIn("--trusted-digest-root", options)
                else:
                    self.assertNotIn("--expected-digests", args)
                    self.assertNotIn("--trusted-digest-root", args)
                self.assertIn("--winner", trainer)
                self.assertEqual(trainer[-2:], ["--probe-rows-total", "96"])
                self.assertEqual(trainer[trainer.index("--probe-seq-len") + 1], "2048")

    @unittest.skipUnless(os.name == "nt" and shutil.which("pwsh"), "Windows bounded job required")
    @unittest.skipUnless(os.name == "nt" and shutil.which("pwsh"), "Windows bounded job required")
    def test_wrapper_epoch_mode_docker_args_use_the_staged_tree_not_code(self):
        # Epoch mode's docker-argument contract moved from an inline snippet to
        # New-TrainerDockerArgs (scripts/train-adapter-stage.ps1): call the real
        # function directly with a synthetic stage/binding, since it needs no
        # custody, git or resolver fixture to exercise the resulting vector.
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            self.skipTest("PowerShell is required to evaluate Docker argument construction")
        source = (Path(__file__).resolve().parents[1] / "scripts" / "train-adapter.ps1").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            hot_root = Path(tmp).as_posix().replace("'", "''")
            (Path(tmp) / 'authority' / 'publication').mkdir(parents=True)
            for name in ('pin.py', 'route-r-trust-root-v2r4.json', 'publication/batch-0001.publication.json'):
                (Path(tmp) / 'authority' / name).write_text('{}')
            # These functions create directories only beneath the temporary HotRoot.
            command = f"""
$ErrorActionPreference = 'Stop'
$HotRoot = '{hot_root}'; $Rows = '{hot_root}/accepted.jsonl'; $Epochs = 4
$ExpectedDigests = 'dummy-digests.json'; $TrustedDigestRoot = 'dummy-root.json'
{_wrapper_mount_construction(source)}
$GpuMarker = '{hot_root}/synthetic-marker'
$stage = @{{ Train = 'C:/synthetic/Train'; Eval = 'C:/synthetic/Eval'; Input = 'C:/synthetic/Input' }}
$args_ = New-TrainerDockerArgs -Image 'unsloth/unsloth@sha256:1644d635bc7c5b57ed64cabbab1ae00647dfb583c2135fdfb6a461aeeba52739' -Name 'dummy-container' -CommonMounts $mounts -Stage $stage `
    -AuthoritySource '# synthetic authenticated source' -Batch 'batch-0001' -RootFile 'route-r-trust-root-v2r4.json' `
    -Locations @{{ PinFile = '{hot_root}/authority/pin.py'; CustodyRoot = '{hot_root}/authority' }} `
    -BindingPath 'C:/synthetic/Meta/publication-binding.json' -Base '{train_adapter.WINNER_BASE}' -Revision '{train_adapter.WINNER_REVISION}' `
    -ReportContainerPath '/workspace/reports/dummy-run.json' -Epochs 4 -EpochContextLimit 4096 -ModelsHostDir 'C:/synthetic/models/m2' `
    -ExtraEnv @('-e', 'HF_HUB_OFFLINE=1', '-e', 'TRANSFORMERS_OFFLINE=1')
ConvertTo-Json -InputObject $args_ -Compress
"""
            completed = subprocess.run(
                [pwsh, "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True, text=True, timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        args = json.loads(completed.stdout)
        image = args.index("unsloth/unsloth@sha256:1644d635bc7c5b57ed64cabbab1ae00647dfb583c2135fdfb6a461aeeba52739")
        self.assertEqual(args[image - 2:image], ["--entrypoint", "python"])
        self.assertEqual(args[image + 1:image + 4], ["-I", "-B", "-c"])
        self.assertTrue(args[image + 4].endswith('bootstrap()\n'))
        options, trainer = args[:image], args[image + 5:]
        self._assert_common_mounts(options, Path(tmp), epoch_mode=True, digests=True)
        self.assertNotIn("/code", " ".join(args))
        self.assertNotIn("/code:ro", options)
        self.assertIn("C:/synthetic/Train:/train:ro", options)
        self.assertIn("C:/synthetic/Eval:/eval:ro", options)
        self.assertIn("C:/synthetic/Input:/workspace/rows:ro", options)
        self.assertIn("C:/synthetic/Meta/publication-binding.json:/workspace/publication-binding.json:ro", options)
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", options)
        self.assertIn("ADAPTER_MODELS_DIR=/workspace/models/m2", options)
        self.assertIn("ADAPTER_MODELS_HOST_DIR=C:/synthetic/models/m2", options)
        self.assertIn(f"ADAPTER_BASE={train_adapter.WINNER_BASE}", options)
        self.assertEqual(trainer[trainer.index("--base") + 1], train_adapter.WINNER_BASE)
        self.assertEqual(trainer[trainer.index("--revision") + 1], train_adapter.WINNER_REVISION)
        self.assertIn("HF_HOME=/workspace/hf", options)
        self.assertIn("HF_HUB_OFFLINE=1", options)
        self.assertEqual(trainer[:2], ["--rows", "/workspace/rows/accepted.jsonl"])
        self.assertIn("--epochs", trainer)
        self.assertEqual(trainer[trainer.index("--epochs") + 1], "4")
        self.assertEqual(trainer[trainer.index("--epoch-context-limit") + 1], "4096")
        self.assertIn("--publication-binding", trainer)
        self.assertEqual(trainer[trainer.index("--publication-binding") + 1], "/workspace/publication-binding.json")
        self.assertNotIn("--steps", args)
        self.assertNotIn("ADAPTER_STEPS=20", args)
        self.assertIn("--report", trainer)
        self.assertEqual(trainer[trainer.index("--report") + 1], "/workspace/reports/dummy-run.json")
        self.assertNotIn("--winner", trainer)
        self.assertNotIn("--probe", trainer)

    def _assert_common_mounts(self, options, root, *, epoch_mode, digests, context_probe=False):
        mounts = [options[index + 1].replace("\\", "/")
                  for index, value in enumerate(options) if value == "-v"]
        self.assertIn(f"{root.as_posix()}/hf:/workspace/hf:ro", mounts)
        model_subdir = "models/m2" if epoch_mode else "models/m2/m2-01234567"
        report_subdir = "reports/adapter-output" if epoch_mode else "reports/adapter-output/m2-01234567"
        if context_probe:
            self.assertFalse(any(":/workspace/models" in mount for mount in mounts))
        else:
            self.assertIn(f"{root.as_posix()}/{model_subdir}:/workspace/models/m2", mounts)
        self.assertIn(f"{root.as_posix()}/{report_subdir}:/workspace/reports", mounts)
        self.assertFalse(any(":/workspace/runs" in mount for mount in mounts))
        self.assertNotIn(f"{root.as_posix()}/reports:/workspace/reports", mounts)
        rows_mounts = [mount for mount in mounts if ":/workspace/rows" in mount]
        self.assertEqual(rows_mounts, ["C:/synthetic/Input:/workspace/rows:ro"] if epoch_mode
                         else [] if context_probe else [f"{root.as_posix()}:/workspace/rows:ro"])
        for filename, host in (("expected-digests", "dummy-digests"), ("trusted-digest-root", "dummy-root")):
            actual = [mount for mount in mounts if f":/workspace/{filename}.json" in mount]
            self.assertEqual(actual, [f"{host}.json:/workspace/{filename}.json:ro"] if digests else [])

    @unittest.skipUnless(os.name == "nt" and shutil.which("pwsh"), "Windows bounded job required")
    def test_epoch_mounts_refuse_writable_protected_aliases(self):
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            self.skipTest("PowerShell is required to evaluate mount isolation")
        helper = Path(__file__).resolve().parents[1] / "scripts" / "train-adapter-stage.ps1"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            protected = root / "runs" / "stage" / "m2-01234567"
            protected.mkdir(parents=True)
            link = root / "output" / "custody-link"
            link.parent.mkdir()
            linked = make_reparse_link(link, protected)
            command = (f". '{helper.with_name('invoke-bounded.ps1').as_posix()}'\n"
                       f". '{helper.as_posix()}'\n$GpuMarker = '{root.as_posix()}/synthetic-marker'\n") + r"""
$ErrorActionPreference = 'Stop'
function Refuses($Mount, $Protected, $Message) {
    try { Assert-TrainerMountIsolation @('-v', $Mount) @($Protected) }
    catch { if ($_.Exception.Message.Contains($Message)) { return }; throw }
    throw "unsafe mount accepted: $Mount"
}
""" + f"""
$root = '{root.as_posix()}'; $protected = '{protected.as_posix()}'
Refuses "$root/runs:/workspace/runs" $protected 'overlaps protected'
Refuses "$protected`:/alternate" $protected 'overlaps protected'
Refuses "$protected/child:/alternate" $protected 'overlaps protected'
Assert-TrainerMountIsolation @('-v', "$root/runs:/workspace/runs:ro") @($protected)
Assert-TrainerMountIsolation @('-v', "$root/runs-other:/workspace/output") @($protected)
"""
            if linked:
                command += f"Refuses '{link.parent.as_posix()}:/workspace/output' $protected 'junction or symbolic link'\n"
            completed = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", command],
                                       capture_output=True, text=True, timeout=30,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            self.assertEqual(completed.returncode, 0, completed.stderr)

    @unittest.skipUnless(os.name == "nt" and shutil.which("pwsh"), "Windows bounded job required")
    def test_writable_mount_scan_is_bounded_and_refuses_incomplete_receipts(self):
        helper = Path(__file__).resolve().parents[1] / "scripts" / "train-adapter-stage.ps1"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output-'quoted-\u00e9"
            (output / "deep" / "leaf").mkdir(parents=True)
            (output / "deep" / "leaf" / "payload.bin").write_bytes(b"synthetic\x00\xff")
            command = (f". '{helper.with_name('invoke-bounded.ps1').as_posix()}'\n"
                       f". '{helper.as_posix()}'\n") + r"""
$ErrorActionPreference = 'Stop'
$GpuMarker = '__ROOT__/synthetic-marker'
$mount = '__OUTPUT__:/workspace/output'
$protected = '__ROOT__/protected'
$realBounded = ${function:Invoke-Bounded}
function Require-Refusal($Message) {
    try { Assert-TrainerMountIsolation @('-v', $mount) @($protected) }
    catch { if ($_.Exception.Message.Contains($Message)) { return }; throw }
    throw 'incomplete scan accepted'
}
# Real traversal of nested directories and an opaque payload; no model read.
Assert-TrainerMountIsolation @('-v', $mount) @($protected)
foreach ($fake in @('empty', 'nonzero', 'extra')) {
    $script:receipt = $fake
    function Invoke-Bounded {
        switch ($script:receipt) {
            'empty' { return @() }
            'nonzero' { return @('TRAINER_MOUNT_SCAN_OK', '__EXIT__1') }
            'extra' { return @('TRAINER_MOUNT_SCAN_OK', 'unexpected', '__EXIT__0') }
        }
    }
    Require-Refusal 'mount scan failed'
}
# Inject a blocking filesystem call INSIDE the real encoded scanner/job.
# The parent's timeout is not a deadline check around the blocking call.
function Invoke-Bounded([string]$Exe, [string[]]$A, [int]$Seconds) {
    if ($Seconds -ne 60) { throw 'production scan deadline changed' }
    $scan = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($A[-1]))
    $fault = "function Get-ChildItem { Set-Content -LiteralPath '__ROOT__/scan-pid' -Value `$PID; Start-Sleep -Seconds 30 }`n"
    $A[-1] = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($fault + $scan))
    & $realBounded $Exe $A 2
}
$clock = [Diagnostics.Stopwatch]::StartNew()
Require-Refusal '__EXIT__timeout'
if ($clock.Elapsed.TotalSeconds -gt 12) { throw 'scanner was not bounded' }
$scanPid = [int](Get-Content -LiteralPath '__ROOT__/scan-pid')
if (Get-Process -Id $scanPid -ErrorAction SilentlyContinue) { throw 'blocked scanner survived cleanup' }
if ($BoundedUnconfirmed -or (Test-Path -LiteralPath $GpuMarker)) { throw 'unconfirmed cleanup; stop' }
# Errors and cleanup uncertainty must never be converted to successful scans.
function Invoke-Bounded([string]$Exe, [string[]]$A, [int]$Seconds) {
    $scan = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($A[-1]))
    $A[-1] = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes("function Get-ChildItem { throw 'synthetic unreadable directory' }`n" + $scan))
    & $realBounded $Exe $A $Seconds
}
Require-Refusal 'synthetic unreadable directory'
function Invoke-Bounded { throw 'synthetic cleanup unconfirmed' }
Require-Refusal 'synthetic cleanup unconfirmed'
Write-Output 'nested, receipt, blocked scan cleanup, unreadable, cleanup propagation: PASS'
"""
            command = command.replace("__ROOT__", root.as_posix().replace("'", "''"))
            command = command.replace("__OUTPUT__", output.as_posix().replace("'", "''"))
            result = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True, text=True, timeout=35, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("propagation: PASS", result.stdout)
            self.assertEqual((output / "deep" / "leaf" / "payload.bin").read_bytes(), b"synthetic\x00\xff")

    def test_epoch_readonly_cache_keeps_linked_layout_without_recursive_scan(self):
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            self.skipTest("PowerShell is required to evaluate mount isolation")
        helper = Path(__file__).resolve().parents[1] / "scripts" / "train-adapter-stage.ps1"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hub = root / "hf" / "hub"
            shared = hub / "blobs"
            shared.mkdir(parents=True)
            repo = hub / "models--synthetic--model"
            snapshot = repo / "snapshots" / ("a" * 40)
            snapshot.mkdir(parents=True)
            # Directory links also work without Windows file-symlink privilege.
            # This tests mount traversal only, not model snapshot admission.
            linked = make_reparse_link(repo / "blobs", shared)
            if linked:
                linked = make_reparse_link(snapshot / "linked", repo / "blobs")
            command = f". '{helper.as_posix()}'\n" + f"""
$ErrorActionPreference = 'Stop'
$m = @(New-TrainerCommonMounts -HotRoot '{root.as_posix()}' -RowsDir '{root.as_posix()}/rows' -EpochMode)
$cache = @($m | Where-Object {{ $_ -like '*:/workspace/hf*' }})
if ($cache.Count -ne 1 -or -not $cache[0].EndsWith(':/workspace/hf:ro')) {{ throw 'cache not read-only' }}
# A scan of HF would fail this control even on hosts without link privilege.
function Get-ChildItem {{ throw 'read-only cache must not be recursively traversed' }}
Assert-TrainerMountIsolation @('-v', $cache[0]) @('{root.as_posix()}/custody')
Write-Output 'PASS'
"""
            completed = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", command],
                                       capture_output=True, text=True, timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            if linked:
                self.assertTrue((snapshot / "linked").is_dir())

    def test_git_payload_requires_commit_object_before_path_lookup(self):
        # The transport is synthetic; assert refusal precedes path extraction,
        # including tags that peel successfully but are not the supplied commit.
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            self.skipTest("PowerShell is required to evaluate the Git object guard")
        helper = Path(__file__).resolve().parents[1] / "scripts" / "train-adapter-stage.ps1"
        command = f". '{helper.as_posix()}'\n" + r"""
$ErrorActionPreference = 'Stop'
$script:count = 0
function Invoke-Bounded($Exe, $A, $Seconds) {
    $script:count++
    if ($script:count -eq 2 -and $script:validCommit) {
        if ($A[-1] -cne (('a' * 40) + ':payload.py')) { throw 'unexpected path lookup' }
        throw 'verified commit reached path lookup'
    }
    if ($A[-1] -cne (('a' * 40) + '^{commit}')) { throw 'path lookup happened before commit verification' }
    return $script:reply
}
$script:validCommit = $false
foreach ($reply in @(@('__EXIT__128'), @(('b' * 40), '__EXIT__0'), @(('a' * 40), 'extra', '__EXIT__0'))) {
    $script:reply = $reply; $script:count = 0
    try { Get-GitBlobBytes -RepoRoot '.' -Commit ('a' * 40) -Path 'payload.py' -ExpectedSha256 ('0' * 64); throw 'accepted invalid commit' }
    catch { if (-not $_.Exception.Message.Contains('must name the exact commit object')) { throw } }
    if ($script:count -ne 1) { throw 'invalid commit reached a second Git call' }
}
$script:validCommit = $true; $script:count = 0; $script:reply = @(('a' * 40), '__EXIT__0')
try { Get-GitBlobBytes -RepoRoot '.' -Commit ('a' * 40) -Path 'payload.py' -ExpectedSha256 ('0' * 64); throw 'missing path lookup' }
catch { if (-not $_.Exception.Message.Contains('verified commit reached path lookup')) { throw } }
if ($script:count -ne 2) { throw 'valid commit did not reach path lookup' }
Write-Output 'PASS'
"""
        completed = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", command],
                                   capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_wrapper_epoch_mode_requires_batch_and_runs_the_binding_chain_before_the_mutex(self):
        # Structural proof, no execution: -Batch is gated with -Epochs, the
        # wrapper dot-sources the new stage script, and every binding step
        # (resolver through docker-arg construction) appears in the source
        # before the GPU mutex is created.
        script = Path(__file__).resolve().parents[1] / "scripts" / "train-adapter.ps1"
        source = script.read_text(encoding="utf-8")
        self.assertIn("train-adapter-stage.ps1", source)
        self.assertIn("-Epochs requires -Batch", source)
        self.assertIn("-Batch is only valid with -Epochs", source)
        mutex_index = source.index("[System.Threading.Mutex]::new(")
        for call in (
            "Get-ProductionLocations", "Read-RootPin", "Read-ApprovedRoot",
            "Invoke-PublicationResolver", "Read-PublicationRecords",
            "New-StageTree", "New-PublicationBinding", "New-TrainerDockerArgs",
        ):
            index = source.index(call)
            self.assertGreater(index, 0, f"{call} not found")
            self.assertLess(index, mutex_index, f"{call} must run before the GPU mutex is created")

    def test_epoch_image_guards_refuse_aliases_before_setup(self):
        pwsh = shutil.which('pwsh')
        if pwsh is None:
            self.skipTest('PowerShell is required for actual wrapper argument guards')
        script = Path(__file__).resolve().parents[1] / 'scripts' / 'train-adapter.ps1'
        source = script.read_text(encoding='utf-8')
        # Execute the real parameter defaults and guard prefix, stopping before
        # row/custody reads. Only dot-sourcing the Windows process helper is
        # omitted: no bounded process is invoked by these argument guards.
        prefix = source[:source.index('$Rows = [IO.Path]::GetFullPath($Rows)')]
        prefix = prefix.replace(". (Join-Path $PSScriptRoot 'invoke-bounded.ps1')", '')
        prefix = prefix.replace(". (Join-Path $PSScriptRoot 'train-adapter-stage.ps1')",
                                ". '" + script.with_name('train-adapter-stage.ps1').as_posix() + "'")
        approved = 'unsloth/unsloth@sha256:1644d635bc7c5b57ed64cabbab1ae00647dfb583c2135fdfb6a461aeeba52739'
        with tempfile.TemporaryDirectory() as tmp:
            prefix_file = Path(tmp) / 'wrapper-guards.ps1'
            prefix_file.write_text(prefix + "\n'GUARDS-PASSED'\n", encoding='utf-8')
            command = f"""
$ErrorActionPreference = 'Stop'
$approved = '{approved}'
$args_ = @{{ Rows = '{Path(tmp).as_posix()}/absent/accepted.jsonl'; Epochs = 1; Batch = 'batch-0001';
    Base = '{train_adapter.WINNER_BASE}'; Revision = '{train_adapter.WINNER_REVISION}'; TimeoutMinutes = 1; EpochContextLimit = 4096 }}
# Both the parameter default and the explicit identical digest pass.
if ((& '{prefix_file.as_posix()}' @args_) -ne 'GUARDS-PASSED') {{ throw 'default refused' }}
if ((& '{prefix_file.as_posix()}' @args_ -Image $approved) -ne 'GUARDS-PASSED') {{ throw 'exact digest refused' }}
foreach ($bad in 'unsloth/unsloth:latest', ('unsloth/unsloth@sha256:' + ('0' * 64)),
        (' ' + $approved), ($approved + ' '), $approved.ToUpperInvariant()) {{
    $denied = $false
    try {{ & '{prefix_file.as_posix()}' @args_ -Image $bad | Out-Null }}
    catch {{ $denied = $_.Exception.Message.Contains('exact approved training image') }}
    if (-not $denied) {{ throw "wrapper admitted alias: $bad" }}
}}
# Non-epoch and probe image selection keeps its previous behavior.
foreach ($probe in $false, $true) {{
    if ((& '{prefix_file.as_posix()}' -Rows 'absent/accepted.jsonl' -Image 'custom/image:tag' -Probe:$probe) -ne 'GUARDS-PASSED') {{ throw 'legacy image changed' }}
}}
. '{script.with_name('train-adapter-stage.ps1').as_posix()}'
foreach ($bad in 'unsloth/unsloth:latest', ('unsloth/unsloth@sha256:' + ('0' * 64)),
        (' ' + $approved), ($approved + ' '), $approved.ToUpperInvariant()) {{
    $denied = $false
    try {{ New-TrainerDockerArgs -Image $bad -Name 'unused' -CommonMounts @('-v','unused:/safe:ro') `
        -Stage @{{}} -BindingPath 'unused' -Base 'unused' -Revision 'unused' -ReportContainerPath 'unused' `
        -Epochs 1 -EpochContextLimit 4096 -AuthoritySource 'unused' -Batch 'batch-0001' -RootFile 'route-r-trust-root-v2r4.json' `
        -Locations @{{}} | Out-Null }}
    catch {{ $denied = $_.Exception.Message.Contains('exact approved training image') }}
    if (-not $denied) {{ throw "direct helper reached authority or mounts with alias: $bad" }}
}}
'PASS default + exact + five wrapper aliases + five direct helper aliases + legacy/probe'
"""
            result = subprocess.run([pwsh, '-NoProfile', '-NonInteractive', '-Command', command],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('PASS default + exact', result.stdout)
            self.assertFalse((Path(tmp) / 'absent').exists())

    def test_epoch_preflight_precedes_first_directory_mutation(self):
        pwsh = shutil.which('pwsh')
        if pwsh is None:
            self.skipTest('PowerShell is required for wrapper ordering checks')
        script = Path(__file__).resolve().parents[1] / 'scripts' / 'train-adapter.ps1'
        source = script.read_text(encoding='utf-8')
        first = source.index('# Epoch storage admission')
        mutex = source.index('$gpuLock = [System.Threading.Mutex]::new(')
        fragment = source[first:mutex]
        self.assertLess(first, source.index('$mounts = New-TrainerCommonMounts'))
        self.assertLess(first, source.index('$stage = New-StageTree'))
        self.assertLess(first, source.index('(Write-StageFile'))
        self.assertLess(mutex, source.index('$pf = @(Invoke-Bounded'))
        self.assertNotIn('New-Item', source[:first])
        self.assertNotIn('Set-Content', source[:first])
        # Execute actual preflight and mount/staging statements. The first
        # mutation is replaced with a sentinel and stops the success control;
        # failure must never reach it or the later stage/binding/GPU statements.
        with tempfile.TemporaryDirectory() as tmp:
            sentinel = Path(tmp) / 'mutation'
            command = f"""
$ErrorActionPreference = 'Stop'
$HotRoot = '{Path(tmp).as_posix()}'; $Rows = 'unused'; $ExpectedDigests = ''; $TrustedDigestRoot = ''; $Epochs = 1
$script:seen = [Collections.Generic.List[string]]::new()
function Invoke-Bounded($exe, $argv, $seconds) {{
    if ($exe -cne 'pwsh' -or $seconds -ne 180 -or
        ($argv -join '|') -cne "-NoProfile|-File|$(Join-Path $PSScriptRoot 'preflight.ps1')|-HotRoot|$HotRoot") {{ throw 'unexpected preflight command' }}
    $script:seen.Add('read-only-preflight')
    $script:reply
}}
function New-TrainerCommonMounts {{
    $script:seen.Add('first-directory-mutation')
    [IO.File]::WriteAllText('{sentinel.as_posix()}', 'synthetic')
    throw 'SYNTHETIC-FIRST-MUTATION-STOP'
}}
foreach ($case in 'exit1', 'timeout', 'empty', 'success') {{
    $script:seen.Clear()
    $script:reply = switch ($case) {{ 'exit1' {{ '__EXIT__1' }} 'timeout' {{ '__EXIT__timeout' }} 'empty' {{ @() }} 'success' {{ '__EXIT__0' }} }}
    $reason = ''
    try {{
{fragment}
    }} catch {{ $reason = $_.Exception.Message }}
    if ($case -ceq 'success') {{
        if ($reason -cne 'SYNTHETIC-FIRST-MUTATION-STOP' -or ($script:seen -join ',') -cne 'read-only-preflight,first-directory-mutation') {{ throw 'success ordering incorrect' }}
    }} else {{
        if (-not $reason.Contains('pre-stage preflight did not pass') -or ($script:seen -join ',') -cne 'read-only-preflight' -or (Test-Path -LiteralPath '{sentinel.as_posix()}')) {{ throw "preflight failure reached a side effect: $case; reason=$reason; seen=$($script:seen -join ',')" }}
    }}
}}
'PASS nonzero/timeout/empty refuse before writes; success preflight precedes first mutation'
"""
            test_script = Path(tmp) / 'preflight-order.ps1'
            test_script.write_text(command, encoding='utf-8')
            result = subprocess.run([pwsh, '-NoProfile', '-NonInteractive', '-File', str(test_script)],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('PASS nonzero/timeout/empty', result.stdout)
            self.assertEqual(set(Path(tmp).iterdir()), {sentinel, test_script})

    def test_wrapper_guard_block_refuses_winner_without_probe_and_bad_epochs(self):
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            self.skipTest("PowerShell is required to evaluate wrapper argument guards")
        script = Path(__file__).resolve().parents[1] / "scripts" / "train-adapter.ps1"
        source = script.read_text(encoding="utf-8")
        start = source.index("$WinnerBase = ")
        guard_start = source.index("if ($Winner -and $Fallback)", start)
        end = source.index("if ($Steps -lt 1)", guard_start)
        prerequisites, guards = source[start:guard_start], source[guard_start:end]
        helper = script.with_name("train-adapter-stage.ps1")
        prerequisites = ". '" + helper.as_posix().replace("'", "''") + "'\n" + prerequisites
        prerequisites += "$Image = '" + 'unsloth/unsloth@sha256:1644d635bc7c5b57ed64cabbab1ae00647dfb583c2135fdfb6a461aeeba52739' + "'\n"
        epoch_pin = {"EpochContextLimit": "4096", "Epochs": "4", "TimeoutMinutes": "1", "Base": "$WinnerBase", "Revision": "$WinnerRevision"}
        cases = [
            ({}, None),
            ({"Winner": "$true", "Probe": "$false"}, "-Winner is only valid together with -Probe"),
            ({"Epochs": "0"}, "-Epochs must be between 1 and 6"),
            ({"Epochs": "7"}, "-Epochs must be between 1 and 6"),
            ({"Epochs": "4", "Steps": "20"}, "-Epochs and -Steps are mutually exclusive"),
            ({"Epochs": "4", "Probe": "$true"}, "-Epochs is a training mode"),
            ({"Epochs": "4"}, "requires explicit positive -TimeoutMinutes"),
            ({"Epochs": "4", "TimeoutMinutes": "1"}, "requires explicit -Base"),
            ({**epoch_pin, "Batch": "'batch-0001'"}, None),
            ({**epoch_pin, "Batch": "'batch-0001'", "Base": "$FallbackBase", "Revision": "$FallbackRevision"}, None),
            ({**epoch_pin, "Batch": "'batch-0001'", "Base": "$FallbackBase"}, "requires explicit -Base"),
            ({**epoch_pin, "Batch": "'batch-0001'", "Revision": "$FallbackRevision"}, "requires explicit -Base"),
            ({**epoch_pin, "Batch": "'batch-0001'", "Base": "'qwen/Qwen3-8B'", "Revision": "$FallbackRevision"}, "requires explicit -Base"),
            (epoch_pin, "-Epochs requires -Batch"),
            ({**{k: v for k, v in epoch_pin.items() if k != "EpochContextLimit"}, "Batch": "'batch-0001'"}, "requires explicit -EpochContextLimit"),
            ({**epoch_pin, "Batch": "'batch-0001'", "EpochContextLimit": "2048"}, "requires explicit -EpochContextLimit"),
            *[({**epoch_pin, "Batch": value}, "-Batch is not a valid batch id")
              for value in ("''", "'../escape'", "'UPPER'")],
            ({"Batch": "'batch-0001'"}, "-Batch is only valid with -Epochs"),
            ({"Epochs": "4", "TimeoutMinutes": "1", "Base": "'smoke'", "Revision": "$WinnerRevision"}, "requires explicit -Base"),
            *[({**epoch_pin, "Batch": "'batch-0001'", "TimeoutMinutes": value},
               "must be finite and positive") for value in ("0", "-1", "[double]::NaN", "[double]::PositiveInfinity")],
            ({"Winner": "$true", "Probe": "$true"}, None),
        ]
        for overrides, message in cases:
            with self.subTest(overrides=overrides, message=message):
                bound = "@{ " + "; ".join(
                    f"{name} = {value}" for name, value in overrides.items()
                    if name in ("Epochs", "Steps", "Base", "Revision", "TimeoutMinutes", "Batch", "EpochContextLimit")
                ) + " }"
                setup = "\n".join(
                    f"${name} = {value}" for name, value in overrides.items()
                )
                command = (
                    "$ErrorActionPreference = 'Stop'\n"
                    "$Winner = $false; $Fallback = $false; $Probe = $false; "
                    "$Epochs = 0; $Steps = 20; $TimeoutMinutes = 90; $Batch = ''; $PSBoundParameters = @{}\n"
                    + prerequisites
                    + setup + "\n"
                    + (f"$PSBoundParameters = {bound}\n" if bound != "@{  }" else "")
                    + guards + "\n'GUARDS-PASSED'"
                )
                completed = subprocess.run(
                    [pwsh, "-NoProfile", "-NonInteractive", "-Command", command],
                    capture_output=True, text=True, timeout=30,
                )
                if message is None:
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertIn("GUARDS-PASSED", completed.stdout)
                else:
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn(message, completed.stderr + completed.stdout)

    def test_root_pin_accepts_only_one_literal_definition_with_optional_comment(self):
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            self.skipTest("PowerShell is required to evaluate the root pin reader")
        helper = (Path(__file__).resolve().parents[1] / "scripts" / "train-adapter-stage.ps1").read_text(encoding="utf-8")
        # Extract the actual reader and its ancestry guard without evaluating
        # the wrapper, loading production pins or starting any GPU operation.
        guard = helper[helper.index("function Assert-NoReparsePointInAncestry("):
                       helper.index("function Test-ExactKeySet(")]
        reader = helper[helper.index("function Read-RootPin {"):
                        helper.index("function Test-PathShaEntry(")]
        expected = {"ROOT_FILE": "route-r-trust-root-v2r4.json",
                    "ROOT_FORMAT": "route-r-trust-root/v2r4",
                    "APPROVED_TRUST_ROOT_SHA256": "a" * 64}
        plain = "".join(f"{name} = '{value}'\n" for name, value in expected.items())
        cases = [(plain, True)]
        for quote in ("'", '"'):
            for newline in ("\n", "\r\n"):
                cases.append((newline.join(
                    f"\t{name} = {quote}{value}{quote}\t # comment 'quoted' = ignored"
                    for name, value in expected.items()) + newline, True))
        for name, value in expected.items():
            line = f"{name} = '{value}'\n"
            for replacement in ("", f"{name} = None # not approved\n",
                                f"{name} = '{value}' + 'suffix'\n",
                                f"{name} = '{value}\n",
                                line + line, line + f"{name} = malformed\n"):
                cases.append((plain.replace(line, replacement), False))
        cases.extend((plain.replace("'" + "a" * 64 + "'", repr(value)), False)
                     for value in ("A" * 64, "a" * 63, "g" * 64, "a" * 64 + "\n"))
        cases.append((plain.replace("route-r-trust-root-v2r4.json", "../root.json"), False))
        with tempfile.TemporaryDirectory() as tmp:
            pin = Path(tmp) / "synthetic_pin.py"
            command = ("$ErrorActionPreference = 'Stop'\n" + guard + reader
                       + "\nRead-RootPin -PinFile '" + str(pin).replace("'", "''")
                       + "' | ConvertTo-Json -Compress")
            for index, (source, valid) in enumerate(cases):
                with self.subTest(case=index, valid=valid):
                    pin.write_bytes(source.encode("utf-8"))
                    result = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", command],
                                            capture_output=True, text=True, timeout=30)
                    if valid:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(json.loads(result.stdout), expected)
                    else:
                        self.assertNotEqual(result.returncode, 0, result.stdout)
                        self.assertIn("root pin", result.stderr + result.stdout)

    def test_length_statistics_on_a_known_list(self):
        lengths = [10, 20, 30, 40, 50]
        stats = train_adapter.length_statistics(lengths)
        self.assertEqual(stats["max"], 50)
        self.assertAlmostEqual(stats["p50"], train_adapter.percentile(lengths, 50.0))
        self.assertAlmostEqual(stats["p95"], train_adapter.percentile(lengths, 95.0))

    def test_probe_timed_window_bookkeeping(self):
        self.assertEqual(
            train_adapter.timed_window_optimizer_updates(
                train_adapter.PROBE_TIMED_MICROSTEPS,
                train_adapter.PROBE_GRAD_ACCUM,
            ),
            5,
        )
        self.assertEqual(train_adapter.PROBE_WARMUP_MICROSTEPS, 10)
        self.assertEqual(train_adapter.PROBE_TIMED_MICROSTEPS, 40)

    def test_warmup_boundary_discards_pending_gradients_without_a_step(self):
        fake_torch = mock.MagicMock()
        fake_torch.cuda.is_available = mock.MagicMock(return_value=False)
        fake_optimizer = fake_torch.optim.AdamW.return_value
        fake_model = mock.MagicMock()
        fake_model.parameters = mock.MagicMock(side_effect=lambda: iter([mock.MagicMock()]))
        with mock.patch.object(train_adapter, "torch", fake_torch):
            # 10 warm-up microsteps at accumulation 8: one update at 8, then the
            # 2 pending microsteps are discarded (zero_grad, no extra step).
            train_adapter.run_probe_microsteps(
                fake_model, [{"input_ids": [1], "attention_mask": [1], "labels": [-100, 2]}],
                warmup_microsteps=10, timed_microsteps=0, gradient_accumulation=8,
            )
            self.assertEqual(fake_optimizer.step.call_count, 1)
            self.assertEqual(fake_optimizer.zero_grad.call_count, 2)
            # The timed phase keeps the previous behaviour: a trailing partial
            # window still performs its optimizer update.
            fake_torch.reset_mock()
            fake_optimizer.reset_mock()
            fake_torch.cuda.is_available = mock.MagicMock(return_value=False)
            fake_torch.optim.AdamW.return_value = fake_optimizer
            train_adapter.run_probe_microsteps(
                fake_model, [{"input_ids": [1], "attention_mask": [1], "labels": [-100, 2]}],
                warmup_microsteps=0, timed_microsteps=10, gradient_accumulation=8,
            )
            self.assertEqual(fake_optimizer.step.call_count, 2)
            self.assertEqual(fake_optimizer.zero_grad.call_count, 2)
        self.assertEqual(train_adapter.PROBE_WARMUP_MICROSTEPS % train_adapter.PROBE_GRAD_ACCUM, 2)
        kwargs = self._minimal_probe_report_kwargs()
        kwargs["warmup_discarded_microsteps"] = train_adapter.PROBE_WARMUP_MICROSTEPS % train_adapter.PROBE_GRAD_ACCUM
        report = train_adapter.build_probe_report(**kwargs)
        self.assertEqual(report["warmup_pending_gradients"], "discarded_at_boundary_without_step")
        self.assertEqual(report["warmup_discarded_microsteps"], 2)

    def test_probe_extrapolation_and_contingency(self):
        self.assertAlmostEqual(train_adapter.extrapolate_wall_seconds(96, 2.0), 576.0)
        self.assertAlmostEqual(train_adapter.wall_seconds_with_contingency(100.0), 120.0)

    def test_probe_pass_booleans_at_boundaries(self):
        self.assertTrue(train_adapter.probe_memory_pass(10.999))
        self.assertFalse(train_adapter.probe_memory_pass(11.0))
        self.assertTrue(train_adapter.probe_time_pass(35999.9))
        self.assertFalse(train_adapter.probe_time_pass(36000.0))

    def test_help_lists_probe_flags(self):
        buffer = io.StringIO()
        with mock.patch.object(sys, "stdout", buffer):
            with self.assertRaises(SystemExit) as raised:
                train_adapter.main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        help_text = buffer.getvalue()
        self.assertIn("--probe", help_text)
        self.assertIn("--probe-rows-total", help_text)
        self.assertIn("--probe-seq-len", help_text)
        self.assertIn("--expected-digests", help_text)

    def _digest_entry(self, root: Path, name: str, data: bytes) -> dict:
        path = root / name
        path.write_bytes(data)
        return {"path": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}

    def _assert_exporter_adapter_hashes(self, leaf: Path) -> None:
        """A re-implementation of the exporter's Assert-AdapterHashes gate."""
        manifest = json.loads((leaf / "adapter-hashes.json").read_text(encoding="utf-8"))
        self.assertIn("files", manifest)
        names = list(manifest["files"])
        for mandatory in ("adapter_config.json", "adapter_model.safetensors"):
            self.assertIn(mandatory, names)
        seen: set[str] = set()
        for rel in names:
            self.assertNotIn("\\", rel)
            self.assertNotIn(":", rel)
            self.assertFalse(rel.startswith("/"))
            for segment in rel.split("/"):
                self.assertNotIn(segment, ("", ".", ".."))
                self.assertFalse(segment.endswith((" ", ".")))
            self.assertRegex(manifest["files"][rel], r"^[0-9a-fA-F]{64}$")
            self.assertNotIn(rel.lower(), seen)
            seen.add(rel.lower())
            full = leaf / rel
            self.assertTrue(full.is_file(), rel)
            self.assertFalse(full.is_symlink() or os.path.isjunction(full))
            self.assertEqual(
                hashlib.sha256(full.read_bytes()).hexdigest(),
                manifest["files"][rel].lower(),
            )
        for path in sorted(leaf.rglob("*")):
            if path.name == "adapter-hashes.json" and path.parent == leaf:
                continue
            self.assertFalse(path.is_symlink() or os.path.isjunction(path), str(path))
            if path.is_file():
                self.assertIn(path.relative_to(leaf).as_posix(), names)

    def _trusted_chain(self, root: Path, *, base=train_adapter.WINNER_BASE,
                       revision=train_adapter.WINNER_REVISION) -> tuple[Path, Path, str]:
        # Synthetic metadata and weights only. Tests patch the fixed code pin,
        # never add a runtime argument that can approve an arbitrary root.
        snapshot = root / "snapshot"
        snapshot.mkdir(exist_ok=True)
        entry = self._digest_entry(snapshot, "model.safetensors", b"synthetic weights")
        key = f"{base}@{revision}"
        listing = root / "upstream.json"
        listing.write_bytes(json.dumps({key: {"files": [entry]}}).encode())
        trusted = root / "approved-root.json"
        trusted.write_bytes(json.dumps({"models": [{
            "hf_repo": base,
            "hf_revision": revision,
            "upstream_weight_digests_sha256": hashlib.sha256(listing.read_bytes()).hexdigest(),
        }]}).encode())
        return trusted, listing, hashlib.sha256(trusted.read_bytes()).hexdigest()

    def test_epoch_8b_authenticates_approved_snapshot_before_model_effects(self):
        import predict_dev
        for fault in (None, "missing_root", "changed_root", "unapproved_entry", "wrong_revision",
                      "missing_list", "changed_list", "changed_weights", "unknown_student"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                base, revision = train_adapter.FALLBACK_BASE, train_adapter.FALLBACK_REVISION
                approved_base = train_adapter.WINNER_BASE if fault == "unapproved_entry" else base
                approved_revision = train_adapter.WINNER_REVISION if fault == "unapproved_entry" else revision
                if fault == "unknown_student":
                    base = approved_base = "Other/Approved-Model"
                trusted, listing, pin = self._trusted_chain(root, base=approved_base, revision=approved_revision)
                fx = _build_publication_fixture(root, rows=slice3_train_rows() + slice3_dev_rows())
                if fault == "changed_root":
                    trusted.write_bytes(trusted.read_bytes() + b" ")
                if fault == "changed_list":
                    listing.write_bytes(listing.read_bytes() + b" ")
                if fault == "changed_weights":
                    (root / "snapshot" / "model.safetensors").write_bytes(b"changed weights")
                if fault == "wrong_revision":
                    revision = "a" * 40
                stop = RuntimeError("synthetic model-load boundary; no model loaded")
                with mock.patch.object(train_adapter, "TRUSTED_DIGEST_ROOT_SHA256", pin), \
                     mock.patch.object(train_adapter, "local_snapshot_dir", return_value=root / "snapshot"), \
                     mock.patch.dict(os.environ, {"ADAPTER_MODELS_DIR": str(root / "models")}), \
                     mock.patch.object(predict_dev, "_default_template_source", return_value=(fx["code_dir"] / "prompt_template.py").read_bytes()), \
                     mock.patch.object(train_adapter, "set_seed") as seed, \
                     mock.patch.object(train_adapter, "FastLanguageModel") as model, \
                     mock.patch.object(train_adapter.torch, "cuda", mock.Mock(max_memory_reserved=mock.Mock(return_value=0)), create=True) as cuda:
                    model.from_pretrained.side_effect = stop
                    with self.assertRaises((RuntimeError, ValueError, SystemExit)) as caught:
                        train_adapter.train(None, None, base, revision, None, root / "out.json",
                            winner=False, epochs=1, expected_digests_path=None if fault == "missing_list" else listing,
                            trusted_digest_root_path=None if fault == "missing_root" else trusted, **fx["train_kwargs"])
                    if fault is None:
                        self.assertIs(caught.exception, stop)
                        model.from_pretrained.assert_called_once_with(str(root / "snapshot"), max_seq_length=4096,
                            load_in_4bit=True, use_exact_model_name=True, local_files_only=True)
                        seed.assert_called_once_with(train_adapter.SEED)
                    else:
                        self.assertIsNot(caught.exception, stop)
                        messages = {
                            "missing_root": "--trusted-digest-root is required",
                            "changed_root": "does not match the approved SHA-256",
                            "unapproved_entry": "no unique approved entry",
                            "wrong_revision": "explicit supported student pin",
                            "missing_list": "--expected-digests is required",
                            "changed_list": "upstream digest list does not match",
                            "changed_weights": "size mismatch for model.safetensors",
                            "unknown_student": "explicit supported student pin",
                        }
                        self.assertIn(messages[fault], str(caught.exception))
                        seed.assert_not_called()
                        self.assertEqual(model.mock_calls, [])
                        self.assertEqual(cuda.mock_calls, [])
                    if (root / "out.json").exists():
                        report = json.loads((root / "out.json").read_bytes())
                        self.assertEqual(report["base_resolved_commit"], revision)
                        self.assertFalse(report["operational_completion"])

    def test_export_student_guard_requires_exact_model_revision_pairs(self):
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            self.skipTest("PowerShell is required for the actual export guard")
        script = Path(__file__).resolve().parents[1] / "scripts" / "export-adapter.ps1"
        source = script.read_text(encoding="utf-8")
        start = source.index("if (-not (($BaseRepo -ceq")
        guard = source[start:source.index("$utcStamp = Get-UtcStamp", start)]
        fixture_source = script.with_name("test-export-adapter.ps1").read_text(encoding="utf-8")
        fixture_revision = re.search(r"^    \$rev = '([0-9a-f]{40})'$", fixture_source, re.MULTILINE).group(1)
        self.assertEqual(fixture_revision, train_adapter.WINNER_REVISION)
        cases = [(train_adapter.WINNER_BASE, fixture_revision, True),
                 (train_adapter.FALLBACK_BASE, train_adapter.FALLBACK_REVISION, True),
                 (train_adapter.FALLBACK_BASE, train_adapter.WINNER_REVISION, False),
                 (train_adapter.WINNER_BASE, train_adapter.FALLBACK_REVISION, False),
                 ("Other/Approved-Model", train_adapter.FALLBACK_REVISION, False),
                 ("qwen/Qwen3-8B", train_adapter.FALLBACK_REVISION, False),
                 (train_adapter.FALLBACK_BASE, "", False)]
        for base, revision, permitted in cases:
            with self.subTest(base=base, revision=revision):
                command = ("$ErrorActionPreference = 'Stop'\n"
                           + f"$BaseRepo = '{base}'; $BaseRevision = '{revision}'\n"
                           + guard + "'GUARDS-PASSED'")
                result = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", command],
                    capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode == 0, permitted, result.stdout + result.stderr)
                self.assertEqual("GUARDS-PASSED" in result.stdout, permitted)

    def test_epoch_nonfinite_memory_cannot_complete_either_student(self):
        for student in ((train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION),
                        (train_adapter.FALLBACK_BASE, train_adapter.FALLBACK_REVISION)):
            for peak in (float("nan"), float("inf"), float("-inf"), -1.0):
                with self.subTest(student=student, peak=peak), tempfile.TemporaryDirectory() as tmp:
                    with self.assertRaises(SystemExit):
                        self._maintenance_epoch_run(Path(tmp), Slice3CompleteTrainer, epochs=1,
                            student=student, peak_gib=peak)
                    payload = json.loads((Path(tmp) / "reports" / "maintenance.json").read_bytes())
                    self.assertFalse(payload["operational_completion"])
                    self.assertFalse(payload["export_ready"])
                    self.assertFalse(payload["qualified"])

    def test_authenticated_chain_matches_before_local_weight_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trusted, listing, pin = self._trusted_chain(root)
            with mock.patch.object(train_adapter, "TRUSTED_DIGEST_ROOT_SHA256", pin), \
                 mock.patch.object(train_adapter, "local_snapshot_dir", return_value=root / "snapshot") as snapshot:
                result = train_adapter.digest_verification_before_load(
                    train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION, listing, trusted,
                )
            snapshot.assert_called_once_with(train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION)
            self.assertEqual(result["reads"], 2)
            self.assertEqual(result["trusted_root_sha256"], pin)
            self.assertEqual(result["upstream_digest_list_sha256"], hashlib.sha256(listing.read_bytes()).hexdigest())

    def test_changed_root_refused_before_parse_or_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trusted, listing, pin = self._trusted_chain(root)
            trusted.write_bytes(b"not even JSON")
            with mock.patch.object(train_adapter, "TRUSTED_DIGEST_ROOT_SHA256", pin), \
                 mock.patch.object(train_adapter, "local_snapshot_dir") as snapshot:
                with self.assertRaisesRegex(SystemExit, "root does not match"):
                    train_adapter.digest_verification_before_load(
                        train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION, listing, trusted,
                    )
            snapshot.assert_not_called()

    def test_self_consistent_changed_weights_and_list_cannot_reapprove_themselves(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trusted, listing, pin = self._trusted_chain(root)
            entry = self._digest_entry(root / "snapshot", "model.safetensors", b"changed weights")
            listing.write_bytes(json.dumps({
                f"{train_adapter.WINNER_BASE}@{train_adapter.WINNER_REVISION}": {"files": [entry]},
            }).encode())
            # The old weight-only check would accept this changed, self-consistent pair.
            train_adapter.verify_expected_digests(root / "snapshot", [entry])
            with mock.patch.object(train_adapter, "TRUSTED_DIGEST_ROOT_SHA256", pin), \
                 mock.patch.object(train_adapter, "local_snapshot_dir") as snapshot:
                with self.assertRaisesRegex(SystemExit, "list does not match"):
                    train_adapter.digest_verification_before_load(
                        train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION, listing, trusted,
                    )
            snapshot.assert_not_called()

    def test_changed_list_is_authenticated_before_json_parse(self):
        with tempfile.TemporaryDirectory() as tmp:
            trusted, listing, pin = self._trusted_chain(Path(tmp))
            listing.write_bytes(b"not JSON")
            with mock.patch.object(train_adapter, "TRUSTED_DIGEST_ROOT_SHA256", pin):
                with self.assertRaisesRegex(SystemExit, "list does not match"):
                    train_adapter.load_authenticated_digests(
                        trusted, listing, train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION,
                    )

    def test_approved_root_refuses_wrong_model_revision_and_unapproved_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            trusted, listing, pin = self._trusted_chain(Path(tmp))
            for base, revision in (
                (train_adapter.WINNER_BASE, "0" * 40),
                ("org/another-model", train_adapter.WINNER_REVISION),
                (train_adapter.FALLBACK_BASE, train_adapter.FALLBACK_REVISION),
            ):
                with self.subTest(base=base, revision=revision), \
                     mock.patch.object(train_adapter, "TRUSTED_DIGEST_ROOT_SHA256", pin), \
                     mock.patch.object(train_adapter, "local_snapshot_dir") as snapshot:
                    with self.assertRaisesRegex(SystemExit, "no unique approved entry"):
                        train_adapter.digest_verification_before_load(base, revision, listing, trusted)
                    snapshot.assert_not_called()

    def test_direct_cli_cannot_bypass_root_by_omitting_winner_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            accepted = write_accepted(root, [row()])
            _, listing, _ = self._trusted_chain(root)
            for base, revision in (
                (train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION),
                (train_adapter.FALLBACK_BASE, train_adapter.FALLBACK_REVISION),
            ):
                for mode in (["--steps", "20"], ["--probe"]):
                    with self.subTest(base=base, mode=mode), \
                         mock.patch.object(train_adapter, "FastLanguageModel") as model, \
                         mock.patch.object(train_adapter, "local_snapshot_dir") as snapshot:
                        with self.assertRaisesRegex(SystemExit, "trusted-digest-root is required"):
                            train_adapter.main([
                                "--rows", str(accepted), "--report", str(root / "report-out.json"),
                                "--base", base, "--revision", revision, "--expected-digests", str(listing),
                                *mode,
                            ])
                        snapshot.assert_not_called()
                        model.from_pretrained.assert_not_called()

    def test_wrapper_authenticates_root_and_list_before_gpu_ownership(self):
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            self.skipTest("PowerShell is required to evaluate metadata preflight")
        source = (Path(__file__).resolve().parents[1] / "scripts" / "train-adapter.ps1").read_text(encoding="utf-8")
        start = source.index("$TrustedDigestRoot = ''")
        end = source.index("if (-not [string]::IsNullOrWhiteSpace($ExpectedDigests))", start)
        self.assertLess(end, source.index("$gpuLock ="))
        block = source[start:end]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for mode in ("matching", "matching_8b", "changed_list", "changed_root", "fallback"):
                approved = ((train_adapter.FALLBACK_BASE, train_adapter.FALLBACK_REVISION)
                            if mode == "matching_8b" else (train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION))
                trusted, listing, pin = self._trusted_chain(root, base=approved[0], revision=approved[1])
                fixture_block = block.replace("C:\\llm\\reports\\m2-trusted-digest-manifest-v3.json", str(trusted))
                fixture_block = fixture_block.replace(train_adapter.TRUSTED_DIGEST_ROOT_SHA256, pin)
                if mode == "changed_list":
                    listing.write_bytes(b"changed")
                if mode == "changed_root":
                    trusted.write_bytes(b"changed")
                base = train_adapter.FALLBACK_BASE if mode in ("fallback", "matching_8b") else train_adapter.WINNER_BASE
                revision = train_adapter.FALLBACK_REVISION if mode in ("fallback", "matching_8b") else train_adapter.WINNER_REVISION
                preamble = (
                    "$ErrorActionPreference = 'Stop'\n"
                    f"$WinnerBase = '{train_adapter.WINNER_BASE}'; $FallbackBase = '{train_adapter.FALLBACK_BASE}'\n"
                    f"$Base = '{base}'; $Revision = '{revision}'; $ExpectedDigests = '{listing}'\n"
                )
                result = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", preamble + fixture_block],
                                        capture_output=True, text=True, timeout=30)
                with self.subTest(mode=mode):
                    self.assertEqual(result.returncode == 0, mode in ("matching", "matching_8b"), result.stderr)

    def test_trainer_has_no_pin_override(self):
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                train_adapter.main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertNotIn("trusted-root-sha", Path(train_adapter.__file__).read_text(encoding="utf-8"))

    def test_python_and_wrappers_use_the_same_approved_v3_root_pin_and_defaults(self):
        approved_pin = "37ff31d7e9c11502d6795c93028ba8a617c6d427f2d6b830381f50d9157348af"
        self.assertEqual(train_adapter.TRUSTED_DIGEST_ROOT_SHA256, approved_pin)
        source = (Path(__file__).resolve().parents[1] / "scripts" / "train-adapter.ps1").read_text(encoding="utf-8")
        self.assertIn(f"$TrustedDigestRootSHA256 = '{train_adapter.TRUSTED_DIGEST_ROOT_SHA256}'", source)
        self.assertIn("$TrustedDigestRoot = 'C:\\llm\\reports\\m2-trusted-digest-manifest-v3.json'", source)
        self.assertIn("$ExpectedDigests = Join-Path $HotRoot 'reports\\m2-expected-qwen3-14b-v2.json'", source)
        self.assertIn("$ExpectedDigests = Join-Path $HotRoot 'reports\\m2-expected-qwen3-8b-v2.json'", source)
        scripts = Path(__file__).resolve().parents[1] / "scripts"
        export = (scripts / "export-adapter.ps1").read_text(encoding="utf-8")
        self.assertIn(f"$script:PinnedTrustedDigestManifestSha256 = '{approved_pin}'", export)
        self.assertIn("'reports\\m2-trusted-digest-manifest-v3.json'", export)
        self.assertIn("'reports\\m2-expected-qwen3-14b-v2.json'", export)
        export_fixture = (scripts / "test-export-adapter.ps1").read_text(encoding="utf-8")
        self.assertIn("$rootPath = Join-Path $cRoot 'reports\\m2-trusted-digest-manifest-v3.json'", export_fixture)
        self.assertIn(approved_pin, export_fixture)

    def _complete_trusted_snapshot(self, root: Path) -> tuple[Path, Path, str]:
        trusted, listing, _ = self._trusted_chain(root)
        snapshot = root / "snapshot"
        key = f"{train_adapter.WINNER_BASE}@{train_adapter.WINNER_REVISION}"
        payload = json.loads(listing.read_bytes())
        for name, data in (
            ("config.json", b'{"model_type":"synthetic"}'),
            ("tokenizer.json", b'{"synthetic_tokenizer":true}'),
            ("model.safetensors.index.json", b'{"weight_map":{"layer":"model.safetensors"}}'),
        ):
            payload[key]["files"].append(self._digest_entry(snapshot, name, data))
        listing.write_bytes(json.dumps(payload).encode())
        approved = json.loads(trusted.read_bytes())
        approved["models"][0]["upstream_weight_digests_sha256"] = hashlib.sha256(listing.read_bytes()).hexdigest()
        trusted.write_bytes(json.dumps(approved).encode())
        return trusted, listing, hashlib.sha256(trusted.read_bytes()).hexdigest()

    def test_complete_snapshot_authenticates_metadata_and_reports_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trusted, listing, pin = self._complete_trusted_snapshot(root)
            with mock.patch.object(train_adapter, "TRUSTED_DIGEST_ROOT_SHA256", pin), \
                 mock.patch.object(train_adapter, "local_snapshot_dir", return_value=root / "snapshot"):
                result = train_adapter.digest_verification_before_load(
                    train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION, listing, trusted,
                )
            self.assertEqual(result["snapshot_coverage"], "all_regular_files")
            self.assertEqual(result["snapshot_files"], 4)
            self.assertEqual(result["files"], 4)
            self.assertEqual(result["weight_files"], 1)
            self.assertTrue(result["weight_map_checked"])

    def test_corrupt_snapshot_loading_metadata_refused_before_index_parse_or_model_load(self):
        for name in ("config.json", "tokenizer.json", "model.safetensors.index.json"):
            for mode in (["--steps", "20"], ["--probe"]):
                with self.subTest(name=name, mode=mode), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    accepted = write_accepted(root, [row()])
                    trusted, listing, pin = self._complete_trusted_snapshot(root)
                    snapshot = root / "snapshot"
                    path = snapshot / name
                    path.write_bytes(b"!" * path.stat().st_size)
                    self.assertEqual((snapshot / "model.safetensors").read_bytes(), b"synthetic weights")
                    with mock.patch.object(train_adapter, "TRUSTED_DIGEST_ROOT_SHA256", pin), \
                         mock.patch.object(train_adapter, "local_snapshot_dir", return_value=snapshot), \
                         mock.patch.object(train_adapter, "FastLanguageModel") as model, \
                         mock.patch.object(train_adapter, "_index_weight_map_shards") as parse_index:
                        with self.assertRaisesRegex(SystemExit, "digest mismatch for " + name.replace(".", r"\.")):
                            train_adapter.main([
                                "--rows", str(accepted), "--report", str(root / "metrics.json"),
                                "--base", train_adapter.WINNER_BASE, "--revision", train_adapter.WINNER_REVISION,
                                "--expected-digests", str(listing), "--trusted-digest-root", str(trusted), *mode,
                            ])
                        parse_index.assert_not_called()
                        model.from_pretrained.assert_not_called()

    def test_unlisted_snapshot_input_requires_existing_approved_digest_before_model_load(self):
        for name in ("config.json", "tokenizer.json", "model.safetensors.index.json", "future-format.data"):
            for mode in (["--steps", "20"], ["--probe"]):
                with self.subTest(name=name, mode=mode), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    accepted = write_accepted(root, [row()])
                    trusted, listing, pin = self._trusted_chain(root)
                    snapshot = root / "snapshot"
                    (snapshot / name).write_bytes(b"unapproved synthetic metadata")
                    with mock.patch.object(train_adapter, "TRUSTED_DIGEST_ROOT_SHA256", pin), \
                         mock.patch.object(train_adapter, "local_snapshot_dir", return_value=snapshot), \
                         mock.patch.object(train_adapter, "FastLanguageModel") as model, \
                         mock.patch.object(train_adapter, "_index_weight_map_shards") as parse_index:
                        with self.assertRaisesRegex(SystemExit, "snapshot input lacks an approved digest"):
                            train_adapter.main([
                                "--rows", str(accepted), "--report", str(root / "metrics.json"),
                                "--base", train_adapter.WINNER_BASE, "--revision", train_adapter.WINNER_REVISION,
                                "--expected-digests", str(listing), "--trusted-digest-root", str(trusted), *mode,
                            ])
                        parse_index.assert_not_called()
                        model.from_pretrained.assert_not_called()
                    self.assertEqual((snapshot / "model.safetensors").read_bytes(), b"synthetic weights")

    def test_index_parsed_only_after_all_snapshot_files_pass_both_digest_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trusted, listing, pin = self._complete_trusted_snapshot(root)
            snapshot = root / "snapshot"
            seen = []
            real_hash = train_adapter._sha256_file
            real_parse = train_adapter._index_weight_map_shards

            def record_hash(path):
                seen.append(path.name)
                return real_hash(path)

            def parse_after_hashes(path):
                self.assertEqual(len(seen), 8)
                for name in ("model.safetensors", "config.json", "tokenizer.json", "model.safetensors.index.json"):
                    self.assertEqual(seen.count(name), 2)
                return real_parse(path)

            with mock.patch.object(train_adapter, "TRUSTED_DIGEST_ROOT_SHA256", pin), \
                 mock.patch.object(train_adapter, "local_snapshot_dir", return_value=snapshot), \
                 mock.patch.object(train_adapter, "_sha256_file", side_effect=record_hash), \
                 mock.patch.object(train_adapter, "_index_weight_map_shards", side_effect=parse_after_hashes) as parse:
                train_adapter.digest_verification_before_load(
                    train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION, listing, trusted,
                )
            parse.assert_called_once_with(snapshot)

    def test_authenticated_snapshot_file_links_support_repo_and_shared_blobs(self):
        for layout in ("repo", "shared", "two_hops"):
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                trusted, listing, pin = self._complete_trusted_snapshot(root)
                hub = root / "hub"
                repo = hub / "models--Qwen--Qwen3-14B"
                snapshot = repo / "snapshots" / train_adapter.WINNER_REVISION
                snapshot.parent.mkdir(parents=True)
                (root / "snapshot").rename(snapshot)
                for path in list(snapshot.iterdir()):
                    data = path.read_bytes()
                    digest = hashlib.sha256(data).hexdigest()
                    blobs = repo / "blobs" if layout == "repo" else hub / "blobs" / digest[:2]
                    blobs.mkdir(parents=True, exist_ok=True)
                    target = blobs / digest
                    target.write_bytes(data)
                    if layout == "two_hops":
                        relay = repo / "blobs" / digest
                        relay.parent.mkdir(parents=True, exist_ok=True)
                        if not make_reparse_link(relay, Path(os.path.relpath(target, relay.parent))):
                            self.skipTest("file symlink creation is unavailable")
                        target = relay
                    path.unlink()
                    if not make_reparse_link(path, Path(os.path.relpath(target, path.parent))):
                        self.skipTest("file symlink creation is unavailable")
                with mock.patch.object(train_adapter, "TRUSTED_DIGEST_ROOT_SHA256", pin), \
                     mock.patch.object(train_adapter, "local_snapshot_dir", return_value=snapshot):
                    report = train_adapter.digest_verification_before_load(
                        train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION, listing, trusted)
                    self.assertEqual(report["files"], 4)
                    self.assertEqual(report["snapshot_files"], 4)
                    self.assertEqual(report["reads"], 2)
                    self.assertTrue(report["weight_map_checked"])
                    self.assertEqual(report["base"], train_adapter.WINNER_BASE)
                    self.assertEqual(report["revision"], train_adapter.WINNER_REVISION)
                    # The allowed location grants no byte authenticity.
                    (snapshot / "config.json").write_bytes(b"changed synthetic configuration")
                    with self.assertRaisesRegex(SystemExit, "(size|digest) mismatch"):
                        train_adapter.digest_verification_before_load(
                            train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION, listing, trusted)

    def test_authenticated_cache_links_with_synthetic_file_link_emulation(self):
        # Windows may deny native file symlinks. Emulate only these fixture file
        # links; directory ancestry and all authentication/hash/index code run
        # unchanged against real synthetic files. This is no native cache smoke.
        for layout in ("repo", "shared", "two_hops", "external", "other_repo", "other_revision", "blob_ancestor"):
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                trusted, listing, pin = self._complete_trusted_snapshot(root)
                repo = root / "hub" / ("models--Other--Model" if layout == "other_repo" else "models--Qwen--Qwen3-14B")
                revision = "b" * 40 if layout == "other_revision" else train_adapter.WINNER_REVISION
                snapshot = repo / "snapshots" / revision
                snapshot.parent.mkdir(parents=True)
                (root / "snapshot").rename(snapshot)
                links = {}
                for path in list(snapshot.iterdir()):
                    data = path.read_bytes()
                    digest = hashlib.sha256(data).hexdigest()
                    blobs = repo / "blobs" if layout in ("repo", "other_repo", "other_revision") else root / "hub" / "blobs"
                    if layout == "external":
                        blobs = root / "outside"
                    blobs.mkdir(parents=True, exist_ok=True)
                    target = blobs / digest
                    target.write_bytes(data)
                    if layout == "two_hops":
                        relay = repo / "blobs" / digest
                        relay.parent.mkdir(parents=True, exist_ok=True)
                        relay.touch()
                        links[relay] = Path(os.path.relpath(target, relay.parent))
                        target = relay
                    if layout == "blob_ancestor":
                        alias = root / "hub" / "blobs" / "linked"
                        if not alias.exists() and not make_reparse_link(alias, blobs):
                            self.skipTest("directory link creation is unavailable")
                        target = alias / digest
                    links[path] = Path(os.path.relpath(target, path.parent))

                original_symlink, original_readlink = Path.is_symlink, Path.readlink
                original_stat, original_open = Path.stat, Path.open

                def resolve_file(path):
                    for _ in range(5):
                        if path not in links:
                            return path
                        path = Path(os.path.abspath(path.parent / links[path]))
                    raise OSError("synthetic file-link cycle")

                with mock.patch.object(Path, "is_symlink", lambda p: p in links or original_symlink(p)), \
                     mock.patch.object(Path, "readlink", lambda p: links[p] if p in links else original_readlink(p)), \
                     mock.patch.object(Path, "stat", lambda p, **kw: original_stat(resolve_file(p), **kw)), \
                     mock.patch.object(Path, "open", lambda p, *a, **kw: original_open(resolve_file(p), *a, **kw)), \
                     mock.patch.object(train_adapter, "TRUSTED_DIGEST_ROOT_SHA256", pin), \
                     mock.patch.object(train_adapter, "local_snapshot_dir", return_value=snapshot):
                    if layout in ("repo", "shared", "two_hops"):
                        payload = train_adapter.digest_verification_before_load(
                            train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION, listing, trusted)
                        self.assertEqual(payload["snapshot_files"], 4)
                        self.assertTrue(payload["weight_map_checked"])
                        (snapshot / "config.json").write_bytes(b"bad synthetic blob")
                        with self.assertRaisesRegex(SystemExit, "(size|digest) mismatch"):
                            train_adapter.digest_verification_before_load(
                                train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION, listing, trusted)
                    else:
                        with mock.patch.object(train_adapter, "_sha256_file") as hashed:
                            with self.assertRaisesRegex(SystemExit, "(escapes the expected directory|directory link)"):
                                train_adapter.digest_verification_before_load(
                                    train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION, listing, trusted)
                            hashed.assert_not_called()

    def test_cache_links_refuse_unbound_layout_and_arbitrary_external_files(self):
        for layout in ("external", "other_repo", "other_revision", "copied"):
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                trusted, listing, pin = self._complete_trusted_snapshot(root)
                repo_name = "models--Other--Model" if layout == "other_repo" else "models--Qwen--Qwen3-14B"
                revision = "b" * 40 if layout == "other_revision" else train_adapter.WINNER_REVISION
                repo = root / "hub" / repo_name
                snapshot = root / "copied" if layout == "copied" else repo / "snapshots" / revision
                snapshot.parent.mkdir(parents=True, exist_ok=True)
                (root / "snapshot").rename(snapshot)
                target = root / "outside" if layout == "external" else repo / "blobs" / "synthetic"
                target.parent.mkdir(parents=True, exist_ok=True)
                path = snapshot / "config.json"
                target.write_bytes(path.read_bytes())
                path.unlink()
                if not make_reparse_link(path, target):
                    self.skipTest("file symlink creation is unavailable")
                with mock.patch.object(train_adapter, "TRUSTED_DIGEST_ROOT_SHA256", pin), \
                     mock.patch.object(train_adapter, "local_snapshot_dir", return_value=snapshot), \
                     mock.patch.object(train_adapter, "_sha256_file") as digest:
                    with self.assertRaisesRegex(SystemExit, "escapes the expected directory"):
                        train_adapter.digest_verification_before_load(
                            train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION, listing, trusted)
                    digest.assert_not_called()

    def test_cache_file_links_refuse_directory_ancestors_even_inside_allowed_roots(self):
        for location in ("snapshot", "blob"):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                repo = root / "hub" / "models--Qwen--Qwen3-14B"
                snapshot = repo / "snapshots" / train_adapter.WINNER_REVISION
                snapshot.mkdir(parents=True)
                blobs = repo / "blobs"
                actual = blobs / "actual"
                actual.mkdir(parents=True)
                target = actual / "synthetic"
                target.write_bytes(b"synthetic weights")
                if location == "blob":
                    if not make_reparse_link(blobs / "linked", actual):
                        self.skipTest("directory link creation is unavailable")
                    target = blobs / "linked" / "synthetic"
                    path = snapshot / "model.safetensors"
                    if not make_reparse_link(path, target):
                        self.skipTest("file symlink creation is unavailable")
                    rel = path.name
                else:
                    real_dir = snapshot / "actual"
                    real_dir.mkdir()
                    (real_dir / "model.safetensors").write_bytes(b"synthetic weights")
                    if not make_reparse_link(snapshot / "linked", real_dir):
                        self.skipTest("directory link creation is unavailable")
                    rel = "linked/model.safetensors"
                roots = train_adapter.authenticated_cache_blob_roots(
                    snapshot, train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION)
                with mock.patch.object(train_adapter, "_sha256_file") as digest:
                    with self.assertRaisesRegex(SystemExit, "directory link"):
                        train_adapter.verify_expected_digests(snapshot, [{
                            "path": rel, "sha256": hashlib.sha256(b"synthetic weights").hexdigest(),
                            "size": len(b"synthetic weights"),
                        }], blob_roots=roots)
                    digest.assert_not_called()

    def test_authenticated_snapshot_root_refuses_directory_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actual = root / "actual"
            actual.mkdir()
            snapshot = root / "hub" / "models--Qwen--Qwen3-14B" / "snapshots" / train_adapter.WINNER_REVISION
            snapshot.parent.mkdir(parents=True)
            if not make_reparse_link(snapshot, actual):
                self.skipTest("directory link creation is unavailable")
            with self.assertRaisesRegex(SystemExit, "directory link"):
                train_adapter.authenticated_cache_blob_roots(
                    snapshot, train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION)

    def test_digest_paths_cannot_escape_complete_snapshot_coverage(self):
        for name in ("../escape.bin", "/absolute.bin", "C:/escape.bin", "folder\\escape.bin"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(SystemExit, "confined relative snapshot files"):
                    train_adapter.verify_expected_digests(
                        Path(tmp), [{"path": name, "sha256": "a" * 64, "size": 1}],
                    )

    def test_digest_traversal_refusal_precedes_duplicate_and_digest_checks(self):
        # A clean-looking name that resolves outside must be refused by
        # containment, not by the duplicate, missing-file or digest checks.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            elsewhere = root / "elsewhere"
            elsewhere.mkdir()
            (elsewhere / "outside.bin").write_bytes(b"outside the snapshot")
            if not make_reparse_link(snapshot / "linked", elsewhere):
                self.skipTest("no reparse-point creation is permitted on this host")
            entries = [
                {"path": "linked/outside.bin", "sha256": "a" * 64, "size": 29},
                {"path": "linked/outside.bin", "sha256": "b" * 64, "size": 1},
            ]
            with self.assertRaisesRegex(SystemExit, "escapes the expected directory"):
                train_adapter.verify_expected_digests(snapshot, entries)
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp)
            entry = {"path": "gone/../gone.bin", "sha256": "a" * 64, "size": 1}
            with self.assertRaisesRegex(SystemExit, "confined relative snapshot files"):
                train_adapter.verify_expected_digests(snapshot, [entry])

    def test_digest_index_entries_must_stay_inside_the_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            weights = self._digest_entry(snapshot, "model-00001-of-00001.safetensors", b"synthetic weights")
            index_payload = json.dumps({"weight_map": {"model.layers.0": "../../outside.bin"}})
            (snapshot / "model.safetensors.index.json").write_text(index_payload, encoding="utf-8")
            index_entry = {
                "path": "model.safetensors.index.json",
                "sha256": hashlib.sha256(index_payload.encode()).hexdigest(),
                "size": len(index_payload),
            }
            # The manifest itself is clean; only the index weight_map escapes.
            # The confinement refusal fires instead of the membership check
            # that would otherwise complain about an unlisted shard.
            entries = [weights, index_entry]
            with self.assertRaisesRegex(SystemExit, "digest index paths must be confined"):
                train_adapter.verify_expected_digests(snapshot, entries)

    def test_verify_expected_digests_passes_with_two_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entries = [
                self._digest_entry(root, "a.bin", b"alpha"),
                self._digest_entry(root, "b.bin", b"beta"),
            ]
            result = train_adapter.verify_expected_digests(root, entries, reads=2)
            self.assertEqual(
                result,
                {
                    "files": 2,
                    "reads": 2,
                    "bytes": len(b"alpha") + len(b"beta"),
                    "weight_files": 2,
                    "weight_map_checked": False,
                    "snapshot_files": 2,
                    "snapshot_coverage": "all_regular_files",
                },
            )

    def test_verify_expected_digests_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(SystemExit):
                train_adapter.verify_expected_digests(root, [{"path": "nope.bin", "sha256": "a" * 64, "size": 1}])

    def test_verify_expected_digests_size_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "x.bin").write_bytes(b"xy")
            with self.assertRaises(SystemExit):
                train_adapter.verify_expected_digests(
                    root, [{"path": "x.bin", "sha256": hashlib.sha256(b"xy").hexdigest(), "size": 99}],
                )

    def test_verify_expected_digests_digest_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "x.bin").write_bytes(b"xy")
            with self.assertRaises(SystemExit):
                train_adapter.verify_expected_digests(
                    root, [{"path": "x.bin", "sha256": "b" * 64, "size": 2}],
                )

    def test_verify_expected_digests_index_shard_missing_from_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shard_a = "model-00001-of-00002.safetensors"
            shard_b = "model-00002-of-00002.safetensors"
            (root / shard_a).write_bytes(b"only-a")
            (root / "model.safetensors.index.json").write_text(
                json.dumps({"weight_map": {"layer.0": shard_a, "layer.1": shard_b}}),
                encoding="utf-8",
            )
            entry = self._digest_entry(root, shard_a, b"only-a")
            index = root / "model.safetensors.index.json"
            index_entry = {"path": index.name, "sha256": hashlib.sha256(index.read_bytes()).hexdigest(),
                           "size": index.stat().st_size}
            with self.assertRaises(SystemExit) as raised:
                train_adapter.verify_expected_digests(root, [entry, index_entry])
            self.assertIn(shard_b, str(raised.exception))

    def test_verify_expected_digests_uncovered_weight_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "extra.safetensors").write_bytes(b"shard")
            entry = self._digest_entry(root, "listed.bin", b"listed")
            with self.assertRaises(SystemExit) as raised:
                train_adapter.verify_expected_digests(root, [entry])
            self.assertIn("weight file not covered", str(raised.exception))

    def test_verify_expected_digests_duplicate_manifest_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entry = self._digest_entry(root, "w.bin", b"w")
            with self.assertRaises(SystemExit) as raised:
                train_adapter.verify_expected_digests(root, [entry, dict(entry)])
            self.assertIn("duplicate path", str(raised.exception))

    def test_verify_expected_digests_requires_manifest_weight_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.json").write_text("{}", encoding="utf-8")
            entry = {
                "path": "config.json",
                "sha256": hashlib.sha256(b"{}").hexdigest(),
                "size": 2,
            }
            with self.assertRaises(SystemExit) as raised:
                train_adapter.verify_expected_digests(root, [entry])
            self.assertIn("no weight file", str(raised.exception))

    def test_verify_expected_digests_second_read_differs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = b"stable"
            entry = self._digest_entry(root, "stable.bin", data)
            calls = 0
            real = train_adapter._sha256_file

            def flaky(path: Path) -> str:
                nonlocal calls
                calls += 1
                if calls == 2:
                    return "c" * 64
                return real(path)

            with mock.patch.object(train_adapter, "_sha256_file", side_effect=flaky):
                with self.assertRaises(SystemExit) as raised:
                    train_adapter.verify_expected_digests(root, [entry], reads=2)
                self.assertIn("read 2", str(raised.exception))

    def test_load_expected_digests_returns_matching_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "digests.json"
            key = f"{train_adapter.WINNER_BASE}@{train_adapter.WINNER_REVISION}"
            files = [{"path": "config.json", "sha256": "a" * 64, "size": 3}]
            path.write_text(json.dumps({key: {"files": files}}), encoding="utf-8")
            loaded = train_adapter.load_expected_digests(path, train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION)
            self.assertEqual(loaded, files)

    def test_load_expected_digests_absent_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "digests.json"
            path.write_text("{}", encoding="utf-8")
            with self.assertRaises(SystemExit):
                train_adapter.load_expected_digests(path, train_adapter.SMOKE_BASE, train_adapter.SMOKE_REVISION)

    def test_main_probe_smoke_pin_calls_run_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_accepted(Path(tmp), [row()])
            with mock.patch.object(train_adapter, "run_probe", return_value={"mode": "d_m1_f_probe"}) as mocked:
                train_adapter.main([
                    "--rows", str(path),
                    "--report", str(Path(tmp, "probe.json")),
                    "--probe",
                    "--base", train_adapter.SMOKE_BASE,
                    "--revision", train_adapter.SMOKE_REVISION,
                ])
            mocked.assert_called_once()

    def test_main_probe_arbitrary_base_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_accepted(Path(tmp), [row()])
            with self.assertRaises(SystemExit):
                train_adapter.main([
                    "--rows", str(path),
                    "--report", str(Path(tmp, "probe.json")),
                    "--probe",
                    "--base", "org/other-model",
                    "--revision", "a" * 40,
                ])

    def test_main_winner_without_expected_digests_refuses_before_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_accepted(Path(tmp), [row()])
            fake_flm = mock.MagicMock()
            fake_flm.from_pretrained = mock.MagicMock()
            with mock.patch.object(train_adapter, "FastLanguageModel", fake_flm):
                with self.assertRaises(SystemExit):
                    train_adapter.main([
                        "--rows", str(path),
                        "--report", str(Path(tmp, "out.json")),
                        "--winner",
                        "--base", train_adapter.WINNER_BASE,
                        "--revision", train_adapter.WINNER_REVISION,
                    ])
            fake_flm.from_pretrained.assert_not_called()

    def test_probe_pins_accepted_by_guard(self):
        self.assertTrue(train_adapter.is_allowed_probe_pin(
            train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION))
        self.assertTrue(train_adapter.is_allowed_probe_pin(
            train_adapter.FALLBACK_BASE, train_adapter.FALLBACK_REVISION))
        self.assertTrue(train_adapter.is_allowed_probe_pin(
            train_adapter.SMOKE_BASE, train_adapter.SMOKE_REVISION))
        self.assertFalse(train_adapter.is_allowed_probe_pin("org/other-model", "a" * 40))

    def test_length_gate_pass_boundary(self):
        self.assertTrue(train_adapter.length_gate_pass(2048.0))
        self.assertFalse(train_adapter.length_gate_pass(2048.001))

    def test_fallback_pin_without_expected_digests_refused_before_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_accepted(Path(tmp), [row()])
            fake_flm = mock.MagicMock()
            fake_flm.from_pretrained = mock.MagicMock()
            with mock.patch.object(train_adapter, "FastLanguageModel", fake_flm):
                with self.assertRaises(SystemExit):
                    train_adapter.main([
                        "--rows", str(path),
                        "--report", str(Path(tmp, "probe.json")),
                        "--probe",
                        "--base", train_adapter.FALLBACK_BASE,
                        "--revision", train_adapter.FALLBACK_REVISION,
                    ])
            fake_flm.from_pretrained.assert_not_called()

    def test_main_probe_fallback_pin_calls_run_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_accepted(Path(tmp), [row()])
            digest_path = Path(tmp) / "digests.json"
            key = f"{train_adapter.FALLBACK_BASE}@{train_adapter.FALLBACK_REVISION}"
            digest_path.write_text(json.dumps({key: {"files": []}}), encoding="utf-8")
            with mock.patch.object(train_adapter, "run_probe", return_value={"pin": "fallback"}) as mocked:
                with mock.patch.object(train_adapter, "load_expected_digests", return_value=[]):
                    with mock.patch.object(train_adapter, "digest_verification_before_load", return_value=None):
                        train_adapter.main([
                            "--rows", str(path),
                            "--report", str(Path(tmp, "probe.json")),
                            "--probe",
                            "--base", train_adapter.FALLBACK_BASE,
                            "--revision", train_adapter.FALLBACK_REVISION,
                            "--expected-digests", str(digest_path),
                        ])
            mocked.assert_called_once()

    def test_probe_length_gate_failure_skips_microsteps(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows_path = write_accepted(Path(tmp), [row()])
            report_path = Path(tmp) / "probe.json"
            digest_path = Path(tmp) / "digests.json"
            key = f"{train_adapter.WINNER_BASE}@{train_adapter.WINNER_REVISION}"
            digest_path.write_text(json.dumps({key: {"files": []}}), encoding="utf-8")
            fake_tokenizer = mock.MagicMock()
            fake_tokenizer.pad_token_id = 0
            fake_tokenizer.apply_chat_template = mock.MagicMock(return_value=[1, 2, 3])
            fake_model = mock.MagicMock()
            fake_model.config._commit_hash = train_adapter.WINNER_REVISION
            fake_flm = mock.MagicMock()
            fake_flm.from_pretrained = mock.MagicMock(return_value=(fake_model, fake_tokenizer))
            fake_flm.get_peft_model = mock.MagicMock(return_value=fake_model)
            bad_stats = {"p50": 100.0, "p95": 2048.001, "max": 3000}
            encoded = [{
                "input_ids": [1, 2, 3],
                "attention_mask": [1, 1, 1],
                "labels": [-100, -100, 3],
            }]
            with mock.patch.object(train_adapter, "FastLanguageModel", fake_flm):
                with mock.patch.object(train_adapter, "digest_verification_before_load", return_value=None):
                    with mock.patch.object(train_adapter, "encode_rows", return_value=encoded):
                        with mock.patch.object(train_adapter, "length_statistics", return_value=bad_stats):
                            with mock.patch.object(train_adapter, "run_probe_microsteps") as micro_mock:
                                with self.assertRaises(SystemExit) as raised:
                                    train_adapter.main([
                                        "--rows", str(rows_path),
                                        "--report", str(report_path),
                                        "--probe",
                                        "--base", train_adapter.WINNER_BASE,
                                        "--revision", train_adapter.WINNER_REVISION,
                                        "--expected-digests", str(digest_path),
                                    ])
            self.assertIn("length gate failed", str(raised.exception))
            micro_mock.assert_not_called()
            self.assertTrue(report_path.is_file())
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertFalse(payload["length_gate_pass"])
            self.assertIsNone(payload["memory_pass"])
            self.assertIsNone(payload["seconds_per_microstep"])
            self.assertIsNone(payload["peak_reserved_vram_gib"])
            self.assertEqual(payload["warmup_discarded_microsteps"], 0)
            self.assertFalse(payload["probe_executed"])
            self.assertEqual(payload["warmup_pending_gradients"], "not_run")

    def test_probe_mode_and_authoritative_matrix(self):
        def report_for(base: str, revision: str, seq_len: int) -> dict:
            pin = train_adapter.probe_pin_name(base, revision)
            authoritative = train_adapter.probe_is_authoritative(base, revision, seq_len, 1, 1)
            mode = "d_m1_f_probe" if authoritative else "diagnostic_probe"
            kwargs = self._minimal_probe_report_kwargs()
            kwargs.update({
                "pin": pin,
                "mode": mode,
                "base": base,
                "revision": revision,
                "sequence_length": seq_len,
                "authoritative": authoritative,
            })
            return train_adapter.build_probe_report(**kwargs)

        winner_2048 = report_for(
            train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION, 2048)
        self.assertEqual(winner_2048["mode"], "d_m1_f_probe")
        self.assertTrue(winner_2048["authoritative"])
        self.assertEqual(winner_2048["pin"], "winner")

        winner_1024 = report_for(
            train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION, 1024)
        self.assertEqual(winner_1024["mode"], "diagnostic_probe")
        self.assertFalse(winner_1024["authoritative"])

        smoke_2048 = report_for(
            train_adapter.SMOKE_BASE, train_adapter.SMOKE_REVISION, 2048)
        self.assertEqual(smoke_2048["mode"], "diagnostic_probe")
        self.assertFalse(smoke_2048["authoritative"])
        self.assertEqual(smoke_2048["pin"], "smoke")

        fallback_2048 = report_for(
            train_adapter.FALLBACK_BASE, train_adapter.FALLBACK_REVISION, 2048)
        self.assertEqual(fallback_2048["mode"], "d_m1_f_probe")
        self.assertTrue(fallback_2048["authoritative"])
        self.assertEqual(fallback_2048["pin"], "fallback")

    def test_main_probe_seq_len_above_seq_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_accepted(Path(tmp), [row()])
            with self.assertRaises(SystemExit):
                train_adapter.main([
                    "--rows", str(path),
                    "--report", str(Path(tmp, "probe.json")),
                    "--probe",
                    "--base", train_adapter.SMOKE_BASE,
                    "--revision", train_adapter.SMOKE_REVISION,
                    "--probe-seq-len", "4096",
                ])

    def test_wrapper_fallback_switch_and_probe_timeout(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "train-adapter.ps1"
        text = script.read_text(encoding="utf-8")
        self.assertIn("-Fallback", text)
        self.assertIn("mutually exclusive", text)
        self.assertIn("-Fallback is only valid together with -Probe", text)
        self.assertIn("m2-expected-qwen3-8b-v2.json", text)
        self.assertIn("ContainsKey('TimeoutMinutes')", text)
        self.assertIn("$TimeoutMinutes = 150", text)

    def test_wrapper_winner_requires_probe_and_epoch_switch_guards(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "train-adapter.ps1"
        text = script.read_text(encoding="utf-8")
        self.assertIn("-Winner is only valid together with -Probe", text)
        self.assertIn("-Rows <jsonl> -Winner -Probe", text)
        self.assertIn("-Rows <jsonl> -Batch <published-batch-id> -Epochs <N> -Base 'Qwen/Qwen3-14B'", text)
        self.assertIn("-TimeoutMinutes <explicit-approved-bound>", text)
        self.assertIn("'-Epochs must be between 1 and 6'", text)
        self.assertIn("'-Epochs and -Steps are mutually exclusive; pass exactly one'", text)
        self.assertIn("ContainsKey('Epochs')", text)
        self.assertIn("ContainsKey('Steps')", text)
        # Mount protections are exercised by the composed argument tests above.

    def _minimal_probe_report_kwargs(self) -> dict:
        return {
            "run": "probe",
            "pin": "smoke",
            "mode": "diagnostic_probe",
            "base": train_adapter.SMOKE_BASE,
            "revision": train_adapter.SMOKE_REVISION,
            "rows": 1,
            "mask_coverage": 0.5,
            "resolved_commit": train_adapter.SMOKE_REVISION,
            "length_stats": {"p50": 1.0, "p95": 2.0, "max": 3},
            "length_gate_passed": True,
            "sequence_length": 2048,
            "sequence_length_method": "attended_prompt_repeat",
            "warmup_microsteps": 1,
            "timed_microsteps": 1,
            "gradient_accumulation": 1,
            "timed_optimizer_updates": 1,
            "seconds_per_microstep": 1.0,
            "peak_reserved_gib": 1.0,
            "rows_total": 96,
            "wall_seconds_three_epochs": 1.0,
            "wall_seconds_three_epochs_contingency": 1.2,
            "memory_pass": True,
            "time_pass": True,
            "authoritative": False,
            "overlength_rows": 0,
            "max_tokens": 3,
        }

    def test_train_refuses_overlength_row_before_trainer(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows_path = write_accepted(Path(tmp), [row()])
            report_path = Path(tmp) / "out.json"
            fake_tokenizer = mock.MagicMock()
            fake_tokenizer.apply_chat_template = mock.MagicMock(return_value=list(range(10)))
            fake_model = mock.MagicMock()
            fake_model.config._commit_hash = train_adapter.SMOKE_REVISION
            fake_flm = mock.MagicMock()
            fake_flm.from_pretrained = mock.MagicMock(return_value=(fake_model, fake_tokenizer))
            fake_flm.get_peft_model = mock.MagicMock(return_value=fake_model)
            long_encoded = [{
                "input_ids": list(range(train_adapter.SEQ + 5)),
                "attention_mask": [1] * (train_adapter.SEQ + 5),
                "labels": [-100] * train_adapter.SEQ + list(range(5)),
            }]
            cuda = mock.MagicMock()
            cuda.reset_peak_memory_stats = mock.MagicMock()
            with mock.patch.object(train_adapter.torch, "cuda", cuda, create=True):
                with mock.patch.object(train_adapter, "FastLanguageModel", fake_flm):
                    with mock.patch.object(train_adapter, "digest_verification_before_load", return_value=None):
                        with mock.patch.object(train_adapter, "encode_rows", return_value=long_encoded):
                            with mock.patch.object(train_adapter, "SFTTrainer") as trainer_mock:
                                with self.assertRaises(SystemExit) as raised:
                                    train_adapter.train(
                                        [row()],
                                        [],
                                        train_adapter.SMOKE_BASE,
                                        train_adapter.SMOKE_REVISION,
                                        20,
                                        report_path,
                                        winner=False,
                                    )
            self.assertIn("exceed SEQ", str(raised.exception))
            trainer_mock.assert_not_called()

    def test_epoch_train_creates_leaves_and_a_truthful_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = self._maintenance_epoch_run(root, Slice3CompleteTrainer, epochs=3)
            binding = json.loads((root / "publication-binding.json").read_bytes())
            self.assertEqual(report["publication"], {
                "batch": binding["batch"], "profile_sha256": binding["resolution"]["profile_sha256"],
                "root_sha256": binding["root_sha256"],
            })
            self.assertEqual(report["completed_epoch_ids"], [1, 2, 3])
            self.assertTrue(report["operational_completion"])
            self.assertFalse(report["qualified"])
            self.assertFalse(report["export_ready"])
            self.assertIsNone(report["final_adapter"])
            self.assertEqual(report["selected_adapter"]["dir"], str(root / "models" / "maintenance" / "epoch-01"))
            self.assertEqual(report["dev_selection_status"], "selected_on_frozen_dev")
            for entry in report["checkpoints"]:
                self.assertRegex(entry["manifest_sha256"], r"^[0-9a-f]{64}$")
                self._assert_exporter_adapter_hashes(Path(entry["dir"]))
            self.assertEqual(report["dev_cadence"]["evaluations"][0]["kind"], "midpoint")
            self.assertEqual([e["kind"] for e in report["dev_cadence"]["evaluations"]].count("epoch_end"), 3)

    def test_epoch_small_cohorts_refused_before_seed_cuda_model_or_trainer(self):
        self.assertEqual(train_adapter.TRAIN_GRAD_ACCUM, 8)
        for count in range(9):
            with self.subTest(rows=count), tempfile.TemporaryDirectory() as tmp, \
                 mock.patch.dict(os.environ, {"ADAPTER_MODELS_DIR": str(Path(tmp) / "models")}), \
                 mock.patch.object(train_adapter, "set_seed") as seed, \
                 mock.patch.object(train_adapter.torch, "cuda", mock.Mock(), create=True) as cuda, \
                 mock.patch.object(train_adapter, "FastLanguageModel") as model, \
                 mock.patch.object(train_adapter, "SFTTrainer") as trainer, \
                 mock.patch.object(train_adapter, "digest_verification_before_load") as digests:
                path = Path(tmp) / "report.json"
                rows, dev_rows = slice3_train_rows(count), slice3_dev_rows()
                fx = _build_publication_fixture(Path(tmp), rows=rows + dev_rows)
                with self.assertRaisesRegex(ValueError, "interior first-epoch update"):
                    train_adapter.train(rows, dev_rows,
                        train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION, None,
                        path, winner=False, epochs=1, **fx["train_kwargs"])
                seed.assert_not_called()
                self.assertEqual(cuda.mock_calls, [])
                self.assertEqual(model.mock_calls, [])
                trainer.assert_not_called()
                digests.assert_not_called()
                report = json.loads(path.read_bytes())
                self.assertEqual(report["rows"], count)
                self.assertEqual(report["state"], "failed")
                self.assertIsNone(report["peak_reserved_vram_gib"])
                self.assertFalse(report["operational_completion"])
                self.assertFalse(report["qualified"])
                self.assertFalse(report["export_ready"])
                self.assertIsNone(report["selected_adapter"])

    def test_epoch_nine_row_boundary_has_interior_optimizer_update(self):
        class NineRowTrainer(Slice3CompleteTrainer):
            def train(self):
                self.state.epoch, self.state.global_step = 1.0, 2
                self.callback.on_epoch_end(None, self.state, None)
                return types.SimpleNamespace(training_loss=1.25)

        step_end = train_adapter.DevEvaluationCallback.on_step_end
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(train_adapter.DevEvaluationCallback, "on_step_end",
                               autospec=True, side_effect=step_end) as midpoint:
            report = self._maintenance_epoch_run(Path(tmp), NineRowTrainer, epochs=1, row_count=9)
            self.assertEqual(midpoint.call_count, 1)
            progress = midpoint.call_args.args[2]
            self.assertEqual(progress.global_step, 1)
            self.assertEqual(progress.epoch, 8 / 9)
            self.assertEqual(report["rows"], 9)
            self.assertEqual(report["completed_epoch_ids"], [1])
            self.assertEqual(report["checkpoints"][0]["global_step"], 2)
            self.assertEqual([e["kind"] for e in report["dev_cadence"]["evaluations"]],
                             ["midpoint", "epoch_end"])
            self.assertTrue(report["operational_completion"])
            self.assertFalse(report["qualified"])
            self.assertFalse(report["export_ready"])

    def test_single_row_legacy_steps_still_train(self):
        model = mock.Mock(config=types.SimpleNamespace(_commit_hash=train_adapter.SMOKE_REVISION))
        encoded = [{"input_ids": [1, 2], "attention_mask": [1, 1], "labels": [-100, 2]}]
        trainer = mock.Mock(train=mock.Mock(return_value=types.SimpleNamespace(training_loss=1.25)))
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(train_adapter, "digest_verification_before_load", return_value=None), \
             mock.patch.object(train_adapter, "FastLanguageModel", mock.Mock(
                 from_pretrained=mock.Mock(return_value=(model, mock.Mock())), get_peft_model=mock.Mock(return_value=model))), \
             mock.patch.object(train_adapter.torch, "cuda", mock.Mock(max_memory_reserved=mock.Mock(return_value=2**30)), create=True), \
             mock.patch.object(train_adapter, "encode_rows", return_value=encoded), \
             mock.patch.object(train_adapter, "Dataset", mock.Mock(from_list=mock.Mock(side_effect=list))), \
             mock.patch.object(train_adapter, "SFTConfig", side_effect=lambda **kw: types.SimpleNamespace(**kw)), \
             mock.patch.object(train_adapter, "SFTTrainer", return_value=trainer) as factory:
            report = train_adapter.train([row()], [], train_adapter.SMOKE_BASE, train_adapter.SMOKE_REVISION,
                                         20, Path(tmp) / "report.json", winner=False)
            trainer.train.assert_called_once_with()
            self.assertEqual(factory.call_args.kwargs["callbacks"], [])
            self.assertEqual(len(factory.call_args.kwargs["train_dataset"]), 1)
            self.assertEqual(report["rows"], 1)
            self.assertEqual(report["steps"], 20)
            self.assertEqual(report["peak_reserved_vram_gib"], 1.0)
            self.assertNotIn("requested_epochs", report)



    def _maintenance_epoch_run(
        self, root: Path, trainer_type, epochs: int, *, peak_gib: float = 1.0, winner: bool = False, scores=None,
        row_count: int = 64, cuda=None, use_gradient_checkpointing="unsloth", flm=None, context_tokenizer=None,
        student=None,
    ):
        import predict_dev
        rows, dev_rows = slice3_train_rows(row_count), slice3_dev_rows()
        fx = _build_publication_fixture(root, rows=rows + dev_rows)
        # Epoch mode uses an explicit supported student even in offline fixtures.
        base, revision = student or (train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION)
        model = mock.Mock(config=types.SimpleNamespace(_commit_hash=revision))
        def save(directory):
            Path(directory, "adapter_config.json").write_bytes(b'{"r":16}')
            Path(directory, "adapter_model.safetensors").write_bytes(b"synthetic adapter")
        model.save_pretrained.side_effect = save
        tokenizer = context_tokenizer if context_tokenizer is not None else mock.Mock()
        if context_tokenizer is None:
            tokenizer.apply_chat_template.return_value = [1, 2]
        encoded = [{"input_ids": [1, 2], "attention_mask": [1, 1], "labels": [-100, 2]}
                   for _ in rows]
        if cuda is None:
            cuda = mock.Mock(max_memory_reserved=mock.Mock(return_value=peak_gib * 2**30))
        report_path = root / "reports" / "maintenance.json"

        def offline_trainer(**kwargs):
            self.assertEqual(len(kwargs["train_dataset"]), len(rows))
            self.assertEqual(kwargs["args"].per_device_train_batch_size, 1)
            self.assertEqual(kwargs["args"].gradient_accumulation_steps, 8)
            callback = kwargs["callbacks"][0]
            control = types.SimpleNamespace(should_training_stop=False)
            class Events:
                def __getattr__(self, name):
                    return getattr(callback, name)
                def on_epoch_end(self, args, state, supplied_control=None, **event_kwargs):
                    return callback.on_epoch_end(args, state, supplied_control or control,
                        processing_class=tokenizer, **event_kwargs)
            kwargs["callbacks"] = [Events()]
            inner = trainer_type(**kwargs)
            def run():
                # Synthetic optimizer-update progress at fixed batch 1/accum 8;
                # this is not a claim about the pinned HF runtime.
                midpoint_step = (len(rows) + 15) // 16
                progress = min(midpoint_step * 8, len(rows)) / len(rows)
                callback.on_step_end(None, types.SimpleNamespace(epoch=progress, global_step=midpoint_step), control,
                                     processing_class=tokenizer)
                result = inner.train()
                callback.on_train_end(None, getattr(inner, "state", None), control,
                                      processing_class=tokenizer)
                return result
            return types.SimpleNamespace(train=run, state=getattr(inner, "state", None))

        fixture_template = (fx["code_dir"] / "prompt_template.py").read_bytes()
        with contextlib.ExitStack() as stack:
            if scores is not None:
                import dev_selection
                stack.enter_context(mock.patch.object(dev_selection, "evaluate_primary_score", side_effect=scores))
            for patch in (
                mock.patch.dict(os.environ, {"ADAPTER_MODELS_DIR": str(root / "models"),
                                             "ADAPTER_MODELS_HOST_DIR": str(root / "models")}),
                mock.patch.object(train_adapter, "FastLanguageModel", flm or mock.Mock(
                    from_pretrained=mock.Mock(return_value=(model, tokenizer)),
                    get_peft_model=mock.Mock(return_value=model))),
                mock.patch.object(train_adapter.torch, "cuda", cuda, create=True),
                mock.patch.object(train_adapter, "Dataset", mock.Mock(from_list=mock.Mock(side_effect=list))),
                mock.patch.object(train_adapter, "encode_rows", return_value=encoded),
                mock.patch.object(train_adapter, "digest_verification_before_load",
                                  return_value={"upstream_digest_list_sha256": "b" * 64}),
                mock.patch.object(predict_dev, "_default_template_source", return_value=fixture_template),
                mock.patch.object(predict_dev, "run_predictions", side_effect=slice3_predictions),
                mock.patch.object(train_adapter, "SFTConfig", lambda **kw: types.SimpleNamespace(**kw)),
                mock.patch.object(train_adapter, "SFTTrainer", offline_trainer),
            ):
                stack.enter_context(patch)
            return train_adapter.train(rows, dev_rows, base, revision, None,
                                       report_path, winner=winner, epochs=epochs,
                                       use_gradient_checkpointing=use_gradient_checkpointing,
                                       **fx["train_kwargs"])

    def test_train_shares_configured_checkpointing_with_peft_and_callback(self):
        for mode in ("unsloth", True, False):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                model = mock.Mock(config=types.SimpleNamespace(_commit_hash=train_adapter.WINNER_REVISION))
                def save(directory):
                    Path(directory, "adapter_config.json").write_bytes(b'{"r":16}')
                    Path(directory, "adapter_model.safetensors").write_bytes(b"synthetic adapter")
                model.save_pretrained.side_effect = save
                model.is_gradient_checkpointing = bool(mode)
                def inference(model):
                    model.is_gradient_checkpointing = False
                tokenizer = ContextTokenizer()
                flm = mock.Mock(from_pretrained=mock.Mock(return_value=(model, tokenizer)),
                                get_peft_model=mock.Mock(return_value=model),
                                for_inference=mock.Mock(side_effect=inference))
                report = self._maintenance_epoch_run(Path(tmp), Slice3CompleteTrainer, epochs=1,
                    use_gradient_checkpointing=mode, flm=flm, context_tokenizer=tokenizer)
                self.assertEqual(flm.get_peft_model.call_args.kwargs["use_gradient_checkpointing"], mode)
                self.assertEqual(flm.for_inference.call_args_list, [mock.call(model)] * 2)
                self.assertEqual(flm.for_training.call_args_list,
                                 [mock.call(model, use_gradient_checkpointing=mode)] * 2)
                self.assertTrue(report["operational_completion"])

    def test_completed_epoch_memory_admission_precedes_ready_handoff(self):
        class CompleteTrainer:
            def __init__(self, **kwargs):
                self.callback = kwargs["callbacks"][0]
                self.state = types.SimpleNamespace(epoch=0.0, global_step=0)

            def train(self):
                for epoch in (1, 2):
                    self.state.epoch, self.state.global_step = float(epoch), epoch * 8
                    self.callback.on_epoch_end(None, self.state, None)
                return types.SimpleNamespace(training_loss=1.25)

        for student, peak_gib, admitted, message in (
            (student, peak, admitted, message)
            for student in ((train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION),
                            (train_adapter.FALLBACK_BASE, train_adapter.FALLBACK_REVISION))
            for peak, admitted, message in ((12.001, False, "exceeds the 12 GiB card"),
                                            (11.001, False, "strictly below 11.0"),
                                            (11.0, False, "strictly below 11.0"),
                                            (10.999, True, None))
        ):
            with self.subTest(student=student, peak=peak_gib), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                if admitted:
                    returned = self._maintenance_epoch_run(
                        root, CompleteTrainer, epochs=2, peak_gib=peak_gib, student=student)
                    self.assertTrue(returned["checkpoint_artifacts_ready"])
                    self.assertFalse(returned["export_ready"])
                else:
                    with self.assertRaisesRegex(SystemExit, message):
                        self._maintenance_epoch_run(
                            root, CompleteTrainer, epochs=2, peak_gib=peak_gib, student=student)
                payload = json.loads((root / "reports" / "maintenance.json").read_bytes())
                self.assertEqual(payload["peak_reserved_vram_gib"], peak_gib)
                self.assertEqual(payload["completed_epoch_ids"], [1, 2])
                self.assertEqual(payload["completed_epochs"], 2)
                self.assertEqual(payload["trainer_epoch"], 2.0)
                self.assertEqual(payload["checkpoint_artifacts_ready"], admitted)
                self.assertEqual(payload["artifacts_complete"], admitted)
                self.assertFalse(payload["export_ready"])
                self.assertFalse(payload["qualified"])
                self.assertEqual((payload["base"], payload["base_revision"]), student)
                if admitted:
                    self.assertIsNotNone(payload["selected_adapter"])
                else:
                    self.assertIsNone(payload["selected_adapter"])
                self.assertIsNone(payload["final_adapter"])
                self.assertEqual(payload["last_epoch_adapter"]["dir"], str(root / "models" / "maintenance" / "epoch-02"))
                if not admitted:
                    self.assertIsNone(payload["loss"])
                for epoch in (1, 2):
                    leaf = root / "models" / "maintenance" / f"epoch-{epoch:02d}"
                    self.assertTrue((leaf / "adapter-hashes.json").is_file())
                    self.assertTrue((leaf / "adapter_model.safetensors").is_file())

    def test_epoch_memory_refusal_preserves_original_exception(self):
        class CompleteTrainer:
            def __init__(self, **kwargs):
                self.callback = kwargs["callbacks"][0]
                self.state = types.SimpleNamespace(epoch=1.0, global_step=8)

            def train(self):
                self.callback.on_epoch_end(None, self.state, None)
                return types.SimpleNamespace(training_loss=1.25)

        refusal = SystemExit("original memory refusal")
        cuda = mock.Mock(max_memory_reserved=mock.Mock(
            side_effect=[12.001 * 2**30, RuntimeError("later telemetry failed")]))
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(train_adapter, "require_vram", side_effect=refusal) as gate:
            root = Path(tmp)
            with self.assertRaises(SystemExit) as caught:
                self._maintenance_epoch_run(root, CompleteTrainer, epochs=1, cuda=cuda)
            self.assertIs(caught.exception, refusal)
            self.assertEqual(cuda.max_memory_reserved.call_count, 2)
            gate.assert_called_once_with(12.001, winner=False)
            payload = json.loads((root / "reports" / "maintenance.json").read_bytes())
            self.assertEqual(payload["peak_reserved_vram_gib"], 12.001)
            self.assertFalse(payload["export_ready"])
            self.assertIsNone(payload["final_adapter"])

    def test_complete_epoch_artifacts_selected_on_frozen_dev_still_not_exportable(self):
        class CompleteTrainer:
            def __init__(self, **kwargs):
                self.callback = kwargs["callbacks"][0]
                self.state = types.SimpleNamespace(epoch=0.0, global_step=0)

            def train(self):
                for epoch in (1, 2):
                    self.state.epoch, self.state.global_step = float(epoch), epoch * 8
                    self.callback.on_epoch_end(None, self.state, None)
                return types.SimpleNamespace(training_loss=1.25)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = self._maintenance_epoch_run(root, CompleteTrainer, epochs=2)
            self.assertEqual(report["requested_epochs"], 2)
            self.assertEqual(report["completed_epoch_ids"], [1, 2])
            self.assertTrue(report["checkpoint_artifacts_ready"])
            self.assertTrue(report["artifacts_complete"])
            self.assertEqual(report["last_epoch_adapter"]["epoch"], 2)
            self.assertRegex(report["last_epoch_adapter"]["manifest_sha256"], r"^[0-9a-f]{64}$")
            self.assertIsNotNone(report["selected_adapter"])
            self.assertIsNone(report["final_adapter"])
            self.assertFalse(report["export_ready"])
            self.assertEqual(report["dev_selection_status"], "selected_on_frozen_dev")
            self.assertTrue(report["operational_completion"])
            self.assertFalse(report["qualified"])
            persisted = json.loads((root / "reports" / "maintenance.json").read_bytes())
            self.assertEqual(persisted, report)

    def test_epoch_report_binds_mount_relative_identifiers_to_host_root(self):
        class CompleteTrainer:
            def __init__(self, **kwargs):
                self.callback = kwargs["callbacks"][0]
                self.state = types.SimpleNamespace(epoch=1.0, global_step=8)

            def train(self):
                self.callback.on_epoch_end(None, self.state, None)
                return types.SimpleNamespace(training_loss=1.25)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = self._maintenance_epoch_run(root, CompleteTrainer, epochs=1)
            self.assertEqual(report["requested_epochs"], 1)
            self.assertEqual(report["adapter_mount"], {"container_root": str(root / "models"),
                                                       "host_root": str(root / "models")})
            self.assertEqual(report["output_mount_relative_dir"], "maintenance")
            self.assertEqual(report["last_epoch_adapter"]["mount_relative_dir"], "maintenance/epoch-01")
            self.assertEqual(report["checkpoints"][0]["mount_relative_dir"], "maintenance/epoch-01")
            host_adapter = Path(report["adapter_mount"]["host_root"]) / report["last_epoch_adapter"]["mount_relative_dir"]
            self.assertTrue((host_adapter / "adapter-hashes.json").is_file())
            self.assertTrue(report["artifacts_complete"])
            self.assertFalse(report["export_ready"])
            self.assertIsNotNone(report["selected_adapter"])
            self.assertIsNone(report["final_adapter"])

    def test_epoch_normal_early_return_cannot_claim_completed_partial_epoch(self):
        for progress in (0.75, 1.75):
            with self.subTest(progress=progress), tempfile.TemporaryDirectory() as tmp:
                class EarlyTrainer:
                    def __init__(self, **kwargs):
                        self.callback = kwargs["callbacks"][0]
                        self.state = types.SimpleNamespace(epoch=0.0, global_step=0)

                    def train(self):
                        if progress > 1:
                            self.state.epoch, self.state.global_step = 1.0, 8
                            self.callback.on_epoch_end(None, self.state, None)
                        self.state.epoch, self.state.global_step = progress, 14
                        self.callback.on_epoch_end(None, self.state, None)
                        return types.SimpleNamespace(training_loss=1.25)

                root = Path(tmp)
                requested = 2 if progress > 1 else 1
                with self.assertRaises((ValueError, SystemExit)):
                    self._maintenance_epoch_run(root, EarlyTrainer, epochs=requested)
                report = json.loads((root / "reports" / "maintenance.json").read_bytes())
                self.assertEqual(report["requested_epochs"], requested)
                self.assertEqual(report["trainer_epoch"], progress)
                self.assertEqual(report["completed_epoch_ids"], [1] if progress > 1 else [])
                self.assertEqual(report["completed_epochs"], 1 if progress > 1 else 0)
                self.assertFalse(report["export_ready"])
                self.assertIsNone(report["final_adapter"])
                self.assertFalse((Path(tmp) / "models" / "maintenance" / "epoch-02").exists())

    def test_epoch_readiness_requires_ordered_ids_and_final_actual_progress(self):
        for observed, final_progress in (([2, 1], 2.0), ([1, 2], 1.75), ([1, 2], None)):
            with self.subTest(observed=observed, final=final_progress), tempfile.TemporaryDirectory() as tmp:
                class IncompleteTrainer(Slice3CompleteTrainer):
                    def train(self):
                        for epoch in observed:
                            self.state.epoch, self.state.global_step = float(epoch), epoch * 8
                            self.callback.on_epoch_end(None, self.state, None)
                        self.state.epoch = final_progress
                        return types.SimpleNamespace(training_loss=1.25)
                root = Path(tmp)
                with self.assertRaises((ValueError, SystemExit)):
                    self._maintenance_epoch_run(root, IncompleteTrainer, epochs=2)
                report = json.loads((root / "reports" / "maintenance.json").read_bytes())
                self.assertFalse(report["operational_completion"])
                self.assertFalse(report["qualified"])
                self.assertFalse(report["export_ready"])
                self.assertIsNone(report["selected_adapter"])

    def test_epoch_constructor_failure_preserves_truthful_report(self):
        def broken_constructor(**kwargs):
            handler = OfflineCallbackHandler(kwargs["callbacks"], kwargs["model"])
            handler.call_event("on_init_end", kwargs["args"], types.SimpleNamespace(epoch=0.0), object())
            raise RuntimeError("constructor failed")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(RuntimeError, "constructor failed"):
                self._maintenance_epoch_run(root, broken_constructor, epochs=1)
            payload = json.loads((root / "reports" / "maintenance.json").read_bytes())
            self.assertEqual(payload["completed_epochs"], 0)
            self.assertEqual(payload["completed_epoch_ids"], [])
            self.assertIsNone(payload["trainer_epoch"])
            self.assertFalse(payload["export_ready"])
            self.assertIsNone(payload["final_adapter"])
            self.assertTrue((root / "models" / "maintenance").is_dir())

    def _scratch_leaf(self, root: Path, extra: dict[str, bytes] | None = None) -> Path:
        leaf = root / "epoch-01"
        leaf.mkdir(parents=True)
        files = {
            "adapter_config.json": b'{"r": 16}',
            "adapter_model.safetensors": b"adapter weights",
            "README.md": b"adapter",
            **(extra or {}),
        }
        for name, data in files.items():
            (leaf / name).write_bytes(data)
        return leaf

    def test_scratch_leaf_manifest_covers_every_file_and_passes_exporter_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            leaf = self._scratch_leaf(Path(tmp), extra={"tokenizer_config.json": b"tokenizer"})
            files, manifest_sha256 = train_adapter.write_leaf_manifest(leaf)
            self.assertEqual(
                sorted(files), ["README.md", "adapter_config.json", "adapter_model.safetensors", "tokenizer_config.json"]
            )
            self.assertRegex(manifest_sha256, r"^[0-9a-f]{64}$")
            self._assert_exporter_adapter_hashes(leaf)

    def test_scratch_leaf_fixtures_fail_the_exporter_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tampered = self._scratch_leaf(root / "tampered")
            train_adapter.write_leaf_manifest(tampered)
            (tampered / "adapter_model.safetensors").write_bytes(b"tampered")
            with self.assertRaises(AssertionError):
                self._assert_exporter_adapter_hashes(tampered)
            missing = self._scratch_leaf(root / "missing")
            train_adapter.write_leaf_manifest(missing)
            (missing / "adapter_config.json").unlink()
            with self.assertRaises(AssertionError):
                self._assert_exporter_adapter_hashes(missing)
            unlisted = self._scratch_leaf(root / "unlisted")
            train_adapter.write_leaf_manifest(unlisted)
            (unlisted / "optimizer.pt").write_bytes(b"optimizer")
            with self.assertRaises(AssertionError):
                self._assert_exporter_adapter_hashes(unlisted)

    def test_manifest_writer_refuses_rewrites_reparse_and_case_collisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            leaf = self._scratch_leaf(root / "rewrite")
            train_adapter.write_leaf_manifest(leaf)
            with self.assertRaisesRegex(SystemExit, "already exists"):
                train_adapter.write_leaf_manifest(leaf)
            # A hand-written manifest with case-colliding keys must fail the gate.
            collision = self._scratch_leaf(root / "collision")
            (collision / "adapter-hashes.json").write_text(json.dumps(
                {"files": {
                    "adapter_config.json": "a" * 64,
                    "ADAPTER_CONFIG.json": "a" * 64,
                    "adapter_model.safetensors": "a" * 64,
                }}), encoding="utf-8")
            with self.assertRaises(AssertionError):
                self._assert_exporter_adapter_hashes(collision)
            linked = self._scratch_leaf(root / "linked")
            elsewhere = root / "elsewhere"
            elsewhere.mkdir()
            (elsewhere / "stolen.safetensors").write_bytes(b"stolen")
            if not make_reparse_link(linked / "linked", elsewhere):
                self.skipTest("no reparse-point creation is permitted on this host")
            with self.assertRaisesRegex(SystemExit, "reparse point"):
                train_adapter.write_leaf_manifest(linked)

    def test_unsafe_leaf_relative_names_are_refused(self):
        for name in ("../escape.safetensors", "/absolute.safetensors", "C:/escape.safetensors", "dir\\escape.safetensors", "a/../b.safetensors", "trailing. /x"):
            with self.subTest(name=name):
                with self.assertRaises(SystemExit):
                    train_adapter.require_confined_relative_path(
                        name, Path("c:/safe"), "adapter leaf names must be confined")

    def test_run_output_directory_is_never_reused_or_clobbered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.dict(os.environ, {"ADAPTER_MODELS_DIR": str(root)}):
                report_path = root / "reports" / "m2-run.json"
                run_dir = train_adapter.create_run_directory(report_path)
                self.assertEqual(run_dir, root / "m2-run")
                with self.assertRaisesRegex(SystemExit, "already exists"):
                    train_adapter.create_run_directory(report_path)
                (root / "m2-other").mkdir()
                with self.assertRaisesRegex(SystemExit, "already exists"):
                    train_adapter.create_run_directory(root / "reports" / "m2-other.json")

    def test_epoch_train_failure_leaves_evidence_but_no_export_ready_record(self):
        failure = RuntimeError("training exploded")
        class FailingTrainer(Slice3CompleteTrainer):
            def train(self):
                self.state.epoch, self.state.global_step = 1.0, 8
                self.callback.on_epoch_end(None, self.state, None)
                raise failure
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(RuntimeError, "training exploded") as caught:
                self._maintenance_epoch_run(root, FailingTrainer, epochs=3, peak_gib=2.0)
            self.assertIs(caught.exception, failure)
            report = json.loads((root / "reports" / "maintenance.json").read_text(encoding="utf-8"))
            self.assertEqual(report["completed_epochs"], 1)
            self.assertEqual(report["state"], "failed")
            self.assertEqual(report["peak_reserved_vram_gib"], 2.0)
            self.assertFalse(report["operational_completion"])
            self.assertFalse(report["qualified"])
            self.assertFalse(report["export_ready"])
            self.assertIsNone(report["selected_adapter"])
            self.assertIsNone(report["final_adapter"])

    def test_failure_telemetry_error_preserves_exact_training_exception(self):
        failure = RuntimeError("original training failure")
        class FailingTrainer(Slice3CompleteTrainer):
            def train(self):
                raise failure
        for telemetry_error in (RuntimeError("sampling failed"), SystemExit("sampling aborted")):
            with self.subTest(telemetry=type(telemetry_error).__name__), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                cuda = mock.Mock(max_memory_reserved=mock.Mock(side_effect=telemetry_error))
                with self.assertRaises(RuntimeError) as caught:
                    self._maintenance_epoch_run(root, FailingTrainer, epochs=1, cuda=cuda)
                self.assertIs(caught.exception, failure)
                self.assertEqual(cuda.reset_peak_memory_stats.call_count, 2)
                cuda.max_memory_reserved.assert_called_once_with()
                report = json.loads((root / "reports" / "maintenance.json").read_bytes())
                self.assertEqual(report["state"], "failed")
                self.assertIsNone(report["peak_reserved_vram_gib"])
                self.assertFalse(report["operational_completion"])
                self.assertFalse(report["qualified"])
                self.assertFalse(report["export_ready"])
                self.assertIsNone(report["selected_adapter"])

    def test_failed_memory_initialization_never_samples_cuda(self):
        failure = RuntimeError("memory tracking initialization failed")
        cuda = mock.Mock(reset_peak_memory_stats=mock.Mock(side_effect=failure))
        trainer = mock.Mock(side_effect=AssertionError("trainer must not be built"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(RuntimeError) as caught:
                self._maintenance_epoch_run(root, trainer, epochs=1, cuda=cuda)
            self.assertIs(caught.exception, failure)
            self.assertEqual(cuda.mock_calls, [mock.call.reset_peak_memory_stats()])
            trainer.assert_not_called()
            report = json.loads((root / "reports" / "maintenance.json").read_bytes())
            self.assertEqual(report["state"], "failed")
            self.assertIsNone(report["peak_reserved_vram_gib"])
            self.assertFalse(report["qualified"])
            self.assertFalse(report["export_ready"])

    def test_from_pretrained_uses_local_files_only_when_digests_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "out.json"
            digest_path = Path(tmp) / "digests.json"
            digest_info = {"files": 1, "reads": 2, "bytes": 1, "weight_files": 1, "weight_map_checked": False}
            fake_flm = mock.MagicMock()
            fake_flm.from_pretrained = mock.MagicMock(side_effect=SystemExit("stop after load"))
            cuda = mock.MagicMock()
            cuda.reset_peak_memory_stats = mock.MagicMock()
            with mock.patch.object(train_adapter.torch, "cuda", cuda, create=True):
                with mock.patch.object(train_adapter, "FastLanguageModel", fake_flm):
                    with mock.patch.object(train_adapter, "digest_verification_before_load", return_value=digest_info):
                        with self.assertRaises(SystemExit):
                            train_adapter.train(
                                [row()],
                                [],
                                train_adapter.WINNER_BASE,
                                train_adapter.WINNER_REVISION,
                                20,
                                report_path,
                                winner=True,
                                expected_digests_path=digest_path,
                            )
            _, kwargs = fake_flm.from_pretrained.call_args
            self.assertTrue(kwargs.get("local_files_only"))

    def test_probe_overlength_row_is_diagnostic_on_winner_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows_path = write_accepted(Path(tmp), [row()])
            report_path = Path(tmp) / "probe.json"
            digest_path = Path(tmp) / "digests.json"
            key = f"{train_adapter.WINNER_BASE}@{train_adapter.WINNER_REVISION}"
            digest_path.write_text(json.dumps({key: {"files": []}}), encoding="utf-8")
            seq = train_adapter.SEQ
            at_limit = {
                "input_ids": list(range(seq)),
                "attention_mask": [1] * seq,
                "labels": [-100] * (seq - 2) + [4, 5],
            }
            over = {
                "input_ids": list(range(seq + 1)),
                "attention_mask": [1] * (seq + 1),
                "labels": [-100] * (seq - 1) + [4, 5],
            }
            encoded = [at_limit] * 99 + [over]
            fake_tokenizer = mock.MagicMock()
            fake_model = mock.MagicMock()
            fake_model.config._commit_hash = train_adapter.WINNER_REVISION
            fake_model.parameters = mock.MagicMock(return_value=iter([mock.MagicMock(device="cpu")]))
            fake_flm = mock.MagicMock()
            fake_flm.from_pretrained = mock.MagicMock(return_value=(fake_model, fake_tokenizer))
            fake_flm.get_peft_model = mock.MagicMock(return_value=fake_model)
            exercise = [at_limit]
            with mock.patch.object(train_adapter, "FastLanguageModel", fake_flm):
                with mock.patch.object(train_adapter, "digest_verification_before_load", return_value=None):
                    with mock.patch.object(train_adapter, "encode_rows", return_value=encoded):
                        with mock.patch.object(
                            train_adapter, "prepare_probe_examples", return_value=(exercise, "attended_prompt_repeat"),
                        ):
                            with mock.patch.object(train_adapter, "run_probe_microsteps", return_value=(1.0, 9.0)):
                                train_adapter.run_probe(
                                    [row() for _ in range(100)],
                                    train_adapter.WINNER_BASE,
                                    train_adapter.WINNER_REVISION,
                                    report_path,
                                    probe_rows_total=100,
                                    probe_seq_len=seq,
                                    expected_digests_path=digest_path,
                                )
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "diagnostic_probe")
            self.assertFalse(payload["authoritative"])
            self.assertEqual(payload["overlength_rows"], 1)
            self.assertEqual(payload["max_tokens"], seq + 1)

    def test_successful_probe_reports_actual_discarded_warmup_count(self):
        encoded = [{"input_ids": [1, 2], "attention_mask": [1, 1], "labels": [-100, 2]}]
        model = mock.Mock(config=types.SimpleNamespace(_commit_hash=train_adapter.SMOKE_REVISION))
        fake_flm = mock.Mock(from_pretrained=mock.Mock(return_value=(model, mock.Mock())),
                             get_peft_model=mock.Mock(return_value=model))
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(train_adapter, "FastLanguageModel", fake_flm), \
             mock.patch.object(train_adapter, "digest_verification_before_load", return_value=None), \
             mock.patch.object(train_adapter, "encode_rows", return_value=encoded), \
             mock.patch.object(train_adapter, "prepare_probe_examples", return_value=(encoded, "synthetic")), \
             mock.patch.object(train_adapter, "run_probe_microsteps", return_value=(0.5, 1.0)) as steps:
            path = Path(tmp) / "probe.json"
            report = train_adapter.run_probe([row()], train_adapter.SMOKE_BASE, train_adapter.SMOKE_REVISION,
                                            path, probe_rows_total=None, probe_seq_len=12)
            steps.assert_called_once_with(model, encoded, warmup_microsteps=10,
                                          timed_microsteps=40, gradient_accumulation=8)
            self.assertEqual(report["warmup_discarded_microsteps"], 2)
            self.assertEqual(json.loads(path.read_bytes())["warmup_discarded_microsteps"], 2)

    def test_probe_exercise_rows_use_attended_prompt_repeat(self):
        seq_len = 12
        example = {
            "input_ids": [10, 11, 12, 20, 21],
            "attention_mask": [1, 1, 1, 1, 1],
            "labels": [-100, -100, -100, 20, 21],
        }
        original_response_labels = example["labels"][-2:]
        exercise = train_adapter.extend_probe_example_to_seq_len(example, seq_len)
        self.assertEqual(len(exercise["input_ids"]), seq_len)
        self.assertEqual(exercise["attention_mask"], [1] * seq_len)
        self.assertEqual(exercise["labels"][-2:], original_response_labels)
        self.assertEqual(exercise["input_ids"][-2:], [20, 21])
        fill_labels = exercise["labels"][3:-2]
        self.assertTrue(fill_labels)
        self.assertTrue(all(label == train_adapter.IGNORED_LABEL for label in fill_labels))
        train_adapter.require_response_only(exercise["labels"], 3)

    def test_probe_authoritative_fails_memory_gate_after_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows_path = write_accepted(Path(tmp), [row()])
            report_path = Path(tmp) / "probe.json"
            self._run_probe_with_gate_outcome(
                rows_path, report_path, memory_pass=False, time_pass=True, authoritative=True,
                expect_exit=True, message="memory gate",
            )

    def test_probe_authoritative_fails_time_gate_after_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows_path = write_accepted(Path(tmp), [row()])
            report_path = Path(tmp) / "probe.json"
            self._run_probe_with_gate_outcome(
                rows_path, report_path, memory_pass=True, time_pass=False, authoritative=True,
                expect_exit=True, message="time gate",
            )

    def test_probe_diagnostic_failed_gate_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows_path = write_accepted(Path(tmp), [row()])
            report_path = Path(tmp) / "probe.json"
            self._run_probe_with_gate_outcome(
                rows_path, report_path, memory_pass=False, time_pass=False, authoritative=False,
                expect_exit=False,
            )

    def test_probe_is_authoritative_only_when_projection_matches_loaded_rows(self):
        self.assertTrue(train_adapter.probe_is_authoritative(
            train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION, 2048, 96, 96,
        ))
        self.assertFalse(train_adapter.probe_is_authoritative(
            train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION, 2048, 1, 96,
        ))
        self.assertFalse(train_adapter.probe_is_authoritative(
            train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION, 1024, 1, 1,
        ))

    def test_main_probe_default_rows_total_is_authoritative_on_winner_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_accepted(Path(tmp), [row()])
            digest_path = Path(tmp) / "digests.json"
            key = f"{train_adapter.WINNER_BASE}@{train_adapter.WINNER_REVISION}"
            digest_path.write_text(json.dumps({key: {"files": []}}), encoding="utf-8")
            captured: dict = {}

            def fake_run_probe(rows, base, revision, report_path, **kwargs):
                loaded = len(rows)
                projection = kwargs["probe_rows_total"]
                if projection is None:
                    projection = loaded
                captured["projection"] = projection
                captured["authoritative"] = train_adapter.probe_is_authoritative(
                    base, revision, kwargs["probe_seq_len"], loaded, projection,
                )
                return {"authoritative": captured["authoritative"], "mode": "d_m1_f_probe"}

            with mock.patch.object(train_adapter, "run_probe", side_effect=fake_run_probe):
                train_adapter.main([
                    "--rows", str(path),
                    "--report", str(Path(tmp, "probe.json")),
                    "--probe",
                    "--base", train_adapter.WINNER_BASE,
                    "--revision", train_adapter.WINNER_REVISION,
                    "--expected-digests", str(digest_path),
                ])
            self.assertEqual(captured["projection"], 1)
            self.assertTrue(captured["authoritative"])

    def test_main_probe_explicit_rows_total_mismatch_is_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_accepted(Path(tmp), [row()])
            digest_path = Path(tmp) / "digests.json"
            key = f"{train_adapter.WINNER_BASE}@{train_adapter.WINNER_REVISION}"
            digest_path.write_text(json.dumps({key: {"files": []}}), encoding="utf-8")
            captured: dict = {}

            def fake_run_probe(rows, base, revision, report_path, **kwargs):
                loaded = len(rows)
                projection = kwargs["probe_rows_total"] or loaded
                captured["authoritative"] = train_adapter.probe_is_authoritative(
                    base, revision, kwargs["probe_seq_len"], loaded, projection,
                )
                return {"authoritative": captured["authoritative"], "mode": "diagnostic_probe"}

            with mock.patch.object(train_adapter, "run_probe", side_effect=fake_run_probe):
                train_adapter.main([
                    "--rows", str(path),
                    "--report", str(Path(tmp, "probe.json")),
                    "--probe",
                    "--base", train_adapter.WINNER_BASE,
                    "--revision", train_adapter.WINNER_REVISION,
                    "--expected-digests", str(digest_path),
                    "--probe-rows-total", "96",
                ])
            self.assertFalse(captured["authoritative"])

    def test_wrapper_passes_probe_rows_total_only_when_bound(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "train-adapter.ps1"
        text = script.read_text(encoding="utf-8")
        self.assertNotRegex(text, r"\$ProbeRowsTotal\s*=\s*96")
        self.assertIn("ContainsKey('ProbeRowsTotal')", text)
        self.assertIn("'--probe-rows-total', \"$ProbeRowsTotal\"", text)

    def _run_probe_with_gate_outcome(
        self,
        rows_path: Path,
        report_path: Path,
        *,
        memory_pass: bool,
        time_pass: bool,
        authoritative: bool,
        expect_exit: bool,
        message: str = "",
    ) -> None:
        digest_path = rows_path.parent / "digests.json"
        key = f"{train_adapter.WINNER_BASE}@{train_adapter.WINNER_REVISION}"
        digest_path.write_text(json.dumps({key: {"files": []}}), encoding="utf-8")
        fake_tokenizer = mock.MagicMock()
        fake_tokenizer.apply_chat_template = mock.MagicMock(return_value=[1, 2, 3, 4, 5])
        base = train_adapter.WINNER_BASE if authoritative else train_adapter.SMOKE_BASE
        revision = train_adapter.WINNER_REVISION if authoritative else train_adapter.SMOKE_REVISION
        fake_model = mock.MagicMock()
        fake_model.config._commit_hash = revision
        fake_model.parameters = mock.MagicMock(return_value=iter([mock.MagicMock(device="cpu")]))
        fake_flm = mock.MagicMock()
        fake_flm.from_pretrained = mock.MagicMock(return_value=(fake_model, fake_tokenizer))
        fake_flm.get_peft_model = mock.MagicMock(return_value=fake_model)
        encoded = [{
            "input_ids": [1, 2, 3, 4, 5],
            "attention_mask": [1, 1, 1, 1, 1],
            "labels": [-100, -100, -100, 4, 5],
        }]
        exercise = [{
            "input_ids": list(range(train_adapter.SEQ)),
            "attention_mask": [1] * train_adapter.SEQ,
            "labels": [-100] * (train_adapter.SEQ - 2) + [4, 5],
        }]
        peak = 10.0 if memory_pass else 12.0
        seconds = 1.0 if time_pass else 12_000.0
        argv = [
            "--rows", str(rows_path),
            "--report", str(report_path),
            "--probe",
            "--base", base,
            "--revision", revision,
        ]
        if authoritative:
            argv.extend(["--expected-digests", str(digest_path)])
        patches = [
            mock.patch.object(train_adapter, "FastLanguageModel", fake_flm),
            mock.patch.object(train_adapter, "digest_verification_before_load", return_value=None),
            mock.patch.object(train_adapter, "encode_rows", return_value=encoded),
            mock.patch.object(train_adapter, "prepare_probe_examples", return_value=(exercise, "attended_prompt_repeat")),
            mock.patch.object(train_adapter, "run_probe_microsteps", return_value=(seconds, peak)),
        ]
        if expect_exit:
            with contextlib.ExitStack() as stack:
                for item in patches:
                    stack.enter_context(item)
                with self.assertRaises(SystemExit) as raised:
                    train_adapter.main(argv)
                if message:
                    self.assertIn(message, str(raised.exception))
            self.assertTrue(report_path.is_file())
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["memory_pass"], memory_pass)
            self.assertEqual(payload["time_pass"], time_pass)
            self.assertEqual(payload["authoritative"], authoritative)
        else:
            with contextlib.ExitStack() as stack:
                for item in patches:
                    stack.enter_context(item)
                train_adapter.main(argv)
            self.assertTrue(report_path.is_file())

    def test_probe_report_authoritative_for_winner_and_smoke_pins(self):
        kwargs = self._minimal_probe_report_kwargs()
        kwargs.update({"pin": "winner", "mode": "d_m1_f_probe", "authoritative": True})
        winner = train_adapter.build_probe_report(**kwargs)
        smoke_kwargs = self._minimal_probe_report_kwargs()
        smoke_kwargs.update({"pin": "smoke", "mode": "diagnostic_probe", "authoritative": False})
        smoke = train_adapter.build_probe_report(**smoke_kwargs)
        self.assertTrue(winner["authoritative"])
        self.assertFalse(smoke["authoritative"])
        self.assertTrue(winner["memory_pass"])
        self.assertTrue(smoke["time_pass"])

    def test_early_stop_is_operational_but_not_qualified_or_exportable(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self._maintenance_epoch_run(Path(tmp), Slice3CompleteTrainer, epochs=6)
            self.assertEqual(report["completed_epoch_ids"], [1, 2, 3])
            self.assertTrue(report["operational_completion"])
            self.assertEqual(report["state"], "stopped")
            self.assertEqual(report["dev_cadence"]["stop_reason"], "two_epochs_without_improvement")
            self.assertFalse(report["qualified"])
            self.assertFalse(report["export_ready"])

    def test_midpoint_only_return_has_no_selectable_leaf_and_fails_completion(self):
        class MidpointOnly(Slice3CompleteTrainer):
            def train(self):
                self.state.epoch, self.state.global_step = 0.75, 6
                return types.SimpleNamespace(training_loss=1.25)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises((ValueError, SystemExit)):
                self._maintenance_epoch_run(root, MidpointOnly, epochs=1)
            report = json.loads((root / "reports" / "maintenance.json").read_bytes())
            self.assertEqual(report["completed_epochs"], 0)
            self.assertFalse(report["operational_completion"])
            self.assertEqual(report["dev_selection_status"], "not_selected")
            self.assertIsNone(report["selected_adapter"])
            self.assertFalse(report["qualified"])
            self.assertFalse(report["export_ready"])

    def test_selection_error_is_run_failure_not_successful_unselected_report(self):
        import dev_selection
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(dev_selection, "select_checkpoint", side_effect=ValueError("synthetic selection failure")):
            root = Path(tmp)
            with self.assertRaises((ValueError, SystemExit)):
                self._maintenance_epoch_run(root, Slice3CompleteTrainer, epochs=1)
            report = json.loads((root / "reports" / "maintenance.json").read_bytes())
            self.assertEqual(report["state"], "failed")
            self.assertFalse(report["operational_completion"])
            self.assertIsNone(report["selected_adapter"])
            self.assertFalse(report["export_ready"])

    def test_template_mismatch_refuses_before_any_model_load(self):
        import predict_dev
        template_path = Path(train_adapter.__file__).with_name("prompt_template.py")
        admitted_lf = template_path.read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(hashlib.sha256(admitted_lf).hexdigest(), train_adapter.ADMITTED_TEMPLATE_SHA256)
        for bad in (b"unadmitted template\n", admitted_lf.replace(b"\n", b"\r\n")):
            with self.subTest(bytes=bad), tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
                fx = _build_publication_fixture(Path(tmp), rows=slice3_train_rows() + slice3_dev_rows())
                stack.enter_context(mock.patch.dict(os.environ, {"ADAPTER_MODELS_DIR": str(Path(tmp) / "models")}))
                loader = mock.Mock(side_effect=AssertionError("must refuse before model load"))
                stack.enter_context(mock.patch.object(train_adapter, "digest_verification_before_load", return_value={"upstream_digest_list_sha256": "b" * 64}))
                stack.enter_context(mock.patch.object(predict_dev, "_default_template_source", return_value=bad))
                stack.enter_context(mock.patch.object(train_adapter, "FastLanguageModel", mock.Mock(from_pretrained=loader)))
                cuda = stack.enter_context(mock.patch.object(train_adapter.torch, "cuda", mock.Mock(), create=True))
                seed = stack.enter_context(mock.patch.object(train_adapter, "set_seed"))
                with self.assertRaisesRegex((ValueError, SystemExit), "template digest mismatch"):
                    train_adapter.train(slice3_train_rows(), slice3_dev_rows(), train_adapter.WINNER_BASE,
                        train_adapter.WINNER_REVISION, None, Path(tmp) / "report.json", winner=True, epochs=1,
                        **fx["train_kwargs"])
                loader.assert_not_called()
                seed.assert_not_called()
                self.assertEqual(cuda.mock_calls, [])

    def test_selected_primary_is_epoch_end_score_not_midpoint_reference(self):
        from fractions import Fraction
        with tempfile.TemporaryDirectory() as tmp:
            report = self._maintenance_epoch_run(Path(tmp), Slice3CompleteTrainer, epochs=1,
                scores=[slice3_score(Fraction(9, 10)), slice3_score(Fraction(43, 50))])
            self.assertTrue(report["operational_completion"])
            self.assertEqual(report["dev_cadence"]["best_primary"], {"numerator": 9, "denominator": 10})
            self.assertEqual(report["selected_primary"], {"numerator": 43, "denominator": 50})
            self.assertEqual(report["dev_cadence"]["best_epoch"], 1)

    def test_smoke_steps_probe_and_check_only_never_load_dev_rows(self):
        for mode in (["--steps", "20"], ["--probe"], ["--check-only"]):
            with self.subTest(mode=mode), mock.patch.object(train_adapter, "load_training_rows", return_value=[row()]), \
                 mock.patch.object(train_adapter, "load_dev_rows", side_effect=AssertionError("epoch-only dev load")) as dev_load, \
                 mock.patch.object(train_adapter, "train", return_value={}) as training, \
                 mock.patch.object(train_adapter, "run_probe", return_value={}), \
                 contextlib.redirect_stdout(io.StringIO()):
                train_adapter.main(["--rows", "synthetic/accepted.jsonl", "--report", "synthetic/report.json",
                    "--base", train_adapter.SMOKE_BASE, "--revision", train_adapter.SMOKE_REVISION, *mode])
                dev_load.assert_not_called()
                if "--steps" in mode:
                    self.assertEqual(training.call_args.args[1], [])

    def test_durable_constructor_failure_sequence_and_reason_survive_train_wrapper(self):
        actual_callback = train_adapter.DevEvaluationCallback
        captured = {}
        def fail_after_durable_initialization(*args, **kwargs):
            callback = actual_callback(*args, **kwargs)
            callback._mark_failed("specific_constructor_failure")
            captured["path"] = callback.run_dir / "best-state.json"
            captured["bytes"] = captured["path"].read_bytes()
            raise ValueError("synthetic constructor failed")
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(train_adapter, "DevEvaluationCallback", side_effect=fail_after_durable_initialization):
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, "synthetic constructor failed"):
                self._maintenance_epoch_run(root, Slice3CompleteTrainer, epochs=1)
            self.assertEqual(captured["path"].read_bytes(), captured["bytes"])
            state = json.loads(captured["bytes"])
            self.assertEqual(state["state"], "failed")
            self.assertEqual(state["stop_reason"], "specific_constructor_failure")
            self.assertGreater(state["sequence"], 1)
            report = json.loads((root / "reports" / "maintenance.json").read_bytes())
            self.assertEqual(report["state"], "failed")
            self.assertFalse(report["operational_completion"])
            self.assertIsNone(report["selected_adapter"])



def slice3_train_rows(count=64):
    # 64 rows give eight optimizer updates: midpoint step 4, epoch end step 8.
    items = []
    for index in range(count):
        item = row()
        item.update(row_id=f"train-synthetic-{index:02d}", source_group="train-group")
        item["input"] = dict(item["input"], id=item["row_id"])
        item["row_id"] = hashlib.sha256(json.dumps({"input": item["input"], "target": item["target"]}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
        items.append(item)
    return items


def slice3_dev_rows():
    items = []
    for index in range(20):
        item = row("dev")
        item.update(row_id=f"dev-{index:02d}", source_group=f"dev-group-{index:02d}")
        item["input"] = dict(item["input"], id=item["row_id"])
        item["row_id"] = hashlib.sha256(json.dumps({"input": item["input"], "target": item["target"]}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
        items.append(item)
    return items


def slice3_context():
    dev = slice3_dev_rows()
    training = slice3_train_rows()
    return {
        "dev_rows": dev,
        "train_rows": training,
        "train_ids": {item["row_id"] for item in training},
        "roster": sorted(item["source_group"] for item in dev),
        "expected_packets": {item["source_group"]: [item["row_id"]] for item in dev},
        "identity": {"base_revision": train_adapter.SMOKE_REVISION,
                     "weights_manifest_sha256": "b" * 64,
                     "template_sha256": train_adapter.ADMITTED_TEMPLATE_SHA256},
    }


def slice3_predictions(dev_rows, model, tokenizer, **kwargs):
    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "predictions": {item["row_id"]: item["target"] for item in dev_rows},
        "schema_invalid_row_ids": [], "complete": True,
        "binding": {
            "source_label": kwargs["source_label"], "seed": 1234,
            "max_new_tokens": kwargs["max_new_tokens"], "thinking": False,
            "roster": list(kwargs["frozen_roster"]),
            "row_ids": sorted(item["row_id"] for item in dev_rows),
            "identity": dict(kwargs["identity"]),
            "expected_packets_sha256": hashlib.sha256(canonical(kwargs["expected_packets"])).hexdigest(),
            "input_sha256": {item["row_id"]: hashlib.sha256(canonical(item["input"])).hexdigest() for item in dev_rows},
        },
    }


def slice3_score(primary, field=None):
    from fractions import Fraction
    primary = Fraction(primary)
    fields = {name: Fraction(field if field is not None else primary) for name in train_adapter.TARGET_FIELDS}
    return {
        "primary": float(primary),
        "primary_exact": {"numerator": primary.numerator, "denominator": primary.denominator},
        "per_field": {name: float(value) for name, value in fields.items()},
        "per_field_exact": {name: {"numerator": value.numerator, "denominator": value.denominator} for name, value in fields.items()},
        "groups": 20, "incomplete_groups": [],
        "incomplete": False, "roster_bound": True, "diagnostic_only": False,
    }


class Slice3CompleteTrainer:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.callback = kwargs["callbacks"][0]
        self.state = types.SimpleNamespace(epoch=0.0, global_step=0)

    def train(self):
        for epoch in range(1, int(self.kwargs["args"].num_train_epochs) + 1):
            self.state.epoch, self.state.global_step = float(epoch), epoch * 8
            control = self.callback.on_epoch_end(None, self.state, None)
            if control.should_training_stop:
                break
        return types.SimpleNamespace(training_loss=1.25)


class TestFrozenDevCadence(unittest.TestCase):
    def setUp(self):
        import predict_dev
        fixture_template = b"synthetic pinned callback template\n"
        for patcher in (
            mock.patch.object(predict_dev, "_default_template_source", return_value=fixture_template),
            mock.patch.object(train_adapter, "ADMITTED_TEMPLATE_SHA256", hashlib.sha256(fixture_template).hexdigest()),
            mock.patch.object(train_adapter, "FastLanguageModel", mock.Mock()),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _callback(self, scores=None, *, context=None, max_epochs=6, predict_fn=None,
                  use_gradient_checkpointing="unsloth"):
        import dev_selection
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        checkpoints = []
        model = mock.Mock()
        def save(directory):
            Path(directory, "adapter_config.json").write_bytes(b'{"r":16}')
            Path(directory, "adapter_model.safetensors").write_bytes(b"synthetic adapter")
        model.save_pretrained.side_effect = save
        self.score_hook = mock.Mock(side_effect=scores or [slice3_score(1)] * 8)
        self.predict_hook = mock.Mock(side_effect=predict_fn or slice3_predictions)
        cb = train_adapter.DevEvaluationCallback(model, root, checkpoints,
            context or slice3_context(), max_epochs=max_epochs,
            predict_fn=self.predict_hook, score_fn=self.score_hook,
            use_gradient_checkpointing=use_gradient_checkpointing)
        return cb, types.SimpleNamespace(should_training_stop=False), checkpoints, root

    def _event(self, cb, control, progress, *, end=False, tokenizer=True):
        method = cb.on_epoch_end if end else cb.on_step_end
        kwargs = {"processing_class": object()} if tokenizer else {}
        return method(None, types.SimpleNamespace(epoch=progress, global_step=int(progress * 8)), control, **kwargs)

    def _state(self, root):
        return json.loads((root / "best-state.json").read_text(encoding="utf-8"))

    def _assert_failed(self, root):
        state = self._state(root)
        self.assertEqual(state["state"], "failed")
        self.assertTrue(state["stop_reason"])
        return state

    def test_midpoint_once_duplicate_idempotence_and_completed_epoch_evidence(self):
        cb, control, leaves, root = self._callback()
        initial = self._state(root) if (root / "best-state.json").exists() else None
        self._event(cb, control, 0.49)
        self.assertEqual(self._state(root) if (root / "best-state.json").exists() else None, initial)
        self._event(cb, control, 0.5)
        first = self._state(root)
        self.assertEqual(first["evaluations"][0]["kind"], "midpoint")
        self.assertEqual(leaves, [])
        self._event(cb, control, 0.75)
        self.assertEqual(self._state(root), first)
        self._event(cb, control, 1.0, end=True)
        second = self._state(root)
        self._event(cb, control, 1.0, end=True)
        self.assertEqual(self._state(root), second)
        self.assertEqual([e["kind"] for e in cb.evaluations], ["midpoint", "epoch_end"])
        self.assertEqual([entry["epoch"] for entry in leaves], [1])
        self.assertEqual(self.predict_hook.call_count, 2)

    def test_generation_mode_transitions_restore_training_even_on_failure(self):
        for checkpointing in ("unsloth", True, False):
            for fault in (None, "inference", "generate", "score", "restore"):
                with self.subTest(checkpointing=checkpointing, fault=fault):
                    events = []
                    failure = RuntimeError("synthetic " + str(fault))
                    cb, control, _, root = self._callback(use_gradient_checkpointing=checkpointing)
                    cb.model.training = True
                    cb.model.is_gradient_checkpointing = bool(checkpointing)

                    def inference(model):
                        events.append("inference")
                        model.training = model.is_gradient_checkpointing = False
                        if fault == "inference":
                            raise failure

                    def training(model, use_gradient_checkpointing=True):
                        events.append(("training", use_gradient_checkpointing))
                        model.training = True
                        model.is_gradient_checkpointing = bool(use_gradient_checkpointing)
                        if fault == "restore":
                            raise failure

                    def predict(*args, **kwargs):
                        events.append(("generate", cb.model.training, cb.model.is_gradient_checkpointing))
                        if fault == "generate":
                            raise failure
                        return slice3_predictions(*args, **kwargs)

                    def score(*args, **kwargs):
                        events.append(("score", cb.model.training, cb.model.is_gradient_checkpointing))
                        if fault == "score":
                            raise failure
                        return slice3_score(1)

                    self.predict_hook.side_effect = predict
                    self.score_hook.side_effect = score
                    api = types.SimpleNamespace(for_inference=inference, for_training=training)
                    with mock.patch.object(train_adapter, "FastLanguageModel", api):
                        if fault:
                            with self.assertRaises(RuntimeError) as raised:
                                self._event(cb, control, 0.5)
                            self.assertIs(raised.exception, failure)
                            self._assert_failed(root)
                        else:
                            self._event(cb, control, 0.5)
                            self.assertEqual(cb.state, "running")
                            self.assertFalse(control.should_training_stop)
                    expected = ["inference"]
                    if fault != "inference":
                        expected.append(("generate", False, False))
                    expected.append(("training", checkpointing))
                    if fault in (None, "score"):
                        expected.append(("score", True, bool(checkpointing)))
                    self.assertEqual(events, expected)
                    self.assertIs(cb.model.training, True)
                    self.assertIs(cb.model.is_gradient_checkpointing, bool(checkpointing))

    def test_exact_five_pp_and_one_count_unit_over_primary_and_field(self):
        from fractions import Fraction
        for which in ("primary", "field"):
            for drop, stopped in ((Fraction(5, 100), False), (Fraction(51, 1000), True)):
                with self.subTest(which=which, drop=drop):
                    baseline = slice3_score(Fraction(4, 5))
                    later = slice3_score(Fraction(4, 5) - drop if which == "primary" else Fraction(4, 5),
                                         Fraction(4, 5) - drop if which == "field" else Fraction(4, 5))
                    cb, control, _, root = self._callback([baseline, later])
                    self._event(cb, control, 0.5)
                    self.assertFalse(control.should_training_stop)
                    self._event(cb, control, 1.0, end=True)
                    self.assertIs(control.should_training_stop, stopped)
                    self.assertEqual(self._state(root)["state"], "stopped" if stopped else "running")
                    if stopped:
                        self.assertEqual(cb.stop_reason, f"{which}_drop_gt_5pp")

    def test_two_nonimproving_epoch_ends_midpoint_does_not_count(self):
        from fractions import Fraction
        cb, control, _, _ = self._callback([slice3_score(Fraction(4, 5))] * 4)
        self._event(cb, control, 0.5)
        for epoch in (1, 2, 3):
            self._event(cb, control, float(epoch), end=True)
            self.assertEqual(cb.epochs_without_improvement, epoch - 1)
            self.assertIs(control.should_training_stop, epoch == 3)
        self.assertEqual(cb.stop_reason, "two_epochs_without_improvement")

    def test_strict_improvement_resets_counter_and_six_epoch_limit(self):
        from fractions import Fraction
        scores = [slice3_score(Fraction(i, 10)) for i in (1, 2, 3, 4, 5, 6, 7)]
        cb, control, _, root = self._callback(scores)
        self._event(cb, control, 0.5)
        for epoch in range(1, 7):
            self._event(cb, control, float(epoch), end=True)
            self.assertIs(control.should_training_stop, epoch == 6)
            self.assertEqual(cb.epochs_without_improvement, 0)
        self.assertEqual(self._state(root)["stop_reason"], "max_epochs")

    def test_exact_primary_selection_earlier_tie_only_epoch_ends(self):
        from fractions import Fraction
        import dev_selection
        cb, control, leaves, _ = self._callback([slice3_score(Fraction(4, 5))] * 4)
        self._event(cb, control, 0.5)
        for epoch in (1, 2, 3):
            self._event(cb, control, float(epoch), end=True)
        entries = [(f"epoch-{e['epoch']:02d}", e["epoch"], Fraction(e["primary"]["numerator"], e["primary"]["denominator"]))
                   for e in cb.evaluations if e["kind"] == "epoch_end"]
        self.assertEqual(dev_selection.select_checkpoint(entries), "epoch-01")
        self.assertEqual(cb.best_epoch, 1)
        self.assertNotIn("midpoint-epoch-01", [name for name, _, _ in entries])

    def test_state_sequence_atomic_sync_replace_and_no_row_text(self):
        cb, control, _, root = self._callback()
        events = []
        real_fsync, real_replace = os.fsync, os.replace
        def fsync(fd):
            events.append("fsync")
            return real_fsync(fd)
        def replace(src, dst):
            events.append("replace")
            self.assertEqual(Path(src).parent, Path(dst).parent)
            return real_replace(src, dst)
        with mock.patch.object(os, "fsync", side_effect=fsync), mock.patch.object(os, "replace", side_effect=replace):
            self._event(cb, control, 0.5)
            first = self._state(root)
            self._event(cb, control, 1.0, end=True)
        second = self._state(root)
        self.assertEqual(events, ["fsync", "replace", "fsync", "replace"])
        self.assertEqual(second["sequence"], first["sequence"] + 1)
        required = {"format", "dev_roster_sha256", "expected_packets_sha256", "evaluations", "best_epoch", "best_primary", "best_per_field", "epochs_without_improvement", "stop_reason", "state", "sequence"}
        self.assertTrue(required <= set(second))
        raw = (root / "best-state.json").read_text(encoding="utf-8")
        for forbidden in ("Retry three times.", "the client stops at three attempts", '"predictions"', '"target"', '"input"'):
            self.assertNotIn(forbidden, raw)

    def test_skipped_midpoint_is_failed_and_fractional_epoch_not_complete(self):
        cb, control, leaves, root = self._callback()
        with self.assertRaises((ValueError, SystemExit)):
            self._event(cb, control, 1.0, end=True)
        self.assertEqual(self._assert_failed(root)["stop_reason"], "midpoint_missing")
        self.assertEqual(self.predict_hook.call_count, 0)
        cb, control, leaves, root = self._callback()
        with self.assertRaises((ValueError, SystemExit)):
            self._event(cb, control, 0.75, end=True)
        self.assertEqual(leaves, [])
        self._assert_failed(root)

    def test_midpoint_only_train_end_is_failed_without_selection(self):
        cb, control, leaves, root = self._callback()
        self._event(cb, control, 0.5)
        with self.assertRaises((ValueError, SystemExit)):
            cb.on_train_end(None, types.SimpleNamespace(epoch=0.75), control)
        self.assertEqual(leaves, [])
        self.assertEqual([e["kind"] for e in cb.evaluations], ["midpoint"])
        self.assertEqual(self._state(root)["state"], "failed")

    def test_predictor_score_and_nonselectable_exceptions_leave_failed_state(self):
        for fault in ("predictor", "scorer", "nonselectable"):
            with self.subTest(fault=fault):
                cb, control, _, root = self._callback()
                if fault == "predictor":
                    self.predict_hook.side_effect = RuntimeError("synthetic predictor failure")
                elif fault == "scorer":
                    self.score_hook.side_effect = RuntimeError("synthetic scorer failure")
                else:
                    invalid = slice3_score(1)
                    invalid["incomplete"] = True
                    self.score_hook.side_effect = [invalid]
                with self.assertRaises(RuntimeError if fault in ("predictor", "scorer") else (ValueError, SystemExit)):
                    self._event(cb, control, 0.5)
                self._assert_failed(root)

    def test_malformed_primary_and_wrong_result_kind_refuse(self):
        for fault in ("boolean", "zero_denominator", "out_of_range", "nan_summary", "wrong_kind", "group_count"):
            with self.subTest(fault=fault):
                value = slice3_score(1)
                if fault == "boolean":
                    value["primary_exact"]["numerator"] = True
                elif fault == "zero_denominator":
                    value["primary_exact"]["denominator"] = 0
                elif fault == "out_of_range":
                    value["primary_exact"]["numerator"] = 2
                elif fault == "nan_summary":
                    value["primary"] = float("nan")
                elif fault == "wrong_kind":
                    value["group_results"] = {}
                else:
                    value["groups"] = True
                cb, control, _, root = self._callback([value])
                with self.assertRaises((ValueError, SystemExit)):
                    self._event(cb, control, 0.5)
                self._assert_failed(root)

    def test_missing_tokenizer_and_leaf_save_failure_are_failed(self):
        for fault in ("tokenizer", "leaf"):
            with self.subTest(fault=fault):
                cb, control, _, root = self._callback()
                with contextlib.ExitStack() as stack:
                    if fault == "leaf":
                        stack.enter_context(mock.patch.object(train_adapter, "write_adapter_leaf", side_effect=OSError("synthetic disk failure")))
                    with self.assertRaises((OSError, ValueError, SystemExit)):
                        self._event(cb, control, 0.5, tokenizer=fault != "tokenizer")
                self._assert_failed(root)
                self.assertEqual(self.predict_hook.call_count, 0)

    def test_malformed_exact_pairs_and_nonfinite_float_refuse(self):
        field = train_adapter.TARGET_FIELDS[0]
        for fault in ("missing", "boolean", "denominator_zero", "outside_range", "inconsistent", "float_nan", "float_bool"):
            with self.subTest(fault=fault):
                value = slice3_score(1)
                if fault == "missing":
                    del value["per_field_exact"][field]
                elif fault == "boolean":
                    value["per_field_exact"][field]["numerator"] = True
                elif fault == "denominator_zero":
                    value["per_field_exact"][field]["denominator"] = 0
                elif fault == "outside_range":
                    value["per_field_exact"][field]["numerator"] = 2
                else:
                    value["per_field"][field] = {"inconsistent": 0.5, "float_nan": float("nan"), "float_bool": True}[fault]
                cb, control, _, root = self._callback([value])
                with self.assertRaises((ValueError, SystemExit)):
                    self._event(cb, control, 0.5)
                self._assert_failed(root)

    def test_incomplete_or_mismatched_prediction_binding_refuses(self):
        for fault in ("incomplete", "identity", "roster", "input_digest", "expected_packets", "row_ids", "thinking"):
            with self.subTest(fault=fault):
                def predict(*args, **kwargs):
                    result = slice3_predictions(*args, **kwargs)
                    if fault == "incomplete":
                        result["complete"] = False
                    elif fault == "identity":
                        result["binding"]["identity"]["adapter_sha256"] = "0" * 64
                    elif fault == "roster":
                        result["binding"]["roster"] = []
                    elif fault == "input_digest":
                        result["binding"]["input_sha256"][next(iter(result["binding"]["input_sha256"]))] = "0" * 64
                    elif fault == "expected_packets":
                        result["binding"]["expected_packets_sha256"] = "0" * 64
                    elif fault == "row_ids":
                        result["binding"]["row_ids"] = []
                    else:
                        result["binding"]["thinking"] = True
                    return result
                cb, control, _, root = self._callback(predict_fn=predict)
                with self.assertRaises((ValueError, SystemExit)):
                    self._event(cb, control, 0.5)
                self._assert_failed(root)
                self.score_hook.assert_not_called()

    def test_handler_lifecycle_preserves_control_and_requested_completion_stops(self):
        cb, control, _, root = self._callback(max_epochs=1)
        handler = OfflineCallbackHandler([cb], cb.model)
        state = types.SimpleNamespace(epoch=0.0, global_step=0)
        for event in ("on_init_end", "on_train_begin", "on_epoch_begin", "on_step_begin", "on_pre_optimizer_step",
                      "on_optimizer_step", "on_substep_end", "on_log", "on_evaluate", "on_predict", "on_prediction_step", "on_save"):
            self.assertIs(handler.call_event(event, None, state, control), control)
        self._event(cb, control, 0.5)
        self.assertIs(self._event(cb, control, 1.0, end=True), control)
        self.assertIs(handler.call_event("on_train_end", None, types.SimpleNamespace(epoch=1.0), control), control)
        self.assertEqual(self._state(root)["state"], "stopped")

    def test_init_roster_disjointness_and_eval_import_refusals_record_failure(self):
        for fault in ("fewer_groups", "duplicate_roster", "empty_group", "roster_mismatch", "packets_mismatch", "row_overlap", "group_overlap", "missing_target", "wrong_split", "identity", "import"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as tmp:
                context = slice3_context()
                if fault == "fewer_groups":
                    context["dev_rows"] = context["dev_rows"][:-1]
                    context["roster"] = context["roster"][:-1]
                    context["expected_packets"].pop("dev-group-19")
                elif fault == "duplicate_roster":
                    context["roster"][1] = context["roster"][0]
                elif fault == "empty_group":
                    context["dev_rows"][0]["source_group"] = ""
                elif fault == "roster_mismatch":
                    context["roster"][0] = "other"
                elif fault == "packets_mismatch":
                    context["expected_packets"]["dev-group-00"] = ["other"]
                elif fault == "row_overlap":
                    context["train_rows"][0]["row_id"] = context["dev_rows"][0]["row_id"]
                    context["train_ids"] = {item["row_id"] for item in context["train_rows"]}
                elif fault == "group_overlap":
                    context["train_rows"][0]["source_group"] = "dev-group-00"
                elif fault == "missing_target":
                    del context["dev_rows"][0]["target"]
                elif fault == "wrong_split":
                    context["dev_rows"][0]["split"] = "train"
                elif fault == "identity":
                    context["identity"]["weights_manifest_sha256"] = "not-a-digest"
                with contextlib.ExitStack() as stack:
                    if fault == "import":
                        stack.enter_context(mock.patch.dict(sys.modules, {"predict_dev": None}))
                    with self.assertRaises((ValueError, ImportError, SystemExit)):
                        train_adapter.DevEvaluationCallback(mock.Mock(), Path(tmp), [], context, max_epochs=6,
                            predict_fn=slice3_predictions, score_fn=lambda *_a, **_k: slice3_score(1),
                            use_gradient_checkpointing="unsloth")
                self._assert_failed(Path(tmp))

    def test_callback_maximum_above_six_is_refused_with_failed_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises((ValueError, SystemExit)):
                train_adapter.DevEvaluationCallback(mock.Mock(), Path(tmp), [], slice3_context(), max_epochs=7,
                    predict_fn=slice3_predictions, score_fn=lambda *_a, **_k: slice3_score(1),
                    use_gradient_checkpointing="unsloth")
            self._assert_failed(Path(tmp))

    def test_load_dev_rows_refuses_bad_json_instead_of_silently_skipping(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "accepted.jsonl"
            path.write_text('{not json}\n' + json.dumps(slice3_dev_rows()[0]) + '\n', encoding="utf-8")
            (Path(tmp) / "report.json").write_text(json.dumps({"n_accepted": 2, "rows_per_split": {"train": 0, "dev": 1}}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                train_adapter.load_dev_rows(path)

    def test_flat_leaf_refuses_added_nested_directory_linked_and_nonregular_entry(self):
        for mutation in ("nested_directory", "linked_entry", "nonregular_entry"):
            with self.subTest(mutation=mutation):
                cb, control, leaves, root = self._callback(max_epochs=1)
                self._event(cb, control, 0.5)
                self._event(cb, control, 1.0, end=True)
                self.assertEqual(cb.completed_selection()["dir"], leaves[0]["dir"])
                leaf = Path(leaves[0]["dir"])
                if mutation == "nested_directory":
                    notes = leaf / "notes"
                    notes.mkdir()
                    (notes / "notes.txt").write_bytes(b"synthetic checkpoint notes")
                    with self.assertRaises((ValueError, SystemExit)):
                        cb.completed_selection()
                elif mutation == "linked_entry":
                    original_is_symlink = Path.is_symlink
                    with mock.patch.object(Path, "is_symlink", lambda path: path.name == "adapter_config.json" or original_is_symlink(path)):
                        with self.assertRaises((ValueError, SystemExit)):
                            cb.completed_selection()
                else:
                    original_is_file = Path.is_file
                    with mock.patch.object(Path, "is_file", lambda path: path.name != "adapter_config.json" and original_is_file(path)):
                        with self.assertRaises((ValueError, SystemExit)):
                            cb.completed_selection()

    def test_initial_failure_state_refuses_corruption_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "best-state.json"
            corrupt = b'{not strict json}\n'
            state_path.write_bytes(corrupt)
            with self.assertRaises((ValueError, SystemExit)):
                train_adapter._initial_failure_state(root, "new generic reason")
            self.assertEqual(state_path.read_bytes(), corrupt)


# WO-R5 slice 4: the publication binding authenticates the staged rows, trainer,
# predictor, scorer and renderer before any GPU, model or tokenizer resource is
# touched in epoch mode. These fixtures build a self-consistent synthetic
# binding directly in Python (three accepted rows, two source groups, so rows
# and unique groups differ and a group/row count mix-up cannot hide behind
# the fixture), then tamper with exactly one aspect per test.
_PUBLICATION_DEFAULT_ROWS = [
    {"source_group": "group-a", "split": "train", "row_id": "r1"},
    {"source_group": "group-a", "split": "train", "row_id": "r2"},
    {"source_group": "group-b", "split": "dev", "row_id": "r3"},
]


def _publication_row_digest(row: dict) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def _publication_write_file(path: Path, data: bytes) -> dict:
    path.write_bytes(data)
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def _publication_admitted_template_bytes() -> bytes:
    """The admitted renderer is an LF Git blob; the host working copy is CRLF.

    This mirrors ``test_template_mismatch_refuses_before_any_model_load`` above:
    normalizing the *test fixture's copy* of the working tree's bytes to LF is
    not the same as normalizing the repository's own working-copy file, which
    stays untouched on disk.
    """
    template_path = Path(train_adapter.__file__).with_name("prompt_template.py")
    return template_path.read_bytes().replace(b"\r\n", b"\n")


def _splice_json_object(text: str, extra_member: str) -> str:
    """Insert ``extra_member`` (a bare ``"key": value`` fragment) as one more
    member of the outermost JSON object in ``text``, for tests that need a
    raw duplicate or extra key a Python dict literal cannot express."""
    stripped = text.rstrip()
    assert stripped.endswith("}")
    idx = stripped.rfind("}")
    return stripped[:idx] + "," + extra_member + stripped[idx:]


def _build_publication_fixture(
    root: Path,
    *,
    batch: str = "batch-0001",
    rows: list[dict] | None = None,
    report_overrides: dict | None = None,
    profile_overrides: dict | None = None,
    candidate_overrides: dict | None = None,
    resolution_overrides: dict | None = None,
    binding_overrides: dict | None = None,
    files_mutator=None,
    tamper_file: tuple[str, str, bytes] | None = None,
    extra_file: tuple[str, str, bytes] | None = None,
    binding_text: str | None = None,
) -> dict:
    """A self-consistent synthetic publication binding plus its three staged
    directories. Every keyword overrides exactly one layer so a test can
    introduce exactly one defect without having to re-derive the rest."""
    rows_dir, code_dir, eval_dir = root / "rows", root / "train", root / "eval"
    for d in (rows_dir, code_dir, eval_dir):
        d.mkdir()

    rows = _PUBLICATION_DEFAULT_ROWS if rows is None else rows
    accepted_bytes = ("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)).encode("utf-8")
    accepted_info = _publication_write_file(rows_dir / "accepted.jsonl", accepted_bytes)
    row_digests = [_publication_row_digest(r) for r in rows]

    groups_and_splits: dict[str, str] = {}
    for r in rows:
        groups_and_splits[r["source_group"]] = r["split"]
    unique_by_split: dict[str, set] = {"train": set(), "dev": set()}
    for group, split in groups_and_splits.items():
        unique_by_split[split].add(group)
    splits = {"train": len(unique_by_split["train"]), "dev": len(unique_by_split["dev"])}

    report = {
        "format": "route-r-validation-report/v1",
        "batch": batch,
        "rows_in": len(rows),
        "rows_admitted": len(rows),
        "schema_valid": len(rows),
        "splits": splits,
        "accepted_jsonl_sha256": accepted_info["sha256"],
    }
    report.update(report_overrides or {})
    report_info = _publication_write_file(rows_dir / "report.json", json.dumps(report, sort_keys=True).encode("utf-8"))

    trainer_info = _publication_write_file(code_dir / "train_adapter.py", b"synthetic trainer bytes\n")
    template_info = _publication_write_file(code_dir / "prompt_template.py", _publication_admitted_template_bytes())
    predictor_info = _publication_write_file(eval_dir / "predict_dev.py", b"synthetic predictor bytes\n")
    scorer_info = _publication_write_file(eval_dir / "dev_selection.py", b"synthetic scorer bytes\n")

    profile = {
        "format": "route-r-publication-profile/v1",
        "batch": batch,
        "built_by": "codex",
        "accepted_jsonl_sha256": accepted_info["sha256"],
        "candidate_row_sha256": row_digests,
        "bundle_manifest_sha256": "1" * 64,
        "recount_sha256": "2" * 64,
        "validation_report_sha256": report_info["sha256"],
        "row_schema_sha256": "3" * 64,
        "trust_root_sha256": "4" * 64,
        "verified_pins_sha256": "5" * 64,
        "source_groups_and_splits": groups_and_splits,
        "input_lineage_sha256": {group: "6" * 64 for group in groups_and_splits},
        "split_table_sha256": "7" * 64,
        "constructor_inputs_sha256": "8" * 64,
        "constructor_manifest_sha256": "9" * 64,
        "constructor_receipt_sha256": "a" * 64,
        "constructor_admission_sha256": "b" * 64,
        "constructor_recount_sha256": "c" * 64,
    }
    profile.update(profile_overrides or {})
    profile_info = _publication_write_file(rows_dir / "profile.json", json.dumps(profile, sort_keys=True).encode("utf-8"))

    candidate = {
        "commit": "d" * 40,
        "trainer_sha256": trainer_info["sha256"],
        "predictor_sha256": predictor_info["sha256"],
        "scorer_sha256": scorer_info["sha256"],
        "template_sha256": template_info["sha256"],
    }
    candidate.update(candidate_overrides or {})

    # Independent synthetic admin files: mutation of stage/binding never rewrites
    # these anchors. Tests patch fixed production locations, not a CLI override.
    global _TEST_AUTHORITY_PATHS
    authority_dir = root / 'authority'
    authority_dir.mkdir()
    authority_root = {
        'format': 'route-r-trust-root/v2r4', 'row_schema': {'sha256': '3' * 64},
        'custody': {'admins': ['codex']},
        'adapter_consumption': {
            'candidate': dict(candidate),
            **{key: {'path': 'synthetic-' + key, 'sha256': 'f' * 64}
               for key in ('bridge', 'interpreter', 'resolver_entry')},
        },
    }
    root_data = json.dumps(authority_root, sort_keys=True).encode()
    root_sha = hashlib.sha256(root_data).hexdigest()
    root_path = authority_dir / 'root.json'
    root_path.write_bytes(root_data)
    pin_path = authority_dir / 'root-pin.py'
    pin_path.write_text(f"ROOT_FILE = 'route-r-trust-root-v2r4.json'\nROOT_FORMAT = 'route-r-trust-root/v2r4'\nAPPROVED_TRUST_ROOT_SHA256 = '{root_sha}'\n")
    if not profile_overrides or 'trust_root_sha256' not in profile_overrides:
        profile['trust_root_sha256'] = root_sha
    profile_info = _publication_write_file(rows_dir / 'profile.json', json.dumps(profile, sort_keys=True).encode())
    publication_path = authority_dir / 'publication.json'
    publication_path.write_text(json.dumps({
        'format': 'route-r-publication/v1', 'batch': batch, 'published_by': 'codex',
        'publication_profile_sha256': profile_info['sha256'], 'accepted_jsonl_sha256': accepted_info['sha256'],
        'bundle_manifest_sha256': profile['bundle_manifest_sha256'], 'recount_sha256': profile['recount_sha256'],
    }, sort_keys=True))
    _TEST_AUTHORITY_PATHS = (pin_path, root_path, publication_path)

    resolution = {
        "format": "route-r-resolution/v1",
        "batch": batch,
        "verified": True,
        "accepted_jsonl_sha256": accepted_info["sha256"],
        "profile_sha256": profile_info["sha256"],
        "publication_sha256": hashlib.sha256(publication_path.read_bytes()).hexdigest(),
        "trust_root_sha256": profile["trust_root_sha256"],
        "validation_report_sha256": report_info["sha256"],
        "bridge_sha256": "f" * 64,
    }
    resolution.update(resolution_overrides or {})

    files = [
        {"dir": "rows", "name": "accepted.jsonl", **accepted_info},
        {"dir": "rows", "name": "report.json", **report_info},
        {"dir": "rows", "name": "profile.json", **profile_info},
        {"dir": "train", "name": "train_adapter.py", **trainer_info},
        {"dir": "train", "name": "prompt_template.py", **template_info},
        {"dir": "eval", "name": "predict_dev.py", **predictor_info},
        {"dir": "eval", "name": "dev_selection.py", **scorer_info},
    ]
    if files_mutator is not None:
        files = files_mutator(files)

    binding = {
        "format": "adapter-publication-binding/v1",
        "batch": batch,
        "candidate": candidate,
        "resolution": resolution,
        "root_sha256": profile["trust_root_sha256"],
        "files": files,
    }
    binding.update(binding_overrides or {})

    directories = {"rows": rows_dir, "train": code_dir, "eval": eval_dir}
    if tamper_file is not None:
        directory, name, data = tamper_file
        directories[directory].joinpath(name).write_bytes(data)
    if extra_file is not None:
        directory, name, data = extra_file
        directories[directory].joinpath(name).write_bytes(data)

    binding_path = root / "publication-binding.json"
    text = binding_text if binding_text is not None else json.dumps(binding, sort_keys=True)
    binding_path.write_text(text, encoding="utf-8")

    return {
        "binding_path": binding_path, "rows_dir": rows_dir, "code_dir": code_dir, "eval_dir": eval_dir,
        "binding": binding, "profile": profile, "report": report, "rows": rows, "row_digests": row_digests,
        "authority_paths": _TEST_AUTHORITY_PATHS,
        "train_kwargs": {"epoch_context_limit": 4096, "publication_binding_path": binding_path, "publication_rows_dir": rows_dir,
                         "publication_code_dir": code_dir, "publication_eval_dir": eval_dir},
    }


class PublicationBindingTests(AuthorityFixtureCase):
    """verify_publication_binding and the --publication-binding CLI gate."""

    def _verify(self, fx):
        return train_adapter.verify_publication_binding(
            fx["binding_path"], fx["rows_dir"], fx["code_dir"], fx["eval_dir"])

    def _load_epoch(self, fx):
        return train_adapter.load_epoch_rows(
            fx["binding_path"], fx["rows_dir"], fx["code_dir"], fx["eval_dir"])

    def test_candidate_digests_bind_whole_rows_in_producer_order(self):
        rows = copy.deepcopy(_PUBLICATION_DEFAULT_ROWS)
        rows[0]["input"] = {"text": "caf\u00e9"}
        # Independent literal vector: UTF-8, compact keys, no final newline.
        canonical = b'{"input":{"text":"caf\xc3\xa9"},"row_id":"r1","source_group":"group-a","split":"train"}'
        expected = hashlib.sha256(canonical).hexdigest()
        self.assertEqual(_publication_row_digest(rows[0]), expected)
        original = [_publication_row_digest(r) for r in rows]
        for case in ("valid", "reordered", "invented", "whole_row_changed", "newline_digest"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                supplied, actual = list(original), copy.deepcopy(rows)
                if case == "reordered": supplied.reverse()
                if case == "invented": supplied[0] = "0" * 64
                if case == "whole_row_changed": actual[0]["receipt_ref"] = "different original receipt"
                if case == "newline_digest": supplied[0] = hashlib.sha256(canonical + b"\n").hexdigest()
                fx = _build_publication_fixture(Path(tmp), rows=actual,
                    profile_overrides={"candidate_row_sha256": supplied})
                if case == "valid":
                    self.assertEqual(self._verify(fx)["batch"], fx["binding"]["batch"])
                else:
                    with self.assertRaisesRegex(SystemExit, "ordered candidate_row_sha256"):
                        self._verify(fx)

    def test_rows_in_covers_all_admitted_and_schema_valid_rows(self):
        for rows_in in (0, 2, 3, 7):
            with self.subTest(rows_in=rows_in), tempfile.TemporaryDirectory() as tmp:
                fx = _build_publication_fixture(Path(tmp), report_overrides={"rows_in": rows_in})
                if rows_in < len(_PUBLICATION_DEFAULT_ROWS):
                    with self.assertRaisesRegex(SystemExit, "rows_in is less"):
                        self._verify(fx)
                else:
                    self._verify(fx)

    def test_epoch_check_only_validation_never_writes_run_or_report(self):
        for case in ("valid", "count", "digest", "row_id", "missing_binding"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                rows = slice3_train_rows(2) + slice3_dev_rows()[:1]
                if case == "row_id": rows[-1]["row_id"] = "0" * 16
                fx = _build_publication_fixture(root, rows=rows,
                    report_overrides={"rows_in": 0} if case == "count" else None,
                    profile_overrides={"candidate_row_sha256": ["0" * 64, "1" * 64, "2" * 64]} if case == "digest" else None)
                if case == "missing_binding": fx["binding_path"].unlink()
                report = root / "reports" / "m2-check.json"
                models = root / "models"
                def snapshot():
                    return {p.relative_to(root).as_posix(): p.read_bytes() if p.is_file() else None
                            for p in root.rglob("*")}
                before = snapshot()
                argv = ["--rows", str(fx["rows_dir"] / "accepted.jsonl"), "--report", str(report),
                    "--epochs", "1", "--check-only", "--base", train_adapter.WINNER_BASE,
                    "--revision", train_adapter.WINNER_REVISION, "--epoch-context-limit", "4096",
                    "--publication-binding", str(fx["binding_path"])]
                with mock.patch.object(train_adapter, "__file__", str(fx["code_dir"] / "train_adapter.py")), \
                     mock.patch.dict(os.environ, {"ADAPTER_MODELS_DIR": str(models)}), \
                     mock.patch.object(train_adapter, "FastLanguageModel") as model, \
                     mock.patch.object(train_adapter.torch, "cuda", mock.Mock(), create=True) as cuda, \
                     contextlib.redirect_stdout(io.StringIO()):
                    if case == "valid": train_adapter.main(argv)
                    else:
                        with self.assertRaises(ValueError if case == "row_id" else SystemExit):
                            train_adapter.main(argv)
                self.assertEqual(snapshot(), before)
                self.assertFalse(models.exists())
                self.assertFalse(report.parent.exists())
                self.assertEqual(model.mock_calls, [])
                self.assertEqual(cuda.mock_calls, [])

    def test_admitted_producer_rows_reach_epoch_check_only_without_legacy_schema(self):
        # Regression for the retained authenticated-chain RED: two training
        # rows share one group; the producer's splits are group counts.
        rows = slice3_train_rows(2) + slice3_dev_rows()[:1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fx = _build_publication_fixture(root, rows=rows)
            publication, training, dev = self._load_epoch(fx)
            self.assertEqual(publication, self._verify(fx))
            self.assertEqual(training, rows[:2])
            self.assertEqual(dev, rows[2:])
            self.assertEqual(fx["report"]["splits"], {"train": 1, "dev": 1})
            self.assertNotIn("rows_per_split", fx["report"])
            output = io.StringIO()
            with mock.patch.object(train_adapter, "__file__", str(fx["code_dir"] / "train_adapter.py")), \
                 mock.patch.object(train_adapter, "load_training_rows", side_effect=AssertionError("legacy loader")), \
                 mock.patch.object(train_adapter, "load_dev_rows", side_effect=AssertionError("legacy loader")), \
                 mock.patch.object(train_adapter, "train") as training_run, \
                 mock.patch.object(train_adapter, "FastLanguageModel") as model, \
                 mock.patch.object(train_adapter.torch, "cuda", mock.Mock(), create=True) as cuda, \
                 contextlib.redirect_stdout(output):
                train_adapter.main(["--rows", str(fx["rows_dir"] / "accepted.jsonl"),
                    "--report", str(root / "out.json"), "--epochs", "1", "--check-only",
                    "--base", train_adapter.WINNER_BASE, "--revision", train_adapter.WINNER_REVISION,
                    "--epoch-context-limit", "4096", "--publication-binding", str(fx["binding_path"])])
            self.assertEqual(json.loads(output.getvalue()), {"train_rows": 2, "check_only": True})
            training_run.assert_not_called()
            self.assertEqual(model.mock_calls, [])
            self.assertEqual(cuda.mock_calls, [])
            self.assertFalse((root / "out.json").exists())

    def test_epoch_consumption_reads_twice_before_parsing_and_never_rereads(self):
        rows = slice3_train_rows(2) + slice3_dev_rows()[:1]
        with tempfile.TemporaryDirectory() as tmp:
            fx = _build_publication_fixture(Path(tmp), rows=rows)
            accepted = fx["rows_dir"] / "accepted.jsonl"
            raw_read, raw_parse = Path.read_bytes, train_adapter._load_strict_json
            reads, parsed = [], []
            def read(path):
                data = raw_read(path)
                if path == accepted:
                    reads.append(data)
                    self.assertLessEqual(len(reads), 2, "third accepted-row read")
                    if len(reads) == 2:
                        # A later disk mutation must not replace consumed bytes.
                        path.write_bytes(b"not the admitted rows")
                return data
            def parse(text):
                if text in [json.dumps(r, sort_keys=True) for r in rows]:
                    self.assertEqual(len(reads), 2)
                    parsed.append(text)
                return raw_parse(text)
            with mock.patch.object(Path, "read_bytes", read), \
                 mock.patch.object(train_adapter, "_load_strict_json", side_effect=parse):
                _, training, dev = self._load_epoch(fx)
            self.assertEqual(training + dev, rows)
            self.assertEqual(len(parsed), len(rows))
            self.assertEqual(len(reads), 2)
            self.assertTrue(all(hashlib.sha256(data).hexdigest() == fx["binding"]["resolution"]["accepted_jsonl_sha256"]
                                for data in reads))

    def test_epoch_digest_mutation_refuses_before_any_row_parse(self):
        rows = slice3_train_rows(2) + slice3_dev_rows()[:1]
        for mutation_read in (1, 2):
            for changed in (b"x", None):
                with self.subTest(read=mutation_read, changed_length=changed is not None), \
                     tempfile.TemporaryDirectory() as tmp:
                    fx = _build_publication_fixture(Path(tmp), rows=rows)
                    accepted = fx["rows_dir"] / "accepted.jsonl"
                    original = accepted.read_bytes()
                    bad = changed if changed is not None else original.replace(b"train-synthetic-00", b"train-synthetic-99")
                    self.assertNotEqual(bad, original)
                    raw_read, raw_parse = Path.read_bytes, train_adapter._load_strict_json
                    count = 0
                    row_parses = []
                    def read(path):
                        nonlocal count
                        if path == accepted:
                            count += 1
                            if count == mutation_read:
                                path.write_bytes(bad)
                        return raw_read(path)
                    def parse(text):
                        if text in [json.dumps(r, sort_keys=True) for r in rows]:
                            row_parses.append(text)
                        return raw_parse(text)
                    with mock.patch.object(Path, "read_bytes", read), \
                         mock.patch.object(train_adapter, "_load_strict_json", side_effect=parse):
                        with self.assertRaises(SystemExit):
                            self._load_epoch(fx)
                    self.assertEqual(row_parses, [])
                    self.assertEqual(count, mutation_read)

    def test_epoch_report_requires_exact_producer_format(self):
        for value in ("route-r-validation-report/v2", "other/v1", "", None, 1):
            with self.subTest(format=value), tempfile.TemporaryDirectory() as tmp:
                fx = _build_publication_fixture(Path(tmp), rows=slice3_train_rows(2) + slice3_dev_rows()[:1], report_overrides={"format": value})
                with self.assertRaisesRegex(SystemExit, "format must be route-r-validation-report/v1"):
                    self._load_epoch(fx)
        with tempfile.TemporaryDirectory() as tmp:
            self._load_epoch(_build_publication_fixture(Path(tmp), rows=slice3_train_rows(2) + slice3_dev_rows()[:1]))

    def test_epoch_consumer_refuses_admitted_row_and_group_count_disagreement(self):
        rows = slice3_train_rows(2) + slice3_dev_rows()[:1]
        for override in ({"rows_admitted": 2}, {"schema_valid": 2},
                         {"splits": {"train": 2, "dev": 1}}, {"splits": {"train": 1, "dev": 0}},
                         {"rows_admitted": True}, {"schema_valid": 3.0}):
            with self.subTest(override=override), tempfile.TemporaryDirectory() as tmp:
                fx = _build_publication_fixture(Path(tmp), rows=rows, report_overrides=override)
                with self.assertRaises(SystemExit):
                    self._load_epoch(fx)

    def test_direct_epoch_train_refuses_caller_row_substitution_before_gpu(self):
        for split in ("train", "dev"):
            with self.subTest(split=split), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                rows, dev = slice3_train_rows(), slice3_dev_rows()
                fx = _build_publication_fixture(root, rows=rows + dev)
                supplied_rows, supplied_dev = json.loads(json.dumps(rows)), json.loads(json.dumps(dev))
                changed = supplied_rows if split == "train" else supplied_dev
                changed[0]["input"]["title"] = "not the authenticated input"
                with mock.patch.dict(os.environ, {"ADAPTER_MODELS_DIR": str(root / "models")}), \
                     mock.patch.object(train_adapter, "set_seed") as seed, \
                     mock.patch.object(train_adapter, "FastLanguageModel") as model, \
                     mock.patch.object(train_adapter.torch, "cuda", mock.Mock(), create=True) as cuda, \
                     mock.patch.object(train_adapter, "digest_verification_before_load") as digests:
                    with self.assertRaisesRegex(ValueError, "caller rows differ"):
                        train_adapter.train(supplied_rows, supplied_dev, train_adapter.WINNER_BASE,
                            train_adapter.WINNER_REVISION, None, root / "out.json", winner=False,
                            epochs=1, **fx["train_kwargs"])
                seed.assert_not_called()
                digests.assert_not_called()
                self.assertEqual(model.mock_calls, [])
                self.assertEqual(cuda.mock_calls, [])

    def test_real_epoch_main_authenticates_once_before_model_or_cuda(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fx = _build_publication_fixture(root, rows=slice3_train_rows() + slice3_dev_rows())
            accepted = fx["rows_dir"] / "accepted.jsonl"
            raw_read = Path.read_bytes
            reads = []
            stop = RuntimeError("authenticated epoch reached model-digest gate")
            def read(path):
                data = raw_read(path)
                if path == accepted:
                    reads.append(data)
                return data
            with mock.patch.object(train_adapter, "__file__", str(fx["code_dir"] / "train_adapter.py")), \
                 mock.patch.dict(os.environ, {"ADAPTER_MODELS_DIR": str(root / "models")}), \
                 mock.patch.object(Path, "read_bytes", read), \
                 mock.patch.object(train_adapter, "load_training_rows", side_effect=AssertionError("legacy loader")), \
                 mock.patch.object(train_adapter, "load_dev_rows", side_effect=AssertionError("legacy loader")), \
                 mock.patch.object(train_adapter, "digest_verification_before_load", side_effect=stop), \
                 mock.patch.object(train_adapter, "set_seed") as seed, \
                 mock.patch.object(train_adapter, "FastLanguageModel") as model, \
                 mock.patch.object(train_adapter.torch, "cuda", mock.Mock(), create=True) as cuda:
                with self.assertRaises(RuntimeError) as caught:
                    train_adapter.main(["--rows", str(accepted), "--report", str(root / "out.json"),
                        "--epochs", "1", "--base", train_adapter.WINNER_BASE,
                        "--revision", train_adapter.WINNER_REVISION,
                        "--epoch-context-limit", "4096", "--publication-binding", str(fx["binding_path"])])
            self.assertIs(caught.exception, stop)
            self.assertEqual(len(reads), 2)
            seed.assert_not_called()
            self.assertEqual(model.mock_calls, [])
            self.assertEqual(cuda.mock_calls, [])

    def test_valid_binding_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = _build_publication_fixture(Path(tmp))
            result = self._verify(fx)
            self.assertEqual(result, {
                "batch": fx["binding"]["batch"],
                "profile_sha256": fx["binding"]["resolution"]["profile_sha256"],
                "root_sha256": fx["binding"]["root_sha256"],
            })

    def test_main_requires_publication_binding_in_epoch_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                train_adapter.main([
                    "--rows", str(Path(tmp, "missing-accepted.jsonl")),
                    "--report", str(Path(tmp, "out.json")),
                    "--base", train_adapter.WINNER_BASE, "--revision", train_adapter.WINNER_REVISION,
                    "--epochs", "1",
                ])

    def test_main_refuses_publication_binding_outside_epoch_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                train_adapter.main([
                    "--rows", str(Path(tmp, "missing-accepted.jsonl")),
                    "--report", str(Path(tmp, "out.json")),
                    "--steps", "20",
                    "--epoch-context-limit", "4096", "--publication-binding", str(Path(tmp, "missing-binding.json")),
                ])

    def test_binding_duplicate_key_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = _build_publication_fixture(Path(tmp))
            text = fx["binding_path"].read_text(encoding="utf-8")
            fx["binding_path"].write_text(
                _splice_json_object(text, f'"batch": {json.dumps(fx["binding"]["batch"])}'), encoding="utf-8")
            with self.assertRaises(SystemExit):
                self._verify(fx)

    def test_binding_extra_key_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = _build_publication_fixture(Path(tmp))
            text = fx["binding_path"].read_text(encoding="utf-8")
            fx["binding_path"].write_text(_splice_json_object(text, '"extra": 1'), encoding="utf-8")
            with self.assertRaises(SystemExit):
                self._verify(fx)

    def test_binding_wrong_type_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = _build_publication_fixture(Path(tmp), binding_overrides={"batch": 123})
            with self.assertRaises(SystemExit):
                self._verify(fx)

    def test_payload_file_byte_tamper_refused(self):
        for directory, name in (
            ("rows", "accepted.jsonl"), ("rows", "report.json"), ("rows", "profile.json"),
            ("train", "train_adapter.py"), ("train", "prompt_template.py"),
            ("eval", "predict_dev.py"), ("eval", "dev_selection.py"),
        ):
            with self.subTest(directory=directory, name=name), tempfile.TemporaryDirectory() as tmp:
                fx = _build_publication_fixture(Path(tmp), tamper_file=(directory, name, b"tampered bytes\n"))
                with self.assertRaises(SystemExit):
                    self._verify(fx)

    def test_payload_file_wrong_byte_count_refused(self):
        def mutate(files):
            for entry in files:
                if entry["dir"] == "rows" and entry["name"] == "accepted.jsonl":
                    entry["bytes"] += 1
            return files
        with tempfile.TemporaryDirectory() as tmp:
            fx = _build_publication_fixture(Path(tmp), files_mutator=mutate)
            with self.assertRaises(SystemExit):
                self._verify(fx)

    def test_extra_file_in_directory_refused(self):
        for directory in ("rows", "train", "eval"):
            with self.subTest(directory=directory), tempfile.TemporaryDirectory() as tmp:
                fx = _build_publication_fixture(Path(tmp), extra_file=(directory, "__pycache__", b""))
                with self.assertRaises(SystemExit):
                    self._verify(fx)

    def test_extra_file_outside_staged_directories_is_not_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = _build_publication_fixture(Path(tmp))
            (Path(tmp) / "unrelated.txt").write_bytes(b"not part of any staged directory\n")
            self._verify(fx)  # must not raise

    def test_renderer_hash_mismatch_refused(self):
        def mutate(files):
            for entry in files:
                if entry["dir"] == "train" and entry["name"] == "prompt_template.py":
                    entry["sha256"] = hashlib.sha256(b"not the admitted renderer\n").hexdigest()
                    entry["bytes"] = len(b"not the admitted renderer\n")
            return files
        with tempfile.TemporaryDirectory() as tmp:
            fx = _build_publication_fixture(
                Path(tmp), files_mutator=mutate,
                tamper_file=("train", "prompt_template.py", b"not the admitted renderer\n"),
                candidate_overrides={"template_sha256": hashlib.sha256(b"not the admitted renderer\n").hexdigest()})
            with self.assertRaises(SystemExit):
                self._verify(fx)

    def test_profile_accepted_split_mismatch_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = _build_publication_fixture(Path(tmp), profile_overrides={
                "source_groups_and_splits": {"group-a": "dev", "group-b": "dev"},
                "input_lineage_sha256": {"group-a": "6" * 64, "group-b": "6" * 64},
            })
            with self.assertRaises(SystemExit):
                self._verify(fx)

    def test_missing_group_in_accepted_rows_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = _build_publication_fixture(Path(tmp), profile_overrides={
                "source_groups_and_splits": {"group-a": "train", "group-b": "dev", "group-c": "train"},
                "input_lineage_sha256": {"group-a": "6" * 64, "group-b": "6" * 64, "group-c": "6" * 64},
            })
            with self.assertRaises(SystemExit):
                self._verify(fx)

    def test_unique_group_count_mismatch_versus_report_splits_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = _build_publication_fixture(Path(tmp), report_overrides={"splits": {"train": 2, "dev": 1}})
            with self.assertRaises(SystemExit):
                self._verify(fx)

    def test_rows_admitted_mismatch_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = _build_publication_fixture(Path(tmp), report_overrides={"rows_admitted": 4})
            with self.assertRaises(SystemExit):
                self._verify(fx)

    def test_schema_valid_mismatch_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = _build_publication_fixture(Path(tmp), report_overrides={"schema_valid": 4})
            with self.assertRaises(SystemExit):
                self._verify(fx)

    def test_input_lineage_not_object_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = _build_publication_fixture(Path(tmp), profile_overrides={"input_lineage_sha256": ["not", "an", "object"]})
            with self.assertRaises(SystemExit):
                self._verify(fx)

    def test_input_lineage_key_set_mismatch_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = _build_publication_fixture(Path(tmp), profile_overrides={"input_lineage_sha256": {"wrong-group": "6" * 64}})
            with self.assertRaises(SystemExit):
                self._verify(fx)

    def test_candidate_row_sha256_not_list_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = _build_publication_fixture(Path(tmp), profile_overrides={"candidate_row_sha256": "not-a-list"})
            with self.assertRaises(SystemExit):
                self._verify(fx)

    def test_candidate_row_sha256_duplicate_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            digest = _publication_row_digest(_PUBLICATION_DEFAULT_ROWS[0])
            fx = _build_publication_fixture(Path(tmp), profile_overrides={"candidate_row_sha256": [digest, digest, digest]})
            with self.assertRaises(SystemExit):
                self._verify(fx)

    def test_files_entry_duplicate_refused(self):
        def mutate(files):
            return files + [dict(files[0])]
        with tempfile.TemporaryDirectory() as tmp:
            fx = _build_publication_fixture(Path(tmp), files_mutator=mutate)
            with self.assertRaises(SystemExit):
                self._verify(fx)

    def test_files_entry_missing_refused(self):
        def mutate(files):
            return [entry for entry in files if entry["name"] != "prompt_template.py"]
        with tempfile.TemporaryDirectory() as tmp:
            fx = _build_publication_fixture(Path(tmp), files_mutator=mutate)
            with self.assertRaises(SystemExit):
                self._verify(fx)

    def test_files_entry_extra_refused(self):
        def mutate(files):
            return files + [{"dir": "rows", "name": "extra.json", "sha256": "0" * 64, "bytes": 0}]
        with tempfile.TemporaryDirectory() as tmp:
            fx = _build_publication_fixture(Path(tmp), files_mutator=mutate)
            with self.assertRaises(SystemExit):
                self._verify(fx)

    def test_files_entry_for_binding_itself_refused(self):
        def mutate(files):
            return files + [{"dir": "rows", "name": "publication-binding.json", "sha256": "0" * 64, "bytes": 0}]
        with tempfile.TemporaryDirectory() as tmp:
            fx = _build_publication_fixture(Path(tmp), files_mutator=mutate)
            with self.assertRaises(SystemExit):
                self._verify(fx)

    def test_files_entry_unsafe_name_refused(self):
        def mutate(files):
            return files + [{"dir": "train", "name": "../evil.py", "sha256": "0" * 64, "bytes": 0}]
        with tempfile.TemporaryDirectory() as tmp:
            fx = _build_publication_fixture(Path(tmp), files_mutator=mutate)
            with self.assertRaises(SystemExit):
                self._verify(fx)

    def test_candidate_hash_disagrees_with_files_entry_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = _build_publication_fixture(Path(tmp), candidate_overrides={"trainer_sha256": "0" * 64})
            with self.assertRaises(SystemExit):
                self._verify(fx)

    def test_resolution_hash_disagrees_with_files_entry_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = _build_publication_fixture(Path(tmp), resolution_overrides={"accepted_jsonl_sha256": "0" * 64})
            with self.assertRaises(SystemExit):
                self._verify(fx)

    def test_verification_runs_before_model_load(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            root = Path(tmp)
            rows_dir, code_dir, eval_dir = root / "rows", root / "train", root / "eval"
            for d in (rows_dir, code_dir, eval_dir):
                d.mkdir()
            stack.enter_context(mock.patch.dict(os.environ, {"ADAPTER_MODELS_DIR": str(root / "models")}))
            fake_flm = mock.MagicMock()
            fake_flm.from_pretrained = mock.MagicMock()
            stack.enter_context(mock.patch.object(train_adapter, "FastLanguageModel", fake_flm))
            with self.assertRaises(SystemExit):
                train_adapter.train(
                    [], [], train_adapter.WINNER_BASE, train_adapter.WINNER_REVISION, None,
                    root / "report.json", winner=False, epochs=1,
                    publication_binding_path=root / "missing-publication-binding.json",
                    publication_rows_dir=rows_dir, publication_code_dir=code_dir, publication_eval_dir=eval_dir,
                )
            fake_flm.from_pretrained.assert_not_called()

    @unittest.skipUnless(
        os.environ.get("ADAPTER_TEST_STAGE_DIR"),
        "set ADAPTER_TEST_STAGE_DIR to a tree built by scripts/test-train-adapter.ps1 to run this probe",
    )
    def test_wrapper_staged_tree_verifies(self):
        import epoch_authority
        stage = Path(os.environ["ADAPTER_TEST_STAGE_DIR"])
        # Test-only host fixture transport; production has no anchor override.
        paths = json.loads(os.environ["ADAPTER_TEST_AUTHORITY_PATHS"])
        self.assertEqual(len(paths), 3)
        self.assertTrue(all(isinstance(path, str) and Path(path).is_absolute() for path in paths))
        with mock.patch.object(epoch_authority, 'production_paths', return_value=tuple(map(Path, paths))):
            result = train_adapter.verify_publication_binding(
                stage / "Meta" / "publication-binding.json", stage / "Input", stage / "Train", stage / "Eval")
        self.assertEqual(set(result), {"batch", "profile_sha256", "root_sha256"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
# [[Cursor | Auto | 2026-09-29 | 01:10 UTC | END]]
# [[Cursor | Auto | 2026-09-29 | 01:12 UTC | END]]
# [[Cursor | Auto | 2026-09-29 | 04:03 UTC | END]]
# [[Cursor | Auto | 2026-09-29 | 04:18 UTC | END]]
