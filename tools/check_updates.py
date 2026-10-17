#!/usr/bin/env python3
r"""
check_updates.py  --  cross-check every mod in the pack against Modrinth AND CurseForge.

READ ONLY. Never writes into pack\ and never touches the Minecraft instance, so it is safe
to run at any time, even with the game open.

How a mod is identified
    pack\mods\*.pw.toml records the sha512 of the bytes you actually run. That hash is the
    whole identity: it survives renames and needs no access to the instance.

Modrinth side (exact)
    POST /version_files         sha512 -> the version object you currently have
    POST /version_files/update  sha512 -> the newest version for <mc> + <loader>
    A file the project does not publish for this target is simply absent from the second
    answer, which is how "no 1.21.1 NeoForge build" is told apart from "up to date".

CurseForge side (project id when known, otherwise located, never guessed)
    api.curseforge.com needs an x-api-key and the community mirror api.curse.tools is
    currently Cloudflare-blocked, so this uses api.cfwidget.com: no key needed, and it
    accepts both a numeric project id and a slug. Its per-file `versions` list mixes game
    versions and loaders -- ["1.21","Client","1.21.1","NeoForge","Server"] -- which is what
    the filter below relies on.

    Only the mods that packwiz already points at CurseForge carry a project id. For the rest
    the project is located by trying, in order, the Modrinth slug, the Modrinth title and the
    filename stem. A candidate found that way is ACCEPTED ONLY IF one of its file names is
    byte-identical to the file we ship, so a name collision cannot produce a wrong answer.

Output
    build\update-report.txt and build\update-report.json, plus a table on stdout.
    Exit code is always 0 -- this is a report, not a gate.

Usage
    py -3 tools/check_updates.py
    py -3 tools/check_updates.py --curseforge known    (only the mods with a known project id)
    py -3 tools/check_updates.py --curseforge none     (Modrinth only, no cfwidget traffic)
    py -3 tools/check_updates.py --refresh             (ignore the cfwidget disk cache)

Stdlib only. Python 3.9+.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import packwiz_from_instance as P  # noqa: E402  (same folder; reused, not duplicated)

ROOT = HERE.parent
PACK = ROOT / "pack"
BUILD = ROOT / "build"
CFWIDGET = "https://api.cfwidget.com/minecraft/mc-mods"
CFW_CACHE = BUILD / "cfwidget-cache"

# CurseForge spells loaders differently from packwiz/Modrinth.
LOADER_TAG = {"neoforge": "neoforge", "forge": "forge", "fabric": "fabric", "quilt": "quilt"}
LOADER_KEYS = ("neoforge", "forge", "fabric", "quilt")
LOADER_DISPLAY = {"neoforge": "NeoForge", "forge": "Forge", "fabric": "Fabric", "quilt": "Quilt"}

# Windows consoles are usually cp936 here, which cannot hold every character a mod name
# carries (one of them has an emoji in it). Replace instead of raising.
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001 - not all streams support it
    pass


# ----------------------------------------------------------------------------- small utils

def say(msg: str = "") -> None:
    print(msg, flush=True)


def slugify(text: str) -> str:
    """'Sophisticated Backpacks' -> 'sophisticated-backpacks' (CurseForge's slug shape)."""
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower())
    return re.sub(r"-{2,}", "-", s).strip("-")


def family_of(mc: str) -> str | None:
    """'1.21.1' -> '1.21'. CurseForge often tags a 1.21.1 build with just '1.21'."""
    parts = mc.split(".")
    return ".".join(parts[:2]) if len(parts) >= 3 else None


# ----------------------------------------------------------------------------- pack reading

def read_target() -> tuple[str, str, str]:
    """(minecraft, loader, pack version) out of pack\\pack.toml."""
    toml = PACK / "pack.toml"
    if not toml.exists():
        raise SystemExit(f"pack\\pack.toml not found under {ROOT} -- run Update-Pack.ps1 first")
    txt = toml.read_text(encoding="utf-8")

    def val(key: str) -> str | None:
        m = re.search(rf'(?m)^{re.escape(key)}\s*=\s*"([^"]*)"', txt)
        return m.group(1) if m else None

    return val("minecraft") or "?", next((s for s in LOADER_KEYS if val(s)), "neoforge"), val("version") or "?"


