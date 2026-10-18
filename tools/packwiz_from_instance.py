#!/usr/bin/env python3
"""
packwiz_from_instance.py  --  build a packwiz pack directly from a live Minecraft instance.

Unlike `packwiz refresh` (which only hashes what is already there and is blind to where a jar
came from), this walks the instance, resolves every mod jar back to its ORIGIN, and emits
proper `mods/*.pw.toml` metafiles so packwiz downloads from the original CDN instead of
re-hosting somebody else's jar.

Origin resolution order (first hit wins):
  1. Modrinth  POST /v2/version_files      (sha1 lookup, keyless)  -> [update.modrinth]
  2. Old pack  hash table from a previous  (sha1 lookup, offline)  -> forgecdn [download]
  3. CurseForge api.curse.tools            (exact-filename match)  -> [update.curseforge]
  4. bundle    leave the jar in mods/ as an internal file

Safety property: step 3 only accepts an EXACT filename match, never a guess, so a resolved
mod is always the byte-identical build the instance is running.

Stdlib only. Python 3.9+.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import fnmatch
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

MODRINTH = "https://api.modrinth.com/v2"
CF = "https://api.curse.tools/v1/cf"
UA = {"User-Agent": "packwiz-from-instance/1.0 (+https://github.com/packwiz/packwiz)"}

# Content at the instance root that belongs in a modpack. Deliberately an ALLOW list:
# it is much safer to miss something the user can add with --include than to silently
# ship a cache directory, a log, or a personal keybind dump.
DEFAULT_INCLUDE = ["config", "defaultconfigs", "resourcepacks", "options.txt"]

# Directory names never packed even when they live inside included content. These hold
# regenerable junk: on this pack config/ was 67 MB, of which 57 MB was a downloaded
# fire-control video in a cache dir and the netMusicListCache album-art/mp3 cache.
DEFAULT_EXCLUDE_DIRS = {"cache", "netmusiclistcache", "logs", "crash-reports", "screenshots"}

# Modrinth's client_side is author-supplied, and for three mods in this pack it is simply
# wrong: the dev instance runs all three on the client every day. Emitting side = "server"
# is the dangerous direction, because packwiz-installer and every .mrpack launcher then
# SKIP the file on the client -- and a skipped LIBRARY breaks mod loading outright.
# That is exactly what shipped in 0.5.0-0.5.2: Lithostitched was skipped, the only copy
# left was the 1.4.8 jar-in-jar inside create_submarine, and the game died with
# "Mod terralith requires lithostitched 1.7.7 or above / Currently, lithostitched is 1.4.8".
# In a client install the "server" side is the integrated server, so server-side mods are
# needed there too. Keyed by Modrinth PROJECT id so a rename cannot silently void it.
FORCE_BOTH = {
    "XaDC71GB",  # Lithostitched                              -- worldgen library Terralith requires
    "tum2vG0T",  # Create: Crafts & Additions <-> Aeronautics -- docking energy transfer
    "4WWQxlQP",  # ServerCore                                 -- also optimises the integrated server
}

# Jars that are neither on Modrinth nor findable on CurseForge by filename, but whose
# exact CurseForge file we identified by hand and confirmed: mediafilez.forgecdn.net
# served a jar with the SAME sha1 as the one in the instance. Pinning lets players
# download the author's own file (and gives the mod real update metadata) instead of us
# re-hosting the jar in the pack. Value is (project id, file id).
#
# Only add an entry after downloading the URL and comparing sha1 -- a wrong pin produces a
# pack whose downloads fail their hash check. Verify-Pack.ps1 re-checks these URLs, so a bad
# pin is caught before release. The file id maps to the CDN path
# files/<id // 1000>/<id % 1000:03d>/<filename>.
# When the mod updates: update the file id, or delete the line so the jar is bundled again.
CURSEFORGE_PINS = {
    # name inside mods/                           project    file
    "crashutilities-9.0.4.jar":                   (371813,   5993450),
    "smsn-neoforge-1.4.5-1.21.1.jar":             (1129397,  8862254),
    # Aeronautics: Deployer Direction Fix
    "deployer_got_dizzy-0.1.2.jar":               (1544067,  8094798),
}


def forgecdn_url(file_id: int, filename: str) -> str:
    """The CDN URL CurseForge hands out for one file id."""
    return (f"https://mediafilez.forgecdn.net/files/"
            f"{file_id // 1000}/{file_id % 1000:03d}/{filename}")


VERISH = re.compile(r"^[vV]?\d")


def walk_content(root: Path, exclude_dirs: set[str], exclude_globs: list[str],
                 prefix: str = ""):
    """Yield (abs_path, rel_posix) for every file under root that survives the excludes.

    `prefix` is prepended to the yielded rel when testing exclude_globs, so globs
    are always written instance-relative ("config/jei/world/*") even though each
    top-level entry is walked separately from its own root.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d.lower() not in exclude_dirs)
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            rel = p.relative_to(root).as_posix()
            if exclude_globs:
                full = f"{prefix}/{rel}" if prefix else rel
                if any(fnmatch.fnmatch(full.lower(), g.lower()) for g in exclude_globs):
                    continue
            yield p, rel


def dir_size(root: Path) -> int:
    return sum(f.stat().st_size for f in root.rglob("*") if f.is_file())


# ----------------------------------------------------------------------------- small utils

def log(msg: str = "") -> None:
    print(msg, flush=True)


def sha1_of(p: Path) -> str:
    return _hash(p, "sha1")


def sha512_of(p: Path) -> str:
    return _hash(p, "sha512")


def _hash(p: Path, algo: str):
    h = hashlib.new(algo)
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_file(p: Path) -> str:
    return _hash(p, "sha256")


def toml_str(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def http_json(url: str, payload=None, method: str | None = None, tries: int = 4, timeout: int = 60):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = dict(UA)
    if data:
        headers["Content-Type"] = "application/json"
    last = None
    for attempt in range(tries):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(1.5 * (attempt + 1))
                last = e
                continue
            return {"__error__": e.code}
        except Exception as e:  # noqa: BLE001 - network is allowed to be flaky
            last = e
            if attempt < tries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            return {"__error__": f"{type(e).__name__}: {e}"}
    return {"__error__": f"{last}"}


# ------------------------------------------------------------------- CurseForge fingerprint
# Kept for reference / future use: api.curse.tools does not proxy POST /fingerprints (405),
# so this is not currently on the hot path.

def murmur2(data: bytes, length: int, seed: int) -> int:
    m, r = 0x5BD1E995, 24
    h = (seed ^ length) & 0xFFFFFFFF
    nblocks = length // 4
    for i in range(nblocks):
        o = i * 4
        k = data[o] | (data[o + 1] << 8) | (data[o + 2] << 16) | (data[o + 3] << 24)
        k = (k * m) & 0xFFFFFFFF
        k ^= k >> r
        k = (k * m) & 0xFFFFFFFF
        h = (h * m) & 0xFFFFFFFF
        h ^= k
    tail, o = length & 3, nblocks * 4
    if tail >= 3:
        h ^= data[o + 2] << 16
    if tail >= 2:
        h ^= data[o + 1] << 8
    if tail >= 1:
        h ^= data[o]
        h = (h * m) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * m) & 0xFFFFFFFF
    h ^= h >> 15
    return h


def cf_fingerprint(path: Path) -> int:
    raw = path.read_bytes()
    filtered = bytes(b for b in raw if b not in (9, 10, 13, 32))
    h = murmur2(filtered, len(filtered) - 4, 1)
    return h - 0x100000000 if h >= 0x80000000 else h


# ------------------------------------------------------------------------------- resolvers

def modrinth_lookup(hashes: list[str]) -> dict:
    """sha1 -> version object. Batched, keyless."""
    out: dict = {}
    for i in range(0, len(hashes), 100):
        chunk = hashes[i:i + 100]
        res = http_json(f"{MODRINTH}/version_files",
                        {"hashes": chunk, "algorithm": "sha1"}, method="POST")
        if isinstance(res, dict) and "__error__" not in res:
            out.update(res)
        elif "__error__" in res:
            log(f"    ! Modrinth batch {i // 100} failed: {res['__error__']}")
    return out


