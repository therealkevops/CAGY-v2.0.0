/**
 * events.js — Lightweight EventBus for decoupled cross-module communication.
 * Loaded before all other app scripts in index.html.
 *
 * API:
 *   AGY_EVENTS.on('event:name', handlerFn)    // subscribe
 *   AGY_EVENTS.off('event:name', handlerFn)   // unsubscribe
 *   AGY_EVENTS.emit('event:name', ...args)    // publish
 *
 * Error handling: handler exceptions are caught and logged; they do not
 * prevent other handlers from running.
 *
 * Event catalogue:
 *   'session:activated'  (sessionId: string)  — user switched to a session
 */
const AGY_EVENTS = (() => {
  const _handlers = {};
  return {
    /**
     * Subscribe to an event.
     * @param {string} event
     * @param {Function} fn
     */
    on(event, fn) {
      if (!_handlers[event]) _handlers[event] = [];
      _handlers[event].push(fn);
    },
    /**
     * Unsubscribe a specific handler from an event.
     * @param {string} event
     * @param {Function} fn
     */
    off(event, fn) {
      if (_handlers[event]) {
        _handlers[event] = _handlers[event].filter(h => h !== fn);
      }
    },
    /**
     * Emit an event to all registered handlers.
     * @param {string} event
     * @param {...*} args
     */
    emit(event, ...args) {
      (_handlers[event] || []).forEach(fn => {
        try {
          fn(...args);
        } catch (e) {
          console.error('[AGY_EVENTS] Handler error for event:', event, e);
        }
      });
    }
  };
})();
