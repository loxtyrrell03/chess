"use strict";

const PROTOCOL_VERSION = 1;
const CONTENT_PORT_NAME = "local-chess-training-v1";
const DEFAULT_ENDPOINT = "ws://127.0.0.1:8765/v1/extension";
const MAX_WIRE_BYTES = 96 * 1024;
const KEEPALIVE_MS = 20_000;
const MAX_RECONNECT_MS = 30_000;
const ALLOWED_HOSTS = new Set(["chess.com", "www.chess.com", "lichess.org", "www.lichess.org"]);
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost"]);

/** @type {WebSocket | null} */
let socket = null;
let socketGeneration = 0;
let reconnectTimer = null;
let keepaliveTimer = null;
let reconnectAttempts = 0;
let bridgeStatus = { state: "idle", detail: "Waiting for configuration" };

/** @type {Map<string, { port: chrome.runtime.Port, tabId: number, frameId: number, senderUrl: string }>} */
const contentPorts = new Map();
/** @type {Map<string, string>} */
const sessionOwners = new Map();
/** @type {Map<string, any>} */
const latestSnapshots = new Map();
/** @type {Map<string, any>} */
const latestDashboardStates = new Map();
/** @type {Map<string, { state: string, response?: any }>} */
const handledCommands = new Map();

function portKey(tabId, frameId) {
  return `${tabId}:${frameId}`;
}

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function asFiniteInteger(value) {
  return Number.isSafeInteger(value) && value >= 0 ? value : null;
}

function parseExpiry(value) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function classifyChessUrl(rawUrl) {
  let url;
  try {
    url = new URL(rawUrl);
  } catch {
    return { allowed: false, mode: "unknown", reason: "invalid_url" };
  }

  if (url.protocol !== "https:" || !ALLOWED_HOSTS.has(url.hostname.toLowerCase())) {
    return { allowed: false, mode: "unknown", reason: "unexpected_origin" };
  }

  const path = url.pathname.replace(/\/{2,}/g, "/").toLowerCase();
  const lichess = ["lichess.org", "www.lichess.org"].includes(url.hostname.toLowerCase());
  const analysis = lichess
    ? ["/analysis", "/study", "/editor"].some((prefix) => path === prefix || path.startsWith(`${prefix}/`))
    : path === "/analysis" || path.startsWith("/analysis/");
  return {
    allowed: true,
    mode: analysis ? "analysis" : lichess ? "lichess" : "training",
    reason: analysis ? "analysis_workspace" : lichess ? "supported_lichess_route" : "all_routes_permitted"
  };
}

function validateEndpoint(value) {
  let url;
  try {
    url = new URL(value || DEFAULT_ENDPOINT);
  } catch {
    return null;
  }
  if (url.protocol !== "ws:" || url.username || url.password) return null;
  if (!LOOPBACK_HOSTS.has(url.hostname.toLowerCase())) return null;
  return url.toString();
}

async function getClientId() {
  const settings = await chrome.storage.local.get({ clientId: "" });
  if (typeof settings.clientId === "string" && settings.clientId.length >= 16) return settings.clientId;
  const clientId = crypto.randomUUID();
  await chrome.storage.local.set({ clientId });
  return clientId;
}

function setStatus(state, detail) {
  bridgeStatus = { state, detail };
  for (const entry of contentPorts.values()) {
    try {
      entry.port.postMessage({ type: "bridge.status", status: bridgeStatus });
    } catch {
      // A disconnected port is removed by its onDisconnect listener.
    }
  }
  chrome.runtime.sendMessage({ type: "bridge.status", status: bridgeStatus }).catch(() => {});
}

function clearSocketTimers() {
  if (reconnectTimer !== null) clearTimeout(reconnectTimer);
  if (keepaliveTimer !== null) clearInterval(keepaliveTimer);
  reconnectTimer = null;
  keepaliveTimer = null;
}

function closeSocket(reason) {
  socketGeneration += 1;
  clearSocketTimers();
  const oldSocket = socket;
  socket = null;
  if (oldSocket && oldSocket.readyState < WebSocket.CLOSING) {
    try {
      oldSocket.close(1000, reason.slice(0, 120));
    } catch {
      // Ignore races while Chrome tears down a service worker.
    }
  }
}

