"""Helpers for interacting with yt-dlp (module or CLI) and format selection.

This module prefers the `yt_dlp` Python module when available and falls
back to invoking the `yt-dlp` CLI when necessary. It exposes a minimal
API used by `download_from_csv.py`.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import typing as t

try:
    import yt_dlp as _yt_dlp
except Exception:
    _yt_dlp = None


def detect_ytdlp() -> dict:
    """Return a dict describing availability of yt-dlp usage.

    Keys:
      - "module": the imported yt_dlp module or None
      - "bin": path to `yt-dlp` CLI or None
    """
    return {"module": _yt_dlp, "bin": shutil.which("yt-dlp")}


def detect_js_runtime() -> t.Optional[tuple[str, str]]:
    """Detect an available JavaScript runtime for yt-dlp EJS (name, path).

    Returns the first matching runtime (name, path) or None if none found.
    Common runtimes: deno, node, jsc, d8.
    """
    for name in ("deno", "node", "jsc", "d8"):
        p = shutil.which(name)
        if p:
            return name, p
    return None


def _parse_first_json_from_text(text: str) -> t.Optional[dict]:
    # yt-dlp --dump-json may emit one JSON object per line; find the
    # first line that parses as JSON and return it.
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except Exception:
            # try to recover multi-line JSON (rare) by looking for leading '{'
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except Exception:
                    continue
            continue
    return None


def dump_json(url: str, *, cookies: t.Optional[str] = None, user_agent: t.Optional[str] = None,
              cookies_from_browser: t.Optional[str] = None, js_runtime: t.Optional[str] = None,
              timeout: int = 30) -> dict:
    """Return extracted metadata for `url`.

    Returns a dict when JSON could be parsed. If yt-dlp prints only an error
    message (no JSON), the returned dict will contain an `__error__` key with
    a short message under `message`.
    """
    info = None
    ytd = detect_ytdlp()
    # If caller requested cookies-from-browser, prefer CLI path (module can't import browser cookies)
    use_cli = False
    if cookies_from_browser:
        use_cli = True

    if ytd["module"] is not None and not use_cli:
        opts = {"quiet": True, "no_warnings": True, "skip_download": True}
        if cookies:
            opts["cookiefile"] = cookies
        if user_agent:
            opts.setdefault("http_headers", {})["User-Agent"] = user_agent
        # support passing a js runtime string (e.g. 'deno' or 'deno:/path')
        if js_runtime:
            opts["jsruntimes"] = js_runtime
            opts["js_runtimes"] = js_runtime
        try:
            ydl = ytd["module"].YoutubeDL(opts)
            info = ydl.extract_info(url, download=False)
            return info if info is not None else {"__error__": {"message": "no info returned"}}
        except Exception as e:
            # fallback to CLI path below
            fallback_err = str(e)
            use_cli = True
    else:
        fallback_err = None

    # CLI fallback
    bin_path = ytd.get("bin")
    if not bin_path:
        return {"__error__": {"message": "yt-dlp not installed (no module or CLI available)"}}

    cmd = [bin_path, "--no-warnings", "--no-playlist", "--skip-download", "--dump-json", url]
    if cookies:
        cmd += ["--cookies", cookies]
    if cookies_from_browser:
        cmd += ["--cookies-from-browser", cookies_from_browser]
    if user_agent:
        cmd += ["--add-header", f"User-Agent: {user_agent}"]
    # if caller provided js_runtime or we can auto-detect, pass it to CLI
    if js_runtime:
        cmd += ["--js-runtimes", js_runtime]
    else:
        auto = detect_js_runtime()
        if auto:
            cmd += ["--js-runtimes", f"{auto[0]}:{auto[1]}"]

    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        return {"__error__": {"message": f"yt-dlp CLI failure: {e}"}}

    content = (p.stdout or "").strip()
    if not content:
        # some errors are printed to stderr (e.g. 'Requested format is not available')
        stderr = (p.stderr or "").strip()
        msg = stderr or fallback_err or "no output from yt-dlp"
        return {"__error__": {"message": msg}}

    parsed = _parse_first_json_from_text(content)
    if parsed is not None:
        return parsed

    # no JSON found but stdout had data: return a short error-containing dict
    return {"__error__": {"message": content}}


def select_format_id(info: dict) -> t.Optional[str]:
    """Pick a concrete audio-capable `format_id` from `info['formats']`.

    Preference order: highest `abr`, then highest `tbr`, then first audio-capable.
    Returns None if no audio-capable formats found.
    """
    formats = info.get("formats") or []
    if not formats:
        return None

    audio_candidates = []
    for f in formats:
        # skip image/storyboard formats and entries with no audio codec
        if f.get("acodec") and f.get("acodec") != "none":
            audio_candidates.append(f)

    if not audio_candidates:
        return None

    def score(f):
        return (f.get("abr") or f.get("tbr") or 0)

    best = max(audio_candidates, key=score)
    return str(best.get("format_id"))


def download_with_format(url: str, format_selector: t.Optional[str], outtmpl: str,
                         *, cookies: t.Optional[str] = None, user_agent: t.Optional[str] = None,
                         cookies_from_browser: t.Optional[str] = None,
                         js_runtime: t.Optional[str] = None, quiet: bool = False) -> dict:
    """Download `url` using a concrete format selector (or textual selector).

    Returns a dict with `success`: bool and `message` details.
    If `cookies_from_browser` is provided, prefer invoking the yt-dlp CLI since
    the Python module cannot import browser cookies directly.
    """
    ytd = detect_ytdlp()

    # If caller requested cookies-from-browser, prefer CLI path
    use_cli = False
    if cookies_from_browser:
        use_cli = True

    if ytd["module"] is not None and not use_cli:
        opts = {"format": format_selector or "bestaudio/best", "outtmpl": outtmpl}
        # attempt to extract audio to mp3 via ffmpeg postprocessor
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
        if cookies:
            opts["cookiefile"] = cookies
        if user_agent:
            opts.setdefault("http_headers", {})["User-Agent"] = user_agent
        if js_runtime:
            opts["jsruntimes"] = js_runtime
            opts["js_runtimes"] = js_runtime
        if quiet:
            opts["quiet"] = True
            opts["no_warnings"] = True

        try:
            ydl = ytd["module"].YoutubeDL(opts)
            ydl.download([url])
            return {"success": True, "message": "downloaded"}
        except Exception as e:
            # fallback to CLI when module fails
            use_cli = True

    # CLI fallback
    bin_path = ytd.get("bin")
    if not bin_path:
        return {"success": False, "message": "yt-dlp not installed"}

    cmd = [bin_path]
    if format_selector:
        cmd += ["-f", str(format_selector)]
    else:
        cmd += ["-f", "bestaudio/best"]
    cmd += ["--extract-audio", "--audio-format", "mp3", "--audio-quality", "192K", "-o", outtmpl, url]
    if cookies:
        cmd += ["--cookies", cookies]
    if cookies_from_browser:
        cmd += ["--cookies-from-browser", cookies_from_browser]
    if user_agent:
        cmd += ["--add-header", f"User-Agent: {user_agent}"]
    if js_runtime:
        cmd += ["--js-runtimes", js_runtime]
    else:
        auto = detect_js_runtime()
        if auto:
            cmd += ["--js-runtimes", f"{auto[0]}:{auto[1]}"]

    try:
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode == 0:
            return {"success": True, "message": "downloaded"}
        else:
            return {"success": False, "message": (p.stderr or p.stdout)[:1000]}
    except Exception as e:
        return {"success": False, "message": str(e)}


def parse_size_to_bytes(s: t.Union[str, int, None]) -> t.Optional[int]:
    """Parse human sizes like '30M', '500K' into integer bytes.

    If input already an int, return as-is.
    """
    if s is None:
        return None
    if isinstance(s, int):
        return s
    if isinstance(s, str):
        s = s.strip().upper()
        try:
            if s.endswith("G"):
                return int(float(s[:-1]) * 1024 ** 3)
            if s.endswith("M"):
                return int(float(s[:-1]) * 1024 ** 2)
            if s.endswith("K"):
                return int(float(s[:-1]) * 1024)
            return int(s)
        except Exception:
            return None
    return None


def preflight_check(info: dict, max_duration: int | None = None, max_filesize: int | None = None,
                    min_views: int | None = None) -> tuple[bool, str]:
    """Return (ok: bool, reason: str)."""
    if not info:
        return False, "no_info"
    if "__error__" in info:
        return False, f"yt-dlp-error: {info['__error__'].get('message')[:200]}"

    duration = info.get("duration")
    if duration and max_duration and duration > max_duration:
        return False, "duration_exceeded"

    # check filesize if available or estimate from abr
    filesize = info.get("filesize") or info.get("filesize_approx")
    if filesize and max_filesize and filesize > max_filesize:
        return False, "filesize_exceeded"

    if (not filesize) and max_filesize:
        # try to estimate using the highest abr available
        formats = info.get("formats") or []
        abr = None
        for f in formats:
            if f.get("acodec") and f.get("acodec") != "none" and f.get("abr"):
                try:
                    val = float(f.get("abr"))
                    if abr is None or val > abr:
                        abr = val
                except Exception:
                    continue
        if abr and duration:
            # abr is in kbits/s -> bytes = (kbits/s * 1000 / 8) * seconds
            est_bytes = int((abr * 1000.0 / 8.0) * float(duration))
            if max_filesize and est_bytes > max_filesize:
                return False, "filesize_exceeded_estimate"

    views = info.get("view_count")
    if views is not None and min_views is not None:
        try:
            if int(views) < int(min_views):
                return False, "views_too_low"
        except Exception:
            pass

    return True, "ok"
