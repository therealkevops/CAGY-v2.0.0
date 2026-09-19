"""Approval and clarification route handlers, SSE state, and helpers.

Decomposed from routes.py (Sprint M1).
"""
import logging
import queue
import threading
import uuid
from contextlib import contextmanager
from urllib.parse import parse_qs

from api.helpers import _CLIENT_DISCONNECT_ERRORS, bad, j
from api.session_events import publish_session_list_changed
from api.sse_chunked import end_sse_headers
from api.streaming import (
    _sse,
    _sse_set_write_deadline,
)

logger = logging.getLogger(__name__)

_SSE_HEARTBEAT_INTERVAL_SECONDS = 5

# Clarify prompts (optional -- graceful fallback if agent not available)
try:
    from api.clarify import (
        submit_pending as submit_clarify_pending,
        get_pending as get_clarify_pending,
        pending_count as get_clarify_pending_count,
        resolve_clarify,
        resolve_clarify_by_id,
        sse_subscribe as clarify_sse_subscribe,
        sse_unsubscribe as clarify_sse_unsubscribe,
    )
except ImportError:
    submit_clarify_pending = lambda *a, **k: None
    get_clarify_pending = lambda *a, **k: None
    get_clarify_pending_count = lambda *a, **k: 0
    clarify_sse_subscribe = None
    resolve_clarify = lambda *a, **k: 0
    resolve_clarify_by_id = lambda *a, **k: False

# Approval system (optional -- graceful fallback if agent not available)
try:
    from tools.approval import (
        submit_pending as _submit_pending_raw,
        approve_session,
        approve_permanent,
        save_permanent_allowlist,
        is_approved,
        _pending,
        _lock,
        _permanent_approved,
        _gateway_queues,
        resolve_gateway_approval,
        enable_session_yolo,
        disable_session_yolo,
        is_session_yolo_enabled,
    )
except ImportError:
    _submit_pending_raw = lambda *a, **k: None
    approve_session = lambda *a, **k: None
    approve_permanent = lambda *a, **k: None
    save_permanent_allowlist = lambda *a, **k: None
    is_approved = lambda *a, **k: True
    resolve_gateway_approval = lambda *a, **k: 0
    enable_session_yolo = lambda *a, **k: None
    disable_session_yolo = lambda *a, **k: None
    is_session_yolo_enabled = lambda *a, **k: False
    _pending = {}
    _lock = threading.Lock()
    _permanent_approved = set()
    _gateway_queues = {}


# ── Approval SSE subscribers (long-connection push) ──────────────────────────
_approval_sse_subscribers: dict[str, list[queue.Queue]] = {}
_GATEWAY_MIRROR_FLAG = "_gateway_mirror"
_GATEWAY_MIRROR_TOKEN = "_gateway_mirror_token"
_GATEWAY_MIRROR_RETAINED = "_gateway_mirror_retained"
_GATEWAY_ENTRY_DATA_TOKEN_KEY = "_webui_mirror_token"
_GATEWAY_AGENT_IDENTITY_V1 = "_gateway_agent_identity_v1"
_gateway_relay_owners: dict[tuple[str, str], str] = {}
_yolo_transition_lock = threading.Lock()
_yolo_transitions: dict[str, dict] = {}
_gateway_yolo_handoff_guard = threading.Lock()
_gateway_yolo_handoffs: dict[str, dict] = {}


@contextmanager
def gateway_yolo_handoff(session_key: str):
    """Serialize one session's YOLO toggles with gateway approval dispatch."""
    session_key = str(session_key or "").strip()
    with _gateway_yolo_handoff_guard:
        entry = _gateway_yolo_handoffs.get(session_key)
        if entry is None:
            entry = {"lock": threading.Lock(), "users": 0}
            _gateway_yolo_handoffs[session_key] = entry
        entry["users"] += 1
    lock = entry["lock"]
    lock.acquire()
    try:
        yield
    finally:
        lock.release()
        with _gateway_yolo_handoff_guard:
            entry["users"] -= 1
            if entry["users"] == 0:
                _gateway_yolo_handoffs.pop(session_key, None)


def begin_session_yolo_transition(session_key: str) -> object | None:
    """Register a pending YOLO enable until one approval relay settles.

    Multiple tabs may relay approvals for different runs in the same session.
    Track every in-flight enable intent so one failed relay cannot undo another
    successful or explicit enable. Do not publish an unconfirmed enable to the
    shared session flag: the gateway stream may only auto-approve later prompts
    after a relay succeeds or an explicit enable wins.
    """
    session_key = str(session_key or "").strip()
    if not session_key:
        return None
    token = object()
    with _yolo_transition_lock:
        transition = _yolo_transitions.get(session_key)
        if transition is None:
            transition = {
                "was_enabled": bool(is_session_yolo_enabled(session_key)),
                "tokens": set(),
                "committed": False,
            }
            _yolo_transitions[session_key] = transition
        transition["tokens"].add(token)
    return token


def finish_session_yolo_transition(session_key: str, token: object | None, *, succeeded: bool) -> None:
    """Settle one pending YOLO enable without exposing or applying stale state."""
    session_key = str(session_key or "").strip()
    if not session_key or token is None:
        return
    with _yolo_transition_lock:
        transition = _yolo_transitions.get(session_key)
        if transition is None or token not in transition["tokens"]:
            return
        transition["tokens"].remove(token)
        if succeeded:
            transition["committed"] = True
            # The first confirmed relay commits YOLO immediately. Any remaining
            # tokens may fail later but cannot revoke this successful enable.
            enable_session_yolo(session_key)
        if transition["tokens"]:
            return
        _yolo_transitions.pop(session_key, None)
        if transition["committed"] or transition["was_enabled"]:
            enable_session_yolo(session_key)
        else:
            disable_session_yolo(session_key)


def set_session_yolo_enabled(session_key: str, enabled: bool) -> None:
    """Apply an explicit YOLO choice and supersede in-flight rollbacks."""
    session_key = str(session_key or "").strip()
    if not session_key:
        return
    with _yolo_transition_lock:
        _yolo_transitions.pop(session_key, None)
        if enabled:
            enable_session_yolo(session_key)
        else:
            disable_session_yolo(session_key)


def _approval_sse_subscribe(session_id: str) -> queue.Queue:
    """Register an SSE subscriber for approval events on a given session."""
    q = queue.Queue(maxsize=16)
    with _lock:
        _approval_sse_subscribers.setdefault(session_id, []).append(q)
    return q


def _approval_sse_unsubscribe(session_id: str, q: queue.Queue) -> None:
    """Remove an SSE subscriber."""
    with _lock:
        subs = _approval_sse_subscribers.get(session_id)
        if subs and q in subs:
            subs.remove(q)
            if not subs:
                _approval_sse_subscribers.pop(session_id, None)


def _approval_sse_notify_locked(session_id: str, head: dict | None, total: int) -> None:
    """Push an approval event to all SSE subscribers for a session.

    CALLER MUST HOLD `_lock`. Snapshots the subscriber list under the held
    lock and then calls `q.put_nowait()` on each (which is itself thread-safe).

    `head` is the approval entry currently at the head of the queue (the one
    the UI should display) — NOT the just-appended entry. With multiple
    parallel approvals (#527), the just-appended entry is at the TAIL, but
    `/api/approval/pending` always returns the HEAD, so SSE must match.

    `total` is the total number of pending approvals.

    Pass `head=None` and `total=0` when the queue has just been emptied (e.g.
    `_handle_approval_respond` popped the last entry) so the client knows to
    hide its approval card.
    """
    payload = {"pending": dict(head) if head else None, "pending_count": total}
    subs = _approval_sse_subscribers.get(session_id, ())
    for q in subs:
        try:
            q.put_nowait(payload)
        except queue.Full:
            pass  # drop if subscriber is slow (bounded queue prevents memory leak)


def _approval_sse_notify(session_id: str, head: dict | None, total: int) -> None:
    """Convenience wrapper that takes `_lock` itself.

    Use only from contexts that don't already hold `_lock`. Production call
    sites (submit_pending, _handle_approval_respond) MUST hold the lock and
    call `_approval_sse_notify_locked` directly to avoid a notify-ordering
    race where a later append's notify can fire before an earlier append's
    notify (resulting in stale `pending_count`).
    """
    with _lock:
        _approval_sse_notify_locked(session_id, head, total)


def _gateway_mirror_entry_token(entry) -> str | None:
    """Return a stable token for the current process lifetime of a gateway head.

    Stamps a token key into the entry's `.data` dict so
    slotted objects like `_ApprovalEntry` work without attribute mutation
    and the token survives CPython `id()` reuse after GC.
    """
    data = getattr(entry, "data", None)
    if isinstance(data, dict):
        token = data.get(_GATEWAY_ENTRY_DATA_TOKEN_KEY)
        if not token:
            token = uuid.uuid4().hex
            data[_GATEWAY_ENTRY_DATA_TOKEN_KEY] = token
        return token
    return None


