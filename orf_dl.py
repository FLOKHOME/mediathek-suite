#!/usr/bin/env python3
"""
orf_dl.py — ORF ON (Tvthek): Suche + Download (max. Qualität, MKV/MP4)
======================================================================

Wichtig (Geo-Falle):
  ORF liefert ausländischen IPs (z. B. Deutschland) für "right": "austria"-
  Inhalte ein PLATZHALTER-Video (statische Karte "nur in Österreich").
  Dieses Modul testet deshalb VOR dem Download die ersten Sekunden auf
  Freeze (Platzhalter = statisches Bild) und bricht sauber ab — keine
  schwarzen/platzhalter Dateien.

Endpunkte:
  - Metadaten/Streams: https://api-tvthek.orf.at/api/v4.3/public/episode/
      encrypted/{base64("3dSlfek03nsLKdj4Jsd" + videoId)}   (offen, weltweiter Zugriff)
  - Such-API /api/v4.3/suggest: nur mit österreichischer IP (401 sonst),
    Client via curl_cffi (Chrome-TLS-Imitation), Fallback: Direkt-Link/ID.
  - CDN (apasfiis.sf.apa.at): HLS/DASH, Platzhalter vs. echt je nach IP.

Kommandos (optional, als CLI):
  search "begriff" | info <url|id> | dl <url|id> [--out DIR] [--max-height N]
        [--subs auto|all|none] [--test] [--format mkv|mp4]
"""
import base64
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
LIB_DIR = HOME / "orf-dl" / "lib"
DEFAULT_OUT = HOME / "Videos" / "ORF"
BASE_VIDEO = "https://on.orf.at/video"
API = "https://api-tvthek.orf.at/api/v4.3"
MAGIC = "3dSlfek03nsLKdj4Jsd"
FORMAT_CHAIN = "bv*[height<={h}]+ba/bv*[height<={h}]+ba/b[height<={h}]/b"
FROZEN = bool(getattr(sys, "frozen", False))

# World-Rechte: überall abspielbar, kein Probe nötig
WORLD_RIGHTS = {"worldwide", "world", "europe", "germany", "dach"}


# --------------------------------------------------------------- yt-dlp laden
def get_ytdlp():
    try:
        import yt_dlp
        return yt_dlp
    except ImportError:
        pass
    if (LIB_DIR / "yt_dlp").is_dir() or (HOME / "arte-dl" / "lib" / "yt_dlp").is_dir():
        lib = LIB_DIR if (LIB_DIR / "yt_dlp").is_dir() \
            else HOME / "arte-dl" / "lib"
        if str(lib) not in sys.path:
            sys.path.insert(0, str(lib))
        import importlib
        importlib.invalidate_caches()
        try:
            import yt_dlp
            return yt_dlp
        except ImportError:
            if str(lib) in sys.path:
                sys.path.remove(str(lib))
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


def http_get_json(url, timeout=25):
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


# --------------------------------------------------------------- ID/URL
def parse_video_id(text):
    """Nimmt on.orf.at-URL (mit/ohne Segment) oder nackte Video-ID."""
    text = (text or "").strip()
    m = re.match(r"https?://on\.orf\.at/video/(\d+)(?:/(\d+))?", text)
    if m:
        return m.group(1), m.group(2)
    if re.fullmatch(r"\d{6,9}", text):
        return text, None
    return None, None


def get_episode_data(vid):
    enc = base64.b64encode(f"{MAGIC}{vid}".encode()).decode()
    return http_get_json(f"{API}/public/episode/encrypted/{enc}")


# --------------------------------------------------------------- Geo-Prüfung
def geo_status(data):
    """'ok' | 'austria' | 'drm' — laut Lizenzfeld (ohne IP-Test)."""
    if data.get("is_drm_protected"):
        return "drm"
    right = (data.get("right") or "").lower()
    if right and right not in WORLD_RIGHTS:
        return "austria"
    return "ok"


