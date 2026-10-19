from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from server.catalog import Lesson

TICKET_LIMIT = 512
SIGNATURE = re.compile(r"^[0-9a-f]{64}$")


class TicketError(ValueError):
    pass


@dataclass(frozen=True)
class Ticket:
    lesson: Lesson
    issued_at: datetime


def _check_lesson(skill: str, level: int, lesson: Lesson | None) -> Lesson:
    if lesson is None or lesson.skill != skill or lesson.level != level:
        raise TicketError("Unknown lesson")
    return lesson


def parse_unsigned_line(line: str, lookup) -> Lesson:
    if not isinstance(line, str) or len(line) > TICKET_LIMIT:
        raise TicketError("Invalid line")
    parts = line.strip().split("|")
    if not parts or parts[0] != "v0":
        raise TicketError("Invalid line")
    fields: dict[str, str] = {}
    for item in parts[1:]:
        key, separator, value = item.partition("=")
        if not separator or not key or key in fields:
            raise TicketError("Invalid line")
        fields[key] = value
    if not {"skill", "level", "lesson"} <= fields.keys():
        raise TicketError("Incomplete line")
    if not fields.keys() <= {"skill", "level", "lesson", "from", "issued", "nonce"}:
        raise TicketError("Unknown field")
    try:
        level = int(fields["level"])
    except ValueError as exc:
        raise TicketError("Invalid level") from exc
    if level < 1:
        raise TicketError("Invalid level")
    lesson = _check_lesson(fields["skill"], level, lookup(fields["lesson"]))
    return lesson


def _payload(lesson: Lesson, issued_at: datetime, nonce: str) -> str:
    issued = issued_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        f"v0|skill={lesson.skill}|level={lesson.level}|lesson={lesson.id}|"
        f"issued={issued}|nonce={nonce}"
    )


def _sign(payload: str, secret: bytes) -> str:
    digest = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{digest}"


def make_ticket(
    lesson: Lesson, secret: bytes, issued_at: datetime | None = None
) -> str:
    issued_at = issued_at or datetime.now(timezone.utc)
    issued_at = issued_at.astimezone(timezone.utc).replace(microsecond=0)
    return _sign(_payload(lesson, issued_at, secrets.token_urlsafe(12)), secret)


def verify_ticket(ticket: str, secret: bytes, lookup, now: datetime | None = None) -> Ticket:
    if not isinstance(ticket, str) or len(ticket) > TICKET_LIMIT:
        raise TicketError("Invalid ticket")
    payload, separator, signature = ticket.rpartition(".")
    if not separator or not payload or not SIGNATURE.fullmatch(signature):
        raise TicketError("Invalid ticket")
    expected = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise TicketError("Invalid ticket")

    parts = payload.split("|")
    if len(parts) != 6 or parts[0] != "v0":
        raise TicketError("Invalid ticket")
    prefixes = ("skill=", "level=", "lesson=", "issued=", "nonce=")
    if any(not part.startswith(prefix) for part, prefix in zip(parts[1:], prefixes)):
        raise TicketError("Invalid ticket")
    skill = parts[1][len("skill=") :]
    try:
        level = int(parts[2][len("level=") :])
        issued_at = datetime.strptime(
            parts[4][len("issued=") :], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise TicketError("Invalid ticket") from exc
    if not parts[5][len("nonce=") :]:
        raise TicketError("Invalid ticket")
    lesson = _check_lesson(skill, level, lookup(parts[3][len("lesson=") :]))
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if issued_at > current + timedelta(minutes=5):
        raise TicketError("Invalid ticket")
    return Ticket(lesson=lesson, issued_at=issued_at)
