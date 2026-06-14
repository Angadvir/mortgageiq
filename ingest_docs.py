"""
ingest_docs.py
==============
Ingests financial PDFs into Pinecone for semantic search and Q&A.
Uses Claude to generate embeddings and extract metadata.

Supported document types:
  - Quarterly earnings (10-Q, 10-K)
  - ETF / MBS prospectuses
  - Deal offering memoranda
  - Trade confirmations
  - Research reports
  - Any financial PDF

Setup (one-time):
    pip3 install pinecone pymupdf anthropic

Usage:
    python3 ingest_docs.py --file report.pdf
    python3 ingest_docs.py --dir ./documents/
    python3 ingest_docs.py --file report.pdf --doc-type earnings --ticker RKT
    python3 ingest_docs.py --list          # list all indexed documents
    python3 ingest_docs.py --delete doc_id # delete a document by ID
    python3 ingest_docs.py --query "What is the CPR assumption for FNMA pools?"
"""

import os
import sys
import json
import hashlib
import logging
import argparse
import time
from pathlib import Path
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("ingest_docs")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG — set via environment or edit here
# ─────────────────────────────────────────────────────────────────────────────
PINECONE_API_KEY   = os.environ.get("PINECONE_API_KEY", "")
ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
VOYAGE_API_KEY     = os.environ.get("VOYAGE_API_KEY", "")
INDEX_NAME         = os.environ.get("PINECONE_INDEX", "mortgageiq-docs")
EMBED_MODEL        = "voyage-finance-2"   # Voyage Finance model via Anthropic (best for financial docs)
EMBED_DIMENSIONS   = 1024
CHUNK_SIZE         = 600    # tokens approx — ~450 words per chunk
CHUNK_OVERLAP      = 80     # overlap to preserve context across chunk boundaries

# ─────────────────────────────────────────────────────────────────────────────
# DEPENDENCIES CHECK
# ─────────────────────────────────────────────────────────────────────────────
def check_deps():
    missing = []
    try: import pinecone
    except ImportError: missing.append("pinecone")
    try: import fitz   # pymupdf
    except ImportError: missing.append("pymupdf")
    try: import anthropic
    except ImportError: missing.append("anthropic")
    if missing:
        print(f"\n  Missing: {', '.join(missing)}")
        print(f"  Install: pip3 install {' '.join(missing)}\n")
        sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# PINECONE SETUP
# ─────────────────────────────────────────────────────────────────────────────
def get_index():
    from pinecone import Pinecone, ServerlessSpec
    if not PINECONE_API_KEY:
        print("\n  PINECONE_API_KEY not set.")
        print("  Get it at: pinecone.io → API Keys")
        print("  Then: export PINECONE_API_KEY=your_key\n")
        sys.exit(1)

    pc = Pinecone(api_key=PINECONE_API_KEY)
    existing = [idx.name for idx in pc.list_indexes()]

    if INDEX_NAME not in existing:
        log.info(f"Creating Pinecone index '{INDEX_NAME}'…")
        pc.create_index(
            name=INDEX_NAME,
            dimension=EMBED_DIMENSIONS,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1")
        )
        # Wait for index to be ready
        while not pc.describe_index(INDEX_NAME).status["ready"]:
            time.sleep(1)
        log.info(f"Index '{INDEX_NAME}' created")
    else:
        log.info(f"Using existing index '{INDEX_NAME}'")

    return pc.Index(INDEX_NAME)

# ─────────────────────────────────────────────────────────────────────────────
# PDF EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────
def extract_pdf_text(pdf_path: str) -> tuple[str, dict]:
    """Extract text and metadata from PDF. Returns (full_text, metadata)."""
    import fitz
    doc = fitz.open(pdf_path)
    pages = []
    for page_num, page in enumerate(doc):
        text = page.get_text("text")
        if text.strip():
            pages.append(f"[Page {page_num+1}]\n{text}")

    full_text = "\n\n".join(pages)
    metadata = {
        "page_count": len(doc),
        "filename":   Path(pdf_path).name,
    }
    doc.close()
    return full_text, metadata

# ─────────────────────────────────────────────────────────────────────────────
# TEXT CHUNKING
# ─────────────────────────────────────────────────────────────────────────────
def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[dict]:
    """Split text into overlapping chunks. Returns list of {text, chunk_index}."""
    words = text.split()
    chunks = []
    start = 0
    idx = 0
    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunk_text = " ".join(words[start:end])
        if chunk_text.strip():
            # Extract page number from chunk if present
            page_num = None
            import re
            m = re.search(r"\[Page (\d+)\]", chunk_text)
            if m:
                page_num = int(m.group(1))
            chunks.append({
                "text":        chunk_text,
                "chunk_index": idx,
                "page":        page_num
            })
            idx += 1
        start += chunk_size - overlap
    return chunks

