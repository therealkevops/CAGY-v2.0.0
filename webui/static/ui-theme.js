/**
 * ui-theme.js — Centralized Theme, Skin, and Appearance Management.
 * Handles dark/light/system modes, design-token skins, font sizes,
 * extension-registered skins, and meta theme-color synchronization.
 *
 * Loaded early in boot sequence (see index.html).
 */

// ── Themes & Skins Definitions ───────────────────────────────────────────────
const _THEMES = [
  { name: 'Light', value: 'light', colors: ['#FEFCF7', '#FAF7F0', '#B8860B'] },
  { name: 'Dark', value: 'dark', colors: ['#0D0D1A', '#141425', '#FFD700'] },
  { name: 'System', value: 'system', colors: ['#FEFCF7', '#0D0D1A', '#B8860B'] },
];

const _SKINS = [
  { name: 'Graphite', value: 'graphite', colors: ['#FFFFFF', '#D6D6D6', '#242424'] },
  { name: 'Slate', value: 'slate', colors: ['#334155', '#475569', '#64748b'] },
  { name: 'Verdigris', value: 'verdigris', colors: ['#C89A5A', '#0F1714', '#22342C'] },
  { name: 'Classic Gold', value: 'default', colors: ['#FFD700', '#FFBF00', '#CD7F32'] },
];

const _VALID_THEMES = new Set((_THEMES || []).map(t => t.value));
const _VALID_SKINS = new Set((_SKINS || []).map(s => (s.value || s.name).toLowerCase()));
const _LEGACY_THEME_MAP = {
  graphite: { theme: 'dark', skin: 'graphite' },
  slate: { theme: 'dark', skin: 'slate' },
  solarized: { theme: 'dark', skin: 'slate' },
  monokai: { theme: 'dark', skin: 'slate' },
  nord: { theme: 'dark', skin: 'slate' },
  oled: { theme: 'dark', skin: 'graphite' },
};

let _systemThemeMq = null;
let _onSystemThemeChange = null;
let _resolvedThemeBaseDark = false;

function _normalizeAppearance(theme, skin) {
  const rawTheme = typeof theme === 'string' ? theme.trim().toLowerCase() : '';
  const rawSkin = typeof skin === 'string' ? skin.trim().toLowerCase() : '';
  const legacy = _LEGACY_THEME_MAP[rawTheme];
  const nextTheme = legacy ? legacy.theme : (_VALID_THEMES.has(rawTheme) ? rawTheme : 'dark');
  const nextSkin = _VALID_SKINS.has(rawSkin) ? rawSkin : (legacy ? legacy.skin : 'graphite');
  return { theme: nextTheme, skin: nextSkin };
}

// Sync <meta name="theme-color"> with the active theme's app chrome color.
function _syncThemeColorMeta() {
  try {
    if (typeof document === 'undefined') return;
    const bg = getComputedStyle(document.documentElement).getPropertyValue('--sidebar').trim();
    if (!bg) return;
    const known = document.getElementById('agy-theme-color');
    if (known) {
      known.setAttribute('content', bg);
      known.removeAttribute('media');
    }
    document.querySelectorAll('meta[name="theme-color"]').forEach(meta => {
      meta.setAttribute('content', bg);
      meta.removeAttribute('media');
    });
  } catch (_) {}
}

function _skinKey(skin) {
  return (skin && String(skin.value || skin.name || '').toLowerCase()) || '';
}

function _findSkinEntry(key) {
  const normalized = String(key || 'graphite').toLowerCase();
  return (_SKINS || []).find(s => _skinKey(s) === normalized) || null;
}

function _activeSkinScheme() {
  if (typeof document === 'undefined') return '';
  const key = (document.documentElement.dataset.skin || 'graphite').toLowerCase();
  const skin = _findSkinEntry(key);
  const scheme = skin && skin._extScheme;
  return scheme === 'light' || scheme === 'dark' ? scheme : '';
}

function _effectiveThemeDark(baseIsDark) {
  const scheme = _activeSkinScheme();
  if (scheme === 'dark') return true;
  if (scheme === 'light') return false;
  return !!baseIsDark;
}

