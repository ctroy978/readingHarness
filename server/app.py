from __future__ import annotations

import html
import json
from datetime import timedelta
from urllib.parse import parse_qs, quote

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from server.config import Config, load_config
from server.tickets import TicketError, make_ticket, parse_unsigned_line, verify_ticket

PACE = timedelta(days=3)

STYLE = """
body{font:17px/1.5 system-ui,sans-serif;max-width:880px;margin:2rem auto;padding:0 1rem;color:#18202a}
table{border-collapse:collapse;width:100%;margin:1rem 0}th,td{border:1px solid #aab3bd;padding:.55rem;text-align:left}
a{color:#064f8c}code,textarea{font:14px/1.4 ui-monospace,monospace}textarea{width:100%;min-height:8rem}
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
    lesson = parse_unsigned_line(line, config.lesson)
    ticket = make_ticket(lesson, config.secret)
    return f"{config.public_base_url}/next?t={quote(ticket, safe='')}"


def create_app(config: Config | None = None) -> Starlette:
    config = config or load_config()

    async def onboarding(request: Request) -> HTMLResponse:
        headings = "".join(
            f"<th>{html.escape(category.label)}</th>" for category in config.categories
        )
        rows = []
        for row in config.star_rows:
            cells = []
            for category in config.categories:
                level = dict(row.entries).get(category.key)
                if level is None:
                    cells.append("<td></td>")
                else:
                    href = f"/start?skill={quote(category.key)}&amp;level={level}"
                    cells.append(
                        f'<td><a href="{href}">Start {html.escape(category.label)} L{level}</a></td>'
                    )
            rows.append(f"<tr><th>{html.escape(row.label)}</th>{''.join(cells)}</tr>")
        return _page(
            "Reading program",
            f"""<p>This is a reading program. You will get one lesson at a time.</p>
<table><thead><tr><th>Star score</th>{headings}</tr></thead><tbody>{''.join(rows)}</tbody></table>
<h2>What to do</h2><ol>
<li>Find your Star score in the table. Click the link in your row.</li>
<li>A file will download. Save it or remember where it went.</li>
<li>Open your AI. Upload the file. Type <code>begin</code>.</li>
<li>When the lesson ends, the AI will give you a ticket line. <a href="/finish">Paste it on the finish page</a>.</li>
<li>Bookmark the link you get there. That bookmark is how you get your next lesson.</li>
</ol><p class="note">Do not share your link. It is yours.</p>""",
        )

    async def start(request: Request):
        skill = request.query_params.get("skill")
        level = request.query_params.get("level")
        try:
            parsed_level = int(level or "")
        except ValueError:
            return _invalid()
        if not skill or parsed_level < 1:
            return _invalid()
        lesson = config.entry(skill, parsed_level)
        if lesson is None:
            return _invalid()
        return _serve(config, lesson)

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
        except (json.JSONDecodeError, TicketError):
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
        except TicketError:
            return _finish_form("That ticket line is not valid.", 400)

    async def next_lesson(request: Request):
        t = request.query_params.get("t")
        if not t:
            return _invalid()
        try:
            ticket = verify_ticket(t, config.secret, config.lesson)
        except TicketError:
            return _invalid()
        available = ticket.issued_at + PACE
        from datetime import datetime, timezone

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
