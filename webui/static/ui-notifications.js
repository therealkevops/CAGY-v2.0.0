/**
 * ui-notifications.js — Centralized Toast, Banner, Dialog, and Chime System.
 *
 * Provides:
 *   - showToast(msg, ms, type), dismissToast(btnOrEl)
 *   - showBanner(msg, type, options), dismissBanner(el)
 *   - showConfirmDialog(opts), showPromptDialog(opts), showAlertDialog(msg, title)
 *   - playNotificationSound(), playAttentionSound(key)
 *
 * Loaded early in boot sequence (see index.html).
 */

const TOAST_DEFAULT_MS = 2800;
const TOAST_ERROR_DEFAULT_MS = 20000;

function clearToastDismissTimer(el) {
  if (!el) return;
  clearTimeout(el._t);
  el._t = null;
}

function setToastDismissTimer(el, duration) {
  if (!el) return;
  clearToastDismissTimer(el);
  el._t = setTimeout(() => {
    el.classList.remove('show');
  }, duration);
}

function dismissToast(btnOrEl) {
  const el = btnOrEl && btnOrEl.closest
    ? btnOrEl.closest('#toast')
    : (btnOrEl && btnOrEl.id === 'toast' ? btnOrEl : null);
  if (!el) return;
  clearToastDismissTimer(el);
  el.classList.remove('show');
}

function _fallbackCopyNotification(text) {
  return new Promise((resolve, reject) => {
    if (typeof document === 'undefined') return resolve();
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;left:0;top:0;width:2em;height:2em;padding:0;border:none;outline:none;box-shadow:none;background:transparent;z-index:-1';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try {
      document.execCommand('copy');
      resolve();
    } catch (e) {
      reject(e);
    } finally {
      document.body.removeChild(ta);
    }
  });
}

function _copyNotificationText(text) {
  if (typeof navigator !== 'undefined' && navigator.clipboard && typeof window !== 'undefined' && window.isSecureContext) {
    return navigator.clipboard.writeText(text).catch(() => _fallbackCopyNotification(text));
  }
  return _fallbackCopyNotification(text);
}

function copyToastText(btn) {
  const el = btn && btn.closest ? btn.closest('#toast') : null;
  const text = el ? (el.dataset.toastMessage || el.textContent || '') : '';
  const done = () => {
    const old = btn.textContent;
    btn.textContent = 'Copied';
    setTimeout(() => {
      btn.textContent = old;
    }, 1200);
  };
  _copyNotificationText(text).then(done).catch(() => {});
}

