from __future__ import annotations

import json

import pytest

from server.catalog import build_catalog
from server.progression import Carry, load_curriculum, select_next_lesson
from server.tickets import Completion, TicketError, parse_completion_line


SKILL = "inference"

WINDOW = {"size": 3, "clears_to_move": 2, "fails_to_move": 2, "fail_max_got_there": 3}

SETTINGS = {
    "on_incomplete_set": "hold_and_alert",
    "on_ceiling": "hold",
    "on_floor": "restart_set",
}


def _lesson(grade: int, stage: int, sequence: int):
    lesson_id = f"{SKILL}_band_{grade}_{stage}_{sequence:04d}"
    return {
        "schema_version": 3,
        "document_type": "reading_lesson",
        "lesson_id": lesson_id,
        "meta": {"status": "published", "skill": SKILL},
        "passage": {},
        "items": [{"id": f"q{n}"} for n in range(1, 6)],
        "rules": {
            "server_routes": {
                name: {"next_file": lesson_id, "next_irl": grade}
                for name in ("advance", "retry", "stopped")
            }
        },
    }


def _catalog(sets):
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
        {"version": 1, "target_set_size": 6, "sets": rows}
    ).encode()
    lessons, _, _ = build_catalog(files, {SKILL})
    return lessons, load_curriculum(files, lessons, {SKILL})


def _result(lessons, curriculum, grade, stage, sequence, unaided, helped=0,
            anchor_failed=False, carry=""):
    lesson = lessons[f"{SKILL}_band_{grade}_{stage}_{sequence:04d}"]
    completion = Completion(
        lesson,
        done=5,
        got_there=unaided + helped,
        stopped=False,
        unaided=unaided,
        helped=helped,
        mercy=0,
        open=5 - unaided - helped,
        anchor_failed=anchor_failed,
        carry=carry,
    )
    return select_next_lesson(
        completion,
        curriculum,
        lessons,
        SETTINGS,
        advance_min=4,
        fallback_file="missing",
        window=WINDOW,
        carry=carry,
    )


def test_one_clear_is_not_a_move():
    lessons, curriculum = _catalog([(3, 1, 6, "complete"), (4, 1, 6, "complete")])
    result = _result(lessons, curriculum, 3, 1, 1, unaided=5)
    assert result.sequence == 2
    assert result.stage == 1
    assert result.carry == "1.1.1.0"


def test_two_clears_in_three_moves_up():
    lessons, curriculum = _catalog([(3, 1, 6, "complete"), (4, 1, 6, "complete")])
    result = _result(lessons, curriculum, 3, 1, 2, unaided=4, carry="1.1.1.0")
    assert result.grade == 4
    assert result.stage == 1
    assert result.sequence == 1
    assert result.carry == "1.0..0"


def test_middle_results_keep_the_reader_in_the_rung():
    lessons, curriculum = _catalog([(3, 1, 6, "complete"), (4, 1, 6, "complete")])
    # 3 unaided plus 1 helped is not a clear and not a hard fail.
    result = _result(lessons, curriculum, 3, 1, 3, unaided=3, helped=1, carry="1.2.1m.0")
    assert result.sequence == 4
    assert result.stage == 1


def test_two_hard_fails_move_down_to_the_middle_of_the_previous_rung():
    lessons, curriculum = _catalog(
        [(3, 1, 6, "complete"), (4, 1, 6, "complete"), (5, 1, 6, "complete")]
    )
    result = _result(lessons, curriculum, 4, 1, 2, unaided=1, helped=1, carry="1.1.0.0")
    assert result.grade == 3
    assert result.sequence == 3
    assert result.carry == "1.0..0"


def test_a_second_down_move_holds_instead_of_moving_again():
    lessons, curriculum = _catalog(
        [(3, 1, 6, "complete"), (4, 1, 6, "complete"), (5, 1, 6, "complete")]
    )
    result = _result(lessons, curriculum, 4, 1, 3, unaided=0, helped=0, carry="1.2.00.1")
    assert result.held
    assert result.hold_reason == "two_down_moves"


