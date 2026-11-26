from typing import Literal

from pydantic import BaseModel, Field


class ArchiveInput(BaseModel):
    """A printing-office archive: one office, identified by source."""

    source: str = Field(min_length=1, max_length=48)
    archive_key: str | None = Field(default=None, max_length=96)
    entries: list[dict]


class ResolveInput(BaseModel):
    """Adopt the document received later, or keep the original one."""

    decision: Literal["adopt", "keep"]


class ArchiveImport(BaseModel):
    id: int
    archive_key: str
    source: str
    total: int
    cursor: int
    state: str
    failure: str | None
    counts: dict[str, int] | None
    actor: str
    created_at: int
    updated_at: int


class ArchiveReceipt(BaseModel):
    id: int
    source: str
    old_no: str
    content: dict
    state: str
    note: str | None
    conflict: dict | None
    import_id: int | None
    created_at: int
    updated_at: int
