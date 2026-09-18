/**
 * api.js — Centralized HTTP client wrapper with CSRF injection,
 * automatic retries, abort/timeout handling, and consistent error normalization.
 *
 * Loaded before all other app scripts (see index.html).
 *
 * Usage:
 *   const data = await apiFetch('/api/sessions');
 *   const result = await apiFetch('/api/sessions', {
 *     method: 'POST',
 *     json: { name: 'New Session' }
 *   });
 */

class ApiError extends Error {
  constructor(message, status = 0, statusText = '', url = '', body = '', data = null) {
    super(message);
    this.name = 'ApiError';
    this.status = Number(status) || 0;
    this.statusText = statusText || '';
    this.url = url || '';
    this.body = body || '';
    this.data = data;
    this.timeout = false;
  }
}

async function apiFetch(pathOrUrl, opts = {}) {
  const rawPath = String(pathOrUrl || '');
  const rel = rawPath.startsWith('/') ? rawPath.slice(1) : rawPath;
  const base = (typeof document !== 'undefined' && document.baseURI) || (typeof location !== 'undefined' && location.href) || 'http://localhost';
  let url;
  try {
    url = new URL(rel, base);
  } catch (_) {
    url = new URL(rawPath, 'http://localhost');
  }

  const timeoutMs = Object.prototype.hasOwnProperty.call(opts, 'timeoutMs')
    ? opts.timeoutMs
    : (Object.prototype.hasOwnProperty.call(opts, 'timeout') ? opts.timeout : 30000);
  const timeoutToast = opts.timeoutToast !== false;
  const redirect401 = opts.redirect401 !== false;
  const toastOnError = opts.toastOnError === true;
  const maxAttempts = Object.prototype.hasOwnProperty.call(opts, 'retries')
    ? Math.max(0, Number(opts.retries) || 0) + 1
    : 3;
  const retryTimeouts = opts.retryTimeouts === true;
  const retryStatuses = Array.isArray(opts.retryStatuses)
    ? opts.retryStatuses.map(Number).filter(Number.isFinite)
    : [];
  const retryDelayMs = Object.prototype.hasOwnProperty.call(opts, 'retryDelayMs')
    ? Math.max(0, Number(opts.retryDelayMs) || 0)
    : 350;

  // Prepare options copy
  const fetchOpts = { credentials: 'include', ...opts };
  delete fetchOpts.timeoutMs;
  delete fetchOpts.timeout;
  delete fetchOpts.timeoutToast;
  delete fetchOpts.toastOnError;
  delete fetchOpts.redirect401;
  delete fetchOpts.retries;
  delete fetchOpts.retryTimeouts;
  delete fetchOpts.retryStatuses;
  delete fetchOpts.retryDelayMs;

  // JSON helper support: opts.json automatically serializes and sets Content-Type
  const headers = new Headers(fetchOpts.headers || {});
  if (opts.json !== undefined && !fetchOpts.body) {
    fetchOpts.body = JSON.stringify(opts.json);
    if (!headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json');
    }
  }

  // Auto-attach CSRF tokens for state-changing operations
  const method = (fetchOpts.method || 'GET').toUpperCase();
  if (/^(POST|PUT|PATCH|DELETE)$/.test(method)) {
    let csrf = '';
    try {
      const cfg = (typeof window !== 'undefined' && (window.__AGY_CONFIG__ || window.__HERMES_CONFIG__)) || {};
      csrf = cfg.csrfToken || '';
    } catch (_) {}
    if (csrf) {
      if (!headers.has('X-Agy-CSRF-Token')) headers.set('X-Agy-CSRF-Token', csrf);
      if (!headers.has('X-Hermes-CSRF-Token')) headers.set('X-Hermes-CSRF-Token', csrf);
    }
  }
  fetchOpts.headers = headers;

  let lastErr;
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    let controller = null;
    let timeoutId = null;
    let didTimeout = false;
    let upstreamSignal = null;
    let upstreamAbort = null;

    try {
      const iterOpts = { ...fetchOpts };
      const useTimeout = Number.isFinite(Number(timeoutMs)) && Number(timeoutMs) > 0;
      if (useTimeout && typeof AbortController !== 'undefined') {
        controller = new AbortController();
        upstreamSignal = iterOpts.signal || null;
        if (upstreamSignal) {
          upstreamAbort = () => controller.abort(upstreamSignal.reason);
          if (upstreamSignal.aborted) upstreamAbort();
          else upstreamSignal.addEventListener('abort', upstreamAbort, { once: true });
        }
        iterOpts.signal = controller.signal;
      }

      const requestPromise = (async () => {
        let res;
        try {
          res = await fetch(url.href, iterOpts);
        } catch (fetchErr) {
          throw fetchErr;
        }

        if (!res.ok) {
          // 401 handling: redirect to login if session expired
          if (res.status === 401) {
            if (redirect401 && typeof window !== 'undefined' && window.location) {
              const p = (window.location.pathname || '').replace(/\/+$/, '');
              if (/(?:^|\/)login$/.test(p)) {
                window.location.href = 'login';
              } else {
                window.location.href = 'login?next=' + encodeURIComponent(window.location.pathname + window.location.search);
              }
            }
            const err401 = new ApiError(`HTTP 401 from ${url.href}: Unauthorized`, 401, res.statusText, url.href, 'Unauthorized');
            throw err401;
          }

          let text = '';
          try {
            text = await res.text();
          } catch (_) {}

          let message = text;
          let parsedData = null;
          try {
            const j = JSON.parse(text);
            parsedData = j;
            message = j.error || j.message || text;
          } catch (_) {}

          const preview = typeof message === 'string' ? message.slice(0, 120) : '';
          const err = new ApiError(`HTTP ${res.status} from ${url.href}: ${preview}`, res.status, res.statusText, url.href, text, parsedData);
          throw err;
        }

        const ct = res.headers.get('content-type') || '';
        if (ct.includes('application/json')) {
          try {
            return await res.json();
          } catch (jsonErr) {
            throw new ApiError(`JSON parse failed for ${url.href}: ${jsonErr.message}`, res.status, res.statusText, url.href);
          }
        }
        return res.text();
      })();

      return useTimeout
        ? await Promise.race([
            requestPromise,
            new Promise((_, reject) => {
              timeoutId = setTimeout(() => {
                didTimeout = true;
                if (controller) controller.abort();
                const timeoutErr = new ApiError(`Request timed out after ${timeoutMs}ms.`, 408, 'Request Timeout', url.href);
                timeoutErr.name = 'TimeoutError';
                timeoutErr.timeout = true;
                reject(timeoutErr);
              }, Number(timeoutMs));
            }),
          ])
        : await requestPromise;
    } catch (e) {
      lastErr = e;
      const isTimeout = didTimeout || (e && (e.timeout === true || e.name === 'TimeoutError'));
      if (isTimeout) {
        if (retryTimeouts && attempt < 2 && attempt < maxAttempts - 1) {
          if (retryDelayMs) await new Promise(r => setTimeout(r, retryDelayMs * Math.pow(2, attempt)));
          continue;
        }
        const err = (e && e.name === 'TimeoutError') ? e : new ApiError('Request timed out. Please try again.', 408, 'Request Timeout', url.href);
        err.name = 'TimeoutError';
        err.timeout = true;
        if (timeoutToast && typeof window !== 'undefined' && typeof window.showToast === 'function') {
          window.showToast('Request timed out. Please try again.', 5000, 'error');
        }
        throw err;
      }

      // Re-throw 401 redirects immediately
      if (e && e.status === 401) throw e;
      if (e && e.message && /401/.test(e.message)) throw e;

      // Retry on network TypeError or opted-in status codes
      if (attempt < 2 && attempt < maxAttempts - 1 && (e instanceof TypeError || (e && retryStatuses.includes(Number(e.status))))) {
        if (retryDelayMs) await new Promise(r => setTimeout(r, retryDelayMs * Math.pow(2, attempt)));
        continue;
      }

      if (toastOnError && typeof window !== 'undefined' && typeof window.showToast === 'function') {
        window.showToast(e.message || 'API request failed', 5000, 'error');
      }
      throw e;
    } finally {
      if (timeoutId) clearTimeout(timeoutId);
      if (upstreamSignal && upstreamAbort) upstreamSignal.removeEventListener('abort', upstreamAbort);
    }
  }
  throw lastErr;
}

