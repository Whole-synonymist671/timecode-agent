"""Public facade for the edit-decision ledger.

TIMECODE facts remain in checkpoints.jsonl. EDITCODE choices live in the
separately locked sequences.jsonl and may only reach terminal states when all
checkpoint references are verified, supported, and time-aligned.
"""

from .sequence_projection import export_edits_md, sequence_cut_selection
from .sequence_schema import (
    SEQ_STATUS_KO,
    SEQUENCE_SCHEMA_EXAMPLE,
    SEQUENCE_SCHEMA_VERSION,
    SEQUENCE_STATUSES,
    SequenceValidationError,
)
from .sequence_store import (
    append_sequence,
    load_sequences,
    sequences_path,
    validate_sequence,
)

__all__ = [
    "SEQUENCE_SCHEMA_EXAMPLE",
    "SEQUENCE_SCHEMA_VERSION",
    "SEQUENCE_STATUSES",
    "SEQ_STATUS_KO",
    "SequenceValidationError",
    "append_sequence",
    "export_edits_md",
    "load_sequences",
    "sequence_cut_selection",
    "sequences_path",
    "validate_sequence",
]
