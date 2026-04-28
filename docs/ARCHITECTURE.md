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

1. **File existence check** — generate a deterministic filename from the CSV's
   artist and track name (format: `Artist - Track.mp3`). If this file already
   exists in the target directory, skip the entry immediately. This ensures that
   any YouTube video fulfilling the same artist/track request is recognized as
   a duplicate and skipped without unnecessary metadata fetches or downloads.
   This is the primary deduplication mechanism.

2. **Flat search** — run `yt-dlp --flat-playlist "ytsearch1:<track> <artist>"` to
   get the top YouTube video URL without triggering a full metadata fetch.
   This step is fast because `--flat-playlist` skips the YouTube player-client
   API calls that are otherwise needed to resolve video formats.

3. **Channel/playlist guard** — if the returned URL points to a channel or playlist
   (detected by path patterns: `/channel/`, `/c/`, `/@`, `/user/`, `/playlist?`,
   `/videos`, `/shorts`), the search is retried with a refined query
   (`<original query> official audio`) across the top 5 results.

4. **Full metadata fetch** — run `yt-dlp --dump-json <video_url>` with
   `--extractor-args youtube:player_client=android_vr` so only a single fast
   player client is used. The call is made via `subprocess.run(timeout=60)` to
   enforce a hard wall-clock cap and prevent indefinite hangs on age-gated content.

5. **Preflight checks** — validate duration, estimated filesize, and view count
   against the `--max-duration`, `--max-filesize`, and `--min-views` thresholds.
   Filesize is estimated as `(max_abr_kbps × 1000 / 8) × duration_secs` when
   an exact size is not available.

6. **Format selection** — `select_format_id()` picks the audio format with the
   highest `abr` (audio bitrate) from the `formats` list. This concrete
   `format_id` is passed to yt-dlp to avoid the "Requested format is not
   available" error that can occur with generic selectors like `bestaudio`.

7. **Download** — `download_with_format()` uses the `yt_dlp` Python module
   (avoids subprocess overhead). If the module raises an exception it falls back
   to the yt-dlp CLI. ffmpeg postprocessing extracts a 192 kbps MP3 and deletes
   the intermediate video file.

8. **Format-unavailable retry** — if the download fails with a format-related
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

## File naming

Files are named using CSV data rather than YouTube video titles to enable
consistent duplicate detection:

- **Filename format**: `Artist - Track.mp3` (derived from CSV columns `Artist name`
  and `Track name`)
- **Benefit**: The same song from _any_ YouTube video (different uploads, covers,
  remixes) will always produce the same filename. This allows the file existence
  check (step 1) to skip redundant downloads.
- **Fallback**: If a track name is unavailable, the download is skipped with a
  `no_csv_filename` error.

## Logging

- `<target>/.ydl_state/failed.log` — tab-separated log of failed/skipped items,
  written with `a` (append) mode. Format: `query\treason_code[\tdetail]`.
  Status codes include: `duplicate_file_exists` (file already on disk),
  `SKIPPED` (preflight checks), `FAILED` (errors).
- No progress log is written; the script prints per-row status to stdout.

## Limitations & potential improvements

- Filenames with invalid characters (e.g., `/`, `\`, `:`) are sanitized by
  removing problematic characters. Very long artist or track names may be
  truncated by the filesystem (typically 255 bytes on most systems).
- No playlist subdirectory sorting — all MP3s land flat in `<target_dir>`.
- Search quality depends entirely on the `Track name + Artist name` string; ISRC
  or Spotify preview URL matching would be more reliable.
- No jitter between requests beyond the 0.5 s `time.sleep()` after each download;
  YouTube rate-limits aggressive runs. Consider adding `--sleep-interval` support.
