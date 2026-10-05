#!/bin/sh
# Run the fastest Stockfish build this CPU supports. The image contains a
# build per instruction set, listed fastest first in $dir/builds:
#   arm64: armv8-dotprod (Raspberry Pi 5), armv8 (Raspberry Pi 4 and others)
#   amd64: x86-64-avx2, x86-64-sse41-popcnt
dir=/usr/local/lib/stockfish
for build in $(cat "$dir/builds"); do
    case "$build" in
        armv8-dotprod) grep -qw asimddp /proc/cpuinfo || continue ;;
        x86-64-avx2) grep -qw avx2 /proc/cpuinfo && grep -qw bmi1 /proc/cpuinfo || continue ;;
    esac
    exec "$dir/stockfish-$build" "$@"
done
echo "No Stockfish build in $dir runs on this CPU" >&2
exit 1
