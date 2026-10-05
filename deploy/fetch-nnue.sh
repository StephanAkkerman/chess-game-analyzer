#!/bin/sh
# Download the NNUE networks named in Stockfish's evaluate.h and verify their
# checksums. Stockfish's own script can leave an empty file behind when a
# mirror fails, which produces an engine that refuses to start.
set -eu
cd "${1:-.}"
for name in $(grep -o 'nn-[a-z0-9]\{12\}\.nnue' evaluate.h | sort -u); do
  for url in "https://tests.stockfishchess.org/api/nn/$name" \
             "https://github.com/official-stockfish/networks/raw/master/$name" \
             "https://raw.githubusercontent.com/official-stockfish/networks/master/$name"; do
    if curl -fsSL --retry 3 -o "$name" "$url" \
       && [ "nn-$(sha256sum "$name" | cut -c 1-12).nnue" = "$name" ]; then
      echo "Downloaded $name from $url"
      break
    fi
    rm -f "$name"
  done
  [ -s "$name" ] || { echo "Could not download $name" >&2; exit 1; }
done
