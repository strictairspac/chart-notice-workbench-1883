from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session

from src.domain import archives as rules
from src.models.archives import archive_imports, archive_receipts
from src.models.base import metadata
from src.services import archives


def make_engine(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path}/workbench.db")
    metadata.create_all(engine)
    return engine


def erratum(seq, notice="外堤浮标灯质更正为黄闪"):
    return {"seq": seq, "issue_date": "2026-11-23", "chart": "CN-12301", "notice": notice}


def signature(actor="reviewer", note="复核无误"):
    return {"actor": actor, "note": note, "created_at": 1700000000}


def archive(source, entries, key=None):
    payload = {"source": source, "entries": entries}
    if key:
        payload["archive_key"] = key
    return rules.normalize(payload)


def entry(old_no, seq=105, notice="外堤浮标灯质更正为黄闪", sig=True):
    row = {"old_no": old_no, "erratum": erratum(seq, notice)}
    if sig:
        row["signature"] = signature()
    return row


def run_import(engine, data, actor="operator"):
    with Session(engine) as session, session.begin():
        import_id = archives.start(session, data, actor)
    drain(engine)
    return import_id


def drain(engine):
    while True:
        with Session(engine) as session, session.begin():
            if not archives.claim(session):
                return


def test_restores_and_separates_sources(engine):
    a = archive("甲印务台", [entry("A-105", seq=105), entry("A-104", seq=104)])
    b = archive("乙印务台", [entry("A-105", seq=205, notice="东锚地界线北移")])
    run_import(engine, a)
    run_import(engine, b)
    with Session(engine) as session:
        rows = session.execute(select(archive_receipts)).mappings().all()
    assert len(rows) == 3
    pairs = {(row["source"], row["old_no"]) for row in rows}
    assert pairs == {("甲印务台", "A-105"), ("甲印务台", "A-104"), ("乙印务台", "A-105")}
    assert all(row["state"] == "restored" for row in rows)
    # The same old number at two offices keeps each office's own content.
    notes = {row["content"]["erratum"]["notice"] for row in rows if row["old_no"] == "A-105"}
    assert len(notes) == 2


def test_retry_same_archive_never_duplicates(engine):
    data = archive("甲印务台", [entry("A-105")])
    first = run_import(engine, data)
    second = run_import(engine, data)
    assert first == second
    with Session(engine) as session:
        assert len(session.execute(select(archive_receipts)).mappings().all()) == 1


def test_explicit_key_conflict_rejected(engine):
    entry_a = entry("A-105")
    entry_b = entry("A-105", notice="不同的勘误")
    run_import(engine, archive("甲印务台", [entry_a], key="留档-01"))
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as caught:
        run_import(engine, archive("甲印务台", [entry_b], key="留档-01"))
    assert caught.value.status_code == 409


def test_conflicting_document_moves_to_pending_rest_restored(engine):
    run_import(engine, archive("甲印务台", [entry("A-105", seq=105), entry("A-104", seq=104)]))
    run_import(engine, archive("甲印务台", [entry("A-105", seq=105, notice="改后的勘误"), entry("A-103", seq=103)]))
    with Session(engine) as session:
        rows = {row["old_no"]: row for row in session.execute(select(archive_receipts)).mappings()}
    assert rows["A-105"]["state"] == "pending"
    assert rows["A-105"]["conflict"]["erratum"]["notice"] == "改后的勘误"
    assert rows["A-105"]["superseded"]["erratum"]["notice"] == "外堤浮标灯质更正为黄闪"
    assert rows["A-104"]["state"] == "restored"
    assert rows["A-103"]["state"] == "restored"


def test_incomplete_requires_both_erratum_and_signature(engine):
    data = archive("甲印务台", [entry("A-105", sig=False), {"old_no": "A-104", "signature": signature()}])
    run_import(engine, data)
    with Session(engine) as session:
        rows = {row["old_no"]: row for row in session.execute(select(archive_receipts)).mappings()}
    assert rows["A-105"]["state"] == "incomplete"
    assert rows["A-105"]["content"]["signature"] is None
    assert rows["A-104"]["state"] == "incomplete"
    assert rows["A-104"]["content"]["erratum"] is None


