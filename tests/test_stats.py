from chess_analyzer.report import format_player_stats
from chess_analyzer.stats import game_record, opening_family, player_stats


def test_opening_family():
    assert opening_family("Sicilian Defense Najdorf Variation") == "Sicilian Defense"
    assert opening_family("Queens Gambit Declined") == "Queens Gambit"
    assert opening_family("Four Knights Game Glek Variation 4...d5") == (
        "Four Knights Game"
    )
    assert opening_family("Sicilian Defense: Najdorf Variation") == "Sicilian Defense"
    assert opening_family("Ruy Lopez") == "Ruy Lopez"


def result(
    date,
    acpl,
    white="alice",
    black="bob",
    score="1-0",
    opening="Sicilian Defense",
    deviation=None,
    categories=None,
    time=None,
    phases=None,
):
    summary = {
        "acpl": acpl,
        "categories": categories or {},
        "phases": phases or {},
        "time": time,
    }
    return {
        "headers": {"White": white, "Black": black, "Result": score, "Date": date},
        "opening": {"name": opening, "checked": True, "deviation": deviation},
        "summary": {"white": summary, "black": {**summary, "acpl": 99}},
    }


def test_game_record_from_players_point_of_view():
    deviation = {"ply": 7, "color": "white"}
    record = game_record(result("2026.10.01", 40, deviation=deviation), "Alice", "x")
    assert record["id"] == "x"
    assert (record["color"], record["opponent"], record["result"]) == (
        "white",
        "bob",
        "win",
    )
    assert (record["acpl"], record["date"], record["left_book"]) == (
        40,
        "2026-10-01",
        4,
    )
    assert record["opponent_left_book"] is None

    record = game_record(result("2026.10.01", 40, deviation=deviation), "bob")
    assert (record["color"], record["acpl"], record["result"]) == (
        "black",
        99,
        "loss",
    )
    assert (record["left_book"], record["opponent_left_book"]) == (None, 4)
    assert game_record(result("2026.10.01", 40), "carol") is None


def records(*results):
    return [game_record(r, "alice") for r in results]


def test_trend_and_order():
    stats = player_stats(
        records(
            result("2026.10.04", 30),
            result("2026.10.01", 70),
            result("2026.10.03", 40),
            result("2026.10.02", 60),
        )
    )
    assert [g["acpl"] for g in stats["games"]] == [70, 60, 40, 30]
    assert stats["acpl"] == 50
    assert stats["trend"] == {
        "direction": "improving",
        "older": 65,
        "recent": 35,
        "games": 2,
    }
    assert "fell from 65" in stats["insights"][0]
    assert player_stats(records(result("2026.10.01", 30)))["trend"] is None


def test_openings_and_book_advice():
    early = {"ply": 5, "color": "white"}  # move 3
    stats = player_stats(
        records(
            result("2026.10.01", 30, deviation=early),
            result(
                "2026.10.02",
                50,
                opening="Sicilian Defense Alapin Variation",
                deviation=early,
                score="0-1",
            ),
            result("2026.10.03", 40, opening="Caro-Kann", deviation=None),
        )
    )
    sicilian = stats["openings"][0]
    assert sicilian["name"] == "Sicilian Defense"
    assert (sicilian["games"], sicilian["left_book"], sicilian["you_left"]) == (
        2,
        3,
        2,
    )
    assert (sicilian["score"], sicilian["advice"]) == (0.5, "study")
    assert any("leave book early in the Sicilian" in i for i in stats["insights"])


def test_categories_phases_and_time():
    time = {
        "moves": 30,
        "average": 5.0,
        "errors": 3,
        "impulsive": 1,
        "time_trouble": 2,
        "wasted_time": 0,
    }
    phases = {
        "opening": {"acpl": 10, "moves": 10},
        "endgame": {"acpl": 90, "moves": 10},
    }
    stats = player_stats(
        records(
            result(
                "2026.10.01",
                40,
                categories={"hung_piece": 2, "positional": 1},
                time=time,
                phases=phases,
            ),
            result("2026.10.02", 40, categories={"hung_piece": 1}, time=time),
        )
    )
    assert stats["categories"] == {"hung_piece": 3, "positional": 1}
    assert stats["phases"]["endgame"] == {"acpl": 90, "moves": 10}
    assert stats["time"]["errors"] == 6
    assert stats["time"]["time_trouble"] == 4
    insights = " ".join(stats["insights"])
    assert "endgame" in insights
    assert "hung a piece" in insights
    assert "time management" in insights

    report = format_player_stats(stats, "alice")
    assert "Summary of 2 games by alice" in report
    assert "hung a piece" in report
    assert "4 mistakes in time trouble" in report
    assert "What to work on" in report


def test_old_results_without_insights():
    old = result("2026.10.01", 40)
    for side in old["summary"].values():
        del side["categories"], side["phases"], side["time"]
    stats = player_stats(records(old))
    assert (stats["categories"], stats["phases"], stats["time"]) == ({}, {}, None)
