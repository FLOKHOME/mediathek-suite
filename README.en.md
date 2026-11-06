# mediathek-suite

*[Deutsch](README.md) | English*

**ARTE · Netzkino · 3sat in one app:** search, select, download — always
**maximum quality as MKV** (subtitles, metadata and cover art embedded
where available).

The three command-line downloaders ([arte-dl](https://github.com/FLOKHOME/arte-dl),
[netzkino-dl](https://github.com/FLOKHOME/netzkino-dl),
[3sat-dl](https://github.com/FLOKHOME/3sat-dl)) are embedded as modules —
the GUI calls them directly.

## Windows EXE (no installation)

Grab the latest `mediathek-suite-windows.exe` from the
[Releases](https://github.com/FLOKHOME/mediathek-suite/releases) page —
**yt-dlp, ffprobe and ffmpeg are bundled** (no Python, no pip).

> Note: The EXE is unsigned — Windows SmartScreen will warn on first start
> ("Unknown publisher"). Click "More info → Run anyway".
> `mediathek-suite-linux` for Linux; macOS from source.

## Usage

1. Pick the **tab** of the source (ARTE / Netzkino / 3sat).
2. Enter a search term → **Suchen** (search). Results show titles,
   categories and remaining availability (3sat) / DRM flags (Netzkino).
3. Select a hit → **Info anzeigen** (formats, languages, subtitles) or
   **Herunterladen** (download) right away.
4. Choose the target folder at the top (persisted, default:
   `Videos\Mediathek\<source>`); quality 720p/1080p/Max, subtitles
   automatic/all/none.

## Features per source

| Source | Search | Special | Output |
|---|---|---|---|
| ARTE | API (emac v4) | German audio, subtitles de/all | MKV |
| Netzkino | GraphQL | bitrate probing (max), DRM/clear-asset check | MKV |
| 3sat | server-rendered page | embedded de subtitles, remaining time | MKV |

## Command line

The CLIs remain usable separately:

```bash
python mediathek_gui.py --selftest      # headless check of all searches
```

## How it works

- The GUI builds the same yt-dlp options as the CLIs (format sort
  `res,tbr`, TV-Ton preference, MKV remux,
  `FFmpegVideoRemuxer`/`FFmpegSubtitlesConvertor`/`FFmpegEmbedSubtitle`/
  `FFmpegMetadata`/`EmbedThumbnail`) and downloads in a background thread
  with a progress bar.
- Requirements (script usage only): Python 3 with tkinter, ffmpeg on
  PATH; yt-dlp is auto-fetched from PyPI.

## Disclaimer

Only use for content the broadcasters offer legally and for free.
Private use only — do not redistribute downloaded movies.
