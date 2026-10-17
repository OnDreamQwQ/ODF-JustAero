<#
.SYNOPSIS
    Compare every mod in pack\mods against Modrinth AND CurseForge, and list what is outdated.

.DESCRIPTION
    Read-only: neither pack\ nor the Minecraft instance is touched, so it is safe to run at
    any time, including with the game open.

    A mod is identified by the sha512 recorded in its *.pw.toml, never by its file name, so a
    renamed jar or an absent instance cannot confuse the result.

    Modrinth is exact - a hash lookup answers "what do I have" and "what is the newest build
    for this loader + game version".

    CurseForge has no public key-free hash API, so the project is taken from the *.pw.toml
    where packwiz already recorded one, and otherwise located by slug. A located project is
    accepted only when one of its file names matches ours exactly - the tool reports "not
    located" rather than guessing.

    Writes build\update-report.txt and build\update-report.json.
    Exit code mirrors the python script, which is 0 - this is a report, not a gate.

.PARAMETER CurseForge
    all     locate a CurseForge project for every mod (default; the first run downloads a
            few megabytes per mod into build\cfwidget-cache, later runs are instant)
    known   only the mods whose *.pw.toml already carries a CurseForge project id
    none    Modrinth only - no cfwidget traffic at all

.PARAMETER Jobs
    Parallel CurseForge requests. Default 6.

.PARAMETER Refresh
    Ignore the local cfwidget cache under build\cfwidget-cache.

.PARAMETER Json
    Also print the full machine-readable result on stdout.

.EXAMPLE
    .\tools\Check-Updates.ps1

.EXAMPLE
    .\tools\Check-Updates.ps1 -CurseForge none
#>
[CmdletBinding()]
param(
    [ValidateSet('all', 'known', 'none')] [string] $CurseForge = 'all',
    [int] $Jobs = 6,
    [switch] $Refresh,
    [switch] $Json
)

$ErrorActionPreference = 'Continue'
. "$PSScriptRoot\settings.ps1"

$script = Join-Path $PSScriptRoot 'check_updates.py'
if (-not (Test-Path $script)) { throw "missing: $script" }
if (-not (Test-Path (Join-Path $PackDir 'pack.toml'))) {
    throw "pack\pack.toml not found - run .\tools\Update-Pack.ps1 first"
}

$pyCmd = Get-PythonCommand
$pyExe = $pyCmd[0]
$pyPre = @()
if ($pyCmd.Count -gt 1) { $pyPre = $pyCmd[1..($pyCmd.Count - 1)] }

New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null

$cargs = @($script, '--curseforge', $CurseForge, '--jobs', $Jobs)
if ($Refresh) { $cargs += '--refresh' }
if ($Json)    { $cargs += '--json' }

& $pyExe @($pyPre + $cargs)
exit $LASTEXITCODE