// Convenience method shortcuts
apiFetch.get = (url, opts = {}) => apiFetch(url, { ...opts, method: 'GET' });
apiFetch.post = (url, bodyOrJson, opts = {}) => {
  const isObj = bodyOrJson && typeof bodyOrJson === 'object' && !(bodyOrJson instanceof FormData) && !(bodyOrJson instanceof Blob);
  return apiFetch(url, { ...opts, method: 'POST', ...(isObj ? { json: bodyOrJson } : { body: bodyOrJson }) });
};
apiFetch.put = (url, bodyOrJson, opts = {}) => {
  const isObj = bodyOrJson && typeof bodyOrJson === 'object' && !(bodyOrJson instanceof FormData) && !(bodyOrJson instanceof Blob);
  return apiFetch(url, { ...opts, method: 'PUT', ...(isObj ? { json: bodyOrJson } : { body: bodyOrJson }) });
};
apiFetch.patch = (url, bodyOrJson, opts = {}) => {
  const isObj = bodyOrJson && typeof bodyOrJson === 'object' && !(bodyOrJson instanceof FormData) && !(bodyOrJson instanceof Blob);
  return apiFetch(url, { ...opts, method: 'PATCH', ...(isObj ? { json: bodyOrJson } : { body: bodyOrJson }) });
};
apiFetch.delete = (url, opts = {}) => apiFetch(url, { ...opts, method: 'DELETE' });

// Global registration
if (typeof window !== 'undefined') {
  window.apiFetch = apiFetch;
  window.ApiError = ApiError;
  window.api = apiFetch;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { apiFetch, ApiError };
}
