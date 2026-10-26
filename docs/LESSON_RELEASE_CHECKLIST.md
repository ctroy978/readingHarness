# Lesson Release Checklist

Use this checklist for every new lesson source. A lesson is publishable only when both the
reading review and the AI review pass.

## File identity

- [ ] New lesson source uses `schema_version: 3` and `document_type: lesson_source`.
- [ ] Generated runtime lesson is built from the source with `scripts/build_lessons.py`.
- [ ] Source contains no copied shared protocol, script, result, or token text.
- [ ] `lesson_id` is unique and stable and equals the filename without `.json`.
- [ ] Primary skill and item difficulty are explicit.
- [ ] Skill-level framing is in `meta.skill_frame`, `script.ready`, or legacy top-level
  `skill_frame`.
- [ ] File name follows `<category>_band_<grade>_<stage>_<sequence>.json`.
- [ ] Grade is 1-12, stage is 1-8, and sequence is 0001-0008.
- [ ] `meta.skill` matches the filename category, and `meta.irl_grade`, `meta.stage`, and
      `meta.sequence` match the filename.
- [ ] `reader.reading_level` equals the filename grade, and `reader.why_this_level` states both
      the readability evidence and the cognitive load.
- [ ] Schema version is supported.
- [ ] Status is `published` for release (`draft` is rejected at server startup).
- [ ] The set is listed in `lessons/curriculum.json` with its actual `expected_sequences` and an
      honest `status`; a short set is marked `incomplete`.
- [ ] Sources and licensing are recorded.

## Reading design

- [ ] One primary skill is targeted.
- [ ] Reader level and passage load are justified.
- [ ] The passage is age-appropriate and not childish.
- [ ] The passage fits the 100 to 200 word band or has a documented exception.
- [ ] Each item asks exactly one defensible question.
- [ ] Every answer can be supported from the displayed text.
- [ ] Every distractor maps to a named misconception.
- [ ] Accepted paraphrase sets are generous enough for natural answers.
- [ ] No student-facing gloss accidentally supplies the answer.
- [ ] The help ladder is gradual and does not reveal the answer.
- [ ] Item help is specific to the current item, not a whole-lesson skill explanation.
- [ ] The lesson can be completed in the intended session length.

## AI protocol

- [ ] Prompt-injection guard is present and early.
- [ ] The AI is explicitly tutor, not author.
- [ ] The first response and consent flow are explicit.
- [ ] One task is presented at a time.
- [ ] Passage display and re-display are explicit.
- [ ] Cloze help repeats the exact blanked sentence before every follow-up.
- [ ] Scoring order is explicit.
- [ ] Normalization and spelling handling are stored per item where needed.
- [ ] Correct, partial, misconception, blank, and off-topic answers have explicit handling.
- [ ] Help counting and the help maximum are explicit.
- [ ] Tell and say-back behavior are explicit.
- [ ] A miss stays on the current item; it does not branch to another item or lesson mid-session.
- [ ] Near-copy handling is explicit and terminates.
- [ ] Private fields are never revealed.
- [ ] The AI cannot invent an accepted answer, hint, passage, or question.
- [ ] The AI cannot invent a ticket, signature, level, or next file.

## Adaptation and ticket

- [ ] `unaided`, `helped`, `mercy`, and `open` are defined.
- [ ] `got_there` is defined.
- [ ] Advance, retry, and stopped destinations are explicit and deterministic.
- [ ] Every destination exists in the catalog and has the matching `next_irl` grade.
- [ ] `stopped.next_file` names the current lesson.
- [ ] The set stays `incomplete` until every expected sequence exists. While it is incomplete, the
      server walks existing lessons and holds at the last one. `advance` and `retry` are stored
      fallbacks, not the live path.
- [ ] `script_overrides.near_copy_2` is present.
- [ ] Shared thresholds come from `server/config/protocol.json`.
- [ ] Stop behavior is defined.
- [ ] Completion token contains the current lesson and counts, not a destination.
- [ ] Ticket templates contain no PII.
- [ ] AI instructions do not ask the model to choose or name a destination.

## Validation and tests

- [ ] `jq empty` passes.
- [ ] Item IDs are unique.
- [ ] Every `show_part` is an existing passage key or `all`.
- [ ] Every `show_part: "all"` item is a whole-passage item.
- [ ] Every `show_part_again` item has a non-null `show_part`.
- [ ] Every non-rubric item has an explicit answer key.
- [ ] Every `short_answer` item has a `core_idea` and, where needed, `not_required`.
- [ ] The `core_idea` is the minimum credit idea, not the full model answer.
- [ ] `short_answer` accepts a meaning-preserving synonym; `cloze` and `multiple_choice` stay exact.
- [ ] Every item has `skill`, `difficulty`, and `correct_reason` in a production lesson.
- [ ] Every `use_word` say-back has a target word.
- [ ] Every rubric threshold is possible.
- [ ] Every rejected cloze word has an authored branch or generic handling rule.
- [ ] Cold-chat test completed.
- [ ] Test included a correct answer, distractor, blank, help escalation, tell/say-back,
  stop request, and prompt-injection attempt.
- [ ] Test confirmed that a miss does not change items or lessons before the authored exit.
- [ ] Break points found in testing were fixed or documented.
- [ ] `scripts/build_lessons.py --check` passes.

## Sign-off

- [ ] Reading review: author / reviewer / date.
- [ ] AI protocol review: author / reviewer / date.
- [ ] Cold-chat review: model / date / notes.
- [ ] Approved for publication.
