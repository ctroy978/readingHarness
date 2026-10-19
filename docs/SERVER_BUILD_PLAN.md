# Stateless Reading Harness Server Build Plan

Updated: 2026-10-18
Status: Implemented

This is the build plan for the stateless FastAPI service that distributes lesson JSON files,
supports onboarding, and enforces 3-day pacing. It incorporates the expanded onboarding,
bookmark, signing, and privacy requirements.

## 1. Outcome

Deliver a small server with five public behaviors:

1. `GET /` shows the teacher-maintained Star score table and student instructions.
2. `GET /start` validates a category and level, then downloads the first lesson at that entry
   point.
3. `POST /sign` turns an unsigned AI exit line naming an exact catalog lesson into a signed,
   bookmarkable `/next` URL.
4. `GET /next` either explains when the next lesson will open or downloads it.
5. `GET /health` returns only `{"ok": true}`.

The service stores no accounts, progress, sessions, cookies, or request history. The signed
ticket is the complete location and pacing state. A startup catalog maps category, level, and
sequence-ordered lesson files to stable lesson IDs without imposing a fixed category count or a
fixed number of lessons per category.

## 2. Contract Decisions

The expanded prompt contains a few statements that cannot all be true at once. The build
will use these resolutions:

1. **The clock starts at `/sign`, not `/start`.** `/start` only downloads an entry lesson.
   A signed ticket cannot begin its wait at `/start` if `/start` does not expose that ticket.
   When the student submits the AI's unsigned exit line to `/sign`, the server stamps
   `issued_at = now`, signs the ticket, and starts the 3-day wait. This follows the final,
   explicit `/start` instruction and removes the unused ticket.

2. **Signed tickets contain only routing and pacing data.** The canonical payload is:

   `v0|skill=inference|level=6|lesson=inference_L6_0042|issued=2026-10-14T18:30:00Z|nonce=<random>`

   `skill` is the category key, `level` is the destination lesson level, and `lesson` is the
   stable catalog ID for the exact file. The three fields must agree in the catalog. The signed
   ticket does not contain a name, email, student ID, score history, current-level counts, a
   free-form path, or a filename. The example's optional `from` value may be accepted from the
   AI as unsigned context, but it is not copied into the signed ticket.

3. **The server owns `issued` and `nonce`.** The signer validates the unsigned line but
   replaces its timestamp with current UTC time and mints a fresh random nonce. A caller
   therefore cannot backdate a ticket and skip the pacing gate. This is the security reason
   `/sign` can be unauthenticated: it can sign any valid catalog lesson, but every signed ticket
   starts a new wait. The pacing gate is a norm, not access control.

4. **The ticket names an exact catalog lesson, not a path.** A lesson ID maps to one file in the
   generated catalog. The server serves only that cached, validated mapping and never interprets
   caller input as a path. This permits multiple lessons at the same category and level while
   keeping each bookmark unambiguous.

5. **Lesson naming stays sequence-numbered.** Release files use
   `<skill>_L<level>_<sequence>.json`; their catalog IDs are the filename without `.json`.
   Compare levels and sequences as integers, never as strings. There is no fixed count of
   categories, levels, or lessons per category. A category with 1,000 or more sequence-numbered
   lessons is just a larger catalog.

6. **Category display order is explicit configuration.** `categories.json` assigns each category
   a stable key, display label, and sort order. Level order is numeric ascending and sequence
   order is numeric ascending. The catalog builder rejects duplicate keys, orders, levels, and
   lesson IDs instead of depending on filesystem or JSON-object ordering.

7. **A no-JavaScript finish page handles the student handoff.** The AI cannot sign, and a
   student should not need an API client. Add `GET /finish` with a plain form, plus
   `POST /finish`, which sends the pasted unsigned line through the same signer and returns an
   HTML page containing the signed link. The required `POST /sign` JSON endpoint remains for
   programmatic use.

8. **The onboarding copy describes the actual finish flow.** Instead of saying the AI gives a
   finished link, it says the AI gives a long ticket line, the student pastes that line on the
   finish page, and the student bookmarks the resulting `/next` link.

9. **Display time uses a configured timezone.** `DISPLAY_TIMEZONE` defaults to `UTC` and may be
   set to an IANA zone such as `America/Los_Angeles`. The availability page also includes an
   ISO `datetime` value in a `<time>` element. No browser state is needed.

