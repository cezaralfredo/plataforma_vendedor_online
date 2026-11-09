from __future__ import annotations

import builtins
from datetime import datetime, time, timedelta

from sqlalchemy.orm import Session

from app.business_time import as_business_time, business_datetime, utc_now
from app.models.availability import Availability
from app.models.professional import Professional
from app.models.professional_service import ProfessionalService
from app.models.service import Service
from app.repositories import (
    AppointmentRepository,
    AvailabilityRepository,
)
from app.schemas.availability import AvailabilityCreate, AvailabilityUpdate, TimeSlot


class AvailabilityService:
    def __init__(self, db: Session):
        self.repo = AvailabilityRepository(db)
        self.appointment_repo = AppointmentRepository(db)

    def create(self, data: AvailabilityCreate):
        if not self.repo.db.get(Professional, data.professional_id):
            raise ValueError("Profissional não encontrado")
        self._ensure_no_overlap(data)
        return self.repo.create(**data.model_dump())

    def get(self, availability_id: int):
        return self.repo.get(availability_id)

    def list(self, professional_id: int | None = None):
        if professional_id is not None:
            return self.repo.list_by_professional(professional_id)
        return self.repo.list()

    def update(self, availability_id: int, data: AvailabilityUpdate):
        availability = self.repo.get(availability_id)
        if not availability:
            return None
        values = data.model_dump(exclude_unset=True)
        merged = {
            "professional_id": availability.professional_id,
            "day_of_week": availability.day_of_week,
            "start_time": availability.start_time,
            "end_time": availability.end_time,
            "specific_date": availability.specific_date,
            **values,
        }
        candidate = AvailabilityCreate(**merged)
        self._ensure_no_overlap(candidate, exclude_availability_id=availability_id)
        return self.repo.update(availability_id, **values)

    def _ensure_no_overlap(
        self,
        data: AvailabilityCreate,
        exclude_availability_id: int | None = None,
    ) -> None:
        query = self.repo.db.query(Availability).filter(
            Availability.professional_id == data.professional_id,
            Availability.start_time < data.end_time,
            Availability.end_time > data.start_time,
        )
        if data.specific_date is not None:
            query = query.filter(Availability.specific_date == data.specific_date)
        else:
            query = query.filter(Availability.day_of_week == data.day_of_week)
        if exclude_availability_id is not None:
            query = query.filter(Availability.id != exclude_availability_id)
        if query.first():
            raise ValueError("O horário se sobrepõe a uma disponibilidade existente")

    def delete(self, availability_id: int):
        return self.repo.delete(availability_id)

    def check_availability(self, professional_id: int, date_str: str) -> builtins.list[TimeSlot]:
        self.appointment_repo.expire_reservations(utc_now())
        self.appointment_repo.db.commit()
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return []

        day_of_week = date_obj.weekday()

        # Get specific date availability first
        slots = self.repo.find_by_date(professional_id, date_obj)
        if not slots:
            # Fall back to recurring weekly availability
            slots = self.repo.find_by_day(professional_id, day_of_week)

        if not slots:
            return []

        available_slots: list[TimeSlot] = []

        for slot in slots:
            if slot.specific_date:
                slot_date = slot.specific_date
            else:
                slot_date = date_obj

            if slot.start_time and slot.end_time:
                slot_start = business_datetime(slot_date, slot.start_time)
                slot_end = business_datetime(slot_date, slot.end_time)
                available_slots.append(TimeSlot(start=slot_start, end=slot_end))

        # Defensive de-duplication: identical availability rows (e.g. the same
        # window inserted more than once) must not produce duplicated slots.
        available_slots = _dedupe_slots(available_slots)

        # Get busy appointments for the day
        day_start = business_datetime(date_obj, time.min)
        day_end = business_datetime(date_obj, time.max)

        busy = self.appointment_repo.find_conflicting(
            professional_id, day_start.isoformat(), day_end.isoformat()
        )

        if not busy:
            return available_slots

        free_slots: list[TimeSlot] = []
        for slot in available_slots:
            current_start = slot.start
            for b in sorted(busy, key=lambda x: x.start_time):
                busy_start = as_business_time(b.start_time)
                busy_end = as_business_time(b.end_time)
                if isinstance(busy_start, str):
                    busy_start = datetime.fromisoformat(busy_start)
                if isinstance(busy_end, str):
                    busy_end = datetime.fromisoformat(busy_end)

                # An appointment outside this availability window must not
                # expand or consume this window.
                if busy_end <= current_start or busy_start >= slot.end:
                    continue
                if busy_start > current_start:
                    free_slots.append(TimeSlot(start=current_start, end=min(busy_start, slot.end)))
                current_start = max(current_start, min(busy_end, slot.end))
            if current_start < slot.end:
                free_slots.append(TimeSlot(start=current_start, end=slot.end))

        return _dedupe_slots(free_slots)

    def get_time_slots_for_service(self, professional_id: int, service_id: int, date_str: str) -> builtins.list[TimeSlot]:
        offering = (
            self.repo.db.query(ProfessionalService)
            .join(Service)
            .join(Professional)
            .filter(
                ProfessionalService.professional_id == professional_id,
                ProfessionalService.service_id == service_id,
                ProfessionalService.active.is_(True),
                Service.active.is_(True),
                Professional.active.is_(True),
            )
            .first()
        )
        if not offering:
            return []

        free_periods = self.check_availability(professional_id, date_str)

        slots: list[TimeSlot] = []
        duration = timedelta(minutes=offering.service.duration_minutes)

        for period in free_periods:
            period_start = period.start
            period_end = period.end
            cursor = period_start
            while cursor + duration <= period_end:
                end = cursor + duration
                slots.append(TimeSlot(
                    start=cursor,
                    end=end,
                ))
                cursor = end

        return _dedupe_slots(slots)

    def is_interval_available(self, professional_id: int, start: datetime, end: datetime) -> bool:
        """Return whether the entire requested interval is inside one free period."""
        start = as_business_time(start)
        end = as_business_time(end)
        date_str = start.date().isoformat()
        if end.date() != start.date():
            return False
        return any(period.start <= start and end <= period.end
                   for period in self.check_availability(professional_id, date_str))


    def get_available_slots(
        self, professional_id: int, date_str: str, service_id: int | None = None
    ) -> list[str]:
        """Retorna lista de horários formatados (HH:MM) para IA e canais conversacionais."""
        if service_id:
            slots = self.get_time_slots_for_service(professional_id, service_id, date_str)
        else:
            slots = self.check_availability(professional_id, date_str)
        return [
            s.start.strftime("%H:%M") if hasattr(s.start, "strftime") else str(s.start)
            for s in slots
        ]


def _dedupe_slots(slots: list[TimeSlot]) -> list[TimeSlot]:
    """Remove duplicate time slots while preserving order.

    Exact duplicate windows can arise when the `availability` table contains
    more than one row describing the same period (e.g. a specific datetime and
    a weekly recurrence that resolve to the same window, or a row inserted
    twice). Serving a window twice is incorrect for availability checks.
    """
    seen: set[tuple[datetime, datetime]] = set()
    result: list[TimeSlot] = []
    for slot in slots:
        key = (slot.start, slot.end)
        if key in seen:
            continue
        seen.add(key)
        result.append(slot)
    return result