def _is_gateway_mirror_entry(entry: dict | None) -> bool:
    return isinstance(entry, dict) and bool(entry.get(_GATEWAY_MIRROR_FLAG))


def _normalize_pending_queue_locked(session_key: str) -> list[dict]:
    """Return the session's polling queue as a mutable list under `_lock`."""
    queue_list = _pending.setdefault(session_key, [])
    if not isinstance(queue_list, list):
        _pending[session_key] = [queue_list]
        queue_list = _pending[session_key]
    return queue_list


def reconcile_gateway_pending_mirror_locked(session_key: str) -> tuple[dict | None, int, bool]:
    """Purge stale gateway mirrors and ensure at most one live head mirror exists.

    CALLER MUST HOLD `_lock`.
    """
    changed = False
    queue_list = list(_normalize_pending_queue_locked(session_key))
    live_gateway_queue = _gateway_queues.get(session_key) or []

    live_head_entry = live_gateway_queue[0] if live_gateway_queue else None
    live_head_data = getattr(live_head_entry, "data", None) or {}
    live_run_id = str(live_head_data.get("run_id") or "").strip()
    # Tokenize EVERY live no-run producer, and derive `live_token` from the
    # authoritative head, whenever `_gateway_queues[session_key]` has live
    # producers. This deliberately does NOT defer to a pre-existing no-run
    # mirror: an unmatched/tokenless mirror A (which the fail-closed binding in
    # submit_gateway_pending_mirror leaves deliberately unbound) must never
    # suppress the live producer's token, or A masks the real pending approval
    # B — B never surfaces as the head and can't be actioned, while responding
    # to A resolves nothing. While any producer is live, a mirror survives only
    # if it is bound to some live producer's own token — the head's via
    # `live_token`, a non-head producer's via `live_local_tokens` (which is what
    # keeps a non-head mirror resolvable) — so unmatched and tokenless copies
    # are discarded instead of masking a real one. Tokenless-orphan retention is
    # reserved for the genuine no-producer case (#7093), which lands here with an
    # empty `live_gateway_queue` and therefore a `None` `live_token` anyway.
    live_local_tokens: set[str] = set()
    for live_entry in live_gateway_queue:
        live_data = getattr(live_entry, "data", None) or {}
        if str(live_data.get("run_id") or "").strip():
            continue
        live_entry_token = _gateway_mirror_entry_token(live_entry) or ""
        if live_entry_token:
            live_local_tokens.add(live_entry_token)
            if not str(live_data.get("approval_id") or "").strip():
                live_data["approval_id"] = f"gwlocal:{live_entry_token}"
    live_token = (
        _gateway_mirror_entry_token(live_head_entry)
        if live_head_entry and live_head_data
        else None
    )
    if live_token and live_run_id and not str(live_head_data.get("approval_id") or "").strip():
        live_head_data["approval_id"] = f"gwrun:{live_run_id}:{live_token}"
    live_approval_id = str(live_head_data.get("approval_id") or "").strip()

    rebuilt: list[dict] = []
    deferred_run_entries: list[dict] = []
    live_mirror_present = False
    for entry in queue_list:
        if not _is_gateway_mirror_entry(entry):
            rebuilt.append(entry)
            continue
        entry_run_id = str(entry.get("run_id") or "").strip()
        entry_approval_id = str(entry.get("approval_id") or "").strip()
        entry_token = str(entry.get(_GATEWAY_MIRROR_TOKEN) or "").strip()
        matches_live_head = False
        if live_token:
            if entry_token and entry_token == live_token:
                matches_live_head = True
            elif (
                live_approval_id
                and live_run_id
                and entry_approval_id == live_approval_id
                and entry_run_id == live_run_id
            ):
                matches_live_head = True

        if entry_run_id:
            if matches_live_head and not live_mirror_present:
                if entry_token != live_token:
                    entry[_GATEWAY_MIRROR_TOKEN] = live_token
                    changed = True
                rebuilt.append(entry)
                live_mirror_present = True
                continue
            if live_token:
                if entry.get(_GATEWAY_MIRROR_RETAINED):
                    rebuilt.append(entry)
                    continue
                if entry_token:
                    changed = True
                    continue
                deferred_run_entries.append(entry)
                continue
            if entry.get(_GATEWAY_MIRROR_RETAINED) or not entry_token:
                rebuilt.append(entry)
                continue
            changed = True
            continue

        # A retained mirror is one whose own producer had already vanished when
        # the user responded (the missing-producer 409 kept visible until an
        # explicit teardown). It survives ONLY while no producer is live: once
        # `_gateway_queues[session_key]` holds a real producer again, an
        # unresolvable retained mirror must not mask it, so fall through to the
        # normal matching below (which keeps it if it still matches a live
        # token and discards it otherwise).
        if entry.get(_GATEWAY_MIRROR_RETAINED) and not live_gateway_queue:
            rebuilt.append(entry)
            continue

        if matches_live_head and not live_mirror_present:
            if entry_token != live_token:
                entry[_GATEWAY_MIRROR_TOKEN] = live_token
                changed = True
            rebuilt.append(entry)
            live_mirror_present = True
            continue

        if entry_token and entry_token in live_local_tokens:
            rebuilt.append(entry)
            continue

        if not live_token:
            if entry_token:
                changed = True
                continue
            rebuilt.append(entry)
            continue

        changed = True

    if live_token and not live_mirror_present:
        mirror_entry = dict(live_head_data)
        mirror_run_id = str(mirror_entry.get("run_id") or "").strip()
        mirror_entry.setdefault(
            "approval_id",
            f"gwrun:{mirror_run_id}:{live_token}" if mirror_run_id else uuid.uuid4().hex,
        )
        mirror_entry[_GATEWAY_MIRROR_FLAG] = True
        mirror_entry[_GATEWAY_MIRROR_TOKEN] = live_token
        rebuilt.append(mirror_entry)
        live_mirror_present = True
        changed = True

    if deferred_run_entries:
        rebuilt.extend(deferred_run_entries)

    if rebuilt:
        if rebuilt != queue_list:
            _pending[session_key] = rebuilt
            changed = True
    else:
        if session_key in _pending:
            _pending.pop(session_key, None)
            changed = True

    head = rebuilt[0] if rebuilt else None
    total = len(rebuilt)
    return head, total, changed


def _gateway_pending_mirror_locked(
    session_key: str,
    approval_id: str = "",
    run_id: str = "",
    mirror_token: str = "",
) -> dict | None:
    """Return the exact live run-backed mirror under `_lock`."""
    approval_id = str(approval_id or "").strip()
    run_id = str(run_id or "").strip()
    mirror_token = str(mirror_token or "").strip()
    queue = _pending.get(session_key)
    entries = queue if isinstance(queue, list) else [queue] if queue else []
    if approval_id:
        matched_entry: dict | None = None
        for entry in entries:
            if not _is_gateway_mirror_entry(entry):
                continue
            if entry.get("approval_id") != approval_id:
                continue
            entry_run_id = str(entry.get("run_id") or "").strip()
            if not entry_run_id:
                if not run_id:
                    return None
                continue
            if run_id and entry_run_id != run_id:
                continue
            if mirror_token and str(entry.get(_GATEWAY_MIRROR_TOKEN) or "").strip() != mirror_token:
                continue
            if run_id:
                return entry
            if matched_entry is not None:
                return None
            matched_entry = entry
        return matched_entry
    for entry in entries:
        if not _is_gateway_mirror_entry(entry) or not str(entry.get("run_id") or "").strip():
            continue
        if mirror_token and str(entry.get(_GATEWAY_MIRROR_TOKEN) or "").strip() != mirror_token:
            continue
        if run_id:
            if entry.get("run_id") == run_id:
                return entry
            continue
        # With no caller-supplied identity, the queue order is authoritative:
        # return the current run-backed projection and let its embedded
        # `(approval_id, run_id)` identify the exact relay owner.
        return entry
    return None


def gateway_pending_mirror(
    session_key: str,
    approval_id: str = "",
    run_id: str = "",
    mirror_token: str = "",
) -> dict | None:
    """Return an exact live run-backed mirror for this session."""
    with _lock:
        reconcile_gateway_pending_mirror_locked(session_key)
        entry = _gateway_pending_mirror_locked(session_key, approval_id, run_id, mirror_token)
        return dict(entry) if entry else None


