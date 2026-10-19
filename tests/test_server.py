from __future__ import annotations

import asyncio
import io
import json
import re
import tarfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from urllib.parse import unquote, urlsplit

import pytest

from server.app import create_app
from server.catalog import Lesson, build_catalog
from server.config import _public_base_url, load_config
from server.github import normalize_repository, parse_lesson_files
from server.tickets import make_ticket


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path / "config"
    lesson_dir = tmp_path / "lessons"
    config_dir.mkdir()
    lesson_dir.mkdir()
    (config_dir / "categories.json").write_text(
        json.dumps(
            {
                "categories": [
                    {"key": "inference", "label": "Inference", "order": 20},
                    {"key": "vocab", "label": "Vocabulary", "order": 10},
                ]
            }
        )
    )
    (config_dir / "onboarding.json").write_text(
        json.dumps(
            {
                "star_rows": [
                    {
                        "key": "300-499",
                        "label": "300-499",
                        "entries": {
                            "vocab": "vocab_L1_0001",
                            "inference": "inference_L1_0010",
                        },
                    },
                    {
                        "key": "500-plus",
                        "label": "500+",
                        "entries": {"inference": "inference_L2_0010"},
                    },
                ]
            }
        )
    )
    lesson_ids = ["vocab_L1_0001"] + [
        f"inference_L{level}_{sequence:04d}"
        for level in (1, 2)
        for sequence in range(1, 12)
    ]
    for name in [f"{lesson_id}.json" for lesson_id in lesson_ids]:
        (lesson_dir / name).write_text(
            json.dumps(_lesson_data(name.removesuffix(".json")))
        )
    monkeypatch.setenv("TICKET_SECRET", "x" * 32)
    monkeypatch.delenv("GITHUB_REPO", raising=False)
    monkeypatch.setenv("LESSON_DIR", str(lesson_dir))
    monkeypatch.setenv("CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.test")
    return load_config()


@pytest.fixture
def client(config):
    return AsgiClient(create_app(config))


def _lesson_data(lesson_id: str) -> dict:
    match = re.fullmatch(r"(?P<skill>[a-z][a-z0-9_-]*)_L(?P<level>\d+)_(?P<sequence>\d+)", lesson_id)
    assert match is not None
    level = int(match["level"])
    sequence = int(match["sequence"])
    return {
        "schema_version": 3,
        "document_type": "reading_lesson",
        "lesson_id": lesson_id,
        "meta": {"status": "published", "skill": lesson_id.split("_L")[0]},
        "script": {},
        "passage": {},
        "items": [{"id": "q1"}],
        "rules": {
            "server_routes": {
                "advance": {
                    "next_file": lesson_id,
                    "next_level": level,
                },
                "retry": {
                    "next_file": (
                        "inference_L1_0010"
                        if level == 1 and sequence == 9
                        else lesson_id
                    ),
                    "next_level": level,
                },
                "stopped": {"next_file": lesson_id, "next_level": level},
            }
        },
    }


class Response:
    def __init__(self, status_code: int, headers: dict[str, str], body: bytes):
        self.status_code = status_code
        self.headers = {key.lower(): value for key, value in headers.items()}
        self.body = body

    @property
    def text(self):
        return self.body.decode()

    def json(self):
        return json.loads(self.body)


class AsgiClient:
    def __init__(self, app):
        self.app = app

    def request(self, method: str, target: str, body: bytes = b"", headers=None):
        async def run():
            split = urlsplit(target)
            messages = []
            sent = False

            async def receive():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return {"type": "http.disconnect"}

            async def send(message):
                messages.append(message)

            scope = {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": method,
                "scheme": "http",
                "path": split.path,
                "raw_path": split.path.encode(),
                "query_string": split.query.encode(),
                "root_path": "",
                "headers": [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()],
                "client": ("127.0.0.1", 0),
                "server": ("testserver", 80),
            }
            await self.app(scope, receive, send)
            start = next(message for message in messages if message["type"] == "http.response.start")
            response_body = b"".join(
                message.get("body", b"")
                for message in messages
                if message["type"] == "http.response.body"
            )
            response_headers = {
                key.decode(): value.decode() for key, value in start.get("headers", [])
            }
            return Response(start["status"], response_headers, response_body)

        return asyncio.run(run())

    def get(self, target: str):
        return self.request("GET", target)

    def post(self, target: str, json=None):
        body = __import__("json").dumps(json).encode() if json is not None else b""
        return self.request("POST", target, body, {"content-type": "application/json"})


