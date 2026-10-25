from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from server.progression import LessonSet


@dataclass(frozen=True)
class AlertSettings:
    api_key: str
    sender: str
    recipient: str
    reply_to: str | None

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.sender and self.recipient)


def load_alert_settings() -> AlertSettings:
    return AlertSettings(
        api_key=os.environ.get("RESEND_API_KEY", "").strip(),
        sender=os.environ.get("RESEND_FROM", "").strip(),
        recipient=os.environ.get("ALERT_EMAIL_TO", "").strip(),
        reply_to=os.environ.get("RESEND_REPLY_TO", "").strip() or None,
    )


def format_gap_alert(
    lesson_set: LessonSet,
    current_lesson_id: str,
    missing_sequences: tuple[int, ...],
    hold_id: str,
) -> tuple[str, str]:
    subject = (
        f"Curriculum gap: {lesson_set.skill} Band "
        f"{lesson_set.grade}.{lesson_set.stage}"
    )
    missing = ", ".join(f"{value:04d}" for value in missing_sequences)
    text = "\n".join(
        [
            "A student reached the frontier of an incomplete reading set.",
            "",
            f"Category: {lesson_set.skill}",
            f"Band: {lesson_set.grade}.{lesson_set.stage}",
            f"Current lesson: {current_lesson_id}",
            f"Missing sequences: {missing}",
            f"Hold ID: {hold_id}",
            "",
            "The student is held at this lesson. Add the missing lessons, mark the set",
            "complete in curriculum.json, and redeploy. The signed hold link will then",
            "re-evaluate and continue progression.",
        ]
    )
    return subject, text


def send_gap_alert(
    settings: AlertSettings,
    subject: str,
    text: str,
) -> bool:
    if not settings.configured:
        return False
    payload = {
        "from": settings.sender,
        "to": [settings.recipient],
        "subject": subject,
        "text": text,
    }
    if settings.reply_to:
        payload["reply_to"] = [settings.reply_to]
    request = Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "reading-harness-server",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            return 200 <= response.status < 300
    except (HTTPError, URLError, TimeoutError):
        return False
