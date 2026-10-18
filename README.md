# readingHarness

An adaptive reading lesson format and tutor protocol for struggling readers. Each lesson is
a self-contained JSON file that an AI presents, scores against authored keys, and adapts
using explicit rules. The server is stateless and routes between lessons using signed exit
tickets.

## Documents

- [Lesson authoring ruleset](docs/LESSON_AUTHORING_RULESET.md): normative design rules for
  reading instruction, AI tutoring, scoring, adaptation, and JSON structure.
- [Lesson release checklist](docs/LESSON_RELEASE_CHECKLIST.md): review and validation gate
  for every new lesson file.

## Current proof-of-concept lessons

- `lessons/inference_diner_01.json`: one-step inference at a fifth-grade reading level.
- `lessons/process_locks_01.json`: nonfiction process reading at a seventh-grade reading
  level, including cloze and short-answer items.
