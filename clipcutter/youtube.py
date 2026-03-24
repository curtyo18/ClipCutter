"""YouTube Data API v3 integration for uploading clips."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Union
from urllib.parse import urlencode

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from clipcutter import config


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
    """Result of a YouTube upload attempt."""
    success: bool
    video_id: Optional[str] = None
    error: Optional[str] = None
    url: Optional[str] = None


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


def get_authenticated_service(creds: YouTubeCredentials):
    """Return a YouTube API service with auto-refreshing credentials.

    The google.oauth2.credentials.Credentials object handles token refresh
    automatically when refresh_token and token_uri are provided.

    Returns:
        Tuple of (service, YouTubeCredentials) where credentials may have
        an updated access_token after refresh.
    """
    google_creds = Credentials(
        token=creds.access_token,
        refresh_token=creds.refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=creds.client_id,
        client_secret=creds.client_secret,
        scopes=config.YOUTUBE_SCOPES,
    )
    service = build("youtube", "v3", credentials=google_creds)

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
                 progress_callback: Optional[Callable] = None) -> UploadResult:
    """Upload a video to YouTube using resumable upload.

    Args:
        creds: YouTube OAuth2 credentials.
        file_path: Path to the video file.
        title: Video title.
        description: Video description.
        tags: List of tag strings.
        category_id: YouTube category ID.
        privacy: Privacy status (private, unlisted, public).
        progress_callback: Optional callback(bytes_sent, bytes_total).

    Returns:
        UploadResult with success status and video ID/URL.
    """
    try:
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
            status, response = request.next_chunk()
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
