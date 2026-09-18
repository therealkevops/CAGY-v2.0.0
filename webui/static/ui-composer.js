/**
 * ui-composer.js — Centralized Chat Composer, Keybindings, and Primary Action Handler.
 *
 * Provides:
 *   - updateSendBtn(), getComposerPrimaryAction(), handleComposerPrimaryAction()
 *   - setBusy(v), setComposerStatus(t, timeoutMs)
 *   - lockComposerForClarify(placeholderText), unlockComposerForClarify()
 *   - autoResizeTextarea(ta), scheduleComposerAutoResize()
 *   - Keybindings (Enter, Shift+Enter, Ctrl+Enter, Cmd+B, Cmd+/, Cmd+K, Escape)
 *   - Large text and image paste handling
 *   - IME composition guards
 *
 * Loaded early in boot sequence (see index.html).
 */

const LARGE_TEXT_PASTE_CHAR_THRESHOLD = 4000;
const LARGE_TEXT_PASTE_LINE_THRESHOLD = 100;

let _composerLockState = null;
let _compressionPlaceholderSaved = null;
let _composerStatusTimer = null;
let _imeComposing = false;

function _getEl(id) {
  if (typeof $ === 'function') return $(id);
  if (typeof document !== 'undefined') return document.getElementById(id);
  return null;
}

function _getState() {
  if (typeof S !== 'undefined' && S) return S;
  return { busy: false, pendingFiles: [] };
}

function _tr(key, fb) {
  if (typeof t === 'function') {
    const val = t(key);
    return val === key ? fb : (val || fb);
  }
  return fb;
}

function setComposerStatus(t, timeoutMs = 0) {
  const el = _getEl('composerStatus');
  if (!el) return;
  if (_composerStatusTimer) {
    clearTimeout(_composerStatusTimer);
    _composerStatusTimer = null;
  }
  if (!t) {
    el.classList.add('composer-control-hidden');
    el.setAttribute('aria-hidden', 'true');
    el.textContent = '';
    return;
  }
  el.classList.remove('composer-control-hidden');
  el.removeAttribute('aria-hidden');
  el.textContent = t;
  el.style.display = '';
  if (timeoutMs > 0) {
    const timer = setTimeout(() => {
      if (_composerStatusTimer !== timer) return;
      _composerStatusTimer = null;
      setComposerStatus('');
    }, timeoutMs);
    _composerStatusTimer = timer;
  }
}

function lockComposerForClarify(placeholderText) {
  const input = _getEl('msg');
  if (!input) return;
  const S = _getState();
  const sid = S && S.session && S.session.session_id;
  if (sid && typeof _saveComposerDraftNow === 'function') {
    _saveComposerDraftNow(sid, input.value || '', S.pendingFiles ? [...S.pendingFiles] : []);
  }
  if (!_composerLockState) {
    _composerLockState = {
      disabled: input.disabled,
      placeholder: input.placeholder,
    };
  }
  input.disabled = true;
  if (placeholderText) input.placeholder = placeholderText;
  updateSendBtn();
}

function unlockComposerForClarify() {
  const input = _getEl('msg');
  if (!input) return;
  if (_composerLockState) {
    input.disabled = !!_composerLockState.disabled;
    if (typeof _composerLockState.placeholder === 'string') {
      input.placeholder = _composerLockState.placeholder;
    }
    _composerLockState = null;
  } else {
    input.disabled = false;
  }
  updateSendBtn();
}

function _composerHasContent() {
  const msg = _getEl('msg');
  const S = _getState();
  const pending = S.pendingFiles || [];
  return !!((msg && msg.value.trim().length > 0) || pending.length > 0 || (typeof window !== 'undefined' && typeof window._hasPendingSelections === 'function' && window._hasPendingSelections()));
}

function _getExplicitBusyCommandAction(text) {
  const trimmed = (text || '').trim();
  if (!trimmed.startsWith('/')) return null;
  const body = trimmed.slice(1);
  const name = (body.split(/\s+/)[0] || '').toLowerCase();
  const args = body.slice(name.length).trim();
  if (!args) return null;
  if (name === 'queue') return 'queue';
  const S = _getState();
  if (name === 'steer') {
    if (S.activeStreamId && typeof _trySteer === 'function') return 'steer';
    return 'queue';
  }
  if (name === 'interrupt') {
    if (S.activeStreamId && typeof cancelStream === 'function') return 'interrupt';
    return 'queue';
  }
  return null;
}

