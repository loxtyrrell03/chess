# Local Chess Training Bridge extension

This Manifest V3 Chromium extension observes Chess.com boards and hosts a semi-transparent in-page dashboard with the reconstructed board, SAN move list, live Stockfish evaluation, best-move arrow, orientation-aware eval bar, and runtime controls. It does not contain a chess engine and does not click the board.

The extension is deliberately fail-closed. It supports only:

- games against the Chess.com computer/bots (`/play/computer` and `/game/computer/...`);
- the standalone analysis board (`/analysis`); and
- computer practice (`/practice/...`).

Live games, Daily games, online play, tournaments, Puzzle Rush/Battle, rated puzzles, linked in-progress game analysis, unknown routes, and unsupported variants are denied. The same allow-list must also be enforced by the desktop service.

## Install for development

1. Start the desktop control centre so its WebSocket server is listening on `127.0.0.1`.
2. Open `chrome://extensions` in a Chromium browser.
3. Enable **Developer mode**.
4. Choose **Load unpacked** and select this `extension` directory.
5. Click the extension toolbar icon to open the compact control and settings popup.
6. Refresh the Chess.com tab. Connection to `ws://127.0.0.1:8765/v1/extension` is automatic.

The endpoint is fixed to loopback and the server accepts only Chrome-extension origins.

## Architecture

`content-script.js` runs in Chrome's isolated world. It:

- scores multiple board candidates instead of assuming the largest rectangle;
- prioritizes Chess.com's current `#board`, then custom-element/data/test selectors;
- derives orientation from explicit attributes, the `a1` square, or coordinate-label geometry;
- extracts DOM piece maps when available;
- treats move-list history as the primary source when the current canvas/WebGL renderer does not expose piece elements;
- watches the board and move list with a debounced `MutationObserver` and requires two identical snapshots before publishing;
- detects SPA navigation, board replacement, rematches, and move-history resets;
- binds every arrow to a `pageId`, monotonic `seq`, and SHA-256 `positionHash`; and
- removes stale arrows as soon as the observed position changes.

`service-worker.js` independently checks the current tab URL and routes JSON messages between the content script and loopback WebSocket. Chrome 116 or newer is required because active WebSocket traffic can keep an MV3 service worker alive. State is still designed to recover after suspension: content scripts reconnect and resend their latest snapshots.

`popup.html` provides connection status and controls for the active board. `options.html` remains the extension-local advanced information page; neither toolbar surface navigates to an external site.

## Wire protocol (version 1)

The WebSocket uses the `local-chess-training-v1` subprotocol. Messages are JSON objects with `v: 1`, are limited to 96 KiB, and are sent only after a loopback connection is established.

### Extension hello

```json
{
  "v": 1,
  "type": "hello",
  "clientId": "uuid",
  "extensionVersion": "0.3.0",
  "capabilities": ["dom-observation", "training-mode-gate", "position-hash", "promotion", "move-idempotency"]
}
```

The server replies with `hello.ack` after validating the local extension origin and protocol version.

### Position snapshot

```json
{
  "v": 1,
  "type": "position.snapshot",
  "pageId": "uuid",
  "seq": 12,
  "gameKey": "computer:123",
  "positionHash": "64-lowercase-hex-characters",
  "observed_at": "2026-07-21T12:00:00.000Z",
  "page": {
    "url": "https://www.chess.com/game/computer/123",
    "path": "/game/computer/123",
    "gameId": "123",
    "visible": true,
    "focused": true
  },
  "eligibility": {
    "allowed": true,
    "mode": "computer",
    "reason": "independent_gates_passed",
    "evidence": ["computer_route", "computer_route", "path:/game/computer/123"]
  },
  "board": {
    "pieces": {},
    "orientation": "white",
    "orientationConfidence": 0.985,
    "playerColor": "white",
    "startingFen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "currentFen": null,
    "pieceSource": "renderer-not-readable",
    "rect": { "left": 80, "top": 120, "width": 720, "height": 720 }
  },
  "moves": ["e4", "e5"],
  "game": {
    "variant": "standard",
    "side_to_move": "white",
    "local_color": "white",
    "full_state_source": "move-list",
    "requires_daemon_reconstruction": true
  },
  "status": {
    "phase": "in_progress",
    "result": null,
    "document_visible": true,
    "document_focused": true
  }
}
```

The desktop service rebuilds the position with a legal chess library, validates every SAN ply, and compares any available DOM placement. `startingFen` is exclusively the root for replaying `moves`; `currentFen`, when Chess.com exposes one, is the complete current position and is never treated as the history root. Complete history is preferred because it preserves the move stack, while a validated `currentFen` lets the service attach safely to an arbitrary setup position. A piece-placement-only FEN must never be used to invent castling rights or en-passant state. If neither history nor a current FEN can produce a unique legal state, do not issue a command.

### Arrow command

```json
{
  "v": 1,
  "type": "arrow.command",
  "pageId": "matching-snapshot-uuid",
  "seq": 12,
  "positionHash": "matching-snapshot-hash",
  "uci": "e7e8n",
  "oddsMode": "queen_for_knight",
  "oddsTitle": "T1 queen-for-material",
  "oddsReason": "queen gap (9); compensation: 2 pawns (2); net -7. T1 is the closest available intermediate odds family."
}
```

The service worker forwards an arrow only when all position identifiers match the newest snapshot and the current route remains eligible. The content script maps the UCI source and destination squares through the verified board orientation and draws a pointer-transparent SVG above the board. `arrow.clear` or any new position removes it.

`dashboard.state` and `arrow.command` both carry `oddsTitle` and `oddsReason`.
This keeps the visible explanation current before analysis starts and after
each live result. The text is generated by the desktop service from the same
confirmed player-relative material assessment used to select the network;
provisional DOM frames retain the previous confirmed explanation.

## Security notes

- Do not add `externally_connectable`; page scripts must not reach privileged extension APIs.
- Bind the desktop server to loopback only, validate the WebSocket `Origin` and subprotocol, cap message size, and rate-limit clients.
- Treat all content-script data as untrusted. The service worker validates structure and the current tab URL; the desktop service must independently validate the route, schema, legal position, side to move, and UCI move.
- Never evaluate messages, interpolate them into shell commands, or accept arbitrary URLs from them.
- Keep logs local and avoid usernames, full URLs, and game chat.
- There is no override that enables human games. Extending the route allow-list should require a code change and tests.

## Suggested tests

- DOM fixtures for `#board`, custom boards, canvas boards, duplicate mini-boards, missing move lists, white/black orientation, and mid-game flips.
- SAN reconstruction and DOM-position comparison for castling, en passant, capture promotions, and all four promotion pieces.
- Route-table tests for every allowed route plus adversarial Live/Daily/tournament/puzzle URLs and analysis URLs linked to ongoing games.
- Two-snapshot stabilization, same-URL rematches, SPA navigation, board replacement, undo-to-start, and MV3 worker restart/reconnect.
- Delayed engine replies, stale hashes, stale sequence numbers, expired commands, duplicated request IDs, malformed JSON, and oversized frames.
- Browser zoom, Windows DPI scaling, resized boards, multiple monitors, hidden tabs, obscuring dialogs, and promotion overlays.
- End-to-end browser tests against local HTML fixtures. Any live-site manual verification must use a bot/computer game only.
