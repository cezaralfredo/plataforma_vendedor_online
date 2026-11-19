from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.appointment import (
    AppointmentCreate,
    AppointmentRead,
    AppointmentUpdate,
)
from app.security import require_api_key
from app.services.appointment_service import AppointmentService

router = APIRouter(
    prefix="/api/appointments",
    tags=["appointments"],
    dependencies=[Depends(require_api_key)],
)


@router.post("", response_model=AppointmentRead, status_code=201)
def create_appointment(data: AppointmentCreate, db: Session = Depends(get_db)):
    service = AppointmentService(db)
    try:
        return service.create(data)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("", response_model=list[AppointmentRead])
def list_appointments(
    user_id: int | None = None,
    professional_id: int | None = None,
    status: str | None = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    service = AppointmentService(db)
    return service.list(user_id=user_id, professional_id=professional_id, status=status, skip=skip, limit=limit)


@router.get("/{appointment_id}", response_model=AppointmentRead)
def get_appointment(appointment_id: int, db: Session = Depends(get_db)):
    service = AppointmentService(db)
    apt = service.get(appointment_id)
    if not apt:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado")
    return apt


@router.put("/{appointment_id}", response_model=AppointmentRead)
def update_appointment(appointment_id: int, data: AppointmentUpdate, db: Session = Depends(get_db)):
    service = AppointmentService(db)
    try:
        apt = service.update(appointment_id, data)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not apt:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado")
    return apt


@router.post("/{appointment_id}/cancel", response_model=AppointmentRead)
def cancel_appointment(appointment_id: int, db: Session = Depends(get_db)):
    service = AppointmentService(db)
    try:
        apt = service.cancel(appointment_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if not apt:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado")
    return apt


@router.post("/{appointment_id}/confirm", response_model=AppointmentRead)
def confirm_appointment(appointment_id: int, db: Session = Depends(get_db)):
    service = AppointmentService(db)
    try:
        apt = service.confirm(appointment_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not apt:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado")
    return apt


@router.delete("/{appointment_id}", status_code=204)
def delete_appointment(appointment_id: int, db: Session = Depends(get_db)):
    service = AppointmentService(db)
    try:
        deleted = service.delete(appointment_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not deleted:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado")
