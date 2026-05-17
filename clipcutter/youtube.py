"""YouTube Data API v3 integration for uploading clips."""

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Union
from urllib.parse import urlencode

from clipcutter import config

# Per-chunk HTTP timeout (seconds). A stalled TCP socket inside
# next_chunk() would otherwise wedge the upload worker forever, so we
# install this on the httplib2 transport used by the YouTube client.
YOUTUBE_HTTP_TIMEOUT_SECONDS = 120

# Retry budget for each chunk's HTTPS PUT. googleapiclient retries with
# exponential backoff on transient network errors when num_retries > 0,
# but eventually gives up so cancellation can take effect.
YOUTUBE_CHUNK_RETRIES = 3

# Timeout for one-shot OAuth token endpoint calls. A wedged Google
# token endpoint must not pin a FastAPI request thread (the OAuth
# callback is synchronous), so cap each request well below the default
# Uvicorn/HTTP read timeout.
OAUTH_HTTP_TIMEOUT_SECONDS = 30


@dataclass
class YouTubeCredentials:
    """OAuth2 credentials for YouTube API access."""
    access_token: str
    refresh_token: str
    token_expiry: Optional[Union[str, int]]
    client_id: str
    client_secret: str


@dataclass
class UploadResult:
    """Result of a YouTube upload attempt.

    cancelled distinguishes a user-initiated abort (event set mid-upload)
    from a genuine error, so the route can render a "Cancelled" pill
    instead of a red error banner.
    """
    success: bool
    video_id: Optional[str] = None
    error: Optional[str] = None
    url: Optional[str] = None
    cancelled: bool = False


def get_auth_url(client_id: str, redirect_uri: str) -> str:
    """Construct a Google OAuth2 authorization URL."""
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(config.YOUTUBE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


def exchange_code(code: str, client_id: str, client_secret: str,
                  redirect_uri: str) -> YouTubeCredentials:
    """Exchange an authorization code for OAuth2 credentials."""
    import requests

    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=OAUTH_HTTP_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = resp.json()

    return YouTubeCredentials(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token", ""),
        token_expiry=data.get("expires_in"),
        client_id=client_id,
        client_secret=client_secret,
    )


def refresh_access_token(creds: YouTubeCredentials) -> YouTubeCredentials:
    """Refresh an expired access token."""
    import requests

    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "refresh_token": creds.refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=OAUTH_HTTP_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = resp.json()

    return YouTubeCredentials(
        access_token=data["access_token"],
        refresh_token=creds.refresh_token,
        token_expiry=data.get("expires_in"),
        client_id=creds.client_id,
        client_secret=creds.client_secret,
    )


def save_credentials(creds: YouTubeCredentials, path: Path) -> None:
    """Save credentials to a JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "access_token": creds.access_token,
        "refresh_token": creds.refresh_token,
        "token_expiry": creds.token_expiry,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_credentials(path: Path) -> Optional[YouTubeCredentials]:
    """Load credentials from a JSON file, or None if missing/corrupt."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return YouTubeCredentials(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            token_expiry=data.get("token_expiry"),
            client_id=data["client_id"],
            client_secret=data["client_secret"],
        )
    except (json.JSONDecodeError, KeyError):
        return None


def get_authenticated_service(creds: YouTubeCredentials,
                              timeout: int = YOUTUBE_HTTP_TIMEOUT_SECONDS):
    """Return a YouTube API service with auto-refreshing credentials.

    The google.oauth2.credentials.Credentials object handles token refresh
    automatically when refresh_token and token_uri are provided.

    A per-request HTTP timeout is installed on the httplib2 transport so
    a wedged TCP socket inside an upload chunk can't pin the worker
    indefinitely — every next_chunk() will surface a socket.timeout that
    next_chunk's own retry logic (num_retries) can recover from, and the
    upload loop's cancel check still gets a chance to fire between chunks.

    Returns:
        Tuple of (service, YouTubeCredentials) where credentials may have
        an updated access_token after refresh.
    """
    # Lazy imports keep clipcutter.youtube importable in test/dev
    # environments without the google/googleapiclient deps installed
    # (so tests can patch clipcutter.youtube.build at the module level).
    import httplib2
    from google.oauth2.credentials import Credentials
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build

    google_creds = Credentials(
        token=creds.access_token,
        refresh_token=creds.refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=creds.client_id,
        client_secret=creds.client_secret,
        scopes=config.YOUTUBE_SCOPES,
    )
    # Build an authorized httplib2 with the timeout baked in. We can't
    # pass both credentials= and http= to build(), so wrap manually.
    authed_http = AuthorizedHttp(google_creds, http=httplib2.Http(timeout=timeout))
    service = build("youtube", "v3", http=authed_http)

    # Return updated creds so callers can persist refreshed tokens
    updated_creds = YouTubeCredentials(
        access_token=google_creds.token or creds.access_token,
        refresh_token=google_creds.refresh_token or creds.refresh_token,
        token_expiry=creds.token_expiry,
        client_id=creds.client_id,
        client_secret=creds.client_secret,
    )
    return service, updated_creds


