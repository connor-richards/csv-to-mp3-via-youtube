#!/usr/bin/env bash
# youtube-download-from-csv.sh
# Read a CSV of tracks, search each song on YouTube, download as MP3.
# By default files are saved directly under the target directory.
# If --use-playlists is provided, files are sorted into <target_dir>/<playlist>/.
# Usage: youtube-download-from-csv.sh [--use-playlists] [--enable-sleep] input.csv target_dir
# Requires: yt-dlp, python3, ffmpeg (for audio conversion)

set -uo pipefail

DRY_RUN=0
LIMIT=0
START_INDEX=1
USE_PLAYLISTS=0
ENABLE_SLEEP=0

# Colorized output helpers (TTY-aware). Set NO_COLOR to disable colors.
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  RED=$'\e[31m'
  GREEN=$'\e[32m'
  YELLOW=$'\e[33m'
  BLUE=$'\e[34m'
  MAGENTA=$'\e[35m'
  CYAN=$'\e[36m'
  BOLD=$'\e[1m'
  RESET=$'\e[0m'
else
  RED=''; GREEN=''; YELLOW=''; BLUE=''; MAGENTA=''; CYAN=''; BOLD=''; RESET=''
fi

ce_section()  { printf "%b\n" "${BOLD}${CYAN}$*${RESET}"; }
ce_heading()  { printf "%b\n" "${BOLD}${MAGENTA}$*${RESET}"; }
ce_info()     { printf "%b\n" "${CYAN}$*${RESET}"; }
ce_success()  { printf "%b\n" "${GREEN}$*${RESET}"; }
ce_warn()     { printf "%b\n" "${YELLOW}$*${RESET}" >&2; }
ce_error()    { printf "%b\n" "${RED}$*${RESET}" >&2; }

usage() {
  cat <<USAGE
Usage: $0 [--dry-run] [--limit N] [--start N] [--use-playlists] [--enable-sleep] <input.csv> <target_dir>

Downloads tracks listed in the CSV into <target_dir>/ as MP3 by default.
If --use-playlists is supplied, files are sorted into <target_dir>/<playlist>/.
CSV should include fields such as "Track name" and "Artist name". "Playlist name" is optional.

Options:
  --dry-run       : don't download; show what would be downloaded
  --limit N       : process only the first N tracks
  --start N       : start processing at the Nth track (1-based)
  --use-playlists : sort downloaded files into playlist folders under target dir
  --enable-sleep  : enable randomized sleep between tracks (uses SLEEP_MIN/SLEEP_MAX env vars)

Requires: yt-dlp, python3, ffmpeg (ffmpeg only needed for real downloads)
 
Environment variables:
  MAX_DURATION    : maximum allowed duration (seconds) for a candidate video (default: 600)
  MAX_FILESIZE    : maximum allowed estimated filesize (e.g. 30M). Used with yt-dlp --max-filesize as a fallback.
  MIN_VIEWS       : minimum allowed view count (e.g. 10000). Used to prefer/popular results.
USAGE
  exit 1
}

