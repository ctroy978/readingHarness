from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

from server.catalog import LESSON_NAME


class LessonBuildError(RuntimeError):
    pass


def load_protocol(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LessonBuildError(f"Invalid protocol file: {path}") from exc
    if not isinstance(raw, dict) or raw.get("version") != "1.0":
        raise LessonBuildError("protocol.json must use version 1.0")
    for key in (
        "routing",
        "results",
        "skill_instructions",
        "scripts",
        "tokens",
        "instructions",
    ):
        if key not in raw:
            raise LessonBuildError(f"protocol.json is missing {key!r}")
    if not isinstance(raw["instructions"], list) or not all(
        isinstance(line, str) and line for line in raw["instructions"]
    ):
        raise LessonBuildError("protocol.json instructions must be non-empty strings")
    return raw


def _require_string(source: Mapping[str, Any], key: str) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value.strip():
        raise LessonBuildError(f"Lesson source needs a non-empty {key!r}")
    return value


def _route(
    source: Mapping[str, Any], destination: str, kind: str
) -> dict[str, Any]:
    value = source.get(destination)
    if not isinstance(value, dict):
        raise LessonBuildError(f"Lesson source needs a {destination!r} route")
    next_file = _require_string(value, "next_file")
    next_irl = value.get("next_irl")
    if not isinstance(next_irl, int) or next_irl < 1:
        raise LessonBuildError(f"{destination!r} route needs a positive next_irl")
    next_means = _require_string(value, "next_means")
    student_line = value.get("student_line")
    if kind == "stopped":
        if student_line is not None and not isinstance(student_line, str):
            raise LessonBuildError("stopped student_line must be a string")
    else:
        if not isinstance(student_line, str) or not student_line.strip():
            raise LessonBuildError(f"{destination!r} route needs a student_line")
    return {
        "next_file": next_file,
        "next_irl": next_irl,
        "next_means": next_means,
        "student_line": student_line,
    }


def compile_lesson(source: Mapping[str, Any], protocol: Mapping[str, Any]) -> dict[str, Any]:
    if source.get("schema_version") != 3 or source.get("document_type") != "lesson_source":
        raise LessonBuildError("Lesson source must use schema_version 3 and lesson_source")
    lesson_id = _require_string(source, "lesson_id")
    match = LESSON_NAME.fullmatch(f"{lesson_id}.json")
    if match is None:
        raise LessonBuildError(
            "lesson_id must use <skill>_band_<grade>_<stage>_<sequence>"
        )
    meta = source.get("meta")
    if not isinstance(meta, dict):
        raise LessonBuildError("Lesson source needs meta")
    skill = match["skill"]
    irl_grade = int(match["irl"])
    stage = int(match["stage"])
    sequence = int(match["sequence"])
    if meta.get("skill") != skill:
        raise LessonBuildError("Lesson meta.skill must match the filename")
    reader = meta.get("reader")
    if not isinstance(reader, dict) or reader.get("reading_level") != irl_grade:
        raise LessonBuildError("Lesson reader.reading_level must equal the IRL grade")
    items = source.get("items")
    if not isinstance(items, list) or not items:
        raise LessonBuildError("Lesson source needs at least one item")
    item_ids = [item.get("id") for item in items if isinstance(item, dict)]
    if len(item_ids) != len(items) or any(not isinstance(value, str) for value in item_ids):
        raise LessonBuildError("Every item needs a string id")
    if len(set(item_ids)) != len(item_ids):
        raise LessonBuildError("Item ids must be unique")

    routes = _route(source, "advance", "route")
    retry = _route(source, "retry", "route")
    stopped = _route(source, "stopped", "stopped")
    runtime_meta = copy.deepcopy(meta)
    runtime_meta.pop("reader", None)
    runtime_meta.pop("irl_grade", None)
    runtime_meta.pop("stage", None)
    runtime_meta.pop("sequence", None)
    result: dict[str, Any] = {
        "schema_version": 3,
        "document_type": "reading_lesson",
        "lesson_id": lesson_id,
        "meta": runtime_meta,
    }
    result["meta"]["protocol_version"] = protocol["version"]
    for key in ("passage", "items"):
        if key not in source:
            raise LessonBuildError(f"Lesson source is missing {key!r}")
        result[key] = copy.deepcopy(source[key])

    scripts = copy.deepcopy(protocol["scripts"])
    overrides = source.get("script_overrides", {})
    if not isinstance(overrides, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in overrides.items()
    ):
        raise LessonBuildError("script_overrides must map names to strings")
    allowed_overrides = {
        "near_copy_2",
    }
    unknown = set(overrides) - set(scripts) - allowed_overrides
    if unknown:
        raise LessonBuildError(f"Unknown script overrides: {sorted(unknown)}")
    scripts.update(overrides)
    result["script"] = scripts

    instructions = [
        line.replace("{last_item}", item_ids[-1]) for line in protocol["instructions"]
    ]
    skill = meta.get("skill")
    if not isinstance(skill, str) or not skill:
        raise LessonBuildError("Lesson meta needs a skill")
    skill_instructions = protocol["skill_instructions"].get(skill, [])
    if not isinstance(skill_instructions, list) or not all(
        isinstance(line, str) and line for line in skill_instructions
    ):
        raise LessonBuildError(f"Invalid skill instructions for {skill!r}")
    additions = source.get("instruction_additions", [])
    if not isinstance(additions, list) or not all(
        isinstance(line, str) and line for line in additions
    ):
        raise LessonBuildError("instruction_additions must be non-empty strings")
    result["tutoring_instructions"] = instructions + skill_instructions + additions

    tokens = protocol["tokens"]
    result["rules"] = {
        "got_there_means": "unaided plus helped. Mercy does not count as got_there.",
        "results": copy.deepcopy(protocol["results"]),
        "server_routes": {
            "advance": routes,
            "retry": retry,
            "stopped": stopped,
        },
        "token_done": tokens["done"],
        "token_stopped": tokens["stopped"],
    }
    return result


def dump_lesson(lesson: Mapping[str, Any]) -> bytes:
    return (json.dumps(lesson, indent=2, ensure_ascii=True) + "\n").encode("utf-8")
