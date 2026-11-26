from sqlalchemy import JSON, BigInteger, Column, ForeignKey, Integer, String, Table, UniqueConstraint
from .base import metadata


# One durable, resumable pass over an archive kept by a printing office.
archive_imports = Table(
    "archive_imports", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("archive_key", String(96), nullable=False),
    Column("source", String(48), nullable=False, index=True),
    Column("entries", JSON(none_as_null=True), nullable=False),
    Column("entries_digest", String(64), nullable=False),
    Column("total", Integer, nullable=False),
    Column("cursor", Integer, nullable=False, default=0),
    Column("state", String(16), nullable=False, index=True),
    Column("failure", String(320), nullable=True),
    Column("counts", JSON(none_as_null=True), nullable=True),
    Column("actor", String(48), nullable=False),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
    UniqueConstraint("source", "archive_key", name="archive_import_key"),
)


# A signed receipt restored from a printing office.  The printing office is the
# source, so colliding old batch numbers from different offices stay separate.
# Receipts already held by this database live in ``reviews`` keyed by run id.
archive_receipts = Table(
    "archive_receipts", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source", String(48), nullable=False, index=True),
    Column("old_no", String(48), nullable=False, index=True),
    Column("content", JSON(none_as_null=True), nullable=False),
    Column("digest", String(64), nullable=False),
    Column("state", String(16), nullable=False, index=True),
    Column("note", String(320), nullable=True),
    Column("conflict", JSON(none_as_null=True), nullable=True),
    Column("conflict_digest", String(64), nullable=True),
    Column("superseded", JSON(none_as_null=True), nullable=True),
    Column("rejected_digest", String(64), nullable=True),
    Column("import_id", ForeignKey("archive_imports.id"), nullable=True),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
    UniqueConstraint("source", "old_no", name="archive_receipt_lookup"),
)