# Preflight check using yt-dlp metadata (JSON) to skip unreasonably long/large candidates.
# Returns 0 when OK, 2 when duration exceeded, 3 when filesize exceeded, 4 when view count too low.
preflight_check() {
  local info_json_file="$1"
  if [ -z "$info_json_file" ] || [ ! -s "$info_json_file" ]; then
  return 0
  fi
  if [ "${HAVE_PYTHON:-0}" -eq 0 ]; then
  # Can't do deep checks without Python; rely on --max-filesize during download
  return 0
  fi
  "$PY_EXEC" - "$info_json_file" "$MAX_DURATION" "$MAX_FILESIZE" "$MIN_VIEWS" <<'PY'
import sys, json, re
fn=sys.argv[1]
try:
  maxdur=int(sys.argv[2]) if sys.argv[2] else 0
except:
  maxdur=0
maxfs_str=sys.argv[3] if len(sys.argv)>3 else ''
min_views_str=sys.argv[4] if len(sys.argv)>4 else ''

def parse_size(s):
  if not s: return None
  m=re.match(r'^(\d+(?:\.\d+)?)([KkMmGg]?)$', s)
  if not m:
    try:
      return int(s)
    except:
      return None
  val=float(m.group(1)); unit=m.group(2).upper()
  if unit=='K': return int(val*1024)
  if unit=='M': return int(val*1024*1024)
  if unit=='G': return int(val*1024*1024*1024)
  return int(val)

def parse_count(s):
  if not s: return None
  m=re.match(r'^(\d+(?:\.\d+)?)([KkMmGg]?)$', s)
  if not m:
    try:
      return int(s)
    except:
      return None
  val=float(m.group(1)); unit=m.group(2).upper()
  if unit=='K': return int(val*1000)
  if unit=='M': return int(val*1000*1000)
  if unit=='G': return int(val*1000*1000*1000)
  return int(val)

maxfs=parse_size(maxfs_str)
min_views=parse_count(min_views_str) if min_views_str else None
try:
  with open(fn,'r') as fh:
    text = fh.read()
    try:
      data = json.loads(text)
    except Exception:
      # try line-delimited JSON (yt-dlp may emit multiple JSON objects); pick first valid one
      data = None
      for line in text.splitlines():
        line=line.strip()
        if not line: continue
        try:
          data = json.loads(line)
          break
        except:
          continue
      if data is None:
        sys.exit(0)
except Exception:
  sys.exit(0)
duration = data.get('duration') or 0
if maxdur and duration and duration > maxdur:
  print('duration_exceeded')
  sys.exit(2)
# check view count (if provided) against min_views
view_count = data.get('view_count')
if min_views and view_count is not None:
  try:
    vc = int(view_count)
  except:
    vc = None
  if vc is not None and vc < min_views:
    print('views_too_low')
    sys.exit(4)
formats = data.get('formats') or []
bestaudio = None
for f in formats:
  if f.get('vcodec') in (None,'none','') and f.get('acodec') not in (None,'none',''):
    if bestaudio is None or (f.get('abr') or 0) > (bestaudio.get('abr') or 0):
      bestaudio = f
if bestaudio is None and formats:
  bestaudio = sorted(formats, key=lambda x: (x.get('abr') or x.get('tbr') or 0), reverse=True)[0]
size = None
if bestaudio:
  size = bestaudio.get('filesize') or bestaudio.get('filesize_approx')
if not size:
  size = data.get('filesize') or data.get('filesize_approx')
if not size and bestaudio and bestaudio.get('abr') and duration:
  try:
    abr = float(bestaudio.get('abr'))
    size = int((abr * 1000.0 / 8.0) * duration)
  except Exception:
    size = None
if size and maxfs and size > maxfs:
  print('filesize_exceeded')
  sys.exit(3)
sys.exit(0)
PY
  return $?
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
    --use-playlists)
      USE_PLAYLISTS=1; shift ;;
    --enable-sleep)
      ENABLE_SLEEP=1; shift ;;
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
  ce_error "Input CSV not found: $CSV"
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
  ce_info "Resuming at track $START_INDEX"
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
    ce_error "yt-dlp not found. Install yt-dlp (try: ${PY_EXEC:-python} -m pip install --user -U yt-dlp)"
    exit 1
  else
    ce_warn "Note: yt-dlp not found; dry-run will be limited."
  fi
fi

command -v ffmpeg >/dev/null 2>&1 || ce_warn "Warning: ffmpeg not found. Real downloads may fail without ffmpeg."

# Optional smoke test to verify yt-dlp can fetch metadata (useful during tests)
ytdlp_smoke_test() {
  if [ -z "${YTDLP_BIN:-}" ]; then
    ce_warn "yt-dlp not found; cannot run smoke test."
    return 1
  fi
  if [ "${SKIP_SMOKE_TEST:-0}" = "1" ]; then
    ce_warn "Skipping yt-dlp smoke test (SKIP_SMOKE_TEST=1)"
    return 0
  fi
  local url="${SMOKE_TEST_URL:-https://www.youtube.com/watch?v=dQw4w9WgXcQ}"
  local tmpf
  tmpf=$(mktemp)
  # run a lightweight metadata fetch
  "$YTDLP_BIN" --no-warnings --no-playlist --skip-download --dump-json "$url" > "$tmpf" 2>&1 || true
  local out
  out=$(cat "$tmpf" || true)
  rm -f "$tmpf"
  if [ -z "$out" ]; then
    ce_warn "yt-dlp smoke test produced no output."
    return 2
  fi
  if printf '%s' "$out" | grep -Ei 'Sign in to confirm|not a bot|Failed to decrypt with DPAPI|could not find .* cookies database' >/dev/null 2>&1; then
    ce_warn "yt-dlp smoke test indicates auth/cookie requirement."
    return 3
  fi
  if printf '%s' "$out" | grep -Ei 'HTTP Error 429|Too Many Requests|rate limit|quota' >/dev/null 2>&1; then
    ce_warn "yt-dlp smoke test indicates rate-limiting."
    return 4
  fi
  if printf '%s' "$out" | grep -Ei '"id"|"webpage_url"|"title"' >/dev/null 2>&1; then
    ce_info "yt-dlp smoke test OK."
    return 0
  fi
  ce_warn "yt-dlp smoke test returned unexpected output."
  return 5
}

