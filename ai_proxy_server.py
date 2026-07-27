"""
ai_proxy_server.py
===============================================================================
The missing piece for the MyHealth AI Analyst to actually work.

WHY THIS EXISTS
---------------
Your dashboard's AI Analyst calls `fetch('https://api.anthropic.com/v1/messages')`
directly from the browser. That call has always failed, because it needs an
API key, and a static HTML file cannot safely hold one — anyone who opened
the file and looked at "view source" would see your key and could use it to
run up charges on your account. That's exactly why the AI Analyst falls back
to `localFallbackAnswer()` and shows the "Live AI connection isn't available"
warning every time.

The fix: this script runs a tiny local web server on your own machine. It
holds your real API key (read from an environment variable, never hardcoded
or shown in any file you'd share). Your dashboard's JavaScript is patched
(by enable_ai_analyst.py) to call THIS server instead of Anthropic directly.
This server then makes the real API call on the dashboard's behalf and
returns the answer.

    Browser (dashboard.html)          This server (your machine)         Anthropic
    ─────────────────────────         ───────────────────────────       ─────────
    fetch('http://localhost:8787/    -->  reads ANTHROPIC_API_KEY   -->  api.anthropic.com
          api/analyze', {...})            adds it to the request         /v1/messages
                                           forwards everything
    <-- real Claude answer  <----------   returns the response  <-------

SETUP (one-time)
-----------------
1. Get an API key from https://console.anthropic.com/settings/keys
   (This is separate from a claude.ai subscription — it's pay-as-you-go API
   billing. A handful of dashboard questions costs a tiny fraction of a cent.)

2. Store it EITHER as a real environment variable OR in a .env file next to
   this script (pick one — you don't need both):

   Option A — .env file (recommended, persists across terminal sessions):
       Create a file named exactly ".env" in the same folder as this script,
       containing one line (no quotes, no "$env:" prefix — that's PowerShell
       syntax, not .env syntax):

           ANTHROPIC_API_KEY=sk-ant-...your-key...

       Then: pip install python-dotenv

       IMPORTANT: never upload, share, or commit this .env file anywhere.
       Add a ".env" line to any .gitignore if this folder is ever put in git.

   Option B — PowerShell environment variable (this terminal session only):
       $env:ANTHROPIC_API_KEY = "sk-ant-...your-key..."
       (must be re-run every time you open a new terminal window)

3. Install the dependencies:
       pip install requests python-dotenv

USAGE
-----
    python ai_proxy_server.py

Leave this running in its own terminal window while you use the dashboard
in your browser. Stop it with Ctrl+C when you're done. It only accepts
connections from your own machine (localhost) — nothing external can reach it.

SECURITY NOTE
-------------
If you ever paste your real key into a chat, document, or anywhere else it
could be seen by someone else (including an AI assistant), treat it as
compromised: go to console.anthropic.com/settings/keys and delete/regenerate
it immediately. Anthropic will bill any usage made with a leaked key.
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
    # Looks for a ".env" file in the same folder as this script specifically
    # (not wherever you happen to run the command from), so it works
    # regardless of your terminal's current directory.
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # .env support is optional — falls back to a real env var if unset

PORT = 8787
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

API_KEY = os.environ.get("ANTHROPIC_API_KEY")


class ProxyHandler(BaseHTTPRequestHandler):
    def _send_cors_headers(self):
        # '*' is fine here because this server only ever listens on
        # localhost and only ever forwards to Anthropic's own API — it's
        # not exposing anything sensitive to the wider internet.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        # Browsers send this "preflight" request automatically before the
        # real POST because the dashboard page (opened as a local file) and
        # this server (localhost) count as different origins. This just
        # has to answer "yes, POST with Content-Type: application/json is
        # allowed" — no actual work happens here.
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        # A simple health check — visit http://localhost:8787/ in a
        # browser to confirm the server is running and has a key loaded,
        # without needing to open the whole dashboard.
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self._send_cors_headers()
        self.end_headers()
        status = "READY (API key loaded)" if API_KEY else "NOT READY (ANTHROPIC_API_KEY not set)"
        self.wfile.write(f"MyHealth AI Analyst proxy — {status}\n".encode())

    def do_POST(self):
        if self.path != "/api/analyze":
            self.send_response(404)
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(b'{"error": "Unknown endpoint"}')
            return

        if not API_KEY:
            self.send_response(500)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "error": "ANTHROPIC_API_KEY is not set on the server. "
                         "Set it and restart ai_proxy_server.py — see the "
                         "setup instructions at the top of this script."
            }).encode())
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self.send_response(400)
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(b'{"error": "Invalid JSON in request body"}')
            return

        try:
            upstream = requests.post(
                ANTHROPIC_URL,
                headers={
                    "x-api-key": API_KEY,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                json=payload,
                timeout=60,
            )
        except requests.exceptions.RequestException as exc:
            self.send_response(502)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"Could not reach Anthropic API: {exc}"}).encode())
            return

        self.send_response(upstream.status_code)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(upstream.content)

    def log_message(self, format, *args):
        # Quieter console output — just show the essentials per request.
        print(f"[{self.log_date_time_string()}] {self.command} {self.path} -> "
              f"{args[1] if len(args) > 1 else ''}")


def main():
    if not API_KEY:
        print("=" * 70)
        print("WARNING: ANTHROPIC_API_KEY environment variable is not set.")
        print("The server will start, but every AI Analyst question will fail")
        print("until you set it. See the setup instructions at the top of")
        print("this script (ai_proxy_server.py) for exactly how.")
        print("=" * 70)
    else:
        masked = API_KEY[:10] + "..." + API_KEY[-4:] if len(API_KEY) > 14 else "***"
        print(f"API key loaded ({masked})")

    server = ThreadingHTTPServer(("localhost", PORT), ProxyHandler)
    print(f"MyHealth AI Analyst proxy running at http://localhost:{PORT}")
    print(f"Health check: http://localhost:{PORT}/")
    print("Leave this window open while using the dashboard. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        server.shutdown()
        sys.exit(0)


if __name__ == "__main__":
    main()