10. **Tickets do not expire.** Validity depends on the HMAC, a valid payload shape, and a live
   catalog entry. Pacing
   depends only on `issued_at`. Rotating `TICKET_SECRET` would invalidate existing bookmarks,
   so the deployment docs will treat secret rotation as a deliberate break-glass operation.

## 3. Proposed Layout

```text
server/
  app/
    __init__.py
    main.py
    config.py
    github.py
    tickets.py
    lessons.py
    onboarding.py
    templates/
      onboarding.html
      finish.html
      signed.html
      not_yet.html
      invalid_link.html
  config/
    categories.json
    onboarding.json
  (lesson files live in the configured GitHub lesson repository)
  tests/
    conftest.py
    test_catalog.py
    test_tickets.py
    test_routes.py
    test_onboarding.py
  pyproject.toml
docs/
  SERVER_BUILD_PLAN.md
  TEACHER_GUIDE.md
README.md                   # updated run, deploy, env, and privacy guidance
```

Use FastAPI, Jinja2, Uvicorn, and the Python standard library. No database, ORM, session
library, analytics package, or third-party browser script is needed.

## 4. Configuration

Read configuration once at startup and fail closed on invalid values.

| Variable | Requirement |
| --- | --- |
| `TICKET_SECRET` | Required. At least 32 bytes of secret material. Never logged or returned. |
| `GITHUB_REPO` | Required in deployment. Repository containing lesson files. |
| `GITHUB_TOKEN` | Required with `GITHUB_REPO`. Fine-grained read-only Contents access. |
| `GITHUB_REF` | Optional branch, tag, or commit. Defaults to `main`. |
| `GITHUB_LESSON_PATH` | Optional directory inside the repository. |
| `LESSON_DIR` | Optional local development fallback when `GITHUB_REPO` is unset. |
| `PUBLIC_BASE_URL` | Required. Absolute `http` or `https` base URL used in returned ticket links. |
| `DISPLAY_TIMEZONE` | Optional. IANA timezone name for human-readable availability text. Defaults to `UTC`. |

The server refuses to start when the secret is absent, too short, or obviously a placeholder.
It also refuses to start when the lesson source, `categories.json`, or `onboarding.json` is
missing or invalid.

The category registry and Star table live in `server/config/categories.json` and
`server/config/onboarding.json`. This keeps a teacher able to add a category column without
changing routing code while still giving the server a strict allowlist. Validate category keys
with `^[a-z][a-z0-9_-]*$`, require the referenced catalog entries to exist, and reject duplicate
or contradictory table rows at startup.

### 4.1 Category Registry

`categories.json` controls presentation order and labels:

```json
{
  "categories": [
    {"key": "inference", "label": "Inference", "order": 10},
    {"key": "vocab", "label": "Vocabulary", "order": 20},
    {"key": "connotation", "label": "Connotation", "order": 30},
    {"key": "denotation", "label": "Denotation", "order": 40}
  ]
}
```

The key is permanent because tickets and URLs contain it. The label is editable. Array order is
not trusted; the numeric `order` field controls display order and must be unique. Adding a
category means adding one entry here, adding its cataloged files, adding its entry levels to the
Star table, and rebuilding the catalog. No route code changes are needed.

### 4.2 Startup Lesson Catalog

In deployment, the server downloads a GitHub repository archive once at startup, reads lesson
files under `GITHUB_LESSON_PATH`, and keeps the validated bytes in memory. In local development,
`LESSON_DIR` may provide the same files from disk. The catalog maps exact lesson IDs to validated
metadata:

```json
{
  "schema_version": 1,
  "lessons": {
    "vocab_L2_0001": {
      "skill": "vocab",
      "level": 2,
      "sequence": 1,
      "filename": "vocab_L2_0001.json"
    },
    "vocab_L2_0002": {
      "skill": "vocab",
      "level": 2,
      "sequence": 2,
      "filename": "vocab_L2_0002.json"
    }
  }
}
```

The catalog loader:

1. scans only regular files directly inside `LESSON_DIR`;
2. accepts names matching `^(?P<skill>[a-z][a-z0-9_-]*)_L(?P<level>\d+)_(?P<sequence>\d+)\.json$`;
3. requires the category to exist in `categories.json`;
4. requires the file's JSON `lesson_id`, when present, to match the filename stem;
5. parses and validates lesson metadata without retaining student data;
6. rejects duplicate lesson IDs, duplicate category-level-sequence tuples, invalid levels or
   sequences, and missing files;