function _escNotification(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function showToast(msg, ms, type) {
  if (typeof document === 'undefined') return;
  const el = (typeof $ === 'function' ? $('toast') : document.getElementById('toast'));
  if (!el) return;
  const s = String(msg == null ? '' : msg);
  let t = type;
  if (!t) {
    const low = s.toLowerCase();
    if (/fail|error|denied|invalid|unavailable|no active|no workspace match|no model match|no personalities/.test(low)) {
      t = 'error';
    } else if (/warn|queued|takes effect|skipped|fallback/.test(low)) {
      t = 'warning';
    } else if (/saved|created|imported|restored|switched|set to|updated|duplicated|moved to|renamed|deleted|complete|pinned|archived|cleared|stopped/.test(low)) {
      t = 'success';
    } else {
      t = 'info';
    }
  }
  const duration = ms == null ? (t === 'error' ? TOAST_ERROR_DEFAULT_MS : TOAST_DEFAULT_MS) : ms;
  el.className = 'toast show ' + t;
  el.dataset.toastMessage = s;
  if (t === 'error') {
    el.innerHTML = `<span class="toast-message">${_escNotification(s)}</span><button class="toast-copy" type="button" data-toast-copy="1" onclick="copyToastText(this);event.stopPropagation()">Copy</button><button class="toast-dismiss" type="button" aria-label="Dismiss error toast" data-toast-dismiss="1" onclick="dismissToast(this);event.stopPropagation()">Dismiss</button>`;
  } else {
    el.textContent = s;
  }
  el.onmouseenter = () => clearToastDismissTimer(el);
  el.onmouseleave = () => setToastDismissTimer(el, duration);
  el.onfocusin = () => clearToastDismissTimer(el);
  el.onfocusout = () => setToastDismissTimer(el, duration);
  el.onclick = t === 'error' ? null : () => dismissToast(el);
  setToastDismissTimer(el, duration);
}

// ── Shared Banner System ─────────────────────────────────────────────────────
function showBanner(msg, type = 'info', options = {}) {
  if (typeof document === 'undefined') return null;
  const container = document.getElementById('bannerContainer') || document.body;
  let banner = document.getElementById(options.id || 'appGlobalBanner');
  if (!banner) {
    banner = document.createElement('div');
    if (options.id) banner.id = options.id;
    banner.className = `app-banner ${type}`;
    container.prepend(banner);
  }
  banner.className = `app-banner ${type} show`;
  banner.innerHTML = `
    <span class="app-banner-msg">${_escNotification(msg)}</span>
    ${options.dismissible !== false ? '<button class="app-banner-close" type="button" onclick="dismissBanner(this)">&times;</button>' : ''}
  `;
  if (options.duration) {
    setTimeout(() => dismissBanner(banner), options.duration);
  }
  return banner;
}

function dismissBanner(btnOrEl) {
  const el = btnOrEl && btnOrEl.closest ? btnOrEl.closest('.app-banner') : btnOrEl;
  if (!el) return;
  el.classList.remove('show');
  if (el.dataset.ephemeral) {
    setTimeout(() => el.remove(), 200);
  }
}

// ── Shared Modal Dialogs (Promise-based) ──────────────────────────────────────
const APP_DIALOG = { resolve: null, kind: null, lastFocus: null };
let _appDialogBound = false;

function _isAppDialogOpen() {
  const overlay = (typeof $ === 'function' ? $('appDialogOverlay') : document.getElementById('appDialogOverlay'));
  return !!(overlay && overlay.style.display !== 'none');
}

function _getAppDialogFocusable() {
  const get = id => (typeof $ === 'function' ? $(id) : document.getElementById(id));
  return [get('appDialogInput'), get('appDialogCancel'), get('appDialogConfirm'), get('appDialogClose')]
    .filter(el => el && el.style.display !== 'none' && !el.disabled);
}

function _finishAppDialog(result, restoreFocus = true) {
  const get = id => (typeof $ === 'function' ? $(id) : document.getElementById(id));
  const overlay = get('appDialogOverlay');
  const dialog = get('appDialog');
  const input = get('appDialogInput');
  const confirmBtn = get('appDialogConfirm');
  const resolve = APP_DIALOG.resolve;
  const lastFocus = APP_DIALOG.lastFocus;

  APP_DIALOG.resolve = null;
  APP_DIALOG.kind = null;
  APP_DIALOG.lastFocus = null;

  if (overlay) {
    overlay.style.display = 'none';
    overlay.setAttribute('aria-hidden', 'true');
  }
  if (dialog) dialog.setAttribute('role', 'dialog');
  if (input) {
    input.value = '';
    input.style.display = 'none';
    input.placeholder = '';
  }
  if (confirmBtn) {
    confirmBtn.classList.remove('danger');
    confirmBtn.textContent = (typeof t === 'function' ? t('dialog_confirm_btn') : 'Confirm');
  }
  if (restoreFocus && lastFocus && typeof lastFocus.focus === 'function') {
    setTimeout(() => lastFocus.focus(), 0);
  }
  if (resolve) resolve(result);
}

function _ensureAppDialogBindings() {
  if (_appDialogBound || typeof document === 'undefined') return;
  _appDialogBound = true;
  const get = id => (typeof $ === 'function' ? $(id) : document.getElementById(id));
  const overlay = get('appDialogOverlay');
  const cancelBtn = get('appDialogCancel');
  const confirmBtn = get('appDialogConfirm');
  const closeBtn = get('appDialogClose');

  if (overlay) {
    overlay.addEventListener('click', e => {
      if (e.target === overlay) _finishAppDialog(APP_DIALOG.kind === 'prompt' ? null : false);
    });
  }
  if (cancelBtn) cancelBtn.addEventListener('click', () => _finishAppDialog(APP_DIALOG.kind === 'prompt' ? null : false));
  if (closeBtn) closeBtn.addEventListener('click', () => _finishAppDialog(APP_DIALOG.kind === 'prompt' ? null : false));
  if (confirmBtn) {
    confirmBtn.addEventListener('click', () => {
      if (APP_DIALOG.kind === 'prompt') {
        const input = get('appDialogInput');
        _finishAppDialog(input ? input.value : null);
      } else {
        _finishAppDialog(true);
      }
    });
  }
  document.addEventListener('keydown', e => {
    if (!_isAppDialogOpen()) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      _finishAppDialog(APP_DIALOG.kind === 'prompt' ? null : false);
      return;
    }
    if (e.key === 'Enter') {
      if (window._isImeEnter && window._isImeEnter(e)) return;
      const target = e.target;
      const isTextarea = target && target.tagName === 'TEXTAREA';
      if (!isTextarea) {
        e.preventDefault();
        if (target === cancelBtn || target === closeBtn) {
          _finishAppDialog(APP_DIALOG.kind === 'prompt' ? null : false);
        } else if (APP_DIALOG.kind === 'prompt') {
          const input = get('appDialogInput');
          _finishAppDialog(input ? input.value : null);
        } else {
          _finishAppDialog(true);
        }
      }
      return;
    }
    if (e.key === 'Tab') {
      const nodes = _getAppDialogFocusable();
      if (!nodes.length) return;
      const idx = nodes.indexOf(document.activeElement);
      let nextIdx = idx;
      if (e.shiftKey) {
        nextIdx = idx <= 0 ? nodes.length - 1 : idx - 1;
      } else {
        nextIdx = idx === -1 || idx === nodes.length - 1 ? 0 : idx + 1;
      }
      e.preventDefault();
      nodes[nextIdx].focus();
    }
  }, true);
}

