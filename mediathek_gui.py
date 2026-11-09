#!/usr/bin/env python3
"""
mediathek_gui.py — Mediathek-Suite: globale Suche + Download
============================================================

Quellen: ARTE, Netzkino, 3sat, ARD Mediathek, ZDF Mediathek.
Eine Suche über alle Quellen gleichzeitig, Details in lesbarer Form,
Auflösungsauswahl anhand der tatsächlich verfügbaren Formate,
Download immer als MKV in der gewählten Qualität (beste vorausgewählt).

Modus:
  Standard        GUI starten (tkinter)
  --selftest      Headless-Prüfung: Module, Suchen, Optionsbau (ohne Tk)

Die Downloader liegen als Module neben dieser Datei (arte_dl, netzkino_dl,
3sat_dl, ard_dl, zdf_dl) und werden direkt benutzt. yt-dlp, ffprobe und
ffmpeg werden für die EXE mitgebaut (siehe .github/workflows).
"""
import importlib
import json
import queue
import re
import sys
import threading
import traceback
import urllib.parse
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent if not getattr(sys, "frozen", False) \
    else Path(sys.executable).resolve().parent
sys.path.insert(0, str(APP_DIR))

SETTINGS_FILE = Path.home() / ".mediathek-suite.json"
SOURCES = ("ARTE", "Netzkino", "3sat", "ARD", "ZDF", "YouTube")
MODULES = {"ARTE": "arte_dl", "Netzkino": "netzkino_dl", "3sat": "3sat_dl",
           "ARD": "ard_dl", "ZDF": "zdf_dl", "YouTube": "yt_dl"}


# --------------------------------------------------------------- Engine
def get_source_module(name):
    # importlib (3sat_dl beginnt mit Ziffer → kein normales `import`),
    # die Module werden per --hidden-import in die EXE gebaut (s. Workflow)
    return importlib.import_module(MODULES[name])


def detect_url_source(text):
    """Erkennt die Quelle einer URL → (source, token) oder (None, None)."""
    text = text.strip()
    if not re.match(r"https?://", text):
        return None, None
    host = urllib.parse.urlparse(text).netloc.lower()
    if "arte.tv" in host:
        import arte_dl
        pid = arte_dl.parse_program_id(text)
        return ("ARTE", pid) if pid else (None, None)
    if "netzkino.de" in host:
        import netzkino_dl
        slug = netzkino_dl.parse_slug(text)
        return ("Netzkino", slug) if slug else (None, None)
    if "3sat.de" in host:
        return "3sat", text
    if "ardmediathek.de" in host:
        return "ARD", text
    if "zdf.de" in host:
        return "ZDF", text
    if "youtube.com" in host or "youtu.be" in host:
        import yt_dl
        vid = yt_dl.parse_video_id(text)
        return ("YouTube", yt_dl.canonical_url(vid)) if vid else (None, None)
    return None, None


def do_search(source, query):
    """Sucht in einer Quelle; liefert (label, token, url, duration_min)."""
    mod = get_source_module(source)
    out = []
    if source == "ARTE":
        for r in mod.search(query):
            label = r["title"]
            if r["subtitle"]:
                label += f" – {r['subtitle']}"
            if r["kind"]:
                label += f"  [{r['kind']}]"
            dmin = round(r["duration"] / 60) if r.get("duration") else None
            out.append((label, r["id"], r["url"], dmin))
    elif source == "Netzkino":
        for r in mod.search(query):
            label = r.get("title") or r.get("originalTitle") or "?"
            if r.get("originalTitle") and r["originalTitle"] != r.get("title"):
                label += f" [orig: {r['originalTitle']}]"
            if r.get("productionYear"):
                label += f" ({r['productionYear']})"
            if r.get("hasDrm"):
                label += "  [DRM-Flag]"
            out.append((label, r["slug"], r["slug"],
                        r.get("duration_min")))
    elif source == "3sat":
        for r in mod.search(query):
            label = r["title"]
            if r["category"]:
                label += f"  [{r['category']}]"
            if r["availability"]:
                label += f"  ({r['availability']})"
            out.append((label, r["url"], r["url"], r.get("duration_min")))
    elif source == "ARD":
        for r in mod.search(query):
            label = r["title"]
            if r["meta"]:
                label += f"  ({r['meta']})"
            out.append((label, r["url"], r["url"], r.get("duration_min")))
    elif source == "YouTube":
        for r in mod.search(query):
            label = r["title"]
            if r["channel"]:
                label += f"  [{r['channel']}]"
            out.append((label, r["url"], r["url"], r.get("duration_min")))
    else:  # ZDF
        for r in mod.search(query):
            label = r["title"]
            if r["content_type"]:
                label += f"  [{r['content_type']}]"
            out.append((label, r["url"], r["url"], r.get("duration_min")))
    return out


