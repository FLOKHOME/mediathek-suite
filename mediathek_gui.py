#!/usr/bin/env python3
"""
mediathek_gui.py — Mediathek-Suite: ARTE + Netzkino + 3sat in einer GUI
=======================================================================

Eine Oberfläche für die drei Downloader (arte-dl, netzkino-dl, 3sat-dl):
Suche pro Quelle, Auswahl, Download in maximaler Qualität als MKV
(eingebettete Untertitel/Metadaten/Cover, wo vorhanden).

Modus:
  Standard        GUI starten (tkinter)
  --selftest      Headless-Prüfung: Module, Suchen, Optionsbau (ohne Tk)

Die drei Downloader liegen als Module (arte_dl, netzkino_dl, 3sat_dl) neben
dieser Datei und werden direkt benutzt. yt-dlp und ffmpeg werden für die
EXE mitgebaut (siehe .github/workflows).
"""
import importlib
import json
import queue
import sys
import threading
import traceback
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent if not getattr(sys, "frozen", False) \
    else Path(sys.executable).resolve().parent
sys.path.insert(0, str(APP_DIR))

SETTINGS_FILE = Path.home() / ".mediathek-suite.json"
SOURCES = ("ARTE", "Netzkino", "3sat")


# --------------------------------------------------------------- Engine
def get_source_module(name):
    return importlib.import_module(
        {"ARTE": "arte_dl", "Netzkino": "netzkino_dl", "3sat": "3sat_dl"}[name])


def do_search(source, query):
    """Sucht in der Quelle; liefert (label, token, url)-Tupel."""
    mod = get_source_module(source)
    out = []
    if source == "ARTE":
        for r in mod.search(query):
            label = r["title"]
            if r["subtitle"]:
                label += f" – {r['subtitle']}"
            if r["kind"]:
                label += f"  [{r['kind']}]"
            if r["desc"]:
                label += f"  |  {r['desc']}"
            out.append((label, r["id"], r["url"]))
    elif source == "Netzkino":
        for r in mod.search(query):
            label = r.get("title") or r.get("originalTitle") or "?"
            if r.get("originalTitle") and r["originalTitle"] != r.get("title"):
                label += f" [orig: {r['originalTitle']}]"
            if r.get("productionYear"):
                label += f" ({r['productionYear']})"
            if r.get("runtimeInSeconds"):
                label += f" {round(r['runtimeInSeconds']/60)} Min"
            flags = []
            if r.get("hasDrm"):
                flags.append("DRM-Flag")
            if not r.get("isActive"):
                flags.append("inaktiv")
            if flags:
                label += "  [" + ",".join(flags) + "]"
            out.append((label, r["slug"], r["slug"]))
    else:  # 3sat
        for r in mod.search(query):
            label = r["title"]
            if r["category"]:
                label += f"  [{r['category']}]"
            if r["availability"]:
                label += f"  ({r['availability']})"
            out.append((label, r["url"], r["url"]))
    return out


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


def prepare_arte(token, out_dir, max_height, subs, test, q, hook):
    mod = get_source_module("ARTE")
    subs_map = {"auto": "de", "alle": "all", "keine": None}
    kw_sub = subs_map.get(subs, "de")
    _pid, url, _od, opts, _args = mod.build_spec(
        token, out_dir=out_dir, max_height=max_height, audio="de",
        subtitles=kw_sub, quality="best", test=test)
    return url, opts


def prepare_netzkino(token, out_dir, max_height, subs, test, q, hook):
    """Netzkino: DRM-/Klartext-Prüfung + eigener Info-Dict für saubere Titel."""
    mod = get_source_module("Netzkino")
    mv = mod.fetch_movie(token)
    if mv.get("hasDrm"):
        q.put(("log", "[*] DRM-Flag in der Lizenz – prüfe Klartext-Stream ..."))
    url, bitrate = mod.best_stream_url(
        (mv.get("videoSource") or {}).get("pmdUrl"), probe=True)
    if not mod.verify_clear_mp4(url):
        raise RuntimeError(
            "Kein unverschlüsselter Stream verfügbar (DRM geschützt).")
    if mv.get("hasDrm"):
        q.put(("log", "[i] Klartext-MP4 bestätigt."))
    info_dict = mod.build_movie_info(mv, token, url, bitrate)
    opts = mod.build_opts(out_dir, test)
    return url, opts, info_dict


