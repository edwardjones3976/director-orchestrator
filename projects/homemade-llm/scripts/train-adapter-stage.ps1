# WO-R5 slice 4: publication-binding staging helper, dot-sourced by
# train-adapter.ps1 like invoke-bounded.ps1. Resolves a batch through the
# protected publisher, stages its rows plus the admitted trainer/predictor/
# scorer/renderer bytes into a sealed sibling tree, and binds them into one
# authenticated publication-binding.json the trainer re-verifies before load.
#
# Honest limit: copies and read-only mounts narrow the mutation window and do
# not prove freedom from same-account modification; the pin file, root and
# records are same-account files, so protection rests on separation of duties
# and the independent recount.
$ErrorActionPreference = 'Stop'

# ---- Frozen constants (one table) --------------------------------------
# The admitted renderer is pinned to this exact commit/blob/digest regardless
# of which candidate commit the approved root names; it is never read from a
# caller path or from the working tree (the working tree is CRLF; this blob
# is the LF original).
$AdmittedRendererCommit = '911f040674b52ed107f1a8eec1f283e2fe721cd4'
$AdmittedRendererPath = 'projects/homemade-llm/train/prompt_template.py'
$AdmittedRendererBlobId = 'ea3e7772e5d4a6825e412ad44977e19e73e6b321'
$AdmittedRendererSHA256 = '6ee4833fbce2b0f38f37b5cd51f5424cd154a78cbfc9d9ae3643359a709908ea'

$StageAllowedNames = @{
    Train = @('train_adapter.py', 'prompt_template.py')
    Eval  = @('predict_dev.py', 'dev_selection.py')
    Input = @('accepted.jsonl', 'report.json', 'profile.json')
    Meta  = @('publication-binding.json')
}
$StageDirBindingName = @{ Train = 'train'; Eval = 'eval'; Input = 'rows' }

# ---- Small shared primitives --------------------------------------------
function Assert-ApprovedEpochImage([string]$Image) {
    # The bootstrap is only trustworthy inside this existing approved image.
    if ($Image -cne 'unsloth/unsloth@sha256:1644d635bc7c5b57ed64cabbab1ae00647dfb583c2135fdfb6a461aeeba52739') {
        Write-Error 'epoch mode requires the exact approved training image'
    }
}

function Get-SHA256Hex([byte[]]$Bytes) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { [Convert]::ToHexString($sha.ComputeHash($Bytes)).ToLowerInvariant() } finally { $sha.Dispose() }
}
function Get-SHA256HexOfFile([string]$Path) { Get-SHA256Hex([IO.File]::ReadAllBytes($Path)) }

function Get-BoundedCallerFileSHA256 {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][ValidateRange(0, [long]::MaxValue)][long]$MaxBytes,
        [ValidateRange(1, 300)][int]$TimeoutSeconds = 60
    )
    # The already authenticated custody buffer supplies the exact byte ceiling.
    # Open, ancestry checks and every read run inside the existing bounded job:
    # even a blocked filesystem read is killed on deadline/pipeline cancellation.
    # One fixed buffer plus a one-byte over-limit probe avoids caller-sized allocation.
    $path64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Path))
    $reader = "function Assert-NoReparsePointInAncestry {" + ${function:Assert-NoReparsePointInAncestry}.ToString() + "}`n" + @'
$ErrorActionPreference = 'Stop'
$stream = $null; $sha = $null
try {
    $path = [IO.Path]::GetFullPath([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__PATH64__')))
    Assert-NoReparsePointInAncestry $path
    $stream = [IO.File]::OpenRead($path)
    $sha = [Security.Cryptography.SHA256]::Create()
    $buffer = [byte[]]::new(65536)
    [long]$remaining = __MAX_BYTES__
    while ($true) {
        $wanted = if ($remaining -ge $buffer.Length) { $buffer.Length } else { [int]$remaining + 1 }
        $count = $stream.Read($buffer, 0, $wanted)
        if ($count -eq 0) { break }
        if ($count -gt $remaining) { throw 'caller file exceeds authenticated custody byte limit' }
        $null = $sha.TransformBlock($buffer, 0, $count, $buffer, 0)
        $remaining -= $count
    }
    $null = $sha.TransformFinalBlock([byte[]]::new(0), 0, 0)
    [Convert]::ToHexString($sha.Hash).ToLowerInvariant()
} catch {
    Write-Output ("CALLER_HASH_REFUSED: " + $_.Exception.Message)
    exit 1
} finally {
    if ($null -ne $sha) { $sha.Dispose() }
    if ($null -ne $stream) { $stream.Dispose() }
}
'@
    $reader = $reader.Replace('__PATH64__', $path64).Replace('__MAX_BYTES__', $MaxBytes.ToString([Globalization.CultureInfo]::InvariantCulture))
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($reader))
    $result = @(Invoke-Bounded 'pwsh' @('-NoProfile', '-NonInteractive', '-EncodedCommand', $encoded) $TimeoutSeconds)
    if ($result.Count -ne 2 -or $result[0] -cnotmatch '^[0-9a-f]{64}$' -or $result[1] -cne '__EXIT__0') {
        Write-Error "bounded caller file hash failed: $($result -join '; ')"
    }
    return $result[0]
}

