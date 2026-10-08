"""Compare a player with other players at their rating.

Losing 60 centipawns per move in the endgame says little on its own; knowing
that players at your rating lose 40 tells you where to start. This module
finds such players ("peers") on Chess.com and compares the two groups.

Finding peers needs no engine. Chess.com pairs players of the same rating, so
the player's recent opponents are peers, and so are many of their opponents.
Starting from them, :func:`collect_peer_games` walks from player to player,
always visiting the one closest to the target rating next, and keeps games
of the player's usual time control in which at least one side is rated
within :data:`RATING_WINDOW` of the target.

Two kinds of numbers are compared:

- **Clock numbers**, read from Chess.com's clock times, so they are known for
  every game in the sample without any analysis: time per move, how often a
  player falls below 10% of the starting clock and how often they lose on
  time. Chess.com's own accuracy is used for the games that someone ran a
  Game Review on.
- **Engine numbers**, which need the peer games to be analysed like the
  player's own: centipawn loss per game phase, blunders, missed and allowed
  tactics and critical moments found. Analysing is costly on a Raspberry Pi
  or a phone, so only a few peer games are analysed at a time and the
  comparison grows with each batch.
"""

from __future__ import annotations

import heapq
from collections import Counter
from datetime import datetime, timezone

import chess
import requests

from chess_analyzer.engine import FOUND
from chess_analyzer.insights import (
    ALLOWED_MATE,
    ALLOWED_TACTIC,
    HUNG_PIECE,
    MISSED_MATE,
    MISSED_TACTIC,
    PHASES,
    TIME_TROUBLE_SHARE,
    move_times,
)
from chess_analyzer.parse import read_games

# Peers are rated within this many points of the target rating.
RATING_WINDOW = 100
# Peer games to collect, and the most monthly archives to download for them.
PEER_GAMES = 100
MAX_ARCHIVES = 25
# The most games to keep from one player's archive, so a few players who play
# a lot don't make up the whole sample.
GAMES_PER_PLAYER = 8
# Games of the player's own to compare, newest first.
OWN_GAMES = 50

LOWER, HIGHER = "lower", "higher"
# Each metric: key, label, group, which direction is better (None when
# neither is), its kind (how it is shown and compared) and the sample it
# needs. ``cpl`` and ``rate`` count moves, ``share`` and ``points`` count
# games or critical moments.
METRICS = (
    ("opening", "Opening CPL", "accuracy", LOWER, "cpl"),
    ("middlegame", "Middlegame CPL", "accuracy", LOWER, "cpl"),
    ("endgame", "Endgame CPL", "accuracy", LOWER, "cpl"),
    ("accuracy", "Chess.com accuracy", "accuracy", HIGHER, "points"),
    ("blunders", "Blunders per 100 moves", "tactics", LOWER, "rate"),
    ("missed_tactics", "Missed tactics per 100 moves", "tactics", LOWER, "rate"),
    ("allowed_tactics", "Tactics allowed per 100 moves", "tactics", LOWER, "rate"),
    ("critical", "Critical moments found", "tactics", HIGHER, "share"),
    ("seconds", "Seconds per move", "time", None, "seconds"),
    ("time_trouble", "Games in time trouble", "time", LOWER, "share"),
    ("timeouts", "Games lost on time", "time", LOWER, "share"),
)
METRIC_LABELS = {m[0]: m[1] for m in METRICS}
# Smallest samples to compare: moves for ``cpl`` and ``rate``, games or
# critical moments for the others, for the player and for the peers.
MIN_MOVES = (30, 100)
MIN_COUNT = (5, 10)
# A difference counts when it is at least this large. For ``cpl`` and
# ``rate`` both the relative and the absolute difference must be reached.
MIN_RELATIVE = 0.2
MIN_ABSOLUTE = {"cpl": 5, "rate": 0.5, "share": 0.1, "points": 3}

MISSED = (MISSED_TACTIC, MISSED_MATE)
ALLOWED = (HUNG_PIECE, ALLOWED_TACTIC, ALLOWED_MATE)
PHASE_ADVICE = {
    "opening": "openings",
    "middlegame": "middlegame plans and tactics",
    "endgame": "endgames",
}


def _month(end_time: int | None) -> tuple[int, int]:
    when = datetime.fromtimestamp(end_time or 0, tz=timezone.utc)
    if not end_time:
        when = datetime.now(tz=timezone.utc)
    return when.year, when.month


def _color_of(raw: dict, username: str) -> str | None:
    for color in ("white", "black"):
        if raw.get(color, {}).get("username", "").lower() == username.lower():
            return color
    return None