def upload_video(creds: YouTubeCredentials, file_path: Path,
                 title: str, description: str,
                 tags: list, category_id: str,
                 privacy: str,
                 progress_callback: Optional[Callable] = None,
                 cancel_event: Optional[threading.Event] = None) -> UploadResult:
    """Upload a video to YouTube using resumable upload.

    The chunk loop checks cancel_event AFTER each next_chunk() (success
    or transient error) so a stalled or slow upload can be aborted
    promptly — without it, a user-hit cancel only takes effect at the
    next clip boundary. Combined with the per-request timeout installed
    in get_authenticated_service, this caps the worst-case time to
    actually stop at ~YOUTUBE_HTTP_TIMEOUT_SECONDS.

    Args:
        creds: YouTube OAuth2 credentials.
        file_path: Path to the video file.
        title: Video title.
        description: Video description.
        tags: List of tag strings.
        category_id: YouTube category ID.
        privacy: Privacy status (private, unlisted, public).
        progress_callback: Optional callback(bytes_sent, bytes_total).
        cancel_event: Optional event; if set between chunks, the upload
            aborts and returns cancelled=True.

    Returns:
        UploadResult. On user cancel, success=False and cancelled=True.
    """
    try:
        from googleapiclient.http import MediaFileUpload

        service, creds = get_authenticated_service(creds)

        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": category_id,
            },
            "status": {
                "privacyStatus": privacy,
            },
        }

        chunk_size = config.YOUTUBE_CHUNK_SIZE_MB * 1024 * 1024
        media = MediaFileUpload(
            str(file_path),
            chunksize=chunk_size,
            resumable=True,
        )

        request = service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response = None
        while response is None:
            # num_retries lets next_chunk handle transient 5xx / network
            # blips internally with exponential backoff before raising,
            # so we don't immediately bail on a flaky connection. Each
            # chunk eventually gives up, which then lets the cancel
            # check below run.
            status, response = request.next_chunk(num_retries=YOUTUBE_CHUNK_RETRIES)

            # Cancel check runs after EVERY next_chunk() (whether it
            # returned progress or completion), so a user cancel during
            # a large upload takes effect within one chunk's worth of
            # time rather than waiting for the whole file.
            if cancel_event is not None and cancel_event.is_set():
                return UploadResult(success=False, cancelled=True)

            if status and progress_callback:
                progress_callback(
                    status.resumable_progress,
                    status.total_size,
                )

        video_id = response["id"]
        url = f"https://www.youtube.com/watch?v={video_id}"

        return UploadResult(
            success=True,
            video_id=video_id,
            url=url,
        )

    except Exception as exc:
        # If the user cancelled mid-flight, classify a transport-level
        # exception (e.g., socket closed when we set the event) as a
        # cancellation rather than a hard error.
        if cancel_event is not None and cancel_event.is_set():
            return UploadResult(success=False, cancelled=True)
        return UploadResult(
            success=False,
            error=str(exc),
        )


def list_playlists(creds: YouTubeCredentials) -> list:
    """List the authenticated user's playlists."""
    service, creds = get_authenticated_service(creds)

    playlists = []
    request = service.playlists().list(part="snippet,contentDetails", mine=True, maxResults=50)
    while request:
        response = request.execute()
        for item in response.get("items", []):
            playlists.append({
                "id": item["id"],
                "title": item["snippet"]["title"],
                "description": item["snippet"].get("description", ""),
                "item_count": item.get("contentDetails", {}).get("itemCount", 0),
            })
        request = service.playlists().list_next(request, response)

    return playlists


def create_playlist(creds: YouTubeCredentials, title: str,
                    privacy: str = "private") -> dict:
    """Create a new playlist."""
    service, creds = get_authenticated_service(creds)

    body = {
        "snippet": {
            "title": title,
        },
        "status": {
            "privacyStatus": privacy,
        },
    }

    response = service.playlists().insert(
        part="snippet,status",
        body=body,
    ).execute()

    return {
        "id": response["id"],
        "title": response["snippet"]["title"],
    }


def add_to_playlist(creds: YouTubeCredentials, playlist_id: str,
                    video_id: str) -> None:
    """Add a video to a playlist."""
    service, creds = get_authenticated_service(creds)

    body = {
        "snippet": {
            "playlistId": playlist_id,
            "resourceId": {
                "kind": "youtube#video",
                "videoId": video_id,
            },
        },
    }

    service.playlistItems().insert(
        part="snippet",
        body=body,
    ).execute()