function Assert-NoReparsePointInAncestry([string]$Path) {
    for ($p = $Path; $p; $p = Split-Path $p -Parent) {
        if ((Test-Path -LiteralPath $p) -and ((Get-Item -LiteralPath $p -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            Write-Error "$p is a junction or symbolic link"
        }
    }
}

function Test-ExactKeySet($Obj, [string[]]$Keys) {
    if (-not ($Obj -is [Collections.Generic.Dictionary[string, object]])) { return $false }
    $actual = @($Obj.Keys) | Sort-Object
    $expected = @($Keys) | Sort-Object
    return -not (Compare-Object $actual $expected)
}

# A compact, dependency-free strict JSON reader: duplicate keys at any object
# depth refuse (built-in ConvertFrom-Json silently keeps the last one), and
# every object round-trips as a case-sensitive Dictionary[string,object] so
# downstream key-set checks are exact.
if (-not ('HomemadeLlm.StrictJson' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
namespace HomemadeLlm {
public static class StrictJson {
    public static object Parse(string text) {
        int i = 0;
        var result = ParseValue(text, ref i);
        SkipWs(text, ref i);
        if (i != text.Length) throw new FormatException("trailing data after JSON value");
        return result;
    }
    static void SkipWs(string s, ref int i) { while (i < s.Length && (s[i] == ' ' || s[i] == '\t' || s[i] == '\n' || s[i] == '\r')) i++; }
    static object ParseValue(string s, ref int i) {
        SkipWs(s, ref i);
        if (i >= s.Length) throw new FormatException("unexpected end of JSON");
        char c = s[i];
        if (c == '{') return ParseObject(s, ref i);
        if (c == '[') return ParseArray(s, ref i);
        if (c == '"') return ParseString(s, ref i);
        if (c == 't') { Expect(s, ref i, "true"); return true; }
        if (c == 'f') { Expect(s, ref i, "false"); return false; }
        if (c == 'n') { Expect(s, ref i, "null"); return null; }
        return ParseNumber(s, ref i);
    }
    static void Expect(string s, ref int i, string lit) {
        if (i + lit.Length > s.Length || s.Substring(i, lit.Length) != lit) throw new FormatException("invalid literal at position " + i);
        i += lit.Length;
    }
    static Dictionary<string, object> ParseObject(string s, ref int i) {
        var d = new Dictionary<string, object>();
        i++; SkipWs(s, ref i);
        if (i < s.Length && s[i] == '}') { i++; return d; }
        while (true) {
            SkipWs(s, ref i);
            if (i >= s.Length || s[i] != '"') throw new FormatException("expected a string key");
            string key = ParseString(s, ref i);
            SkipWs(s, ref i);
            if (i >= s.Length || s[i] != ':') throw new FormatException("expected ':'");
            i++;
            object val = ParseValue(s, ref i);
            if (d.ContainsKey(key)) throw new FormatException("duplicate key: " + key);
            d[key] = val;
            SkipWs(s, ref i);
            if (i < s.Length && s[i] == ',') { i++; continue; }
            if (i < s.Length && s[i] == '}') { i++; break; }
            throw new FormatException("expected ',' or '}'");
        }
        return d;
    }
    static List<object> ParseArray(string s, ref int i) {
        var list = new List<object>();
        i++; SkipWs(s, ref i);
        if (i < s.Length && s[i] == ']') { i++; return list; }
        while (true) {
            list.Add(ParseValue(s, ref i));
            SkipWs(s, ref i);
            if (i < s.Length && s[i] == ',') { i++; continue; }
            if (i < s.Length && s[i] == ']') { i++; break; }
            throw new FormatException("expected ',' or ']'");
        }
        return list;
    }
    static string ParseString(string s, ref int i) {
        i++; var sb = new StringBuilder();
        while (true) {
            if (i >= s.Length) throw new FormatException("unterminated string");
            char c = s[i++];
            if (c == '"') break;
            if (c == '\\') {
                if (i >= s.Length) throw new FormatException("bad escape");
                char e = s[i++];
                switch (e) {
                    case '"': sb.Append('"'); break;
                    case '\\': sb.Append('\\'); break;
                    case '/': sb.Append('/'); break;
                    case 'b': sb.Append('\b'); break;
                    case 'f': sb.Append('\f'); break;
                    case 'n': sb.Append('\n'); break;
                    case 'r': sb.Append('\r'); break;
                    case 't': sb.Append('\t'); break;
                    case 'u':
                        if (i + 4 > s.Length) throw new FormatException("bad unicode escape");
                        sb.Append((char)Convert.ToInt32(s.Substring(i, 4), 16));
                        i += 4;
                        break;
                    default: throw new FormatException("bad escape char");
                }
            } else { sb.Append(c); }
        }
        return sb.ToString();
    }
    static object ParseNumber(string s, ref int i) {
        int start = i;
        if (i < s.Length && s[i] == '-') i++;
        while (i < s.Length && char.IsDigit(s[i])) i++;
        bool isFloat = false;
        if (i < s.Length && s[i] == '.') { isFloat = true; i++; while (i < s.Length && char.IsDigit(s[i])) i++; }
        if (i < s.Length && (s[i] == 'e' || s[i] == 'E')) { isFloat = true; i++; if (i < s.Length && (s[i] == '+' || s[i] == '-')) i++; while (i < s.Length && char.IsDigit(s[i])) i++; }
        string token = s.Substring(start, i - start);
        if (token.Length == 0 || token == "-") throw new FormatException("invalid number");
        if (!isFloat) { long lv; if (long.TryParse(token, NumberStyles.Integer, CultureInfo.InvariantCulture, out lv)) return lv; }
        return double.Parse(token, CultureInfo.InvariantCulture);
    }
}}
'@
}
function ConvertFrom-StrictJson([string]$Text) {
    try { return [HomemadeLlm.StrictJson]::Parse($Text) }
    catch { Write-Error "not usable (strict JSON): $($_.Exception.Message)" }
}

# Canonical JSON: sorted keys, minimal escaping, no trailing whitespace. Used
# only for the publication binding this helper writes; never for parsing.
function ConvertTo-CanonicalJson($Value) {
    if ($null -eq $Value) { return 'null' }
    if ($Value -is [bool]) { return $(if ($Value) { 'true' } else { 'false' }) }
    if ($Value -is [string]) {
        $escaped = $Value.Replace('\', '\\').Replace('"', '\"').Replace("`n", '\n').Replace("`r", '\r').Replace("`t", '\t')
        return '"' + $escaped + '"'
    }
    if ($Value -is [int] -or $Value -is [long]) { return "$Value" }
    if ($Value -is [System.Collections.IDictionary]) {
        $parts = foreach ($key in ($Value.Keys | Sort-Object)) {
            (ConvertTo-CanonicalJson ([string]$key)) + ':' + (ConvertTo-CanonicalJson $Value[$key])
        }
        return '{' + ($parts -join ',') + '}'
    }
    if ($Value -is [System.Collections.IEnumerable]) {
        $parts = foreach ($item in $Value) { ConvertTo-CanonicalJson $item }
        return '[' + ($parts -join ',') + ']'
    }
    Write-Error "ConvertTo-CanonicalJson: unsupported value type $($Value.GetType().FullName)"
}

# ---- Production locations ------------------------------------------------
# No wrapper parameter names a profile, hash, pin file, authority file,
# interpreter or resolver; these are fixed constants, reachable from tests
# only through the -Locations seam each function below accepts.
function Get-ProductionLocations {
    [CmdletBinding()]
    param()
    return @{
        PinFile     = 'C:\llm\runs\m2\rr\route_r_root_pin.py'
        CustodyRoot = 'C:/llm/reports/route-r'
    }
}

# ---- 1. Root pin and approved root ---------------------------------------
function Read-RootPin {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$PinFile)
    if (-not (Test-Path -LiteralPath $PinFile -PathType Leaf)) { Write-Error "root pin file not found: $PinFile" }
    Assert-NoReparsePointInAncestry $PinFile
    $text = [IO.File]::ReadAllText($PinFile)
    $result = @{}
    foreach ($name in 'ROOT_FILE', 'ROOT_FORMAT', 'APPROVED_TRUST_ROOT_SHA256') {
        # Count definitions separately so a malformed second assignment cannot
        # hide behind one well-formed value. Comments are data, never evaluated.
        $definitions = [regex]::Matches($text, "(?m)^[ \t]*$name[ \t]*=")
        $pattern = "(?m)^[ \t]*$name[ \t]*=[ \t]*(?:'([^'\r\n]*)'|`"([^`"\r\n]*)`"|(None))[ \t]*(?:#[^\r\n]*)?\r?`$"
        $found = [regex]::Matches($text, $pattern)
        if ($definitions.Count -ne 1 -or $found.Count -ne 1) { Write-Error "root pin constant $name must appear exactly once with a valid value: $PinFile" }
        $g = $found[0].Groups
        if ($g[3].Success) { Write-Error "root pin constant $name is None" }
        $result[$name] = $(if ($g[1].Success) { $g[1].Value } else { $g[2].Value })
    }
    if ($result.APPROVED_TRUST_ROOT_SHA256 -cnotmatch '^[0-9a-f]{64}$') { Write-Error 'root pin APPROVED_TRUST_ROOT_SHA256 is not 64 lowercase hex' }
    if ($result.ROOT_FILE -cnotmatch '^route-r-trust-root-v[0-9a-z]+\.json$') { Write-Error "root pin ROOT_FILE has an unexpected name: $($result.ROOT_FILE)" }
    return $result
}

function Test-PathShaEntry($Obj) {
    return (Test-ExactKeySet $Obj @('path', 'sha256')) -and ($Obj['path'] -is [string]) -and $Obj['path'] -and
    ($Obj['sha256'] -is [string]) -and ($Obj['sha256'] -cmatch '^[0-9a-f]{64}$')
}
function Test-CandidateEntry($Obj) {
    if (-not (Test-ExactKeySet $Obj @('commit', 'trainer_sha256', 'predictor_sha256', 'scorer_sha256', 'template_sha256'))) { return $false }
    if ($Obj['commit'] -isnot [string] -or $Obj['commit'] -cnotmatch '^[0-9a-f]{40}$') { return $false }
    foreach ($k in 'trainer_sha256', 'predictor_sha256', 'scorer_sha256', 'template_sha256') {
        if ($Obj[$k] -isnot [string] -or $Obj[$k] -cnotmatch '^[0-9a-f]{64}$') { return $false }
    }
    return $Obj['template_sha256'] -ceq $AdmittedRendererSHA256
}

function Read-ApprovedRoot {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$CustodyRoot, [Parameter(Mandatory)][System.Collections.IDictionary]$Pin)
    $rootPath = Join-Path $CustodyRoot $Pin.ROOT_FILE
    if (-not (Test-Path -LiteralPath $rootPath -PathType Leaf)) { Write-Error "approved root file not found: $rootPath" }
    Assert-NoReparsePointInAncestry $rootPath
    $bytes = [IO.File]::ReadAllBytes($rootPath)
    if ((Get-SHA256Hex $bytes) -cne $Pin.APPROVED_TRUST_ROOT_SHA256) { Write-Error 'approved root does not match the pinned SHA-256' }
    $root = ConvertFrom-StrictJson ([Text.Encoding]::UTF8.GetString($bytes))
    if (-not ($root -is [Collections.Generic.Dictionary[string, object]])) { Write-Error 'approved root must be a JSON object' }
    if ($root['format'] -cne $Pin.ROOT_FORMAT) { Write-Error "approved root format must be $($Pin.ROOT_FORMAT)" }

    $consumption = $root['adapter_consumption']
    $hasExactSection = Test-ExactKeySet $consumption @('resolver_entry', 'interpreter', 'bridge', 'candidate')
    # Only index into $consumption once its shape is confirmed: -and does not
    # short-circuit in PowerShell, and indexing a non-dictionary (including
    # $null, when the section is absent entirely) throws instead of refusing cleanly.
    $consumptionOk = $false
    if ($hasExactSection) {
        $consumptionOk = (Test-PathShaEntry $consumption['resolver_entry']) -and
        (Test-PathShaEntry $consumption['interpreter']) -and
        (Test-PathShaEntry $consumption['bridge']) -and
        (Test-CandidateEntry $consumption['candidate'])
    }
    # Exact message required: a root missing this section, or carrying one
    # with extra, missing or mistyped keys, is indistinguishable from an
    # unqualified one and refuses identically before any GPU, Docker or model step.
    if (-not $consumptionOk) { Write-Error 'admission authority missing or unqualified' }
    $schema = $root['row_schema']
    if ($schema -isnot [System.Collections.IDictionary] -or $schema['sha256'] -isnot [string] -or
        $schema['sha256'] -cnotmatch '^[0-9a-f]{64}$') { Write-Error 'approved row schema missing or unqualified' }
    return $root
}

# ---- 2. Resolver -----------------------------------------------------------
function Invoke-PublicationResolver {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][System.Collections.IDictionary]$Root,
        [Parameter(Mandatory)][string]$ApprovedTrustRootSHA256,
        [Parameter(Mandatory)][string]$Batch,
        # Production always runs with the 300-second bound below; this is a
        # test-only seam (like -Locations) so a refusal test can prove the
        # __EXIT__timeout path without a 300-second wait.
        [int]$TimeoutSeconds = 300
    )
    $consumption = $Root['adapter_consumption']
    $interpreter = $consumption['interpreter']
    $entry = $consumption['resolver_entry']
    $bridge = $consumption['bridge']
    foreach ($pair in @(@($interpreter, 'interpreter'), @($entry, 'resolver_entry'), @($bridge, 'bridge'))) {
        $info = $pair[0]; $label = $pair[1]
        if (-not (Test-Path -LiteralPath $info['path'] -PathType Leaf)) { Write-Error "$label file not found: $($info['path'])" }
        if ((Get-SHA256HexOfFile $info['path']) -cne $info['sha256']) { Write-Error "$label bytes do not match the pinned sha256" }
    }
    # Path exactly as the root names it: no PATH lookup for the interpreter.
    $out = @(Invoke-Bounded $interpreter['path'] @('-I', '-B', $entry['path'], $Batch) $TimeoutSeconds)
    if ($out[-1] -ne '__EXIT__0') { Write-Error "publication resolver did not exit 0 ($($out[-1] -replace '^__EXIT__', 'exit '))" }
    # @(...) around the whole if: a one-line result must stay a one-ELEMENT
    # array, or the scalar collapses to a string and [0] would index a CHARACTER.
    $lines = @(if ($out.Count -gt 1) { $out[0..($out.Count - 2)] } else { @() })
    if ($lines.Count -ne 1) { Write-Error "publication resolver must print exactly one line of JSON; got $($lines.Count)" }
    $resolution = ConvertFrom-StrictJson $lines[0]
    $keys = @('format', 'batch', 'verified', 'accepted_jsonl_sha256', 'profile_sha256', 'publication_sha256',
        'trust_root_sha256', 'validation_report_sha256', 'bridge_sha256')
    if (-not (Test-ExactKeySet $resolution $keys)) { Write-Error 'publication resolution has an unexpected key set' }
    if ($resolution['format'] -cne 'route-r-resolution/v1') { Write-Error 'publication resolution format must be route-r-resolution/v1' }
    if ($resolution['batch'] -isnot [string] -or $resolution['batch'] -cne $Batch) { Write-Error 'publication resolution batch does not match the request' }
    if ($resolution['verified'] -isnot [bool] -or $resolution['verified'] -ne $true) { Write-Error 'publication resolution verified must be literal true' }
    foreach ($k in 'accepted_jsonl_sha256', 'profile_sha256', 'publication_sha256', 'trust_root_sha256', 'validation_report_sha256', 'bridge_sha256') {
        if ($resolution[$k] -isnot [string] -or $resolution[$k] -cnotmatch '^[0-9a-f]{64}$') { Write-Error "publication resolution $k is invalid" }
    }
    if ($resolution['trust_root_sha256'] -cne $ApprovedTrustRootSHA256) { Write-Error 'publication resolution trust_root_sha256 does not match the pinned approved digest' }
    if ($resolution['bridge_sha256'] -cne $bridge['sha256']) { Write-Error 'publication resolution bridge_sha256 does not match the approved root' }
    return $resolution
}

