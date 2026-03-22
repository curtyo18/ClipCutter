"""Data classes for ClipCutter."""

from dataclasses import dataclass, field
from enum import Enum
from typing import List


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

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "source_video": self.source_video,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": self.duration,
            "detection_reasons": self.detection_reasons,
            "confidence": round(self.confidence, 4),
            "status": self.status,
        }

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
        )