7. sorts categories by registry order, levels numerically, and sequences numerically;
8. stores only the validated lesson bytes and metadata in memory.

The server fetches the GitHub archive only at startup. A failed or invalid fetch prevents startup
rather than serving a partial catalog. After startup, requests use the immutable in-memory
catalog. Update GitHub content and then restart or redeploy the service.

The generated catalog can comfortably handle thousands of lessons because it is built once and
held as an immutable in-memory dictionary. Requests use O(1) lesson-ID lookup and never list the
lesson directory.

### 4.3 Authorization and Ordering

Ordering is metadata, not student progress. The server does not remember which lesson a student
finished. The AI selects the next exact lesson ID from authored exit rules, the student signs
that line, and the server validates that the ID exists and belongs to the stated category and
level.

The catalog may include all reviewed or published lessons. Drafts must not be placed in the
release `LESSON_DIR`; the builder should also reject `meta.status == "draft"` for schema-v2
lessons. For the current schema-v1 POCs, treat inclusion in the release directory as the publish
signal and migrate their metadata when they enter the production bank.

To find the first lesson at a category-level entry point, `/start` selects the lowest numeric
sequence for that category and level. If the teacher wants a different entry lesson, they can
change the sequence number or add an explicit `entry_lesson` field to the catalog configuration.
Do not sort filenames lexically. Sequence `10` comes after `9`, not after `1`.

## 5. Ticket Module

Implement signing and verification in one small module with no FastAPI dependency.

### 5.1 Signed payload

Use an exact field order and a strict parser:

```text
v0|skill=<category-key>|level=<integer>|lesson=<catalog-id>|issued=<UTC ISO-8601>|nonce=<urlsafe-token>
```

Canonical rules:

- `skill` must be in the configured category registry.
- `level` must be a positive integer. A configurable sanity ceiling may bound absurd input, but
  there is no fixed curriculum-size ceiling such as 12.
- `lesson` must be an exact catalog ID.
- The catalog entry for `lesson` must have the same `skill` and `level` as the ticket.
- `issued` is UTC, second precision, formatted with a trailing `Z`.
- `nonce` is generated with `secrets.token_urlsafe` and is not derived from student data.
- Reject missing, duplicate, unknown, overlong, or out-of-order fields.
- Limit the whole payload and ticket to a small maximum length before parsing.

### 5.2 Signature

- Compute `HMAC-SHA256(secret, payload_bytes)` with the standard `hmac` and `hashlib` modules.
- Hex-encode the digest and form `<payload>.<signature>`.
- Verify by splitting at the final `.` and comparing with `hmac.compare_digest`.
- Verify the exact payload bytes before decoding or normalizing values.
- URL-encode the complete ticket when placing it in `/next?t=...`; the HMAC still covers the
  unencoded payload exactly.

Do not encrypt the payload. Do not add an expiry claim. The signature prevents modification;
the pace check governs timing.

### 5.3 Signing boundary

`POST /sign` accepts JSON of the form `{"line": "<unsigned exit line>"}`. The signer:

1. validates the line's shape, category, level, and exact lesson ID against the catalog;
2. discards any client-supplied time and nonce;
3. stamps current UTC time and a fresh nonce;
4. returns `{"url": "<PUBLIC_BASE_URL>/next?t=<signed-ticket>"}`.

Put a code comment beside this endpoint documenting the no-auth trade-off: a caller can request
any valid catalog lesson, but cannot backdate the ticket or bypass the wait. Access control is
not a goal of this service.

The current schema-v1 test lessons use `skill`, `level`, and the exact destination `lesson` in
their exit lines. Production lessons must use that same signing contract. Do not add a broad
legacy parser unless distributing older lesson formats becomes an explicit requirement.

## 6. Route Behavior

### `GET /`

Render the onboarding page from the validated table configuration. It includes:

- the short reading-program explanation;
- a Star score to entry-level table with one `/start?skill=...&level=...` link per cell or row;
- numbered instructions for finding the score, downloading the lesson, uploading it to the AI,
  pasting the AI's unsigned exit line on the finish page, and bookmarking the returned link;
- the "Do not share your link" note.

Use server-side HTML escaping. Do not add analytics, third-party scripts, cookies, or local
storage.

### `GET /start?level=<n>&skill=<name>`

- Validate skill against the configured category registry and level against the integer range.
- Return HTTP 400 with the plain invalid-link page for invalid input.
- Select the lowest numeric sequence catalog lesson for that skill and level.
- Resolve only the catalog's stored filename from `LESSON_DIR`; never scan the directory per
  request.
