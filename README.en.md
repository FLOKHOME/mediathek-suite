# mediathek-suite

*[Deutsch](README.md) | English*

**One search across all mediatheks — one download button.**
Sources: **ARTE · Netzkino · 3sat · ARD Mediathek · ZDF Mediathek · ORF · YouTube**.

**ORF note:** ORF search and most ORF content require an Austrian IP. The
suite checks before every ORF download whether the ORF serves real video
or only the geo placeholder ("Austria only") at the current IP and aborts
cleanly instead of saving placeholder files. With an AT IP (e.g. VPN),
search and download work. ORF content marked worldwide (e.g. older
Vorstadtweiber episodes) also works from outside Austria.
Downloads always as **MKV** in the selected quality (subtitles, metadata
and cover art embedded where available).

The command-line modules ([arte-dl](https://github.com/FLOKHOME/arte-dl),
[netzkino-dl](https://github.com/FLOKHOME/netzkino-dl),
[3sat-dl](https://github.com/FLOKHOME/3sat-dl)) are embedded as modules —
plus ard_dl, zdf_dl and yt_dl.

## Windows EXE (no installation)

Grab the latest `mediathek-suite-windows.exe` from the
[Releases](https://github.com/FLOKHOME/mediathek-suite/releases) page —
**yt-dlp, ffprobe and ffmpeg are bundled** (no Python, no pip).

> Note: The EXE is unsigned — Windows SmartScreen will warn on first start
> ("Unknown publisher"). Click "More info → Run anyway".
> `mediathek-suite-linux` for Linux; macOS from source.

## Usage

1. **Enter a search term or URL** → "Suchen" searches **all 6 sources at
   once**; results appear as they arrive, tagged with their source.
   A pasted URL is auto-matched to its source (arte.tv, netzkino.de,
   3sat.de, ardmediathek.de, zdf.de).
2. Select a hit → **Details laden**: a readable panel with title,
   description, duration, audio tracks, subtitles — and the list of
   **actually available resolutions** (with bitrate).
3. **Choose resolution, format (MKV/MP4) and a size limit** →
   download. MP4 embeds subtitles as mov_text, MKV as SRT.

**Size limit:** ARTE/ORF automatically pick a lower quality
ladder to fit the limit; for YouTube and the other
mediatheks the download aborts with a clear message when the
file would exceed the limit.

**ARTE preview trap:** If a film is not yet published as a
full VOD, the player only serves a short excerpt (e.g. 80 s
of a 99 min film). The suite detects this via durations and
aborts cleanly instead of saving a broken file.
   Target folder at the top (persisted, default:
   `Videos\Mediathek\<source>`), subtitles automatic/all/none.

## Features per source

| Source | Search | Special |
|---|---|---|
| ARTE | emac-v4 API | German audio, subtitles de/all |
| Netzkino | GraphQL | bitrate probing (max), DRM/clear-asset check, size estimate |
| 3sat | server-rendered page | embedded de subtitles, remaining time |
| ARD | server-rendered search page | HLS up to 1080p |
| ZDF | GraphQL (persisted query, token from page) | 1080p/6.6 Mbit, de subtitles |
| YouTube | ytsearch (yt-dlp) | up to 4K, auto subtitles de; note: YouTube changes may require a yt-dlp update (rebuild) |

## Command line

The five modules remain usable as CLIs (each `search`, `info`, `dl`);
suite check:

```bash
python mediathek_gui.py --selftest      # headless check of all searches
```

## Requirements (script usage only)

- Python 3 with tkinter, **ffmpeg** on PATH
- yt-dlp is auto-fetched from PyPI

## Disclaimer

Only use for content the broadcasters offer legally and for free.
Private use only — do not redistribute downloaded movies.
