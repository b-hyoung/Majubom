"""Model-independent benchmark contracts."""
from dataclasses import dataclass
from enum import Enum

SCHEMA_VERSION = 'benchmark-0.1-draft'

class ProcessingStatus(str, Enum):
    OK = 'ok'
    FAILED = 'failed'
    NOT_PROCESSED = 'not_processed'

@dataclass(frozen=True)
class FrameKey:
    run_id: str
    video_id: str
    frame_id: int

    def __post_init__(self):
        if any(not isinstance(value, str) or not value for value in (self.run_id, self.video_id)):
            raise ValueError('Frame identifiers must not be empty')
        if type(self.frame_id) is not int or self.frame_id < 0:
            raise ValueError('frame_id must be a nonnegative integer')


COCO17 = ('nose', 'left_eye', 'right_eye', 'left_ear', 'right_ear',
          'left_shoulder', 'right_shoulder', 'left_elbow', 'right_elbow',
          'left_wrist', 'right_wrist', 'left_hip', 'right_hip',
          'left_knee', 'right_knee', 'left_ankle', 'right_ankle')


@dataclass(frozen=True)
class FramePacket:
    video_id: str
    frame_id: int
    rgb: object
    spec: dict

    @property
    def width(self):
        return self.rgb.shape[1]

    @property
    def height(self):
        return self.rgb.shape[0]


@dataclass(frozen=True)
class ModelInput:
    frame: FramePacket
    model_timestamp_ms: int | None


@dataclass(frozen=True)
class PoseOutput:
    candidates: tuple
    model_timestamp_ms: int | None
    sdk_call_started: bool = False
    silhouette_observation: dict | None = None
    silhouette_preview: dict | None = None  # Transient display only; never a recording field.


class SourceError(RuntimeError):
    """A known input frame cannot be reproduced."""


class AdapterCallError(RuntimeError):
    def __init__(self, message, sdk_call_started=False):
        super().__init__(message)
        self.sdk_call_started = sdk_call_started


class MappingError(ValueError):
    pass


class IntegrityError(ValueError):
    pass
