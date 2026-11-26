from time import time

from fastapi import HTTPException
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from src.config.resources import engine
from src.constants.archive import ImportState, ReceiptState
from src.domain import archives as rules
from src.models.archives import archive_imports, archive_receipts
from . import audit

BATCH = 25

OUTCOMES = ("restored", "incomplete", "pending", "skipped", "conflict")


class ArchiveFailure(Exception):
    def __init__(self, import_id, cause):
        super().__init__(str(cause))
        self.import_id = import_id
        self.cause = cause


def empty_counts():
    return {name: 0 for name in OUTCOMES}


_PARTS = ("erratum", "signature")


def _completed(kept, incoming):
    """Union of both documents when the later one only supplies what was missing."""
    for part in _PARTS:
        if kept.get(part) and incoming.get(part) and kept[part] != incoming[part]:
            return None
    merged = {part: kept.get(part) or incoming.get(part) for part in _PARTS}
    if not all(merged.get(part) for part in _PARTS):
        return None
    return merged


def view(db, import_id):
    row = db.execute(select(archive_imports).where(archive_imports.c.id == import_id)).mappings().first()
    if row is None:
        raise HTTPException(404, "留档导入不存在")
    return dict(row)


def start(db, data, actor):
    """Create the import, or return the identical one so a retry resumes it."""
    existing = db.execute(select(archive_imports).where(
        archive_imports.c.source == data["source"],
        archive_imports.c.archive_key == data["archive_key"],
    )).mappings().first()
    if existing is not None:
        if existing["entries_digest"] != data["entries_digest"]:
            raise HTTPException(409, "该留档标识已用于内容不同的留档，请更换标识")
        if existing["state"] == ImportState.FAILED:
            # Retrying the same archive picks the interrupted one back up.
            db.execute(update(archive_imports).where(archive_imports.c.id == existing["id"]).values(
                state=ImportState.RUNNING, failure=None, updated_at=int(time()),
            ))
            audit.record(db, "archive_resume", actor, "archive_import", existing["id"])
        return existing["id"]
    try:
        change = db.execute(insert(archive_imports).values(
            archive_key=data["archive_key"], source=data["source"], entries=data["entries"],
            entries_digest=data["entries_digest"], total=data["total"], cursor=0,
            state=ImportState.RUNNING, failure=None, counts=empty_counts(),
            actor=actor, created_at=int(time()), updated_at=int(time()),
        ))
        import_id = change.inserted_primary_key[0]
        audit.record(db, "archive_import", actor, "archive_import", import_id)
        return import_id
    except IntegrityError:
        # A concurrent request stored the same archive first; resume that one.
        return db.execute(select(archive_imports.c.id).where(
            archive_imports.c.source == data["source"],
            archive_imports.c.archive_key == data["archive_key"],
        )).scalar_one()


