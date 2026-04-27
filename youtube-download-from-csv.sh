#!/usr/bin/env bash
# youtube-download-from-csv.sh
# Read a CSV of tracks, search each song on YouTube, download as MP3,
# and sort into playlist directories under a target directory.
# Usage: youtube-download-from-csv.sh input.csv target_dir
# Requires: yt-dlp, python3, ffmpeg (for audio conversion)

set -uo pipefail

DRY_RUN=0
LIMIT=0
START_INDEX=1

usage() {
  cat <<USAGE
Usage: $0 [--dry-run] [--limit N] <input.csv> <target_dir>

Downloads tracks listed in the CSV into <target_dir>/<playlist>/ as MP3.
CSV must contain a header with fields such as "Track name", "Artist name",
and "Playlist name". The script is tolerant of missing headers and will
attempt to use columns by position when needed.

Options:
  --dry-run     : don't download; show what would be downloaded
  --limit N     : process only the first N tracks
  --start N     : start processing at the Nth track (1-based)

Requires: yt-dlp, python3, ffmpeg (ffmpeg only needed for real downloads)
USAGE
  exit 1
}

# Parse optional flags
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1; shift ;;
    --limit|-n)
      LIMIT="$2"; shift 2 ;;
    --start|-s)
      START_INDEX="$2"; shift 2 ;;
    --help|-h)
      usage ;;
    --*)
      echo "Unknown flag: $1" >&2; usage ;;
    *)
      break ;;
  esac
done

if [ "$#" -lt 2 ]; then
  usage
fi

CSV="$1"
TARGET_DIR="$2"

if [ ! -f "$CSV" ]; then
  echo "Input CSV not found: $CSV" >&2
  exit 1
fi

# validate start index
if ! echo "$START_INDEX" | grep -qE '^[0-9]+$'; then
  echo "Invalid --start value: $START_INDEX" >&2
  exit 1
fi
if [ "$START_INDEX" -lt 1 ]; then
  echo "Invalid --start value: $START_INDEX (must be >= 1)" >&2
  exit 1
fi

if [ "$START_INDEX" -gt 1 ]; then
  echo "Resuming at track $START_INDEX"
fi

HAVE_PYTHON=0
# Prefer a working python3/python binary (reject Windows Store shim that prints a message)
if command -v python3 >/dev/null 2>&1; then
  if python3 -c 'import sys' >/dev/null 2>&1; then
    PY_EXEC=python3; HAVE_PYTHON=1
  fi
fi
if [ "$HAVE_PYTHON" -eq 0 ] && command -v python >/dev/null 2>&1; then
  if python -c 'import sys' >/dev/null 2>&1; then
    PY_EXEC=python; HAVE_PYTHON=1
  fi
fi

if [ "$DRY_RUN" -eq 0 ]; then
  : # we'll resolve yt-dlp path below
else
  :
fi

# Locate yt-dlp: prefer system binary, then a local ./tools copy
YTDLP_BIN=""
if command -v yt-dlp >/dev/null 2>&1; then
  YTDLP_BIN=$(command -v yt-dlp)
elif [ -x "./tools/yt-dlp.exe" ]; then
  YTDLP_BIN="./tools/yt-dlp.exe"
elif [ -x "./tools/yt-dlp" ]; then
  YTDLP_BIN="./tools/yt-dlp"
fi

if [ -z "$YTDLP_BIN" ]; then
  if [ "$DRY_RUN" -eq 0 ]; then
    echo "yt-dlp not found. Install yt-dlp (try: ${PY_EXEC:-python} -m pip install --user -U yt-dlp)" >&2
    exit 1
  else
    echo "Note: yt-dlp not found; dry-run will be limited." >&2
  fi
fi

command -v ffmpeg >/dev/null 2>&1 || echo "Warning: ffmpeg not found. Real downloads may fail without ffmpeg." >&2

