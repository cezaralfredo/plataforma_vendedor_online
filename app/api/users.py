from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.repositories.base import RelatedRecordsError
from app.schemas.user import (
    UserCreate,
    UserImportRequest,
    UserImportSummary,
    UserRead,
    UserUpdate,
)
from app.security import require_api_key
from app.services.client_import_service import ClientImportService
from app.services.user_service import UserService

router = APIRouter(
    prefix="/api/users",
    tags=["users"],
    dependencies=[Depends(require_api_key)],
)


@router.post("", response_model=UserRead, status_code=201)
def create_user(data: UserCreate, db: Session = Depends(get_db)):
    try:
        return UserService(db).create(data)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("", response_model=list[UserRead])
def list_users(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return UserService(db).repo.list(skip=skip, limit=limit)


@router.post("/import", response_model=UserImportSummary)
def import_users_json(data: UserImportRequest, db: Session = Depends(get_db)):
    return ClientImportService(db).import_items(data)


@router.post("/import/csv", response_model=UserImportSummary)
async def import_users_csv(
    file: UploadFile = File(...),
    source: str = Query("import_csv", description="Identificador da plataforma ou origem"),
    deduplication_strategy: str = Query("update", pattern="^(update|skip|error)$"),
    import_batch_id: str | None = Query(None),
    db: Session = Depends(get_db),
):
    max_csv_bytes = 5 * 1024 * 1024  # 5 MB
    try:
        content_bytes = await file.read(max_csv_bytes + 1)
        if len(content_bytes) > max_csv_bytes:
            raise HTTPException(status_code=413, detail="Arquivo CSV excede o limite máximo de 5 MB.")
        csv_text = content_bytes.decode("utf-8-sig", errors="replace")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Erro ao ler arquivo CSV: {exc}") from exc

    return ClientImportService(db).import_csv_content(
        csv_text=csv_text,
        source=source,
        deduplication_strategy=deduplication_strategy,
        import_batch_id=import_batch_id,
    )


@router.get("/{user_id}", response_model=UserRead)
def get_user(user_id: int, db: Session = Depends(get_db)):
    user = UserService(db).get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    return user


@router.put("/{user_id}", response_model=UserRead)
def update_user(user_id: int, data: UserUpdate, db: Session = Depends(get_db)):
    try:
        user = UserService(db).update(user_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    return user


@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: int, db: Session = Depends(get_db)):
    try:
        if not UserService(db).delete(user_id):
            raise HTTPException(status_code=404, detail="Usuário não encontrado")
    except RelatedRecordsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
