# syntax=docker/dockerfile:1
# Runs on x86-64 and on 64-bit Raspberry Pi OS (arm64).

# ---- Stockfish ---------------------------------------------------------------
# Built from source so the image always has a recent Stockfish with its NNUE
# networks embedded. This stage runs on the build machine and cross-compiles
# for other platforms, which is much faster than compiling under emulation.
FROM --platform=$BUILDPLATFORM debian:trixie-slim AS stockfish
ARG TARGETARCH
ARG BUILDARCH
ARG STOCKFISH_VERSION=sf_17.1
# Compiler target. Defaults to armv8 (every 64-bit Raspberry Pi) and
# x86-64-avx2. Use armv8-dotprod for a Raspberry Pi 5, or x86-64-sse41-popcnt
# for PCs without AVX2.
ARG STOCKFISH_ARCH=

RUN set -eux; \
    packages="ca-certificates curl git make g++"; \
    if [ "$TARGETARCH" != "$BUILDARCH" ]; then \
        case "$TARGETARCH" in \
            arm64) packages="$packages g++-aarch64-linux-gnu" ;; \
            amd64) packages="$packages g++-x86-64-linux-gnu" ;; \
        esac; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends $packages; \
    rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch "$STOCKFISH_VERSION" \
        https://github.com/official-stockfish/Stockfish.git /stockfish
WORKDIR /stockfish/src
COPY deploy/fetch-nnue.sh /usr/local/bin/fetch-nnue
RUN fetch-nnue .

RUN set -eux; \
    case "$TARGETARCH" in \
        arm64) arch="${STOCKFISH_ARCH:-armv8}"; triplet=aarch64-linux-gnu ;; \
        amd64) arch="${STOCKFISH_ARCH:-x86-64-avx2}"; triplet=x86_64-linux-gnu ;; \
        *) echo "Unsupported platform: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    cxx=g++; \
    if [ "$TARGETARCH" != "$BUILDARCH" ]; then cxx="$triplet-g++"; fi; \
    make -j"$(nproc)" build ARCH="$arch" COMP=gcc CXX="$cxx"; \
    install -m 755 stockfish /usr/local/bin/stockfish

# ---- App ---------------------------------------------------------------------
FROM python:3.12-slim-trixie

COPY --from=stockfish /usr/local/bin/stockfish /usr/local/bin/stockfish
# A short benchmark fails the build if the engine or its networks are broken.
RUN stockfish bench 16 1 6 > /dev/null

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STOCKFISH_PATH=/usr/local/bin/stockfish \
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
    && mkdir -p /data /syzygy /books \
    && chown analyzer /data /syzygy /books
USER analyzer
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD ["python", "-c", \
    "import os, urllib.request as u; u.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT', '8000'))"]

CMD ["chess-analyzer-web"]
