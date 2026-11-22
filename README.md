# FBR Digital Invoicing Assistant

A RAG service: upload documents, ask questions, get grounded answers with citations or a clear
refusal. Full documentation follows in Phase 12.

## Run locally

```powershell
docker compose up -d qdrant          # vector database
uv run fastapi dev app/main.py       # API on http://127.0.0.1:8000/docs
cd frontend; npm install; npm run dev  # UI on http://localhost:5173 (see frontend/README.md)
```
