#!/usr/bin/env python3
"""
zdf_dl.py — ZDF Mediathek: Suche + Download (max. Qualität, MKV)
================================================================

Suche: persistierte GraphQL-Query auf api.zdf.de (getSearchResults).
Die Header (zdf-app-id + api-auth-Token) werden aus der HTML-Seite von
zdf.de gelesen und pro Sitzung frisch geholt.
Download: yt-dlp (ZDF-Extractor), progressive MP4 bis 1080p/6,6 Mbit +
HLS, MKV-Remux, de-Untertitel eingebettet.

Kommandos (optional, als CLI):
  search "begriff" | info <url> | dl <url> [--out DIR] [--max-height N]
                  [--subs auto|all|none] [--test]
"""
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
LIB_DIR = HOME / "arte-dl" / "lib"
DEFAULT_OUT = HOME / "Videos" / "ZDF"
GQL_URL = "https://api.zdf.de/graphql"
SEARCH_HASH = "5f8ae3abb4189c371a3297f8e669c755d08e35b491ebe37bf9e6e34a742518d9"
FORMAT_CHAIN = ("bv*[height<={h}]+ba[format_id*=TV_Ton]/"
                "bv*[height<={h}]+ba/b[height<={h}]/b")
FROZEN = bool(getattr(sys, "frozen", False))

_api_cache = {}


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


def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


# --------------------------------------------------------------- API-Header
def api_headers():
    """zdf-app-id + api-auth-Token frisch aus der ZDF-Startseite lesen."""
    if _api_cache:
        return _api_cache
    page = http_get("https://www.zdf.de/suche")
    appid = re.search(r'appId\\":\\"(zdf-web[^"\\]+)', page)
    token = re.search(r'apiAuthToken\\":\\"([^"\\]+)', page)
    headers = {
        "User-Agent": USER_AGENT,
        "Origin": "https://www.zdf.de",
        "Accept": "application/json",
        "apollo-require-preflight": "1",
        "zdf-app-id": appid.group(1) if appid else "zdf-web-e9cba975",
        "api-auth": "Bearer " + (token.group(1) if token else ""),
    }
    if not token:
        raise RuntimeError("ZDF-API-Token nicht in der Startseite gefunden")
    _api_cache.update(headers)
    return _api_cache


def gql(query_vars, operation="getSearchResults", sha=SEARCH_HASH):
    ext = {"clientLibrary": {"name": "@apollo/client", "version": "4.1.9"},
           "persistedQuery": {"version": 1, "sha256Hash": sha}}
    url = (f"{GQL_URL}?operationName={operation}"
           f"&variables={urllib.parse.quote(json.dumps(query_vars))}"
           f"&extensions={urllib.parse.quote(json.dumps(ext))}")
    req = urllib.request.Request(url, headers=api_headers())
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode())
    if data.get("errors"):
        raise RuntimeError(f"ZDF-API: {data['errors'][0].get('message')}")
    return data.get("data", {})


