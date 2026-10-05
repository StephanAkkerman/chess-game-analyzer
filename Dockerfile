# Runs on x86-64 and on 64-bit Raspberry Pi OS (arm64).
FROM python:3.12-slim-trixie

RUN apt-get update \
    && apt-get install -y --no-install-recommends stockfish \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STOCKFISH_PATH=/usr/games/stockfish \
    DATA_DIR=/data \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-deps .

RUN useradd --create-home --uid 1000 analyzer \
    && mkdir -p /data \
    && chown analyzer /data
USER analyzer
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD ["python", "-c", \
    "import os, urllib.request as u; u.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT', '8000'))"]

CMD ["chess-analyzer-web"]