# ---- 3. Custody records ----------------------------------------------------
function Read-PublicationRecords {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$CustodyRoot,
        [Parameter(Mandatory)][string]$Batch,
        [Parameter(Mandatory)][System.Collections.IDictionary]$Resolution,
        [Parameter(Mandatory)][string]$Rows
    )
    if ($Batch -cnotmatch '^[a-z0-9][a-z0-9-]{0,63}$') { Write-Error "batch id is invalid: $Batch" }
    $accepted = Join-Path $CustodyRoot "accepted/$Batch.accepted.jsonl"
    $report = Join-Path $CustodyRoot "accepted/$Batch.validation-report.json"
    $profile = Join-Path $CustodyRoot "publication/$Batch.profile.json"
    foreach ($p in $accepted, $report, $profile) {
        if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { Write-Error "custody file not found: $p" }
        Assert-NoReparsePointInAncestry $p
    }
    $acceptedBytes = [IO.File]::ReadAllBytes($accepted)
    $reportBytes = [IO.File]::ReadAllBytes($report)
    $profileBytes = [IO.File]::ReadAllBytes($profile)
    if ((Get-SHA256Hex $acceptedBytes) -cne $Resolution['accepted_jsonl_sha256']) { Write-Error 'custody accepted.jsonl does not match the resolution' }
    if ((Get-SHA256Hex $reportBytes) -cne $Resolution['validation_report_sha256']) { Write-Error 'custody validation report does not match the resolution' }
    if ((Get-SHA256Hex $profileBytes) -cne $Resolution['profile_sha256']) { Write-Error 'custody profile does not match the resolution' }

    $profileJson = ConvertFrom-StrictJson ([Text.Encoding]::UTF8.GetString($profileBytes))
    $profileKeys = @('format', 'batch', 'built_by', 'accepted_jsonl_sha256', 'candidate_row_sha256', 'bundle_manifest_sha256',
        'recount_sha256', 'validation_report_sha256', 'row_schema_sha256', 'trust_root_sha256', 'verified_pins_sha256',
        'source_groups_and_splits', 'input_lineage_sha256', 'split_table_sha256', 'constructor_inputs_sha256',
        'constructor_manifest_sha256', 'constructor_receipt_sha256', 'constructor_admission_sha256', 'constructor_recount_sha256')
    if (-not (Test-ExactKeySet $profileJson $profileKeys)) { Write-Error 'profile.json has an unexpected key set' }
    if ($profileJson['format'] -cne 'route-r-publication-profile/v1') { Write-Error 'profile.json format is invalid' }
    if ($profileJson['batch'] -isnot [string] -or $profileJson['batch'] -cne $Batch) { Write-Error 'profile.json batch does not match the request' }
    if ($profileJson['accepted_jsonl_sha256'] -cne $Resolution['accepted_jsonl_sha256']) { Write-Error 'profile.json accepted_jsonl_sha256 does not match the resolution' }
    if ($profileJson['validation_report_sha256'] -cne $Resolution['validation_report_sha256']) { Write-Error 'profile.json validation_report_sha256 does not match the resolution' }
    if ($profileJson['trust_root_sha256'] -cne $Resolution['trust_root_sha256']) { Write-Error 'profile.json trust_root_sha256 does not match the resolution' }
    foreach ($field in 'bundle_manifest_sha256', 'recount_sha256', 'row_schema_sha256', 'verified_pins_sha256', 'split_table_sha256',
        'constructor_inputs_sha256', 'constructor_manifest_sha256', 'constructor_receipt_sha256', 'constructor_admission_sha256', 'constructor_recount_sha256') {
        if ($profileJson[$field] -isnot [string] -or $profileJson[$field] -cnotmatch '^[0-9a-f]{64}$') { Write-Error "profile.json $field is invalid" }
    }
    $groupsAndSplits = $profileJson['source_groups_and_splits']
    # Each guard below checks the concrete type before touching .Count/.Values/
    # .Keys: PowerShell's -or does not short-circuit, and those members do not
    # exist on every JSON shape a malformed or hostile profile could carry.
    if (-not ($groupsAndSplits -is [Collections.Generic.Dictionary[string, object]])) { Write-Error 'profile.json source_groups_and_splits must be an object' }
    if ($groupsAndSplits.Count -eq 0 -or ($groupsAndSplits.Values | Where-Object { $_ -ne 'train' -and $_ -ne 'dev' })) {
        Write-Error 'profile.json source_groups_and_splits must map each source group to train or dev'
    }
    $lineage = $profileJson['input_lineage_sha256']
    if (-not ($lineage -is [Collections.Generic.Dictionary[string, object]])) { Write-Error 'profile.json input_lineage_sha256 must be an object' }
    if (Compare-Object @($lineage.Keys) @($groupsAndSplits.Keys)) { Write-Error 'profile.json input_lineage_sha256 key set must equal source_groups_and_splits' }
    $rowDigests = $profileJson['candidate_row_sha256']
    if (-not ($rowDigests -is [Collections.Generic.List[object]])) { Write-Error 'profile.json candidate_row_sha256 must be an array' }
    if ($rowDigests.Count -eq 0 -or
        (@($rowDigests) | Select-Object -Unique).Count -ne $rowDigests.Count -or
        ($rowDigests | Where-Object { $_ -isnot [string] -or $_ -cnotmatch '^[0-9a-f]{64}$' })) {
        Write-Error 'profile.json candidate_row_sha256 must be a non-empty duplicate-free ordered list of row digests'
    }

    $rowsFull = [IO.Path]::GetFullPath($Rows)
    if (-not (Test-Path -LiteralPath $rowsFull -PathType Leaf)) { Write-Error "caller rows file not found: $rowsFull" }
    if ((Get-BoundedCallerFileSHA256 -Path $rowsFull -MaxBytes $acceptedBytes.LongLength) -cne $Resolution['accepted_jsonl_sha256']) { Write-Error "caller rows file does not match the verified custody accepted.jsonl: $rowsFull" }
    $callerReport = Join-Path (Split-Path $rowsFull -Parent) 'report.json'
    if (-not (Test-Path -LiteralPath $callerReport -PathType Leaf)) { Write-Error "caller report.json not found beside rows: $callerReport" }
    if ((Get-BoundedCallerFileSHA256 -Path $callerReport -MaxBytes $reportBytes.LongLength) -cne $Resolution['validation_report_sha256']) { Write-Error "caller report.json does not match the verified custody validation report: $callerReport" }

    # The staged copy is made from these verified custody bytes, never from a
    # second read of the caller's path.
    return @{ AcceptedBytes = $acceptedBytes; ReportBytes = $reportBytes; ProfileBytes = $profileBytes; Profile = $profileJson }
}