function showConfirmDialog(opts = {}) {
  _ensureAppDialogBindings();
  if (APP_DIALOG.resolve) _finishAppDialog(false, false);
  const get = id => (typeof $ === 'function' ? $(id) : document.getElementById(id));
  const overlay = get('appDialogOverlay');
  const dialog = get('appDialog');
  const title = get('appDialogTitle');
  const desc = get('appDialogDesc');
  const input = get('appDialogInput');
  const cancelBtn = get('appDialogCancel');
  const confirmBtn = get('appDialogConfirm');

  APP_DIALOG.resolve = null;
  APP_DIALOG.kind = 'confirm';
  APP_DIALOG.lastFocus = typeof document !== 'undefined' ? document.activeElement : null;

  const tr = (k, fb) => (typeof t === 'function' ? t(k) : fb);

  if (title) title.textContent = opts.title || tr('dialog_confirm_title', 'Confirm');
  if (desc) desc.textContent = opts.message || '';
  if (input) {
    input.style.display = 'none';
    input.value = '';
  }
  if (cancelBtn) {
    if (opts.hideCancel) {
      cancelBtn.style.display = 'none';
    } else {
      cancelBtn.style.display = '';
      cancelBtn.textContent = opts.cancelLabel || tr('cancel', 'Cancel');
    }
  }
  if (confirmBtn) {
    confirmBtn.textContent = opts.confirmLabel || tr('dialog_confirm_btn', 'Confirm');
    confirmBtn.classList.toggle('danger', !!opts.danger);
  }
  if (dialog) dialog.setAttribute('role', opts.danger ? 'alertdialog' : 'dialog');
  if (overlay) {
    overlay.style.display = 'flex';
    overlay.setAttribute('aria-hidden', 'false');
  }
  return new Promise(resolve => {
    APP_DIALOG.resolve = resolve;
    setTimeout(() => ((opts.focusCancel ? cancelBtn : confirmBtn) || confirmBtn || cancelBtn).focus(), 0);
  });
}

function showPromptDialog(opts = {}) {
  _ensureAppDialogBindings();
  if (APP_DIALOG.resolve) _finishAppDialog(null, false);
  const get = id => (typeof $ === 'function' ? $(id) : document.getElementById(id));
  const overlay = get('appDialogOverlay');
  const dialog = get('appDialog');
  const title = get('appDialogTitle');
  const desc = get('appDialogDesc');
  const input = get('appDialogInput');
  const cancelBtn = get('appDialogCancel');
  const confirmBtn = get('appDialogConfirm');

  APP_DIALOG.resolve = null;
  APP_DIALOG.kind = 'prompt';
  APP_DIALOG.lastFocus = typeof document !== 'undefined' ? document.activeElement : null;

  const tr = (k, fb) => (typeof t === 'function' ? t(k) : fb);

  if (title) title.textContent = opts.title || tr('dialog_prompt_title', 'Input');
  if (desc) desc.textContent = opts.message || '';
  if (input) {
    input.type = opts.inputType || 'text';
    input.style.display = '';
    const prefill = opts.value != null ? opts.value : (opts.defaultValue != null ? opts.defaultValue : '');
    input.value = prefill;
    input.placeholder = opts.placeholder || '';
    input.autocomplete = 'off';
    input.spellcheck = false;
  }
  if (cancelBtn) {
    cancelBtn.style.display = '';
    cancelBtn.textContent = opts.cancelLabel || tr('cancel', 'Cancel');
  }
  if (confirmBtn) {
    confirmBtn.textContent = opts.confirmLabel || tr('create', 'OK');
    confirmBtn.classList.toggle('danger', !!opts.danger);
  }
  if (dialog) dialog.setAttribute('role', opts.danger ? 'alertdialog' : 'dialog');
  if (overlay) {
    overlay.style.display = 'flex';
    overlay.setAttribute('aria-hidden', 'false');
  }
  return new Promise(resolve => {
    APP_DIALOG.resolve = resolve;
    setTimeout(() => {
      if (input && input.style.display !== 'none') {
        input.focus();
        const v = input.value || '';
        if (opts.selectStem && v) {
          const dot = v.lastIndexOf('.');
          if (dot > 0) input.setSelectionRange(0, dot);
          else input.select();
        } else if (opts.selectAll && v) {
          input.select();
        }
      } else if (confirmBtn) {
        confirmBtn.focus();
      }
    }, 0);
  });
}

