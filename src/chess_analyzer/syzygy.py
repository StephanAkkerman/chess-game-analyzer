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
from collections.abc import Sequence
from pathlib import Path

import chess
import chess.syzygy
import requests

# Mirrors are tried in order for each file.
DEFAULT_URLS = (
    "https://tablebase.lichess.ovh/tables/standard/3-4-5/",
    "http://tablebase.sesse.net/syzygy/3-4-5/",
)
# First bytes of valid WDL and DTZ files, to reject error pages and the like.
MAGIC = {".rtbw": chess.Board.tbw_magic, ".rtbz": chess.Board.tbz_magic}


def table_files(pieces: int = 5, dtz: bool = True) -> list[str]:
    """Return the file names of all tables with up to ``pieces`` pieces."""
    suffixes = [".rtbw", ".rtbz"] if dtz else [".rtbw"]
    names = sorted(chess.syzygy.tablenames(piece_count=pieces))
    return [name + suffix for name in names for suffix in suffixes]


def _fetch(session: requests.Session, url: str, target: Path) -> None:
    """Download ``url`` to ``target`` if it is a valid table file."""
    partial = target.with_suffix(target.suffix + ".part")
    try:
        with session.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(partial, "wb") as f:
                f.writelines(r.iter_content(chunk_size=1 << 20))
        with open(partial, "rb") as f:
            if f.read(4) != MAGIC[target.suffix]:
                raise ValueError(f"{url} is not a Syzygy table")
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)


def download(
    directory: str | Path,
    urls: Sequence[str] = DEFAULT_URLS,
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

    Raises
    ------
    RuntimeError
        If a file could not be downloaded from any mirror.
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
        errors = []
        for base in urls:
            try:
                _fetch(session, base.rstrip("/") + "/" + name, target)
                break
            except (requests.RequestException, ValueError) as exc:
                errors.append(str(exc))
        else:
            raise RuntimeError(f"Could not download {name}: " + "; ".join(errors))
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
    parser.add_argument(
        "--url",
        action="append",
        help="Mirror to download from; may be repeated (default: lichess.ovh, "
        "then sesse.net).",
    )
    args = parser.parse_args(argv)
    try:
        count = download(args.dir, args.url or DEFAULT_URLS, dtz=not args.wdl_only)
    except RuntimeError as exc:
        print(f"Download failed: {exc}", file=sys.stderr)
        return 1
    print(f"Done: {count} files downloaded to {args.dir}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
