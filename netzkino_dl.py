#!/usr/bin/env python3
"""
netzkino-dl.py — Filme von Netzkino.de herunterladen (max. Qualität, MKV)
=========================================================================

Kommandos:
  search "begriff"          Suche im Netzkino-Katalog
  info   <slug|url>         Details + beste Stream-Qualität anzeigen
  dl     <slug|url>         Download: immer maximale Qualität, als MKV

Beispiele:
  netzkino-dl search "mondmann"
  netzkino-dl dl man-on-the-moon
  netzkino-dl dl https://www.netzkino.de/details/man-on-the-moon
  netzkino-dl dl man-on-the-moon --out ~/Videos/netzkino

Optionen für 'dl':
  --out DIR            Zielordner (Standard: ~/Videos/netzkino)
  --test               nur die ersten 60 Sekunden laden (Qualitätscheck)
  --no-sub             keine Prüfung höherer Bitraten (schneller)

Die maximal auflösende Variante wird automatisch ermittelt: für die
gefundene Stream-URL werden höhere Bitraten (8M … 3,5M) per HEAD-Request
getestet und die beste erreichbare genommen. Quelle ist der Netzkino-PMD
(pmd.netzkino-seite.netzkino.de); DRM-geschützte Titel werden abgelehnt.

Ergebnisdatei: MKV (remux, verlustfrei), Metadaten + Cover eingebettet.
yt-dlp wird wie bei arte-dl automatisch geladen; ffmpeg wird benötigt.
"""
import html
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64; rv:141.0) "
              "Gecko/20100101 Firefox/141.0")
HOME = Path.home()
LIB_DIR = HOME / "arte-dl" / "lib"          # teilt sich die yt-dlp-Bibliothek
DEFAULT_OUT = HOME / "Videos" / "netzkino"
GQL_URL = "https://data.netzkino.de/netzkino/graphql"
PMD_BASE = "https://pmd.netzkino-seite.netzkino.de/"
DETAILS_URL = "https://www.netzkino.de/details/"
FROZEN = bool(getattr(sys, "frozen", False))

MOVIE_FIELDS = """
slug title originalTitle productionYear runtimeInSeconds fskRating
longSynopsis productionCountry copyright hasDrm isActive validLicenseEnd
audioLanguages videoSource { pmdUrl } coverImage { masterUrl }
"""

PROBE_BITRATES = [10_000_000, 8_000_000, 6_000_000, 5_000_000, 4_000_000,
                  3_500_000, 3_000_000, 2_000_000, 1_000_000, 500_000]


# --------------------------------------------------------------- yt-dlp laden
def get_ytdlp():
    try:
        import yt_dlp
        return yt_dlp
    except ImportError:
        pass
    if (LIB_DIR / "yt_dlp").is_dir():
        if str(LIB_DIR) not in sys.path:
            sys.path.insert(0, str(LIB_DIR))
        import importlib
        importlib.invalidate_caches()
        try:
            import yt_dlp
            return yt_dlp
        except ImportError:
            if str(LIB_DIR) in sys.path:
                sys.path.remove(str(LIB_DIR))
    print("[*] yt-dlp wird von PyPI geladen ...", file=sys.stderr)
    LIB_DIR.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen("https://pypi.org/pypi/yt-dlp/json",
                                timeout=30) as r:
        urls = [u["url"] for u in json.load(r)["urls"]
                if u["filename"].endswith("py3-none-any.whl")]
    wheel = LIB_DIR / "yt_dlp.whl"
    with urllib.request.urlopen(urls[0], timeout=180) as r:
        wheel.write_bytes(r.read())
    with zipfile.ZipFile(wheel) as z:
        z.extractall(LIB_DIR)
    wheel.unlink()
    if str(LIB_DIR) not in sys.path:
        sys.path.insert(0, str(LIB_DIR))
    import importlib
    importlib.invalidate_caches()
    import yt_dlp
    return yt_dlp


def ytdlp_cli_argv():
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    return [sys.executable, "-m", "yt_dlp"]