function socketIsOpen() {
  return socket?.readyState === WebSocket.OPEN;
}

function sendWire(message) {
  if (!socketIsOpen()) return false;
  let encoded;
  try {
    encoded = JSON.stringify(message);
  } catch {
    return false;
  }
  if (encoded.length > MAX_WIRE_BYTES) return false;
  socket.send(encoded);
  return true;
}

function scheduleReconnect(generation) {
  if (generation !== socketGeneration || reconnectTimer !== null) return;
  const base = Math.min(MAX_RECONNECT_MS, 1000 * 2 ** Math.min(reconnectAttempts, 5));
  const delay = Math.round(base * (0.8 + Math.random() * 0.4));
  reconnectAttempts += 1;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    void connectSocket("retry");
  }, delay);
}

async function connectSocket(reason) {
  const generation = ++socketGeneration;
  clearSocketTimers();

  if (socket && socket.readyState < WebSocket.CLOSING) {
    try {
      socket.close(1000, "reconnect");
    } catch {
      // Ignore close races.
    }
  }
  socket = null;

  const endpoint = validateEndpoint(DEFAULT_ENDPOINT);
  if (!endpoint) {
    setStatus("error", "The configured endpoint is not a permitted loopback WebSocket URL");
    return;
  }

  setStatus("connecting", reason === "retry" ? "Retrying the local control centre" : "Connecting to the local control centre");
  let nextSocket;
  try {
    nextSocket = new WebSocket(endpoint, "local-chess-training-v1");
  } catch {
    setStatus("error", "Could not create the local WebSocket connection");
    scheduleReconnect(generation);
    return;
  }
  socket = nextSocket;

  nextSocket.addEventListener("open", async () => {
    if (generation !== socketGeneration || socket !== nextSocket) return;
    reconnectAttempts = 0;
    const clientId = await getClientId();
    if (generation !== socketGeneration || socket !== nextSocket) return;
    sendWire({
      v: PROTOCOL_VERSION,
      type: "hello",
      clientId,
      extensionVersion: chrome.runtime.getManifest().version,
      capabilities: [
        "dom-observation",
        "training-mode-gate",
        "position-hash",
        "best-move-arrow"
      ]
    });
    setStatus("connected", "Connected automatically to the local control centre");
    for (const snapshot of latestSnapshots.values()) sendWire(snapshot);
    keepaliveTimer = setInterval(() => {
      sendWire({ v: PROTOCOL_VERSION, type: "ping", ts: Date.now() });
    }, KEEPALIVE_MS);
  });

  nextSocket.addEventListener("message", (event) => {
    if (generation !== socketGeneration || socket !== nextSocket) return;
    void handleServerMessage(event.data);
  });

  nextSocket.addEventListener("error", () => {
    if (generation !== socketGeneration || socket !== nextSocket) return;
    setStatus("error", "The local control centre rejected or lost the connection");
  });

  nextSocket.addEventListener("close", (event) => {
    if (generation !== socketGeneration || socket !== nextSocket) return;
    socket = null;
    if (keepaliveTimer !== null) clearInterval(keepaliveTimer);
    keepaliveTimer = null;
    setStatus("disconnected", "Local control centre disconnected");
    scheduleReconnect(generation);
  });
}

function snapshotLooksValid(snapshot) {
  if (!isPlainObject(snapshot)) return false;
  if (snapshot.v !== PROTOCOL_VERSION || snapshot.type !== "position.snapshot") return false;
  if (typeof snapshot.session_id !== "string" || snapshot.session_id.length < 8 || snapshot.session_id.length > 128) return false;
  if (asFiniteInteger(snapshot.seq) === null) return false;
  if (typeof snapshot.position_hash !== "string" || !/^[a-f0-9]{64}$/i.test(snapshot.position_hash)) return false;
  if (!isPlainObject(snapshot.page) || typeof snapshot.page.url !== "string") return false;
  if (!isPlainObject(snapshot.eligibility)) return false;
  if (!Array.isArray(snapshot.moves) || snapshot.moves.length > 700) return false;
  try {
    return JSON.stringify(snapshot).length <= MAX_WIRE_BYTES;
  } catch {
    return false;
  }
}

