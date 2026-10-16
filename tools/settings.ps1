# ===========================================================================
#  settings.ps1  --  the only file you normally need to edit.
#  Dot-sourced by every other script in tools\.
#  ASCII-only on purpose: Windows PowerShell 5.1 misreads BOM-less UTF-8.
# ===========================================================================

# --- layout (derived from this file's location; do not edit) ---------------
$RepoRoot = Split-Path -Parent $PSScriptRoot
$PackDir  = Join-Path $RepoRoot 'pack'
$BuildDir = Join-Path $RepoRoot 'build'
$DistDir  = Join-Path $RepoRoot 'dist'
$ToolsBin = Join-Path $PSScriptRoot 'bin'
$PwCache  = Join-Path $RepoRoot '.packwiz-cache'

# --- the live Minecraft instance this pack is extracted from ---------------
# Change this if you rename or move your PCL instance.
$InstancePath = 'D:\Minecraft\.minecraft\versions\ODF-JustAero'

# --- pack identity (only used when pack\pack.toml does not exist yet) ------
$PackName    = 'ODF-JustAero'
$PackAuthor  = 'OnDreamQwQ'
$PackVersion = '0.5.0'

# --- previous pack export, used as a second (offline) origin source -------
# A .mrpack index maps sha1 -> the exact CDN URL a file was published at.
# Without it, mods that exist ONLY on CurseForge cannot be traced back to a
# download URL, so they get bundled inside the pack instead of linked.
# Regenerate it from any older .mrpack you still have:
#   Expand-Archive -> modrinth.index.json -> tools\legacy\
$OldIndex = Join-Path $RepoRoot 'tools\legacy\modrinth.index.json'

# --- per-player client state that must NOT ship in the pack ----------------
# The instance's config\ folder is copied 1:1 into the pack, which also drags
# along UI state that is only meaningful on your own machine: JEI's per-world
# search history (contains your world names), spark's scratch dumps, Axiom's
# hotbar snapshots and every "*.json.backup" file. fnmatch syntax (* also
# crosses "/"), matched against the instance-relative path.
$ExcludeGlobs = @(
    'config/jei/world/*',
    'config/spark/tmp/*',
    'config/spark/tmp-client/*',
    'config/axiom/hotbars/*',
    '*.backup'
)

# --- python launcher -------------------------------------------------------
# Prefers the Windows `py` launcher, falls back to `python` on PATH.
function Get-PythonCommand {
    if (Get-Command py -ErrorAction SilentlyContinue)     { return @('py', '-3') }
    if (Get-Command python -ErrorAction SilentlyContinue) { return @('python') }
    throw 'Python 3.9+ not found. Install it from https://www.python.org/downloads/'
}

# --- packwiz.exe -----------------------------------------------------------
function Get-PackwizPath {
    $exe = Join-Path $ToolsBin 'packwiz.exe'
    if (-not (Test-Path $exe)) {
        throw "packwiz.exe missing. Run:  `"$PSScriptRoot\Get-Tools.ps1`""
    }
    return $exe
}

# --- read name/author/version out of pack\pack.toml ------------------------
function Read-PackMeta {
    $meta = @{ name = $PackName; author = $PackAuthor; version = $PackVersion }
    $toml = Join-Path $PackDir 'pack.toml'
    if (Test-Path $toml) {
        foreach ($line in Get-Content $toml) {
            if ($line -match '^name\s*=\s*"(.*)"')    { $meta.name    = $Matches[1] }
            if ($line -match '^author\s*=\s*"(.*)"')  { $meta.author  = $Matches[1] }
            if ($line -match '^version\s*=\s*"(.*)"') { $meta.version = $Matches[1] }
        }
    }
    return $meta
}