def gateway_pending_mirrors(session_key: str) -> list[dict]:
    """Return every currently parked run-backed mirror in queue order."""
    with _lock:
        reconcile_gateway_pending_mirror_locked(session_key)
        queue = _pending.get(session_key)
        entries = queue if isinstance(queue, list) else [queue] if queue else []
        return [
            dict(entry)
            for entry in entries
            if _is_gateway_mirror_entry(entry)
            and str(entry.get("run_id") or "").strip()
        ]


def claim_gateway_approval_relay_owner(session_key: str, run_id: str, approval_id: str) -> bool:
    """Claim the single-flight relay owner for one `(session, run)` pair."""
    session_key = str(session_key or "").strip()
    run_id = str(run_id or "").strip()
    approval_id = str(approval_id or "").strip()
    if not session_key or not run_id:
        return False
    with _lock:
        key = (session_key, run_id)
        if key in _gateway_relay_owners:
            return False
        _gateway_relay_owners[key] = approval_id
        return True


def release_gateway_approval_relay_owner(session_key: str, run_id: str, approval_id: str = "") -> None:
    """Release the single-flight relay owner for one `(session, run)` pair."""
    session_key = str(session_key or "").strip()
    run_id = str(run_id or "").strip()
    approval_id = str(approval_id or "").strip()
    if not session_key or not run_id:
        return
    with _lock:
        key = (session_key, run_id)
        current = str(_gateway_relay_owners.get(key) or "").strip()
        if approval_id and current and current != approval_id:
            return
        _gateway_relay_owners.pop(key, None)


def retire_gateway_pending_mirror(
    session_key: str,
    approval_id: str = "",
    run_id: str = "",
    mirror_token: str = "",
) -> bool:
    """Retire one approval, or every mirror for a terminal run."""
    with _lock:
        reconcile_gateway_pending_mirror_locked(session_key)
        queue = _pending.get(session_key)
        entries = queue if isinstance(queue, list) else [queue] if queue else []
        normalized_run_id = str(run_id or "").strip()
        gateway_queue = _gateway_queues.get(session_key) or []
        retained_gateway_queue = gateway_queue
        gateway_queue_changed = False
        if approval_id:
            match = _gateway_pending_mirror_locked(
                session_key,
                approval_id,
                run_id,
                mirror_token,
            )
            if match is None and not normalized_run_id:
                match = next((entry for entry in entries if _is_gateway_mirror_entry(entry)
                              and not str(entry.get("run_id") or "").strip()
                              and str(entry.get("approval_id") or "").strip() == approval_id), None)
            retired = [match] if match else []
        else:
            retired = [
                entry for entry in entries
                if _is_gateway_mirror_entry(entry)
                and str(entry.get("run_id") or "").strip() == normalized_run_id
            ] if normalized_run_id else [
                entry for entry in entries
                if _is_gateway_mirror_entry(entry)
                and not str(entry.get("run_id") or "").strip()
            ]
            if normalized_run_id:
                retained_gateway_queue = []
                for entry in gateway_queue:
                    data = getattr(entry, "data", None) or {}
                    if str(data.get("run_id") or "").strip() == normalized_run_id:
                        gateway_queue_changed = True
                        continue
                    retained_gateway_queue.append(entry)
        if not retired and not gateway_queue_changed:
            head, total, changed = reconcile_gateway_pending_mirror_locked(session_key)
            _approval_sse_notify_locked(session_key, head, total)
            if changed:
                publish_session_list_changed("attention_resolved")
            return changed
        for match in retired:
            entries.remove(match)
        if normalized_run_id and not approval_id:
            if retained_gateway_queue:
                _gateway_queues[session_key] = retained_gateway_queue
            else:
                _gateway_queues.pop(session_key, None)
        if entries:
            _pending[session_key] = entries
        else:
            _pending.pop(session_key, None)
        head, total, _changed = reconcile_gateway_pending_mirror_locked(session_key)
        _approval_sse_notify_locked(session_key, head, total)
    publish_session_list_changed("attention_resolved")
    return True


def _gateway_mirrored_pending_run_id(session_key: str, approval_id: str) -> str | None:
    """Compatibility wrapper for exact run-backed lookup."""
    approval_id = str(approval_id or "").strip()
    if not approval_id:
        return None
    with _lock:
        entry = _gateway_pending_mirror_locked(session_key, approval_id=approval_id)
        if entry:
            return str(entry.get("run_id") or "").strip() or None
    return None


