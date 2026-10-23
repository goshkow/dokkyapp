"""Расписание автоматизаций: понятные человеку правила и чистая функция «пора запускать».

Формат строки: `manual`, `hourly`, `interval@N` (раз в N минут, 15-1440), `daily@HH:MM`, `weekdays@HH:MM`,
`weekly@D@HH:MM` (D от 1 понедельника до 7 воскресенья). Время локальное, как на часах пользователя.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

DAYS = ["понедельникам", "вторникам", "средам", "четвергам", "пятницам", "субботам", "воскресеньям"]
_TIME = r"([01]\d|2[0-3]):([0-5]\d)"
_PATTERNS = {
    "manual": re.compile(r"^manual$"),
    "hourly": re.compile(r"^hourly$"),
    "interval": re.compile(r"^interval@(\d{2,4})$"),
    "daily": re.compile(rf"^daily@{_TIME}$"),
    "weekdays": re.compile(rf"^weekdays@{_TIME}$"),
    "weekly": re.compile(rf"^weekly@([1-7])@{_TIME}$"),
}


class ScheduleError(ValueError):
    pass


def parse(schedule: str) -> dict:
    """Разбирает строку расписания; неверная строка - понятная ошибка."""
    text = str(schedule or "").strip()
    for kind, pattern in _PATTERNS.items():
        match = pattern.match(text)
        if not match:
            continue
        parts = match.groups()
        if kind == "interval":
            minutes = int(parts[0])
            if not 15 <= minutes <= 1440:
                raise ScheduleError("Интервал: от 15 минут до 24 часов")
            return {"kind": kind, "minutes": minutes}
        if kind in {"daily", "weekdays"}:
            return {"kind": kind, "hour": int(parts[0]), "minute": int(parts[1])}
        if kind == "weekly":
            return {"kind": kind, "day": int(parts[0]), "hour": int(parts[1]), "minute": int(parts[2])}
        return {"kind": kind}
    raise ScheduleError("Неизвестное расписание. Допустимо: вручную, каждый час, каждые N минут, каждый день, по будням, раз в неделю")


def describe(schedule: str) -> str:
    """Расписание словами для списка автоматизаций."""
    rule = parse(schedule)
    kind = rule["kind"]
    if kind == "manual":
        return "Вручную"
    if kind == "hourly":
        return "Каждый час"
    if kind == "interval":
        minutes = rule["minutes"]
        return f"Каждые {minutes // 60} ч" if minutes % 60 == 0 else f"Каждые {minutes} мин"
    at = f"{rule['hour']:02d}:{rule['minute']:02d}"
    if kind == "daily":
        return f"Каждый день в {at}"
    if kind == "weekdays":
        return f"По будням в {at}"
    return f"По {DAYS[rule['day'] - 1]} в {at}"


def last_slot(rule: dict, now: datetime) -> datetime | None:
    """Последний момент по расписанию не позже `now` (местное время без пояса); для ручных и интервальных - `None`."""
    kind = rule["kind"]
    if kind == "hourly":
        return now.replace(minute=0, second=0, microsecond=0)
    if kind in {"daily", "weekdays", "weekly"}:
        slot = now.replace(hour=rule["hour"], minute=rule["minute"], second=0, microsecond=0)
        for _ in range(8):
            fits = slot <= now and (
                kind == "daily"
                or (kind == "weekdays" and slot.weekday() < 5)
                or (kind == "weekly" and slot.isoweekday() == rule["day"])
            )
            if fits:
                return slot
            slot -= timedelta(days=1)
    return None


def to_local(value: str | None) -> datetime | None:
    """Время из базы (`YYYY-MM-DD HH:MM:SS`, UTC) в местное без пояса."""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().replace(tzinfo=None)


def is_due(schedule: str, base: datetime | None, now: datetime) -> bool:
    """Пора ли запускать: после последнего запуска (или создания) наступил запланированный момент."""
    try:
        rule = parse(schedule)
    except ScheduleError:
        return False
    if rule["kind"] == "manual":
        return False
    if base is None:
        return False
    if rule["kind"] == "interval":
        return now >= base + timedelta(minutes=rule["minutes"])
    slot = last_slot(rule, now)
    return slot is not None and slot > base


def next_run(schedule: str, base: datetime | None, now: datetime) -> datetime | None:
    """Ближайший будущий запуск для подписи «Следующий запуск»."""
    try:
        rule = parse(schedule)
    except ScheduleError:
        return None
    kind = rule["kind"]
    if kind == "manual":
        return None
    if kind == "interval":
        return max(now, (base or now) + timedelta(minutes=rule["minutes"]))
    if kind == "hourly":
        return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    candidate = now.replace(hour=rule["hour"], minute=rule["minute"], second=0, microsecond=0)
    for _ in range(9):
        fits = candidate > now and (
            kind == "daily"
            or (kind == "weekdays" and candidate.weekday() < 5)
            or (kind == "weekly" and candidate.isoweekday() == rule["day"])
        )
        if fits:
            return candidate
        candidate += timedelta(days=1)
    return None