def modrinth_projects(ids: list[str]) -> dict:
    """project_id -> project object (for accurate client/server side)."""
    out: dict = {}
    ids = sorted(set(ids))
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        q = urllib.parse.quote(json.dumps(chunk))
        res = http_json(f"{MODRINTH}/projects?ids={q}")
        if isinstance(res, list):
            for p in res:
                out[p["id"]] = p
    return out


def search_term(filename: str) -> str:
    """'Create-Utilities-J-1.21.1-0.3.5+1.21.1.jar' -> 'Create Utilities J'"""
    stem = re.sub(r"\.jar(\.disabled)?$", "", filename, flags=re.I)
    keep = []
    for part in re.split(r"[-_\s]+", stem):
        if VERISH.match(part) and keep:
            break
        keep.append(part)
    return " ".join(keep) if keep else stem


def cf_mod_from_search(mod: dict) -> list[dict]:
    files = list(mod.get("latestFiles") or [])
    fl = http_json(f"{CF}/mods/{mod['id']}/files?pageSize=50")
    if isinstance(fl, dict) and isinstance(fl.get("data"), list):
        files += fl["data"]
    return files


def cf_sha1(file_obj: dict) -> str | None:
    """CurseForge reports a file's hashes as [{value, algo}] with algo 1 = sha1 and
    2 = md5, so a name match can always be promoted to a byte-level match."""
    for h in ((file_obj or {}).get("hashes") or []):
        if str(h.get("algo")) in ("1", "sha1"):
            v = h.get("value")
            return v.lower() if isinstance(v, str) else None
    return None


def cf_resolve(filename: str, cache: dict, delay: float = 0.0) -> dict | None:
    """Find the CurseForge file whose fileName matches EXACTLY. Returns
    {projectId, fileId, url, name, sha1} or None. Never guesses."""
    key = filename.lower()
    if key in cache:
        hit = cache[key]
        # caches written before the sha1 capture cannot be verified - refetch them
        if hit is None or "sha1" in hit:
            return hit
    term = search_term(filename)
    slug = re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-")
    queries = [f"slug={urllib.parse.quote(slug)}",
               f"searchFilter={urllib.parse.quote(term)}"]
    found = None
    seen: set[int] = set()
    for query in queries:
        sv = http_json(f"{CF}/mods/search?gameId=432&pageSize=20&{query}")
        if not isinstance(sv, dict) or not isinstance(sv.get("data"), list):
            continue
        for mod in sv["data"]:
            if mod.get("id") in seen:
                continue
            seen.add(mod.get("id"))
            for f in cf_mod_from_search(mod):
                if (f.get("fileName") or "").lower() == key:
                    found = {
                        "projectId": mod["id"],
                        "fileId": f["id"],
                        "url": f.get("downloadUrl"),
                        "name": mod.get("name") or filename,
                        "slug": mod.get("slug"),
                        "sha1": cf_sha1(f),
                    }
                    break
            if found:
                break
        if found:
            break
        if delay:
            time.sleep(delay)
    cache[key] = found
    return found


def load_old_index(path: Path):
    """Read a previous .mrpack (zip) or bare modrinth.index.json -> (by_sha1, by_name, deps)."""
    if path.suffix.lower() in (".zip", ".mrpack"):
        with zipfile.ZipFile(path) as z:
            raw = z.read("modrinth.index.json")
    else:
        raw = path.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    by_sha1, by_name = {}, {}
    for f in data.get("files", []):
        hashes = f.get("hashes") or {}
        urls = f.get("downloads") or []
        rec = {
            "path": f.get("path"),
            "filename": os.path.basename(f.get("path") or ""),
            "sha1": hashes.get("sha1"),
            "sha512": hashes.get("sha512"),
            "cf_url": next((u for u in urls if "forgecdn.net" in u), None),
            "mr_url": next((u for u in urls if "cdn.modrinth.com" in u), None),
            "env": f.get("env") or {},
        }
        if rec["sha1"]:
            by_sha1[rec["sha1"]] = rec
        if rec["filename"]:
            by_name[rec["filename"].lower()] = rec
    return by_sha1, by_name, (data.get("dependencies") or {})


def file_id_from_forgecdn(url: str) -> int | None:
    m = re.search(r"forgecdn\.net/files/(\d+)/(\d+)/", url or "")
    return int(m.group(1)) * 1000 + int(m.group(2)) if m else None


# ------------------------------------------------------------------------------ pack writer

