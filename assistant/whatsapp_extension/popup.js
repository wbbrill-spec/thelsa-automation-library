async function show() {
  const s = await chrome.storage.local.get(["syncKey", "lastSync", "lastStatus"]);
  document.getElementById("key").value = s.syncKey ? "••••••••" + s.syncKey.slice(-4) : "";
  document.getElementById("status").textContent = s.lastSync
    ? "Last sync: " + new Date(s.lastSync).toLocaleString() + " (" + (s.lastStatus || "") + ")"
    : (s.syncKey ? "Key saved. Open web.whatsapp.com to sync." : "Not set up yet.");
}
document.getElementById("save").onclick = async () => {
  const v = document.getElementById("key").value.trim();
  if (v.startsWith("wa_")) {
    await chrome.storage.local.set({ syncKey: v });
    const tabs = await chrome.tabs.query({ url: "https://web.whatsapp.com/*" }).catch(() => []);
    tabs.forEach((t) => chrome.tabs.sendMessage(t.id, { type: "sync-now" }).catch(() => {}));
  }
  show();
};
show();