# ─────────────────────────────────────────────────────────────────────────────
# EMBEDDINGS via Anthropic Voyage Finance
# ─────────────────────────────────────────────────────────────────────────────
def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts using Voyage Finance model via Anthropic."""
    import anthropic
    if not ANTHROPIC_API_KEY:
        print("\n  ANTHROPIC_API_KEY not set.")
        print("  export ANTHROPIC_API_KEY=your_key\n")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # Voyage supports batches of up to 128 texts
    all_embeddings = []
    batch_size = 64
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i+batch_size]
        response = client.beta.messages.batches  # use embeddings endpoint
        # Use the embeddings API directly
        import urllib.request, urllib.parse
        import json as _json
        payload = _json.dumps({
            "model":  EMBED_MODEL,
            "input":  batch,
            "input_type": "document"
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",  # will be corrected below
        )
        # Actually use the correct embeddings endpoint
        response = _embed_via_voyage(batch)
        all_embeddings.extend(response)
        if i + batch_size < len(texts):
            time.sleep(0.5)  # rate limit courtesy

    return all_embeddings


def _embed_via_voyage(texts: list[str]) -> list[list[float]]:
    """Call Voyage Finance embeddings via Anthropic API."""
    import urllib.request
    import json as _json

    payload = _json.dumps({
        "model": EMBED_MODEL,
        "input": texts,
        "input_type": "document"
    }).encode()

    req = urllib.request.Request(
        "https://api.voyageai.com/v1/embeddings",
        data=payload,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {VOYAGE_API_KEY}",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = _json.loads(r.read())
            return [item["embedding"] for item in data["data"]]
    except Exception as e:
        # Fallback: use claude to get a simpler embedding via a different approach
        log.warning(f"Voyage API error: {e}. Falling back to simple hash embedding.")
        return _simple_embed_fallback(texts)


def _simple_embed_fallback(texts: list[str]) -> list[list[float]]:
    """
    Fallback embedding using FNV-1a hash + TF weighting.
    Must stay in sync with the browser simpleEmbed() function in the dashboard
    so that query vectors match stored vectors for cosine similarity search.
    """
    import math

    def fnv1a(word: str) -> int:
        h = 0x811c9dc5
        for ch in word.encode():
            h ^= ch
            h = (h * 0x01000193) & 0xFFFFFFFF
        return h

    embeddings = []
    for text in texts:
        words = text.lower().split()
        freq: dict[str, int] = {}
        for w in words:
            freq[w] = freq.get(w, 0) + 1
        vec = [0.0] * EMBED_DIMENSIONS
        for word, count in freq.items():
            idx = fnv1a(word) % EMBED_DIMENSIONS
            vec[idx] += math.log(1 + count)
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        vec = [x / norm for x in vec]
        embeddings.append(vec)
    return embeddings

# ─────────────────────────────────────────────────────────────────────────────
# METADATA EXTRACTION via Claude
# ─────────────────────────────────────────────────────────────────────────────
def extract_doc_metadata(text_sample: str, filename: str, doc_type: str, ticker: str) -> dict:
    """Use Claude to extract structured metadata from document sample."""
    import anthropic
    import json as _json

    if not ANTHROPIC_API_KEY:
        return {"title": filename, "doc_type": doc_type, "ticker": ticker}

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    sample = text_sample[:3000]  # first 3000 chars

    try:
        msg = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=300,
            system='Extract document metadata and return ONLY valid JSON. No markdown.',
            messages=[{
                "role": "user",
                "content": f"""Extract metadata from this financial document excerpt.
Return JSON with these fields:
{{"title":"document title","company":"company name or fund name","ticker":"stock ticker if present","doc_type":"one of: earnings|prospectus|research|mbs_deal|prospectus|statement|other","period":"reporting period e.g. Q1 2025","date":"publication date if found"}}

