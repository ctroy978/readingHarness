from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

LESSON_NAME = re.compile(
    r"^(?P<skill>[a-z][a-z0-9_-]*)_L(?P<level>\d+)_(?P<sequence>\d+)\.json$"
)


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    order: int


@dataclass(frozen=True)
class Lesson:
    skill: str
    level: int
    sequence: int
    filename: str

    @property
    def id(self) -> str:
        return self.filename.removesuffix(".json")


@dataclass(frozen=True)
class Route:
    next_file: str
    next_level: int


@dataclass(frozen=True)
class ServerRoutes:
    advance: Route
    retry: Route
    stopped: Route


@dataclass(frozen=True)
class LessonPolicy:
    item_count: int
    routes: ServerRoutes


def _route(value: object, filename: str, name: str) -> Route:
    if not isinstance(value, dict):
        raise RuntimeError(f"Lesson {filename} needs rules.server_routes.{name}")
    next_file = value.get("next_file")
    next_level = value.get("next_level")
    if not isinstance(next_file, str) or not next_file:
        raise RuntimeError(f"Lesson {filename} has an invalid {name} destination")
    if not isinstance(next_level, int) or next_level < 1:
        raise RuntimeError(f"Lesson {filename} has an invalid {name} level")
    return Route(next_file=next_file, next_level=next_level)


def _load_policy(data: Mapping[str, object], filename: str) -> LessonPolicy | None:
    if data.get("schema_version") != 3:
        return None
    items = data.get("items")
    rules = data.get("rules")
    if not isinstance(items, list) or not items:
        raise RuntimeError(f"Schema-v3 lesson needs items: {filename}")
    if not isinstance(rules, dict) or not isinstance(rules.get("server_routes"), dict):
        raise RuntimeError(f"Schema-v3 lesson needs rules.server_routes: {filename}")
    server_routes = rules["server_routes"]
    return LessonPolicy(
        item_count=len(items),
        routes=ServerRoutes(
            advance=_route(server_routes.get("advance"), filename, "advance"),
            retry=_route(server_routes.get("retry"), filename, "retry"),
            stopped=_route(server_routes.get("stopped"), filename, "stopped"),
        ),
    )


def build_catalog(
    files: Mapping[str, bytes], allowed_skills: set[str]
) -> tuple[
    dict[str, Lesson],
    dict[tuple[str, int], tuple[Lesson, ...]],
    dict[str, LessonPolicy],
]:
    lessons: dict[str, Lesson] = {}
    groups: dict[tuple[str, int], list[Lesson]] = {}
    policies: dict[str, LessonPolicy] = {}
    for filename, content in files.items():
        match = LESSON_NAME.fullmatch(filename)
        if match is None:
            continue
        skill = match["skill"]
        level = int(match["level"])
        sequence = int(match["sequence"])
        if skill not in allowed_skills:
            raise RuntimeError(f"Lesson uses unknown category {skill!r}: {filename}")
        if level < 1:
            raise RuntimeError(f"Lesson level must be positive: {filename}")
        try:
            data = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Invalid lesson JSON: {filename}") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"Lesson must be a JSON object: {filename}")
        lesson_id = data.get("lesson_id")
        if lesson_id is not None and lesson_id != Path(filename).stem:
            raise RuntimeError(f"lesson_id does not match filename: {filename}")
        status = data.get("meta", {}).get("status") if isinstance(data.get("meta"), dict) else None
        if status == "draft":
            raise RuntimeError(f"Draft lesson cannot be published: {filename}")

        lesson = Lesson(skill=skill, level=level, sequence=sequence, filename=filename)
        if lesson.id in lessons:
            raise RuntimeError(f"Duplicate lesson: {lesson.id}")
        lessons[lesson.id] = lesson
        policy = _load_policy(data, filename)
        if policy is not None:
            policies[lesson.id] = policy
        groups.setdefault((skill, level), []).append(lesson)

    entries = {
        key: tuple(sorted(group, key=lambda lesson: lesson.sequence))
        for key, group in groups.items()
    }
    for lesson_id, policy in policies.items():
        for route in (
            policy.routes.advance,
            policy.routes.retry,
            policy.routes.stopped,
        ):
            destination = lessons.get(route.next_file)
            if destination is None:
                raise RuntimeError(
                    f"Lesson {lesson_id} points to missing lesson {route.next_file!r}"
                )
            if destination.level != route.next_level:
                raise RuntimeError(
                    f"Lesson {lesson_id} route level does not match {route.next_file!r}"
                )
    return lessons, entries, policies
