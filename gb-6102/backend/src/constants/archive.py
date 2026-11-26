from enum import StrEnum


class ReceiptState(StrEnum):
    """State of a signed receipt restored from a printing-office archive."""

    RESTORED = "restored"
    PENDING = "pending"
    INCOMPLETE = "incomplete"


class ImportState(StrEnum):
    """State of a whole archive import (resumable in batches)."""

    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