LENGTH_FILTERS = {
    "Alle": None,
    "Filme (ab 45 Min)": (45, None),
    "Mittel (15–45 Min)": (15, 45),
    "Kurz (unter 15 Min)": (None, 15),
}


def apply_filters(rows, source_filter, length_filter):
    """Filtert Ergebniszeilen; liefert (sichtbare, Anzahl_ohne_Länge)."""
    rng = LENGTH_FILTERS.get(length_filter)
    out, hidden_unknown = [], 0
    for r in rows:
        if source_filter != "Alle" and r["source"] != source_filter:
            continue
        if rng:
            lo, hi = rng
            dm = r["duration_min"]
            if dm is None:
                if r["source"] == "Netzkino":
                    dm = 999        # Netzkino enthält nur Spielfilme
                else:
                    hidden_unknown += 1
                    continue
            if lo is not None and dm < lo:
                continue
            if hi is not None and dm > hi:
                continue
        out.append(r)
    return out, hidden_unknown


class _GuiLogger:
    """Leitet yt-dlp-Logzeilen in die GUI-Warteschlange."""

    def __init__(self, q):
        self.q = q

    def debug(self, msg):
        if msg and not msg.startswith("["):
            self.q.put(("log", msg))

    info = debug

    def warning(self, msg):
        self.q.put(("log", f"WARNUNG: {msg}"))

    def error(self, msg):
        self.q.put(("log", f"FEHLER: {msg}"))


# --------------------------------------------------------------- Details
def resolutions_from_info(info_dict):
    """Verfügbare Auflösungen aus einem yt-dlp-Info-Dict.
    Liefert [(label, max_height)] sortiert, beste zuerst."""
    seen = {}
    for f in info_dict.get("formats", []):
        h = f.get("height")
        if not h or f.get("vcodec") in (None, "none"):
            continue
        tbr = f.get("tbr") or 0
        if h not in seen or tbr > seen[h]:
            seen[h] = tbr
    out = [("Maximal", max(seen) if seen else None)]
    for h in sorted(seen, reverse=True):
        tbr = seen[h]
        t = f"{h}p" + (f" · {tbr/1e3:.1f} Mbit/s" if tbr else "")
        out.append((t, h))
    return out


def details_from_info(source, info_dict):
    """Menschlich lesbare Details aus einem yt-dlp-Info-Dict."""
    minutes = round((info_dict.get("duration") or 0) / 60)
    lines = [f"Titel:          {info_dict.get('title')}",
             f"Quelle:         {source}",
             f"Dauer:          {minutes} Min" if minutes else "Dauer: ?"]
    if info_dict.get("release_year"):
        lines.append(f"Jahr:           {info_dict['release_year']}")
    if info_dict.get("uploader"):
        lines.append(f"Kanal:          {info_dict['uploader']}")
    if info_dict.get("upload_date"):
        lines.append(f"Veröffentlicht: {info_dict['upload_date']}")
    if info_dict.get("description"):
        desc = " ".join(info_dict["description"].split())
        lines.append("Beschreibung:   " + desc[:400])
    subs = ", ".join((info_dict.get("subtitles") or {}).keys()) or "keine"
    lines.append(f"Untertitel:     {subs}")
    audios = {f.get("format_note") or f.get("language") or "?"
              for f in info_dict.get("formats", [])
              if f.get("vcodec") == "none"}
    if audios:
        lines.append(f"Tonspuren:      {', '.join(sorted(audios))}")
    return lines


