# syntax=docker/dockerfile:1
FROM --platform=$BUILDPLATFORM node:24-bookworm-slim AS frontend
WORKDIR /build/frontend
RUN npm install --global pnpm@10.24.0
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build && pnpm export:static

FROM --platform=$BUILDPLATFORM python:3.12-slim-bookworm AS models
WORKDIR /build
ENV PYTHONPATH=/build/src DOCLING_MODELS_DIR=/build/models DOCLING_ENV_FILE=/dev/null
RUN pip install --no-cache-dir huggingface-hub==1.33.0 python-dotenv==1.2.4
COPY src/docling_desk/ src/docling_desk/
RUN python -m docling_desk.operations.download_models

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 \
    HF_HOME=/tmp/huggingface DOCLING_DATA_DIR=/var/lib/docling/data \
    DOCLING_MODELS_DIR=/opt/docling/models DOCLING_STATE_DIR=/var/lib/docling \
    DOCLING_CACHE_DIR=/var/lib/docling/cache DOCLING_ENV_FILE=/dev/null \
    HOME=/home/docling OMP_NUM_THREADS=4 TOKENIZERS_PARALLELISM=false
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-impress libreoffice-writer libreoffice-calc \
    tesseract-ocr tesseract-ocr-jpn tesseract-ocr-eng \
    fonts-noto-cjk fonts-liberation libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements-lock.txt ./
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
    torch==2.14.1 torchvision==0.29.1 \
    && pip install --no-cache-dir -r requirements-lock.txt
COPY docling/ /opt/docling-source/
RUN pip install --no-cache-dir --no-deps /opt/docling-source
COPY packages/docling-azure-ocr/ /opt/docling-azure-ocr/
RUN pip install --no-cache-dir --no-deps /opt/docling-azure-ocr
COPY pyproject.toml README.md LICENSE NOTICE.md ./
COPY src/ src/
COPY --from=frontend /build/src/docling_desk/resources/static/frontend/ src/docling_desk/resources/static/frontend/
RUN pip install --no-cache-dir --no-deps --no-build-isolation .
COPY --from=models /build/models/ /opt/docling/models/
RUN groupadd --gid 10001 docling && useradd --uid 10001 --gid 10001 --create-home docling \
    && mkdir -p /var/lib/docling/data /var/lib/docling/cache \
    && chown -R docling:docling /var/lib/docling /home/docling
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health/ready', timeout=4)"
CMD ["python", "-m", "docling_desk", "--host", "0.0.0.0", "--port", "8765"]