def prepare_3sat(url, out_dir, max_height, subs, test, q, hook):
    mod = get_source_module("3sat")
    opts = mod.build_opts(out_dir, max_height=max_height, subs=subs, test=test)
    return url, opts


def build_job(source, token, out_dir, max_height, subs, test, q, hook):
    """Baut (url, opts, [info_dict]) für die yt-dlp-Ausführung."""
    ytdlp = get_source_module(source)
    if source == "ARTE":
        url, opts = prepare_arte(token, out_dir, max_height, subs, test, q, hook)
        info = None
    elif source == "Netzkino":
        url, opts, info = prepare_netzkino(token, out_dir, max_height, subs,
                                           test, q, hook)
    else:
        url, opts = prepare_3sat(token, out_dir, max_height, subs, test, q, hook)
        info = None
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
    url, opts, info = build_job(source, token, out_dir, max_height, subs,
                                test, q, hook)
    ytdlp = get_source_module(source)
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
    for src, q_, expect in (("ARTE", "dark water", "Dark Water"),
                            ("Netzkino", "mondmann", "Mondmann"),
                            ("3sat", "sterben", "Sterben")):
        r = do_search(src, q_)
        assert any(expect in x[0] for x in r), f"{src}-Suche leer"
        print(f"[selftest] {src}: {len(r)} Treffer, erster: {r[0][0][:60]}")
    print("[selftest] Download-Optionen bauen ...")
    null_hook = lambda d: None  # noqa: E731
    url, opts, info = build_job("ARTE", "133122-000-A", Path("/tmp"), 1080,
                                "auto", False, queue.Queue(), null_hook)
    assert "mkv" in str(opts.get("merge_output_format")).lower()
    print(f"  arte: url={url[:55]}... merge={opts['merge_output_format']}")
    url, opts, info = build_job("Netzkino", "man-on-the-moon", Path("/tmp"),
                                1080, "keine", False, queue.Queue(), null_hook)
    assert info and info["title"]
    keys = [p["key"] for p in opts["postprocessors"]]
    assert "FFmpegVideoRemuxer" in keys
    print(f"  netzkino: title={info['title']} pps={keys}")
    url, opts, info = build_job(
        "3sat", "https://www.3sat.de/film/spielfilm/sterben-104.html",
        Path("/tmp"), 1080, "auto", False, queue.Queue(), null_hook)
    keys = [p["key"] for p in opts["postprocessors"]]
    assert "FFmpegVideoRemuxer" in keys and "FFmpegEmbedSubtitle" in keys
    print(f"  3sat: pps={keys}")
    print("[selftest] OK")