async function currentTabUrl(entry, fallback) {
  try {
    const tab = await chrome.tabs.get(entry.tabId);
    return typeof tab.url === "string" ? tab.url : fallback;
  } catch {
    return fallback;
  }
}

async function acceptSnapshot(entry, untrustedSnapshot) {
  if (!snapshotLooksValid(untrustedSnapshot)) return;
  const tabUrl = await currentTabUrl(entry, untrustedSnapshot.page.url);
  const actualGate = classifyChessUrl(tabUrl);
  const claimedGate = classifyChessUrl(untrustedSnapshot.page.url);
  const modeMatches = actualGate.mode === claimedGate.mode;
  const contentAllows = untrustedSnapshot.eligibility.allowed === true;
  const allowed = actualGate.allowed && claimedGate.allowed && modeMatches && contentAllows;

  const snapshot = {
    ...untrustedSnapshot,
    page: {
      ...untrustedSnapshot.page,
      url: tabUrl
    },
    eligibility: {
      allowed,
      mode: allowed ? actualGate.mode : actualGate.mode || "unknown",
      evidence: [actualGate.reason, claimedGate.reason, ...(Array.isArray(untrustedSnapshot.eligibility.evidence) ? untrustedSnapshot.eligibility.evidence.slice(0, 8) : [])],
      reason: allowed ? "independent_gates_passed" : `denied:${actualGate.reason}:${claimedGate.reason}`
    }
  };

  const ownerKey = portKey(entry.tabId, entry.frameId);
  for (const [oldSessionId, owner] of sessionOwners.entries()) {
    if (owner === ownerKey && oldSessionId !== snapshot.session_id) {
      sessionOwners.delete(oldSessionId);
      latestSnapshots.delete(oldSessionId);
      latestDashboardStates.delete(oldSessionId);
    }
  }
  const previous = latestSnapshots.get(snapshot.session_id);
  if (previous && previous.position_hash !== snapshot.position_hash) {
    try {
      entry.port.postMessage({ type: "arrow.clear" });
    } catch {
      // The disconnect handler will remove this page immediately afterward.
    }
  }
  sessionOwners.set(snapshot.session_id, ownerKey);
  latestSnapshots.set(snapshot.session_id, snapshot);
  while (latestSnapshots.size > 16) {
    const oldest = latestSnapshots.keys().next().value;
    latestSnapshots.delete(oldest);
    sessionOwners.delete(oldest);
    latestDashboardStates.delete(oldest);
  }
  sendWire(snapshot);
}

function commandError(command, reason, snapshot = null) {
  const requestId = typeof command?.request_id === "string" ? command.request_id : typeof command?.requestId === "string" ? command.requestId : "invalid";
  const sessionId = typeof command?.session_id === "string" ? command.session_id : typeof command?.pageId === "string" ? command.pageId : null;
  const positionHash = typeof command?.position_hash === "string" ? command.position_hash : typeof command?.positionHash === "string" ? command.positionHash : null;
  return {
    v: PROTOCOL_VERSION,
    type: "move.result",
    requestId,
    request_id: requestId,
    pageId: sessionId,
    session_id: sessionId,
    seq: Number.isSafeInteger(command?.seq) ? command.seq : null,
    positionHash,
    position_hash: positionHash,
    status: "rejected",
    reason,
    observed_position_hash: snapshot?.position_hash || null,
    ts: Date.now()
  };
}

function rememberCommand(requestId, record) {
  handledCommands.set(requestId, record);
  while (handledCommands.size > 512) handledCommands.delete(handledCommands.keys().next().value);
}

