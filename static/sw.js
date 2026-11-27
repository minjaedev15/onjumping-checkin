/* 온점핑 출석 서비스 워커 (PWA 설치 조건을 채우는 최소 구현) */
const CACHE = "onjumping-v1";
const SHELL = ["/manifest.webmanifest", "/icons/icon-192.png", "/icons/icon-512.png"];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE)
      .then(function (cache) {
        return cache.addAll(SHELL).catch(function () {});
      })
      .then(function () {
        return self.skipWaiting();
      })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys()
      .then(function (keys) {
        return Promise.all(keys.filter(function (key) {
          return key !== CACHE;
        }).map(function (key) {
          return caches.delete(key);
        }));
      })
      .then(function () {
        return self.clients.claim();
      })
  );
});

self.addEventListener("fetch", function (event) {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  event.respondWith(
    fetch(request).catch(function () {
      return caches.match(request).then(function (hit) {
        return hit || Response.error();
      });
    })
  );
});
