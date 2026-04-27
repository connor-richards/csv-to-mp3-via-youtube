# Architecture & Implementation Notes

High-level flow

1. Parse CSV -> produce a TSV of `playlist<TAB>track<TAB>artist`.
2. For each row:
   - Sanitize playlist and filename strings.
   - Skip if target MP3 already exists (resume behavior).
   - Use `yt-dlp` with `ytsearch1:` to find the best match for `Track + Artist`.
   - Download best audio, extract/convert to MP3 (ffmpeg used under the hood).
   - Move the resulting MP3 into `<target>/<Playlist>/<Track - Artist>.mp3`.
   - Log success/skip/failure to the target's `.ydl_state` directory.

Implementation details

- CSV parsing: prefers Python's `csv.DictReader`; falls back to a `gawk` FPAT
  parser for systems without Python.
- `yt-dlp` invocation: uses `--extract-audio --audio-format mp3 --audio-quality 0`
  to get high-quality MP3 output. The script supports a local `./tools/yt-dlp.exe`
  for Windows.
- Filenames: the script performs conservative sanitization (removes slashes,
  control chars, collapses spaces). You may further adjust `sanitize()` in the
  script to match your preferences.

Logging & resume

- A `<target>/.ydl_state/progress.log` line is appended per track with a simple
  `playlist|track|artist|status` format. `status` can be `done`, `failed`, `skipped`,
  or `dryrun`.
- To resume, re-run the script with the same CSV and target directory; the
  script will skip existing files.

Limitations & future improvements

- Better matching heuristics (use album, ISRC, or metadata scoring).
- Rate-limiting and parallel downloads (currently serial to reduce false matches).
- Option to keep original audio files with `-k` to preserve formats.
