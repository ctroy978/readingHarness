from __future__ import annotations

import html
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, quote

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from server.config import Config, load_config
from server.tickets import (
    TicketError,
    choose_route,
    make_ticket,
    parse_completion_line,
    verify_ticket,
)

STYLE = """
body{font:17px/1.5 system-ui,sans-serif;max-width:880px;margin:2rem auto;padding:0 1rem;color:#18202a}
a{color:#064f8c}code,textarea{font:14px/1.4 ui-monospace,monospace}textarea{width:100%;min-height:8rem}
label{display:block;font-weight:600;margin:.9rem 0 .3rem}select{width:100%;max-width:32rem;font:inherit;padding:.55rem}
button{font:inherit;padding:.55rem 1rem;margin-top:1rem}
.note{background:#f2f5f7;border-left:4px solid #60758a;padding:.7rem 1rem}
""".strip()


def _page(title: str, body: str) -> HTMLResponse:
    document = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>{STYLE}</style></head><body><h1>{html.escape(title)}</h1>{body}</body></html>"""
    return HTMLResponse(document)


def _invalid(status: int = 400) -> HTMLResponse:
    response = _page(
        "Link not valid",
        "<p>This link is not valid. Ask your teacher for your start link.</p>",
    )
    response.status_code = status
    return response


def _unavailable() -> HTMLResponse:
    return _page(
        "Lesson not available",
        '<p>Lesson not available for that Star score and lesson type.</p><p><a href="/">Choose another lesson</a></p>',
    )


def _finish_form(message: str = "", status: int = 200) -> HTMLResponse:
    notice = f"<p><strong>{html.escape(message)}</strong></p>" if message else ""
    response = _page(
        "Finish a lesson",
        f"""{notice}<p>Paste the ticket line your AI gave you.</p>
<form method="post" action="/finish"><textarea name="line" required></textarea>
<p><button type="submit">Get my next link</button></p></form>
<p><a href="/">Back to the start</a></p>""",
    )
    response.status_code = status
    return response


def _signed_page(url: str) -> HTMLResponse:
    escaped = html.escape(url, quote=True)
    return _page(
        "Your next lesson link",
        f"""<p><a href="{escaped}">Open or bookmark this link</a></p>
<p class="note">Bookmark this link. It is how you get your next lesson.</p>""",
    )


def _serve(config: Config, lesson, status_on_missing: int = 404) -> Response | HTMLResponse:
    content = config.lesson_bytes.get(lesson.filename)
    if content is None:
        return _invalid(status_on_missing)
    return Response(
        content,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{lesson.filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


def _sign_line(config: Config, line: str) -> str:
    completion = parse_completion_line(line, config.lesson, config.policies)
    policy = config.policies[completion.lesson.id]
    routing = config.protocol["routing"]
    route = choose_route(completion, policy, routing["advance_min_got_there"])
    lesson = config.lesson(route.next_file)
    if lesson is None:
        raise TicketError("Unknown lesson")
    now = datetime.now(timezone.utc)
    pace_days = config.protocol["pace_days"]
    available_at = now if completion.stopped else now + timedelta(days=pace_days)
    ticket = make_ticket(
        lesson,
        config.secret,
        issued_at=now,
        available_at=available_at,
    )
    return f"{config.public_base_url}/next?t={quote(ticket, safe='')}"


def create_app(config: Config | None = None) -> Starlette:
    config = config or load_config()

    async def onboarding(request: Request) -> HTMLResponse:
        score_options = "".join(
            f'<option value="{html.escape(row.key, quote=True)}">{html.escape(row.label)}</option>'
            for row in config.star_rows
        )
        type_options = "".join(
            f'<option value="{html.escape(category.key, quote=True)}">{html.escape(category.label)}</option>'
            for category in config.categories
        )
        return _page(
            "Reading program",
            f"""<p>This is a reading program. You will get one lesson at a time.</p>
<form method="get" action="/start">
<label for="star">Star score</label><select id="star" name="star" required>{score_options}</select>
<label for="skill">Lesson type</label><select id="skill" name="skill" required>{type_options}</select>
<button type="submit">Find my starting lesson</button>
</form>
<h2>What to do</h2><ol>
<li>Choose your Star score and lesson type. Your starting lesson will download.</li>
<li>Open your AI. Upload the file. Type <code>begin</code>.</li>
<li>When the lesson ends, the AI will give you a ticket line. <a href="/finish">Paste it on the finish page</a>.</li>
<li>The server will choose your next lesson and show when it unlocks. Bookmark the link. Do not edit it.</li>
</ol><p class="note">Do not share your link. It is yours.</p>""",
        )

    async def start(request: Request):
        skill = request.query_params.get("skill")
        star = request.query_params.get("star")
        if star is not None:
            row = config.star_row(star)
            if row is None or not skill or skill not in {category.key for category in config.categories}:
                return _invalid()
            lesson = config.star_entry(star, skill)
            return _serve(config, lesson) if lesson is not None else _unavailable()
        # Later lessons are reachable only through a server-selected, signed link.
        return _invalid()

    async def sign(request: Request):
        # Signing is unauthenticated by design. Callers may choose any catalog lesson,
        # but the server sets time and nonce so a ticket cannot skip the pacing gate.
        body = await request.body()
        if len(body) > 4096:
            return JSONResponse({"error": "invalid line"}, status_code=400)
        try:
            payload = json.loads(body)
            line = payload.get("line") if isinstance(payload, dict) else None
            if not isinstance(line, str):
                raise TicketError("Invalid line")
            return JSONResponse({"url": _sign_line(config, line)})
        except (json.JSONDecodeError, KeyError, TypeError, TicketError):
            return JSONResponse({"error": "invalid line"}, status_code=400)

    async def finish(request: Request) -> HTMLResponse:
        return _finish_form()

    async def finish_post(request: Request) -> HTMLResponse:
        body = await request.body()
        if len(body) > 4096:
            return _finish_form("That line is too long.", 400)
        values = parse_qs(body.decode("utf-8", "replace")).get("line", [])
        try:
            return _signed_page(_sign_line(config, values[0] if values else ""))
        except (KeyError, TypeError, TicketError):
            return _finish_form("That ticket line is not valid.", 400)

    async def next_lesson(request: Request):
        t = request.query_params.get("t")
        if not t:
            return _invalid()
        try:
            ticket = verify_ticket(t, config.secret, config.lesson)
        except TicketError:
            return _invalid()
        available = ticket.available_at
        now = datetime.now(timezone.utc)
        if now < available:
            local = available.astimezone(config.display_timezone)
            human = local.strftime("%A, %B %d, %Y at %I:%M %p %Z").replace(" 0", " ")
            iso = available.strftime("%Y-%m-%dT%H:%M:%SZ")
            return _page(
                "Not yet",
                f"""<p>Your next lesson isn't ready yet.</p>
<p>It will be available <time datetime="{iso}">{html.escape(human)}</time>.</p>
<p class="note">Bookmark this page. Come back after that time and click the same bookmark.</p>""",
            )
        return _serve(config, ticket.lesson)

    async def health(request: Request):
        return JSONResponse({"ok": True})

    return Starlette(
        routes=[
            Route("/", onboarding),
            Route("/start", start),
            Route("/sign", sign, methods=["POST"]),
            Route("/finish", finish),
            Route("/finish", finish_post, methods=["POST"]),
            Route("/next", next_lesson),
            Route("/health", health),
        ]
    )