async function handleMoveCommand(message) {
  const incoming = isPlainObject(message.payload) ? message.payload : message;
  if (!isPlainObject(incoming)) return;
  const command = {
    ...incoming,
    request_id: incoming.request_id || incoming.requestId,
    session_id: incoming.session_id || incoming.pageId,
    position_hash: incoming.position_hash || incoming.positionHash,
    expires_at: incoming.expires_at ?? incoming.expiresAt
  };
  if (typeof command.request_id !== "string" || command.request_id.length < 8 || command.request_id.length > 128) {
    sendWire(commandError(command, "invalid_request_id"));
    return;
  }

  const previous = handledCommands.get(command.request_id);
  if (previous) {
    if (previous.response) sendWire(previous.response);
    else sendWire({ v: PROTOCOL_VERSION, type: "move.ack", request_id: command.request_id, status: "duplicate_ignored" });
    return;
  }

  const snapshot = latestSnapshots.get(command.session_id);
  const reject = (reason) => {
    const response = commandError(command, reason, snapshot);
    rememberCommand(command.request_id, { state: "rejected", response });
    sendWire(response);
  };

  if (!snapshot) return reject("unknown_session");
  if (asFiniteInteger(command.seq) === null || command.seq !== snapshot.seq) return reject("stale_sequence");
  if (command.position_hash !== snapshot.position_hash) return reject("stale_position_hash");
  if (typeof command.uci !== "string" || !/^[a-h][1-8][a-h][1-8][qrbn]?$/i.test(command.uci)) return reject("invalid_uci");
  const expiry = parseExpiry(command.expires_at);
  if (expiry === null || expiry < Date.now() || expiry > Date.now() + 120_000) return reject("expired_or_invalid_expiry");
  if (snapshot.eligibility?.allowed !== true) return reject("page_not_eligible");

  const ownerKey = sessionOwners.get(command.session_id);
  const owner = ownerKey ? contentPorts.get(ownerKey) : null;
  if (!owner) return reject("page_disconnected");
  const tabUrl = await currentTabUrl(owner, snapshot.page.url);
  const gate = classifyChessUrl(tabUrl);
  if (!gate.allowed || gate.mode !== snapshot.eligibility.mode) return reject("route_changed_or_not_eligible");

  rememberCommand(command.request_id, { state: "accepted" });
  try {
    owner.port.postMessage({
      type: "move.command",
      command: {
        v: PROTOCOL_VERSION,
        request_id: command.request_id,
        session_id: command.session_id,
        seq: command.seq,
        position_hash: command.position_hash,
        uci: command.uci.toLowerCase(),
        expires_at: expiry
      }
    });
    sendWire({ v: PROTOCOL_VERSION, type: "move.ack", request_id: command.request_id, status: "accepted", ts: Date.now() });
  } catch {
    const response = commandError(command, "content_port_failed", snapshot);
    rememberCommand(command.request_id, { state: "failed", response });
    sendWire(response);
  }
}