# Run a quick smoke test to detect blocking when performing real downloads
if [ -n "${YTDLP_BIN:-}" ] && [ "$DRY_RUN" -eq 0 ]; then
  ytdlp_smoke_test
  rc_smoke=$?
  if [ "$rc_smoke" -ne 0 ]; then
    ce_error "yt-dlp smoke test failed (rc=$rc_smoke). This environment may be blocked or requires authentication/proxy."
    ce_info "Hints: provide cookies via YTDLP_COOKIES_FILE or YTDLP_COOKIES_FROM_BROWSER, set YTDLP_USER_AGENT, or use YTDLP_PROXY to route traffic."
    ce_info "To force continuation despite smoke test failures set SKIP_SMOKE_TEST=1 in the environment."
    exit 1
  fi
fi

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

# jittered sleep config (seconds; used only when --enable-sleep is passed)
SLEEP_MIN=${SLEEP_MIN:-1}
SLEEP_MAX=${SLEEP_MAX:-3}

# Safety thresholds (can be overridden via environment variables)
# MAX_DURATION: skip candidates with duration (seconds) greater than this
# MAX_FILESIZE: human-readable size (e.g. 30M). Used as a yt-dlp --max-filesize fallback.
MAX_DURATION=${MAX_DURATION:-600}
MAX_FILESIZE=${MAX_FILESIZE:-30M}
# MIN_VIEWS: minimum view count to prefer popular videos (can use K/M suffixes)
MIN_VIEWS=${MIN_VIEWS:-10000}

# Convert human-friendly counts (10K, 1.2M) into integers for --match-filter
parse_count_bash() {
  local s="${1:-}"
  [ -z "$s" ] && printf "" && return
  if printf '%s' "$s" | grep -Eq '^[0-9]+$'; then
    printf '%s' "$s"
    return
  fi
  if printf '%s' "$s" | grep -Eq '^[0-9]+(\.[0-9]+)?[kKmMgG]$'; then
    local num; num=$(printf '%s' "$s" | sed -E 's/^([0-9]+(\.[0-9]+)?)[kKmMgG]$/\1/')
    local unit; unit=$(printf '%s' "$s" | sed -E 's/^[0-9]+(\.[0-9]+)?([kKmMgG])$/\2/' | tr '[:lower:]' '[:upper:]')
    local mult=1
    case "$unit" in
      K) mult=1000 ;; 
      M) mult=1000000 ;; 
      G) mult=1000000000 ;; 
    esac
    awk -v n="$num" -v m="$mult" 'BEGIN{printf("%d", n*m)}'
    return
  fi
  # fallback: take leading digits
  printf '%s' "$s" | sed -E 's/^([0-9]+).*/\1/'
}

