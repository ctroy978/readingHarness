# Stateless Reading Harness Server Build Plan

Updated: 2026-10-25
Status: Implemented

This is the build record for the stateless Starlette service that distributes lesson JSON files,
supports Band onboarding, and enforces a one-day pacing gate. It describes the code in
`server/` as it runs today. The lesson authoring contract is owned by
`readJson/README.md` and `readJson/DESIGN_RULES.md`.

## 1. Outcome

The service has seven public behaviors:

1. `GET /` shows the reading Band and lesson-type selectors plus student instructions.
2. `GET /start?band=<grade>.<stage>&skill=<category>` downloads the exact entry lesson mapped for
   that Band and category.
3. `POST /sign` turns an unsigned AI completion line into a signed, bookmarkable `/next` URL.
4. `GET /finish` and `POST /finish` give a no-JavaScript form for the same handoff.
5. `GET /next?t=<ticket>` either explains when the next lesson opens or downloads it.
6. `GET /resume?h=<hold-ticket>` re-evaluates a held student after a curriculum gap is filled.
7. `GET /health` returns only `{"ok": true}`.

The service stores no accounts, progress, sessions, cookies, or request history. Signed tickets
carry the complete location and pacing state.

## 2. Contract Decisions

1. **Lesson identity is the Band contract.** Release files are named
   `<category>_band_<grade>_<stage>_<sequence>.json`. The grade is the whole-number Star IRL
   (1-12), the stage is 1-8, and the sequence is 0001-0008. The catalog ID is the filename
   without `.json`, and a present `lesson_id` must equal it.

2. **The server owns route selection.** The AI prints only
   `v1|from=<lesson_id>|done=<n>|got_there=<n>` (plus `|stop=1` when stopping). The server
   validates the counts, resolves the destination against `curriculum.json`, and signs the next
   link. A stopped session returns the current lesson. While the set is `incomplete`, the server
   serves the next lesson that exists in the band and holds at the last one. It does not follow
   the compiled `advance` or `retry` fields. The AI never names a destination and never signs.

3. **Routing is authored, progression is computed.** Each lesson stores three fallback
   destinations, but in a complete curriculum set the server derives the next lesson from the
   set's `expected_sequences` and the mastery rule. Authored fallbacks decide only when the
   protocol selects the `authored_route` policy for an incomplete set.

4. **Mastery is `got_there >= advance_min_got_there`.** `protocol.json` sets the threshold to 4.
   `got_there` is `unaided + helped`; mercy and open do not count. A stopped session returns the
   current lesson and does not move the student.

5. **Incomplete sets hold and alert.** `curriculum.json` marks each set `complete` or
   `incomplete`. With the default `hold_and_alert` policy, earlier lessons in a short set still
   move to the next file that exists. A student who reaches the last authored lesson is held in
   place, the server emails `ALERT_EMAIL_TO` through Resend with the category, Band, current
   lesson, missing sequences, and an opaque hold ID, and the signed `/resume` link re-checks the
   set later. Adding the next lesson and restarting serves that lesson from the same ticket
   while the set stays `incomplete`. A set marked `complete` is rejected at startup unless every
   expected lesson exists.

6. **The signed ticket names an exact catalog lesson, not a path.** Its payload is
   `v0|skill=<category>|level=<irl>|lesson=<catalog-id>|issued=<UTC>|available=<UTC>|nonce=<random>`
   signed with HMAC-SHA256 as `<payload>.<hex digest>`. `skill`, `level`, and `lesson` must agree
   in the catalog. The server replaces client-supplied time and nonce, so a caller cannot
   backdate a link or skip the pacing gate. `/sign` is unauthenticated by design; access control
   is not a goal.

7. **One-day pacing is a norm, not enforcement.** The server stamps `available_at` one day after
   signing, except that a stopped session returns the current lesson immediately. Re-clicking a
   bookmark is idempotent: no ticket is consumed and no server state changes.

8. **Every entry point is explicit configuration.** `onboarding.json` maps each student-facing
   Band key to exact lesson IDs per category. Adding another sequence never changes the entry
   point. An empty `entries` object renders "Lesson not available" rather than substituting a
   different Band.

## 3. Layout

```text
readingHarness/
|-- server/
|   |-- app.py              # Starlette routes and server-rendered pages
|   |-- alerts.py           # Resend curriculum-gap email
|   |-- catalog.py          # filename parsing, catalog, route policies
|   |-- config.py           # environment, categories, onboarding, startup checks
|   |-- github.py           # one-shot repository archive fetch
|   |-- lessons.py          # compiler: source + protocol -> runtime lesson
|   |-- progression.py      # curriculum manifest, mastery and step-down logic
|   |-- tickets.py          # completion-line parser, HMAC tickets, hold tickets
|   `-- config/
|       |-- categories.json
|       |-- onboarding.json
|       `-- protocol.json
|-- scripts/build_lessons.py
|-- tests/
`-- docs/
    |-- LESSON_AUTHORING_RULESET.md
    |-- LESSON_RELEASE_CHECKLIST.md
    |-- SERVER_BUILD_PLAN.md
    `-- TEACHER_GUIDE.md
```

