#!/usr/bin/env python3
"""
ard_dl.py — ARD Mediathek: Suche + Download (max. Qualität, MKV)
================================================================

Suche: serverseitig gerenderte Suchseite ardmediathek.de/suche/<q>?q=<q>
Download: yt-dlp (ARDMediathek-Extractor), HLS bis 1080p, Merge nach MKV.

Kommandos (optional, als CLI):
  search "begriff" | info <url> | dl <url> [--out DIR] [--max-height N]
                  [--subs auto|all|none] [--test]
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
LIB_DIR = HOME / "arte-dl" / "lib"
DEFAULT_OUT = HOME / "Videos" / "ARD"
BASE = "https://www.ardmediathek.de"
FORMAT_CHAIN = ("bv*[height<={h}]+ba[format_id*=main]/"
                "bv*[height<={h}]+ba/b[height<={h}]/b")
FROZEN = bool(getattr(sys, "frozen", False))


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


# --------------------------------------------------------------- Suche
def search(query):
    """SSR der Suchseite: Karten mit /video/-Links, schema.org Movie."""
    q = urllib.parse.quote(query)
    page = http_get(f"{BASE}/suche/{q}?q={q}")
    results, seen = [], set()
    for m in re.finditer(r'href="(/video/[^"]+)"[^>]*', page):
        path = html.unescape(m.group(1))
        url = BASE + path
        if url in seen:
            continue
        seen.add(url)
        window = page[m.end():m.end() + 3000]
        toks = [p.strip() for p in re.sub(r"<[^>]+>", "|", window).split("|")
                if p.strip()]
        dur, t_idx = "", 0
        for i, t in enumerate(toks):
            if t in ("Min.", "Std.", "Sek.") and i > 0 \
                    and re.fullmatch(r"\d+", toks[i - 1]):
                dur, t_idx = f"{toks[i - 1]} {t}", i + 1
                break
        title = toks[t_idx] if t_idx < len(toks) else path
        meta = next((t for t in toks[t_idx + 1:] if "∙" in t), "")
        results.append({"url": url, "title": title, "duration": dur,
                        "meta": meta})
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
            print(f"  {r['title'][:50]:52} {r['duration']:9} {r['meta'][:40]}")
            print(f"    {r['url']}")
    elif cmd == "info":
        info(sys.argv[2])
    elif cmd in ("dl", "download"):
        kw = {"out_dir": None, "max_height": 1080, "subs": "auto",
              "test": False}
        argv, i = sys.argv[2:], 0
        url = argv[0]
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
