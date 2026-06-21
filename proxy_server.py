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
        elif self.path == "/ingest":
            self._ingest()
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

            print(f"  [smart-query] Query: '{query}' | topK={top_k}")

            # Step 1 — embed query via Voyage Finance
            vectors = self._voyage_embed([query], input_type="query")
            if not vectors:
                raise RuntimeError("Voyage embedding returned no vector")
            print(f"  [smart-query] Got embedding: dim={len(vectors[0])}, first5={vectors[0][:5]}")

            # Step 2 — query Pinecone with the embedding
            pinecone_body = json.dumps({
                "vector":          vectors[0],
                "topK":            top_k,
                "includeMetadata": True,
                "includeValues":   False,
            }).encode()

            print(f"  [smart-query] Querying Pinecone at {PINECONE_HOST}/query ...")
            pc_req = urllib.request.Request(
                PINECONE_HOST + "/query",
                data=pinecone_body,
                method="POST",
                headers={"Content-Type": "application/json", "Api-Key": PINECONE_API_KEY}
            )
            with urllib.request.urlopen(pc_req, timeout=20) as pc_resp:
                pc_data = pc_resp.read()
                pc_json = json.loads(pc_data)
                matches = pc_json.get("matches", [])
                print(f"  [smart-query] Pinecone returned {len(matches)} matches")
                for m in matches:
                    print(f"    score={m.get('score','?'):.4f} id={m.get('id','?')}")

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

    def _ingest(self):
        """
        Full ingestion pipeline in one endpoint:
        1. Receive PDF as base64 + metadata from browser
        2. Extract text via Claude API
        3. Chunk the text
        4. Embed chunks via Voyage Finance
        5. Upsert to Pinecone
        6. Return summary
        """
        import math, hashlib, re as _re

        content_len = int(self.headers.get("Content-Length", 0))
        body        = self.rfile.read(content_len) if content_len else b"{}"

        try:
            req_data  = json.loads(body)
            pdf_b64   = req_data.get("pdf_base64", "")
            filename  = req_data.get("filename", "document.pdf")
            doc_type  = req_data.get("doc_type", "auto")
            ticker    = req_data.get("ticker", "")

            if not pdf_b64:
                raise ValueError("Missing pdf_base64")

            print(f"  [ingest] Starting: {filename}")

            # ── Step 1: Extract text via Claude ──────────────────
            ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
            if not ANTHROPIC_KEY:
                raise RuntimeError("ANTHROPIC_API_KEY not set in .env")

            extract_payload = json.dumps({
                "model": "claude-haiku-4-5",
                "max_tokens": 4000,
                "system": "Extract all text from this PDF. Return the raw text only, preserving page structure with [Page N] markers. No commentary.",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64}},
                        {"type": "text", "text": "Extract all text from this document, marking each page with [Page N]."}
                    ]
                }]
            }).encode()

            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=extract_payload,
                method="POST",
                headers={"Content-Type": "application/json", "x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01"}
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                extract_data = json.loads(r.read())
            full_text = extract_data["content"][0]["text"]
            print(f"  [ingest] Extracted {len(full_text):,} chars")

            # ── Step 2: Extract metadata via Claude ───────────────
            meta_payload = json.dumps({
                "model": "claude-haiku-4-5",
                "max_tokens": 200,
                "system": "Return ONLY valid JSON. No markdown.",
                "messages": [{
                    "role": "user",
                    "content": 'Extract metadata from this text. Return JSON: {"title":"...","company":"...","period":"...","doc_type":"earnings|prospectus|research|mbs_deal|statement|other"}\n\n' + full_text[:2000]
                }]
            }).encode()

            req2 = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=meta_payload,
                method="POST",
                headers={"Content-Type": "application/json", "x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01"}
            )
            with urllib.request.urlopen(req2, timeout=30) as r:
                meta_data = json.loads(r.read())
            meta_text = meta_data["content"][0]["text"].strip()
            meta_text = meta_text.replace("```json","").replace("```","").strip()
            try:
                doc_meta = json.loads(meta_text)
            except Exception:
                doc_meta = {"title": filename, "company": "", "period": ""}
            if ticker:
                doc_meta["ticker"] = ticker
            if doc_type != "auto":
                doc_meta["doc_type"] = doc_type
            print(f"  [ingest] Metadata: {doc_meta.get('title','?')} | {doc_meta.get('doc_type','?')}")

            # ── Step 3: Chunk text ────────────────────────────────
            CHUNK_SIZE    = 200
            CHUNK_OVERLAP = 40
            words  = full_text.split()
            chunks = []
            start  = 0
            while start < len(words):
                end   = min(start + CHUNK_SIZE, len(words))
                chunk = " ".join(words[start:end])
                page  = None
                m = _re.search(r"\[Page (\d+)\]", chunk)
                if m:
                    page = int(m.group(1))
                if chunk.strip():
                    chunks.append({"text": chunk, "page": page or 1, "idx": len(chunks)})
                start += CHUNK_SIZE - CHUNK_OVERLAP
            print(f"  [ingest] {len(chunks)} chunks")

            # ── Step 4: Embed all chunks via Voyage ───────────────
            all_embeddings = []
            batch_size = 32
            for i in range(0, len(chunks), batch_size):
                batch_texts = [c["text"] for c in chunks[i:i+batch_size]]
                embeddings  = self._voyage_embed(batch_texts, input_type="document")
                all_embeddings.extend(embeddings)
                import time as _time
                if i + batch_size < len(chunks):
                    _time.sleep(0.3)
            print(f"  [ingest] Got {len(all_embeddings)} embeddings")

            # ── Step 5: Upsert to Pinecone ────────────────────────
            file_hash = hashlib.md5(pdf_b64[:1000].encode()).hexdigest()[:12]
            doc_id    = "doc_" + file_hash

            vectors = []
            for i, (chunk, emb) in enumerate(zip(chunks, all_embeddings)):
                vectors.append({
                    "id": doc_id + "_chunk_" + str(i),
                    "values": emb,
                    "metadata": {
                        "doc_id":    doc_id,
                        "filename":  filename,
                        "title":     str(doc_meta.get("title", filename)),
                        "company":   str(doc_meta.get("company", "")),
                        "ticker":    str(doc_meta.get("ticker", ticker)),
                        "doc_type":  str(doc_meta.get("doc_type", doc_type)),
                        "period":    str(doc_meta.get("period", "")),
                        "page":      chunk.get("page", 1),
                        "text":      chunk["text"][:1000],
                        "chunk_idx": chunk["idx"],
                    }
                })

            # Upsert in batches of 100
            pc_headers = {"Content-Type": "application/json", "Api-Key": PINECONE_API_KEY}
            upserted = 0
            for i in range(0, len(vectors), 100):
                batch = vectors[i:i+100]
                upsert_payload = json.dumps({"vectors": batch}).encode()
                pc_req = urllib.request.Request(
                    PINECONE_HOST + "/vectors/upsert",
                    data=upsert_payload,
                    method="POST",
                    headers=pc_headers
                )
                with urllib.request.urlopen(pc_req, timeout=20) as r:
                    upsert_result = json.loads(r.read())
                    upserted += upsert_result.get("upsertedCount", len(batch))

            print(f"  [ingest] Upserted {upserted} vectors")

            # ── Step 6: Update docs_library.json ─────────────────
            from pathlib import Path as _Path
            import datetime as _dt
            library_path = _Path(__file__).parent / "docs_library.json"
            try:
                library = json.loads(library_path.read_text()) if library_path.exists() else []
            except Exception:
                library = []
            library = [d for d in library if d.get("doc_id") != doc_id]
            library.append({
                "doc_id":     doc_id,
                "title":      doc_meta.get("title", filename),
                "company":    doc_meta.get("company", ""),
                "ticker":     doc_meta.get("ticker", ticker),
                "doc_type":   doc_meta.get("doc_type", doc_type),
                "period":     doc_meta.get("period", ""),
                "filename":   filename,
                "chunks":     len(chunks),
                "indexed_at": _dt.datetime.utcnow().isoformat() + "Z"
            })
            library_path.write_text(json.dumps(library, indent=2))

            result = {
                "success":  True,
                "doc_id":   doc_id,
                "title":    doc_meta.get("title", filename),
                "chunks":   len(chunks),
                "upserted": upserted,
                "doc_type": doc_meta.get("doc_type", doc_type),
                "company":  doc_meta.get("company", ""),
                "period":   doc_meta.get("period", "")
            }
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

        except urllib.error.HTTPError as e:
            err = e.read().decode()
            print(f"  [ingest] HTTPError {e.code}: {err[:200]}")
            self.send_response(e.code)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"HTTP {e.code}: {err[:200]}"}).encode())
        except Exception as ex:
            import traceback
            print(f"  [ingest] Error: {ex}")
            traceback.print_exc()
            self.send_response(500)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(ex)}).encode())

    def log_message(self, fmt, *args):
        # Show proxy and smart-query calls, suppress file-serving noise
        try:
            path = str(args[0]) if args else ""
            if "/pinecone/" in path or "/smart-query" in path or "/embed" in path:
                print(f"  → {path}")
        except Exception:
            pass


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