Use Starlette, Uvicorn, and the Python standard library. No database, ORM, session library,
analytics package, Jinja2, or third-party browser script is used. Pages are server-rendered with
escaping.

## 4. Configuration

Read configuration once at startup and fail closed on invalid values. `.env` is loaded from the
working directory, then the process environment; existing environment values win.

| Variable | Requirement |
| --- | --- |
| `TICKET_SECRET` | Required. At least 32 bytes. Used for HMAC signing; never logged or returned. |
| `GITHUB_REPO` | Required in deployment. Repository containing lesson files. |
| `GITHUB_TOKEN` | Required with `GITHUB_REPO`. Fine-grained read-only Contents access. |
| `GITHUB_REF` | Optional branch, tag, or commit. Defaults to `main`. |
| `GITHUB_LESSON_PATH` | Optional directory inside the repository. Production uses `lessons`. |
| `LESSON_DIR` | Optional local development fallback when `GITHUB_REPO` is unset. |
| `PUBLIC_BASE_URL` | Required in deployment. Absolute base URL for signed links; falls back to `RENDER_EXTERNAL_URL` and then `http://127.0.0.1:8000`. |
| `DISPLAY_TIMEZONE` | Optional IANA timezone for the wait page. Defaults to `UTC`. |
| `CONFIG_DIR` | Optional. Defaults to `server/config`. |
| `RESEND_API_KEY`, `RESEND_FROM`, `ALERT_EMAIL_TO` | Optional. Enable curriculum-gap email. Alerts are skipped when unset. |
| `RESEND_REPLY_TO` | Optional reply address on gap alerts. |

Startup rejects a missing or short secret, a non-absolute public URL, an unknown timezone, a
missing or invalid category registry, a lesson outside the category allowlist or Band ranges, a
route to a missing lesson or mismatched IRL grade, a draft runtime lesson, an invalid
`curriculum.json`, a complete set with missing lessons, and an onboarding row pointing at a
missing lesson or the wrong category.

## 5. Category Registry and Onboarding

`categories.json` assigns each category a permanent lowercase key, a display label, and a unique
numeric order. Keys must match `^[a-z][a-z0-9_-]*$`. The registry is the allowlist for lesson
files, onboarding entries, and compiler skill instructions.

`onboarding.json` holds ordered `irl_rows`. Each row has a key such as `5.1`, a student-facing
label `Band 5.1`, and an `entries` object mapping a category key to the exact entry lesson ID.
Rows with no entry for a category show "Lesson not available." Bands are never sorted or
substituted at request time.

## 6. Startup Lesson Catalog

In deployment the server downloads the GitHub repository archive once at startup, reads lesson
files directly under `GITHUB_LESSON_PATH`, and keeps the validated bytes in memory. In local
development `LESSON_DIR` may provide the same files from disk. Requests never call GitHub and
never scan a directory.

The catalog loader:

1. scans only regular files directly inside the lesson source;
2. accepts names matching
   `^(?P<skill>[a-z][a-z0-9_-]*)_band_(?P<irl>\d{1,2})_(?P<stage>[1-8])_(?P<sequence>\d{4})\.json$`;
3. requires the category to exist in `categories.json` and the grade to be 1-12;
4. requires a present `lesson_id` to equal the filename stem, and schema-v3 `meta.skill` to match;
5. rejects duplicate lesson IDs and `meta.status == "draft"`;
6. parses `rules.server_routes` for every schema-v3 lesson and rejects a route whose destination
   is missing or whose catalog IRL grade differs from `next_irl`;
7. groups lessons by `(skill, grade)` and sorts them by stage then sequence;
8. stores only validated bytes and metadata in memory.

A failed or invalid fetch prevents startup rather than serving a partial catalog. Updating
lessons requires a push and a restart or redeploy.

## 7. Curriculum and Progression

`lessons/curriculum.json` (version 1) declares `target_set_size` and a `sets` list:

```json
{"skill": "inference", "grade": 5, "stage": 1,
 "expected_sequences": [1, 2, 3, 4, 5, 6, 7, 8], "status": "incomplete"}
```

Validation rejects an unknown skill, a grade outside 1-12, a stage outside 1-8, a status other
than `complete`/`incomplete`, duplicate or empty sequence lists, duplicate set keys, and any
lesson that exists outside its set's declared sequences. A `complete` set must contain every
expected lesson, and its runnable lessons must equal the declared set.

