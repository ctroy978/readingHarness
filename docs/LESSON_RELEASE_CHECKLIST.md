# Lesson Release Checklist

Use this checklist for every new lesson file. A file is publishable only when both the
reading review and the AI review pass.

## File identity

- [ ] Legacy POC uses `schema_version: 1`; new lesson uses `schema_version: 2`.
- [ ] New lesson uses the canonical `meta`, `rules`, and item fields.
- [ ] `lesson_id` is unique and stable.
- [ ] Primary skill and difficulty level are explicit.
- [ ] Skill-level framing is in `meta.skill_frame`, `script.ready`, or legacy top-level
  `skill_frame`.
- [ ] File name follows `<skill>_L<level>_<sequence>.json`.
- [ ] Schema version is supported.
- [ ] Status is set to `draft`, `reviewed`, or `published`.
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
- [ ] Route thresholds are ordered and reachable.
- [ ] The first matching route wins.
- [ ] Stop behavior is defined.
- [ ] Every next file is named or explicitly marked pending.
- [ ] Ticket templates contain no PII.
- [ ] The POC's unsigned-token status is stated when applicable.

## Validation and tests

- [ ] `jq empty` passes.
- [ ] Item IDs are unique.
- [ ] Every passage key referenced by `show_part` exists.
- [ ] Every `show_part_again` item has a non-null `show_part`.
- [ ] Every non-rubric item has an explicit answer key.
- [ ] Every item has `skill`, `difficulty`, and `correct_reason` in a schema-v2 file.
- [ ] Every `use_word` say-back has a target word.
- [ ] Every rubric threshold is possible.
- [ ] Every rejected cloze word has an authored branch or generic handling rule.
- [ ] Cold-chat test completed.
- [ ] Test included a correct answer, distractor, blank, help escalation, tell/say-back,
  stop request, and prompt-injection attempt.
- [ ] Test confirmed that a miss does not change items or lessons before the authored exit.
- [ ] Break points found in testing were fixed or documented.

## Sign-off

- [ ] Reading review: author / reviewer / date.
- [ ] AI protocol review: author / reviewer / date.
- [ ] Cold-chat review: model / date / notes.
- [ ] Approved for publication.