def test_onboarding_and_ordering(client):
    response = client.get("/")
    assert response.status_code == 200
    assert '<select id="star" name="star"' in response.text
    assert '<select id="skill" name="skill"' in response.text
    assert "Vocabulary" in response.text
    assert "Inference" in response.text
    assert response.text.index("Vocabulary") < response.text.index("Inference")
    assert "Bookmark" in response.text


def test_star_and_type_select_starting_lesson(client):
    response = client.get("/start?star=300-499&skill=inference")
    assert response.status_code == 200
    assert "inference_L1_0010.json" in response.headers["content-disposition"]


def test_star_entry_does_not_depend_on_sequence_order(
    config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    lessons = dict(config.lesson_bytes)
    lessons["inference_L1_0001.json"] = json.dumps(
        {"lesson_id": "inference_L1_0001", "schema_version": 1}
    ).encode()
    lessons["inference_L1_9999.json"] = json.dumps(
        {"lesson_id": "inference_L1_9999", "schema_version": 1}
    ).encode()
    new_lessons, _, new_policies = build_catalog(
        lessons, {"vocab", "inference", "process"}
    )
    replacement = replace(
        config,
        lessons=MappingProxyType(new_lessons),
        policies=MappingProxyType(new_policies),
        lesson_bytes=MappingProxyType(lessons),
    )
    response = AsgiClient(create_app(replacement)).get("/start?star=300-499&skill=inference")
    assert response.status_code == 200
    assert "inference_L1_0010.json" in response.headers["content-disposition"]


def test_unavailable_star_and_type_combination(client):
    response = client.get("/start?star=500-plus&skill=vocab")
    assert response.status_code == 200
    assert "Lesson not available" in response.text
    assert client.get("/start?star=missing&skill=vocab").status_code == 400


def test_start_uses_lowest_numeric_sequence(client):
    response = client.get("/start?skill=inference&level=1")
    assert response.status_code == 400


def test_invalid_start_is_400(client):
    assert client.get("/start?skill=unknown&level=1").status_code == 400
    assert client.get("/start?skill=vocab&level=nope").status_code == 400


def test_server_selects_advance_destination(client):
    response = client.post(
        "/sign", json={"line": "v1|from=inference_L1_0009|done=1|got_there=1"}
    )
    assert response.status_code == 200
    url = response.json()["url"]
    assert "inference_L1_0010" in unquote(url)


def test_server_selects_retry_destination(client):
    response = client.post(
        "/sign", json={"line": "v1|from=inference_L1_0009|done=1|got_there=0"}
    )
    assert response.status_code == 200
    assert "inference_L1_0010" in response.json()["url"]


def test_server_selects_stopped_destination_immediately(client):
    response = client.post(
        "/sign",
        json={"line": "v1|from=inference_L1_0009|done=1|got_there=0|stop=1"},
    )
    assert response.status_code == 200
    url = response.json()["url"]
    assert "inference_L1_0009" in url
    assert client.get(url).headers["content-disposition"].endswith(
        'filename="inference_L1_0009.json"'
    )


def test_completion_line_cannot_name_a_destination(client):
    response = client.post(
        "/sign",
        json={"line": "v1|from=inference_L1_0009|done=1|got_there=1|lesson=inference_L2_0010"},
    )
    assert response.status_code == 400


def test_completion_counts_are_bounded(client):
    for line in (
        "v1|from=inference_L1_0009|done=2|got_there=2",
        "v1|from=inference_L1_0009|done=1|got_there=2",
        "v1|from=inference_L1_0009|done=-1|got_there=0",
    ):
        assert client.post("/sign", json={"line": line}).status_code == 400


def test_signed_link_contains_server_available_time(client):
    response = client.post(
        "/sign", json={"line": "v1|from=inference_L1_0010|done=1|got_there=1"}
    )
    assert response.status_code == 200
    ticket = unquote(urlsplit(response.json()["url"]).query.removeprefix("t="))
    assert "available=" in ticket


def test_sign_and_pacing(client, config):
    line = "v1|from=vocab_L1_0001|done=1|got_there=1"
    response = client.post("/sign", json={"line": line})
    assert response.status_code == 200
    assert response.json()["url"].startswith("https://example.test/next?t=")

    fresh = Lesson("vocab", 1, 1, "vocab_L1_0001.json")
    fresh_url = "/next?t=" + make_ticket(
        fresh,
        config.secret,
        available_at=datetime.now(timezone.utc) + timedelta(days=3),
    )
    wait = client.get(fresh_url)
    assert wait.status_code == 200
    assert "ready yet" in wait.text
    assert "<time datetime=" in wait.text


def test_mature_ticket_is_idempotent(client, config):
    lesson = Lesson("vocab", 1, 1, "vocab_L1_0001.json")
    old_issue = datetime.now(timezone.utc) - timedelta(days=3, seconds=1)
    old = make_ticket(
        lesson,
        config.secret,
        issued_at=old_issue,
        available_at=old_issue,
    )
    url = "/next?t=" + old
    first = client.get(url)
    second = client.get(url)
    assert first.status_code == second.status_code == 200
    assert first.headers["content-disposition"] == second.headers["content-disposition"]


def test_tampered_and_traversal_tickets_are_rejected(client, config):
    lesson = Lesson("vocab", 1, 1, "vocab_L1_0001.json")
    ticket = make_ticket(lesson, config.secret)
    assert client.get("/next?t=" + ticket[:-1] + "0").status_code == 400
    assert client.get("/next?t=bad").status_code == 400


def test_no_cookies_and_health(client):
    for path in ["/", "/finish", "/health", "/start?skill=vocab&level=1", "/next?t=bad"]:
        assert "set-cookie" not in client.get(path).headers
    assert client.get("/health").json() == {"ok": True}


def test_missing_secret_refuses_to_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    lesson_dir = tmp_path / "lessons"
    lesson_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TICKET_SECRET", raising=False)
    monkeypatch.setenv("LESSON_DIR", str(lesson_dir))
    with pytest.raises(RuntimeError, match="TICKET_SECRET"):
        load_config()


def test_github_repository_normalization():
    assert normalize_repository("owner/repo") == "owner/repo"
    assert normalize_repository("https://github.com/owner/repo.git") == "owner/repo"
    with pytest.raises(RuntimeError):
        normalize_repository("not a repository")


def test_render_external_url_supplies_public_base(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://reading-harness.onrender.com/")
    assert _public_base_url() == "https://reading-harness.onrender.com"

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://lessons.example.org/")
    assert _public_base_url() == "https://lessons.example.org"


def test_parse_github_archive_lesson_path():
    archive_buffer = io.BytesIO()
    with tarfile.open(fileobj=archive_buffer, mode="w:gz") as archive:
        for name, content in [
            ("owner-repo-sha/lessons/vocab_L1_0001.json", b'{"lesson_id":"vocab_L1_0001"}'),
            ("owner-repo-sha/README.md", b"ignored"),
            ("owner-repo-sha/other/vocab_L1_0002.json", b"{}"),
        ]:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))

    files = parse_lesson_files(archive_buffer.getvalue(), "lessons")
    assert set(files) == {"vocab_L1_0001.json"}