function getComposerPrimaryAction() {
  const msg = _getEl('msg');
  const hasContent = _composerHasContent();
  const locked = !!(msg && msg.disabled);
  if (locked) return 'disabled';
  const compressionRunning = typeof isCompressionUiRunning === 'function' && isCompressionUiRunning();
  const S = _getState();
  const isBusy = !!S.busy || compressionRunning;
  if (!isBusy) return hasContent ? 'send' : 'disabled';
  if (!hasContent) {
    if (S.activeStreamId && typeof cancelStream === 'function') return 'stop';
    if (compressionRunning) return 'queue';
    return 'disabled';
  }
  const explicitAction = _getExplicitBusyCommandAction(msg && msg.value);
  if (explicitAction) return explicitAction;
  const defaultMessageMode = (typeof window !== 'undefined' && window._defaultMessageMode) || 'steer';
  if (defaultMessageMode === 'steer') {
    if (S.activeStreamId && typeof _trySteer === 'function') return 'steer';
    return 'queue';
  }
  if (defaultMessageMode === 'interrupt') {
    if (S.activeStreamId && typeof cancelStream === 'function') return 'interrupt';
    return 'queue';
  }
  return 'queue';
}

function _applyBusyComposerPlaceholder() {
  const input = _getEl('msg');
  if (!input) return;
  if (_compressionPlaceholderSaved !== null) return;
  if (input.disabled) return;
  if (_composerHasContent()) return;
  const assistantName = typeof assistantDisplayName === 'function' ? assistantDisplayName() : 'AGY';
  const idlePlaceholder = 'Message ' + assistantName + '\u2026';
  const S = _getState();
  if (typeof window !== 'undefined' && (!window._showBusyPlaceholderHint || !S.busy)) {
    input.placeholder = idlePlaceholder;
    return;
  }
  const busyMode = (typeof window !== 'undefined' && window._defaultMessageMode) || 'steer';
  const busyPlaceholderKey = busyMode === 'interrupt'
    ? 'composer_placeholder_busy_interrupt'
    : busyMode === 'steer'
      ? 'composer_placeholder_busy_steer'
      : 'composer_placeholder_busy_queue';
  const busyPlaceholderFallback = busyMode === 'interrupt'
    ? 'Enter = interrupt | /queue | /background | /steer'
    : busyMode === 'steer'
      ? 'Enter = steer | /queue | /background | /interrupt'
      : 'Enter = queue | /interrupt | /background | /steer';
  input.placeholder = _tr(busyPlaceholderKey, busyPlaceholderFallback);
}

function _setComposerPrimaryButtonIcon(btn, action) {
  const icons = {
    send: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>',
    queue: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M16 5H3"/><path d="M16 12H3"/><path d="M9 19H3"/><path d="m16 16-3 3 3 3"/><path d="M21 5v12a2 2 0 0 1-2 2h-6"/></svg>',
    interrupt: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 4v16"/><path d="M6.029 4.285A2 2 0 0 0 3 6v12a2 2 0 0 0 3.029 1.715l9.997-5.998a2 2 0 0 0 .003-3.432z"/></svg>',
    steer: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="m16.24 7.76-1.804 5.411a2 2 0 0 1-1.265 1.265L7.76 16.24l1.804-5.411a2 2 0 0 1 1.265-1.265z"/></svg>',
    stop: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="5" y="5" width="14" height="14" rx="2"></rect></svg>',
    disabled: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>'
  };
  const next = icons[action] || icons.send;
  if (btn.innerHTML !== next) btn.innerHTML = next;
}

