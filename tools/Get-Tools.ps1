<#
.SYNOPSIS
    Fetch the helper binaries into tools\bin.

.DESCRIPTION
    tools\bin is git-ignored, so run this once after cloning the repo.
    It fetches:
        packwiz.exe                        the packwiz binary
        packwiz-installer.jar              the pack installer
        packwiz-installer-bootstrap.jar    the one you actually launch

    packwiz publishes NO GitHub releases - the prebuilt Windows binary only
    exists as a GitHub Actions artifact - so this script needs the GitHub CLI
    (`gh`, logged in). If you do not have gh, either drop a packwiz.exe into
    tools\bin yourself, or use one of these instead:
        go install github.com/packwiz/packwiz@latest
        https://nightly.link/packwiz/packwiz/workflows/go/main

.PARAMETER Force
    Re-download even if the file is already present.
#>
[CmdletBinding()]
param([switch]$Force)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\settings.ps1"

New-Item -ItemType Directory -Path $ToolsBin  -Force | Out-Null
New-Item -ItemType Directory -Path $BuildDir  -Force | Out-Null

$GhExe = (Get-Command gh -ErrorAction SilentlyContinue).Source
if (-not $GhExe) {
    $fallback = 'C:\Program Files\GitHub CLI\gh.exe'
    if (Test-Path $fallback) { $GhExe = $fallback }
}

function Get-Url {
    param([string]$Url, [string]$Dest, [string[]]$CurlArgs = @())
    if ((Test-Path $Dest) -and -not $Force) {
        Write-Host ("  present : {0}" -f (Split-Path -Leaf $Dest))
        return
    }
    Write-Host ("  fetching: {0}" -f (Split-Path -Leaf $Dest))
    & curl.exe -sSL @CurlArgs -o $Dest $Url
    if ($LASTEXITCODE -ne 0) { throw "download failed: $Url" }
    Write-Host ("  ok      : {0} ({1:N2} MB)" -f (Split-Path -Leaf $Dest), ((Get-Item $Dest).Length / 1MB))
}

Write-Host ''
Write-Host 'packwiz binaries'
Write-Host '----------------'

# --- packwiz.exe -----------------------------------------------------------
$packwizExe = Join-Path $ToolsBin 'packwiz.exe'
if ((Test-Path $packwizExe) -and -not $Force) {
    Write-Host ("  present : packwiz.exe ({0:N2} MB)" -f ((Get-Item $packwizExe).Length / 1MB))
}
else {
    if (-not $GhExe -or -not (Test-Path $GhExe)) {
        throw "gh CLI not found. Install it (winget install GitHub.cli), or put packwiz.exe into $ToolsBin yourself."
    }
    Write-Host '  looking up the newest packwiz build ...'
    $listing = & $GhExe api 'repos/packwiz/packwiz/actions/artifacts?per_page=100' | ConvertFrom-Json
    $artifact = $listing.artifacts |
        Where-Object { $_.name -eq 'Windows 64-bit' -and -not $_.expired } |
        Sort-Object created_at -Descending |
        Select-Object -First 1
    if (-not $artifact) {
        throw 'No unexpired "Windows 64-bit" artifact. Use https://nightly.link/packwiz/packwiz/workflows/go/main instead.'
    }
    Write-Host ("  artifact: #{0} built {1}" -f $artifact.id, $artifact.created_at)
    $token = & $GhExe auth token
    $zip   = Join-Path $BuildDir 'packwiz-win.zip'
    Get-Url -Url $artifact.archive_download_url -Dest $zip -CurlArgs @('-H', "Authorization: Bearer $token")
    Expand-Archive -Path $zip -DestinationPath $ToolsBin -Force
    Remove-Item $zip -Force
    Write-Host ("  ok      : packwiz.exe ({0:N2} MB)" -f ((Get-Item $packwizExe).Length / 1MB))
}

# --- installer jars --------------------------------------------------------
if (-not $GhExe -or -not (Test-Path $GhExe)) {
    Write-Host '  (skipping installer jars: gh not available, download them manually)'
    Write-Host '    https://github.com/packwiz/packwiz-installer-bootstrap/releases/latest'
    Write-Host '    https://github.com/packwiz/packwiz-installer/releases/latest'
}
else {
    foreach ($repo in @('packwiz/packwiz-installer', 'packwiz/packwiz-installer-bootstrap')) {
        $release = & $GhExe api "repos/$repo/releases/latest" | ConvertFrom-Json
        $asset   = $release.assets | Where-Object { $_.name -like '*.jar' } | Select-Object -First 1
        if (-not $asset) { Write-Host ("  no .jar asset in {0} {1}" -f $repo, $release.tag_name); continue }
        Get-Url -Url $asset.browser_download_url -Dest (Join-Path $ToolsBin $asset.name)
    }
}

Write-Host ''
Write-Host 'Done. Next:  .\tools\Update-Pack.ps1'
Write-Host ''