def load_mods() -> tuple[list[dict], list[str]]:
    """Every pack\\mods\\*.pw.toml as a plain record, plus the bundled jar names."""
    mods: list[dict] = []
    for pw in sorted((PACK / "mods").glob("*.pw.toml")):
        rec = P.read_pw_toml(pw)
        upd = rec.get("update") or {}
        fmt = (rec.get("dl_hash-format") or "").lower()
        mods.append({
            "pw": pw.name,
            "name": rec.get("name") or pw.stem,
            "filename": rec.get("filename") or (pw.stem + ".jar"),
            "hash": rec.get("dl_hash") if fmt in ("sha1", "sha512") else None,
            "algo": fmt if fmt in ("sha1", "sha512") else None,
            "side": rec.get("side") or "both",
            "site": upd.get("kind"),
            "mr_id": upd.get("mod-id") if upd.get("kind") == "modrinth" else None,
            "cf_id": upd.get("project-id") if upd.get("kind") == "curseforge" else None,
            "cf_file_id": upd.get("file-id") if upd.get("kind") == "curseforge" else None,
        })
    bundled = sorted(p.name for p in (PACK / "mods").glob("*.jar"))
    return mods, bundled


# ----------------------------------------------------------------------------- Modrinth

def modrinth_batches(hashes_by_algo: dict, mc: str, loader: str) -> tuple[dict, dict]:
    """(installed, latest) keyed by hash. One request per 100 hashes per algorithm."""
    installed: dict = {}
    latest: dict = {}
    for algo, hashes in hashes_by_algo.items():
        if not hashes:
            continue
        for i in range(0, len(hashes), 100):
            chunk = hashes[i:i + 100]
            r = P.http_json(f"{P.MODRINTH}/version_files", {"hashes": chunk, "algorithm": algo})
            if isinstance(r, dict) and "__error__" not in r:
                installed.update(r)
            elif isinstance(r, dict):
                say(f"  ! Modrinth /version_files failed: {r['__error__']}")
    for algo, hashes in hashes_by_algo.items():
        if not hashes:
            continue
        for i in range(0, len(hashes), 100):
            chunk = hashes[i:i + 100]
            r = P.http_json(f"{P.MODRINTH}/version_files/update",
                            {"hashes": chunk, "algorithm": algo,
                             "loaders": [loader], "game_versions": [mc]})
            if isinstance(r, dict) and "__error__" not in r:
                latest.update(r)
            elif isinstance(r, dict):
                say(f"  ! Modrinth /version_files/update failed: {r['__error__']}")
    return installed, latest


# ----------------------------------------------------------------------------- CurseForge

def cfw_get(key: str, refresh: bool = False) -> dict | None:
    """One whole cfwidget project, disk-cached. 404s are NOT cached on purpose."""
    key = str(key).strip().lower()
    safe = re.sub(r"[^a-z0-9_.-]", "_", key)
    path = CFW_CACHE / f"{safe}.json"
    if path.exists() and not refresh:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a corrupt cache file is just a cache miss
            pass
    url = f"{CFWIDGET}/{urllib.parse.quote(key, safe='')}"
    try:
        req = urllib.request.Request(url, headers=P.UA)
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.loads(r.read().decode())
    except Exception:  # noqa: BLE001 - 404 and network errors both mean "not located"
        return None
    if not isinstance(data, dict) or not data.get("files"):
        return None
    CFW_CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def cfw_pick(proj: dict, mc: str, loader: str) -> dict | None:
    """Newest file of `proj` published for this MC version AND this loader."""
    want = LOADER_TAG.get(loader, loader)
    fam = family_of(mc)
    best = None
    for f in proj.get("files") or []:
        vs = [str(v).lower() for v in (f.get("versions") or [])]
        if want not in vs:
            continue
        if mc.lower() not in vs and not (fam and fam in vs):
            continue
        if best is None or (f.get("uploaded_at") or "") > (best.get("uploaded_at") or ""):
            best = f
    return best


