"""Download games from the Chess.com Public API and the Lichess API.

Both are free and need no authentication, but Chess.com asks clients to send
a descriptive ``User-Agent`` header. See
https://www.chess.com/news/view/published-data-api and https://lichess.org/api.

Lichess games are converted to the shape of Chess.com's game objects, so the
rest of the package handles games from either site the same way.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import requests

BASE_URL = "https://api.chess.com/pub"
LICHESS_URL = "https://lichess.org"
SITES = ("chess.com", "lichess")
# The most games to download from Lichess when no maximum is given. Lichess
# streams about 20 games a second, so this keeps a request under a minute.
LICHESS_MAX_GAMES = 300
DEFAULT_USER_AGENT = (
    "chess-game-analyzer (https://github.com/StephanAkkerman/chess-game-analyzer)"
)


class ChessComClient:
    """Small client for the parts of the Chess.com Public API we need.

    Parameters
    ----------
    user_agent : str, optional
        Value for the ``User-Agent`` header.
    session : requests.Session, optional
        Session to reuse; a new one is created when omitted.
    timeout : float, optional
        Request timeout in seconds.
    """

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        session: requests.Session | None = None,
        timeout: float = 30,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self.timeout = timeout

    def _get(self, url: str) -> dict:
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def get_archives(self, username: str) -> list[str]:
        """Return the monthly archive URLs for a player, oldest first."""
        url = f"{BASE_URL}/player/{username.lower()}/games/archives"
        return self._get(url).get("archives", [])

    def get_monthly_games(self, username: str, year: int, month: int) -> list[dict]:
        """Return all games a player finished in the given month."""
        url = f"{BASE_URL}/player/{username.lower()}/games/{year:04d}/{month:02d}"
        return self._get(url).get("games", [])

    def get_recent_games(
        self,
        username: str,
        months: int = 1,
        max_games: int | None = None,
        time_class: str | None = None,
    ) -> list[dict]:
        """Return a player's most recent standard chess games, newest first.

        Parameters
        ----------
        username : str
            Chess.com username.
        months : int, optional
            Number of most recent monthly archives to search.
        max_games : int, optional
            Stop after this many games.
        time_class : str, optional
            Only keep games of this time class (``bullet``, ``blitz``,
            ``rapid`` or ``daily``).

        Returns
        -------
        list of dict
            Game objects as returned by the API. Variants such as Chess960 are
            skipped.
        """
        games: list[dict] = []
        for archive_url in reversed(self.get_archives(username)[-months:]):
            monthly = self._get(archive_url).get("games", [])
            monthly = [
                g
                for g in monthly
                if g.get("rules", "chess") == "chess"
                and g.get("pgn")
                and (time_class is None or g.get("time_class") == time_class)
            ]
            monthly.sort(key=lambda g: g.get("end_time", 0), reverse=True)
            games.extend(monthly)
            if max_games is not None and len(games) >= max_games:
                break
        return games if max_games is None else games[:max_games]


# Lichess speeds as Chess.com time classes. Chess.com has no classical class:
# its rapid class covers every game of 10 minutes or more.
LICHESS_TIME_CLASSES = {
    "ultraBullet": "bullet",
    "bullet": "bullet",
    "blitz": "blitz",
    "rapid": "rapid",
    "classical": "rapid",
    "correspondence": "daily",
}
# Lichess game statuses as the Chess.com result of the side that lost, or of
# both sides in a draw.
LICHESS_LOSSES = {
    "mate": "checkmated",
    "resign": "resigned",
    "outoftime": "timeout",
    "timeout": "abandoned",
}
LICHESS_DRAWS = {
    "stalemate": "stalemate",
    "insufficientMaterialClaim": "insufficient",
    "outoftime": "timevsinsufficient",
}
LICHESS_UNFINISHED = {"created", "started", "aborted", "noStart"}


def _month_start(months: int, now: datetime | None = None) -> datetime:
    """Return the first moment of the month ``months - 1`` months ago (UTC).

    This matches Chess.com's monthly archives: ``months=1`` is the current
    month.
    """
    now = now or datetime.now(timezone.utc)
    index = now.year * 12 + now.month - 1 - (months - 1)
    return datetime(index // 12, index % 12 + 1, 1, tzinfo=timezone.utc)


def lichess_game(raw: dict) -> dict | None:
    """Convert a game from the Lichess API to the shape of a Chess.com game.

    Returns ``None`` for games that did not finish. The PGN gets a ``Link``
    tag with the game's URL, as Chess.com's PGNs have.
    """
    status = raw.get("status", "")
    if status in LICHESS_UNFINISHED or not raw.get("pgn"):
        return None
    url = f"{LICHESS_URL}/{raw['id']}"
    winner = raw.get("winner")
    clock = raw.get("clock")
    sides = {}
    for color in ("white", "black"):
        player = raw.get("players", {}).get(color, {})
        if winner == color:
            result = "win"
        elif winner:
            result = LICHESS_LOSSES.get(status, "lose")
        else:
            result = LICHESS_DRAWS.get(status, "agreed")
        name = (player.get("user") or {}).get("name")
        if name is None and "aiLevel" in player:
            name = f"lichess AI level {player['aiLevel']}"
        sides[color] = {
            "username": name or "?",
            "rating": player.get("rating"),
            "result": result,
        }
    game = {
        "url": url,
        "pgn": f'[Link "{url}"]\n' + raw["pgn"],
        "end_time": (raw.get("lastMoveAt") or raw.get("createdAt") or 0) // 1000,
        "time_class": LICHESS_TIME_CLASSES.get(raw.get("speed", "")),
        # As in the PGN's TimeControl tag.
        "time_control": (f"{clock['initial']}+{clock['increment']}" if clock else "-"),
        "rated": raw.get("rated", False),
        "rules": "chess" if raw.get("variant") == "standard" else raw.get("variant"),
        **sides,
    }
    accuracies = {
        color: analysis["accuracy"]
        for color in ("white", "black")
        if "accuracy"
        in (analysis := raw.get("players", {}).get(color, {}).get("analysis") or {})
    }
    if accuracies:
        game["accuracies"] = accuracies
    return game


class LichessClient:
    """Small client for the Lichess game export API.

    Parameters
    ----------
    token : str, optional
        Lichess API token. Not needed, but Lichess streams games faster with
        one.
    user_agent : str, optional
        Value for the ``User-Agent`` header.
    session : requests.Session, optional
        Session to reuse; a new one is created when omitted.
    timeout : float, optional
        Request timeout in seconds.
    """

    def __init__(
        self,
        token: str | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        session: requests.Session | None = None,
        timeout: float = 60,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        self.timeout = timeout

    def get_recent_games(
        self,
        username: str,
        months: int = 1,
        max_games: int | None = None,
        time_class: str | None = None,
    ) -> list[dict]:
        """Return a player's most recent standard chess games, newest first.

        Takes the same arguments as :meth:`ChessComClient.get_recent_games`
        and returns games in the same shape (see :func:`lichess_game`).
        ``months=1`` means games since the start of the current month. At most
        ``LICHESS_MAX_GAMES`` games are downloaded when ``max_games`` is not
        given.
        """
        since = _month_start(months)
        params = {
            "since": int(since.timestamp() * 1000),
            "max": max_games or LICHESS_MAX_GAMES,
            "pgnInJson": "true",
            "clocks": "true",
            "opening": "true",
            "accuracy": "true",
        }
        # Asking for these speeds leaves out variants such as Chess960.
        params["perfType"] = ",".join(
            speed
            for speed, cls in LICHESS_TIME_CLASSES.items()
            if time_class is None or cls == time_class
        )
        response = self.session.get(
            f"{LICHESS_URL}/api/games/user/{username}",
            params=params,
            headers={"Accept": "application/x-ndjson"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        games = []
        for line in response.text.splitlines():
            if not line.strip():
                continue
            game = lichess_game(json.loads(line))
            if game and game["rules"] == "chess":
                games.append(game)
        games.sort(key=lambda g: g["end_time"], reverse=True)
        return games if max_games is None else games[:max_games]


def make_client(
    site: str, lichess_token: str | None = None
) -> ChessComClient | LichessClient:
    """Return the client for ``site`` (``chess.com`` or ``lichess``)."""
    if site == "lichess":
        return LichessClient(token=lichess_token)
    if site == "chess.com":
        return ChessComClient()
    raise ValueError(f"Unknown site {site!r}; choose from {', '.join(SITES)}.")
