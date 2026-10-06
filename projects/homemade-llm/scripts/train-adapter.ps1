# [[Cursor | Auto | 2026-09-29 | 01:10 UTC | BEGIN]]
# M2-T1: run train/train_adapter.py inside the pinned Unsloth image.
#   .\train-adapter.ps1 -Rows C:\llm\datasets\director-v1\accepted.jsonl
#       20-step dry run on the smoke base (the M0 pins).
#   .\train-adapter.ps1 -Rows <jsonl> -Winner -Probe
#       D-M2-6 probe on Qwen/Qwen3-14B at 40c069824f4251a91eefaf281ebe4c544efd3e18.
#   .\train-adapter.ps1 -Rows <jsonl> -Batch <published-batch-id> -Epochs <N> -Base 'Qwen/Qwen3-14B' `
#       -Revision '40c069824f4251a91eefaf281ebe4c544efd3e18' `
#       -EpochContextLimit <measured-total-positions> -ExpectedDigests <approved-list> -TimeoutMinutes <explicit-approved-bound>
#       Epoch-mode training: one adapter leaf per epoch under models\m2\<run id>.
#       Epoch summaries are saved under reports\adapter-output\<run id>.json.
#   .\train-adapter.ps1 -Rows <accepted.jsonl> -Winner -Probe -ProbeContextInputs <inputs.jsonl> `
#       -ProbeContextLimit <measured-prompt-max-plus-2048> -TimeoutMinutes <explicit-bound>
#       Separate context probe: one full generation, output discarded; Director gate still required.
#       Smoke/probe reports and trainer scratch are under reports\adapter-output\<run id>.
# -Winner is only valid together with -Probe; -Epochs is a training mode and
#   excludes -Steps, -Probe, -Winner and -Fallback.
# Refuses a short revision, a winner probe pointed at any other base, a hot root
# off C:, a stored Hugging Face token in the mounted cache, and a second GPU job.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Rows,
    [string]$Batch,
    [string]$Image = 'unsloth/unsloth@sha256:1644d635bc7c5b57ed64cabbab1ae00647dfb583c2135fdfb6a461aeeba52739',
    [string]$HotRoot = 'C:\llm',
    [string]$Base = 'unsloth/qwen3-0.6b-unsloth-bnb-4bit',
    [string]$Revision = 'e86f5289a2ca40ab7114071959dc2079cf97cb3c',
    [string]$MergeSource = 'unsloth/qwen3-0.6b',
    [string]$MergeRevision = 'db4e90b8359f816f09bf5c944797ce116f26da3c',
    [int]$Steps = 20,
    [int]$Epochs = 0,
    [int]$EpochContextLimit,
    [switch]$Winner,
    [switch]$Fallback,
    [switch]$Probe,
    [string]$ExpectedDigests,
    [int]$ProbeRowsTotal,
    [string]$ProbeContextInputs,
    [int]$ProbeContextLimit,
    [int]$ProbeSeqLen = 2048,
    [double]$TimeoutMinutes = 90
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'invoke-bounded.ps1')
. (Join-Path $PSScriptRoot 'train-adapter-stage.ps1')

