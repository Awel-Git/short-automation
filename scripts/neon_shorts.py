"""Render and download exactly three captioned Shorts from the local NeonClipping API."""
import argparse
import json
from pathlib import Path
import math
import re
import shutil
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, parse_qs
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8770"
SOURCE = "https://www.youtube.com/watch?v=hwSg39yIBGU"
COUNT = 3
SIZE = (1080, 1920)


def source_url(value):
    url = urlsplit(value)
    if url.scheme != "https" or url.netloc not in {"www.youtube.com", "youtube.com", "youtu.be"}:
        raise ValueError("Enter an HTTPS YouTube watch, live, or share link.")
    if url.netloc == "youtu.be":
        video = url.path.removeprefix("/")
    elif url.path.startswith("/live/"):
        video = url.path.removeprefix("/live/")
    else:
        video = parse_qs(url.query).get("v", [""])[0] if url.path == "/watch" else ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video):
        raise ValueError("The link must identify one YouTube video.")
    return f"https://www.youtube.com/watch?v={video}"


def check_source(url):
    from yt_dlp.utils import DownloadError
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ytdl import YDL

    try:
        with YDL({"noplaylist": True, "quiet": True, "socket_timeout": 20}) as downloader:
            info = downloader.extract_info(url, download=False)
    except DownloadError as error:
        raise RuntimeError(f"YouTube source check failed: {error}") from error
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming", "post_live"):
        raise RuntimeError("YouTube has no completed replay ready. Wait for the archive or use another completed video.")
    if not info.get("duration"):
        raise RuntimeError("YouTube did not report a duration for this video.")
    print(f"Source ready ({info['duration']} seconds).", flush=True)


def api(route, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(BASE + route, data, {"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=15) as response:
            return json.load(response)
    except HTTPError as error:
        detail = error.read(1024).decode(errors="replace")
        raise RuntimeError(f"{route}: HTTP {error.code}: {detail}") from error


def wait_for_server():
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            api("/openapi.json")
            return
        except (URLError, TimeoutError):
            time.sleep(1)
    raise RuntimeError("NeonClipping did not start within 90 seconds.")


def job_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise RuntimeError(f"Unexpected NeonClipping job ID: {value!r}")
    return value


def upload_source(path):
    response = subprocess.run(
        ["curl", "--silent", "--show-error", "--fail-with-body", "--max-time", "300",
         "--form", f"file=@{path};filename=source.mp4;type=video/mp4",
         f"{BASE}/upload"],
        capture_output=True, text=True, check=False,
    )
    if response.returncode != 0:
        detail = response.stdout.strip() or response.stderr.strip()
        raise RuntimeError(f"NeonClipping could not accept the MP4: {detail}")
    return job_id(json.loads(response.stdout)["id"])


def wait_for_clips(batch):
    deadline = time.monotonic() + 70 * 60
    last_stage = None
    while time.monotonic() < deadline:
        result = api(f"/make/{batch}")
        stage = result.get("stage")
        if stage != last_stage:
            print(stage, flush=True)
            last_stage = stage
        status = result["status"]
        if status == "error":
            raise RuntimeError(f"NeonClipping job failed: {result.get('error')}")
        if status == "done":
            clips = result["clips"]
            if len(clips) != COUNT:
                raise RuntimeError(f"Expected {COUNT} clips, got {len(clips)}. Last stage: {stage}")
            return clips
        if status != "running":
            raise RuntimeError(f"Unexpected NeonClipping status: {status!r}")
        time.sleep(5)
    raise RuntimeError("NeonClipping exceeded the 70-minute render limit.")


def download_clip(clip, destination):
    clip_id = job_id(clip.get("file"))
    export = api(f"/status/{clip_id}")
    if clip.get("error") or export["status"] != "done":
        detail = clip.get("error") or export.get("error") or export["status"]
        raise RuntimeError(f"{destination.name}: export failed: {detail}")
    if export.get("caption_words", 0) < 1:
        raise RuntimeError(f"{destination.name}: the export reported no caption words.")

    with urlopen(f"{BASE}/media/{clip_id}", timeout=60) as response:
        with destination.open("wb") as output:
            shutil.copyfileobj(response, output)

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(destination)],
        capture_output=True, text=True, check=True, timeout=30,
    )
    media = json.loads(probe.stdout)
    video = next((stream for stream in media["streams"] if stream["codec_type"] == "video"), {})
    dimensions = (video.get("width"), video.get("height"))
    duration = float(media["format"].get("duration", 0))
    if (video.get("codec_name") != "h264" or dimensions != SIZE
            or "mp4" not in media["format"]["format_name"].split(",")
            or not math.isfinite(duration) or not 19.5 <= duration <= 45.5
            or not any(stream["codec_type"] == "audio" and stream.get("codec_name") == "aac"
                       for stream in media["streams"])):
        raise RuntimeError(f"{destination.name}: expected a 1080x1920 H.264/AAC MP4 near 20–45s, "
                           f"got {dimensions} and {duration:.2f}s.")
    print(f"{destination.name}: {dimensions[0]}x{dimensions[1]}, "
          f"{export['caption_words']} caption words", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path, nargs="?")
    parser.add_argument("--source", default=SOURCE)
    parser.add_argument("--check-source")
    parser.add_argument("--video-file", type=Path)
    args = parser.parse_args()
    if args.check_source:
        check_source(source_url(args.check_source))
        return
    if args.output_dir is None:
        parser.error("output_dir is required when rendering")

    wait_for_server()
    source = {"id": upload_source(args.video_file)} if args.video_file else {"url": source_url(args.source)}
    batch = job_id(api("/make", {
        **source,
        "count": COUNT,
        "min_len": 20,
        "max_len": 45,
        "mode": "fast",
        "accurate": "fast",
        "language": "",
        "top": .32,
        "aspect": "vertical",
        "resolution": 1080,
        "source_height": 720,
        "fps": 30,
        "layout": "blur",
        "caption_style": "gta",
    })["job"])
    clips = wait_for_clips(batch)
    ids = {job_id(clip.get("file")) for clip in clips}
    if len(ids) != COUNT:
        raise RuntimeError("NeonClipping returned duplicate clip IDs.")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    for number, clip in enumerate(clips, start=1):
        download_clip(clip, args.output_dir / f"short-{number:02d}.mp4")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f"NeonClipping: {error}", file=sys.stderr)
        sys.exit(1)