# ---- 4. Git blobs -----------------------------------------------------------
function Get-GitBlobBytes {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][string]$Commit,
        [Parameter(Mandatory)][string]$Path,
        [string]$ExpectedOid,
        [Parameter(Mandatory)][string]$ExpectedSha256
    )
    if ($Commit -cnotmatch '^[0-9a-f]{40}$') { Write-Error "git commit id is not a full 40-hex value: $Commit" }
    if ($ExpectedOid -and $ExpectedOid -cnotmatch '^[0-9a-f]{40}$') { Write-Error "expected git blob id is not a full 40-hex value: $ExpectedOid" }
    if ($ExpectedSha256 -cnotmatch '^[0-9a-f]{64}$') { Write-Error "expected SHA-256 is invalid: $ExpectedSha256" }
    $gitExe = (Get-Command git -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source

    # A tree can resolve commit:path too; annotated tags peel to a different
    # object. Require the supplied full id itself to be a commit first.
    $commitOut = @(Invoke-Bounded $gitExe @('-C', $RepoRoot, 'rev-parse', '--verify', "$($Commit)^{commit}") 60)
    if ($commitOut.Count -ne 2 -or $commitOut[-1] -cne '__EXIT__0' -or $commitOut[0] -cne $Commit) {
        Write-Error "git commit id must name the exact commit object: $Commit"
    }

    $oidOut = @(Invoke-Bounded $gitExe @('-C', $RepoRoot, 'rev-parse', '--verify', "$($Commit):$($Path)") 60)
    if ($oidOut[-1] -ne '__EXIT__0') { Write-Error "git object not found: ${Commit}:${Path}" }
    # @(...) keeps a one-line result an array (a scalar string's [0] is a CHARACTER).
    $oidLines = @(if ($oidOut.Count -gt 1) { $oidOut[0..($oidOut.Count - 2)] } else { @() })
    if ($oidLines.Count -ne 1 -or $oidLines[0] -cnotmatch '^[0-9a-f]{40}$') { Write-Error "git rev-parse returned an unexpected result for ${Commit}:${Path}" }
    $oid = $oidLines[0]
    $typeOut = @(Invoke-Bounded $gitExe @('-C', $RepoRoot, 'cat-file', '-t', $oid) 60)
    if ($typeOut[-1] -ne '__EXIT__0') { Write-Error "git cat-file -t failed for oid $oid" }
    # @(...) keeps a one-line result an array (a scalar string's [0] is a CHARACTER).
    $typeLines = @(if ($typeOut.Count -gt 1) { $typeOut[0..($typeOut.Count - 2)] } else { @() })
    if ($typeLines.Count -ne 1 -or $typeLines[0] -cne 'blob') { Write-Error "git object ${Commit}:${Path} is not a blob" }
    if ($ExpectedOid -and $oid -cne $ExpectedOid) { Write-Error "git blob id mismatch for ${Commit}:${Path}: got $oid, expected $ExpectedOid" }

    # Raw stdout stream, never the PowerShell text pipeline: the bytes must
    # round-trip exactly, including CRLF-vs-LF, which a text decode would mangle.
    $raw = Invoke-Bounded $gitExe @('-C', $RepoRoot, 'cat-file', 'blob', "$($Commit):$($Path)") 60 -RawBytes
    if ($raw -isnot [pscustomobject] -or $raw.ExitCode -ne 0 -or $raw.Bytes -isnot [byte[]]) {
        Write-Error "git cat-file blob failed or timed out for ${Commit}:${Path}"
    }
    $bytes = $raw.Bytes
    if ((Get-SHA256Hex $bytes) -cne $ExpectedSha256) { Write-Error "git blob SHA-256 mismatch for ${Commit}:${Path}" }
    return ,$bytes
}