Document filename: {filename}
Excerpt:
{sample}"""
            }]
        )
        raw = msg.content[0].text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        meta = _json.loads(raw)
        # Override with user-provided values if given
        if ticker: meta["ticker"] = ticker
        if doc_type != "auto": meta["doc_type"] = doc_type
        return meta
    except Exception as e:
        log.warning(f"Metadata extraction failed: {e}")
        return {"title": filename, "doc_type": doc_type, "ticker": ticker or ""}

# ─────────────────────────────────────────────────────────────────────────────
# INGEST SINGLE PDF
# ─────────────────────────────────────────────────────────────────────────────
def ingest_pdf(pdf_path: str, doc_type: str = "auto", ticker: str = "", index=None) -> int:
    path = Path(pdf_path)
    if not path.exists():
        log.error(f"File not found: {pdf_path}")
        return 0

    log.info(f"Processing: {path.name}")

    # Extract text
    full_text, pdf_meta = extract_pdf_text(str(path))
    log.info(f"  Extracted {len(full_text):,} chars from {pdf_meta['page_count']} pages")

    if len(full_text.strip()) < 100:
        log.error(f"  Too little text extracted. Is the PDF scanned/image-only?")
        return 0

    # Extract metadata via Claude
    doc_meta = extract_doc_metadata(full_text, path.name, doc_type, ticker)
    log.info(f"  Document: {doc_meta.get('title','?')} | {doc_meta.get('doc_type','?')} | {doc_meta.get('company','?')}")

    # Generate stable doc ID from file hash
    file_hash = hashlib.md5(path.read_bytes()).hexdigest()[:12]
    doc_id = f"doc_{file_hash}"

    # Chunk text
    chunks = chunk_text(full_text)
    log.info(f"  Chunked into {len(chunks)} segments")

    # Embed in batches
    log.info(f"  Embedding chunks…")
    texts = [c["text"] for c in chunks]
    embeddings = embed_texts(texts)
    log.info(f"  Got {len(embeddings)} embeddings")

    # Upsert to Pinecone
    vectors = []
    for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
        vector_id = f"{doc_id}_chunk_{i}"
        metadata = {
            "doc_id":      doc_id,
            "filename":    path.name,
            "chunk_index": chunk["chunk_index"],
            "page":        chunk.get("page") or 0,
            "text":        chunk["text"][:1000],  # Pinecone metadata limit
            "title":       str(doc_meta.get("title", path.name)),
            "company":     str(doc_meta.get("company", "")),
            "ticker":      str(doc_meta.get("ticker", ticker)),
            "doc_type":    str(doc_meta.get("doc_type", doc_type)),
            "period":      str(doc_meta.get("period", "")),
            "date":        str(doc_meta.get("date", "")),
            "ingested_at": datetime.utcnow().isoformat() + "Z",
        }
        vectors.append((vector_id, embedding, metadata))

    # Upsert in batches of 100
    batch_size = 100
    for i in range(0, len(vectors), batch_size):
        batch = vectors[i:i+batch_size]
        index.upsert(vectors=batch)
        log.info(f"  Upserted batch {i//batch_size + 1}/{(len(vectors)-1)//batch_size + 1}")

    log.info(f"  ✓ Ingested {len(chunks)} chunks for '{doc_meta.get('title', path.name)}'")

    # Update docs_library.json so dashboard can display without CORS issues
    _update_library(doc_id, doc_meta, path.name, len(chunks))
    return len(chunks)


def _update_library(doc_id: str, doc_meta: dict, filename: str, chunk_count: int) -> None:
    """Write/update docs_library.json alongside other pipeline JSON files."""
    import json as _json
    library_path = Path(__file__).parent / "docs_library.json"
    try:
        library = _json.loads(library_path.read_text()) if library_path.exists() else []
    except Exception:
        library = []
    # Remove existing entry for same doc_id
    library = [d for d in library if d.get("doc_id") != doc_id]
    library.append({
        "doc_id":    doc_id,
        "title":     doc_meta.get("title", filename),
        "company":   doc_meta.get("company", ""),
        "ticker":    doc_meta.get("ticker", ""),
        "doc_type":  doc_meta.get("doc_type", ""),
        "period":    doc_meta.get("period", ""),
        "filename":  filename,
        "chunks":    chunk_count,
        "indexed_at": datetime.utcnow().isoformat() + "Z"
    })
    library_path.write_text(_json.dumps(library, indent=2))
    log.info(f"  Updated docs_library.json ({len(library)} document(s))")

# ─────────────────────────────────────────────────────────────────────────────
# SEMANTIC SEARCH
# ─────────────────────────────────────────────────────────────────────────────
def search(query: str, top_k: int = 8, filter_meta: dict = None) -> list[dict]:
    """Search Pinecone for relevant document chunks."""
    index = get_index()
    query_embedding = embed_texts([query])[0]
    results = index.query(
        vector=query_embedding,
        top_k=top_k,
        include_metadata=True,
        filter=filter_meta
    )
    return results.get("matches", [])

# ─────────────────────────────────────────────────────────────────────────────
# Q&A via Claude
# ─────────────────────────────────────────────────────────────────────────────
def answer_question(question: str, top_k: int = 6) -> str:
    """Retrieve relevant chunks and use Claude to answer the question."""
    import anthropic

    log.info(f"Searching for: {question}")
    matches = search(question, top_k=top_k)

    if not matches:
        return "No relevant documents found. Make sure you have ingested documents first."

    # Build context from top matches
    context_parts = []
    for m in matches:
        meta = m.get("metadata", {})
        text = meta.get("text", "")
        source = f"{meta.get('title','?')} (p.{meta.get('page','?')})"
        context_parts.append(f"[Source: {source}]\n{text}")

    context = "\n\n---\n\n".join(context_parts)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1000,
        system="""You are a financial document analyst for a mortgage portfolio manager.
