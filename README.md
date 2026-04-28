# YouTube CSV Downloader

Small helper that reads a CSV exported from a music service (example: Spotify CSV)
and downloads each track from YouTube as an MP3, sorted into directories by
playlist name.

Why this repo

- Converts track lists to local MP3 files grouped by playlist.
- Resumable: skips already-downloaded files and logs progress & failures.
- Portable: uses `yt-dlp` + `ffmpeg`; ships a local `tools/` fallback when needed.

Quick links

- CLI: [src/download_from_csv.py](src/download_from_csv.py)
- Installation instructions: [docs/INSTALL.md](docs/INSTALL.md)
- Usage guide and examples: [docs/USAGE.md](docs/USAGE.md)
- Architecture notes: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

Requirements

- `yt-dlp` (or `./tools/yt-dlp.exe` on Windows)
- `ffmpeg` (for audio extraction)
- `bash` / POSIX shell (Windows: Git Bash, WSL, MSYS, or use Windows Terminal)

Quick start

1. Do a dry-run to see what would be downloaded without writing files:

```bash
python3 src/download_from_csv.py sample_test.csv downloads --dry-run --limit 5
```

2. Run the real download (will skip existing MP3s and resume automatically):

```bash
python3 src/download_from_csv.py "My Spotify Library.csv" downloads
```

Logs and resume

- Progress and failures are saved in `<target>/.ydl_state/progress.log`
  and `<target>/.ydl_state/failed.log`.
- To resume after an interruption, re-run the same command with the same
  `<target>` directory: the script will skip already-present MP3 files.

Local `tools/` fallback

- If `yt-dlp` is not installed system-wide, the script will use `./tools/yt-dlp.exe`
  (downloaded during testing). Similarly, a portable `ffmpeg` can be placed in
  `./tools/ffmpeg/...` and the script will pick it up through the PATH.

Legal / Terms

- This tool downloads content from third-party services. Ensure you have the
  right to download content and comply with the content provider's Terms of Service.

Cookies & Rate-Limiting

- If `yt-dlp` fails with a message like "Sign in to confirm you’re not a bot",
  it usually requires authentication cookies to proceed. The script supports
  passing cookies and other request options through environment variables.

- Environment variables supported:
  - `YTDLP_COOKIES_FROM_BROWSER=<browser>` — ask `yt-dlp` to import cookies
    directly from an installed browser (works when running natively on the same OS
    as the browser; e.g. `chrome`, `edge`, `firefox`).
  - `YTDLP_COOKIES_FILE=path/to/cookies.txt` — preferred: export a Netscape-format
    cookies file (use a browser extension like "cookies.txt" / "Get cookies.txt")
    and point the script to it.
  - `YTDLP_PROXY` — proxy URL (e.g. `http://127.0.0.1:8080`) to rotate IPs.
  - `YTDLP_USER_AGENT` — set a realistic browser user-agent string.
  - `SLEEP_MIN` / `SLEEP_MAX` — jittered sleep interval (seconds) between tracks
    to reduce request bursts.

Examples

```bash
# dry-run with exported cookies file and a 5-12s jitter between tracks
YTDLP_COOKIES_FILE=cookies.txt SLEEP_MIN=5 SLEEP_MAX=12 \
  python3 src/download_from_csv.py --dry-run --limit 10 sample_test.csv test_downloads

# native-Windows: let yt-dlp import browser cookies (works when run from Windows)
YTDLP_COOKIES_FROM_BROWSER=chrome python3 src/download_from_csv.py "My Spotify Library.csv" downloads
```

Notes

- On WSL the `--cookies-from-browser` option may not work reliably; prefer
  `YTDLP_COOKIES_FILE` when running under WSL. If you see DPAPI / decrypt errors,
  export cookies to a file and re-run the script with `YTDLP_COOKIES_FILE`.
- For very large libraries consider chunking the CSV (use `--limit` in loops),
  or using the YouTube Data API (requires an API key) to perform searches more
  politely