# --------------------------------------------------------------- Geo/Platzhalter-Test
def _manifest_stream_seconds(data):
    """Summiert die EXTINF-Dauern der ersten HLS-Variante.
    Platzhalter an Nicht-AT-IPs: 0 s oder 10-s-Loop statt echter Länge."""
    try:
        src = data.get("sources", {}).get("hls", [])
        if not src:
            return None
        master = urllib.request.urlopen(urllib.request.Request(
            src[0]["src"], headers={"User-Agent": USER_AGENT}),
            timeout=25).read().decode("utf-8", "replace")
        variants = re.findall(r"(https?://\S+\.m3u8\S*)", master) or [
            src[0]["src"].rsplit("/", 1)[0] + "/" + v
            for v in re.findall(r"^(?!#)(\S+\.m3u8)", master, re.M)]
        if not variants:
            return None
        pl = urllib.request.urlopen(urllib.request.Request(
            variants[0], headers={"User-Agent": USER_AGENT}),
            timeout=25).read().decode("utf-8", "replace")
        durs = [float(x) for x in re.findall(r"EXTINF:([\d.]+)", pl)]
        return sum(durs)
    except Exception:  # noqa: BLE001
        return None


def geo_stream_ok(data):
    """True = Stream passt zur Metadaten-Länge (echter Inhalt).
    None = unklar (Manifest nicht lesbar). False = Platzhalter."""
    meta = data.get("duration_seconds") or data.get("exact_duration")
    if not meta or meta < 60:      # Kurzclips/Live: kein aussagekräftiger Test
        return None
    seen = _manifest_stream_seconds(data)
    if seen is None:
        return None
    return seen >= 0.5 * meta      # Platzhalter: ~0-10 s statt voller Länge


