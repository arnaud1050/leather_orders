// Service worker for a catalog-mode kiosk link (showcase SC31). Served from
// /k/<token>/sw.js, so its scope is that link and nothing else in the app.
//
// - The page: network first, so a piece published at the bench shows up on
//   the next load; the saved copy when the network is gone. A 404 means the
//   link was deleted or the feature switched off: the saved copy is thrown
//   away rather than served, so a lost tablet stops showing the catalog the
//   next time it's online.
// - Everything else (photos, logo, script, styles, fonts): saved copy first.
//   A photo's URL never changes what it shows, so a saved one is never stale;
//   the rest is refreshed in the background, so an update lands one load later.
// - The page tells the worker which photos it lists ("cache" message); the
//   worker fetches the missing ones and drops photos no longer listed, so
//   the device holds the current catalog and no more.
var CACHE = 'showcase-catalog-v1';

self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (event) {
  event.waitUntil(caches.keys().then(function (names) {
    return Promise.all(names.filter(function (n) { return n !== CACHE; })
      .map(function (n) { return caches.delete(n); }));
  }).then(function () { return self.clients.claim(); }));
});

self.addEventListener('message', function (event) {
  if (event.data && event.data.type === 'cache' && Array.isArray(event.data.urls)) {
    event.waitUntil(keep(event.data.urls));
  }
});

function keep(urls) {
  var wanted = new Set(urls.map(function (u) { return new URL(u, self.location.href).href; }));
  return caches.open(CACHE).then(function (cache) {
    return cache.keys().then(function (requests) {
      var drops = requests.filter(function (r) {
        return r.url.indexOf('/photos/') !== -1 && !wanted.has(r.url);
      }).map(function (r) { return cache.delete(r); });
      var fills = Array.from(wanted).map(function (url) {
        return cache.match(url).then(function (hit) {
          if (hit) return null;
          return fetch(url).then(function (response) {
            if (response.ok) return cache.put(url, response);
          }).catch(function () {});
        });
      });
      return Promise.all(drops.concat(fills));
    });
  });
}

function isPage(request) {
  return request.mode === 'navigate';
}

self.addEventListener('fetch', function (event) {
  var request = event.request;
  if (request.method !== 'GET') return;

  if (isPage(request)) {
    event.respondWith(fetch(request).then(function (response) {
      if (response.status === 404) {
        return caches.delete(CACHE).then(function () { return response; });
      }
      if (response.ok) {
        var copy = response.clone();
        caches.open(CACHE).then(function (cache) { cache.put(request, copy); });
      }
      return response;
    }).catch(function () {
      return caches.open(CACHE).then(function (cache) {
        return cache.match(request, { ignoreSearch: true });
      }).then(function (hit) {
        return hit || new Response('Offline, and this catalog was never saved on this device.',
          { status: 503, headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
      });
    }));
    return;
  }

  var isPhoto = request.url.indexOf('/photos/') !== -1;
  event.respondWith(caches.open(CACHE).then(function (cache) {
    return cache.match(request).then(function (hit) {
      var fresh = fetch(request).then(function (response) {
        if (response.ok || response.type === 'opaque') cache.put(request, response.clone());
        return response;
      });
      if (!hit) return fresh;
      // Photos never change behind their URL; scripts, styles and the logo
      // can, so those are refreshed in the background for next time.
      if (!isPhoto) event.waitUntil(fresh.catch(function () {}));
      return hit;
    });
  }));
});