$WinnerBase = 'Qwen/Qwen3-14B'
$WinnerRevision = '40c069824f4251a91eefaf281ebe4c544efd3e18'
$FallbackBase = 'Qwen/Qwen3-8B'
$FallbackRevision = 'b968826d9c46dd6066d109eabc6255188de91218'
if ($Winner -and $Fallback) { Write-Error '-Winner and -Fallback are mutually exclusive' }
if ($Winner -and -not $Probe) { Write-Error '-Winner is only valid together with -Probe' }
if ($Fallback -and -not $Probe) { Write-Error '-Fallback is only valid together with -Probe' }
if ($PSBoundParameters.ContainsKey('Epochs')) {
    Assert-ApprovedEpochImage -Image $Image
    if ($Epochs -lt 1 -or $Epochs -gt 6) { Write-Error '-Epochs must be between 1 and 6' }
    if ($PSBoundParameters.ContainsKey('Steps')) { Write-Error '-Epochs and -Steps are mutually exclusive; pass exactly one' }
    if ($Probe) { Write-Error '-Epochs is a training mode and cannot be combined with -Probe' }
    if ($Winner) { Write-Error '-Epochs cannot be combined with -Winner (winner pinning is probe-only)' }
    if ($Fallback) { Write-Error '-Epochs cannot be combined with -Fallback' }
    if (-not $PSBoundParameters.ContainsKey('TimeoutMinutes')) { Write-Error '-Epochs requires explicit positive -TimeoutMinutes' }
    if (-not $PSBoundParameters.ContainsKey('Base') -or -not $PSBoundParameters.ContainsKey('Revision') -or
        -not (($Base -ceq $WinnerBase -and $Revision -ceq $WinnerRevision) -or
              ($Base -ceq $FallbackBase -and $Revision -ceq $FallbackRevision))) {
        Write-Error "-Epochs requires explicit -Base and -Revision for $WinnerBase@$WinnerRevision or $FallbackBase@$FallbackRevision"
    }
    if (-not $PSBoundParameters.ContainsKey('Batch')) { Write-Error '-Epochs requires -Batch (the publication batch id to resolve and stage)' }
    if (-not $PSBoundParameters.ContainsKey('EpochContextLimit') -or $EpochContextLimit -le 2048) { Write-Error '-Epochs requires explicit -EpochContextLimit greater than max_new_tokens' }
    if ($Batch -cnotmatch '^[a-z0-9][a-z0-9-]{0,63}$') { Write-Error "-Batch is not a valid batch id: $Batch" }
}
if ($PSBoundParameters.ContainsKey('EpochContextLimit') -and -not $PSBoundParameters.ContainsKey('Epochs')) { Write-Error '-EpochContextLimit is only valid with -Epochs' }
if ($PSBoundParameters.ContainsKey('Batch') -and -not $PSBoundParameters.ContainsKey('Epochs')) {
    Write-Error '-Batch is only valid with -Epochs'
}
if ($PSBoundParameters.ContainsKey('ProbeContextInputs') -or $PSBoundParameters.ContainsKey('ProbeContextLimit')) {
    if (-not $Probe -or [string]::IsNullOrWhiteSpace($ProbeContextInputs) -or $ProbeContextLimit -le 2048) { Write-Error 'context probe requires -Probe -ProbeContextInputs and explicit -ProbeContextLimit greater than 2048' }
    $ProbeContextInputs = [IO.Path]::GetFullPath($ProbeContextInputs)
    if (-not (Test-Path -LiteralPath $ProbeContextInputs -PathType Leaf)) { Write-Error 'context probe input file is missing' }
}
if ($Probe -and -not $PSBoundParameters.ContainsKey('TimeoutMinutes')) { $TimeoutMinutes = 150 }
if ([double]::IsNaN($TimeoutMinutes) -or [double]::IsInfinity($TimeoutMinutes) -or $TimeoutMinutes -le 0) {
    Write-Error '-TimeoutMinutes must be finite and positive'
}
if ($Winner) {
    foreach ($name in 'Base', 'Revision', 'MergeSource', 'MergeRevision') {
        if ($PSBoundParameters.ContainsKey($name)) { Write-Error "-Winner is pinned to $WinnerBase@$WinnerRevision; do not pass -$name" }
    }
    $Base = $WinnerBase
    $Revision = $WinnerRevision
    $MergeSource = $WinnerBase
    $MergeRevision = $WinnerRevision
}
if ($Fallback) {
    foreach ($name in 'Base', 'Revision', 'MergeSource', 'MergeRevision') {
        if ($PSBoundParameters.ContainsKey($name)) { Write-Error "-Fallback is pinned to $FallbackBase@$FallbackRevision; do not pass -$name" }
    }
    $Base = $FallbackBase
    $Revision = $FallbackRevision
    $MergeSource = $FallbackBase
    $MergeRevision = $FallbackRevision
}
if ($Steps -lt 1) { Write-Error '-Steps must be positive' }
if ($PSBoundParameters.ContainsKey('ProbeRowsTotal') -and $ProbeRowsTotal -lt 1) { Write-Error '-ProbeRowsTotal must be positive' }
if ($ProbeSeqLen -lt 1) { Write-Error '-ProbeSeqLen must be positive' }
foreach ($r in $Revision, $MergeRevision) { if ($r -notmatch '^[0-9a-f]{40}$') { Write-Error "revision '$r' is not a full 40-hex commit" } }
$Rows = [IO.Path]::GetFullPath($Rows)
if ((Split-Path -Leaf $Rows) -ne 'accepted.jsonl') { Write-Error "trainer reads the validator's accepted.jsonl, not raw rows: $Rows" }
if (-not (Test-Path -LiteralPath $Rows -PathType Leaf)) { Write-Error "training rows not found: $Rows" }
if (-not (Test-Path -LiteralPath (Join-Path (Split-Path $Rows -Parent) 'report.json') -PathType Leaf)) { Write-Error "validator report.json is missing beside accepted.jsonl" }
if ($Rows -match '\\secrets\\') { Write-Error "refusing to mount a rows path under secrets: $Rows" }

