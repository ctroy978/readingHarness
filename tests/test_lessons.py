from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.lessons import LessonBuildError, compile_lesson, load_protocol


@pytest.fixture
def protocol():
    return load_protocol(Path("server/config/protocol.json"))


@pytest.fixture
def source():
    return {
        "schema_version": 3,
        "document_type": "lesson_source",
        "lesson_id": "inference_band_5_1_0002",
        "meta": {
            "status": "published",
            "title": "Example",
            "skill": "inference",
            "irl_grade": 5,
            "stage": 1,
            "sequence": 2,
            "reader": {"reading_level": 5},
        },
        "script_overrides": {
            "near_copy_2": "Start with the shelf. One sentence is enough."
        },
        "instruction_additions": ["Keep this lesson's evidence in view."],
        "passage": {"part_1": "Example passage."},
        "items": [{"id": "q1"}, {"id": "q2"}],
        "advance": {
            "next_file": "inference_band_6_1_0001",
            "next_irl": 6,
            "next_means": "Higher band.",
            "student_line": "Advance.",
        },
        "retry": {
            "next_file": "inference_band_5_1_0003",
            "next_irl": 5,
            "next_means": "Same band.",
            "student_line": "Retry.",
        },
        "stopped": {
            "next_file": "inference_band_5_1_0002",
            "next_irl": 5,
            "next_means": "Resume.",
        },
    }


def test_compiler_uses_shared_protocol_and_lesson_overrides(protocol, source):
    lesson = compile_lesson(source, protocol)
    assert lesson["schema_version"] == 3
    assert lesson["script"]["ready"] == protocol["scripts"]["ready"]
    assert lesson["script"]["near_copy_2"].startswith("Start with the shelf")
    assert lesson["tutoring_instructions"][-1] == source["instruction_additions"][0]
    assert "q2" in "\n".join(lesson["tutoring_instructions"])
    assert lesson["rules"]["token_done"].startswith("v2|from={lesson_id}")
    assert "{carry}" in lesson["rules"]["token_done"]
    assert lesson["rules"]["server_routes"]["advance"]["next_file"] == "inference_band_6_1_0001"
    assert lesson["meta"].get("reader") is None


def test_compiler_rejects_metadata_that_disagrees_with_filename(protocol, source):
    source["meta"]["reader"]["reading_level"] = 6
    with pytest.raises(LessonBuildError, match="reading_level"):
        compile_lesson(source, protocol)


def test_compiler_rejects_unknown_script_override(protocol, source):
    source["script_overrides"] = {"not_a_script": "value"}
    with pytest.raises(LessonBuildError, match="Unknown script"):
        compile_lesson(source, protocol)


def test_protocol_has_single_source_for_common_instructions(protocol):
    instructions = protocol["instructions"]
    assert len(instructions) == len(set(instructions))
    done = protocol["tokens"]["done"]
    assert done.startswith("v2|from={lesson_id}|done={done}")
    for field in ("{unaided}", "{helped}", "{mercy}", "{open}", "{anchor}", "{carry}"):
        assert field in done
    routing = protocol["routing"]
    assert routing["advance_min_got_there"] == 4
    window = routing["window"]
    assert window["clears_to_move"] == 2
    assert window["fails_to_move"] == 2
    assert 1 <= window["size"] <= 3
