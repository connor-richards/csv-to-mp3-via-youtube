#!/usr/bin/env python3
"""Download MP3s from a Spotify-export CSV file using yt-dlp, with resumable progress.

For each CSV row the script:
  1. Issues a fast flat YouTube search (ytsearch1:) to get a video URL
     without triggering the yt-dlp player-client loop.
  2. Fetches full metadata for that specific URL (one player client,
     hard timeout via subprocess).
  3. Runs preflight checks: duration, estimated filesize, view count.
  4. Downloads the video and extracts a 192 kbps MP3 via ffmpeg.

Files are written directly into <target_dir> named by YouTube video title.
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
    detect_ytdlp,
    detect_js_runtime,
    download_with_format,
    parse_size_to_bytes,
    preflight_check,
    select_format_id,
)


def safe_mkdir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def build_query(row: dict) -> str:
    # Expect columns like 'Track name' and 'Artist name' from sample_test.csv
    track = row.get("Track name") or row.get("Track") or row.get("title") or ""
    artist = row.get("Artist name") or row.get("Artist") or ""
    q = f"{track} {artist}".strip()
    return q or track or artist


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
        print(f"Skipping {len(skip_queries)} already-processed entries (use --retry* flags to reprocess)")

    ytd = detect_ytdlp()
    print("Detected yt-dlp module:" , bool(ytd.get("module")), "CLI:", ytd.get("bin"))

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
        print("Using JS runtime:", js_runtime)

    if not args.skip_smoke_test and not dry_run:
        print("Running smoke test...")
        smoke = dump_json(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            cookies=args.cookies,
            cookies_from_browser=args.cookies_from_browser,
            user_agent=args.user_agent,
            js_runtime=js_runtime,
        )
        if smoke.get("__error__"):
            print("Smoke test failed:", smoke.get("__error__").get("message"))
            print("Hint: provide cookies or user-agent, or run with --skip-smoke-test to continue")
            return 3
        print("Smoke OK")

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

    def pick_best_video(info: dict) -> t.Optional[dict]:
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

    processed = 0
    with open(csvfile, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if limit and processed >= limit:
                break
            processed += 1
            query = build_query(row)
            
            # Skip if already processed and not retrying
            if query in skip_queries:
                print(f"\n[{processed}] Query: {query} [SKIPPED: already processed]")
                continue
            
            print(f"\n[{processed}] Query: {query}")
            # Step 1: flat search — get video URL quickly without fetching full metadata.
            # Using --flat-playlist means yt-dlp returns just id/title/url for each
            # search result without running the player client to get formats.
            search = f"ytsearch1:{query}"
            flat_info = dump_json(search, cookies=args.cookies, cookies_from_browser=args.cookies_from_browser, user_agent=args.user_agent, js_runtime=js_runtime, timeout=30, flat=True)
            if flat_info.get("__error__"):
                reason = flat_info.get("__error__").get("message")
                print(f"  Search failed: {reason}")
                write_progress_entry(progress_log, query, "FAILED", f"search_error:{reason[:100]}")
                with open(failed_log, "a", encoding="utf-8") as ff:
                    ff.write(f"{query}\tERROR\t{reason}\n")
                continue

            # Pick the best video URL from flat search results
            flat_entry = pick_best_video(flat_info)

            # If flat search returned a channel/playlist URL, retry with refined query
            if flat_entry is None or is_channel_url(flat_entry.get("webpage_url") or flat_entry.get("url") or ""):
                if flat_entry is not None:
                    print(f"  Search returned a channel/playlist URL; retrying with refined query...")
                else:
                    print("  No immediate video result; trying refined query...")
                refined = f"{query} official audio"
                flat_info2 = dump_json(f"ytsearch5:{refined}", cookies=args.cookies, cookies_from_browser=args.cookies_from_browser, user_agent=args.user_agent, js_runtime=js_runtime, timeout=30, flat=True)
                flat_entry = None
                if not flat_info2.get("__error__"):
                    for e in ([pick_best_video(flat_info2)] + list(flat_info2.get("entries") or [])):
                        if e and is_video_entry(e) and not is_channel_url(e.get("webpage_url") or e.get("url") or ""):
                            flat_entry = e
                            break

            if not flat_entry:
                print("  No result from search")
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
                print(f"  Preflight failure (yt-dlp error): {reason}")
                write_progress_entry(progress_log, query, "FAILED", f"metadata_error:{reason[:100]}")
                with open(failed_log, "a", encoding="utf-8") as ff:
                    ff.write(f"{query}\tERROR\t{reason}\n")
                continue

            entry = pick_best_video(info) or info

            if not entry:
                print("  No result from search")
                write_progress_entry(progress_log, query, "FAILED", "no_result_after_metadata")
                with open(failed_log, "a", encoding="utf-8") as ff:
                    ff.write(f"{query}\tNO_RESULT\n")
                continue

            ok, reason = preflight_check(entry, max_duration=max_duration, max_filesize=max_filesize, min_views=min_views)
            if not ok:
                print(f"  Skipping: {reason}")
                write_progress_entry(progress_log, query, "SKIPPED", reason)
                with open(failed_log, "a", encoding="utf-8") as ff:
                    ff.write(f"{query}\tSKIPPED\t{reason}\n")
                continue

            # choose a concrete format id when possible
            fmt = select_format_id(entry)
            chosen = fmt or "bestaudio/best"
            webpage = entry.get("webpage_url") or entry.get("url") or None
            if not webpage:
                print("  No webpage URL available for download. Skipping.")
                write_progress_entry(progress_log, query, "FAILED", "no_webpage_url")
                with open(failed_log, "a", encoding="utf-8") as ff:
                    ff.write(f"{query}\tNO_WEBPAGE\n")
                continue

            outtmpl = os.path.join(target_dir, "%(title)s.%(ext)s")

            if dry_run:
                print(f"  Dry-run: would download {webpage} using format {chosen}")
                write_progress_entry(progress_log, query, "DRYRUN", "dry_run_mode")
                continue

            print(f"  Downloading {webpage} using format {chosen} ...")
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
                print(f"  Download failed: {msg}")
                lower = msg.lower()
                # If failure appears to be due to unavailable format, try to inspect formats and retry with a concrete id
                if ("requested format is not available" in lower) or ("format not available" in lower) or ("no formats" in lower) or ("requested format" in lower):
                    print("  Requested format not available; inspecting formats and retrying with concrete format_id...")
                    meta = dump_json(webpage, cookies=args.cookies, cookies_from_browser=args.cookies_from_browser, user_agent=args.user_agent, js_runtime=js_runtime)
                    if meta.get("__error__"):
                        fallback_reason = meta.get("__error__").get("message")
                        print(f"  Could not fetch metadata for fallback: {fallback_reason}")
                        write_progress_entry(progress_log, query, "FAILED", f"download_format_error:{msg[:80]}")
                        with open(failed_log, "a", encoding="utf-8") as ff:
                            ff.write(f"{query}\tDOWNLOAD_FAILED\t{msg[:200]} | fallback_meta_error:{(fallback_reason or '')[:200]}\n")
                    else:
                        fmt2 = select_format_id(meta)
                        if fmt2:
                            print(f"  Retrying download with concrete format id {fmt2} ...")
                            r2 = download_with_format(webpage, fmt2, outtmpl, cookies=args.cookies, cookies_from_browser=args.cookies_from_browser, user_agent=args.user_agent, js_runtime=js_runtime)
                            if r2.get("success"):
                                print(f"  Download succeeded with format id {fmt2}")
                                write_progress_entry(progress_log, query, "SUCCESS", f"retry_with_format:{fmt2}")
                            else:
                                print(f"  Retry failed: {r2.get('message')}")
                                write_progress_entry(progress_log, query, "FAILED", f"download_retry_failed:{r2.get('message')[:80]}")
                                with open(failed_log, "a", encoding="utf-8") as ff:
                                    ff.write(f"{query}\tDOWNLOAD_FAILED_RETRY\t{(r2.get('message') or '')[:200]}\n")
                        else:
                            print("  No audio-capable format id found to retry.")
                            write_progress_entry(progress_log, query, "FAILED", "no_audio_formats")
                            with open(failed_log, "a", encoding="utf-8") as ff:
                                ff.write(f"{query}\tNO_AUDIO_FORMATS\n")
                else:
                    write_progress_entry(progress_log, query, "FAILED", f"download_failed:{msg[:80]}")
                    with open(failed_log, "a", encoding="utf-8") as ff:
                        ff.write(f"{query}\tDOWNLOAD_FAILED\t{msg[:200]}\n")
            else:
                print("  Download succeeded")
                write_progress_entry(progress_log, query, "SUCCESS", "downloaded")

            # be polite to services
            time.sleep(0.5)

    print(f"\nProcessed: {processed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
