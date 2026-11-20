document.addEventListener("DOMContentLoaded", async () => {
  const toggle = document.getElementById("intercept-toggle");
  const dot = document.getElementById("status-dot");
  const text = document.getElementById("status-text");

  // Load saved toggle state
  const { interceptionEnabled = true } = await chrome.storage.local.get("interceptionEnabled");
  toggle.checked = interceptionEnabled;

  toggle.addEventListener("change", () => {
    chrome.storage.local.set({ interceptionEnabled: toggle.checked });
  });

  // Check if NFD app is running
  async function checkStatus() {
    try {
      const res = await fetch("http://127.0.0.1:8000/api/config", {
        method: "GET",
        headers: { "Accept": "application/json" },
        signal: AbortSignal.timeout(2000)
      });
      if (res.ok) {
        dot.classList.add("connected");
        text.innerText = "NFD App Connected";
        text.style.color = "#10b981";
      } else {
        dot.classList.remove("connected");
        text.innerText = `Backend error (${res.status})`;
        text.style.color = "#f59e0b";
      }
    } catch (e) {
      dot.classList.remove("connected");
      text.innerText = "NFD is not running";
      text.style.color = "#ef4444";
    }
  }

  await checkStatus();
});
