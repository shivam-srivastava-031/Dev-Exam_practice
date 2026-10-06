// Errors from the visitor's browser go to the server (/api/client-errors), so a problem seen on
// someone's screen can be diagnosed without access to their browser's console.

const build = () => document.querySelector<HTMLScriptElement>('script[type="module"][src]')?.src.split('/').pop() ?? '';

export function reportError(error: unknown, component?: string) {
  const e = error instanceof Error ? error : new Error(String(error));
  if (e.name === 'AbortError') return;
  try {
    void fetch('/api/client-errors', {
      method: 'POST',
      keepalive: true,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: `${e.name}: ${e.message}`.slice(0, 500),
        stack: (e.stack ?? '').slice(0, 2000),
        component: (component ?? '').slice(0, 1500),
        url: location.pathname + location.search,
        ua: navigator.userAgent,
        build: build(),
      }),
    }).catch(() => {});
  } catch {
    /* reporting must never cause an error of its own */
  }
}

/** Errors outside React's rendering: event handlers, timers, failed promises. */
export function reportUncaught() {
  window.addEventListener('error', (e) => {
    if (e.error) reportError(e.error);
  });
  window.addEventListener('unhandledrejection', (e) => reportError(e.reason));
}