def test_resume_after_interruption(engine):
    entries = [entry(f"A-{i}", seq=i) for i in range(1, 61)]
    data = archive("甲印务台", entries)
    with Session(engine) as session, session.begin():
        import_id = archives.start(session, data, "operator")
        # One chunk commits, then the worker dies before the next one starts.
        assert archives.claim(session)
        committed = archives.view(session, import_id)["cursor"]
        assert committed == 25
        session.execute(update(archive_imports).where(archive_imports.c.id == import_id).values(
            state="failed", failure="worker stopped",
        ))
    with Session(engine) as session, session.begin():
        archives.resume(session, import_id, "operator")
    drain(engine)
    with Session(engine) as session:
        view = archives.view(session, import_id)
        rows = session.execute(select(archive_receipts)).mappings().all()
    assert view["state"] == "done"
    assert view["cursor"] == 60
    assert len(rows) == 60
    # Retrying the same archive after completion is still a no-op.
    assert run_import(engine, data) == import_id
    with Session(engine) as session:
        assert len(session.execute(select(archive_receipts)).mappings().all()) == 60


def test_failed_import_resumes_explicitly(engine):
    data = archive("甲印务台", [entry("A-105")])
    with Session(engine) as session, session.begin():
        import_id = archives.start(session, data, "operator")
        session.execute(update(archive_imports).where(archive_imports.c.id == import_id).values(
            state="failed", failure="worker stopped",
        ))
    with Session(engine) as session, session.begin():
        archives.resume(session, import_id, "operator")
    drain(engine)
    with Session(engine) as session:
        assert archives.view(session, import_id)["state"] == "done"


def test_retry_of_interrupted_archive_resumes_it(engine):
    data = archive("甲印务台", [entry("A-105")])
    with Session(engine) as session, session.begin():
        import_id = archives.start(session, data, "operator")
        session.execute(update(archive_imports).where(archive_imports.c.id == import_id).values(
            state="failed", failure="worker stopped",
        ))
    # Uploading the identical archive again picks the interrupted import back up.
    assert run_import(engine, data) == import_id
    drain(engine)
    with Session(engine) as session:
        assert archives.view(session, import_id)["state"] == "done"


def test_batches_are_bounded(engine):
    entries = [entry(f"A-{i}", seq=i) for i in range(1, 61)]
    data = archive("甲印务台", entries)
    with Session(engine) as session, session.begin():
        import_id = archives.start(session, data, "operator")
    seen = []
    while True:
        with Session(engine) as session, session.begin():
            before = archives.view(session, import_id)["cursor"]
            if not archives.claim(session):
                break
            after = archives.view(session, import_id)["cursor"]
        if after != before:
            seen.append(after - before)
        if after == 60:
            break
    assert max(seen) <= archives.BATCH
    assert len(seen) >= 3


def test_resolve_pending_either_way(engine):
    run_import(engine, archive("甲印务台", [entry("A-105", seq=105)]))
    run_import(engine, archive("甲印务台", [entry("A-105", seq=105, notice="改后的勘误")]))
    with Session(engine) as session:
        receipt = session.execute(select(archive_receipts)).mappings().first()
    with Session(engine) as session, session.begin():
        kept = archives.resolve(session, receipt["id"], "keep", "reviewer")
    assert kept["state"] == "restored"
    assert kept["content"]["erratum"]["notice"] == "外堤浮标灯质更正为黄闪"
    assert kept["conflict"] is None

    # A later document that is complete can be adopted in place of the original.
    run_import(engine, archive("乙印务台", [entry("B-1", seq=1)]))
    run_import(engine, archive("乙印务台", [entry("B-1", seq=2, notice="后来的")]))
    with Session(engine) as session:
        second = session.execute(select(archive_receipts).where(
            archive_receipts.c.source == "乙印务台"
        )).mappings().first()
    with Session(engine) as session, session.begin():
        adopted = archives.resolve(session, second["id"], "adopt", "reviewer")
    assert adopted["state"] == "restored"
    assert adopted["content"]["erratum"]["seq"] == 2


def test_resolve_rejects_incomplete_adopt(engine):
    import pytest
    from fastapi import HTTPException
    run_import(engine, archive("丙印务台", [entry("C-1", seq=1)]))
    run_import(engine, archive("丙印务台", [{"old_no": "C-1", "erratum": erratum(2, "后来的")}]))
    with Session(engine) as session:
        receipt = session.execute(select(archive_receipts)).mappings().first()
    with pytest.raises(HTTPException) as caught:
        with Session(engine) as session, session.begin():
            archives.resolve(session, receipt["id"], "adopt", "reviewer")
    assert caught.value.status_code == 422


