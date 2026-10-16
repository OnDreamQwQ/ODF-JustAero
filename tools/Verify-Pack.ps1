<#
.SYNOPSIS
    Compare pack\ back against the live instance and list every discrepancy.

.DESCRIPTION
    Read-only. Checks, in order:
        1  loader / game version match
        2  every file in index.toml is present, and its sha256 still matches
        3  every jar in the instance is accounted for (and nothing extra)
        4  every recorded download hash is the hash of the bytes you actually run
        5  bundled jars are byte-identical to the instance copies
        6  duplicate mod ids inside the instance
        7  client/server side flags
        8  config / resourcepacks / options.txt parity
        9  every download URL still answers (skip with -SkipUrls)

    Writes pack\verify-report.txt and pack\verify-report.json, and exits
    non-zero when any check fails - which makes it usable as a CI gate.

.PARAMETER SkipUrls
    Do not HTTP-check the download URLs (much faster, no network).

.EXAMPLE
    .\tools\Verify-Pack.ps1
#>
[CmdletBinding()]
param(
    [string] $Instance,
    [switch] $SkipUrls
)

$ErrorActionPreference = 'Continue'
. "$PSScriptRoot\settings.ps1"

if ($Instance) { $InstancePath = $Instance }

$extractScript = Join-Path $PSScriptRoot 'packwiz_from_instance.py'
if (-not (Test-Path $InstancePath)) { throw "instance folder not found: $InstancePath" }

$pyCmd = Get-PythonCommand
$pyExe = $pyCmd[0]
$pyPre = @()
if ($pyCmd.Count -gt 1) { $pyPre = $pyCmd[1..($pyCmd.Count - 1)] }

New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null

$vargs = @(
    $extractScript,
    '--instance', $InstancePath,
    '--out',      $PackDir,
    '--cache',    (Join-Path $BuildDir 'cf-cache.json'),
    '--verify'
)
if (-not $SkipUrls) { $vargs += '--check-urls' }

& $pyExe @($pyPre + $vargs)
$code = $LASTEXITCODE

$report = Join-Path $PackDir 'verify-report.txt'
if (Test-Path $report) {
    Write-Host ''
    Write-Host "report: $report"
}

exit $code