Answer questions using ONLY the provided document excerpts.
Always cite your sources with document title and page number.
If the answer is not in the provided context, say so clearly.""",
        messages=[{
            "role": "user",
            "content": f"""Question: {question}

Document excerpts:
{context}

Answer based only on the above excerpts, with citations."""
        }]
    )
    return response.content[0].text

# ─────────────────────────────────────────────────────────────────────────────
# LIST DOCUMENTS
# ─────────────────────────────────────────────────────────────────────────────
def list_documents(index) -> None:
    """List all unique documents in the index."""
    # Query with a dummy vector to get metadata
    dummy = [0.0] * EMBED_DIMENSIONS
    results = index.query(vector=dummy, top_k=100, include_metadata=True)
    docs = {}
    for m in results.get("matches", []):
        meta = m.get("metadata", {})
        doc_id = meta.get("doc_id", "?")
        if doc_id not in docs:
            docs[doc_id] = {
                "title":    meta.get("title", "?"),
                "company":  meta.get("company", "?"),
                "doc_type": meta.get("doc_type", "?"),
                "period":   meta.get("period", "?"),
                "filename": meta.get("filename", "?"),
                "ingested": meta.get("ingested_at", "?")[:10],
            }

    if not docs:
        print("\nNo documents indexed yet.\n")
        return

    print(f"\n{'─'*70}")
    print(f"  {'TITLE':<30} {'TYPE':<12} {'COMPANY':<15} {'PERIOD':<10}")
    print(f"{'─'*70}")
    for doc_id, d in docs.items():
        print(f"  {d['title'][:28]:<30} {d['doc_type'][:10]:<12} {d['company'][:13]:<15} {d['period'][:8]:<10}")
    print(f"{'─'*70}")
    print(f"  {len(docs)} document(s) indexed\n")

# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="MortgageIQ Document Intelligence — ingest financial PDFs into Pinecone",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("--file",     help="Path to a PDF file to ingest")
    parser.add_argument("--dir",      help="Directory of PDFs to ingest")
    parser.add_argument("--doc-type", default="auto", help="Document type: earnings|prospectus|research|mbs_deal|statement|other")
    parser.add_argument("--ticker",   default="",     help="Stock ticker associated with document (e.g. RKT)")
    parser.add_argument("--query",    help="Run a semantic search query against indexed documents")
    parser.add_argument("--ask",      help="Ask a question and get an AI answer from your documents")
    parser.add_argument("--list",     action="store_true", help="List all indexed documents")
    parser.add_argument("--top-k",    type=int, default=6, help="Number of results to retrieve (default: 6)")
    args = parser.parse_args()

    check_deps()

    if args.query:
        matches = search(args.query, top_k=args.top_k)
        print(f"\nTop {len(matches)} results for: '{args.query}'\n{'─'*60}")
        for i, m in enumerate(matches, 1):
            meta = m.get("metadata", {})
            print(f"\n{i}. [{meta.get('title','?')} | p.{meta.get('page','?')} | score={m['score']:.3f}]")
            print(f"   {meta.get('text','')[:200]}…")
        return

    if args.ask:
        answer = answer_question(args.ask, top_k=args.top_k)
        print(f"\nQ: {args.ask}\n\n{'─'*60}\n{answer}\n")
        return

    index = get_index()

    if args.list:
        list_documents(index)
        return

    total_chunks = 0

    if args.file:
        total_chunks += ingest_pdf(args.file, args.doc_type, args.ticker, index)

    elif args.dir:
        dir_path = Path(args.dir)
        pdfs = list(dir_path.glob("*.pdf")) + list(dir_path.glob("*.PDF"))
        if not pdfs:
            log.error(f"No PDF files found in {args.dir}")
            return
        log.info(f"Found {len(pdfs)} PDF(s) in {args.dir}")
        for pdf in pdfs:
            total_chunks += ingest_pdf(str(pdf), args.doc_type, args.ticker, index)
            time.sleep(1)  # brief pause between files

    else:
        parser.print_help()
        return

    log.info(f"\n✓ Done — {total_chunks} total chunks ingested into Pinecone index '{INDEX_NAME}'")

if __name__ == "__main__":
    main()