def submit_gateway_pending_mirror(session_key: str, approval: dict) -> tuple[dict | None, int]:
    """Mirror the live gateway head into WebUI polling state under a typed tag.

    Every mirrored entry describes one pending approval to the UI. Run-backed
    mirrors carry ``run_id`` (remote gateway runs) and are bound to the parked
    ``_ApprovalEntry`` via ``approval_id``. No-run mirrors, which represent an
    in-process (legacy) approval parked in ``_gateway_queues``, have no
    ``run_id`` and instead bind back to the live entry through
    ``_GATEWAY_MIRROR_TOKEN``: ``_resolve_approval_legacy()`` matches
    ``pending[_GATEWAY_MIRROR_TOKEN]`` against the live entry's
    ``_webui_mirror_token`` before it will call ``resolve_gateway_pending_local()``
    to unblock the agent thread. A no-run mirror's token is stamped from its
    OWN live producer — resolved via the mirror's ``request_id``/
    ``approval_id`` — and from nothing else. If no live producer's identity
    matches, the mirror is left tokenless (fail closed): guessing ownership
    from "first unclaimed token" or the queue head would let approving THIS
    (possibly stale/foreign) mirror resolve a DIFFERENT live producer than
    the one the user actually saw, which is an approval-integrity violation,
    not a convenience. A tokenless mirror is only wired up automatically when
    there is truly no live producer at all (#7093); ``reconcile_gateway_
    pending_mirror_locked`` binds a fresh, correctly-bound mirror to the
    authoritative live head on its own. Without a token, THIS specific click
    returns ``ok:true`` and the card clears without unblocking any producer —
    the correct producer's own card reappears on the next reconcile.
    """
    with _lock:
        run_id = str(approval.get("run_id") or "").strip()
        approval_id = str(approval.get("approval_id") or "").strip()
        live_gateway_queue = _gateway_queues.get(session_key) or []
        exact_local_entry = next(
            (
                entry for entry in live_gateway_queue
                if getattr(entry, "data", None) is approval
            ),
            None,
        ) if not run_id else None
        if exact_local_entry is None and not run_id and approval_id:
            exact_local_entry = next(
                (
                    entry for entry in live_gateway_queue
                    if str(((getattr(entry, "data", None) or {}).get("approval_id") or "")).strip() == approval_id
                ),
                None,
            )
        if exact_local_entry is None and not run_id:
            # Fall back to matching on the core's per-approval `request_id`.
            # The gateway core notifies WebUI with a COPY of the entry payload
            # (`notify_cb(dict(entry.data))`), so the identity match above
            # (`entry.data is approval`) never holds for a real gateway head,
            # and a local `_ApprovalEntry` carries a `request_id` but no
            # `approval_id`, so the approval_id fallback misses too. The
            # `request_id` is stamped once on the source entry
            # (`_ApprovalEntry.__init__` -> `data.setdefault("request_id", ...)`)
            # and preserved through the copy, so it uniquely reunites the
            # notified copy with its queued entry. Without this, the mirror is
            # created with no token, reconcile keeps the orphan, and
            # `_session_has_pending_approval` stays True after the entry is
            # dropped (the stale-approval-card dead-end, #4948 local variant).
            request_id = str(approval.get("request_id") or "").strip()
            if request_id:
                exact_local_entry = next(
                    (
                        entry for entry in live_gateway_queue
                        if str(((getattr(entry, "data", None) or {}).get("request_id") or "")).strip() == request_id
                    ),
                    None,
                )
        if exact_local_entry is not None:
            mirror_entries = _normalize_pending_queue_locked(session_key)
            entries_to_mirror = [live_gateway_queue[0]] if live_gateway_queue else []
            if exact_local_entry not in entries_to_mirror:
                entries_to_mirror.append(exact_local_entry)
            for entry in entries_to_mirror:
                local_data = entry.data
                token = _gateway_mirror_entry_token(entry)
                entry_approval_id = str(local_data.get("approval_id") or "").strip()
                if entry is exact_local_entry:
                    entry_approval_id = approval_id or entry_approval_id or f"gwlocal:{token}"
                    approval_id = entry_approval_id
                    approval["approval_id"] = entry_approval_id
                elif not entry_approval_id:
                    entry_approval_id = f"gwlocal:{token}"
                local_data["approval_id"] = entry_approval_id
                if not any(
                    _is_gateway_mirror_entry(mirror)
                    and str(mirror.get(_GATEWAY_MIRROR_TOKEN) or "") == token
                    for mirror in mirror_entries
                ):
                    mirror_entry = dict(local_data)
                    mirror_entry["approval_id"] = entry_approval_id
                    mirror_entry[_GATEWAY_MIRROR_FLAG] = True
                    mirror_entry[_GATEWAY_MIRROR_TOKEN] = token
                    mirror_entries.append(mirror_entry)
        if run_id:
            live_head_entry = live_gateway_queue[0] if live_gateway_queue else None
            live_head_data = getattr(live_head_entry, "data", None) or {}
            live_head_run_id = str(live_head_data.get("run_id") or "").strip()
            live_head_approval_id = str(live_head_data.get("approval_id") or "").strip()
            live_token = (
                _gateway_mirror_entry_token(live_head_entry)
                if live_head_entry and live_head_data
                else None
            )
            if (
                live_token
                and live_head_run_id == run_id
                and (
                    not approval_id
                    or not live_head_approval_id
                    or live_head_approval_id == approval_id
                )
            ):
                if approval_id:
                    live_head_data["approval_id"] = approval_id
                else:
                    approval_id = live_head_approval_id
                    if not approval_id:
                        approval_id = f"gwrun:{run_id}:{live_token}"
                        live_head_data["approval_id"] = approval_id
                    approval["approval_id"] = approval_id
            else:
                if not approval_id:
                    approval_id = f"gwrun:{run_id}:{uuid.uuid4().hex}"
                    approval["approval_id"] = approval_id
                mirror_entry = dict(approval)
                mirror_entry["run_id"] = run_id
                mirror_entry["approval_id"] = approval_id
                mirror_entry[_GATEWAY_MIRROR_FLAG] = True
                mirror_entry[_GATEWAY_MIRROR_TOKEN] = uuid.uuid4().hex
                mirror_entry[_GATEWAY_MIRROR_RETAINED] = True
                if not _gateway_pending_mirror_locked(session_key, approval_id=approval_id, run_id=run_id):
                    _normalize_pending_queue_locked(session_key).append(mirror_entry)
        elif not exact_local_entry:
            if not approval_id:
                approval_id = uuid.uuid4().hex
                approval["approval_id"] = approval_id
            queue = _pending.get(session_key)
            entries = queue if isinstance(queue, list) else [queue] if queue else []
            no_run_mirror = next(
                (
                    entry for entry in reversed(entries)
                    if _is_gateway_mirror_entry(entry)
                    and not str(entry.get("run_id") or "").strip()
                    and str(entry.get("approval_id") or "").strip() == approval_id
                ),
                None,
            )
            if no_run_mirror:
                approval["approval_id"] = str(no_run_mirror.get("approval_id") or approval_id).strip()
            elif not _gateway_pending_mirror_locked(session_key, approval_id=approval_id):
                # Stamp the mirror token from the mirror's OWN live producer so
                # the first respond can link this mirror to the right
                # _ApprovalEntry in _gateway_queues. Without it,
                # _resolve_approval_legacy cannot match the no-run mirror to its
                # gateway entry (both token fields are empty) and the agent
                # thread is never unblocked on the first click (#6008 legacy).
                # We MUST NOT blindly take the live head: for a non-head mirror
                # (multiple parked producers, #7093 exact-producer isolation)
                # the head belongs to a sibling, and stamping its token would
                # bind the mirror to the wrong entry. Only an explicit
                # request_id/approval_id match may bind a token.
                #
                # FAIL CLOSED when no producer's identity matches: never infer
                # ownership from "first unclaimed token" or the queue head. A
                # stale/foreign approval (mismatched request_id, or no
                # identity at all) that borrows another live producer's token
                # would let approving THIS mirror resolve a DIFFERENT producer
                # than the one the user actually saw — an approval-integrity
                # violation, not a convenience (found in review of 818fd2fd).
                # A tokenless orphan is only legitimate when there is no live
                # producer at all (#7093); reconcile_gateway_pending_mirror_locked
                # binds a real mirror to the authoritative live head on its own.
                live_queue_for_mirror = _gateway_queues.get(session_key) or []
                request_id_for_mirror = str(approval.get("request_id") or "").strip()
                mirror_producer = None
                if request_id_for_mirror or approval_id:
                    for cand in live_queue_for_mirror:
                        cand_data = getattr(cand, "data", None) or {}
                        if (request_id_for_mirror and
                                str(cand_data.get("request_id") or "").strip() == request_id_for_mirror):
                            mirror_producer = cand
                            break
                        if (approval_id and not request_id_for_mirror and
                                str(cand_data.get("approval_id") or "").strip() == approval_id):
                            mirror_producer = cand
                            break
                mirror_token = (
                    _gateway_mirror_entry_token(mirror_producer)
                    if mirror_producer is not None else None
                )
                mirror_entry = dict(approval)
                mirror_entry["approval_id"] = approval_id
                mirror_entry[_GATEWAY_MIRROR_FLAG] = True
                if mirror_token:
                    mirror_entry[_GATEWAY_MIRROR_TOKEN] = mirror_token
                _normalize_pending_queue_locked(session_key).append(mirror_entry)
        head, total, _changed = reconcile_gateway_pending_mirror_locked(session_key)
        _approval_sse_notify_locked(session_key, head, total)
    publish_session_list_changed("attention_pending")
    return (dict(head) if head else None), total


def resolve_gateway_pending_local(
    session_key: str, approval_id: str, choice: str, reason: str | None = None
) -> tuple[int, dict | None, int]:
    """Resolve the exact parked local entry bound to an approval mirror."""
    target = None
    with _lock:
        approval_id = str(approval_id or "").strip()
        gateway_queue = _gateway_queues.get(session_key) or []
        for index, entry in enumerate(gateway_queue):
            data = getattr(entry, "data", None) or {}
            if str(data.get("approval_id") or "").strip() == approval_id:
                target = gateway_queue.pop(index)
                break
        if gateway_queue:
            _gateway_queues[session_key] = gateway_queue
        else:
            _gateway_queues.pop(session_key, None)
        head, total, _changed = reconcile_gateway_pending_mirror_locked(session_key)
        _approval_sse_notify_locked(session_key, head, total)
    if target is None:
        return 0, head, total
    target.result = choice
    if reason:
        target.reason = reason
    target.event.set()
    publish_session_list_changed("attention_resolved")
    return 1, head, total


def resolve_gateway_pending_local_no_run_mirror(
    session_key: str, approval_id: str, choice: str, reason: str | None = None
) -> tuple[bool, int, dict | None, int]:
    """Resolve an exact no-run mirror only while its parked producer still exists."""
    target = None
    with _lock:
        approval_id = str(approval_id or "").strip()
        queue = _pending.get(session_key)
        entries = queue if isinstance(queue, list) else [queue] if queue else []
        matched_mirror = next(
            (
                entry for entry in entries
                if _is_gateway_mirror_entry(entry)
                and not str(entry.get("run_id") or "").strip()
                and str(entry.get("approval_id") or "").strip() == approval_id
            ),
            None,
        )
        if matched_mirror is None:
            return False, 0, entries[0] if entries else None, len(entries)

        gateway_queue = _gateway_queues.get(session_key) or []
        for index, entry in enumerate(gateway_queue):
            data = getattr(entry, "data", None) or {}
            if str(data.get("approval_id") or "").strip() == approval_id:
                target = gateway_queue.pop(index)
                break
        if target is None:
            matched_mirror[_GATEWAY_MIRROR_RETAINED] = True
            return True, 0, entries[0] if entries else None, len(entries)

        if gateway_queue:
            _gateway_queues[session_key] = gateway_queue
        else:
            _gateway_queues.pop(session_key, None)
        entries.remove(matched_mirror)
        if entries:
            _pending[session_key] = entries
        else:
            _pending.pop(session_key, None)
        head, total, _changed = reconcile_gateway_pending_mirror_locked(session_key)
        _approval_sse_notify_locked(session_key, head, total)
    target.result = choice
    if reason:
        target.reason = reason
    target.event.set()
    publish_session_list_changed("attention_resolved")
    return True, 1, head, total


def resolve_gateway_pending_local_all(
    session_key: str,
    choice: str,
    reason: str | None = None,
) -> tuple[int, dict | None, int]:
    """Resolve every parked local/no-run approval without touching remote runs."""
    targets = []
    removed_pending = False
    with _lock:
        reconcile_gateway_pending_mirror_locked(session_key)

        gateway_queue = _gateway_queues.get(session_key) or []
        retained_gateway_queue = []
        for entry in gateway_queue:
            data = getattr(entry, "data", None) or {}
            if str(data.get("run_id") or "").strip():
                retained_gateway_queue.append(entry)
            else:
                targets.append(entry)
        if retained_gateway_queue:
            _gateway_queues[session_key] = retained_gateway_queue
        else:
            _gateway_queues.pop(session_key, None)

        queue = _pending.get(session_key)
        entries = queue if isinstance(queue, list) else [queue] if queue else []
        retained_pending = [
            entry
            for entry in entries
            if _is_gateway_mirror_entry(entry)
            and str(entry.get("run_id") or "").strip()
        ]
        removed_pending = len(retained_pending) != len(entries)
        if retained_pending:
            _pending[session_key] = retained_pending
        else:
            _pending.pop(session_key, None)

        head, total, _changed = reconcile_gateway_pending_mirror_locked(session_key)
        _approval_sse_notify_locked(session_key, head, total)

    for entry in targets:
        entry.result = choice
        if reason:
            entry.reason = reason
        entry.event.set()
    if targets or removed_pending:
        publish_session_list_changed("attention_resolved")
    return len(targets), head, total


