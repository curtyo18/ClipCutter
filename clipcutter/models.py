"""Data classes for ClipCutter."""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class DetectionType(Enum):
    VOLUME_SPIKE = "volume_spike"
    LAUGHTER = "laughter"
    SHOUTING = "shouting"
    SUDDEN_NOISE = "sudden_noise"
    FALLBACK = "fallback"


@dataclass
class Highlight:
    """A single detected moment in the audio."""
    timestamp: float              # Seconds from start
    duration: float               # How long the highlight lasts
    detection_type: DetectionType
    raw_score: float              # Unnormalized signal strength
    confidence: float = 0.0       # Normalized 0-1 after scoring
    details: dict = field(default_factory=dict)


@dataclass
class ClipBoundary:
    """Computed boundaries for a clip, possibly merging multiple highlights."""
    start_time: float
    end_time: float
    highlights: List[Highlight] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    @property
    def confidence(self) -> float:
        if not self.highlights:
            return 0.0
        return max(h.confidence for h in self.highlights)

    @property
    def primary_reason(self) -> str:
        if not self.highlights:
            return "unknown"
        best = max(self.highlights, key=lambda h: h.confidence)
        return best.detection_type.value

    @property
    def detection_reasons(self) -> List[str]:
        seen = []
        for h in sorted(self.highlights, key=lambda x: -x.confidence):
            if h.detection_type.value not in seen:
                seen.append(h.detection_type.value)
        return seen


@dataclass
class ClipMetadata:
    """Metadata for a single extracted clip."""
    filename: str
    source_video: str
    start_time: float
    end_time: float
    duration: float
    detection_reasons: List[str]
    confidence: float
    status: str = "pending"
    custom_name: Optional[str] = None
    encoded_filename: Optional[str] = None
    encoding_preset: Optional[str] = None
    youtube_video_id: Optional[str] = None
    youtube_url: Optional[str] = None
    youtube_upload_status: Optional[str] = None

    def to_dict(self) -> dict:
        d = {
            "filename": self.filename,
            "source_video": self.source_video,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": self.duration,
            "detection_reasons": self.detection_reasons,
            "confidence": round(self.confidence, 4),
            "status": self.status,
        }
        if self.custom_name is not None:
            d["custom_name"] = self.custom_name
        if self.encoded_filename is not None:
            d["encoded_filename"] = self.encoded_filename
        if self.encoding_preset is not None:
            d["encoding_preset"] = self.encoding_preset
        if self.youtube_video_id is not None:
            d["youtube_video_id"] = self.youtube_video_id
        if self.youtube_url is not None:
            d["youtube_url"] = self.youtube_url
        if self.youtube_upload_status is not None:
            d["youtube_upload_status"] = self.youtube_upload_status
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ClipMetadata":
        return cls(
            filename=d["filename"],
            source_video=d["source_video"],
            start_time=d["start_time"],
            end_time=d["end_time"],
            duration=d["duration"],
            detection_reasons=d["detection_reasons"],
            confidence=d["confidence"],
            status=d.get("status", "pending"),
            custom_name=d.get("custom_name", None),
            encoded_filename=d.get("encoded_filename", None),
            encoding_preset=d.get("encoding_preset", None),
            youtube_video_id=d.get("youtube_video_id", None),
            youtube_url=d.get("youtube_url", None),
            youtube_upload_status=d.get("youtube_upload_status", None),
        )
