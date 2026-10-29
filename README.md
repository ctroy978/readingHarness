# readingHarness

An adaptive reading lesson format and a stateless server for distributing lesson JSON files.
The server supports onboarding, signed bookmarks, and a one-day pacing gate. It has no accounts,
database, cookies, or student records.

## Run locally

Requirements: Python 3.11 or newer and `uv`.

```bash
uv sync --all-groups
cp .env.example .env
# Edit .env with your GitHub repository and token.
uv run uvicorn server.main:app --reload --no-access-log
```

Open `http://127.0.0.1:8000/`. Run tests with `uv run pytest`.

Configuration:

| Variable | Purpose |
| --- | --- |
| `TICKET_SECRET` | Required. HMAC key. Use at least 32 random bytes. Never commit it. |
| `GITHUB_REPO` | GitHub repository containing lessons, as `owner/repo` or its URL. |
| `GITHUB_TOKEN` | Fine-grained token with read-only repository Contents access. |
| `GITHUB_REF` | Optional branch, tag, or commit SHA. Defaults to `main`. |
| `GITHUB_LESSON_PATH` | Optional directory inside the repository. Empty means repository root. |
| `LESSON_DIR` | Optional local lesson directory for development. Used only when `GITHUB_REPO` is unset. |
| `PUBLIC_BASE_URL` | Required in deployment. Absolute base URL used in signed links. |
| `DISPLAY_TIMEZONE` | Optional IANA timezone for the wait page. Defaults to `UTC`. |
| `CONFIG_DIR` | Optional. Defaults to `server/config`. |
| `RESEND_API_KEY` | Optional. Resend API key for curriculum-gap email alerts. |
| `RESEND_FROM` | Optional. Verified Resend sender for curriculum-gap alerts. |
| `RESEND_REPLY_TO` | Optional. Reply address on curriculum-gap alerts. |
| `ALERT_EMAIL_TO` | Optional. Recipient for curriculum-gap alerts. |

The server loads `.env` from the working directory, then the process environment. The server
refuses to start without a valid secret and lesson source. It fetches the GitHub repository
archive once at startup and keeps the lessons in memory, so requests do not call GitHub and
students do not need GitHub access.

On Render, `PUBLIC_BASE_URL` falls back to the automatically supplied
`RENDER_EXTERNAL_URL`. Set it explicitly when using a custom domain.

## GitHub lesson repository

Create a fine-grained GitHub token scoped to the lesson repository with:

- **Repository contents:** Read-only.
- No access to actions, issues, pull requests, or other repositories.

Put the repository and token in `.env`:

```dotenv
GITHUB_REPO=your-account/reading-lessons
GITHUB_TOKEN=github_pat_...
GITHUB_REF=main
GITHUB_LESSON_PATH=lessons
```

To publish a lesson or category, commit and push it to GitHub, then restart or redeploy the
server. The server does not poll GitHub. Pin `GITHUB_REF` to a commit SHA if a deployment must
stay on one known lesson set.

## Add a category

Edit `server/config/categories.json`. Each category has a permanent lowercase `key`, a display
`label`, and a unique numeric `order`. Lower order values appear first.

```json
{"key": "vocab", "label": "Vocabulary", "order": 20}
```

Then add one or more lesson files using the new key to the GitHub lesson repository, map its entry
lesson in `server/config/onboarding.json`, and restart the server. No Python changes are needed.

Keys may contain lowercase letters, digits, underscores, and hyphens. Do not rename a key after
students have signed tickets containing it.

## Add lessons

Lesson files live in the configured GitHub directory (or `LESSON_DIR` during local development)
and use this exact pattern:

```text
<category>_band_<grade>_<stage>_<sequence>.json
```

Examples:

```text
vocab_band_5_1_0001.json
vocab_band_5_1_0002.json
vocab_band_5_2_0001.json
inference_band_9_1_0001.json
```

Rules:

- Use a configured category key for `<category>`.
- `<grade>` is the whole-number Star IRL grade, from 1 through 12.
- `<stage>` is an authored progression stage, from 1 through 8.
- `<sequence>` is a lesson position, from 0001 through 0008.
- Eight lessons form one horizontal. Stages advance vertically before the grade changes.
- The JSON `lesson_id`, when present, must equal the filename without `.json`.
- Set `meta.status` to `published`; `draft` runtime files are rejected.
- Place only published lessons in the lesson directory.
- `/start?band=5.1&skill=vocab` opens the configured starting lesson for Band 5.1 and that category.
- `/start?skill=vocab&level=5` is rejected. Later lessons are reachable only through a
  server-selected, signed next-lesson link.
- An AI exit line reports the current lesson and outcome counts. It does not name a destination.

The unsigned exit line format is:

```text
v2|from=vocab_band_5_1_0001|done=5|u=4|h=1|m=0|o=0|an=1|carry=
```

The AI prints that line. The student pastes it at `/finish`. The server resolves the next lesson
from `curriculum.json` and returns a signed `/next` URL containing the destination,
`available_at`, and the updated `carry` state. A move needs two clears among the last three
lessons, so the link also carries the result of this lesson. `carry` is empty on the first lesson
of a rung and grows as the student works through it. While the current set is `incomplete`,
that destination is the next existing lesson in the band, or a hold when the student is already on
the last existing lesson. The one-day clock is set by the server when it signs the line. The link
cannot be edited to change the destination, the unlock time, or the carried history.

This is a pacing gate, not a tamper-proof one-lesson-per-student enforcement system. Without
server state, a student can still start another progression or copy a link. The server stores no
student identifier, progress record, cookie, or database.

