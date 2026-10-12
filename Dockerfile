# syntax=docker/dockerfile:1.7
# Terrier, hosted: one image for every district. No voter data is built in. A district's stores arrive as a read-only
# volume (CU_DATA_DIR=/data); the app writes only to the state volume (CU_STATE_DIR=/state) and /tmp; every request must
# carry a Cloudflare Access token (server/research/access.py). See HOSTED.md and compose.yaml.
#
# requirements.linux.lock is compiled from requirements.linux.in on Linux (see that file) and committed before CI builds.

FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS deps
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
COPY requirements.linux.lock /tmp/requirements.linux.lock
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --require-hashes --no-deps -r /tmp/requirements.linux.lock

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH=/opt/venv/bin:$PATH \
    CU_DEPLOYMENT=hosted CU_BIND=0.0.0.0 PORT=8080 CU_HTTPS_REDIRECT=0 ACCESS_ENFORCE=1 \
    CU_DATA_DIR=/data CU_STATE_DIR=/state CU_BUNDLE=/data/bundle.zip
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin app \
 && mkdir /state && chown 10001:10001 /state
COPY --from=deps /opt/venv /opt/venv
WORKDIR /app
COPY manage.py ./
COPY server/ ./server/
COPY tools/ ./tools/
COPY data/criteria.json data/field_dictionary.csv ./data/
COPY --from=frontend /build/dist/ ./frontend/dist/
USER 10001:10001
VOLUME ["/state"]
EXPOSE 8080
# From inside the container the probe is a loopback peer with no proxy headers, the one request the Access gate lets through.
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4)"]
CMD ["python", "tools/container_start.py"]
