"""All tunable constants for ClipCutter."""

# Audio extraction
AUDIO_SAMPLE_RATE = 22050  # Hz

# Feature computation
HOP_LENGTH = 512           # ~23ms per frame at 22050 Hz
FRAME_LENGTH = 2048        # ~93ms window
ROLLING_WINDOW_SECONDS = 30.0

# Volume spike detection
VOLUME_ZSCORE_THRESHOLD = 2.5
VOLUME_MIN_DURATION_SECONDS = 0.3

# Laughter detection
LAUGHTER_AUTOCORR_MIN_FREQ = 2.0   # Hz
LAUGHTER_AUTOCORR_MAX_FREQ = 8.0   # Hz
LAUGHTER_AUTOCORR_THRESHOLD = 0.3
LAUGHTER_MIN_DURATION_SECONDS = 1.0

# Shouting detection
SHOUTING_ENERGY_ZSCORE = 2.0
SHOUTING_CENTROID_ZSCORE = 1.5
SHOUTING_MIN_DURATION_SECONDS = 1.0

# Sudden noise detection
ONSET_STRENGTH_ZSCORE = 3.0
ONSET_STRENGTH_MIN_RATIO = 4.0

# Scoring weights
WEIGHT_VOLUME = 0.35
WEIGHT_LAUGHTER = 0.20
WEIGHT_SHOUTING = 0.25
WEIGHT_SUDDEN_NOISE = 0.20

# Bonuses
SUSTAINED_INTENSITY_SECONDS = 2.0
SUSTAINED_INTENSITY_BONUS = 0.15
MULTI_VOICE_BONUS = 0.10
COINCIDENCE_BONUS = 0.15
COINCIDENCE_WINDOW_SECONDS = 2.0

# Clip construction
CLIP_CONTEXT_BEFORE_SECONDS = 20.0
CLIP_CONTEXT_AFTER_SECONDS = 20.0
CLIP_MIN_LENGTH_SECONDS = 30.0
CLIP_MAX_LENGTH_SECONDS = 240.0
CLIP_MERGE_GAP_SECONDS = 10.0

# Fallback
FALLBACK_DURATION_SECONDS = 300.0  # 5 minutes

# Quality filter
MIN_CONFIDENCE_THRESHOLD = 0.25
MAX_CLIPS_PER_VIDEO = 20

# Silence trimming
SILENCE_THRESHOLD_DB = -40.0
SILENCE_CHECK_SECONDS = 3.0

# Output directory names
DIR_PENDING = "pending"
DIR_KEPT = "kept"
DIR_DISCARDED = "discarded"
DIR_CLIPS = "clips"
DIR_METADATA = "metadata"

# Supported video extensions
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v"}

# Compilations
DIR_COMPILATIONS = "compilations"
COMPILATION_CROSSFADE_MIN = 0.1
COMPILATION_CROSSFADE_MAX = 3.0
COMPILATION_CROSSFADE_DEFAULT = 0.5

# Encoding presets
DIR_ENCODED = "encoded"

ENCODING_PRESETS = {
    "original": {
        "display_name": "Original (No Re-encode)",
        "extension": None,  # Keep source extension
        "ffmpeg_args": [],  # No re-encoding, just copy
    },
    "high": {
        "display_name": "High Quality",
        "extension": ".mp4",
        "ffmpeg_args": ["-c:v", "libx264", "-preset", "slow", "-crf", "18", "-c:a", "aac", "-b:a", "192k"],
    },
    "low": {
        "display_name": "Low Quality (Smaller)",
        "extension": ".mp4",
        "ffmpeg_args": ["-c:v", "libx264", "-preset", "medium", "-crf", "26", "-c:a", "aac", "-b:a", "96k"],
    },
    "gif": {
        "display_name": "Animated GIF (no sound)",
        "extension": ".gif",
        "ffmpeg_args": ["-vf", "split=2[m0][m1];[m0]palettegen[p];[m1][p]paletteuse", "-an"],
    },
}

DEFAULT_ENCODING_PRESET = "original"
DEFAULT_TARGET_FPS = None

# YouTube
YOUTUBE_CREDENTIALS_FILE = ".youtube_credentials.json"
YOUTUBE_DEFAULT_PRIVACY = "private"
YOUTUBE_DEFAULT_CATEGORY = "20"
YOUTUBE_CHUNK_SIZE_MB = 10
YOUTUBE_DESCRIPTION_TEMPLATE = "Highlight from {source_video} ({start_time} - {end_time})\nDetected: {detection_reasons}"
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube"]
