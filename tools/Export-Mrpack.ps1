<#
.SYNOPSIS
    Export pack\ into a .mrpack that any launcher can install.

.DESCRIPTION
    Produces dist\<name>-<version>.mrpack - a standard Modrinth pack file.
    Prism Launcher, Modrinth App, ATLauncher and MultiMC all import it, and
    unlike the raw packwiz folder it needs no installer jar.

    Note on --restrictDomains
        Modrinth itself only accepts downloads from cdn.modrinth.com, github.com,
        raw.githubusercontent.com and gitlab.com. A few mods in this pack live on
        the CurseForge CDN, so the default (false) keeps those working for normal
        launchers. Pass -RestrictDomains if you want to see what a Modrinth
        submission would look like - the export will then refuse those files.

    Note on speed
        packwiz downloads every external file once to record its size. The first
        run moves roughly 900 MB through .packwiz-cache and takes a few minutes;
        later runs reuse the cache and finish in seconds.

.PARAMETER Out
    Output file. Defaults to dist\<name>-<version>.mrpack.

.PARAMETER RestrictDomains
    Only allow Modrinth-whitelisted download hosts (strict submission mode).

.PARAMETER NoRefresh
    Skip `packwiz refresh`. Slightly faster, but the index may lag behind pack\.

.EXAMPLE
    .\tools\Export-Mrpack.ps1
#>
[CmdletBinding()]
param(
    [string] $Out,
    [switch] $RestrictDomains,
    [switch] $NoRefresh
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\settings.ps1"

$meta = Read-PackMeta
if (-not $Out) {
    New-Item -ItemType Directory -Path $DistDir -Force | Out-Null
    $Out = Join-Path $DistDir ("{0}-{1}.mrpack" -f $meta.name, $meta.version)
}
$Out = [System.IO.Path]::GetFullPath($Out)
New-Item -ItemType Directory -Path (Split-Path -Parent $Out) -Force | Out-Null

$packwiz = Get-PackwizPath

Write-Host ''
Write-Host 'Export .mrpack' -ForegroundColor White
Write-Host ("  pack   : {0}  v{1}" -f $meta.name, $meta.version)
Write-Host ("  target : {0}" -f $Out)

Push-Location $PackDir
try {
    if (-not $NoRefresh) {
        Write-Host ''
        Write-Host '  refreshing index ...'
        & $packwiz --cache $PwCache refresh
        if ($LASTEXITCODE -ne 0) { throw "packwiz refresh failed (exit $LASTEXITCODE)" }
    }

    Write-Host ''
    $exportArgs = @('--cache', $PwCache, 'modrinth', 'export', '-o', $Out)
    if (-not $RestrictDomains) { $exportArgs += '--restrictDomains=false' }
    & $packwiz @exportArgs
    if ($LASTEXITCODE -ne 0) { throw "export failed (exit $LASTEXITCODE)" }
}
finally { Pop-Location }

# ---------------------------------------------------------------------------
# sanity-check the archive so a broken export cannot reach players
# ---------------------------------------------------------------------------
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead($Out)
try {
    $entry = $zip.GetEntry('modrinth.index.json')
    if (-not $entry) { throw 'the .mrpack has no modrinth.index.json - it is not a valid pack' }
    $reader = New-Object System.IO.StreamReader($entry.Open())
    $index  = $reader.ReadToEnd() | ConvertFrom-Json
    $reader.Close()

    $bundled = @($zip.Entries | Where-Object { $_.FullName -like 'overrides/mods/*' })
    $hosts = $index.files | ForEach-Object { $_.downloads[0] } |
             ForEach-Object { ([uri]$_).Host } | Group-Object | Sort-Object Count -Descending

    Write-Host ''
    Write-Host 'Result' -ForegroundColor Green
    Write-Host ("  file         : {0}  ({1:N2} MB)" -f $Out, ((Get-Item $Out).Length / 1MB))
    Write-Host ("  formatVersion: {0}" -f $index.formatVersion)
    Write-Host ("  dependencies : {0}" -f (($index.dependencies | ConvertTo-Json -Compress)))
    Write-Host ("  manifest     : {0} files" -f @($index.files).Count)
    Write-Host ("  bundled      : {0} jar(s) in overrides\mods" -f $bundled.Count)
    Write-Host '  sources      :'
    foreach ($h in $hosts) { Write-Host ("     {0,4}  {1}" -f $h.Count, $h.Name) }
}
finally { $zip.Dispose() }

Write-Host ''