function _setResolvedTheme(isDark) {
  _resolvedThemeBaseDark = !!isDark;
  const effectiveDark = _effectiveThemeDark(_resolvedThemeBaseDark);
  if (typeof document !== 'undefined') {
    document.documentElement.classList.toggle('dark', effectiveDark);
    const link = document.getElementById('prism-theme');
    if (!link) {
      _syncThemeColorMeta();
      return;
    }
    const want = effectiveDark
      ? 'https://cdn.jsdelivr.net/npm/prismjs@1.29.0/themes/prism-tomorrow.min.css'
      : 'https://cdn.jsdelivr.net/npm/prismjs@1.29.0/themes/prism.min.css';
    if (link.href !== want) {
      link.integrity = '';
      link.href = want;
    }
    _syncThemeColorMeta();
  }
}

function _applyTheme(name) {
  const normalized = _normalizeAppearance(name, 'graphite');
  if (typeof document !== 'undefined') {
    delete document.documentElement.dataset.theme;
  }
  if (_systemThemeMq && _onSystemThemeChange) {
    _systemThemeMq.removeEventListener('change', _onSystemThemeChange);
    _systemThemeMq = null;
    _onSystemThemeChange = null;
  }
  if (normalized.theme === 'system') {
    if (typeof window !== 'undefined' && window.matchMedia) {
      _systemThemeMq = window.matchMedia('(prefers-color-scheme:dark)');
      _onSystemThemeChange = () => _setResolvedTheme(_systemThemeMq.matches);
      _setResolvedTheme(_systemThemeMq.matches);
      _systemThemeMq.addEventListener('change', _onSystemThemeChange);
    }
    return;
  }
  _setResolvedTheme(normalized.theme === 'dark');
}

function _applySkin(name) {
  const raw = (name || '').toLowerCase();
  const key = (_VALID_SKINS.has(raw) && raw) ? raw : 'graphite';
  if (typeof document !== 'undefined') {
    document.documentElement.dataset.skin = key;
  }
  _setResolvedTheme(_resolvedThemeBaseDark);
}

function _pickTheme(name) {
  const currentSkin = typeof localStorage !== 'undefined' ? localStorage.getItem('agy-skin') : null;
  const appearance = _normalizeAppearance(name, currentSkin);
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem('agy-theme', appearance.theme);
    localStorage.setItem('agy-skin', appearance.skin);
  }
  _applyTheme(appearance.theme);
  _applySkin(appearance.skin);
  _syncThemePicker(appearance.theme);
  _syncSkinPicker(appearance.skin);
  const hidden = typeof $ === 'function' ? $('settingsTheme') : null;
  if (hidden) hidden.value = appearance.theme;
  const skinHidden = typeof $ === 'function' ? $('settingsSkin') : null;
  if (skinHidden) skinHidden.value = appearance.skin;
  if (typeof window !== 'undefined' && typeof window._scheduleAppearanceAutosave === 'function') {
    window._scheduleAppearanceAutosave();
  }
}

function _pickSkin(name) {
  const currentTheme = typeof localStorage !== 'undefined' ? localStorage.getItem('agy-theme') : null;
  const appearance = _normalizeAppearance(currentTheme, name);
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem('agy-theme', appearance.theme);
    localStorage.setItem('agy-skin', appearance.skin);
  }
  _applyTheme(appearance.theme);
  _applySkin(appearance.skin);
  _syncThemePicker(appearance.theme);
  _syncSkinPicker(appearance.skin);
  const hidden = typeof $ === 'function' ? $('settingsSkin') : null;
  if (hidden) hidden.value = appearance.skin;
  const themeHidden = typeof $ === 'function' ? $('settingsTheme') : null;
  if (themeHidden) themeHidden.value = appearance.theme;
  if (typeof window !== 'undefined' && typeof window._scheduleAppearanceAutosave === 'function') {
    window._scheduleAppearanceAutosave();
  }
}

function _syncThemePicker(active) {
  if (typeof document === 'undefined') return;
  document.querySelectorAll('#themePickerGrid .theme-pick-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.themeVal === active);
    btn.style.borderColor = '';
    btn.style.boxShadow = '';
  });
}

function _syncSkinPicker(active) {
  if (typeof document === 'undefined') return;
  document.querySelectorAll('#skinPickerGrid .skin-pick-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.skinVal === active);
    btn.style.borderColor = '';
    btn.style.boxShadow = '';
  });
}

function _applyFontSize(size) {
  if (typeof document === 'undefined') return;
  if (size && size !== 'default') {
    document.documentElement.dataset.fontSize = size;
  } else {
    delete document.documentElement.dataset.fontSize;
  }
}

function _pickFontSize(size) {
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem('agy-font-size', size);
  }
  _applyFontSize(size);
  _syncFontSizePicker(size);
  const hidden = typeof $ === 'function' ? $('settingsFontSize') : null;
  if (hidden) hidden.value = size;
  if (typeof window !== 'undefined' && typeof window._scheduleAppearanceAutosave === 'function') {
    window._scheduleAppearanceAutosave();
  }
}

