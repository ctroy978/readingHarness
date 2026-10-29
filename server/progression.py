from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from server.catalog import Lesson
from server.tickets import Completion


class ProgressionError(ValueError):
    pass


@dataclass(frozen=True)
class Carry:
    """The rung memory a stateless server cannot remember for itself.

    stage  - the authored stage the memory belongs to
    n      - lessons finished in this rung
    hist   - up to three results, oldest first: 1 clear, 0 hard fail, m neither
    downs  - consecutive down-moves, so a third cannot happen
    """

    stage: int = 0
    n: int = 0
    hist: str = ""
    downs: int = 0

    @classmethod
    def parse(cls, text: str | None) -> "Carry":
        if not text:
            return cls()
        parts = text.split(".")
        if len(parts) != 4:
            return cls()
        stage, n, hist, downs = parts
        if not stage.isdigit() or not n.isdigit() or not downs.isdigit():
            return cls()
        if len(hist) > 3 or set(hist) - set("01m"):
            return cls()
        return cls(int(stage), int(n), hist, int(downs))

    def text(self) -> str:
        return f"{self.stage}.{self.n}.{self.hist}.{self.downs}"


@dataclass(frozen=True)
class ProgressionResult:
    lesson: Lesson | None
    hold_reason: str | None = None
    hold_set: "LessonSet | None" = None
    carry: str | None = None

    @property
    def grade(self) -> int | None:
        return self.lesson.irl if self.lesson is not None else None

    @property
    def stage(self) -> int | None:
        return self.lesson.stage if self.lesson is not None else None

    @property
    def sequence(self) -> int | None:
        return self.lesson.sequence if self.lesson is not None else None

    @property
    def held(self) -> bool:
        return self.lesson is None