def get_details(source, token, q):
    """Lädt Details; liefert (text_lines, resolution_list)."""
    mod = get_source_module(source)
    if source == "Netzkino":
        mv = mod.fetch_movie(token)
        if mv.get("hasDrm"):
            q.put(("log", "[*] DRM-Flag – prüfe Klartext-Stream ..."))
        url, bitrate = mod.best_stream_url(
            (mv.get("videoSource") or {}).get("pmdUrl"), probe=True)
        if not mod.verify_clear_mp4(url):
            raise RuntimeError("Kein unverschlüsselter Stream verfügbar.")
        size = mod.http_head(url)[1]
        minutes = round((mv.get("runtimeInSeconds") or 0) / 60)
        label = mv.get("title") or "?"
        if mv.get("originalTitle") and mv["originalTitle"] != mv.get("title"):
            label += f" (Original: {mv['originalTitle']})"
        lines = [f"Titel:          {label}",
                 "Quelle:         Netzkino"]
        lines.append(f"Dauer:          {minutes} Min" if minutes
                     else "Dauer:          ?")
        lines.append(f"Jahr/Land:      {mv.get('productionYear')} / "
                     f"{mv.get('productionCountry')}")
        if mv.get("longSynopsis"):
            lines.append("Beschreibung:   "
                         + " ".join(mv["longSynopsis"].split())[:400])
        if size:
            gb = int(size) / 1e9
            lines.append(f"Qualität:       Maximum (Full HD, "
                         f"ca. {gb:.1f} GB)")
        lines.append(f"Lizenz bis:    {(mv.get('validLicenseEnd') or '?')[:10]}")
        res = [("Maximal (Full HD)", None)]
        return lines, res
    info = mod.fetch_info_dict(token)
    lines = details_from_info(source, info)
    res = resolutions_from_info(info)
    return lines, res


# --------------------------------------------------------------- Download
def prepare(source, token, out_dir, max_height, subs, test, q, hook):
    """Baut (url, opts, [info_dict]) für die yt-dlp-Ausführung."""
    mod = get_source_module(source)
    if source == "ARTE":
        kw_sub = {"auto": "de", "alle": "all", "keine": None}.get(subs, "de")
        _pid, url, _od, opts, _args = mod.build_spec(
            token, out_dir=out_dir, max_height=max_height or 1080,
            audio="de", subtitles=kw_sub, quality="best", test=test)
        info = None
    elif source == "Netzkino":
        mv = mod.fetch_movie(token)
        if mv.get("hasDrm"):
            q.put(("log", "[*] DRM-Flag – prüfe Klartext-Stream ..."))
        url, bitrate = mod.best_stream_url(
            (mv.get("videoSource") or {}).get("pmdUrl"), probe=True)
        if not mod.verify_clear_mp4(url):
            raise RuntimeError("Kein unverschlüsselter Stream verfügbar.")
        if mv.get("hasDrm"):
            q.put(("log", "[i] Klartext-MP4 bestätigt."))
        info = mod.build_movie_info(mv, token, url, bitrate)
        opts = mod.build_opts(out_dir, test)
    elif source == "3sat":
        url, info = token, None
        opts = mod.build_opts(out_dir, max_height=max_height or 1080,
                              subs=subs, test=test)
    elif source == "YouTube":
        # max_height=None = kein Cap (YouTube hat bis 4K/8K)
        url, info = token, None
        opts = mod.build_opts(token, out_dir, max_height=max_height,
                              subs=subs, test=test)
    else:  # ARD / ZDF
        url, info = token, None
        opts = mod.build_opts(token, out_dir, max_height=max_height or 1080,
                              subs=subs, test=test)
    opts = dict(opts)
    opts["noprogress"] = True            # Fortschritt kommt in die GUI
    opts["progress_hooks"] = [hook]
    opts["logger"] = _GuiLogger(q)
    # Frozen (exe): gebündeltes ffmpeg nutzen
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", ""))
        if meipass and ((meipass / "ffmpeg.exe").exists()
                        or (meipass / "ffmpeg").exists()):
            opts["ffmpeg_location"] = str(meipass)
    return url, opts, info