- Return the file as `application/json` with an attachment filename. Do not expose a ticket.
- Use the same plain invalid-link page for missing files.

### `POST /sign`

- Accept a bounded JSON body containing one unsigned line.
- Return a signed absolute URL as JSON.
- Never log the request body, ticket, or client IP.
- Return a small JSON error for malformed input; do not include request contents.

### `GET /finish` and `POST /finish`

- Serve a plain form for pasting the AI's unsigned exit line.
- On submit, call the same signer and render the signed URL as a clickable link.
- Use POST and no cookies, local storage, or browser-side state.
- Show a concise invalid-input message when signing fails.

This page exists because the AI cannot hold the signing secret. It is the student-facing bridge
between the AI's unsigned output and the server's signed `/next` URL.

### `GET /next?t=<ticket>`

1. Verify length, payload shape, HMAC, skill, level, lesson ID, timestamp, and nonce.
2. On any signature or payload failure, return the plain invalid-link page with HTTP 400.
3. Resolve the exact lesson ID through the catalog and confirm its skill and level agree.
4. Compute `available_at = issued_at + 3 days` using timezone-aware UTC values.
5. If `now < available_at`, return the "Not yet" page with HTTP 200. Do not serve the file and do
   not change the ticket.
6. If `now >= available_at`, serve the catalog entry's filename as an attachment.
7. If the catalog entry or file is missing, return the same invalid-link page so the student cannot
   distinguish a missing file from a bad link.

The endpoint is idempotent. Re-clicking the bookmark before the gate opens repeats the wait page;
re-clicking after it opens serves the file every time. No ticket is consumed.

### `GET /health`

Return only `{"ok": true}`. Do not add version, environment, path, secret, or diagnostic details.

## 7. File Safety

For every lesson request:

1. Look up the filename from the immutable generated catalog; never derive it from a free-form
   ticket field.
2. Require the catalog filename to match
   `^[a-z][a-z0-9_-]*_L\d+_\d+\.json$`.
3. Reject any slash, backslash, dot segment, absolute path, or unexpected character.
4. Join to `LESSON_DIR`, resolve the path, and confirm it remains beneath the resolved lesson
   directory.
5. Serve only regular files; never list directories or accept a path from the query string.

Set `Content-Type: application/json`,
`Content-Disposition: attachment; filename="<catalog filename>"`, and
`X-Content-Type-Options: nosniff`.

## 8. HTML and Pacing Presentation

There are four student-facing pages: onboarding, finish, not-yet, and invalid-link. All are
server-rendered, escaped, dependency-free, and usable without JavaScript. A signed-result page
may share the finish template.

The "Not yet" page contains:

- "Your next lesson isn't ready yet."
- A `<time datetime="<ISO UTC value>">` element plus human-readable text in
  `DISPLAY_TIMEZONE`, including the timezone name.
- "Bookmark this page. Come back after that time and click the same bookmark."

Do not add a live countdown, client clock calculation, tracking pixel, or stored state.

## 9. Privacy and Logging

- Run Uvicorn with access logging disabled, for example `--no-access-log` or
  `access_log=False` in the programmatic entrypoint.
- Do not add middleware that logs paths, query strings, bodies, headers, or client addresses.
- Do not enable debug tracebacks or vendor error reporting that captures requests.
- Do not set cookies or use server sessions. Add a regression test that asserts no
  `Set-Cookie` header appears on any route.
- Keep tickets opaque and free of names, emails, student IDs, IP addresses, or score history.
- Document that the hosting provider may still retain request metadata outside the app's
  control; deployment guidance should minimize or disable those logs where possible.
- Open `LESSON_DIR` read-only and do not write application state to disk.

## 10. Tests and Acceptance Mapping

Use a temporary lesson directory, a test secret, a small test catalog, and an injectable clock.
Keep timing tests independent of the real current date. Add catalog tests for numeric ordering,
duplicate rejection, category additions, and a synthetic category with more than 1,000 lessons.

