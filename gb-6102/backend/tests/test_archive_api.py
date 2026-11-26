import importlib
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    # Point settings and the engine at a throwaway database before importing the app.
    url = f"sqlite+pysqlite:///{tmp_path}/api.db"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("SEED_DEMO", "false")
    from src.config import settings, resources

    importlib.reload(settings)
    importlib.reload(resources)
    resources.engine.dispose()
    resources.engine = create_engine(url, pool_pre_ping=True)

    from src.models.base import metadata
    from src.models.users import users
    from src.utils.passwords import hash_password

    metadata.create_all(resources.engine)
    with Session(resources.engine) as session, session.begin():
        for role in ("operator", "reviewer", "viewer"):
            session.execute(users.insert().values(
                username=role, role=role, password_hash=hash_password("Demo-DbWorkbench-2026"),
            ))

    # Modules that bound ``engine`` at import time must be rebuilt against the
    # throwaway database.
    for name in list(sys.modules):
        if name.startswith(("src.routes", "src.controllers", "src.services", "src.worker")):
            importlib.reload(sys.modules[name])
    import src.main as application
    importlib.reload(application)
    with TestClient(application.app) as client:
        yield client, resources.engine


def token(client, role):
    response = client.post("/auth/login", json={"username": role, "password": "Demo-DbWorkbench-2026"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def entry(old_no="A-105", seq=105, notice="外堤浮标灯质更正为黄闪", chart="CN-12301"):
    return {"old_no": old_no,
            "erratum": {"seq": seq, "issue_date": "2026-11-23", "chart": chart, "notice": notice},
            "signature": {"actor": "reviewer", "note": "复核无误", "created_at": 1700000000}}


def body(source="甲印务台", entries=None):
    return {"source": source, "entries": entries or [entry()]}


def drain(engine):
    from sqlalchemy.orm import Session
    from src.services import archives

    with Session(engine) as session, session.begin():
        while archives.claim(session):
            pass


def test_upload_requires_operator(app_client):
    client, _ = app_client
    operator = token(client, "operator")
    reviewer = token(client, "reviewer")
    assert client.post("/archives", json=body(), headers=reviewer).status_code == 403
    assert client.post("/archives", json=body(), headers=operator).status_code == 202


def test_upload_validates_entries(app_client):
    client, _ = app_client
    headers = token(client, "operator")
    assert client.post("/archives", json={"source": "甲印务台", "entries": [{"old_no": "", "erratum": {}}]}, headers=headers).status_code == 422
    assert client.post("/archives", json={"source": "甲印务台", "entries": [{"old_no": "A-1"}]}, headers=headers).status_code == 422
    assert client.post("/archives", json={"source": "", "entries": []}, headers=headers).status_code == 422


def test_receipt_search_by_source(app_client):
    client, engine = app_client
    headers = token(client, "operator")
    assert client.post("/archives", json=body("甲印务台"), headers=headers).status_code == 202
    assert client.post("/archives", json=body("乙印务台", [
        entry(seq=205, notice="东锚地界线北移", chart="CN-12309"),
    ]), headers=headers).status_code == 202
    drain(engine)
    found = client.get("/archives/receipts", params={"old_no": "A-105"}, headers=headers)
    assert found.status_code == 200
    rows = found.json()
    assert {row["source"] for row in rows} == {"甲印务台", "乙印务台"}
    one = client.get("/archives/receipts", params={"source": "乙印务台", "old_no": "A-105"}, headers=headers)
    assert [row["source"] for row in one.json()] == ["乙印务台"]
    assert one.json()[0]["content"]["erratum"]["notice"] == "东锚地界线北移"


def test_receipt_routes_precede_import_detail(app_client):
    client, _ = app_client
    headers = token(client, "operator")
    # /archives/receipts must not be swallowed by /archives/{import_id}.
    assert client.get("/archives/receipts", headers=headers).status_code == 200


def test_receipts_hidden_from_anonymous(app_client):
    client, _ = app_client
    assert client.get("/archives/receipts").status_code == 401
    assert client.get("/archives").status_code == 401


def test_duplicate_upload_reuses_import(app_client):
    client, engine = app_client
    headers = token(client, "operator")
    first = client.post("/archives", json=body(), headers=headers).json()
    second = client.post("/archives", json=body(), headers=headers).json()
    assert first["id"] == second["id"]
    drain(engine)
    assert client.get("/archives/receipts", headers=headers).json()[0]["state"] == "restored"


def test_resolve_requires_reviewer(app_client):
    client, engine = app_client
    operator = token(client, "operator")
    reviewer = token(client, "reviewer")
    client.post("/archives", json=body(), headers=operator)
    client.post("/archives", json=body(entries=[entry(notice="改后的勘误")]), headers=operator)
    import_id = client.get("/archives", headers=operator).json()[0]["id"]
    drain(engine)
    receipt = client.get("/archives/receipts", headers=operator).json()[0]
    assert receipt["state"] == "pending"
    assert client.post(f"/archives/receipts/{receipt['id']}/resolve", json={"decision": "keep"}, headers=operator).status_code == 403
    resolved = client.post(f"/archives/receipts/{receipt['id']}/resolve", json={"decision": "keep"}, headers=reviewer)
    assert resolved.status_code == 202
    assert resolved.json()["state"] == "restored"
    assert client.post(f"/archives/receipts/{receipt['id']}/resolve", json={"decision": "adopt"}, headers=reviewer).status_code == 409
    assert import_id > 0