def test_never_moves_into_an_incomplete_set():
    lessons, curriculum = _catalog([(3, 1, 6, "complete"), (7, 1, 6, "incomplete")])
    result = _result(lessons, curriculum, 3, 1, 2, unaided=5, carry="1.1.1.0")
    assert result.held
    assert result.hold_reason == "next_set_incomplete"


def test_floor_push_back_uses_a_sequence_that_has_not_been_shown():
    lessons, curriculum = _catalog([(3, 1, 6, "complete")])
    result = _result(lessons, curriculum, 3, 1, 4, unaided=0, helped=0, carry="1.3.00.0")
    assert result.sequence == 5
    assert result.carry == "1.4.000.1"


def test_exhausted_floor_holds_for_a_teacher():
    lessons, curriculum = _catalog([(3, 1, 6, "complete")])
    result = _result(lessons, curriculum, 3, 1, 6, unaided=0, helped=0, carry="1.6.00m.0")
    assert result.held
    assert result.hold_reason == "rung_exhausted"


def test_anchor_failure_blocks_a_clear():
    lessons, curriculum = _catalog([(3, 1, 6, "complete"), (4, 1, 6, "complete")])
    result = _result(lessons, curriculum, 3, 1, 2, unaided=5, anchor_failed=True,
                     carry="1.1.1.0")
    assert result.stage == 1
    assert result.sequence == 3


def test_carry_round_trip():
    carry = Carry.parse("2.3.1m0.1")
    assert (carry.stage, carry.n, carry.hist, carry.downs) == (2, 3, "1m0", 1)
    assert carry.text() == "2.3.1m0.1"
    assert Carry.parse("garbage") == Carry()
    assert Carry.parse("") == Carry()


def _lookup_and_policies():
    from server.catalog import Lesson

    lesson = Lesson(
        skill=SKILL,
        irl=3,
        stage=1,
        sequence=1,
        filename="inference_band_3_1_0001.json",
    )
    policy = type("P", (), {"item_count": 5})()
    return lesson, policy


def test_v2_line_parses_the_result_split():
    lesson, policy = _lookup_and_policies()

    def lookup(_lesson_id):
        return lesson

    completion = parse_completion_line(
        "v2|from=inference_band_3_1_0001|done=5|u=3|h=1|m=1|o=0|an=1|carry=1.1.1.0",
        lookup,
        {lesson.id: policy},
    )
    assert (completion.unaided, completion.helped, completion.mercy, completion.open) == (3, 1, 1, 0)
    assert completion.got_there == 4
    assert completion.carry == "1.1.1.0"


def test_v2_line_rejects_counts_that_do_not_add_up():
    lesson, policy = _lookup_and_policies()

    def lookup(_lesson_id):
        return lesson

    with pytest.raises(TicketError):
        parse_completion_line(
            "v2|from=inference_band_3_1_0001|done=5|u=4|h=0|carry=",
            lookup,
            {lesson.id: policy},
        )


def test_v2_line_rejects_a_malformed_carry():
    lesson, policy = _lookup_and_policies()

    def lookup(_lesson_id):
        return lesson

    with pytest.raises(TicketError):
        parse_completion_line(
            "v2|from=inference_band_3_1_0001|done=5|u=5|h=0|carry=not-a-carry",
            lookup,
            {lesson.id: policy},
        )


def test_a_legacy_line_without_a_result_split_is_neither_clear_nor_fail():
    lessons, curriculum = _catalog([(3, 1, 6, "complete"), (4, 1, 6, "complete")])
    lesson = lessons[f"{SKILL}_band_4_1_0002"]
    # A v1 line records done and got_there only, so u + h + m + o != done.
    completion = Completion(
        lesson, done=5, got_there=4, stopped=False, carry="1.1.m0.0"
    )
    result = select_next_lesson(
        completion,
        curriculum,
        lessons,
        SETTINGS,
        advance_min=4,
        fallback_file="missing",
        window=WINDOW,
        carry="1.1.m0.0",
    )
    # One earlier fail plus this line stays one fail: no step down, and the
    # lesson is recorded as m rather than 0.
    assert result.stage == 1
    assert result.sequence == 3
    assert result.carry == "1.2.m0m.0"
