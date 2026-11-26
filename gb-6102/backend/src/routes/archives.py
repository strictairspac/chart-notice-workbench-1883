from fastapi import APIRouter, Depends, HTTPException, Query

from src.config import resources
from src.constants.archive import ReceiptState
from src.controllers.access import identity, operator, reviewer
from src.controllers.workbench import new_archive, resolve_receipt, resume_archive
from src.domain import archives as rules
from src.schemas.archives import ArchiveImport, ArchiveInput, ArchiveReceipt, ResolveInput
from src.services import archives

router = APIRouter(prefix="/archives", tags=["Archives"])


@router.post("", response_model=ArchiveImport, status_code=202)
def upload(form: ArchiveInput, user=Depends(operator)):
    try:
        data = rules.normalize(form.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return new_archive(data, user)


@router.get("", response_model=list[ArchiveImport], dependencies=[Depends(identity)])
def list_imports():
    with resources.engine.connect() as db:
        return archives.list_imports(db)


@router.get("/receipts", response_model=list[ArchiveReceipt], dependencies=[Depends(identity)])
def list_receipts(
    source: str | None = Query(default=None, max_length=48),
    old_no: str | None = Query(default=None, max_length=48),
    state: ReceiptState | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=300),
):
    with resources.engine.connect() as db:
        return archives.receipts(db, source, old_no, state, limit)


@router.post("/receipts/{receipt_id}/resolve", response_model=ArchiveReceipt, status_code=202)
def resolve(receipt_id: int, form: ResolveInput, user=Depends(reviewer)):
    return resolve_receipt(receipt_id, form.decision, user)


@router.get("/{import_id}", response_model=ArchiveImport, dependencies=[Depends(identity)])
def import_detail(import_id: int):
    with resources.engine.connect() as db:
        return archives.view(db, import_id)


@router.post("/{import_id}/resume", response_model=ArchiveImport, status_code=202)
def resume(import_id: int, user=Depends(operator)):
    return resume_archive(import_id, user)
