(function () {
  "use strict";

  const PROTOCOL_VERSION = 1;
  const PORT_NAME = "local-chess-training-v1";
  const SCAN_DEBOUNCE_MS = 35;
  const STABILITY_RECHECK_MS = 55;
  const URL_POLL_MS = 300;
  const MAX_MOVES = 700;
  const STANDARD_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
  const ALLOWED_HOSTS = new Set(["chess.com", "www.chess.com", "lichess.org", "www.lichess.org"]);
  const FILES = "abcdefgh";
  const ODDS_MODE_LABELS = Object.freeze({
    none: "BT4 normal",
    knight: "T1 minor-equivalent",
    rook: "T1 rook-equivalent",
    queen_for_knight: "T1 queen-for-material",
    queen: "LQO near-full queen"
  });
  const PIECE_NAMES = {
    pawn: "p",
    knight: "n",
    bishop: "b",
    rook: "r",
    queen: "q",
    king: "k"
  };
  const UNICODE_PIECES = {
    "♙": "wp", "♘": "wn", "♗": "wb", "♖": "wr", "♕": "wq", "♔": "wk",
    "♟": "bp", "♞": "bn", "♝": "bb", "♜": "br", "♛": "bq", "♚": "bk"
  };
  const BOARD_SELECTORS = [
    "#board",
    "cg-board",
    ".cg-wrap cg-board",
    "wc-chess-board",
    "chess-board",
    "[data-board]",
    "[data-cy*='board' i]",
    "[data-testid*='board' i]",
    "[id^='board-']",
    ".board",
    "[class*='chessboard' i]"
  ];
  const MOVE_LIST_SELECTORS = [
    "i5d",
    ".round__app app",
    ".analyse__tools .tview2",
    ".analyse__tools .pgn",
    ".move-list",
    "#move-list",
    "wc-vertical-move-list",
    "vertical-move-list",
    "wc-move-list",
    "[data-cy*='move-list' i]",
    "[data-testid*='move-list' i]",
    "[aria-label*='move list' i]",
    "[class*='move-list' i]",
    "[class*='movelist' i]"
  ];

  let port = null;
  let reconnectTimer = null;
  let scanTimer = null;
  let scanning = false;
  let scanAgain = false;
  let lastUrl = location.href;
  let pendingCandidate = null;
  let lastEmittedKey = "";
  let currentSnapshot = null;
  let currentBoard = null;
  let currentArrowCommand = null;
  let lastEvaluationCommand = null;
  let arrowOverlay = null;
  let evalOverlay = null;
  let dashboard = null;
  let dashboardState = { site: siteLabel(), monitoring: true, showOverlays: true, engineKind: "stockfish", multiPv: 1, oddsMode: "none", oddsTitle: "Network pending", oddsReason: "Waiting for a synchronized position.", lc0Contempt: 0, lc0AutoNetwork: false, lc0AutoContempt: false, analyzeOpponent: false, showOpponentArrows: false };
  let dashboardPlacement = null;
  let activeGameMarker = "";
  let lastBoardElement = null;
  let lastMoveCount = 0;
  let sessionId = crypto.randomUUID();
  let sessionPlayerColor = null;
  let sequence = 0;
  let executing = false;
  let lichessInitCache = { node: null, text: "", data: null };
  const commandResults = new Map();

  function isPlainObject(value) {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
  }

  function isVisible(element) {
    if (!(element instanceof Element) || !element.isConnected) return false;
    const rect = element.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return false;
    const style = getComputedStyle(element);
    return style.display !== "none" && style.visibility !== "hidden" && Number.parseFloat(style.opacity || "1") > 0.02;
  }

  function queryAllSafe(root, selector) {
    try {
      return Array.from(root.querySelectorAll(selector));
    } catch {
      return [];
    }
  }

  function uniqueElements(elements) {
    return Array.from(new Set(elements.filter((element) => element instanceof Element)));
  }

  function rounded(value, places = 2) {
    const factor = 10 ** places;
    return Math.round(value * factor) / factor;
  }

  function classifyPage(rawUrl = location.href) {
    let url;
    try {
      url = new URL(rawUrl);
    } catch {
      return { allowed: false, mode: "unknown", reason: "invalid_url", evidence: ["invalid_url"] };
    }
    if (url.protocol !== "https:" || !ALLOWED_HOSTS.has(url.hostname.toLowerCase())) {
      return { allowed: false, mode: "unknown", reason: "unexpected_origin", evidence: ["unexpected_origin"] };
    }
    const path = url.pathname.replace(/\/{2,}/g, "/").toLowerCase();
    const lichess = ["lichess.org", "www.lichess.org"].includes(url.hostname.toLowerCase());
    const analysis = lichess
      ? ["/analysis", "/study", "/editor"].some((prefix) => path === prefix || path.startsWith(`${prefix}/`))
      : path === "/analysis" || path.startsWith("/analysis/");
    return {
      allowed: true,
      mode: analysis ? "analysis" : lichess ? "lichess" : "training",
      reason: analysis ? "analysis_workspace" : lichess ? "supported_lichess_route" : "all_routes_permitted",
      evidence: [`site:${lichess ? "lichess" : "chess.com"}`, `path:${path}`]
    };
  }

  function siteLabel(rawUrl = location.href) {
    try {
      return new URL(rawUrl).hostname.toLowerCase().endsWith("lichess.org") ? "Lichess" : "Chess.com";
    } catch {
      return "Chess site";
    }
  }

  function lichessPageData() {
    if (siteLabel() !== "Lichess") return null;
    const node = document.getElementById("page-init-data");
    const text = node?.textContent || "";
    if (!text) return null;
    if (lichessInitCache.node === node && lichessInitCache.text === text) return lichessInitCache.data;
    let data = null;
    try {
      const parsed = JSON.parse(text);
      data = isPlainObject(parsed?.data) ? parsed.data : null;
    } catch {
      data = null;
    }
    lichessInitCache = { node, text, data };
    return data;
  }

  function lichessInitialState(currentPlacement = null) {
    const data = lichessPageData();
    if (!data) return null;
    const steps = Array.isArray(data.steps) ? data.steps.filter(isPlainObject) : [];
    const fens = [steps.at(-1)?.fen, data.game?.fen].filter((fen) => typeof fen === "string");
    const currentFen = fens.find((fen) => !currentPlacement || fen.split(/\s+/)[0] === currentPlacement) || null;
    const statusName = String(data.game?.status?.name || "started").toLowerCase();
    const winner = normalizeColor(data.game?.winner);
    const result = winner === "white" ? "1-0" : winner === "black" ? "0-1" : statusName === "started" ? null : statusName;
    return {
      gameId: typeof data.game?.id === "string" ? data.game.id : null,
      variant: String(data.game?.variant?.key || "standard").toLowerCase(),
      orientation: normalizeColor(data.orientation),
      playerColor: normalizeColor(data.player?.color),
      currentFen,
      startingFen: typeof steps[0]?.fen === "string" ? steps[0].fen : null,
      moves: steps.map((step) => normalizeSanToken(step.san)).filter(Boolean).slice(0, MAX_MOVES),
      result
    };
  }

  function scoreBoard(element) {
    if (!isVisible(element)) return Number.NEGATIVE_INFINITY;
    const rect = element.getBoundingClientRect();
    const shorter = Math.min(rect.width, rect.height);
    const longer = Math.max(rect.width, rect.height);
    if (shorter < 180 || longer / shorter > 1.18) return Number.NEGATIVE_INFINITY;

    let score = Math.min(shorter, 900) / 10;
    if (element.id === "board") score += 180;
    if (element.tagName === "CG-BOARD") score += 220;
    if (["WC-CHESS-BOARD", "CHESS-BOARD"].includes(element.tagName)) score += 150;
    if (element.matches(".board, [data-board]")) score += 65;
    if (element.querySelector("canvas")) score += 35;
    if (element.querySelector(".coordinates, [class*='coordinates' i]")) score += 30;
    if (element.hasAttribute("data-coordinates")) score += 20;

    const pieceCount = queryAllSafe(element, "piece, [data-piece], [class~='piece'], [class*=' piece '], [class^='piece ']").length;
    const squareCount = queryAllSafe(element, "[data-square], [class*='square-']").length;
    if (pieceCount >= 2 && pieceCount <= 40) score += 50 + pieceCount;
    if (squareCount >= 32) score += 45;
    if (rect.top < 0 || rect.left < 0 || rect.bottom > innerHeight + 5 || rect.right > innerWidth + 5) score -= 25;
    return score;
  }

  function findBoard() {
    const candidates = uniqueElements(BOARD_SELECTORS.flatMap((selector) => queryAllSafe(document, selector)));
    let best = null;
    let bestScore = Number.NEGATIVE_INFINITY;
    for (const element of candidates) {
      const score = scoreBoard(element);
      if (score > bestScore) {
        best = element;
        bestScore = score;
      }
    }
    return bestScore >= 25 ? { element: best, score: rounded(bestScore) } : null;
  }

  function normalizeColor(value) {
    const text = String(value || "").trim().toLowerCase();
    if (/^(?:w|white)$/.test(text)) return "white";
    if (/^(?:b|black)$/.test(text)) return "black";
    return null;
  }

  function orientationFromAttributes(board) {
    const names = ["data-orientation", "orientation", "data-player-color", "data-bottom-color", "data-perspective"];
    for (const name of names) {
      const color = normalizeColor(board.getAttribute(name));
      if (color) return { value: color, confidence: 1, source: `attribute:${name}` };
    }
    for (const name of ["data-flipped", "flipped"]) {
      if (!board.hasAttribute(name)) continue;
      const raw = board.getAttribute(name);
      const flipped = raw === "" || /^(?:1|true|yes|black)$/i.test(raw || "");
      return { value: flipped ? "black" : "white", confidence: 0.99, source: `attribute:${name}` };
    }
    return null;
  }

  function squareTokenFromElement(element) {
    for (const name of ["data-square", "square", "data-cell", "data-coord"]) {
      const value = element.getAttribute?.(name);
      const match = String(value || "").toLowerCase().match(/(?:^|[^a-h])([a-h][1-8])(?:$|[^a-z0-9])/);
      if (match) return match[1];
      if (/^[a-h][1-8]$/.test(String(value || "").toLowerCase())) return String(value).toLowerCase();
    }
    for (const token of element.classList || []) {
      const algebraic = token.toLowerCase().match(/^square-([a-h][1-8])$/);
      if (algebraic) return algebraic[1];
      const numeric = token.toLowerCase().match(/^square-([1-8])([1-8])$/);
      if (numeric) return `${FILES[Number(numeric[1]) - 1]}${numeric[2]}`;
    }
    const aria = element.getAttribute?.("aria-label") || "";
    const ariaMatch = aria.toLowerCase().match(/\b([a-h][1-8])\b/);
    return ariaMatch ? ariaMatch[1] : null;
  }

  function orientationFromSquareAnchor(board) {
    const anchor = queryAllSafe(board, "[data-square='a1' i], .square-11").find(isVisible);
    if (!anchor) return null;
    const boardRect = board.getBoundingClientRect();
    const rect = anchor.getBoundingClientRect();
    const x = (rect.left + rect.width / 2 - boardRect.left) / boardRect.width;
    const y = (rect.top + rect.height / 2 - boardRect.top) / boardRect.height;
    if (x < 0.25 && y > 0.75) return { value: "white", confidence: 0.995, source: "a1-anchor" };
    if (x > 0.75 && y < 0.25) return { value: "black", confidence: 0.995, source: "a1-anchor" };
    return null;
  }

  function orientationFromCoordinates(board) {
    const boardRect = board.getBoundingClientRect();
    const labels = queryAllSafe(board, ".coordinates text, [class*='coordinates' i] text, text")
      .filter(isVisible)
      .map((element) => {
        const text = (element.textContent || "").trim().toLowerCase();
        const rect = element.getBoundingClientRect();
        return {
          text,
          x: (rect.left + rect.width / 2 - boardRect.left) / boardRect.width,
          y: (rect.top + rect.height / 2 - boardRect.top) / boardRect.height
        };
      });
    const a = labels.find((label) => label.text === "a");
    const one = labels.find((label) => label.text === "1");
    if (a && one) {
      if (a.x < 0.3 && a.y > 0.7 && one.y > 0.7) {
        return { value: "white", confidence: 0.985, source: "coordinate-labels" };
      }
      if (a.x > 0.7 && a.y > 0.7 && one.y < 0.3) {
        return { value: "black", confidence: 0.985, source: "coordinate-labels" };
      }
    }
    return null;
  }

  function orientationFromLichess(board) {
    if (siteLabel() !== "Lichess") return null;
    const oriented = board.closest(".orientation-black, .orientation-white");
    if (oriented?.classList.contains("orientation-black")) {
      return { value: "black", confidence: 0.995, source: "lichess-orientation-class" };
    }
    if (oriented?.classList.contains("orientation-white")) {
      return { value: "white", confidence: 0.995, source: "lichess-orientation-class" };
    }
    const orientation = lichessInitialState()?.orientation;
    return orientation ? { value: orientation, confidence: 0.995, source: "lichess-page-state" } : null;
  }

  function detectOrientation(board) {
    if (!board) return { value: "unknown", confidence: 0, source: "no-board" };
    const strong = orientationFromAttributes(board) || orientationFromLichess(board) || orientationFromSquareAnchor(board) || orientationFromCoordinates(board);
    if (strong) return strong;
    const classText = `${board.id} ${board.className || ""}`.toLowerCase();
    if (/\b(?:flipped|orientation-black|black-orientation|perspective-black)\b/.test(classText)) {
      return { value: "black", confidence: 0.93, source: "class-token" };
    }
    if (/\b(?:orientation-white|white-orientation|perspective-white)\b/.test(classText)) {
      return { value: "white", confidence: 0.93, source: "class-token" };
    }
    return { value: "unknown", confidence: 0, source: "not-detected" };
  }

  function parsePieceCode(element) {
    const values = [
      element.getAttribute?.("data-piece"),
      element.getAttribute?.("piece"),
      element.getAttribute?.("data-type"),
      element.getAttribute?.("aria-label"),
      Array.from(element.classList || []).join(" "),
      element.textContent
    ].filter(Boolean).map((value) => String(value).trim().toLowerCase());

    for (const raw of values) {
      if (raw.length === 1 && UNICODE_PIECES[raw]) return UNICODE_PIECES[raw];
      const compact = raw.match(/(?:^|[\s_-])([wb])([pnbrqk])(?:$|[\s_-])/);
      if (compact) return `${compact[1]}${compact[2]}`;
      const named = raw.match(/\b(white|black)[\s_-]*(pawn|knight|bishop|rook|queen|king)\b/);
      if (named) return `${named[1] === "white" ? "w" : "b"}${PIECE_NAMES[named[2]]}`;
    }
    for (const value of Object.keys(UNICODE_PIECES)) {
      if ((element.textContent || "").includes(value)) return UNICODE_PIECES[value];
    }
    return null;
  }

  function squareFromGeometry(element, board, orientation) {
    if (!board || !["white", "black"].includes(orientation)) return null;
    const boardRect = board.getBoundingClientRect();
    const rect = element.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    if (cx < boardRect.left || cx > boardRect.right || cy < boardRect.top || cy > boardRect.bottom) return null;
    let col = Math.min(7, Math.max(0, Math.floor((cx - boardRect.left) / (boardRect.width / 8))));
    let row = Math.min(7, Math.max(0, Math.floor((cy - boardRect.top) / (boardRect.height / 8))));
    if (orientation === "black") {
      col = 7 - col;
      row = 7 - row;
    }
    return `${FILES[col]}${8 - row}`;
  }

  function placementFen(pieces) {
    const ranks = [];
    for (let rank = 8; rank >= 1; rank -= 1) {
      let empty = 0;
      let text = "";
      for (const file of FILES) {
        const code = pieces[`${file}${rank}`];
        if (!code) {
          empty += 1;
          continue;
        }
        if (empty) {
          text += String(empty);
          empty = 0;
        }
        const symbol = code[1];
        text += code[0] === "w" ? symbol.toUpperCase() : symbol;
      }
      if (empty) text += String(empty);
      ranks.push(text);
    }
    return ranks.join("/");
  }

  function extractPieces(board, orientation) {
    if (!board) return { pieces: {}, placement_fen: null, source: "none", confidence: 0 };
    const selectors = [
      "piece", "[data-piece]", "[piece]", "[class~='piece']", "[class^='piece ']", "[class*=' piece ']",
      "[aria-label*='pawn' i]", "[aria-label*='knight' i]", "[aria-label*='bishop' i]",
      "[aria-label*='rook' i]", "[aria-label*='queen' i]", "[aria-label*='king' i]"
    ];
    const candidates = uniqueElements(selectors.flatMap((selector) => queryAllSafe(board, selector))).filter(isVisible);
    const pieces = {};
    let explicitSquares = 0;
    for (const element of candidates) {
      const code = parsePieceCode(element);
      if (!code) continue;
      let square = squareTokenFromElement(element);
      if (square) explicitSquares += 1;
      else square = squareFromGeometry(element, board, orientation.value);
      if (square && !pieces[square]) pieces[square] = code;
    }
    const count = Object.keys(pieces).length;
    if (count < 2 || count > 40) return { pieces: {}, placement_fen: null, source: "renderer-not-readable", confidence: 0 };
    return {
      pieces,
      placement_fen: placementFen(pieces),
      source: explicitSquares >= count * 0.75 ? "dom-square-attributes" : "dom-geometry",
      confidence: explicitSquares >= count * 0.75 ? 0.98 : 0.78
    };
  }

  function normalizeSanToken(raw) {
    let token = String(raw || "").trim();
    token = token.replace(/^\d+\.(?:\.\.)?/, "").trim();
    token = token.replace(/[!?]+$/g, "");
    token = token.replace(/^0-0-0/, "O-O-O").replace(/^0-0/, "O-O");
    token = token.replace(/\u00a0/g, "");
    if (!token || /^(?:1-0|0-1|1\/2-1\/2|\*|e\.p\.)$/i.test(token)) return null;
    const san = /^(?:O-O(?:-O)?|[KQRBN]?[a-h1-8]{0,2}x?[a-h][1-8](?:=[QRBN])?)[+#]?$/;
    return san.test(token) ? token : null;
  }

  function sansFromText(text) {
    return String(text || "")
      .replace(/\u2026/g, "...")
      .replace(/\d+\.(?:\.\.)?/g, " ")
      .split(/\s+/)
      .map(normalizeSanToken)
      .filter(Boolean)
      .slice(0, MAX_MOVES);
  }

  function scoreMoveList(element, board) {
    if (!isVisible(element)) return Number.NEGATIVE_INFINITY;
    const text = element.innerText || element.textContent || "";
    const sans = sansFromText(text);
    const rect = element.getBoundingClientRect();
    let score = sans.length * 6;
    if (element.matches(".move-list, #move-list, wc-vertical-move-list, vertical-move-list, wc-move-list")) score += 80;
    score += Math.min(30, queryAllSafe(element, "[data-ply], [data-san], [data-move]").length * 2);
    if (board) {
      const boardRect = board.getBoundingClientRect();
      const horizontalDistance = Math.abs(rect.left - boardRect.right);
      const verticalDistance = Math.abs(rect.top - boardRect.top);
      if (horizontalDistance < boardRect.width && verticalDistance < boardRect.height) score += 20;
    }
    if (rect.width < 80 || rect.height < 30) score -= 30;
    return score;
  }

  function findMoveList(board) {
    const candidates = MOVE_LIST_SELECTORS.flatMap((selector) => queryAllSafe(document, selector));
    if (siteLabel() === "Lichess") {
      candidates.push(...queryAllSafe(document, ".round__app *").filter((element) => {
        const text = element.innerText || element.textContent || "";
        return text.length <= 20_000 && sansFromText(text).length > 0;
      }));
    }
    const uniqueCandidates = uniqueElements(candidates);
    let best = null;
    let bestScore = Number.NEGATIVE_INFINITY;
    for (const element of uniqueCandidates) {
      const score = scoreMoveList(element, board);
      if (score > bestScore) {
        best = element;
        bestScore = score;
      }
    }
    return bestScore >= 10 ? best : null;
  }

  function movesFromAttributedNodes(root) {
    if (!root) return [];
    // Move lists are commonly virtualized or hide old plies once scrolled. The
    // list itself must be visible, but its attributed history nodes need not be.
    const nodes = queryAllSafe(root, "[data-ply], [data-san], [data-move]");
    const byPly = new Map();
    for (const node of nodes) {
      const rawPly = Number.parseInt(node.getAttribute("data-ply") || "", 10);
      const san = normalizeSanToken(node.getAttribute("data-san") || node.getAttribute("data-move") || node.textContent);
      if (!san) continue;
      const ply = Number.isSafeInteger(rawPly) && rawPly > 0 ? rawPly : byPly.size + 1;
      if (!byPly.has(ply)) byPly.set(ply, san);
    }
    return Array.from(byPly.entries()).sort((a, b) => a[0] - b[0]).map(([, san]) => san).slice(0, MAX_MOVES);
  }

  function extractMoves(root) {
    const attributed = movesFromAttributedNodes(root);
    const textMoves = root ? sansFromText(root.innerText || root.textContent || "") : [];
    const sans = attributed.length >= textMoves.length ? attributed : textMoves;
    return sans.map((san, index) => ({ ply: index + 1, san }));
  }

  function extractDeclaredFens(board, currentPlacement) {
    const sources = [];
    if (board) {
      sources.push(board, board.closest("[data-fen], [fen]") || null);
    }
    sources.push(...queryAllSafe(document, "[data-fen], input[name*='fen' i], textarea[name*='fen' i]"));
    let current = null;
    let starting = null;
    for (const element of sources.filter(Boolean)) {
      const value = element.getAttribute?.("data-fen") || element.getAttribute?.("fen") || element.value || "";
      const normalized = String(value).trim();
      if (/^(?:[prnbqkPRNBQK1-8]+\/){7}[prnbqkPRNBQK1-8]+\s+[wb]\s+(?:-|[KQkq]+)\s+(?:-|[a-h][36])\s+\d+\s+\d+$/.test(normalized)) {
        if (currentPlacement && normalized.split(/\s+/)[0] === currentPlacement) {
          current ||= normalized;
        } else {
          // A FEN whose placement predates the renderer can be the root of an
          // analysis-board move list. The daemon will accept it only if legal
          // replay reaches the observed position.
          starting ||= normalized;
        }
      }
    }
    return { current, starting };
  }

  function extractGameId(url, lichessState = null) {
    if (lichessState?.gameId) return lichessState.gameId;
    if (url.hostname.toLowerCase().endsWith("lichess.org")) {
      const lichessMatch = url.pathname.match(/^\/([a-z0-9]{8})(?:[a-z0-9]{4})?(?:\/|$)/i);
      if (lichessMatch) return lichessMatch[1];
    }
    const pathMatch = url.pathname.match(/\/game\/(?:computer|live|daily)\/([a-z0-9_-]+)/i);
    if (pathMatch) return pathMatch[1];
    for (const selector of ["[data-game-id]", "[data-game-uuid]", "[data-uuid]"]) {
      const element = document.querySelector(selector);
      const value = element?.getAttribute("data-game-id") || element?.getAttribute("data-game-uuid") || element?.getAttribute("data-uuid");
      if (value && value.length <= 128) return value;
    }
    return null;
  }

  function detectVariant(lichessState = null) {
    if (lichessState?.variant) {
      if (lichessState.variant === "standard") return "standard";
      if (lichessState.variant === "chess960") return "chess960";
      return "unsupported";
    }
    const board = document.getElementById("board");
    const raw = `${board?.getAttribute("data-variant") || ""} ${document.body?.getAttribute("data-variant") || ""} ${location.pathname}`.toLowerCase();
    if (/chess[-_ ]?960|fischer[-_ ]?random/.test(raw)) return "chess960";
    if (/king.of.the.hill|three.check|crazyhouse|bughouse/.test(raw)) return "unsupported";
    return "standard";
  }

  function gameResult(moveRoot) {
    const text = `${moveRoot?.innerText || ""} ${document.querySelector("#game-over-modal")?.innerText || ""}`;
    if (/\b1\/2-1\/2\b/.test(text)) return "1/2-1/2";
    if (/\b1-0\b/.test(text)) return "1-0";
    if (/\b0-1\b/.test(text)) return "0-1";
    return null;
  }

  function boardRectSnapshot(board) {
    if (!board) return null;
    const rect = board.getBoundingClientRect();
    const viewport = window.visualViewport;
    return {
      left: rounded(rect.left),
      top: rounded(rect.top),
      right: rounded(rect.right),
      bottom: rounded(rect.bottom),
      width: rounded(rect.width),
      height: rounded(rect.height),
      device_pixel_ratio: rounded(window.devicePixelRatio || 1, 3),
      screen_x: rounded(window.screenX),
      screen_y: rounded(window.screenY),
      outer_width: rounded(window.outerWidth),
      outer_height: rounded(window.outerHeight),
      inner_width: rounded(window.innerWidth),
      inner_height: rounded(window.innerHeight),
      visual_viewport: viewport ? {
        offset_left: rounded(viewport.offsetLeft),
        offset_top: rounded(viewport.offsetTop),
        width: rounded(viewport.width),
        height: rounded(viewport.height),
        scale: rounded(viewport.scale, 4)
      } : null
    };
  }

  function candidateStateKey(candidate) {
    return JSON.stringify({
      url: candidate.page.url,
      game_marker: candidate.game_marker,
      mode: candidate.eligibility.mode,
      allowed: candidate.eligibility.allowed,
      moves: candidate.moves.map((move) => move.san),
      placement: candidate.board.piece_placement_fen,
      orientation: candidate.board.orientation,
      orientation_confidence: candidate.board.orientation_confidence,
      declared_fen: candidate.board.declared_fen,
      starting_fen: candidate.board.starting_fen,
      variant: candidate.game.variant,
      result: candidate.status.result,
      visible: candidate.status.document_visible,
      focused: candidate.status.document_focused,
      rect: candidate.board.rect && [candidate.board.rect.left, candidate.board.rect.top, candidate.board.rect.width, candidate.board.rect.height]
    });
  }

  function positionEvidenceKey(candidate) {
    return JSON.stringify({
      game_marker: candidate.game_marker,
      moves: candidate.moves.map((move) => move.san),
      placement: candidate.board.piece_placement_fen,
      declared_fen: candidate.board.declared_fen,
      starting_fen: candidate.board.starting_fen,
      turn: candidate.game.side_to_move,
      result: candidate.status.result
    });
  }

  function buildCandidate() {
    const url = new URL(location.href);
    const gate = classifyPage(url.href);
    const boardMatch = findBoard();
    const board = boardMatch?.element || null;
    const orientation = detectOrientation(board);
    const pieces = extractPieces(board, orientation);
    const lichessState = lichessInitialState(pieces.placement_fen);
    const moveRoot = findMoveList(board);
    const domMoves = extractMoves(moveRoot);
    const initialMoves = (lichessState?.moves || []).map((san, index) => ({ ply: index + 1, san }));
    const moves = domMoves.length >= initialMoves.length ? domMoves : initialMoves;
    const declaredFens = extractDeclaredFens(board, pieces.placement_fen);
    const declaredFen = declaredFens.current || lichessState?.currentFen || null;
    const startingFen = declaredFens.starting || lichessState?.startingFen || null;
    const fenTurn = declaredFen?.split(/\s+/)[1];
    const startingTurn = startingFen?.split(/\s+/)[1] || "w";
    const replayedTurn = moves.length % 2 === 0 ? startingTurn : startingTurn === "w" ? "b" : "w";
    const sideToMove = (fenTurn || replayedTurn) === "w" ? "white" : "black";
    const result = gameResult(moveRoot) || lichessState?.result || null;
    const variant = detectVariant(lichessState);
    const gameId = extractGameId(url, lichessState);
    const gameMarker = `${gate.mode}:${gameId || `${url.pathname}${url.search}`}`;
    const allowed = gate.allowed && variant === "standard";

    return {
      board_element: board,
      move_root: moveRoot,
      game_marker: gameMarker,
      page: {
        url: url.href,
        path: url.pathname,
        game_id: gameId,
        site: siteLabel(url.href)
      },
      eligibility: {
        allowed,
        mode: gate.mode,
        reason: allowed ? gate.reason : variant !== "standard" ? "unsupported_variant" : gate.reason,
        evidence: [...gate.evidence, `variant:${variant}`]
      },
      board: {
        selector_hint: board ? (board.id ? `#${board.id}` : board.tagName.toLowerCase()) : null,
        confidence: boardMatch?.score || 0,
        orientation: orientation.value,
        orientation_confidence: orientation.confidence,
        orientation_source: orientation.source,
        rect: boardRectSnapshot(board),
        piece_placement_fen: pieces.placement_fen,
        piece_map: pieces.pieces,
        piece_source: pieces.source,
        piece_confidence: pieces.confidence,
        declared_fen: declaredFen,
        starting_fen: startingFen
      },
      moves,
      game: {
        variant,
        side_to_move: sideToMove,
        local_color: lichessState?.playerColor || (gate.mode === "computer" || gate.mode === "practice" ? orientation.value : null),
        full_state_source: declaredFen ? "declared-fen" : pieces.placement_fen ? "dom-placement-plus-moves" : "move-list",
        requires_daemon_reconstruction: !declaredFen
      },
      status: {
        phase: result ? "complete" : board ? "in_progress" : "setup",
        result,
        document_visible: document.visibilityState === "visible",
        document_focused: document.hasFocus()
      }
    };
  }

  function shouldStartNewSession(candidate) {
    if (!activeGameMarker) return false;
    if (candidate.game_marker !== activeGameMarker) return true;
    const boardReplaced = lastBoardElement && candidate.board_element && lastBoardElement !== candidate.board_element && !lastBoardElement.isConnected;
    const historyReset = lastMoveCount >= 2 && candidate.moves.length === 0;
    return boardReplaced || historyReset;
  }

  async function sha256Hex(value) {
    const bytes = new TextEncoder().encode(value);
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
  }

  async function emitStableCandidate(candidate, stateKey) {
    if (shouldStartNewSession(candidate)) {
      sessionId = crypto.randomUUID();
      sessionPlayerColor = null;
      sequence = 0;
      lastEmittedKey = "";
      commandResults.clear();
    }

    activeGameMarker = candidate.game_marker;
    lastBoardElement = candidate.board_element;
    lastMoveCount = candidate.moves.length;
    if (sessionPlayerColor === null && ["white", "black"].includes(candidate.game.local_color)) {
      sessionPlayerColor = candidate.game.local_color;
    }
    if (
      sessionPlayerColor === null &&
      candidate.eligibility.mode !== "analysis" &&
      candidate.eligibility.allowed &&
      candidate.board.orientation_confidence >= 0.95 &&
      ["white", "black"].includes(candidate.board.orientation)
    ) {
      // Chess.com initially orients a game board to the player's side. Keep
      // that color stable if the user later presses "Flip board".
      sessionPlayerColor = candidate.board.orientation;
    }
    const positionHash = await sha256Hex(positionEvidenceKey(candidate));
    if (!pendingCandidate || pendingCandidate.key !== stateKey) return;

    if (currentArrowCommand && currentArrowCommand.position_hash !== positionHash) {
      clearBestMoveArrow(true);
    }

    sequence += 1;
    const moveStrings = candidate.moves.map((move) => move.san);
    const snapshot = {
      v: PROTOCOL_VERSION,
      type: "position.snapshot",
      pageId: sessionId,
      session_id: sessionId,
      seq: sequence,
      gameKey: candidate.game_marker,
      positionHash,
      position_hash: positionHash,
      observed_at: new Date().toISOString(),
      page: {
        ...candidate.page,
        gameId: candidate.page.game_id,
        visible: candidate.status.document_visible,
        focused: candidate.status.document_focused
      },
      eligibility: candidate.eligibility,
      board: {
        ...candidate.board,
        pieces: candidate.board.piece_map,
        orientationConfidence: candidate.board.orientation_confidence,
        playerColor: sessionPlayerColor,
        // Keep the observed current state distinct from the root used to
        // replay the move list.
        currentFen: candidate.board.declared_fen,
        startingFen: candidate.board.starting_fen || (moveStrings.length ? STANDARD_FEN : null),
        promotionOptions: {},
        pieceSource: candidate.board.piece_source
      },
      moves: moveStrings,
      move_details: candidate.moves,
      game: {
        ...candidate.game,
        local_color: sessionPlayerColor
      },
      status: candidate.status.phase === "in_progress" ? "playing" : candidate.status.phase,
      state: candidate.status
    };
    currentSnapshot = snapshot;
    currentBoard = candidate.board_element;
    // Keep the dashboard's board and move list tied directly to the DOM
    // observation. Engine reconciliation may finish later, but it must never
    // leave the visible dashboard one move or one orientation behind.
    ensureDashboard();
    dashboard.site.textContent = candidate.page.site;
    renderDashboardBoard(candidate.board.piece_placement_fen || "", candidate.board.orientation);
    renderDashboardMoves(moveStrings);
    dashboard.sync.textContent = `${candidate.page.site} · live · ${(sessionPlayerColor || candidate.board.orientation || "unknown").replace(/^./, (letter) => letter.toUpperCase())}`;
    positionDashboard();
    if (lastEvaluationCommand) renderEvaluationBar(lastEvaluationCommand);
    lastEmittedKey = stateKey;
    sendToWorker({ type: "content.snapshot", snapshot });
  }

  async function scanNow() {
    if (scanning) {
      scanAgain = true;
      return;
    }
    scanning = true;
    try {
      const candidate = buildCandidate();
      const key = candidateStateKey(candidate);
      if (!pendingCandidate || pendingCandidate.key !== key) {
        pendingCandidate = { key, count: 1 };
        setTimeout(scheduleScan, STABILITY_RECHECK_MS);
        return;
      }
      pendingCandidate.count += 1;
      if (pendingCandidate.count < 2 || key === lastEmittedKey) return;
      await emitStableCandidate(candidate, key);
    } catch {
      // Supported chess sites frequently replace large DOM subtrees. A later observer tick retries.
    } finally {
      scanning = false;
      if (scanAgain) {
        scanAgain = false;
        scheduleScan();
      }
    }
  }

  function scheduleScan() {
    if (scanTimer !== null) return;
    scanTimer = setTimeout(() => {
      scanTimer = null;
      void scanNow();
    }, SCAN_DEBOUNCE_MS);
  }

  function sendToWorker(message) {
    try {
      port?.postMessage(message);
    } catch {
      connectPort();
    }
  }

  function connectPort() {
    if (reconnectTimer !== null) clearTimeout(reconnectTimer);
    reconnectTimer = null;
    try {
      port = chrome.runtime.connect({ name: PORT_NAME });
    } catch {
      reconnectTimer = setTimeout(connectPort, 1500);
      return;
    }
    port.onMessage.addListener((message) => {
      if (message?.type === "move.command") void handleMoveCommand(message.command);
      else if (message?.type === "arrow.command") showBestMoveArrow(message.command);
      else if (message?.type === "arrow.clear") clearBestMoveArrow(true);
      else if (message?.type === "overlay.clear") clearBestMoveArrow(true);
      else if (message?.type === "recalibrate.command") recalibrateBoard();
      else if (message?.type === "dashboard.state") updateDashboardState(message.state);
      else if (message?.type === "bridge.status") updateDashboardConnection(message.status);
    });
    port.onDisconnect.addListener(() => {
      port = null;
      reconnectTimer = setTimeout(connectPort, 1200);
    });
    if (currentSnapshot) sendToWorker({ type: "content.snapshot", snapshot: currentSnapshot });
    else scheduleScan();
  }

  function squareCenter(square, board, orientation) {
    if (!/^[a-h][1-8]$/.test(square) || !board || !["white", "black"].includes(orientation)) return null;
    const rect = board.getBoundingClientRect();
    let fileIndex = FILES.indexOf(square[0]);
    let rankIndexFromTop = 8 - Number(square[1]);
    if (orientation === "black") {
      fileIndex = 7 - fileIndex;
      rankIndexFromTop = 7 - rankIndexFromTop;
    }
    return {
      x: rect.left + (fileIndex + 0.5) * rect.width / 8,
      y: rect.top + (rankIndexFromTop + 0.5) * rect.height / 8
    };
  }

  function ensureDashboard() {
    if (dashboard?.host?.isConnected) return;
    const host = document.createElement("div");
    host.id = "chess-trainer-dashboard-host";
    Object.assign(host.style, {
      position: "fixed",
      zIndex: "2147483645",
      width: "370px",
      pointerEvents: "none"
    });
    const shadow = host.attachShadow({ mode: "open" });
    shadow.innerHTML = `
      <style>
        *{box-sizing:border-box}
        .panel{pointer-events:auto;color:#e9edf2;background:rgba(24,25,29,.88);border:1px solid rgba(255,255,255,.13);border-radius:10px;box-shadow:0 16px 42px rgba(0,0,0,.48);backdrop-filter:blur(14px);font:13px/1.35 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;overflow:hidden}
        .head{display:flex;align-items:center;gap:8px;min-height:39px;padding:7px 9px;border-bottom:1px solid rgba(255,255,255,.1);cursor:grab;user-select:none;touch-action:none}.head:active{cursor:grabbing}.grip{width:12px;color:#777f89;font-size:15px;line-height:11px;letter-spacing:1px}.title{font-weight:750;letter-spacing:.1px}.site{color:#c7d7e9;background:rgba(47,111,173,.24);border:1px solid rgba(95,157,219,.3);border-radius:4px;padding:2px 5px;font-size:9px;font-weight:750;letter-spacing:.25px}.dot{width:7px;height:7px;border-radius:50%;background:#d39a3c}.dot.on{background:#81b64c;box-shadow:0 0 0 3px rgba(129,182,76,.16)}.spacer{flex:1}.collapse{border:0;background:transparent;color:#aeb5bf;font-size:18px;cursor:pointer;padding:0 4px;border-radius:4px}.collapse:hover{background:rgba(255,255,255,.08)}
        .body{padding:10px}.panel.collapsed .body{display:none}.panel.collapsed .head{border-bottom:0}.muted{color:#969da7;font-size:11px}.sync{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:7px}
        .workspace{display:grid;grid-template-columns:150px minmax(0,1fr);gap:10px;align-items:start}.board{display:grid;grid-template-columns:repeat(8,minmax(0,1fr));grid-template-rows:repeat(8,minmax(0,1fr));width:150px;height:150px;aspect-ratio:1;border-radius:4px;overflow:hidden;box-shadow:0 0 0 1px rgba(0,0,0,.35)}.sq{position:relative;display:flex;align-items:center;justify-content:center;min-width:0;min-height:0;overflow:hidden}.sq.light{background:#eeeed2}.sq.dark{background:#769656}.piece{display:block;width:100%;height:100%;max-width:100%;max-height:100%;object-fit:contain;pointer-events:none;user-select:none}.coord{position:absolute;z-index:1;font-size:7px;font-weight:800;line-height:1}.coord.file{right:2px;bottom:1px}.coord.rank{left:2px;top:2px}.sq.light .coord{color:#769656}.sq.dark .coord{color:#eeeed2}
        .moves{height:150px;overflow:auto;font:12px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace;background:rgba(0,0,0,.2);border:1px solid rgba(255,255,255,.07);border-radius:5px;padding:6px 7px;color:#d0d5dc;scrollbar-width:thin}.move-row{display:grid;grid-template-columns:24px 1fr 1fr;gap:4px;padding:1px 0}.move-no{color:#777f89}.empty-moves{color:#777f89;padding:5px 2px}
        .engine{margin-top:10px;border:1px solid rgba(255,255,255,.11);border-radius:7px;background:rgba(10,11,14,.26);overflow:hidden}.engine-head{display:flex;align-items:center;gap:7px;min-height:34px;padding:6px 8px;border-bottom:1px solid rgba(255,255,255,.08)}.engine-mark{width:4px;align-self:stretch;border-radius:4px;background:#2f6fad}.engine-name{font-weight:750}.engine-state{min-width:0;flex:1;color:#929aa5;font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.eval-badge{font:750 13px/1 ui-monospace,SFMono-Regular,Consolas,monospace;padding:4px 7px;border-radius:5px;background:rgba(255,255,255,.08)}.odds-status{display:grid;gap:2px;padding:6px 8px;border-bottom:1px solid rgba(255,255,255,.07);background:rgba(47,111,173,.08)}.odds-title{color:#dbeeff;font-size:10px;font-weight:750}.odds-reason{color:#929daa;font-size:9.5px;line-height:1.35}
        .metrics{display:grid;grid-template-columns:repeat(5,1fr);padding:7px 6px 6px}.metric{text-align:center;border-right:1px solid rgba(255,255,255,.07)}.metric:last-child{border-right:0}.metric-label{display:block;color:#7f8791;font-size:9px;font-weight:700;letter-spacing:.45px;text-transform:uppercase}.metric-value{display:block;margin-top:2px;color:#e7eaf0;font:700 11px/1.2 ui-monospace,SFMono-Regular,Consolas,monospace}
        .engine-lines-head{display:flex;align-items:center;justify-content:space-between;padding:6px 8px 5px;border-top:1px solid rgba(255,255,255,.07);color:#7f8791;font-size:9px;font-weight:750;letter-spacing:.45px;text-transform:uppercase}.lines-control{display:flex;align-items:center;gap:5px}.lines-control select{border:1px solid rgba(255,255,255,.12);border-radius:4px;background:#25272c;color:#e9edf2;padding:2px 18px 2px 5px;font:700 10px "Segoe UI",sans-serif}.engine-lines{display:grid;gap:4px;padding:0 7px 7px}.line{display:grid;grid-template-columns:17px 43px minmax(0,1fr);gap:6px;align-items:baseline;padding:6px 7px;border-radius:5px;background:rgba(255,255,255,.045)}.line:first-child{background:rgba(47,111,173,.13);box-shadow:inset 2px 0 #2f6fad}.line-rank{color:#69727e;font:750 9px ui-monospace,SFMono-Regular,Consolas,monospace}.line-score{color:#e6e9ed;font:700 11px ui-monospace,SFMono-Regular,Consolas,monospace}.pv{min-width:0;color:#aeb5bf;font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
        .settings{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-top:9px;padding:8px;border:1px solid rgba(255,255,255,.1);border-radius:7px;background:rgba(10,11,14,.2)}.field{display:grid;gap:3px}.field>span,.contempt-head{color:#8f97a2;font-size:9px;font-weight:700;letter-spacing:.4px;text-transform:uppercase}.field select{width:100%;border:1px solid rgba(255,255,255,.13);border-radius:4px;background:#292b30;color:#edf0f4;padding:5px;font:600 11px "Segoe UI",sans-serif}.contempt{grid-column:1/-1}.contempt-head{display:flex;justify-content:space-between}.contempt-value{color:#dce6f2;font:700 10px ui-monospace,SFMono-Regular,Consolas,monospace}.contempt input{width:100%;accent-color:#2f6fad}.checks{grid-column:1/-1;display:grid;grid-template-columns:1fr 1fr;gap:5px}.check{display:flex;align-items:center;gap:5px;color:#b7bec8;font-size:10px;cursor:pointer}.check input{accent-color:#2f6fad}.hint{grid-column:1/-1;color:#777f89;font-size:9px;line-height:1.3}
        .buttons{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-top:9px}.buttons button{min-width:0;border:1px solid rgba(255,255,255,.13);border-radius:5px;background:rgba(255,255,255,.065);color:#e5e8ec;padding:6px 4px;font:650 11px "Segoe UI",sans-serif;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.buttons button:hover{border-color:#2f6fad;color:#fff}.buttons button.on{background:rgba(47,111,173,.28);border-color:#2f6fad;color:#dbeeff}
      </style>
      <section class="panel">
        <div class="head" title="Drag to move; double-click to dock beside the board"><span class="grip">::</span><span class="dot"></span><span class="title">Chess Trainer</span><span class="site">${siteLabel()}</span><span class="spacer"></span><span class="muted connection">Connecting…</span><button class="collapse" title="Collapse">−</button></div>
        <div class="body">
          <div class="muted sync">Waiting for board</div>
          <div class="workspace"><div class="board"></div><div class="moves"><div class="empty-moves">Moves will appear here</div></div></div>
          <section class="engine thinking">
            <div class="engine-head"><span class="engine-mark"></span><span class="engine-name">Stockfish 18</span><span class="engine-state">Waiting for a position</span><span class="eval-badge">—</span></div>
            <div class="odds-status"><span class="odds-title">Network pending</span><span class="odds-reason">Waiting for a synchronized position.</span></div>
            <div class="metrics">
              <div class="metric"><span class="metric-label">Eval</span><span class="metric-value metric-eval">—</span></div>
              <div class="metric"><span class="metric-label">Depth</span><span class="metric-value depth">—</span></div>
              <div class="metric"><span class="metric-label">Nodes</span><span class="metric-value nodes">—</span></div>
              <div class="metric"><span class="metric-label">NPS</span><span class="metric-value nps">—</span></div>
              <div class="metric"><span class="metric-label">Time</span><span class="metric-value time">—</span></div>
            </div>
            <div class="engine-lines-head"><span>Principal variations</span><label class="lines-control"><span>Lines</span><select class="multipv-select"><option value="1">1</option><option value="2">2</option><option value="3">3</option></select></label></div>
            <div class="engine-lines"><div class="line"><span class="line-rank">#1</span><span class="line-score">—</span><span class="pv">Waiting for engine analysis</span></div></div>
          </section>
          <section class="settings">
            <label class="field"><span>Engine</span><select class="engine-select"><option value="stockfish">Stockfish 18</option><option value="lc0">LCZero 0.32.1</option></select></label>
            <label class="field"><span>Odds network</span><select class="odds-select"><option value="none">Normal (BT4)</option><option value="knight">Minor-equivalent (T1)</option><option value="rook">Rook-equivalent (T1)</option><option value="queen_for_knight">Queen-for-material (T1)</option><option value="queen">Near-full queen odds (LQO)</option></select></label>
            <label class="field contempt"><span class="contempt-head"><span>LC0 contempt</span><output class="contempt-value">0</output></span><input class="contempt-slider" type="range" min="-1000" max="1000" step="25" value="0"></label>
            <div class="checks"><label class="check"><input class="auto-network" type="checkbox">Auto network</label><label class="check"><input class="auto-contempt" type="checkbox">Auto contempt</label><label class="check"><input class="analyze-opponent" type="checkbox">Analyze opponent</label><label class="check"><input class="opponent-arrows" type="checkbox">Opponent arrows</label></div>
            <div class="hint">Auto network uses the player's net deficit plus unmatched piece counts. One or two pawns and an exchange stay on BT4; a full minor with only one pawn back still uses T1. LQO is reserved for an almost uncompensated queen gap. Auto contempt probes at neutral, then adds up to +250 practical bias when the side to move is worse.</div>
          </section>
          <div class="buttons"><button data-action="monitoring">Pause</button><button data-action="recalibrate">Recalibrate</button><button data-action="analyze">Analyze</button><button data-action="overlays">Arrows on</button></div>
        </div>
      </section>`;
    document.documentElement.appendChild(host);
    const panel = shadow.querySelector(".panel");
    shadow.querySelector(".collapse").addEventListener("click", () => {
      panel.classList.toggle("collapsed");
      shadow.querySelector(".collapse").textContent = panel.classList.contains("collapsed") ? "+" : "−";
    });
    const head = shadow.querySelector(".head");
    head.addEventListener("dblclick", (event) => {
      if (event.target.closest("button")) return;
      dashboardPlacement = null;
      chrome.storage.local.remove("dashboardPosition");
      positionDashboard();
    });
    head.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || event.target.closest("button")) return;
      event.preventDefault();
      const startRect = host.getBoundingClientRect();
      const startX = event.clientX;
      const startY = event.clientY;
      head.setPointerCapture(event.pointerId);
      const move = (moveEvent) => {
        const maxX = Math.max(8, innerWidth - host.offsetWidth - 8);
        const panelHeight = Math.max(39, host.offsetHeight);
        const panelFits = panelHeight <= innerHeight - 16;
        const minY = panelFits ? 8 : innerHeight - panelHeight - 8;
        const maxY = panelFits ? innerHeight - panelHeight - 8 : innerHeight - 39 - 8;
        dashboardPlacement = {
          x: Math.max(8, Math.min(maxX, startRect.left + moveEvent.clientX - startX)),
          y: Math.max(minY, Math.min(maxY, startRect.top + moveEvent.clientY - startY))
        };
        positionDashboard();
      };
      const finish = () => {
        head.removeEventListener("pointermove", move);
        head.removeEventListener("pointerup", finish);
        head.removeEventListener("pointercancel", finish);
        if (dashboardPlacement) chrome.storage.local.set({ dashboardPosition: dashboardPlacement });
      };
      head.addEventListener("pointermove", move);
      head.addEventListener("pointerup", finish);
      head.addEventListener("pointercancel", finish);
    });
    shadow.querySelectorAll("button[data-action]").forEach((button) => {
      button.addEventListener("click", () => {
        const action = button.dataset.action;
        if (action === "monitoring") {
          dashboardState.monitoring = !dashboardState.monitoring;
          updateDashboardState(dashboardState);
          sendDashboardAction(action, dashboardState.monitoring);
        } else if (action === "overlays") {
          dashboardState.showOverlays = !dashboardState.showOverlays;
          updateDashboardState(dashboardState);
          if (!dashboardState.showOverlays) clearBestMoveArrow(true);
          sendDashboardAction(action, dashboardState.showOverlays);
        }
        else sendDashboardAction(action);
      });
    });
    dashboard = {
      host, shadow, panel,
      dot: shadow.querySelector(".dot"), connection: shadow.querySelector(".connection"),
      site: shadow.querySelector(".site"), sync: shadow.querySelector(".sync"), engine: shadow.querySelector(".engine"),
      engineState: shadow.querySelector(".engine-state"), evalBadge: shadow.querySelector(".eval-badge"),
      oddsTitle: shadow.querySelector(".odds-title"), oddsReason: shadow.querySelector(".odds-reason"),
      metricEval: shadow.querySelector(".metric-eval"), depth: shadow.querySelector(".depth"),
      nodes: shadow.querySelector(".nodes"), nps: shadow.querySelector(".nps"), time: shadow.querySelector(".time"),
      board: shadow.querySelector(".board"), moves: shadow.querySelector(".moves"), engineLines: shadow.querySelector(".engine-lines"),
      monitor: shadow.querySelector('[data-action="monitoring"]'), overlays: shadow.querySelector('[data-action="overlays"]'),
      engineName: shadow.querySelector(".engine-name"), engineSelect: shadow.querySelector(".engine-select"), multiPvSelect: shadow.querySelector(".multipv-select"),
      oddsSelect: shadow.querySelector(".odds-select"), contemptSlider: shadow.querySelector(".contempt-slider"),
      contemptValue: shadow.querySelector(".contempt-value"), analyzeOpponent: shadow.querySelector(".analyze-opponent"),
      opponentArrows: shadow.querySelector(".opponent-arrows"), autoNetwork: shadow.querySelector(".auto-network"),
      autoContempt: shadow.querySelector(".auto-contempt")
    };
    dashboard.engineSelect.addEventListener("change", () => {
      dashboardState.engineKind = dashboard.engineSelect.value;
      updateDashboardState(dashboardState);
      sendDashboardAction("engine", undefined, dashboard.engineSelect.value);
    });
    dashboard.multiPvSelect.addEventListener("change", () => {
      dashboardState.multiPv = Number(dashboard.multiPvSelect.value);
      updateDashboardState(dashboardState);
      sendDashboardAction("multipv", undefined, dashboardState.multiPv);
    });
    dashboard.oddsSelect.addEventListener("change", () => {
      dashboardState.oddsMode = dashboard.oddsSelect.value;
      dashboardState.oddsTitle = ODDS_MODE_LABELS[dashboardState.oddsMode] || dashboardState.oddsMode;
      dashboardState.oddsReason = "Manual selection requested; waiting for the control centre.";
      updateDashboardState(dashboardState);
      sendDashboardAction("odds", undefined, dashboard.oddsSelect.value);
    });
    dashboard.contemptSlider.addEventListener("input", () => { dashboard.contemptValue.textContent = dashboard.contemptSlider.value; });
    dashboard.contemptSlider.addEventListener("change", () => sendDashboardAction("contempt", undefined, Number(dashboard.contemptSlider.value)));
    dashboard.autoNetwork.addEventListener("change", () => {
      dashboardState.lc0AutoNetwork = dashboard.autoNetwork.checked;
      updateDashboardState(dashboardState);
      sendDashboardAction("auto_network", dashboard.autoNetwork.checked);
    });
    dashboard.autoContempt.addEventListener("change", () => {
      dashboardState.lc0AutoContempt = dashboard.autoContempt.checked;
      updateDashboardState(dashboardState);
      sendDashboardAction("auto_contempt", dashboard.autoContempt.checked);
    });
    dashboard.analyzeOpponent.addEventListener("change", () => {
      dashboardState.analyzeOpponent = dashboard.analyzeOpponent.checked;
      updateDashboardState(dashboardState);
      sendDashboardAction("analyze_opponent", dashboard.analyzeOpponent.checked);
    });
    dashboard.opponentArrows.addEventListener("change", () => {
      dashboardState.showOpponentArrows = dashboard.opponentArrows.checked;
      sendDashboardAction("opponent_arrows", dashboard.opponentArrows.checked);
    });
    positionDashboard();
    chrome.storage.local.get("dashboardPosition", (stored) => {
      const value = stored?.dashboardPosition;
      if (value && Number.isFinite(value.x) && Number.isFinite(value.y)) {
        dashboardPlacement = { x: value.x, y: value.y };
        positionDashboard();
      }
    });
  }

  function positionDashboard() {
    if (!dashboard?.host) return;
    const width = 370;
    if (dashboardPlacement) {
      const maxX = Math.max(8, innerWidth - width - 8);
      const panelHeight = Math.max(39, dashboard.host.offsetHeight);
      const panelFits = panelHeight <= innerHeight - 16;
      const minY = panelFits ? 8 : innerHeight - panelHeight - 8;
      const maxY = panelFits ? innerHeight - panelHeight - 8 : innerHeight - 39 - 8;
      dashboardPlacement.x = Math.max(8, Math.min(maxX, dashboardPlacement.x));
      dashboardPlacement.y = Math.max(minY, Math.min(maxY, dashboardPlacement.y));
      dashboard.host.style.left = `${dashboardPlacement.x}px`;
      dashboard.host.style.top = `${dashboardPlacement.y}px`;
      dashboard.host.style.right = "auto";
      return;
    }
    const rect = currentBoard?.isConnected ? currentBoard.getBoundingClientRect() : null;
    const top = rect ? Math.max(10, Math.min(innerHeight - 80, rect.top)) : 70;
    dashboard.host.style.top = `${top}px`;
    if (rect && rect.right + width + 24 <= innerWidth) {
      dashboard.host.style.left = `${rect.right + 12}px`;
      dashboard.host.style.right = "auto";
    } else {
      dashboard.host.style.left = "auto";
      dashboard.host.style.right = "14px";
    }
  }

  function sendDashboardAction(action, enabled, value) {
    if (!currentSnapshot) return;
    sendToWorker({ type: "dashboard.action", pageId: currentSnapshot.session_id, action, enabled, value });
  }

  function updateDashboardConnection(status) {
    ensureDashboard();
    const connected = status?.state === "connected";
    dashboard.dot.classList.toggle("on", connected);
    dashboard.connection.textContent = connected ? "Connected" : "Offline";
  }

  function updateDashboardState(state) {
    if (!isPlainObject(state)) return;
    ensureDashboard();
    dashboardState = { ...dashboardState, ...state };
    dashboard.site.textContent = dashboardState.site || siteLabel();
    dashboard.sync.textContent = `${dashboardState.site || siteLabel()} · ${String(state.sync || "waiting").replaceAll("_", " ")} · ${state.playerColor || "Unknown"}`;
    renderDashboardBoard(String(state.fen || ""), String(state.orientation || "white"));
    renderDashboardMoves(Array.isArray(state.moves) ? state.moves : []);
    dashboard.monitor.textContent = dashboardState.monitoring ? "Pause" : "Resume";
    dashboard.monitor.classList.toggle("on", Boolean(dashboardState.monitoring));
    dashboard.overlays.textContent = dashboardState.showOverlays ? "Arrows on" : "Arrows off";
    dashboard.overlays.classList.toggle("on", Boolean(dashboardState.showOverlays));
    dashboard.engineName.textContent = dashboardState.engineName || (dashboardState.engineKind === "lc0" ? "LCZero 0.32.1" : "Stockfish 18");
    dashboard.engineSelect.value = dashboardState.engineKind === "lc0" ? "lc0" : "stockfish";
    dashboard.multiPvSelect.value = String([1, 2, 3].includes(dashboardState.multiPv) ? dashboardState.multiPv : 1);
    dashboard.oddsSelect.value = ["none", "knight", "rook", "queen_for_knight", "queen"].includes(dashboardState.oddsMode) ? dashboardState.oddsMode : "none";
    dashboard.oddsTitle.textContent = dashboardState.oddsTitle || ODDS_MODE_LABELS[dashboardState.oddsMode] || "LCZero network";
    dashboard.oddsReason.textContent = dashboardState.oddsReason || "Waiting for the material explanation.";
    dashboard.contemptSlider.value = dashboardState.lc0AutoContempt ? "0" : String(Number.isSafeInteger(dashboardState.lc0Contempt) ? dashboardState.lc0Contempt : 0);
    dashboard.contemptValue.textContent = dashboardState.lc0AutoContempt ? "Auto +0" : dashboard.contemptSlider.value;
    dashboard.autoNetwork.checked = Boolean(dashboardState.lc0AutoNetwork);
    dashboard.autoContempt.checked = Boolean(dashboardState.lc0AutoContempt);
    dashboard.autoNetwork.disabled = dashboardState.engineKind !== "lc0";
    dashboard.autoContempt.disabled = dashboardState.engineKind !== "lc0";
    dashboard.contemptSlider.disabled = dashboardState.engineKind !== "lc0" || dashboardState.lc0AutoContempt;
    dashboard.oddsSelect.disabled = dashboardState.engineKind !== "lc0" || dashboardState.lc0AutoNetwork;
    dashboard.analyzeOpponent.checked = Boolean(dashboardState.analyzeOpponent);
    dashboard.opponentArrows.checked = Boolean(dashboardState.showOpponentArrows);
    dashboard.opponentArrows.disabled = !dashboard.analyzeOpponent.checked;
    if (!dashboardState.monitoring) {
      dashboard.engine.classList.remove("thinking");
      dashboard.engineState.textContent = "Paused";
    } else if (dashboardState.opponentTurn && !dashboardState.analyzeOpponent) {
      dashboard.engine.classList.remove("thinking");
      dashboard.engineState.textContent = "Waiting for your turn";
    } else if (!currentArrowCommand) {
      dashboard.engine.classList.add("thinking");
      dashboard.engineState.textContent = "Analyzing current position";
    }
  }

  function formatEngineCount(value) {
    if (!Number.isFinite(value)) return "—";
    if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)}G`;
    if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
    if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 100_000 ? 0 : 1)}k`;
    return String(value);
  }

  function formatEngineTime(value) {
    if (!Number.isFinite(value)) return "—";
    return value < 1000 ? `${value}ms` : `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)}s`;
  }

  function updateDashboardAnalysis(analysis) {
    ensureDashboard();
    if (!analysis) {
      const shouldThink = Boolean(dashboardState.monitoring) && !(dashboardState.opponentTurn && !dashboardState.analyzeOpponent);
      dashboard.engine.classList.toggle("thinking", shouldThink);
      dashboard.engineState.textContent = !dashboardState.monitoring
        ? "Paused"
        : dashboardState.opponentTurn && !dashboardState.analyzeOpponent
          ? "Waiting for your turn"
          : "Analyzing current position";
      dashboard.evalBadge.textContent = "—";
      dashboard.metricEval.textContent = "—";
      dashboard.depth.textContent = "—";
      dashboard.nodes.textContent = "—";
      dashboard.nps.textContent = "—";
      dashboard.time.textContent = "—";
      renderDashboardVariations(null);
      return;
    }
    if (analysis.engine_name) dashboard.engineName.textContent = analysis.engine_name;
    dashboard.engine.classList.toggle("thinking", Boolean(dashboardState.monitoring));
    const networkLabel = analysis.odds_title || ODDS_MODE_LABELS[analysis.odds_mode] || String(analysis.odds_mode || "BT4").replaceAll("_", " ");
    if (dashboardState.lc0AutoNetwork && Object.hasOwn(ODDS_MODE_LABELS, analysis.odds_mode)) {
      dashboardState.oddsMode = analysis.odds_mode;
      dashboard.oddsSelect.value = analysis.odds_mode;
    }
    dashboardState.oddsTitle = networkLabel;
    dashboardState.oddsReason = analysis.odds_reason || dashboardState.oddsReason;
    dashboard.oddsTitle.textContent = dashboardState.oddsTitle;
    dashboard.oddsReason.textContent = dashboardState.oddsReason;
    const contemptLabel = Number.isSafeInteger(analysis.effective_contempt) ? `C${analysis.effective_contempt >= 0 ? "+" : ""}${analysis.effective_contempt}` : "";
    dashboard.engineState.textContent = `${analysis.opponent_turn ? "Opponent" : "Best move"}: ${analysis.best_move_san || analysis.uci}${contemptLabel ? ` · ${contemptLabel}` : ""}`;
    if (dashboardState.lc0AutoContempt && Number.isSafeInteger(analysis.effective_contempt)) {
      dashboard.contemptSlider.value = String(analysis.effective_contempt);
      dashboard.contemptValue.textContent = `Auto ${analysis.effective_contempt >= 0 ? "+" : ""}${analysis.effective_contempt}`;
    }
    dashboard.evalBadge.textContent = analysis.evaluation || "—";
    dashboard.metricEval.textContent = analysis.evaluation || "—";
    dashboard.depth.textContent = analysis.depth ?? "—";
    dashboard.nodes.textContent = formatEngineCount(analysis.nodes);
    dashboard.nps.textContent = Number.isFinite(analysis.nps) ? `${formatEngineCount(analysis.nps)}/s` : "—";
    dashboard.time.textContent = formatEngineTime(analysis.time_ms);
    renderDashboardVariations(analysis);
  }

  function renderDashboardBoard(fen, orientation) {
    if (!dashboard?.board) return;
    const placement = fen.split(/\s+/)[0] || "";
    const ranks = placement.split("/");
    const pieces = new Map();
    if (ranks.length === 8) {
      ranks.forEach((rankText, row) => {
        let file = 0;
        for (const token of rankText) {
          if (/\d/.test(token)) file += Number(token);
          else { pieces.set(`${FILES[file]}${8 - row}`, token); file += 1; }
        }
      });
    }
    dashboard.board.replaceChildren();
    for (let row = 0; row < 8; row += 1) {
      for (let col = 0; col < 8; col += 1) {
        const fileIndex = orientation === "black" ? 7 - col : col;
        const rank = orientation === "black" ? row + 1 : 8 - row;
        const symbol = pieces.get(`${FILES[fileIndex]}${rank}`) || "";
        const square = document.createElement("div");
        square.className = `sq ${(fileIndex + rank - 1) % 2 ? "light" : "dark"}`;
        if (symbol) {
          const image = document.createElement("img");
          const pieceCode = `${symbol === symbol.toUpperCase() ? "w" : "b"}${symbol.toLowerCase()}`;
          image.className = "piece";
          image.src = chrome.runtime.getURL(`assets/chesscom-neo/${pieceCode}.png`);
          image.alt = "";
          image.draggable = false;
          square.appendChild(image);
        }
        if (row === 7) {
          const fileLabel = document.createElement("span");
          fileLabel.className = "coord file";
          fileLabel.textContent = FILES[fileIndex];
          square.appendChild(fileLabel);
        }
        if (col === 0) {
          const rankLabel = document.createElement("span");
          rankLabel.className = "coord rank";
          rankLabel.textContent = String(rank);
          square.appendChild(rankLabel);
        }
        dashboard.board.appendChild(square);
      }
    }
  }

  function renderDashboardMoves(moves) {
    dashboard.moves.replaceChildren();
    if (!moves.length) {
      const empty = document.createElement("div");
      empty.className = "empty-moves";
      empty.textContent = "Moves will appear here";
      dashboard.moves.appendChild(empty);
      return;
    }
    for (let index = 0; index < moves.length; index += 2) {
      const row = document.createElement("div");
      row.className = "move-row";
      const number = document.createElement("span");
      number.className = "move-no";
      number.textContent = `${index / 2 + 1}.`;
      const white = document.createElement("span");
      white.textContent = moves[index] || "";
      const black = document.createElement("span");
      black.textContent = moves[index + 1] || "";
      row.append(number, white, black);
      dashboard.moves.appendChild(row);
    }
    dashboard.moves.scrollTop = dashboard.moves.scrollHeight;
  }

  function removeArrowOverlay() {
    arrowOverlay?.remove();
    arrowOverlay = null;
  }

  function removeEvaluationOverlay() {
    evalOverlay?.remove();
    evalOverlay = null;
  }

  function removeBoardOverlays() {
    removeArrowOverlay();
    removeEvaluationOverlay();
  }

  function clearBestMoveArrow(preserveEvaluation = false) {
    currentArrowCommand = null;
    removeArrowOverlay();
    if (!preserveEvaluation) {
      lastEvaluationCommand = null;
      removeEvaluationOverlay();
      updateDashboardAnalysis(null);
    }
  }

  function renderEvaluationBar(analysis) {
    if (!analysis || !currentSnapshot || !currentBoard?.isConnected || !["white", "black"].includes(currentSnapshot.board.orientation)) return;
    const rect = currentBoard.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return;
    removeEvaluationOverlay();
    const barWidth = 25;
    const evalBar = document.createElement("div");
    evalBar.setAttribute("aria-hidden", "true");
    Object.assign(evalBar.style, {
      position: "fixed", left: `${Math.max(2, rect.left - barWidth - 7)}px`, top: `${rect.top}px`,
      width: `${barWidth}px`, height: `${rect.height}px`, overflow: "hidden", borderRadius: "2px",
      background: "#343a40", pointerEvents: "none", zIndex: "2147483646"
    });
    let whiteShare = 0.5;
    if (analysis.white_mate !== null) whiteShare = analysis.white_mate > 0 ? 0.985 : analysis.white_mate < 0 ? 0.015 : 0.5;
    else if (analysis.white_score_cp !== null) {
      whiteShare = 1 / (1 + Math.exp(-Math.max(-2000, Math.min(2000, analysis.white_score_cp)) / 400));
      whiteShare = Math.max(0.025, Math.min(0.975, whiteShare));
    }
    const whiteFill = document.createElement("div");
    Object.assign(whiteFill.style, {
      position: "absolute", left: "0", width: "100%", height: `${whiteShare * 100}%`, background: "#e9ecef",
      top: currentSnapshot.board.orientation === "black" ? "0" : "auto",
      bottom: currentSnapshot.board.orientation === "white" ? "0" : "auto", transition: "height 180ms ease"
    });
    evalBar.appendChild(whiteFill);
    const scoreLabel = document.createElement("div");
    scoreLabel.textContent = analysis.white_mate !== null ? `M${Math.abs(analysis.white_mate)}` : analysis.white_score_cp !== null
      ? `${Math.abs(analysis.white_score_cp) >= 995 ? Math.round(Math.abs(analysis.white_score_cp) / 100) : (Math.abs(analysis.white_score_cp) / 100).toFixed(1)}` : "0.0";
    const whiteWinning = whiteShare >= 0.5;
    const labelAtBottom = whiteWinning === (currentSnapshot.board.orientation === "white");
    Object.assign(scoreLabel.style, {
      position: "absolute", left: "0", right: "0", top: labelAtBottom ? "auto" : "4px", bottom: labelAtBottom ? "4px" : "auto",
      color: whiteWinning ? "#343a40" : "#e9ecef", textAlign: "center",
      font: "700 11px/1 ui-monospace, SFMono-Regular, Consolas, monospace", letterSpacing: "-.3px", whiteSpace: "nowrap"
    });
    evalBar.appendChild(scoreLabel);
    document.documentElement.appendChild(evalBar);
    evalOverlay = evalBar;
  }

  function recalibrateBoard() {
    clearBestMoveArrow();
    sessionId = crypto.randomUUID();
    sessionPlayerColor = null;
    sequence = 0;
    pendingCandidate = null;
    lastEmittedKey = "";
    activeGameMarker = "";
    lastBoardElement = null;
    lastMoveCount = 0;
    currentSnapshot = null;
    currentBoard = null;
    scheduleScan();
  }

  function renderDashboardVariations(analysis) {
    if (!dashboard?.engineLines) return;
    const variations = analysis
      ? (analysis.variations.length
          ? analysis.variations
          : [{
              rank: 1,
              uci: analysis.uci,
              evaluation: analysis.evaluation,
              pv: analysis.pv
            }])
      : [];
    dashboard.engineLines.replaceChildren();
    if (!variations.length) {
      const row = document.createElement("div");
      row.className = "line";
      row.innerHTML = '<span class="line-rank">#1</span><span class="line-score">—</span><span class="pv">Waiting for engine analysis</span>';
      dashboard.engineLines.appendChild(row);
      return;
    }
    for (const variation of variations.slice(0, dashboardState.multiPv || 1)) {
      const row = document.createElement("div");
      row.className = "line";
      const rank = document.createElement("span");
      rank.className = "line-rank";
      rank.textContent = `#${variation.rank}`;
      const score = document.createElement("span");
      score.className = "line-score";
      score.textContent = variation.evaluation || (variation.rank === 1 ? analysis.evaluation : "—");
      const pv = document.createElement("span");
      pv.className = "pv";
      pv.textContent = variation.pv || (variation.rank === 1 ? analysis.pv : variation.uci) || "Waiting for line";
      pv.title = pv.textContent;
      row.append(rank, score, pv);
      dashboard.engineLines.appendChild(row);
    }
  }

  function showBestMoveArrow(command) {
    if (!isPlainObject(command) || !currentSnapshot || !currentBoard?.isConnected) return;
    const normalized = {
      session_id: command.session_id || command.pageId,
      seq: command.seq,
      position_hash: command.position_hash || command.positionHash,
      uci: String(command.uci || "").toLowerCase(),
      white_score_cp: Number.isFinite(command.white_score_cp) ? command.white_score_cp : Number.isFinite(command.whiteScoreCp) ? command.whiteScoreCp : null,
      white_mate: Number.isSafeInteger(command.white_mate) ? command.white_mate : Number.isSafeInteger(command.whiteMate) ? command.whiteMate : null,
      best_move_san: String(command.best_move_san || command.bestMoveSan || ""),
      evaluation: String(command.evaluation || "—"),
      pv: String(command.pv || ""),
      depth: Number.isSafeInteger(command.depth) ? command.depth : null,
      nodes: Number.isSafeInteger(command.nodes) && command.nodes >= 0 ? command.nodes : null,
      nps: Number.isSafeInteger(command.nps) && command.nps >= 0 ? command.nps : null,
      time_ms: Number.isSafeInteger(command.time_ms) && command.time_ms >= 0 ? command.time_ms : null,
      show_overlays: command.show_overlays !== false && command.showOverlays !== false,
      engine_name: String(command.engine_name || command.engineName || ""),
      engine_kind: String(command.engine_kind || command.engineKind || ""),
      multi_pv: Number.isSafeInteger(command.multi_pv) ? Math.max(1, Math.min(3, command.multi_pv)) : 1,
      variations: (Array.isArray(command.variations) ? command.variations : [])
        .filter((variation) => isPlainObject(variation) && typeof variation.uci === "string")
        .map((variation, index) => ({
          rank: Number.isSafeInteger(variation.rank) ? variation.rank : index + 1,
          uci: String(variation.uci).toLowerCase(),
          score_cp: Number.isFinite(variation.score_cp) ? variation.score_cp : null,
          mate: Number.isSafeInteger(variation.mate) ? variation.mate : null,
          depth: Number.isSafeInteger(variation.depth) ? variation.depth : null,
          evaluation: String(variation.evaluation || "—"),
          pv: String(variation.pv || "")
        }))
        .slice(0, 3),
      odds_mode: String(command.odds_mode || command.oddsMode || "none"),
      odds_title: String(command.odds_title || command.oddsTitle || ""),
      odds_reason: String(command.odds_reason || command.oddsReason || ""),
      effective_contempt: Number.isSafeInteger(command.effective_contempt) ? command.effective_contempt : Number.isSafeInteger(command.effectiveContempt) ? command.effectiveContempt : null,
      lc0_auto_network: command.lc0_auto_network === true || command.lc0AutoNetwork === true,
      lc0_auto_contempt: command.lc0_auto_contempt === true || command.lc0AutoContempt === true,
      opponent_turn: command.opponent_turn === true || command.opponentTurn === true
    };
    if (normalized.session_id !== currentSnapshot.session_id) return;
    if (normalized.seq !== currentSnapshot.seq) return;
    if (normalized.position_hash !== currentSnapshot.position_hash) return;
    if (!/^[a-h][1-8][a-h][1-8][qrbn]?$/.test(normalized.uci)) return;
    currentArrowCommand = normalized;
    lastEvaluationCommand = normalized;
    updateDashboardAnalysis(normalized);
    renderEvaluationBar(normalized);
    if (!normalized.show_overlays || !["white", "black"].includes(currentSnapshot.board.orientation)) {
      removeArrowOverlay();
      return;
    }

    const rect = currentBoard.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return;
    const rawVariations = normalized.variations.length
      ? normalized.variations
      : [{ rank: 1, uci: normalized.uci, score_cp: normalized.white_score_cp, mate: normalized.white_mate, depth: normalized.depth }];
    const variations = visibleEngineVariations(rawVariations);
    if (!variations.length) return;

    removeArrowOverlay();
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${rect.width} ${rect.height}`);
    svg.setAttribute("aria-hidden", "true");
    Object.assign(svg.style, {
      position: "fixed",
      left: `${rect.left}px`,
      top: `${rect.top}px`,
      width: `${rect.width}px`,
      height: `${rect.height}px`,
      pointerEvents: "none",
      overflow: "visible",
      zIndex: "2147483646"
    });

    const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
    svg.appendChild(defs);

    for (const [index, variation] of variations.entries()) {
      const from = squareCenter(variation.uci.slice(0, 2), currentBoard, currentSnapshot.board.orientation);
      const to = squareCenter(variation.uci.slice(2, 4), currentBoard, currentSnapshot.board.orientation);
      if (!from || !to) continue;
      const opacity = index === 0 ? 0.72 : index === 1 ? 0.38 : 0.24;
      const markerId = `chess-trainer-best-move-head-${index}`;
      const marker = document.createElementNS("http://www.w3.org/2000/svg", "marker");
      marker.setAttribute("id", markerId);
      marker.setAttribute("markerWidth", "4");
      marker.setAttribute("markerHeight", "4");
      marker.setAttribute("refX", "2.05");
      marker.setAttribute("refY", "2");
      marker.setAttribute("orient", "auto");
      marker.setAttribute("markerUnits", "strokeWidth");
      marker.setAttribute("overflow", "visible");
      const head = document.createElementNS("http://www.w3.org/2000/svg", "path");
      head.setAttribute("d", "M0,0 V4 L3,2 Z");
      head.setAttribute("fill", "#2f6fad");
      marker.appendChild(head);
      defs.appendChild(marker);

      const dx = to.x - from.x;
      const dy = to.y - from.y;
      const angle = Math.atan2(dy, dx);
      const arrowMargin = rect.width / 51.2;
      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("x1", String(from.x - rect.left));
      line.setAttribute("y1", String(from.y - rect.top));
      line.setAttribute("x2", String(to.x - rect.left - Math.cos(angle) * arrowMargin));
      line.setAttribute("y2", String(to.y - rect.top - Math.sin(angle) * arrowMargin));
      line.setAttribute("stroke", "#2f6fad");
      line.setAttribute(
        "stroke-width",
        String(index === 0
          ? Math.max(6, rect.width * 14 / 512)
          : Math.max(3, rect.width * variation.brushWidth / 512))
      );
      line.setAttribute("stroke-linecap", "round");
      line.setAttribute("opacity", String(opacity));
      line.setAttribute("marker-end", `url(#${markerId})`);
      svg.appendChild(line);
    }

    document.documentElement.appendChild(svg);

    arrowOverlay = svg;
  }

  function visibleEngineVariations(rawVariations) {
    const sorted = [...rawVariations]
      .filter((variation) => /^[a-h][1-8][a-h][1-8][qrbn]?$/.test(variation.uci))
      .sort((left, right) => left.rank - right.rank);
    if (!sorted.length) return [];
    const visible = [];
    const bestWinChance = engineWinChance(sorted[0]);
    for (const variation of sorted) {
      const winChance = engineWinChance(variation);
      const winChanceDrop = bestWinChance === null || winChance === null ? (variation.rank - 1) * 2.5 : bestWinChance - winChance;
      visible.push({
        ...variation,
        brushWidth: winChanceDrop < 2.5 ? 11 : winChanceDrop < 5 ? 7.5 : 4
      });
    }
    return visible;
  }

  function engineWinChance(variation) {
    const score = variation.mate !== null
      ? (variation.mate > 0 ? 100000 : -100000)
      : variation.score_cp;
    if (!Number.isFinite(score)) return null;
    return 50 + 50 * (2 / (1 + Math.exp(-0.00368208 * score)) - 1);
  }

  function targetBelongsToBoard(target, board) {
    return target === board || board.contains(target);
  }

  function dispatchPointerClick(target, point) {
    const common = {
      bubbles: true,
      cancelable: true,
      composed: true,
      clientX: point.x,
      clientY: point.y,
      screenX: window.screenX + point.x,
      screenY: window.screenY + point.y,
      button: 0,
      buttons: 1,
      view: window
    };
    if (typeof PointerEvent === "function") {
      target.dispatchEvent(new PointerEvent("pointermove", { ...common, pointerId: 1, pointerType: "mouse", isPrimary: true }));
      target.dispatchEvent(new PointerEvent("pointerdown", { ...common, pointerId: 1, pointerType: "mouse", isPrimary: true }));
      target.dispatchEvent(new PointerEvent("pointerup", { ...common, buttons: 0, pointerId: 1, pointerType: "mouse", isPrimary: true }));
    } else {
      target.dispatchEvent(new MouseEvent("mousedown", common));
      target.dispatchEvent(new MouseEvent("mouseup", { ...common, buttons: 0 }));
    }
    target.dispatchEvent(new MouseEvent("click", { ...common, buttons: 0 }));
  }

  function clickBoardSquare(square, board, orientation) {
    const point = squareCenter(square, board, orientation);
    if (!point) return { ok: false, reason: "square_mapping_failed" };
    const target = document.elementFromPoint(point.x, point.y);
    if (!(target instanceof Element) || !targetBelongsToBoard(target, board)) {
      return { ok: false, reason: "board_square_obstructed" };
    }
    dispatchPointerClick(target, point);
    return { ok: true };
  }

  function promotionCode(element) {
    const raw = [
      element.getAttribute?.("data-promotion"),
      element.getAttribute?.("data-piece"),
      element.getAttribute?.("data-type"),
      element.getAttribute?.("aria-label"),
      Array.from(element.classList || []).join(" "),
      element.textContent
    ].filter(Boolean).join(" ").toLowerCase();
    const named = raw.match(/\b(?:promote(?: to)?[\s:_-]*)?(queen|rook|bishop|knight)\b/);
    if (named) return PIECE_NAMES[named[1]];
    const compact = raw.match(/(?:^|[\s_-])[wb]?([qrbn])(?:$|[\s_-])/);
    return compact ? compact[1] : null;
  }

  function findPromotionChoice(code, board) {
    const selectors = [
      "[data-promotion]",
      "[class*='promotion' i] [data-piece]",
      "[class*='promotion' i] [class~='piece']",
      "[class*='promotion' i] button",
      "[role='dialog'] [data-piece]",
      "[role='dialog'] button[aria-label]",
      "[aria-label*='promote' i]"
    ];
    const boardRect = board.getBoundingClientRect();
    return uniqueElements(selectors.flatMap((selector) => queryAllSafe(document, selector)))
      .filter(isVisible)
      .filter((element) => promotionCode(element) === code)
      .filter((element) => {
        const rect = element.getBoundingClientRect();
        const margin = Math.max(boardRect.width, boardRect.height) / 8 + 12;
        return rect.right >= boardRect.left - margin && rect.left <= boardRect.right + margin && rect.bottom >= boardRect.top - margin && rect.top <= boardRect.bottom + margin;
      })
      .sort((a, b) => {
        const aDialog = a.closest("[role='dialog'], [class*='promotion' i]") ? 1 : 0;
        const bDialog = b.closest("[role='dialog'], [class*='promotion' i]") ? 1 : 0;
        return bDialog - aDialog;
      })[0] || null;
  }

  async function waitForPromotionChoice(code, board, timeoutMs = 1300) {
    const deadline = performance.now() + timeoutMs;
    while (performance.now() < deadline) {
      const choice = findPromotionChoice(code, board);
      if (choice) return choice;
      await new Promise((resolve) => setTimeout(resolve, 45));
    }
    return null;
  }

  function clickPromotionChoice(choice) {
    const rect = choice.getBoundingClientRect();
    const point = { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    const target = document.elementFromPoint(point.x, point.y);
    if (!(target instanceof Element) || !(target === choice || choice.contains(target) || target.contains(choice))) return false;
    dispatchPointerClick(target, point);
    return true;
  }

  async function waitForPositionChange(beforeKey, timeoutMs = 2200) {
    const deadline = performance.now() + timeoutMs;
    while (performance.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 70));
      try {
        const candidate = buildCandidate();
        if (positionEvidenceKey(candidate) !== beforeKey) return true;
      } catch {
        // Retry while the UI is between animation frames.
      }
    }
    return false;
  }

  function rectStillMatches(snapshotRect, board) {
    if (!snapshotRect || !board) return false;
    const rect = board.getBoundingClientRect();
    return Math.abs(rect.left - snapshotRect.left) <= 2 && Math.abs(rect.top - snapshotRect.top) <= 2 && Math.abs(rect.width - snapshotRect.width) <= 2 && Math.abs(rect.height - snapshotRect.height) <= 2;
  }

  function commandResult(command, status, reason, observedHash = null) {
    return {
      v: PROTOCOL_VERSION,
      type: "move.result",
      requestId: command?.request_id || "invalid",
      request_id: command?.request_id || "invalid",
      pageId: command?.session_id || sessionId,
      session_id: command?.session_id || sessionId,
      seq: Number.isSafeInteger(command?.seq) ? command.seq : null,
      positionHash: typeof command?.position_hash === "string" ? command.position_hash : null,
      position_hash: typeof command?.position_hash === "string" ? command.position_hash : null,
      status,
      reason,
      observed_position_hash: observedHash || currentSnapshot?.position_hash || null,
      ts: Date.now()
    };
  }

  function finishCommand(command, result) {
    commandResults.set(command.request_id, result);
    while (commandResults.size > 256) commandResults.delete(commandResults.keys().next().value);
    sendToWorker({ type: "content.move_result", result });
  }

  async function handleMoveCommand(command) {
    if (!isPlainObject(command) || typeof command.request_id !== "string") return;
    const prior = commandResults.get(command.request_id);
    if (prior) {
      sendToWorker({ type: "content.move_result", result: prior });
      return;
    }
    const reject = (reason) => finishCommand(command, commandResult(command, "rejected", reason));
    if (executing) return reject("another_command_in_progress");
    if (!currentSnapshot || !currentBoard?.isConnected) return reject("no_stable_board");
    if (command.session_id !== currentSnapshot.session_id) return reject("stale_session");
    if (command.seq !== currentSnapshot.seq) return reject("stale_sequence");
    if (command.position_hash !== currentSnapshot.position_hash) return reject("stale_position_hash");
    if (typeof command.expires_at !== "number" || command.expires_at < Date.now()) return reject("expired_command");
    if (typeof command.uci !== "string" || !/^[a-h][1-8][a-h][1-8][qrbn]?$/.test(command.uci)) return reject("invalid_uci");
    const gate = classifyPage(location.href);
    if (!gate.allowed || gate.mode !== currentSnapshot.eligibility.mode || currentSnapshot.eligibility.allowed !== true) return reject("page_not_eligible");
    if (document.visibilityState !== "visible" || !document.hasFocus()) return reject("page_not_visible_and_focused");
    if (currentSnapshot.board.orientation_confidence < 0.95 || !["white", "black"].includes(currentSnapshot.board.orientation)) return reject("orientation_not_verified");
    if (!rectStillMatches(currentSnapshot.board.rect, currentBoard)) return reject("board_moved_or_resized");

    executing = true;
    const beforeKey = positionEvidenceKey(buildCandidate());
    try {
      const from = command.uci.slice(0, 2);
      const to = command.uci.slice(2, 4);
      const promotion = command.uci[4] || null;
      const first = clickBoardSquare(from, currentBoard, currentSnapshot.board.orientation);
      if (!first.ok) return reject(first.reason);
      await new Promise((resolve) => setTimeout(resolve, 85));
      if (command.position_hash !== currentSnapshot.position_hash || command.session_id !== currentSnapshot.session_id) return reject("position_changed_during_move");
      const second = clickBoardSquare(to, currentBoard, currentSnapshot.board.orientation);
      if (!second.ok) return reject(second.reason);

      if (promotion) {
        const choice = await waitForPromotionChoice(promotion, currentBoard);
        if (choice) {
          if (!clickPromotionChoice(choice)) return reject("promotion_choice_obstructed");
        } else if (promotion !== "q") {
          return reject("underpromotion_picker_not_semantically_readable");
        }
      }

      const changed = await waitForPositionChange(beforeKey);
      if (!changed) return reject(promotion ? "promotion_or_move_not_observed" : "move_not_observed");
      scheduleScan();
      finishCommand(command, commandResult(command, "observed_change", null));
    } catch (error) {
      reject(error instanceof Error ? `execution_error:${error.message.slice(0, 100)}` : "execution_error");
    } finally {
      executing = false;
    }
  }

  const observer = new MutationObserver(scheduleScan);
  function startObservation() {
    if (!document.documentElement) {
      setTimeout(startObservation, 50);
      return;
    }
    observer.observe(document.documentElement, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true,
      attributeFilter: [
        "class", "style", "data-square", "data-piece", "data-fen", "data-orientation",
        "data-flipped", "data-coordinates", "data-game-id", "aria-label"
      ]
    });
    ensureDashboard();
    scheduleScan();
  }

  window.addEventListener("resize", scheduleScan, { passive: true });
  window.addEventListener("resize", () => {
    if (currentArrowCommand) showBestMoveArrow(currentArrowCommand);
    else if (lastEvaluationCommand) renderEvaluationBar(lastEvaluationCommand);
  }, { passive: true });
  window.addEventListener("scroll", () => {
    if (currentArrowCommand) showBestMoveArrow(currentArrowCommand);
    else if (lastEvaluationCommand) renderEvaluationBar(lastEvaluationCommand);
  }, { passive: true });
  window.addEventListener("resize", positionDashboard, { passive: true });
  window.addEventListener("scroll", positionDashboard, { passive: true });
  document.addEventListener("visibilitychange", scheduleScan, { passive: true });
  window.addEventListener("focus", scheduleScan, { passive: true });
  window.addEventListener("blur", scheduleScan, { passive: true });
  setInterval(() => {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      pendingCandidate = null;
      scheduleScan();
    }
  }, URL_POLL_MS);
  // A low-frequency health scan covers renderer-only changes without walking
  // the document twice per second during otherwise idle pages.
  setInterval(scheduleScan, 5000);

  connectPort();
  startObservation();
})();