def claim(db):
    """Lock one interrupted or running import and advance it by a bounded chunk."""
    statement = (
        select(archive_imports)
        .where(archive_imports.c.state == ImportState.RUNNING)
        .order_by(archive_imports.c.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    row = db.execute(statement).mappings().first()
    if row is None:
        return False
    try:
        _advance(db, row)
    except Exception as exc:
        # Let the failed transaction roll back before the caller records the error.
        raise ArchiveFailure(row["id"], exc) from exc
    return True


def _advance(db, row):
    import_id = row["id"]
    cursor = row["cursor"]
    entries = row["entries"]
    counts = {name: row["counts"].get(name, 0) for name in OUTCOMES} if row["counts"] else empty_counts()
    stop = min(cursor + BATCH, row["total"])
    for position in range(cursor, stop):
        outcome = _restore_entry(db, import_id, row["source"], entries[position])
        counts[outcome] = counts.get(outcome, 0) + 1
    cursor = stop
    state = ImportState.DONE if cursor >= row["total"] else ImportState.RUNNING
    db.execute(update(archive_imports).where(archive_imports.c.id == import_id).values(
        cursor=cursor, state=state, failure=None, counts=counts, updated_at=int(time()),
    ))
    if state == ImportState.DONE:
        audit.record(db, "archive_done", "worker", "archive_import", import_id)


def run_until_idle(limit=None):
    """Drain every resumable import; each chunk commits on its own."""
    processed = 0
    while limit is None or processed < limit:
        try:
            with engine.begin() as db:
                if not claim(db):
                    break
        except ArchiveFailure as exc:
            mark_failed(exc.import_id, exc.cause)
            processed += 1
            continue
        processed += 1
    return processed


def _restore_entry(db, import_id, source, entry):
    """Compare one archived entry with the receipt already kept for this office."""
    stored = db.execute(select(archive_receipts).where(
        archive_receipts.c.source == source,
        archive_receipts.c.old_no == entry["old_no"],
    )).mappings().first()
    moment = int(time())
    if stored is not None and stored["digest"] == entry["digest"]:
        # The same archive retried: never build a second receipt for this number.
        return "skipped"
    if stored is not None:
        if stored["state"] == ReceiptState.PENDING and stored["conflict_digest"] == entry["digest"]:
            # The same later document retried while the number awaits reconciliation.
            return "skipped"
        if stored["rejected_digest"] == entry["digest"]:
            # The reviewer already kept the original against this document.
            return "skipped"
        merged = _completed(stored["content"], {"erratum": entry["erratum"], "signature": entry["signature"]})
        if merged is not None:
            # The later document only supplied what the kept one still lacked:
            # both the original erratum and the original signature are now in,
            # so this number is restored rather than sent to reconciliation.
            db.execute(update(archive_receipts).where(archive_receipts.c.id == stored["id"]).values(
                content=merged, digest=rules.digest_of(merged), state=ReceiptState.RESTORED,
                conflict=None, conflict_digest=None, superseded=None, rejected_digest=None,
                import_id=import_id, updated_at=moment,
            ))
            audit.record(db, "archive_restore", "worker", "archive_receipt", stored["id"])
            return "restored"
        # A different document under the same old number: this number goes to
        # reconciliation while the rest of the archive keeps importing.  Keep the
        # original aside so the number can be settled either way later.
        db.execute(update(archive_receipts).where(archive_receipts.c.id == stored["id"]).values(
            state=ReceiptState.PENDING,
            conflict={"erratum": entry["erratum"], "signature": entry["signature"]},
            conflict_digest=entry["digest"],
            superseded=stored["content"],
            import_id=import_id, updated_at=moment,
        ))
        audit.record(db, "archive_conflict", "worker", "archive_receipt", stored["id"])
        return "conflict"
    change = db.execute(insert(archive_receipts).values(
        source=source, old_no=entry["old_no"],
        content={"erratum": entry["erratum"], "signature": entry["signature"]},
        digest=entry["digest"], state=entry["state"], note=None, conflict=None,
        conflict_digest=None, superseded=None, rejected_digest=None, import_id=import_id,
        created_at=moment, updated_at=moment,
    ))
    receipt_id = change.inserted_primary_key[0]
    audit.record(db, "archive_restore", "worker", "archive_receipt", receipt_id)
    return "restored" if entry["state"] == ReceiptState.RESTORED else "incomplete"


def mark_failed(import_id, reason):
    with engine.begin() as db:
        db.execute(update(archive_imports).where(
            archive_imports.c.id == import_id, archive_imports.c.state != ImportState.DONE,
        ).values(state=ImportState.FAILED, failure=str(reason)[:320], updated_at=int(time())))
        audit.record(db, "archive_failed", "worker", "archive_import", import_id)


def resume(db, import_id, actor):
    row = view(db, import_id)
    if row["state"] == ImportState.DONE:
        return row["id"]
    db.execute(update(archive_imports).where(archive_imports.c.id == import_id).values(
        state=ImportState.RUNNING, failure=None, updated_at=int(time()),
    ))
    audit.record(db, "archive_resume", actor, "archive_import", import_id)
    return import_id


def resolve(db, receipt_id, decision, actor):
    """Settle a receipt held for reconciliation: adopt the later document or keep the original."""
    row = db.execute(select(archive_receipts).where(archive_receipts.c.id == receipt_id)).mappings().first()
    if row is None:
        raise HTTPException(404, "核收单不存在")
    if row["state"] != ReceiptState.PENDING:
        raise HTTPException(409, "只有待核对的核收单需要裁定")
    incoming = row["conflict"] or {}
    erratum = incoming.get("erratum")
    signature = incoming.get("signature")
    rejected = None
    if decision == "adopt":
        if erratum is None or signature is None:
            raise HTTPException(422, "后来收到的单缺少原勘误或原签字，不能采为核收单")
        content = {"erratum": erratum, "signature": signature}
        state = ReceiptState.RESTORED
    else:
        content = row["superseded"] or row["content"]
        state = ReceiptState.RESTORED if content.get("erratum") and content.get("signature") else ReceiptState.INCOMPLETE
        # Remember the document that was turned down, so a retry of it does not
        # reopen reconciliation after the reviewer already settled the number.
        rejected = row["conflict_digest"]
    db.execute(update(archive_receipts).where(archive_receipts.c.id == receipt_id).values(
        content=content, digest=rules.digest_of(content), state=state, conflict=None,
        conflict_digest=None, superseded=None, rejected_digest=rejected,
        note=f"{actor}：{'采后来单' if decision == 'adopt' else '保留原单'}",
        updated_at=int(time()),
    ))
    audit.record(db, "archive_resolve", actor, "archive_receipt", receipt_id)
    return db.execute(select(archive_receipts).where(
        archive_receipts.c.id == receipt_id
    )).mappings().first()


def list_imports(db):
    return [dict(row) for row in db.execute(
        select(archive_imports).order_by(archive_imports.c.id.desc()).limit(100)
    ).mappings()]


def receipts(db, source, old_no, state, limit):
    query = select(archive_receipts)
    if source:
        query = query.where(archive_receipts.c.source == source)
    if old_no:
        query = query.where(archive_receipts.c.old_no == old_no)
    if state:
        query = query.where(archive_receipts.c.state == state)
    query = query.order_by(archive_receipts.c.source, archive_receipts.c.old_no).limit(limit)
    return [dict(row) for row in db.execute(query).mappings()]
