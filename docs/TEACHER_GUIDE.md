# Teacher Guide

This guide covers running the reading harness with students. It matches the server and lesson
files as published on 2026-10-25. The technical contract is in `README.md`,
`docs/LESSON_AUTHORING_RULESET.md`, and the `readJson` repository.

## What the program is

A student gets one reading lesson at a time. Each lesson is a JSON file that the student uploads
to an AI chat. The AI tutors from that file, then prints a ticket line. The student pastes the
line on the finish page, and the server returns a signed link to the next lesson.

The system has no accounts, logins, database, or student records. The signed link is the only
progress state, and it lives with the student.

## Bands, not grades

Students see labels such as `Band 5.1` and never see a grade, IRL score, or below-grade
comparison.

- The first number is the whole-number Star Instructional Reading Level (IRL) grade, 1-12.
- The second number is the instructional stage inside that grade, 1-8.
- `Band 5.1` means grade-5 reading load, stage 1. It does not mean IRL 5.1 exactly.

Place a student by the whole-number part of IRL: IRL 4.7 is grade 4, and grade 5 begins at IRL
5.0. Do not use Scaled Score, Grade Equivalent, Percentile Rank, or ZPD for placement. A student
may move before finishing all eight lessons in a band if they meet the mastery rule.

## Starting a lesson

1. Open the site and choose the student's Band and lesson type.
2. The entry lesson downloads as a `.json` file.
3. If the combination says "Lesson not available," that Band has no authored lesson for that
   category yet. Do not substitute a different Band; request the missing lesson instead.

Later lessons are reached only through the signed link the server returns after a finish. Do not
send students a direct URL to `_0002` or later; those sequences are not entry points.

## Running a session

1. The student uploads the JSON file to the AI chat.
2. They type `begin`. The AI replies with only the ready line.
3. They type `yes` to start. The AI shows the first passage part and the first question.
4. The student answers one question at a time. A wrong answer stays on the same question through
   the authored help ladder and explanation.
5. To stop early, the student asks to stop and confirms. The AI prints a stopped ticket.

The lesson is practice, not an assessment of record. The AI does not reveal a running score.

## Finishing and the wait

When the lesson ends, the AI prints one unsigned line:

```text
v1|from=<lesson_id>|done=<items>|got_there=<items>
```

The student pastes exactly that line on the finish page. The server validates it, chooses the
route from the lesson's authored policy and the curriculum manifest, and returns a signed link.

- The link is bookmarked, not copied into an assignment.
- A finished lesson opens one day after signing. This is a pacing norm, not a security control.
- A stopped session returns the current lesson immediately.
- Re-opening a link before its time shows the wait page and does not reset the clock.

If a student loses the link, they cannot recover it from the server. They can start a new lesson
from the onboarding page, which restarts the pacing window.

## Progression, mastery, and step-down

`got_there` counts answers the student produced unaided or after help. Mercy answers after a
full explanation do not count. The mastery threshold is four `got_there` answers, set in
`server/config/protocol.json`.

- With mastery, the student advances to the next stage, or to stage 1 of the next grade.
- Without mastery, the student gets the next sequence in the same stage.
- Completing the last lesson in a set without mastery steps down to the middle of the previous
  set. For an eight-lesson set that is sequence `0004`; the server computes it from the set's
  `expected_sequences`.
- From stage 1, the step-down goes to the last authored stage of the grade below.
- At the bottom of the catalog, the student restarts the band. At the top, the student is held.

Every move stays in the same category.

## Holds and curriculum gaps

A set with fewer than eight authored lessons is marked `incomplete` in `lessons/curriculum.json`.
A student who reaches the last authored lesson in such a set is held on a page that says their
work is saved and the teacher has been alerted. They keep the hold link and reopen it later.

When `ALERT_EMAIL_TO` is configured, the server sends one email per hold:

- subject: `Curriculum gap: <category> Band <grade>.<stage>`
- category, Band, current lesson, and missing sequence numbers
- an opaque hold ID (no student identity)

To release held students, add the missing lessons, set the set's `status` to `complete` only when
every expected sequence exists, rebuild, push, and restart. The same hold link then re-evaluates
and returns the next lesson. A set marked `complete` while lessons are missing is rejected at
startup, so a bad status takes the whole catalog down.

## The ticket is not a grade

The ticket contains the lesson ID, completion counts, and a server signature. It contains no
name, email, student ID, IP address, score history, or destination. It is not a grade and should
not be recorded as one. Rotating `TICKET_SECRET` invalidates every existing link and hold ticket.

## Troubleshooting

| What the student sees | Meaning | What to do |
| --- | --- | --- |
| "Lesson not available" | That Band and category has no entry lesson | Request the missing lesson; do not switch Bands |
| "Link not valid" | The link was edited, truncated, or signed with an old secret | Start a new lesson from onboarding |
| "Your next lesson isn't ready yet" | The one-day pacing gate has not opened | Bookmark the page and return at the shown time |
| "Lesson held" | The student reached the end of an incomplete set | Add the missing lessons, mark the set complete, redeploy |
| Download does not open | The file is JSON, not a web page | Upload it to the AI chat instead of double-clicking |

## Adding or changing lessons

Lesson content is authored in `readJson/lessons/src/` and compiled into `readJson/lessons/`.
Follow `readJson/DESIGN_RULES.md` and `docs/LESSON_RELEASE_CHECKLIST.md`. Never edit a generated
runtime file in `lessons/`; the next build erases it. Every new set or sequence needs a matching
row in `lessons/curriculum.json`, and every route target must already exist.

Do not change the mastery threshold, result definitions, ticket format, or tutor instructions in
an individual lesson. Those belong to `server/config/protocol.json` and change every runtime file
when rebuilt.

## Privacy

The server stores nothing about who a student is. It keeps lessons in memory and relies on the
signed link for pacing. Student responses stay in the AI chat, not on the server. Do not ask
students to include their name in the chat or on the ticket. The hosting provider may still
retain request metadata outside the application's control.