@dataclass(frozen=True)
class LessonSet:
    skill: str
    grade: int
    stage: int
    expected_sequences: tuple[int, ...]
    status: str

    @property
    def complete(self) -> bool:
        return self.status == "complete"

    @property
    def first_sequence(self) -> int:
        return self.expected_sequences[0]

    @property
    def last_sequence(self) -> int:
        return self.expected_sequences[-1]

    @property
    def middle_sequence(self) -> int:
        return self.expected_sequences[(len(self.expected_sequences) - 1) // 2]


@dataclass(frozen=True)
class Curriculum:
    target_set_size: int
    sets: Mapping[tuple[str, int, int], LessonSet]

    def get(self, skill: str, grade: int, stage: int) -> LessonSet | None:
        return self.sets.get((skill, grade, stage))

    def ordered_for_skill(self, skill: str) -> tuple[LessonSet, ...]:
        return tuple(
            sorted(
                (item for item in self.sets.values() if item.skill == skill),
                key=lambda item: (item.grade, item.stage),
            )
        )


def _lesson_id(skill: str, grade: int, stage: int, sequence: int) -> str:
    return f"{skill}_band_{grade}_{stage}_{sequence:04d}"


def load_curriculum(
    files: Mapping[str, bytes],
    lessons: Mapping[str, Lesson],
    allowed_skills: set[str],
) -> Curriculum:
    content = files.get("curriculum.json")
    if content is None:
        raise ProgressionError("Lesson repository needs curriculum.json")
    try:
        raw = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProgressionError("curriculum.json is not valid JSON") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ProgressionError("curriculum.json must use version 1")
    target = raw.get("target_set_size")
    if not isinstance(target, int) or target < 1:
        raise ProgressionError("curriculum.json needs a positive target_set_size")
    rows = raw.get("sets")
    if not isinstance(rows, list):
        raise ProgressionError("curriculum.json needs a sets list")

    parsed: dict[tuple[str, int, int], LessonSet] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ProgressionError("Each curriculum set must be an object")
        skill = row.get("skill")
        grade = row.get("grade")
        stage = row.get("stage")
        status = row.get("status")
        sequences = row.get("expected_sequences")
        if not isinstance(skill, str) or skill not in allowed_skills:
            raise ProgressionError(f"Unknown curriculum set skill: {skill!r}")
        if not isinstance(grade, int) or not 1 <= grade <= 12:
            raise ProgressionError(f"Invalid curriculum set grade: {grade!r}")
        if not isinstance(stage, int) or not 1 <= stage <= 8:
            raise ProgressionError(f"Invalid curriculum set stage: {stage!r}")
        if status not in {"complete", "incomplete"}:
            raise ProgressionError("Curriculum set status must be complete or incomplete")
        if not isinstance(sequences, list) or not sequences:
            raise ProgressionError("Curriculum set needs expected_sequences")
        if any(not isinstance(value, int) or value < 1 for value in sequences):
            raise ProgressionError("Expected sequences must be positive integers")
        ordered = tuple(sorted(sequences))
        if len(ordered) != len(set(ordered)):
            raise ProgressionError("Expected sequences must be unique")
        key = (skill, grade, stage)
        if key in parsed:
            raise ProgressionError(f"Duplicate curriculum set: {key}")
        parsed[key] = LessonSet(
            skill=skill,
            grade=grade,
            stage=stage,
            expected_sequences=ordered,
            status=status,
        )

    for item in parsed.values():
        actual = {
            lesson.sequence
            for lesson in lessons.values()
            if (lesson.skill, lesson.irl, lesson.stage)
            == (item.skill, item.grade, item.stage)
        }
        if not actual <= set(item.expected_sequences):
            raise ProgressionError("Lesson exists outside its declared expected_sequences")
        if item.complete:
            for sequence in item.expected_sequences:
                lesson = lessons.get(
                    _lesson_id(item.skill, item.grade, item.stage, sequence)
                )
                if lesson is None:
                    raise ProgressionError(
                        f"Complete set is missing {_lesson_id(item.skill, item.grade, item.stage, sequence)}"
                    )
            if actual != set(item.expected_sequences):
                raise ProgressionError("Complete set does not match its runnable lessons")

    return Curriculum(target_set_size=target, sets=parsed)


def _target(lesson_id: str, lessons: Mapping[str, Lesson]) -> Lesson:
    lesson = lessons.get(lesson_id)
    if lesson is None:
        raise ProgressionError(f"Progression target is missing: {lesson_id}")
    return lesson


def _incomplete_action(
    completion: Completion,
    current_set: LessonSet,
    ordered: tuple[LessonSet, ...],
    settings: Mapping[str, Any],
    lessons: Mapping[str, Lesson],
    advance_min: int,
) -> ProgressionResult:
    action = settings["on_incomplete_set"]
    if action == "authored_route":
        raise ProgressionError("authored_route needs a fallback destination")
    if action != "hold_and_alert":
        raise ProgressionError("Incomplete sets are unavailable for progression")

    current = completion.lesson
    actual = sorted(
        lesson.sequence
        for lesson in lessons.values()
        if (lesson.skill, lesson.irl, lesson.stage)
        == (current.skill, current.irl, current.stage)
    )
    if not actual or current.sequence not in actual:
        raise ProgressionError("Current lesson is outside its available set")

    position = actual.index(current.sequence)
    if position + 1 < len(actual):
        return ProgressionResult(
            _target(
                _lesson_id(
                    current.skill,
                    current.irl,
                    current.stage,
                    actual[position + 1],
                ),
                lessons,
            )
        )
    return ProgressionResult(None, "current_set_frontier", current_set)


def _windowed(
    completion: Completion,
    current_set: LessonSet,
    ordered: tuple[LessonSet, ...],
    index: int,
    lessons: Mapping[str, Lesson],
    settings: Mapping[str, Any],
    advance_min: int,
    window: Mapping[str, Any],
    carry_text: str,
) -> ProgressionResult:
    """Decide a move from the last three lessons, not from this one.

    A single lesson at 4 of 5 is a 0.38-0.96 interval, so one clear cannot carry
    a move. The rung holds its own history in the signed continuation.
    """
    carry = Carry.parse(carry_text)
    if carry.stage != current_set.stage:
        carry = Carry(current_set.stage, 0, "", 0)

    fail_at = int(window.get("fail_max_got_there", 3))
    need = int(window.get("clears_to_move", 2))
    fails = int(window.get("fails_to_move", 2))

    hard_fail = completion.unaided + completion.helped <= fail_at or completion.anchor_failed
    clears = completion.unaided >= advance_min and not completion.anchor_failed
    split = completion.unaided + completion.helped + completion.mercy + completion.open
    if split != completion.done:
        # A legacy v1 or v0 line has no result split, so the window has nothing to
        # judge. Count it as neither a clear nor a fail rather than punishing the
        # reader for the server's old format.
        symbol = "m"
    else:
        symbol = "1" if clears else ("0" if hard_fail else "m")

    history = (carry.hist + symbol)[-3:]
    n = carry.n + 1
    state = Carry(current_set.stage, n, history, carry.downs)
    sequences = list(current_set.expected_sequences)

    def target(set_, sequence):
        return _target(
            _lesson_id(set_.skill, set_.grade, set_.stage, sequence), lessons
        )

    def unused_sequence() -> int | None:
        if n < len(sequences):
            return sequences[n]
        return None

    def push_back(reason: str) -> ProgressionResult:
        if settings.get("on_floor") != "restart_set":
            return ProgressionResult(None, reason, current_set, state.text())
        sequence = unused_sequence()
        if sequence is None:
            return ProgressionResult(None, "rung_exhausted", current_set, state.text())
        return ProgressionResult(
            target(current_set, sequence),
            carry=state.text(),
        )

    if history.count("1") >= need:
        if index + 1 >= len(ordered):
            return ProgressionResult(None, "ceiling", current_set, state.text())
        following = ordered[index + 1]
        if not following.complete:
            # Never move into a set that is not finished: the ladder has a gap.
            return ProgressionResult(None, "next_set_incomplete", following, state.text())
        return ProgressionResult(
            target(following, following.first_sequence),
            carry=Carry(following.stage, 0, "", 0).text(),
        )

    if history.count("0") >= fails:
        if state.downs >= 1:
            return ProgressionResult(None, "two_down_moves", current_set, state.text())
        state = Carry(current_set.stage, n, history, state.downs + 1)
        if index > 0:
            previous = ordered[index - 1]
            if not previous.complete:
                return ProgressionResult(None, "previous_set_incomplete", previous, state.text())
            return ProgressionResult(
                target(previous, previous.middle_sequence),
                carry=Carry(previous.stage, 0, "", 0).text(),
            )
        return push_back("floor")

    if completion.lesson.sequence in sequences:
        position = sequences.index(completion.lesson.sequence)
        if position + 1 < len(sequences):
            return ProgressionResult(
                target(current_set, sequences[position + 1]),
                carry=state.text(),
            )
    return push_back("rung_end_stay")


def select_next_lesson(
    completion: Completion,
    curriculum: Curriculum,
    lessons: Mapping[str, Lesson],
    settings: Mapping[str, Any],
    advance_min: int,
    fallback_file: str,
    window: Mapping[str, Any] | None = None,
    carry: str = "",
) -> ProgressionResult:
    if completion.stopped:
        return ProgressionResult(completion.lesson, carry=carry)

    current = completion.lesson
    current_set = curriculum.get(current.skill, current.irl, current.stage)
    if current_set is None:
        raise ProgressionError("Current lesson has no curriculum set")

    mastered = completion.got_there >= advance_min
    ordered = curriculum.ordered_for_skill(current.skill)
    index = ordered.index(current_set)

    if not current_set.complete:
        if settings["on_incomplete_set"] == "authored_route":
            return ProgressionResult(_target(fallback_file, lessons))
        return _incomplete_action(
            completion,
            current_set,
            ordered,
            settings,
            lessons,
            advance_min,
        )

    if window is not None:
        return _windowed(
            completion,
            current_set,
            ordered,
            index,
            lessons,
            settings,
            advance_min,
            window,
            carry,
        )

    if mastered:
        if index + 1 >= len(ordered):
            if settings["on_ceiling"] == "hold":
                return ProgressionResult(current)
            raise ProgressionError("No next curriculum set exists")
        following = ordered[index + 1]
        if not following.complete:
            fallback = settings["on_incomplete_set"]
            if fallback == "authored_route":
                return ProgressionResult(_target(fallback_file, lessons))
            if fallback == "hold_and_alert":
                following_actual = sorted(
                    lesson.sequence
                    for lesson in lessons.values()
                    if (lesson.skill, lesson.irl, lesson.stage)
                    == (following.skill, following.grade, following.stage)
                )
                if following_actual:
                    return ProgressionResult(
                        _target(
                            _lesson_id(
                                following.skill,
                                following.grade,
                                following.stage,
                                following_actual[0],
                            ),
                            lessons,
                        )
                    )
                return ProgressionResult(None, "next_set_missing", following)
            raise ProgressionError("Incomplete sets are unavailable for progression")
        return ProgressionResult(
            _target(
                _lesson_id(
                    following.skill,
                    following.grade,
                    following.stage,
                    following.first_sequence,
                ),
                lessons,
            )
        )

    if current.sequence != current_set.last_sequence:
        sequences = list(current_set.expected_sequences)
        try:
            position = sequences.index(current.sequence)
        except ValueError as exc:
            raise ProgressionError("Current lesson is outside its curriculum set") from exc
        if position + 1 >= len(sequences):
            raise ProgressionError("Current lesson has no following sequence")
        return ProgressionResult(
            _target(
                _lesson_id(
                    current.skill,
                    current.irl,
                    current.stage,
                    sequences[position + 1],
                ),
                lessons,
            )
        )

    if index > 0:
        previous = ordered[index - 1]
        if not previous.complete:
            action = settings["on_incomplete_set"]
            if action == "authored_route":
                return ProgressionResult(_target(fallback_file, lessons))
            if action == "hold_and_alert":
                return ProgressionResult(None, "previous_set_incomplete", previous)
            raise ProgressionError("Incomplete sets are unavailable for progression")
        return ProgressionResult(
            _target(
                _lesson_id(
                    previous.skill,
                    previous.grade,
                    previous.stage,
                    previous.middle_sequence,
                ),
                lessons,
            )
        )

    if settings["on_floor"] == "restart_set":
        return ProgressionResult(
            _target(
                _lesson_id(
                    current.skill,
                    current.irl,
                    current.stage,
                    current_set.first_sequence,
                ),
                lessons,
            )
        )
    raise ProgressionError("No previous curriculum set exists")
