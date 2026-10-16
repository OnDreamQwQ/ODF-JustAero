<#
.SYNOPSIS
    Rebuild pack\ from the live Minecraft instance.

.DESCRIPTION
    The whole point of this repo. One command takes your installed, actually
    working pack and turns it into a portable packwiz pack.

    Flow
        1. read the instance      D:\Minecraft\.minecraft\versions\ODF-JustAero
        2. resolve every mod jar  -> Modrinth / previous .mrpack / CurseForge
        3. write pack\            mods\*.pw.toml, index.toml, pack.toml, config\
        4. packwiz refresh        normalise the index
        5. audit + verify         compare the result back against the instance
        6. optional: commit+push  (publishing is a separate manual step:
                                   gh workflow run build-mrpack.yml)

    pack\ contains nothing but generated content, so it is wiped and rebuilt
    every run. Your Minecraft instance is only ever READ, never modified.

.PARAMETER Instance
    Instance folder. Defaults to $InstancePath in tools\settings.ps1.

.PARAMETER Version
    Pin the pack version, e.g. -Version 0.6.0. Defaults to the current one.

.PARAMETER Bump
    Increment the patch number instead: 0.5.0 -> 0.5.1.

.PARAMETER SkipVerify
    Skip the post-build comparison against the instance.

.PARAMETER SkipAudit
    Skip the "which mods have updates" report.

.PARAMETER Commit
    git add + commit the regenerated pack.

.PARAMETER Push
    git push. Implies -Commit.

.EXAMPLE
    .\tools\Update-Pack.ps1
    Rebuild, verify, and leave the changes for you to review.

.EXAMPLE
    .\tools\Update-Pack.ps1 -Bump -Push
    Rebuild as 0.5.1 and push. Pushing no longer builds anything - run
    "gh workflow run build-mrpack.yml" when you want to publish.