`server/progression.py` resolves the next lesson in this order:

1. A stopped session returns the current lesson.
2. The current lesson must belong to a declared set.
3. If the set is incomplete, apply `on_incomplete_set`. `hold_and_alert` serves the next lesson
   that exists in the band and holds at the frontier. `authored_route` uses the lesson's fallback
   destination.
4. If mastery and a later set exists, go to that set's first expected sequence. If the later set
   is incomplete, hold or enter its first available lesson according to policy.
5. If no mastery and the current sequence is not last in the set, go to the next expected
   sequence in the same set.
6. If no mastery on the last sequence, step down to the middle of the previous set
   (`expected_sequences[(len - 1) // 2]`). If the current set is the first for the category, the
   previous set is the last authored set of the grade below.
7. At the bottom of the catalog, restart the current set at its first sequence.
8. At the ceiling, hold on the current lesson when `on_ceiling` is `hold`.

The server keeps no cross-request counter. It derives everything from the ticket's lesson and
the startup manifest.

## 8. Ticket Module

### 8.1 AI completion line

```text
v1|from=<lesson_id>|done=<completed-items>|got_there=<unaided-or-helped>
v1|from=<lesson_id>|done=<completed-items>|got_there=<unaided-or-helped>|stop=1
```

`from` must be a catalog lesson with a stored route policy. `done` and `got_there` must be
non-negative integers, `done <= item_count`, and `got_there <= done`. A legacy `v0` line with
`skill`, `level`, and `lesson` is still accepted, but it has no outcome counts, so it records
`got_there = 0` and cannot claim mastery.

### 8.2 Signed lesson ticket

```text
v0|skill=<category>|level=<irl>|lesson=<catalog-id>|issued=<UTC>|available=<UTC>|nonce=<token>
```

- `skill` must be in the category registry.
- `level` must be a positive integer and match the catalog lesson.
- `lesson` must be an exact catalog ID and match `skill` and `level`.
- `issued` and `available` are UTC, second precision, trailing `Z`; `available >= issued`.
- `nonce` comes from `secrets.token_urlsafe(12)` and carries no student data.
- Reject missing, duplicate, unknown, overlong, or out-of-order fields. The whole ticket is
  limited to 512 characters.

Sign with `HMAC-SHA256(secret, payload_bytes)`, hex-encode, and format `<payload>.<signature>`.
Verify by splitting at the final `.` and comparing with `hmac.compare_digest`. URL-encode the
ticket in `/next?t=...`; the HMAC covers the unencoded payload. Do not encrypt the payload and do
not add an expiry claim.

### 8.3 Hold ticket

A hold ticket is
`v0|hold=1|from=<lesson>|done=<n>|got_there=<n>|stop=<0|1>|issued=<UTC>|nonce=<token>`
signed the same way. It carries the completion record so `/resume` can re-run progression after
the curriculum changes. The hold ID in the gap email is the first 16 hex characters of a SHA-256
of the hold ticket, so it identifies the hold without exposing the ticket.

## 9. Route Behavior

### `GET /`

Render the onboarding page from validated configuration: the reading Band selector, the
lesson-type selector (all configured categories), numbered student instructions, and the
"Do not share your link" note. Escape all configured text. No analytics, third-party scripts,
cookies, or local storage.

### `GET /start?band=<grade>.<stage>&skill=<category>`

- Validate the Band key against `onboarding.json` and the skill against the category registry.
- Return HTTP 400 with the plain invalid-link page for invalid input.
- Serve the exact lesson ID mapped for that Band and category.
- Return "Lesson not available" when the mapping is empty.
- A request without `band` is invalid; later lessons are reachable only through signed links.
- Serve the catalog's stored bytes with `Content-Type: application/json`,
  `Content-Disposition: attachment; filename="<catalog filename>"`, and
  `X-Content-Type-Options: nosniff`.

### `POST /sign`

- Accept a bounded JSON body `{"line": "<unsigned completion line>"}` (4096-byte limit).
- Parse and validate the line, resolve progression, and return
  `{"url": "<PUBLIC_BASE_URL>/next?t=<signed-ticket>"}` or a hold URL.
- Stamp current UTC time and a fresh nonce; discard any client-supplied values.
- Return a small JSON error for malformed input; never include request contents.
- Never log the body, ticket, or client IP.

### `GET /finish` and `POST /finish`

- Serve a plain form for pasting the AI's completion line.
- On submit, call the same signer and render the signed URL as a clickable link.
- Use POST and no cookies, local storage, or browser-side state.
- Show a concise invalid-input message when signing fails.

### `GET /next?t=<ticket>`

