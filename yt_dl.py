#!/usr/bin/env python3
"""
yt_dl.py — YouTube: Suche + Download (max. Qualität, MKV)
=========================================================

Suche über yt-dlps ytsearch (metadatenleicht, schnell), Download über den
YouTube-Extractor: bis 4K (2160p), Tonspur getrennt, Merge nach MKV,
de-Untertitel (inkl. automatisch erzeugte) eingebettet.

Kommandos (optional, als CLI):
  search "begriff" | info <url> | dl <url> [--out DIR] [--max-height N|0]
                  [--subs auto|all|none] [--test]

Hinweis: YouTube ändert gelegentlich etwas — dann braucht yt-dlp ein
Update (neue EXE bauen oder `pip install -U yt-dlp`).
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
DEFAULT_OUT = HOME / "Videos" / "YouTube"
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


# --------------------------------------------------------------- URL/ID
def parse_video_id(text):
    """Extrahiert die 11-stellige Video-ID aus jeder YouTube-URL-Form."""
    m = re.search(r"(?:youtu\.be/|watch\?(?:.*&)?v=|shorts/|embed/)"
                  r"([0-9A-Za-z_-]{11})", text)
    if m:
        return m.group(1)
    if re.fullmatch(r"[0-9A-Za-z_-]{11}", text.strip()):
        return text.strip()
    return None


def canonical_url(vid):
    return f"https://www.youtube.com/watch?v={vid}"


# --------------------------------------------------------------- Suche
def search(query, limit=20):
    """ytsearch (flat): Titel, Kanal, Dauer in Minuten."""
    ytdlp = get_ytdlp()
    opts = {"quiet": True, "no_warnings": True,
            "extract_flat": "in_playlist", "playlistend": limit}
    with ytdlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    out = []
    for e in info.get("entries") or []:
        if not e:
            continue
        dur = e.get("duration")
        out.append({
            "url": canonical_url(e.get("id")),
            "title": e.get("title") or "?",
            "channel": e.get("uploader") or e.get("channel") or "",
            "duration_min": round(dur / 60) if dur else None,
        })
    return out


# --------------------------------------------------------------- Info
def fetch_info_dict(url):
    ytdlp = get_ytdlp()
    with ytdlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
        return ydl.extract_info(url, download=False)


def info(url):
    d = fetch_info_dict(url)
    print(f"Titel:      {d.get('title')}")
    print(f"Kanal:      {d.get('uploader')}")
    print(f"Dauer:      {round((d.get('duration') or 0)/60)} Min")
    print(f"Untertitel: {', '.join((d.get('subtitles') or {}).keys()) or '-'} "
          f"(auto: {len(d.get('automatic_captions') or {})} Sprachen)")
    vids = [f for f in d.get("formats", []) if f.get("vcodec") not in
            (None, "none")]
    vids.sort(key=lambda f: ((f.get("height") or 0), (f.get("tbr") or 0)),
              reverse=True)
    for f in vids[:8]:
        print(f"  {f.get('format_id'):18} {f.get('ext'):5} "
              f"res={f.get('resolution')} tbr={f.get('tbr')}")
    return d


# --------------------------------------------------------------- Download
def build_opts(url, out_dir, max_height=None, subs="auto", test=False):
    """max_height=None → kein Cap (bis 4K); sonst Höhe begrenzen."""
    h = max_height
    if h:
        fmt = f"bv*[height<={h}]+ba/b[height<={h}]/b"
    else:
        fmt = "bv*+ba/b"
    opts = {
        "format": fmt,
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
        "no_playlist": True,
    }
    if subs in ("all", "auto"):
        opts.update(writesubtitles=True, writeautomaticsub=True,
                    embedsubtitles=True,
                    sleep_interval_subtitles=1)
        opts["subtitleslangs"] = (["de", "de-DE", "de-orig", "en",
                                   "en-orig", "en-US", "en-GB"]
                                  if subs == "all"
                                  else ["de", "de-DE", "de-orig"])
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


def build_args(url, out_dir, max_height=None, subs="auto", test=False):
    h = max_height
    fmt = (f"bv*[height<={h}]+ba/b[height<={h}]/b" if h else "bv*+ba/b")
    args = ["--format", fmt, "--format-sort", "res,tbr",
            "--remux-video", "mkv", "--embed-metadata", "--embed-thumbnail",
            "--write-thumbnail", "--no-mtime", "--no-playlist",
            "--output", str(out_dir / "%(title)s.%(ext)s"), url]
    if subs == "all":
        args += ["--write-subs", "--write-auto-subs", "--sub-langs",
                 "de,de-DE,de-orig,en,en-orig,en-US,en-GB", "--embed-subs"]
    elif subs == "auto":
        args += ["--write-subs", "--write-auto-subs", "--sub-langs",
                 "de,de-DE,de-orig", "--embed-subs"]
    if test:
        args += ["--download-sections", "*0-60", "--force-keyframes-at-cuts"]
    if FROZEN:
        meipass = Path(getattr(sys, "_MEIPASS", ""))
        if meipass and ((meipass / "ffmpeg.exe").exists()
                        or (meipass / "ffmpeg").exists()):
            args += ["--ffmpeg-location", str(meipass)]
    return args


def download(url, out_dir=None, max_height=None, subs="auto", test=False):
    out_dir = Path(out_dir or DEFAULT_OUT).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    attempts = (subs, "none") if subs != "none" else (subs,)
    last_exc = None
    for attempt_subs in attempts:
        opts = build_opts(url, out_dir, max_height, attempt_subs, test)
        args = build_args(url, out_dir, max_height, attempt_subs, test)
        print(f"[*] Lade {url} nach {out_dir}")
        try:
            ytdlp = get_ytdlp()
        except Exception as e:  # noqa: BLE001
            ytdlp = None
            if FROZEN:
                sys.exit(f"[!] yt-dlp konnte nicht geladen werden: {e}")
        if ytdlp is not None:
            try:
                with ytdlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])
                return
            except Exception as e:  # noqa: BLE001
                last_exc = e
                if "subtitle" not in str(e).lower() \
                        or attempt_subs == "none":
                    raise
                print("[i] Untertitel nicht verfügbar (Rate-Limit) — "
                      "lade ohne Untertitel neu ...", file=sys.stderr)
                continue
        try:
            exe = shutil.which("yt-dlp")
            argv = [exe] if exe else [sys.executable, "-m", "yt_dlp"]
            subprocess.run(argv + args, check=True,
                           env={**os.environ, "PYTHONPATH": str(LIB_DIR)})
            return
        except subprocess.CalledProcessError as e:
            last_exc = e
            out = (e.stdout or "") + (e.stderr or "")
            if "subtitle" not in out.lower() or attempt_subs == "none":
                raise
            print("[i] Untertitel nicht verfügbar (Rate-Limit) — "
                  "lade ohne Untertitel neu ...", file=sys.stderr)
    raise last_exc


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    cmd = sys.argv[1]
    if cmd == "search":
        for r in search(sys.argv[2]):
            print(f"  {(r['duration_min'] or '?'):>4} Min  {r['title'][:60]} "
                  f"({r['channel'][:30]})")
            print(f"    {r['url']}")
    elif cmd == "info":
        info(sys.argv[2])
    elif cmd in ("dl", "download"):
        argv = sys.argv[2:]
        url = argv[0]
        vid = parse_video_id(url)
        url = canonical_url(vid) if vid else url
        kw = {"out_dir": None, "max_height": None, "subs": "auto",
              "test": False}
        i = 1
        while i < len(argv):
            a = argv[i]
            if a == "--out":
                kw["out_dir"] = argv[i + 1]; i += 2
            elif a == "--max-height":
                v = int(argv[i + 1]); kw["max_height"] = v or None; i += 2
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
