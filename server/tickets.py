from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from server.catalog import Lesson, LessonPolicy

TICKET_LIMIT = 512
SIGNATURE = re.compile(r"^[0-9a-f]{64}$")


class TicketError(ValueError):
    pass


@dataclass(frozen=True)
class Ticket:
    lesson: Lesson
    issued_at: datetime
    available_at: datetime


@dataclass(frozen=True)
class Completion:
    lesson: Lesson
    done: int
    got_there: int
    stopped: bool


def _check_lesson(skill: str, level: int, lesson: Lesson | None) -> Lesson:
    if lesson is None or lesson.skill != skill or lesson.level != level:
        raise TicketError("Unknown lesson")
    return lesson


def _parse_fields(line: str) -> dict[str, str]:
    if not isinstance(line, str) or len(line) > TICKET_LIMIT:
        raise TicketError("Invalid line")
    parts = line.strip().split("|")
    if not parts or parts[0] not in {"v0", "v1"}:
        raise TicketError("Invalid line")
    fields: dict[str, str] = {}
    for item in parts[1:]:
        key, separator, value = item.partition("=")
        if not separator or not key or key in fields:
            raise TicketError("Invalid line")
        fields[key] = value
    return fields


def parse_unsigned_line(line: str, lookup) -> Lesson:
    fields = _parse_fields(line)
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


def parse_completion_line(line: str, lookup, policies) -> Completion:
    parts = line.strip().split("|") if isinstance(line, str) else []
    if not parts or parts[0] not in {"v1", "v0"}:
        raise TicketError("Invalid line")
    fields = _parse_fields(line)
    if parts[0] == "v0":
        if not {"skill", "level", "lesson"} <= fields.keys():
            raise TicketError("Invalid line")
        legacy = parse_unsigned_line(line, lookup)
        policy = policies.get(legacy.id)
        if policy is None:
            raise TicketError("Unknown lesson")
        return Completion(
            lesson=legacy,
            done=policy.item_count,
            # A legacy line has no outcome counts, so it cannot claim mastery.
            got_there=0,
            stopped=False,
        )
    expected = {"from", "done", "got_there"}
    if not expected <= fields.keys() or not fields.keys() <= expected | {"stop"}:
        raise TicketError("Incomplete line")
    lesson = lookup(fields["from"])
    if lesson is None:
        raise TicketError("Unknown lesson")
    policy = policies.get(lesson.id)
    if policy is None:
        raise TicketError("Lesson has no server routing policy")
    try:
        done = int(fields["done"])
        got_there = int(fields["got_there"])
    except ValueError as exc:
        raise TicketError("Invalid outcome counts") from exc
    if done < 0 or got_there < 0 or done > policy.item_count or got_there > done:
        raise TicketError("Invalid outcome counts")
    stop = fields.get("stop")
    if stop is not None and stop != "1":
        raise TicketError("Invalid stop value")
    return Completion(lesson=lesson, done=done, got_there=got_there, stopped=stop == "1")


def choose_route(completion: Completion, policy: LessonPolicy, advance_min: int):
    if completion.stopped:
        return policy.routes.stopped
    if completion.got_there >= advance_min:
        return policy.routes.advance
    return policy.routes.retry


def _payload(lesson: Lesson, issued_at: datetime, available_at: datetime, nonce: str) -> str:
    issued = issued_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    available = available_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        f"v0|skill={lesson.skill}|level={lesson.level}|lesson={lesson.id}|"
        f"issued={issued}|available={available}|nonce={nonce}"
    )


def _sign(payload: str, secret: bytes) -> str:
    digest = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{digest}"


def make_ticket(
    lesson: Lesson,
    secret: bytes,
    issued_at: datetime | None = None,
    available_at: datetime | None = None,
) -> str:
    issued_at = issued_at or datetime.now(timezone.utc)
    issued_at = issued_at.astimezone(timezone.utc).replace(microsecond=0)
    available_at = available_at or issued_at
    available_at = available_at.astimezone(timezone.utc).replace(microsecond=0)
    return _sign(_payload(lesson, issued_at, available_at, secrets.token_urlsafe(12)), secret)


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
    if len(parts) != 7 or parts[0] != "v0":
        raise TicketError("Invalid ticket")
    prefixes = ("skill=", "level=", "lesson=", "issued=", "available=", "nonce=")
    if any(not part.startswith(prefix) for part, prefix in zip(parts[1:], prefixes)):
        raise TicketError("Invalid ticket")
    skill = parts[1][len("skill=") :]
    try:
        level = int(parts[2][len("level=") :])
        issued_at = datetime.strptime(
            parts[4][len("issued=") :], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
        available_at = datetime.strptime(
            parts[5][len("available=") :], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise TicketError("Invalid ticket") from exc
    if not parts[6][len("nonce=") :]:
        raise TicketError("Invalid ticket")
    lesson = _check_lesson(skill, level, lookup(parts[3][len("lesson=") :]))
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if issued_at > current + timedelta(minutes=5):
        raise TicketError("Invalid ticket")
    if available_at < issued_at:
        raise TicketError("Invalid ticket")
    return Ticket(lesson=lesson, issued_at=issued_at, available_at=available_at)