# ---- 5. Stage tree and binding ---------------------------------------------
function New-StageTree {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$HotRoot, [Parameter(Mandatory)][string]$RunId)
    if ($RunId -cnotmatch '^m2-[0-9a-f]{8}$') { Write-Error "RunId must match ^m2-[0-9a-f]{8}`$: $RunId" }
    $hotRootFull = [IO.Path]::GetFullPath($HotRoot)
    if (-not $hotRootFull.StartsWith('C:\', [StringComparison]::OrdinalIgnoreCase)) { Write-Error "HotRoot must be on C:: $hotRootFull" }
    $stageRoot = Join-Path $hotRootFull 'runs\stage'
    $runDir = Join-Path $stageRoot $RunId
    if (Test-Path -LiteralPath $runDir) { Write-Error "stage run directory already exists: $runDir" }
    Assert-NoReparsePointInAncestry $stageRoot
    New-Item -ItemType Directory -Path $runDir | Out-Null
    $subdirs = @{}
    foreach ($name in 'Train', 'Eval', 'Input', 'Meta') {
        $sub = Join-Path $runDir $name
        New-Item -ItemType Directory -Path $sub | Out-Null
        $subdirs[$name] = $sub
    }
    return @{ RunDir = $runDir; Train = $subdirs.Train; Eval = $subdirs.Eval; Input = $subdirs.Input; Meta = $subdirs.Meta }
}

function Write-StageFile {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][ValidateSet('Train', 'Eval', 'Input', 'Meta')][string]$DirLabel,
        [Parameter(Mandatory)][string]$Dir,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][byte[]]$Bytes,
        [Parameter(Mandatory)][string]$ExpectedSha256
    )
    if ($Name -notin $StageAllowedNames[$DirLabel]) { Write-Error "disallowed staged file name for ${DirLabel}: $Name" }
    if ($ExpectedSha256 -cnotmatch '^[0-9a-f]{64}$') { Write-Error "expected SHA-256 is invalid for ${DirLabel}/${Name}" }
    if ((Get-SHA256Hex $Bytes) -cne $ExpectedSha256) { Write-Error "bytes to stage do not match the expected SHA-256 for ${DirLabel}/${Name}" }
    $path = Join-Path $Dir $Name
    $stream = [IO.File]::Open($path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
    try { $stream.Write($Bytes, 0, $Bytes.Length) } finally { $stream.Dispose() }
    if ((Get-Item -LiteralPath $path).Length -ne $Bytes.Length) { Write-Error "staged file has an unexpected byte count: $path" }
    $rehash = Get-SHA256HexOfFile $path
    if ($rehash -cne $ExpectedSha256) { Write-Error "staged file did not re-hash to the expected SHA-256: $path" }
    (Get-Item -LiteralPath $path).IsReadOnly = $true
    return @{ dir = $StageDirBindingName[$DirLabel]; name = $Name; sha256 = $rehash; bytes = $Bytes.Length }
}

