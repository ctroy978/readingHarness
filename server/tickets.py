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
    carry: str = ""


@dataclass(frozen=True)
class HoldTicket:
    completion: Completion
    issued_at: datetime


@dataclass(frozen=True)
class Completion:
    lesson: Lesson
    done: int
    got_there: int
    stopped: bool
    unaided: int = 0
    helped: int = 0
    mercy: int = 0
    open: int = 0
    anchor_failed: bool = False
    carry: str = ""


def _check_lesson(skill: str, level: int, lesson: Lesson | None) -> Lesson:
    if lesson is None or lesson.skill != skill or lesson.level != level:
        raise TicketError("Unknown lesson")
    return lesson


def _parse_fields(line: str) -> dict[str, str]:
    if not isinstance(line, str) or len(line) > TICKET_LIMIT:
        raise TicketError("Invalid line")
    parts = line.strip().split("|")
    if not parts or parts[0] not in {"v0", "v1", "v2"}:
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


CARRY = re.compile(r"^\d+\.\d+\.[01m]{0,3}\.\d+$")


def _parse_v2(fields: dict[str, str], lookup, policies) -> Completion:
    """v2 carries the per-item result split and the rung memory.

    u = items right with no help, h = right after a help, m = right only after
    tell and a say-back, o = given up. an=0 means an anchor item needed help.
    """
    allowed = {"from", "done", "u", "h", "m", "o", "an", "carry", "stop"}
    if not {"from", "done", "u", "h", "carry"} <= fields.keys() or not fields.keys() <= allowed:
        raise TicketError("Incomplete line")
    lesson = lookup(fields["from"])
    if lesson is None:
        raise TicketError("Unknown lesson")
    policy = policies.get(lesson.id)
    if policy is None:
        raise TicketError("Lesson has no server routing policy")
    try:
        done = int(fields["done"])
        unaided = int(fields["u"])
        helped = int(fields["h"])
        mercy = int(fields.get("m", "0"))
        given_up = int(fields.get("o", "0"))
    except ValueError as exc:
        raise TicketError("Invalid outcome counts") from exc
    counts = (done, unaided, helped, mercy, given_up)
    if any(value < 0 for value in counts) or done > policy.item_count:
        raise TicketError("Invalid outcome counts")
    if unaided + helped + mercy + given_up != done:
        raise TicketError("Invalid outcome counts")
    anchor = fields.get("an", "1")
    if anchor not in {"0", "1"}:
        raise TicketError("Invalid anchor value")
    stop = fields.get("stop")
    if stop is not None and stop != "1":
        raise TicketError("Invalid stop value")
    carry = fields["carry"]
    if carry != "" and CARRY.fullmatch(carry) is None:
        raise TicketError("Invalid carry")
    return Completion(
        lesson=lesson,
        done=done,
        got_there=unaided + helped,
        stopped=stop == "1",
        unaided=unaided,
        helped=helped,
        mercy=mercy,
        open=given_up,
        anchor_failed=anchor == "0",
        carry=carry,
    )


def parse_completion_line(line: str, lookup, policies) -> Completion:
    parts = line.strip().split("|") if isinstance(line, str) else []
    if not parts or parts[0] not in {"v1", "v0", "v2"}:
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
    if parts[0] == "v2":
        return _parse_v2(fields, lookup, policies)
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


