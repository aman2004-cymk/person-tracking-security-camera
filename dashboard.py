"""Loopback-only dashboard for reviewing locally stored security events."""
from __future__ import annotations

import argparse
import csv
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

PROJECT_DIR = Path(__file__).resolve().parent
ALERT_DIR = PROJECT_DIR / "alerts"

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Local Security Event Review</title>
<style>
body{font:16px system-ui,sans-serif;background:#101827;color:#e5e7eb;margin:0}
main{max-width:1100px;margin:2rem auto;padding:0 1rem}
h1{margin-bottom:.2rem}.notice{color:#fbbf24}
.event{display:grid;grid-template-columns:180px 1fr;gap:1rem;padding:1rem 0;border-top:1px solid #374151}
img{max-width:180px;max-height:160px;object-fit:contain;background:#030712}
.meta{color:#9ca3af;font-size:.9rem}.badge{font-weight:700}
@media(max-width:600px){.event{grid-template-columns:1fr}img{max-width:100%}}
</style>
</head>
<body><main>
<h1>Local Security Event Review</h1>
<p class="notice">Local workstation only. Labels and recognition are unverified indications for human review.</p>
<p id="state" class="meta">Loading events…</p>
<section id="events"></section>
</main>
<script>
async function refresh(){
  const state=document.getElementById('state');
  try{
    const response=await fetch('/api/events',{cache:'no-store'});
    if(!response.ok)throw new Error('HTTP '+response.status);
    const events=await response.json();
    const root=document.getElementById('events');
    root.replaceChildren();
    state.textContent=`${events.length} recent event(s) · refreshed ${new Date().toLocaleTimeString()}`;
    if(!events.length){root.textContent='No local alert or incident records yet.';return;}
    for(const event of events){
      const article=document.createElement('article');article.className='event';
      if(event.image_file){
        const image=document.createElement('img');image.alt='Saved event evidence';
        image.src='/evidence/'+encodeURIComponent(event.image_file);article.append(image);
      }
      const details=document.createElement('div');
      const title=document.createElement('strong');title.className='badge';
      title.textContent=`${event.event_type} · ${event.status||'STATUS NOT RECORDED'}`;
      const summary=document.createElement('p');
      summary.textContent=`Track #${event.track_id||'?'} · ${event.identity||'Unknown identity'}`;
      const metadata=document.createElement('p');metadata.className='meta';
      metadata.textContent=`${event.timestamp||'Unknown time'}${event.action?' · '+event.action:''}`;
      details.append(title,summary,metadata);article.append(details);root.append(article);
    }
  }catch(error){state.textContent='Could not load local events: '+error.message;}
}
refresh();setInterval(refresh,5000);
</script>
</body></html>"""


def load_events(alert_dir: Path, limit: int = 100) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    for filename, event_type in (("alerts_", "AUTOMATIC ALERT"), ("incidents_", "OPERATOR INCIDENT")):
        for path in alert_dir.glob(f"{filename}*.csv"):
            with path.open(newline="", encoding="utf-8") as file:
                for row in csv.DictReader(file):
                    events.append({**row, "event_type": event_type})
    events.sort(key=lambda event: event.get("timestamp", ""), reverse=True)
    return events[:limit]


class DashboardHandler(BaseHTTPRequestHandler):
    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self'; connect-src 'self'",
        )
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        request = urlsplit(self.path)
        if request.path == "/":
            self._send(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))
        elif request.path == "/api/events":
            payload = json.dumps(load_events(ALERT_DIR), ensure_ascii=False).encode("utf-8")
            self._send(200, "application/json; charset=utf-8", payload)
        elif request.path.startswith("/evidence/"):
            filename = unquote(request.path.removeprefix("/evidence/"))
            if Path(filename).name != filename or not filename.lower().endswith(".jpg"):
                self._send(404, "text/plain; charset=utf-8", b"Not found")
                return
            image_path = ALERT_DIR / filename
            try:
                contents = image_path.read_bytes()
            except FileNotFoundError:
                self._send(404, "text/plain; charset=utf-8", b"Not found")
                return
            self._send(200, "image/jpeg", contents)
        else:
            self._send(404, "text/plain; charset=utf-8", b"Not found")

    def log_message(self, format: str, *args: object) -> None:
        print(f"dashboard: {format % args}")


class LocalDashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> None:
    parser = argparse.ArgumentParser(description="Review local alerts and operator incident records.")
    parser.add_argument("--port", type=int, default=8765, help="loopback HTTP port (default: 8765)")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    server = LocalDashboardServer(("127.0.0.1", args.port), DashboardHandler)
    print(f"Local dashboard: http://127.0.0.1:{args.port}/ (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping local dashboard.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
