#!/bin/sh
# Download Stockfish.js (github.com/nmrugg/stockfish.js), the WebAssembly
# build of Stockfish that browsers run for analyses on the user's device, and
# verify the checksums. Serve the directory with BROWSER_ENGINE_DIR.
#
# Only the "lite" builds (about 1.7 MB each, with a small NNUE network) are
# fetched: the full ones are around 100 MB, too much to download per visitor.
# The single-threaded build runs everywhere; the multi-threaded one is used by
# browsers that allow it.
#
# Usage: fetch-stockfish-js.sh [directory]   (default: ./stockfish-js)
set -eu
version=19.0.0
dir="${1:-stockfish-js}"
base="https://github.com/nmrugg/stockfish.js/releases/download/v$version"
mkdir -p "$dir"
cd "$dir"
while read -r sha file; do
  if [ ! -f "$file" ] || ! echo "$sha  $file" | sha256sum -c - >/dev/null 2>&1; then
    curl -fsSL --retry 3 -o "$file" "$base/$file"
    echo "$sha  $file" | sha256sum -c -
  fi
done <<SUMS
d3344124ab067fb0b90ee77873bb8e9fbf5fc01bc525fe714b0f942581e889e6 stockfish-19-lite-single.js
57ac2d72312aba346760e3f173f687a8c211208e97a87268436f7f0e10bb5387 stockfish-19-lite-single.wasm
2f98d35d20bf435c16925f8955fe4b0c2062e66962799a407667218ff9ea709d stockfish-19-lite.js
18727c9ade11a8ca04391ab5a298232bc6fffebe2002e7cfffac82e7ad453447 stockfish-19-lite.wasm
SUMS
# Stockfish.js is GPLv3; its license and source go along with the binaries.
curl -fsSL --retry 3 -o Copying.txt \
  "https://raw.githubusercontent.com/nmrugg/stockfish.js/v$version/Copying.txt"
echo "Source: https://github.com/nmrugg/stockfish.js/tree/v$version" > SOURCE.txt
