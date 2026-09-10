// 앱 껍데기만 캐시. API 응답은 캐시하지 않음(실시간).
const C = "cx-jp-v2";
const SHELL = ["./", "./index.html", "./manifest.webmanifest", "./icon.svg"];
self.addEventListener("install", e => { e.waitUntil(caches.open(C).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener("activate", e => { e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== C).map(k => caches.delete(k)))).then(() => self.clients.claim())); });
self.addEventListener("fetch", e => {
  const u = new URL(e.request.url);
  if (u.pathname.includes("/api/")) return;                     // 실시간 API는 항상 네트워크
  if (u.origin !== location.origin) return;                     // 타일·CDN은 브라우저 캐시에 맡김
  e.respondWith(fetch(e.request).then(r => { const cp = r.clone(); caches.open(C).then(c => c.put(e.request, cp)); return r; })
                .catch(() => caches.match(e.request)));
});
