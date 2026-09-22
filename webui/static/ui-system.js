/**
 * ui-system.js — System State: Reconnect Banner, Inflight, Todo, Health Monitors, Update Banner
 *
 * Extracted from ui.js (Sprint F-M4+, ADR 016).
 */

// ── Reconnect banner (B4/B5: reload resilience) ──
const INFLIGHT_KEY = 'agy-webui-inflight'; // localStorage key for in-flight session tracking
const INFLIGHT_STATE_KEY = 'agy-webui-inflight-state'; // localStorage snapshots for mid-stream reload recovery
const INFLIGHT_STATE_DEFAULT_LIMITS = {
  maxSessions:8,
  messages:24,
  toolCalls:48,
  stringChars:60000,
  jsonChars:1500000,
};

function _boundedInflightInt(value, fallback, min, max){
  const n=parseInt(value,10);
  if(!Number.isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, n));
}
function _getInflightStateLimits(){
  const configured=(typeof window!=='undefined'&&window._inflightStateLimits&&typeof window._inflightStateLimits==='object')?window._inflightStateLimits:{};
  return {
    maxSessions:_boundedInflightInt(configured.maxSessions, INFLIGHT_STATE_DEFAULT_LIMITS.maxSessions, 1, 25),
    messages:_boundedInflightInt(configured.messages, INFLIGHT_STATE_DEFAULT_LIMITS.messages, 1, 100),
    toolCalls:_boundedInflightInt(configured.toolCalls, INFLIGHT_STATE_DEFAULT_LIMITS.toolCalls, 1, 200),
    stringChars:_boundedInflightInt(configured.stringChars, INFLIGHT_STATE_DEFAULT_LIMITS.stringChars, 1000, 500000),
    jsonChars:_boundedInflightInt(configured.jsonChars, INFLIGHT_STATE_DEFAULT_LIMITS.jsonChars, 100000, 4000000),
  };
}

function _readInflightStateMap(){
  try{
    const raw=localStorage.getItem(INFLIGHT_STATE_KEY);
    const parsed=raw?JSON.parse(raw):{};
    return parsed&&typeof parsed==='object'?parsed:{};
  }catch(_){
    return {};
  }
}
function _isStorageQuotaError(err){
  return !!err && (
    err.name==='QuotaExceededError' ||
    err.name==='NS_ERROR_DOM_QUOTA_REACHED' ||
    err.code===22 ||
    err.code===1014
  );
}
function _truncateInflightValue(value, maxChars){
  const limits=_getInflightStateLimits();
  const stringLimit=_boundedInflightInt(maxChars, limits.stringChars, 1000, 500000);
  if(typeof value==='string'){
    if(value.length<=stringLimit) return value;
    return value.slice(0,stringLimit)+'\n\n[truncated for browser recovery storage]';
  }
  if(Array.isArray(value)) return value.map(v=>_truncateInflightValue(v, Math.max(2000, Math.floor(stringLimit/2))));
  if(value&&typeof value==='object'){
    const out={};
    for(const [k,v] of Object.entries(value)) out[k]=_truncateInflightValue(v, stringLimit);
    return out;
  }
  return value;
}
function _compactInflightState(state){
  const limits=_getInflightStateLimits();
  const messages=Array.isArray(state.messages)?state.messages.slice(-limits.messages):[];
  const toolCalls=Array.isArray(state.toolCalls)?state.toolCalls.slice(-limits.toolCalls):[];
  // Phase 2: persist the live todo snapshot so reload / SSE reattach
  // restores the panel without waiting for the next live `todo` write.
  // The list is bounded by the agent (typically <20 items) and each
  // item is small, so no per-list cap is needed beyond the existing
  // stringChars truncation in _truncateInflightValue.
  const todos=Array.isArray(state.todos)?state.todos:null;
  const todoStateMeta=(state.todoStateMeta&&typeof state.todoStateMeta==='object')?state.todoStateMeta:null;
  return _truncateInflightValue({
    streamId:state.streamId||null,
    messages,
    uploaded:Array.isArray(state.uploaded)?state.uploaded.slice(-20):[],
    toolCalls,
    lastAssistantText:state.lastAssistantText||'',
    lastReasoningText:state.lastReasoningText||'',
    lastRunJournalSeq:state.lastRunJournalSeq||0,
    lastRunJournalEventId:state.lastRunJournalEventId||'',
    journalReplayFromStart:!!state.journalReplayFromStart,
    currentActivityBurstId:state.currentActivityBurstId||0,
    currentLiveSegmentSeq:state.currentLiveSegmentSeq||0,
    activityBurstAnchors:Array.isArray(state.activityBurstAnchors)?state.activityBurstAnchors.slice(-50):[],
    todos,
    todoStateMeta,
  }, limits.stringChars);
}
function _writeInflightStateMap(all){
  const limits=_getInflightStateLimits();
  const entries=Object.entries(all||{})
    .sort((a,b)=>Number(b[1]&&b[1].updated_at||0)-Number(a[1]&&a[1].updated_at||0))
    .slice(0,limits.maxSessions);
  const compact={};
  for(const [sid,entry] of entries) compact[sid]=entry;
  let json=JSON.stringify(compact);
  if(json.length>limits.jsonChars){
    const current=entries[0];
    json=JSON.stringify(current?{[current[0]]:current[1]}:{});
  }
  if(json.length>limits.jsonChars){
    localStorage.removeItem(INFLIGHT_STATE_KEY);
    return false;
  }
  localStorage.setItem(INFLIGHT_STATE_KEY,json);
  return true;
}
function saveInflightState(sid, state){
  if(!sid||!state) return;
  const entry={..._compactInflightState(state),updated_at:Date.now()};
  try{
    const all=_readInflightStateMap();
    all[sid]=entry;
    _writeInflightStateMap(all);
  }catch(err){
    if(!_isStorageQuotaError(err)) return;
    try{
      localStorage.removeItem(INFLIGHT_STATE_KEY);
      _writeInflightStateMap({[sid]:entry});
    }catch(_){
      try{localStorage.removeItem(INFLIGHT_STATE_KEY);}catch(__){}
    }
  }
}
function loadInflightState(sid, streamId){
  if(!sid) return null;
  const all=_readInflightStateMap();
  const entry=all[sid];
  if(!entry) return null;
  if(streamId&&entry.streamId&&entry.streamId!==streamId) return null;
  if(entry.updated_at&&Date.now()-entry.updated_at>10*60*1000){
    clearInflightState(sid);
    return null;
  }
  return entry;
}
function clearInflightState(sid){
  if(!sid) return;
  try{
    const all=_readInflightStateMap();
    if(!(sid in all)) return;
    delete all[sid];
    if(Object.keys(all).length) localStorage.setItem(INFLIGHT_STATE_KEY, JSON.stringify(all));
    else localStorage.removeItem(INFLIGHT_STATE_KEY);
  }catch(_){ }
}

// ─── Todo state: single source of truth + render scheduling ─────────────────
//
// Three concerns live together so they can share state cleanly:
//
//   1. _todosHash(items)  — cheap content fingerprint; skips re-render when
//      a snapshot would paint the same DOM.  Used both as a short-circuit
//      and as the hash that compares "rendered vs current" snapshots.
//
//   2. scheduleTodosRefresh() — coalesces multiple `todo_state` events that
//      land in the same animation frame into a single refresh pass. It keeps
//      the left sidebar Todos behavior unchanged, and also lets the workspace
//      Todos tab repaint when that tab is enabled and currently visible.
//
//   3. _hydrateTodosFromSession(session) — applies cold-load todo_state
//      from the session GET payload, or clears the panel when neither a
//      cold-load nor an INFLIGHT signal is available.  Called at every
//      `S.session = ...` settle point so cross-session navigation never
//      leaves a stale list visible.
//
// The hash is keyed on (id, content/text, status); the render itself uses
// `esc()` for any user-controlled string, so XSS surface is the same as
// any other innerHTML path in this file.
let _todosLastRenderedHash=null;
let _todosRenderRafId=0;

function _todosHash(items){
  if(!Array.isArray(items)) return '';
  // String concat outperforms JSON.stringify on small arrays in V8 (no
  // intermediate object allocation) and is exact enough — the field set
  // matches what the renderer reads, so any visible change in DOM
  // implies a hash change.  Field separators (\x1f, \x1e) are control
  // chars unlikely to appear in real todo content, so collisions across
  // boundaries are not realistic.
  let h=items.length+'|';
  for(let i=0;i<items.length;i++){
    const t=items[i]||{};
    const content=t.content==null?(t.text==null?'':t.text):t.content;
    h+=String(t.id==null?'':t.id)+'\x1f'+String(content)+'\x1f'+String(t.status==null?'':t.status)+'\x1e';
  }
  return h;
}

const TODO_STATUS_RENDERING=Object.freeze({
  pending:Object.freeze({icon:'square',color:'var(--muted)'}),
  in_progress:Object.freeze({icon:'loader',color:'var(--blue)'}),
  completed:Object.freeze({icon:'check',color:'rgba(100,200,100,.8)'}),
  cancelled:Object.freeze({icon:'x',color:'rgba(200,100,100,.5)'}),
});

function todoStatusKey(status){
  const key=String(status||'pending');
  return Object.prototype.hasOwnProperty.call(TODO_STATUS_RENDERING,key)?key:'pending';
}

function todoStatusVisual(status){
  const key=todoStatusKey(status);
  return TODO_STATUS_RENDERING[key];
}

function renderTodoStatusIcon(status,size=14){
  const visual=todoStatusVisual(status);
  return typeof li==='function'?li(visual.icon,size):'';
}

function todoContent(todo){
  if(!todo) return '';
  return todo.content==null?(todo.text==null?'':todo.text):todo.content;
}

function renderTodoEmptyState(options={}){
  const centered=!!(options&&options.centered);
  const style=centered
    ? 'padding:24px 12px;text-align:center;color:var(--muted);font-size:12px'
    : 'color:var(--muted);font-size:12px;padding:4px 0';
  return `<div style="${style}">${esc(t('todos_no_active'))}</div>`;
}

function renderTodoRow(todo,options={}){
  const td=todo||{};
  const status=todoStatusKey(td.status);
  const visual=todoStatusVisual(status);
  const showMetadata=!(options&&options.metadata===false);
  const isCompleted=status==='completed';
  const isCancelled=status==='cancelled';
  const contentColor=(isCompleted||isCancelled)?'var(--muted)':'var(--text)';
  const completedStyle=(isCompleted||isCancelled)?'text-decoration:line-through;opacity:.5':'';
  const metadata=showMetadata
    ? `<div style="font-size:10px;color:var(--muted);margin-top:2px;opacity:.6">${esc(td.id)} · ${esc(status)}</div>`
    : '';
  return `
    <div style="display:flex;align-items:flex-start;gap:10px;padding:6px 0;border-bottom:1px solid var(--border);">
      <span style="font-size:14px;display:inline-flex;align-items:center;flex-shrink:0;margin-top:1px;color:${visual.color}">${renderTodoStatusIcon(status,14)}</span>
      <div style="flex:1;min-width:0">
        <div style="font-size:13px;color:${contentColor};${completedStyle};line-height:1.4">${esc(todoContent(td))}</div>
        ${metadata}
      </div>
    </div>`;
}

function renderTodoRows(todos,options={}){
  const items=Array.isArray(todos)?todos:[];
  return items.map(td=>renderTodoRow(td,options)).join('');
}

function _todosPanelIsActive(){
  if(typeof document==='undefined') return false;
  const panel=document.getElementById('panelTodos');
  return !!(panel&&panel.classList&&panel.classList.contains('active'));
}

function scheduleTodosRefresh(){
  // Idempotent: many `todo_state` events fire on each tool result, but
  // only the latest snapshot needs to paint.  RAF lets us coalesce
  // without timer drift.
  if(_todosRenderRafId) return;
  if(typeof requestAnimationFrame!=='function'){
    if(typeof loadTodos==='function') loadTodos();
    if(typeof _refreshWorkspacePanelTodos==='function') _refreshWorkspacePanelTodos();
    return;
  }
  _todosRenderRafId=requestAnimationFrame(()=>{
    _todosRenderRafId=0;
    const sidebarActive=_todosPanelIsActive();
    if(sidebarActive&&typeof loadTodos==='function') loadTodos();
    if(typeof _refreshWorkspacePanelTodos==='function') _refreshWorkspacePanelTodos();
  });
}

function _resetTodosRenderCache(){
  // Clear after every cross-session navigation so the next render is
  // never short-circuited against a hash from a different session.
  _todosLastRenderedHash=null;
  if(typeof _resetWorkspaceTodosRenderCache==='function') _resetWorkspaceTodosRenderCache();
}

function _hydrateTodosFromSession(session){
  // Three input cases, three deterministic outcomes:
  //   a) cold-load AND inflight both present  → pick newer by ts so a
  //      stale cold-load from the session GET cannot regress a fresher
  //      INFLIGHT snapshot persisted from a still-running stream
  //      (avoids visible rollback on reload).
  //   b) only one of cold-load / inflight is present  → use it.
  //   c) neither  → reset to empty + sentinel so loadTodos() falls
  //      through to the legacy reverse-scan or paints the empty state.
  const sid=(session&&session.session_id)||'';
  const inflight=(typeof INFLIGHT==='object'&&INFLIGHT&&sid)?INFLIGHT[sid]:null;
  const cold=session&&session.todo_state;
  const coldOk=!!(cold&&Array.isArray(cold.todos));
  const inflightOk=!!(inflight&&Array.isArray(inflight.todos)&&inflight.todoStateMeta);
  const coldTs=coldOk?(Number(cold.ts)||0):0;
  const inflightTs=inflightOk?(Number(inflight.todoStateMeta&&inflight.todoStateMeta.ts)||0):0;
  // Whether a live stream currently owns this session. This is the signal
  // that disambiguates a ts-less cold-load (see below); it comes from the
  // session GET payload (mirrors sessions.js `S.session.active_stream_id`).
  const streamActive=!!(session&&session.active_stream_id);
  if(coldOk&&inflightOk){
    // Reconcile the server's settled cold-load snapshot against the
    // locally-persisted INFLIGHT snapshot.
    //
    // coldTs===0 means the cold-load carries NO usable timestamp, so we
    // cannot order it against INFLIGHT by recency. A todo tool message can
    // legitimately lose its `timestamp` during context compression/rebuild
    // (the on-disk message ends up timestamp=None), and derive_todo_state
    // (api/todo_state.py) then returns the correct latest-by-POSITION todos
    // but omits `ts`. The tie-break depends on who owns the INFLIGHT tail:
    //
    //   - stream ACTIVE → INFLIGHT is the live tail. The most recent todo
    //     write may still be in flight and not yet settled into the message
    //     list derive_todo_state scans, so a ts-less cold-load can be an
    //     OLDER (pre-latest-write) view. Letting cold win here rolls the
    //     panel back to a stale list, and since the stream may have just
    //     ended on that very write there is no guaranteed forward SSE event
    //     to self-heal. So prefer INFLIGHT. If cold is in fact newer, the
    //     reattach replay (sessions.js attachLiveStream, reconnecting) re-
    //     emits the journaled `todo_state` events which reconcile forward by
    //     ts, so any transient discrepancy corrects itself.
    //
    //   - stream IDLE → INFLIGHT is leftover from a finished/crashed stream
    //     (idle sessions purge it shortly after, sessions.js), and there is
    //     no replay to correct anything. The settled cold-load is the
    //     authoritative latest-by-position view, so prefer cold. This also
    //     preserves the original fix for the "shows an old todo list" bug,
    //     where a stale prior-turn INFLIGHT must not beat a ts-less cold-load.
    //
    // When coldTs>0 the original recency rule stands: strict ">", and on a
    // tie prefer INFLIGHT for the freshest in-tab edits.
    const coldWins=(coldTs===0)?(!streamActive):(coldTs>inflightTs);
    if(coldWins){
      S.todos=cold.todos;
      S.todoStateMeta={
        ts:coldTs,
        source:'cold-load',
        version:Number(cold.version)||1,
      };
    }else{
      S.todos=inflight.todos;
      S.todoStateMeta=inflight.todoStateMeta;
    }
  }else if(coldOk){
    S.todos=cold.todos;
    S.todoStateMeta={
      ts:coldTs,
      source:'cold-load',
      version:Number(cold.version)||1,
    };
  }else if(inflightOk){
    S.todos=inflight.todos;
    S.todoStateMeta=inflight.todoStateMeta;
  }else{
    S.todos=[];
    S.todoStateMeta=null;
  }
  _resetTodosRenderCache();
  if(typeof scheduleTodosRefresh==='function') scheduleTodosRefresh();
}

function snapshotLiveTurnHtmlForSession(sid){
  // Keep the DOM snapshot memory-only. Persisted INFLIGHT state intentionally
  // stores structured stream state, not outerHTML, so a hard reload still uses
  // the safer flat replay path instead of reviving stale nodes/listeners.
  if(!sid||!INFLIGHT[sid]) return;
  const turn=$('liveAssistantTurn');
  if(!turn) return;
  if(turn.dataset&&turn.dataset.sessionId&&turn.dataset.sessionId!==sid) return;
  let snapshotTurn=turn;
  const sourceControls=turn.querySelectorAll
    ? Array.from(turn.querySelectorAll('.transparent-event-copy,.thinking-copy-btn'))
    : [];
  if(sourceControls.some(control=>!!control._transparentCopiedFeedbackNormal)){
    // Copy-success feedback is transient, while its normal presentation lives
    // only on element properties. Sanitize an independent clone so switching
    // sessions cannot persist the check/Copied/accent presentation, without
    // mutating the visible control or disturbing its active feedback timer.
    snapshotTurn=turn.cloneNode(true);
    const clonedControls=Array.from(snapshotTurn.querySelectorAll('.transparent-event-copy,.thinking-copy-btn'));
    sourceControls.forEach((source,index)=>{
      const normal=source._transparentCopiedFeedbackNormal;
      const clone=clonedControls[index];
      if(!normal||!clone) return;
      clone.innerHTML=normal.innerHTML;
      if(clone.style){
        if(normal.styleCssText!==null) clone.style.cssText=normal.styleCssText;
        else clone.style.color=normal.color||'';
      }
      if(normal.titleAttr===null) clone.removeAttribute('title');
      else clone.setAttribute('title',normal.titleAttr);
      if(normal.ariaLabel===null) clone.removeAttribute('aria-label');
      else clone.setAttribute('aria-label',normal.ariaLabel);
    });
  }
  INFLIGHT[sid].liveTurnHtml=snapshotTurn.outerHTML;
}

function _liveAssistantSegmentTextLength(seg){
  if(!seg) return 0;
  const body=seg.querySelector('.msg-body')||seg;
  return String(body.textContent||'').trim().length;
}

function _mergeRestoredLiveAssistantSegment(restored, existing){
  if(!restored||!existing) return;
  const existingLive=existing.querySelector('[data-live-assistant="1"]');
  if(!existingLive) return;
  const restoredLive=restored.querySelector('[data-live-assistant="1"]');
  const existingLen=_liveAssistantSegmentTextLength(existingLive);
  const restoredLen=_liveAssistantSegmentTextLength(restoredLive);
  if(existingLen<=restoredLen) return;
  const replacement=existingLive.cloneNode(true);
  if(restoredLive){
    restoredLive.replaceWith(replacement);
    return;
  }
  const blocks=_assistantTurnBlocks(restored);
  if(!blocks) return;
  const anchor=Array.from(blocks.children).filter(el=>
    el.matches('.tool-call-group,.tool-card-row,.agent-activity-thinking,.thinking-card-row,[data-live-assistant="1"]')
  ).pop();
  if(anchor) anchor.insertAdjacentElement('afterend', replacement);
  else blocks.appendChild(replacement);
}

function restoreLiveTurnHtmlForSession(sid){
  const inflight=INFLIGHT[sid];
  if(!sid||!inflight||!inflight.liveTurnHtml) return false;
  const inner=$('msgInner');
  if(!inner) return false;
  const template=document.createElement('template');
  template.innerHTML=String(inflight.liveTurnHtml||'').trim();
  const restored=template.content.firstElementChild;
  if(!restored) return false;
  restored.id='liveAssistantTurn';
  if(S.session) restored.dataset.sessionId=S.session.session_id;
  const existing=$('liveAssistantTurn');
  _mergeRestoredLiveAssistantSegment(restored, existing);
  if(existing) existing.replaceWith(restored);
  else inner.appendChild(restored);
  // Transparent Stream: liveTurnHtml is restored via template.innerHTML, which
  // drops the property-bound onclick/onkeydown handlers wired by
  // _wireTransparentHeaderToggle / _attachCopyButton / _syncTransparentEventControls /
  // _wireTransparentTurnToggle. The settled cache fast-path re-runs the rehydrate;
  // this active-session live-turn restore path must too, or row toggles, copy
  // buttons, expand/collapse, and the turn chevron silently stop working after a
  // session-switch/reconnect restore. (Codex trifecta finding C1.)
  if(typeof _rehydrateTransparentStreamDom==='function') _rehydrateTransparentStreamDom(restored);
  if(typeof normalizeLiveActivityGroupPlacement==='function') normalizeLiveActivityGroupPlacement(restored);
  if(typeof _dedupeLiveProcessedWorklogAnchors==='function') _dedupeLiveProcessedWorklogAnchors(restored);
  const liveGroup=restored.querySelector('.tool-call-group[data-live-tool-call-group="1"]');
  if(liveGroup&&typeof _startActivityElapsedTimer==='function') _startActivityElapsedTimer(liveGroup);
  if(typeof placeLiveToolCardsHost==='function') placeLiveToolCardsHost();
  requestAnimationFrame(()=>_postProcessWithAnchorSuppression(restored));
  return true;
}

