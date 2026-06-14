"""
proxy_server.py
===============
A tiny local HTTP server that proxies Pinecone API calls from the browser,
solving the CORS issue when running the dashboard on GitHub Pages or locally.

Run this INSTEAD of python3 -m http.server when you want full Pinecone search:

    python3 proxy_server.py

Then open: http://localhost:8080/Mortgage_IQ_Portfolio_App.html

The proxy:
  - Serves all static files (HTML, JSON) from the current directory
  - Forwards /pinecone/* requests to your Pinecone index with correct auth
  - Handles CORS headers so the browser can call it freely
  - Reads PINECONE_API_KEY and PINECONE_HOST from environment or .env file
"""

import os
import json
import http.server
import urllib.request
import urllib.parse
from pathlib import Path

# ── Load config ────────────────────────────────────────────────────────
def load_env():
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

load_env()

PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY", "")
PINECONE_HOST    = os.environ.get("PINECONE_HOST", "").rstrip("/")
VOYAGE_API_KEY   = os.environ.get("VOYAGE_API_KEY", "")
PORT             = int(os.environ.get("PORT", 8080))


class ProxyHandler(http.server.SimpleHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        if self.path.startswith("/pinecone/"):
            self._proxy_pinecone()
        elif self.path == "/smart-query":
            self._smart_query()
        elif self.path == "/embed":
            self._embed()
        else:
            self.send_error(404)

    def do_GET(self):
        # Serve static files normally
        super().do_GET()

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Api-Key, Authorization")

    def _proxy_pinecone(self):
        if not PINECONE_API_KEY or not PINECONE_HOST:
            self.send_response(500)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "error": "PINECONE_API_KEY or PINECONE_HOST not set. "
                         "Add them to your .env file or export as environment variables."
            }).encode())
            return

        # Strip /pinecone prefix and forward to real Pinecone host
        pinecone_path = self.path[len("/pinecone"):]
        target_url    = PINECONE_HOST + pinecone_path

        # Read request body
        content_len = int(self.headers.get("Content-Length", 0))
        body        = self.rfile.read(content_len) if content_len else b""

        try:
            req = urllib.request.Request(
                target_url,
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Api-Key":      PINECONE_API_KEY,
                }
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
                self.send_response(resp.status)
                self._cors()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data)

        except urllib.error.HTTPError as e:
            err_body = e.read()
            self.send_response(e.code)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(err_body)

        except Exception as ex:
            self.send_response(500)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(ex)}).encode())

    def _embed(self):
        """Embed text via Voyage Finance and return the vector."""
        content_len = int(self.headers.get("Content-Length", 0))
        body        = self.rfile.read(content_len) if content_len else b"{}"
        try:
            req_data = json.loads(body)
            texts    = req_data.get("texts") or [req_data.get("text", "")]
            vectors  = self._voyage_embed(texts, input_type="query")
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"embeddings": vectors}).encode())
        except Exception as e:
            self.send_response(500)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def _smart_query(self):
        """End-to-end: take a text query, embed via Voyage, query Pinecone, return matches."""
        content_len = int(self.headers.get("Content-Length", 0))
        body        = self.rfile.read(content_len) if content_len else b"{}"
        try:
            req_data = json.loads(body)
            query    = req_data.get("query", "").strip()
            top_k    = int(req_data.get("topK", 6))
            if not query:
                raise ValueError("Missing 'query' field")

            # Step 1 — embed query via Voyage Finance
            vectors = self._voyage_embed([query], input_type="query")
            if not vectors:
                raise RuntimeError("Voyage embedding returned no vector")

            # Step 2 — query Pinecone with the embedding
            pinecone_body = json.dumps({
                "vector":          vectors[0],
                "topK":            top_k,
                "includeMetadata": True,
                "includeValues":   False,
            }).encode()

            pc_req = urllib.request.Request(
                PINECONE_HOST + "/query",
                data=pinecone_body,
                method="POST",
                headers={"Content-Type": "application/json", "Api-Key": PINECONE_API_KEY}
            )
            with urllib.request.urlopen(pc_req, timeout=20) as pc_resp:
                pc_data = pc_resp.read()

            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(pc_data)

        except urllib.error.HTTPError as e:
            err_body = e.read()
            self.send_response(e.code)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(err_body)
        except Exception as e:
            self.send_response(500)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def _voyage_embed(self, texts, input_type="query"):
        """Call Voyage Finance embeddings API server-side."""
        if not VOYAGE_API_KEY:
            raise RuntimeError("VOYAGE_API_KEY not set in .env or environment")
        payload = json.dumps({
            "model":      "voyage-finance-2",
            "input":      texts,
            "input_type": input_type,
        }).encode()
        req = urllib.request.Request(
            "https://api.voyageai.com/v1/embeddings",
            data=payload,
            method="POST",
            headers={
                "Content-Type":  "application/json",
                "Authorization": f"Bearer {VOYAGE_API_KEY}",
            }
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
            return [item["embedding"] for item in data.get("data", [])]

    def log_message(self, fmt, *args):
        # Suppress file-serving noise, only show proxy calls
        if "/pinecone/" in (args[0] if args else ""):
            print(f"  → Pinecone proxy: {args[0]}")


def main():
    if not PINECONE_API_KEY:
        print("\n⚠  PINECONE_API_KEY not set.")
        print("   Add it to your .env file:  PINECONE_API_KEY=pcsk_...")
        print("   Or: export PINECONE_API_KEY=pcsk_...\n")

    if not PINECONE_HOST:
        print("\n⚠  PINECONE_HOST not set.")
        print("   Add it to your .env file:  PINECONE_HOST=https://mortgageiq-docs-xxxx.svc.xxx.pinecone.io")
        print("   Or: export PINECONE_HOST=https://...\n")

    print(f"\nMortgageIQ Proxy Server")
    print(f"  Dashboard : http://localhost:{PORT}/Mortgage_IQ_Portfolio_App.html")
    print(f"  Pinecone  : {PINECONE_HOST or '(not configured)'}")
    print(f"  Voyage    : {'✓ connected' if VOYAGE_API_KEY else '✗ not set — search will use hash fallback'}")
    print(f"  Press Ctrl+C to stop\n")

    server = http.server.HTTPServer(("localhost", PORT), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
