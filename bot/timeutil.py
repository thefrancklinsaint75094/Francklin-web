"""Dates, heures et découpage des nuits (heure de Paris)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from bot.config import PARIS

MOIS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_ts(value) -> datetime | None:
    """Convertit un horodatage renvoyé par PostgREST en datetime UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).replace("Z", "+00:00")
        # PostgREST peut renvoyer une précision de 1 à 6 décimales.
        if "." in text:
            head, rest = text.split(".", 1)
            frac = ""
            tail = ""
            for i, ch in enumerate(rest):
                if ch.isdigit():
                    frac += ch
                else:
                    tail = rest[i:]
                    break
            text = f"{head}.{(frac + '000000')[:6]}{tail}"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_paris(dt: datetime) -> datetime:
    return dt.astimezone(PARIS)


def hhmm(dt: datetime) -> str:
    """« 23h42 » en heure de Paris."""
    p = to_paris(dt)
    return f"{p.hour}h{p.minute:02d}"


def night_start_date(now: datetime, end_hour: int) -> date:
    """Date D de la nuit « du D au D+1 » contenant l'instant donné.

    Une nuit couvre [D à end_hour, D+1 à end_hour) : tout ce qui est livré
    avant 6h appartient à la nuit commencée la veille, tout ce qui est livré
    à partir de 6h appartient à la nuit suivante.
    """
    p = to_paris(now)
    if p.hour < end_hour:
        return (p - timedelta(days=1)).date()
    return p.date()


def night_bounds(night_date: date, end_hour: int) -> tuple[datetime, datetime]:
    """Bornes UTC [début, fin) de la nuit du night_date au lendemain."""
    start = datetime.combine(night_date, time(end_hour), tzinfo=PARIS)
    end = datetime.combine(night_date + timedelta(days=1), time(end_hour), tzinfo=PARIS)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def night_label(night_date: date) -> str:
    """« 3 au 4 septembre » ou « 30 septembre au 1er octobre »."""
    nxt = night_date + timedelta(days=1)

    def day(d: date) -> str:
        return "1er" if d.day == 1 else str(d.day)

    if night_date.month == nxt.month:
        return f"{day(night_date)} au {day(nxt)} {MOIS[nxt.month - 1]}"
    return f"{day(night_date)} {MOIS[night_date.month - 1]} au {day(nxt)} {MOIS[nxt.month - 1]}"


def minutes_since(dt: datetime | None, now: datetime | None = None) -> int:
    if dt is None:
        return 0
    now = now or now_utc()
    return max(0, int((now - dt).total_seconds() // 60))
