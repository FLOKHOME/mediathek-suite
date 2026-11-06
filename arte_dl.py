#!/usr/bin/env python3
"""
arte-dl.py — Filme aus der ARTE Mediathek herunterladen
========================================================

Kommandos:
  search "begriff"          Suche in der Mediathek (DE)
  info   <url|id>           Formate, Sprachen, Untertitel anzeigen
  dl     <url|id>           Download (beste Qualität, Sprache wählbar)

Beispiele:
  arte-dl search "dark water"
  arte-dl dl 133122-000-A                     # Dark Water, 1080p, Deutsch
  arte-dl dl 133122-000-A --audio ja --subtitles de
  arte-dl dl https://www.arte.tv/de/videos/133131-000-A/ring-das-original/ \
              --max-height 720 --out ~/Videos/arte

Optionen für 'dl':
  --out DIR            Zielordner (Standard: ~/Videos/arte)
  --max-height N       max. Auflösung: 432 / 720 / 1080 (Standard 1080)
  --audio LANG         Tonspur: de / fr / ja / ... (Standard de)
  --subtitles LANGS    Untertitel mitspeichern: de, fr, ... oder all (= SRT)
  --quality best|klein 'klein' erzwingt H.264 (kleinere Dateien)
  --test               nur die ersten 60 Sekunden laden

yt-dlp wird benötigt und automatisch geladen:
  1. als Python-Bibliothek (in die arte-dl.exe eingebaut),
  2. aus ~/arte-dl/lib/ (Wird bei Bedarf von PyPI geladen),
  3. als yt-dlp-Binary aus dem PATH (Subprocess).
ffmpeg muss installiert sein (Merge von Video+Audio, HLS).
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
LIB_DIR = Path(getattr(sys, "_MEIPASS2", None) or
               (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
                else Path(__file__).resolve().parent)) / "lib"
DEFAULT_OUT = HOME / "Videos" / "arte"
FROZEN = bool(getattr(sys, "frozen", False))

PROGRAM_RE = re.compile(
    r"/de/videos/(\d{6}-[0-9A-Z]{2,3}-[A-Z])(?:/([^/\"'<>]+))?",
    re.IGNORECASE,
)
CARD_RE = re.compile(
    r'id="(?P<id>\d{6}-[0-9A-Z]{2,3}-[A-Z])_de-title"[^>]*>(?P<title>[^<]+)</h2>'
    r'(?:<h3[^>]*>(?P<subtitle>[^<]+)</h3>)?'
    r'(?:<p[^>]*>(?P<desc>[^<]+)</p>)?'
    r'.{0,600}?<span[^>]*>(?P<kind>[^<]+)</span>',
    re.IGNORECASE | re.DOTALL,
)
# Vorschlags-Items (Typahead-Liste), falls keine Karten gerendert werden
TS_ITEM_RE = re.compile(
    r'data-testid="ts-tsItemLink"[^>]*'
    r'href="/de/videos/(?P<id>\d{6}-[0-9A-Z]{2,3}-[A-Z])(?:/(?P<slug>[^"\'<>]+))?/?"'
    r'.{0,1500}?<h3[^>]*>(?P<title>[^<]+)</h3>'
    r'(?:.{0,400}?<p[^>]*>(?P<subtitle>[^<]+)</p>)?',
    re.IGNORECASE | re.DOTALL,
)


# --------------------------------------------------------------- yt-dlp laden
def get_ytdlp():
    """Gibt das yt_dlp-Modul zurück; lädt es bei Bedarf nach (auch frozen)."""
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
    """Subprocess-Fallback: yt-dlp-Binary oder python -m yt_dlp."""
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    return [sys.executable, "-m", "yt_dlp"]


# --------------------------------------------------------------- Suche
def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_program_id(token):
    """Akzeptiert: URL, 133122-000-A, 133122."""
    m = PROGRAM_RE.search(token)
    if m:
        return m.group(1)
    if re.fullmatch(r"\d{6}-[0-9A-Z]{2,3}-[A-Z]", token, re.IGNORECASE):
        return token.upper()
    if re.fullmatch(r"\d{6}", token):
        return token + "-000-A"  # Standard-Variante (ARTE)
    return None


def search(query, lang="de"):
    """Suche über die emac-v4-API (SSR der Suchseite ist unzuverlässig)."""
    results, seen = [], set()
    page = 1
    while page <= 10:
        raw = http_get("https://api.arte.tv/api/emac/v4/"
                       f"{lang}/web/search/results?page={page}"
                       f"&query={urllib.parse.quote(query)}")
        try:
            data = json.loads(raw)
        except ValueError:
            break
        items = []
        for z in data.get("zones") or []:
            items += (z.get("content") or {}).get("data") or []
        if not items:
            break
        new = 0
        for it in items:
            pid = it.get("programId") or ""
            if not re.fullmatch(r"\d{6}-[0-9A-Z]{2,3}-[A-Z]", pid):
                continue                    # Sammlungen/Serien ohne Video
            if pid in seen:
                continue
            seen.add(pid)
            new += 1
            results.append({
                "id": pid,
                "title": (it.get("title") or "").strip(),
                "subtitle": (it.get("subtitle") or "").strip(),
                "desc": (it.get("shortDescription") or "").strip()[:120],
                "kind": ((it.get("kind") or {}).get("label") or "").strip(),
                "url": f"https://www.arte.tv/{lang}/videos/{pid}/",
            })
        if new == 0:
            break
        pages = 1
        for z in data.get("zones") or []:
            pg = (z.get("content") or {}).get("pagination") or {}
            try:
                pages = max(pages, int(pg.get("pages") or 1))
            except (TypeError, ValueError):
                pass
        page += 1
        if page > pages:
            break
    if not results:
        return _search_html(query, lang)   # Fallback: HTML der Suchseite
    return results


def _search_html(query, lang="de"):
    page = http_get(f"https://www.arte.tv/{lang}/search/?q={urllib.parse.quote(query)}")
    results, seen = [], set()
    for m in CARD_RE.finditer(page):
        pid = m.group("id")
        if pid in seen:
            continue
        seen.add(pid)
        slug = PROGRAM_RE.search(page[m.end():m.end() + 800])
        results.append({
            "id": pid,
            "title": html.unescape(m.group("title")).strip(),
            "subtitle": html.unescape(m.group("subtitle") or "").strip(),
            "desc": html.unescape(m.group("desc") or "").strip()[:120],
            "kind": (m.group("kind") or "").strip(),
            "url": f"https://www.arte.tv/{lang}/videos/{pid}/"
                   + (slug.group(2) if slug and slug.group(1) == pid else "")
                   + "/",
        })
    # Vorschlagsliste ergänzen (wird gerendert, wenn keine Karten da sind)
    for m in TS_ITEM_RE.finditer(page):
        pid = m.group("id")
        if pid in seen:
            continue
        seen.add(pid)
        results.append({
            "id": pid,
            "title": html.unescape(m.group("title")).strip(),
            "subtitle": html.unescape(m.group("subtitle") or "").strip(),
            "desc": "",
            "kind": "Vorschlag",
            "url": f"https://www.arte.tv/{lang}/videos/{pid}/"
                   + (m.group("slug") or "").rstrip("/") + "/",
        })
    return results


# --------------------------------------------------------------- Info
def fetch_info_dict(pid):
    """Metadaten per yt-dlp-API (in-process) oder per CLI (-J)."""
    url = f"https://www.arte.tv/de/videos/{pid}/"
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


def info(token):
    pid = parse_program_id(token)
    if not pid:
        sys.exit(f"[!] Keine Programm-ID in {token!r}")
    d = fetch_info_dict(pid)
    minutes = round((d.get("duration") or 0) / 60)
    print(f"Titel:      {d.get('title')}")
    if d.get("subtitle"):
        print(f"Untertitel: {d['subtitle']}")
    print(f"ID:         {pid}")
    print(f"Dauer:      {minutes} Min ({d.get('duration')} s)")
    print(f"Veröffentlicht: {d.get('upload_date') or d.get('timestamp')}")
    if d.get("description"):
        print(f"Beschreibung: {d['description'][:200]}")
    audios = {f.get("language") for f in d.get("formats", [])
              if f.get("vcodec") == "none" and f.get("language")}
    print(f"Audio-Sprachen: {', '.join(sorted(audios))}")
    print(f"Untertitel-Spuren: {', '.join((d.get('subtitles') or {}).keys())}")
    print("Videoformate:")
    for f in d.get("formats", []):
        if f.get("vcodec") != "none":
            print(f"  {f.get('format_id'):18} "
                  f"{f.get('resolution') or '':>10} "
                  f"codec={f.get('vcodec')}")
    return d


# --------------------------------------------------------------- Download
def build_spec(token, out_dir=None, max_height=1080, audio="de",
               subtitles=None, quality="best", test=False):
    """Baut Format-String, Output-Template und Optionen."""
    pid = parse_program_id(token)
    if not pid:
        sys.exit(f"[!] Keine Programm-ID in {token!r}")
    url = f"https://www.arte.tv/de/videos/{pid}/"
    out_dir = Path(out_dir or DEFAULT_OUT).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    tpl = str(out_dir / "%(title)s.%(ext)s")

    h = max_height
    audio_fmt = f"ba[language^={audio}]" if audio else "ba"
    if quality == "best":
        fmt = (f"bv*[height<={h}]+{audio_fmt}/"
               f"bv*[height<={h}]+ba/b[height<={h}]/b")
    else:  # klein
        fmt = f"bv*[height<={h}][vcodec^=avc1]+{audio_fmt}/b[height<={h}]/b"

    if subtitles == "all":
        sub_langs, convert_srt = ["all"], True
    elif subtitles:
        sub_langs = [f"{s}.*" for s in subtitles.split(",")]
        convert_srt = False
    else:
        sub_langs, convert_srt = None, False

    opts = {
        "format": fmt,
        "merge_output_format": "mkv",
        "outtmpl": tpl,
        "mtime": False,
        "noprogress": False,
    }
    pps = [{"key": "FFmpegVideoRemuxer", "preferedformat": "mkv"}]
    if sub_langs:
        opts["writesubtitles"] = True
        opts["subtitleslangs"] = sub_langs
        opts["embedsubtitles"] = True
        if convert_srt:
            opts["convertsubtitles"] = "srt"
        pps += [{"key": "FFmpegSubtitlesConvertor", "format": "srt"},
                {"key": "FFmpegEmbedSubtitle", "already_have_subtitle": True}]
    pps += [{"key": "FFmpegMetadata"}, {"key": "EmbedThumbnail"}]
    opts["writethumbnail"] = True
    opts["postprocessors"] = pps
    if test:
        # API: download_ranges erwartet ein download_range_func-Objekt
        try:
            ytdlp_mod = get_ytdlp()
            opts["download_ranges"] = ytdlp_mod.utils.download_range_func(
                [], [(0, 60)])
        except Exception:  # Subprocess-Fallback nutzt den CLI-String
            opts.pop("download_ranges", None)
        opts["force_keyframes_at_cuts"] = True

    args = ["--format", fmt, "--merge-output-format", "mkv", "--remux-video", "mkv",
            "--output", tpl, "--newline", "--no-mtime", url]
    if sub_langs:
        args += ["--write-subtitles", "--sub-langs", ",".join(sub_langs),
                 "--embed-subs"]
        if convert_srt:
            args += ["--convert-subs", "srt"]
    if test:
        args += ["--download-sections", "*0-60", "--force-keyframes-at-cuts"]
    # Frozen (exe): mitgeliefertes ffmpeg aus dem _MEIPASS-Paket verwenden
    if FROZEN:
        meipass = Path(getattr(sys, "_MEIPASS", ""))
        if meipass and ((meipass / "ffmpeg.exe").exists()
                        or (meipass / "ffmpeg").exists()):
            opts["ffmpeg_location"] = str(meipass)
            args += ["--ffmpeg-location", str(meipass)]
    return pid, url, out_dir, opts, args


def download(token, **kw):
    pid, url, out_dir, opts, args = build_spec(token, **kw)
    print(f"[*] Lade {pid} nach {out_dir}")
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
    # Subprocess-Fallback (python -m yt_dlp, kein frozen)
    subprocess.run(ytdlp_cli_argv() + args, check=True,
                   env={**os.environ, "PYTHONPATH": str(LIB_DIR)})


# --------------------------------------------------------------- Main
def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    cmd = sys.argv[1]
    if cmd == "search":
        q = sys.argv[2]
        results = search(q)
        print(f"{len(results)} Treffer für {q!r}:")
        for r in results:
            line = f"  {r['id']}  {r['title']}"
            if r["subtitle"]:
                line += f" – {r['subtitle']}"
            if r["kind"]:
                line += f" [{r['kind']}]"
            print(line)
            if r["desc"]:
                print(f"        {r['desc']}")
            print(f"        {r['url']}")
    elif cmd == "info":
        info(sys.argv[2])
    elif cmd in ("dl", "download"):
        token = sys.argv[2]
        kwargs = {}
        i = 3
        while i < len(sys.argv):
            a = sys.argv[i]
            if a == "--out":
                kwargs["out_dir"] = sys.argv[i + 1]; i += 2
            elif a == "--max-height":
                kwargs["max_height"] = int(sys.argv[i + 1]); i += 2
            elif a == "--audio":
                kwargs["audio"] = sys.argv[i + 1]; i += 2
            elif a == "--subtitles":
                kwargs["subtitles"] = sys.argv[i + 1]; i += 2
            elif a == "--quality":
                kwargs["quality"] = sys.argv[i + 1]; i += 2
            elif a == "--test":
                kwargs["test"] = True; i += 1
            else:
                sys.exit(f"[!] Unbekannte Option {a!r}")
        download(token, **kwargs)
    else:
        print(__doc__)
        sys.exit(f"[!] Unbekanntes Kommando {cmd!r}")


if __name__ == "__main__":
    main()