function _syncFontSizePicker(active) {
  if (typeof document === 'undefined') return;
  document.querySelectorAll('#fontSizePickerGrid .font-size-pick-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.fontSizeVal === (active || 'default'));
    btn.style.borderColor = '';
    btn.style.boxShadow = '';
  });
}

function _buildSkinPicker(activeSkin) {
  if (typeof document === 'undefined') return;
  const grid = typeof $ === 'function' ? $('skinPickerGrid') : document.getElementById('skinPickerGrid');
  if (!grid) return;
  grid.innerHTML = '';
  for (const skin of _SKINS) {
    const key = (skin.value || skin.name).toLowerCase();
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'skin-pick-btn';
    btn.dataset.skinVal = key;
    btn.style.cssText = 'border:1px solid var(--border2);border-radius:8px;padding:8px 4px;text-align:center;cursor:pointer;background:none;transition:all .15s';
    btn.onclick = () => _pickSkin(key);

    const dotRow = document.createElement('div');
    dotRow.style.cssText = 'display:flex;gap:3px;justify-content:center;margin-bottom:4px';
    for (const c of (skin.colors || [])) {
      const dot = document.createElement('span');
      dot.style.cssText = 'display:inline-block;width:10px;height:10px;border-radius:50%';
      dot.style.background = c;
      dotRow.appendChild(dot);
    }
    const labelEl = document.createElement('span');
    labelEl.style.cssText = 'font-size:11px;color:var(--text)';
    labelEl.textContent = skin.label || skin.name || '';
    btn.appendChild(dotRow);
    btn.appendChild(labelEl);
    grid.appendChild(btn);
  }
  _syncSkinPicker((activeSkin || 'default').toLowerCase());
}