## Build lesson JSON

Authored lesson sources use `schema_version: 3` with `document_type: "lesson_source"`. The shared
tutor protocol, common scripts, result definitions, route thresholds, and completion-token format
live in `server/config/protocol.json`. A source lesson contains only its metadata, passage, items,
script overrides, and three route destinations.

Compile sources into ordinary self-contained runtime lesson JSON:

```bash
uv run python scripts/build_lessons.py \
  --source-dir /path/to/lesson-repo/lessons/src \
  --output-dir /path/to/lesson-repo/lessons
```

Check that committed generated files match their sources:

```bash
uv run python scripts/build_lessons.py \
  --source-dir /path/to/lesson-repo/lessons/src \
  --output-dir /path/to/lesson-repo/lessons \
  --check
```

Changing a shared rule or the mastery threshold is a change to `protocol.json` followed by a
rebuild. Do not copy shared protocol text into individual lesson sources.

## Add or change Band entry points

Edit `server/config/onboarding.json`. Each row has a stable Band `key`, a student-facing `label`,
and an `entries` object mapping category keys to the exact ID of the first lesson:

```json
{
  "key": "5.1",
  "label": "Band 5.1",
  "entries": {"inference": "inference_band_5_1_0001", "vocab": "vocab_band_5_1_0001"}
}
```

Every entry must name a published lesson in that category. A row may have an empty `entries`
object; those Band and lesson-type combinations display "Lesson not available". Restart the
server after editing.

The onboarding page has two selectors: reading Band and lesson type. It submits directly to the
configured entry lesson for that combination. Later lessons are reached only through signed exit
tickets, so adding another file in that Band never changes the onboarding entry point. The
lesson type list comes from `categories.json`, so a new category appears without changing the
page template.

Student-facing Band labels must never expose a grade, IRL, or below-grade comparison. `Band 5.1`
is the correct presentation; `Grade 5`, `IRL 5.1`, and `5th grade` are not.

## Progression and exhaustion

The target horizontal is eight lessons in one category, grade, and stage. Set boundaries are
declared by the lesson repository's `lessons/curriculum.json`. A student may advance before the end
of a set by meeting the mastery threshold. Vertical progression is:

```text
5.1 -> 5.2 -> ... -> 5.8 -> 6.1
```

Every move stays in the same category. After completing the last lesson in a set without mastery,
move to the middle of the previous set. The server computes the middle from that set's actual
`expected_sequences`; an eight-lesson set gives sequence `0004`, a five-lesson set gives `0003`, and
a one-lesson set gives `0001`. If the student is in stage 1, the previous set is the last authored
stage of the grade below. At the bottom Band, restart at the first sequence.

`server/progression.py` owns this transition logic. It does not count lessons across requests and
does not ask the AI to choose a destination. The AI reports results; the server resolves the next
lesson against the catalog.

Sets marked `incomplete` in `curriculum.json` do not use central progression yet. The default
`on_incomplete_set` policy is `hold_and_alert`. Finishing a lesson in a short set serves the next
file that already exists in that band. A student who reaches the last authored lesson is held
there; the server emails `ALERT_EMAIL_TO` with the category, Band, current lesson, missing
sequences, and an opaque hold ID. The student bookmarks the signed hold link or pastes the same
ticket again after the server restarts. If the set is still short, the student stays on hold.
Adding the next lesson and restarting serves that lesson from the same ticket while the set stays
`incomplete`. Mark the set `complete` only when every expected sequence exists. Completion turns
on mastery and step-down. A complete set is rejected at startup if any expected lesson is missing.

## Deploy

### Render

The repository includes `render.yaml` for a GitHub-connected Render Blueprint. It builds with
`uv sync --frozen --no-dev`, starts Uvicorn on Render's `PORT`, and uses `/health` for the health
check.

1. Push the service files, `pyproject.toml`, `uv.lock`, and `render.yaml` to GitHub.
2. Create a Render Blueprint from that repository.
3. Enter `GITHUB_REPO` and `GITHUB_TOKEN` when Render prompts for the required values. The
   repository must contain published lessons at `GITHUB_LESSON_PATH` using the naming rules
   above.
4. After the first deploy, open the generated `onrender.com` URL and verify `/health` returns
   `{"ok":true}`.

The Blueprint generates `TICKET_SECRET`. On the free plan, Render sleeps an idle service and
waits for it to wake before handling the next request. Startup downloads the lesson archive, so
the first request after sleep can be slow. A failed startup check will keep the service from
becoming healthy.

### Other hosts

Run Uvicorn with `--no-access-log`, serve HTTPS, and inject `TICKET_SECRET`, `GITHUB_REPO`, and
`GITHUB_TOKEN` from the host's secret store. Do not enable debug tracebacks or request-body
logging. The hosting provider may retain request metadata outside the app's control.

The GitHub token is server-only. It is never included in tickets, pages, responses, or logs. A
restart fetches the current lesson set; a failed GitHub fetch prevents startup rather than serving
an incomplete catalog.

Rotating `TICKET_SECRET` invalidates every existing bookmark. Treat rotation as a deliberate
break-glass operation.

The signed ticket is not a grade. It contains only category, IRL grade, Band lesson ID, issue time,
and a random nonce. The system stores nothing about who the student is.

## Documents

- [Lesson authoring ruleset](docs/LESSON_AUTHORING_RULESET.md)
- [Lesson release checklist](docs/LESSON_RELEASE_CHECKLIST.md)
- [Teacher guide](docs/TEACHER_GUIDE.md)
- [Server build plan](docs/SERVER_BUILD_PLAN.md)
