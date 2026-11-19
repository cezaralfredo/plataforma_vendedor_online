from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.payment import Payment
from app.schemas.payment import PaymentCreate, PaymentRead
from app.security import require_api_key
from app.services.asaas_client import AsaasIntegrationError
from app.services.payment_service import AsaasReconciliationError, PaymentService

router = APIRouter(
    prefix="/api/payments",
    tags=["payments"],
    dependencies=[Depends(require_api_key)],
)


@router.post("", response_model=PaymentRead, status_code=201)
async def create_payment(data: PaymentCreate, db: Session = Depends(get_db)):
    if data.appointment_id is None:
        raise HTTPException(status_code=400, detail="appointment_id é obrigatório")
    svc = PaymentService(db)
    try:
        payment = await svc.create_charge(data.appointment_id, data.billing_type, data.amount_cents)
        return payment
    except AsaasReconciliationError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except AsaasIntegrationError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/{payment_id}", response_model=PaymentRead)
def get_payment(payment_id: int, db: Session = Depends(get_db)):
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Pagamento não encontrado")
    return payment


@router.post("/{payment_id}/refresh", response_model=PaymentRead)
async def refresh_payment(payment_id: int, db: Session = Depends(get_db)):
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Pagamento não encontrado")
    svc = PaymentService(db)
    await svc.check_payment_status(payment)
    return payment


@router.post("/verify-recent", response_model=list[PaymentRead])
async def verify_recent_payments(db: Session = Depends(get_db)):
    svc = PaymentService(db)
    updated = await svc.verify_recent_payments()
    return updated