function New-PublicationBinding {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Batch,
        [Parameter(Mandatory)][System.Collections.IDictionary]$Candidate,
        [Parameter(Mandatory)][System.Collections.IDictionary]$Resolution,
        [Parameter(Mandatory)][string]$RootSha256,
        [Parameter(Mandatory)][array]$Files,
        [Parameter(Mandatory)][string]$MetaDir
    )
    $expectedKeys = @('rows/accepted.jsonl', 'rows/report.json', 'rows/profile.json',
        'train/train_adapter.py', 'train/prompt_template.py', 'eval/predict_dev.py', 'eval/dev_selection.py')
    if ($Files.Count -ne 7) { Write-Error "publication binding files must list exactly seven payload files; got $($Files.Count)" }
    $byKey = @{}
    foreach ($f in $Files) {
        $safeName = $f.name -is [string] -and $f.name -and $f.name -notmatch '[\\/]' -and $f.name -ne '..' -and $f.name -ne '.'
        if ($f.dir -notin @('rows', 'train', 'eval') -or -not $safeName -or
            $f.sha256 -isnot [string] -or $f.sha256 -cnotmatch '^[0-9a-f]{64}$' -or
            -not ($f.bytes -is [int] -or $f.bytes -is [long]) -or $f.bytes -lt 0) {
            Write-Error "publication binding files entry is invalid: $($f.dir)/$($f.name)"
        }
        $key = "$($f.dir)/$($f.name)"
        if ($byKey.ContainsKey($key)) { Write-Error "publication binding files has a duplicate entry: $key" }
        $byKey[$key] = $f
    }
    $missing = $expectedKeys | Where-Object { -not $byKey.ContainsKey($_) }
    $extra = $byKey.Keys | Where-Object { $_ -notin $expectedKeys }
    if ($missing -or $extra) { Write-Error "publication binding files must list exactly the seven payload files (missing=$($missing -join ','), extra=$($extra -join ','))" }

    if ($Candidate.template_sha256 -cne $AdmittedRendererSHA256) { Write-Error 'candidate template_sha256 is not the admitted renderer digest' }
    foreach ($pair in @(
            @('train/train_adapter.py', $Candidate.trainer_sha256),
            @('eval/predict_dev.py', $Candidate.predictor_sha256),
            @('eval/dev_selection.py', $Candidate.scorer_sha256),
            @('train/prompt_template.py', $Candidate.template_sha256))) {
        if ($byKey[$pair[0]].sha256 -cne $pair[1]) { Write-Error "candidate hash does not match the staged $($pair[0])" }
    }
    foreach ($pair in @(
            @('rows/accepted.jsonl', $Resolution.accepted_jsonl_sha256),
            @('rows/report.json', $Resolution.validation_report_sha256),
            @('rows/profile.json', $Resolution.profile_sha256))) {
        if ($byKey[$pair[0]].sha256 -cne $pair[1]) { Write-Error "resolution hash does not match the staged $($pair[0])" }
    }

    $binding = [ordered]@{
        format     = 'adapter-publication-binding/v1'
        batch      = $Batch
        candidate  = [ordered]@{
            commit           = $Candidate.commit
            trainer_sha256   = $Candidate.trainer_sha256
            predictor_sha256 = $Candidate.predictor_sha256
            scorer_sha256    = $Candidate.scorer_sha256
            template_sha256  = $Candidate.template_sha256
        }
        resolution = [ordered]@{
            format                   = $Resolution.format
            batch                    = $Resolution.batch
            verified                 = $Resolution.verified
            accepted_jsonl_sha256    = $Resolution.accepted_jsonl_sha256
            profile_sha256           = $Resolution.profile_sha256
            publication_sha256       = $Resolution.publication_sha256
            trust_root_sha256        = $Resolution.trust_root_sha256
            validation_report_sha256 = $Resolution.validation_report_sha256
            bridge_sha256            = $Resolution.bridge_sha256
        }
        root_sha256 = $RootSha256
        files       = @($Files | Sort-Object dir, name | ForEach-Object { [ordered]@{ dir = $_.dir; name = $_.name; sha256 = $_.sha256; bytes = $_.bytes } })
    }
    $text = ConvertTo-CanonicalJson $binding
    $bindingPath = Join-Path $MetaDir 'publication-binding.json'
    $utf8NoBom = [Text.UTF8Encoding]::new($false)
    $stream = [IO.File]::Open($bindingPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
    try {
        $bytes = $utf8NoBom.GetBytes($text)
        $stream.Write($bytes, 0, $bytes.Length)
    } finally { $stream.Dispose() }
    (Get-Item -LiteralPath $bindingPath).IsReadOnly = $true
    return $bindingPath
}

