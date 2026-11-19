from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.availability import (
    AvailabilityCreate,
    AvailabilityRead,
    AvailabilityUpdate,
    TimeSlot,
)
from app.security import require_api_key
from app.services.availability_service import AvailabilityService

router = APIRouter(
    prefix="/api/availability",
    tags=["availability"],
    dependencies=[Depends(require_api_key)],
)


@router.post("", response_model=AvailabilityRead, status_code=201)
def create_availability(data: AvailabilityCreate, db: Session = Depends(get_db)):
    service = AvailabilityService(db)
    try:
        return service.create(data)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("", response_model=list[AvailabilityRead])
def list_availability(professional_id: int | None = None, db: Session = Depends(get_db)):
    service = AvailabilityService(db)
    return service.list(professional_id=professional_id)


@router.get("/{availability_id}", response_model=AvailabilityRead)
def get_availability(availability_id: int, db: Session = Depends(get_db)):
    service = AvailabilityService(db)
    av = service.get(availability_id)
    if not av:
        raise HTTPException(status_code=404, detail="Disponibilidade não encontrada")
    return av


@router.put("/{availability_id}", response_model=AvailabilityRead)
def update_availability(availability_id: int, data: AvailabilityUpdate, db: Session = Depends(get_db)):
    service = AvailabilityService(db)
    try:
        av = service.update(availability_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not av:
        raise HTTPException(status_code=404, detail="Disponibilidade não encontrada")
    return av


@router.delete("/{availability_id}", status_code=204)
def delete_availability(availability_id: int, db: Session = Depends(get_db)):
    service = AvailabilityService(db)
    if not service.delete(availability_id):
        raise HTTPException(status_code=404, detail="Disponibilidade não encontrada")


@router.get("/check/{professional_id}", response_model=list[TimeSlot])
def check_availability(professional_id: int, date: str, db: Session = Depends(get_db)):
    service = AvailabilityService(db)
    return service.check_availability(professional_id, date)


@router.get("/slots/{professional_id}/{service_id}", response_model=list[TimeSlot])
def get_time_slots(professional_id: int, service_id: int, date: str, db: Session = Depends(get_db)):
    service = AvailabilityService(db)
    return service.get_time_slots_for_service(professional_id, service_id, date)
