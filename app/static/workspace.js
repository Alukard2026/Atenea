/* Navegación accesible y contador privado; sin contenido de tareas en storage. */
(() => {
  'use strict';
  const nav = document.getElementById('workspace-navigation');
  if (!nav) return;
  const mobile = document.querySelector('.mobile-menu-toggle');
  const narrow = window.matchMedia('(max-width: 1100px)');
  const triggers = [...nav.querySelectorAll('.nav-trigger')];
  const setGroup = (button, open) => {
    button.setAttribute('aria-expanded', String(open));
    document.getElementById(button.getAttribute('aria-controls')).hidden = !open;
  };
  const closeGroups = () => triggers.forEach(button => setGroup(button, false));
  nav.classList.add('enhanced');
  closeGroups();
  const responsive = () => {
    mobile.hidden = !narrow.matches;
    mobile.setAttribute('aria-expanded', 'false');
    nav.hidden = narrow.matches;
    closeGroups();
  };
  responsive();
  narrow.addEventListener('change', responsive);
  mobile.addEventListener('click', () => {
    const open = mobile.getAttribute('aria-expanded') !== 'true';
    mobile.setAttribute('aria-expanded', String(open));
    nav.hidden = !open;
  });
  triggers.forEach(button => button.addEventListener('click', () => {
    const open = button.getAttribute('aria-expanded') !== 'true';
    closeGroups();
    setGroup(button, open);
  }));
  document.addEventListener('click', event => {
    if (!nav.contains(event.target)) closeGroups();
  });
  nav.addEventListener('focusout', event => {
    if (!nav.contains(event.relatedTarget)) closeGroups();
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    const open = triggers.find(button => button.getAttribute('aria-expanded') === 'true');
    if (open) { closeGroups(); open.focus(); }
    else if (narrow.matches && !nav.hidden) { nav.hidden = true; mobile.setAttribute('aria-expanded', 'false'); mobile.focus(); }
  });

  const link = document.getElementById('notification-link');
  const badge = document.getElementById('notification-count');
  const label = document.getElementById('notification-count-label');
  const banner = document.getElementById('notification-banner');
  const key = `atenea:reminder-notice:${link.dataset.notificationScope}`;
  let shown = false;
  try { shown = sessionStorage.getItem(key) === 'shown'; } catch (_) { /* Storage opcional. */ }
  const remember = () => {
    shown = true;
    try { sessionStorage.setItem(key, 'shown'); } catch (_) { /* Sin persistencia. */ }
  };
  document.getElementById('dismiss-notification').addEventListener('click', () => { banner.hidden = true; remember(); });
  let stopped = false;
  async function refresh() {
    if (stopped || document.hidden) return;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch('/notifications/status', {credentials: 'same-origin', cache: 'no-store', signal: controller.signal});
      if (response.redirected || response.status === 401 || response.status === 403) {
        stopped = true; badge.hidden = true; banner.hidden = true; label.textContent = ''; return;
      }
      if (!response.ok) return;
      const data = await response.json();
      if (!Number.isSafeInteger(data.pending) || data.pending < 0) return;
      badge.textContent = data.pending > 9 ? '9+' : String(data.pending);
      badge.hidden = data.pending === 0;
      label.textContent = data.pending ? `: ${data.pending} recordatorios requieren atención` : ': sin recordatorios que requieran atención';
      if (!data.pending) {
        banner.hidden = true;
        shown = false;
        try { sessionStorage.removeItem(key); } catch (_) { /* Storage opcional. */ }
      } else if (!shown) {
        banner.hidden = window.location.pathname === '/notifications';
        remember();
      }
    } catch (_) { /* Un fallo temporal no interrumpe la página ni muestra datos internos. */ }
    finally { window.clearTimeout(timeout); }
  }
  async function poll() {
    await refresh();
    if (!stopped) window.setTimeout(poll, 60000);
  }
  poll();
})();
