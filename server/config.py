from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from server.catalog import Category, Lesson, build_catalog
from server.github import fetch_lesson_files, normalize_repository

CATEGORY_KEY = re.compile(r"^[a-z][a-z0-9_-]*$")
STAR_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


@dataclass(frozen=True)
class StarRow:
    key: str
    label: str
    entries: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Config:
    secret: bytes
    public_base_url: str
    display_timezone: ZoneInfo
    categories: tuple[Category, ...]
    lessons: MappingProxyType[str, Lesson]
    lesson_bytes: MappingProxyType[str, bytes]
    entries: MappingProxyType[tuple[str, int], tuple[Lesson, ...]]
    star_rows: tuple[StarRow, ...]

    def entry(self, skill: str, level: int) -> Lesson | None:
        lessons = self.entries.get((skill, level), ())
        return lessons[0] if lessons else None

    def lesson(self, lesson_id: str) -> Lesson | None:
        return self.lessons.get(lesson_id)

    def star_row(self, key: str) -> StarRow | None:
        return next((row for row in self.star_rows if row.key == key), None)

    def star_entry(self, star: str, skill: str) -> Lesson | None:
        row = self.star_row(star)
        lesson_id = dict(row.entries).get(skill) if row is not None else None
        return self.lesson(lesson_id) if lesson_id is not None else None


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid configuration file: {path}") from exc


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise RuntimeError(f"Invalid .env line for {key or 'unknown key'}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _local_lesson_files(lesson_dir: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in lesson_dir.iterdir():
        if path.is_file() and path.suffix == ".json":
            files[path.name] = path.read_bytes()
    return files


def _public_base_url() -> str:
    configured = os.environ.get("PUBLIC_BASE_URL", "").strip()
    return (configured or os.environ.get("RENDER_EXTERNAL_URL", "").strip()).rstrip("/")


def _lesson_files() -> dict[str, bytes]:
    repository = os.environ.get("GITHUB_REPO", "").strip()
    if repository:
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        if not token:
            raise RuntimeError("GITHUB_TOKEN is required when GITHUB_REPO is set")
        return fetch_lesson_files(
            normalize_repository(repository),
            token,
            os.environ.get("GITHUB_REF", "main").strip() or "main",
            os.environ.get("GITHUB_LESSON_PATH", "").strip(),
        )
    lesson_dir = Path(os.environ.get("LESSON_DIR", "")).expanduser()
    if not str(lesson_dir) or not lesson_dir.is_dir():
        raise RuntimeError("Set GITHUB_REPO or LESSON_DIR to a lesson source")
    return _local_lesson_files(lesson_dir.resolve())


def _load_categories(config_dir: Path) -> tuple[Category, ...]:
    raw = _read_json(config_dir / "categories.json")
    if not isinstance(raw, dict) or not isinstance(raw.get("categories"), list):
        raise RuntimeError("categories.json must contain a categories list")

    categories: list[Category] = []
    keys: set[str] = set()
    orders: set[int] = set()
    for item in raw["categories"]:
        if not isinstance(item, dict):
            raise RuntimeError("Each category must be an object")
        key, label, order = item.get("key"), item.get("label"), item.get("order")
        if not isinstance(key, str) or not CATEGORY_KEY.fullmatch(key):
            raise RuntimeError(f"Invalid category key: {key!r}")
        if not isinstance(label, str) or not label.strip():
            raise RuntimeError(f"Category {key!r} needs a label")
        if not isinstance(order, int) or order < 0:
            raise RuntimeError(f"Category {key!r} needs a non-negative integer order")
        if key in keys or order in orders:
            raise RuntimeError("Category keys and display orders must be unique")
        keys.add(key)
        orders.add(order)
        categories.append(Category(key=key, label=label.strip(), order=order))
    if not categories:
        raise RuntimeError("At least one category is required")
    return tuple(sorted(categories, key=lambda category: category.order))


def _load_star_rows(config_dir: Path, categories: tuple[Category, ...]) -> tuple[StarRow, ...]:
    raw = _read_json(config_dir / "onboarding.json")
    if not isinstance(raw, dict) or not isinstance(raw.get("star_rows"), list):
        raise RuntimeError("onboarding.json must contain a star_rows list")
    allowed = {category.key for category in categories}
    rows: list[StarRow] = []
    for item in raw["star_rows"]:
        if not isinstance(item, dict):
            raise RuntimeError("Each Star row must be an object")
        key = item.get("key")
        label = item.get("label")
        entries = item.get("entries")
        if not isinstance(key, str) or not STAR_KEY.fullmatch(key):
            raise RuntimeError("Each Star row needs a stable key")
        if not isinstance(label, str) or not label.strip():
            raise RuntimeError("Each Star row needs a label")
        if not isinstance(entries, dict):
            raise RuntimeError(f"Star row {label!r} needs an entries object")
        if any(row.key == key for row in rows):
            raise RuntimeError("Star row keys must be unique")
        parsed: list[tuple[str, str]] = []
        for skill, lesson_id in entries.items():
            if skill not in allowed:
                raise RuntimeError(f"Unknown category in Star table: {skill!r}")
            if not isinstance(lesson_id, str) or not lesson_id:
                raise RuntimeError(f"Invalid entry lesson for {skill!r} in Star row {label!r}")
            parsed.append((skill, lesson_id))
        rows.append(StarRow(key=key, label=label.strip(), entries=tuple(parsed)))
    if not rows:
        raise RuntimeError("At least one Star row is required")
    return tuple(rows)


def load_config() -> Config:
    load_dotenv()
    config_dir = Path(os.environ.get("CONFIG_DIR", Path(__file__).parent / "config"))
    secret_text = os.environ.get("TICKET_SECRET", "")
    if len(secret_text.encode("utf-8")) < 32:
        raise RuntimeError("TICKET_SECRET must be set to at least 32 bytes")

    base_url = _public_base_url() or "http://127.0.0.1:8000"
    parsed_base = urlparse(base_url)
    if parsed_base.scheme not in {"http", "https"} or not parsed_base.netloc:
        raise RuntimeError("PUBLIC_BASE_URL must be an absolute http or https URL")

    try:
        display_timezone = ZoneInfo(os.environ.get("DISPLAY_TIMEZONE", "UTC"))
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError("DISPLAY_TIMEZONE is not a known timezone") from exc

    categories = _load_categories(config_dir)
    lesson_files = _lesson_files()
    lessons, entries = build_catalog(lesson_files, {category.key for category in categories})
    star_rows = _load_star_rows(config_dir, categories)
    for row in star_rows:
        for skill, lesson_id in row.entries:
            lesson = lessons.get(lesson_id)
            if lesson is None:
                raise RuntimeError(
                    f"Star row {row.label!r} points to missing lesson {lesson_id!r}"
                )
            if lesson.skill != skill:
                raise RuntimeError(
                    f"Star row {row.label!r} maps {skill!r} to {lesson_id!r}, "
                    f"which belongs to {lesson.skill!r}"
                )

    return Config(
        secret=secret_text.encode("utf-8"),
        public_base_url=base_url,
        display_timezone=display_timezone,
        categories=categories,
        lessons=MappingProxyType(lessons),
        lesson_bytes=MappingProxyType(lesson_files),
        entries=MappingProxyType(entries),
        star_rows=star_rows,
    )
