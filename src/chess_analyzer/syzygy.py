"""Download Syzygy endgame tablebases.

The 3-4-5 piece tables take about 940 MB (WDL 380 MB, DTZ 560 MB). WDL tables
give the result of a position; DTZ tables are used to choose the best move in
won and lost positions, so they are optional.

Run ``chess-analyzer-syzygy --dir syzygy`` (or ``python -m
chess_analyzer.syzygy``) to download them.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import chess.syzygy
import requests

DEFAULT_URL = "https://tablebase.lichess.ovh/tables/standard/3-4-5/"


def table_files(pieces: int = 5, dtz: bool = True) -> list[str]:
    """Return the file names of all tables with up to ``pieces`` pieces."""
    suffixes = [".rtbw", ".rtbz"] if dtz else [".rtbw"]
    names = sorted(chess.syzygy.tablenames(piece_count=pieces))
    return [name + suffix for name in names for suffix in suffixes]


def download(
    directory: str | Path,
    url: str = DEFAULT_URL,
    pieces: int = 5,
    dtz: bool = True,
    session: requests.Session | None = None,
    log=print,
) -> int:
    """Download missing table files into ``directory``.

    Files that already exist are skipped, so an interrupted download can be
    resumed by running it again.

    Returns
    -------
    int
        The number of files downloaded.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    session = session or requests.Session()
    files = table_files(pieces, dtz)
    downloaded = 0
    for i, name in enumerate(files, start=1):
        target = directory / name
        if target.exists() and target.stat().st_size > 0:
            continue
        log(f"[{i}/{len(files)}] {name}")
        partial = target.with_suffix(target.suffix + ".part")
        with session.get(url.rstrip("/") + "/" + name, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(partial, "wb") as f:
                f.writelines(r.iter_content(chunk_size=1 << 20))
        os.replace(partial, target)
        downloaded += 1
    return downloaded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="chess-analyzer-syzygy",
        description="Download Syzygy endgame tablebases (3-4-5 pieces).",
    )
    parser.add_argument("--dir", default="syzygy", help="Target directory.")
    parser.add_argument(
        "--wdl-only",
        action="store_true",
        help="Skip the DTZ tables (saves 560 MB; best moves in tablebase "
        "positions are then less precise).",
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="Mirror to download from.")
    args = parser.parse_args(argv)
    try:
        count = download(args.dir, args.url, dtz=not args.wdl_only)
    except requests.RequestException as exc:
        print(f"Download failed: {exc}", file=sys.stderr)
        return 1
    print(f"Done: {count} files downloaded to {args.dir}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
