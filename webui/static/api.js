/**
 * api.js — Shared fetch wrapper for consistent HTTP error handling.
 * Loaded before all other app scripts (see index.html).
 *
 * Usage:
 *   const data = await apiFetch('/api/sessions');
 *   const result = await apiFetch('/api/sessions', {
 *     method: 'POST',
 *     body: JSON.stringify({...}),
 *     headers: { 'Content-Type': 'application/json' }
 *   });
 */
async function apiFetch(url, options = {}) {
  let resp;
  try {
    resp = await fetch(url, options);
  } catch (err) {
    console.error('[apiFetch] Network error:', url, err);
    throw new Error(`Network error contacting ${url}: ${err.message}`);
  }
  if (!resp.ok) {
    let body = '';
    try {
      body = await resp.text();
    } catch (_) {}
    console.error('[apiFetch] HTTP error:', resp.status, url, body.slice(0, 200));
    throw new Error(`HTTP ${resp.status} from ${url}: ${body.slice(0, 120)}`);
  }
  const ct = resp.headers.get('content-type') || '';
  if (ct.includes('application/json')) {
    try {
      return await resp.json();
    } catch (err) {
      console.error('[apiFetch] JSON parse error:', url, err);
      throw new Error(`JSON parse failed for ${url}: ${err.message}`);
    }
  }
  return resp.text();
}
