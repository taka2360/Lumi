[CmdletBinding()]
param(
    [ValidateSet("Core", "Shell", "Stage")]
    [string[]]$Job = @("Core", "Shell", "Stage")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$coreDirectory = Join-Path $repoRoot "core"
$shellDirectory = Join-Path $repoRoot "shell/src-tauri"
$stageDirectory = Join-Path $repoRoot "stage"

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
        Invoke-CiStep `
            -Name "Core: sync dependencies" `
            -WorkingDirectory $coreDirectory `
            -FilePath "uv" `
            -Arguments @("sync", "--locked")

        Invoke-CiStep `
            -Name "Core: lint (ruff check)" `
            -WorkingDirectory $coreDirectory `
            -FilePath "uv" `
            -Arguments @("run", "ruff", "check")

        Invoke-CiStep `
            -Name "Core: format check (ruff format)" `
            -WorkingDirectory $coreDirectory `
            -FilePath "uv" `
            -Arguments @("run", "ruff", "format", "--check")

        Invoke-CiStep `
            -Name "Core: type check (mypy)" `
            -WorkingDirectory $coreDirectory `
            -FilePath "uv" `
            -Arguments @("run", "mypy")

        Invoke-CiStep `
            -Name "Core: tests (pytest)" `
            -WorkingDirectory $coreDirectory `
            -FilePath "uv" `
            -Arguments @("run", "pytest")
    }

    if ($jobsToRun -contains "Shell") {
        $coreDistributionDirectory = Join-Path $repoRoot "core/dist/lumi-core"
        New-Item -ItemType Directory -Force $coreDistributionDirectory | Out-Null

        Invoke-CiStep `
            -Name "Shell: format check (cargo fmt)" `
            -WorkingDirectory $shellDirectory `
            -FilePath "cargo" `
            -Arguments @("fmt", "--check")

        Invoke-CiStep `
            -Name "Shell: lint (clippy)" `
            -WorkingDirectory $shellDirectory `
            -FilePath "cargo" `
            -Arguments @("clippy", "--all-targets", "--", "-D", "warnings")

        Invoke-CiStep `
            -Name "Shell: tests (cargo test)" `
            -WorkingDirectory $shellDirectory `
            -FilePath "cargo" `
            -Arguments @("test")
    }

    if ($jobsToRun -contains "Stage") {
        Invoke-CiStep `
            -Name "Stage: install dependencies" `
            -WorkingDirectory $repoRoot `
            -FilePath "pnpm" `
            -Arguments @("install", "--frozen-lockfile")

        Invoke-CiStep `
            -Name "Stage: lint + format check (biome)" `
            -WorkingDirectory $stageDirectory `
            -FilePath "pnpm" `
            -Arguments @("lint")

        Invoke-CiStep `
            -Name "Stage: type check (tsc)" `
            -WorkingDirectory $stageDirectory `
            -FilePath "pnpm" `
            -Arguments @("typecheck")

        Invoke-CiStep `
            -Name "Stage: tests (vitest)" `
            -WorkingDirectory $stageDirectory `
            -FilePath "pnpm" `
            -Arguments @("test")

        Invoke-CiStep `
            -Name "Stage: build (Vite)" `
            -WorkingDirectory $stageDirectory `
            -FilePath "pnpm" `
            -Arguments @("build")
    }

    Write-Host "`nCI completed successfully." -ForegroundColor Green
}
catch {
    Write-Error $_
    exit 1
}