def _find_target_url(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "target_url" and isinstance(v, str):
                return v
            r = _find_target_url(v)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_target_url(v)
            if r:
                return r
    return None


# --------------------------------------------------------------- Suche
def search(query):
    """Suche über getSearchResults (alle Ergebnisse)."""
    results, seen = [], set()
    after = None
    for _page in range(3):                      # max. 3 Seiten (~72 Treffer)
        data = gql({"query": query,
                    "mode": "ALL_RESULTS_EXCLUDING_TOP_RESULTS",
                    "group": "gruppe-e", "first": 30, "after": after})
        sr = data.get("searchDocuments") or {}
        for entry in sr.get("results") or []:
            it = entry.get("item") or {}
            url = _find_target_url(it)
            if not url or "/video/" not in url or url in seen:
                continue
            seen.add(url)
            def _find_duration(o):
                if isinstance(o, dict):
                    for k, v in o.items():
                        if k == "duration" and isinstance(v, (int, float)):
                            return v
                        r = _find_duration(v)
                        if r is not None:
                            return r
                elif isinstance(o, list):
                    for v in o:
                        r = _find_duration(v)
                        if r is not None:
                            return r
                return None
            dsec = _find_duration(it)
            results.append({
                "url": url,
                "title": (it.get("title") or "").strip(),
                "content_type": it.get("contentType") or "",
                "duration_min": round(dsec / 60) if dsec else None,
                "availability": (it.get("availability") or "") if
                isinstance(it.get("availability"), str) else "",
            })
        info = sr.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            break
        after = info.get("endCursor")
    return results


# --------------------------------------------------------------- Info
def fetch_info_dict(url):
    ytdlp = get_ytdlp()
    with ytdlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
        return ydl.extract_info(url, download=False)


def info(url):
    d = fetch_info_dict(url)
    print(f"Titel:      {d.get('title')}")
    print(f"Dauer:      {round((d.get('duration') or 0)/60)} Min")
    if d.get("description"):
        print(f"Inhalt:     {d['description'][:200]}")
    print(f"Untertitel: {', '.join((d.get('subtitles') or {}).keys()) or '-'}")
    vids = [f for f in d.get("formats", []) if f.get("vcodec") != "none"]
    vids.sort(key=lambda f: f.get("tbr") or 0, reverse=True)
    for f in vids[:6]:
        print(f"  {f.get('format_id'):30} res={f.get('resolution')} "
              f"tbr={f.get('tbr')}")
    return d


# --------------------------------------------------------------- Download
def build_opts(url, out_dir, max_height=1080, subs="auto", test=False):
    h = max_height
    opts = {
        "format": FORMAT_CHAIN.format(h=h),
        "format_sort": ["res", "tbr"],
        "outtmpl": str(out_dir / "%(title)s.%(ext)s"),
        "mtime": False,
        "noprogress": False,
        "postprocessors": [
            {"key": "FFmpegVideoRemuxer", "preferedformat": "mkv"},
            {"key": "FFmpegMetadata"},
            {"key": "EmbedThumbnail"},
        ],
        "writethumbnail": True,
    }
    if subs == "all":
        opts.update(writesubtitles=True, subtitleslangs=["all"],
                    embedsubtitles=True)
    elif subs == "auto":
        opts.update(writesubtitles=True, subtitleslangs=["de", "deu.*"],
                    embedsubtitles=True)
    if opts.get("embedsubtitles"):
        opts["postprocessors"] = [
            opts["postprocessors"][0],
            {"key": "FFmpegSubtitlesConvertor", "format": "srt"},
            {"key": "FFmpegEmbedSubtitle", "already_have_subtitle": True},
            *opts["postprocessors"][1:],
        ]
    if test:
        try:
            ytdlp_mod = get_ytdlp()
            opts["download_ranges"] = ytdlp_mod.utils.download_range_func(
                [], [(0, 60)])
        except Exception:  # noqa: BLE001
            opts.pop("download_ranges", None)
        opts["force_keyframes_at_cuts"] = True
    if FROZEN:
        meipass = Path(getattr(sys, "_MEIPASS", ""))
        if meipass and ((meipass / "ffmpeg.exe").exists()
                        or (meipass / "ffmpeg").exists()):
            opts["ffmpeg_location"] = str(meipass)
    return opts


def build_args(url, out_dir, max_height=1080, subs="auto", test=False):
    h = max_height
    args = ["--format", FORMAT_CHAIN.format(h=h), "--format-sort", "res,tbr",
            "--remux-video", "mkv", "--embed-metadata", "--embed-thumbnail",
            "--write-thumbnail", "--no-mtime",
            "--output", str(out_dir / "%(title)s.%(ext)s"), url]
    if subs == "all":
        args += ["--write-subs", "--sub-langs", "all", "--embed-subs"]
    elif subs == "auto":
        args += ["--write-subs", "--sub-langs", "de,deu.*", "--embed-subs"]
    if test:
        args += ["--download-sections", "*0-60", "--force-keyframes-at-cuts"]
    if FROZEN:
        meipass = Path(getattr(sys, "_MEIPASS", ""))
        if meipass and ((meipass / "ffmpeg.exe").exists()
                        or (meipass / "ffmpeg").exists()):
            args += ["--ffmpeg-location", str(meipass)]
    return args


def download(url, out_dir=None, max_height=1080, subs="auto", test=False):
    out_dir = Path(out_dir or DEFAULT_OUT).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    opts = build_opts(url, out_dir, max_height, subs, test)
    args = build_args(url, out_dir, max_height, subs, test)
    print(f"[*] Lade {url} nach {out_dir}")
    try:
        ytdlp = get_ytdlp()
    except Exception as e:  # noqa: BLE001
        ytdlp = None
        if FROZEN:
            sys.exit(f"[!] yt-dlp konnte nicht geladen werden: {e}")
    if ytdlp is not None:
        with ytdlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        return
    exe = shutil.which("yt-dlp")
    argv = [exe] if exe else [sys.executable, "-m", "yt_dlp"]
    subprocess.run(argv + args, check=True,
                   env={**os.environ, "PYTHONPATH": str(LIB_DIR)})


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    cmd = sys.argv[1]
    if cmd == "search":
        for r in search(sys.argv[2]):
            print(f"  {r['title'][:50]:52} {r['content_type']:8} {r['url']}")
    elif cmd == "info":
        info(sys.argv[2])
    elif cmd in ("dl", "download"):
        argv = sys.argv[2:]
        url, kw = argv[0], {"out_dir": None, "max_height": 1080,
                            "subs": "auto", "test": False}
        i = 1
        while i < len(argv):
            a = argv[i]
            if a == "--out":
                kw["out_dir"] = argv[i + 1]; i += 2
            elif a == "--max-height":
                kw["max_height"] = int(argv[i + 1]); i += 2
            elif a == "--subs":
                kw["subs"] = argv[i + 1]; i += 2
            elif a == "--test":
                kw["test"] = True; i += 1
            else:
                sys.exit(f"[!] Unbekannte Option {a!r}")
        download(url, **kw)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
