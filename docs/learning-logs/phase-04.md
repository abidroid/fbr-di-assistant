# Learning Log: Phase 4 (Ingestion)

Concepts: upload validation by magic bytes, PDF text layers vs scans, running headers/footers,
amendment footnotes as metadata, structure-aware chunking (one rule / table row / JSON sample per
chunk) vs fixed windows, the context header, dense (meaning) vs sparse BM25 (exact words)
vectors, HNSW, loading models once in the lifespan, CPU work in a thread, 201 vs 202 by chunk
count, delete order (vectors, file, row).

**1. Walk a PDF from upload to stored points.**

My words: _(to add before the video)_

Fuller answer (from review): `POST /documents` gives the file to `document_service.upload_document`.

1. **Validate** (`validate.py`). Reading stops at 25 MB (413). The real type comes from the bytes:
   `%PDF-` means PDF, so a renamed `.exe` gets 415. An encrypted or corrupt PDF gets 400. The
   sha256 of the bytes is computed.
2. **Duplicate check**. If this user already has a file with that sha256, the answer is 409 with
   the existing id.
3. **Save** a `documents` row with status `processing`, and the file as
   `uploads/<owner>/<doc_id>.pdf`. The name is built from ids, never from the user's file name.
4. **Extract** (`extract.py`). PyMuPDF reads every page that has a text layer. It removes the
   running header ("Sales Tax Rules, 2006") and printed page numbers, drops superscript footnote
   markers and turns amendment footnotes into `amended_by` ("S.R.O. 2071(I)/2025"). It reads ruled
   tables as Markdown and follows headings to build the section path. Image-only pages are
   captioned by Haiku. The result is a list of blocks (text, page, section path, kind).
5. **Chunk** (`chunk.py`). One legal rule, one table row (with its header row), one JSON sample,
   one FAQ or one caption per chunk; anything over ~500 tokens is split 500/75. Each chunk keeps
   its page range and gets `embed_text` = "Doc title > section path" + text.
6. **Embed** (`embed.py`), dense and sparse at the same time. BGE-M3 gives a normalised
   1024-number vector (meaning), BM25 a sparse vector (exact words). This is CPU work, so it runs
   in a worker thread.
7. **Store** (`store.py`). Each chunk becomes one Qdrant point with both named vectors (`dense`,
   `bm25`) and the payload a citation needs (doc_id, owner_id, filename, pages, section path,
   authority, amended_by, ocr, suspicious, text).

Steps 1-5 take seconds. With at most 120 chunks the request also embeds and stores, sets `ready`
and returns 201 (SRO 69: 26 chunks, 46 s). A bigger document (the Rules: 842 chunks, ~15 min)
returns 202 `processing` at once, and a background task finishes it and sets `ready` or `failed`.
Embedding cost follows chunks (~1 s each on CPU), not pages, which is why the limit counts chunks.

**2. Why prepend a context header before embedding?**

My words: _(to add before the video)_

Fuller answer (from review): A chunk cut out of a long document loses its surroundings. Rule
150V on its own says "the Commissioner ... may allow extension in time, for up to sixty days"; it
never says it is about *integration with FBR's Digital Invoicing system*, or that it comes from
the *Sales Tax Rules, Chapter XIV*. Those words sit in the chapter heading, pages earlier.
Embedding "The Sales Tax Rules, 2006 > CHAPTER XIV Procedure for ... integration ... > Rule 150V"
together with the text puts that meaning into the vector. A question that mentions "digital
invoicing" or "Rule 150V" then lands near the right chunk, and BM25 can match "150V" exactly.
Short chunks need this most: a table row ("|0052|Please provide valid HS Code|") means little
without "API spec > 7. Sales Error Codes" above it.

The header is embedded, but `text` (what users see and what citations quote) stays clean.
Trade-off: a few extra tokens per chunk, a little more embedding time, and a header that is too
long or generic could pull unrelated chunks of the same chapter closer together. `chunk_blocks`
can switch the header off (`context_header=False`), so Phase 5 can measure its effect instead of
assuming it.

**3. How do you prove deleted content can never be retrieved?**

My words: _(to add before the video)_

Fuller answer (from review): Retrieval can only return what is in Qdrant, so the proof is that a
deleted document leaves **zero points** there, checked by counting, not assumed.

- **Order of deletion:** `delete_document` removes the vectors first, then the file, then the
  row. If Qdrant is down, the call fails with 503 and nothing else is deleted, so the user can
  retry. Searchable vectors can never outlive their row.
- **Waited for:** the Qdrant delete uses `wait=True`, so the 204 is sent only after the points are
  really gone, not just queued.
- **Measured:** `count_document_points` does an *exact* count filtered on `doc_id`. The API test
  `test_delete_removes_vectors_file_and_row` counts more than 0 points before and 0 after, checks
  the file is gone and that GET then returns 404. The live check against the real Docker Qdrant
  showed the same: 26 points before, 0 after.
- **Nothing else deleted by mistake:** a second document's points survive the delete, and another
  user's DELETE returns 404 and leaves the points untouched (tests prove both).
- **Edge case handled:** if a document is deleted while its background ingestion is still
  running, the task finds the row gone when it finishes and removes the vectors it just wrote.
- **Defence in depth:** every search will also filter on `owner_id` and only search what is
  stored, so even a bug elsewhere could not surface another user's or a deleted document.
