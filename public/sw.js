// Revacc Service Worker — minimal stub to prevent 500 errors
// No caching logic; just a no-op that responds to install/activate events.

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  // Pass through — no interception
});
