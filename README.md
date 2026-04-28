# YouTube CSV Downloader

Reads a CSV exported from a music service (e.g. Spotify) and downloads each
track from YouTube as an MP3.

What it does

- Searches YouTube for each `Track + Artist` row and downloads the best audio match.
- Runs preflight checks (duration, estimated file size, view count) before downloading.
- Extracts a 192 kbps MP3 via ffmpeg.
- Logs failed/skipped items to `<target>/.ydl_state/failed.log`.

Quick links

- CLI: [src/download_from_csv.py](src/download_from_csv.py)
- Installation instructions: [docs/INSTALL.md](docs/INSTALL.md)
- Usage guide and examples: [docs/USAGE.md](docs/USAGE.md)
- Architecture notes: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

Requirements

- Python 3.9+
- `yt-dlp` (install via `pip install yt-dlp` or use the `.venv`)
- `ffmpeg` (for audio extraction — must be on PATH)

Quick start

1. Install dependencies into a virtual environment:

```bash
python3 -m venv .venv && .venv/bin/pip install yt-dlp
```

2. Dry-run to preview what would be downloaded:

```bash
.venv/bin/python src/download_from_csv.py sample_test.csv downloads --dry-run --limit 5
```

3. Real download run:

```bash
.venv/bin/python src/download_from_csv.py "My Spotify Library.csv" downloads
```

Or use the wrapper script which activates the venv automatically:

```bash
scripts/run.sh "My Spotify Library.csv" downloads
```

Output

- MP3 files are written directly into `<target_dir>`, named by the YouTube video title.
- Failed and skipped items are recorded in `<target_dir>/.ydl_state/failed.log`.

Cookies & Rate-Limiting

If yt-dlp fails with "Sign in to confirm you're not a bot", pass a cookies file:

```bash
.venv/bin/python src/download_from_csv.py "My Spotify Library.csv" downloads \
  --cookies cookies.txt \
  --user-agent "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
```

To export a cookies file use a browser extension such as "Get cookies.txt LOCALLY"
and export while logged in to YouTube.

Alternatively, let yt-dlp import cookies directly from an installed browser
(works best when running natively on the same machine as the browser):

```bash
.venv/bin/python src/download_from_csv.py "My Spotify Library.csv" downloads \
  --cookies-from-browser chrome
```

Note: `--cookies-from-browser` may fail under WSL due to Windows DPAPI key access.
Prefer a cookies file in that case.

All CLI flags

| Flag | Default | Description |
|------|---------|-------------|
| `--dry-run` | off | Preflight checks only, no downloads |
| `--limit N` | 0 (all) | Process only the first N rows |
| `--max-duration SECS` | 600 | Skip videos longer than this |
| `--max-filesize SIZE` | 30M | Skip if estimated filesize exceeds this |
| `--min-views N` | 10000 | Skip videos with fewer views |
| `--cookies PATH` | none | Path to Netscape-format cookies.txt |
| `--cookies-from-browser BROWSER` | none | Import cookies from browser (chrome, firefox…) |
| `--user-agent STRING` | none | Custom User-Agent header |
| `--js-runtimes` | auto | JS runtime for yt-dlp (auto, deno, node, deno:/path) |
| `--skip-smoke-test` | off | Skip the startup connectivity check |

Legal / Terms

This tool downloads content from third-party services. Ensure you have the
right to download the content and comply with the provider's Terms of Service.