async function handleArrowCommand(message, clear = false) {
  const incoming = isPlainObject(message.payload) ? message.payload : message;
  if (!isPlainObject(incoming)) return;
  const sessionId = incoming.session_id || incoming.pageId;
  const positionHash = incoming.position_hash || incoming.positionHash;
  const snapshot = latestSnapshots.get(sessionId);
  if (!snapshot) return;
  if (asFiniteInteger(incoming.seq) === null || incoming.seq !== snapshot.seq) return;
  if (positionHash !== snapshot.position_hash) return;
  if (!clear && (typeof incoming.uci !== "string" || !/^[a-h][1-8][a-h][1-8][qrbn]?$/i.test(incoming.uci))) return;
  if (snapshot.eligibility?.allowed !== true) return;

  const ownerKey = sessionOwners.get(sessionId);
  const owner = ownerKey ? contentPorts.get(ownerKey) : null;
  if (!owner) return;
  const tabUrl = await currentTabUrl(owner, snapshot.page.url);
  const gate = classifyChessUrl(tabUrl);
  if (!gate.allowed || gate.mode !== snapshot.eligibility.mode) return;

  try {
    owner.port.postMessage(clear ? { type: "arrow.clear" } : {
      type: "arrow.command",
      command: {
        v: PROTOCOL_VERSION,
        session_id: sessionId,
        seq: snapshot.seq,
        position_hash: snapshot.position_hash,
        uci: incoming.uci.toLowerCase(),
        white_score_cp: Number.isFinite(incoming.whiteScoreCp) ? Math.max(-100000, Math.min(100000, incoming.whiteScoreCp)) : null,
        white_mate: Number.isSafeInteger(incoming.whiteMate) ? incoming.whiteMate : null,
        best_move_san: typeof incoming.bestMoveSan === "string" ? incoming.bestMoveSan.slice(0, 24) : "",
        evaluation: typeof incoming.evaluation === "string" ? incoming.evaluation.slice(0, 24) : "—",
        pv: typeof incoming.pv === "string" ? incoming.pv.slice(0, 500) : "",
        depth: Number.isSafeInteger(incoming.depth) ? incoming.depth : null,
        nodes: Number.isSafeInteger(incoming.nodes) && incoming.nodes >= 0 ? incoming.nodes : null,
        nps: Number.isSafeInteger(incoming.nps) && incoming.nps >= 0 ? incoming.nps : null,
        time_ms: Number.isSafeInteger(incoming.timeMs) && incoming.timeMs >= 0 ? incoming.timeMs : null,
        show_overlays: incoming.showOverlays !== false,
        engine_name: typeof incoming.engineName === "string" ? incoming.engineName.slice(0, 100) : "",
        engine_kind: incoming.engineKind === "lc0" ? "lc0" : "stockfish",
        multi_pv: Number.isSafeInteger(incoming.multiPv) ? Math.max(1, Math.min(3, incoming.multiPv)) : 1,
        variations: (Array.isArray(incoming.variations) ? incoming.variations : [])
          .slice(0, 3)
          .filter((variation) => isPlainObject(variation) && typeof variation.uci === "string" && /^[a-h][1-8][a-h][1-8][qrbn]?$/i.test(variation.uci))
          .map((variation, index) => ({
            rank: Number.isSafeInteger(variation.rank) ? Math.max(1, Math.min(3, variation.rank)) : index + 1,
            uci: variation.uci.toLowerCase(),
            score_cp: Number.isFinite(variation.scoreCp) ? Math.max(-100000, Math.min(100000, variation.scoreCp)) : null,
            mate: Number.isSafeInteger(variation.mate) ? variation.mate : null,
            depth: Number.isSafeInteger(variation.depth) ? variation.depth : null,
            evaluation: typeof variation.evaluation === "string" ? variation.evaluation.slice(0, 24) : "—",
            pv: typeof variation.pv === "string" ? variation.pv.slice(0, 500) : ""
          })),
        odds_mode: typeof incoming.oddsMode === "string" ? incoming.oddsMode.slice(0, 32) : "none",
        odds_title: typeof incoming.oddsTitle === "string" ? incoming.oddsTitle.slice(0, 120) : "",
        odds_reason: typeof incoming.oddsReason === "string" ? incoming.oddsReason.slice(0, 400) : "",
        effective_contempt: Number.isSafeInteger(incoming.effectiveContempt) ? incoming.effectiveContempt : null,
        lc0_auto_network: incoming.lc0AutoNetwork === true,
        lc0_auto_contempt: incoming.lc0AutoContempt === true,
        opponent_turn: incoming.opponentTurn === true
      }
    });
  } catch {
    // A disconnected content port is removed by its disconnect listener.
  }
}

async function handleOverlayClear(message) {
  const sessionId = message.pageId || message.session_id;
  const snapshot = latestSnapshots.get(sessionId);
  if (!snapshot || message.seq !== snapshot.seq || message.positionHash !== snapshot.position_hash) return;
  const ownerKey = sessionOwners.get(sessionId);
  const owner = ownerKey ? contentPorts.get(ownerKey) : null;
  if (!owner) return;
  try {
    owner.port.postMessage({ type: "overlay.clear" });
  } catch {
    // The disconnect handler removes stale ownership.
  }
}

function handleRecalibrateCommand(message) {
  const sessionId = message.pageId || message.session_id;
  const ownerKey = sessionOwners.get(sessionId);
  const owner = ownerKey ? contentPorts.get(ownerKey) : null;
  if (!owner) return;
  try {
    owner.port.postMessage({ type: "recalibrate.command" });
  } catch {
    // The disconnect handler removes stale ownership.
  }
}