#>
[CmdletBinding()]
param(
    [string] $Instance,
    [string] $Version,
    [switch] $Bump,
    [switch] $SkipAudit,
    [switch] $SkipVerify,
    [switch] $SkipUrls,
    [switch] $Commit,
    [switch] $Push,
    [switch] $DryRun
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\settings.ps1"

if ($Push) { $Commit = $true }
if ($Instance) { $InstancePath = $Instance }

$meta = Read-PackMeta
if ($Version) { $meta.version = $Version }
elseif ($Bump) {
    $parts = $meta.version -split '\.'
    if ($parts.Count -ge 3) {
        $parts[2] = ([int]$parts[2] + 1).ToString()
        $meta.version = ($parts -join '.')
    }
    else {
        $meta.version = "$($meta.version).1"
    }
}

$extractScript = Join-Path $PSScriptRoot 'packwiz_from_instance.py'
$ignoreTemplate = Join-Path $PSScriptRoot 'pack.packwizignore'

function Write-Step([string]$Text) {
    Write-Host ''
    Write-Host "== $Text" -ForegroundColor Cyan
}

# ---------------------------------------------------------------------------
Write-Host ''
Write-Host 'ODF-JustAero  --  rebuild pack from instance' -ForegroundColor White
Write-Host ("  instance : {0}" -f $InstancePath)
Write-Host ("  pack     : {0}  v{1}" -f $meta.name, $meta.version)
Write-Host ("  output   : {0}" -f $PackDir)

if (-not (Test-Path $InstancePath)) {
    throw "instance folder not found: $InstancePath`n  Edit `$InstancePath in tools\settings.ps1."
}
if (-not (Test-Path $extractScript)) {
    throw "extractor not found: $extractScript"
}
if (-not (Test-Path (Join-Path $InstancePath 'mods'))) {
    Write-Warning "no mods\ folder in the instance - is that really an instance folder?"
}

# ---------------------------------------------------------------------------
Write-Step '1/5  resolving mods and writing the pack'

$pyCmd  = Get-PythonCommand
$pyExe  = $pyCmd[0]
$pyPre  = @()
if ($pyCmd.Count -gt 1) { $pyPre = $pyCmd[1..($pyCmd.Count - 1)] }

New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null

$extractArgs = @(
    $extractScript,
    '--instance', $InstancePath,
    '--out',      $PackDir,
    '--name',     $meta.name,
    '--author',   $meta.author,
    '--version',  $meta.version,
    '--cache',    (Join-Path $BuildDir 'cf-cache.json'),
    '--report',   (Join-Path $BuildDir 'extract-report.txt'),
    '--force'
)
if ($DryRun) { $extractArgs += '--dry-run' }

# Keep per-player client state out of the shipped pack (see settings.ps1).
foreach ($g in $ExcludeGlobs) { $extractArgs += @('--exclude-glob', $g) }

# Copy the extra instance-root entries (mod-owned folders) as well.
$extraInclude = @()
if ($IncludeExtra) {
    foreach ($e in $IncludeExtra) {
        if (Test-Path (Join-Path $InstancePath $e)) { $extraInclude += $e }
        else { Write-Warning "`$IncludeExtra entry not present in the instance: $e" }
    }
}
foreach ($e in $extraInclude) { $extractArgs += @('--include', $e) }
if ($extraInclude) { Write-Host ("  include  : {0}" -f ($extraInclude -join ', ')) }

# The old .mrpack index is what lets CurseForge-only mods resolve back to a
# real download URL instead of being bundled. Losing it costs ~14 bundled jars.
if (Test-Path $OldIndex) {
    $extractArgs += @('--old-index', $OldIndex)
    Write-Host ("  old index: {0}" -f $OldIndex)
}
else {
    Write-Warning "no old pack index at $OldIndex - CurseForge-only mods will be bundled instead of linked"
}

& $pyExe @($pyPre + $extractArgs)
if ($LASTEXITCODE -ne 0) { throw "extraction failed (exit $LASTEXITCODE)" }

# the extractor cannot know about this repo's own ignore rules
if ((Test-Path $ignoreTemplate) -and -not $DryRun) {
    Copy-Item $ignoreTemplate (Join-Path $PackDir '.packwizignore') -Force
}

# ---------------------------------------------------------------------------
Write-Step '2/5  packwiz refresh'

$packwiz = Get-PackwizPath
Push-Location $PackDir
try {
    & $packwiz --cache $PwCache refresh
    if ($LASTEXITCODE -ne 0) { throw "packwiz refresh failed (exit $LASTEXITCODE)" }
}
finally { Pop-Location }

# ---------------------------------------------------------------------------
Write-Step '3/5  audit: which mods have newer builds'

if ($SkipAudit) { Write-Host '  skipped' }
else {
    & $pyExe @($pyPre + @(
        $extractScript, '--instance', $InstancePath, '--out', $PackDir,
        '--cache', (Join-Path $BuildDir 'cf-cache.json'), '--audit-only'
    ))
    $report = Join-Path $PackDir 'audit-report.txt'
    if (Test-Path $report) { Write-Host "  -> $report" }
}

# ---------------------------------------------------------------------------
Write-Step '4/5  verify: does the pack still match the instance'

if ($SkipVerify) { Write-Host '  skipped' }
else {
    $vargs = @(
        $extractScript, '--instance', $InstancePath, '--out', $PackDir,
        '--cache', (Join-Path $BuildDir 'cf-cache.json'), '--verify'
    )
    if (-not $SkipUrls) { $vargs += '--check-urls' }
    foreach ($g in $ExcludeGlobs) { $vargs += @('--exclude-glob', $g) }
    & $pyExe @($pyPre + $vargs)
    $vcode = $LASTEXITCODE
    $report = Join-Path $PackDir 'verify-report.txt'
    if (Test-Path $report) { Write-Host "  -> $report" }
    if ($vcode -ne 0) {
        Write-Warning "verify reported problems (exit $vcode). Read the report before pushing."
    }
}

# ---------------------------------------------------------------------------
Write-Step '5/5  commit'

if (-not $Commit) {
    Write-Host '  skipped (pass -Commit or -Push)'
    Write-Host ''
    Write-Host 'Done. Review with:'
    Write-Host '  git -C "$RepoRoot" status --short'
    Write-Host '  git -C "$RepoRoot" diff --stat'
}
else {
    git -C $RepoRoot add -A
    $msg = "pack: rebuild from instance ($($meta.name) $($meta.version))"
    git -C $RepoRoot commit -m $msg
    if ($LASTEXITCODE -ne 0) { Write-Warning 'nothing to commit, or commit failed' }
    if ($Push) {
        git -C $RepoRoot push
        if ($LASTEXITCODE -ne 0) { throw 'git push failed' }
        Write-Host '  pushed - publish with:  gh workflow run build-mrpack.yml'
    }
}

Write-Host ''
