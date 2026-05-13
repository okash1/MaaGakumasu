$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

function Copy-DirectoryContents {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    if (-not (Test-Path -LiteralPath $Source)) {
        return
    }

    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Get-ChildItem -LiteralPath $Source -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $Destination -Recurse -Force
    }
}

Copy-DirectoryContents `
    -Source (Join-Path $repoRoot "assets\resource") `
    -Destination (Join-Path $repoRoot "resource")

Copy-DirectoryContents `
    -Source (Join-Path $repoRoot "assets\tasks") `
    -Destination (Join-Path $repoRoot "tasks")

Copy-DirectoryContents `
    -Source (Join-Path $repoRoot "assets\lang") `
    -Destination (Join-Path $repoRoot "lang")

Copy-DirectoryContents `
    -Source (Join-Path $repoRoot "assets\data") `
    -Destination (Join-Path $repoRoot "data")

Copy-Item `
    -LiteralPath (Join-Path $repoRoot "assets\interface.json") `
    -Destination (Join-Path $repoRoot "interface.json") `
    -Force

$interfacePath = Join-Path $repoRoot "interface.json"
$interface = Get-Content -LiteralPath $interfacePath -Raw | ConvertFrom-Json
$interface.agent.child_exec = "./python/python.exe"
$interface.agent.child_args = @("-u", "./agent/main.py")
$interface | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $interfacePath -Encoding UTF8

Write-Host "Synced fork assets to local runnable workspace: $repoRoot"
