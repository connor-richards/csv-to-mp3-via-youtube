# Usage

CLI: `src/download_from_csv.py`

Options

- `--dry-run` : show what would be downloaded without writing files.
- `--limit N` or `-n N` : process only the first N tracks (useful for testing).
- `--help` or `-h` : show usage.

Examples

- Dry-run 5 tracks:

```bash
python3 src/download_from_csv.py sample_test.csv downloads --dry-run --limit 5
```

- Real run (download everything and sort into playlist folders):

```bash
python3 src/download_from_csv.py "My Spotify Library.csv" downloads
```

Behavior notes

- The script parses the CSV with Python's `csv` module when available; if
  Python is not available it will attempt a `gawk`-based fallback.
- For each row the script builds a `ytsearch1:` query using `Track` + `Artist`.
- Files are named `<Track> - <Artist>.mp3` inside `<target>/<Playlist name>/`.
- If the MP3 already exists the script skips that track — this is how resume works.
- Progress and failures are logged to `<target>/.ydl_state/progress.log` and
  `<target>/.ydl_state/failed.log`.

Tips

- If the audio match is poor, try adding more metadata to the CSV (e.g., album)
  or tuning the search query in the script.
- If you want to keep the original audio file produced by `yt-dlp`, run
  `yt-dlp` with `-k` (the script deletes the original by default).

Additional useful options for the Python downloader (`src/download_from_csv.py`):

- `--cookies PATH` : pass a `cookies.txt` file exported from your browser.
- `--cookies-from-browser BROWSER` : ask `yt-dlp` to load cookies directly from a browser profile (e.g. `chrome`, `firefox`).
- `--user-agent STRING` : set a custom User-Agent header for requests.
- `--js-runtimes` : supply a JavaScript runtime to yt-dlp (e.g. `node`, `deno`) or use `auto` to attempt detection.

These help when videos are age-restricted, geo-restricted, or when yt-dlp requires a JS runtime to extract richer formats.

To run with a local virtualenv (if present):

```bash
scripts/run.sh sample_test.csv downloads --dry-run
```