function markInflight(sid, streamId) {
  const payload=JSON.stringify({sid, streamId, ts: Date.now()});
  try{
    localStorage.setItem(INFLIGHT_KEY, payload);
  }catch(err){
    if(!_isStorageQuotaError(err)) return;
    try{
      localStorage.removeItem(INFLIGHT_STATE_KEY);
      localStorage.setItem(INFLIGHT_KEY, payload);
    }catch(_){}
  }
}
function clearInflight() {
  localStorage.removeItem(INFLIGHT_KEY);
}
function showReconnectBanner(msg) {
  $('reconnectMsg').textContent = msg || 'A response may have been in progress when you last left.';
  $('reconnectBanner').classList.add('visible');
}
function dismissReconnect() {
  $('reconnectBanner').classList.remove('visible');
  clearInflight();
}

// ── Live host resource health panel (#693) ──
const SYSTEM_HEALTH_INTERVAL_MS=5000;
let _systemHealthTimer=null;
function _systemHealthPercent(metric){
  const percent=Number(metric&&metric.percent);
  if(!Number.isFinite(percent)) return null;
  return Math.max(0,Math.min(100,Math.round(percent*10)/10));
}
function _formatSystemHealthPercent(percent){
  if(percent == null) return '—';
  return `${percent.toFixed(percent%1?1:0)}%`;
}
function _formatSystemHealthBytes(metric){
  if(!metric||!metric.used_bytes||!metric.total_bytes) return '';
  const units=['B','KB','MB','GB','TB'];
  const fmt=(bytes)=>{
    let value=Number(bytes)||0, idx=0;
    while(value>=1024&&idx<units.length-1){value/=1024;idx++;}
    return `${value.toFixed(value>=10||idx===0?0:1)} ${units[idx]}`;
  };
  return `${fmt(metric.used_bytes)} / ${fmt(metric.total_bytes)}`;
}
function _updateSystemHealthMetric(name,metric){
  const row=document.querySelector(`[data-system-health-metric="${name}"]`);
  if(!row) return;
  const rawPercent=_systemHealthPercent(metric);
  const percent=rawPercent == null ? 0 : rawPercent;
  const label=row.querySelector('[data-system-health-value]');
  const bar=row.querySelector('.system-health-bar');
  const fill=row.querySelector('.system-health-bar-fill');
  const text=_formatSystemHealthPercent(rawPercent);
  if(label){
    label.textContent=text;
    const bytes=(name==='memory'||name==='disk')?_formatSystemHealthBytes(metric):'';
    label.title=bytes||text;
  }
  if(bar) bar.setAttribute('aria-valuenow',String(percent));
  if(fill) fill.style.width=`${percent}%`;
}
function setSystemHealthUnavailable(message){
  const panel=$('systemHealthPanel');
  const status=$('systemHealthStatus');
  if(!panel) return;
  panel.classList.remove('loading');
  panel.classList.add('unavailable');
  if(status) status.textContent=message||'Unavailable';
  ['cpu','memory','disk'].forEach(name=>_updateSystemHealthMetric(name,null));
}
function renderSystemHealth(payload){
  const panel=$('systemHealthPanel');
  const status=$('systemHealthStatus');
  if(!panel) return;
  if(!payload||payload.available===false){
    setSystemHealthUnavailable('Unavailable');
    return;
  }
  panel.classList.remove('loading','unavailable');
  if(status) status.textContent=payload.status==='partial'?'Partial':'Live';
  _updateSystemHealthMetric('cpu',payload.cpu);
  _updateSystemHealthMetric('memory',payload.memory);
  _updateSystemHealthMetric('disk',payload.disk);
}
async function pollSystemHealth(){
  if(document.visibilityState !== 'visible') return;
  if(!_systemHealthPanelIsVisible()) return;
  try{
    const payload=await api('/api/system/health',{timeoutToast:false});
    renderSystemHealth(payload);
  }catch(_){
    setSystemHealthUnavailable('Unavailable');
  }
}
function _systemHealthPanelIsVisible(){
  return document.visibilityState === 'visible' &&
    !!document.querySelector('main.main.showing-insights') &&
    !!$('systemHealthPanel');
}
function startSystemHealthMonitor(){
  if(!_systemHealthPanelIsVisible()) return;
  if(_systemHealthTimer) return;
  void pollSystemHealth();
  _systemHealthTimer=setInterval(pollSystemHealth,SYSTEM_HEALTH_INTERVAL_MS);
}
function stopSystemHealthMonitor(){
  if(_systemHealthTimer){clearInterval(_systemHealthTimer);_systemHealthTimer=null;}
}
function _syncSystemHealthMonitorVisibility(){
  if(_systemHealthPanelIsVisible()) startSystemHealthMonitor();
  else stopSystemHealthMonitor();
}
document.addEventListener('visibilitychange',_syncSystemHealthMonitorVisibility);
if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',startSystemHealthMonitor);
else startSystemHealthMonitor();

