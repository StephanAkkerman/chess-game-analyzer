import chess

from chess_analyzer.takeaway import game_takeaway, outcome, turning_point, win_chance


def move(ply, color, classification, before, after, board=None, uci=None, **extra):
    board = board or chess.Board()
    uci = uci or next(iter(board.legal_moves)).uci()
    return {
        "ply": ply,
        "color": color,
        "classification": classification,
        "eval_before": before,
        "eval_after": after,
        "fen_before": board.fen(),
        "uci": uci,
        "san": board.san(chess.Move.from_uci(uci)),
        "label": f"{ply}. {board.san(chess.Move.from_uci(uci))}",
        **extra,
    }


def scholars_mate():
    """The Scholar's Mate up to 3...Nf6??, with White's mate as the reply."""
    board = chess.Board()
    moves = []
    for ply, san in enumerate(("e4", "e5", "Bc4", "Nc6", "Qh5"), 1):
        color = "white" if board.turn else "black"
        moves.append(
            move(ply, color, "book", None, None, board, board.parse_san(san).uci())
        )
        board.push_san(san)
    return moves + [
        move(
            6,
            "black",
            "blunder",
            -30,
            10_000,
            board,
            "g8f6",
            best_uci="g7g6",
            reply_uci="h5f7",
            category="allowed_mate",
            time_flag="impulsive",
        )
    ]


def test_win_chance():
    assert win_chance(0) == 50
    assert win_chance(10_000) == win_chance(1_000) > 95
    assert win_chance(-300) < 30


def test_outcome():
    assert outcome({"headers": {"Result": "1-0"}}, "white") == "won"
    assert outcome({"headers": {"Result": "1-0"}}, "black") == "lost"
    assert outcome({"headers": {"Result": "1/2-1/2"}}, "black") == "drew"
    assert outcome({"headers": {"Result": "*"}}, "white") is None


def test_turning_point_is_the_costliest_error():
    small = move(2, "black", "mistake", 0, 150)
    big = move(4, "black", "blunder", 100, 600)
    # Losing 400 centipawns from equal matters more than from far behind.
    late = move(6, "black", "blunder", 900, 1_400)
    moves = [small, big, late, move(1, "white", "blunder", 0, -900)]
    assert turning_point(moves, "black") is big
    assert turning_point(moves, "white")["ply"] == 1
    assert turning_point([small], "white") is None


def test_takeaway_of_a_lost_game():
    result = {"headers": {"Result": "1-0"}, "moves": scholars_mate()}
    takeaway = game_takeaway(result, "black")
    assert takeaway["kind"] == "turning_point"
    assert takeaway["outcome"] == "lost"
    assert takeaway["title"] == "What could have saved the game"
    assert takeaway["ply"] == 6
    assert "Nf6 is a blunder" in takeaway["text"]
    assert "Better was g6" in takeaway["text"]
    assert "Qh5 set up the mate" in takeaway["advice"]
    assert "checks and threats against your king" in takeaway["advice"]
    assert "under 3 seconds" in takeaway["advice"]
    hidden = game_takeaway(result, "black", hide_best=True)
    assert "g6" not in hidden["text"]


def test_takeaway_of_a_clean_game():
    moves = [
        move(1, "white", "book", None, None),
        move(3, "white", "best", 20, 25),
        move(5, "white", "great", 30, 30, critical=True),
        move(7, "white", "good", 30, 20),
    ]
    result = {
        "headers": {"Result": "1-0"},
        "moves": moves,
        "summary": {"white": {"acpl": 5, "counts": {"inaccuracy": 0}}},
    }
    takeaway = game_takeaway(result, "white")
    assert takeaway["kind"] == "clean"
    assert takeaway["title"] == "A clean win"
    assert takeaway["ply"] is None
    advice = takeaway["advice"]
    assert advice.startswith("Keep doing this: finding the only good move with 5.")
    assert "top choice in 2 of your 3 moves" in advice


def test_clean_loss_on_time():
    result = {
        "headers": {"Result": "0-1", "Termination": "Black won on time"},
        "moves": [move(1, "white", "inaccuracy", 0, -60)],
        "summary": {"white": {"acpl": 40, "counts": {"inaccuracy": 1}}},
    }
    takeaway = game_takeaway(result, "white")
    assert takeaway["title"] == "Lost on the clock, not on the board"
    assert "average loss to 40 centipawns" in takeaway["advice"]
