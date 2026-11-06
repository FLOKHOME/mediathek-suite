#!/usr/bin/env python3
"""
3sat-dl.py — Sendungen und Filme aus der 3sat-Mediathek herunterladen
=====================================================================

Kommandos:
  search "begriff"          Suche auf 3sat.de (serverseitig gerendert)
  info   <url>              Formate, Untertitel, Dauer anzeigen
  dl     <url>              Download: max. Qualität, Ziel-MKV

Beispiele:
  3sat-dl search "sterben"
  3sat-dl dl https://www.3sat.de/film/spielfilm/sterben-104.html
  3sat-dl dl <url> --out ~/Videos/3sat

Optionen für 'dl':
  --out DIR            Zielordner (Standard: ~/Videos/3sat)
  --max-height N       max. Auflösung (Standard 1080)
  --subs all|none      Untertitel: all = alle mitspeichern+einbetten
                       (Standard: de/deu automatisch, wenn vorhanden)
  --audio auto|tvton   TV-Ton bevorzugen (statt Audiodeskription)
  --test               nur die ersten 60 Sekunden laden

Qualität: Format-Sortierung res,tbr übersteuert yt-dlps Codec-Präferenz
(sonst wählt es VP9 mit weniger Bitrate). TV-Ton-Audiospur wird der
Audiodeskription vorgezogen. Ergebnis: MKV (verlustfreies Remux),
Untertitel + Metadaten + Cover eingebettet.

yt-dlp wird automatisch geladen (wie bei arte-dl); ffmpeg wird benötigt.
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
DEFAULT_OUT = HOME / "Videos" / "3sat"
SEARCH_URL = "https://www.3sat.de/suche?q="
FROZEN = bool(getattr(sys, "frozen", False))

FORMAT_CHAIN = ("bv*[height<={h}]+ba[format_id*=TV_Ton]/"
                "bv*[height<={h}]+ba/b[height<={h}]/b")

TEASER_RE = re.compile(
    r'<a[^>]*class="teaser-title-link[^"]*"[^>]*>', re.IGNORECASE)
HREF_RE = re.compile(r'href="(?P<path>[^"]+\.html)"', re.IGNORECASE)
TITLE_RE = re.compile(r'title="(?P<title>[^"]+)"')
SUBHEAD_RE = re.compile(
    r'class="a--subheadline[^"]*"[^>]*>\s*(?P<cat>[^<]+?)\s*<', re.DOTALL)
AVAIL_RE = re.compile(
    r'<span class="label">(?P<avail>[^<]+)</span>')


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


# --------------------------------------------------------------- Suche
def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def search(query):
    page = http_get(SEARCH_URL + urllib.parse.quote(query))
    results, seen = [], set()
    for m in TEASER_RE.finditer(page):
        tag = m.group(0)
        hm = HREF_RE.search(tag)
        tm = TITLE_RE.search(tag)
        if not hm or not tm:
            continue
        path = hm.group("path")
        if not re.search(r"-\d+\.html$", path):
            continue
        url = "https://www.3sat.de" + html.unescape(path)
        if url in seen:
            continue
        seen.add(url)
        window = page[m.end():m.end() + 2500]
        cat = SUBHEAD_RE.search(window)
        avail = AVAIL_RE.search(window)
        results.append({
            "url": url,
            "title": html.unescape(tm.group("title")).strip(),
            "category": html.unescape(cat.group("cat")).strip() if cat else "",
            "availability": html.unescape(avail.group("avail")).strip()
                            if avail else "",
        })
    return results


# --------------------------------------------------------------- Info
def fetch_info_dict(url):
    try:
        ytdlp = get_ytdlp()
    except Exception as e:  # noqa: BLE001
        ytdlp = None
        if FROZEN:
            sys.exit(f"[!] yt-dlp konnte nicht geladen werden: {e}")
    if ytdlp is not None:
        with ytdlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
            return ydl.extract_info(url, download=False)
    out = subprocess.run(
        ytdlp_cli_argv() + ["-J", url],
        capture_output=True, text=True, check=True,
        env={**os.environ, "PYTHONPATH": str(LIB_DIR)},
    )
    return json.loads(out.stdout)


def info(url):
    d = fetch_info_dict(url)
    print(f"Titel:      {d.get('title')}")
    print(f"Dauer:      {round((d.get('duration') or 0)/60)} Min "
          f"({d.get('duration')} s)")
    if d.get("upload_date"):
        print(f"Veröffentlicht: {d['upload_date']}")
    if d.get("description"):
        print(f"Inhalt:     {d['description'][:200]}")
    print(f"Untertitel: {', '.join((d.get('subtitles') or {}).keys()) or '-'}")
    print("Formate (Video, sortiert nach Bitrate):")
    vids = [f for f in d.get("formats", []) if f.get("vcodec") != "none"]
    vids.sort(key=lambda f: f.get("tbr") or 0, reverse=True)
    for f in vids[:6]:
        print(f"  {f.get('format_id'):34} res={f.get('resolution')} "
              f"tbr={f.get('tbr')} v={f.get('vcodec')} proto={f.get('protocol')}")
    audios = {f.get("format_note") or "?" for f in d.get("formats", [])
              if f.get("vcodec") == "none"}
    print(f"Audiospuren: {', '.join(sorted(audios))}")
    return d


# --------------------------------------------------------------- Download
def build_opts(out_dir, max_height=1080, subs="auto", test=False):
    h = max_height
    opts = {
        "format": FORMAT_CHAIN.format(h=h),
        "format_sort": ["res", "tbr"],           # Bitrate > Codec-Präferenz
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
    if subs == "all":
        opts["writesubtitles"] = True
        opts["subtitleslangs"] = ["all"]
        opts["embedsubtitles"] = True
    elif subs == "auto":
        opts["writesubtitles"] = True
        opts["subtitleslangs"] = ["de", "deu.*"]
        opts["embedsubtitles"] = True
    if opts.get("embedsubtitles"):
        # API-Pfad: Subtitle-PPs wie im CLI-Entry explizit setzen
        opts["postprocessors"] = [
            opts["postprocessors"][0],                      # Remuxer zuerst
            {"key": "FFmpegSubtitlesConvertor", "format": "srt"},
            {"key": "FFmpegEmbedSubtitle", "already_have_subtitle": True},
            *opts["postprocessors"][1:],                    # Metadata, Cover
        ]
    if test:
        try:
            ytdlp_mod = get_ytdlp()
            opts["download_ranges"] = ytdlp_mod.utils.download_range_func(
                [], [(0, 60)])
        except Exception:  # Subprocess-Fallback nutzt den CLI-String
            opts.pop("download_ranges", None)
        opts["force_keyframes_at_cuts"] = True
    # Frozen (exe): mitgeliefertes ffmpeg aus dem _MEIPASS-Paket verwenden
    if FROZEN:
        meipass = Path(getattr(sys, "_MEIPASS", ""))
        if meipass and ((meipass / "ffmpeg.exe").exists()
                        or (meipass / "ffmpeg").exists()):
            opts["ffmpeg_location"] = str(meipass)
    return opts


def build_args(url, out_dir, max_height=1080, subs="auto", test=False):
    h = max_height
    args = ["--format", FORMAT_CHAIN.format(h=h),
            "--format-sort", "res,tbr",
            "--remux-video", "mkv",
            "--embed-metadata", "--embed-thumbnail", "--write-thumbnail",
            "--no-mtime",
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
    opts = build_opts(out_dir, max_height, subs, test)
    args = build_args(url, out_dir, max_height, subs, test)
    print(f"[*] Lade {url} ({max_height}p max, Untertitel: {subs}) nach {out_dir}")
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
            line = f"  {r['title']}"
            if r["category"]:
                line += f" [{r['category']}]"
            if r["availability"]:
                line += f" ({r['availability']})"
            print(line)
            print(f"        {r['url']}")
    elif cmd == "info":
        if len(sys.argv) < 3:
            sys.exit("[!] URL fehlt")
        info(sys.argv[2])
    elif cmd in ("dl", "download"):
        url = sys.argv[2]
        kwargs = {}
        i = 3
        while i < len(sys.argv):
            a = sys.argv[i]
            if a == "--out":
                kwargs["out_dir"] = sys.argv[i + 1]; i += 2
            elif a == "--max-height":
                kwargs["max_height"] = int(sys.argv[i + 1]); i += 2
            elif a == "--subs":
                kwargs["subs"] = sys.argv[i + 1]; i += 2
            elif a == "--test":
                kwargs["test"] = True; i += 1
            else:
                sys.exit(f"[!] Unbekannte Option {a!r}")
        download(url, **kwargs)
    else:
        print(__doc__)
        sys.exit(f"[!] Unbekanntes Kommando {cmd!r}")


if __name__ == "__main__":
    main()