def settle_gateway_pending_local_notification(
    session_key: str,
    approval: dict,
) -> tuple[bool, dict | None, int]:
    """Auto-resolve or publish one local approval at the YOLO handoff boundary.

    The Agent adds its blocking entry before invoking WebUI's notify callback.
    Serialize that callback with session YOLO commit/disable so a waiter arriving
    after a drain snapshot cannot be parked behind an already-committed enable.
    Run-backed approvals stay on the Runs API path and are never resolved here.
    """
    with gateway_yolo_handoff(session_key):
        run_id = str((approval or {}).get("run_id") or "").strip()
        if not run_id and is_session_yolo_enabled(session_key):
            _resolved, head, total = resolve_gateway_pending_local_all(
                session_key,
                "once",
            )
            return True, head, total
        head, total = submit_gateway_pending_mirror(session_key, approval)
        return False, head, total


def submit_pending(session_key: str, approval: dict) -> None:
    """Append a pending approval to the per-session queue.

    Wraps the agent's submit_pending to:
    - Add a stable approval_id (uuid4 hex) so the respond endpoint can target
      a specific entry even when multiple approvals are queued simultaneously.
    - Change the storage from a single overwriting dict value to a list, so
      parallel tool calls each get their own approval slot (fixes #527).
    - Notify any connected SSE subscribers immediately.
    """
    entry = dict(approval)
    entry.setdefault("approval_id", uuid.uuid4().hex)
    with _lock:
        queue_list = _normalize_pending_queue_locked(session_key)
        queue_list.append(entry)
        total = len(queue_list)
        head = queue_list[0]  # /api/approval/pending always returns head
        # Push to SSE subscribers from inside _lock so two parallel
        # submit_pending calls can't deliver out-of-order (T2's later
        # notify arriving before T1's earlier notify with a stale count).
        _approval_sse_notify_locked(session_key, head, total)
    publish_session_list_changed("attention_pending")
    # NOTE: We do NOT call _submit_pending_raw here — that function overwrites
    # _pending[session_key] with a single dict, which would undo the list we just
    # built. The gateway blocking path uses _gateway_queues (a separate mechanism
    # managed by check_all_command_guards / register_gateway_notify), which is
    # unaffected by _pending. The _pending dict is only used for UI polling.


def _session_attention_summary(session_id: str) -> dict | None:
    """Return sidebar attention metadata for pending approval/clarify work."""
    approval_count = 0
    with _lock:
        reconcile_gateway_pending_mirror_locked(session_id)
        queue_list = _pending.get(session_id)
        if isinstance(queue_list, list):
            approval_count = len(queue_list)
        elif queue_list:
            approval_count = 1
    if approval_count > 0:
        return {
            "kind": "approval",
            "count": approval_count,
            "severity": "critical",
        }

    clarify_count = int(get_clarify_pending_count(session_id) or 0)
    if clarify_count > 0:
        return {
            "kind": "clarify",
            "count": clarify_count,
            "severity": "question",
        }
    return None


def _handle_approval_pending(handler, parsed):
    sid = parse_qs(parsed.query).get("session_id", [""])[0]
    with _lock:
        _head, _total, _changed = reconcile_gateway_pending_mirror_locked(sid)
        queue = _pending.get(sid)
        # Support both the new list format and a legacy single-dict value.
        if isinstance(queue, list):
            p = queue[0] if queue else None
            total = len(queue)
        elif queue:
            p = queue
            total = 1
        else:
            p = None
            total = 0
        if p is None:
            gw_queue = _gateway_queues.get(sid) or []
            if gw_queue:
                raw = getattr(gw_queue[0], "data", None) or {}
                if raw:
                    p = raw
                    total = len(gw_queue)
                else:
                    logger.warning("Gateway queue entry for %s has no .data attribute", sid)
    if p:
        return j(handler, {"pending": dict(p), "pending_count": total})
    return j(handler, {"pending": None, "pending_count": 0})


def _handle_approval_sse_stream(handler, parsed):
    """SSE endpoint for real-time approval notifications.

    Long-lived connection that pushes approval events the moment they arrive,
    replacing the 1.5s polling loop.  The frontend uses EventSource and falls
    back to HTTP polling if the connection fails.
    """
    sid = parse_qs(parsed.query).get("session_id", [""])[0]
    if not sid:
        return bad(handler, "session_id is required")

    q = queue.Queue(maxsize=16)
    initial_pending = None
    initial_count = 0
    with _lock:
        _approval_sse_subscribers.setdefault(sid, []).append(q)
        reconcile_gateway_pending_mirror_locked(sid)
        q_list = _pending.get(sid)
        if isinstance(q_list, list):
            initial_pending = dict(q_list[0]) if q_list else None
            initial_count = len(q_list)
        elif q_list:
            initial_pending = dict(q_list)
            initial_count = 1

    handler.send_response(200)
    handler.send_header('Content-Type', 'text/event-stream; charset=utf-8')
    handler.send_header('Cache-Control', 'no-cache')
    handler.send_header('X-Accel-Buffering', 'no')
    handler.send_header('Connection', 'close')
    end_sse_headers(handler)
    _sse_set_write_deadline(handler)

    _sse(handler, 'initial', {"pending": initial_pending, "pending_count": initial_count})

    try:
        while True:
            try:
                payload = q.get(timeout=_SSE_HEARTBEAT_INTERVAL_SECONDS)
            except queue.Empty:
                handler.wfile.write(b': keepalive\n\n')
                handler.wfile.flush()
                continue
            if payload is None:
                break
            _sse(handler, 'approval', payload)
    except _CLIENT_DISCONNECT_ERRORS:
        pass
    finally:
        _approval_sse_unsubscribe(sid, q)


def _handle_approval_inject(handler, parsed):
    """Inject a fake pending approval -- loopback-only, used by automated tests."""
    qs = parse_qs(parsed.query)
    sid = qs.get("session_id", [""])[0]
    key = qs.get("pattern_key", ["test_pattern"])[0]
    cmd = qs.get("command", ["rm -rf /tmp/test"])[0]
    if sid:
        submit_pending(
            sid,
            {
                "command": cmd,
                "pattern_key": key,
                "pattern_keys": [key],
                "description": "test pattern",
            },
        )
        return j(handler, {"ok": True, "session_id": sid})
    return j(handler, {"error": "session_id required"}, status=400)


def _handle_clarify_pending(handler, parsed):
    sid = parse_qs(parsed.query).get("session_id", [""])[0]
    pending = get_clarify_pending(sid)
    if pending:
        return j(handler, {"pending": pending})
    return j(handler, {"pending": None})


def _handle_clarify_sse_stream(handler, parsed):
    """SSE endpoint for real-time clarify notifications.

    Long-lived connection that pushes clarify events the moment they arrive,
    replacing the 1.5s polling loop.  The frontend uses EventSource and falls
    back to HTTP polling if the connection fails.
    """
    if clarify_sse_subscribe is None:
        return bad(handler, "clarify SSE not available")

    sid = parse_qs(parsed.query).get("session_id", [""])[0]
    if not sid:
        return bad(handler, "session_id is required")

    from api.clarify import (
        _lock as _clarify_lock,
        _clarify_sse_subscribers as _clarify_subs,
        _gateway_queues as _clarify_gateway_queues,
        _pending as _clarify_pending,
    )
    q = queue.Queue(maxsize=16)
    initial_pending = None
    initial_count = 0
    with _clarify_lock:
        _clarify_subs.setdefault(sid, []).append(q)
        gw_q = _clarify_gateway_queues.get(sid) or []
        if gw_q:
            initial_pending = dict(gw_q[0].data)
            initial_count = len(gw_q)
        else:
            _legacy = _clarify_pending.get(sid)
            if _legacy:
                initial_pending = dict(_legacy)
                initial_count = 1

    handler.send_response(200)
    handler.send_header('Content-Type', 'text/event-stream; charset=utf-8')
    handler.send_header('Cache-Control', 'no-cache')
    handler.send_header('X-Accel-Buffering', 'no')
    handler.send_header('Connection', 'close')
    end_sse_headers(handler)
    _sse_set_write_deadline(handler)

    _sse(handler, 'initial', {"pending": initial_pending, "pending_count": initial_count})

    try:
        while True:
            try:
                payload = q.get(timeout=_SSE_HEARTBEAT_INTERVAL_SECONDS)
            except queue.Empty:
                handler.wfile.write(b': keepalive\n\n')
                handler.wfile.flush()
                continue
            if payload is None:
                break
            _sse(handler, 'clarify', payload)
    except _CLIENT_DISCONNECT_ERRORS:
        pass
    finally:
        clarify_sse_unsubscribe(sid, q)