// ── AGY agent/gateway heartbeat alert (#716) ──
const AGENT_HEALTH_INTERVAL_MS=30000;
const AGENT_HEALTH_DISMISSED_KEY='agent-health-dismissed';
let _agentHealthTimer=null;
let _agentHealthLastState='unknown';
let _lastGatewayRestartTime=0;
function _agentHealthDismissed(){
  try{return localStorage.getItem(AGENT_HEALTH_DISMISSED_KEY)==='1';}
  catch(_){return false;}
}
function _setAgentHealthDismissed(value){
  try{
    if(value)localStorage.setItem(AGENT_HEALTH_DISMISSED_KEY,'1');
    else localStorage.removeItem(AGENT_HEALTH_DISMISSED_KEY);
  }catch(_){ }
}
function _hideAgentHealthAlert(){
  const banner=$('agentHealthBanner');
  if(banner){banner.classList.remove('visible');banner.hidden=true;}
}
function _showAgentHealthAlert(payload){
  if(_agentHealthDismissed()) return;
  const banner=$('agentHealthBanner');
  const title=$('agentHealthTitle');
  const details=$('agentHealthDetails');
  if(!banner) return;
  if(title) title.textContent='AGY agent is not responding';
  const state=payload&&payload.details&&payload.details.gateway_state?` State: ${payload.details.gateway_state}.`:'';
  if(details) details.textContent=`Gateway heartbeat failed.${state} Messages may not be delivered until it comes back.`;
  banner.hidden=false;
  banner.classList.add('visible');
}
function dismissAgentHealthAlert(){
  _setAgentHealthDismissed(true);
  _hideAgentHealthAlert();
}
async function restartGatewayService(){
  const btn = $('btnRestartGateway');
  const dismissBtn = $('agentHealthDismiss');
  if(!btn) return;
  btn.disabled = true;
  if(dismissBtn) dismissBtn.disabled = true;
  const originalText = btn.textContent;
  btn.textContent = 'Restarting...';
  try {
    const res = await api('/api/health/restart', {method: 'POST'});
    if(res && res.ok){
      showToast('Gateway service restarted successfully');
      _hideAgentHealthAlert();
      _lastGatewayRestartTime = Date.now();
      setTimeout(pollAgentHealth, 15000);
    } else {
      showToast(res && res.error || 'Failed to restart gateway service');
    }
  } catch(e) {
    showToast('Failed to restart gateway service: ' + e.message);
  } finally {
    btn.disabled = false;
    if(dismissBtn) dismissBtn.disabled = false;
    btn.textContent = originalText;
  }
}
async function pollAgentHealth(){
  if(document.visibilityState !== 'visible') return;
  if(Date.now() - _lastGatewayRestartTime < 15000) return;
  try{
    const payload=await api('/api/health/agent',{timeoutToast:false});
    if(payload.alive === true){
      _agentHealthLastState='alive';
      _setAgentHealthDismissed(false);
      _hideAgentHealthAlert();
      return;
    }
    if(payload.alive === false){
      _agentHealthLastState='down';
      _showAgentHealthAlert(payload);
      return;
    }
    if(payload.alive == null){
      _agentHealthLastState='unknown';
      _hideAgentHealthAlert();
    }
  }catch(_){
    _agentHealthLastState='unknown';
    _hideAgentHealthAlert();
  }
}
function startAgentHealthMonitor(){
  if(document.visibilityState !== 'visible') return;
  if(_agentHealthTimer) return;
  void pollAgentHealth();
  _agentHealthTimer=setInterval(pollAgentHealth, AGENT_HEALTH_INTERVAL_MS);
}
function stopAgentHealthMonitor(){
  if(_agentHealthTimer){clearInterval(_agentHealthTimer);_agentHealthTimer=null;}
}
function _syncAgentHealthMonitorVisibility(){
  if(document.visibilityState === 'visible') startAgentHealthMonitor();
  else stopAgentHealthMonitor();
}
document.addEventListener('visibilitychange',_syncAgentHealthMonitorVisibility);
if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',startAgentHealthMonitor);
else startAgentHealthMonitor();
async function refreshSession() {
  // When the banner is in post-update restart mode, the "Reload" button
  // should do a full page reload — a session refresh would just 502 while
  // the server is still restarting.
  if (window._restartingForUpdate) { location.reload(); return; }
  dismissReconnect();
  if (!S.session) return;
  try {
    const data = await api(`/api/session?session_id=${encodeURIComponent(S.session.session_id)}`);
    S.session = data.session;
    if(typeof _adoptRegenerationRevision==='function') _adoptRegenerationRevision(data.session);
    S.messages = data.session.messages || [];
    _messagesTruncated = !!data.session._messages_truncated;
    _oldestIdx = data.session._messages_offset || 0;
    if (typeof _mergePendingSessionMessage !== 'function') {
      throw new Error('Pending-session merge helper unavailable');
    }
    _mergePendingSessionMessage(data.session, S.messages);
    S.activeStreamId = data.session.active_stream_id || null;

    syncTopbar(); _renderMessagesWithScrollSnapshot();
    showToast('Conversation refreshed');
  } catch(e) { setStatus('Refresh failed: ' + e.message); }
}
// ── Update banner ──
function _formatUpdateTargetStatus(label,info){
  const manualNoGit=!!(info&&info.no_git&&info.manual_update&&info.behind>0);
  if(!info||(info.no_git&&!manualNoGit)||!(info.behind>0)) return null;
  const release=(info.release_based&&info.latest_version)
    ?` (${info.current_version||'unknown'} -> ${info.latest_version})`
    :(info.branch?` (${info.branch})`:'');
  const noun=info.release_based?'release':'update';
  return `${label}${release}: ${info.behind} ${noun}${info.behind>1?'s':''}`;
}
function _formatManualUpdateInstruction(info){
  if(!(info&&info.no_git&&info.manual_update&&info.behind>0)) return null;
  return t('settings_update_manual_docker','docker compose pull && docker compose up -d');
}
function _formatUpdateCheckError(label,info){
  if(!info||!info.error) return null;
  const detail=String(info.error).replace(/^fetch failed:?\s*/i,'').trim();
  return detail ? `${label}: ${detail}` : label;
}
function _isSafeUpdateCompareUrl(url){
  if(!url||!/^https?:\/\//i.test(url)) return false;
  try{
    const parsed=new URL(url);
    return parsed.protocol==='https:'||parsed.protocol==='http:';
  }catch(e){
    return false;
  }
}
function _updateCompareUrl(info){
  if(!info) return null;
  const compareUrl=info.compare_url||null;
  if(compareUrl) return _isSafeUpdateCompareUrl(compareUrl)?compareUrl:null;
  const repo_url=info.repo_url;
  const currentSha=info.current_sha;
  const latestSha=info.latest_sha;
  if(!(repo_url&&currentSha&&latestSha)) return null;
  const fallbackUrl=repo_url+'/compare/'+currentSha+'...'+latestSha;
  return _isSafeUpdateCompareUrl(fallbackUrl)?fallbackUrl:null;
}
function _updateWhatsNewTargets(data){
  const targets=[
    {key:'webui',label:'WebUI',info:data&&data.webui},
    {key:'agent',label:'Agent',info:data&&data.agent},
  ];
  return targets.map((target)=>({
    key:target.key,
    label:target.label,
    info:target.info,
    url:_updateCompareUrl(target.info),
  })).filter((target)=>target.info&&target.info.behind>0&&target.url);
}
function _appendUpdateDiffLinks(container,targets,prefix){
  if(!container) return;
  if(prefix) container.appendChild(document.createTextNode(prefix));
  targets.forEach((target,idx)=>{
    if(idx>0) container.appendChild(document.createTextNode(' \u00b7 '));
    const link=document.createElement('a');
    link.href=target.url;
    link.target='_blank';
    link.rel='noopener';
    link.style.color='var(--accent)';
    link.style.textDecoration='underline';
    link.textContent=target.label;
    container.appendChild(link);
  });
}
function _hideUpdateSummaryPanel(){
  const panel=$('updateSummaryPanel');
  const text=$('updateSummaryText');
  const links=$('updateSummaryDiffLinks');
  const toolbar=$('updateSummaryToolbar');
  if(panel){
    panel.style.display='none';
    panel.classList.remove('update-summary-expanded');
  }
  if(toolbar) toolbar.style.display='none';
  _syncUpdateSummaryExpandButton(false);
  if(text) text.textContent='';
  if(links){links.replaceChildren();links.style.display='none';}
}
function _syncUpdateSummaryExpandButton(expanded){
  const btn=$('btnUpdateSummaryExpand');
  if(!btn) return;
  btn.setAttribute('aria-expanded',expanded?'true':'false');
  btn.textContent=expanded?'Collapse summary':'Expand summary';
}
function toggleUpdateSummaryExpanded(){
  const panel=$('updateSummaryPanel');
  if(!panel||panel.style.display==='none') return;
  const expanded=!panel.classList.contains('update-summary-expanded');
  panel.classList.toggle('update-summary-expanded',expanded);
  _syncUpdateSummaryExpandButton(expanded);
}
const WHATS_NEW_SUMMARY_STORAGE_KEY='agy-whats-new-generated-summaries';
const WHATS_NEW_SUMMARY_STORAGE_MAX_BYTES=256*1024;
function _summaryStorageByteLength(value){
  const text=typeof value==='string'?value:JSON.stringify(value);
  if(text==null) return 0;
  if(typeof TextEncoder==='function') return new TextEncoder().encode(text).length;
  let bytes=0;
  for(const ch of text){
    const code=ch.codePointAt(0);
    bytes+=code<=0x7f?1:(code<=0x7ff?2:(code<=0xffff?3:4));
  }
  return bytes;
}
function _summaryCacheEntriesSortedByRecency(entries){
  return entries.slice().sort((left,right)=>{
    const leftKey=left[0];
    const rightKey=right[0];
    const leftSummary=left[1];
    const rightSummary=right[1];
    const leftUpdatedAt=leftSummary&&leftSummary.updatedAt;
    const rightUpdatedAt=rightSummary&&rightSummary.updatedAt;
    if(typeof leftUpdatedAt==='number'&&typeof rightUpdatedAt==='number'&&leftUpdatedAt!==rightUpdatedAt){
      return rightUpdatedAt-leftUpdatedAt;
    }
    if(typeof leftUpdatedAt==='number') return -1;
    if(typeof rightUpdatedAt==='number') return 1;
    if(leftKey==='webui'&&rightKey!=='webui') return -1;
    if(rightKey==='webui'&&leftKey!=='webui') return 1;
    if(leftKey==='agent'&&rightKey!=='agent') return -1;
    if(rightKey==='agent'&&leftKey!=='agent') return 1;
    return leftKey<rightKey?-1:(leftKey>rightKey?1:0);
  });
}
function _loadStoredUpdateSummaries(){
  window._whatsNewGeneratedSummaries=window._whatsNewGeneratedSummaries||{};
  try{
    const raw=sessionStorage.getItem(WHATS_NEW_SUMMARY_STORAGE_KEY);
    if(!raw) return window._whatsNewGeneratedSummaries;
    const stored=JSON.parse(raw);
    if(stored&&typeof stored==='object') window._whatsNewGeneratedSummaries=stored;
  }catch(_e){
    try{sessionStorage.removeItem(WHATS_NEW_SUMMARY_STORAGE_KEY);}catch(_ignore){}
  }
  return window._whatsNewGeneratedSummaries;
}
function _persistGeneratedSummaries(){
  const current=window._whatsNewGeneratedSummaries||{};
  const next={};
  try{
    _summaryCacheEntriesSortedByRecency(Object.entries(current)).forEach((entry)=>{
      const candidate={...next,...Object.fromEntries([entry])};
      if(_summaryStorageByteLength(JSON.stringify(candidate))<=WHATS_NEW_SUMMARY_STORAGE_MAX_BYTES){
        Object.assign(next, Object.fromEntries([entry]));
      }
    });
    window._whatsNewGeneratedSummaries=next;
    sessionStorage.setItem(WHATS_NEW_SUMMARY_STORAGE_KEY,JSON.stringify(next));
  }catch(_e){}
}
function _pruneGeneratedSummaries(data){
  const cache=_loadStoredUpdateSummaries();
  const valid=new Set(_updateWhatsNewTargets(data||{}).map((target)=>target.key));
  let changed=false;
  Object.keys(cache).forEach((key)=>{
    if(!valid.has(key)){delete cache[key];changed=true;}
  });
  if(changed) _persistGeneratedSummaries();
}
function _updateSummarySignature(info){
  if(!info) return '';
  return [info.current_sha||'',info.latest_sha||'',info.behind||0,info.compare_url||''].join('|');
}
function _updateSummaryButtonLabel(target,data){
  const labels=target.key==='webui'
    ? {generate:'Generate WebUI update summary',view:'View generated WebUI update summary',regenerate:'Re-generate WebUI update summary'}
    : {generate:'Generate Agent update summary',view:'View generated Agent update summary',regenerate:'Re-generate Agent update summary'};
  const cache=_loadStoredUpdateSummaries()[target.key];
  const signature=_updateSummarySignature(data&&data[target.key]);
  if(cache&&cache.signature===signature&&cache.payload) return labels.view;
  if(cache&&cache.signature!==signature) return labels.regenerate;
  return labels.generate;
}
function _rememberGeneratedSummary(target,payload,data){
  if(!target) return;
  window._whatsNewGeneratedSummaries=window._whatsNewGeneratedSummaries||{};
  window._whatsNewGeneratedSummaries[target]={
    signature:_updateSummarySignature(data&&data[target]),
    payload:payload,
    updatedAt:Date.now(),
  };
  _persistGeneratedSummaries();
}
function _renderUpdateSummaryPanel(payload,data,targetKey){
  const panel=$('updateSummaryPanel');
  const text=$('updateSummaryText');
  const links=$('updateSummaryDiffLinks');
  const toolbar=$('updateSummaryToolbar');
  if(!panel||!text) return;
  panel.style.display='block';
  panel.classList.remove('update-summary-expanded');
  _syncUpdateSummaryExpandButton(false);
  if(toolbar) toolbar.style.display='flex';
  const sections=Array.isArray(payload&&payload.summary_sections)?payload.summary_sections:null;
  text.replaceChildren();
  if(sections&&sections.length){
    const wrap=document.createElement('div');
    wrap.id='updateSummarySections';
    wrap.style.display='grid';
    wrap.style.gap='8px';
    sections.forEach((section)=>{
      const block=document.createElement('section');
      const title=document.createElement('div');
      title.style.fontWeight='650';
      title.style.marginBottom='3px';
      title.textContent=section.title||'Summary';
      block.appendChild(title);
      const ul=document.createElement('ul');
      ul.style.margin='0';
      ul.style.paddingLeft='18px';
      (Array.isArray(section.items)?section.items:[]).forEach((item)=>{
        const li=document.createElement('li');
        li.textContent=String(item||'').trim();
        if(li.textContent) ul.appendChild(li);
      });
      if(!ul.children.length){
        const li=document.createElement('li');
        li.textContent='No summary details available.';
        ul.appendChild(li);
      }
      block.appendChild(ul);
      wrap.appendChild(block);
    });
    text.appendChild(wrap);
  }else{
    text.textContent=(payload&&payload.summary)||payload||'No summary available.';
  }
  const targets=_updateWhatsNewTargets(data||window._updateData||{}).filter((target)=>!targetKey||target.key===targetKey);
  if(links){
    links.replaceChildren();
    if(targets.length){
      links.style.display='block';
      _appendUpdateDiffLinks(links,targets,'Regular diff comparison: ');
    }else{
      links.style.display='none';
    }
  }
}
async function showWhatsNewSummary(target){
  const data=window._updateData||{};
  const scopedUpdates=target?{[target]:data[target]}:data;
  const cache=target?_loadStoredUpdateSummaries()[target]:null;
  const signature=target?_updateSummarySignature(data[target]):'';
  if(cache&&cache.signature===signature&&cache.payload){
    _renderUpdateSummaryPanel(cache.payload,data,target);
    _renderUpdateWhatsNewLinks(data,{mode:'summary'});
    return;
  }
  _renderUpdateSummaryPanel({summary:'Writing a simple summary…'},data,target);
  try{
    const res=await api('/api/updates/summary',{method:'POST',body:JSON.stringify({updates:scopedUpdates,target:target||null}),timeoutMs:60000});
    _rememberGeneratedSummary(target,res,data);
    _renderUpdateSummaryPanel(res,data,target);
    _renderUpdateWhatsNewLinks(data,{mode:'summary'});
  }catch(e){
    console.warn('[updates] summary failed',e);
    _renderUpdateSummaryPanel({
      summary_sections:[
        {title:"What you'll notice",items:['Could not generate the summary right now.']},
        {title:'Worth knowing',items:['Try again later, or use the comparison links below for the raw update details.']},
      ],
    },data,target);
  }
}
function _renderUpdateWhatsNewLinks(data){
  const options=arguments.length>1&&arguments[1]?arguments[1]:{};
  const container=$('updateWhatsNewLinks');
  if(!container) return;
  container.replaceChildren();
  const targets=_updateWhatsNewTargets(data);
  if(!targets.length){
    container.style.display='none';
    _hideUpdateSummaryPanel();
    return;
  }
  container.style.display='block';
  _pruneGeneratedSummaries(data);
  const useSummary=(options.mode||'')==='summary'||window._whatsNewSummaryEnabled===true;
  if(useSummary){
    targets.forEach((target,idx)=>{
      if(idx>0) container.appendChild(document.createTextNode(' \u00b7 '));
      const btn=document.createElement('button');
      btn.type='button';
      btn.className='linklike';
      btn.style.color='var(--accent)';
      btn.style.textDecoration='underline';
      btn.style.background='none';
      btn.style.border='0';
      btn.style.padding='0';
      btn.style.cursor='pointer';
      btn.textContent=_updateSummaryButtonLabel(target,data);
      btn.onclick=()=>showWhatsNewSummary(target.key);
      container.appendChild(btn);
    });
    return;
  }
  _hideUpdateSummaryPanel();
  if(targets.length===1){
    const target=targets[0];
    const link=document.createElement('a');
    link.href=target.url;
    link.target='_blank';
    link.rel='noopener';
    link.style.color='var(--accent)';
    link.style.textDecoration='underline';
    link.textContent="What's new in "+target.label+'?';
    container.appendChild(link);
    return;
  }
  _appendUpdateDiffLinks(container,targets,"What's new: ");
}
function _showUpdateBanner(data){
  const parts=[];
  const webuiPart=_formatUpdateTargetStatus('WebUI',data.webui);
  const agentPart=_formatUpdateTargetStatus('Agent',data.agent);
  if(webuiPart) parts.push(webuiPart);
  if(agentPart) parts.push(agentPart);
  window._updateData=data;
  const btnApply=$('btnApplyUpdate');
  if(btnApply){
    const webuiManual=!!(data&&data.webui&&data.webui.manual_update&&data.webui.behind>0);
    const webuiUpdatable=!!(data&&data.webui&&data.webui.behind>0&&!webuiManual);
    const agentUpdatable=!!(data&&data.agent&&data.agent.behind>0);
    const hasApplyTargets=webuiUpdatable||agentUpdatable;
    btnApply.disabled=!hasApplyTargets;
    btnApply.style.display=hasApplyTargets?'':'none';
    if(webuiManual){
      const forceBtn=$('btnForceUpdate');
      if(forceBtn){forceBtn.disabled=true;forceBtn.style.display='none';forceBtn.dataset.target='';}
      const clearLockBtn=$('btnClearUpdateLock');
      if(clearLockBtn){clearLockBtn.disabled=true;clearLockBtn.style.display='none';clearLockBtn.dataset.target='';}
    }
  }
  if(!parts.length){
    _renderUpdateWhatsNewLinks(data);
    const staleBanner=$('updateBanner');
    if(staleBanner) staleBanner.classList.remove('visible');
    return;
  }
  const msg=$('updateMsg');
  if(msg){
    const manualInstruction=_formatManualUpdateInstruction(data&&data.webui);
    msg.textContent='\u2B06 '+parts.join(', ')+' available'+(manualInstruction?' · '+manualInstruction:'');
  }
  const banner=$('updateBanner');
  if(banner) banner.classList.add('visible');
  const summaryMode=window._whatsNewSummaryEnabled===true?'summary':'diff';
  _renderUpdateWhatsNewLinks(data,{mode:summaryMode});
}
function _i18nUpdateText(key, fallback){
  if(typeof t==='function'){
    const val=t(key);
    if(val&&val!==key) return val;
  }
  return fallback;
}
function dismissUpdate(){
  const b=$('updateBanner');if(b)b.classList.remove('visible');
  sessionStorage.setItem('agy-update-dismissed','1');
}
function _isUpdateApplyNetworkError(error){
  if(error && error.status) return false;
  const message=(error&&error.message)||String(error||'');
  return /Failed to fetch|NetworkError|Load failed/i.test(message);
}
function _formatUpdateApplyExceptionMessage(error){
  if(_isUpdateApplyNetworkError(error)){
    return _i18nUpdateText('update_failed_network','Update failed: could not reach the WebUI server. It may have restarted or the connection was interrupted. Please wait a few seconds, reload the page, then check the server if it still does not come back.');
  }
  const message=(error&&error.message)||String(error||'unknown error');
  return _i18nUpdateText('update_failed_prefix','Update failed: ')+message;
}
async function applyUpdates(){
  if(window._updateApplyInFlight) return;
  window._updateApplyInFlight=true;
  const updateText=(key,fallback)=>(typeof _i18nUpdateText==='function'?_i18nUpdateText(key,fallback):fallback);
  const btn=$('btnApplyUpdate');
  const resetApplyButton=(delayMs)=>{
    const reset=()=>{
      window._updateApplyInFlight=false;
      if(btn){btn.disabled=false;btn.textContent=updateText('update_now','Update Now');}
    };
    if(delayMs>0) setTimeout(reset,delayMs);
    else reset();
  };
  if(btn){btn.disabled=true;btn.textContent=updateText('update_updating','Updating\u2026');}
  const errEl=$('updateError');
  if(errEl){errEl.style.display='none';errEl.textContent='';}
  // Hide any leftover force-update button from a prior conflict so a fresh
  // retry starts clean (otherwise stale state points at the wrong target).
  const forceBtnReset=$('btnForceUpdate');
  if(forceBtnReset){forceBtnReset.style.display='none';forceBtnReset.dataset.target='';}
  const targets=[];
  if(window._updateData?.agent?.behind>0) targets.push('agent');
  if(window._updateData?.webui?.behind>0&&!window._updateData?.webui?.manual_update) targets.push('webui');
  if(!targets.length){
    const msg=updateText('update_no_target','No update target selected. Refresh update status and retry.');
    if(errEl){errEl.textContent=msg;errEl.style.display='block';}
    else showToast(msg,5000,'error');
    resetApplyButton(0);
    return;
  }
  try{
    const stashConflictMessages=[];
    const baselineServerIdentity = await _readHealthServerIdentity();
    for(const target of targets){
      // Send the channel the CHECK reported for this target (what was actually
      // offered in the banner), not a fresh settings read — otherwise a channel
      // switch whose debounced autosave hasn't landed yet races apply, which
      // would then read the OLD saved channel (Codex gate). webui carries the
      // channel; agent is channel-neutral server-side so omitting it is fine.
      const _applyBody={target};
      const _ch=window._updateData?.[target]?.channel;
      if(_ch==='stable'||_ch==='experimental') _applyBody.channel=_ch;
      const res=await api('/api/updates/apply',{method:'POST',body:JSON.stringify(_applyBody),timeoutMs:120000});
      if(!res.ok){
        _showUpdateError(target,res);
        resetApplyButton(0);
        return;
      }
      if(res.stash_conflict){
        stashConflictMessages.push('Update applied ('+target+'): '+(res.message||'Local changes were preserved in git stash.'));
        if(errEl){errEl.textContent=stashConflictMessages.join('\n\n');errEl.style.display='block';}
      }
    }
    const stashConflictMessage=stashConflictMessages.join('\n\n');
    showToast(stashConflictMessage||'Update applied — restarting…',stashConflictMessages.length?10000:undefined,stashConflictMessages.length?'warning':undefined);
    sessionStorage.removeItem('agy-update-checked');
    sessionStorage.removeItem('agy-update-dismissed');
    _waitForServerThenReload({baselineServerIdentity});
  }catch(e){
    const msg=_formatUpdateApplyExceptionMessage(e);
    if(errEl){errEl.textContent=msg;errEl.style.display='block';}
    else showToast(msg);
    resetApplyButton(_isUpdateApplyNetworkError(e)?5000:0);
  }
}
function _showUpdateError(target,res){
  const errEl=$('updateError');
  const forceBtn=$('btnForceUpdate');
  const msg='Update failed ('+target+'): '+(res.message||'unknown error');
  if(errEl){
    errEl.textContent=msg;
    errEl.style.display='block';
  } else {
    showToast(msg);
  }
  // Show "Force update" button ONLY for errors recoverable by a destructive
  // hard reset. Lock-only failures are routed to a separate non-destructive
  // "Clear lock and retry update" button (BRICK-2 fix for PR #5688: a lock
  // error should never invoke apply_force_update, which would discard local
  // modifications).
  if(forceBtn&&(res.conflict||res.diverged)){
    forceBtn.dataset.target=target;
    forceBtn.style.display='inline-block';
  }
  // Show "Clear lock and retry update" when the only failure was a stale
  // git lock. This calls the new non-destructive /api/updates/clear_lock
  // endpoint, which probes the lock for a holder and refuses if held.
  const clearLockBtn=$('btnClearUpdateLock');
  if(clearLockBtn&&res.lock_conflict){
    clearLockBtn.dataset.target=target;
    clearLockBtn.style.display='inline-block';
  }
}
async function applyClearUpdateLock(btn){
  if(window._clearLockInFlight) return;
  const target=btn.dataset.target;
  if(!target) return;
  window._clearLockInFlight=true;
  btn.disabled=true;
  const originalLabel=btn.textContent;
  btn.textContent='Checking lock…';
  try{
    const res=await api('/api/updates/clear_lock',{method:'POST',body:JSON.stringify({target}),timeoutMs:60000});
    if(res.ok){
      sessionStorage.removeItem('agy-update-checked');
      sessionStorage.removeItem('agy-update-dismissed');
      showToast('Update applied — restarting…');
      _waitForServerThenReload({});
    } else if(res.lock_held){
      // v2.2: server returns manual-instruction. Show the exact `rm`
      // command + a one-click "I've removed it, retry update" affordance
      // that POSTs the same endpoint a second time (now that the user
      // has presumably removed the lock, the server's success branch
      // runs the normal non-destructive apply).
      _renderLockManualInstruction(target, res);
    } else {
      const msg='Could not check the lock: '+(res.message||'unknown error');
      const errEl=$('updateError');
      if(errEl){errEl.textContent=msg;errEl.style.display='block';}
      else showToast(msg);
    }
  }catch(e){
    const msg='Lock-check request failed: '+((e&&e.message)||String(e));
    const errEl=$('updateError');
    if(errEl){errEl.textContent=msg;errEl.style.display='block';}
    else showToast(msg);
  }finally{
    window._clearLockInFlight=false;
    btn.disabled=false;
    btn.textContent=originalLabel;
  }
}
function _renderLockManualInstruction(target, res){
  // Replace the inline `updateError` text with a richer block that shows
  // the exact manual command and offers a one-click retry button. The
  // "retry" handler re-invokes `applyClearUpdateLock`; this time, with
  // the lock gone, the server's success branch runs the normal apply.
  const cmd = res.manual_command || ('rm -f ' + (res.well_known_lock_path || '.git/index.lock'));
  const errEl=$('updateError');
  if(!errEl){
    showToast('Lock present. Run: '+cmd);
    return;
  }
  errEl.style.display='block';
  errEl.innerHTML='';
  const intro=document.createElement('div');
  intro.style.marginBottom='6px';
  intro.textContent='A stale .git/index.lock is present. The server cannot remove it safely — please run this command on the host:';
  errEl.appendChild(intro);
  const code=document.createElement('pre');
  code.style.background='rgba(0,0,0,0.05)';
  code.style.padding='6px';
  code.style.margin='4px 0';
  code.style.fontFamily='var(--font-mono)';
  code.style.borderRadius='4px';
  code.style.whiteSpace='pre-wrap';
  code.style.wordBreak='break-all';
  code.textContent=cmd;
  errEl.appendChild(code);
  const actions=document.createElement('div');
  actions.style.display='flex';
  actions.style.gap='8px';
  actions.style.flexWrap='wrap';
  const copyBtn=document.createElement('button');
  copyBtn.type='button';
  copyBtn.className='update-btn';
  copyBtn.textContent='Copy command';
  copyBtn.onclick=async()=>{
    try{
      if(navigator.clipboard&&navigator.clipboard.writeText){
        await navigator.clipboard.writeText(cmd);
        copyBtn.textContent='Copied';
        setTimeout(()=>{ copyBtn.textContent='Copy command'; }, 1500);
      } else {
        copyBtn.textContent='Clipboard unavailable';
      }
    } catch(_){
      copyBtn.textContent='Copy failed';
    }
  };
  actions.appendChild(copyBtn);
  const retryBtn=document.createElement('button');
  retryBtn.type='button';
  retryBtn.className='update-btn update-primary';
  retryBtn.textContent="I've removed the lock — retry update";
  retryBtn.dataset.target=target;
  retryBtn.onclick=()=>{ applyClearUpdateLock(retryBtn); };
  actions.appendChild(retryBtn);
  errEl.appendChild(actions);
  if(Array.isArray(res.other_locks)&&res.other_locks.length){
    const other=document.createElement('div');
    other.style.marginTop='6px';
    other.style.fontSize='11px';
    other.style.opacity='0.85';
    other.textContent='Other lock files also present: '+res.other_locks.join(', ');
    errEl.appendChild(other);
  }
}
function _normalizeHealthServerIdentity(rawIdentity){
  if(rawIdentity===undefined||rawIdentity===null) return null;
  if(typeof rawIdentity==='string'){
    const value=rawIdentity.trim();
    return value ? value : null;
  }
  const numeric=Number(rawIdentity);
  return Number.isFinite(numeric) ? String(numeric) : null;
}

function _healthResponseServerIdentity(data){
  if(!data||typeof data!=='object') return null;
  const serverStartedAt=_normalizeHealthServerIdentity(data.server_started_at);
  const hasUptimeSeconds=data.uptime_seconds!==null&&data.uptime_seconds!==undefined;
  const uptimeSeconds=hasUptimeSeconds?Number(data.uptime_seconds):NaN;
  const normalizedUptime=Number.isFinite(uptimeSeconds)&&uptimeSeconds>=0 ? uptimeSeconds : null;
  if(serverStartedAt===null&&normalizedUptime===null) return null;
  return {serverStartedAt,uptimeSeconds:normalizedUptime};
}

async function _readHealthServerIdentity() {
  try {
    const r=await fetch(new URL('health', document.baseURI||location.href).href,{cache:'no-store'});
    if(!r.ok) return null;
    const data=await r.json();
    return _healthResponseServerIdentity(data);
  } catch (_) {
    return null;
  }
}
async function forceUpdate(btn){
  const target=btn&&btn.dataset.target;
  if(!target) return;
  const confirmed=await showConfirmDialog({
    title:'Force update '+target+'?',
    message:'This will discard all local changes and delete untracked files in the '+target+' repo, then reset to the latest remote version. This cannot be undone.',
    confirmLabel:'Force update',
    danger:true,
    focusCancel:true,
  });
  if(!confirmed) return;
  btn.disabled=true;btn.textContent='Force updating\u2026';
  const errEl=$('updateError');
  if(errEl){errEl.style.display='none';}
  try{
    const baselineServerIdentity = await _readHealthServerIdentity();
    const res=await api('/api/updates/force',{method:'POST',body:JSON.stringify((()=>{const b={target};const _ch=window._updateData?.[target]?.channel;if(_ch==='stable'||_ch==='experimental')b.channel=_ch;return b;})()),timeoutMs:120000});
    if(!res.ok){
      if(errEl){errEl.textContent='Force update failed: '+(res.message||'unknown error');errEl.style.display='block';}
      btn.disabled=false;btn.textContent='Force update';
      return;
    }
    showToast('Force update applied — restarting…');
    sessionStorage.removeItem('agy-update-checked');
    sessionStorage.removeItem('agy-update-dismissed');
    _waitForServerThenReload({baselineServerIdentity});
  }catch(e){
    if(errEl){errEl.textContent='Force update failed: '+e.message;errEl.style.display='block';}
    btn.disabled=false;btn.textContent='Force update';
  }
}

// Poll /health after an update-triggered restart, then reload.  Replaces the
// blind setTimeout(reload, 2500) that race-lost against slow hardware or
// reverse proxies that 502 immediately when the upstream socket closes (#874).
async function _waitForServerThenReload(opts){
  // Polls the /health endpoint; implementation uses a relative URL so subpath mounts keep working.
  opts=opts||{};
  const interval=opts.interval||500;
  const maxMs=opts.maxMs||15000;
  const baselineServerIdentity=(()=>{
    const rawIdentity=opts.baselineServerIdentity;
    if(!rawIdentity||typeof rawIdentity!=='object'){
      const normalizedServerStartedAt=_normalizeHealthServerIdentity(rawIdentity);
      return normalizedServerStartedAt===null ? null : {serverStartedAt:normalizedServerStartedAt,uptimeSeconds:null};
    }
    const normalizedIdentity={
      serverStartedAt:_normalizeHealthServerIdentity(rawIdentity.serverStartedAt),
      uptimeSeconds:Number.isFinite(Number(rawIdentity.uptimeSeconds))&&Number(rawIdentity.uptimeSeconds)>=0 ? Number(rawIdentity.uptimeSeconds) : null,
    };
    return normalizedIdentity.serverStartedAt===null&&normalizedIdentity.uptimeSeconds===null ? null : normalizedIdentity;
  })();
  window._restartingForUpdate=true;
  const msgEl=$('reconnectMsg');
  const banner=$('reconnectBanner');
  if(msgEl) msgEl.textContent='⏳ Restarting… please wait';
  if(banner) banner.classList.add('visible');
  const deadline=Date.now()+maxMs;
  // Track restart-outage evidence. An outage (failed or non-OK /health probes)
  // followed by a healthy response is a reliable new-instance signal even when
  // only uptime_seconds is comparable and the replacement's uptime is not strictly
  // lower than the captured baseline (e.g. a deployment that strips
  // server_started_at and whose baseline uptime was very low). We require at least
  // TWO consecutive outage probes before trusting it, so a single transient network
  // blip (with the OLD process still up and its uptime merely increasing) cannot
  // trigger a premature reload onto the old server. Both thrown fetch errors AND
  // non-OK responses (e.g. a reverse-proxy 502/503 during restart) count as outage
  // evidence. (#3713 Codex catches)
  let _consecutiveOutages=0;
  const _restartOutageObserved=()=>_consecutiveOutages>=2;
  // Give the server a moment to actually begin its restart before the first
  // probe — otherwise the old process may still respond ok on the first poll.
  await new Promise(r=>setTimeout(r, interval));
  while(Date.now()<deadline){
    try{
      const r=await fetch(new URL('health', document.baseURI||location.href).href,{cache:'no-store'});
      if(r.ok){
        let data={};
        try{ data=await r.json(); }catch(_){}
        if(data && data.status==='ok'){
          const nextServerIdentity=_healthResponseServerIdentity(data);
          if (baselineServerIdentity===null){
            location.reload();
            return;
          }
          if(
            nextServerIdentity===null &&
            (
              baselineServerIdentity.serverStartedAt!==null ||
              baselineServerIdentity.uptimeSeconds!==null
            )
          ){
            // If the replacement server comes back healthy without either
            // identity field after the baseline exposed a comparable identity,
            // treat that healthy response as the new server instead of timing
            // out on an uncomparable identity shape.
            location.reload();
            return;
          }
          if(
            nextServerIdentity!==null &&
            baselineServerIdentity.serverStartedAt!==null &&
            nextServerIdentity.serverStartedAt===null &&
            nextServerIdentity.uptimeSeconds!==null
          ){
            // If the baseline exposed server_started_at but the replacement
            // health response degrades to uptime-only, there is no longer a
            // comparable started_at field. Treat the first healthy uptime-only
            // response as the new server instead of timing out.
            location.reload();
            return;
          }
          if(
            nextServerIdentity!==null&&(
              (baselineServerIdentity.serverStartedAt===null&&nextServerIdentity.serverStartedAt!==null)||
              (baselineServerIdentity.serverStartedAt!==null&&nextServerIdentity.serverStartedAt!==null&&nextServerIdentity.serverStartedAt!==baselineServerIdentity.serverStartedAt)||
              (baselineServerIdentity.uptimeSeconds!==null&&nextServerIdentity.uptimeSeconds!==null&&nextServerIdentity.uptimeSeconds<baselineServerIdentity.uptimeSeconds)
            )
          ){
            location.reload();
            return;
          }
          if(
            _restartOutageObserved() &&
            nextServerIdentity!==null &&
            baselineServerIdentity.serverStartedAt===null &&
            nextServerIdentity.serverStartedAt===null &&
            baselineServerIdentity.uptimeSeconds!==null &&
            nextServerIdentity.uptimeSeconds!==null
          ){
            // Uptime-only on both sides AND we saw a sustained restart outage
            // (>=2 consecutive failed/non-OK probes) before this healthy response:
            // treat that outage as the restart, so reload even though the
            // replacement uptime is not strictly lower than a very-low baseline.
            location.reload();
            return;
          }
          // Healthy response still describing the pre-restart process: this is the
          // OLD server answering, so any earlier outage was a transient blip, not a
          // restart — reset the outage evidence so it can't accumulate into a false
          // positive across unrelated blips.
          _consecutiveOutages=0;
          // Keep polling while /health still describes the pre-restart process.
        }else{
          // Reachable but not status:ok (still starting up) — counts as outage.
          _consecutiveOutages++;
        }
      }else{
        // Non-OK HTTP (e.g. reverse-proxy 502/503 during restart) — outage evidence.
        _consecutiveOutages++;
      }
    }catch(_){ _consecutiveOutages++; /* socket closed during restart — retry */ }
    await new Promise(r=>setTimeout(r, interval));
  }
  if(msgEl) msgEl.textContent='⚠️ Server is taking longer than expected — click Reload when ready';
}

function _pendingCurrentTailUserMessage(messages){
  const list=Array.isArray(messages)?messages:[];
  for(let i=list.length-1;i>=0;i--){
    const msg=list[i];
    if(!msg) continue;
    if(String(msg.role||'')==='user'){
      // Compaction rows are synthetic user-role markers, not submitted turns.
      if(typeof _isContextCompactionMessage==='function'&&_isContextCompactionMessage(msg)) continue;
      return msg;
    }
    if(msg._live||String(msg.role||'')==='tool') continue;
    return null;
  }
  return null;
}

// Precision-only epsilon when matching a transcript row against
// session.pending_started_at. The server stamps the active turn's user row with
// the exact pending_started_at float; state.db round-trips can lose
// sub-microsecond precision, so 1e-6 absorbs float drift without ever treating a
// whole-second (or sub-second) difference as identity. Anything wider is
// ambiguous — a fast "继续"/"continue" double-send lands ~1s after the previous
// turn — and must NOT match: fail toward materializing the pending turn (a
// harmless transient duplicate the settle render clears) rather than hiding a
// turn and moving its attachments onto an earlier row.
const _PENDING_ACTIVE_TURN_TS_EPSILON=1e-6;

function _messageTimestampSeconds(msg){
  if(!msg) return null;
  const raw=msg._ts!=null?msg._ts:msg.timestamp;
  const value=Number(raw);
  return Number.isFinite(value)&&value>0?value:null;
}

/**
 * Exact-identity match for the active turn's user row via its
 * `_active_turn_token`, mirroring the server's `build_active_turn_token`
 * ("{stream_id}:{started_at}") stamped by the eager-checkpoint path. The token
 * embeds the stream_id, which no other turn can share, so a row carrying the
 * current session's token IS the active turn — no timestamp tolerance needed.
 */
function _activeTurnTokenMatches(msg, session){
  if(!msg||typeof msg._active_turn_token!=='string') return false;
  const streamId=session&&session.active_stream_id;
  const startedAt=Number(session&&session.pending_started_at);
  if(!streamId||!Number.isFinite(startedAt)||startedAt<=0) return false;
  const sep=msg._active_turn_token.lastIndexOf(':');
  if(sep<=0) return false;
  if(msg._active_turn_token.slice(0,sep).trim()!==String(streamId).trim()) return false;
  const tokenStarted=Number(msg._active_turn_token.slice(sep+1));
  return Number.isFinite(tokenStarted)&&tokenStarted>0
    && Math.abs(tokenStarted-startedAt)<=_PENDING_ACTIVE_TURN_TS_EPSILON;
}

/**
 * Find the active turn's user row even when the current turn's output already
 * follows it in the transcript.
 *
 * While a turn is live the server can reconcile the current user row into the
 * returned transcript (the sidecar/state.db merge picks it up from state.db,
 * where the agent writes it immediately) while `pending_user_message` is still
 * set. The row is then followed by that same turn's assistant/tool rows, so the
 * strict tail scan in `_pendingCurrentTailUserMessage` stops at the completed
 * assistant and reports "no current user row" — and the caller materializes the
 * pending prompt a SECOND time, rendering a duplicate user bubble until the
 * settle render replaces the list.
 *
 * Scanning past assistant/tool rows alone would be wrong: a user who submits the
 * same text twice in a row (a plain "继续" follow-up) legitimately gets two
 * identical user turns, and matching on text would swallow the new one. The
 * discriminator is therefore exact identity, never proximity: the active turn's
 * public row carries `_active_turn_user`, while private rows carry the server-
 * stamped `_active_turn_token` (stream_id + started_at — unique to this turn),
 * or their timestamp equals `pending_started_at` within a precision-only epsilon
 * that absorbs float/state.db drift but never a full second. A whole-second (or
 * sub-second) mismatch is ambiguous and returns null so the caller materializes
 * the pending turn — the transient duplicate is harmless, hiding a turn + moving
 * its attachments is not. Text equality is still required downstream, so a false
 * match needs identical text AND an exact identity signal.
 */
function _pendingActiveTurnUserMessage(messages, session){
  const startedAt=Number(session?.pending_started_at);
  if(!Number.isFinite(startedAt)||startedAt<=0) return null;
  const list=Array.isArray(messages)?messages:[];
  for(let i=list.length-1;i>=0;i--){
    const msg=list[i];
    if(!msg||String(msg.role||'')!=='user') continue;
    if(typeof _isContextCompactionMessage==='function'&&_isContextCompactionMessage(msg)) continue;
    // Public projections replace the private token with this authoritative marker.
    if(msg._active_turn_user===true) return msg;
    // Unambiguous: the row carries the active turn's exact token
    // (stream_id + started_at) stamped by the server's eager-checkpoint path.
    if(typeof _activeTurnTokenMatches==='function'&&_activeTurnTokenMatches(msg,session)) return msg;
    // Unambiguous: the row's timestamp IS pending_started_at within
    // precision-only float drift (never a whole second).
    const ts=_messageTimestampSeconds(msg);
    if(ts===null) continue;
    if(Math.abs(ts-startedAt)<=_PENDING_ACTIVE_TURN_TS_EPSILON) return msg;
  }
  // Any wider drift (whole-second truncation, a rapid repeat ~1s later) is
  // ambiguous: return null so getPendingSessionMessage() materializes the
  // pending turn rather than guessing.
  return null;
}

function getPendingSessionMessage(session, messagesOverride=null){
  const text=String(session?.pending_user_message||'').trim();
  if(!text) return null;
  const attachments=Array.isArray(session?.pending_attachments)?session.pending_attachments.filter(Boolean):[];
  const sourceMessages=Array.isArray(messagesOverride)?messagesOverride:session?.messages;
  const messages=Array.isArray(sourceMessages)?sourceMessages:[];
  const pendingCandidate={role:'user',content:text};
  const _matchesPending=(row)=>{
    if(!row) return false;
    return typeof _sameTranscriptMessage==='function'
      ? _sameTranscriptMessage(row,pendingCandidate)
      : String(msgContent(row)||'').trim()===text;
  };
  const _adoptExistingRow=(row)=>{
    if(attachments.length&&!row.attachments?.length) row.attachments=attachments;
    return null;
  };
  const currentTailUser=_pendingCurrentTailUserMessage(messages);
  if(currentTailUser){
    const sameCurrentTurn=_matchesPending(currentTailUser);
    if(sameCurrentTurn) return _adoptExistingRow(currentTailUser);
  }
  // Fallback: the current turn's user row is already in the transcript but the
  // strict tail scan above could not see it because this turn's assistant/tool
  // output follows it. Matched by pending_started_at, so previous turns that
  // repeat the same text are unaffected. Guarded with typeof so a partial load
  // (or a static probe that extracts only some helpers) degrades to the
  // original strict-tail behaviour instead of throwing.
  const activeTurnUser=typeof _pendingActiveTurnUserMessage==='function'
    ? _pendingActiveTurnUserMessage(messages,session)
    : null;
  if(activeTurnUser&&activeTurnUser!==currentTailUser&&_matchesPending(activeTurnUser)){
    return _adoptExistingRow(activeTurnUser);
  }
  return {
    role:'user',
    content:text,
    attachments:attachments.length?attachments:undefined,
    _ts:session?.pending_started_at||Date.now()/1000,
    _pending:true,
    _source:session?.pending_user_source||undefined,
  };
}
async function checkInflightOnBoot(sid) {
  const raw = localStorage.getItem(INFLIGHT_KEY);
  if (!raw) return;
  try {
    const {sid: inflightSid, streamId, ts} = JSON.parse(raw);
    if (inflightSid !== sid) { clearInflight(); return; }
    if (S.activeStreamId && S.activeStreamId === streamId) return;
    // Only show banner if the in-flight entry is less than 10 minutes old
    if (Date.now() - ts > 10 * 60 * 1000) { clearInflight(); return; }
    // Check if stream is still active
    const status = await api(`/api/chat/stream/status?stream_id=${encodeURIComponent(streamId || '')}`);
    if (status.active) {
      // Stream is genuinely still running -- show the banner
      showReconnectBanner(t('reconnect_active'));
    } else {
      // Stream finished. Only show banner if reload happened within 90 seconds
      // (longer gap = normal completed session, not a mid-stream reload)
      if (Date.now() - ts < 90 * 1000) {
        showReconnectBanner(t('reconnect_finished'));
      } else {
        clearInflight();  // completed normally, no banner needed
      }
    }
  } catch(e) { clearInflight(); }
}

function _topbarLoadedMessageCount(){
  const messages=Array.isArray(S.messages)?S.messages:[];
  return messages.filter(m=>m&&m.role&&m.role!=='tool').length;
}
function _topbarMessageMetaText(){
  const loadedCount=_topbarLoadedMessageCount();
  const totalCount=Number(S.session&&S.session.message_count);
  const hasTotal=Number.isFinite(totalCount)&&totalCount>0;
  const isTruncated=!!(typeof _messagesTruncated!=='undefined'&&_messagesTruncated);
  if(isTruncated&&hasTotal&&totalCount>loadedCount){
    return `${loadedCount} loaded of ${totalCount} messages`;
  }
  // Fully loaded: use the tool-row-filtered loadedCount, NOT the raw server
  // total (api/routes.py sets message_count to len(_all_msgs), which counts
  // role:"tool" rows the topbar has always excluded). Only the truncated
  // branch above surfaces the raw server total, and only as "loaded of total".
  return t('n_messages',loadedCount);
}
function syncTopbar(){
  if(!S.session){
    document.title=assistantDisplayName();
    if(typeof syncWorkspaceDisplays==='function') syncWorkspaceDisplays();
    if(typeof _syncWorkspaceHeadingState==='function') _syncWorkspaceHeadingState();
    if(typeof syncModelChip==='function') syncModelChip();
    if(typeof syncTerminalButton==='function') syncTerminalButton();
    if(typeof _syncAgyPanelSessionActions==='function') _syncAgyPanelSessionActions();
    else {
      const sidebarName=$('sidebarWsName');
      if(sidebarName && sidebarName.textContent==='Workspace'){
        sidebarName.textContent=t('no_workspace');
      }
    }
    if(typeof syncAppTitlebar==='function') syncAppTitlebar();
    // Update profile chip even when no session is active (e.g. right after profile switch)
    const _profileLabel=$('profileChipLabel');
    if(_profileLabel) _profileLabel.textContent=S.activeProfile||'default';
    const _titleLabel=$('titlebarProfileLabel');
    if(_titleLabel) _titleLabel.textContent=S.activeProfile||'default';
    return;
  }
  const sessionTitle=S.session.title||t('untitled');
  const _topbarTitle=$('topbarTitle');if(_topbarTitle)_topbarTitle.textContent=sessionTitle;
  document.title=sessionTitle+' \u2014 '+assistantDisplayName();
  if(typeof activeSessionHasPendingPromptAttention==='function'&&activeSessionHasPendingPromptAttention()){
    document.title='● '+document.title;
  }
  const _topbarMeta=$('topbarMeta');
  if(_topbarMeta){
    let sourceLabel=(S.session&&(S.session.source_label||S.session.source_tag||S.session.raw_source))||'';
    // Recovered sidecars stamp source_label 'WebUI' (api/session_recovery.py); don't badge a native session as its own source (#3338).
    if(/^webui$/i.test(sourceLabel)) sourceLabel='';
    const metaText=_topbarMessageMetaText();
    _topbarMeta.textContent=metaText;
    if(sourceLabel){
      const badge=document.createElement('span');
      badge.className='topbar-source-badge';
      badge.textContent=sourceLabel+(S.session.read_only?' · read-only':'');
      _topbarMeta.appendChild(document.createTextNode(' '));
      _topbarMeta.appendChild(badge);
    }
  }
  if(typeof syncAppTitlebar==='function') syncAppTitlebar();
  if(typeof _syncWorkspaceHeadingState==='function') _syncWorkspaceHeadingState();
  // If a profile switch just happened, apply its model rather than the session's stale value.
  // S._pendingProfileModel is set by switchToProfile() and cleared here after one application.
  const modelOverride=S._pendingProfileModel;
  let currentModel=S.session.model||'';
  if(modelOverride){
    S._pendingProfileModel=null;
    const providerOverride=S._pendingProfileModelProvider||null;
    S._pendingProfileModelProvider=null;
    _applyModelToDropdown(modelOverride,$('modelSelect'),providerOverride);
    currentModel=modelOverride;
  } else {
    const modelSel=$('modelSelect');
    const rawCurrentModel=String(currentModel||'').trim();
    const hasSessionModel=rawCurrentModel&&rawCurrentModel.toLowerCase()!=='unknown';
    if(!hasSessionModel){
      // Missing/unknown session metadata must not leave the picker on the
      // previously viewed chat's model (#1771). Apply the configured default
      // first, then the first available option only as an HTML fallback.
      const fallback=_applySessionModelFallback(modelSel);
      if(fallback){
        // Defer state mutation + network write while the live model resolution
        // is in flight — sessions.js sets _modelResolutionDeferred=true between
        // the fast-path session render and the resolve_model=1 round-trip.
        // Persisting here would race that resolution and would also issue
        // silent /api/session/update POSTs against imported/read-only CLI
        // sessions whose model field reads "unknown" (#1779 stage-310 review).
        // The visible sel.value change still happens above for UX; only the
        // state mutation + persist defers.
        const deferModelCorrection=Boolean(S.session._modelResolutionDeferred);
        if(!deferModelCorrection){
          S.session.model=fallback.model;
          S.session.model_provider=fallback.model_provider||null;
          currentModel=fallback.model;
          _persistSessionModelCorrection(fallback.model,S.session.model_provider||null);
        }
      }
    } else {
      const applied=_applyModelToDropdown(currentModel,modelSel,S.session.model_provider||null);
      // If the session model is missing from the current provider list, inject
      // a session-scoped option instead of displaying the previous/static
      // selection. Only fall back if that repair path is unavailable.
      if(!applied){
        const deferModelCorrection=Boolean(S.session._modelResolutionDeferred);
        const missingModelIsRoutable=_providerDefersMissingModelFallback(S.session.model_provider||window._activeProvider||null);
        // Also defer if a live model fetch is still in flight — the model may be
        // in the list once the fetch completes. Persisting now would corrupt the
        // session with the wrong model before live models arrive (#1169).
        const liveStillPending=window._activeProvider&&_liveModelFetchPending.has(window._activeProvider);
        if(liveStillPending||missingModelIsRoutable){
          // Live fetch in flight — don't touch sel.value or S.session.model yet.
          // _addLiveModelsToSelect() will re-apply S.session.model once done (#1169).
          // Named custom providers/OpenRouter can also route vendor-prefixed IDs
          // outside the static catalog, so preserve the user's explicit choice.
          if(typeof _ensureModelOptionInDropdown==='function'){
            const sessionOption=_ensureModelOptionInDropdown(currentModel,modelSel,S.session.model_provider||null);
            if(sessionOption) currentModel=sessionOption;
          }
        } else {
          const sessionOption=(typeof _ensureModelOptionInDropdown==='function')
            ? _ensureModelOptionInDropdown(currentModel,modelSel,S.session.model_provider||null)
            : null;
          if(sessionOption){
            currentModel=sessionOption;
          } else {
            const fallback=_applySessionModelFallback(modelSel);
            if(fallback&&!deferModelCorrection){
              S.session.model=fallback.model;
              S.session.model_provider=fallback.model_provider||null;
              currentModel=fallback.model;
              // Persist the correction so the session doesn't re-inject on next load.
              _persistSessionModelCorrection(fallback.model,S.session.model_provider||null);
            }
          }
        }
      }
    }
  }
  if(typeof syncModelChip==='function') syncModelChip();
  if(typeof syncReasoningChip==='function') syncReasoningChip();
  if(typeof syncToolsetsChip==='function') syncToolsetsChip();
  // Show Clear button only when session has messages
  const clearBtn=$('btnClearConv');
  if(clearBtn) clearBtn.style.display=(S.messages&&S.messages.filter(msg=>msg.role!=='tool').length>0)?'':'none';
  if(typeof _syncAgyPanelSessionActions==='function') _syncAgyPanelSessionActions();
  if(typeof syncWorkspaceDisplays==='function') syncWorkspaceDisplays();
  if(typeof syncTerminalButton==='function') syncTerminalButton();
  // modelSelect already set above
  // Update profile chip label.
  // The chip is the profile-SWITCHER trigger (it fronts the profile dropdown) and
  // governs where the next message / new chat routes — both follow the client
  // active profile (the hermes_profile cookie, set only by /api/profile/switch).
  // It must therefore reflect S.activeProfile, NOT the loaded session's profile.
  // #3331 briefly keyed this on S.session.profile so the label would track the
  // session being browsed, but loadSession() never updates S.activeProfile, so
  // opening a cross-profile session made the chip disagree with the dropdown
  // checkmark and lie about message routing (#3635). #3331's legitimate work —
  // scoping project/session operations to the session's own profile — is
  // unaffected by this line.
  const profileLabel=$('profileChipLabel');
  if(profileLabel) profileLabel.textContent=S.activeProfile||'default';
  const titleLabel=$('titlebarProfileLabel');
  if(titleLabel) titleLabel.textContent=S.activeProfile||'default';
}

function msgContent(m){
  // Extract plain text content from a message for filtering
  let c=m.content||'';
  if(Array.isArray(c))c=c.filter(p=>p&&p.type==='text').map(p=>p.text||'').join('').trim();
  return String(c).trim();
}

function _isRecoveryControlMessageText(text){
  const normalized=String(text||'').replace(/\s+/g,' ').trim();
  if(!normalized) return false;
  const systemRecovery=/^\[System:/i.test(normalized)
    && (/continue exactly where you left off/i.test(normalized)
      || /do not retry the same tool call/i.test(normalized));
  const backendRecovery=/^the live worker stopped before this run finished\.?$/i.test(normalized);
  return !!(systemRecovery || backendRecovery);
}
function _isRecoveryControlMessage(m){
  if(!m||m.role==='tool') return false;
  if(m.recovery_control===true) return true;
  // Backward-compat ONLY: strict fully-anchored text match for pre-marker
  // persisted sessions. NOT provider_details_label — a real "Response
  // interrupted" card carries 'Interruption details' and must stay visible.
  return _isRecoveryControlMessageText(msgContent(m)||String(m.content||''));
}
function _assistantAnchorSceneFinalAnswerText(m){
  const scene=m&&m._anchor_activity_scene&&typeof m._anchor_activity_scene==='object'
    ? m._anchor_activity_scene
    : null;
  const text=scene&&typeof scene.final_answer==='string'?scene.final_answer:'';
  return String(text||'').trim()?text:'';
}
function _assistantMessageHasVisibleContent(m){
  if(!m||m.role!=='assistant') return false;
  if(_isRecoveryControlMessage(m)) return false;
  if(_assistantAnchorSceneFinalAnswerText(m)) return true;
  const content=m.content;
  if(typeof content==='string') return !_isAssistantEmptyPlaceholderContent(m, content)&&!!content.trim();
  if(!Array.isArray(content)) return false;
  return content.some(part=>{
    if(typeof part==='string') return !!part.trim();
    if(!part||typeof part!=='object') return false;
    if(part.type==='text'||part.type==='input_text'||part.type==='output_text'){
      return !!String(part.text||part.content||'').trim();
    }
    return false;
  });
}

function _fmtDateSep(d){
  const todayStart=new Date();todayStart.setHours(0,0,0,0);
  const dStart=new Date(d);dStart.setHours(0,0,0,0);
  const diffDays=Math.round((todayStart-dStart)/86400000);
  if(diffDays===0) return 'Today';
  if(diffDays===1) return 'Yesterday';
  if(diffDays>0 && diffDays<7) return dStart.toLocaleDateString([], {weekday:'long'});
  const opts={month:'short', day:'numeric'};
  if(todayStart.getFullYear()!==dStart.getFullYear()) opts.year='numeric';
  return dStart.toLocaleDateString([], opts);
}
const _ERR_MSG_RE=/^(?:\*\*error\b|error:|connection lost|no response received)/i;
function _messageHasReasoningPayload(m){
  if(!m||m.role!=='assistant') return false;
  if(m.reasoning||m.reasoning_content||m.thinking||m._reasoning) return true;
  if(Array.isArray(m.content)) return m.content.some(p=>p&&(p.type==='thinking'||p.type==='reasoning'));
  if(typeof window!=='undefined'&&typeof window._extractInlineThinkingFromContentForRender==='function'){
    const split=window._extractInlineThinkingFromContentForRender(String(m.content||''),'');
    return !!(split&&split.reasoning);
  }
  return /^\s*(?:<think>[\s\S]*?<\/think>|<\|channel\|?>thought\n?[\s\S]*?<channel\|>|<\|turn\|>thinking\n[\s\S]*?<turn\|>)/.test(String(m.content||''));
}
function _isAssistantEmptyPlaceholderContent(m, content){
  if(!m||m.role!=='assistant') return false;
  if(String(content||'').trim()!=='(empty)') return false;
  return _messageHasReasoningPayload(m);
}
function _formatTurnTps(value){
  const n=Number(value);
  if(!Number.isFinite(n)||n<=0) return '';
  const fixed=n>=100?Math.round(n).toLocaleString():n>=10?n.toFixed(1):n.toFixed(1);
  return `${fixed} t/s`;
}
function isTpsDisplayEnabled(){
  return window._showTps===true;
}
function _assistantRoleHtml(tsTitle='', tpsText=''){
  const _bn=assistantDisplayName();
  const tps=(isTpsDisplayEnabled()&&tpsText)?`<span class="msg-tps-inline" title="Tokens per second">${esc(tpsText)}</span>`:'';
  return `<div class="msg-role assistant" ${tsTitle?`title="${esc(tsTitle)}"`:''}><div class="role-icon assistant">${esc(_bn.charAt(0).toUpperCase())}</div><span class="msg-role-name">${esc(_bn)}</span>${tps}</div>`;
}
function _setAssistantTurnTps(turn, tpsText=''){
  if(!turn) return;
  const role=turn.querySelector('.msg-role.assistant');
  if(!role) return;
  let chip=role.querySelector('.msg-tps-inline');
  const text=String(tpsText||'').trim();
  if(!text){if(chip) chip.remove();return;}
  if(!chip){
    chip=document.createElement('span');
    chip.className='msg-tps-inline';
    chip.title='Tokens per second';
    role.appendChild(chip);
  }
  chip.textContent=text;
}
function _setLiveAssistantTps(value){
  _setAssistantTurnTps($('liveAssistantTurn'), isTpsDisplayEnabled()?_formatTurnTps(value):'');
}
function _createAssistantTurn(tsTitle='', tpsText=''){
  const row=document.createElement('div');
  row.className='msg-row assistant-turn';
  row.dataset.role='assistant';
  if(S.session) row.dataset.sessionId=S.session.session_id;
  row.innerHTML=`${_assistantRoleHtml(tsTitle, tpsText)}<div class="assistant-turn-blocks"></div>`;
  return row;
}
function _setLatestAssistantTurnLandmark(turn, isLatest){
  if(!turn) return;
  const label='Latest AGY response';
  if(isLatest){
    if(typeof document!=='undefined'){
      document.querySelectorAll('.assistant-turn[data-latest-assistant-response="true"]').forEach(el=>{
        if(el!==turn) _setLatestAssistantTurnLandmark(el, false);
      });
    }
    turn.setAttribute('role','region');
    turn.setAttribute('aria-label',label);
    turn.dataset.latestAssistantResponse='true';
    return;
  }
  if(turn.getAttribute('role')==='region') turn.removeAttribute('role');
  if(turn.getAttribute('aria-label')===label) turn.removeAttribute('aria-label');
  delete turn.dataset.latestAssistantResponse;
}
function _assistantTurnBlocks(turn){
  return turn?turn.querySelector('.assistant-turn-blocks'):null;
}
function _assistantMessageBelongsInWorklog(m, rawIdx, toolCallAssistantIdxs, visibleContent, opts){
  if(!m||m.role!=='assistant') return false;
  if(m._error) return false;
  const isTurnFinalAssistant=!!(opts&&opts.isTurnFinalAssistant);
  const visibleText=String(visibleContent!==undefined?visibleContent:msgContent(m)||'').trim();
  const hasVisibleText=!!visibleText&&!_isAssistantEmptyPlaceholderContent(m, visibleText);
  if(m._live) return true;
  if(hasVisibleText&&m._anchor_activity_scene) return false;
  if(hasVisibleText&&isTurnFinalAssistant) return false;
  if(m._activityBurstId!==undefined||m._liveSegmentSeq!==undefined) return true;
  const hasToolMetadata=!!(
    (toolCallAssistantIdxs&&toolCallAssistantIdxs.has(rawIdx))||
    (Array.isArray(m.tool_calls)&&m.tool_calls.length)||
    (Array.isArray(m.content)&&m.content.some(p=>p&&typeof p==='object'&&p.type==='tool_use'))
  );
  if(hasVisibleText) return false;
  if(hasToolMetadata) return true;
  return false;
}
function _assistantThinkingBelongsInWorklog(m, rawIdx, toolCallAssistantIdxs){
  return !!_assistantReasoningPayloadText(m)||_assistantMessageBelongsInWorklog(m, rawIdx, toolCallAssistantIdxs);
}
function _assistantReasoningPayloadText(m){
  if(!m||m.role!=='assistant') return '';
  const direct=m.reasoning_content||m.reasoning||m.thinking||m._reasoning||'';
  if(String(direct||'').trim()) return String(direct).trim();
  if(Array.isArray(m.content)){
    const parts=m.content
      .filter(p=>p&&typeof p==='object'&&(p.type==='thinking'||p.type==='reasoning'))
      .map(p=>p.text||p.content||'')
      .filter(text=>String(text||'').trim());
    return parts.join('\n').trim();
  }
  const text=String(m.content||'');
  if(typeof window!=='undefined'&&typeof window._extractInlineThinkingFromContentForRender==='function'){
    const split=window._extractInlineThinkingFromContentForRender(text,'');
    if(split&&String(split.reasoning||'').trim()) return String(split.reasoning).trim();
  }
  // Extract a LEADING thinking block even when visible answer text follows it
  // (e.g. "<think>…</think>4"). The matching display-content stripper
  // (_stripLeadingAssistantThinkingMarkup) is non-anchored, so the extractor must
  // be too — a trailing `$` anchor here dropped the reasoning whenever the turn
  // also had a visible answer, hiding the Thinking card entirely (#3401 regression
  // vs master, which used the non-anchored form). (#3709/#3592 family)
  const thinkMatch=text.match(/^\s*<think>([\s\S]*?)<\/think>\s*/);
  if(thinkMatch) return thinkMatch[1].trim();
  const thoughtMatch=text.match(/^\s*<\|channel\|?>thought\n?([\s\S]*?)<channel\|>\s*/);
  if(thoughtMatch) return thoughtMatch[1].trim();
  const turnMatch=text.match(/^\s*<\|turn\|>thinking\n([\s\S]*?)<turn\|>\s*/);
  if(turnMatch) return turnMatch[1].trim();
  return '';
}
function _stripLeadingAssistantThinkingMarkup(content){
  let out=String(content||'');
  const thinkMatch=out.match(/^\s*<think>([\s\S]*?)<\/think>\s*/);
  if(thinkMatch) out=out.replace(/^\s*<think>[\s\S]*?<\/think>\s*/,'').trimStart();
  const thoughtMatch=out.match(/^\s*<\|channel\|?>thought\n?([\s\S]*?)<channel\|>\s*/);
  if(thoughtMatch) out=out.replace(/^\s*<\|channel\|?>thought\n?[\s\S]*?<channel\|>\s*/,'').trimStart();
  const turnMatch=out.match(/^\s*<\|turn\|>thinking\n([\s\S]*?)<turn\|>\s*/);
  if(turnMatch) out=out.replace(/^\s*<\|turn\|>thinking\n[\s\S]*?<turn\|>\s*/,'').trimStart();
  return out;
}
function _assistantVisibleContentForReasoningCompare(m){
  if(!m||m.role!=='assistant') return '';
  const anchorFinal=_assistantAnchorSceneFinalAnswerText(m);
  if(anchorFinal) return anchorFinal;
  let content=m.content||'';
  if(Array.isArray(content)){
    content=content.filter(p=>p&&p.type==='text').map(p=>p.text||p.content||'').join('\n');
  }
  if(typeof content==='string'){
    if(typeof window!=='undefined'&&typeof window._extractInlineThinkingFromContentForRender==='function'){
      const split=window._extractInlineThinkingFromContentForRender(content,'');
      content=split&&typeof split.content==='string'?split.content:_stripLeadingAssistantThinkingMarkup(content);
    } else {
      content=_stripLeadingAssistantThinkingMarkup(content);
    }
  }
  if(_isMarkerOnlyAssistantCompressionMessage(m)){
    content='**Error:** No response received after context compression. Please retry.';
  }
  if(_isAssistantEmptyPlaceholderContent(m, content)) return '';
  return String(content||'');
}
function _assistantTurnFinalVisibleContentMap(visWithIdx){
  const out=new Map();
  let runIdxs=[];
  let finalVisible='';
  const flush=()=>{
    for(const idx of runIdxs) out.set(idx, finalVisible);
    runIdxs=[];
    finalVisible='';
  };
  for(const entry of visWithIdx||[]){
    const m=entry&&entry.m;
    if(m&&m.role==='assistant'){
      runIdxs.push(entry.rawIdx);
      const visible=_assistantVisibleContentForReasoningCompare(m);
      if(String(visible||'').trim()) finalVisible=visible;
    }else{
      flush();
    }
  }
  flush();
  return out;
}
function _assistantTurnVisibleContentMap(visWithIdx){
  const out=new Map();
  let runIdxs=[];
  let visibleTexts=[];
  const flush=()=>{
    for(const idx of runIdxs) out.set(idx, visibleTexts.slice());
    runIdxs=[];
    visibleTexts=[];
  };
  for(const entry of visWithIdx||[]){
    const m=entry&&entry.m;
    if(m&&m.role==='assistant'){
      runIdxs.push(entry.rawIdx);
      const visible=_assistantVisibleContentForReasoningCompare(m);
      if(String(visible||'').trim()) visibleTexts.push(visible);
    }else{
      flush();
    }
  }
  flush();
  return out;
}
function _worklogReasoningTextFromMessage(m, rawIdx, toolCallAssistantIdxs, visibleContent, turnFinalVisibleContent, turnVisibleContents){
  const thinkingText=_assistantReasoningPayloadText(m);
  const visibleTexts=Array.isArray(turnVisibleContents)?turnVisibleContents:[];
  return _stripVisibleAssistantEchoFromThinking(thinkingText, visibleContent, turnFinalVisibleContent, ...visibleTexts);
}
function _worklogDetailsExpandedDefault(){
  return window._worklogDetailsExpandedByDefault===true;
}
function _applyWorklogDetailsExpandedDefault(root){
  const scope=root&&root.querySelectorAll?root:document;
  const open=_worklogDetailsExpandedDefault();
  scope.querySelectorAll('.thinking-card').forEach(card=>{
    card.classList.toggle('open', open);
  });
  scope.querySelectorAll('.tool-group[data-tool-worklog-tool-group="1"],.tool-worklog-tool-group').forEach(group=>{
    group.classList.toggle('open', open);
    group.classList.toggle('tool-worklog-tool-group-collapsed', !open);
    const summary=group.querySelector('.tool-group-head,.tool-worklog-tool-group-head');
    if(summary) summary.setAttribute('aria-expanded', String(open));
  });
}
const _worklogDetailDisclosureSelector='.thinking-card,.tool-card,.tool-group[data-tool-worklog-tool-group="1"],.tool-worklog-tool-group';
function _worklogDetailTextKey(text, maxLen){
  return String(text||'').replace(/\s+/g,' ').trim().slice(0,maxLen||160);
}
function _worklogDetailHashKey(value){
  const s=String(value||'');
  let hash=2166136261;
  for(let i=0;i<s.length;i++){
    hash^=s.charCodeAt(i);
    hash=Math.imul(hash,16777619)>>>0;
  }
  return hash.toString(36);
}
function _worklogDetailBaseKey(el){
  if(!el||!el.classList) return '';
  const activity=el.closest&&el.closest('.agent-activity-group,.tool-worklog-group[data-tool-worklog-group="1"],.tool-call-group[data-tool-call-group="1"],.live-worklog[data-live-worklog-shell="1"]');
  const scope=activity?[
    activity.getAttribute('data-anchor-stream-id')?`stream:${activity.getAttribute('data-anchor-stream-id')}`:'',
    activity.getAttribute('data-activity-disclosure-key')||'',
    activity.getAttribute('data-tool-worklog-key')||'',
    activity.getAttribute('data-live-segment-seq')||'',
    activity.getAttribute('data-activity-burst-id')||'',
  ].filter(Boolean).join('|'):'';
  if(el.classList.contains('thinking-card')){
    const row=el.closest('.agent-activity-thinking,.thinking-card-row');
    const stable=row&&(
      row.getAttribute('data-thinking-key')||
      row.getAttribute('data-live-thinking-key')||
      row.getAttribute('data-live-segment-seq')||
      row.getAttribute('data-activity-burst-id')||
      row.id||
      ''
    );
    return `thinking:${scope}:${stable||'ordinal'}`;
  }
  if(el.classList.contains('tool-card')){
    const row=el.closest('.tool-card-row');
    const tid=row&&(
      row.getAttribute('data-tool-disclosure-key')||
      row.getAttribute('data-live-tid')||
      row.getAttribute('data-tool-call-id')||
      row.getAttribute('data-tool-id')||
      ''
    );
    const label=row&&(row.dataset&&row.dataset.toolActionLabel)||'';
    const name=el.querySelector('.tool-card-name');
    return `tool:${scope}:${tid||label||_worklogDetailTextKey(name?name.textContent:'tool',80)}`;
  }
  if(el.matches&&el.matches('.tool-group[data-tool-worklog-tool-group="1"],.tool-worklog-tool-group')){
    const stable=
      el.getAttribute('data-tool-group-disclosure-key')||
      el.getAttribute('data-activity-disclosure-key')||
      el.getAttribute('data-tool-worklog-key')||
      el.getAttribute('data-live-segment-seq')||
      el.getAttribute('data-activity-burst-id')||
      'group';
    return `tool-group:${scope}:${stable}`;
  }
  return '';
}
function _worklogDetailDisclosureIsOpen(el){
  return !!(el&&el.classList&&el.classList.contains('open'));
}
function _worklogDetailScrollableBody(el){
  if(!el||!el.querySelector) return null;
  return el.querySelector('.thinking-card-body,.tool-card-detail');
}
function _setWorklogDetailDisclosureOpen(el, open){
  if(!el||!el.classList) return;
  // #5966 (Codex F2 r2): restoring an OPEN state on a settled Transparent Stream
  // tool row whose detail was deferred must MATERIALIZE the body first, or the
  // card restores open-but-empty after an in-session renderMessages() rebuild
  // (e.g. the next send re-defers it, then this toggles .open with no content).
  if(open){
    const drow=(el.matches&&el.matches('.transparent-event-row[data-transparent-detail-deferred="1"]'))
      ? el
      : (el.closest&&el.closest('.transparent-event-row[data-transparent-detail-deferred="1"]'));
    if(drow&&typeof _materializeTransparentToolDetail==='function') _materializeTransparentToolDetail(drow);
  }
  el.classList.toggle('open', !!open);
  if(el.matches&&el.matches('.tool-group[data-tool-worklog-tool-group="1"],.tool-worklog-tool-group')){
    el.classList.toggle('tool-worklog-tool-group-collapsed', !open);
    const summary=el.querySelector('.tool-group-head,.tool-worklog-tool-group-head');
    if(summary) summary.setAttribute('aria-expanded', String(!!open));
  }
}
function _worklogDetailDisclosureKeyForElement(el, counts){
  const base=_worklogDetailBaseKey(el);
  if(!base) return '';
  const idx=counts[base]||0;
  counts[base]=idx+1;
  return `${base}#${idx}`;
}
function _captureWorklogDetailDisclosureState(root){
  const state=new Map();
  if(!root||!root.querySelectorAll) return state;
  // Stamp the capturing session so a restore can't replay one session's
  // disclosure state onto another. Cross-session switches currently wipe
  // #msgInner (sessions.js loading placeholder) so the capture is normally
  // empty, but the ordinal/derived keys carry no session id — this stamp makes
  // the isolation explicit instead of depending on that wipe invariant. (Opus #4063.)
  try{ state._sid=S.session?S.session.session_id:null; }catch(_){ state._sid=null; }
  const counts=Object.create(null);
  root.querySelectorAll(_worklogDetailDisclosureSelector).forEach(el=>{
    const key=_worklogDetailDisclosureKeyForElement(el, counts);
    if(!key) return;
    const body=_worklogDetailScrollableBody(el);
    state.set(key,{
      open:_worklogDetailDisclosureIsOpen(el),
      scrollTop:body?Math.max(0,Number(body.scrollTop)||0):0,
    });
  });
  return state;
}
function _restoreWorklogDetailDisclosureState(root, state){
  if(!root||!root.querySelectorAll||!state||!state.size) return;
  // Don't restore a snapshot captured under a different session.
  try{ if(state._sid!==undefined && state._sid!==(S.session?S.session.session_id:null)) return; }catch(_){ /* fall through */ }
  const counts=Object.create(null);
  root.querySelectorAll(_worklogDetailDisclosureSelector).forEach(el=>{
    const key=_worklogDetailDisclosureKeyForElement(el, counts);
    if(!key||!state.has(key)) return;
    const saved=state.get(key);
    const open=(saved&&typeof saved==='object'&&'open' in saved)?saved.open:saved;
    _setWorklogDetailDisclosureOpen(el, open);
    const scrollTop=(saved&&typeof saved==='object')?Number(saved.scrollTop):0;
    if(open&&Number.isFinite(scrollTop)&&scrollTop>0){
      const body=_worklogDetailScrollableBody(el);
      if(body) body.scrollTop=Math.min(scrollTop, Math.max(0, body.scrollHeight-body.clientHeight));
    }
  });
}
function _thinkingCardHtml(text, open){
  const clean=_sanitizeThinkingDisplayText(text);
  const copyBtn=`<button class="thinking-copy-btn" onclick="event.stopPropagation();_copyThinkingText(this)" title="${t('copy')}" aria-label="${t('copy')}">${li('copy',12)}</button>`;
  const shouldOpen=!!open||_worklogDetailsExpandedDefault();
  const classes=`thinking-card${shouldOpen?' open':''}`;
  return `<div class="${classes}"><div class="thinking-card-header" onclick="this.parentElement.classList.toggle('open')"><span class="thinking-card-icon">${li('lightbulb',14)}</span><span class="thinking-card-label">${t('thinking')}</span><span class="thinking-card-btn-row">${copyBtn}<span class="thinking-card-toggle">${li('chevron-right',12)}</span></span></div><div class="thinking-card-body"><pre>${esc(clean)}</pre></div></div>`;
}
function isSimplifiedToolCalling(){
  return window._simplifiedToolCalling!==false;
}
function _thinkingActivityNode(text, open, disclosureKey){
  const row=document.createElement('div');
  row.className='agent-activity-thinking';
  row.setAttribute('data-worklog-thinking-card','1');
  if(disclosureKey) row.setAttribute('data-thinking-key', String(disclosureKey));
  row.innerHTML=_thinkingCardHtml(text, open);
  _renderThinkingInto(row,text);
  return row;
}
function chatActivityMode(){
  if(typeof window==='undefined') return 'compact_worklog';
  const mode=window._chatActivityDisplayMode;
  if(mode==='compact_worklog'||mode==='transparent_stream'||mode==='hide_all_activity') return mode;
  return window._transparentStream ? 'transparent_stream' : 'compact_worklog';
}
function isTransparentStream(){
  return chatActivityMode()==='transparent_stream';
}
function isFinalAnswerOnlyMode(){
  return chatActivityMode()==='hide_all_activity';
}
function isCompactWorklogMode(){
  return isSimplifiedToolCalling()&&chatActivityMode()==='compact_worklog';
}
if(typeof window!=='undefined'){
  window.chatActivityMode=chatActivityMode;
  window.isTransparentStream=isTransparentStream;
  window.isFinalAnswerOnlyMode=isFinalAnswerOnlyMode;
  window.isCompactWorklogMode=isCompactWorklogMode;
}
function _toolShortName(name){
  const raw=String(name||'').trim();
  if(!raw) return 'tool';
  if(raw.startsWith('mcp__')){
    const parts=raw.split('__').filter(Boolean);
    if(parts.length>1) return parts.slice(1).join('/');
  }
  if(raw.startsWith('mcp.')){
    const parts=raw.split('.').filter(Boolean);
    if(parts.length>1) return parts.slice(1).join('/');
  }
  return raw;
}
function _transparentEventPreview(text){
  const clean=_sanitizeThinkingDisplayText(String(text||'')).replace(/\s+/g,' ').trim();
  if(!clean) return '';
  return clean.length>180?`${clean.slice(0,177)}...`:clean;
}
function _transparentToolStatus(tc, settled){
  if(tc&&tc.is_error) return 'Failed';
  if(tc&&tc.done===false) return settled?'Interrupted':'Running';
  return 'Completed';
}
// Quiet one-line summary for a collapsed transparent tool row (#4658).
// The transparent view overrides the row name to the bare tool name
// (_toolShortName, e.g. "read_file"/"terminal"), so — unlike the worklog view —
// it has no action-label carrying the target, and buildToolCard's collapsed
// preview is blanked for the common arg/shell case (the #4411 suppression that
// assumes the name carries the target). That left transparent rows showing only
// the bare tool name with no hint of what each call did. Rebuild a summary from
// the call's TARGET (path/command/query/skill/...) — NOT the raw result JSON —
// so it stays consistent with the "keep collapsed previews quiet" intent
// (test_tool_card_preview_summary.py) while restoring "understand the call
// without expanding it".
function _transparentToolSummary(tc){
  if(!tc||typeof tc!=='object') return '';
  // Explicit progress text (e.g. subagent_progress) wins while still running.
  const explicit=String(tc.preview||'').trim();
  if(tc.done===false&&explicit) return _shortToolLabel(explicit,160);
  // Target-based summary only (path/command/query/skill). Deliberately NO generic
  // arg-preview fallback: a call with args but no real target (e.g. `terminal`
  // with only {workdir} or an unknown tool with {mode:"dry-run"}) must yield an
  // EMPTY collapsed preview rather than dumping a raw arg snippet — that keeps the
  // collapsed row quiet and consistent with the no-args case (#4658 review).
  const target=typeof _toolVisibleTargetLabel==='function'?_toolVisibleTargetLabel(tc,{limit:160,rangeFirst:true}):'';
  if(target) return target;
  return '';
}
function _showTransparentCopiedFeedback(control,row,opts){
  if(!control&&!row) return;
  opts=opts||{};
  // A live-row refresh may replace a header's copy control with new markup.
  // Keep the lifetime on the persistent row, then resolve the currently visible
  // control for every render/expiry rather than retaining a detached button.
  const feedbackRow=row||(control&&control.closest?control.closest('.transparent-event-row'):null);
  const owner=feedbackRow||control;
  if(!owner) return;
  const currentControl=()=>{
    if(feedbackRow&&feedbackRow.querySelector){
      return feedbackRow.querySelector('.transparent-event-copy,.thinking-copy-btn');
    }
    return control||null;
  };
  const connectedControl=()=>{
    const target=currentControl();
    if(!target||target.isConnected!==true) return null;
    if(feedbackRow&&feedbackRow.isConnected!==true) return null;
    return target;
  };
  const clearFeedbackState=(state)=>{
    if(state&&state.timer){
      clearTimeout(state.timer);
      state.timer=null;
    }
    if(owner._transparentCopiedFeedback===state) delete owner._transparentCopiedFeedback;
  };
  const restoreControl=(target)=>{
    if(!target) return;
    const normal=target._transparentCopiedFeedbackNormal;
    if(!normal) return;
    target.innerHTML=normal.innerHTML;
    if(target.style){
      if(normal.styleCssText!==null) target.style.cssText=normal.styleCssText;
      else target.style.color=normal.color||'';
    }
    if(normal.titleAttr===null){
      if(target.removeAttribute) target.removeAttribute('title');
    }else if(target.setAttribute){
      target.setAttribute('title',normal.titleAttr);
    }
    if(normal.ariaLabel===null){
      if(target.removeAttribute) target.removeAttribute('aria-label');
    }else if(target.setAttribute){
      target.setAttribute('aria-label',normal.ariaLabel);
    }
    delete target._transparentCopiedFeedbackNormal;
  };
  const renderCopiedControl=(target)=>{
    if(!target) return;
    if(!target._transparentCopiedFeedbackNormal){
      target._transparentCopiedFeedbackNormal={
        innerHTML:target.innerHTML,
        color:target.style?target.style.color:undefined,
        styleCssText:target.style&&typeof target.style.cssText==='string'?target.style.cssText:null,
        titleAttr:target.getAttribute?target.getAttribute('title'):null,
        ariaLabel:target.getAttribute?target.getAttribute('aria-label'):null,
      };
    }
    const copiedLabel=t('copied')||'Copied';
    target.innerHTML=typeof li==='function'?li('check',11):'✓';
    if(target.style){
      target.style.color='var(--accent)';
      target.style.opacity='1';
    }
    if(target.setAttribute) target.setAttribute('title',copiedLabel);
    else target.title=copiedLabel;
    if(target.setAttribute) target.setAttribute('aria-label',copiedLabel);
  };
  const now=Date.now();
  let state=owner._transparentCopiedFeedback;
  if(opts.rehydrate){
    if(!state) return;
    const target=connectedControl();
    if(!target){
      clearFeedbackState(state);
      return;
    }
    if(!state.expiresAt||state.expiresAt<=now){
      if(state.timer) clearTimeout(state.timer);
      state.timer=null;
      restoreControl(target);
      if(owner._transparentCopiedFeedback===state) delete owner._transparentCopiedFeedback;
      return;
    }
    renderCopiedControl(target);
    return;
  }
  const target=connectedControl();
  if(!target){
    if(state) clearFeedbackState(state);
    return;
  }
  if(!state){
    state={timer:null,generation:0,expiresAt:0};
    owner._transparentCopiedFeedback=state;
  }else if(state.timer){
    clearTimeout(state.timer);
  }
  state.generation+=1;
  const generation=state.generation;
  state.expiresAt=now+1500;
  renderCopiedControl(target);
  state.timer=setTimeout(()=>{
    if(owner._transparentCopiedFeedback!==state||state.generation!==generation) return;
    const expiryTarget=connectedControl();
    if(!expiryTarget){
      state.timer=null;
      delete owner._transparentCopiedFeedback;
      return;
    }
    state.timer=null;
    restoreControl(expiryTarget);
    delete owner._transparentCopiedFeedback;
  },1500);
}
function _copyEventToClipboard(row,control){
  if(!row) return;
  const type=row.getAttribute('data-event-type');
  let text='';
  let label='event';
  if(type==='tool'){
    const tc=row._tcData||{};
    const fallbackName=row.getAttribute('data-event-name')||row.getAttribute('data-tool-name')||'tool';
    label=`tool ${tc.name||fallbackName}`;
    const parts=[`tool: ${tc.name||fallbackName}`];
    if(tc.args&&Object.keys(tc.args).length){
      // Redact secret-bearing arg values before copying to clipboard, mirroring
      // the Full-tab render — content args can be long commands with secrets
      // past the first line (#4928 gate).
      let argsForCopy=tc.args;
      if(typeof _redactToolTargetLabel==='function'){
        try{
          argsForCopy={};
          Object.entries(tc.args).forEach(([k,v])=>{
            argsForCopy[k]=typeof v==='string'?_redactToolTargetLabel(v):v;
          });
        }catch(e){ argsForCopy=tc.args; }
      }
      parts.push('args: '+JSON.stringify(argsForCopy,null,2));
    }
    if(tc.snippet) parts.push('output:\n'+String(tc.snippet));
    if(parts.length===1){
      const argsText=Array.from(row.querySelectorAll('.tool-card-args .tool-arg-pair'))
        .map(pair=>String(pair.textContent||'').trim())
        .filter(Boolean)
        .join('\n');
      const outputText=String((row.querySelector('.tool-card-result pre')||{}).textContent||'').trim();
      if(argsText) parts.push('args:\n'+argsText);
      if(outputText) parts.push('output:\n'+outputText);
    }
    text=parts.join('\n');
  }else if(type==='thinking'){
    const pre=row.querySelector('.thinking-card-body pre');
    text=pre?pre.textContent:(row.textContent||'').replace(/^\s*Thinking\s*/i,'');
    label='thinking';
  }else{
    text=row.textContent||'';
  }
  if(!text) return;
  const copied=()=>_showTransparentCopiedFeedback(control,row);
  const fallback=()=>{
    let ta=null;
    try{
      ta=document.createElement('textarea');
      ta.value=text;
      ta.setAttribute('readonly','');
      ta.style.position='absolute';
      ta.style.left='-9999px';
      document.body.appendChild(ta);
      ta.select();
      const ok=document.execCommand('copy');
      if(ok) copied();
      if(typeof showToast==='function') showToast(ok?(t('copied')||'Copied'):(t('copy_failed')||'Copy failed'),1600);
    }catch(_){
      if(typeof showToast==='function') showToast(t('copy_failed')||'Copy failed',2000,'error');
    }finally{
      if(ta&&ta.parentNode) ta.parentNode.removeChild(ta);
    }
  };
  if(navigator&&navigator.clipboard&&navigator.clipboard.writeText){
    navigator.clipboard.writeText(text).then(()=>{
      copied();
      if(typeof showToast==='function') showToast(`${t('copied')||'Copied'} ${label}`,1600);
    }).catch(fallback);
  }else{
    fallback();
  }
}
function _attachCopyButton(header){
  if(!header) return null;
  const bindCopyButton=(btn)=>{
    if(!btn) return null;
    const row=header.closest?header.closest('.transparent-event-row'):null;
    const feedbackState=row&&row._transparentCopiedFeedback;
    const feedbackActive=!!(feedbackState&&Number(feedbackState.expiresAt)>Date.now());
    btn.classList.add('transparent-event-copy');
    btn.setAttribute('role','button');
    btn.setAttribute('tabindex','0');
    btn.setAttribute('data-transparent-copy','1');
    if(!btn._transparentCopiedFeedback&&!feedbackActive){
      btn.setAttribute('aria-label',t('copy')||'Copy');
      btn.title=t('copy')||'Copy';
    }
    const handler=function(ev){
      ev.stopPropagation();
      ev.preventDefault();
      _copyEventToClipboard(header.closest('.transparent-event-row'),btn);
    };
    btn.onclick=handler;
    btn.onkeydown=function(ev){
      if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();handler(ev);}
    };
    if(btn.parentNode&&typeof _showTransparentCopiedFeedback==='function'){
      _showTransparentCopiedFeedback(btn,row,{rehydrate:true});
    }
    return btn;
  };
  // Reuse ANY existing copy button (handles both .transparent-event-copy
  // added by this function AND the legacy .thinking-copy-btn baked into the
  // thinking-card template). Returning the existing one prevents the
  // duplicate copy buttons that appeared in thinking boxes.
  const existing=header.querySelector('.transparent-event-copy,.thinking-copy-btn');
  if(existing){
    // Normalise the class so CSS treats them identically.
    return bindCopyButton(existing);
  }
  const btn=document.createElement('span');
  btn.className='transparent-event-copy';
  btn.innerHTML=`<svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>`;
  bindCopyButton(btn);
  // Place the copy button at a FIXED flexbox position regardless of
  // whether a toggle or status badge is present: always right before
  // the toggle. The CSS uses flexbox order to keep it visually stable
  // even if other elements are inserted between header and toggle.
  const toggle=header.querySelector('.tool-card-toggle,.thinking-card-toggle');
  if(toggle&&toggle.parentNode===header) header.insertBefore(btn,toggle);
  else header.appendChild(btn);
  const row=header.closest?header.closest('.transparent-event-row'):null;
  if(typeof _showTransparentCopiedFeedback==='function') _showTransparentCopiedFeedback(btn,row,{rehydrate:true});
  return btn;
}
function _transparentEventCountLabel(toolCount){
  return toolCount?`Trace: ${toolCount} ${toolCount===1?'tool':'tools'}`:'Trace';
}
function _setTransparentDetailMode(tab, mode){
  const row=tab&&tab.closest?tab.closest('.transparent-event-row'):null;
  const detail=row&&row.querySelector('.tool-card-detail');
  if(!detail) return;
  const next=mode==='output'?'output':'full';
  detail.setAttribute('data-transparent-detail-mode',next);
  detail.querySelectorAll('.transparent-detail-mode').forEach(el=>{
    el.classList.toggle('active', el===tab || el.getAttribute('data-mode')===next);
  });
}
function _setTransparentCardOpen(card, open){
  if(!card) return;
  const expanded=!!open;
  const row=card.closest&&card.closest('.transparent-event-row');
  // #5966: a settled tool row whose detail body was deferred at render must
  // materialize it the first time it's opened (before we flip .open so the
  // detail exists for the same paint). No-op on live/already-materialized rows.
  if(expanded&&row&&row.getAttribute('data-transparent-detail-deferred')==='1'){
    _materializeTransparentToolDetail(row);
  }
  card.classList.toggle('open',expanded);
  if(row) row.setAttribute('data-expanded',expanded?'1':'0');
  const header=card.querySelector('.tool-card-header,.thinking-card-header');
  if(header) header.setAttribute('aria-expanded',expanded?'true':'false');
}
// #5966: build the deferred `.tool-card-detail` for a settled transparent tool
// row on first expand — the lazy counterpart of the eager build in
// _decorateTransparentEventRow. Recovers the tool call from the in-memory stash
// or, after a _sessionHtmlCache innerHTML round-trip drops the JS property, from
// the row's data-anchor-row-id → S.messages scene (the #5839 recovery pattern).
// Idempotent: clears the deferred flag and runs anchor-suppressed post-processing
// (Prism / copy buttons / KaTeX / Mermaid / trees) on just the new subtree so an
// expanded deferred row is byte-identical to the eager path.
// #5966: does this tool call have a detail body worth deferring? Mirrors
// buildToolCard's `hasDetail` (snippet OR args, gated by _toolCardAllowsDetail)
// so we only mark a row deferred/expandable when there's genuinely something to
// build — a detail-less tool row keeps its `tool-card-no-detail` (no chevron).
function _transparentToolRowHasDetail(tc){
  if(!tc||typeof tc!=='object') return false;
  const hasRaw=!!tc.snippet||(tc.args&&typeof tc.args==='object'&&Object.keys(tc.args).length>0);
  if(!hasRaw) return false;
  if(typeof _toolActionKind==='function'&&typeof _toolCardAllowsDetail==='function'){
    try{ return !!_toolCardAllowsDetail(_toolActionKind(tc), tc); }catch(_){ return true; }
  }
  return true;
}
function _materializeTransparentToolDetail(row){
  if(!row||row.getAttribute('data-transparent-detail-deferred')!=='1') return false;
  const card=row.querySelector('.tool-card');
  if(!card){ row.removeAttribute('data-transparent-detail-deferred'); return false; }
  let tc=row._deferredToolCall;
  if(!tc){
    tc=_transparentToolCallFromRowDataset(row);
  }
  if(!tc){ row.removeAttribute('data-transparent-detail-deferred'); return false; }
  const status=String(row.getAttribute('data-event-status')||_transparentToolStatus(tc,true));
  row.removeAttribute('data-transparent-detail-deferred');
  row._deferredToolCall=null;
  if(!card.querySelector('.tool-card-detail')){
    // Codex F1(r2): rebuild through the CANONICAL buildToolCard() detail path, not
    // the thinner _transparentToolDetailHtml() — the latter drops diff coloring,
    // "Show diff/Show more", and canonical shell-command detail that buildToolCard
    // produces, so an expanded deferred row must transplant buildToolCard's own
    // `.tool-card-detail` to stay byte-identical to the eager path.
    let sourceDetail=null;
    try{
      const rebuilt=buildToolCard(tc);
      sourceDetail=rebuilt&&rebuilt.querySelector('.tool-card-detail');
    }catch(_){ sourceDetail=null; }
    if(sourceDetail){
      card.appendChild(sourceDetail);   // move the canonical detail node onto the live card
    }else{
      // Fallback (e.g. buildToolCard unavailable): the lighter detail is better than none.
      card.insertAdjacentHTML('beforeend',_transparentToolDetailHtml(tc,status));
    }
    const detail=card.querySelector('.tool-card-detail');
    if(detail&&!detail.querySelector('.transparent-detail-modes')){
      const modes=document.createElement('div');
      modes.className='transparent-detail-modes';
      modes.setAttribute('role','tablist');
      modes.innerHTML=`<span class="transparent-detail-mode active" role="tab" tabindex="0" data-mode="full" onclick="_setTransparentDetailMode(this,'full')">Full</span><span class="transparent-detail-mode" role="tab" tabindex="0" data-mode="output" onclick="_setTransparentDetailMode(this,'output')">Output</span>`;
      const firstChild=detail.firstChild;
      if(firstChild&&firstChild.parentNode===detail) detail.insertBefore(modes, firstChild);
      else detail.appendChild(modes);
      detail.setAttribute('data-transparent-detail-mode','full');
    }
    // Match the eager path's post-processing so highlight/copy/KaTeX/Mermaid land.
    if(typeof _postProcessWithAnchorSuppression==='function'){
      requestAnimationFrame(()=>{ try{ _postProcessWithAnchorSuppression(card); }catch(_){ } });
    }
  }
  return true;
}
// Recover a tool call for a deferred row whose _deferredToolCall JS property was
// dropped by an innerHTML cache round-trip: walk data-anchor-row-id back to the
// owning message's anchor scene and rebuild the tool call from the matching row.
function _transparentToolCallFromRowDataset(row){
  try{
    const rowId=row.getAttribute('data-anchor-row-id')||'';
    // #5966 (Codex F2): resolve the OWNER message by its stamped index, not the
    // turn's first assistant segment — a multi-segment turn's scene is owned by a
    // later segment, so the first-segment lookup recovered the wrong (or no) scene.
    const ownerIdxAttr=row.getAttribute('data-anchor-owner-idx');
    let msg=null;
    if(ownerIdxAttr!==null&&ownerIdxAttr!==''){
      const oi=Number(ownerIdxAttr);
      if(Number.isFinite(oi)) msg=S.messages[oi];
    }
    if(!msg){
      // Fallback: find the assistant segment in this turn that actually owns a scene.
      const turn=row.closest&&row.closest('.assistant-turn');
      const segs=turn?Array.from(turn.querySelectorAll('.assistant-segment[data-msg-idx]')):[];
      for(const seg of segs){
        const i=Number(seg.getAttribute('data-msg-idx'));
        if(Number.isFinite(i)&&S.messages[i]&&S.messages[i]._anchor_activity_scene){ msg=S.messages[i]; break; }
      }
    }
    const scene=msg&&msg._anchor_activity_scene;
    if(!scene||!rowId) return null;
    const rows=_anchorSceneRowsForRendering(scene,{settled:true})||[];
    const match=rows.find(r=>String(r.row_id||r.local_id||'')===rowId&&String(r.role||'')==='tool');
    return match?_anchorSceneToolCallFromRow(match,{settled:true}):null;
  }catch(_){ return null; }
}
function _wireTransparentHeaderToggle(header){
  if(!header) return;
  header.setAttribute('data-transparent-toggle-bound','1');
  header.onclick=function(ev){
    const target=ev&&ev.target;
    if(target&&target.closest&&target.closest('.transparent-event-copy,.transparent-detail-mode,.tool-card-more')) return;
    const card=this.closest('.tool-card,.thinking-card');
    _setTransparentCardOpen(card,!(card&&card.classList.contains('open')));
  };
  header.onkeydown=function(ev){
    if(ev.key!=='Enter'&&ev.key!==' ') return;
    ev.preventDefault();
    const card=this.closest('.tool-card,.thinking-card');
    _setTransparentCardOpen(card,!(card&&card.classList.contains('open')));
  };
  header.setAttribute('role','button');
  header.setAttribute('tabindex','0');
}
function _transparentToolDetailHtml(tc, status){
  const args=tc&&tc.args&&typeof tc.args==='object'?tc.args:{};
  const argEntries=Object.entries(args);
  // The tool name is already shown in the row header and the status is shown as
  // a badge, so don't repeat them as pseudo-args in the body. Only surface a
  // duration meta when present. (Trifecta finding V6 — reduce redundancy.)
  const meta=[];
  if(tc&&tc.duration!==undefined&&tc.duration!==null) meta.push(['duration', String(tc.duration)]);
  const preview=String((tc&&(tc.snippet||tc.preview||tc.result||tc.output))||'').trim();
  const argHtml=[...meta,...argEntries].map(([k,v])=>{
    let sv=typeof v==='string'?v:JSON.stringify(v,null,2);
    // Redact secret-bearing arg values before rendering the transparent Full
    // tab — content args can be long multi-line commands (#4928) whose later
    // lines may carry secrets the short label never showed (#4928 gate).
    if(typeof _redactToolTargetLabel==='function'){ try{ sv=_redactToolTargetLabel(sv); }catch(e){} }
    return `<div class="tool-arg-pair"><span class="tool-arg-key">${esc(String(k))}</span><span class="tool-arg-val">${esc(sv)}</span></div>`;
  }).join('');
  return `<div class="tool-card-detail" data-transparent-detail-mode="full"><div class="transparent-detail-modes" role="tablist"><span class="transparent-detail-mode active" role="tab" tabindex="0" data-mode="full" onclick="_setTransparentDetailMode(this,'full')">Full</span><span class="transparent-detail-mode" role="tab" tabindex="0" data-mode="output" onclick="_setTransparentDetailMode(this,'output')">Output</span></div><div class="tool-card-args">${argHtml}</div>${preview?`<div class="tool-card-result"><pre>${esc(preview)}</pre></div>`:''}</div>`;
}
function _syncTransparentEventControls(turn){
  if(!turn||!isTransparentStream()) return;
  const blocks=_assistantTurnBlocks(turn);
  if(!blocks) return;
  const rows=Array.from(blocks.querySelectorAll(':scope > .transparent-event-row,[data-transparent-event-row="1"]'));
  const mountedToolCount=rows.filter(row=>row.getAttribute('data-event-type')==='tool').length;
  // #5966: when this turn's earlier steps are capped (some prefix rows are not
  // mounted yet), the true tool count is stashed on the turn so the "Trace: N
  // tools" label reflects the whole run, not just what's currently in the DOM.
  // Falls back to the mounted count for uncapped turns and the live path.
  const stashedTotal=Number(turn.getAttribute('data-transparent-total-tool-count'));
  const toolCount=(Number.isFinite(stashedTotal)&&stashedTotal>mountedToolCount)?stashedTotal:mountedToolCount;
  let bar=blocks.querySelector(':scope > .transparent-event-controls');
  if(!rows.length){
    if(bar) bar.remove();
    return;
  }
  if(!bar){
    bar=document.createElement('div');
    bar.className='transparent-event-controls';
    const label=document.createElement('span');
    label.className='transparent-event-controls-label';
    label.setAttribute('data-transparent-tool-count','1');
    const expand=document.createElement('span');
    expand.className='transparent-event-control';
    expand.setAttribute('role','button');
    expand.setAttribute('tabindex','0');
    expand.setAttribute('data-transparent-expand-all','1');
    expand.textContent=t('expand_all')||'Expand all';
    expand.onclick=function(ev){ev.stopPropagation();_setTransparentRowsExpanded(this.closest('.assistant-turn'),true);};
    expand.onkeydown=function(ev){if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();ev.stopPropagation();_setTransparentRowsExpanded(this.closest('.assistant-turn'),true);}};
    const collapse=document.createElement('span');
    collapse.className='transparent-event-control';
    collapse.setAttribute('role','button');
    collapse.setAttribute('tabindex','0');
    collapse.setAttribute('data-transparent-collapse-all','1');
    collapse.textContent=t('collapse_all')||'Collapse all';
    collapse.onclick=function(ev){ev.stopPropagation();_setTransparentRowsExpanded(this.closest('.assistant-turn'),false);};
    collapse.onkeydown=function(ev){if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();ev.stopPropagation();_setTransparentRowsExpanded(this.closest('.assistant-turn'),false);}};
    bar.appendChild(label);
    bar.appendChild(expand);
    bar.appendChild(collapse);
    // Guard: firstChild may be null (empty blocks) or orphaned from a prior
    // DOM rebuild. Only insertBefore when it is still a child of blocks.
    if(blocks.firstChild&&blocks.firstChild.parentNode===blocks) blocks.insertBefore(bar, blocks.firstChild);
    else blocks.appendChild(bar);
  }
  const expand=bar.querySelector('[data-transparent-expand-all]');
  if(expand){
    expand.onclick=function(ev){ev.stopPropagation();_setTransparentRowsExpanded(this.closest('.assistant-turn'),true);};
    expand.onkeydown=function(ev){if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();ev.stopPropagation();_setTransparentRowsExpanded(this.closest('.assistant-turn'),true);}};
  }
  const collapse=bar.querySelector('[data-transparent-collapse-all]');
  if(collapse){
    collapse.onclick=function(ev){ev.stopPropagation();_setTransparentRowsExpanded(this.closest('.assistant-turn'),false);};
    collapse.onkeydown=function(ev){if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();ev.stopPropagation();_setTransparentRowsExpanded(this.closest('.assistant-turn'),false);}};
  }
  const label=bar.querySelector('.transparent-event-controls-label');
  if(label){
    label.textContent=_transparentEventCountLabel(toolCount);
    label.setAttribute('data-transparent-tool-count',String(toolCount));
  }
  bar.setAttribute('data-tool-count',String(toolCount));
  // Wire the AGY chat name tag toggle for the live turn.
  _wireTransparentTurnToggle(turn);
  // Apply recency fade so the newest activity stands out while streaming. The
  // fade helper is internally gated to the live turn, so this no-ops on settled
  // turns — without this call the fade feature stayed dormant (only ever cleared
  // from the settled render loop). (Trifecta r2 follow-up.)
  _applyTransparentRowFading(turn);
}
function _rehydrateTransparentStreamDom(root){
  if(!root||!isTransparentStream()) return;
  // Handle BOTH a container root and a root that IS itself an assistant turn
  // (the live-turn restore path passes the #liveAssistantTurn element directly,
  // which querySelectorAll('.assistant-turn') would not match). (Trifecta C1 r2.)
  const turns=[];
  if(root.matches&&root.matches('.assistant-turn')) turns.push(root);
  root.querySelectorAll('.assistant-turn').forEach(turn=>turns.push(turn));
  turns.forEach(turn=>{
    _wireTransparentTurnToggle(turn);
    _syncTransparentEventControls(turn);
  });
  root.querySelectorAll('.transparent-event-row').forEach(row=>{
    const card=row.querySelector('.tool-card,.thinking-card');
    const header=row.querySelector('.tool-card-header,.thinking-card-header');
    if(header){
      _wireTransparentHeaderToggle(header);
      _attachCopyButton(header);
    }
    if(card) _setTransparentCardOpen(card,card.classList.contains('open'));
  });
  // #5966: re-wire the "Show earlier steps" affordance. Its click handler was
  // added via addEventListener and is lost when the session HTML-cache restores
  // innerHTML; the DOM + data-earlier-count survive, so rebind by walking back to
  // the owning message/segment. _revealTransparentEarlierSteps recovers the rows
  // from the scene, so no JS-property stash is needed.
  root.querySelectorAll('.transparent-earlier-steps[data-anchor-earlier-steps="1"]').forEach(el=>{
    if(el.getAttribute('data-earlier-rewired')==='1') return;
    el.setAttribute('data-earlier-rewired','1');
    // #5966 (Codex F2): rebind to the OWNER message by stamped index (multi-segment
    // turns own the scene on a later segment, not the first).
    const turn=el.closest&&el.closest('.assistant-turn');
    const ownerIdxAttr=el.getAttribute('data-anchor-owner-idx');
    let idx=(ownerIdxAttr!==null&&ownerIdxAttr!=='')?Number(ownerIdxAttr):NaN;
    let msg=Number.isFinite(idx)?S.messages[idx]:null;
    let seg=(turn&&Number.isFinite(idx))?turn.querySelector('.assistant-segment[data-msg-idx="'+idx+'"]'):null;
    if(!msg||!msg._anchor_activity_scene||!seg){
      // Fallback: the scene-owning segment in this turn.
      const segs=turn?Array.from(turn.querySelectorAll('.assistant-segment[data-msg-idx]')):[];
      for(const s of segs){
        const i=Number(s.getAttribute('data-msg-idx'));
        if(Number.isFinite(i)&&S.messages[i]&&S.messages[i]._anchor_activity_scene){ idx=i; msg=S.messages[i]; seg=s; break; }
      }
    }
    if(!msg||!seg){ return; }
    const handler=()=>_revealTransparentEarlierSteps(msg,seg,idx,el);
    el.addEventListener('click',handler);
    el.addEventListener('keydown',(ev)=>{ if(ev.key==='Enter'||ev.key===' '){ ev.preventDefault(); el.click(); } });
  });
}
function _decorateTransparentEventRow(row, opts){
  if(!row) return row;
  opts=opts||{};
  const type=String(opts.type||row.getAttribute('data-event-type')||'event');
  row.classList.add('transparent-event-row');
  row.setAttribute('data-transparent-event-row','1');
  row.setAttribute('data-transparent-stream','1');
  row.setAttribute('data-event-type',type);
  if(opts.name) row.setAttribute('data-event-name',String(opts.name));
  if(opts.segmentSeq) row.setAttribute('data-live-segment-seq',String(opts.segmentSeq));
  if(opts.burstId) row.setAttribute('data-activity-burst-id',String(opts.burstId));
  if(type==='tool'){
    const tc=opts.toolCall||row._tcData||{};
    const name=String(opts.name||tc.name||'tool');
    row.setAttribute('data-tool-name',name);
    const status=String(opts.status||_transparentToolStatus(tc));
    row.setAttribute('data-event-status',status);
    const header=row.querySelector('.tool-card-header');
    const card=row.querySelector('.tool-card');
    if(card) card.classList.add('transparent-event-card');
    if(header){
      const nameEl=header.querySelector('.tool-card-name');
      // The status badge (now legible per V2) already carries "Running", so don't
      // also prefix the name with "Running:" — that's the same redundancy class
      // V6 removed from the detail body. (Trifecta r2 #4.)
      if(nameEl) nameEl.textContent=_toolShortName(name);
      // #4658: restore the collapsed-row inline summary. buildToolCard emits a
      // `.tool-card-preview` span but blanks its text for the common case, and
      // the bare _toolShortName above drops the target the worklog view carries
      // in its action-label name. Populate the preview from a quiet,
      // target-based summary so each collapsed row says what it did without
      // expanding. Idempotent across re-decoration (status updates re-run this).
      const previewEl=header.querySelector('.tool-card-preview');
      if(previewEl){
        const summary=_transparentToolSummary(tc);
        if(summary){
          previewEl.textContent=summary;
          previewEl.removeAttribute('hidden');
        }else{
          previewEl.textContent='';
        }
      }
      let statusEl=header.querySelector('.transparent-event-status');
      if(!statusEl){
        statusEl=document.createElement('span');
        statusEl.className='transparent-event-status';
        const toggle=header.querySelector('.tool-card-toggle');
        // Guard against stale toggle (see thinking-preview fix above).
        if(toggle&&toggle.parentNode===header) header.insertBefore(statusEl,toggle);
        else header.appendChild(statusEl);
      }
      if(status==='Completed'){
        statusEl.textContent='';
        statusEl.removeAttribute('data-status');
      }else{
        statusEl.textContent=status;
        statusEl.setAttribute('data-status',status.toLowerCase());
      }
      row.setAttribute('data-event-status',status);
      // Update the 3D progress bar to reflect the new status.
      const progress=card.querySelector('.transparent-event-progress');
      if(progress){
        if(status==='Completed'||status==='Failed'||status==='Interrupted'){
          progress.removeAttribute('data-progress-running');
          progress.setAttribute('data-progress-percent','100%');
          progress.style.setProperty('--transparent-progress-percent','100%');
        }else if(status==='Running'){
          progress.setAttribute('data-progress-running','1');
          progress.setAttribute('data-progress-percent','60%');
          progress.style.setProperty('--transparent-progress-percent','60%');
        }
      }
      let detail=row.querySelector('.tool-card-detail');
      // #5966: on a SETTLED, COLLAPSED tool row, defer the heavy `.tool-card-detail`
      // body (full tool input/output HTML + Prism/KaTeX/Mermaid post-processing)
      // until first expand. A reasoning-heavy history can carry thousands of settled
      // tool rows; eagerly materializing every detail at load is the Transparent-
      // Stream analogue of the #5860 compact-worklog freeze. NOTE buildToolCard
      // PRE-BUILDS `.tool-card-detail` whenever the tool has args/output (hasDetail),
      // so we must strip that prebuilt body too — a `!detail` guard would skip
      // exactly the heavy rows we need to defer (Codex gate F1). The header (name,
      // preview, status, chevron) stays; a deferred row looks identical collapsed.
      // _materializeTransparentToolDetail() rebuilds on expand from the stashed tool
      // call, or from data-anchor-row-id → owner message scene after the
      // _sessionHtmlCache innerHTML round-trip drops the JS stash (#5839 class).
      // Live and already-open rows keep their detail (about to be read).
      const _deferDetail=card&&opts.settled===true&&!card.classList.contains('open')&&_transparentToolRowHasDetail(tc);
      if(_deferDetail){
        if(detail){ detail.remove(); detail=null; }   // drop any buildToolCard prebuilt body
        if(!header.querySelector('.tool-card-toggle')){
          const toggle=document.createElement('span');
          toggle.className='tool-card-toggle';
          toggle.innerHTML=li('chevron-right',12);
          header.appendChild(toggle);
        }
        card.classList.remove('tool-card-no-detail');  // it DOES have detail (deferred)
        row._deferredToolCall=tc;
        row.setAttribute('data-transparent-detail-deferred','1');
      }else if(!detail&&card){
        card.insertAdjacentHTML('beforeend',_transparentToolDetailHtml(tc,status));
        detail=row.querySelector('.tool-card-detail');
        if(!header.querySelector('.tool-card-toggle')){
          const toggle=document.createElement('span');
          toggle.className='tool-card-toggle';
          toggle.innerHTML=li('chevron-right',12);
          header.appendChild(toggle);
        }
      }
      if(detail&&!detail.querySelector('.transparent-detail-modes')){
        const modes=document.createElement('div');
        modes.className='transparent-detail-modes';
        modes.setAttribute('role','tablist');
        modes.innerHTML=`<span class="transparent-detail-mode active" role="tab" tabindex="0" data-mode="full" onclick="_setTransparentDetailMode(this,'full')">Full</span><span class="transparent-detail-mode" role="tab" tabindex="0" data-mode="output" onclick="_setTransparentDetailMode(this,'output')">Output</span>`;
        // Guard: firstChild may be orphaned from a prior DOM rebuild.
        const firstChild=detail.firstChild;
        if(firstChild&&firstChild.parentNode===detail) detail.insertBefore(modes, firstChild);
        else detail.appendChild(modes);
        detail.setAttribute('data-transparent-detail-mode','full');
      }
      if(typeof _syncTransparentEventTimestamp==='function') _syncTransparentEventTimestamp(row, header, {toolCall:tc, ts:opts.ts, live:opts.live===true});
      _wireTransparentHeaderToggle(header);
      _attachCopyButton(header);
    }
    // Attach the 3D progress bar BEFORE the early return so tool rows
    // also get the bottom bar (the function used to return before
    // reaching the bar attachment, which left tool rows bar-less).
    _attachProgressBar(row, opts);
    return row;
  }
  if(type==='thinking'){
    row.classList.add('transparent-thinking-event');
    row.setAttribute('data-event-name','thinking');
    const card=row.querySelector('.thinking-card');
    const header=row.querySelector('.thinking-card-header');
    if(card) card.classList.add('transparent-event-card');
    if(header){
      const btnRow=header.querySelector('.thinking-card-btn-row');
      const copy=header.querySelector('.thinking-copy-btn,.transparent-event-copy');
      const toggle=header.querySelector('.thinking-card-toggle');
      if(copy&&copy.parentNode!==header) header.appendChild(copy);
      if(toggle&&toggle.parentNode!==header) header.appendChild(toggle);
      if(btnRow&&btnRow.parentNode===header&&!btnRow.children.length) btnRow.remove();
      header.style.flexDirection='row';
      const label=header.querySelector('.thinking-card-label');
      if(label) label.textContent='Thinking';
      let preview=header.querySelector('.transparent-event-preview,.transparent-event-thinking-preview');
      const previewText=_transparentEventPreview(opts.preview||opts.text||row.textContent||'');
      if(previewText){
        if(!preview){
          preview=document.createElement('span');
          preview.className='transparent-event-preview transparent-event-thinking-preview';
          if(label&&label.parentNode===header&&label.nextSibling) header.insertBefore(preview,label.nextSibling);
          else if(label&&label.parentNode===header) header.appendChild(preview);
          else header.appendChild(preview);
        }
        preview.classList.add('transparent-event-thinking-preview');
        preview.textContent=previewText;
      }else if(preview){
        preview.remove();
      }
      if(typeof _syncTransparentEventTimestamp==='function') _syncTransparentEventTimestamp(row, header, {ts:opts.ts, live:opts.live===true});
      _wireTransparentHeaderToggle(header);
      _attachCopyButton(header);
    }
    _attachProgressBar(row, opts);
  }
  return row;
}

