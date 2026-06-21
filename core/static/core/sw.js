// ClubHangar Service Worker — push notifications + image caching

var IMAGE_CACHE = 'ch-images-v1';

// Cache images (media uploads + static img) on first fetch, serve from cache thereafter.
// Stale-while-revalidate: return cached copy immediately, update in background.
self.addEventListener('fetch', function(event) {
  var url = event.request.url;
  if (event.request.method !== 'GET') return;
  if (event.request.destination !== 'image') return;
  // Only cache our own images — media uploads and static img files
  if (url.indexOf('/media/') === -1 && url.indexOf('/static/core/img/') === -1) return;

  event.respondWith(
    caches.open(IMAGE_CACHE).then(function(cache) {
      return cache.match(event.request).then(function(cached) {
        var networkFetch = fetch(event.request).then(function(resp) {
          if (resp.ok) cache.put(event.request, resp.clone());
          return resp;
        }).catch(function() { return cached; });
        return cached || networkFetch;
      });
    })
  );
});

self.addEventListener('push', function(event) {
  if (!event.data) return;
  var data = {};
  try { data = event.data.json(); } catch(e) { data = { title: 'ClubHangar', body: event.data.text() }; }

  var title   = data.title || 'ClubHangar';
  var options = {
    body:    data.body  || '',
    icon:    data.icon  || '/static/core/img/icon-192.png',
    badge:   '/static/core/img/icon-96.png',
    data:    { url: data.url || '/' },
    vibrate: [200, 100, 200],
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', function(event) {
  event.notification.close();
  var url = (event.notification.data && event.notification.data.url) ? event.notification.data.url : '/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function(windowClients) {
      for (var i = 0; i < windowClients.length; i++) {
        var client = windowClients[i];
        if (client.url === url && 'focus' in client) {
          return client.focus();
        }
      }
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});
