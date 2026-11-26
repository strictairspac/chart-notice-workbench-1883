import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("DEMO_PASSWORD", "Demo-DbWorkbench-2026")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")


@pytest.fixture()
def engine(tmp_path):
    from src.models.base import metadata

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path}/workbench.db")
    metadata.create_all(engine)
    yield engine
    engine.dispose()
