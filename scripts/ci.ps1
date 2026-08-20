[CmdletBinding()]
param(
    [ValidateSet("Core", "Shell", "Stage")]
    [string[]]$Job = @("Core", "Shell", "Stage")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path $PSScriptRoot).Path
$coreDirectory = Join-Path $repoRoot "../core"
$shellDirectory = Join-Path $repoRoot "../shell/src-tauri"
$stageDirectory = Join-Path $repoRoot "../stage"

function Assert-CommandAvailable {
    param(
        [Parameter(Mandatory)]
        [string]$Name
    )

    if ($null -eq (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found in PATH."
    }
}

function Invoke-CiStep {
    param(
        [Parameter(Mandatory)]
        [string]$Name,

        [Parameter(Mandatory)]
        [string]$WorkingDirectory,

        [Parameter(Mandatory)]
        [string]$FilePath,

        [string[]]$Arguments = @()
    )

    Write-Host "`n==> $Name" -ForegroundColor Cyan
    Push-Location $WorkingDirectory
    try {
        & $FilePath @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "'$FilePath $($Arguments -join ' ')' failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
}

$jobsToRun = @($Job)

if ($jobsToRun -contains "Core") {
    Assert-CommandAvailable "uv"
}
if ($jobsToRun -contains "Shell") {
    Assert-CommandAvailable "cargo"
}
if ($jobsToRun -contains "Stage") {
    Assert-CommandAvailable "pnpm"
}

try {
    if ($jobsToRun -contains "Core") {
        Invoke-CiStep "Core: sync dependencies" $coreDirectory "uv" @("sync", "--locked")
        Invoke-CiStep "Core: lint (ruff check)" $coreDirectory "uv" @("run", "ruff", "check")
        Invoke-CiStep "Core: format check (ruff format)" $coreDirectory "uv" @("run", "ruff", "format", "--check")
        Invoke-CiStep "Core: type check (mypy)" $coreDirectory "uv" @("run", "mypy")
        Invoke-CiStep "Core: tests (pytest)" $coreDirectory "uv" @("run", "pytest")
    }

    if ($jobsToRun -contains "Shell") {
        $coreDistributionDirectory = Join-Path $repoRoot "core/dist/lumi-core"
        New-Item -ItemType Directory -Force $coreDistributionDirectory | Out-Null

        Invoke-CiStep "Shell: format check (cargo fmt)" $shellDirectory "cargo" @("fmt", "--check")
        Invoke-CiStep "Shell: lint (clippy)" $shellDirectory "cargo" @("clippy", "--all-targets", "--", "-D", "warnings")
        Invoke-CiStep "Shell: tests (cargo test)" $shellDirectory "cargo" @("test")
    }

    if ($jobsToRun -contains "Stage") {
        Invoke-CiStep "Stage: install dependencies" $repoRoot "pnpm" @("install", "--frozen-lockfile")
        Invoke-CiStep "Stage: lint + format check (biome)" $stageDirectory "pnpm" @("lint")
        Invoke-CiStep "Stage: type check (tsc)" $stageDirectory "pnpm" @("typecheck")
        Invoke-CiStep "Stage: tests (vitest)" $stageDirectory "pnpm" @("test")
    }

    Write-Host "`nCI completed successfully." -ForegroundColor Green
}
catch {
    Write-Error $_
    exit 1
}