function showAlertDialog(message, title = 'Alert') {
  return showConfirmDialog({
    title,
    message,
    hideCancel: true,
    confirmLabel: typeof t === 'function' ? t('ok') : 'OK',
  });
}

// ── Audio Chime Helpers ──────────────────────────────────────────────────────
function playNotificationSound() {
  if (typeof window === 'undefined' || !window._soundEnabled) return;
  try {
    const C = window.AudioContext || window.webkitAudioContext;
    if (!C) return;
    const ctx = new C();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.type = 'sine';
    osc.frequency.setValueAtTime(660, ctx.currentTime);
    osc.frequency.setValueAtTime(880, ctx.currentTime + 0.1);
    gain.gain.setValueAtTime(0.3, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.3);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.3);
    osc.onended = () => ctx.close();
  } catch (e) {
    console.warn('Notification sound failed:', e);
  }
}

function playAttentionSound(key) {
  if (typeof window === 'undefined' || !window._soundEnabled) return;
  const nowMs = Date.now();
  if (window._lastAttentionSoundAt && nowMs - window._lastAttentionSoundAt < 900) return;
  const dedupeKey = key ? String(key) : '';
  if (dedupeKey) {
    const seen = window._attentionSoundSeenKeys instanceof Map ? window._attentionSoundSeenKeys : new Map();
    window._attentionSoundSeenKeys = seen;
    for (const [seenKey, seenAt] of seen) {
      if (nowMs - Number(seenAt || 0) > 300000) seen.delete(seenKey);
    }
    if (seen.has(dedupeKey)) return;
    seen.set(dedupeKey, nowMs);
  }
  window._lastAttentionSoundAt = nowMs;
  try {
    const C = window.AudioContext || window.webkitAudioContext;
    if (!C) return;
    const ctx = new C();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.type = 'sine';
    osc.frequency.setValueAtTime(880, ctx.currentTime);
    osc.frequency.setValueAtTime(660, ctx.currentTime + 0.075);
    gain.gain.setValueAtTime(0.24, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.24);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.24);
    osc.onended = () => ctx.close();
  } catch (e) {
    console.warn('Attention sound failed:', e);
  }
}

// ── Global registration ──────────────────────────────────────────────────────
if (typeof window !== 'undefined') {
  window.TOAST_DEFAULT_MS = TOAST_DEFAULT_MS;
  window.TOAST_ERROR_DEFAULT_MS = TOAST_ERROR_DEFAULT_MS;
  window.clearToastDismissTimer = clearToastDismissTimer;
  window.setToastDismissTimer = setToastDismissTimer;
  window.dismissToast = dismissToast;
  window.copyToastText = copyToastText;
  window.showToast = showToast;
  window.showBanner = showBanner;
  window.dismissBanner = dismissBanner;
  window.showConfirmDialog = showConfirmDialog;
  window.showPromptDialog = showPromptDialog;
  window.showAlertDialog = showAlertDialog;
  window.playNotificationSound = playNotificationSound;
  window.playAttentionSound = playAttentionSound;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    TOAST_DEFAULT_MS,
    TOAST_ERROR_DEFAULT_MS,
    clearToastDismissTimer,
    setToastDismissTimer,
    dismissToast,
    copyToastText,
    showToast,
    showBanner,
    dismissBanner,
    showConfirmDialog,
    showPromptDialog,
    showAlertDialog,
    playNotificationSound,
    playAttentionSound,
  };
}
