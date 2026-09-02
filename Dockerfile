# ---------- stage 1: build the TypeScript frontend ----------
FROM node:22-alpine AS web

WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build          # tsc typecheck + vite build -> /web/dist

# ---------- stage 2: python service that also serves the frontend ----------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /srv

# Dependencies first, so app edits don't invalidate the pip layer.
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY --from=web /web/dist ./static

# Never run the service as root.
RUN useradd --create-home --uid 10001 appuser \
 && mkdir -p /srv/data && chown -R appuser:appuser /srv
USER appuser

ENV AUDIT_DB_PATH=/srv/data/audit.db

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=4).status==200 else 1)"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