def write_pw_toml(path: Path, *, name, filename, side, dl_url, dl_hash, dl_format,
                  update=None, option=None) -> None:
    lines = [
        f"name = {toml_str(name)}",
        f"filename = {toml_str(filename)}",
        f"side = {toml_str(side)}",
        "",
        "[download]",
        f"hash-format = {toml_str(dl_format)}",
        f"hash = {toml_str(dl_hash)}",
    ]
    if dl_url:
        lines.append(f"url = {toml_str(dl_url)}")
    if update:
        kind, body = update
        lines += ["", f"[update.{kind}]"]
        for k, v in body.items():
            lines.append(f"{k} = {v if isinstance(v, int) else toml_str(v)}")
    if option:
        lines += ["", "[option]"]
        lines.append(f"optional = {'true' if option.get('optional') else 'false'}")
        lines.append(f"default = {'true' if option.get('default') else 'false'}")
        lines.append(f"description = {toml_str(option.get('description', ''))}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_index(out: Path) -> int:
    entries = []
    for p in sorted(out.rglob("*")):
        if p.is_dir():
            continue
        rel = p.relative_to(out).as_posix()
        rec = {"file": rel, "hash": sha256_file(p)}
        if rel.endswith(".pw.toml"):
            rec["metafile"] = True
        entries.append(rec)
    lines = ['hash-format = "sha256"', ""]
    for e in entries:
        lines += ["[[files]]",
                  f"file = {toml_str(e['file'])}",
                  f"hash = {toml_str(e['hash'])}"]
        if e.get("metafile"):
            lines.append("metafile = true")
        lines.append("")
    (out / "index.toml").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    ignore = out / ".packwizignore"
    if not ignore.exists():
        ignore.write_text(
            "# packwiz-from-instance: reports are yours to read, not part of the pack\n"
            "audit-report.*\n"
            "verify-report.*\n"
            "# never distribute the ignore file itself\n"
            ".packwizignore\n", encoding="utf-8")
    return len(entries)


def write_pack_toml(out: Path, name, author, version, mc, loader, loader_version) -> None:
    idx_hash = sha256_file(out / "index.toml")
    txt = f"""name = {toml_str(name)}
author = {toml_str(author)}
version = {toml_str(version)}
pack-format = "packwiz:1.1.0"

[index]
file = "index.toml"
hash-format = "sha256"
hash = {toml_str(idx_hash)}

[versions]
minecraft = {toml_str(mc)}
{loader} = {toml_str(loader_version)}
"""
    (out / "pack.toml").write_text(txt, encoding="utf-8")


# ------------------------------------------------------------------------------------- main

def detect_loader(instance: Path, mc: str) -> tuple[str, str]:
    """PCL writes the loader version as a bare string into the instance json."""
    js = next((p for p in instance.glob("*.json")
               if p.name.lower() != "modrinth.index.json"), None)
    if js:
        try:
            text = js.read_text(encoding="utf-8", errors="replace")
            minor = ".".join(mc.split(".")[1:])          # 1.21.1 -> 21.1
            cands = re.findall(rf'"{re.escape(minor)}\.(\d+)"', text)
            if cands:
                return "neoforge", f"{minor}.{max(set(cands), key=cands.count)}"
        except OSError:
            pass
    return "neoforge", ""


# ------------------------------------------------------------------------------------- audit

def read_pw_toml(path: Path) -> dict:
    """Read back a .pw.toml written by this script (flat sections, one level deep)."""
    rec: dict = {"update": None}
    section = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if "=" not in line:
            continue
        key, val = (x.strip() for x in line.split("=", 1))
        if len(val) >= 2 and val.startswith('"') and val.endswith('"'):
            val = val[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        if section is None:
            rec[key] = val
        elif section == "download":
            rec["dl_" + key] = val
        elif section.startswith("update."):
            if rec["update"] is None:
                rec["update"] = {"kind": section.split(".", 1)[1]}
            rec["update"][key] = val
    return rec


def modrinth_latest_for(hashes: list, mc: str, loader: str) -> dict:
    """One batch call: sha1 -> latest version object for this loader + game version.

    Files the project does not publish for this target are simply absent from the
    response, which is how we tell 'no Modrinth build for us' from 'up to date'.
    """
    if not hashes:
        return {}
    payload = {"hashes": list(hashes), "algorithm": "sha1",
               "loaders": [loader], "game_versions": [mc]}
    try:
        r = http_json(f"{MODRINTH}/version_files/update", payload)
    except Exception as e:  # noqa: BLE001
        log(f"  ! modrinth update check failed: {type(e).__name__} {e}")
        return {}
    return r if isinstance(r, dict) else {}


def cf_latest_file(project_id, mc: str, loader: str, cache: dict):
    """Newest CurseForge file for one project, filtered to this MC version + loader.

    The filter is NOT optional: unfiltered, CTM's newest file is a 1.20.1 build.
    """
    key = f"latest:{project_id}:{mc}:{loader}"
    if key in cache:
        return cache[key]
    url = (f"{CF}/mods/{project_id}/files?pageSize=50"
           f"&gameVersion={urllib.parse.quote(mc)}&modLoader={urllib.parse.quote(loader)}")
    best = None
    try:
        r = http_json(url)
        files = r.get("data") if isinstance(r, dict) else None
        for f in files or []:
            if best is None or (f.get("fileDate") or "") > (best.get("fileDate") or ""):
                best = f
    except Exception:  # noqa: BLE001
        best = None
    cache[key] = best
    return best


def modrinth_candidates(name: str, mc: str, loader: str, cache: dict, limit: int = 3) -> list:
    """Best-effort Modrinth search for a project by human name. Heuristic by nature."""
    key = f"cand:{name}:{mc}:{loader}"
    if key in cache:
        return cache[key]
    facets = json.dumps([["project_type:mod"], [f"versions:{mc}"], [f"categories:{loader}"]])
    try:
        r = http_json(f"{MODRINTH}/search?query={urllib.parse.quote(name)}"
                      f"&limit={limit}&facets={urllib.parse.quote(facets)}")
        hits = (r.get("hits") or []) if isinstance(r, dict) else []
    except Exception:  # noqa: BLE001
        hits = []
    cache[key] = hits
    return hits


def audit_pack(out: Path, instance: Path, mc: str, loader: str, *,
               cross: bool, jobs: int, cache: dict) -> tuple:
    """Compare the finished pack against both platforms. READ ONLY: never writes to the pack."""
    pws = sorted((out / "mods").glob("*.pw.toml"))
    if not pws:
        return ["no .pw.toml files found -- build the pack first (drop --audit-only)"], {}

    mods = []
    for p in pws:
        rec = read_pw_toml(p)
        rec["_file"] = p.name
        mods.append(rec)
    mr = [m for m in mods if (m.get("update") or {}).get("kind") == "modrinth"]
    cf = [m for m in mods if (m.get("update") or {}).get("kind") == "curseforge"]
    none = [m for m in mods if not m.get("update")]

    sha1_by_name = {}
    moddir = instance / "mods"
    if moddir.is_dir():
        for jar in moddir.glob("*.jar"):
            sha1_by_name[jar.name] = sha1_of(jar)

    L = [f"pack      : {out}",
         f"instance  : {instance}",
         f"target    : minecraft={mc} {loader}",
         "",
         f"mods: {len(mods)}   modrinth={len(mr)}  curseforge={len(cf)}  no-update-metadata={len(none)}",
         ""]

    # ---- A. Modrinth: which of the installed jars have a newer build here? -----
    hashes = [sha1_by_name[m["filename"]] for m in mr if m["filename"] in sha1_by_name]
    latest = modrinth_latest_for(hashes, mc, loader)
    mr_up = []
    mr_unmatched = []
    for m in mr:
        sha = sha1_by_name.get(m["filename"])
        v = latest.get(sha) if sha else None
        if not v:
            mr_unmatched.append(m)
            continue
        if v.get("id") != m["update"].get("version"):
            files = v.get("files") or []
            nf = next((f.get("filename") for f in files if f.get("primary")), None) \
                or next((f.get("filename") for f in files), None)
            mr_up.append((m["filename"], nf or v.get("version_number"),
                          v.get("version_type"), (v.get("date_published") or "")[:10]))
    L.append(f"== A. Modrinth: {len(mr_up)} update(s) available ==")
    L.append(f"   checked {len(hashes)}/{len(mr)} installed jars "
             f"(loaders=[{loader}] game_versions=[{mc}])")
    for cur, new, vt, dt in sorted(mr_up):
        L.append(f"   {cur}")
        L.append(f"      -> {new}   [{vt}, {dt}]")
    if not mr_up:
        L.append("   (none)")
    if mr_unmatched:
        L.append(f"   {len(mr_unmatched)} modrinth-sourced jar(s) had no build for this target "
                 f"(usually the project dropped NeoForge 1.21.1):")
        for m in sorted(mr_unmatched, key=lambda x: x["filename"]):
            L.append(f"      ? {m['filename']}")
    L.append("")

    # ---- B. CurseForge: newest file per project, same filters ------------------
    cf_up = []
    cf_failed = []
    if cf:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
            futs = {ex.submit(cf_latest_file, m["update"].get("project-id"), mc, loader, cache): m
                    for m in cf}
            for fut in concurrent.futures.as_completed(futs):
                m = futs[fut]
                try:
                    best = fut.result()
                except Exception:  # noqa: BLE001
                    best = None
                if not best:
                    cf_failed.append(m)
                    continue
                try:
                    cur = int(m["update"].get("file-id") or 0)
                except (TypeError, ValueError):
                    cur = -1
                if int(best.get("id") or 0) != cur:
                    cf_up.append((m["filename"], best.get("fileName"), best.get("id"),
                                  (best.get("fileDate") or "")[:10]))
    L.append(f"== B. CurseForge: {len(cf_up)} update(s) available out of {len(cf)} project(s) ==")
    for cur, new, fid, dt in sorted(cf_up):
        L.append(f"   {cur}")
        L.append(f"      -> {new}   [fileId {fid}, {dt}]")
    if not cf_up:
        L.append("   (none)")
    for m in sorted(cf_failed, key=lambda x: x["filename"]):
        L.append(f"      ? could not query project-id={m['update'].get('project-id')} "
                 f"for {m['filename']}")
    L.append("")

    # ---- C. Mods packwiz cannot update at all ---------------------------------
    L.append(f"== C. No update metadata: {len(none)} mod(s) ==")
    L.append("   'packwiz update' skips these; bump them by hand (or add a [update.*] block).")
    for m in sorted(none, key=lambda x: x.get("filename") or ""):
        url = m.get("dl_url") or ""
        plat = ("curseforge-url" if "forgecdn" in url
                else "modrinth-url" if "cdn.modrinth.com" in url else "locally re-hosted")
        L.append(f"   {m.get('filename')}   [{plat}]")
    L.append("")

    # ---- D. Jars you redistribute yourself ------------------------------------
    bundled = sorted(p.name for p in (out / "mods").glob("*.jar"))
    L.append(f"== D. Bundled in the pack by you: {len(bundled)} jar(s) ==")
    L.append("   These ship inside the pack, so you always need your own rights to them.")
    for b in bundled:
        L.append(f"   {b}")
    L.append("")

    # ---- E. cross-platform candidates (heuristic, opt-in) ---------------------
    cross_rows = []
    if cross and cf:
        L.append(f"== E. CurseForge-sourced mods that may also exist on Modrinth ({len(cf)}) ==")
        L.append("   Name-based heuristic. HIGH = the slug matches the mod name exactly.")
        L.append("   Confirm before switching: the version on the other platform usually differs.")
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
            futs = {ex.submit(modrinth_candidates, (m.get("name") or m.get("filename") or ""),
                              mc, loader, cache): m for m in cf}
            for fut in concurrent.futures.as_completed(futs):
                m = futs[fut]
                try:
                    hits = fut.result()
                except Exception:  # noqa: BLE001
                    hits = []
                want = re.sub(r"[^a-z0-9]+", "-", (m.get("name") or "").lower()).strip("-")
                best, conf = None, ""
                for h in hits:
                    if h.get("slug") == want:
                        best, conf = h, "HIGH"
                        break
                if best is None and hits:
                    best, conf = hits[0], "?"
                cross_rows.append((m.get("name") or m.get("filename"),
                                   (best or {}).get("title"), (best or {}).get("slug"), conf))
        for name, title, slug, conf in sorted(cross_rows):
            L.append(f"   {name}")
            if title:
                L.append(f"      ~ {title}  (slug: {slug})  [{conf}]")
            else:
                L.append("      ~ no candidate found")
        L.append("")

    summary = {
        "pack": str(out),
        "instance": str(instance),
        "minecraft": mc,
        "loader": loader,
        "counts": {"total": len(mods), "modrinth": len(mr), "curseforge": len(cf),
                   "no_update_metadata": len(none)},
        "modrinth_updates": [{"current": c, "latest": n, "type": t, "date": d}
                             for c, n, t, d in sorted(mr_up)],
        "modrinth_no_build_for_target": sorted(m["filename"] for m in mr_unmatched),
        "curseforge_updates": [{"current": c, "latest": n, "fileId": fid, "date": d}
                               for c, n, fid, d in sorted(cf_up)],
        "no_update_metadata": sorted(m.get("filename") for m in none),
        "bundled": bundled,
        "cross_platform_candidates": [{"name": n, "title": t, "slug": s, "confidence": c}
                                      for n, t, s, c in sorted(cross_rows)],
    }
    return L, summary


def write_audit(out: Path, lines: list, summary: dict) -> None:
    txt = out / "audit-report.txt"
    txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "audit-report.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    log(f"\naudit report written: {txt}")


# --------------------------------------------------------------------------- verify

def _toml_val(txt: str, key: str) -> str | None:
    m = re.search(rf'^{re.escape(key)}\s*=\s*"([^"]*)"', txt, re.M)
    return m.group(1) if m else None


def read_index_toml(path: Path) -> list[dict]:
    """Minimal reader for the [[files]] table array packwiz writes."""
    entries: list[dict] = []
    cur: dict | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line == "[[files]]":
            cur = {}
            entries.append(cur)
            continue
        if cur is None or line.startswith("[") or "=" not in line:
            continue
        k, v = (x.strip() for x in line.split("=", 1))
        if len(v) >= 2 and v.startswith('"') and v.endswith('"'):
            v = v[1:-1]
        elif v == "true":
            v = True
        elif v == "false":
            v = False
        cur[k] = v
    return entries


def modids_of(jar: Path) -> list[tuple[str, str]]:
    """[(modId, version)] declared inside a mod jar (best effort, never raises)."""
    found: list[tuple[str, str]] = []
    try:
        with zipfile.ZipFile(jar) as z:
            names = z.namelist()
            tomls = [n for n in names
                     if n.endswith("neoforge.mods.toml") or n.endswith("/mods.toml")]
            if tomls:
                txt = z.read(tomls[0]).decode("utf-8", "replace")
                for block in re.split(r"\[\[mods\]\]", txt)[1:]:
                    mid = re.search(r'modId\s*=\s*"([^"]+)"', block)
                    ver = re.search(r'(?<![\w.])version\s*=\s*"([^"]+)"', block)
                    if mid:
                        found.append((mid.group(1), ver.group(1) if ver else ""))
                return found
            fab = [n for n in names if n.endswith("fabric.mod.json")]
            if fab:
                d = json.loads(z.read(fab[0]).decode("utf-8", "replace"))
                if d.get("id"):
                    found.append((str(d["id"]), str(d.get("version") or "")))
    except Exception:  # noqa: BLE001
        pass
    return found


def fetch_hash(url: str, timeout: int = 300) -> tuple[str, str]:
    """Download a URL and return (sha512, sha1) of the bytes it really serves."""
    h512 = hashlib.sha512()
    h1 = hashlib.sha1()
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "packwiz-from-instance-verify/1.0")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            h512.update(chunk)
            h1.update(chunk)
    return h512.hexdigest(), h1.hexdigest()


def head_ok(url: str, timeout: int = 30) -> tuple[str, str]:
    """('ok'|'bad') for a download URL, without pulling the whole body."""
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, method=method)
            req.add_header("User-Agent", "packwiz-from-instance-verify/1.0")
            if method == "GET":
                req.add_header("Range", "bytes=0-0")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return "ok", str(r.status)
        except urllib.error.HTTPError as e:
            if method == "HEAD" and e.code in (403, 405, 501):
                continue
            return "bad", f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001
            if method == "HEAD":
                continue
            return "bad", type(e).__name__
    return "bad", "no answer to HEAD or GET"


