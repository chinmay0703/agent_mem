# Agent Mem

Memory-augmented AI agent. Self-hosted. Drop in your documents, talk to it,
and watch it build a knowledge graph from the conversation in real time.

## Stack

- **Frontend**: React + Vite + D3
- **Backend**: FastAPI + LangGraph + OpenAI
- **Storage**: Postgres (threads, messages, audit) · Neo4j (knowledge graph) · FAISS (RAG chunks)

## Run locally

```bash
# Backend
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8001

# Frontend (separate terminal)
cd frontend
npm install
VITE_API_TARGET=http://localhost:8001 npm run dev
```

Open http://localhost:5173. The app boots into a setup wizard on first
launch — plug in your OpenAI key, Postgres creds, and Neo4j creds, and
each connection is verified live before being saved.

Prereqs:
- Python 3.11+
- Node 20+
- Postgres (any recent version)
- Neo4j 5.x

## Single-deploy

The backend serves the API under `/api` AND the built frontend from
`frontend/dist`, so one process is enough in production:

```bash
cd frontend && npm run build
cd ../backend && uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Vercel

The repo includes `vercel.json` and `api/index.py`. Set credentials as
Project Environment Variables in the Vercel dashboard — Vercel's
serverless filesystem isn't persistent, so the wizard's on-disk save
won't survive cold starts.
# agent_mem
