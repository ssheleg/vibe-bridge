/* vibe-bridge service worker: offline honesty + consent push (SCN-004/019).
   Scope is the origin root — served from /sw.js. */
const OFFLINE_URL = "/offline.html";
const CACHE = "vb-shell-v1";

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.add(OFFLINE_URL)));
  self.skipWaiting();
});
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (e) => {
  if (e.request.mode !== "navigate") return;
  e.respondWith(
    fetch(e.request).catch(() => caches.match(OFFLINE_URL))
  );
});

self.addEventListener("push", (e) => {
  let data = {};
  try { data = e.data ? e.data.json() : {}; } catch (_) {}
  const title = data.title || "vibe-bridge";
  const opts = {
    body: data.summary || data.text || "",
    tag: data.id || "vb",
    data: { id: data.id || null, url: "/" },
    // Allow/Deny прямо на уведомлении — Android; iOS игнорирует actions,
    // там тап открывает панель с карточкой (research-notes §C).
    actions: data.kind === "consent" ? [
      { action: "allow", title: "Разрешить" },
      { action: "deny", title: "Отклонить" },
    ] : [],
  };
  e.waitUntil(self.registration.showNotification(title, opts));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const id = e.notification.data && e.notification.data.id;
  if (e.action === "allow" || e.action === "deny") {
    e.waitUntil(
      fetch("/api/consent/decide", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ id, decision: e.action }),
      }).then((r) => {
        const word = e.action === "allow" ? "разрешено" : "отклонено";
        return self.registration.showNotification("vibe-bridge",
          { body: r.ok ? "Действие " + word : "Запрос уже решён или истёк",
            tag: "vb-result" });
      }).catch(() =>
        self.registration.showNotification("vibe-bridge",
          { body: "Нет связи с мостом — решение не доставлено", tag: "vb-result" }))
    );
    return;
  }
  e.waitUntil(self.clients.matchAll({ type: "window" }).then((ws) => {
    for (const w of ws) { if ("focus" in w) return w.focus(); }
    return self.clients.openWindow("/");
  }));
});