function handleDashboardState(message) {
  const sessionId = message.pageId || message.session_id;
  const snapshot = latestSnapshots.get(sessionId);
  if (!snapshot || message.seq !== snapshot.seq || message.positionHash !== snapshot.position_hash) return;
  const ownerKey = sessionOwners.get(sessionId);
  const owner = ownerKey ? contentPorts.get(ownerKey) : null;
  if (!owner) return;
  latestDashboardStates.set(sessionId, message);
  try {
    owner.port.postMessage({ type: "dashboard.state", state: message });
  } catch {
    // The disconnect handler removes stale ownership.
  }
}

async function activePopupContext() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!Number.isInteger(tab?.id)) return null;
  let best = null;
  for (const [ownerKey, entry] of contentPorts.entries()) {
    if (entry.tabId !== tab.id) continue;
    for (const [sessionId, sessionOwner] of sessionOwners.entries()) {
      if (sessionOwner !== ownerKey) continue;
      const snapshot = latestSnapshots.get(sessionId);
      if (!snapshot || (best && snapshot.seq <= best.snapshot.seq)) continue;
      best = {
        sessionId,
        ownerKey,
        owner: entry,
        snapshot,
        dashboard: latestDashboardStates.get(sessionId) || null
      };
    }
  }
  return best;
}

function optimisticDashboardState(context, action, enabled, value) {
  const current = context.dashboard || {};
  const keys = {
    monitoring: "monitoring",
    overlays: "showOverlays",
    analyze_opponent: "analyzeOpponent",
    opponent_arrows: "showOpponentArrows",
    engine: "engineKind",
    multipv: "multiPv",
    odds: "oddsMode",
    contempt: "lc0Contempt",
    auto_network: "lc0AutoNetwork",
    auto_contempt: "lc0AutoContempt"
  };
  const key = keys[action];
  if (!key) return;
  const next = { ...current, [key]: value ?? enabled };
  latestDashboardStates.set(context.sessionId, next);
  try {
    context.owner.port.postMessage({ type: "dashboard.state", state: next });
  } catch {
    // The content-port disconnect handler removes stale sessions.
  }
}

async function handlePopupAction(message) {
  const allowed = new Set([
    "analyze", "recalibrate", "monitoring", "overlays", "analyze_opponent",
    "opponent_arrows", "engine", "multipv", "odds", "contempt", "auto_network", "auto_contempt"
  ]);
  const action = String(message.action || "");
  if (!allowed.has(action)) return { ok: false, reason: "unsupported_action" };
  const context = await activePopupContext();
  if (!context) return { ok: false, reason: "no_active_board" };
  const enabled = typeof message.enabled === "boolean" ? message.enabled : undefined;
  const value = typeof message.value === "string" || Number.isSafeInteger(message.value) ? message.value : undefined;
  const ok = sendWire({
    v: PROTOCOL_VERSION,
    type: "dashboard.action",
    pageId: context.sessionId,
    action,
    enabled,
    value
  });
  if (!ok) return { ok: false, reason: "control_centre_offline" };
  optimisticDashboardState(context, action, enabled, value);
  return { ok: true };
}

async function handleServerMessage(rawData) {
  if (typeof rawData !== "string" || rawData.length > MAX_WIRE_BYTES) return;
  let message;
  try {
    message = JSON.parse(rawData);
  } catch {
    return;
  }
  if (!isPlainObject(message) || message.v !== PROTOCOL_VERSION || typeof message.type !== "string") return;

  if (message.type === "ping") {
    sendWire({ v: PROTOCOL_VERSION, type: "pong", ts: Date.now() });
  } else if (message.type === "hello.ack" || message.type === "extension.ready") {
    setStatus("connected", message.detail || "Connected to the local engine");
  } else if (message.type === "arrow.command") {
    await handleArrowCommand(message);
  } else if (message.type === "arrow.clear") {
    await handleArrowCommand(message, true);
  } else if (message.type === "overlay.clear") {
    await handleOverlayClear(message);
  } else if (message.type === "recalibrate.command") {
    handleRecalibrateCommand(message);
  } else if (message.type === "dashboard.state") {
    handleDashboardState(message);
  }
}

