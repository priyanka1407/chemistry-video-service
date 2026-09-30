/* Northbridge Bank - shared front-end helpers (vanilla JS, no framework). */
const NB = (() => {
  const NAV = [
    { href: '/dashboard.html', label: 'Dashboard', perm: 'ACCOUNTS_VIEW' },
    { href: '/payments.html', label: 'New payment', perm: 'PAYMENTS_CREATE' },
    { href: '/approvals.html', label: 'Approvals', perm: 'PAYMENTS_APPROVE' },
    { href: '/card-auth.html', label: 'Card authorization', perm: 'CARDS_AUTHORIZE' },
    { href: '/settlements.html', label: 'Settlements', perm: 'SETTLEMENTS_VIEW' },
    { href: '/admin.html', label: 'Administration', perm: 'ADMIN_USERS' },
  ];

  async function api(path, opts = {}) {
    const init = { method: opts.method || 'GET', headers: { 'Accept': 'application/json', ...(opts.headers || {}) } };
    if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.headers['Content-Type'] = 'application/json'; }
    const res = await fetch(path, init);
    let data = null;
    try { data = await res.json(); } catch (e) { /* empty body */ }
    return { status: res.status, ok: res.ok, data };
  }

  function fmtMoney(amount, currency) {
    return new Intl.NumberFormat('en-GB', { style: 'currency', currency: currency || 'GBP' }).format(Number(amount));
  }

  function badge(status) {
    const s = document.createElement('span');
    s.className = 'badge ' + status;
    s.textContent = status.replaceAll('_', ' ');
    s.setAttribute('data-testid', 'status-badge');
    return s;
  }

  function toast(msg) {
    const t = document.createElement('div');
    t.className = 'toast'; t.setAttribute('role', 'status'); t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 4000);
  }

  function renderShell(me) {
    const header = document.createElement('header');
    header.className = 'topbar';
    header.innerHTML = `<span class="logo">Northbridge Bank · Corporate</span>
      <span><span data-testid="current-user">${me.displayName} (${me.role})</span>
      <button type="button" class="secondary" id="signout">Sign out</button></span>`;
    const nav = document.createElement('nav');
    nav.className = 'main-nav'; nav.setAttribute('aria-label', 'Main');
    NAV.filter(n => me.permissions.includes(n.perm)).forEach(n => {
      const a = document.createElement('a');
      a.href = n.href; a.textContent = n.label;
      if (location.pathname === n.href) a.setAttribute('aria-current', 'page');
      nav.appendChild(a);
    });
    document.body.prepend(nav);
    document.body.prepend(header);
    document.getElementById('signout').addEventListener('click', async () => {
      await api('/api/auth/logout', { method: 'POST' });
      location.href = '/login.html?reason=signedout';
    });
  }

  /* Client-side idle timeout driven by setTimeout -> testable with Playwright's page.clock(). */
  function startIdleTimer(me) {
    const idleMs = me.sessionIdleTimeoutSeconds * 1000;
    let warnTimer, logoutTimer, dialog;
    const reset = () => {
      clearTimeout(warnTimer); clearTimeout(logoutTimer);
      if (dialog) { dialog.remove(); dialog = null; }
      warnTimer = setTimeout(showWarning, idleMs - 60000);
      logoutTimer = setTimeout(expire, idleMs);
    };
    const showWarning = () => {
      dialog = document.createElement('div');
      dialog.className = 'modal-backdrop';
      dialog.innerHTML = `<div class="modal" role="alertdialog" aria-modal="true" aria-labelledby="idle-title">
        <h2 id="idle-title">Your session is about to expire</h2>
        <p>For your security you will be signed out in 60 seconds.</p>
        <button type="button" id="stay-signed-in">Stay signed in</button></div>`;
      document.body.appendChild(dialog);
      dialog.querySelector('#stay-signed-in').addEventListener('click', (e) => { e.stopPropagation(); reset(); });
    };
    const expire = async () => {
      await api('/api/auth/logout', { method: 'POST' });
      location.href = '/login.html?reason=timeout';
    };
    ['click', 'keydown'].forEach(evt => document.addEventListener(evt, () => { if (!dialog) reset(); }, true));
    reset();
  }

  async function requireSession(permission) {
    const res = await api('/api/me');
    if (res.status === 401) { location.href = '/login.html?reason=' + ((res.data && res.data.error && res.data.error.code) || 'unauthenticated'); return null; }
    const me = res.data;
    renderShell(me);
    startIdleTimer(me);
    if (permission && !me.permissions.includes(permission)) {
      document.querySelector('main').innerHTML =
        `<section class="card" role="alert" data-testid="access-denied"><h1>Access denied</h1>
         <p>Your role <strong>${me.role}</strong> does not have permission to view this page.</p></section>`;
      return null;
    }
    return me;
  }

  function showFieldErrors(form, fields) {
    form.querySelectorAll('.field-error').forEach(e => { e.textContent = ''; });
    form.querySelectorAll('[aria-invalid]').forEach(e => e.removeAttribute('aria-invalid'));
    (fields || []).forEach(f => {
      const el = form.querySelector(`[name="${f.field}"]`);
      const holder = form.querySelector(`#${f.field}-error`);
      if (el) el.setAttribute('aria-invalid', 'true');
      if (holder) holder.textContent = f.message;
    });
  }

  // Back/forward cache: a page restored after sign-out must re-validate the session, otherwise
  // the Back button shows protected data from memory (found by AuthenticationAndSessionTest).
  window.addEventListener('pageshow', (e) => { if (e.persisted) location.reload(); });

  return { api, fmtMoney, badge, toast, requireSession, showFieldErrors };
})();