# Numeric form for match-filter (empty if parse failed)
MIN_VIEWS_NUM=$(parse_count_bash "${MIN_VIEWS:-}")

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
  # enforce max filesize (user-overridable)
  if [ -n "${MAX_FILESIZE:-}" ]; then
    CURRENT_YTDLP_ARGS+=(--max-filesize "${MAX_FILESIZE}")
  fi
  # prefer videos with at least MIN_VIEWS_NUM views (server-side filter when supported)
  if [ -n "${MIN_VIEWS_NUM:-}" ]; then
    CURRENT_YTDLP_ARGS+=(--match-filter "view_count >= ${MIN_VIEWS_NUM}")
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
ce_section "$info Starting downloads..."

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
  if [ "$USE_PLAYLISTS" -eq 1 ]; then
    [ -z "$playlist" ] && playlist="Unknown"
    pdir=$(sanitize "$playlist")
    tdir="$TARGET_DIR/$pdir"
    mkdir -p "$tdir"
  else
    tdir="$TARGET_DIR"
    mkdir -p "$tdir"
  fi
  fname=$(sanitize "$track - $artist")
  target="$tdir/$fname.mp3"

  if [ -f "$target" ]; then
    ce_info "[$count/$total] Skipping (exists): $target"
    echo "$playlist|$track|$artist|skipped" >> "$PROGRESS_LOG"
    continue
  fi

  ce_heading "[$count/$total] Searching/Downloading: $track - $artist (Playlist: $playlist)"
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
          # preflight: inspect metadata to skip long/large candidates; prefer downloading the direct video URL
          tmp_info=$(mktemp)
          $YTDLP_BIN --no-warnings --no-playlist --skip-download --dump-json "${CURRENT_YTDLP_ARGS[@]}" "ytsearch1:${q}" > "$tmp_info" 2>/dev/null || true
          dl_url=""
          if [ -s "$tmp_info" ]; then
            preflight_check "$tmp_info"
            rc_pf=$?
            if [ "$rc_pf" -eq 2 ]; then
              ce_warn "    Dry-run: candidate skipped (duration > ${MAX_DURATION}s)"
              echo "$playlist|$track|$artist|skipped_duration" >> "$PROGRESS_LOG"
              rm -f "$tmp_info"
              continue
            elif [ "$rc_pf" -eq 3 ]; then
              ce_warn "    Dry-run: candidate skipped (filesize > ${MAX_FILESIZE})"
              echo "$playlist|$track|$artist|skipped_filesize" >> "$PROGRESS_LOG"
              rm -f "$tmp_info"
              continue
            elif [ "$rc_pf" -eq 4 ]; then
              ce_warn "    Dry-run: candidate skipped (views < ${MIN_VIEWS})"
              echo "$playlist|$track|$artist|skipped_views" >> "$PROGRESS_LOG"
              rm -f "$tmp_info"
              continue
            fi
            # extract a direct URL for the top search result to avoid playlist download quirks
            dl_url=$($PY_EXEC - "$tmp_info" <<'PY'
import sys,json
fn=sys.argv[1]
def first_json(fn):
  try:
    return json.load(open(fn))
  except:
    with open(fn) as fh:
      for line in fh:
        line=line.strip()
        if not line: continue
        try:
          return json.loads(line)
        except:
          continue
  return None
data=first_json(fn)
if not data:
  sys.exit(0)
# search results may be a playlist with 'entries'
entry = None
if isinstance(data, dict) and data.get('entries'):
  entries = data.get('entries')
  if isinstance(entries, list) and entries:
    entry = entries[0]
elif isinstance(data, list) and data:
  entry = data[0]
else:
  entry = data
if not entry:
  sys.exit(0)
url = entry.get('webpage_url') or entry.get('url') or entry.get('original_url') or ('https://www.youtube.com/watch?v=' + str(entry.get('id','')))
print(url)
PY
)
          fi
          rm -f "$tmp_info"
          tmp_err=$(mktemp)
          # determine what to ask yt-dlp to resolve for filenames (prefer direct URL when available)
          if [ -n "$dl_url" ]; then
            fn=$($YTDLP_BIN --no-warnings --no-playlist --get-filename -o "%(title)s.%(ext)s" "${CURRENT_YTDLP_ARGS[@]}" "$dl_url" 2> "$tmp_err" | head -n1 || true)
          else
            fn=$($YTDLP_BIN --no-warnings --no-playlist --get-filename -o "%(title)s.%(ext)s" "${CURRENT_YTDLP_ARGS[@]}" "ytsearch1:${q}" 2> "$tmp_err" | head -n1 || true)
          fi
          err=$(cat "$tmp_err" || true)
          rm -f "$tmp_err"
          if [ -n "$fn" ]; then
            ce_info "  Would download: $fn -> $target"
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
        # preflight: inspect metadata to skip long/large candidates; prefer a direct video URL
        tmp_info=$(mktemp)
        $YTDLP_BIN --no-warnings --no-playlist --skip-download --dump-json "${CURRENT_YTDLP_ARGS[@]}" "ytsearch1:${q}" > "$tmp_info" 2>/dev/null || true
        if [ -s "$tmp_info" ]; then
          preflight_check "$tmp_info"
          rc_pf=$?
          if [ "$rc_pf" -eq 2 ]; then
            ce_warn "    Candidate skipped (duration > ${MAX_DURATION}s)"
            echo "$playlist|$track|$artist|skipped_duration" >> "$PROGRESS_LOG"
            rm -f "$tmp_info"
            continue
          elif [ "$rc_pf" -eq 3 ]; then
            ce_warn "    Candidate skipped (filesize > ${MAX_FILESIZE})"
            echo "$playlist|$track|$artist|skipped_filesize" >> "$PROGRESS_LOG"
            rm -f "$tmp_info"
            continue
          elif [ "$rc_pf" -eq 4 ]; then
            ce_warn "    Candidate skipped (views < ${MIN_VIEWS})"
            echo "$playlist|$track|$artist|skipped_views" >> "$PROGRESS_LOG"
            rm -f "$tmp_info"
            continue
          fi
          dl_url=$($PY_EXEC - "$tmp_info" <<'PY'
import sys,json
fn=sys.argv[1]
def first_json(fn):
  try:
    return json.load(open(fn))
  except:
    with open(fn) as fh:
      for line in fh:
        line=line.strip()
        if not line: continue
        try:
          return json.loads(line)
        except:
          continue
  return None
data=first_json(fn)
if not data:
  sys.exit(0)
entry = None
if isinstance(data, dict) and data.get('entries'):
  entries = data.get('entries')
  if isinstance(entries, list) and entries:
    entry = entries[0]
elif isinstance(data, list) and data:
  entry = data[0]
else:
  entry = data
if not entry:
  sys.exit(0)
url = entry.get('webpage_url') or entry.get('url') or entry.get('original_url') or ('https://www.youtube.com/watch?v=' + str(entry.get('id','')))
print(url)
PY
)
        fi
        rm -f "$tmp_info"
        # If we have a direct URL, download that; otherwise fall back to search query
        target_source="$([ -n "${dl_url:-}" ] && printf "%s" "${dl_url}" || printf "ytsearch1:%s" "$q")"
        # Try several format selections to handle videos where 'bestaudio' isn't available
        found=''
        for fmt_choice in 'bestaudio' 'bestaudio/best' 'best' ''; do
          tmp_err=$(mktemp)
          if [ -n "$fmt_choice" ]; then
            $YTDLP_BIN --no-warnings --no-overwrites --no-playlist "${CURRENT_YTDLP_ARGS[@]}" -o "$tmpd/%(id)s.%(ext)s" -f "$fmt_choice" "$target_source" --extract-audio --audio-format mp3 --audio-quality 0 2> "$tmp_err"
          else
            $YTDLP_BIN --no-warnings --no-overwrites --no-playlist "${CURRENT_YTDLP_ARGS[@]}" -o "$tmpd/%(id)s.%(ext)s" "$target_source" --extract-audio --audio-format mp3 --audio-quality 0 2> "$tmp_err"
          fi
          rc=$?
          err=$(cat "$tmp_err" || true)
          rm -f "$tmp_err"
          found=$(find "$tmpd" -type f -iname '*.mp3' -print -quit || true)
          if [ -n "$found" ] && [ -s "$found" ]; then
            break
          fi
          # if error indicates requested format not available, try next fmt_choice
          if echo "$err" | grep -Ei 'Requested format is not available|format not available' >/dev/null 2>&1; then
            ce_warn "    Format '$fmt_choice' not available; trying fallback format..."
            continue
          fi
          # If other non-recoverable error, break and handle normally
          if [ $rc -ne 0 ]; then
            break
          fi
        done
        if [ -n "$found" ] && [ -s "$found" ]; then
          mv "$found" "$target"
          ce_success "  Saved: $target"
          echo "$playlist|$track|$artist|done" >> "$PROGRESS_LOG"
          success=1
          break 2
        fi
        if echo "$err" | grep -Ei 'Sign in to confirm|not a bot|Failed to decrypt with DPAPI|could not find .* cookies database' >/dev/null 2>&1; then
          auth_error=1
          ce_warn "    Query produced auth/cookie error; trying next template..."
          continue
        fi
        if echo "$err" | grep -Ei 'HTTP Error 429|Too Many Requests|rate limit|quota' >/dev/null 2>&1; then
          backoff=$((60 + RANDOM % 120))
          ce_warn "  Detected rate-limiting signal. Backing off for $backoff seconds..."
          sleep $backoff
        else
          ce_warn "  Attempt $attempt for query failed (rc=$rc). Will try next template or retry later."
          sleep $((attempt * 2 + RANDOM % 4))
        fi
      done
      if [ "$auth_error" -eq 1 ] && [ "$success" -ne 1 ]; then
        ce_warn "  yt-dlp requires authentication/cookies (bot check or DPAPI error). Skipping track."
        ce_info "  Hint: export browser cookies to a cookies.txt file and re-run with YTDLP_COOKIES_FILE=path, or run natively on Windows and use YTDLP_COOKIES_FROM_BROWSER=<browser>."
        ce_info "  (See README for export instructions)"
        echo "$playlist|$track|$artist|failed_auth" >> "$PROGRESS_LOG"
        break
      fi
    fi
  done

  if [ $success -ne 1 ]; then
    ce_error "Failed to download: $track - $artist"
    echo "$track - $artist" | tee -a "$FAILED_LOG"
    echo "$playlist|$track|$artist|failed" >> "$PROGRESS_LOG"
  fi

  rm -rf "$tmpd"
  # small randomized sleep between tracks to reduce rate-limiting/bot-detection (optional)
  if [ "$ENABLE_SLEEP" -eq 1 ]; then
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
  fi
done < "$TMP_TSV"

echo "All done. Failures (if any) logged in: $FAILED_LOG"
