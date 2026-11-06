# mediathek-suite

*Deutsch | [English](README.en.md)*

**ARTE · Netzkino · 3sat in einem Programm:** suchen, auswählen,
herunterladen — immer **maximale Qualität als MKV** (Untertitel, Metadaten
und Cover werden eingebettet, wo vorhanden).

Die drei Kommandozeilen-Downloader ([arte-dl](https://github.com/FLOKHOME/arte-dl),
[netzkino-dl](https://github.com/FLOKHOME/netzkino-dl),
[3sat-dl](https://github.com/FLOKHOME/3sat-dl)) stecken als Module in
dieser Suite — die GUI ruft sie direkt auf.

## Windows-EXE (ohne Installation)

Von [Releases](https://github.com/FLOKHOME/mediathek-suite/releases) die
aktuelle `mediathek-suite-windows.exe` laden und starten — **yt-dlp,
ffprobe und ffmpeg sind eingebaut** (kein Python, kein pip).

> Hinweis: Die EXE ist nicht signiert — beim ersten Start meldet Windows
> SmartScreen „Unbekannter Herausgeber". Über „Weitere Informationen →
> Trotzdem ausführen" starten. `mediathek-suite-linux` für Linux;
> macOS aus dem Quellcode.

## Bedienung

1. **Tab** der Quelle wählen (ARTE / Netzkino / 3sat).
2. Suchbegriff eingeben → **Suchen**. Die Trefferliste zeigt Titel,
   Rubrik und Restzeit (3sat) bzw. DRM-Hinweise (Netzkino).
3. Treffer markieren → **Info anzeigen** (Formate, Sprachen, Untertitel)
   oder direkt **Herunterladen**.
4. Zielordner oben wählbar (wird gespeichert, Standard:
   `Videos\Mediathek\<Quelle>`); Qualität 720p/1080p/Max, Untertitel
   automatisch/alle/keine.

## Features pro Quelle

| Quelle | Suche | Besonderheit | Ziel |
|---|---|---|---|
| ARTE | API (emac v4) | Tonspur Deutsch, Untertitel de/all | MKV |
| Netzkino | GraphQL | Bitraten-Probe (max.), DRM-/Klartext-Prüfung | MKV |
| 3sat | Server-rendered Seite | de-Untertitel eingebettet, Restzeit | MKV |

## Kommandozeile

Die CLIs bleiben separat nutzbar:

```bash
python mediathek_gui.py --selftest      # Headless-Prüfung aller Suchen
```

## Wie es funktioniert

- Die GUI baut für jede Quelle dieselben yt-dlp-Optionen wie die CLIs
  (Format `res,tbr`-Sortierung, TV-Ton-Präferenz, MKV-Remux,
  `FFmpegVideoRemuxer`/`FFmpegSubtitlesConvertor`/`FFmpegEmbedSubtitle`/
  `FFmpegMetadata`/`EmbedThumbnail`) und führt den Download in einem
  Hintergrundthread mit Fortschrittsanzeige aus.
- Voraussetzungen (nur Skriptnutzung): Python 3 mit tkinter, ffmpeg im
  PATH; yt-dlp wird automatisch von PyPI geladen.

## Haftungshinweis

Nur für Inhalte nutzen, die die Sender legal und kostenlos anbieten.
Private Nutzung — keine Weiterverbreitung der heruntergeladenen Filme.
