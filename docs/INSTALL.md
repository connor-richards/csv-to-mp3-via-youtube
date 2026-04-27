# Installation

Platform-specific installation notes for the downloader's dependencies.

Windows (recommended using `winget`)

1. Install `yt-dlp` and FFmpeg via winget:

```powershell
winget install --id yt-dlp.yt-dlp -e --accept-package-agreements --accept-source-agreements
winget install --id Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements
```

2. Install Python (if you don't have it):

```powershell
winget install --id Python.Python.3.12 -e
# or use the Microsoft Store or anaconda/miniconda
```

Alternative: install `yt-dlp` with pip (cross-platform):

```bash
python -m pip install --user -U yt-dlp
```

Linux / macOS

Debian/Ubuntu example:

```bash
sudo apt update
sudo apt install -y python3 python3-pip ffmpeg
python3 -m pip install --user -U yt-dlp
```

macOS (homebrew):

```bash
brew install yt-dlp ffmpeg
```

Portable tools

- If installing globally is not possible, place `yt-dlp` and `ffmpeg` into
  `./tools/` (the script prefers `./tools/yt-dlp.exe` on Windows). The script
  will detect those local binaries and use them.
