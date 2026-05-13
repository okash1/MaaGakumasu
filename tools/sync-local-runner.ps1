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

Write-Host "Synced fork assets to local runnable workspace: $repoRoot"
