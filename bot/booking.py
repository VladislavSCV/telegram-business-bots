"""Online booking domain: catalog of a demo barbershop and free-slot calculation."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta


@dataclass(frozen=True)
class Service:
    code: str
    title: str
    duration_min: int
    price: int


SERVICES = [
    Service("cut", "Мужская стрижка", 60, 1800),
    Service("cut_beard", "Стрижка + борода", 90, 2500),
    Service("beard", "Моделирование бороды", 30, 1000),
    Service("kids", "Детская стрижка", 45, 1300),
]
MASTERS = ["Артём", "Илья", "Марк"]
OPEN, CLOSE = time(10, 0), time(21, 0)
STEP_MIN = 30
# A slot must start at least this far from now so the master can prepare.
LEAD_TIME = timedelta(hours=1)
DAYS_AHEAD = 7

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def service_by_code(code: str) -> Service | None:
    return next((s for s in SERVICES if s.code == code), None)


def overlaps(start: datetime, duration_min: int, other_start: datetime, other_duration_min: int) -> bool:
    end = start + timedelta(minutes=duration_min)
    other_end = other_start + timedelta(minutes=other_duration_min)
    return start < other_end and other_start < end


def booking_days(now: datetime) -> list[date]:
    return [(now + timedelta(days=i)).date() for i in range(DAYS_AHEAD)]


def free_slots(day: date, duration_min: int, busy: list[tuple[datetime, int]], now: datetime) -> list[datetime]:
    """Start times on `day` (every STEP_MIN) where the service fits before closing and does not
    overlap existing bookings of the master. `now` and the result share the same timezone."""
    tz = now.tzinfo
    cursor = datetime.combine(day, OPEN, tzinfo=tz)
    closing = datetime.combine(day, CLOSE, tzinfo=tz)
    slots = []
    while cursor + timedelta(minutes=duration_min) <= closing:
        if cursor >= now + LEAD_TIME and not any(overlaps(cursor, duration_min, s, d) for s, d in busy):
            slots.append(cursor)
        cursor += timedelta(minutes=STEP_MIN)
    return slots


def day_label(day: date, today: date) -> str:
    if day == today:
        prefix = "Сегодня"
    elif day == today + timedelta(days=1):
        prefix = "Завтра"
    else:
        prefix = WEEKDAYS[day.weekday()]
    return f"{prefix}, {day:%d.%m}"
