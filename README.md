# Chess Game Analyzer

---
<p align="center">
  <img alt="GitHub Actions Workflow Status" src="https://img.shields.io/github/actions/workflow/status/StephanAkkerman/chess-game-analyzer/pyversions.yml?label=python%203.10%20%7C%203.11%20%7C%203.12%20%7C%203.13&logo=python&style=flat-square">
  <img src="https://img.shields.io/github/license/StephanAkkerman/chess-game-analyzer.svg?color=brightgreen" alt="License">
  <a href="https://github.com/psf/black"><img src="https://img.shields.io/badge/code%20style-black-000000.svg" alt="Code style: black"></a>
</p>

## Introduction

Chess Game Analyzer downloads your recent games from Chess.com, runs Stockfish over every move and checks the opening against established theory. Use it from your phone through the web app, or from the command line. For each game it reports:

- your average centipawn loss and how many of your moves were best, good, inaccuracies, mistakes or blunders;
- your biggest mistakes, with the evaluation before and after and the move Stockfish preferred;
- the move where you or your opponent left opening theory, and the book moves that score best from that position.

## Table of Contents 🗂

- [How it works](#how-it-works)
- [Installation](#installation)
- [Usage](#usage)
- [Web app](#web-app)
- [Self-hosting on a Raspberry Pi](#self-hosting-on-a-raspberry-pi)
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

## Web app 📱

<p align="center">
  <img src="docs/screenshot-games.png" alt="List of recent games" width="280">
  <img src="docs/screenshot-analysis.png" alt="Analysis of a game with the best move shown" width="280">
</p>

The web app is the easiest way to use the analyzer from a phone. Enter a Chess.com username and tap a game. Stockfish analyses it in the background, and the page shows:

- the board with an evaluation bar. Step through the moves with the arrows, by swiping the board or by tapping a move;
- each move's classification. Inaccuracies, mistakes and blunders also show the engine's best move, and "Show best move" draws it on the board;
- an evaluation graph. Tap or drag on it to jump through the game;
- both players' average centipawn loss and biggest mistakes;
- where the game left opening theory, and how the book moves score.

Finished analyses are stored, so the **Share** button gives a link a friend can open. A game that was already analysed opens immediately. The app can be added to the phone's home screen.

To run it locally:

```bash
pip install -e .
chess-analyzer-web          # http://127.0.0.1:8000
```

The server is configured with environment variables:

| Variable | Default | Description |
| --- | --- | --- |
| `ACCESS_CODE` | *(empty)* | When set, fetching games and starting analyses require this code. Shared analysis links work without it. |
| `ANALYSIS_TIME` | `0.3` | Engine seconds per position. |
| `ANALYSIS_DEPTH` | *(empty)* | Fixed search depth instead of `ANALYSIS_TIME`. |
| `STOCKFISH_PATH` | `stockfish` on `PATH` | Stockfish binary. |
| `STOCKFISH_THREADS` / `STOCKFISH_HASH` | `1` / `64` | Engine threads and hash size (MB). |
| `WORKERS` | `1` | Games analysed in parallel. |
| `MAX_QUEUE` | `20` | Maximum number of waiting analyses. |
| `OPENING_EXPLORER` | `lichess` | `lichess`, `masters` or `none`. |
| `OPENING_BOOK` | *(empty)* | Polyglot book to use instead of the explorer. |
| `LICHESS_TOKEN` | *(empty)* | Lichess API token for the explorer. |
| `DATA_DIR` | `data` | Where the SQLite database of analyses is kept. |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Address to listen on. |

## Self-hosting on a Raspberry Pi 🍓

The Docker image runs on 64-bit Raspberry Pi OS (arm64) as well as on x86-64 machines, and includes Stockfish. A Raspberry Pi 4 or 5 analyses a 40-move game in about half a minute with the default settings.

1. Install Docker on the Pi (`curl -fsSL https://get.docker.com | sh`) and clone this repository.
2. Create the configuration and set an access code for your friends:

   ```bash
   cp .env.example .env
   nano .env
   ```

3. Choose how the site is reached from the internet. Both options give you HTTPS on `chess.akkerman.ai`:

   - **Caddy** (`COMPOSE_PROFILES=caddy`, the default). Add a DNS `A` record for `chess.akkerman.ai` pointing to your home IP address, and forward ports 80 and 443 on your router to the Pi. Caddy gets a Let's Encrypt certificate on its own.
   - **Cloudflare Tunnel** (`COMPOSE_PROFILES=tunnel`). Use this if you can't or don't want to open ports, or if your home IP address changes. It requires the domain's DNS to be on Cloudflare. In the Cloudflare dashboard, go to *Zero Trust → Networks → Tunnels*, create a tunnel and add the public hostname `chess.akkerman.ai` with service `http://app:8000`. Then put the tunnel token in `CLOUDFLARE_TUNNEL_TOKEN`.

4. Start it:

   ```bash
   docker compose up -d
   ```

   This pulls the image that GitHub Actions publishes to `ghcr.io/stephanakkerman/chess-game-analyzer` for every push to `main`. If that package is private, run `docker login ghcr.io` first, or make the package public in its GitHub settings. You can also build the image on the Pi with `docker compose up -d --build`.

To update, run `docker compose pull && docker compose up -d`. Analyses are kept in the `analyzer-data` volume.

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