# ---- 6. Docker arguments (epoch mode only) ---------------------------------
# The one place the common host mounts are composed, so the rows-mount
# decision is real code the tests exercise, not a copy. In epoch mode the
# staged Input directory (mounted by New-TrainerDockerArgs as
# /workspace/rows:ro) is the ONLY rows mount: mounting the caller's rows
# directory too would put a second, unverified /workspace/rows destination in
# the container. Non-epoch modes keep the caller rows mount exactly as before.
function New-TrainerCommonMounts {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$HotRoot,
        [Parameter(Mandatory)][string]$RowsDir,
        [string]$ExpectedDigests,
        [string]$TrustedDigestRoot,
        [switch]$EpochMode,
        [switch]$ContextProbe,
        [string]$RunId
    )
    $mounts = @()
    # No mode exposes runs or custody-bearing reports parents for writes.
    if ($EpochMode -and $ContextProbe) { Write-Error 'context probe mounts cannot be epoch mounts' }
    if (-not $EpochMode -and $RunId -cnotmatch '^m2-[0-9a-f]{8}$') { Write-Error 'smoke/probe mounts require a run id' }
    $pairs = if ($EpochMode) {
        @(@('hf', 'hf'), @('models\m2', 'models/m2'), @('reports\adapter-output', 'reports'))
    } elseif ($ContextProbe) {
        @(@('hf', 'hf'), @(('reports\adapter-output\' + $RunId), 'reports'))
    } else {
        @(@('hf', 'hf'), @(('models\m2\' + $RunId), 'models/m2'), @(('reports\adapter-output\' + $RunId), 'reports'))
    }
    foreach ($pair in $pairs) {
        $hostDir = Join-Path $HotRoot $pair[0]
        New-Item -ItemType Directory -Force $hostDir | Out-Null
        # Preserve legitimate HF blob links without exposing any of their targets
        # for write. Cache availability and read-only image compatibility are runtime gates.
        $access = if ($pair[1] -ceq 'hf') { ':ro' } else { '' }
        $mounts += @('-v', "${hostDir}:/workspace/$($pair[1])${access}")
    }
    if (-not $EpochMode -and -not $ContextProbe) {
        $mounts += @('-v', "${RowsDir}:/workspace/rows:ro")
    }
    if (-not [string]::IsNullOrWhiteSpace($ExpectedDigests)) {
        $mounts += @('-v', "${ExpectedDigests}:/workspace/expected-digests.json:ro")
    }
    if (-not [string]::IsNullOrWhiteSpace($TrustedDigestRoot)) {
        $mounts += @('-v', "${TrustedDigestRoot}:/workspace/trusted-digest-root.json:ro")
    }
    return $mounts
}

# Reject writable aliases before constructing the epoch launch vector. A
# read-only child bind does not protect bytes exposed through another parent.
function Assert-TrainerMountIsolation {
    [CmdletBinding()]
    param([string[]]$Mounts, [string[]]$ProtectedPaths)
    for ($i = 0; $i -lt $Mounts.Count; $i += 2) {
        if ($Mounts[$i] -cne '-v' -or $i + 1 -ge $Mounts.Count -or
            $Mounts[$i + 1] -cnotmatch '^(.+):(/[^:]+)(:ro)?$') {
            Write-Error 'unexpected trainer common mount syntax'
        }
        $hostPath = [IO.Path]::GetFullPath($Matches[1]).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
        if ($Matches[3] -ceq ':ro') { continue }
        foreach ($protected in $ProtectedPaths) {
            $protectedFull = [IO.Path]::GetFullPath($protected).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
            $separator = [IO.Path]::DirectorySeparatorChar
            if ($hostPath.Equals($protectedFull, [StringComparison]::OrdinalIgnoreCase) -or
                $protectedFull.StartsWith($hostPath + $separator, [StringComparison]::OrdinalIgnoreCase) -or
                $hostPath.StartsWith($protectedFull + $separator, [StringComparison]::OrdinalIgnoreCase)) {
                Write-Error "writable trainer mount overlaps protected publication path: $hostPath"
            }
        }
        # Filesystem calls (including ancestry checks) run inside the existing
        # bounded job. A blocked enumeration cannot strand the wrapper before
        # its deadline; timeout/uncertain cleanup refuses through Invoke-Bounded.
        $path64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($hostPath))
        $scan = "function Assert-NoReparsePointInAncestry {" + ${function:Assert-NoReparsePointInAncestry}.ToString() + "}`n" + @'
$ErrorActionPreference = 'Stop'
try {
    $path = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__PATH64__'))
    Assert-NoReparsePointInAncestry $path
    $pending = [Collections.Generic.Stack[string]]::new()
    if (Test-Path -LiteralPath $path) { $pending.Push($path) }
    while ($pending.Count) {
        $current = $pending.Pop()
        # Enumerate one directory at a time; refuse links BEFORE descending.
        foreach ($entry in (Get-ChildItem -LiteralPath $current -Force -ErrorAction Stop)) {
            if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "writable trainer mount contains a junction or symbolic link: $($entry.FullName)"
            }
            if ($entry.PSIsContainer) { $pending.Push($entry.FullName) }
        }
    }
    Write-Output 'TRAINER_MOUNT_SCAN_OK'
} catch {
    Write-Output ("TRAINER_MOUNT_SCAN_REFUSED: " + $_.Exception.Message)
    exit 1
}
'@
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($scan.Replace('__PATH64__', $path64)))
        $result = @(Invoke-Bounded 'pwsh' @('-NoProfile', '-NonInteractive', '-EncodedCommand', $encoded) 60)
        if ($result.Count -ne 2 -or $result[0] -cne 'TRAINER_MOUNT_SCAN_OK' -or $result[1] -cne '__EXIT__0') {
            Write-Error "writable trainer mount scan failed: $($result -join '; ')"
        }
    }
}

function New-TrainerDockerArgs {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Image,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string[]]$CommonMounts,
        [Parameter(Mandatory)][hashtable]$Stage,
        [Parameter(Mandatory)][string]$BindingPath,
        [Parameter(Mandatory)][string]$Base,
        [Parameter(Mandatory)][string]$Revision,
        [Parameter(Mandatory)][string]$ReportContainerPath,
        [Parameter(Mandatory)][int]$Epochs,
        [Parameter(Mandatory)][ValidateRange(2049, [int]::MaxValue)][int]$EpochContextLimit,
        [Parameter(Mandatory)][string]$AuthoritySource,
        [Parameter(Mandatory)][string]$Batch,
        [Parameter(Mandatory)][string]$RootFile,
        [string]$ModelsHostDir,
        [string[]]$ExtraEnv = @(),
        [hashtable]$Locations = (Get-ProductionLocations)
    )
    Assert-ApprovedEpochImage -Image $Image
    if ($Batch -cnotmatch '^[a-z0-9][a-z0-9-]{0,63}$' -or $RootFile -cnotmatch '^route-r-trust-root-v[0-9a-z]+\.json$') {
        Write-Error 'invalid authority batch or root file'
    }
    $authorityMounts = @()
    foreach ($pair in @(@($Locations.PinFile, 'root-pin.py'),
            @((Join-Path $Locations.CustodyRoot $RootFile), 'root.json'),
            @((Join-Path $Locations.CustodyRoot "publication/$Batch.publication.json"), 'publication.json'))) {
        Assert-NoReparsePointInAncestry $pair[0]
        if (-not (Test-Path -LiteralPath $pair[0] -PathType Leaf)) { Write-Error "authority file missing: $($pair[0])" }
        $authorityMounts += @('-v', "$($pair[0]):/run/route-r-authority/$($pair[1]):ro")
    }
    # Read-only aliases can also shadow a trusted destination. Reserve the
    # complete authority namespace and all fixed stage mount destinations.
    $seenDestinations = @()
    foreach ($mount in $CommonMounts) {
        if ($mount -ceq '-v') { continue }
        if ($mount -cnotmatch '^(.+):(/[^:]+)(:ro)?$') { Write-Error 'unexpected trainer common mount syntax' }
        $dest = $Matches[2].TrimEnd('/')
        if ($dest -match '(^|/)\.\.?(/|$)' -or $dest.Contains('//')) { Write-Error 'noncanonical mount destination' }
        foreach ($seen in $seenDestinations) {
            if ($dest -ceq $seen -or $dest.StartsWith($seen + '/', [StringComparison]::Ordinal) -or $seen.StartsWith($dest + '/', [StringComparison]::Ordinal)) {
                Write-Error "overlapping trainer mount destinations: $dest and $seen"
            }
        }
        $seenDestinations += $dest
        foreach ($reserved in '/run/route-r-authority', '/train', '/eval', '/workspace/rows', '/workspace/publication-binding.json') {
            if ($dest -ceq $reserved -or $dest -ceq '' -or $dest.StartsWith($reserved + '/', [StringComparison]::Ordinal) -or $reserved.StartsWith($dest + '/', [StringComparison]::Ordinal)) {
                Write-Error "trainer mount shadows reserved destination: $dest"
            }
        }
    }
    Assert-TrainerMountIsolation -Mounts $CommonMounts -ProtectedPaths @(
        $Stage.Train, $Stage.Eval, $Stage.Input, $BindingPath, $Locations.PinFile, $Locations.CustodyRoot)
    @('run', '--rm', '--name', $Name, '--gpus', 'all', '--shm-size', '4g') + $CommonMounts + $authorityMounts + @(
        '-v', "$($Stage.Train):/train:ro",
        '-v', "$($Stage.Eval):/eval:ro",
        '-v', "$($Stage.Input):/workspace/rows:ro",
        '-v', "${BindingPath}:/workspace/publication-binding.json:ro",
        '-e', "ADAPTER_BASE=$Base", '-e', "ADAPTER_REVISION=$Revision",
        '-e', 'ADAPTER_MODELS_DIR=/workspace/models/m2',
        '-e', "ADAPTER_MODELS_HOST_DIR=$ModelsHostDir",
        '-e', 'PYTHONDONTWRITEBYTECODE=1') + $ExtraEnv + @(
        '-e', 'HF_HOME=/workspace/hf', '-e', 'HF_TOKEN_PATH=/nonexistent/hf-token',
        '--entrypoint', 'python', $Image, '-I', '-B', '-c', ($AuthoritySource + "`nbootstrap()`n"),
        '--rows', '/workspace/rows/accepted.jsonl',
        '--base', $Base, '--revision', $Revision,
        '--report', $ReportContainerPath,
        '--epochs', "$Epochs",
        '--epoch-context-limit', "$EpochContextLimit",
        '--publication-binding', '/workspace/publication-binding.json')
}
