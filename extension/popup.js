(function () {
  "use strict";

  const elements = {
    dot: document.getElementById("status-dot"),
    connection: document.getElementById("connection-label"),
    board: document.getElementById("board-label"),
    analyze: document.getElementById("analyze"),
    recalibrate: document.getElementById("recalibrate"),
    engine: document.getElementById("engine"),
    monitoring: document.getElementById("monitoring"),
    overlays: document.getElementById("overlays"),
    analyzeOpponent: document.getElementById("analyze-opponent"),
    feedback: document.getElementById("feedback"),
    settings: document.getElementById("settings")
  };

  const boardControls = [
    elements.analyze, elements.recalibrate, elements.engine,
    elements.monitoring, elements.overlays, elements.analyzeOpponent
  ];

  function setFeedback(message, error = false) {
    elements.feedback.textContent = message;
    elements.feedback.classList.toggle("error", error);
  }

  function render(data) {
    const status = data?.status || { state: "idle" };
    const connected = status.state === "connected";
    elements.dot.className = `status-dot ${connected ? "connected" : ["error", "disconnected"].includes(status.state) ? "error" : ""}`;
    elements.connection.textContent = connected ? "Control centre connected" : status.detail || "Waiting for the control centre";
    elements.board.textContent = data?.activeBoard
      ? `${data.site || "Chess board"} · active tab`
      : "Open or focus a supported Chess.com or Lichess board";

    const enabled = connected && Boolean(data?.activeBoard);
    boardControls.forEach((control) => { control.disabled = !enabled; });
    const state = data?.dashboard || {};
    elements.engine.value = state.engineKind === "lc0" ? "lc0" : "stockfish";
    elements.monitoring.checked = state.monitoring !== false;
    elements.overlays.checked = state.showOverlays !== false;
    elements.analyzeOpponent.checked = state.analyzeOpponent === true;
  }

  async function refresh() {
    try {
      render(await chrome.runtime.sendMessage({ type: "popup.get_state" }));
    } catch {
      render({ status: { state: "error", detail: "Extension service worker is restarting" }, activeBoard: false });
    }
  }

  async function sendAction(action, values = {}) {
    setFeedback("Applying…");
    try {
      const response = await chrome.runtime.sendMessage({ type: "popup.action", action, ...values });
      if (!response?.ok) {
        const labels = {
          no_active_board: "Focus a supported chess board first.",
          control_centre_offline: "Start the desktop control centre first."
        };
        setFeedback(labels[response?.reason] || "That control could not be applied.", true);
        return;
      }
      setFeedback(action === "recalibrate" ? "Recalibrating board…" : action === "analyze" ? "Analysis requested." : "Setting updated.");
    } catch {
      setFeedback("The extension service worker is restarting.", true);
    }
  }

  elements.analyze.addEventListener("click", () => void sendAction("analyze"));
  elements.recalibrate.addEventListener("click", () => void sendAction("recalibrate"));
  elements.engine.addEventListener("change", () => void sendAction("engine", { value: elements.engine.value }));
  elements.monitoring.addEventListener("change", () => void sendAction("monitoring", { enabled: elements.monitoring.checked }));
  elements.overlays.addEventListener("change", () => void sendAction("overlays", { enabled: elements.overlays.checked }));
  elements.analyzeOpponent.addEventListener("change", () => void sendAction("analyze_opponent", { enabled: elements.analyzeOpponent.checked }));
  elements.settings.addEventListener("click", () => void chrome.runtime.openOptionsPage());

  chrome.runtime.onMessage.addListener((message) => {
    if (message?.type === "bridge.status") void refresh();
  });

  void refresh();
})();
