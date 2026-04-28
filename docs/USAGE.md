# Usage

CLI: `src/download_from_csv.py`

## Basic examples

Dry-run (preflight checks only, no downloads):

```bash
.venv/bin/python src/download_from_csv.py sample_test.csv downloads --dry-run --limit 5
```

Real run — download everything:

```bash
.venv/bin/python src/download_from_csv.py "My Spotify Library.csv" downloads
```

With cookies and a custom user-agent (recommended to avoid bot-detection):

```bash
.venv/bin/python src/download_from_csv.py "My Spotify Library.csv" downloads \
  --cookies cookies.txt \
  --user-agent "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
```

Using the venv wrapper:

```bash
scripts/run.sh "My Spotify Library.csv" downloads
```

## All options

| Flag | Default | Description |
|------|---------|-------------|
| `--dry-run` | off | Preflight only, no downloads written |
| `--limit N` | 0 (all) | Stop after N rows |
| `--max-duration SECS` | 600 | Skip videos longer than this many seconds |
| `--max-filesize SIZE` | 30M | Skip if estimated audio filesize exceeds this |
| `--min-views N` | 10000 | Skip videos with fewer than N views |
| `--cookies PATH` | none | Netscape-format cookies.txt file |
| `--cookies-from-browser BROWSER` | none | Load cookies from an installed browser (e.g. `chrome`, `firefox`) |
| `--user-agent STRING` | none | Custom User-Agent header sent with requests |
| `--js-runtimes` | auto | JS runtime for yt-dlp format extraction (`auto`, `deno`, `node`, `deno:/path`) |
| `--skip-smoke-test` | off | Skip the startup connectivity check |

## Behavior

**CSV format**

The script reads columns `Track name` and `Artist name` (Spotify export format).
It also accepts `Track`/`Artist` and `title` as fallbacks.

**Search strategy (two-step)**

For each row the script:
1. Issues a `ytsearch1:` query with `--flat-playlist` to get the top video URL
   quickly, without fetching full video metadata (avoids slow player-client requests).
2. Fetches full metadata for that specific video URL using a single player client
   (`android_vr`) with a 60-second hard timeout via subprocess.

If the initial search returns a channel or playlist URL instead of a video, it
automatically retries with a refined query (`{query} official audio`) using the
top 5 results.

**Preflight checks**

Before downloading, the script validates:
- Duration ≤ `--max-duration` (skips live streams and full albums)
- Estimated filesize ≤ `--max-filesize` (estimated from audio bitrate × duration when exact size is unavailable)
- View count ≥ `--min-views` (filters out obscure mismatches)

**Download & audio extraction**

Downloads use the yt-dlp Python module (faster, no subprocess overhead) and fall
back to the CLI if the module fails. ffmpeg extracts the audio and writes a
192 kbps MP3.

If yt-dlp reports "Requested format is not available", the script fetches fresh
metadata, selects the best available audio `format_id`, and retries.

**Output location**

MP3 files are written directly into `<target_dir>`, named by the YouTube video
title (e.g. `TWO DOOR CINEMA CLUB | WHAT YOU KNOW.mp3`).

**Failure log**

Items that fail or are skipped are appended to `<target_dir>/.ydl_state/failed.log`
with a tab-separated `query<TAB>reason<TAB>detail` format. Reason codes:

| Code | Meaning |
|------|---------|
| `ERROR` | yt-dlp returned an error during search or metadata fetch |
| `SKIPPED` | Preflight check failed (see detail: `duration_exceeded`, `views_too_low`, etc.) |
| `NO_RESULT` | Search returned no usable video |
| `DOWNLOAD_FAILED` | yt-dlp download step failed |
| `DOWNLOAD_FAILED_RETRY` | Download failed even after format-id retry |
| `NO_AUDIO_FORMATS` | No audio-capable format found to retry with |

**Startup smoke test**

On each run (unless `--skip-smoke-test` is passed) the script fetches metadata
for a known public YouTube video to verify yt-dlp and network connectivity are
working. The run exits early with a hint if the smoke test fails.

## Cookies & authentication

Export a Netscape-format cookies file using a browser extension (e.g.
"Get cookies.txt LOCALLY") while logged in to YouTube, then pass it with
`--cookies cookies.txt`.

`--cookies-from-browser` works when running natively on the same OS as the
browser. Under WSL it may fail with DPAPI decryption errors — use a cookies file
instead.

## Tips

- Start with `--dry-run --limit 10` to validate your CSV and check preflight results.
- If `--min-views` is filtering out legitimate tracks (e.g. indie artists), lower it:
  `--min-views 1000`.
- The `--js-runtimes auto` option auto-detects deno, node, jsc, or d8. Set
  explicitly if auto-detection picks up an unexpected runtime.
- Rate-limit errors ("This content isn't available, try again later") are a
  temporary YouTube-side block from too many requests. Wait an hour and rerun.
