const SERVER = "__SERVER__";
let pending = null;

async function send(chats) {
  const { syncKey } = await chrome.storage.local.get("syncKey");
  if (!syncKey) return;
  try {
    const r = await fetch(SERVER + "/api/assistant/whatsapp/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + syncKey },
      body: JSON.stringify({ chats })
    });
    await chrome.storage.local.set({ lastSync: new Date().toISOString(), lastStatus: r.ok ? "ok" : ("error " + r.status) });
  } catch (e) {
    await chrome.storage.local.set({ lastStatus: "error: " + e.message });
  }
}

chrome.runtime.onMessage.addListener((m) => {
  if (m && m.type === "chats") { pending = m.chats; send(m.chats); }
});

// Heartbeat every 5 minutes so the dashboard's "as of" time stays current.
chrome.alarms.create("wa-sync", { periodInMinutes: 5 });
chrome.alarms.onAlarm.addListener(async () => {
  const tabs = await chrome.tabs.query({ url: "https://web.whatsapp.com/*" }).catch(() => []);
  tabs.forEach((t) => chrome.tabs.sendMessage(t.id, { type: "sync-now" }).catch(() => {}));
});