def test_dotenv_supplies_github_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "categories.json").write_text(
        json.dumps({"categories": [{"key": "vocab", "label": "Vocabulary", "order": 1}]})
    )
    (config_dir / "onboarding.json").write_text(
        json.dumps(
            {
                "star_rows": [
                    {
                        "key": "300",
                        "label": "300",
                        "entries": {"vocab": "vocab_L1_0001"},
                    }
                ]
            }
        )
    )
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        f"TICKET_SECRET={'x' * 32}\nGITHUB_REPO=owner/repo\nGITHUB_TOKEN=test-token\n"
    )
    for key in ["TICKET_SECRET", "GITHUB_REPO", "GITHUB_TOKEN", "LESSON_DIR"]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CONFIG_DIR", str(config_dir))
    monkeypatch.chdir(tmp_path)

    def fake_fetch(repo, token, ref, lesson_path):
        assert (repo, token, ref) == ("owner/repo", "test-token", "main")
        lesson = _lesson_data("vocab_L1_0001")
        lesson["rules"]["server_routes"] = {
            "advance": {"next_file": "vocab_L1_0001", "next_level": 1},
            "retry": {"next_file": "vocab_L1_0001", "next_level": 1},
            "stopped": {"next_file": "vocab_L1_0001", "next_level": 1},
        }
        return {"vocab_L1_0001.json": json.dumps(lesson).encode()}

    monkeypatch.setattr("server.config.fetch_lesson_files", fake_fetch)
    config = load_config()
    assert config.lesson("vocab_L1_0001") is not None
    assert config.lesson_bytes["vocab_L1_0001.json"].startswith(b"{")
