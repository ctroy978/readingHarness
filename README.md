# readingHarness

An adaptive reading lesson format and a stateless server for distributing lesson JSON files.
The server supports onboarding, signed bookmarks, and a 3-day pacing gate. It has no accounts,
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
levels in `server/config/onboarding.json`, and restart the server. No Python changes are needed.

Keys may contain lowercase letters, digits, underscores, and hyphens. Do not rename a key after
students have signed tickets containing it.

## Add lessons

Lesson files live in the configured GitHub directory (or `LESSON_DIR` during local development)
and use this exact pattern:

```text
<category>_L<level>_<sequence>.json
```

Examples:

```text
vocab_L1_0001.json
vocab_L1_0002.json
vocab_L2_0001.json
inference_L10_1000.json
```

Rules:

- Use a configured category key for `<category>`.
- Levels and sequences are positive integers and sort numerically. There is no fixed maximum.
- The JSON `lesson_id`, when present, must equal the filename without `.json`.
- Set schema-v2 `meta.status` to `published`; `draft` files are rejected.
- Place only published lessons in the lesson directory.
- `/start?skill=vocab&level=1` opens the lowest sequence at that category and level.
- An AI exit line names the exact destination lesson, so multiple lessons at one level are safe.

The unsigned exit line format is:

```text
v0|skill=vocab|level=1|lesson=vocab_L1_0002
```

The AI prints that line. The student pastes it at `/finish`, which returns a signed `/next` URL.
The 3-day clock starts when the server signs the line.

## Add or change Star entry levels

Edit `server/config/onboarding.json`. Each row has a stable `key`, a displayed score `label`, and
an `entries` object mapping category keys to entry levels:

```json
{
  "key": "600-699",
  "label": "600-699",
  "entries": {"inference": 3, "vocab": 2}
}
```

Every entry must point to at least one published lesson at that category and level. A row may
have an empty `entries` object; those score and lesson-type combinations display "Lesson not
available". Restart the server after editing.

The onboarding page has two selectors: Star score and lesson type. It submits directly to the
starting lesson for that combination. The lesson type list comes from `categories.json`, so a
new category appears without changing the page template.

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

The signed ticket is not a grade. It contains only category, level, lesson ID, issue time, and a
random nonce. The system stores nothing about who the student is.

## Documents

- [Lesson authoring ruleset](docs/LESSON_AUTHORING_RULESET.md)
- [Lesson release checklist](docs/LESSON_RELEASE_CHECKLIST.md)
- [Server build plan](docs/SERVER_BUILD_PLAN.md)