| Acceptance item | Planned verification |
| --- | --- |
| Onboarding content | Route test asserts explanation, table links, numbered instructions, bookmark warning, and no third-party scripts. |
| Category registry | Adding a configured category with cataloged lessons makes its onboarding links and downloads work without code changes. |
| Entry download | `/start?level=4&skill=inference` selects the lowest numeric sequence at that level and returns the cataloged attachment; bad inputs return 400. |
| Sequence order | Levels and sequences are compared numerically; `10` follows `9`, and `1000` is accepted. |
| Signing | Valid unsigned line with an exact catalog lesson returns a signed URL; tampering and unknown lesson IDs fail. |
| Fresh ticket | Fixed clock returns 200 "Not yet", exact availability time, and no file. |
| Mature ticket | Fixed clock at or beyond 3 days serves the file; a second request serves it again. |
| Tampered ticket | Changed payload or signature returns the invalid-link page and no file. |
| Path traversal | Malicious `skill`, `level`, `lesson`, or signed payload shape is rejected before filesystem access. |
| No cookies | All route tests assert `Set-Cookie` is absent. |
| No persistent state | Tests use a read-only lesson fixture and verify no files are created or modified. |
| Secret configuration | App startup fails without `TICKET_SECRET`; responses never expose it. |
| Privacy | Tests check ticket parsing has no identity fields and app code has no request logging hooks. |
| Chromebook usability | Manual check of pages and downloads in a browser with default settings and JavaScript disabled. |

Also test the exact boundary at `issued_at + 3 days`, future timestamps, duplicate fields,
overlong tickets, missing lesson files, a missing catalog entry, and startup rejection for an
invalid category registry, catalog, or onboarding table.

## 11. Documentation Deliverables

Update `README.md` with:

- purpose and stateless architecture;
- the required and optional environment variables;
- how to add a category, rebuild the catalog, and publish sequence-numbered lesson files;
- how category, level, and sequence ordering works, including the absence of fixed limits;
- local run and production commands with access logging disabled;
- how `POST /sign` and the finish form turn an unsigned AI line into a bookmark;
- deployment guidance for HTTPS, read-only lesson files, and host log suppression;
- the statement that tickets are not grades and the system stores nothing about who a student
  is;
- the consequence of rotating `TICKET_SECRET`.

Add `docs/TEACHER_GUIDE.md` with:

- how to edit the Star table and add a category column in the JSON configuration;
- how to add numbered lesson files and rebuild the catalog;
- how category order, level order, and sequence order are controlled;
- how to change entry thresholds;
- what to tell students at each step;
- what to do when a student has no Star data by sending the placement lesson directly;
- that the 3-day gate is a pacing norm rather than enforcement;
- that re-running a lesson or submitting a new exit line resets the clock, which the teacher
  monitors;
- that sharing a link lets another person use that pacing window.

Update the authoring and release documents after the server contract is accepted:

- align exit-line production with destination `skill`, `level`, and exact `lesson`;
- require an unsigned token from the AI and a signed URL from the server;
- make the catalog and sequence-numbered release naming authoritative;
- extend the release checklist with server-contract validation.

## 12. Implementation Sequence

1. Add the server package, category registry, templates, and test harness.
2. Implement the catalog generator, deterministic ordering, and validation tests.
3. Implement the ticket parser, signer, verifier, and constant-time comparison tests.
4. Implement signer endpoints and the finish form.
5. Implement safe catalog-backed lesson resolution and the `/start` and `/next` paths.
6. Implement onboarding configuration, validation, and page rendering.
7. Add privacy, logging, cookie, startup, and timing regression tests.
8. Write deployment and teacher documentation.
9. Perform the full acceptance pass, including a browser check with JavaScript disabled.

## 13. Deployment Checks

- Serve only over HTTPS.
- Disable Uvicorn access logs and verify the host does not retain full request paths or client IPs.
- Mount lesson files read-only.
- Fetch and validate the GitHub lesson snapshot at startup. Do not call GitHub per request.
- Inject `TICKET_SECRET` from the host secret store, not an image, repository file, or build log.
- Confirm the server process has no writable application-data directory.
- Verify server time synchronization, since pacing is based on wall-clock UTC time.
- Smoke-test `/health`, onboarding, entry download, signing, fresh-ticket wait, mature-ticket
  download, and tampered-ticket rejection.

## 14. Explicit Non-Goals

- Accounts, logins, passwords, rosters, per-student records, or teacher progress dashboards.
- Databases, migrations, session stores, cookies, or server-side ticket consumption.
- Analytics, tracking pixels, third-party scripts, or receipt of student lesson responses.
- Grade calculation, reward tokens, encryption of the ticket payload, or access control beyond
  the signed ticket.
- Authoring or modifying lesson content as part of the server build.