# --------------------------------------------------------------- GUI
def run_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    root = tk.Tk()
    root.title("Mediathek-Suite — ARTE · Netzkino · 3sat")
    root.geometry("920x700")
    root.minsize(800, 580)

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
    qual_var = tk.StringVar(value="Max")
    subs_var = tk.StringVar(value="Automatisch")

    # ---- Kopf
    top = ttk.Frame(root, padding=8)
    top.pack(fill="x")
    ttk.Label(top, text="Zielordner:").pack(side="left")
    ttk.Entry(top, textvariable=out_dir_var).pack(
        side="left", fill="x", expand=True, padx=4)
    ttk.Button(top, text="Auswählen …", command=lambda: out_dir_var.set(
        filedialog.askdirectory() or out_dir_var.get())).pack(side="left")
    optrow = ttk.Frame(root, padding=(8, 0, 8, 4))
    optrow.pack(fill="x")
    ttk.Label(optrow, text="Qualität:").pack(side="left")
    ttk.Combobox(optrow, textvariable=qual_var, state="readonly", width=7,
                 values=("Max", "1080p", "720p")).pack(side="left", padx=4)
    ttk.Label(optrow, text="Untertitel:").pack(side="left", padx=(16, 0))
    ttk.Combobox(optrow, textvariable=subs_var, state="readonly", width=13,
                 values=("Automatisch", "Alle", "Keine")
                 ).pack(side="left", padx=4)
    ttk.Label(optrow, text="Ziel: MKV · max. Qualität").pack(
        side="left", padx=(16, 0))

    # ---- Tabs
    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True, padx=8, pady=4)

    def selected(source):
        tree = tabs[source]["tree"]
        sel = tree.selection()
        return tree.item(sel[0])["values"] if sel else None

    def on_search(source):
        query = tabs[source]["entry"].get().strip()
        if not query:
            q.put(("error", "Bitte Suchbegriff eingeben."))
            return
        q.put(("status", f"Suche in {source} ..."))

        def work():
            try:
                q.put(("results", source, do_search(source, query)))
            except Exception as e:  # noqa: BLE001
                q.put(("error", f"{source}-Suche fehlgeschlagen: {e}"))
        threading.Thread(target=work, daemon=True).start()

    def on_info(source):
        sel = selected(source)
        if not sel:
            q.put(("error", "Bitte zuerst ein Suchergebnis auswählen."))
            return
        q.put(("status", "Lade Infos ..."))

        def work():
            try:
                import io
                from contextlib import redirect_stdout
                buf = io.StringIO()
                with redirect_stdout(buf):
                    get_source_module(source).info(sel[1])
                for line in (buf.getvalue() or "(keine Ausgabe)").splitlines():
                    q.put(("log", line))
                q.put(("status", "Info geladen."))
            except Exception as e:  # noqa: BLE001
                q.put(("error", f"Info fehlgeschlagen: {e}"))
        threading.Thread(target=work, daemon=True).start()

    def on_download(source):
        sel = selected(source)
        if not sel:
            q.put(("error", "Bitte zuerst ein Suchergebnis auswählen."))
            return
        token = sel[1]
        out_root = Path(out_dir_var.get().strip() or
                        str(default_out)).expanduser()
        out_dir = out_root / source
        out_dir.mkdir(parents=True, exist_ok=True)
        max_h = {"720p": 720}.get(qual_var.get(), 1080)
        subs = {"alle": "all", "keine": "none"}.get(
            subs_var.get().lower(), "auto")
        q.put(("status", f"[{source}] Download gestartet: {token}"))
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
            except SystemExit as e:          # sys.exit-Fehler der Module
                q.put(("error", f"Abgebrochen: {e}"))
            except Exception as e:  # noqa: BLE001
                q.put(("error", f"Download fehlgeschlagen: {e}"))
        threading.Thread(target=work, daemon=True).start()

    # ---- Tab-Inhalt
    tabs = {}
    for src in SOURCES:
        frame = ttk.Frame(nb, padding=6)
        nb.add(frame, text=src)
        row = ttk.Frame(frame)
        row.pack(fill="x")
        entry_var = tk.StringVar()
        ttk.Entry(row, textvariable=entry_var).pack(
            side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(row, text="Suchen",
                   command=lambda s=src: on_search(s)).pack(side="right")
        tree = ttk.Treeview(frame, columns=("label", "token"),
                            show="headings", height=10)
        tree.heading("label", text="Titel")
        tree.heading("token", text="ID / URL")
        tree.column("label", width=460)
        tree.column("token", width=280)
        tree.pack(fill="both", expand=True, pady=4)
        row2 = ttk.Frame(frame)
        row2.pack(fill="x")
        ttk.Button(row2, text="Info anzeigen",
                   command=lambda s=src: on_info(s)).pack(side="left")
        ttk.Button(row2, text="Herunterladen (MKV · max. Qualität)",
                   command=lambda s=src: on_download(s)).pack(side="left",
                                                              padx=6)
        tabs[src] = {"entry": entry_var, "tree": tree, "frame": frame}

    # ---- Fuß: Fortschritt + Log
    foot = ttk.Frame(root, padding=8)
    foot.pack(fill="x", side="bottom")
    progress = ttk.Progressbar(foot, mode="determinate", maximum=1.0)
    progress.pack(fill="x")
    status_var = tk.StringVar(value="Bereit.")
    ttk.Label(foot, textvariable=status_var, anchor="w").pack(
        fill="x", pady=(4, 2))
    log = tk.Text(foot, height=8, state="disabled", font=("TkFixedFont", 9))
    log.pack(fill="both", expand=True)

    def append_log(text):
        log.configure(state="normal")
        log.insert("end", text.rstrip() + "\n")
        log.see("end")
        log.configure(state="disabled")

    def pump():
        try:
            while True:
                item = q.get_nowait()
                kind = item[0]
                if kind == "results":
                    _, source, results = item
                    tree = tabs[source]["tree"]
                    tree.delete(*tree.get_children())
                    for label, token, url in results:
                        tree.insert("", "end", values=(label, token))
                    status_var.set(f"{source}: {len(results)} Treffer.")
                    append_log(f"[{source}] {len(results)} Treffer für "
                               f"{tabs[source]['entry'].get()!r}")
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
