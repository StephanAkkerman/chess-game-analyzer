# Chess Game Analyzer

---
<p align="center">
  <img alt="GitHub Actions Workflow Status" src="https://img.shields.io/github/actions/workflow/status/StephanAkkerman/chess-game-analyzer/pyversions.yml?label=python%203.10%20%7C%203.11%20%7C%203.12%20%7C%203.13&logo=python&style=flat-square">
  <img src="https://img.shields.io/github/license/StephanAkkerman/chess-game-analyzer.svg?color=brightgreen" alt="License">
  <a href="https://github.com/psf/black"><img src="https://img.shields.io/badge/code%20style-black-000000.svg" alt="Code style: black"></a>
</p>

## Introduction

Chess Game Analyzer downloads your recent games from Chess.com, runs Stockfish over every move and checks the opening against established theory. For each game it reports:

- your average centipawn loss and how many of your moves were best, good, inaccuracies, mistakes or blunders;
- your biggest mistakes, with the evaluation before and after and the move Stockfish preferred;
- the move where you or your opponent left opening theory, and the book moves that score best from that position.

## Table of Contents 🗂

- [How it works](#how-it-works)
- [Installation](#installation)
- [Usage](#usage)
- [Citation](#citation)
- [Contributing](#contributing)
- [License](#license)

## How it works 🔑

1. **Fetch the games.** Games come from the free [Chess.com Public API](https://www.chess.com/news/view/published-data-api), which needs no authentication (`chess_analyzer.fetch`). Chess variants such as Chess960 are skipped.
2. **Parse the PGNs.** [`python-chess`](https://python-chess.readthedocs.io/) reads the PGN and replays the game (`chess_analyzer.parse`).
3. **Run the engine.** Stockfish evaluates every position over UCI for a fixed time or depth (`chess_analyzer.engine`). Each move's centipawn loss is the drop in evaluation for the side that moved. Evaluations are capped at ±10 pawns, so going from "mate in 5" to "+15" does not count as a blunder. A loss of 50 centipawns or more is an inaccuracy, 100 or more a mistake, and 300 or more a blunder.
4. **Check the opening.** The game's first moves are compared with a local [Polyglot](https://www.chessprogramming.org/PolyGlot) book (`.bin`) or the [Lichess Opening Explorer](https://lichess.org/api#tag/Opening-Explorer) (`chess_analyzer.openings`). The first move that is not in the book is reported, along with the book alternatives, ranked by how well they score for the side to move.

## Installation ⚙️

You need Python 3.10 or later and a [Stockfish](https://stockfishchess.org/download/) binary.

```bash
pip install -r requirements.txt
```

or

```bash
pip install git+https://github.com/StephanAkkerman/chess-game-analyzer.git
```

Stockfish is found automatically if `stockfish` is on your `PATH` (for example after `apt install stockfish` or `brew install stockfish`). Otherwise, pass `--engine /path/to/stockfish` or set `STOCKFISH_PATH`.

## Usage ⌨️

Analyse your five most recent games from this month:

```bash
python -m chess_analyzer YOUR_USERNAME
```

When the package is installed with pip, the `chess-analyzer` command does the same. From a clone without installing, run it with `PYTHONPATH=src`.

Some useful options:

| Option | Description |
| --- | --- |
| `--months N` | Search the last N monthly archives (default 1). |
| `--max-games N` | Analyse at most N games (default 5). |
| `--time-class blitz` | Only analyse `bullet`, `blitz`, `rapid` or `daily` games. |
| `--pgn FILE` | Analyse games from a local PGN file instead of downloading them. |
| `--time 0.5` / `--depth 18` | Engine time in seconds per position, or a fixed search depth. |
| `--no-engine` | Only run the opening check. |
| `--book FILE` | Use a local Polyglot opening book instead of the Lichess explorer. |
| `--explorer lichess\|masters\|none` | Lichess explorer database to use, or `none` to skip the opening check. |
| `--opening-plies N` | How many half-moves count as the opening (default 30). |

If the Lichess explorer asks for authentication, create a [personal API token](https://lichess.org/account/oauth/token) and set it as `LICHESS_TOKEN`.

Example output:

```text
Alice (1500) vs bob (1450)  1-0
2026.10.01  600  Bishops Opening
https://www.chess.com/game/live/1

Opening
  You left theory with 3...Nf6.
  Book moves in that position:
    g6       55% score over 200 games

Black: average centipawn loss 340
  2 best, 0 good, 0 inaccuracy, 0 mistake, 1 blunder
  Biggest mistakes:
    3...Nf6      blunder  -0.20 -> +M1  best was g6
```

The modules can also be used from Python:

```python
import chess.engine

from chess_analyzer.engine import analyze_game, find_engine
from chess_analyzer.fetch import ChessComClient
from chess_analyzer.parse import read_games

raw = ChessComClient().get_recent_games("YOUR_USERNAME", max_games=1)
game = read_games(raw[0]["pgn"])[0]
with chess.engine.SimpleEngine.popen_uci(find_engine()) as engine:
    analysis = analyze_game(game, engine, chess.engine.Limit(time=0.5))
```

## Citation ✍️

If you use this project in your research, please cite as follows:

```bibtex
@misc{chess_game_analyzer,
  author  = {Stephan Akkerman},
  title   = {Chess Game Analyzer},
  year    = {2026},
  publisher = {GitHub},
  journal = {GitHub repository},
  howpublished = {\url{https://github.com/StephanAkkerman/chess-game-analyzer}}
}
```

## Contributing 🛠

Contributions are welcome! If you have a feature request, bug report, or proposal for code refactoring, please feel free to open an issue on GitHub. We appreciate your help in improving this project.\
![https://github.com/StephanAkkerman/chess-game-analyzer/graphs/contributors](https://contributors-img.firebaseapp.com/image?repo=StephanAkkerman/chess-game-analyzer)

## License 📜

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
