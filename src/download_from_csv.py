#!/usr/bin/env python3
"""Download MP3s from a CSV using yt-dlp with safer preflight checks.

Usage (basic):
  python3 src/download_from_csv.py sample_test.csv test_downloads --dry-run

This script performs a metadata preflight (duration, filesize, views)
and prefers to select a concrete `format_id` from yt-dlp's `formats` list
for robust downloads.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
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

    processed = 0
    with open(csvfile, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if limit and processed >= limit:
                break
            processed += 1
            query = build_query(row)
            print(f"\n[{processed}] Query: {query}")
            search = f"ytsearch1:{query}"
            info = dump_json(search, cookies=args.cookies, cookies_from_browser=args.cookies_from_browser, user_agent=args.user_agent, js_runtime=js_runtime)
            if info.get("__error__"):
                reason = info.get("__error__").get("message")
                print(f"  Preflight failure (yt-dlp error): {reason}")
                with open(failed_log, "a", encoding="utf-8") as ff:
                    ff.write(f"{query}\tERROR\t{reason}\n")
                continue

            # ytsearch1 returns a dict with 'entries' (list)
            entry = None
            if isinstance(info, dict) and info.get("entries"):
                if isinstance(info.get("entries"), list) and info.get("entries"):
                    entry = info.get("entries")[0]
            if entry is None and isinstance(info, dict):
                entry = info

            if not entry:
                print("  No result from search")
                with open(failed_log, "a", encoding="utf-8") as ff:
                    ff.write(f"{query}\tNO_RESULT\n")
                continue

            ok, reason = preflight_check(entry, max_duration=max_duration, max_filesize=max_filesize, min_views=min_views)
            if not ok:
                print(f"  Skipping: {reason}")
                with open(failed_log, "a", encoding="utf-8") as ff:
                    ff.write(f"{query}\tSKIPPED\t{reason}\n")
                continue

            # choose a concrete format id when possible
            fmt = select_format_id(entry)
            chosen = fmt or "bestaudio/best"
            webpage = entry.get("webpage_url") or entry.get("url") or None
            if not webpage:
                print("  No webpage URL available for download. Skipping.")
                with open(failed_log, "a", encoding="utf-8") as ff:
                    ff.write(f"{query}\tNO_WEBPAGE\n")
                continue

            outtmpl = os.path.join(target_dir, "%(title)s.%(ext)s")

            if dry_run:
                print(f"  Dry-run: would download {webpage} using format {chosen}")
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
                        with open(failed_log, "a", encoding="utf-8") as ff:
                            ff.write(f"{query}\tDOWNLOAD_FAILED\t{msg[:200]} | fallback_meta_error:{(fallback_reason or '')[:200]}\n")
                    else:
                        fmt2 = select_format_id(meta)
                        if fmt2:
                            print(f"  Retrying download with concrete format id {fmt2} ...")
                            r2 = download_with_format(webpage, fmt2, outtmpl, cookies=args.cookies, cookies_from_browser=args.cookies_from_browser, user_agent=args.user_agent, js_runtime=js_runtime)
                            if r2.get("success"):
                                print(f"  Download succeeded with format id {fmt2}")
                            else:
                                print(f"  Retry failed: {r2.get('message')}")
                                with open(failed_log, "a", encoding="utf-8") as ff:
                                    ff.write(f"{query}\tDOWNLOAD_FAILED_RETRY\t{(r2.get('message') or '')[:200]}\n")
                        else:
                            print("  No audio-capable format id found to retry.")
                            with open(failed_log, "a", encoding="utf-8") as ff:
                                ff.write(f"{query}\tNO_AUDIO_FORMATS\n")
                else:
                    with open(failed_log, "a", encoding="utf-8") as ff:
                        ff.write(f"{query}\tDOWNLOAD_FAILED\t{msg[:200]}\n")
            else:
                print("  Download succeeded")

            # be polite to services
            time.sleep(0.5)

    print(f"\nProcessed: {processed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