def _usable(raw: dict, time_control: str) -> bool:
    return (
        raw.get("rules", "chess") == "chess"
        and bool(raw.get("pgn"))
        and raw.get("time_control") == time_control
        and raw.get("rated", True)
    )


def time_controls(games: list[dict]) -> list[dict]:
    """Count the time controls of a player's games, most played first.

    Daily games come last: they have no clock to compare.
    """
    counts = Counter(
        g.get("time_control")
        for g in games
        if g.get("rules", "chess") == "chess" and g.get("time_control")
    )
    return [
        {"time_control": tc, "time_class": _time_class(games, tc), "games": n}
        for tc, n in sorted(counts.items(), key=lambda i: ("/" in i[0], -i[1]))
    ]


def _time_class(games: list[dict], time_control: str) -> str | None:
    return next(
        (g.get("time_class") for g in games if g.get("time_control") == time_control),
        None,
    )


def current_rating(games: list[dict], username: str, time_control: str) -> int | None:
    """Return the player's rating in their newest game of ``time_control``."""
    newest = sorted(games, key=lambda g: g.get("end_time", 0), reverse=True)
    for raw in newest:
        color = _color_of(raw, username)
        if color and raw.get("time_control") == time_control:
            return raw[color].get("rating")
    return None


def clock_profile(game, color: chess.Color) -> dict | None:
    """Summarise how one side used the clock, or ``None`` without clocks.

    Returns the number of timed moves, the seconds spent on them and whether
    the side ever had less than 10% of the starting clock left.
    """
    times, base = move_times(game)
    if not base:
        return None
    nodes = list(game.mainline())
    own = [
        t
        for t, node in zip(times, nodes)
        if t is not None and node.board().turn != color
    ]
    if not own:
        return None
    return {
        "moves": len(own),
        "seconds": round(sum(t.spent for t in own), 1),
        "time_trouble": min(t.clock for t in own) < TIME_TROUBLE_SHARE * base,
    }


def side_profile(raw: dict, color: str, game=None) -> dict:
    """Describe one side of a Chess.com game without an engine.

    ``game`` is the parsed PGN; it is read from ``raw`` when omitted.
    """
    if game is None:
        games = read_games(raw.get("pgn", ""))
        game = games[0] if games else None
    side = raw.get(color, {})
    turn = chess.WHITE if color == "white" else chess.BLACK
    accuracy = (raw.get("accuracies") or {}).get(color)
    return {
        "color": color,
        "username": side.get("username"),
        "rating": side.get("rating"),
        "accuracy": accuracy,
        "timeout": side.get("result") == "timeout",
        "clock": clock_profile(game, turn) if game else None,
    }


def collect_peer_games(
    client,
    username: str,
    games: list[dict],
    time_control: str,
    target: int,
    window: int = RATING_WINDOW,
    limit: int = PEER_GAMES,
    max_archives: int = MAX_ARCHIVES,
) -> list[dict]:
    """Find Chess.com games of players rated around ``target``.

    Parameters
    ----------
    client : ChessComClient
        Client for the Chess.com Public API.
    username : str
        The player being compared; their own games are left out.
    games : list of dict
        The player's recent games, as returned by the API. Their opponents
        are where the search starts.
    time_control : str
        Only games of this time control (e.g. ``"180"``) are kept.
    target : int
        The rating to compare with, usually the player's own.
    window : int, optional
        How far from ``target`` a peer's rating may be.
    limit : int, optional
        Stop after this many games.
    max_archives : int, optional
        The most monthly archives to download.

    Returns
    -------
    list of dict
        Each with the game's ``url``, ``pgn``, ``end_time`` and ``sides``:
        the :func:`side_profile` of each player rated within the window.
    """
    name = username.lower()
    # Players to visit, closest to the target rating first, with the month in
    # which they played at that rating.
    todo: list[tuple[int, int, str, tuple[int, int]]] = []
    seen = {name}
    order = 0

    def visit_later(player: str | None, rating: int | None, end_time: int | None):
        nonlocal order
        if not player or rating is None or player.lower() in seen:
            return
        seen.add(player.lower())
        order += 1
        heapq.heappush(todo, (abs(rating - target), order, player, _month(end_time)))

    for raw in sorted(games, key=lambda g: g.get("end_time", 0), reverse=True):
        color = _color_of(raw, username)
        if color and _usable(raw, time_control):
            other = raw["black" if color == "white" else "white"]
            visit_later(other.get("username"), other.get("rating"), raw.get("end_time"))

    found: dict[str, dict] = {}
    archives = 0
    while todo and len(found) < limit and archives < max_archives:
        _, _, player, (year, month) = heapq.heappop(todo)
        archives += 1
        try:
            monthly = client.get_monthly_games(player, year, month)
        except (requests.RequestException, ValueError):
            continue
        kept = 0
        monthly = sorted(monthly, key=lambda g: g.get("end_time", 0), reverse=True)
        for raw in monthly:
            if not _usable(raw, time_control) or _color_of(raw, username):
                continue
            color = _color_of(raw, player)
            if color:
                other = raw["black" if color == "white" else "white"]
                visit_later(
                    other.get("username"), other.get("rating"), raw.get("end_time")
                )
            url = raw.get("url")
            if not url or url in found or kept >= GAMES_PER_PLAYER:
                continue
            sides = [
                c
                for c in ("white", "black")
                if raw.get(c, {}).get("rating") is not None
                and abs(raw[c]["rating"] - target) <= window
            ]
            if not sides:
                continue
            parsed = read_games(raw["pgn"])
            if not parsed or parsed[0].errors:
                continue
            found[url] = {
                "url": url,
                "pgn": raw["pgn"],
                "end_time": raw.get("end_time"),
                "sides": {c: side_profile(raw, c, parsed[0]) for c in sides},
            }
            kept += 1
            if len(found) >= limit:
                break
    return list(found.values())


