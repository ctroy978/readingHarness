from __future__ import annotations

import json

import pytest

from server.catalog import build_catalog
from server.progression import ProgressionError, load_curriculum, select_next_lesson
from server.tickets import Completion


SKILL = "inference"


def _lesson(grade: int, stage: int, sequence: int):
    return {
        "schema_version": 3,
        "document_type": "reading_lesson",
        "lesson_id": f"{SKILL}_band_{grade}_{stage}_{sequence:04d}",
        "meta": {"status": "published", "skill": SKILL},
        "passage": {},
        "items": [{"id": "q1"}],
        "rules": {
            "server_routes": {
                "advance": {
                    "next_file": f"{SKILL}_band_{grade}_{stage}_{sequence:04d}",
                    "next_irl": grade,
                },
                "retry": {
                    "next_file": f"{SKILL}_band_{grade}_{stage}_{sequence:04d}",
                    "next_irl": grade,
                },
                "stopped": {
                    "next_file": f"{SKILL}_band_{grade}_{stage}_{sequence:04d}",
                    "next_irl": grade,
                },
            }
        },
    }


def _files(sets):
    files = {}
    rows = []
    for grade, stage, count, status in sets:
        sequences = list(range(1, count + 1))
        rows.append(
            {
                "skill": SKILL,
                "grade": grade,
                "stage": stage,
                "expected_sequences": sequences,
                "status": status,
            }
        )
        for sequence in sequences:
            lesson = _lesson(grade, stage, sequence)
            files[f"{lesson['lesson_id']}.json"] = json.dumps(lesson).encode()
    files["curriculum.json"] = json.dumps(
        {"version": 1, "target_set_size": 8, "sets": rows}
    ).encode()
    return files


def _catalog(files):
    lessons, _, _ = build_catalog(files, {SKILL})
    curriculum = load_curriculum(files, lessons, {SKILL})
    return lessons, curriculum


def _completion(lessons, grade, stage, sequence, got_there, stopped=False):
    lesson = lessons[f"{SKILL}_band_{grade}_{stage}_{sequence:04d}"]
    return Completion(lesson, done=5, got_there=got_there, stopped=stopped)


SETTINGS = {
    "on_incomplete_set": "authored_route",
    "on_ceiling": "hold",
    "on_floor": "restart_set",
}

HOLD_SETTINGS = {
    "on_incomplete_set": "hold_and_alert",
    "on_ceiling": "hold",
    "on_floor": "restart_set",
}


def test_mastery_moves_to_start_of_next_set():
    files = _files([(5, 1, 8, "complete"), (5, 2, 8, "complete")])
    lessons, curriculum = _catalog(files)
    result = select_next_lesson(
        _completion(lessons, 5, 1, 8, got_there=4),
        curriculum,
        lessons,
        SETTINGS,
        advance_min=4,
        fallback_file="missing",
    )
    assert result.lesson is not None
    assert result.stage == 2
    assert result.sequence == 1


@pytest.mark.parametrize("count,expected_middle", [(5, 3), (7, 4), (8, 4)])
def test_last_nonmastery_moves_to_middle_of_previous_variable_set(count, expected_middle):
    files = _files([(5, 1, count, "complete"), (5, 2, 8, "complete")])
    lessons, curriculum = _catalog(files)
    result = select_next_lesson(
        _completion(lessons, 5, 2, 8, got_there=0),
        curriculum,
        lessons,
        SETTINGS,
        advance_min=4,
        fallback_file="missing",
    )
    assert result.lesson is not None
    assert result.stage == 1
    assert result.sequence == expected_middle


def test_nonmastery_before_last_stays_in_set():
    files = _files([(5, 1, 4, "complete")])
    lessons, curriculum = _catalog(files)
    result = select_next_lesson(
        _completion(lessons, 5, 1, 2, got_there=0),
        curriculum,
        lessons,
        SETTINGS,
        advance_min=4,
        fallback_file="missing",
    )
    assert result.lesson is not None
    assert result.sequence == 3


def test_last_nonmastery_at_floor_restarts_set():
    files = _files([(5, 1, 4, "complete")])
    lessons, curriculum = _catalog(files)
    result = select_next_lesson(
        _completion(lessons, 5, 1, 4, got_there=0),
        curriculum,
        lessons,
        SETTINGS,
        advance_min=4,
        fallback_file="missing",
    )
    assert result.lesson is not None
    assert result.sequence == 1


def test_stopped_session_resumes_current_lesson():
    files = _files([(5, 1, 8, "complete"), (5, 2, 8, "complete")])
    lessons, curriculum = _catalog(files)
    completion = _completion(lessons, 5, 1, 8, got_there=0, stopped=True)
    result = select_next_lesson(
        completion,
        curriculum,
        lessons,
        SETTINGS,
        advance_min=4,
        fallback_file="missing",
    )
    assert result.lesson == completion.lesson


def test_incomplete_set_uses_authored_fallback_route():
    files = _files([(5, 1, 2, "incomplete")])
    lessons, curriculum = _catalog(files)
    fallback = lessons["inference_band_5_1_0002"]
    result = select_next_lesson(
        _completion(lessons, 5, 1, 2, got_there=0),
        curriculum,
        lessons,
        SETTINGS,
        advance_min=4,
        fallback_file=fallback.id,
    )
    assert result.lesson == fallback


def test_short_set_holds_at_frontier():
    files = _files([(5, 1, 2, "incomplete")])
    lessons, curriculum = _catalog(files)
    result = select_next_lesson(
        _completion(lessons, 5, 1, 2, got_there=4),
        curriculum,
        lessons,
        HOLD_SETTINGS,
        advance_min=4,
        fallback_file="missing",
    )
    assert result.held
    assert result.hold_reason == "current_set_frontier"
    assert result.hold_set is not None
    assert result.hold_set.expected_sequences == (1, 2)


def test_short_set_does_not_skip_missing_lessons_after_mastery():
    files = _files([(5, 1, 2, "incomplete"), (5, 2, 8, "complete")])
    lessons, curriculum = _catalog(files)
    result = select_next_lesson(
        _completion(lessons, 5, 1, 1, got_there=4),
        curriculum,
        lessons,
        HOLD_SETTINGS,
        advance_min=4,
        fallback_file="missing",
    )
    assert result.lesson is not None
    assert result.sequence == 2


def test_short_set_holds_at_last_lesson_even_when_mastered():
    files = _files([(5, 1, 2, "incomplete"), (5, 2, 8, "complete")])
    lessons, curriculum = _catalog(files)
    result = select_next_lesson(
        _completion(lessons, 5, 1, 2, got_there=4),
        curriculum,
        lessons,
        HOLD_SETTINGS,
        advance_min=4,
        fallback_file="missing",
    )
    assert result.held
    assert result.hold_reason == "current_set_frontier"


def test_complete_set_rejects_missing_lesson():
    files = _files([(5, 1, 3, "complete")])
    lessons, _, _ = build_catalog(files, {SKILL})
    raw = json.loads(files["curriculum.json"])
    raw["sets"][0]["expected_sequences"] = [1, 2, 3, 4, 5, 6, 7, 8]
    files["curriculum.json"] = json.dumps(raw).encode()
    with pytest.raises(ProgressionError, match="Complete set is missing"):
        load_curriculum(files, lessons, {SKILL})