# --------------------------------------------------------------- GraphQL
def gql(query, variables=None, timeout=30):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(GQL_URL, data=body, headers={
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode())
    if data.get("errors"):
        raise RuntimeError(f"Netzkino-API: {data['errors'][0].get('message')}")
    return data.get("data", {})


def parse_slug(token):
    m = re.search(r"netzkino\.de/details/([^/?#]+)", token)
    if m:
        return m.group(1)
    if re.fullmatch(r"[a-z0-9-]{2,80}", token, re.IGNORECASE):
        return token.lower()
    return None


def fetch_movie(slug):
    data = gql("query($s: String!) { cmsMovieBySlug(slug: $s) { %s } }" % MOVIE_FIELDS,
               {"s": slug})
    movie = data.get("cmsMovieBySlug")
    if not movie:
        sys.exit(f"[!] Kein Film für Slug {slug!r}")
    return movie


# --------------------------------------------------------------- Suche
def search(query):
    data = gql("""
    query($q: String!) { allCmsMovies(first: 20, filter: {or: [
        {title: {includesInsensitive: $q}},
        {originalTitle: {includesInsensitive: $q}}]}) {
      nodes { slug title originalTitle productionYear runtimeInSeconds
              hasDrm isActive }
    } }""", {"q": query})
    return data.get("allCmsMovies", {}).get("nodes", [])


# --------------------------------------------------------------- Qualität
def http_head(url, timeout=15):
    req = urllib.request.Request(url, method="HEAD",
                                 headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get("Content-Length")
    except urllib.error.HTTPError as e:
        return e.code, None


def verify_clear_mp4(url):
    """Klartext-Prüfung: erste 2 KB müssen eine MP4-ftyp-Signatur haben.
    True = unverschlüsselt, False = vermutlich verschlüsselt/kein MP4."""
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Range": "bytes=0-2047"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return b"ftyp" in r.read(2048)[:32]
    except Exception:  # noqa: BLE001
        return False


def best_stream_url(pmd_url, probe=True):
    """Ermittelt die höchste verfügbare Bitrate für die gegebene PMD-Datei."""
    if not pmd_url:
        sys.exit("[!] Dieser Film hat keinen Video-Stream (nur Kauf/Trailer?)")
    url = PMD_BASE + urllib.parse.quote(pmd_url)
    m = re.search(r"_(\d{6,8})\.mp4$", url)
    if not m:
        # Ohne Suffix: _3000000-Variante probieren, sonst URL unverändert
        base = url[:-4]
        for br in (3_000_000, 2_000_000, 1_000_000):
            cand = f"{base}_{br}.mp4"
            code, _ = http_head(cand)
            if code == 200:
                return cand, br
        return url, None
    found = int(m.group(1))
    if not probe:
        return url, found
    candidates = [br for br in PROBE_BITRATES if br > found] + [found]
    for br in candidates:
        cand = re.sub(r"_\d{6,8}\.mp4$", f"_{br}.mp4", url)
        code, length = http_head(cand)
        if code == 200:
            return cand, br
    return url, found


# --------------------------------------------------------------- Info
def info(token, probe=True):
    slug = parse_slug(token)
    if not slug:
        sys.exit(f"[!] Kein Slug in {token!r}")
    mv = fetch_movie(slug)
    minutes = round((mv.get("runtimeInSeconds") or 0) / 60)
    print(f"Titel:      {mv.get('title')}"
          + (f" (Original: {mv['originalTitle']})"
             if mv.get("originalTitle") and mv["originalTitle"] != mv.get("title")
             else ""))
    print(f"Jahr/Land:  {mv.get('productionYear')} / {mv.get('productionCountry')}")
    print(f"FSK:        {mv.get('fskRating') or '?'} | "
          f"Dauer: {minutes if minutes else '?'} Min")
    print(f"Slug:       {slug}")
    print(f"Lizenz bis: {(mv.get('validLicenseEnd') or '?')[:10]}  "
          f"DRM: {mv.get('hasDrm')}")
    if mv.get("longSynopsis"):
        print(f"Inhalt:     {mv['longSynopsis'][:200]}")
    url, bitrate = best_stream_url((mv.get("videoSource") or {}).get("pmdUrl"),
                                   probe=probe)
    size = http_head(url)[1]
    print(f"Stream:     {url}")
    print(f"Qualität:   {bitrate/1e6:.0f} Mbit/s" if bitrate else "Qualität: unbekannt")
    if size:
        print(f"Größe:      {int(size)/1e9:.2f} GB")
    return mv, url, bitrate


# --------------------------------------------------------------- Download
def sanitize(name):
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip() or "film"


def build_movie_info(mv, slug, url, bitrate):
    title = mv.get("title") or mv.get("originalTitle") or slug
    year = mv.get("productionYear")
    display = f"{title} ({year})" if year else title
    return {
        "_type": "video",
        "id": slug,
        "title": display,
        "description": mv.get("longSynopsis"),
        "age_limit": mv.get("fskRating"),
        "duration": mv.get("runtimeInSeconds"),
        "release_year": year,
        "webpage_url": DETAILS_URL + slug,
        "extractor": "netzkino",
        "thumbnail": (mv.get("coverImage") or {}).get("masterUrl"),
        "formats": [{
            "format_id": f"max-{bitrate or 'auto'}",
            "url": url,
            "ext": "mp4",
            "protocol": "https",
        }],
    }


def build_opts(out_dir, test):
    opts = {
        "format": "best",
        "remuxvideo": "mkv",                 # Ziel-MKV, verlustfreies Remux
        "outtmpl": str(out_dir / "%(title)s.%(ext)s"),
        "mtime": False,
        "noprogress": False,
        "postprocessors": [
            # Remux nach MKV (API braucht den Remuxer explizit)
            {"key": "FFmpegVideoRemuxer", "preferedformat": "mkv"},
            {"key": "FFmpegMetadata"},   # Metadaten einbetten
            {"key": "EmbedThumbnail"},   # Cover einbetten
        ],
        "writethumbnail": True,
    }
    if test:
        try:
            ytdlp_mod = get_ytdlp()
            opts["download_ranges"] = ytdlp_mod.utils.download_range_func(
                [], [(0, 60)])
        except Exception:
            opts.pop("download_ranges", None)
        opts["force_keyframes_at_cuts"] = True
    return opts


def download(token, out_dir=None, test=False, no_sub=False):
    slug = parse_slug(token)
    if not slug:
        sys.exit(f"[!] Kein Slug in {token!r}")
    mv = fetch_movie(slug)
    if mv.get("hasDrm"):
        # DRM-Flag = der Player nutzt DASH+Widevine. Das CMS referenziert
        # aber oft weiterhin einen unverschlüsselten MP4-Master – der wird
        # unten per ftyp-Check verifiziert.
        print("[*] DRM-Flag in der Lizenz – prüfe Klartext-Stream ...")
    url, bitrate = best_stream_url((mv.get("videoSource") or {}).get("pmdUrl"),
                                   probe=not no_sub)
    if not verify_clear_mp4(url):
        sys.exit("[!] Kein unverschlüsselter Stream verfügbar "
                 "(Datei verschlüsselt oder kein Klartext-MP4).")
    if mv.get("hasDrm"):
        print("[i] Klartext-MP4 bestätigt – Download ohne DRM-Umgehung möglich.")
    out_dir = Path(out_dir or DEFAULT_OUT).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    opts = build_opts(out_dir, test)
    args = ["--format", "best", "--remux-video", "mkv",
            "--embed-metadata", "--embed-thumbnail", "--write-thumbnail",
            "--no-mtime", "--output", str(out_dir / "%(title)s.%(ext)s"), url]
    if test:
        args += ["--download-sections", "*0-60", "--force-keyframes-at-cuts"]
    # Titelschema überschreiben (deutscher Titel + Jahr)
    title = mv.get("title") or mv.get("originalTitle") or slug
    year = mv.get("productionYear")
    display = f"{title} ({year})" if year else title
    display = sanitize(display)
    opts["outtmpl"] = str(out_dir / f"{display}.%(ext)s")
    args[args.index("--output") + 1] = str(out_dir / f"{display}.%(ext)s")
    print(f"[*] Lade {display} ({bitrate/1e6:.0f} Mbit/s)" if bitrate
          else f"[*] Lade {display}")
    print(f"    Quelle: {url}")
    try:
        ytdlp = get_ytdlp()
    except Exception as e:  # noqa: BLE001
        ytdlp = None
        if FROZEN:
            sys.exit(f"[!] yt-dlp konnte nicht geladen werden: {e}")
    if ytdlp is not None:
        info_dict = build_movie_info(mv, slug, url, bitrate)
        with ytdlp.YoutubeDL(opts) as ydl:
            ydl.process_ie_result(info_dict, download=True)
        return
    subprocess.run(ytdlp_cli_argv() + args, check=True,
                   env={**os.environ, "PYTHONPATH": str(LIB_DIR)})


# --------------------------------------------------------------- Main
def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    cmd = sys.argv[1]
    if cmd == "search":
        if len(sys.argv) < 3:
            sys.exit("[!] Suchbegriff fehlt")
        results = search(sys.argv[2])
        print(f"{len(results)} Treffer für {sys.argv[2]!r}:")
        for r in results:
            t = r.get("title") or r.get("originalTitle") or "?"
            if r.get("originalTitle") and r["originalTitle"] != r.get("title"):
                t += f" [orig: {r['originalTitle']}]"
            if r.get("productionYear"):
                t += f" ({r['productionYear']})"
            if r.get("runtimeInSeconds"):
                t += f" {round(r['runtimeInSeconds']/60)} Min"
            flags = []
            if r.get("hasDrm"):
                flags.append("DRM")
            if not r.get("isActive"):
                flags.append("inaktiv")
            if flags:
                t += "  [" + ",".join(flags) + "]"
            print(f"  {r['slug']:45} {t}")
    elif cmd == "info":
        probe = True
        args = sys.argv[2:]
        if "--no-sub" in args:
            args.remove("--no-sub"); probe = False
        info(args[0], probe=probe)
    elif cmd in ("dl", "download"):
        token = sys.argv[2]
        kwargs = {}
        i = 3
        while i < len(sys.argv):
            a = sys.argv[i]
            if a == "--out":
                kwargs["out_dir"] = sys.argv[i + 1]; i += 2
            elif a == "--test":
                kwargs["test"] = True; i += 1
            elif a == "--no-sub":
                kwargs["no_sub"] = True; i += 1
            else:
                sys.exit(f"[!] Unbekannte Option {a!r}")
        download(token, **kwargs)
    else:
        print(__doc__)
        sys.exit(f"[!] Unbekanntes Kommando {cmd!r}")


if __name__ == "__main__":
    main()