def _handle_clarify_inject(handler, parsed):
    """Inject a fake pending clarify prompt -- loopback-only, used by automated tests."""
    qs = parse_qs(parsed.query)
    sid = qs.get("session_id", [""])[0]
    question = qs.get("question", ["Which option?"])[0]
    choices = qs.get("choices", [])
    if sid:
        submit_clarify_pending(
            sid,
            {
                "question": question,
                "choices_offered": choices,
                "session_id": sid,
                "kind": "clarify",
            },
        )
        return j(handler, {"ok": True, "session_id": sid})
    return j(handler, {"error": "session_id required"}, status=400)


def _resolve_approval_legacy(sid: str, approval_id: str, choice: str, run_id: str = "") -> bool:
    """Resolve an approval through the existing callback path."""
    pending = None
    found_target = False
    gateway_keys = []
    local_gateway_approval_id = ""
    gateway_head_matches_target = False
    with _lock:
        reconcile_gateway_pending_mirror_locked(sid)
        queue = _pending.get(sid)
        if isinstance(queue, list):
            if approval_id:
                preferred_index = None
                fallback_index = None
                for i, entry in enumerate(queue):
                    if entry.get("approval_id") != approval_id:
                        continue
                    if run_id and str(entry.get("run_id") or "").strip() != run_id:
                        continue
                    if not entry.get(_GATEWAY_MIRROR_FLAG) or not str(entry.get("run_id") or "").strip():
                        preferred_index = i
                        break
                    if fallback_index is None:
                        fallback_index = i
                match_index = preferred_index if preferred_index is not None else fallback_index
                if match_index is not None:
                    pending = queue.pop(match_index)
                    found_target = True
                else:
                    pending = None
            else:
                pending = queue.pop(0) if queue else None
                found_target = pending is not None
            if not queue:
                _pending.pop(sid, None)
        elif queue:
            if (
                not approval_id
                or (
                    queue.get("approval_id") == approval_id
                    and (not run_id or str(queue.get("run_id") or "").strip() == run_id)
                )
            ):
                pending = _pending.pop(sid, None)
                found_target = pending is not None
        if not pending and not approval_id:
            gw_queue = _gateway_queues.get(sid)
            if gw_queue and len(gw_queue) > 0:
                gw_entry = gw_queue[0]
                gw_data = getattr(gw_entry, 'data', None) or {}
                gateway_keys = gw_data.get("pattern_keys") or [gw_data.get("pattern_key", "")]
                found_target = True
        elif approval_id:
            gw_queue = _gateway_queues.get(sid)
            if gw_queue and len(gw_queue) > 0:
                gw_entry = gw_queue[0]
                gw_data = getattr(gw_entry, "data", None) or {}
                gw_approval_id = str(gw_data.get("approval_id") or "").strip()
                gw_run_id = str(gw_data.get("run_id") or "").strip()
                gateway_head_matches_target = bool(
                    run_id and gw_approval_id == approval_id and gw_run_id == run_id
                )
                if gw_approval_id == approval_id and (not run_id or gw_run_id == run_id):
                    local_gateway_approval_id = approval_id
                elif not run_id and found_target and pending:
                    pending_token = str(pending.get(_GATEWAY_MIRROR_TOKEN) or "").strip()
                    matched_data = None
                    for _cand in gw_queue:
                        _cand_data = getattr(_cand, "data", None) or {}
                        _cand_token = str(_cand_data.get("_webui_mirror_token") or "").strip()
                        if pending_token and _cand_token == pending_token:
                            matched_data = _cand_data
                            break
                        if (str(_cand_data.get("approval_id") or "").strip() == approval_id
                                and not str(_cand_data.get("run_id") or "").strip()):
                            matched_data = _cand_data
                            break
                    if matched_data is not None:
                        matched_data["approval_id"] = approval_id
                        local_gateway_approval_id = approval_id
        if not local_gateway_approval_id:
            if isinstance(_pending.get(sid), list) and _pending[sid]:
                _approval_sse_notify_locked(sid, _pending[sid][0], len(_pending[sid]))
            else:
                _approval_sse_notify_locked(sid, None, 0)

    keys_from_pending = pending.get("pattern_keys") or [pending.get("pattern_key", "")] if pending else []
    all_keys = [k for k in keys_from_pending if k] + [k for k in gateway_keys if k]
    if choice == "session":
        for k in all_keys:
            approve_session(sid, k)
    elif choice == "always":
        for k in all_keys:
            approve_session(sid, k)
            approve_permanent(k)
        save_permanent_allowlist(_permanent_approved)

    gateway_resolved = 0
    local_gateway_resolved = 0
    if approval_id and found_target and not run_id and local_gateway_approval_id:
        local_gateway_resolved, _head, _total = resolve_gateway_pending_local(
            sid, local_gateway_approval_id, choice
        )
    elif approval_id and found_target and run_id and gateway_head_matches_target:
        gateway_resolved = resolve_gateway_approval(sid, choice, resolve_all=False) or 0
    elif not approval_id:
        gateway_resolved = resolve_gateway_approval(sid, choice, resolve_all=False) or 0

    resolved = bool(pending) or bool(gateway_resolved) or bool(local_gateway_resolved) or not bool(approval_id)
    if resolved:
        publish_session_list_changed("attention_resolved")
    return resolved


_GATEWAY_APPROVAL_RELAY_UNAVAILABLE = (
    "Gateway approval could not be relayed because the active run is unavailable. "
    "Reopen the session or retry after it reconnects."
)
_GATEWAY_APPROVAL_RELAY_IN_PROGRESS = (
    "Another approval response for this Gateway run is already in progress. "
    "Wait for it to finish, then retry if the card is still visible."
)


def _gateway_approval_failure(
    sid: str,
    choice: str,
    *,
    code: str,
    error: str,
    status: int,
    enable_yolo: bool,
    relayed: bool = False,
) -> tuple[dict, int]:
    """Build a failed relay response with authoritative session-YOLO state."""
    payload = {
        "ok": False,
        "choice": choice,
        "relayed": relayed,
        "code": code,
        "error": error,
    }
    if enable_yolo:
        payload["yolo_enabled"] = bool(is_session_yolo_enabled(sid))
    return payload, status


def _relay_gateway_run_approval(
    sid: str,
    mirror: dict,
    choice: str,
    *,
    enable_yolo: bool,
) -> tuple[dict, int]:
    """Relay one exact run-backed mirror under the shared `(session, run)` owner."""
    from api.config import gateway_supports_approval_identity_v1, get_config as _get_config
    from api.gateway_chat import _gateway_api_key, _gateway_base_url
    from api.runner_client import HttpRunnerClient, RunnerClientError

    run_id = str(mirror.get("run_id") or "").strip()
    approval_id = str(mirror.get("approval_id") or "").strip()
    mirror_token = str(mirror.get(_GATEWAY_MIRROR_TOKEN) or "").strip()
    if not run_id or not approval_id:
        return _gateway_approval_failure(
            sid,
            choice,
            code="gateway_run_unavailable",
            error=_GATEWAY_APPROVAL_RELAY_UNAVAILABLE,
            status=409,
            enable_yolo=enable_yolo,
        )
    if not claim_gateway_approval_relay_owner(sid, run_id, approval_id):
        return _gateway_approval_failure(
            sid,
            choice,
            code="gateway_approval_in_progress",
            error=_GATEWAY_APPROVAL_RELAY_IN_PROGRESS,
            status=409,
            enable_yolo=enable_yolo,
        )

    try:
        current_mirror = gateway_pending_mirror(
            sid,
            approval_id=approval_id,
            run_id=run_id,
            mirror_token=mirror_token,
        )
        if not current_mirror:
            return _gateway_approval_failure(
                sid,
                choice,
                code="gateway_run_unavailable",
                error=_GATEWAY_APPROVAL_RELAY_UNAVAILABLE,
                status=409,
                enable_yolo=enable_yolo,
            )

        base_url = _gateway_base_url(_get_config())
        api_key = _gateway_api_key()
        identity_v1 = bool(current_mirror.get(_GATEWAY_AGENT_IDENTITY_V1)) and (
            gateway_supports_approval_identity_v1(base_url, api_key)
        )
        if not identity_v1:
            run_head = gateway_pending_mirror(sid, run_id=run_id)
            if not run_head or str(run_head.get("approval_id") or "").strip() != approval_id:
                return _gateway_approval_failure(
                    sid,
                    choice,
                    code="gateway_run_unavailable",
                    error=_GATEWAY_APPROVAL_RELAY_UNAVAILABLE,
                    status=409,
                    enable_yolo=enable_yolo,
                )

        yolo_transition = begin_session_yolo_transition(sid) if enable_yolo else None
        relay_error = None
        relay_succeeded = False
        try:
            HttpRunnerClient(base_url=base_url, api_key=api_key).respond_approval(
                run_id,
                approval_id if identity_v1 else "",
                choice,
            )
            relay_succeeded = True
        except (RunnerClientError, ValueError) as exc:
            relay_error = str(exc)
        finally:
            finish_session_yolo_transition(sid, yolo_transition, succeeded=relay_succeeded)

        if relay_error is not None:
            return _gateway_approval_failure(
                sid,
                choice,
                code="gateway_approval_relay_failed",
                error=relay_error,
                status=502,
                enable_yolo=enable_yolo,
                relayed=True,
            )

        _resolve_approval_legacy(sid, approval_id, choice, run_id=run_id)
        retire_gateway_pending_mirror(
            sid,
            approval_id=approval_id,
            run_id=run_id,
            mirror_token=mirror_token,
        )
        return {
            "ok": True,
            "choice": choice,
            "relayed": True,
            **(
                {"yolo_enabled": bool(is_session_yolo_enabled(sid))}
                if enable_yolo
                else {}
            ),
        }, 200
    finally:
        release_gateway_approval_relay_owner(sid, run_id, approval_id)


