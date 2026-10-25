from __future__ import annotations

import json

import pytest

from server.alerts import AlertSettings, format_gap_alert, send_gap_alert
from server.progression import LessonSet


def test_gap_alert_identifies_missing_lessons():
    lesson_set = LessonSet("inference", 5, 1, (1, 2), "incomplete")
    subject, text = format_gap_alert(
        lesson_set,
        "inference_band_5_1_0002",
        (),
        "hold123",
    )
    assert subject == "Curriculum gap: inference Band 5.1"
    assert "Current lesson: inference_band_5_1_0002" in text
    assert "Hold ID: hold123" in text


def test_resend_request_uses_configured_recipient(monkeypatch: pytest.MonkeyPatch):
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        captured["auth"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("server.alerts.urlopen", fake_urlopen)
    settings = AlertSettings(
        api_key="re_test",
        sender="Krewone Tutorials <tutorials@krewnine.umaread.org>",
        recipient="ctroy978@gmail.com",
        reply_to="ctroy978@gmail.com",
    )
    assert send_gap_alert(settings, "Curriculum gap", "Missing lesson") is True
    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["auth"] == "Bearer re_test"
    assert captured["body"]["to"] == ["ctroy978@gmail.com"]
    assert captured["body"]["reply_to"] == ["ctroy978@gmail.com"]
