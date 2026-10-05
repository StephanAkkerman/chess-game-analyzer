"""Download games from the Chess.com Public API.

The API is free and needs no authentication, but Chess.com asks clients to send
a descriptive ``User-Agent`` header. See https://www.chess.com/news/view/published-data-api.
"""

from __future__ import annotations

import requests

BASE_URL = "https://api.chess.com/pub"
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