/* ── Global Window Exports ─────────────────────────────────────────────────── */
if (typeof window !== 'undefined') {
  window.INFLIGHT_KEY = typeof INFLIGHT_KEY !== 'undefined' ? INFLIGHT_KEY : undefined;
  window.INFLIGHT_STATE_KEY = typeof INFLIGHT_STATE_KEY !== 'undefined' ? INFLIGHT_STATE_KEY : undefined;
  window.INFLIGHT_STATE_DEFAULT_LIMITS = typeof INFLIGHT_STATE_DEFAULT_LIMITS !== 'undefined' ? INFLIGHT_STATE_DEFAULT_LIMITS : undefined;
  window._boundedInflightInt = typeof _boundedInflightInt !== 'undefined' ? _boundedInflightInt : undefined;
  window._getInflightStateLimits = typeof _getInflightStateLimits !== 'undefined' ? _getInflightStateLimits : undefined;
  window._readInflightStateMap = typeof _readInflightStateMap !== 'undefined' ? _readInflightStateMap : undefined;
  window._isStorageQuotaError = typeof _isStorageQuotaError !== 'undefined' ? _isStorageQuotaError : undefined;
  window._truncateInflightValue = typeof _truncateInflightValue !== 'undefined' ? _truncateInflightValue : undefined;
  window._compactInflightState = typeof _compactInflightState !== 'undefined' ? _compactInflightState : undefined;
  window._writeInflightStateMap = typeof _writeInflightStateMap !== 'undefined' ? _writeInflightStateMap : undefined;
  window.saveInflightState = typeof saveInflightState !== 'undefined' ? saveInflightState : undefined;
  window.loadInflightState = typeof loadInflightState !== 'undefined' ? loadInflightState : undefined;
  window.clearInflightState = typeof clearInflightState !== 'undefined' ? clearInflightState : undefined;
  window._todosLastRenderedHash = typeof _todosLastRenderedHash !== 'undefined' ? _todosLastRenderedHash : undefined;
  window._todosRenderRafId = typeof _todosRenderRafId !== 'undefined' ? _todosRenderRafId : undefined;
  window._todosHash = typeof _todosHash !== 'undefined' ? _todosHash : undefined;
  window.TODO_STATUS_RENDERING = typeof TODO_STATUS_RENDERING !== 'undefined' ? TODO_STATUS_RENDERING : undefined;
  window.todoStatusKey = typeof todoStatusKey !== 'undefined' ? todoStatusKey : undefined;
  window.todoStatusVisual = typeof todoStatusVisual !== 'undefined' ? todoStatusVisual : undefined;
  window.renderTodoStatusIcon = typeof renderTodoStatusIcon !== 'undefined' ? renderTodoStatusIcon : undefined;
  window.todoContent = typeof todoContent !== 'undefined' ? todoContent : undefined;
  window.renderTodoEmptyState = typeof renderTodoEmptyState !== 'undefined' ? renderTodoEmptyState : undefined;
  window.renderTodoRow = typeof renderTodoRow !== 'undefined' ? renderTodoRow : undefined;
  window.renderTodoRows = typeof renderTodoRows !== 'undefined' ? renderTodoRows : undefined;
  window._todosPanelIsActive = typeof _todosPanelIsActive !== 'undefined' ? _todosPanelIsActive : undefined;
  window.scheduleTodosRefresh = typeof scheduleTodosRefresh !== 'undefined' ? scheduleTodosRefresh : undefined;
  window._resetTodosRenderCache = typeof _resetTodosRenderCache !== 'undefined' ? _resetTodosRenderCache : undefined;
  window._hydrateTodosFromSession = typeof _hydrateTodosFromSession !== 'undefined' ? _hydrateTodosFromSession : undefined;
  window.snapshotLiveTurnHtmlForSession = typeof snapshotLiveTurnHtmlForSession !== 'undefined' ? snapshotLiveTurnHtmlForSession : undefined;
  window._liveAssistantSegmentTextLength = typeof _liveAssistantSegmentTextLength !== 'undefined' ? _liveAssistantSegmentTextLength : undefined;
  window._mergeRestoredLiveAssistantSegment = typeof _mergeRestoredLiveAssistantSegment !== 'undefined' ? _mergeRestoredLiveAssistantSegment : undefined;
  window.restoreLiveTurnHtmlForSession = typeof restoreLiveTurnHtmlForSession !== 'undefined' ? restoreLiveTurnHtmlForSession : undefined;
  window.markInflight = typeof markInflight !== 'undefined' ? markInflight : undefined;
  window.clearInflight = typeof clearInflight !== 'undefined' ? clearInflight : undefined;
  window.showReconnectBanner = typeof showReconnectBanner !== 'undefined' ? showReconnectBanner : undefined;
  window.dismissReconnect = typeof dismissReconnect !== 'undefined' ? dismissReconnect : undefined;
  window.SYSTEM_HEALTH_INTERVAL_MS = typeof SYSTEM_HEALTH_INTERVAL_MS !== 'undefined' ? SYSTEM_HEALTH_INTERVAL_MS : undefined;
  window._systemHealthTimer = typeof _systemHealthTimer !== 'undefined' ? _systemHealthTimer : undefined;
  window._systemHealthPercent = typeof _systemHealthPercent !== 'undefined' ? _systemHealthPercent : undefined;
  window._formatSystemHealthPercent = typeof _formatSystemHealthPercent !== 'undefined' ? _formatSystemHealthPercent : undefined;
  window._formatSystemHealthBytes = typeof _formatSystemHealthBytes !== 'undefined' ? _formatSystemHealthBytes : undefined;
  window._updateSystemHealthMetric = typeof _updateSystemHealthMetric !== 'undefined' ? _updateSystemHealthMetric : undefined;
  window.setSystemHealthUnavailable = typeof setSystemHealthUnavailable !== 'undefined' ? setSystemHealthUnavailable : undefined;
  window.renderSystemHealth = typeof renderSystemHealth !== 'undefined' ? renderSystemHealth : undefined;
  window.pollSystemHealth = typeof pollSystemHealth !== 'undefined' ? pollSystemHealth : undefined;
  window._systemHealthPanelIsVisible = typeof _systemHealthPanelIsVisible !== 'undefined' ? _systemHealthPanelIsVisible : undefined;
  window.startSystemHealthMonitor = typeof startSystemHealthMonitor !== 'undefined' ? startSystemHealthMonitor : undefined;
  window.stopSystemHealthMonitor = typeof stopSystemHealthMonitor !== 'undefined' ? stopSystemHealthMonitor : undefined;
  window._syncSystemHealthMonitorVisibility = typeof _syncSystemHealthMonitorVisibility !== 'undefined' ? _syncSystemHealthMonitorVisibility : undefined;
  window.AGENT_HEALTH_INTERVAL_MS = typeof AGENT_HEALTH_INTERVAL_MS !== 'undefined' ? AGENT_HEALTH_INTERVAL_MS : undefined;
  window.AGENT_HEALTH_DISMISSED_KEY = typeof AGENT_HEALTH_DISMISSED_KEY !== 'undefined' ? AGENT_HEALTH_DISMISSED_KEY : undefined;
  window._agentHealthTimer = typeof _agentHealthTimer !== 'undefined' ? _agentHealthTimer : undefined;
  window._agentHealthLastState = typeof _agentHealthLastState !== 'undefined' ? _agentHealthLastState : undefined;
  window._lastGatewayRestartTime = typeof _lastGatewayRestartTime !== 'undefined' ? _lastGatewayRestartTime : undefined;
  window._agentHealthDismissed = typeof _agentHealthDismissed !== 'undefined' ? _agentHealthDismissed : undefined;
  window._setAgentHealthDismissed = typeof _setAgentHealthDismissed !== 'undefined' ? _setAgentHealthDismissed : undefined;
  window._hideAgentHealthAlert = typeof _hideAgentHealthAlert !== 'undefined' ? _hideAgentHealthAlert : undefined;
  window._showAgentHealthAlert = typeof _showAgentHealthAlert !== 'undefined' ? _showAgentHealthAlert : undefined;
  window.dismissAgentHealthAlert = typeof dismissAgentHealthAlert !== 'undefined' ? dismissAgentHealthAlert : undefined;
  window.restartGatewayService = typeof restartGatewayService !== 'undefined' ? restartGatewayService : undefined;
  window.pollAgentHealth = typeof pollAgentHealth !== 'undefined' ? pollAgentHealth : undefined;
  window.startAgentHealthMonitor = typeof startAgentHealthMonitor !== 'undefined' ? startAgentHealthMonitor : undefined;
  window.stopAgentHealthMonitor = typeof stopAgentHealthMonitor !== 'undefined' ? stopAgentHealthMonitor : undefined;
  window._syncAgentHealthMonitorVisibility = typeof _syncAgentHealthMonitorVisibility !== 'undefined' ? _syncAgentHealthMonitorVisibility : undefined;
  window.refreshSession = typeof refreshSession !== 'undefined' ? refreshSession : undefined;
  window._formatUpdateTargetStatus = typeof _formatUpdateTargetStatus !== 'undefined' ? _formatUpdateTargetStatus : undefined;
  window._formatManualUpdateInstruction = typeof _formatManualUpdateInstruction !== 'undefined' ? _formatManualUpdateInstruction : undefined;
  window._formatUpdateCheckError = typeof _formatUpdateCheckError !== 'undefined' ? _formatUpdateCheckError : undefined;
  window._isSafeUpdateCompareUrl = typeof _isSafeUpdateCompareUrl !== 'undefined' ? _isSafeUpdateCompareUrl : undefined;
  window._updateCompareUrl = typeof _updateCompareUrl !== 'undefined' ? _updateCompareUrl : undefined;
  window._updateWhatsNewTargets = typeof _updateWhatsNewTargets !== 'undefined' ? _updateWhatsNewTargets : undefined;
  window._appendUpdateDiffLinks = typeof _appendUpdateDiffLinks !== 'undefined' ? _appendUpdateDiffLinks : undefined;
  window._hideUpdateSummaryPanel = typeof _hideUpdateSummaryPanel !== 'undefined' ? _hideUpdateSummaryPanel : undefined;
  window._syncUpdateSummaryExpandButton = typeof _syncUpdateSummaryExpandButton !== 'undefined' ? _syncUpdateSummaryExpandButton : undefined;
  window.toggleUpdateSummaryExpanded = typeof toggleUpdateSummaryExpanded !== 'undefined' ? toggleUpdateSummaryExpanded : undefined;
  window.WHATS_NEW_SUMMARY_STORAGE_KEY = typeof WHATS_NEW_SUMMARY_STORAGE_KEY !== 'undefined' ? WHATS_NEW_SUMMARY_STORAGE_KEY : undefined;
  window.WHATS_NEW_SUMMARY_STORAGE_MAX_BYTES = typeof WHATS_NEW_SUMMARY_STORAGE_MAX_BYTES !== 'undefined' ? WHATS_NEW_SUMMARY_STORAGE_MAX_BYTES : undefined;
  window._summaryStorageByteLength = typeof _summaryStorageByteLength !== 'undefined' ? _summaryStorageByteLength : undefined;
  window._summaryCacheEntriesSortedByRecency = typeof _summaryCacheEntriesSortedByRecency !== 'undefined' ? _summaryCacheEntriesSortedByRecency : undefined;
  window._loadStoredUpdateSummaries = typeof _loadStoredUpdateSummaries !== 'undefined' ? _loadStoredUpdateSummaries : undefined;
  window._persistGeneratedSummaries = typeof _persistGeneratedSummaries !== 'undefined' ? _persistGeneratedSummaries : undefined;
  window._pruneGeneratedSummaries = typeof _pruneGeneratedSummaries !== 'undefined' ? _pruneGeneratedSummaries : undefined;
  window._updateSummarySignature = typeof _updateSummarySignature !== 'undefined' ? _updateSummarySignature : undefined;
  window._updateSummaryButtonLabel = typeof _updateSummaryButtonLabel !== 'undefined' ? _updateSummaryButtonLabel : undefined;
  window._rememberGeneratedSummary = typeof _rememberGeneratedSummary !== 'undefined' ? _rememberGeneratedSummary : undefined;
  window._renderUpdateSummaryPanel = typeof _renderUpdateSummaryPanel !== 'undefined' ? _renderUpdateSummaryPanel : undefined;
  window.showWhatsNewSummary = typeof showWhatsNewSummary !== 'undefined' ? showWhatsNewSummary : undefined;
  window._renderUpdateWhatsNewLinks = typeof _renderUpdateWhatsNewLinks !== 'undefined' ? _renderUpdateWhatsNewLinks : undefined;
  window._showUpdateBanner = typeof _showUpdateBanner !== 'undefined' ? _showUpdateBanner : undefined;
  window._i18nUpdateText = typeof _i18nUpdateText !== 'undefined' ? _i18nUpdateText : undefined;
  window.dismissUpdate = typeof dismissUpdate !== 'undefined' ? dismissUpdate : undefined;
  window._isUpdateApplyNetworkError = typeof _isUpdateApplyNetworkError !== 'undefined' ? _isUpdateApplyNetworkError : undefined;
  window._formatUpdateApplyExceptionMessage = typeof _formatUpdateApplyExceptionMessage !== 'undefined' ? _formatUpdateApplyExceptionMessage : undefined;
  window.applyUpdates = typeof applyUpdates !== 'undefined' ? applyUpdates : undefined;
  window._showUpdateError = typeof _showUpdateError !== 'undefined' ? _showUpdateError : undefined;
  window.applyClearUpdateLock = typeof applyClearUpdateLock !== 'undefined' ? applyClearUpdateLock : undefined;
  window._renderLockManualInstruction = typeof _renderLockManualInstruction !== 'undefined' ? _renderLockManualInstruction : undefined;
  window._normalizeHealthServerIdentity = typeof _normalizeHealthServerIdentity !== 'undefined' ? _normalizeHealthServerIdentity : undefined;
  window._healthResponseServerIdentity = typeof _healthResponseServerIdentity !== 'undefined' ? _healthResponseServerIdentity : undefined;
  window._readHealthServerIdentity = typeof _readHealthServerIdentity !== 'undefined' ? _readHealthServerIdentity : undefined;
  window.forceUpdate = typeof forceUpdate !== 'undefined' ? forceUpdate : undefined;
  window._waitForServerThenReload = typeof _waitForServerThenReload !== 'undefined' ? _waitForServerThenReload : undefined;
  window._pendingCurrentTailUserMessage = typeof _pendingCurrentTailUserMessage !== 'undefined' ? _pendingCurrentTailUserMessage : undefined;
  window._PENDING_ACTIVE_TURN_TS_EPSILON = typeof _PENDING_ACTIVE_TURN_TS_EPSILON !== 'undefined' ? _PENDING_ACTIVE_TURN_TS_EPSILON : undefined;
  window._messageTimestampSeconds = typeof _messageTimestampSeconds !== 'undefined' ? _messageTimestampSeconds : undefined;
  window._activeTurnTokenMatches = typeof _activeTurnTokenMatches !== 'undefined' ? _activeTurnTokenMatches : undefined;
  window._pendingActiveTurnUserMessage = typeof _pendingActiveTurnUserMessage !== 'undefined' ? _pendingActiveTurnUserMessage : undefined;
  window.getPendingSessionMessage = typeof getPendingSessionMessage !== 'undefined' ? getPendingSessionMessage : undefined;
  window.checkInflightOnBoot = typeof checkInflightOnBoot !== 'undefined' ? checkInflightOnBoot : undefined;
  window._topbarLoadedMessageCount = typeof _topbarLoadedMessageCount !== 'undefined' ? _topbarLoadedMessageCount : undefined;
  window._topbarMessageMetaText = typeof _topbarMessageMetaText !== 'undefined' ? _topbarMessageMetaText : undefined;
  window.syncTopbar = typeof syncTopbar !== 'undefined' ? syncTopbar : undefined;
  window.msgContent = typeof msgContent !== 'undefined' ? msgContent : undefined;
  window._isRecoveryControlMessageText = typeof _isRecoveryControlMessageText !== 'undefined' ? _isRecoveryControlMessageText : undefined;
  window._isRecoveryControlMessage = typeof _isRecoveryControlMessage !== 'undefined' ? _isRecoveryControlMessage : undefined;
  window._assistantAnchorSceneFinalAnswerText = typeof _assistantAnchorSceneFinalAnswerText !== 'undefined' ? _assistantAnchorSceneFinalAnswerText : undefined;
  window._assistantMessageHasVisibleContent = typeof _assistantMessageHasVisibleContent !== 'undefined' ? _assistantMessageHasVisibleContent : undefined;
  window._fmtDateSep = typeof _fmtDateSep !== 'undefined' ? _fmtDateSep : undefined;
  window._ERR_MSG_RE = typeof _ERR_MSG_RE !== 'undefined' ? _ERR_MSG_RE : undefined;
  window._messageHasReasoningPayload = typeof _messageHasReasoningPayload !== 'undefined' ? _messageHasReasoningPayload : undefined;
  window._isAssistantEmptyPlaceholderContent = typeof _isAssistantEmptyPlaceholderContent !== 'undefined' ? _isAssistantEmptyPlaceholderContent : undefined;
  window._formatTurnTps = typeof _formatTurnTps !== 'undefined' ? _formatTurnTps : undefined;
  window.isTpsDisplayEnabled = typeof isTpsDisplayEnabled !== 'undefined' ? isTpsDisplayEnabled : undefined;
  window._assistantRoleHtml = typeof _assistantRoleHtml !== 'undefined' ? _assistantRoleHtml : undefined;
  window._setAssistantTurnTps = typeof _setAssistantTurnTps !== 'undefined' ? _setAssistantTurnTps : undefined;
  window._setLiveAssistantTps = typeof _setLiveAssistantTps !== 'undefined' ? _setLiveAssistantTps : undefined;
  window._createAssistantTurn = typeof _createAssistantTurn !== 'undefined' ? _createAssistantTurn : undefined;
  window._setLatestAssistantTurnLandmark = typeof _setLatestAssistantTurnLandmark !== 'undefined' ? _setLatestAssistantTurnLandmark : undefined;
  window._assistantTurnBlocks = typeof _assistantTurnBlocks !== 'undefined' ? _assistantTurnBlocks : undefined;
  window._assistantMessageBelongsInWorklog = typeof _assistantMessageBelongsInWorklog !== 'undefined' ? _assistantMessageBelongsInWorklog : undefined;
  window._assistantThinkingBelongsInWorklog = typeof _assistantThinkingBelongsInWorklog !== 'undefined' ? _assistantThinkingBelongsInWorklog : undefined;
  window._assistantReasoningPayloadText = typeof _assistantReasoningPayloadText !== 'undefined' ? _assistantReasoningPayloadText : undefined;
  window._stripLeadingAssistantThinkingMarkup = typeof _stripLeadingAssistantThinkingMarkup !== 'undefined' ? _stripLeadingAssistantThinkingMarkup : undefined;
  window._assistantVisibleContentForReasoningCompare = typeof _assistantVisibleContentForReasoningCompare !== 'undefined' ? _assistantVisibleContentForReasoningCompare : undefined;
  window._assistantTurnFinalVisibleContentMap = typeof _assistantTurnFinalVisibleContentMap !== 'undefined' ? _assistantTurnFinalVisibleContentMap : undefined;
  window._assistantTurnVisibleContentMap = typeof _assistantTurnVisibleContentMap !== 'undefined' ? _assistantTurnVisibleContentMap : undefined;
  window._worklogReasoningTextFromMessage = typeof _worklogReasoningTextFromMessage !== 'undefined' ? _worklogReasoningTextFromMessage : undefined;
  window._worklogDetailsExpandedDefault = typeof _worklogDetailsExpandedDefault !== 'undefined' ? _worklogDetailsExpandedDefault : undefined;
  window._applyWorklogDetailsExpandedDefault = typeof _applyWorklogDetailsExpandedDefault !== 'undefined' ? _applyWorklogDetailsExpandedDefault : undefined;
  window._worklogDetailDisclosureSelector = typeof _worklogDetailDisclosureSelector !== 'undefined' ? _worklogDetailDisclosureSelector : undefined;
  window._worklogDetailTextKey = typeof _worklogDetailTextKey !== 'undefined' ? _worklogDetailTextKey : undefined;
  window._worklogDetailHashKey = typeof _worklogDetailHashKey !== 'undefined' ? _worklogDetailHashKey : undefined;
  window._worklogDetailBaseKey = typeof _worklogDetailBaseKey !== 'undefined' ? _worklogDetailBaseKey : undefined;
  window._worklogDetailDisclosureIsOpen = typeof _worklogDetailDisclosureIsOpen !== 'undefined' ? _worklogDetailDisclosureIsOpen : undefined;
  window._worklogDetailScrollableBody = typeof _worklogDetailScrollableBody !== 'undefined' ? _worklogDetailScrollableBody : undefined;
  window._setWorklogDetailDisclosureOpen = typeof _setWorklogDetailDisclosureOpen !== 'undefined' ? _setWorklogDetailDisclosureOpen : undefined;
  window._worklogDetailDisclosureKeyForElement = typeof _worklogDetailDisclosureKeyForElement !== 'undefined' ? _worklogDetailDisclosureKeyForElement : undefined;
  window._captureWorklogDetailDisclosureState = typeof _captureWorklogDetailDisclosureState !== 'undefined' ? _captureWorklogDetailDisclosureState : undefined;
  window._restoreWorklogDetailDisclosureState = typeof _restoreWorklogDetailDisclosureState !== 'undefined' ? _restoreWorklogDetailDisclosureState : undefined;
  window._thinkingCardHtml = typeof _thinkingCardHtml !== 'undefined' ? _thinkingCardHtml : undefined;
  window.isSimplifiedToolCalling = typeof isSimplifiedToolCalling !== 'undefined' ? isSimplifiedToolCalling : undefined;
  window._thinkingActivityNode = typeof _thinkingActivityNode !== 'undefined' ? _thinkingActivityNode : undefined;
  window.chatActivityMode = typeof chatActivityMode !== 'undefined' ? chatActivityMode : undefined;
  window.isTransparentStream = typeof isTransparentStream !== 'undefined' ? isTransparentStream : undefined;
  window.isFinalAnswerOnlyMode = typeof isFinalAnswerOnlyMode !== 'undefined' ? isFinalAnswerOnlyMode : undefined;
  window.isCompactWorklogMode = typeof isCompactWorklogMode !== 'undefined' ? isCompactWorklogMode : undefined;
  window._toolShortName = typeof _toolShortName !== 'undefined' ? _toolShortName : undefined;
  window._transparentEventPreview = typeof _transparentEventPreview !== 'undefined' ? _transparentEventPreview : undefined;
  window._transparentToolStatus = typeof _transparentToolStatus !== 'undefined' ? _transparentToolStatus : undefined;
  window._transparentToolSummary = typeof _transparentToolSummary !== 'undefined' ? _transparentToolSummary : undefined;
  window._showTransparentCopiedFeedback = typeof _showTransparentCopiedFeedback !== 'undefined' ? _showTransparentCopiedFeedback : undefined;
  window._copyEventToClipboard = typeof _copyEventToClipboard !== 'undefined' ? _copyEventToClipboard : undefined;
  window._attachCopyButton = typeof _attachCopyButton !== 'undefined' ? _attachCopyButton : undefined;
  window._transparentEventCountLabel = typeof _transparentEventCountLabel !== 'undefined' ? _transparentEventCountLabel : undefined;
  window._setTransparentDetailMode = typeof _setTransparentDetailMode !== 'undefined' ? _setTransparentDetailMode : undefined;
  window._setTransparentCardOpen = typeof _setTransparentCardOpen !== 'undefined' ? _setTransparentCardOpen : undefined;
  window._transparentToolRowHasDetail = typeof _transparentToolRowHasDetail !== 'undefined' ? _transparentToolRowHasDetail : undefined;
  window._materializeTransparentToolDetail = typeof _materializeTransparentToolDetail !== 'undefined' ? _materializeTransparentToolDetail : undefined;
  window._transparentToolCallFromRowDataset = typeof _transparentToolCallFromRowDataset !== 'undefined' ? _transparentToolCallFromRowDataset : undefined;
  window._wireTransparentHeaderToggle = typeof _wireTransparentHeaderToggle !== 'undefined' ? _wireTransparentHeaderToggle : undefined;
  window._transparentToolDetailHtml = typeof _transparentToolDetailHtml !== 'undefined' ? _transparentToolDetailHtml : undefined;
  window._syncTransparentEventControls = typeof _syncTransparentEventControls !== 'undefined' ? _syncTransparentEventControls : undefined;
  window._rehydrateTransparentStreamDom = typeof _rehydrateTransparentStreamDom !== 'undefined' ? _rehydrateTransparentStreamDom : undefined;
  window._decorateTransparentEventRow = typeof _decorateTransparentEventRow !== 'undefined' ? _decorateTransparentEventRow : undefined;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    INFLIGHT_KEY,
    INFLIGHT_STATE_KEY,
    INFLIGHT_STATE_DEFAULT_LIMITS,
    _boundedInflightInt,
    _getInflightStateLimits,
    _readInflightStateMap,
    _isStorageQuotaError,
    _truncateInflightValue,
    _compactInflightState,
    _writeInflightStateMap,
    saveInflightState,
    loadInflightState,
    clearInflightState,
    _todosLastRenderedHash,
    _todosRenderRafId,
    _todosHash,
    TODO_STATUS_RENDERING,
    todoStatusKey,
    todoStatusVisual,
    renderTodoStatusIcon,
    todoContent,
    renderTodoEmptyState,
    renderTodoRow,
    renderTodoRows,
    _todosPanelIsActive,
    scheduleTodosRefresh,
    _resetTodosRenderCache,
    _hydrateTodosFromSession,
    snapshotLiveTurnHtmlForSession,
    _liveAssistantSegmentTextLength,
    _mergeRestoredLiveAssistantSegment,
    restoreLiveTurnHtmlForSession,
    markInflight,
    clearInflight,
    showReconnectBanner,
    dismissReconnect,
    SYSTEM_HEALTH_INTERVAL_MS,
    _systemHealthTimer,
    _systemHealthPercent,
    _formatSystemHealthPercent,
    _formatSystemHealthBytes,
    _updateSystemHealthMetric,
    setSystemHealthUnavailable,
    renderSystemHealth,
    pollSystemHealth,
    _systemHealthPanelIsVisible,
    startSystemHealthMonitor,
    stopSystemHealthMonitor,
    _syncSystemHealthMonitorVisibility,
    AGENT_HEALTH_INTERVAL_MS,
    AGENT_HEALTH_DISMISSED_KEY,
    _agentHealthTimer,
    _agentHealthLastState,
    _lastGatewayRestartTime,
    _agentHealthDismissed,
    _setAgentHealthDismissed,
    _hideAgentHealthAlert,
    _showAgentHealthAlert,
    dismissAgentHealthAlert,
    restartGatewayService,
    pollAgentHealth,
    startAgentHealthMonitor,
    stopAgentHealthMonitor,
    _syncAgentHealthMonitorVisibility,
    refreshSession,
    _formatUpdateTargetStatus,
    _formatManualUpdateInstruction,
    _formatUpdateCheckError,
    _isSafeUpdateCompareUrl,
    _updateCompareUrl,
    _updateWhatsNewTargets,
    _appendUpdateDiffLinks,
    _hideUpdateSummaryPanel,
    _syncUpdateSummaryExpandButton,
    toggleUpdateSummaryExpanded,
    WHATS_NEW_SUMMARY_STORAGE_KEY,
    WHATS_NEW_SUMMARY_STORAGE_MAX_BYTES,
    _summaryStorageByteLength,
    _summaryCacheEntriesSortedByRecency,
    _loadStoredUpdateSummaries,
    _persistGeneratedSummaries,
    _pruneGeneratedSummaries,
    _updateSummarySignature,
    _updateSummaryButtonLabel,
    _rememberGeneratedSummary,
    _renderUpdateSummaryPanel,
    showWhatsNewSummary,
    _renderUpdateWhatsNewLinks,
    _showUpdateBanner,
    _i18nUpdateText,
    dismissUpdate,
    _isUpdateApplyNetworkError,
    _formatUpdateApplyExceptionMessage,
    applyUpdates,
    _showUpdateError,
    applyClearUpdateLock,
    _renderLockManualInstruction,
    _normalizeHealthServerIdentity,
    _healthResponseServerIdentity,
    _readHealthServerIdentity,
    forceUpdate,
    _waitForServerThenReload,
    _pendingCurrentTailUserMessage,
    _PENDING_ACTIVE_TURN_TS_EPSILON,
    _messageTimestampSeconds,
    _activeTurnTokenMatches,
    _pendingActiveTurnUserMessage,
    getPendingSessionMessage,
    checkInflightOnBoot,
    _topbarLoadedMessageCount,
    _topbarMessageMetaText,
    syncTopbar,
    msgContent,
    _isRecoveryControlMessageText,
    _isRecoveryControlMessage,
    _assistantAnchorSceneFinalAnswerText,
    _assistantMessageHasVisibleContent,
    _fmtDateSep,
    _ERR_MSG_RE,
    _messageHasReasoningPayload,
    _isAssistantEmptyPlaceholderContent,
    _formatTurnTps,
    isTpsDisplayEnabled,
    _assistantRoleHtml,
    _setAssistantTurnTps,
    _setLiveAssistantTps,
    _createAssistantTurn,
    _setLatestAssistantTurnLandmark,
    _assistantTurnBlocks,
    _assistantMessageBelongsInWorklog,
    _assistantThinkingBelongsInWorklog,
    _assistantReasoningPayloadText,
    _stripLeadingAssistantThinkingMarkup,
    _assistantVisibleContentForReasoningCompare,
    _assistantTurnFinalVisibleContentMap,
    _assistantTurnVisibleContentMap,
    _worklogReasoningTextFromMessage,
    _worklogDetailsExpandedDefault,
    _applyWorklogDetailsExpandedDefault,
    _worklogDetailDisclosureSelector,
    _worklogDetailTextKey,
    _worklogDetailHashKey,
    _worklogDetailBaseKey,
    _worklogDetailDisclosureIsOpen,
    _worklogDetailScrollableBody,
    _setWorklogDetailDisclosureOpen,
    _worklogDetailDisclosureKeyForElement,
    _captureWorklogDetailDisclosureState,
    _restoreWorklogDetailDisclosureState,
    _thinkingCardHtml,
    isSimplifiedToolCalling,
    _thinkingActivityNode,
    chatActivityMode,
    isTransparentStream,
    isFinalAnswerOnlyMode,
    isCompactWorklogMode,
    _toolShortName,
    _transparentEventPreview,
    _transparentToolStatus,
    _transparentToolSummary,
    _showTransparentCopiedFeedback,
    _copyEventToClipboard,
    _attachCopyButton,
    _transparentEventCountLabel,
    _setTransparentDetailMode,
    _setTransparentCardOpen,
    _transparentToolRowHasDetail,
    _materializeTransparentToolDetail,
    _transparentToolCallFromRowDataset,
    _wireTransparentHeaderToggle,
    _transparentToolDetailHtml,
    _syncTransparentEventControls,
    _rehydrateTransparentStreamDom,
    _decorateTransparentEventRow,
  };
}
