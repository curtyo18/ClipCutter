"""YouTube upload and OAuth endpoints."""
import logging
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from clipcutter.config import (
    DIR_CLIPS, DIR_ENCODED, DIR_KEPT, DIR_METADATA,
    YOUTUBE_CREDENTIALS_FILE, YOUTUBE_DEFAULT_CATEGORY, YOUTUBE_DEFAULT_PRIVACY,
)
from clipcutter.metadata import load_metadata, update_clip_youtube
from clipcutter.routes._helpers import _safe_join
from clipcutter.state import AppState


class YouTubeAuthStartRequest(BaseModel):
    client_id: str
    client_secret: str


class YouTubeUploadRequest(BaseModel):
    video_stem: str
    filename: str
    use_encoded: bool = True
    title: str
    description: str = ""
    tags: List[str] = []
    category_id: str = YOUTUBE_DEFAULT_CATEGORY
    privacy: str = YOUTUBE_DEFAULT_PRIVACY
    playlist_id: Optional[str] = None


class YouTubeBatchUploadRequest(BaseModel):
    clips: List[YouTubeUploadRequest]


class PlaylistCreateRequest(BaseModel):
    title: str
    privacy: str = "private"


def create_router(state: AppState) -> APIRouter:
    router = APIRouter()
    creds_path = state.output_dir / YOUTUBE_CREDENTIALS_FILE

    @router.get("/api/youtube/status")
    def youtube_status():
        """Check if YouTube credentials exist and are valid."""
        from clipcutter.youtube import load_credentials, get_authenticated_service
        creds = load_credentials(creds_path)
        if creds is None:
            return {"authenticated": False}

        try:
            service, new_creds = get_authenticated_service(creds)
            resp = service.channels().list(part="snippet", mine=True).execute()
            channel_name = ""
            items = resp.get("items", [])
            if items:
                channel_name = items[0].get("snippet", {}).get("title", "")
            if new_creds.access_token != creds.access_token:
                from clipcutter.youtube import save_credentials
                save_credentials(new_creds, creds_path)
            return {"authenticated": True, "channel_name": channel_name}
        except Exception:
            return {"authenticated": False, "error": "Credentials expired or invalid"}

    @router.post("/api/youtube/auth/start")
    def youtube_auth_start(req: YouTubeAuthStartRequest, request: Request):
        """Save client_id/secret and return the OAuth2 authorization URL."""
        from clipcutter.youtube import get_auth_url, YouTubeCredentials, save_credentials

        base_url = str(request.base_url).rstrip("/")
        redirect_uri = f"{base_url}/oauth/callback"

        auth_url = get_auth_url(req.client_id, redirect_uri)

        partial_creds = YouTubeCredentials(
            access_token="",
            refresh_token="",
            token_expiry=None,
            client_id=req.client_id,
            client_secret=req.client_secret,
        )
        save_credentials(partial_creds, creds_path)

        return {"auth_url": auth_url}

    @router.get("/oauth/callback", response_class=HTMLResponse)
    def oauth_callback(code: str, request: Request):
        """Exchange authorization code for credentials."""
        from clipcutter.youtube import (
            exchange_code, load_credentials, save_credentials,
        )

        partial = load_credentials(creds_path)
        if partial is None:
            raise HTTPException(400, "No pending OAuth flow (client_id/secret missing)")

        base_url = str(request.base_url).rstrip("/")
        redirect_uri = f"{base_url}/oauth/callback"

        try:
            creds = exchange_code(
                code=code,
                client_id=partial.client_id,
                client_secret=partial.client_secret,
                redirect_uri=redirect_uri,
            )
            save_credentials(creds, creds_path)
        except Exception as exc:
            raise HTTPException(500, f"Token exchange failed: {exc}")

        return HTMLResponse("""
<!DOCTYPE html>
<html>
<head><title>ClipCutter - YouTube Auth</title></head>
<body style="background:#1a1a2e;color:#eee;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
<div style="text-align:center">
<h2>YouTube Authentication Successful</h2>
<p>You can close this window.</p>
</div>
<script>
if (window.opener) {
    window.opener.postMessage({type: 'youtube-auth-success'}, window.location.origin);
    setTimeout(function() { window.close(); }, 1500);
}
</script>
</body>
</html>
""")

    @router.post("/api/youtube/auth/revoke")
    def youtube_auth_revoke():
        """Delete stored YouTube credentials."""
        if creds_path.exists():
            creds_path.unlink()
        return {"status": "revoked"}

    @router.get("/api/youtube/playlists")
    def youtube_playlists():
        from clipcutter.youtube import load_credentials, list_playlists
        creds = load_credentials(creds_path)
        if creds is None:
            raise HTTPException(401, "Not authenticated with YouTube")

        try:
            playlists = list_playlists(creds)
            return {"playlists": playlists}
        except Exception as exc:
            raise HTTPException(500, f"Failed to list playlists: {exc}")

    @router.post("/api/youtube/playlists")
    def youtube_create_playlist(req: PlaylistCreateRequest):
        from clipcutter.youtube import load_credentials, create_playlist
        creds = load_credentials(creds_path)
        if creds is None:
            raise HTTPException(401, "Not authenticated with YouTube")

        try:
            playlist = create_playlist(creds, req.title, req.privacy)
            return playlist
        except Exception as exc:
            raise HTTPException(500, f"Failed to create playlist: {exc}")

    @router.post("/api/youtube/upload")
    def start_youtube_upload(req: YouTubeBatchUploadRequest):
        import threading
        if state.upl.running:
            raise HTTPException(409, "Upload already in progress")

        from clipcutter.youtube import load_credentials
        creds = load_credentials(creds_path)
        if creds is None:
            raise HTTPException(401, "Not authenticated with YouTube")

        # Up-front path validation: reject any clip with a traversal-laden
        # video_stem/filename before we kick off the worker. Catches the
        # exfiltration vector where a hostile fetch supplies "../../etc"
        # and YouTube ends up hosting the user's secrets.
        meta_base = state.output_dir / DIR_METADATA
        kept_base = state.output_dir / DIR_CLIPS / DIR_KEPT
        encoded_base = state.output_dir / DIR_CLIPS / DIR_ENCODED
        for clip_req in req.clips:
            _safe_join(kept_base, clip_req.video_stem, clip_req.filename)
            _safe_join(meta_base, f"{clip_req.video_stem}_clips.json")

        state.upl.reset(total=len(req.clips))

        def run():
            from clipcutter.youtube import (
                upload_video, add_to_playlist, load_credentials, save_credentials,
            )

            current_creds = load_credentials(creds_path)

            for i, clip_req in enumerate(req.clips):
                if state.upl.cancelled:
                    break

                state.upl.set_current(clip_req.filename, i + 1)

                # Check for duplicate upload (skip if already uploaded)
                already_uploaded = False
                meta_path = _safe_join(meta_base, f"{clip_req.video_stem}_clips.json")
                if meta_path.exists():
                    dup_metas = load_metadata(meta_path)
                    for dm in dup_metas:
                        if dm.filename == clip_req.filename and dm.youtube_video_id:
                            state.upl.add_completed(
                                clip_req.filename, dm.youtube_video_id, dm.youtube_url or "")
                            already_uploaded = True
                            break
                if already_uploaded:
                    continue

                # Determine which file to upload
                if clip_req.use_encoded:
                    meta_path = _safe_join(meta_base, f"{clip_req.video_stem}_clips.json")
                    file_path = None
                    if meta_path.exists():
                        clip_metas = load_metadata(meta_path)
                        for cm in clip_metas:
                            if cm.filename == clip_req.filename and cm.encoded_filename:
                                try:
                                    enc_path = _safe_join(
                                        encoded_base, clip_req.video_stem, cm.encoded_filename
                                    )
                                except HTTPException:
                                    enc_path = None
                                if enc_path is not None and enc_path.exists():
                                    file_path = enc_path
                                break

                    if file_path is None:
                        file_path = _safe_join(
                            kept_base, clip_req.video_stem, clip_req.filename
                        )
                else:
                    file_path = _safe_join(
                        kept_base, clip_req.video_stem, clip_req.filename
                    )

                if not file_path.exists():
                    state.upl.add_error(clip_req.filename, f"File not found: {file_path.name}")
                    continue

                def progress_cb(bytes_sent, bytes_total):
                    state.upl.update_progress(bytes_sent, bytes_total)

                result = upload_video(
                    creds=current_creds,
                    file_path=file_path,
                    title=clip_req.title,
                    description=clip_req.description,
                    tags=clip_req.tags,
                    category_id=clip_req.category_id,
                    privacy=clip_req.privacy,
                    progress_callback=progress_cb,
                )

                if result.success:
                    state.upl.add_completed(clip_req.filename, result.video_id, result.url)

                    meta_path = _safe_join(meta_base, f"{clip_req.video_stem}_clips.json")
                    if meta_path.exists():
                        if not update_clip_youtube(
                            meta_path, clip_req.filename,
                            result.video_id, result.url,
                        ):
                            logger.warning(
                                "update_clip_youtube: no match for %s in %s",
                                clip_req.filename, meta_path,
                            )

                    if clip_req.playlist_id and result.video_id:
                        try:
                            add_to_playlist(current_creds, clip_req.playlist_id, result.video_id)
                        except Exception:
                            pass  # Non-fatal: upload succeeded
                else:
                    state.upl.add_error(clip_req.filename, result.error or "Unknown error")
                    meta_path = _safe_join(meta_base, f"{clip_req.video_stem}_clips.json")
                    if meta_path.exists():
                        if not update_clip_youtube(
                            meta_path, clip_req.filename,
                            video_id="", url="", status="failed",
                        ):
                            logger.warning(
                                "update_clip_youtube: no match for %s in %s",
                                clip_req.filename, meta_path,
                            )

            state.upl.finish()

        threading.Thread(target=run, daemon=True).start()
        return {"status": "started"}

    @router.get("/api/youtube/upload/status")
    def youtube_upload_status():
        return state.upl.snapshot()

    @router.post("/api/youtube/upload/cancel")
    def cancel_youtube_upload():
        state.upl.cancelled = True
        return {"status": "cancelling"}

    return router