def run_download(source, token, out_dir, max_height, subs, test, q, hook):
    url, opts, info = prepare(source, token, out_dir, max_height, subs,
                              test, q, hook)
    ytdlp = get_source_module(source).get_ytdlp()
    with ytdlp.YoutubeDL(opts) as ydl:
        if info is not None:      # Netzkino: eigene Metadaten nutzen
            ydl.process_ie_result(info, download=True)
        else:
            ydl.download([url])


# --------------------------------------------------------------- Selftest
def selftest():
    print("[selftest] Module laden ...")
    for src in SOURCES:
        get_source_module(src)
    print("[selftest] URL-Erkennung ...")
    assert detect_url_source(
        "https://www.arte.tv/de/videos/133122-000-A/dark-water/")[0] == "ARTE"
    assert detect_url_source(
        "https://www.zdf.de/filme/sterben-100.html")[0] == "ZDF"
    assert detect_url_source(
        "https://www.ardmediathek.de/video/x/y")[0] == "ARD"
    assert detect_url_source(
        "https://www.netzkino.de/details/man-on-the-moon")[0] == "Netzkino"
    assert detect_url_source(
        "https://www.3sat.de/film/spielfilm/sterben-104.html")[0] == "3sat"
    assert detect_url_source("https://youtu.be/vSFdwSjRrw0")[0] == "YouTube"
    assert detect_url_source(
        "https://www.youtube.com/watch?v=vSFdwSjRrw0")[0] == "YouTube"
    assert detect_url_source(
        "https://www.youtube.com/shorts/vSFdwSjRrw0")[0] == "YouTube"
    print("  alle 6 Quellen erkannt")
    for src, q_ in (("ARTE", "dark water"), ("Netzkino", "mondmann"),
                    ("3sat", "sterben"), ("ARD", "sterben"),
                    ("ZDF", "sterben"), ("YouTube", "anunnaki")):
        try:
            r = do_search(src, q_)
        except Exception as e:  # noqa: BLE001
            print(f"[selftest] {src}: Suche fehlgeschlagen ({e}) — "
                  "möglich: Geo-/Bot-Restriction im CI-Netz, übersprungen")
            continue
        if not r:
            print(f"[selftest] {src}: 0 Treffer (möglich: Geo-Restriction "
                  "im CI-Netz) – übersprungen")
            continue
        print(f"[selftest] {src}: {len(r)} Treffer, erster: {r[0][0][:60]}")
    print("[selftest] Auflösungs-Extraktion ...")
    fake = {"formats": [
        {"height": 1080, "vcodec": "h264", "tbr": 6628},
        {"height": 720, "vcodec": "h264", "tbr": 2648},
        {"height": 1080, "vcodec": "h264", "tbr": 5350},
        {"vcodec": "none", "tbr": 128},
    ]}
    res = resolutions_from_info(fake)
    assert res[0] == ("Maximal", 1080)
    assert len([x for x in res if x[1] == 1080]) == 2
    print(f"  {res}")
    print("[selftest] Filter ...")
    rows = [
        {"source": "ARTE", "label": "Film A", "duration_min": 96},
        {"source": "3sat", "label": "Clip B", "duration_min": 5},
        {"source": "ARD", "label": "Doku C", "duration_min": 30},
        {"source": "Netzkino", "label": "Spielfilm D", "duration_min": None},
    ]
    out, hidden = apply_filters(rows, "Alle", "Alle")
    assert len(out) == 4 and hidden == 0
    out, hidden = apply_filters(rows, "Netzkino", "Alle")
    assert len(out) == 1
    out, hidden = apply_filters(rows, "Alle", "Filme (ab 45 Min)")
    assert [r["label"] for r in out] == ["Film A", "Spielfilm D"]
    assert hidden == 0          # Netzkino ohne Laufzeit zählt als Spielfilm
    out, hidden = apply_filters(rows, "Alle", "Kurz (unter 15 Min)")
    assert [r["label"] for r in out] == ["Clip B"]
    print("  Anbieter- und Längenfilter OK")
    print("[selftest] Download-Optionen bauen ...")
    null_hook = lambda d: None  # noqa: E731
    url, opts, info = prepare("ARTE", "133122-000-A", Path("/tmp"), 1080,
                              "auto", False, queue.Queue(), null_hook)
    assert "mkv" in str(opts.get("merge_output_format")).lower()
    print(f"  arte: merge={opts['merge_output_format']}")
    for src, url_, label in (
            ("ARD", "https://www.ardmediathek.de/video/puzzle/sterben/br/"
                    "Y3JpZDovL2JyLmRlL2Jyb2FkY2FzdC9GMjAyNVdPMDA4OTE0QTA",
             "ard"),
            ("ZDF", "https://www.zdf.de/filme/sterben-100.html", "zdf"),
            ("3sat", "https://www.3sat.de/film/spielfilm/sterben-104.html",
             "3sat")):
        url, opts, info = prepare(src, url_, Path("/tmp"), 1080, "auto",
                                  False, queue.Queue(), null_hook)
        keys = [p["key"] for p in opts["postprocessors"]]
        assert "FFmpegVideoRemuxer" in keys
        print(f"  {label}: pps={keys}")
    try:
        url, opts, info = prepare("Netzkino", "man-on-the-moon", Path("/tmp"),
                                  1080, "keine", False, queue.Queue(),
                                  null_hook)
        assert info and info["title"]
        print(f"  netzkino: title={info['title']}")
    except RuntimeError as e:
        print(f"  netzkino: {e} (Geo-Restriction im CI-Netz möglich) "
              "– übersprungen")
    url, opts, info = prepare("YouTube", "https://www.youtube.com/watch?v="
                              "vSFdwSjRrw0", Path("/tmp"), None, "auto",
                              False, queue.Queue(), null_hook)
    keys = [p["key"] for p in opts["postprocessors"]]
    assert "FFmpegVideoRemuxer" in keys
    assert opts["writeautomaticsub"] is True
    print(f"  youtube: pps={keys} | auto-subs an")
    print("[selftest] OK")