def test_receipts_search_by_source_and_old_no(engine):
    run_import(engine, archive("甲印务台", [entry("A-105", seq=105), entry("A-104", seq=104)]))
    run_import(engine, archive("乙印务台", [entry("A-105", seq=205, notice="东锚地界线北移")]))
    with Session(engine) as session:
        wide = archives.receipts(session, None, "A-105", None, 100)
        narrow = archives.receipts(session, "乙印务台", "A-105", None, 100)
    assert {row["source"] for row in wide} == {"甲印务台", "乙印务台"}
    assert len(narrow) == 1 and narrow[0]["source"] == "乙印务台"
    assert narrow[0]["content"]["erratum"]["notice"] == "东锚地界线北移"


def test_missing_part_later_supplied_restores(engine):
    # First arrival keeps only the erratum; the signature arrives later.  The
    # number becomes restored once both originals are in, not a conflict.
    run_import(engine, archive("甲印务台", [{"old_no": "A-7", "erratum": erratum(7)}]))
    run_import(engine, archive("甲印务台", [{"old_no": "A-7", "signature": signature()}]))
    with Session(engine) as session:
        row = session.execute(select(archive_receipts)).mappings().first()
    assert row["state"] == "restored"
    assert row["content"]["erratum"]["seq"] == 7
    assert row["content"]["signature"]["actor"] == "reviewer"
    assert row["conflict"] is None


def test_disagreeing_erratum_is_a_conflict_not_a_merge(engine):
    run_import(engine, archive("甲印务台", [{"old_no": "A-7", "erratum": erratum(7, "原勘误")}]))
    run_import(engine, archive("甲印务台", [{"old_no": "A-7", "erratum": erratum(7, "另一份勘误"), "signature": signature()}]))
    with Session(engine) as session:
        row = session.execute(select(archive_receipts)).mappings().first()
    assert row["state"] == "pending"
    assert row["superseded"]["erratum"]["notice"] == "原勘误"


def test_duplicate_archive_key_with_different_entries_rejected(engine):
    import pytest
    from fastapi import HTTPException

    run_import(engine, archive("甲印务台", [entry("A-1", seq=1)], key="档案-甲-01"))
    with pytest.raises(HTTPException) as caught:
        run_import(engine, archive("甲印务台", [entry("A-2", seq=2)], key="档案-甲-01"))
    assert caught.value.status_code == 409


def test_same_key_across_offices_is_independent(engine):
    run_import(engine, archive("甲印务台", [entry("A-1", seq=1)], key="档案-01"))
    run_import(engine, archive("乙印务台", [entry("A-1", seq=2, notice="另一海图")], key="档案-01"))
    with Session(engine) as session:
        rows = session.execute(select(archive_receipts)).mappings().all()
    assert len(rows) == 2
    assert {row["source"] for row in rows} == {"甲印务台", "乙印务台"}


def test_rejected_document_retry_does_not_reopen_reconciliation(engine):
    original = archive("甲印务台", [entry("A-105", seq=105)])
    later = archive("甲印务台", [entry("A-105", seq=105, notice="改后的勘误")])
    run_import(engine, original)
    run_import(engine, later)
    with Session(engine) as session:
        receipt = session.execute(select(archive_receipts)).mappings().first()
    with Session(engine) as session, session.begin():
        archives.resolve(session, receipt["id"], "keep", "reviewer")
    # Uploading the turned-down archive again must leave the settled number alone.
    run_import(engine, later)
    run_import(engine, original)
    with Session(engine) as session:
        row = session.execute(select(archive_receipts)).mappings().first()
    assert row["state"] == "restored"
    assert row["content"]["erratum"]["notice"] == "外堤浮标灯质更正为黄闪"
    assert row["conflict"] is None


def test_third_conflicting_document_while_pending_keeps_one_conflict(engine):
    run_import(engine, archive("甲印务台", [entry("A-105", seq=105)]))
    run_import(engine, archive("甲印务台", [entry("A-105", seq=105, notice="第二份")]))
    run_import(engine, archive("甲印务台", [entry("A-105", seq=105, notice="第三份")]))
    with Session(engine) as session:
        row = session.execute(select(archive_receipts)).mappings().first()
    assert row["state"] == "pending"
    # The original is still the one set aside for a "keep" decision.
    assert row["superseded"]["erratum"]["notice"] == "外堤浮标灯质更正为黄闪"
    assert row["conflict"]["erratum"]["notice"] == "第三份"
    with Session(engine) as session, session.begin():
        kept = archives.resolve(session, row["id"], "keep", "reviewer")
    assert kept["state"] == "restored"
    assert kept["content"]["erratum"]["notice"] == "外堤浮标灯质更正为黄闪"
