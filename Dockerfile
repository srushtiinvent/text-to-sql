FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY t2s ./t2s
COPY api ./api
COPY eval ./eval
COPY data/demo ./data/demo
COPY config.example.toml ./config.toml
# web/dist is served by the API if present (build the frontend first: cd web && npm run build)
COPY web ./web
EXPOSE 8000
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