STATE_DIR="$TARGET_DIR/.ydl_state"
mkdir -p "$STATE_DIR"
FAILED_LOG="$STATE_DIR/failed.log"
PROGRESS_LOG="$STATE_DIR/progress.log"

# Temporary TSV extracted from CSV (playlist<TAB>track<TAB>artist)
TMP_TSV=$(mktemp)
trap 'rm -f "$TMP_TSV"' EXIT

# Optional extra yt-dlp args (set via environment variables)
# Examples:
#  YTDLP_COOKIES_FROM_BROWSER=chrome \
#  YTDLP_COOKIES_FILE=cookies.txt \    # alternative to cookies-from-browser
#  YTDLP_PROXY=http://127.0.0.1:8080 \ # optional proxy
#  YTDLP_USER_AGENT='Mozilla/5.0 (...)' \
#  SLEEP_MIN=3 SLEEP_MAX=10 \
#  bash youtube-download-from-csv.sh input.csv downloads
YTDLP_EXTRA_ARGS=()
if [ -n "${YTDLP_COOKIES_FROM_BROWSER:-}" ]; then
  YTDLP_EXTRA_ARGS+=(--cookies-from-browser "${YTDLP_COOKIES_FROM_BROWSER}")
elif [ -n "${YTDLP_COOKIES_FILE:-}" ]; then
  YTDLP_EXTRA_ARGS+=(--cookies "${YTDLP_COOKIES_FILE}")
fi
if [ -n "${YTDLP_PROXY:-}" ]; then
  YTDLP_EXTRA_ARGS+=(--proxy "${YTDLP_PROXY}")
fi
if [ -n "${YTDLP_USER_AGENT:-}" ]; then
  YTDLP_EXTRA_ARGS+=(--add-header "User-Agent: ${YTDLP_USER_AGENT}")
fi

# yt-dlp built-in sleep/rate options (optional)
if [ -n "${YTDLP_SLEEP_REQUESTS:-}" ]; then
  YTDLP_EXTRA_ARGS+=(--sleep-requests "${YTDLP_SLEEP_REQUESTS}")
fi
if [ -n "${YTDLP_SLEEP_INTERVAL:-}" ]; then
  YTDLP_EXTRA_ARGS+=(--sleep-interval "${YTDLP_SLEEP_INTERVAL}")
fi
if [ -n "${YTDLP_MAX_SLEEP_INTERVAL:-}" ]; then
  YTDLP_EXTRA_ARGS+=(--max-sleep-interval "${YTDLP_MAX_SLEEP_INTERVAL}")
fi
if [ -n "${YTDLP_RETRY_SLEEP:-}" ]; then
  YTDLP_EXTRA_ARGS+=(--retry-sleep "${YTDLP_RETRY_SLEEP}")
fi

# jittered sleep config (seconds)
SLEEP_MIN=${SLEEP_MIN:-1}
SLEEP_MAX=${SLEEP_MAX:-3}

# Optional proxy list and user-agent list files (one entry per line)
# Set via env: YTDLP_PROXY_FILE, YTDLP_USER_AGENTS_FILE
PROXIES=()
UAS=()
if [ -n "${YTDLP_PROXY_FILE:-}" ] && [ -f "${YTDLP_PROXY_FILE}" ]; then
  while IFS= read -r p; do
    p_trim=$(echo "$p" | sed 's/^ *//; s/ *$//')
    [ -z "$p_trim" ] && continue
    case "$p_trim" in
      \#*) continue ;;
    esac
    PROXIES+=("$p_trim")
  done < "$YTDLP_PROXY_FILE"
fi
if [ -n "${YTDLP_USER_AGENTS_FILE:-}" ] && [ -f "${YTDLP_USER_AGENTS_FILE}" ]; then
  while IFS= read -r ua; do
    ua_trim=$(echo "$ua" | sed 's/^ *//; s/ *$//')
    [ -z "$ua_trim" ] && continue
    case "$ua_trim" in
      \#*) continue ;;
    esac
    UAS+=("$ua_trim")
  done < "$YTDLP_USER_AGENTS_FILE"
