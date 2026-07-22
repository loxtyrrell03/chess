# Chess Trainer Control Centre

A Windows training assistant with a DOM-aware browser extension, Stockfish 18, and GPU-accelerated LCZero analysis.

## What changed

- Stockfish 18 and LCZero run locally; a second browser/Chessify board is no longer needed.
- A Chromium extension observes stable piece and move-list changes instead of matching screenshot pixels.
- `python-chess` reconciles every observation against legal moves, including castling, en passant, captures, and all four promotion pieces.
- New games and board orientation are detected automatically.
- The draggable, translucent dashboard lives directly on Chess.com or Lichess and shows the board, move list, engine metrics, and controls.
- The selected engine analyzes continuously and its current best move is drawn as an arrow tied to the exact observed position.
- The orientation-aware eval bar persists between turns. Opponent-turn analysis and arrows can be enabled independently.
- LCZero supports standard BT4 analysis, T1 material-odds modes, the LQO v2 queen-odds network, and a configurable contempt value.
- Optional automatic LCZero controls select the network from the current material imbalance and derive a live practical contempt value from a short neutral probe. The auto value is capped at +250 and shown directly on the moving slider.

The original prototype is preserved under `legacy/` for reference. It is not imported by the new application.

## Supported use

The assistant observes supported Chess.com and Lichess boards and displays the engine's current best move as an overlay arrow. It does not click or play moves.

## One-time setup

Open PowerShell in this repository and run:

```powershell
.\scripts\setup.ps1
.\scripts\install_lc0.ps1
.\scripts\build.ps1
.\scripts\install_shortcuts.ps1 -DesktopShortcut
```

The setup script:

1. Creates a Python 3.12 virtual environment.
2. Installs the application and test dependencies.
3. Downloads the official Stockfish 18 Windows BMI2 build selected for the Ryzen 7 5700X.
4. Verifies the release SHA-256 before extracting it.

`install_lc0.ps1` downloads a fresh official LCZero v0.32.1 CUDA 12 build, the current BT4-it332 network, T1 odds network, and LQO v2 queen-odds network. Every download is SHA-256 verified. It does not reuse an installed LC0.

The configured continuous-analysis profile is:

```text
Threads: 14
Hash: 4096 MiB
MultiPV: 1
Skill Level: 20
UCI_LimitStrength: false
Search limit: none; the current principal variation is streamed while the position is unchanged
```

The engine keeps refining the current position until the board changes. LCZero uses the CUDA FP16 backend on the NVIDIA GPU for faster first results. Sixteen Stockfish threads is available as a dedicated-CPU setting, but 14 keeps the browser and Windows responsive.

## Install the browser extension

1. Open `chrome://extensions` or `edge://extensions`.
2. Turn on **Developer mode**.
3. Choose **Load unpacked** and select this repository's `extension` folder (or the packaged app's `extension` folder).
4. Open the extension's **Options** page.
5. Refresh the Chess.com or Lichess tab. The extension connects to the local control centre automatically.

The extension connects only to `127.0.0.1:8765` and the desktop bridge accepts only Chrome-extension origins. No pairing step is required.

## Run and test

From source:

```powershell
.\scripts\run.ps1
```

Tests:

```powershell
.\scripts\test.ps1
```

The app is installed in the Start menu as **Chess Trainer**. While running, its knight icon appears in the taskbar notification area. Windows requires the user to choose **Pin to taskbar** once if a permanent large taskbar button is desired.

## Architecture

- `chess_trainer/reconcile.py` — legal position reconstruction and game lifecycle.
- `chess_trainer/engine.py` — Stockfish/LCZero UCI ownership, odds-network selection, and full-strength configuration.
- `chess_trainer/bridge.py` — authenticated loopback WebSocket bridge.
- `chess_trainer/runtime.py` — stale-result protection and orchestration.
- `chess_trainer/ui.py` — Qt control centre and taskbar tray.
- `extension/` — Manifest V3 DOM adapter and move executor.
- `tests/` — lifecycle, special-move, policy, protocol, and engine checks.

Stockfish is GPLv3 software from the [official Stockfish project](https://stockfishchess.org/). Its locally downloaded binary is intentionally excluded from Git. Packaged builds include Stockfish's `COPYING.txt`; the exact corresponding Stockfish 18 source is available from the [official `sf_18` release](https://github.com/official-stockfish/Stockfish/releases/tag/sf_18).