function acceptMoveResult(entry, result) {
  if (!isPlainObject(result) || result.v !== PROTOCOL_VERSION || result.type !== "move.result") return;
  if (typeof result.request_id !== "string" || !handledCommands.has(result.request_id)) return;
  if (typeof result.session_id !== "string" || sessionOwners.get(result.session_id) !== portKey(entry.tabId, entry.frameId)) return;
  const snapshot = latestSnapshots.get(result.session_id);
  const safe = {
    v: PROTOCOL_VERSION,
    type: "move.result",
    requestId: result.request_id,
    request_id: result.request_id,
    pageId: result.session_id,
    session_id: result.session_id,
    seq: Number.isSafeInteger(result.seq) ? result.seq : null,
    positionHash: typeof result.position_hash === "string" ? result.position_hash : null,
    position_hash: typeof result.position_hash === "string" ? result.position_hash : null,
    status: typeof result.status === "string" ? result.status.slice(0, 48) : "unknown",
    reason: typeof result.reason === "string" ? result.reason.slice(0, 160) : null,
    observed_position_hash: typeof result.observed_position_hash === "string" ? result.observed_position_hash : snapshot?.position_hash || null,
    ts: Date.now()
  };
  rememberCommand(result.request_id, { state: "complete", response: safe });
  sendWire(safe);
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== CONTENT_PORT_NAME || !port.sender.tab || port.sender.frameId !== 0) {
    port.disconnect();
    return;
  }
  const tabId = port.sender.tab.id;
  if (!Number.isInteger(tabId)) {
    port.disconnect();
    return;
  }
  const senderUrl = port.sender.url || port.sender.tab.url || "";
  const senderGate = classifyChessUrl(senderUrl);
  if (senderGate.reason === "unexpected_origin" || senderGate.reason === "invalid_url") {
    port.disconnect();
    return;
  }

  const entry = { port, tabId, frameId: port.sender.frameId, senderUrl };
  const key = portKey(tabId, port.sender.frameId);
  contentPorts.set(key, entry);
  port.postMessage({ type: "bridge.status", status: bridgeStatus });
  port.onMessage.addListener((message) => {
    if (!isPlainObject(message)) return;
    if (message.type === "content.snapshot") void acceptSnapshot(entry, message.snapshot);
    else if (message.type === "content.move_result") acceptMoveResult(entry, message.result);
    else if (message.type === "dashboard.action") {
      const action = String(message.action || "");
      if (!["analyze", "recalibrate", "monitoring", "overlays", "analyze_opponent", "opponent_arrows", "engine", "multipv", "odds", "contempt", "auto_network", "auto_contempt"].includes(action)) return;
      if (sessionOwners.get(message.pageId) !== key) return;
      sendWire({
        v: PROTOCOL_VERSION,
        type: "dashboard.action",
        pageId: message.pageId,
        action,
        enabled: typeof message.enabled === "boolean" ? message.enabled : undefined,
        value: typeof message.value === "string" || Number.isSafeInteger(message.value) ? message.value : undefined
      });
    }
  });
  port.onDisconnect.addListener(() => {
    contentPorts.delete(key);
    for (const [sessionId, owner] of sessionOwners.entries()) {
      if (owner === key) {
        sessionOwners.delete(sessionId);
        latestSnapshots.delete(sessionId);
        latestDashboardStates.delete(sessionId);
      }
    }
  });
  if (!socketIsOpen() && socket?.readyState !== WebSocket.CONNECTING) void connectSocket("content_connected");
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "options.get_status") {
    sendResponse({ status: bridgeStatus });
    return false;
  }
  if (message?.type === "options.reconnect") {
    void connectSocket("settings_updated");
    sendResponse({ ok: true });
    return false;
  }
  if (message?.type === "popup.get_state") {
    void activePopupContext().then((context) => {
      sendResponse({
        status: bridgeStatus,
        activeBoard: Boolean(context),
        site: context?.snapshot?.page?.site || "",
        url: context?.snapshot?.page?.url || "",
        dashboard: context?.dashboard || null
      });
    });
    return true;
  }
  if (message?.type === "popup.action") {
    void handlePopupAction(message).then(sendResponse);
    return true;
  }
  return false;
});

chrome.runtime.onInstalled.addListener(() => {
  void getClientId();
});

void connectSocket("service_worker_started");