def make_hold_ticket(
    completion: Completion,
    secret: bytes,
    issued_at: datetime | None = None,
) -> str:
    issued_at = (issued_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    issued_at = issued_at.replace(microsecond=0)
    stop = "1" if completion.stopped else "0"
    payload = (
        f"v0|hold=1|from={completion.lesson.id}|done={completion.done}|"
        f"got_there={completion.got_there}|stop={stop}|"
        f"issued={issued_at.strftime('%Y-%m-%dT%H:%M:%SZ')}|"
        f"nonce={secrets.token_urlsafe(12)}"
    )
    return _sign(payload, secret)


def verify_hold_ticket(
    ticket: str,
    secret: bytes,
    lookup,
    policies,
) -> HoldTicket:
    if not isinstance(ticket, str) or len(ticket) > TICKET_LIMIT:
        raise TicketError("Invalid hold ticket")
    payload, separator, signature = ticket.rpartition(".")
    if not separator or not payload or not SIGNATURE.fullmatch(signature):
        raise TicketError("Invalid hold ticket")
    expected = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise TicketError("Invalid hold ticket")
    parts = payload.split("|")
    if len(parts) != 8 or parts[0] != "v0" or parts[1] != "hold=1":
        raise TicketError("Invalid hold ticket")
    prefixes = ("from=", "done=", "got_there=", "stop=", "issued=", "nonce=")
    if any(not part.startswith(prefix) for part, prefix in zip(parts[2:], prefixes)):
        raise TicketError("Invalid hold ticket")
    lesson = lookup(parts[2][len("from=") :])
    if lesson is None or lesson.id not in policies:
        raise TicketError("Unknown lesson")
    try:
        done = int(parts[3][len("done=") :])
        got_there = int(parts[4][len("got_there=") :])
        stop = parts[5][len("stop=") :]
        issued_at = datetime.strptime(
            parts[6][len("issued=") :], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise TicketError("Invalid hold ticket") from exc
    policy = policies[lesson.id]
    if stop not in {"0", "1"} or done < 0 or got_there < 0:
        raise TicketError("Invalid hold ticket")
    if done > policy.item_count or got_there > done:
        raise TicketError("Invalid hold ticket")
    if not parts[7][len("nonce=") :]:
        raise TicketError("Invalid hold ticket")
    if issued_at > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise TicketError("Invalid hold ticket")
    return HoldTicket(
        completion=Completion(
            lesson=lesson,
            done=done,
            got_there=got_there,
            stopped=stop == "1",
        ),
        issued_at=issued_at,
    )


def choose_route(completion: Completion, policy: LessonPolicy, advance_min: int):
    if completion.stopped:
        return policy.routes.stopped
    if completion.got_there >= advance_min:
        return policy.routes.advance
    return policy.routes.retry


def _payload(
    lesson: Lesson,
    issued_at: datetime,
    available_at: datetime,
    nonce: str,
    carry: str = "",
) -> str:
    issued = issued_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    available = available_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    base = (
        f"v0|skill={lesson.skill}|level={lesson.level}|lesson={lesson.id}|"
        f"issued={issued}|available={available}|nonce={nonce}"
    )
    return f"{base}|carry={carry}" if carry else base


def _sign(payload: str, secret: bytes) -> str:
    digest = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{digest}"


def make_ticket(
    lesson: Lesson,
    secret: bytes,
    issued_at: datetime | None = None,
    available_at: datetime | None = None,
    carry: str = "",
) -> str:
    issued_at = issued_at or datetime.now(timezone.utc)
    issued_at = issued_at.astimezone(timezone.utc).replace(microsecond=0)
    available_at = available_at or issued_at
    available_at = available_at.astimezone(timezone.utc).replace(microsecond=0)
    return _sign(
        _payload(lesson, issued_at, available_at, secrets.token_urlsafe(12), carry),
        secret,
    )


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
    if len(parts) not in {7, 8} or parts[0] != "v0":
        raise TicketError("Invalid ticket")
    prefixes = ("skill=", "level=", "lesson=", "issued=", "available=", "nonce=")
    if any(not part.startswith(prefix) for part, prefix in zip(parts[1:], prefixes)):
        raise TicketError("Invalid ticket")
    carry = ""
    if len(parts) == 8:
        if not parts[7].startswith("carry="):
            raise TicketError("Invalid ticket")
        carry = parts[7][len("carry=") :]
        if carry and CARRY.fullmatch(carry) is None:
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
    return Ticket(
        lesson=lesson, issued_at=issued_at, available_at=available_at, carry=carry
    )