# --------------------------------------------------------------- GUI
def run_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    root = tk.Tk()
    root.title("Mediathek-Suite — ARTE · Netzkino · 3sat · ARD · ZDF · YouTube")
    root.geometry("1000x780")
    root.minsize(880, 660)

    q = queue.Queue()
    default_out = Path.home() / "Videos" / "Mediathek"
    settings = {}
    if SETTINGS_FILE.exists():
        try:
            settings = json.loads(SETTINGS_FILE.read_text())
        except Exception:
            settings = {}
    out_dir_var = tk.StringVar(value=settings.get("out_dir",
                                                  str(default_out)))
    subs_var = tk.StringVar(value="Automatisch")
    res_var = tk.StringVar()
    src_filter = tk.StringVar(value="Alle")
    len_filter = tk.StringVar(value="Alle")
    all_rows = []          # alle Treffer: {source, label, dur_text, token, duration_min}

    # ---- Kopf: Zielordner
    top = ttk.Frame(root, padding=8)
    top.pack(fill="x")
    ttk.Label(top, text="Zielordner:").pack(side="left")
    ttk.Entry(top, textvariable=out_dir_var).pack(
        side="left", fill="x", expand=True, padx=4)
    ttk.Button(top, text="Auswählen …", command=lambda: out_dir_var.set(
        filedialog.askdirectory() or out_dir_var.get())).pack(side="left")

    # ---- Globale Suche
    srow = ttk.Frame(root, padding=(8, 2, 8, 4))
    srow.pack(fill="x")
    ttk.Label(srow, text="Suche (alle Quellen) oder URL:").pack(side="left")
    query_var = tk.StringVar()
    e = ttk.Entry(srow, textvariable=query_var)
    e.pack(side="left", fill="x", expand=True, padx=4)
    e.bind("<Return>", lambda _e: on_search())
    ttk.Button(srow, text="Suchen", command=lambda: on_search()
               ).pack(side="right")

    # ---- Filter
    frow = ttk.Frame(root, padding=(8, 0, 8, 2))
    frow.pack(fill="x")
    ttk.Label(frow, text="Anbieter:").pack(side="left")
    src_box = ttk.Combobox(frow, textvariable=src_filter, state="readonly",
                           width=10, values=("Alle",) + SOURCES)
    src_box.current(0)
    src_box.pack(side="left", padx=4)
    ttk.Label(frow, text="Länge:").pack(side="left", padx=(12, 2))
    len_box = ttk.Combobox(frow, textvariable=len_filter, state="readonly",
                           width=20, values=tuple(LENGTH_FILTERS))
    len_box.current(0)
    len_box.pack(side="left", padx=4)
    src_box.bind("<<ComboboxSelected>>", lambda _e: redraw_results())
    len_box.bind("<<ComboboxSelected>>", lambda _e: redraw_results())
    ttk.Label(frow, text="Ziel: MKV · Untertitel/Metadaten/Cover eingebettet"
              ).pack(side="right")

    # ---- Trefferliste
    tree = ttk.Treeview(root, columns=("quelle", "titel", "laenge", "token"),
                        show="headings", height=8)
    tree.heading("quelle", text="Quelle")
    tree.heading("titel", text="Titel")
    tree.heading("laenge", text="Länge")
    tree.heading("token", text="ID / URL")
    tree.column("quelle", width=85, anchor="center")
    tree.column("titel", width=430)
    tree.column("laenge", width=110, anchor="center")
    tree.column("token", width=180)
    tree.pack(fill="both", expand=True, padx=8, pady=2)

    # ---- Aktionen
    arow = ttk.Frame(root, padding=(8, 2))
    arow.pack(fill="x")
    ttk.Button(arow, text="Details laden",
               command=lambda: on_details()).pack(side="left")
    ttk.Button(arow, text="Herunterladen (MKV)",
               command=lambda: on_download()).pack(side="left", padx=6)
    ttk.Label(arow, text="Auflösung:").pack(side="left", padx=(12, 2))
    res_box = ttk.Combobox(arow, textvariable=res_var, state="readonly",
                           width=34, values=("Maximal",))
    res_box.current(0)
    res_box.pack(side="left")
    ttk.Label(arow, text="Untertitel:").pack(side="left", padx=(12, 2))
    ttk.Combobox(arow, textvariable=subs_var, state="readonly", width=13,
                 values=("Automatisch", "Alle", "Keine")).pack(side="left")

    # ---- Details-Fenster
    dframe = ttk.LabelFrame(root, text="Details zum ausgewählten Titel",
                            padding=4)
    dframe.pack(fill="both", padx=8, pady=4)
    details = tk.Text(dframe, height=8, state="disabled",
                      font=("TkFixedFont", 9), wrap="word")
    details.pack(fill="both", expand=True)

    # ---- Fuß: Fortschritt + Log
    foot = ttk.Frame(root, padding=8)
    foot.pack(fill="x", side="bottom")
    progress = ttk.Progressbar(foot, mode="determinate", maximum=1.0)
    progress.pack(fill="x")
    status_var = tk.StringVar(
        value="Bereit. Sucht gleichzeitig in: ARTE · Netzkino · 3sat · "
              "ARD · ZDF · YouTube.")
    ttk.Label(foot, textvariable=status_var, anchor="w").pack(
        fill="x", pady=(4, 2))
    log = tk.Text(foot, height=5, state="disabled",
                  font=("TkFixedFont", 9))
    log.pack(fill="both", expand=True)

    def append_log(text):
        log.configure(state="normal")
        log.insert("end", text.rstrip() + "\n")
        log.see("end")
        log.configure(state="disabled")

    def selected():
        sel = tree.selection()
        if not sel:
            return None
        v = tree.item(sel[0])["values"]
        return {"source": str(v[0]), "token": str(v[3])}

    def on_search():
        text = query_var.get().strip()
        if not text:
            q.put(("error", "Bitte Suchbegriff oder URL eingeben."))
            return
        all_rows.clear()
        if re.match(r"https?://", text):
            source, token = detect_url_source(text)
            if not source:
                q.put(("error", "URL gehört zu keiner unterstützten Quelle "
                                "(arte.tv, netzkino.de, 3sat.de, "
                                "ardmediathek.de, zdf.de)."))
                return
            all_rows.append({"source": source, "label": "URL direkt "
                             "eingegeben", "dur_text": "Länge ?",
                             "token": token, "duration_min": None})
            redraw_results()
            q.put(("status", f"{source}: URL erkannt — 'Details laden' oder "
                             "direkt 'Herunterladen'."))
            return
        q.put(("status", "Suche in 6 Quellen gleichzeitig ..."))
        for src in SOURCES:
            def work(s=src):
                try:
                    q.put(("results_src", s, do_search(s, text)))
                except Exception as err:  # noqa: BLE001
                    q.put(("source_error", s, str(err)))
            threading.Thread(target=work, daemon=True).start()

    def on_details():
        sel = selected()
        if not sel:
            q.put(("error", "Bitte zuerst ein Suchergebnis auswählen."))
            return
        source, token = sel["source"], sel["token"]
        q.put(("status", f"[{source}] Lade Details ..."))
        details.configure(state="normal")
        details.delete("1.0", "end")
        details.configure(state="disabled")
        res_box.configure(values=("Maximal",))
        res_box.current(0)

        def work():
            try:
                lines, res = get_details(source, token, q)
                q.put(("details", source, lines, res))
            except SystemExit as err:
                q.put(("error", f"Abgebrochen: {err}"))
            except Exception as err:  # noqa: BLE001
                q.put(("error", f"Details fehlgeschlagen: {err}"))
        threading.Thread(target=work, daemon=True).start()

    def on_download():
        sel = selected()
        if not sel:
            q.put(("error", "Bitte zuerst ein Suchergebnis auswählen."))
            return
        source, token = sel["source"], sel["token"]
        out_root = Path(out_dir_var.get().strip() or
                        str(default_out)).expanduser()
        out_dir = out_root / source
        out_dir.mkdir(parents=True, exist_ok=True)
        max_h = 1080
        m = re.search(r"(\d{3,4})p", res_var.get() or "")
        if m:
            max_h = int(m.group(1))
        subs = {"alle": "all", "keine": "none"}.get(
            subs_var.get().lower(), "auto")
        if source == "Netzkino":
            subs = "none"       # Netzkino liefert keine Untertitel
        q.put(("status", f"[{source}] Download gestartet "
                         f"({res_var.get() or 'Maximal'})"))
        q.put(("progress", 0.0))

        def hook(d):
            if d["status"] == "downloading":
                total = d.get("total_bytes") or \
                        d.get("total_bytes_estimate") or 0
                done = d.get("downloaded_bytes") or 0
                if total:
                    q.put(("progress", done / total))
            elif d["status"] == "finished":
                q.put(("progress", 1.0))
            elif d["status"] == "postprocessing":
                q.put(("status", "Nachbearbeitung (MKV, Untertitel, Cover) …"))

        def work():
            try:
                run_download(source, token, out_dir, max_h, subs, False,
                             q, hook)
                q.put(("progress", 1.0))
                q.put(("status", f"[{source}] Fertig → {out_dir}"))
            except SystemExit as err:    # sys.exit-Fehler der Module
                q.put(("error", f"Abgebrochen: {err}"))
            except Exception as err:  # noqa: BLE001
                low = str(err).lower()
                if "subtitle" in low and subs != "keine":
                    q.put(("log", "[i] Untertitel-Download blockiert "
                                  "(Rate-Limit) — wiederhole ohne "
                                  "Untertitel ..."))
                    try:
                        run_download(source, token, out_dir, max_h,
                                     "keine", False, q, hook)
                        q.put(("progress", 1.0))
                        q.put(("status", f"[{source}] Fertig (ohne "
                                         f"Untertitel) → {out_dir}"))
                    except Exception as err2:  # noqa: BLE001
                        q.put(("error", f"Download fehlgeschlagen: {err2}"))
                else:
                    q.put(("error", f"Download fehlgeschlagen: {err}"))
        threading.Thread(target=work, daemon=True).start()

    def redraw_results():
        tree.delete(*tree.get_children())
        out, hidden = apply_filters(all_rows, src_filter.get(),
                                    len_filter.get())
        for r in out:
            tree.insert("", "end", values=(r["source"], r["label"],
                                           r["dur_text"], r["token"]))
        if hidden:
            q.put(("status", f"{len(out)} von {len(all_rows)} Treffern "
                             f"angezeigt ({hidden} ohne Längenangabe "
                             "ausgeblendet)."))
        else:
            q.put(("status", f"{len(out)} von {len(all_rows)} Treffern "
                             "angezeigt."))

    def pump():
        try:
            while True:
                item = q.get_nowait()
                kind = item[0]
                if kind == "results_src":
                    _, source, results = item
                    for label, token, url, dmin in results:
                        all_rows.append({
                            "source": source, "label": label,
                            "dur_text": (f"{dmin} Min." if dmin
                                         else "Länge ?"),
                            "token": token, "duration_min": dmin,
                        })
                    redraw_results()
                    append_log(f"[{source}] {len(results)} Treffer")
                elif kind == "source_error":
                    _, source, err = item
                    append_log(f"[{source}] Suche fehlgeschlagen: {err}")
                    q.put(("status", f"{source}: Suche fehlgeschlagen "
                                     f"(siehe Log)."))
                elif kind == "details":
                    _, source, lines, res = item
                    details.configure(state="normal")
                    details.delete("1.0", "end")
                    details.insert("end", "\n".join(lines))
                    details.configure(state="disabled")
                    labels = [r[0] for r in res]
                    res_box.configure(values=labels)
                    res_box.current(0)
                    q.put(("status", f"[{source}] Details geladen — "
                                     "Auflösung wählbar."))
                elif kind == "status":
                    status_var.set(item[1])
                elif kind == "progress":
                    progress["value"] = max(0.0, min(1.0, float(item[1])))
                elif kind == "log":
                    append_log(item[1])
                elif kind == "error":
                    status_var.set(item[1])
                    append_log("[FEHLER] " + item[1])
        except queue.Empty:
            pass
        root.after(150, pump)

    def on_close():
        try:
            SETTINGS_FILE.write_text(
                json.dumps({"out_dir": out_dir_var.get()}))
        except Exception:
            pass
        root.destroy()

    pump()
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


def main():
    args = sys.argv[1:]
    if "--selftest" in args:
        selftest()
        return
    try:
        run_gui()
    except Exception:  # noqa: BLE001
        err = traceback.format_exc()
        try:
            (APP_DIR / "mediathek-suite-crash.log").write_text(err)
        except Exception:
            pass
        try:
            import tkinter as tk
            from tkinter import messagebox
            r = tk.Tk()
            r.withdraw()
            messagebox.showerror(
                "Mediathek-Suite", "Unerwarteter Fehler:\n\n" + err[-1200:])
        except Exception:
            print(err, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
