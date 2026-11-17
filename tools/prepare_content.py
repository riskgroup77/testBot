"""Yangi test uchun materiallarni tayyorlaydi.

    python tools/prepare_content.py <loyiha-nomi> <test.pdf> <kalit-sahifalar-soni> [video.mp4]

- PDF dan oxirgi <kalit-sahifalar-soni> sahifa (javoblar kaliti) olib tashlanadi —
  o'quvchiga javobsiz nusxa yuboriladi.
- Video Telegram cheklovi (50 MB) ga sig'ishi uchun sifatini yo'qotmasdan qismlarga bo'linadi.
Natija: content/<loyiha-nomi>/test.pdf va video_1.mp4, video_2.mp4 ...
"""
import math
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg
from pypdf import PdfReader, PdfWriter

BASE = Path(__file__).resolve().parent.parent
LIMIT_MB = 45  # Telegram bot cheklovi 50 MB, zaxira bilan


def strip_key_pages(src: Path, dst: Path, key_pages: int) -> int:
    reader = PdfReader(src)
    writer = PdfWriter()
    keep = len(reader.pages) - key_pages
    for page in reader.pages[:keep]:
        writer.add_page(page)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("wb") as f:
        writer.write(f)
    return keep


def split_video(src: Path, out_dir: Path) -> list[Path]:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    size_mb = src.stat().st_size / 1024 / 1024
    probe = subprocess.run([ffmpeg, "-hide_banner", "-i", str(src)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace").stderr
    dur_str = probe.split("Duration:")[1].split(",")[0].strip()
    h, m, s = dur_str.split(":")
    duration = int(h) * 3600 + int(m) * 60 + float(s)
    parts = max(1, math.ceil(size_mb / LIMIT_MB * 1.15))
    seg = math.ceil(duration / parts)
    for old in out_dir.glob("video_*.mp4"):
        old.unlink()
    subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
        "-c", "copy", "-map", "0", "-f", "segment", "-segment_time", str(seg),
        "-reset_timestamps", "1", "-segment_format_options", "movflags=+faststart",
        "-segment_start_number", "1", str(out_dir / "video_%d.mp4"),
    ], check=True)
    files = sorted(out_dir.glob("video_*.mp4"), key=lambda p: int(p.stem.split("_")[1]))
    for f in files:
        mb = f.stat().st_size / 1024 / 1024
        if mb > 49:
            raise SystemExit(f"{f.name} {mb:.1f} MB — juda katta, LIMIT_MB ni kamaytiring")
    return files


if __name__ == "__main__":
    name, pdf, key_pages = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3])
    out = BASE / "content" / name
    kept = strip_key_pages(pdf, out / "test.pdf", key_pages)
    print(f"test.pdf: {kept} sahifa (kalit olib tashlandi)")
    if len(sys.argv) > 4:
        for f in split_video(Path(sys.argv[4]), out):
            print(f"{f.name}: {f.stat().st_size / 1024 / 1024:.1f} MB")
