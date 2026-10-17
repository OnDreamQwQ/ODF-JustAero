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
#
# config/net_music_list.toml belongs to "Net Music: Better Experience"
# (net_music_list-*.jar). It stores your NetEase / QQ / Bilibili login cookies
# in PLAIN TEXT:
#     neteaseCookie = "MUSIC_U=..."   <- full account credential, no expiry
# They are empty until you log in through the mod, at which point the next
# build would publish them to a PUBLIC repo. Never pack this file.
#
# config/GeneralFeedback/feedback.json belongs to "General Feedback"
# (generalfeedback-*.jar). It carries a real API token so the in-game button can
# post to a feedback table:
#     "token": "github_pat_11..."     <- full credential, PLAIN TEXT
# The mod is DISABLED in this pack: all three displayFeedbackButton* flags are
# false in config/generalfeedback_config.toml and this file no longer exists.
# The rule stays anyway, as a permanent guard - re-enabling the button without
# it would publish a credential to a PUBLIC repo on the very next build.
$ExcludeGlobs = @(
    'config/jei/world/*',
    'config/spark/tmp/*',
    'config/spark/tmp-client/*',
    'config/axiom/hotbars/*',
    'config/net_music_list.toml',
    'config/GeneralFeedback/*',
    '*.backup',
    # A mod rewriting its own config leaves a "*.bak" next to the real file
    # (downgrading Vista to 5.5.0 left vista-client-1.toml.bak and
    # vista-common-1.toml.bak). That is your local state, not pack content.
    '*.bak'
)

# --- extra instance-root entries to copy into the pack ---------------------
# Only config\, defaultconfigs\, resourcepacks\ and options.txt are packed.
# Anything a mod writes to the INSTANCE ROOT is missed; add its name here to
# include it. Each name is a file or folder relative to the instance root.
# Empty by default - add your own, then run Update-Pack.ps1 again.
# Candidates seen in this instance (uncomment to pack them):
#   'omni-display-packs',   # Omni Display HUD / instrument packs
#   'visual_keybinder',     # on-screen key layout definitions
#   'skin_palettes',        # skin palette presets
#   'parachute',            # parachute model overrides
#   'log4j2.xml'            # log configuration
# Deliberately NOT listed: saves\, xaero\ (waypoints), exposures\, flashback\,
# armourers_workshop\ (skin cache), fancymenu_data\ (per-player menu state),
# and every launcher/cache/log folder - those are personal or regenerated.
$IncludeExtra = @()

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
