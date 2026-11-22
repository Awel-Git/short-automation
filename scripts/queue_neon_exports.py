"""Queue new, completed NeonClipping exports for GitHub-hosted YouTube upload."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

REPO = "Awel-Git/short-automation"
API = f"https://api.github.com/repos/{REPO}"
DEFAULT_DATA = Path(os.environ["LOCALAPPDATA"]) / "NeonClipping" / "app" / "data"


def github_token():
    response = subprocess.run(
        ["git", "-c", "credential.interactive=never", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True, check=False,
    )
    for line in response.stdout.splitlines():
        if line.startswith("password="):
            return line.removeprefix("password=")
    raise RuntimeError("Sign in to GitHub with Git Credential Manager before starting the queue.")


def request(token, url, payload, video=None):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    if video is None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    else:
        headers.update({"Content-Type": "video/mp4", "Content-Length": str(os.fstat(video.fileno()).st_size)})
        data = video
    try:
        with urlopen(Request(url, data, headers), timeout=180) as response:
            body = response.read()
    except HTTPError as error:
        raise RuntimeError(f"GitHub returned HTTP {error.code}: {error.read(1000).decode(errors='replace')}") from None
    return json.loads(body) if body else None


def save(path, state):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.replace(path)


def finished(data, since):
    for metadata in data.glob("*.json"):
        if not re.fullmatch(r"[0-9a-f]{32}", metadata.stem) or metadata.stat().st_mtime < since:
            continue
        try:
            record = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        result = record.get("result", {})
        clip = metadata.with_suffix(".mp4")
        if result.get("status") == "done" and result.get("file") == clip.stem and clip.is_file():
            yield clip, record.get("settings", {}).get("title") or "NeonClipping highlight"


def queue(token, clip, title, state_path, state):
    item = state["clips"].setdefault(clip.stem, {})
    if item.get("dispatched"):
        return
    if "release_id" not in item:
        release = request(token, f"{API}/releases", {
            "tag_name": f"neon-clip-{clip.stem}", "name": "NeonClipping upload queue",
            "draft": True, "target_commitish": "main",
        })
        item["release_id"] = release["id"]
        save(state_path, state)
    if "asset_id" not in item:
        url = f"https://uploads.github.com/repos/{REPO}/releases/{item['release_id']}/assets?name=finished.mp4"
        with clip.open("rb") as video:
            item["asset_id"] = request(token, url, None, video)["id"]
        save(state_path, state)
    request(token, f"{API}/actions/workflows/neon.yml/dispatches", {
        "ref": "main", "inputs": {"asset_id": str(item["asset_id"]), "title": title[:92]},
    })
    item["dispatched"] = True
    save(state_path, state)
    print(f"Queued {clip.name}. GitHub can finish the upload with this PC off.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--file", type=Path, help="Queue one finished MP4 already on this PC.")
    args = parser.parse_args()
    if not args.data.is_dir():
        parser.error(f"NeonClipping data folder does not exist: {args.data}")
    token = github_token()
    try:
        with urlopen(Request(f"{API}/actions/secrets/YOUTUBE_TOKEN_JSON", headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        }), timeout=20):
            pass
    except HTTPError as error:
        raise RuntimeError("Add the YOUTUBE_TOKEN_JSON Actions secret before starting the queue.") from error
    state_path = args.data / "youtube-queue-state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    else:
        state = {"since": time.time(), "clips": {}}
        save(state_path, state)
    if args.file:
        if args.file.suffix.lower() != ".mp4" or not args.file.is_file():
            parser.error("--file must point to a finished MP4")
        queue(token, args.file, "NeonClipping highlight", state_path, state)
        return
    print("Watching for new completed exports. Keep this window open while NeonClipping runs.", flush=True)
    while True:
        for clip, title in finished(args.data, state["since"]):
            queue(token, clip, title, state_path, state)
        time.sleep(4)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
    except (OSError, RuntimeError, ValueError) as error:
        sys.exit(str(error))
