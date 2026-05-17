"""Unit tests for clipcutter.routes._helpers.

These cover the small pure helpers that the route handlers compose with
FastAPI. The function under test is module-private but importable for the
purposes of pinning behaviour.
"""

from clipcutter.routes._helpers import _media_type


class TestMediaType:
    """Maps a clip filename to the Content-Type header value used by
    FileResponse on the clip-serving routes.
    """

    def test_webm_returns_video_webm(self):
        # Regression: the lookup table used to key on "webm" (no dot) while
        # Path(...).suffix yields ".webm", so .webm clips were served as
        # video/mp4. Pin the correct mapping.
        assert _media_type("clip_001.webm") == "video/webm"

    def test_gif_returns_image_gif(self):
        assert _media_type("clip_001.gif") == "image/gif"

    def test_mp4_falls_back_to_video_mp4(self):
        assert _media_type("clip_001.mp4") == "video/mp4"

    def test_unknown_extension_falls_back_to_video_mp4(self):
        assert _media_type("clip_001.xyz") == "video/mp4"

    def test_extension_case_insensitive(self):
        assert _media_type("CLIP.WEBM") == "video/webm"
        assert _media_type("CLIP.GIF") == "image/gif"
