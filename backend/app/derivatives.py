"""Thumbnail + preview generation (DESIGN §3 derivatives).

Photos go straight through Pillow; videos have a frame pulled by ffmpeg first,
then the same resize path. All blocking (Pillow + subprocess) — callers run this
via a thread (see processing.py).
"""

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass

from PIL import Image, ImageOps

from app import storage

THUMB_MAX = 320
PREVIEW_MAX = 2048
_SIZES = (("thumb", THUMB_MAX), ("preview", PREVIEW_MAX))


@dataclass
class DerivativeFile:
    kind: str
    storage_key: str
    width: int
    height: int


@dataclass
class BuildResult:
    derivatives: list[DerivativeFile]
    width: int
    height: int
    duration_ms: int | None


def _save_resized(source: Image.Image, max_side: int, dest_key: str) -> tuple[int, int]:
    im = source.copy()
    im.thumbnail((max_side, max_side))  # preserves aspect ratio, never upscales
    dest = storage.abspath(dest_key)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    im.convert("RGB").save(dest, "JPEG", quality=85)
    return im.size


def _video_metadata(path: str) -> tuple[int, int, int | None]:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height:format=duration",
            "-of", "json", path,
        ],
        check=True, capture_output=True, text=True,
    ).stdout
    data = json.loads(out)
    stream = data["streams"][0]
    duration = data.get("format", {}).get("duration")
    duration_ms = int(float(duration) * 1000) if duration else None
    return int(stream["width"]), int(stream["height"]), duration_ms


def _video_frame(path: str) -> str:
    fd, tmp = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    subprocess.run(
        ["ffmpeg", "-y", "-i", path, "-frames:v", "1", "-q:v", "3", tmp],
        check=True, capture_output=True,
    )
    return tmp


def build(media_id, kind: str, original_abs: str) -> BuildResult:
    tmp_frame: str | None = None
    duration_ms: int | None = None
    try:
        if kind == "video":
            width, height, duration_ms = _video_metadata(original_abs)
            tmp_frame = _video_frame(original_abs)
            source = ImageOps.exif_transpose(Image.open(tmp_frame))
        else:
            source = ImageOps.exif_transpose(Image.open(original_abs))
            width, height = source.size

        files = []
        for d_kind, max_side in _SIZES:
            key = storage.derivative_key(media_id, d_kind)
            w, h = _save_resized(source, max_side, key)
            files.append(DerivativeFile(d_kind, key, w, h))
        return BuildResult(files, width, height, duration_ms)
    finally:
        if tmp_frame and os.path.exists(tmp_frame):
            os.remove(tmp_frame)
