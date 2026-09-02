#!/usr/bin/env bash
# Starts backend + frontend together for the demo.
set -e
(cd backend && ./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000) &
(cd frontend && npm run dev)