function updateSendBtn() {
  const btn = _getEl('btnSend');
  if (!btn) {
    _applyBusyComposerPlaceholder();
    return;
  }
  const action = getComposerPrimaryAction();
  btn.dataset.action = action;
  btn.classList.toggle('stop', action === 'stop');
  btn.classList.toggle('queue', action === 'queue');
  btn.classList.toggle('interrupt', action === 'interrupt');
  btn.classList.toggle('steer', action === 'steer');

  let _btnTitle;
  if (action === 'disabled') {
    const _dmsg = _getEl('msg');
    if (_dmsg && _dmsg.disabled) _btnTitle = _tr('composer_disabled_clarify', 'Respond to the clarification request');
    else _btnTitle = _tr('composer_disabled_empty', 'Type a message to send');
  } else if (action === 'queue' && typeof isCompressionUiRunning === 'function' && isCompressionUiRunning()) {
    _btnTitle = _tr('composer_compression_will_queue', 'Type a message — it will queue and send after compression');
  } else {
    const _tmap = { send: 'Send message', queue: 'Queue message', interrupt: 'Interrupt and send', steer: 'Steer current response', stop: 'Stop generation' };
    _btnTitle = _tr('composer_' + action, _tmap[action] || 'Send message');
  }
  btn.title = _btnTitle;
  btn.setAttribute('aria-label', _btnTitle);
  _setComposerPrimaryButtonIcon(btn, action);
  _applyBusyComposerPlaceholder();

  btn.style.display = '';
  btn.disabled = action === 'disabled';
  if (action !== 'disabled' && !btn.classList.contains('visible')) {
    btn.classList.remove('visible');
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(() => btn.classList.add('visible'));
    else btn.classList.add('visible');
  } else if (action === 'disabled') {
    btn.classList.remove('visible');
  }
}

async function handleComposerPrimaryAction() {
  if (typeof window !== 'undefined' && window._micActive) {
    window._micPendingSend = true;
    if (typeof _stopMic === 'function') _stopMic();
    return;
  }
  const action = typeof getComposerPrimaryAction === 'function' ? getComposerPrimaryAction() : 'send';
  if (action === 'disabled') return;
  if (action === 'stop') {
    if (typeof cancelStream === 'function') {
      const ok = await cancelStream('composer-stop');
      if (!ok && typeof showToast === 'function') showToast(_tr('cancel_failed', 'Cancel failed'), null, 'error');
    }
    return;
  }
  if (typeof send === 'function') await send();
}

function setBusy(v) {
  const S = _getState();
  S.busy = v;
  updateSendBtn();
  if (!v) {
    if (typeof _clearActivityElapsedTimer === 'function') _clearActivityElapsedTimer();
    if (typeof setStatus === 'function') setStatus('');
    setComposerStatus('');
    const sid = (typeof _queueDrainSid !== 'undefined' && _queueDrainSid) || (S.session && S.session.session_id);
    if (typeof _queueDrainSid !== 'undefined') _queueDrainSid = null;
    if (typeof updateQueueBadge === 'function') updateQueueBadge(sid);

    const _isViewedSid = !S.session || sid === S.session.session_id;
    const next = sid && _isViewedSid && typeof shiftQueuedSessionMessage === 'function' ? shiftQueuedSessionMessage(sid) : null;
    if (next) {
      if (typeof updateQueueBadge === 'function') updateQueueBadge(sid);
      setTimeout(() => {
        if (S.session && S.session.session_id !== sid) {
          if (typeof queueSessionMessage === 'function') queueSessionMessage(sid, next);
          if (typeof updateQueueBadge === 'function') updateQueueBadge(sid);
          return;
        }
        const msgEl = _getEl('msg');
        if (msgEl) msgEl.value = next.text || '';
        S.pendingFiles = Array.isArray(next.files) ? [...next.files] : [];
        if (next.model && S.session && next.model !== S.session.model) {
          S.session.model = next.model;
        }
        if (next.model_provider && S.session) S.session.model_provider = next.model_provider;
        if (next.model && S.session) {
          if (typeof _applyModelToDropdown === 'function' && _getEl('modelSelect')) {
            _applyModelToDropdown(next.model, _getEl('modelSelect'), S.session.model_provider || null);
          }
          if (typeof syncModelChip === 'function') syncModelChip();
        }
        if (typeof autoResize === 'function') autoResize();
        if (typeof renderTray === 'function') renderTray();
        if (typeof send === 'function') send();
      }, 120);
    }
  }
}

function autoResizeTextarea(ta) {
  if (!ta) return;
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight, 300) + 'px';
}

function _isImeEnter(e) {
  return e.isComposing || e.keyCode === 229 || _imeComposing;
}

function _hasFinePointerCoexisting() {
  try {
    return typeof matchMedia === 'function' && matchMedia('(any-pointer:fine)').matches;
  } catch (_) {
    return false;
  }
}

