"""Read PGN text into ``python-chess`` game objects."""

from __future__ import annotations

import io

import chess
import chess.pgn


def read_games(pgn_text: str) -> list[chess.pgn.Game]:
    """Parse every game in a PGN string.

    Parameters
    ----------
    pgn_text : str
        One or more games in PGN format.

    Returns
    -------
    list of chess.pgn.Game
        The parsed games, in file order.
    """
    stream = io.StringIO(pgn_text)
    games = []
    while (game := chess.pgn.read_game(stream)) is not None:
        games.append(game)
    return games


def player_color(game: chess.pgn.Game, username: str) -> chess.Color | None:
    """Return the colour ``username`` played, or ``None`` if they did not play."""
    name = username.lower()
    if game.headers.get("White", "").lower() == name:
        return chess.WHITE
    if game.headers.get("Black", "").lower() == name:
        return chess.BLACK
    return None


def opening_name(game: chess.pgn.Game) -> str | None:
    """Return the opening name from the PGN headers, if there is one.

    Chess.com does not write an ``Opening`` tag but links to the opening in
    ``ECOUrl``; the last path segment of that URL is used as a fallback.
    """
    if name := game.headers.get("Opening"):
        return name
    if url := game.headers.get("ECOUrl"):
        return url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
    return None
