/* Navegación accesible y contador privado; sin contenido de tareas en storage. */
(() => {
  'use strict';
  // Añadir error junto al campo y su asociación accesible en formularios existentes.
  document.querySelectorAll('.alert a[href^="#"]').forEach(link => {
    const field = document.getElementById(link.getAttribute('href').slice(1));
    if (!field || !field.matches('input,select,textarea')) return;
    const id = `error-${field.id}`;
    if (!document.getElementById(id)) {
      const message = document.createElement('p');
      message.id = id; message.className = 'field-error'; message.textContent = link.textContent;
      field.insertAdjacentElement('afterend', message);
    }
    field.setAttribute('aria-invalid', 'true');
    field.setAttribute('aria-describedby', id);
  });
  document.querySelectorAll('.ai-analyze-form').forEach(form => {
    window.addEventListener('pageshow', () => {
      delete form.dataset.submitting;
      form.querySelector('button').disabled = false;
      form.querySelector('.ai-loading').hidden = true;
    });
    form.addEventListener('submit', event => {
      if (form.dataset.submitting) { event.preventDefault(); return; }
      form.dataset.submitting = 'true';
      form.querySelector('button').disabled = true;
      form.querySelector('.ai-loading').hidden = false;
    });
  });
  const nav = document.getElementById('workspace-navigation');
  if (!nav) return;
  document.body.classList.add('workspace-enhanced');
  const mobile = document.querySelector('.mobile-menu-toggle');
  const sidebar = document.getElementById('workspace-sidebar');
  const backdrop = document.getElementById('sidebar-backdrop');
  const narrow = window.matchMedia('(max-width: 767px)');
  const triggers = [...nav.querySelectorAll('.nav-trigger')];
  const setGroup = (button, open) => {
    button.setAttribute('aria-expanded', String(open));
    document.getElementById(button.getAttribute('aria-controls')).hidden = !open;
  };
  const setMenu = open => {
    sidebar.hidden = narrow.matches && !open;
    backdrop.hidden = !narrow.matches || !open;
    mobile.setAttribute('aria-expanded', String(open));
    document.body.classList.toggle('menu-open', narrow.matches && open);
    document.getElementById('main-content').inert = narrow.matches && open;
  };
  const responsive = () => { mobile.hidden = !narrow.matches; setMenu(false); };
  responsive();
  narrow.addEventListener('change', responsive);
  mobile.addEventListener('click', () => {
    const open = mobile.getAttribute('aria-expanded') !== 'true';
    setMenu(open);
    if (open) sidebar.querySelector('a').focus();
  });
  backdrop.addEventListener('click', () => { setMenu(false); mobile.focus(); });
  triggers.forEach(button => button.addEventListener('click', () => setGroup(button, button.getAttribute('aria-expanded') !== 'true')));
  document.addEventListener('keydown', event => {
    if (!narrow.matches || sidebar.hidden) return;
    if (event.key === 'Escape') { setMenu(false); mobile.focus(); }
    if (event.key === 'Tab') {
      const focusable = [...sidebar.querySelectorAll('a,button')].filter(el => el.getClientRects().length);
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
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
