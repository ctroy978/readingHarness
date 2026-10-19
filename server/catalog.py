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


def build_catalog(
    files: Mapping[str, bytes], allowed_skills: set[str]
) -> tuple[dict[str, Lesson], dict[tuple[str, int], tuple[Lesson, ...]]]:
    lessons: dict[str, Lesson] = {}
    groups: dict[tuple[str, int], list[Lesson]] = {}
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
        groups.setdefault((skill, level), []).append(lesson)

    entries = {
        key: tuple(sorted(group, key=lambda lesson: lesson.sequence))
        for key, group in groups.items()
    }
    return lessons, entries