def cfw_short_label(f: dict) -> str:
    """'19.64.0.455 for NeoForge 1.21.1' -> '19.64.0.455'."""
    for text in (f.get("display"), f.get("name")):
        if text:
            return str(text).split(" for ")[0]
    return "?"


def cf_candidates(mod: dict) -> list[tuple[str, str]]:
    """Ordered (key, how) guesses at this mod's CurseForge project."""
    out: list[tuple[str, str]] = []
    if mod["cf_id"]:
        out.append((str(mod["cf_id"]), "project-id"))
    if mod.get("mr_slug"):
        out.append((slugify(mod["mr_slug"]), "modrinth-slug"))
    if mod.get("mr_title"):
        out.append((slugify(mod["mr_title"]), "modrinth-title"))
    stem = P.search_term(mod["filename"])
    if stem:
        out.append((slugify(stem), "filename"))
    out.append((slugify(re.sub(r"\.jar$", "", mod["filename"], flags=re.I)), "filename-full"))
    seen: set[str] = set()
    uniq: list[tuple[str, str]] = []
    for key, how in out:
        if key and key not in seen:
            seen.add(key)
            uniq.append((key, how))
    return uniq


def cf_resolve_all(mods: list[dict], mc: str, loader: str, *, scope: str,
                   jobs: int, refresh: bool) -> dict:
    """pw -> (project, how, newest file) for every mod that could be located."""
    if scope == "none":
        return {}
    pick = [m for m in mods if scope == "all" or m["cf_id"]]
    cands = {m["pw"]: cf_candidates(m) for m in pick}
    keys = sorted({k for lst in cands.values() for k, _ in lst})
    say(f"  CurseForge: probing {len(keys)} candidate project key(s) for {len(pick)} mod(s) "
        f"({jobs} thread(s), cache {'ignored' if refresh else 'on'})")

    fetched: dict = {}

    def one(key: str):
        return key, cfw_get(key, refresh)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        for i, (key, proj) in enumerate(ex.map(one, keys), 1):
            fetched[key] = proj
            if i % 25 == 0:
                say(f"    ... {i}/{len(keys)}")

    out: dict = {}
    for m in pick:
        for key, how in cands[m["pw"]]:
            proj = fetched.get(key)
            if not proj:
                continue
            files = proj.get("files") or []
            have_name = any(str(f.get("name") or "").lower() == m["filename"].lower() for f in files)
            # A project id straight out of the pw.toml is authoritative. Every other route
            # has to prove itself with an exact file name, or a name collision would be
            # reported as if it were this mod.
            if how == "project-id" or have_name:
                out[m["pw"]] = (proj, how + (" +filename" if have_name and how != "project-id" else ""),
                                cfw_pick(proj, mc, loader))
                break
    return out


# ----------------------------------------------------------------------------- report

def mr_label(r: dict) -> str:
    """Newest Modrinth version, flagged when the version string did not move at all.

    A re-upload keeps version_number but gets a new version id, so a plain version column
    would print "1.2.0 -> 1.2.0" and look like a bug instead of an update.
    """
    v = str(r["modrinth"]["version"] or "?")
    return v + (" (重建)" if v == str(r["current"] or "") else "")


