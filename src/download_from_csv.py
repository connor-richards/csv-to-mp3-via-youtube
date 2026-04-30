#!/usr/bin/env python3
"""Download MP3s from a Spotify-export CSV file using yt-dlp, with resumable progress.

For each CSV row the script:
  1. Generates a deterministic filename from CSV artist and track name.
     If the file already exists, skip (duplicate detection via filename).
  2. Issues a fast flat YouTube search (ytsearch1:) to get a video URL
     without triggering the yt-dlp player-client loop.
  3. Fetches full metadata for that specific URL (one player client,
     hard timeout via subprocess).
  4. Runs preflight checks: duration, estimated filesize, view count.
  5. Downloads the video and extracts a 192 kbps MP3 via ffmpeg.

Files are written to <target_dir> with names derived from CSV data (Artist - Track.mp3).
This consistent naming enables duplicate detection: any YouTube video of the same
song is recognized by filename and skipped automatically.

All outcomes logged to <target_dir>/.ydl_state/progress.log (query|status|detail|timestamp).

Resume & Retry:
  - By default, already-processed entries are skipped on reruns.
  - Use --retry to reprocess failed entries, --retry-skipped for skipped, --retry-all for both.

Examples:
  python3 src/download_from_csv.py sample_test.csv downloads --dry-run --limit 5
  python3 src/download_from_csv.py "My Spotify Library.csv" downloads
  python3 src/download_from_csv.py "My Spotify Library.csv" downloads --retry
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import datetime
from typing import Optional

from ydl_helpers import (
    dump_json,
    dump_json_flat_search,
    detect_ytdlp,
    detect_js_runtime,
    download_with_format,
    parse_size_to_bytes,
    preflight_check,
    select_format_id,
)


class Colors:
    """ANSI color codes for terminal output."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    
    # Foreground colors
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    
    # Bright colors
    BRIGHT_BLACK = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"


def colored(text: str, color: str, bold: bool = False) -> str:
    """Apply color to text for terminal output."""
    prefix = f"{Colors.BOLD}" if bold else ""
    return f"{prefix}{color}{text}{Colors.RESET}"


def score_video_title(title: str) -> int:
    """Score a video title: higher is better.
    
    Prefer: official audio, official
    Avoid: live, remix, cover, acoustic
    """
    title_lower = title.lower()
    score = 0
    
    # Boost official versions
    if "official audio" in title_lower:
        score += 100
    elif "official" in title_lower:
        score += 50
    
    # Penalize undesired versions
    if "live" in title_lower:
        score -= 100
    if "remix" in title_lower:
        score -= 50
    if "cover" in title_lower:
        score -= 30
    if "acoustic" in title_lower:
        score -= 20
    
    return score


def get_youtube_url_from_row(row: dict) -> str:
    """Extract optional YouTube URL from CSV row.
    
    Looks for columns: 'YouTube URL', 'YouTube Link', 'URL', 'Link'
    Returns empty string if not found.
    """
    url = (
        row.get("YouTube URL") or
        row.get("YouTube Link") or
        row.get("URL") or
        row.get("Link") or
        ""
    )
    return url.strip() if url else ""


def safe_mkdir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def build_query(row: dict) -> str:
    # Expect columns like 'Track name' and 'Artist name' from sample_test.csv
    track = row.get("Track name") or row.get("Track") or row.get("title") or ""
    artist = row.get("Artist name") or row.get("Artist") or ""
    q = f"{track} {artist}".strip()
    # Append "official audio" to prioritize official versions in YouTube search
    return (q + " official audio" if q else (track or artist)) or artist


