# Troubleshooting

Common problems and how to fix them.

1. "python not found" on Windows

- Windows can show a Microsoft Store shim if Python isn't installed. Install
  Python via `winget`, the Microsoft Store, or from python.org. Alternatively
  the script falls back to a `gawk` parser when available.

2. `yt-dlp` or `ffmpeg` missing

- Install via `pip` (cross-platform) or `winget` (Windows). The script will
  look for a local `./tools/yt-dlp.exe` if `yt-dlp` is not installed system-wide.

3. Poor or incorrect match for a song

- The script searches by `Track + Artist`. For ambiguous titles try adding
  album information to the CSV or adjusting the query in the script.

4. Downloads stop unexpectedly

- The script logs failures to `<target>/.ydl_state/failed.log`. Check that
  file and re-run the script; it will skip already-complete MP3 files.

5. Want to change output naming

- Edit the `sanitize()` call and `target` construction inside
  `youtube-download-from-csv.sh` to control exact naming.