fi

# Build per-attempt yt-dlp args into CURRENT_YTDLP_ARGS array
build_ytdlp_args() {
  local attempt_idx=${1:-1}
  CURRENT_YTDLP_ARGS=("${YTDLP_EXTRA_ARGS[@]}")
  # rotate proxies if provided
  if [ "${#PROXIES[@]}" -gt 0 ]; then
    # use count variable to offset selection when available
    local base=0
    if [ -n "${count:-}" ]; then base=$count; fi
    local idx=$(( (base + attempt_idx - 1) % ${#PROXIES[@]} ))
    CURRENT_YTDLP_ARGS+=(--proxy "${PROXIES[$idx]}")
  fi
  # rotate user-agents if provided
  if [ "${#UAS[@]}" -gt 0 ]; then
    local base2=0
    if [ -n "${count:-}" ]; then base2=$count; fi
    local idx2=$(( (base2 + attempt_idx - 1) % ${#UAS[@]} ))
    CURRENT_YTDLP_ARGS+=(--add-header "User-Agent: ${UAS[$idx2]}")
  fi
}

if [ "$HAVE_PYTHON" -eq 1 ]; then
  # Try parsing with Python using several common encodings (utf-8-sig, utf-8, cp1252, latin-1)
  "$PY_EXEC" - "$CSV" > "$TMP_TSV" <<'PY'
import csv,sys
fn=sys.argv[1]
encodings = ['utf-8-sig', 'utf-8', 'cp1252', 'latin-1']
success = False
for enc in encodings:
    try:
        with open(fn, newline='', encoding=enc) as f:
            r = csv.DictReader(f)
            if r.fieldnames is None:
                f.seek(0)
                for row in csv.reader(f):
                    if not row: continue
                    playlist = row[3] if len(row)>3 else 'Unknown'
                    track = row[0] if len(row)>0 else ''
                    artist = row[1] if len(row)>1 else ''
                    if track: print('\t'.join([playlist.strip(), track.strip(), artist.strip()]))
            else:
                for row in r:
                    track = row.get('Track name') or row.get('track name') or row.get('Track') or row.get('Title') or row.get('title') or ''
                    artist = row.get('Artist name') or row.get('artist name') or row.get('Artist') or row.get('artist') or ''
                    playlist = row.get('Playlist name') or row.get('playlist name') or row.get('Playlist') or row.get('playlist') or 'Unknown'
                    track = track.strip()
                    artist = artist.strip()
                    playlist = playlist.strip() if playlist else 'Unknown'
                    if track:
                        print('\t'.join([playlist, track, artist]))
        success = True
        break
    except Exception:
        continue
if not success:
    sys.exit(1)
PY
fi

# If Python produced no output (e.g. encoding issues), fall back to awk/gawk parser
if [ ! -s "$TMP_TSV" ]; then
  if command -v gawk >/dev/null 2>&1 || awk --version 2>/dev/null | grep -q 'GNU Awk'; then
    awk 'BEGIN{FPAT = "([^,]+)|(\"([^\"]|\"\")*\")"; OFS="\t"}
    NR==1{
      for(i=1;i<=NF;i++){
        v=$i; gsub(/^\"|\"$/,"",v); f=tolower(v);
        if(f ~ /track/) idx_track=i;
        if(f ~ /artist/) idx_artist=i;
        if(f ~ /playlist/) idx_playlist=i;
      }
      if(!idx_track) idx_track=1; if(!idx_artist) idx_artist=2; if(!idx_playlist) idx_playlist=3; next
    }
    {
      track=$idx_track; artist=$idx_artist; playlist=$idx_playlist;
      gsub(/^\"|\"$/,"",track); gsub(/^\"|\"$/,"",artist); gsub(/^\"|\"$/,"",playlist);
      print playlist, track, artist
    }' "$CSV" > "$TMP_TSV" || true
  fi
fi

total=$(wc -l < "$TMP_TSV" | tr -d ' ')
if [ -z "$total" ] || [ "$total" -eq 0 ]; then
  echo "No tracks found in CSV. Nothing to do."; exit 0
fi

info="Found $total tracks."
if [ "$LIMIT" -gt 0 ]; then
  info="$info Processing up to $LIMIT tracks."
fi
if [ "$DRY_RUN" -eq 1 ]; then
  info="$info (dry-run mode)"
fi
echo "$info Starting downloads..."

# sanitize: basic filename-safe substitutions
sanitize() {
  local s="$1"
  s="${s//\\/}";
  s="${s//\//_}";
  s="${s//:/_}";
  s="${s//\*/_}";
  s="${s//\?/}";
  s="${s//\"/}";
  s="${s//</_}";
  s="${s//>/_}";
  s="${s//|/_}";
  s="${s//$'\r'/}";
  # collapse spaces and trim
  s=$(echo "$s" | tr -s ' ' | sed 's/^ *//; s/ *$//')
  printf "%s" "$s"
}

count=0
while IFS=$'\t' read -r playlist track artist || [ -n "$track" ]; do
  count=$((count+1))
  # skip until start index (1-based)
  if [ "$count" -lt "$START_INDEX" ]; then
    continue
  fi
  if [ "$LIMIT" -gt 0 ] && [ "$count" -gt "$LIMIT" ]; then
    echo "Reached limit ($LIMIT). Stopping."
    break
  fi
  [ -z "$playlist" ] && playlist="Unknown"
  pdir=$(sanitize "$playlist")
  tdir="$TARGET_DIR/$pdir"
  mkdir -p "$tdir"
  fname=$(sanitize "$track - $artist")
  target="$tdir/$fname.mp3"

  if [ -f "$target" ]; then
    echo "[$count/$total] Skipping (exists): $target"
    echo "$playlist|$track|$artist|skipped" >> "$PROGRESS_LOG"
    continue
  fi

  echo "[$count/$total] Searching/Downloading: $track - $artist (Playlist: $playlist)"
  tmpd=$(mktemp -d)

  max_attempts=3
  attempt=0
  success=0
  # try a series of query templates per track to increase chance of finding an accessible video
  queries=()
  queries+=("$track $artist audio")
  queries+=("$track $artist")
  queries+=("$track official audio")
  queries+=("$track $artist official video")
  queries+=("$track $artist lyric")
  queries+=("$artist $track audio")
  while [ $attempt -lt $max_attempts ]; do
    attempt=$((attempt+1))
    echo "  Attempt $attempt/$max_attempts"
    if [ "$DRY_RUN" -eq 1 ]; then
      if [ -n "$YTDLP_BIN" ]; then
        auth_error=0
        for q in "${queries[@]}"; do
          echo "    Query: $q"
          build_ytdlp_args "$attempt"
          tmp_err=$(mktemp)
          fn=$($YTDLP_BIN --no-warnings --no-playlist --get-filename -o "%(title)s.%(ext)s" "${CURRENT_YTDLP_ARGS[@]}" "ytsearch1:${q}" 2> "$tmp_err" | head -n1 || true)
          err=$(cat "$tmp_err" || true)
          rm -f "$tmp_err"
          if [ -n "$fn" ]; then
            echo "  Would download: $fn -> $target"
            echo "$playlist|$track|$artist|dryrun" >> "$PROGRESS_LOG"
            success=1
            break 2
          fi
          if echo "$err" | grep -Ei 'Sign in to confirm|not a bot|Failed to decrypt with DPAPI|could not find .* cookies database' >/dev/null 2>&1; then
            auth_error=1
            echo "    Query produced auth/cookie error; trying next template..."
            continue
          fi
        done
        if [ "$auth_error" -eq 1 ] && [ "$success" -ne 1 ]; then
          echo "  Dry-run: yt-dlp requires authentication/cookies (bot check or DPAPI error)."
          echo "  Hint: export browser cookies to a cookies.txt file and re-run with YTDLP_COOKIES_FILE=path, or run natively on Windows and use YTDLP_COOKIES_FROM_BROWSER=<browser>."
          echo "  (See README for export instructions)"
          echo "$playlist|$track|$artist|failed_auth" >> "$PROGRESS_LOG"
          break
        fi
      else
        echo "  Dry-run: yt-dlp not installed; cannot resolve results."
      fi
      sleep 1
    else
      auth_error=0
      for q in "${queries[@]}"; do
        echo "    Query: $q"
        build_ytdlp_args "$attempt"
        tmp_err=$(mktemp)
        $YTDLP_BIN --no-warnings --no-overwrites --no-playlist "${CURRENT_YTDLP_ARGS[@]}" -o "$tmpd/%(id)s.%(ext)s" -f bestaudio "ytsearch1:${q}" --extract-audio --audio-format mp3 --audio-quality 0 2> "$tmp_err"
        rc=$?
        err=$(cat "$tmp_err" || true)
        rm -f "$tmp_err"
        found=$(find "$tmpd" -type f -iname '*.mp3' -print -quit || true)
        if [ -n "$found" ] && [ -s "$found" ]; then
          mv "$found" "$target"
          echo "  Saved: $target"
          echo "$playlist|$track|$artist|done" >> "$PROGRESS_LOG"
          success=1
          break 2
        fi
        if echo "$err" | grep -Ei 'Sign in to confirm|not a bot|Failed to decrypt with DPAPI|could not find .* cookies database' >/dev/null 2>&1; then
          auth_error=1
          echo "    Query produced auth/cookie error; trying next template..."
          continue
        fi
        if echo "$err" | grep -Ei 'HTTP Error 429|Too Many Requests|rate limit|quota' >/dev/null 2>&1; then
          backoff=$((60 + RANDOM % 120))
          echo "  Detected rate-limiting signal. Backing off for $backoff seconds..."
          sleep $backoff
        else
          echo "  Attempt $attempt for query failed (rc=$rc). Will try next template or retry later."
          sleep $((attempt * 2 + RANDOM % 4))
        fi
      done
      if [ "$auth_error" -eq 1 ] && [ "$success" -ne 1 ]; then
        echo "  yt-dlp requires authentication/cookies (bot check or DPAPI error). Skipping track."
        echo "  Hint: export browser cookies to a cookies.txt file and re-run with YTDLP_COOKIES_FILE=path, or run natively on Windows and use YTDLP_COOKIES_FROM_BROWSER=<browser>."
        echo "  (See README for export instructions)"
        echo "$playlist|$track|$artist|failed_auth" >> "$PROGRESS_LOG"
        break
      fi
    fi
  done

  if [ $success -ne 1 ]; then
    echo "Failed to download: $track - $artist" | tee -a "$FAILED_LOG"
    echo "$playlist|$track|$artist|failed" >> "$PROGRESS_LOG"
  fi

  rm -rf "$tmpd"
  # small randomized sleep between tracks to reduce rate-limiting/bot-detection
  if [ "$SLEEP_MAX" -ge "$SLEEP_MIN" ] && [ "$SLEEP_MAX" -gt 0 ]; then
    min=$SLEEP_MIN; max=$SLEEP_MAX
    if [ "$max" -lt "$min" ]; then max=$min; fi
    if [ "$min" -eq "$max" ]; then
      sleep_time=$min
    else
      range=$((max - min + 1))
      sleep_time=$((min + RANDOM % range))
    fi
    echo "Sleeping $sleep_time seconds before next track..."
    sleep "$sleep_time"
  fi
done < "$TMP_TSV"

echo "All done. Failures (if any) logged in: $FAILED_LOG"
