"""Offline, opaque-origin MCP app proxy; no authenticated state lives here."""

PATH = "/mcp-apps/frame"
CSP = "; ".join(
    [
        "default-src 'none'",
        "script-src 'unsafe-inline'",
        "style-src 'unsafe-inline'",
        "img-src data: blob:",
        "font-src data:",
        "media-src data: blob:",
        "connect-src 'none'",
        "worker-src 'none'",
        "frame-src 'none'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'self'",
        "sandbox allow-scripts allow-forms",
    ]
)

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Isolated app</title><style>html,body,iframe{margin:0;width:100%;height:100%;border:0}</style>
</head><body><script>
(() => {
  const channel = location.hash.slice(1), host = location.origin;
  if (!/^[a-f0-9-]{36}$/.test(channel)) return;
  let view = null, count = 0, since = performance.now();
  const send = (message) => parent.postMessage({channel, message}, host);
  addEventListener('message', (event) => {
    if (event.source === parent && event.origin === host) {
      const data = event.data;
      if (!data || data.channel !== channel) return;
      const msg = data.message;
      if (!msg || msg.jsonrpc !== '2.0') return;
      if (msg.method === 'ui/notifications/sandbox-resource-ready') {
        if (view || typeof msg.params?.html !== 'string' || msg.params.html.length > 196608) return;
        view = document.createElement('iframe');
        view.title = 'App content';
        view.setAttribute('sandbox', 'allow-scripts allow-forms');
        view.setAttribute('allow', "camera 'none'; microphone 'none'; " +
          "geolocation 'none'; clipboard-write 'none'");
        view.srcdoc = msg.params.html;
        document.body.append(view);
      } else if (!String(msg.method || '').startsWith('ui/notifications/sandbox-')) {
        view?.contentWindow?.postMessage(msg, '*');
      }
    } else if (view && event.source === view.contentWindow && event.origin === 'null') {
      const msg = event.data;
      if (!msg || msg.jsonrpc !== '2.0' ||
          String(msg.method || '').startsWith('ui/notifications/sandbox-')) return;
      if (performance.now() - since > 1000) { count = 0; since = performance.now(); }
      if (++count > 40) return;
      try { if (JSON.stringify(msg).length > 24576) return; } catch { return; }
      send(msg);
    }
  });
  send({jsonrpc:'2.0', method:'ui/notifications/sandbox-proxy-ready'});
})();
</script></body></html>"""