def _pending_approval_owner_state(
    sid: str,
    approval_id: str,
    run_id: str = "",
    mirror_token: str = "",
) -> tuple[bool, bool]:
    """Return `(exact_owner_exists, any_pending_exists)` under queue authority."""
    approval_id = str(approval_id or "").strip()
    run_id = str(run_id or "").strip()
    mirror_token = str(mirror_token or "").strip()
    with _lock:
        reconcile_gateway_pending_mirror_locked(sid)
        queue = _pending.get(sid)
        entries = queue if isinstance(queue, list) else [queue] if queue else []
        exact = False
        for entry in entries:
            if not isinstance(entry, dict) or str(entry.get("approval_id") or "") != approval_id:
                continue
            entry_run_id = str(entry.get("run_id") or "").strip()
            entry_mirror_token = str(entry.get(_GATEWAY_MIRROR_TOKEN) or "").strip()
            if run_id and entry_run_id != run_id:
                continue
            if mirror_token and entry_mirror_token != mirror_token:
                continue
            if (run_id or mirror_token) and not entry.get(_GATEWAY_MIRROR_FLAG):
                continue
            exact = True
            break
        return exact, bool(entries or _gateway_queues.get(sid))


def _enable_session_yolo_and_release_pending(
    sid: str,
    *,
    choice: str,
    approval_id: str = "",
    run_id: str = "",
    mirror_token: str = "",
    include_choice: bool = False,
) -> tuple[dict, int]:
    """Relay every parked remote approval, drain local waiters, then commit YOLO."""
    approval_id = str(approval_id or "").strip()
    run_id = str(run_id or "").strip()
    mirror_token = str(mirror_token or "").strip()
    has_exact_remote_owner = bool(run_id or mirror_token)
    if has_exact_remote_owner and (not approval_id or not run_id or not mirror_token):
        return _gateway_approval_failure(
            sid,
            choice,
            code="gateway_run_unavailable",
            error=_GATEWAY_APPROVAL_RELAY_UNAVAILABLE,
            status=409,
            enable_yolo=True,
        )

    yolo_transition = None
    try:
        with gateway_yolo_handoff(sid):
            yolo_transition = begin_session_yolo_transition(sid)
            stale_cleared = False
            if approval_id:
                exact_owner, any_pending = _pending_approval_owner_state(
                    sid,
                    approval_id,
                    run_id,
                    mirror_token,
                )
                if not exact_owner and (has_exact_remote_owner or any_pending):
                    return _gateway_approval_failure(
                        sid,
                        choice,
                        code="gateway_run_unavailable",
                        error=_GATEWAY_APPROVAL_RELAY_UNAVAILABLE,
                        status=409,
                        enable_yolo=True,
                    )
                stale_cleared = not exact_owner

            run_mirrors = gateway_pending_mirrors(sid)
            relayed = 0
            for mirror in run_mirrors:
                relay_payload, relay_status = _relay_gateway_run_approval(
                    sid,
                    mirror,
                    choice,
                    enable_yolo=False,
                )
                if relay_status != 200 or not relay_payload.get("ok"):
                    finish_session_yolo_transition(
                        sid,
                        yolo_transition,
                        succeeded=False,
                    )
                    yolo_transition = None
                    return {
                        **relay_payload,
                        "yolo_enabled": bool(is_session_yolo_enabled(sid)),
                    }, relay_status
                relayed += 1

            resolve_gateway_pending_local_all(
                sid,
                choice,
            )
            finish_session_yolo_transition(sid, yolo_transition, succeeded=True)
            yolo_transition = None
            return {
                "ok": True,
                "yolo_enabled": bool(is_session_yolo_enabled(sid)),
                **({"choice": choice} if include_choice or relayed else {}),
                **({"relayed": True} if relayed else {}),
                **({"stale_cleared": True} if stale_cleared else {}),
            }, 200
    finally:
        if yolo_transition is not None:
            finish_session_yolo_transition(sid, yolo_transition, succeeded=False)


def _gateway_pending_approval_without_run_id(sid: str, approval_id: str) -> bool:
    with _lock:
        reconcile_gateway_pending_mirror_locked(sid)
        queue = _pending.get(sid)
        if isinstance(queue, list):
            entries = queue
        elif queue:
            entries = [queue]
        else:
            entries = []
        if approval_id:
            for entry in entries:
                if isinstance(entry, dict) and entry.get("approval_id") == approval_id:
                    return bool(entry.get(_GATEWAY_MIRROR_FLAG)) and not str(entry.get("run_id") or "").strip()
            return False
        if not entries or not isinstance(entries[0], dict):
            return False
        return bool(entries[0].get(_GATEWAY_MIRROR_FLAG)) and not str(entries[0].get("run_id") or "").strip()


def _session_has_pending_approval(sid: str) -> bool:
    """True when the session still has any live pending approval to act on."""
    with _lock:
        reconcile_gateway_pending_mirror_locked(sid)
        queue = _pending.get(sid)
        if isinstance(queue, list):
            if queue:
                return True
        elif queue:
            return True
        gw_queue = _gateway_queues.get(sid)
        return bool(gw_queue)


