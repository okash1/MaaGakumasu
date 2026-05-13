param(
    [string]$ReleasePath = (Join-Path $PSScriptRoot "..\..\MaaGakumasu-release")
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$releaseRoot = Resolve-Path $ReleasePath

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
    -Destination (Join-Path $releaseRoot "resource")

Copy-DirectoryContents `
    -Source (Join-Path $repoRoot "assets\tasks") `
    -Destination (Join-Path $releaseRoot "tasks")

Copy-DirectoryContents `
    -Source (Join-Path $repoRoot "assets\lang") `
    -Destination (Join-Path $releaseRoot "lang")

Copy-DirectoryContents `
    -Source (Join-Path $repoRoot "assets\data") `
    -Destination (Join-Path $releaseRoot "data")

Copy-DirectoryContents `
    -Source (Join-Path $repoRoot "agent") `
    -Destination (Join-Path $releaseRoot "agent")

Copy-Item `
    -LiteralPath (Join-Path $repoRoot "assets\interface.json") `
    -Destination (Join-Path $releaseRoot "interface.json") `
    -Force

Write-Host "Synced fork assets and agent to $releaseRoot"