def own_profiles(games: list[dict], username: str, time_control: str) -> list[dict]:
    """Return :func:`side_profile` for the player's newest games of a time control."""
    profiles = []
    newest = sorted(games, key=lambda g: g.get("end_time", 0), reverse=True)
    for raw in newest:
        color = _color_of(raw, username)
        if color and _usable(raw, time_control):
            profiles.append(side_profile(raw, color))
            if len(profiles) >= OWN_GAMES:
                break
    return profiles


def engine_profile(result: dict, color: str) -> dict:
    """Extract the numbers that need an engine from one analysed side.

    ``result`` is an analysis as produced by
    :func:`chess_analyzer.serialize.result_to_dict`.
    """
    summary = result["summary"][color]
    counts = summary.get("counts") or {}
    categories = summary.get("categories") or {}
    moves = result.get("moves", [])
    critical = [
        moves[ply - 1] for ply in summary.get("critical") or [] if 0 < ply <= len(moves)
    ]
    return {
        "decisions": sum(n for k, n in counts.items() if k not in ("book", "forced")),
        "blunders": counts.get("blunder", 0),
        "missed_tactics": sum(categories.get(c, 0) for c in MISSED),
        "allowed_tactics": sum(categories.get(c, 0) for c in ALLOWED),
        "phases": summary.get("phases") or {},
        "critical": len(critical),
        "critical_found": sum(m["classification"] in FOUND for m in critical),
    }


def summarise(profiles: list[dict], engine: list[dict]) -> dict:
    """Pool the numbers of one group of players.

    ``profiles`` are clock profiles (:func:`side_profile`), ``engine``
    profiles (:func:`engine_profile`) of the analysed sides. Each metric is
    returned as ``{"value": ..., "n": ...}``, where ``n`` is the sample it
    rests on (see :data:`METRICS`), or ``None`` without data.
    """
    out: dict[str, dict | None] = {}
    timed = [p["clock"] for p in profiles if p.get("clock")]
    moves = sum(c["moves"] for c in timed)
    out["seconds"] = moves and {
        "value": round(sum(c["seconds"] for c in timed) / moves, 1),
        "n": len(timed),
    }
    out["time_trouble"] = timed and {
        "value": sum(c["time_trouble"] for c in timed) / len(timed),
        "n": len(timed),
    }
    out["timeouts"] = profiles and {
        "value": sum(p["timeout"] for p in profiles) / len(profiles),
        "n": len(profiles),
    }
    accuracies = [p["accuracy"] for p in profiles if p.get("accuracy") is not None]
    out["accuracy"] = accuracies and {
        "value": round(sum(accuracies) / len(accuracies), 1),
        "n": len(accuracies),
    }
    for phase in PHASES:
        data = [e["phases"][phase] for e in engine if phase in e["phases"]]
        n = sum(d["moves"] for d in data)
        out[phase] = n and {
            "value": round(sum(d["acpl"] * d["moves"] for d in data) / n),
            "n": n,
        }
    decisions = sum(e["decisions"] for e in engine)
    for key in ("blunders", "missed_tactics", "allowed_tactics"):
        out[key] = decisions and {
            "value": round(100 * sum(e[key] for e in engine) / decisions, 1),
            "n": decisions,
        }
    moments = sum(e["critical"] for e in engine)
    out["critical"] = moments and {
        "value": sum(e["critical_found"] for e in engine) / moments,
        "n": moments,
    }
    return {k: v or None for k, v in out.items()}


