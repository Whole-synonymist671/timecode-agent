"""Public facade for image identity, causal storage, and catalog projection."""

from .image_catalog import load_image_records, merge_image_events
from .image_model import (
    PROVENANCE_SCHEMA,
    cause_identity,
    image_identity,
    normalize_image_path,
    resolve_image_path,
)
from .image_store import (
    PROVENANCE_FILENAME,
    append_image_provenance,
    load_provenance_events,
    record_legacy_capture_reasons,
)
from .image_types import ImageCause, ImageEvent, ImageEventInput, ImageRecord

__all__ = [
    "PROVENANCE_FILENAME",
    "PROVENANCE_SCHEMA",
    "ImageCause",
    "ImageEvent",
    "ImageEventInput",
    "ImageRecord",
    "append_image_provenance",
    "cause_identity",
    "image_identity",
    "load_image_records",
    "load_provenance_events",
    "merge_image_events",
    "normalize_image_path",
    "resolve_image_path",
    "record_legacy_capture_reasons",
]