1. Verify length, payload shape, HMAC, skill, level, lesson ID, timestamps, and nonce.
2. On any failure, return the plain invalid-link page with HTTP 400.
3. Confirm the catalog lesson matches `skill` and `level`.
4. If `now < available_at`, return the "Not yet" page with HTTP 200 and the local time in
   `DISPLAY_TIMEZONE`. Do not serve the file.
5. Otherwise serve the lesson attachment.
6. A missing catalog entry or file returns the same invalid-link page.

The endpoint is idempotent. Re-clicking before the gate repeats the wait page; after it opens,
the file is served every time.

### `GET /resume?h=<hold-ticket>`

Verify the hold ticket, re-run progression against the current catalog and curriculum, and
either serve a new signed next link or show the hold page again. This is how a held student
continues after missing lessons are published and the set is marked complete.

### `GET /health`

Return only `{"ok": true}`. Do not add version, environment, path, secret, or diagnostic details.

## 10. Privacy and Logging

- Run Uvicorn with access logging disabled, for example `--no-access-log`.
- Do not add middleware that logs paths, query strings, bodies, headers, or client addresses.
- Do not enable debug tracebacks or vendor error reporting that captures requests.
- Do not set cookies or use server sessions; tests assert no `Set-Cookie` header appears.
- Keep tickets free of names, emails, student IDs, IP addresses, and score history.
- Document that the host may retain request metadata outside the app's control.
- Treat lesson files as read-only input and write no application state to disk.
- Rotating `TICKET_SECRET` invalidates every bookmark and hold link; treat it as break-glass.

## 11. Tests and Acceptance Mapping

Tests use a temporary lesson fixture, a test secret, and fixed clocks. The suite currently has
41 passing tests. Coverage includes:

| Acceptance item | Verification |
| --- | --- |
| Onboarding | Band and category selectors render; empty entries show "Lesson not available"; no third-party scripts. |
| Entry download | `/start?band=5.1&skill=inference` serves the mapped lesson; bad Band or skill returns 400. |
| Band identity | Catalog rejects malformed names, unknown categories, grades outside 1-12, and filename/`lesson_id` mismatch. |
| Routes | Every compiled route target must exist and match `next_irl`; missing targets fail startup. |
| Curriculum | Complete sets must be whole; lessons outside `expected_sequences` fail startup. |
| Signing | A valid `v1` line returns a signed URL; unknown lessons, bad counts, and tampering fail. |
| Fresh ticket | Fixed clock returns 200 "Not yet", the exact availability time, and no file. |
| Mature ticket | A fixed clock at or beyond `available_at` serves the file; repeat requests still serve it. |
| Hold and resume | An incomplete set frontier holds, sends a gap alert when configured, and resumes once the set is complete. |
| Step-down | The last lesson without mastery moves to the middle of the previous set. |
| No cookies | Every route test asserts `Set-Cookie` is absent. |
| Secret configuration | Startup fails without a 32-byte `TICKET_SECRET`; responses never expose it. |
| Privacy | Ticket parsing has no identity fields; no request logging hooks exist. |

## 12. Documentation Deliverables

- `README.md`: purpose, environment variables, GitHub lesson repository, adding categories and
  lessons, Band onboarding, progression and holds, build commands, deployment, privacy.
- `docs/LESSON_AUTHORING_RULESET.md`: schema-v3 source contract, item shapes, Band naming,
  `next_irl` routes, curriculum manifest, review gates.
- `docs/LESSON_RELEASE_CHECKLIST.md`: release gate for one lesson source.
- `docs/TEACHER_GUIDE.md`: teacher-facing operation: Band labels, start and finish flow, pacing,
  holds and gap alerts, privacy.
- `readJson/README.md` and `readJson/DESIGN_RULES.md`: authoritative lesson content contract.

## 13. Deployment Checks

- Serve only over HTTPS and set `PUBLIC_BASE_URL` explicitly when using a custom domain.
- Disable Uvicorn access logs and verify the host does not retain full request paths or client IPs.
- Inject `TICKET_SECRET` from the host secret store, never from an image, repository file, or
  build log.
- Fetch and validate the GitHub lesson snapshot at startup; do not call GitHub per request.
- Mount no writable application-data directory.
- Verify server clock synchronization, since pacing uses wall-clock UTC.
- Smoke-test `/health`, onboarding, entry download, signing, fresh-ticket wait, mature-ticket
  download, tampered-ticket rejection, a held resume, and a gap alert.

## 14. Explicit Non-Goals

- Accounts, logins, passwords, rosters, per-student records, or teacher progress dashboards.
- Databases, migrations, session stores, cookies, or server-side ticket consumption.
- Analytics, tracking pixels, third-party scripts, or receipt of student lesson responses.
- Grade calculation, reward tokens, encryption of the ticket payload, or access control beyond
  the signed ticket.
- Authoring or modifying lesson content as part of the server build.