def main() -> int:
    ap = argparse.ArgumentParser(description="Cross-check pack mods against Modrinth and CurseForge.")
    ap.add_argument("--curseforge", choices=("all", "known", "none"), default="all",
                    help="all = locate every mod (default), known = only mods with a project id, "
                         "none = Modrinth only")
    ap.add_argument("--jobs", type=int, default=6, help="parallel cfwidget requests (default 6)")
    ap.add_argument("--refresh", action="store_true", help="ignore the cfwidget disk cache")
    ap.add_argument("--json", action="store_true", help="also dump the result as JSON on stdout")
    args = ap.parse_args()

    mc, loader, pack_version = read_target()
    mods, bundled = load_mods()
    if not mods:
        raise SystemExit("pack\\mods has no *.pw.toml -- run Update-Pack.ps1 first")

    say(f"target: Minecraft {mc} / {LOADER_DISPLAY.get(loader, loader)}  --  pack {pack_version}")
    say(f"mods:   {len(mods)} metafile(s)" + (f"  (+{len(bundled)} bundled jar(s), not checked)"
                                              if bundled else ""))

    # ---- Modrinth -----------------------------------------------------------
    by_algo: dict = {"sha512": [], "sha1": []}
    for m in mods:
        if m["hash"]:
            by_algo[m["algo"]].append(m["hash"])
    installed, latest = modrinth_batches(by_algo, mc, loader)

    # Project slugs/titles make the CurseForge probing far more likely to land.
    projects = P.modrinth_projects([v["project_id"] for v in installed.values() if v.get("project_id")])
    for m in mods:
        cur = installed.get(m["hash"]) if m["hash"] else None
        pid = (cur or {}).get("project_id")
        proj = projects.get(pid) if pid else None
        m["mr_slug"] = (proj or {}).get("slug")
        m["mr_title"] = (proj or {}).get("title")

    # ---- CurseForge ---------------------------------------------------------
    cf = cf_resolve_all(mods, mc, loader, scope=args.curseforge, jobs=args.jobs, refresh=args.refresh)

    # ---- merge --------------------------------------------------------------
    rows: list[dict] = []
    for m in mods:
        cur = installed.get(m["hash"]) if m["hash"] else None
        new = latest.get(m["hash"]) if m["hash"] else None

        if cur is None:
            mr_state, mr_latest = "missing", None
        elif new is None:
            mr_state, mr_latest = "no-build", None
        elif new.get("id") != cur.get("id"):
            mr_state, mr_latest = "update", new
        else:
            mr_state, mr_latest = "current", cur

        cf_proj, cf_how, cf_best = cf.get(m["pw"]) or (None, None, None)
        if cf_proj is None:
            cf_state = "unlocated" if args.curseforge != "none" else "skipped"
        elif cf_best is None:
            cf_state = "no-build"
        else:
            if m["cf_file_id"]:
                changed = str(cf_best.get("id")) != str(m["cf_file_id"])
            else:
                changed = str(cf_best.get("name") or "").lower() != m["filename"].lower()
            cf_state = "update" if changed else "current"

        rows.append({
            "name": m["name"],
            "filename": m["filename"],
            "side": m["side"],
            "source": m["site"],
            "current": (cur or {}).get("version_number") or m["filename"],
            "modrinth": {"state": mr_state,
                         "version": (mr_latest or {}).get("version_number") if mr_state == "update" else None,
                         "file": ((mr_latest or {}).get("files") or [{}])[0].get("filename") if mr_state == "update" else None,
                         "date": (mr_latest or {}).get("date_published") if mr_state == "update" else None,
                         "url": f"https://modrinth.com/mod/{(projects.get((cur or {}).get('project_id')) or {}).get('slug') or ''}/version/{(mr_latest or {}).get('id') or ''}" if mr_state == "update" else None},
            "curseforge": {"state": cf_state,
                           "version": cfw_short_label(cf_best) if cf_state == "update" and cf_best else None,
                           "file": (cf_best or {}).get("name") if cf_state == "update" else None,
                           "date": (cf_best or {}).get("uploaded_at") if cf_state == "update" else None,
                           "url": (cf_best or {}).get("url") if cf_state == "update" else None,
                           "how": cf_how},
            "needs_update": mr_state == "update" or cf_state == "update",
        })

    rows.sort(key=lambda r: (not r["needs_update"], r["name"].lower()))
    need = [r for r in rows if r["needs_update"]]
    unable = [r for r in rows if not r["needs_update"]
              and r["modrinth"]["state"] in ("missing", "no-build")
              and r["curseforge"]["state"] in ("unlocated", "no-build", "skipped")]
    unable_files = {r["filename"] for r in unable}
    ok = [r for r in rows if not r["needs_update"] and r["filename"] not in unable_files]

    # ---- stdout -------------------------------------------------------------
    def cell(text, width):
        text = str(text)
        return text if len(text) <= width else text[: width - 1] + "~"

    def table(items: list[dict]) -> None:
        say(f"  {'模组':<30}{'现在':<24}{'Modrinth':<22}CurseForge")
        for r in items:
            mr = "-> " + cell(mr_label(r), 17) if r["modrinth"]["state"] == "update" else ""
            cf = "-> " + cell(r["curseforge"]["version"], 17) if r["curseforge"]["state"] == "update" else ""
            if r["modrinth"]["state"] == "no-build":
                mr = "(无此目标版本)"
            elif r["modrinth"]["state"] == "missing":
                mr = "(未收录)"
            if r["curseforge"]["state"] == "unlocated":
                cf = "(未定位)"
            elif r["curseforge"]["state"] == "no-build":
                cf = "(无此目标版本)"
            elif r["curseforge"]["state"] == "skipped":
                cf = "(已跳过)"
            say(f"  {cell(r['name'], 29):<30}{cell(r['current'], 23):<24}{cell(mr, 21):<22}{cf}")

    say("")
    say(f"== 需要更新 ({len(need)}) ==")
    if need:
        table(need)
    else:
        say("  (没有)")

    if unable:
        say("")
        say(f"== 无法确认 ({len(unable)}) ==")
        table(unable)

    if ok:
        say("")
        say(f"== 已是最新 ({len(ok)}) ==")
        names = [r["name"] for r in ok]
        line = "  "
        for n in names:
            if len(line) + len(n) > 110:
                say(line)
                line = "  "
            line += n + "  "
        if line.strip():
            say(line)

    say("")
    say(f"  已是最新 {len(ok)}   需要更新 {len(need)}   无法确认 {len(unable)}   "
        f"（其中 CurseForge 未定位 {sum(1 for r in rows if r['curseforge']['state'] == 'unlocated')}）")

    # ---- files -------------------------------------------------------------
    BUILD.mkdir(parents=True, exist_ok=True)
    txt_lines = [f"target: Minecraft {mc} / {LOADER_DISPLAY.get(loader, loader)}  --  pack {pack_version}",
                 f"mods: {len(mods)} metafile(s), {len(bundled)} bundled jar(s) not checked",
                 "",
                 f"== 需要更新 ({len(need)}) =="]
    txt_lines += [f"  {r['name']}  |  {r['current']}  |  Modrinth {mr_label(r) if r['modrinth']['state'] == 'update' else r['modrinth']['state']}  |  "
                  f"CurseForge {r['curseforge']['version'] or r['curseforge']['state']}" for r in need] or ["  (没有)"]
    txt_lines += ["", f"== 无法确认 ({len(unable)}) =="]
    txt_lines += [f"  {r['name']}  |  {r['current']}  |  Modrinth {r['modrinth']['state']}  |  "
                  f"CurseForge {r['curseforge']['state']}" for r in unable] or ["  (没有)"]
    txt_lines += ["", f"== 已是最新 ({len(ok)}) =="]
    txt_lines += [f"  {r['name']}" for r in ok] or ["  (没有)"]
    (BUILD / "update-report.txt").write_text("\n".join(txt_lines) + "\n", encoding="utf-8")

    payload = {"minecraft": mc, "loader": loader, "pack_version": pack_version,
               "counts": {"mods": len(mods), "bundled": len(bundled), "need_update": len(need),
                          "unable": len(unable), "up_to_date": len(ok)},
               "mods": rows}
    (BUILD / "update-report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
    say(f"  report: {BUILD / 'update-report.txt'}")
    if args.json:
        say("")
        say(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