// ── Extension-Registered Skins ───────────────────────────────────────────────
const _EXT_SKIN_STYLE_ID = 'agyExtensionSkinStyles';
const _EXT_SKIN_KEYS = new Set();
const _RESERVED_SKIN_KEYS = new Set((_SKINS || []).map(s => (s.value || s.name).toLowerCase()));
const _ALLOWED_SKIN_TOKENS = new Set([
  '--bg', '--surface', '--surface2', '--surface-subtle', '--text', '--text2', '--muted',
  '--accent', '--accent2', '--accent3', '--accent-contrast', '--accent-hover',
  '--accent-text', '--accent-bg', '--accent-bg-strong', '--accent-rgb',
  '--border', '--border2', '--hover-bg', '--code-bg', '--code-text',
  '--sidebar', '--sidebar-text', '--user-bubble', '--assistant-bubble',
  '--success', '--warning', '--danger', '--info', '--link'
]);
const _SAFE_SKIN_VALUE_RE = /^(#(?:[0-9a-fA-F]{3,8})|rg(?:b|ba)\(\s*[0-9.,%\s/]+\)|hsl(?:a)?\(\s*[0-9.,%\s/deg]+\)|[0-9]{1,3}\s*,\s*[0-9]{1,3}\s*,\s*[0-9]{1,3}|[a-zA-Z]{3,20}|[0-9.]+(?:px|em|rem|%)?)$/;

function _sanitizeSkinScheme(scheme) {
  const value = String(scheme || '').trim().toLowerCase();
  return value === 'light' || value === 'dark' ? value : '';
}

function _sanitizeSkinTokens(tokens) {
  const out = {};
  if (!tokens || typeof tokens !== 'object') return out;
  for (const rawKey of Object.keys(tokens)) {
    const key = String(rawKey).trim();
    if (!_ALLOWED_SKIN_TOKENS.has(key)) continue;
    const val = String(tokens[rawKey]).trim();
    if (val.length > 64) continue;
    if (!_SAFE_SKIN_VALUE_RE.test(val)) continue;
    out[key] = val;
  }
  return out;
}

function _renderExtensionSkinStyles() {
  if (typeof document === 'undefined') return;
  let styleEl = document.getElementById(_EXT_SKIN_STYLE_ID);
  if (!styleEl) {
    styleEl = document.createElement('style');
    styleEl.id = _EXT_SKIN_STYLE_ID;
    document.head.appendChild(styleEl);
  }
  const blocks = [];
  for (const skin of _SKINS) {
    if (!skin || !skin._extToken) continue;
    const key = (skin.value || skin.name).toLowerCase();
    const decls = Object.keys(skin._extToken).map(k => `${k}:${skin._extToken[k]}`).join(';');
    if (decls) blocks.push(`:root[data-skin="${key}"]{${decls}}`);
  }
  styleEl.textContent = blocks.join('\n');
}

function registerAgySkin(descriptor) {
  try {
    if (!descriptor || typeof descriptor !== 'object') return false;
    const name = String(descriptor.name || '').trim();
    if (!name) return false;
    const rawVal = String(descriptor.value || name).trim().toLowerCase();
    const key = rawVal.replace(/[^a-z0-9_-]/g, '');
    if (!key) return false;
    if (_RESERVED_SKIN_KEYS.has(key)) return false;
    const tokens = _sanitizeSkinTokens(descriptor.tokens);
    if (Object.keys(tokens).length === 0) return false;
    const scheme = _sanitizeSkinScheme(descriptor.scheme);
    let colors = Array.isArray(descriptor.colors) ? descriptor.colors.slice(0, 3) : [];
    colors = colors.map(c => String(c).trim()).filter(c => _SAFE_SKIN_VALUE_RE.test(c));
    while (colors.length < 3) colors.push(tokens['--accent'] || tokens['--bg'] || tokens['--text'] || '#888');
    const label = String(descriptor.label || name).slice(0, 40);
    const entry = { name: name.slice(0, 40), value: key, label, colors, _extToken: tokens, _extScheme: scheme, _extension: true };

    const existingIdx = _SKINS.findIndex(s => (s.value || s.name).toLowerCase() === key);
    if (existingIdx >= 0 && _EXT_SKIN_KEYS.has(key)) {
      _SKINS[existingIdx] = entry;
    } else if (existingIdx >= 0) {
      return false;
    } else {
      _SKINS.push(entry);
    }
    _EXT_SKIN_KEYS.add(key);
    _VALID_SKINS.add(key);
    _renderExtensionSkinStyles();
    if (typeof document !== 'undefined' && document.getElementById('skinPickerGrid')) {
      _buildSkinPicker((localStorage.getItem('agy-skin') || 'default').toLowerCase());
    }
    if (typeof localStorage !== 'undefined' && (localStorage.getItem('agy-skin') || '').toLowerCase() === key) {
      _applySkin(key);
    }
    return true;
  } catch (_) {
    return false;
  }
}

// ── Public Modern Theme API ──────────────────────────────────────────────────
function getTheme() {
  return (typeof localStorage !== 'undefined' && localStorage.getItem('agy-theme')) || 'dark';
}

function setTheme(name) {
  _pickTheme(name);
}

function toggleTheme() {
  const current = getTheme();
  const next = current === 'dark' ? 'light' : 'dark';
  setTheme(next);
  return next;
}

// Export to window for browser runtime
if (typeof window !== 'undefined') {
  window._THEMES = _THEMES;
  window._SKINS = _SKINS;
  window._VALID_THEMES = _VALID_THEMES;
  window._VALID_SKINS = _VALID_SKINS;
  window._LEGACY_THEME_MAP = _LEGACY_THEME_MAP;
  window._normalizeAppearance = _normalizeAppearance;
  window._syncThemeColorMeta = _syncThemeColorMeta;
  window._skinKey = _skinKey;
  window._findSkinEntry = _findSkinEntry;
  window._activeSkinScheme = _activeSkinScheme;
  window._effectiveThemeDark = _effectiveThemeDark;
  window._setResolvedTheme = _setResolvedTheme;
  window._applyTheme = _applyTheme;
  window._applySkin = _applySkin;
  window._pickTheme = _pickTheme;
  window._pickSkin = _pickSkin;
  window._syncThemePicker = _syncThemePicker;
  window._syncSkinPicker = _syncSkinPicker;
  window._applyFontSize = _applyFontSize;
  window._pickFontSize = _pickFontSize;
  window._syncFontSizePicker = _syncFontSizePicker;
  window._buildSkinPicker = _buildSkinPicker;
  window.registerAgySkin = registerAgySkin;
  window.registerHermesSkin = registerAgySkin;
  window.getTheme = getTheme;
  window.setTheme = setTheme;
  window.toggleTheme = toggleTheme;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    _THEMES,
    _SKINS,
    _VALID_THEMES,
    _VALID_SKINS,
    _LEGACY_THEME_MAP,
    _normalizeAppearance,
    _sanitizeSkinTokens,
    _sanitizeSkinScheme,
    getTheme,
    setTheme,
    toggleTheme,
    registerAgySkin
  };
}
