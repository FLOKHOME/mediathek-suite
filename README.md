# mediathek-suite

*Deutsch | [English](README.en.md)*

**Eine Suche über alle Mediatheken — ein Download-Knopf.**
Quellen: **ARTE · Netzkino · 3sat · ARD Mediathek · ZDF Mediathek**.
Download immer in der gewählten Qualität als **MKV** (Untertitel,
Metadaten und Cover werden eingebettet, wo vorhanden).

Die Kommandozeilen-Module ([arte-dl](https://github.com/FLOKHOME/arte-dl),
[netzkino-dl](https://github.com/FLOKHOME/netzkino-dl),
[3sat-dl](https://github.com/FLOKHOME/3sat-dl)) stecken als Module in
dieser Suite — dazu ard_dl und zdf_dl für ARD und ZDF.

## Windows-EXE (ohne Installation)

Von [Releases](https://github.com/FLOKHOME/mediathek-suite/releases) die
aktuelle `mediathek-suite-windows.exe` laden und starten — **yt-dlp,
ffprobe und ffmpeg sind eingebaut** (kein Python, kein pip).

> Hinweis: Die EXE ist nicht signiert — beim ersten Start meldet Windows
> SmartScreen „Unbekannter Herausgeber". Über „Weitere Informationen →
> Trotzdem ausführen" starten. `mediathek-suite-linux` für Linux;
> macOS aus dem Quellcode.

## Bedienung

1. **Suchbegriff oder URL eingeben** → „Suchen" durchsucht **gleichzeitig
   alle 5 Quellen**; die Treffer erscheinen sofort, mit Quellen-Spalte.
   Eine eingefügte URL wird automatisch der passenden Quelle zugeordnet
   (arte.tv, netzkino.de, 3sat.de, ardmediathek.de, zdf.de).
2. Treffer markieren → **Details laden**: lesbares Fenster mit Titel,
   Beschreibung, Dauer, Tonspuren, Untertiteln — und der Liste der
   **tatsächlich verfügbaren Auflösungen** (mit Bitrate).
3. **Auflösung wählen** (beste ist vorausgewählt) → **Herunterladen**.
   Zielordner oben wählbar (wird gespeichert, Standard:
   `Videos\Mediathek\<Quelle>`), Untertitel automatisch/alle/keine.

## Features pro Quelle

| Quelle | Suche | Besonderheit |
|---|---|---|
| ARTE | emac-v4-API | Tonspur Deutsch, Untertitel de/all |
| Netzkino | GraphQL | Bitraten-Probe (max.), DRM-/Klartext-Prüfung, Größenvorhersage |
| 3sat | Server-rendered Seite | de-Untertitel eingebettet, Restzeit |
| ARD | Server-rendered Suchseite | HLS bis 1080p |
| ZDF | GraphQL (persistierte Query, Token aus der Seite) | 1080p/6,6 Mbit, de-Untertitel |

## Kommandozeile

Die CLIs der fünf Module bleiben einzeln nutzbar (jeweils
`search "begriff"`, `info <url>`, `dl <url>`); Suite-Prüfung:

```bash
python mediathek_gui.py --selftest      # Headless-Prüfung aller Suchen
```

## Voraussetzungen (nur Skript-Nutzung)

- Python 3 mit tkinter, **ffmpeg** im PATH
- yt-dlp wird automatisch von PyPI geladen (`~/arte-dl/lib`)

## Haftungshinweis

Nur für Inhalte nutzen, die die Sender legal und kostenlos anbieten.
Private Nutzung — keine Weiterverbreitung der heruntergeladenen Filme.
