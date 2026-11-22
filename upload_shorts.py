
import os
import json
import time
import random
from pathlib import Path

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

# CONFIGURATION
WATCH_FOLDER = Path(
    os.getenv("SHORTS_OUTPUT_DIR", "output")
)

PRIVACY_STATUS = "public"
CATEGORY_ID = "20"  # Gaming
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload"
]

TOKEN_FILE = Path("token.json")
TOKEN_SECRET = os.getenv("YOUTUBE_TOKEN_JSON")

MAX_RETRIES = 5


def get_youtube_service():
    """Authenticate using an existing OAuth token."""

    if TOKEN_SECRET:
        token_info = json.loads(TOKEN_SECRET)
    elif TOKEN_FILE.exists():
        token_info = json.loads(
            TOKEN_FILE.read_text(encoding="utf-8")
        )
    else:
        raise RuntimeError(
            "Missing YouTube OAuth token. "
            "Set YOUTUBE_TOKEN_JSON in GitHub Secrets."
        )

    creds = Credentials.from_authorized_user_info(
        token_info, SCOPES
    )

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            raise RuntimeError(
                "YouTube authorization expired. "
                "Generate a new OAuth token."
            )

    return build(
        "youtube",
        "v3",
        credentials=creds,
        cache_discovery=False
    )


def upload_short(youtube, filepath):
    """Upload one MP4 directly to YouTube."""

    filename = filepath.stem.replace("_", " ")
    filename = filename.replace("-", " ").strip()

    title = f"{filename[:85]} #Shorts"

    body = {
        "snippet": {
            "title": title,
            "description": (
                f"{filename}\n\n"
                "#Shorts #Gaming"
            ),
            "categoryId": CATEGORY_ID
        },
        "status": {
            "privacyStatus": PRIVACY_STATUS,
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(
        str(filepath),
        mimetype="video/mp4",
        resumable=True,
        chunksize=1024 * 1024 * 8
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )

    response = None
    retries = 0

    while response is None:
        try:
            status, response = request.next_chunk()

            if status:
                print(
                    f"Uploading {filepath.name}: "
                    f"{int(status.progress() * 100)}%"
                )

        except (HttpError, OSError) as error:
            retryable = isinstance(error, OSError)

            if isinstance(error, HttpError):
                retryable = error.resp.status in [
                    500, 502, 503, 504
                ]

            if not retryable or retries >= MAX_RETRIES:
                raise

            retries += 1
            delay = min(
                2 ** retries + random.random(), 60
            )

            print(
                f"Retry {retries}/{MAX_RETRIES} "
                f"after {delay:.1f}s: {error}"
            )
            time.sleep(delay)

    video_id = response["id"]

    print(
        f"Uploaded: https://www.youtube.com/watch?v="
        f"{video_id}"
    )

    return video_id


def main():
    if not WATCH_FOLDER.is_dir():
        raise FileNotFoundError(
            f"Output folder not found: {WATCH_FOLDER}"
        )

    videos = sorted(
        p for p in WATCH_FOLDER.rglob("*.mp4")
        if p.is_file() and p.stat().st_size > 0
    )

    if not videos:
        raise RuntimeError(
            "No MP4 videos found to upload."
        )

    print(f"Found {len(videos)} videos.")

    youtube = get_youtube_service()
    uploaded = []
    failures = []

    for video in videos:
        try:
            video_id = upload_short(youtube, video)
            uploaded.append({
                "file": video.name,
                "youtube_id": video_id
            })

        except Exception as error:
            print(f"Failed: {video.name}: {error}")
            failures.append(video.name)

    Path("upload_results.json").write_text(
        json.dumps({
            "uploaded": uploaded,
            "failed": failures
        }, indent=2),
        encoding="utf-8"
    )

    print(
        f"Completed: {len(uploaded)} uploaded, "
        f"{len(failures)} failed."
    )

    if failures:
        raise RuntimeError(
            f"Failed uploads: {', '.join(failures)}"
        )


if __name__ == "__main__":
    main()