$HotRoot = [IO.Path]::GetFullPath($HotRoot)
if (-not $HotRoot.StartsWith('C:\', [StringComparison]::OrdinalIgnoreCase)) { Write-Error "HotRoot must be on C: (preflight gates only C:); got $HotRoot" }
if ($Winner -and [string]::IsNullOrWhiteSpace($ExpectedDigests)) {
    $ExpectedDigests = Join-Path $HotRoot 'reports\m2-expected-qwen3-14b-v2.json'
}
if ($Fallback -and [string]::IsNullOrWhiteSpace($ExpectedDigests)) {
    $ExpectedDigests = Join-Path $HotRoot 'reports\m1-expected-qwen3-8b.json'
}
# The approved root has a different schema from ExpectedDigests. Authenticate
# both byte sequences before GPU ownership or any model/cache access.
# The trainer also requires approved digests for every snapshot file, including
# tokenizer/config/index metadata; a weights-only list cannot authorize a load.
$TrustedDigestRoot = ''
$TrustedDigestRootSHA256 = 'd1a4a2d921c3f5197c2867dcdb50dbfb46d2189bbb88141ed56d3f3eb19d36af'
if ($Base -eq $WinnerBase -or $Base -eq $FallbackBase) {
    $TrustedDigestRoot = 'C:\llm\reports\m2-trusted-digest-manifest-v2.json'
    $rootBytes = [IO.File]::ReadAllBytes($TrustedDigestRoot)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $rootHash = [Convert]::ToHexString($sha.ComputeHash($rootBytes)).ToLowerInvariant()
        if ($rootHash -cne $TrustedDigestRootSHA256) { Write-Error 'trusted digest root does not match the approved SHA-256' }
        $rootRecord = [Text.Encoding]::UTF8.GetString($rootBytes) | ConvertFrom-Json
        $approved = @($rootRecord.models | Where-Object { $_.hf_repo -ceq $Base -and $_.hf_revision -ceq $Revision })
        if ($approved.Count -ne 1) { Write-Error "trusted digest root has no unique approved entry for $Base@$Revision" }
        $upstreamSHA = $approved[0].upstream_weight_digests_sha256
        if ($upstreamSHA -cnotmatch '^[0-9a-f]{64}$') { Write-Error 'approved model entry has no valid upstream digest-list SHA-256' }
        if ([string]::IsNullOrWhiteSpace($ExpectedDigests)) { Write-Error '-ExpectedDigests is required for pinned M2 models' }
        $ExpectedDigests = [IO.Path]::GetFullPath($ExpectedDigests)
        $listBytes = [IO.File]::ReadAllBytes($ExpectedDigests)
        $listHash = [Convert]::ToHexString($sha.ComputeHash($listBytes)).ToLowerInvariant()
        if ($listHash -cne $upstreamSHA) { Write-Error 'upstream digest list does not match the approved root' }
    } finally { $sha.Dispose() }
}
if (-not [string]::IsNullOrWhiteSpace($ExpectedDigests)) {
    $ExpectedDigests = [IO.Path]::GetFullPath($ExpectedDigests)
    if (-not (Test-Path -LiteralPath $ExpectedDigests -PathType Leaf)) { Write-Error "expected digests file not found: $ExpectedDigests" }
}
foreach ($sub in '', 'hf', 'runs', 'models', 'models\m2', 'reports') {
    for ($p = Join-Path $HotRoot $sub; $p; $p = Split-Path $p -Parent) {
        if ((Test-Path -LiteralPath $p) -and ((Get-Item -LiteralPath $p -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            Write-Error "$p is a junction or symbolic link; HotRoot and its mounted folders must be real folders on C:"
        }
    }
}
foreach ($t in 'token', 'stored_tokens') {
    if (Test-Path -LiteralPath (Join-Path $HotRoot "hf\$t")) { Write-Error "$HotRoot\hf\$t exists (a stored Hugging Face credential); move it out of the mounted cache before training" }
}

# Epoch storage admission must precede even common-mount directory creation.
# Keep the final prelaunch check below: staging can change available storage.
if ($Epochs) {
    $preStage = @(Invoke-Bounded 'pwsh' @('-NoProfile', '-File', (Join-Path $PSScriptRoot 'preflight.ps1'), '-HotRoot', $HotRoot) 180)
    $preStage | Where-Object { $_ -notmatch '^__EXIT__' } | Out-Host
    if ($preStage[-1] -ne '__EXIT__0') { Write-Error "pre-stage preflight did not pass ($($preStage[-1] -replace '^__EXIT__', 'exit ')); not staging training" }
}

# Steps 1-5 (resolve the batch, read its custody records, stage the admitted
# code and verified rows, bind them, and build the training image's argument
# vector) complete before the GPU lock: none of it needs GPU ownership, and a
# run that cannot be authenticated must never reach the point of poisoning the
# GPU marker. $mounts/$code/$evalCode/$name/$runId are shared with the
# non-epoch argument vector built later, once the lock and preflight pass.
# Epoch mode mounts the staged Input tree as /workspace/rows (New-TrainerDockerArgs),
# never the caller's rows directory; smoke and probe keep the caller rows mount.
$runId = 'm2-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
$mounts = @()
if ($Epochs) {
    $mounts = New-TrainerCommonMounts -HotRoot $HotRoot -RowsDir (Split-Path $Rows -Parent) `
        -ExpectedDigests $ExpectedDigests -TrustedDigestRoot $TrustedDigestRoot -EpochMode:([bool]$Epochs) -ContextProbe:(-not [string]::IsNullOrWhiteSpace($ProbeContextInputs)) -RunId $runId
}
$code = (Resolve-Path (Join-Path $PSScriptRoot '..\train')).Path
$evalCode = (Resolve-Path (Join-Path $PSScriptRoot '..\eval')).Path
$name = 'hl-adapter-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
# A context probe authenticates the admitted LF renderer, but this working tree
# is CRLF: its renderer is staged from Git after admission (below), as epoch
# mode does. Resolving the repository here is read-only.
$repoRoot = $null
if (-not [string]::IsNullOrWhiteSpace($ProbeContextInputs)) {
    $contextRootOut = @(Invoke-Bounded 'git' @('-C', $PSScriptRoot, 'rev-parse', '--show-toplevel') 30)
    $contextRootLines = @(if ($contextRootOut.Count -gt 1) { $contextRootOut[0..($contextRootOut.Count - 2)] } else { @() })
    if ($contextRootOut[-1] -ne '__EXIT__0' -or $contextRootLines.Count -ne 1) { Write-Error 'could not resolve the repository root for the admitted renderer' }
    $repoRoot = $contextRootLines[0]
}

$epochDockerArgs = $null
if ($Epochs) {
    $locations = Get-ProductionLocations
    $pin = Read-RootPin -PinFile $locations.PinFile
    $root = Read-ApprovedRoot -CustodyRoot $locations.CustodyRoot -Pin $pin
    $resolution = Invoke-PublicationResolver -Root $root -ApprovedTrustRootSHA256 $pin.APPROVED_TRUST_ROOT_SHA256 -Batch $Batch
    $records = Read-PublicationRecords -CustodyRoot $locations.CustodyRoot -Batch $Batch -Resolution $resolution -Rows $Rows
    if ($records.Profile['row_schema_sha256'] -cne $root['row_schema']['sha256']) { Write-Error 'publication row schema differs from approved contract' }
    # The candidate commit is assumed to live in the repository this worktree
    # belongs to; worktrees share one object database, so any worktree root
    # resolves any reachable commit's blobs.
    $repoRootOut = @(Invoke-Bounded 'git' @('-C', $PSScriptRoot, 'rev-parse', '--show-toplevel') 30)
    if ($repoRootOut[-1] -ne '__EXIT__0') { Write-Error 'could not resolve the repository root for the candidate commit' }
    # @(...) keeps a one-line result an array (a scalar string's [0] is a CHARACTER).
    $repoRootLines = @(if ($repoRootOut.Count -gt 1) { $repoRootOut[0..($repoRootOut.Count - 2)] } else { @() })
    if ($repoRootLines.Count -ne 1) { Write-Error 'git rev-parse --show-toplevel returned an unexpected result' }
    $repoRoot = $repoRootLines[0]
    $candidate = $root['adapter_consumption']['candidate']
    $stage = New-StageTree -HotRoot $HotRoot -RunId $runId
    $trainerBytes = Get-GitBlobBytes -RepoRoot $repoRoot -Commit $candidate['commit'] -Path 'projects/homemade-llm/train/train_adapter.py' -ExpectedSha256 $candidate['trainer_sha256']
    # Acyclic identity: approved root -> trainer bytes -> verifier bytes. Parse
    # only one literal constant from the authenticated trainer, never execute it.
    $trainerText = [Text.Encoding]::UTF8.GetString($trainerBytes)
    $authorityDefinitions = [regex]::Matches($trainerText, '(?m)^EPOCH_AUTHORITY_SHA256\s*=')
    $authorityPins = [regex]::Matches($trainerText, '(?m)^EPOCH_AUTHORITY_SHA256 = "([0-9a-f]{64})"\r?$')
    if ($authorityDefinitions.Count -ne 1 -or $authorityPins.Count -ne 1) { Write-Error 'candidate trainer must pin exactly one epoch authority digest' }
    $authorityBytes = Get-GitBlobBytes -RepoRoot $repoRoot -Commit $candidate['commit'] -Path 'projects/homemade-llm/train/epoch_authority.py' -ExpectedSha256 $authorityPins[0].Groups[1].Value
    $predictorBytes = Get-GitBlobBytes -RepoRoot $repoRoot -Commit $candidate['commit'] -Path 'projects/homemade-llm/eval/predict_dev.py' -ExpectedSha256 $candidate['predictor_sha256']
    $scorerBytes = Get-GitBlobBytes -RepoRoot $repoRoot -Commit $candidate['commit'] -Path 'projects/homemade-llm/eval/dev_selection.py' -ExpectedSha256 $candidate['scorer_sha256']
    $rendererBytes = Get-GitBlobBytes -RepoRoot $repoRoot -Commit $AdmittedRendererCommit -Path $AdmittedRendererPath -ExpectedOid $AdmittedRendererBlobId -ExpectedSha256 $AdmittedRendererSHA256
    $stagedFiles = @(
        (Write-StageFile -DirLabel Train -Dir $stage.Train -Name 'train_adapter.py' -Bytes $trainerBytes -ExpectedSha256 $candidate['trainer_sha256']),
        (Write-StageFile -DirLabel Train -Dir $stage.Train -Name 'prompt_template.py' -Bytes $rendererBytes -ExpectedSha256 $AdmittedRendererSHA256),
        (Write-StageFile -DirLabel Eval -Dir $stage.Eval -Name 'predict_dev.py' -Bytes $predictorBytes -ExpectedSha256 $candidate['predictor_sha256']),
        (Write-StageFile -DirLabel Eval -Dir $stage.Eval -Name 'dev_selection.py' -Bytes $scorerBytes -ExpectedSha256 $candidate['scorer_sha256']),
        (Write-StageFile -DirLabel Input -Dir $stage.Input -Name 'accepted.jsonl' -Bytes $records.AcceptedBytes -ExpectedSha256 $resolution['accepted_jsonl_sha256']),
        (Write-StageFile -DirLabel Input -Dir $stage.Input -Name 'report.json' -Bytes $records.ReportBytes -ExpectedSha256 $resolution['validation_report_sha256']),
        (Write-StageFile -DirLabel Input -Dir $stage.Input -Name 'profile.json' -Bytes $records.ProfileBytes -ExpectedSha256 $resolution['profile_sha256'])
    )
    $bindingPath = New-PublicationBinding -Batch $Batch -Candidate $candidate -Resolution $resolution -RootSha256 $pin.APPROVED_TRUST_ROOT_SHA256 -Files $stagedFiles -MetaDir $stage.Meta
    $epochDockerArgs = New-TrainerDockerArgs -Image $Image -Name $name -CommonMounts $mounts -Stage $stage -BindingPath $bindingPath `
        -AuthoritySource ([Text.Encoding]::UTF8.GetString($authorityBytes)) -Batch $Batch -RootFile $pin.ROOT_FILE `
        -Base $Base -Revision $Revision -ReportContainerPath "/workspace/reports/$runId.json" -Epochs $Epochs -EpochContextLimit $EpochContextLimit `
        -ModelsHostDir (Join-Path $HotRoot 'models\m2') `
        -ExtraEnv @(if (-not [string]::IsNullOrWhiteSpace($ExpectedDigests)) { @('-e', 'HF_HUB_OFFLINE=1', '-e', 'TRANSFORMERS_OFFLINE=1') } else { @() })
    if (-not [string]::IsNullOrWhiteSpace($ExpectedDigests)) {
        $epochDockerArgs += @('--expected-digests', '/workspace/expected-digests.json')
    }
    if (-not [string]::IsNullOrWhiteSpace($TrustedDigestRoot)) {
        $epochDockerArgs += @('--trusted-digest-root', '/workspace/trusted-digest-root.json')
    }
}

$gpuLock = [System.Threading.Mutex]::new($false, 'Global\homemade-llm-gpu')
try { $held = $gpuLock.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $held = $true }
if (-not $held) { Write-Error 'another GPU job (train-adapter, train-smoke or serve-smoke) is running; not starting' }
try {
    $poison = $GpuMarker
    if (Test-Path -LiteralPath $poison) { Write-Error "GPU is poisoned: a previous job was not confirmed stopped ($((Get-Content -LiteralPath $poison -Raw).Trim())). Verify it is gone, then delete $poison" }
    New-Item -ItemType Directory -Force (Split-Path $GpuMarker) | Out-Null
    Set-Content -LiteralPath $GpuMarker -Value "train-adapter (pid $PID) GPU job in progress since $((Get-Date).ToUniversalTime().ToString('o'))"
    $markerWritten = $true

    $pf = @(Invoke-Bounded 'pwsh' @('-NoProfile', '-File', (Join-Path $PSScriptRoot 'preflight.ps1'), '-HotRoot', $HotRoot) 180)
    $pf | Where-Object { $_ -notmatch '^__EXIT__' } | Out-Host
    if ($pf[-1] -ne '__EXIT__0') { Write-Error "preflight did not pass ($($pf[-1] -replace '^__EXIT__', 'exit ')); not starting training" }
    # Smoke/probe output directories are created only after mutex, poison
    # and preflight admission. Epoch staging retains its separate early check.
    if (-not $Epochs) {
        $mounts = New-TrainerCommonMounts -HotRoot $HotRoot -RowsDir (Split-Path $Rows -Parent) `
            -ExpectedDigests $ExpectedDigests -TrustedDigestRoot $TrustedDigestRoot -EpochMode:([bool]$Epochs) -ContextProbe:(-not [string]::IsNullOrWhiteSpace($ProbeContextInputs)) -RunId $runId
        if (-not [string]::IsNullOrWhiteSpace($ProbeContextInputs)) {
            $mounts += @('-v', "${ProbeContextInputs}:/workspace/context-inputs.jsonl:ro")
            # Stage the exact admitted renderer blob (commit/blob/SHA pinned in the stage helper) and bind it
            # read-only over /code/prompt_template.py, so the probe hashes and executes the LF bytes it admits.
            $rendererBytes = Get-GitBlobBytes -RepoRoot $repoRoot -Commit $AdmittedRendererCommit -Path $AdmittedRendererPath -ExpectedOid $AdmittedRendererBlobId -ExpectedSha256 $AdmittedRendererSHA256
            $rendererStage = New-StageTree -HotRoot $HotRoot -RunId $runId
            [void](Write-StageFile -DirLabel Train -Dir $rendererStage.Train -Name 'prompt_template.py' -Bytes $rendererBytes -ExpectedSha256 $AdmittedRendererSHA256)
            $stagedRenderer = Join-Path $rendererStage.Train 'prompt_template.py'
        }
        $locations = Get-ProductionLocations
        Assert-TrainerMountIsolation -Mounts $mounts -ProtectedPaths @(
            (Join-Path $HotRoot 'runs\stage'), $Rows, $code, $evalCode, $locations.PinFile, $locations.CustodyRoot;
            if (-not [string]::IsNullOrWhiteSpace($ProbeContextInputs)) { $ProbeContextInputs; $stagedRenderer })
    }
    if (Get-Command lms -ErrorAction SilentlyContinue) {
        $r = @(Invoke-Bounded 'lms' @('unload', '--all') 60)
        $r | Where-Object { $_ -notmatch '^__EXIT__' } | Out-Host
        if ($r[-1] -ne '__EXIT__0') { Write-Error "lms unload --all failed ($($r[-1] -replace '^__EXIT__', 'exit ')); not starting training" }
    }

    $dockerArgs = if ($Epochs) {
        $epochDockerArgs
    } else {
        $d = @('run', '--rm', '--name', $name, '--gpus', 'all', '--shm-size', '4g') + $mounts + @(
            '-v', "${code}:/code:ro"
            if (-not [string]::IsNullOrWhiteSpace($ProbeContextInputs)) { '-v', "${stagedRenderer}:/code/prompt_template.py:ro" }
            if ([string]::IsNullOrWhiteSpace($ProbeContextInputs)) { '-v', "${evalCode}:/eval:ro" }
            '-e', "ADAPTER_BASE=$Base", '-e', "ADAPTER_REVISION=$Revision",
            '-e', "ADAPTER_STEPS=$Steps",
            '-e', 'HF_HOME=/workspace/hf', '-e', 'HF_TOKEN_PATH=/nonexistent/hf-token')
        if (-not [string]::IsNullOrWhiteSpace($ExpectedDigests)) {
            $d += @('-e', 'HF_HUB_OFFLINE=1', '-e', 'TRANSFORMERS_OFFLINE=1')
        }
        $d += @(
            '--entrypoint', 'python', $Image, '/code/train_adapter.py',
            '--rows', ("/workspace/rows/" + (Split-Path $Rows -Leaf)),
            '--base', $Base, '--revision', $Revision,
            '--report', "/workspace/reports/$runId.json",
            '--steps', "$Steps")
        if ($Winner) { $d += '--winner' }
        if (-not [string]::IsNullOrWhiteSpace($ExpectedDigests)) {
            $d += @('--expected-digests', '/workspace/expected-digests.json')
        }
        if (-not [string]::IsNullOrWhiteSpace($TrustedDigestRoot)) {
            $d += @('--trusted-digest-root', '/workspace/trusted-digest-root.json')
        }
        if ($Probe) {
            $d += @('--probe', '--probe-seq-len', "$ProbeSeqLen")
            if (-not [string]::IsNullOrWhiteSpace($ProbeContextInputs)) {
                $d += @('--probe-context-inputs', '/workspace/context-inputs.jsonl', '--probe-context-limit', "$ProbeContextLimit")
            }
            if ($PSBoundParameters.ContainsKey('ProbeRowsTotal')) {
                $d += @('--probe-rows-total', "$ProbeRowsTotal")
            }
        }
        $d
    }
    function Get-AdapterState {
        try {
            $ps = @(Invoke-Bounded 'docker' @('ps', '-a', '--filter', "name=^$name$", '--format', '{{.Names}}') 20)
            if ($ps[-1] -ne '__EXIT__0') { 'state UNKNOWN (docker did not answer); check: docker ps -a' }
            elseif ($ps | Where-Object { $_ -eq $name }) { 'could NOT be removed; run: docker rm -f ' + $name }
            else { 'stopped and removed' }
        } catch { "state UNKNOWN (check failed: $($_.Exception.Message)); check: docker ps -a" }
    }
    function Stop-Adapter {
        try {
            if (@(Invoke-Bounded 'docker' @('stop', '--time', '20', $name) 40)[-1] -ne '__EXIT__0') { Invoke-Bounded 'docker' @('kill', $name) 20 | Out-Null }
            if (Wait-Job $job -Timeout 60) { Receive-Job $job | Where-Object { $_ -notmatch '^__EXIT__' } | Out-Host }
            Remove-Job $job -Force
            Get-AdapterState
        } catch { "state UNKNOWN (cleanup failed: $($_.Exception.Message)); check: docker ps -a" }
    }
    try { Set-Content -LiteralPath $GpuMarker -Value "container $name launch in flight since $((Get-Date).ToUniversalTime().ToString('o'))" } catch { }
    $containerGone = $false
    $job = Start-Job -ScriptBlock { param($a) & docker @a 2>&1; "__EXIT__$LASTEXITCODE" } -ArgumentList (, $dockerArgs)
    $done = $false
    try {
        if (-not (Wait-Job $job -Timeout ([int]($TimeoutMinutes * 60)))) {
            $done = $true
            $state = Stop-Adapter
            $containerGone = $state -eq 'stopped and removed'
            Write-Error "training exceeded ${TimeoutMinutes} min; container $name $state"
        }
        $done = $true
        $state = Get-AdapterState
        $containerGone = $state -eq 'stopped and removed'
        if (-not $containerGone) { Write-Warning "docker run returned but container $name is $state" }
    } finally {
        if (-not $done) { $state = Stop-Adapter; $containerGone = $state -eq 'stopped and removed'; Write-Warning "interrupted; container $name $state" }
    }
    $out = @(Receive-Job $job); Remove-Job $job
    $out | Where-Object { $_ -notmatch '^__EXIT__' } | Out-Host
    $exitLine = $out | Where-Object { $_ -match '^__EXIT__' } | Select-Object -Last 1
    exit $(if ($exitLine) { [int]($exitLine -replace '^__EXIT__', '') } else { 1 })
} finally {
    if ($markerWritten) {
        if ($containerGone -eq $false) {
            try { Set-Content -LiteralPath $GpuMarker -Value "container $name not confirmed stopped at $((Get-Date).ToUniversalTime().ToString('o'))" } catch { }
            if (Test-Path -LiteralPath $GpuMarker) { Write-Warning "GPU poisoned: container $name was not confirmed stopped; see $GpuMarker" }
            else { $keepLock = $true }
        } elseif (-not $BoundedUnconfirmed) {
            try { Remove-Item -LiteralPath $GpuMarker -Force } catch { Write-Warning "container is gone but $GpuMarker could not be removed" }
        }
    }
    if (-not $keepLock) { $gpuLock.ReleaseMutex(); $gpuLock.Dispose() }
}
# [[Cursor | Auto | 2026-09-29 | 01:10 UTC | END]]
