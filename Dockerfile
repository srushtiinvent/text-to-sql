# Stage 1: build the frontend
FROM node:20-slim AS web
WORKDIR /web
COPY web/package.json ./
RUN npm install
COPY web ./
RUN npm run build

# Stage 2: API + built frontend (FastAPI serves web/dist)
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
COPY --from=web /web/dist ./web/dist
EXPOSE 8000
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