def build_filename(row: dict) -> str:
    """Build a consistent filename from CSV artist and track name.
    
    Returns: "Artist - Track" with sanitized characters suitable for filenames.
    This ensures the same song from different YouTube videos gets the same filename,
    enabling proper duplicate detection and file skipping.
    """
    track = (row.get("Track name") or row.get("Track") or row.get("title") or "").strip()
    artist = (row.get("Artist name") or row.get("Artist") or "").strip()
    
    if not track:
        # Fallback to query-based naming if no track name
        return None
    
    # Use "Artist - Track" format, or just Track if no artist
    if artist:
        base = f"{artist} - {track}"
    else:
        base = track
    
    # Sanitize for filesystem: remove/replace problematic characters
    # Keep alphanumerics, spaces, hyphens, underscores, and parentheses
    import re
    safe = re.sub(r'[<>:"/\\|?*]', '', base)  # Remove forbidden chars
    safe = re.sub(r'\s+', ' ', safe).strip()  # Normalize whitespace
    
    return safe if safe else None


def load_progress_log(progress_log_path: str) -> dict:
    """Load progress.log into a dict: query -> (status, detail, timestamp).
    
    Status is one of: SUCCESS, SKIPPED, FAILED.
    """
    progress = {}
    if not os.path.isfile(progress_log_path):
        return progress
    try:
        with open(progress_log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 2:
                    query, status = parts[0], parts[1]
                    detail = parts[2] if len(parts) > 2 else ""
                    ts = parts[3] if len(parts) > 3 else ""
                    progress[query] = (status, detail, ts)
    except Exception as e:
        print(f"Warning: could not read progress log: {e}")
    return progress


def write_progress_entry(path: str, query: str, status: str, detail: str = "") -> None:
    """Append an entry to progress.log: query<TAB>status<TAB>detail<TAB>timestamp."""
    ts = datetime.now().isoformat()
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{query}\t{status}\t{detail}\t{ts}\n")


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Download MP3s from CSV rows (yt-dlp)")
    p.add_argument("csvfile")
    p.add_argument("target_dir")
    p.add_argument("--dry-run", action="store_true", help="Perform preflight checks but don't download")
    p.add_argument("--max-duration", type=int, default=600, help="Max duration seconds (default: 600)")
    p.add_argument("--max-filesize", default="30M", help="Max filesize (e.g. 30M). Uses estimate if exact size unavailable")
    p.add_argument("--min-views", type=int, default=10000, help="Minimum view count to accept")
    p.add_argument("--limit", type=int, default=0, help="Limit number of rows processed (0 = all)")
    p.add_argument("--cookies", default=None, help="Path to cookies.txt for yt-dlp")
    p.add_argument("--user-agent", default=None, help="User-Agent header to add")
    p.add_argument("--js-runtimes", default="auto", help="JS runtime to pass to yt-dlp (auto|deno|node|deno:/path)")
    p.add_argument("--cookies-from-browser", default=None, help="Browser name for --cookies-from-browser (e.g. chrome, firefox)")
    p.add_argument("--skip-smoke-test", action="store_true")
    p.add_argument("--retry", action="store_true", help="Retry previously failed (ERROR) entries")
    p.add_argument("--retry-skipped", action="store_true", help="Retry previously skipped entries")
    p.add_argument("--retry-all", action="store_true", help="Retry all previously attempted entries (both failed and skipped)")
    args = p.parse_args(argv)

    csvfile = args.csvfile
    target_dir = args.target_dir
    dry_run = args.dry_run
    max_duration = args.max_duration
    max_filesize = parse_size_to_bytes(args.max_filesize)
    min_views = args.min_views
    limit = args.limit

    if not os.path.isfile(csvfile):
        print(f"CSV file not found: {csvfile}")
        return 2

    safe_mkdir(target_dir)
    state_dir = os.path.join(target_dir, ".ydl_state")
    safe_mkdir(state_dir)
    failed_log = os.path.join(state_dir, "failed.log")
    progress_log = os.path.join(state_dir, "progress.log")
    
    # Load existing progress
    progress = load_progress_log(progress_log)
    already_processed = set(progress.keys())
    
    # Determine which queries to skip
    skip_queries = set()
    if args.retry_all:
        skip_queries = set()  # process everything
    elif args.retry:
        # Skip SUCCESS and SKIPPED, but reprocess FAILED
        skip_queries = {q for q, (s, _, _) in progress.items() if s in ("SUCCESS", "SKIPPED")}
    elif args.retry_skipped:
        # Skip SUCCESS and FAILED, but reprocess SKIPPED
        skip_queries = {q for q, (s, _, _) in progress.items() if s in ("SUCCESS", "FAILED")}
    else:
        # Default: skip all previously processed
        skip_queries = already_processed
    
    if skip_queries:
        remaining = sum(1 for line in open(csvfile) if line.strip() and not line.startswith('"Track')) - len(skip_queries)
        print(colored(f"⊘ Skipping {len(skip_queries)} already-processed entries", Colors.DIM))
        if remaining > 0:
            print(colored(f"→ {remaining} new entries to process", Colors.CYAN))
        else:
            print(colored("→ No new entries to process", Colors.YELLOW))

    ytd = detect_ytdlp()
    print(colored(f"✓ yt-dlp module: {bool(ytd.get('module'))}", Colors.GREEN), colored(f"CLI: {ytd.get('bin')}", Colors.DIM))

    # smoke test
    # determine JS runtime to provide to yt-dlp (CLI/module)
    js_runtime: Optional[str] = None
    if args.js_runtimes and args.js_runtimes != "auto":
        js_runtime = args.js_runtimes
    elif args.js_runtimes == "auto":
        auto = detect_js_runtime()
        if auto:
            js_runtime = f"{auto[0]}:{auto[1]}"

    if js_runtime:
        print(colored(f"✓ Using JS runtime: {js_runtime}", Colors.GREEN))

    if not args.skip_smoke_test and not dry_run:
        print(colored("→ Running smoke test...", Colors.CYAN))
        smoke = dump_json(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            cookies=args.cookies,
            cookies_from_browser=args.cookies_from_browser,
            user_agent=args.user_agent,
            js_runtime=js_runtime,
        )
        if smoke.get("__error__"):
            print(colored(f"✗ Smoke test failed: {smoke.get('__error__').get('message')}", Colors.RED, bold=True))
            print(colored("Hint: provide cookies or user-agent, or run with --skip-smoke-test to continue", Colors.YELLOW))
            return 3
        print(colored("✓ Smoke test OK", Colors.GREEN))

    def is_channel_url(url: str) -> bool:
        """Return True if url points to a channel or playlist, not a specific video."""
        if not isinstance(url, str):
            return False
        for pat in ("/channel/", "/c/", "/@", "/user/", "/playlist?", "/videos", "/shorts"):
            if pat in url:
                return True
        return False

    def is_video_entry(e: dict) -> bool:
        if not isinstance(e, dict):
            return False
        url = e.get("webpage_url") or e.get("url") or ""
        if is_channel_url(url):
            return False  # channels are not individual videos
        if e.get("formats"):
            return True
        if e.get("duration"):
            return True
        if isinstance(url, str) and "watch" in url:
            return True
        return False

    def pick_best_video(info: dict) -> Optional[dict]:
        # Prefer a direct video-like entry. Search entries list for the
        # first item that looks like a video (has formats/duration/watch URL).
        if not isinstance(info, dict):
            return None
        entries = []
        if info.get("entries"):
            if isinstance(info.get("entries"), list):
                entries = info.get("entries")
            else:
                entries = [info.get("entries")]

        for e in entries:
            if is_video_entry(e):
                return e

        # sometimes the top-level info is itself a video
        if is_video_entry(info):
            return info

        # look deeper in nested entries
        for e in entries:
            if isinstance(e, dict) and e.get("entries"):
                nested = e.get("entries")
                if isinstance(nested, list):
                    for ne in nested:
                        if is_video_entry(ne):
                            return ne

        return None

    def pick_best_video_by_title(info: dict) -> Optional[dict]:
        """Pick best video from search results, scoring by title.
        
        Prefers official audio, avoids live/remix/cover versions.
        Falls back to first valid video if scoring yields no results.
        """
        if not isinstance(info, dict):
            return None
        
        entries = []
        if info.get("entries"):
            if isinstance(info.get("entries"), list):
                entries = info.get("entries")
            else:
                entries = [info.get("entries")]
        
        # Score and filter entries
        candidates = []
        for e in entries:
            if is_video_entry(e):
                url = e.get("webpage_url") or e.get("url") or ""
                if not is_channel_url(url):
                    title = e.get("title") or ""
                    score = score_video_title(title)
                    candidates.append((score, e))
        
        if candidates:
            # Sort by score (highest first)
            candidates.sort(key=lambda x: x[0], reverse=True)
            return candidates[0][1]
        
        # Fallback: if no scored candidates, return first valid video (original behavior)
        for e in entries:
            if is_video_entry(e) and not is_channel_url(e.get("webpage_url") or e.get("url") or ""):
                return e
        
        return None

    # Count total entries to process
    # utf-8-sig strips the BOM that Spotify (and many Windows apps) prepend,
    # which otherwise corrupts the first column name ('Track name' -> '\ufeffTrack name')
    total_entries = 0
    with open(csvfile, newline="", encoding="utf-8-sig") as fh:
        total_entries = sum(1 for _ in csv.DictReader(fh))
    if limit and limit < total_entries:
        total_entries = limit
    print(colored(f"\n📦 Total entries: {total_entries}", Colors.BLUE, bold=True))
    print("-" * 70)

    processed = 0
    with open(csvfile, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if limit and processed >= limit:
                break
            processed += 1
            
            # Extract track and artist names
            track = (row.get("Track name") or row.get("Track") or row.get("title") or "").strip()
            artist = (row.get("Artist name") or row.get("Artist") or "").strip()
            query = build_query(row)
            
            # Format progress header
            progress_str = colored(f"[{processed}/{total_entries}]", Colors.CYAN, bold=True)
            track_str = colored(track, Colors.BRIGHT_WHITE, bold=True) if track else colored("[no track]", Colors.DIM)
            artist_str = colored(artist, Colors.BRIGHT_WHITE) if artist else colored("[no artist]", Colors.DIM)
            
            # Skip if already processed and not retrying
            if query in skip_queries:
                print(f"\n{progress_str} {track_str} - {artist_str}")
                print(colored("  ⊘ Already processed (cached skip)", Colors.YELLOW, bold=True))
                continue
            
            print(f"\n{progress_str} {track_str} - {artist_str}")
            
            # Step 0: Check for optional YouTube URL in CSV
            manual_url = get_youtube_url_from_row(row)
            flat_entry = None
            
            if manual_url:
                # User provided a YouTube URL; use it directly
                print(colored(f"  → Using YouTube URL from CSV", Colors.CYAN))
                flat_entry = {"webpage_url": manual_url, "url": manual_url}
            else:
                # Step 1: flat search — get video URL quickly without fetching full metadata.
                # Use dump_json_flat_search to get all 5 results, then score and pick the best.
                search = f"ytsearch5:{query}"
                flat_info = dump_json_flat_search(search, cookies=args.cookies, cookies_from_browser=args.cookies_from_browser, user_agent=args.user_agent, js_runtime=js_runtime, timeout=30)
                if flat_info.get("__error__"):
                    reason = flat_info.get("__error__").get("message")
                    print(colored(f"  ✗ Search failed: {reason}", Colors.RED, bold=True))
                    write_progress_entry(progress_log, query, "FAILED", f"search_error:{reason[:100]}")
                    with open(failed_log, "a", encoding="utf-8") as ff:
                        ff.write(f"{query}\tERROR\t{reason}\n")
                    continue

                # Pick the best video by title scoring (prefers official audio, avoids live)
                flat_entry = pick_best_video_by_title(flat_info)

                # If search returned a channel/playlist URL or nothing, retry without the extra keywords
                if flat_entry is None or is_channel_url(flat_entry.get("webpage_url") or flat_entry.get("url") or ""):
                    if flat_entry is not None:
                        print(colored("  → Detected channel/playlist; retrying...", Colors.YELLOW))
                    else:
                        print(colored("  → No good result; retrying with broader search...", Colors.YELLOW))
                    # Rebuild query without "official audio" for broader search
                    base_query = (row.get("Track name") or row.get("Track") or row.get("title") or "").strip()
                    if not base_query:
                        base_query = (row.get("Artist name") or row.get("Artist") or "").strip()
                    if base_query:
                        artist_str_for_search = (row.get("Artist name") or row.get("Artist") or "").strip()
                        broader_query = f"{base_query} {artist_str_for_search}".strip()
                        flat_info2 = dump_json_flat_search(f"ytsearch5:{broader_query}", cookies=args.cookies, cookies_from_browser=args.cookies_from_browser, user_agent=args.user_agent, js_runtime=js_runtime, timeout=30)
                        if not flat_info2.get("__error__"):
                            flat_entry = pick_best_video_by_title(flat_info2)

            if not flat_entry:
                print(colored("  ✗ No result from search", Colors.RED, bold=True))
                write_progress_entry(progress_log, query, "FAILED", "no_search_result")
                with open(failed_log, "a", encoding="utf-8") as ff:
                    ff.write(f"{query}\tNO_RESULT\n")
                continue

            # Step 2: fetch full metadata for the specific video URL (fast — direct URL,
            # no search overhead, one player client only).
            video_url = flat_entry.get("webpage_url") or flat_entry.get("url")
            info = dump_json(video_url, cookies=args.cookies, cookies_from_browser=args.cookies_from_browser, user_agent=args.user_agent, js_runtime=js_runtime, timeout=60)
            if info.get("__error__"):
                reason = info.get("__error__").get("message")
                print(colored(f"  ✗ Metadata error: {reason}", Colors.RED, bold=True))
                write_progress_entry(progress_log, query, "FAILED", f"metadata_error:{reason[:100]}")
                with open(failed_log, "a", encoding="utf-8") as ff:
                    ff.write(f"{query}\tERROR\t{reason}\n")
                continue

            entry = pick_best_video(info) or info

            if not entry:
                print(colored("  ✗ No result from metadata", Colors.RED, bold=True))
                write_progress_entry(progress_log, query, "FAILED", "no_result_after_metadata")
                with open(failed_log, "a", encoding="utf-8") as ff:
                    ff.write(f"{query}\tNO_RESULT\n")
                continue

            ok, reason = preflight_check(entry, max_duration=max_duration, max_filesize=max_filesize, min_views=min_views)
            if not ok:
                print(colored(f"  ⊘ Preflight check failed: {reason}", Colors.YELLOW, bold=True))
                write_progress_entry(progress_log, query, "SKIPPED", reason)
                with open(failed_log, "a", encoding="utf-8") as ff:
                    ff.write(f"{query}\tSKIPPED\t{reason}\n")
                continue

            # Generate filename from CSV data for consistent duplicate detection
            csv_filename = build_filename(row)
            if not csv_filename:
                print(colored("  ✗ Could not generate filename from CSV data", Colors.RED, bold=True))
                write_progress_entry(progress_log, query, "FAILED", "no_csv_filename")
                with open(failed_log, "a", encoding="utf-8") as ff:
                    ff.write(f"{query}\tFAILED\tno_csv_filename\n")
                continue

            # Check if file already exists (duplicate detection via CSV-based naming)
            existing_mp3 = os.path.join(target_dir, f"{csv_filename}.mp3")
            if os.path.isfile(existing_mp3):
                print(colored(f"  ✓ File already exists (skipping duplicate)", Colors.BRIGHT_BLACK, bold=True))
                write_progress_entry(progress_log, query, "SKIPPED", "duplicate_file_exists")
                with open(failed_log, "a", encoding="utf-8") as ff:
                    ff.write(f"{query}\tSKIPPED\tduplicate_file_exists\n")
                continue

            # choose a concrete format id when possible
            fmt = select_format_id(entry)
            chosen = fmt or "bestaudio/best"
            webpage = entry.get("webpage_url") or entry.get("url") or None
            if not webpage:
                print(colored("  ✗ No webpage URL available", Colors.RED, bold=True))
                write_progress_entry(progress_log, query, "FAILED", "no_webpage_url")
                with open(failed_log, "a", encoding="utf-8") as ff:
                    ff.write(f"{query}\tNO_WEBPAGE\n")
                continue

            # Use CSV-derived filename instead of YouTube video title
            outtmpl = os.path.join(target_dir, f"{csv_filename}.%(ext)s")

            if dry_run:
                print(colored(f"  → [DRY-RUN] Would download and convert to MP3", Colors.CYAN))
                write_progress_entry(progress_log, query, "DRYRUN", "dry_run_mode")
                continue

            print(colored(f"  → Downloading and converting to MP3...", Colors.CYAN))
            r = download_with_format(
                webpage,
                chosen,
                outtmpl,
                cookies=args.cookies,
                cookies_from_browser=args.cookies_from_browser,
                user_agent=args.user_agent,
                js_runtime=js_runtime,
            )
            if not r.get("success"):
                msg = r.get("message") or ""
                print(colored(f"  ✗ Download failed", Colors.RED, bold=True))
                lower = msg.lower()
                # If failure appears to be due to unavailable format, try to inspect formats and retry with a concrete id
                if ("requested format is not available" in lower) or ("format not available" in lower) or ("no formats" in lower) or ("requested format" in lower):
                    print(colored(f"  → Retrying with alternate format...", Colors.YELLOW))
                    meta = dump_json(webpage, cookies=args.cookies, cookies_from_browser=args.cookies_from_browser, user_agent=args.user_agent, js_runtime=js_runtime)
                    if meta.get("__error__"):
                        fallback_reason = meta.get("__error__").get("message")
                        print(colored(f"  ✗ Could not fetch alternate metadata", Colors.RED))
                        write_progress_entry(progress_log, query, "FAILED", f"download_format_error:{msg[:80]}")
                        with open(failed_log, "a", encoding="utf-8") as ff:
                            ff.write(f"{query}\tDOWNLOAD_FAILED\t{msg[:200]} | fallback_meta_error:{(fallback_reason or '')[:200]}\n")
                    else:
                        fmt2 = select_format_id(meta)
                        if fmt2:
                            print(colored(f"  → Retrying download with alternate format ID...", Colors.YELLOW))
                            r2 = download_with_format(webpage, fmt2, outtmpl, cookies=args.cookies, cookies_from_browser=args.cookies_from_browser, user_agent=args.user_agent, js_runtime=js_runtime)
                            if r2.get("success"):
                                print(colored(f"  ✓ Download succeeded with alternate format", Colors.GREEN, bold=True))
                                write_progress_entry(progress_log, query, "SUCCESS", f"retry_with_format:{fmt2}")
                            else:
                                print(colored(f"  ✗ Retry failed", Colors.RED, bold=True))
                                write_progress_entry(progress_log, query, "FAILED", f"download_retry_failed:{r2.get('message')[:80]}")
                                with open(failed_log, "a", encoding="utf-8") as ff:
                                    ff.write(f"{query}\tDOWNLOAD_FAILED_RETRY\t{(r2.get('message') or '')[:200]}\n")
                        else:
                            print(colored(f"  ✗ No audio-capable format found", Colors.RED, bold=True))
                            write_progress_entry(progress_log, query, "FAILED", "no_audio_formats")
                            with open(failed_log, "a", encoding="utf-8") as ff:
                                ff.write(f"{query}\tNO_AUDIO_FORMATS\n")
                else:
                    write_progress_entry(progress_log, query, "FAILED", f"download_failed:{msg[:80]}")
                    with open(failed_log, "a", encoding="utf-8") as ff:
                        ff.write(f"{query}\tDOWNLOAD_FAILED\t{msg[:200]}\n")
            else:
                print(colored(f"  ✓ Download succeeded", Colors.GREEN, bold=True))
                write_progress_entry(progress_log, query, "SUCCESS", "downloaded")

            # be polite to services
            time.sleep(0.5)

    print(f"\n{colored('-' * 70, Colors.DIM)}")
    print(colored(f"✓ Completed: {processed}/{total_entries} entries processed", Colors.GREEN, bold=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