def verdict(kind: str, better: str | None, you: dict, peers: dict) -> str | None:
    """Return ``ahead``, ``behind`` or ``level``, or ``None`` if the samples
    are too small or neither direction is better."""
    if better is None or not you or not peers:
        return None
    minimum = MIN_MOVES if kind in ("cpl", "rate") else MIN_COUNT
    if you["n"] < minimum[0] or peers["n"] < minimum[1]:
        return None
    diff = you["value"] - peers["value"]
    if abs(diff) < MIN_ABSOLUTE[kind]:
        return "level"
    if kind in ("cpl", "rate") and abs(diff) < MIN_RELATIVE * max(
        you["value"], peers["value"]
    ):
        return "level"
    return "ahead" if (diff < 0) == (better == LOWER) else "behind"


def compare(you: dict, peers: dict) -> list[dict]:
    """Put the player's numbers next to their peers' (see :func:`summarise`)."""
    rows = []
    for key, label, group, better, kind in METRICS:
        if not you.get(key) and not peers.get(key):
            continue
        rows.append(
            {
                "key": key,
                "label": label,
                "group": group,
                "kind": kind,
                "better": better,
                "you": you.get(key),
                "peers": peers.get(key),
                "verdict": verdict(kind, better, you.get(key), peers.get(key)),
            }
        )
    return rows


def _format(row: dict, side: str) -> str:
    value = row[side]["value"]
    return f"{value:.0%}" if row["kind"] == "share" else f"{value:g}"


def peer_insights(rows: list[dict], target: int, own_rating: int | None) -> list[str]:
    """Say in plain sentences where the player stands against their peers."""
    who = (
        "players at your rating"
        if own_rating is None or abs(target - own_rating) < RATING_WINDOW
        else f"players rated around {target}"
    )
    by_key = {r["key"]: r for r in rows if r["verdict"]}
    lines = []

    phases = [by_key[p] for p in PHASES if p in by_key]
    behind = [r for r in phases if r["verdict"] == "behind"]
    ahead = [r for r in phases if r["verdict"] == "ahead"]
    if behind:
        worst = max(behind, key=lambda r: r["you"]["value"] / r["peers"]["value"])
        lines.append(
            f"Your {worst['key']} is your weakest phase compared with {who}: you "
            f"lose {_format(worst, 'you')} centipawns per move, they lose "
            f"{_format(worst, 'peers')}. Study {PHASE_ADVICE[worst['key']]} first."
        )
    if ahead:
        names = " and ".join(r["key"] for r in ahead)
        if behind:
            verb = "are" if len(ahead) > 1 else "is"
            topics = " and ".join(PHASE_ADVICE[r["key"]] for r in ahead)
            lines.append(
                f"Your {names} {verb} already better than theirs, so studying "
                f"{topics} gains you less right now."
            )
        else:
            lines.append(f"You play the {names} better than {who}.")
    if len(phases) == len(PHASES) and not behind:
        line = f"No phase of the game lags behind {who}"
        if who == "players at your rating":
            line += (
                ": to see what separates you from the next level, compare "
                "yourself with stronger players"
            )
        lines.append(line + ".")

    templates = {
        "missed_tactics": "You miss more tactics than {who} ({you} against "
        "{peers} per 100 moves): solve puzzles to spot forcing moves.",
        "allowed_tactics": "You allow more tactics than {who} ({you} against "
        "{peers} per 100 moves): check your opponent's checks and captures "
        "before each move.",
        "blunders": "You blunder more often than {who} ({you} against {peers} "
        "per 100 moves).",
        "critical": "{who_cap} find the only good move in critical moments more "
        "often than you ({peers} against {you}).",
        "time_trouble": "You get into time trouble in {you} of your games, "
        "{who} in {peers}: play faster in the opening and middlegame.",
        "timeouts": "You lose {you} of your games on time, {who} {peers}.",
        "accuracy": "Your Chess.com accuracy is {you}, against {peers} for {who}.",
    }
    for key, template in templates.items():
        row = by_key.get(key)
        if row and row["verdict"] == "behind":
            lines.append(
                template.format(
                    who=who,
                    who_cap=who[0].upper() + who[1:],
                    you=_format(row, "you"),
                    peers=_format(row, "peers"),
                )
            )
    return lines
