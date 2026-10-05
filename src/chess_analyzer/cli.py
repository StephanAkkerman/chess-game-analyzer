"""Command-line entry point: ``python -m chess_analyzer USERNAME``."""

from __future__ import annotations

import argparse
import os
import sys
from contextlib import ExitStack

import chess
import chess.engine
import chess.pgn

from chess_analyzer.engine import (
    DEFAULT_DEPTH,
    DEFAULT_MAX_TIME,
    analyze_game,
    default_threads,
    find_engine,
    make_limit,
    open_engine,
    open_tablebase,
)
from chess_analyzer.fetch import ChessComClient
from chess_analyzer.openings import (
    LichessExplorer,
    OpeningSource,
    PolyglotBook,
    book_plies,
    find_deviation,
)
from chess_analyzer.parse import player_color, read_games
from chess_analyzer.report import format_game_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chess-analyzer",
        description=(
            "Download your Chess.com games and analyse them with Stockfish "
            "and an opening book."
        ),
    )
    parser.add_argument("username", help="Chess.com username to analyse.")
    source = parser.add_argument_group("games")
    source.add_argument(
        "--pgn",
        metavar="FILE",
        help="Read games from a local PGN file instead of the Chess.com API.",
    )
    source.add_argument(
        "--months",
        type=int,
        default=1,
        help="Number of recent monthly archives to search (default: 1).",
    )
    source.add_argument(
        "--max-games",
        type=int,
        default=5,
        help="Maximum number of games to analyse (default: 5).",
    )
    source.add_argument(
        "--time-class",
        choices=["bullet", "blitz", "rapid", "daily"],
        help="Only analyse games of this time class.",
    )

    engine = parser.add_argument_group("engine")
    engine.add_argument(
        "--engine",
        metavar="PATH",
        help="Stockfish binary (default: $STOCKFISH_PATH or stockfish on PATH).",
    )
    engine.add_argument(
        "--depth",
        type=int,
        default=DEFAULT_DEPTH,
        help=f"Search depth per position (default: {DEFAULT_DEPTH}).",
    )
    engine.add_argument(
        "--max-time",
        type=float,
        default=DEFAULT_MAX_TIME,
        help="Stop a search after this many seconds even if the depth was not "
        f"reached (default: {DEFAULT_MAX_TIME:g}).",
    )
    engine.add_argument(
        "--time",
        type=float,
        help="Search each position for this many seconds instead of to a depth.",
    )
    engine.add_argument(
        "--threads",
        type=int,
        default=default_threads(),
        help="Engine threads (default: CPU cores minus one).",
    )
    engine.add_argument(
        "--syzygy",
        metavar="DIR",
        default=os.environ.get("SYZYGY_PATH"),
        help="Syzygy tablebase directory; endgames it covers are not searched "
        "(default: $SYZYGY_PATH).",
    )
    engine.add_argument(
        "--no-engine", action="store_true", help="Skip the engine analysis."
    )

    openings = parser.add_argument_group("openings")
    openings.add_argument(
        "--book",
        metavar="FILE",
        help="Polyglot opening book (.bin). Overrides --explorer.",
    )
    openings.add_argument(
        "--explorer",
        choices=["lichess", "masters", "none"],
        default="lichess",
        help="Lichess Opening Explorer database to use (default: lichess).",
    )
    openings.add_argument(
        "--opening-plies",
        type=int,
        default=30,
        help="Only look for a deviation in the first N half-moves (default: 30).",
    )
    return parser


def load_games(args: argparse.Namespace) -> list[chess.pgn.Game]:
    if args.pgn:
        with open(args.pgn, encoding="utf-8") as f:
            games = read_games(f.read())
    else:
        client = ChessComClient()
        raw = client.get_recent_games(
            args.username,
            months=args.months,
            max_games=args.max_games,
            time_class=args.time_class,
        )
        games = [g for r in raw for g in read_games(r["pgn"])]
    return games[: args.max_games]


def open_opening_source(
    args: argparse.Namespace, stack: ExitStack
) -> OpeningSource | None:
    if args.book:
        return stack.enter_context(PolyglotBook(args.book))
    if args.explorer != "none":
        return LichessExplorer(
            database=args.explorer, token=os.environ.get("LICHESS_TOKEN")
        )
    return None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    games = load_games(args)
    if not games:
        print(f"No games found for {args.username}.", file=sys.stderr)
        return 1

    with ExitStack() as stack:
        engine = None
        if not args.no_engine:
            path = find_engine(args.engine)
            if path is None:
                print(
                    "Stockfish not found. Install it, pass --engine PATH or set "
                    "STOCKFISH_PATH, or use --no-engine.",
                    file=sys.stderr,
                )
                return 1
            engine = stack.enter_context(
                open_engine(path, threads=args.threads, syzygy_path=args.syzygy)
            )
        tablebase = (
            stack.enter_context(open_tablebase(args.syzygy)) if args.syzygy else None
        )
        limit = (
            make_limit(depth=None, time=args.time)
            if args.time
            else make_limit(depth=args.depth, max_time=args.max_time)
        )
        source = open_opening_source(args, stack)

        for i, game in enumerate(games):
            color = player_color(game, args.username)
            deviation = (
                find_deviation(game, source, max_ply=args.opening_plies)
                if source
                else None
            )
            # Book moves need no engine time.
            book = book_plies(game, deviation, args.opening_plies) if source else set()
            analysis = (
                analyze_game(game, engine, limit, book_plies=book, tablebase=tablebase)
                if engine
                else None
            )
            if i:
                print("\n" + "=" * 72 + "\n")
            print(
                format_game_report(
                    game, color, analysis, deviation, show_opening=source is not None
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
