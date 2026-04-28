# Architecture & Implementation Notes

## Source layout

```
src/
  download_from_csv.py   -- main CLI entry point
  ydl_helpers.py         -- yt-dlp interaction layer (metadata, format selection, download)
scripts/
  run.sh                 -- venv wrapper: activates .venv and invokes download_from_csv.py
```

## High-level flow

For each row in the CSV:

1. **Flat search** — run `yt-dlp --flat-playlist "ytsearch1:<track> <artist>"` to
   get the top YouTube video URL without triggering a full metadata fetch.
   This step is fast because `--flat-playlist` skips the YouTube player-client
   API calls that are otherwise needed to resolve video formats.

2. **Channel/playlist guard** — if the returned URL points to a channel or playlist
   (detected by path patterns: `/channel/`, `/c/`, `/@`, `/user/`, `/playlist?`,
   `/videos`, `/shorts`), the search is retried with a refined query
   (`<original query> official audio`) across the top 5 results.

3. **Full metadata fetch** — run `yt-dlp --dump-json <video_url>` with
   `--extractor-args youtube:player_client=android_vr` so only a single fast
   player client is used. The call is made via `subprocess.run(timeout=60)` to
   enforce a hard wall-clock cap and prevent indefinite hangs on age-gated content.

4. **Preflight checks** — validate duration, estimated filesize, and view count
   against the `--max-duration`, `--max-filesize`, and `--min-views` thresholds.
   Filesize is estimated as `(max_abr_kbps × 1000 / 8) × duration_secs` when
   an exact size is not available.

5. **Format selection** — `select_format_id()` picks the audio format with the
   highest `abr` (audio bitrate) from the `formats` list. This concrete
   `format_id` is passed to yt-dlp to avoid the "Requested format is not
   available" error that can occur with generic selectors like `bestaudio`.

6. **Download** — `download_with_format()` uses the `yt_dlp` Python module
   (avoids subprocess overhead). If the module raises an exception it falls back
   to the yt-dlp CLI. ffmpeg postprocessing extracts a 192 kbps MP3 and deletes
   the intermediate video file.

7. **Format-unavailable retry** — if the download fails with a format-related
   error, the script fetches fresh metadata, re-runs `select_format_id()`, and
   retries with the concrete format_id.

## yt-dlp binary selection

`detect_ytdlp()` prefers the binary sitting next to the active Python interpreter
(i.e. `.venv/bin/yt-dlp`) over any system-installed binary. This ensures the
subprocess and the Python module are always the same version, avoiding the
"Requested format is not available" failures seen with outdated system packages.

## Why CLI-only for metadata, module for download

- **Metadata (`dump_json`)**: always uses the CLI subprocess. The `yt_dlp`
  Python module's `extract_info()` has no wall-clock timeout; on age-gated or
  slow-to-respond videos it cycles through multiple player clients
  (ios, android, web) and can hang for several minutes. `subprocess.run(timeout=N)`
  provides an unconditional hard cap.

- **Download (`download_with_format`)**: prefers the Python module because it
  avoids subprocess spawn overhead and integrates ffmpeg postprocessing cleanly.
  Falls back to CLI when the module fails or when `--cookies-from-browser` is
  requested (the module cannot import browser cookies directly).

## Logging

- `<target>/.ydl_state/failed.log` — tab-separated log of failed/skipped items,
  written with `a` (append) mode. Format: `query\treason_code[\tdetail]`.
- No progress log is written; the script prints per-row status to stdout.

## Limitations & potential improvements

- No resume/skip for already-downloaded files (re-running re-downloads everything).
- No playlist subdirectory sorting — all MP3s land flat in `<target_dir>`.
- Search quality depends entirely on the `Track name + Artist name` string; ISRC
  or Spotify preview URL matching would be more reliable.
- No jitter between requests beyond the 0.5 s `time.sleep()` after each download;
  YouTube rate-limits aggressive runs. Consider adding `--sleep-interval` support.
