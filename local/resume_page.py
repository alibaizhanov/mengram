"""The page behind `mengram resume --open`: correct the card, pick another task,
copy the context for a new session. A local server for as long as the page
is open, nothing leaves the machine."""
from __future__ import annotations

import html
import json
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from local import resume


def _page(cwd, key: str | None) -> str:
    cards = resume.all_cards()
    current, relation = (resume.load(key), "chosen") if key else resume.load_for(cwd)
    e = html.escape
    rows = []
    for c in cards:
        mark = " ◀" if current and c.get("key") == current.get("key") else ""
        rows.append(f'<li><a href="/?key={e(c["key"])}">{e(c.get("task") or "(no task line)")}</a> '
                    f'<small>{e(c.get("remote") or c.get("root") or "")} · {e(c.get("branch") or "")} · '
                    f'{e(resume._age(c.get("ts")))}{"" if c.get("draft") else " · confirmed"}</small>{mark}</li>')
    if not current:
        body = "<p>No card yet. A card is written when an agent stops working in a repository with the hooks installed.</p>"
        block = ""
    else:
        block = resume.render(current, cwd, relation if relation in ("exact", "other-branch") else "exact")
        body = f"""
<form method="post" action="/confirm">
<input type="hidden" name="key" value="{e(current['key'])}">
<p><b>Task</b> {'<span class=draft>agent draft</span>' if current.get('draft') else '<span class=ok>confirmed</span>'}<br>
<input name="task" value="{e(current.get('task') or '')}" size="90"></p>
<p><b>Done</b> (one per line)<br><textarea name="done" rows="5" cols="90">{e(chr(10).join(current.get('done') or []))}</textarea></p>
<p><b>Remaining</b> (one per line)<br><textarea name="remaining" rows="5" cols="90">{e(chr(10).join(current.get('remaining') or []))}</textarea></p>
<p><button type="submit">Confirm this state</button> <small>Confirmed text is kept; the agent will not redraft it.</small></p>
</form>
<p><b>Record</b> (verbatim, not editable): last check {e(json.dumps(current.get('last_check') or {}, ensure_ascii=False))}<br>
files: {e(', '.join((current.get('files') or [])[-10:]))}</p>
<p><button onclick="navigator.clipboard.writeText(document.getElementById('ctx').textContent)">Copy context for a new session</button></p>
<pre id="ctx">{e(block)}</pre>
"""
    return f"""<!doctype html><meta charset="utf-8"><title>mengram resume</title>
<style>body{{font:14px/1.4 -apple-system,Segoe UI,sans-serif;max-width:900px;margin:32px auto;padding:0 16px;color:#222}}
pre{{background:#f6f6f6;padding:12px;white-space:pre-wrap}} .draft{{color:#b35}} .ok{{color:#284}} small{{color:#666}} li{{margin:4px 0}}</style>
<h2>mengram resume</h2>
<p><small>Cards live in {e(str(resume.directory()))}. Repository here: {e(resume.repo_info(cwd).get('remote') or str(cwd))}.</small></p>
{body}
<h3>All cards</h3><ul>{''.join(rows) or '<li>none</li>'}</ul>
"""


class _Handler(BaseHTTPRequestHandler):
    cwd = "."

    def log_message(self, *a):
        pass

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        self._send(_page(self.cwd, (q.get("key") or [None])[0]))

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        form = urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8"))
        key = (form.get("key") or [""])[0]
        card = resume.load(key)
        if card and self.path == "/confirm":
            resume.confirm(card, task=(form.get("task") or [""])[0],
                           done=(form.get("done") or [""])[0].splitlines(),
                           remaining=(form.get("remaining") or [""])[0].splitlines())
            resume.save(card)
        self.send_response(303); self.send_header("Location", f"/?key={urllib.parse.quote(key)}"); self.end_headers()

    def _send(self, body: str):
        data = body.encode("utf-8")
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)


def serve(cwd, port: int = 0, open_browser: bool = True) -> str:
    """Serve the page on localhost until Ctrl-C. Returns the URL."""
    _Handler.cwd = str(cwd)
    srv = HTTPServer(("127.0.0.1", port), _Handler)
    url = f"http://127.0.0.1:{srv.server_port}/"
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return url
