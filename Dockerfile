# syntax=docker/dockerfile:1
# Runs on x86-64 and on 64-bit Raspberry Pi OS (arm64). The image contains
# everything the analyzer uses: Stockfish 17.1 with its NNUE networks, the
# Syzygy 3-4-5 piece endgame tablebases and a Polyglot opening book.

# ---- Stockfish ---------------------------------------------------------------
# Built from source so the image always has a recent Stockfish with its NNUE
# networks embedded. This stage runs on the build machine and cross-compiles
# for other platforms, which is much faster than compiling under emulation.
FROM --platform=$BUILDPLATFORM debian:trixie-slim AS stockfish
ARG TARGETARCH
ARG BUILDARCH
ARG STOCKFISH_VERSION=sf_17.1
# Builds to include, fastest first; the launcher picks the first one the CPU
# supports. Empty: armv8-dotprod and armv8 on arm64, x86-64-avx2 and
# x86-64-sse41-popcnt on amd64.
ARG STOCKFISH_BUILDS=

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
        arm64) builds="${STOCKFISH_BUILDS:-armv8-dotprod armv8}"; triplet=aarch64-linux-gnu ;; \
        amd64) builds="${STOCKFISH_BUILDS:-x86-64-avx2 x86-64-sse41-popcnt}"; triplet=x86_64-linux-gnu ;; \
        *) echo "Unsupported platform: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    cxx=g++; \
    if [ "$TARGETARCH" != "$BUILDARCH" ]; then cxx="$triplet-g++"; fi; \
    mkdir -p /out; \
    for arch in $builds; do \
        make -j"$(nproc)" build ARCH="$arch" COMP=gcc CXX="$cxx"; \
        install -m 755 stockfish "/out/stockfish-$arch"; \
        make clean; \
    done; \
    echo "$builds" > /out/builds

# ---- Endgame tablebases and opening book -------------------------------------
# Platform independent, so the layers are shared between amd64 and arm64.
FROM --platform=$BUILDPLATFORM python:3.12-slim-trixie AS data
# full: WDL + DTZ tables (940 MB), wdl: WDL only (380 MB, best moves in
# tablebase endings are less precise), none: no tablebases.
ARG SYZYGY=full
# Polyglot book (gm2001.bin from github.com/michaeldv/donna_opening_books).
# Set OPENING_BOOK_URL to empty to leave it out; the Lichess explorer is then
# used for openings.
ARG OPENING_BOOK_URL=https://raw.githubusercontent.com/michaeldv/donna_opening_books/master/gm2001.bin
ARG OPENING_BOOK_SHA256=fb6e9f3f27bb19a5b2fdefcc441c88ddeae48db61d5f00ad83973abb9f939c87

RUN pip install --no-cache-dir --disable-pip-version-check chess requests
COPY src/chess_analyzer/syzygy.py /usr/local/bin/download-syzygy
RUN set -eux; \
    mkdir -p /opt/syzygy; \
    case "$SYZYGY" in \
        full) python /usr/local/bin/download-syzygy --dir /opt/syzygy ;; \
        wdl) python /usr/local/bin/download-syzygy --dir /opt/syzygy --wdl-only ;; \
        none) ;; \
        *) echo "SYZYGY must be full, wdl or none" >&2; exit 1 ;; \
    esac
RUN set -eux; \
    mkdir -p /opt/books; \
    if [ -n "$OPENING_BOOK_URL" ]; then \
        python -c "import urllib.request, sys; urllib.request.urlretrieve(sys.argv[1], '/opt/books/book.bin')" "$OPENING_BOOK_URL"; \
        echo "$OPENING_BOOK_SHA256  /opt/books/book.bin" | sha256sum -c -; \
    fi

# ---- App ---------------------------------------------------------------------
FROM python:3.12-slim-trixie

# Large, rarely changing layers first, so app updates only download the small
# layers below them.
COPY --from=data /opt/syzygy /opt/syzygy
COPY --from=data /opt/books /opt/books
COPY --from=stockfish /out /usr/local/lib/stockfish
COPY deploy/stockfish.sh /usr/local/bin/stockfish
COPY deploy/check-stockfish.sh /usr/local/bin/check-stockfish
RUN check-stockfish

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STOCKFISH_PATH=/usr/local/bin/stockfish \
    SYZYGY_PATH=/opt/syzygy \
    OPENING_BOOK=/opt/books/book.bin \
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
