(function () {
  "use strict";

  const statusText = document.getElementById("connection-status");
  const statusDot = document.getElementById("status-dot");

  function setConnectionStatus(status) {
    const state = status?.state || "idle";
    const labels = {
      connected: "Connected automatically to the control centre",
      connecting: "Connecting automatically…",
      error: status?.detail || "Local service unavailable",
      disconnected: status?.detail || "Local service disconnected",
      idle: "Waiting for the local control centre"
    };
    const visualState = state === "connected" ? "connected" : state === "error" || state === "disconnected" ? "error" : "idle";
    statusText.textContent = labels[state] || status?.detail || state;
    statusText.dataset.state = visualState;
    statusDot.dataset.state = visualState;
  }

  async function refreshStatus() {
    try {
      const response = await chrome.runtime.sendMessage({ type: "options.get_status" });
      setConnectionStatus(response?.status);
    } catch {
      setConnectionStatus({ state: "disconnected", detail: "Extension service worker is restarting" });
    }
  }

  chrome.runtime.onMessage.addListener((message) => {
    if (message?.type === "bridge.status") setConnectionStatus(message.status);
  });

  void refreshStatus();
  setInterval(refreshStatus, 2500);
})();