function _isNumpadEnter(e) {
  return e.key === 'Enter' && (e.code === 'NumpadEnter' || e.location === (typeof KeyboardEvent !== 'undefined' ? KeyboardEvent.DOM_KEY_LOCATION_NUMPAD : 3));
}

function _largeTextPasteLineCount(text) {
  const value = String(text || '');
  const lines = value.split('\n');
  return value.endsWith('\n') ? lines.length - 1 : lines.length;
}

function _shouldAttachLargePastedText(text) {
  if (typeof window !== 'undefined' && window._largeTextPasteAsAttachment === false) return false;
  const value = String(text || '');
  if (!value.trim()) return false;
  return value.length >= LARGE_TEXT_PASTE_CHAR_THRESHOLD || _largeTextPasteLineCount(value) >= LARGE_TEXT_PASTE_LINE_THRESHOLD;
}

function _largeTextPasteFileName(now) {
  const d = new Date(now || Date.now());
  const p = n => String(n).padStart(2, '0');
  const stamp = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}_${p(d.getHours())}-${p(d.getMinutes())}-${p(d.getSeconds())}-${String(d.getMilliseconds()).padStart(3, '0')}`;
  const S = _getState();
  const existing = new Set((S.pendingFiles || []).map(f => f && f.name).filter(Boolean));
  let name = `pasted-text-${stamp}.md`;
  for (let i = 2; existing.has(name); i++) name = `pasted-text-${stamp}-${i}.md`;
  return name;
}

function _largeTextPasteFile(text, now) {
  const name = _largeTextPasteFileName(now || Date.now());
  if (typeof File !== 'undefined') {
    return new File([String(text || '')], name, { type: 'text/markdown;charset=utf-8' });
  }
  return { name, text: String(text || '') };
}

function _largeTextPasteFitsUploadLimit(file) {
  const maxBytes = typeof MAX_UPLOAD_BYTES === 'number' ? MAX_UPLOAD_BYTES : Infinity;
  return !(file && file.size > maxBytes);
}

function _attachLargePastedText(file) {
  if (typeof addFiles === 'function') addFiles([file]);
  if (typeof setStatus === 'function') setStatus(_tr('text_pasted', 'Pasted text: ') + file.name);
  return file;
}

// ── Global registration ──────────────────────────────────────────────────────
if (typeof window !== 'undefined') {
  window.LARGE_TEXT_PASTE_CHAR_THRESHOLD = LARGE_TEXT_PASTE_CHAR_THRESHOLD;
  window.LARGE_TEXT_PASTE_LINE_THRESHOLD = LARGE_TEXT_PASTE_LINE_THRESHOLD;
  window.setComposerStatus = setComposerStatus;
  window.lockComposerForClarify = lockComposerForClarify;
  window.unlockComposerForClarify = unlockComposerForClarify;
  window.getComposerPrimaryAction = getComposerPrimaryAction;
  window.updateSendBtn = updateSendBtn;
  window.handleComposerPrimaryAction = handleComposerPrimaryAction;
  window.setBusy = setBusy;
  window.autoResizeTextarea = autoResizeTextarea;
  window._isImeEnter = _isImeEnter;
  window._hasFinePointerCoexisting = _hasFinePointerCoexisting;
  window._isNumpadEnter = _isNumpadEnter;
  window._largeTextPasteLineCount = _largeTextPasteLineCount;
  window._shouldAttachLargePastedText = _shouldAttachLargePastedText;
  window._largeTextPasteFileName = _largeTextPasteFileName;
  window._largeTextPasteFile = _largeTextPasteFile;
  window._largeTextPasteFitsUploadLimit = _largeTextPasteFitsUploadLimit;
  window._attachLargePastedText = _attachLargePastedText;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    LARGE_TEXT_PASTE_CHAR_THRESHOLD,
    LARGE_TEXT_PASTE_LINE_THRESHOLD,
    setComposerStatus,
    lockComposerForClarify,
    unlockComposerForClarify,
    getComposerPrimaryAction,
    updateSendBtn,
    handleComposerPrimaryAction,
    setBusy,
    autoResizeTextarea,
    _isImeEnter,
    _largeTextPasteLineCount,
    _shouldAttachLargePastedText,
    _largeTextPasteFileName,
    _largeTextPasteFile,
    _largeTextPasteFitsUploadLimit,
  };
}
