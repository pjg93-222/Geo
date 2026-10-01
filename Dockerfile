FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 CU_DEPLOYMENT=hosted CU_DATA_DIR=/data
WORKDIR /app
COPY requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt && useradd --create-home --uid 10001 app && mkdir /data && chown app:app /data
COPY manage.py ./
COPY server/ ./server/
COPY tools/container_start.py ./tools/container_start.py
COPY tools/__init__.py tools/measures.py ./tools/
COPY data/criteria.json data/field_dictionary.csv data/co-cd005-b0edb0f04d6c.zip ./data/
COPY --from=frontend /build/dist/ ./frontend/dist/
USER app
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health')" || exit 1
CMD ["python", "tools/container_start.py"]
