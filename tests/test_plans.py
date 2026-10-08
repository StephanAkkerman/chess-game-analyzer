import chess

from chess_analyzer.plans import (
    MISSED,
    NOT_PLAYED,
    PLAYED,
    UNSOUND,
    check_plans,
    opening_breaks,
)
from chess_analyzer.report import format_player_stats
from chess_analyzer.stats import game_record, player_stats

FRENCH_ADVANCE = "e4 e6 d4 d5 e5 c5 c3 Nc6 Nf3 Qb6 a3 f6"
FRENCH_NO_BREAKS = "e4 e6 d4 d5 e5 Nc6 Nf3 Nge7 Bd3 Ng6"


def moves(sans, best=None, classes=None):
    """Serialised moves for the SAN string ``sans``.

    ``best`` and ``classes`` map plies to the engine's move and the classification.
    """
    best, classes = best or {}, classes or {}
    board = chess.Board()
    out = []
    for ply, san in enumerate(sans.split(), start=1):
        move = board.parse_san(san)
        dots = "." if board.turn == chess.WHITE else "..."
        out.append(
            {
                "ply": ply,
                "color": "white" if board.turn == chess.WHITE else "black",
                "label": f"{(ply + 1) // 2}{dots}{san}",
                "san": san,
                "uci": move.uci(),
                "best_uci": best.get(ply),
                "classification": classes.get(ply, "good"),
                "fen_before": board.fen(),
            }
        )
        board.push(move)
    return out


def by_name(checks):
    return {c["name"]: c for c in checks}


def test_opening_breaks_by_name():
    names = [b.label(chess.BLACK) for b in opening_breaks("French Defense", False)]
    assert names == ["...c5", "...f6"]
    assert opening_breaks("Caro-Kann Defence: Advance", chess.BLACK)
    assert opening_breaks("Kings Indian Attack", chess.WHITE) == ()
    assert [b.square for b in opening_breaks("Semi-Slav Defense", chess.BLACK)] == [
        "c5",
        "e5",
    ]
    assert opening_breaks("Bongcloud", chess.WHITE) == ()
    assert opening_breaks(None, chess.WHITE) == ()


def test_played_and_unsound_breaks():
    checks = by_name(
        check_plans(
            "French Defense Advance Variation",
            "black",
            moves(FRENCH_ADVANCE, classes={12: "mistake"}),
        )
    )
    assert (checks["...c5"]["status"], checks["...c5"]["label"]) == (PLAYED, "3...c5")
    assert checks["...f6"]["status"] == UNSOUND
    assert checks["...c5"]["late"] is False


def test_missed_and_late_breaks():
    # The engine wanted ...c5 on move 3 and ...f6 on move 5, neither played.
    game = moves(FRENCH_NO_BREAKS, best={6: "c7c5", 10: "f7f6"})
    checks = by_name(check_plans("French Defense", "black", game))
    assert (checks["...c5"]["status"], checks["...c5"]["ply"]) == (MISSED, 6)
    assert checks["...f6"]["status"] == MISSED

    # Played on move 7 after the engine wanted it on move 3.
    game = moves(FRENCH_NO_BREAKS + " Bf4 Na5 O-O c5", best={6: "c7c5"})
    check = by_name(check_plans("French Defense", "black", game))["...c5"]
    assert (check["status"], check["late"]) == (PLAYED, True)

    # White's f5 break is not Black's concern; captures onto c5 don't count.
    assert check_plans("French Defense", "white", game)[0]["status"] == NOT_PLAYED
    game = moves("e4 e6 d4 d5 e5 b6 c4 Bb7 c5 bxc5")
    assert by_name(check_plans("French", "black", game))["...c5"]["status"] == (
        NOT_PLAYED
    )


def french(date, sans, score, **kwargs):
    summary = {"acpl": 30, "categories": {}, "phases": {}, "time": None}
    return {
        "headers": {"White": "bob", "Black": "alice", "Result": score, "Date": date},
        "opening": {"name": "French Defense", "checked": True, "deviation": None},
        "moves": moves(sans, **kwargs),
        "summary": {"white": summary, "black": summary},
    }


def test_plan_stats_and_advice():
    missed = {"best": {6: "c7c5"}}
    records = [
        game_record(r, "alice")
        for r in (
            french("2026.10.01", FRENCH_ADVANCE, "0-1"),
            french("2026.10.02", FRENCH_ADVANCE, "1/2-1/2"),
            french("2026.10.03", FRENCH_NO_BREAKS, "1-0", **missed),
            french("2026.10.04", FRENCH_NO_BREAKS, "1-0", **missed),
        )
    ]
    stats = player_stats(records)
    plans = by_name(stats["openings"][0]["plans"])
    c5 = plans["...c5"]
    assert (c5["games"], c5["played"], c5["missed"]) == (4, 2, 2)
    assert (c5["score_with"], c5["score_without"]) == (0.75, 0.0)
    assert plans["...f6"]["played"] == 2
    games = stats["openings"][0]["game_list"]
    assert games[0]["plans"][0] == {
        "name": "...c5",
        "status": MISSED,
        "ply": 6,
        "label": "3...Nc6",
    }
    insights = " ".join(stats["insights"])
    assert "engine wanted the ...c5 break in 2 of your 4 games" in insights
    assert "you score 75% when you play ...c5 and 0% when you don't" in insights
    report = format_player_stats(stats, "alice")
    assert "...c5  break: played in 2 of 4, missed in 2, 75% score with it" in report