def _handle_approval_respond(handler, body):
    sid = body.get("session_id", "")
    if not sid:
        return bad(handler, "session_id is required")
    choice = body.get("choice", "deny")
    if choice not in ("once", "session", "always", "deny"):
        return bad(handler, f"Invalid choice: {choice}")
    approval_id = body.get("approval_id", "")
    enable_yolo = body.get("yolo") is True
    requested_run_id = str(body.get("run_id") or "").strip()
    requested_mirror_token = str(body.get("mirror_token") or "").strip()

    if enable_yolo:
        payload, status = _enable_session_yolo_and_release_pending(
            sid,
            choice=choice,
            approval_id=approval_id,
            run_id=requested_run_id,
            mirror_token=requested_mirror_token,
            include_choice=True,
        )
        return j(handler, payload, status=status)

    if requested_run_id or requested_mirror_token:
        if not approval_id or not requested_run_id or not requested_mirror_token:
            relay_payload, relay_status = _gateway_approval_failure(
                sid,
                choice,
                code="gateway_run_unavailable",
                error=_GATEWAY_APPROVAL_RELAY_UNAVAILABLE,
                status=409,
                enable_yolo=False,
            )
            return j(handler, relay_payload, status=relay_status)
        exact_mirror = gateway_pending_mirror(
            sid,
            approval_id=approval_id,
            run_id=requested_run_id,
            mirror_token=requested_mirror_token,
        )
        if exact_mirror is None:
            relay_payload, relay_status = _gateway_approval_failure(
                sid,
                choice,
                code="gateway_run_unavailable",
                error=_GATEWAY_APPROVAL_RELAY_UNAVAILABLE,
                status=409,
                enable_yolo=False,
            )
            return j(handler, relay_payload, status=relay_status)
        relay_payload, relay_status = _relay_gateway_run_approval(
            sid,
            exact_mirror,
            choice,
            enable_yolo=False,
        )
        return j(handler, relay_payload, status=relay_status)

    try:
        from api.gateway_chat import (
            _STREAM_RUN_IDS,
            webui_gateway_chat_enabled,
        )
        from api.config import get_config as _get_config
        from api.models import get_session
        s = get_session(sid)
        _candidate_run_id = None
        if s is not None:
            active_sid = getattr(s, "active_stream_id", None)
            if active_sid:
                _candidate_run_id = _STREAM_RUN_IDS.get(active_sid)
        local_match = False
        run_backed_gateway_matches = 0
        same_run_stale_without_token = False
        with _lock:
            queue = _pending.get(sid)
            entries = queue if isinstance(queue, list) else [queue] if queue else []
            if approval_id:
                local_match = any(
                    isinstance(entry, dict)
                    and entry.get("approval_id") == approval_id
                    and (
                        not entry.get(_GATEWAY_MIRROR_FLAG)
                        or not str(entry.get("run_id") or "").strip()
                    )
                    for entry in entries
                )
                run_backed_gateway_matches = sum(
                    1
                    for entry in entries
                    if isinstance(entry, dict)
                    and entry.get("approval_id") == approval_id
                    and entry.get(_GATEWAY_MIRROR_FLAG)
                    and str(entry.get("run_id") or "").strip()
                )
                gateway_queue = _gateway_queues.get(sid) or []
                live_head_data = getattr(gateway_queue[0], "data", None) or {} if gateway_queue else {}
                live_head_run_id = str(live_head_data.get("run_id") or "").strip()
                live_head_token = (
                    _gateway_mirror_entry_token(gateway_queue[0])
                    if gateway_queue and live_head_data
                    else None
                )
                live_head_approval_id = str(live_head_data.get("approval_id") or "").strip()
                if not live_head_approval_id and live_head_token and live_head_run_id:
                    live_head_approval_id = f"gwrun:{live_head_run_id}:{live_head_token}"
                stale_same_run_id = _candidate_run_id or live_head_run_id
                if (
                    stale_same_run_id
                    and live_head_run_id == stale_same_run_id
                    and live_head_approval_id
                    and live_head_approval_id != approval_id
                ):
                    same_run_stale_without_token = any(
                        isinstance(entry, dict)
                        and entry.get("approval_id") == approval_id
                        and entry.get(_GATEWAY_MIRROR_FLAG)
                        and str(entry.get("run_id") or "").strip() == stale_same_run_id
                        and not str(entry.get(_GATEWAY_MIRROR_TOKEN) or "").strip()
                        for entry in entries
                    )
            else:
                local_match = any(
                    isinstance(entry, dict)
                    and (
                        not entry.get(_GATEWAY_MIRROR_FLAG)
                        or not str(entry.get("run_id") or "").strip()
                    )
                    for entry in entries
                )
        if local_match:
            _candidate_run_id = None
        if approval_id and not local_match and same_run_stale_without_token:
            relay_payload, relay_status = _gateway_approval_failure(
                sid,
                choice,
                code="gateway_run_unavailable",
                error=_GATEWAY_APPROVAL_RELAY_UNAVAILABLE,
                status=409,
                enable_yolo=enable_yolo,
            )
            return j(handler, relay_payload, status=relay_status)
        matched_mirror = (
            gateway_pending_mirror(sid, approval_id=approval_id, run_id=_candidate_run_id)
            if approval_id and not local_match
            else None
        )
        _run_id = matched_mirror["run_id"] if matched_mirror else None
        if not matched_mirror and approval_id:
            if local_match:
                _candidate_run_id = None
            elif run_backed_gateway_matches > 1 and not _candidate_run_id:
                relay_payload, relay_status = _gateway_approval_failure(
                    sid,
                    choice,
                    code="gateway_run_unavailable",
                    error=_GATEWAY_APPROVAL_RELAY_UNAVAILABLE,
                    status=409,
                    enable_yolo=enable_yolo,
                )
                return j(handler, relay_payload, status=relay_status)
        if _run_id:
            if enable_yolo:
                with gateway_yolo_handoff(sid):
                    current_mirror = gateway_pending_mirror(
                        sid,
                        approval_id=approval_id,
                        run_id=_run_id,
                    )
                    if current_mirror is None:
                        relay_payload, relay_status = _gateway_approval_failure(
                            sid,
                            choice,
                            code="gateway_run_unavailable",
                            error=_GATEWAY_APPROVAL_RELAY_UNAVAILABLE,
                            status=409,
                            enable_yolo=True,
                        )
                    else:
                        relay_payload, relay_status = _relay_gateway_run_approval(
                            sid,
                            current_mirror,
                            choice,
                            enable_yolo=True,
                        )
            else:
                relay_payload, relay_status = _relay_gateway_run_approval(
                    sid,
                    matched_mirror or {},
                    choice,
                    enable_yolo=False,
                )
            return j(handler, relay_payload, status=relay_status)
        if _candidate_run_id:
            relay_payload, relay_status = _gateway_approval_failure(
                sid,
                choice,
                code="gateway_run_unavailable",
                error=_GATEWAY_APPROVAL_RELAY_UNAVAILABLE,
                status=409,
                enable_yolo=enable_yolo,
            )
            return j(handler, relay_payload, status=relay_status)
        if webui_gateway_chat_enabled(_get_config()):
            handled_no_run_mirror, resolved_count, _, _ = resolve_gateway_pending_local_no_run_mirror(
                sid, approval_id, choice
            )
            if handled_no_run_mirror and resolved_count == 1:
                if enable_yolo:
                    set_session_yolo_enabled(sid, True)
                return j(handler, {
                    "ok": True,
                    "choice": choice,
                    "local_retired": True,
                    **(
                        {"yolo_enabled": bool(is_session_yolo_enabled(sid))}
                        if enable_yolo
                        else {}
                    ),
                })
            if handled_no_run_mirror:
                relay_payload, relay_status = _gateway_approval_failure(
                    sid,
                    choice,
                    code="gateway_run_unavailable",
                    error=_GATEWAY_APPROVAL_RELAY_UNAVAILABLE,
                    status=409,
                    enable_yolo=enable_yolo,
                )
                return j(handler, relay_payload, status=relay_status)
    except Exception:
        logger.warning("Silent exception in _handle_approval_respond", exc_info=True)
        pass

    from api.runtime_adapter import LegacyJournalRuntimeAdapter, runtime_adapter_enabled

    if runtime_adapter_enabled():
        adapter = LegacyJournalRuntimeAdapter(approval_delegate=_resolve_approval_legacy)
        ok = adapter.respond_approval(sid, approval_id, choice).accepted
    else:
        ok = _resolve_approval_legacy(sid, approval_id, choice)
    if not ok and not _session_has_pending_approval(sid):
        if enable_yolo:
            set_session_yolo_enabled(sid, True)
        return j(handler, {
            "ok": True,
            "choice": choice,
            "stale_cleared": True,
            **(
                {"yolo_enabled": bool(is_session_yolo_enabled(sid))}
                if enable_yolo
                else {}
            ),
        })
    if ok and enable_yolo:
        set_session_yolo_enabled(sid, True)
    return j(handler, {
        "ok": ok,
        "choice": choice,
        **(
            {"yolo_enabled": bool(is_session_yolo_enabled(sid))}
            if ok and enable_yolo
            else {}
        ),
    })


def _resolve_clarify_legacy(sid: str, clarify_id: str, response: str) -> bool:
    """Resolve clarify through the existing callback path without new state."""
    if clarify_id:
        from api.clarify import resolve_clarify_by_id
        return resolve_clarify_by_id(sid, clarify_id, response)
    resolved = resolve_clarify(sid, response, resolve_all=False)
    return bool(resolved)


def _handle_clarify_respond(handler, body):
    sid = body.get("session_id", "")
    if not sid:
        return bad(handler, "session_id is required")
    response = body.get("response")
    if response is None:
        response = body.get("answer")
    if response is None:
        response = body.get("choice")
    response = str(response or "").strip()
    if not response:
        return bad(handler, "response is required")
    clarify_id = body.get("clarify_id", "")

    from api.runtime_adapter import LegacyJournalRuntimeAdapter, runtime_adapter_enabled

    if runtime_adapter_enabled():
        adapter = LegacyJournalRuntimeAdapter(clarify_delegate=_resolve_clarify_legacy)
        ok = adapter.respond_clarify(sid, clarify_id, response).accepted
    else:
        ok = _resolve_clarify_legacy(sid, clarify_id, response)

    if not ok:
        return j(handler, {
            "ok": False,
            "error": "Clarification prompt expired or not found. The agent may have already proceeded.",
            "stale": True,
        }, status=409)

    return j(handler, {"ok": True, "response": response})