def _freeze_seconds(path, seconds=60):
    """Sekunden statisches Bild in den ersten `seconds` Sekunden."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        if FROZEN:
            meipass = Path(getattr(sys, "_MEIPASS", ""))
            cand = meipass / "ffmpeg.exe" if (meipass / "ffmpeg.exe").exists() \
                else meipass / "ffmpeg"
            if cand.exists():
                ffmpeg = str(cand)
    if not ffmpeg:
        return 0.0  # ohne ffmpeg kein Urteil (kein Fehlalarm)
    total = 0.0
    try:
        p = subprocess.run(
            [ffmpeg, "-v", "info", "-t", str(seconds), "-i", str(path),
             "-vf", "freezedetect=n=-30dB:d=15", "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120)
        for m in re.finditer(r"frozen_duration:\s*([\d.]+)", p.stderr or ""):
            total += float(m.group(1))
    except Exception:  # noqa: BLE001
        return 0.0
    return total


def _probe_placeholder(url, tmp_dir):
    """Video-Fallback-Probe: erste ~12 s in kleinster Video-Qualität."""
    tmp_dir = Path(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    ytdlp = get_ytdlp()
    opts = {
        "format": "wv/w",
        "outtmpl": str(tmp_dir / "probe.%(ext)s"),
        "quiet": True,
        "noprogress": True,
        "download_ranges": ytdlp.utils.download_range_func([], [(0, 12)]),
        "force_keyframes_at_cuts": True,
        "writethumbnail": False,
    }
    if FROZEN:
        meipass = Path(getattr(sys, "_MEIPASS", ""))
        if meipass and (meipass / "ffmpeg.exe").exists():
            opts["ffmpeg_location"] = str(meipass)
    with ytdlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    files = sorted(tmp_dir.glob("probe.*"))
    if not files:
        return False
    frozen = _freeze_seconds(files[0], seconds=15)
    for f in files:
        f.unlink(missing_ok=True)
    return frozen >= 10.0  # ≥10 s Standbild in 12 s Material = Platzhalter


# --------------------------------------------------------------- Suche
def _cffi_get(url, params=None):
    """GET über curl_cffi (Chrome-TLS). None, wenn curl_cffi fehlt."""
    try:
        from curl_cffi import requests as cr
    except ImportError:
        return None
    r = cr.get(url, params=params or {}, impersonate="chrome", timeout=20,
               headers={"Origin": "https://on.orf.at",
                        "Referer": "https://on.orf.at/",
                        "Accept": "application/json"})
    return r


def search(query):
    """ORF-Suche (suggest-API). Funktioniert nur mit österreichischer IP;
    sonst RuntimeError mit klarer Meldung. Direkt-Link/ID geht immer."""
    r = _cffi_get(f"{API}/suggest", params={"query": query})
    if r is None:
        raise RuntimeError(
            "ORF-Suche braucht curl_cffi (Chrome-TLS). Bitte die Suite-EXE "
            "nutzen oder Direkt-Link / Video-ID eingeben.")
    if r.status_code == 401:
        raise RuntimeError(
            "ORF-Suche ist nur mit österreichischer IP verfügbar "
            "(401). Direkt-Link oder Video-ID eingeben — der Download "
            "eines Links funktioniert, sobald eine AT-IP dahinter steckt.")
    if r.status_code != 200:
        raise RuntimeError(f"ORF-Suche: HTTP {r.status_code}")
    try:
        data = r.json()
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"ORF-Suche: unerwartete Antwort ({e})")
    results = []

    def walk(node):
        if isinstance(node, dict):
            vid = node.get("id")
            href = node.get("href") or ""
            title = node.get("title") or node.get("headline")
            if isinstance(vid, int) and (title or href):
                url = href if href.startswith("http") \
                    else f"{BASE_VIDEO}/{vid}"
                results.append({
                    "id": str(vid),
                    "title": str(title or vid),
                    "duration": node.get("duration_seconds"),
                    "url": url,
                    "right": (node.get("right") or "") or None,
                })
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(data)
    seen, out = set(), []
    for it in results:
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        out.append(it)
    return out[:25]


# --------------------------------------------------------------- yt-dlp-Optionen
def build_opts(url, out_dir, max_height=1080, subs="auto", test=False,
               container="mkv"):
    h = max_height
    opts = {
        "format": FORMAT_CHAIN.format(h=h),
        "format_sort": ["res", "tbr"],
        "outtmpl": str(out_dir / "%(title)s.%(ext)s"),
        "mtime": False,
        "noprogress": False,
        "postprocessors": [
            {"key": "FFmpegVideoRemuxer", "preferedformat": container},
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


def build_args(url, out_dir, max_height=1080, subs="auto", test=False,
               container="mkv"):
    h = max_height
    args = ["--format", FORMAT_CHAIN.format(h=h), "--format-sort", "res,tbr",
            "--remux-video", container, "--embed-metadata", "--embed-thumbnail",
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


def _ffprobe_duration(path):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        p = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=60)
        return float(p.stdout.strip())
    except Exception:  # noqa: BLE001
        return None


def fetch_info_dict(token):
    """GUI-Details: token = URL oder ID."""
    vid, _seg = parse_video_id(token)
    if not vid:
        raise RuntimeError(f"ORF: keine Video-ID erkennbar ({token[:60]})")
    d = get_episode_data(vid)
    return {
        "title": d.get("title") or d.get("headline") or vid,
        "duration": (d.get("duration_seconds") or 0) / 60.0,
        "description": (d.get("description") or d.get("teaser_text") or "")[:400],
        "thumb": d.get("_embedded", {}).get("image", {})
                 .get("public_urls", {}).get("highlight_teaser", {}).get("url"),
        "url": f"{BASE_VIDEO}/{vid}",
        "right": d.get("right"),
    }


# --------------------------------------------------------------- Download
def download(url, out_dir=None, max_height=1080, subs="auto", test=False,
             container="mkv"):
    out_dir = Path(out_dir or DEFAULT_OUT).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    vid, _seg = parse_video_id(url)
    if not vid:
        raise RuntimeError("ORF: Bitte einen on.orf.at-Link oder eine "
                           "Video-ID angeben.")
    data = get_episode_data(vid)
    status = geo_status(data)
    title = data.get("title") or vid
    right = data.get("right") or "?"
    meta = data.get("duration_seconds") or 0
    probe_dir = out_dir / ".orf_probe"
    if status == "drm":
        shutil.rmtree(probe_dir, ignore_errors=True)
        raise RuntimeError(
            f"ORF: „{title}“ ist DRM-geschützt – kein Download möglich.")

    def _abort_geo():
        raise RuntimeError(
            f"ORF: „{title}“ ist nur in Österreich abspielbar "
            f"(right={right}) — an dieser IP gibt der ORF nur einen "
            "Platzhalter aus. Abbruch, keine Datei angelegt. Mit AT-IP "
            "(VPN/Standort) funktioniert der Download.")

    if status == "austria":
        # Lizenz nur Österreich → Stream-Dauer-Test, BEVOR geladen wird
        print(f"[*] ORF-Geo-Test (right={right}): prüfe Manifest ...")
        stream_ok = geo_stream_ok(data)
        if stream_ok is False:
            shutil.rmtree(probe_dir, ignore_errors=True)
            _abort_geo()
        if stream_ok is None:
            # Manifest nicht lesbar → Video-Probe (12 s, kleinste Qualität)
            print("[*] Manifest unklar → Video-Probe ...")
            url_full = f"{BASE_VIDEO}/{vid}"
            try:
                placeholder = _probe_placeholder(url_full, probe_dir)
            finally:
                shutil.rmtree(probe_dir, ignore_errors=True)
            if placeholder:
                _abort_geo()
    else:
        shutil.rmtree(probe_dir, ignore_errors=True)

    opts = build_opts(f"{BASE_VIDEO}/{vid}", out_dir, max_height, subs,
                      test, container)
    print(f"[*] Lade ORF {vid} nach {out_dir}")
    ytdlp = get_ytdlp()
    with ytdlp.YoutubeDL(opts) as ydl:
        ydl.download([f"{BASE_VIDEO}/{vid}"])

    # Abschluss-Tests (nur Voll-Download): keine Platzhalter-Datei
    if not test:
        latest = max(out_dir.glob(f"*.{container}"),
                     key=lambda p: p.stat().st_mtime, default=None)
        if latest is not None:
            # 1) Dauer-Diskrepanz: Platzhalter-Loop statt echter Länge
            got = _ffprobe_duration(latest)
            if got and meta >= 60 and got < 0.5 * meta:
                latest.unlink(missing_ok=True)
                raise RuntimeError(
                    f"ORF: „{latest.name}“ ist nur {got:.0f}s lang "
                    f"(erwartet {meta:.0f}s) — Geo-Platzhalter, Datei "
                    "gelöscht. (VPN mit AT-IP nötig.)")
            # 2) Standbild-Sicherheitsnetz
            frozen = _freeze_seconds(latest, seconds=60)
            if frozen >= 45.0:
                latest.unlink(missing_ok=True)
                raise RuntimeError(
                    f"ORF: „{latest.name}“ enthielt fast nur Standbild "
                    f"({frozen:.0f}s/60s) — Platzhalter-Verdacht, Datei "
                    "gelöscht. (Geo-Sperre? VPN mit AT-IP nötig.)")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    cmd = sys.argv[1]
    if cmd == "search":
        for i, it in enumerate(search(" ".join(sys.argv[2:])), 1):
            dur = it.get("duration")
            mm = f"{int(dur)//60:02d}:{int(dur)%60:02d}" if dur else "--:--"
            print(f"{i:2}. [{mm}] {it['title']}  ({it['right'] or '?'})\n"
                  f"    {it['url']}")
    elif cmd == "info":
        d = fetch_info_dict(sys.argv[2])
        print(json.dumps(d, ensure_ascii=False, indent=2))
    elif cmd == "dl":
        rest = sys.argv[2:]
        out, mh, subs, test, cont = None, 1080, "auto", False, "mkv"
        i = 0
        while i < len(rest):
            if rest[i] == "--out":
                out = rest[i + 1]; i += 2
            elif rest[i] == "--max-height":
                mh = int(rest[i + 1]); i += 2
            elif rest[i] == "--subs":
                subs = rest[i + 1]; i += 2
            elif rest[i] == "--test":
                test = True; i += 1
            elif rest[i] == "--format":
                cont = rest[i + 1]; i += 2
            else:
                url = rest[i]; i += 1
        download(url, out, mh, subs, test, cont)
    else:
        # Bequemlichkeit: erste Arg = URL/ID → Download
        download(sys.argv[1])


if __name__ == "__main__":
    main()
