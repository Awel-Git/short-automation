"""Upload the verified NeonClipping batch to the channel authorized by the OAuth token."""
import json
import os
from pathlib import Path
import sys

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
TOKEN_URI = "https://oauth2.googleapis.com/token"


def main():
    single_file = os.environ.get("SHORTS_INPUT_FILE")
    if single_file:
        clips = [Path(single_file)]
    else:
        directory = Path(os.environ["SHORTS_OUTPUT_DIR"])
        clips = [directory / f"short-{number:02d}.mp4" for number in range(1, 4)]
    if not all(clip.is_file() and clip.stat().st_size > 0 for clip in clips):
        raise ValueError("The finished MP4 files must be present before uploading.")

    raw_token = os.environ.get("YOUTUBE_TOKEN_JSON")
    if not raw_token:
        raise ValueError("Add the YOUTUBE_TOKEN_JSON repository Actions secret.")
    token = json.loads(raw_token)
    credentials = Credentials(
        token=None, token_uri=TOKEN_URI, client_id=token["client_id"],
        client_secret=token["client_secret"], refresh_token=token["refresh_token"],
        scopes=[UPLOAD_SCOPE],
    )
    credentials.refresh(Request())
    summary = Path(os.environ["GITHUB_STEP_SUMMARY"])
    public_count = 0
    with build("youtube", "v3", credentials=credentials, cache_discovery=False) as youtube:
        for number, clip in enumerate(clips, start=1):
            print(f"Uploading {clip.name}", flush=True)
            title = os.environ.get("SHORTS_TITLE", "").strip()[:92] or f"Highlight {number}"
            upload = youtube.videos().insert(
                part="snippet,status",
                body={
                    "snippet": {"title": f"{title} #Shorts", "categoryId": "22"},
                    "status": {"privacyStatus": "public"},
                },
                media_body=MediaFileUpload(str(clip), mimetype="video/mp4",
                                           chunksize=8 * 1024 * 1024, resumable=True),
            )
            result = None
            while result is None:
                _, result = upload.next_chunk(num_retries=3)
            visibility = result.get("status", {}).get("privacyStatus", "unknown")
            message = f"{clip.name}: https://www.youtube.com/watch?v={result['id']} ({visibility})"
            print(message, flush=True)
            with summary.open("a", encoding="utf-8") as output:
                output.write(f"- {message}\n")
            public_count += visibility == "public"
    if public_count != len(clips):
        raise ValueError("YouTube did not report public visibility for every upload. Check the run summary and YouTube Studio.")


if __name__ == "__main__":
    try:
        main()
    except json.JSONDecodeError:
        sys.exit("YOUTUBE_TOKEN_JSON must contain valid JSON.")
    except KeyError as error:
        sys.exit(f"Missing required configuration or YouTube response field: {error}")
    except RefreshError:
        sys.exit("Google rejected the OAuth refresh. Check the client and renew channel authorization.")
    except HttpError as error:
        sys.exit(f"YouTube upload failed (HTTP {error.resp.status}): {error.reason}")
    except ValueError as error:
        sys.exit(str(error))
