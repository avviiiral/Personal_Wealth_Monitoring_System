self.addEventListener('push', (event) => {
  let payload = {};

  try {
    payload = event.data ? event.data.json() : {};
  } catch (error) {
    payload = {
      title: 'PWMS Portfolio Alert',
      body: event.data ? event.data.text() : 'A new portfolio alert is available.',
    };
  }

  const title = payload.title || 'PWMS Portfolio Alert';
  const options = {
    body: payload.body || 'A new portfolio alert is available.',
    tag: payload.tag || 'pwms-portfolio-alert',
    data: {
      url: payload.url || '/portfolio-news',
      alert_id: payload.alert_id,
    },
    icon: '/favicon.ico',
    badge: '/favicon.ico',
  };

  event.waitUntil(
    self.registration.showNotification(title, options)
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();

  const targetUrl = event.notification.data?.url || '/portfolio-news';

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if ('focus' in client) {
          client.navigate(targetUrl);
          return client.focus();
        }
      }

      if (clients.openWindow) {
        return clients.openWindow(targetUrl);
      }

      return undefined;
    })
  );
});
