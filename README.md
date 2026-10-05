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
3. **Check the opening.** The game's first 15 moves are compared with a local [Polyglot](https://www.chessprogramming.org/PolyGlot) book (`.bin`) or the [Lichess Opening Explorer](https://lichess.org/api#tag/Opening-Explorer) (`chess_analyzer.openings`). The first move that is not in the book is reported, along with the book alternatives, ranked by how well they score for the side to move.
4. **Evaluate the moves, using the engine as little as possible** (`chess_analyzer.engine`). Each position is handled by the cheapest source that can answer it:
   - **Opening book:** moves before the deviation are theory. They are marked *Book* and are not evaluated. The deviating move itself is still judged by the engine.
   - **Endgame tablebases:** positions with 5 pieces or fewer are looked up in the [Syzygy](https://syzygy-tables.info/) tablebases. That gives the exact result (win, draw or loss) and the best move with no search. Stockfish also uses the tablebases inside its own search.
   - **Forced moves:** a position with only one legal move takes the evaluation of the position after it.
   - **Stockfish** searches everything else to depth 16 by default. It uses Stockfish 16 or later (the Docker image builds 17.1), which evaluates positions with its NNUE neural network.

   Each move's centipawn loss is the drop in evaluation for the side that moved. Evaluations are capped at ±10 pawns, so going from "mate in 5" to "+15" does not count as a blunder. A loss of 50 centipawns or more is an inaccuracy, 100 or more a mistake, and 300 or more a blunder. Book and forced moves don't count towards the average centipawn loss.

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
| `--depth 16` / `--max-time 15` | Search depth per position (default 16), and the most seconds a single search may take. |
| `--time 0.5` | Search each position for a fixed time instead of to a depth. |
| `--threads N` | Engine threads (default: CPU cores minus one). |
| `--syzygy DIR` | Syzygy tablebase directory (default: `$SYZYGY_PATH`). |
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

Black: average centipawn loss 1020
  2 book, 0 forced, 0 best, 0 good, 0 inaccuracy, 0 mistake, 1 blunder
  Biggest mistakes:
    3...Nf6      blunder  -0.20 -> +M1  best was g6

Engine searched 2 of 8 positions (5 book, 1 terminal).
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
| `ANALYSIS_DEPTH` | `16` | Search depth per position. `0` searches for `ANALYSIS_TIME` seconds instead. |
| `ANALYSIS_MAX_TIME` | `15` | The most seconds a single search to `ANALYSIS_DEPTH` may take. |
| `ANALYSIS_TIME` | *(empty)* | Seconds per position when `ANALYSIS_DEPTH` is `0`. |
| `STOCKFISH_PATH` | `stockfish` on `PATH` | Stockfish binary. |
| `STOCKFISH_THREADS` | CPU cores minus one | Engine threads per worker. |
| `STOCKFISH_HASH` | `128` | Engine hash size (MB). |
| `ENGINE_NICE` | `10` | Lowers the engine's CPU priority (0–19) so the system stays responsive. |
| `SYZYGY_PATH` | *(empty)* | Directory with Syzygy tablebases. |
| `WORKERS` | `1` | Games analysed in parallel. |
| `MAX_QUEUE` | `20` | Maximum number of waiting analyses. |
| `OPENING_EXPLORER` | `lichess` | `lichess`, `masters` or `none`. |
| `OPENING_BOOK` | *(empty)* | Polyglot book to use instead of the explorer, when the file exists. |
| `LICHESS_TOKEN` | *(empty)* | Lichess API token for the explorer. |
| `DATA_DIR` | `data` | Where the SQLite database of analyses is kept. |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Address to listen on. |

## Self-hosting on a Raspberry Pi 🍓

The Docker image runs on 64-bit Raspberry Pi OS (arm64) as well as on x86-64 machines. It includes Stockfish 17.1, built from source with its NNUE networks embedded. The image is set up to be gentle on a Pi:

- Stockfish uses all cores but one (3 on a Pi 4 or 5) and runs at lower priority, so the Pi stays responsive and has some thermal headroom.
- Each position is searched to depth 16, so a slow CPU takes longer but doesn't analyse less deeply. A search stops after 15 seconds if depth 16 still hasn't been reached.
- One game is analysed at a time; other requests wait in a queue. Book moves, tablebase endgames and forced moves skip the engine entirely, and a finished game is never analysed twice.

Expect roughly one to three minutes per game on a Pi 4 or 5. That's an estimate: it hasn't been measured on a real Pi, and it depends heavily on the game.

1. Install Docker on the Pi (`curl -fsSL https://get.docker.com | sh`) and clone this repository.
2. Create the configuration and set an access code for your friends:

   ```bash
   cp .env.example .env
   nano .env
   ```

3. Optionally, add an opening book and endgame tablebases. Without them everything still works, but Stockfish has to analyse every position.

   ```bash
   mkdir -p books syzygy
   # Syzygy 3-4-5 piece tablebases, about 940 MB (add --wdl-only for 380 MB):
   docker compose run --rm app chess-analyzer-syzygy --dir /syzygy
   ```

   For the opening book, put any Polyglot book in `books/book.bin` (or set `OPENING_BOOK_FILE`). Without a book, the Lichess explorer is used for the opening, which needs internet access but no disk space.

4. Choose how the site is reached from the internet. Both options give you HTTPS on `chess.akkerman.ai`:

   - **Caddy** (`COMPOSE_PROFILES=caddy`, the default). Add a DNS `A` record for `chess.akkerman.ai` pointing to your home IP address, and forward ports 80 and 443 on your router to the Pi. Caddy gets a Let's Encrypt certificate on its own.
   - **Cloudflare Tunnel** (`COMPOSE_PROFILES=tunnel`). Use this if you can't or don't want to open ports, or if your home IP address changes. It requires the domain's DNS to be on Cloudflare. In the Cloudflare dashboard, go to *Zero Trust → Networks → Tunnels*, create a tunnel and add the public hostname `chess.akkerman.ai` with service `http://app:8000`. Then put the tunnel token in `CLOUDFLARE_TUNNEL_TOKEN`.

5. Start it:

   ```bash
   docker compose up -d
   ```

   This pulls the image that GitHub Actions publishes to `ghcr.io/stephanakkerman/chess-game-analyzer` for every push to `main`. If that package is private, run `docker login ghcr.io` first, or make the package public in its GitHub settings. You can also build the image on the Pi with `docker compose up -d --build`.

To update, run `docker compose pull && docker compose up -d`. Analyses are kept in the `analyzer-data` volume. On a Raspberry Pi 5 you can build a faster engine with `STOCKFISH_ARCH=armv8-dotprod` in `.env` and `docker compose up -d --build`.

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
