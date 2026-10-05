#!/bin/sh
# Run a short benchmark with every Stockfish build in the image, so a build
# without its NNUE networks (or otherwise broken) fails the image build.
# Builds for instructions the build machine lacks exit with SIGILL (132) and
# are skipped; the last, most portable build must always work.
set -u
dir=/usr/local/lib/stockfish
last=$(tail -n 1 "$dir/builds" | tr ' ' '\n' | tail -n 1)
for build in $(cat "$dir/builds"); do
    "$dir/stockfish-$build" bench 16 1 6 > /dev/null 2>&1
    status=$?
    if [ "$status" -eq 0 ]; then
        echo "stockfish-$build: ok"
    elif [ "$status" -eq 132 ] && [ "$build" != "$last" ]; then
        echo "stockfish-$build: not supported by this CPU, not checked"
    else
        echo "stockfish-$build: failed with status $status" >&2
        exit 1
    fi
done
