// Reads the WhatsApp Web chat list (only what's on screen: name, unread badge,
// last-message preview) and hands it to the background worker. Never opens
// chats and never reads message history.
(function () {
  function text(el) { return el ? (el.innerText || el.textContent || "").trim() : ""; }

  function readChats() {
    var pane = document.querySelector("#pane-side") || document;
    var rows = pane.querySelectorAll('[role="listitem"], [role="row"]');
    var out = [];
    rows.forEach(function (row) {
      var titleEl = row.querySelector("span[title]");
      var name = titleEl ? titleEl.getAttribute("title") : "";
      if (!name) return;
      var unread = 0;
      row.querySelectorAll("[aria-label]").forEach(function (b) {
        var label = (b.getAttribute("aria-label") || "").toLowerCase();
        if (/unread|no le[ií]do/.test(label)) {
          var n = parseInt(text(b), 10) || parseInt(label, 10);
          unread = Math.max(unread, isNaN(n) || n <= 0 ? 1 : n);
        }
      });
      if (!unread) return;
      var spans = row.querySelectorAll("span[title]");
      var preview = spans.length > 1 ? spans[spans.length - 1].getAttribute("title") : "";
      if (preview === name) preview = "";
      out.push({ name: name, unread: unread, preview: (preview || "").slice(0, 300) });
    });
    return out;
  }

  var last = "";
  function push(force) {
    var chats = readChats();
    var sig = JSON.stringify(chats);
    if (!force && sig === last) return;
    last = sig;
    chrome.runtime.sendMessage({ type: "chats", chats: chats });
  }

  setTimeout(function () { push(true); }, 8000);          // after WhatsApp loads
  setInterval(function () { push(false); }, 60 * 1000);  // check for changes each minute
  chrome.runtime.onMessage.addListener(function (m) { if (m && m.type === "sync-now") push(true); });
})();