def modrinth_versions(ids: list[str]) -> dict:
    out: dict = {}
    ids = sorted(set(ids))
    for i in range(0, len(ids), 100):
        q = urllib.parse.quote(json.dumps(ids[i:i + 100]))
        res = http_json(f"{MODRINTH}/versions?ids={q}")
        if isinstance(res, list):
            for v in res:
                out[v["id"]] = v
    return out


def cf_file_by_id(project_id, file_id, mc: str, loader: str, cache: dict) -> dict | None:
    key = f"file|{project_id}|{file_id}"
    if key in cache:
        return cache[key]
    out = None
    for extra in (f"&gameVersion={mc}&modLoader={loader}", ""):
        res = http_json(f"{CF}/mods/{project_id}/files?pageSize=50&index=0{extra}")
        if isinstance(res, dict):
            for f in res.get("data") or []:
                if str(f.get("id")) == str(file_id):
                    out = f
                    break
        if out:
            break
    cache[key] = out
    return out


def verify_pack(instance: Path, out: Path, *, mc: str, loader: str,
                check_urls: bool = False, jobs: int = 8,
                exclude_globs: list[str] | None = None) -> tuple[list, dict]:
    """Compare a generated packwiz pack against the instance it was built from."""
    L: list[str] = []
    problems: list[str] = []
    notes: list[str] = []

    def bad(msg: str) -> None:
        problems.append(msg)
        L.append("   !! " + msg)

    def ok(msg: str) -> None:
        L.append("   ok: " + msg)

    L.append(f"pack     : {out}")
    L.append(f"instance : {instance}")
    L.append("")

    # ---- 1. target versions ---------------------------------------------------
    ptxt = (out / "pack.toml").read_text(encoding="utf-8")
    L.append("== 1. target versions ==")
    L.append(f"   pack.toml: name={_toml_val(ptxt, 'name')} version={_toml_val(ptxt, 'version')}")
    pmc, plv = _toml_val(ptxt, "minecraft"), _toml_val(ptxt, loader)
    L.append(f"   pack     : minecraft={pmc} {loader}={plv}")
    iv = None
    # NeoForge numbers itself after the minor version: MC 1.21.1 -> loader 21.1.x
    nf_prefix = ".".join(mc.split(".")[1:]) if "." in mc else mc
    for j in sorted(instance.glob("*.json")):
        try:
            t = j.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        m = (re.search(rf'"{re.escape(loader)}"\s*:\s*"([^"]+)"', t)
             or re.search(rf'{re.escape(loader)}[-_](\d+\.\d+\.\d+)', t)
             or re.search(rf'"({re.escape(nf_prefix)}\.\d+)"', t)
             or re.search(rf'\b({re.escape(nf_prefix)}\.\d+)\b', t))
        if m:
            iv = m.group(1)
            break
    L.append(f"   instance : minecraft={mc} {loader}={iv or 'not found'}")
    if pmc and pmc != mc:
        bad(f"pack targets minecraft {pmc}, instance is {mc}")
    if iv and plv and iv != plv:
        notes.append(f"loader version differs: pack={plv} instance={iv}")
        L.append(f"   note: loader version differs (pack {plv} vs instance {iv})")
    if not problems:
        ok("targets line up")

    # ---- 2. index.toml <-> disk ----------------------------------------------
    L.append("")
    L.append("== 2. index.toml vs files on disk ==")
    idx = read_index_toml(out / "index.toml")
    listed = {e.get("file"): e for e in idx if e.get("file")}
    skip = {"pack.toml", "index.toml", ".packwizignore"}
    on_disk = set()
    for f in sorted(out.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(out).as_posix()
        if rel in skip or re.match(r"^(audit|verify)-report\.(txt|json)$", rel):
            continue
        on_disk.add(rel)
    gone = sorted(set(listed) - on_disk)
    unlisted = sorted(on_disk - set(listed))
    changed = [r for r in sorted(set(listed) & on_disk)
               if listed[r].get("hash") != sha256_file(out / r)]
    L.append(f"   index entries {len(idx)} / files on disk {len(on_disk)}")
    if gone:
        bad(f"{len(gone)} index entr(ies) with no file on disk: {gone[:5]}")
    if changed:
        bad(f"{len(changed)} file(s) whose sha256 != index.toml: {changed[:5]}")
    if unlisted:
        notes.append(f"{len(unlisted)} file(s) exist but are not in index.toml "
                     f"(never downloaded by players): {unlisted[:5]}")
        L.append(f"   note: {len(unlisted)} unlisted file(s) (harmless): {unlisted[:5]}")
    if not gone and not changed:
        ok("every index entry exists and its sha256 matches")

    # ---- 3. mod coverage ------------------------------------------------------
    L.append("")
    L.append("== 3. coverage: every active jar in the instance ==")
    inst_mods = instance / "mods"
    active = sorted(p.name for p in inst_mods.glob("*.jar") if p.is_file())
    bundled = sorted(p.name for p in (out / "mods").glob("*.jar"))
    metas = {p.name[:-len(".pw.toml")]: read_pw_toml(p)
             for p in sorted((out / "mods").glob("*.pw.toml"))}
    by_name: dict[str, list[str]] = {}
    for stem, rec in metas.items():
        by_name.setdefault(rec.get("filename") or "", []).append(stem)
    gaps, renamed, orphans = [], [], []
    for jar in active:
        if jar in by_name or jar in bundled:
            continue
        stem = jar[:-4]
        if stem in metas:
            renamed.append((jar, metas[stem].get("filename")))
        else:
            gaps.append(jar)
    for stem, rec in sorted(metas.items()):
        fn = rec.get("filename") or ""
        if fn in active or (stem + ".jar") in active:
            continue
        orphans.append(fn or stem)
    L.append(f"   instance {len(active)} active jar(s) / pack {len(metas)} metafile(s) + {len(bundled)} bundled")
    if gaps:
        bad(f"{len(gaps)} active jar(s) in NO form in the pack: {gaps[:8]}")
    if orphans:
        bad(f"{len(orphans)} metafile(s) point at a mod the instance does not have: {orphans[:8]}")
    if renamed:
        for jar, fn in renamed:
            notes.append(f"local jar is a renamed copy of a published file: {jar} -> {fn}")
            L.append(f"   note: '{jar}' installs as '{fn}' (same bytes, published name)")
    if not gaps and not orphans:
        ok(f"all {len(active)} active jar(s) are accounted for")

    # ---- 4a. recorded hash == your bytes ---------------------------------------
    L.append("")
    L.append("== 4a. does the pack install exactly the bytes you tested? ==")
    mism, unver = [], []
    for stem, rec in sorted(metas.items()):
        local = inst_mods / (rec.get("filename") or "")
        if not local.exists():
            alt = inst_mods / (stem + ".jar")
            local = alt if alt.exists() else None
        if local is None:
            unver.append(stem)
            continue
        fmt = rec.get("dl_format") or "sha512"
        want = (rec.get("dl_hash") or "").lower()
        try:
            got = _hash(local, fmt).lower()
        except Exception:  # noqa: BLE001
            unver.append(stem)
            continue
        if want and got != want:
            mism.append((local.name, fmt, want[:12], got[:12]))
    L.append(f"   compared {len(metas) - len(unver)}/{len(metas)} metafile(s) with the local jar")
    if mism:
        for n, f, a, b in mism[:20]:
            bad(f"hash mismatch {n}: pack has {f}={a}... local is {b}...")
    else:
        ok("every recorded hash equals the bytes on disk")
    if unver:
        L.append(f"   note: {len(unver)} metafile(s) could not be matched to a local jar: {unver[:5]}")

    # ---- 4b. recorded hash == what the source publishes for that URL -----------
    L.append("")
    L.append("== 4b. does the download URL really serve that hash? ==")
    mr_meta = {s: r for s, r in metas.items() if (r.get("update") or {}).get("kind") == "modrinth"}
    ver_ids = sorted({(r["update"] or {}).get("version") for r in mr_meta.values()
                      if (r["update"] or {}).get("version")})
    versions = modrinth_versions(ver_ids) if ver_ids else {}
    mr_bad = 0
    for stem, rec in sorted(mr_meta.items()):
        v = versions.get((rec["update"] or {}).get("version"))
        if not v:
            continue
        f = next((x for x in v.get("files", []) if (x.get("url") or "") == rec.get("dl_url")), None)
        if f is None:
            f = next((x for x in v.get("files", [])
                      if (x.get("filename") or "") == (rec.get("filename") or "")), None)
        if f is None:
            mr_bad += 1
            bad(f"Modrinth version {v.get('id')} has no file matching {rec.get('filename')}")
            continue
        declared = (f.get("hashes") or {}).get("sha512") or ""
        if declared and declared.lower() != (rec.get("dl_hash") or "").lower():
            mr_bad += 1
            bad(f"Modrinth file hash differs for {rec.get('filename')}")
    if mr_meta and not mr_bad:
        ok(f"all {len(mr_meta)} Modrinth metafile(s) match what Modrinth publishes for that URL")

    cache: dict = {}
    cf_meta = {s: r for s, r in metas.items() if (r.get("update") or {}).get("kind") == "curseforge"}
    url_only = [r.get("filename") for s, r in metas.items()
                if (r.get("update") or {}).get("kind") != "modrinth" and not r.get("update")]
    if url_only:
        notes.append(f"{len(url_only)} metafile(s) carry no [update] block, so their URL was never "
                     f"cross-checked: {sorted(x for x in url_only if x)[:5]}")
    cf_checked = cf_bad = 0
    if cf_meta:
        def _one(item):
            stem, rec = item
            u = rec["update"]
            f = cf_file_by_id(u.get("project-id"), u.get("file-id"), mc, loader, cache)
            return stem, rec, f
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
            for stem, rec, f in ex.map(_one, list(cf_meta.items())):
                if not f:
                    continue
                cf_checked += 1
                local = inst_mods / (rec.get("filename") or "")
                if not local.exists():
                    continue
                declared = next((h.get("value") for h in (f.get("hashes") or [])
                                 if str(h.get("algo")) in ("1", "sha1")), None)
                if declared and declared.lower() != sha1_of(local).lower():
                    cf_bad += 1
                    bad(f"CurseForge file {f.get('id')} sha1 != local jar for {rec.get('filename')}")
        if cf_checked and not cf_bad:
            ok(f"all {cf_checked} CurseForge metafile(s) confirmed by file id against CurseForge")

    # ---- 5. bundled jars ------------------------------------------------------
    L.append("")
    L.append("== 5. bundled jars you redistribute ==")
    b_bad = []
    for b in bundled:
        src, dst = inst_mods / b, out / "mods" / b
        if not src.exists():
            b_bad.append(f"{b}: not in the instance at all")
        elif sha256_file(src) != sha256_file(dst):
            b_bad.append(f"{b}: bundled copy differs from the instance jar")
    if b_bad:
        for m in b_bad:
            bad(m)
    else:
        L.append(f"   {len(bundled)} jar(s), all byte-identical to the instance copy")
        for b in bundled:
            L.append(f"     - {b}")

    # ---- 6. duplicate mod ids -------------------------------------------------
    L.append("")
    L.append("== 6. duplicate mod ids in the instance ==")
    ids: dict[str, list[str]] = {}
    alljars = [inst_mods / n for n in active]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        results = list(ex.map(modids_of, alljars))
    for jar, pairs in zip(alljars, results):
        for mid, ver in pairs:
            ids.setdefault(mid, []).append(f"{jar.name} (v{ver or '?'})")
    dupes = {k: v for k, v in ids.items() if len(v) > 1}
    if dupes:
        for k, v in sorted(dupes.items()):
            bad(f"duplicate modId '{k}': {', '.join(v)}")
    else:
        ok(f"read {len(alljars)} jar(s), {len(ids)} distinct modId, no duplicates")
    unreadable = [j.name for j, r in zip(alljars, results) if not r]
    if unreadable:
        L.append(f"   note: no mod id found in {len(unreadable)} jar(s): {unreadable[:5]}")

    # ---- 7. side distribution -------------------------------------------------
    L.append("")
    L.append("== 7. client/server side ==")
    sides: dict[str, list[str]] = {}
    for stem, rec in metas.items():
        sides.setdefault(rec.get("side") or "?", []).append(rec.get("filename") or stem)
    for k in sorted(sides):
        L.append(f"   {k}: {len(sides[k])}")
    if "server" in sides or "client" in sides:
        for k in ("client", "server"):
            for n in sides.get(k, [])[:10]:
                L.append(f"     {k}: {n}")

    # ---- 8. packed content parity ---------------------------------------------
    L.append("")
    L.append("== 8. packed content vs the instance ==")
    for top in ("config", "defaultconfigs", "resourcepacks"):
        ip, pp = instance / top, out / top
        if not ip.exists() and not pp.exists():
            continue
        ifiles = {p.relative_to(instance).as_posix() for p in ip.rglob("*") if p.is_file()} if ip.exists() else set()
        pfiles = {p.relative_to(out).as_posix() for p in pp.rglob("*") if p.is_file()} if pp.exists() else set()
        dropped = sorted(ifiles - pfiles)
        added = sorted(pfiles - ifiles)
        globs = list(exclude_globs or [])

        def _by_rule(d: str) -> str | None:
            if any(part.lower() in DEFAULT_EXCLUDE_DIRS for part in d.split("/")):
                return "cache/junk"
            if any(fnmatch.fnmatch(d.lower(), g.lower()) for g in globs):
                return "excluded by rule"
            return None

        excluded = [d for d in dropped if _by_rule(d) == "cache/junk"]
        by_rule = [d for d in dropped if _by_rule(d) == "excluded by rule"]
        other = [d for d in dropped if _by_rule(d) is None]
        L.append(f"   {top}/: instance {len(ifiles)} file(s) -> pack {len(pfiles)}")
        if excluded:
            L.append(f"     excluded cache/junk: {len(excluded)}")
        if by_rule:
            L.append(f"     excluded by rule: {len(by_rule)} (e.g. {by_rule[:3]})")
        if other:
            L.append(f"     not packed for another reason: {len(other)} (e.g. {other[:3]})")
        if added:
            notes.append(f"{top}/: {len(added)} file(s) in the pack but not in the instance")
            L.append(f"     only in the pack: {len(added)} (e.g. {added[:3]})")
        if not other and not added:
            ok(f"{top}/ reproduced exactly (minus {len(excluded)} cache file(s)"
               + (f", {len(by_rule)} excluded by rule" if by_rule else "") + ")")
    for single in ("options.txt",):
        ip, pp = instance / single, out / single
        if ip.exists() and pp.exists():
            same = sha256_file(ip) == sha256_file(pp)
            L.append(f"   {single}: {'identical' if same else 'DIFFERS'}")
            if not same:
                bad(f"{single} in the pack differs from the instance")

    # ---- 9. URL reachability --------------------------------------------------
    if check_urls:
        L.append("")
        L.append("== 9. download URLs ==")
        urls = {}
        for stem, rec in metas.items():
            u = rec.get("dl_url")
            if u:
                urls.setdefault(u, []).append(rec.get("filename") or stem)
        for b in bundled:
            urls.setdefault("(bundled)", []).append(b)
        distinct = [u for u in urls if u != "(bundled)"]
        L.append(f"   checking {len(distinct)} distinct URL(s)...")
        dead = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
            for u, (status, detail) in zip(distinct, ex.map(head_ok, distinct)):
                if status != "ok":
                    dead.append((u, detail, urls[u][:2]))
        if dead:
            for u, detail, who in dead[:25]:
                bad(f"unreachable ({detail}): {u}  [{', '.join(who)}]")
        else:
            ok(f"all {len(distinct)} URL(s) answered")

        # A reachable URL is not a correct URL. The metafiles that carry no
        # [update] block were never cross-checked against any source API, so pull
        # them and compare the bytes - this is the check that catches a locally
        # patched jar being pointed at an upstream download.
        unchecked = {s: r for s, r in metas.items()
                     if not (r.get("update") or {}).get("kind") and r.get("dl_url")}
        if unchecked:
            L.append("")
            L.append("== 9b. bytes served by the un-cross-checked URLs ==")
            L.append(f"   fetching {len(unchecked)} metafile(s) that have no [update] block...")

            def _fetch(item):
                stem, rec = item
                try:
                    got512, got1 = fetch_hash(rec["dl_url"])
                except Exception as e:  # noqa: BLE001
                    return stem, rec, None, f"{type(e).__name__}: {e}"
                # read_pw_toml keys the [download] section as dl_<key>, so the
                # format key really is "dl_hash-format" (TOML spelling, hyphen).
                fmt = rec.get("dl_hash-format") or rec.get("dl_hash_format") or "sha512"
                got = got512 if fmt == "sha512" else got1
                want = (rec.get("dl_hash") or "").lower()
                if want and got.lower() != want:
                    return stem, rec, False, f"served {fmt} {got[:16]}... but recorded {want[:16]}..."
                return stem, rec, True, None

            fetch_bad = 0
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
                for stem, rec, good, detail in ex.map(_fetch, list(unchecked.items())):
                    if good is False:
                        fetch_bad += 1
                        bad(f"URL does not serve the recorded bytes: {rec.get('filename')}  ({detail})")
                    elif good is None:
                        fetch_bad += 1
                        bad(f"could not fetch {rec.get('filename')}: {detail}")
            if not fetch_bad:
                ok(f"all {len(unchecked)} un-cross-checked URL(s) serve exactly the recorded bytes")

    # ---- summary --------------------------------------------------------------
    L.append("")
    L.append("== result ==")
    if problems:
        L.append(f"   {len(problems)} PROBLEM(S):")
        for m in problems:
            L.append(f"     - {m}")
    else:
        L.append("   no problems found: the pack reproduces this instance")
    if notes:
        L.append(f"   {len(notes)} note(s):")
        for m in notes:
            L.append(f"     - {m}")

    summary = {
        "pack": str(out), "instance": str(instance), "minecraft": mc, "loader": loader,
        "problems": problems, "notes": notes,
        "counts": {"instance_jars": len(active), "metafiles": len(metas), "bundled": len(bundled),
                   "index_entries": len(idx), "url_only_metafiles": len(url_only)},
        "renamed_local_jars": [{"local": a, "published": b} for a, b in renamed],
        "duplicate_mod_ids": dupes,
        "bundled": bundled,
    }
    return L, summary


def write_verify(out: Path, lines: list, summary: dict) -> None:
    txt = out / "verify-report.txt"
    txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "verify-report.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    log(f"\nverify report written: {txt}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Build a packwiz pack from a live Minecraft instance")
    ap.add_argument("--instance", required=True, help=r"e.g. D:\Minecraft\.minecraft\versions\ODF-JustAero")
    ap.add_argument("--out", required=True, help="output packwiz pack folder")
    ap.add_argument("--name", default=None)
    ap.add_argument("--author", default="")
    ap.add_argument("--version", default="1.0.0")
    ap.add_argument("--mc", default=None)
    ap.add_argument("--loader", default="neoforge")
    ap.add_argument("--loader-version", default=None)
    ap.add_argument("--old-index", default=None, help="previous .mrpack/.zip or modrinth.index.json")
    ap.add_argument("--include", action="append", default=[], help="extra root entry to copy (repeatable)")
    ap.add_argument("--exclude-glob", action="append", default=[],
                    help="extra path glob to skip inside copied content (repeatable)")
    ap.add_argument("--keep-caches", action="store_true",
                    help="do not strip cache/log directories from copied content")
    ap.add_argument("--no-options", action="store_true", help="do not ship options.txt")
    ap.add_argument("--include-disabled", action="store_true", help="ship .jar.disabled as disabled-by-default optional mods")
    ap.add_argument("--no-cf-search", action="store_true", help="skip keyless CurseForge filename resolution")
    ap.add_argument("--no-bundle", action="store_true", help="do not bundle unresolved jars")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--cache", default=None, help="JSON cache of CurseForge lookups")
    ap.add_argument("--report", default=None)
    ap.add_argument("--force", action="store_true", help="allow writing into a non-empty output folder")
    ap.add_argument("--dry-run", action="store_true", help="show the plan, write nothing")
    ap.add_argument("--audit", action="store_true",
                    help="after building, write audit-report.txt/.json: which mods have a newer "
                         "build on Modrinth or CurseForge, and which ones packwiz cannot update")
    ap.add_argument("--audit-only", action="store_true",
                    help="skip the build and audit the existing pack in --out (read-only)")
    ap.add_argument("--audit-cross", action="store_true",
                    help="also search Modrinth for every CurseForge-sourced mod (slow, heuristic)")
    ap.add_argument("--verify", action="store_true",
                    help="skip the build and compare the pack in --out against the instance: "
                         "coverage, hashes, duplicates, content parity (read-only)")
    ap.add_argument("--check-urls", action="store_true",
                    help="with --verify: also HTTP-check every download URL")
    args = ap.parse_args()

    instance = Path(args.instance).resolve()
    out = Path(args.out).resolve()
    if not instance.is_dir():
        log(f"instance not found: {instance}")
        return 2

    # ---- audit-only: never touch the pack, so it must run BEFORE the safety check
    if args.audit_only:
        pack_toml = out / "pack.toml"
        if not pack_toml.exists():
            log(f"no pack.toml in {out} -- build the pack first (drop --audit-only)")
            return 2
        ptxt = pack_toml.read_text(encoding="utf-8")
        m = re.search(r'^minecraft\s*=\s*"([^"]+)"', ptxt, re.M)
        mc = args.mc or (m.group(1) if m else "1.21.1")
        cache = {}
        if args.cache and Path(args.cache).exists():
            try:
                cache = json.loads(Path(args.cache).read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                cache = {}
        lines, summary = audit_pack(out, instance, mc, args.loader,
                                    cross=args.audit_cross, jobs=args.jobs, cache=cache)
        if args.cache:
            Path(args.cache).write_text(json.dumps(cache, indent=1), encoding="utf-8")
        write_audit(out, lines, summary)
        return 0

    # ---- verify: read-only comparison against the instance --------------------
    if args.verify:
        pack_toml = out / "pack.toml"
        if not pack_toml.exists():
            log(f"no pack.toml in {out} -- build the pack first (drop --verify)")
            return 2
        ptxt = pack_toml.read_text(encoding="utf-8")
        mc = args.mc or _toml_val(ptxt, "minecraft") or "1.21.1"
        lines, summary = verify_pack(instance, out, mc=mc, loader=args.loader,
                                     check_urls=args.check_urls,
                                     jobs=max(args.jobs, 8),
                                     exclude_globs=list(args.exclude_glob))
        write_verify(out, lines, summary)
        log("")
        for line in lines:
            log(line)
        return 1 if summary["problems"] else 0

    name = args.name or instance.name
    report: list[str] = []

    def say(s: str = "") -> None:
        log(s)
        report.append(s)

    # ---- output folder safety -------------------------------------------------
    if out.exists() and any(out.iterdir()) and not args.force and not args.dry_run:
        log(f"refusing to write into non-empty folder: {out}\n  pass --force to wipe it")
        return 2
    if args.force and out.exists() and out.parent != out:
        shutil.rmtree(out)

    # ---- metadata -------------------------------------------------------------
    mc = args.mc
    loader_version = args.loader_version
    old_deps = {}
    if args.old_index:
        _, _, old_deps = load_old_index(Path(args.old_index).resolve())
    mc = mc or old_deps.get("minecraft") or "1.21.1"
    if not loader_version:
        loader_version = old_deps.get(args.loader) or detect_loader(instance, mc)[1]
    say(f"pack        : {name} {args.version}")
    say(f"instance    : {instance}")
    say(f"output      : {out}")
    say(f"versions    : minecraft={mc} {args.loader}={loader_version}")
    say()

    # ---- plan the content copy ------------------------------------------------
    include = list(DEFAULT_INCLUDE)
    if args.no_options:
        include = [i for i in include if i != "options.txt"]
    include += args.include
    present = [e for e in include if (instance / e).exists()]
    missing = [e for e in include if not (instance / e).exists()]
    skipped = sorted(p.name for p in instance.iterdir() if p.name not in present and p.name != "mods")

    say("content to copy:")
    excluded_dirs = set() if args.keep_caches else set(DEFAULT_EXCLUDE_DIRS)
    excluded_globs = [] if args.keep_caches else list(args.exclude_glob)
    total_dropped = 0
    copy_plan: list[tuple[Path, str]] = []
    for e in present:
        src = instance / e
        if src.is_dir():
            kept = 0
            for p, rel in walk_content(src, excluded_dirs, excluded_globs, prefix=e):
                copy_plan.append((p, f"{e}/{rel}"))
                kept += p.stat().st_size
            full = dir_size(src)
            total_dropped += full - kept
            extra = f"   (-{(full - kept) / 1048576:.2f} MB cache/junk)" if full - kept else ""
            say(f"  + {e + '/':<28} {kept / 1048576:8.2f} MB{extra}")
        else:
            copy_plan.append((src, e))
            say(f"  + {e:<28} {src.stat().st_size / 1048576:8.2f} MB")
    if total_dropped:
        say(f"  excluded from packed content: {total_dropped / 1048576:.2f} MB "
            f"(dirs: {', '.join(sorted(excluded_dirs)) or '-'})")
    if missing:
        say(f"  (not present, skipped: {', '.join(missing)})")
    say()
    say("instance root entries NOT packed (add with --include <name> if wanted):")
    say("  " + ", ".join(skipped))
    say()

    # ---- mods -----------------------------------------------------------------
    moddir = instance / "mods"
    jars = sorted([p for p in moddir.glob("*.jar") if p.is_file()])
    disabled = sorted([p for p in moddir.glob("*.jar.disabled") if p.is_file()])
    say(f"mods: {len(jars)} active jar(s), {len(disabled)} .jar.disabled")
    say()

    info = {}
    for p in jars:
        info[p.name] = {"path": p, "sha1": sha1_of(p), "sha512": sha512_of(p), "size": p.stat().st_size}

    # ---- resolver 1: Modrinth -------------------------------------------------
    say("resolving origins...")
    mr_hits = modrinth_lookup([v["sha1"] for v in info.values()])
    say(f"  Modrinth sha1 lookup : {len(mr_hits)}/{len(jars)} matched")
    project_ids = [v["project_id"] for v in mr_hits.values()]
    projects = modrinth_projects(project_ids) if project_ids else {}

    resolved: dict[str, dict] = {}
    forced_both: set[str] = set()
    for fname, rec in info.items():
        v = mr_hits.get(rec["sha1"])
        if not v:
            continue
        f = next((x for x in v.get("files", []) if (x.get("hashes") or {}).get("sha1") == rec["sha1"]), None)
        if not f:
            continue
        proj = projects.get(v["project_id"], {})
        cs, ss = proj.get("client_side"), proj.get("server_side")
        if v["project_id"] in FORCE_BOTH:
            side = "both"
            forced_both.add(v["project_id"])
        else:
            side = "client" if ss == "unsupported" else "server" if cs == "unsupported" else "both"
        resolved[fname] = {
            "kind": "modrinth",
            "name": proj.get("title") or v.get("name") or fname,
            "filename": f.get("filename") or fname,
            "url": f.get("url"),
            "hash": (f.get("hashes") or {}).get("sha512") or rec["sha512"],
            "hash_format": "sha512",
            "side": side,
            "update": ("modrinth", {"mod-id": v["project_id"], "version": v["id"]}),
        }

    if forced_both:
        names = ", ".join(sorted(projects.get(p, {}).get("title") or p for p in forced_both))
        say(f"  side forced to both    : {len(forced_both)} ({names}) -- Modrinth says")
        say( "                           client_side=unsupported, but the client needs them")

    # ---- resolver 2: previous pack --------------------------------------------
    by_sha1 = by_name = {}
    if args.old_index:
        by_sha1, by_name, _ = load_old_index(Path(args.old_index).resolve())
        n = 0
        for fname, rec in info.items():
            if fname in resolved:
                continue
            old = by_sha1.get(rec["sha1"])
            if old and old["cf_url"]:
                fid = file_id_from_forgecdn(old["cf_url"])
                resolved[fname] = {
                    "kind": "old-index",
                    "name": os.path.splitext(fname)[0],
                    "filename": fname,
                    "url": old["cf_url"],
                    "hash": rec["sha512"],
                    "hash_format": "sha512",
                    "side": "both",
                    "update": None,
                    "cf_file_id": fid,
                }
                n += 1
        say(f"  previous pack hash    : {n} extra matched ({Path(args.old_index).name})")

    # ---- resolver 3: CurseForge (keyless, exact filename only) -----------------
    cache = {}
    if args.cache and Path(args.cache).exists():
        try:
            cache = json.loads(Path(args.cache).read_text(encoding="utf-8"))
            say(f"  CurseForge cache      : {len(cache)} entries loaded")
        except Exception:  # noqa: BLE001
            cache = {}
    todo = [f for f in info if f not in resolved]
    if todo and not args.no_cf_search:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
            futures = {ex.submit(cf_resolve, f, cache): f for f in todo}
            for fut in concurrent.futures.as_completed(futures):
                fname = futures[fut]
                try:
                    hit = fut.result()
                except Exception:  # noqa: BLE001
                    hit = None
                if not hit or not hit.get("url"):
                    continue
                # A filename is not an identity. CurseForge publishes the file's
                # sha1, so require it to equal the bytes we hashed locally; a
                # locally patched jar that reuses an upstream name must be bundled,
                # not pointed at a download that would fail its hash check.
                if hit.get("sha1") != info[fname]["sha1"]:
                    say(f"      ! name matched but bytes differ, bundling instead: {fname}")
                    continue
                resolved[fname] = {
                    "kind": "curseforge",
                    "name": hit.get("name") or fname,
                    "filename": fname,
                    "url": hit["url"],
                    "hash": info[fname]["sha512"],
                    "hash_format": "sha512",
                    "side": "both",
                    "update": ("curseforge", {"file-id": hit["fileId"], "project-id": hit["projectId"]}),
                }
        say(f"  CurseForge name match : {sum(1 for v in resolved.values() if v['kind'] == 'curseforge')} matched")
        if args.cache:
            Path(args.cache).write_text(json.dumps(cache, indent=1), encoding="utf-8")

    # ---- resolver 3b: pinned CurseForge files ---------------------------------
    # See CURSEFORGE_PINS. These jars could not be resolved automatically, but we do know
    # exactly which CurseForge file each one is, so point packwiz at that file.
    pinned = 0
    for fname, (pid, fid) in CURSEFORGE_PINS.items():
        if fname in resolved or fname not in info:
            continue
        resolved[fname] = {
            "kind": "curseforge-pin",
            "name": os.path.splitext(fname)[0],
            "filename": fname,
            "url": forgecdn_url(fid, fname),
            "hash": info[fname]["sha512"],
            "hash_format": "sha512",
            "side": "both",
            "update": ("curseforge", {"file-id": fid, "project-id": pid}),
        }
        pinned += 1
    if pinned:
        say(f"  CurseForge pinned     : {pinned} (hand-checked project/file ids)")

    # ---- resolver 4: give the previous-pack matches a project id ---------------
    # The old .mrpack gave us a forgecdn URL, which carries the FILE id but not the
    # PROJECT id. Without a project id packwiz has nothing to query, so those mods
    # would be permanently un-updatable. Look the project up by exact filename and
    # accept it only when the file id agrees with the one encoded in the URL.
    stale = [f for f, r in resolved.items()
             if r.get("update") is None and r.get("cf_file_id") is not None]
    if stale and not args.no_cf_search:
        got = 0
        dropped = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
            futures = {ex.submit(cf_resolve, f, cache): f for f in stale}
            for fut in concurrent.futures.as_completed(futures):
                fname = futures[fut]
                try:
                    hit = fut.result()
                except Exception:  # noqa: BLE001
                    hit = None
                if not hit or hit.get("fileId") != resolved[fname].get("cf_file_id"):
                    continue
                if hit.get("sha1") and hit["sha1"] != info[fname]["sha1"]:
                    # the URL the previous pack shipped never served these bytes,
                    # so its hash would fail too - bundle the jar we actually run
                    say(f"      ! previous-pack URL serves different bytes, bundling: {fname}")
                    del resolved[fname]
                    dropped += 1
                    continue
                resolved[fname]["update"] = ("curseforge", {"file-id": hit["fileId"],
                                                            "project-id": hit["projectId"]})
                got += 1
        say(f"  CurseForge project id : {got}/{len(stale)} recovered (rest stay url-only)")
        if dropped:
            say(f"  CurseForge mismatched : {dropped} demoted to bundled")
        if args.cache:
            Path(args.cache).write_text(json.dumps(cache, indent=1), encoding="utf-8")

    unresolved = [f for f in info if f not in resolved]
    say(f"  -> resolved {len(resolved)}/{len(jars)}, unresolved {len(unresolved)}")
    say()

    if args.dry_run:
        say("unresolved (would be bundled):")
        for f in unresolved:
            say(f"  - {f}")
        log("\n--dry-run: nothing written.")
        return 0

    # ---- write the pack -------------------------------------------------------
    out.mkdir(parents=True, exist_ok=True)
    for src, rel in copy_plan:
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    (out / "mods").mkdir(parents=True, exist_ok=True)
    for fname, r in sorted(resolved.items()):
        pw = out / "mods" / (os.path.splitext(fname)[0] + ".pw.toml")
        update = r.get("update")
        write_pw_toml(pw, name=r["name"], filename=r["filename"], side=r["side"],
                      dl_url=r["url"], dl_hash=r["hash"], dl_format=r["hash_format"],
                      update=update)

    bundled = []
    if not args.no_bundle:
        for fname in unresolved:
            shutil.copy2(info[fname]["path"], out / "mods" / fname)
            bundled.append(fname)

    if args.include_disabled:
        for p in disabled:
            shutil.copy2(p, out / "mods" / p.name)

    n_indexed = build_index(out)
    write_pack_toml(out, name, args.author, args.version, mc, args.loader, loader_version)

    # ---- report ---------------------------------------------------------------
    say("=" * 66)
    say(f"resolved via Modrinth      : {sum(1 for v in resolved.values() if v['kind'] == 'modrinth')}")
    say(f"resolved via previous pack : {sum(1 for v in resolved.values() if v['kind'] == 'old-index')}")
    say(f"resolved via CurseForge    : {sum(1 for v in resolved.values() if v['kind'] == 'curseforge')}")
    say(f"bundled (unresolved)       : {len(bundled)}")
    if bundled:
        say("  bundled files: " + ", ".join(bundled))
    say(f"index.toml entries         : {n_indexed}")
    meta_files = len(list((out / 'mods').glob('*.pw.toml')))
    bundle_mb = sum((out / 'mods' / b).stat().st_size for b in bundled) / 1048576
    say(f"re-hosted mod bytes        : {bundle_mb:.1f} MB  ({len(bundled)} jar(s))")
    say("=" * 66)
    say()
    say("next steps:")
    say("  1. review the bundled list above -- those are jars you are re-distributing")
    say("  2. push this folder to a repo and serve it (GitHub Pages / raw.githubusercontent)")
    say("  3. players install with:")
    say("     java -jar packwiz-installer-bootstrap.jar <URL to pack.toml>")

    if args.report:
        Path(args.report).write_text("\n".join(report) + "\n", encoding="utf-8")
        log(f"\nreport written: {args.report}")

    # ---- optional audit -------------------------------------------------------
    if args.audit:
        acache = {}
        if args.cache and Path(args.cache).exists():
            try:
                acache = json.loads(Path(args.cache).read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                acache = {}
        lines, summary = audit_pack(out, instance, mc, args.loader,
                                    cross=args.audit_cross, jobs=args.jobs, cache=acache)
        if args.cache:
            Path(args.cache).write_text(json.dumps(acache, indent=1), encoding="utf-8")
        write_audit(out, lines, summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
