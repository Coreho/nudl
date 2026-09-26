// nudl's service worker: works offline, and keeps shared links off the network.
//
// A link shared to the installed app from Android's share sheet arrives as
// ./?text=<the link>. Without this worker that would be a request to the web server,
// with the link in it. With it, every page load is answered from the cache, and on a
// miss the page is fetched WITHOUT the query — the link never leaves the phone.

const VERSION = "__BUILD__"; // replaced by tools/build_web.py with a hash of the build
const CACHE = `nudl-${VERSION}`;
const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/";
const SHELL = [
  "./",
  "./index.html",
  "./engine/clean.py",
  "./engine/rules.json",
  "./manifest.webmanifest",
  "./icon-192.png",
  "./icon-512.png",
];

self.addEventListener("install", (event) => {
  // The install must never fail over the cache. A worker that fails to install is no
  // worker at all, and then a shared link goes to the server in the page's address —
  // the one thing this file exists to prevent. Cache what can be cached (storage can be
  // full, or refused outright in a private window); the fetch handler copes without it.
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => Promise.allSettled(SHELL.map((url) => cache.add(url))))
      .catch(() => undefined),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  if (request.mode === "navigate") {
    // Whatever happens, the page is fetched WITHOUT its query: that is where a shared
    // link sits, and it stays on the device.
    const page = new URL("./", self.registration.scope);
    event.respondWith(
      caches
        .match("./index.html")
        .catch(() => undefined)
        .then((hit) => hit || fetch(page)),
    );
    return;
  }

  const url = new URL(request.url);
  const ours = url.origin === self.location.origin;
  if (!ours && !request.url.startsWith(PYODIDE)) return;

  // Cache first: the engine and Pyodide are versioned, so a cached copy is never stale
  // within one build, and a new build gets a new cache.
  event.respondWith(
    caches.match(request).then(
      (hit) =>
        hit ||
        fetch(request).then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        }),
    ),
  );
});
