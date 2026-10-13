param(
    [Parameter(Mandatory = $true)][ValidateSet('Read', 'List', 'Search')][string]$Action,
    [Parameter(Mandatory = $true)][string]$Path,
    [string]$NeedleBase64 = ''
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

switch ($Action) {
    'Read' {
        $result = @{ text = [string](Get-Content -LiteralPath $Path -Raw -Encoding UTF8) }
    }
    'List' {
        $items = @(Get-ChildItem -LiteralPath $Path -Force |
            Select-Object -First 200 |
            ForEach-Object { @{ name = $_.Name; directory = $_.PSIsContainer } })
        $result = @{ entries = $items }
    }
    'Search' {
        $needle = [System.Text.Encoding]::UTF8.GetString(
            [System.Convert]::FromBase64String($NeedleBase64))
        $matches = @(Select-String -LiteralPath $Path -SimpleMatch -Pattern $needle -Encoding UTF8 |
            Select-Object -First 100 |
            ForEach-Object { @{ line = $_.LineNumber; text = $_.Line } })
        $result = @{ matches = $matches }
    }
}
ConvertTo-Json -InputObject $result -Compress -Depth 5
