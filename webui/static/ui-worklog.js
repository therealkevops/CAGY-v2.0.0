/**
 * ui-worklog.js — Worklog: Transparent Turns, 3D Progress Bar, Live Footer, Streaming Event Rows
 *
 * Extracted from ui.js (Sprint F-M4+, ADR 016).
 */

// ── 3D progress bar (loading path / follow-up indicator) ───────────
// Each transparent event card has a thin 3D bar at its bottom edge.
// While the underlying tool is running (status === 'Running') the bar
// shows a shimmer animation; once the tool completes the bar fills to
// 100% and stops. The bar doubles as a visual step-break between rows
// in the stack, so the eye reads the stream as discrete steps.
function _attachProgressBar(row, opts){
  if(!row) return;
  opts=opts||{};
  const card=row.querySelector('.tool-card,.thinking-card');
  if(!card) return;
  let bar=card.querySelector('.transparent-event-progress');
  if(!bar){
    bar=document.createElement('div');
    bar.className='transparent-event-progress';
    // Append to the card so it sits flush at the bottom edge.
    card.appendChild(bar);
  }
  // Set the running state from opts or current status.
  const status=String(opts.status||(row.getAttribute('data-event-status')||''));
  const isRunning=(status==='Running'||status==='running');
  const isCompleted=(status==='Completed'||status==='completed'||status==='Failed'||status==='failed'||status==='Interrupted'||status==='interrupted');
  if(isRunning) bar.setAttribute('data-progress-running','1');
  else bar.removeAttribute('data-progress-running');
  if(isCompleted){
    bar.setAttribute('data-progress-percent','100%');
    bar.style.setProperty('--transparent-progress-percent','100%');
  }else if(isRunning){
    bar.setAttribute('data-progress-percent','60%');
    bar.style.setProperty('--transparent-progress-percent','60%');
  }else{
    bar.removeAttribute('data-progress-percent');
    bar.style.removeProperty('--transparent-progress-percent');
  }
}
function _setTransparentRowsExpanded(root, expanded){
  const scope=root||document;
  // #5966: "Expand all" must include a capped turn's hidden earlier steps —
  // reveal them first so expansion genuinely opens the whole run. (Collapse-all
  // leaves the cap as-is; it only closes what's mounted.)
  if(expanded){
    scope.querySelectorAll('.transparent-earlier-steps[data-anchor-earlier-steps="1"]').forEach(el=>{
      if(typeof el.click==='function') el.click();
    });
  }
  scope.querySelectorAll('.transparent-event-row .tool-card,.transparent-event-row .thinking-card').forEach(card=>{
    _setTransparentCardOpen(card,!!expanded);
  });
}
// ── Transparent turn-level collapse (AGY chat name tag) ──────────────────
// In transparent_stream mode the assistant role label is the turn's "name
// tag". Clicking it collapses the entire event stack underneath so the
// transcript shows only the final answer (Output only). A chevron on the
// role telegraph the affordance; the blocks body animates with the same
// max-height transition used by individual event cards. Persisted via
// data-attribute only — the turn's render path reads it on rebuild.
const _transparentTurnCollapsedStates={}; // key: `${sid}:${turnMsgIdx}` → boolean
function _wireTransparentTurnToggle(turn){
  if(!turn) return;
  if(!isTransparentStream()) return;
  const role=turn.querySelector('.msg-role.assistant');
  if(!role) return;
  turn.setAttribute('data-transparent-turn-toggle-bound','1');
  // Add chevron if missing.
  if(!role.querySelector('.transparent-turn-chevron')){
    const chev=document.createElement('span');
    chev.className='transparent-turn-chevron';
    chev.innerHTML=li('chevron-down',10);
    role.appendChild(chev);
  }
  role.setAttribute('role','button');
  role.setAttribute('tabindex','0');
  role.setAttribute('aria-expanded',turn.getAttribute('data-transparent-turn-collapsed')==='1'?'false':'true');
  const toggle=function(ev){
    if(ev&&ev.target&&ev.target.closest&&ev.target.closest('.msg-tps-inline')) return;
    const collapsed=turn.getAttribute('data-transparent-turn-collapsed')==='1';
    turn.setAttribute('data-transparent-turn-collapsed',collapsed?'0':'1');
    role.setAttribute('aria-expanded',collapsed?'true':'false');
    // Persist state across DOM rebuilds.
    if(S.session){
      const seg=turn.querySelector('.assistant-segment');
      if(seg){
        const mi=seg.getAttribute('data-msg-idx');
        if(mi!=null) _transparentTurnCollapsedStates[`${S.session.session_id}:${mi}`]=!collapsed;
      }
    }
  };
  role.onclick=toggle;
  role.onkeydown=function(ev){
    if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();toggle(ev);}
  };
}
// ── Transparent old-event fading (medium → low) ───────────────────────────
// In long streams the earliest rows fade to a lower opacity so the user's
// eye lands on the most recent activity. The fade is per-turn: the newest
// event stays at full opacity, each earlier event drops one step. Floors
// at 0.32 so labels stay readable.
function _applyTransparentRowFading(turn){
  if(!turn||!isTransparentStream()) return;
  // Recency-fading only makes sense on the LIVE turn (draw the eye to the most
  // recent activity). On settled/historical turns it permanently dims the trace
  // below readable contrast (floor .32) — the opposite of a transparent record.
  // So clear any fade on non-live turns and only fade the live turn.
  // (Trifecta finding V8.)
  const blocks=_assistantTurnBlocks(turn);
  if(!blocks) return;
  const rows=Array.from(blocks.querySelectorAll(':scope > .transparent-event-row'));
  const isLive=turn.id==='liveAssistantTurn'||turn.getAttribute('data-live-assistant-turn')==='1';
  if(!isLive){
    rows.forEach(row=>row.removeAttribute('data-transparent-fade'));
    return;
  }
  const total=rows.length;
  for(let i=0;i<total;i++){
    const row=rows[i];
    // Newest = full opacity; each step back drops by 1 (floors at 5).
    const stepsFromEnd=total-1-i;
    if(stepsFromEnd<=0){row.removeAttribute('data-transparent-fade');continue;}
    const step=Math.min(5,stepsFromEnd);
    row.setAttribute('data-transparent-fade',String(step));
  }
}
// Resolve the assistant message that carries a transparent turn's settled
// metadata (duration / used model / TTFT / usage). A tool-using turn renders
// multiple assistant segments — earlier activity segment(s) then the final
// answer — and the metadata is stamped only on the LAST assistant message
// (see api/streaming.py finalization). `turn.querySelector('.assistant-segment')`
// returns the FIRST segment, which has no metadata, so the footer (model chip
// included) would render empty exactly on tool turns (#6068 gate round 2).
// Scan segments last→first for the final metadata-bearing assistant message,
// falling back to the last assistant segment when none carries metadata yet.
function _transparentTurnMetaMessage(turn){
  if(!turn)return null;
  const segs=turn.querySelectorAll('.assistant-segment[data-msg-idx]');
  let fallback=null;
  for(let si=segs.length-1;si>=0;si--){
    const mi=segs[si].getAttribute('data-msg-idx');
    if(mi==null)continue;
    const candidate=S.messages[Number(mi)];
    if(!candidate||candidate.role!=='assistant')continue;
    if(!fallback)fallback=candidate;
    if(candidate._turnDuration!=null||candidate._usedModel||candidate._firstTokenMs!=null||candidate._turnUsage)return candidate;
  }
  return fallback;
}
// ── Transparent turn footer (elapsed · tokens · TTFT · status) ───────────
// Mirrors the live run-status line for settled turns in transparent
// mode. Shows duration, first-token time, token usage, and final status.
// Only rendered for turns that have transparent event rows.
function _transparentTurnFooterHtml(durationText, modelText, ttftText, tokensText, statusText, modelTitle){
  const parts=[];
  if(durationText) parts.push(`<span class="lf-time">${esc(durationText)}</span>`);
  if(modelText){
    const titleAttr=(modelTitle&&modelTitle!==modelText)?` title="${esc(modelTitle)}"`:'';
    parts.push(`<span class="lf-model"${titleAttr}>${esc(modelText)}</span>`);
  }
  if(ttftText) parts.push(`<span class="lf-ttft" title="${esc(t('first_token_time')||'Time to first token')}">TTFT ${esc(ttftText)}</span>`);
  if(tokensText) parts.push(`<span class="lf-tokens">${esc(tokensText)}</span>`);
  if(statusText) parts.push(`<span class="lf-status">${esc(statusText)}</span>`);
  if(!parts.length) return '';
  return `<div class="transparent-turn-footer">${parts.join('<span class="lf-sep">·</span>')}</div>`;
}
function _renderTransparentTurnFooter(turn, opts){
  if(!turn||!isTransparentStream()) return;
  const blocks=_assistantTurnBlocks(turn);
  if(!blocks) return;
  const hasRows=blocks.querySelector(':scope > .transparent-event-row');
  if(!hasRows){
    // No events → no footer (the answer itself carries the duration).
    const existing=turn.querySelector('.transparent-turn-footer');
    if(existing) existing.remove();
    return;
  }
  const durationText=opts&&opts.durationText||'';
  const modelText=opts&&opts.modelText||'';
  const modelTitle=opts&&opts.modelTitle||'';
  const ttftText=opts&&opts.ttftText||'';
  const tokensText=opts&&opts.tokensText||'';
  const statusText=opts&&opts.statusText||(t('done')||'Done');
  const html=_transparentTurnFooterHtml(durationText, modelText, ttftText, tokensText, statusText, modelTitle);
  let footer=turn.querySelector('.transparent-turn-footer');
  if(!html){
    if(footer) footer.remove();
    return;
  }
  if(!footer){
    footer=document.createElement('div');
    footer.className='transparent-turn-footer';
    const blocks=turn.querySelector('.assistant-turn-blocks');
    // Guard: nextSibling may be null (blocks is last child) or orphaned from
    // a prior DOM rebuild. Only insertBefore when it is still a child of turn.
    if(blocks&&blocks.nextSibling&&blocks.nextSibling.parentNode===turn){
      turn.insertBefore(footer, blocks.nextSibling);
    }else{
      turn.appendChild(footer);
    }
  }
  footer.innerHTML=html.replace(/^<div class="transparent-turn-footer">|<\/div>$/g,'');
}
// ── Activity-group user expand intent (#1298) ──────────────────────────────
// When the user manually expands the live "Activity" dropdown during streaming,
// preserve that intent across the destroy/recreate cycle that fires on every
// thinking/tool event. Without this, ensureActivityGroup() re-creates the group
// with the default collapsed state and finalizeThinkingCard() force-collapses
// it whenever the assistant transitions from thinking → tool → thinking, so
// the panel snaps shut every few seconds while the user is trying to read it.
//
// The tracker is a singleton boolean: there is at most one live activity group
// at a time (selector .tool-call-group[data-live-tool-call-group="1"]). It is
// set to true when the user clicks the summary to expand, false when they
// click to collapse, and cleared back to undefined when the live group is
// finalized into a settled assistant turn (the live attribute is removed in
// _convertLiveActivityGroupToSettled / when liveAssistantTurn loses its id).
let _liveActivityUserExpanded;
const _activityDisclosureStoragePrefix='agy-activity-disclosure:';
function _activityDisclosureStorageKey(activityKey){
  if(!activityKey||!S.session||!S.session.session_id) return null;
  return _activityDisclosureStoragePrefix+S.session.session_id+':'+activityKey;
}
function _readActivityDisclosureState(activityKey){
  const key=_activityDisclosureStorageKey(activityKey);
  if(!key) return null;
  try{
    const saved=localStorage.getItem(key);
    return saved==='open'||saved==='closed'?saved:null;
  }catch(_){return null;}
}
function _writeActivityDisclosureState(activityKey, open){
  const key=_activityDisclosureStorageKey(activityKey);
  if(!key) return;
  try{localStorage.setItem(key, open?'open':'closed');}catch(_){}
}
function _copyActivityDisclosureState(fromActivityKey, toActivityKey){
  const state=_readActivityDisclosureState(fromActivityKey);
  if(state) _writeActivityDisclosureState(toActivityKey, state==='open');
}
function _activityKeyForLiveTurn(){
  return S.activeStreamId?'live:'+S.activeStreamId:null;
}
function _onLiveActivityToggle(group){
  if(!group) return;
  // Only track explicit user clicks on the live group, not programmatic toggles.
  if(group.getAttribute('data-live-tool-call-group')!=='1') return;
  _liveActivityUserExpanded = !group.classList.contains('tool-call-group-collapsed');
}
function _materializeDeferredWorklogRows(group){
  // #5839: build the row DOM for a settled worklog whose rows were deferred at
  // render time (collapsed). Idempotent — clears the marker so it runs once.
  if(!group||group.getAttribute('data-worklog-rows-deferred')!=='1') return false;
  let rows=group._deferredWorklogRows;
  // The JS-property stash is dropped when the transcript is restored from the
  // HTML cache (innerHTML round-trip). Recover the rows from the owning message
  // via the disclosure key (anchor-scene:<rawIdx>) so a post-restore expand
  // still fills the worklog. (#5839)
  if((!rows||!rows.length)&&typeof _deferredWorklogRowsFromGroup==='function'){
    rows=_deferredWorklogRowsFromGroup(group);
  }
  group.removeAttribute('data-worklog-rows-deferred');
  group._deferredWorklogRows=null;
  if(!rows||!rows.length) return false;
  const ok=_renderAnchorSceneRowsIntoWorklog(group,rows,{settled:true});
  if(!ok) return false;
  // #5839 fix: the eager render path post-processes its rows (syntax highlight,
  // copy buttons, mermaid, katex, structured trees) and restores detail-disclosure
  // state; a lazily-materialized group must do the same or expanded rows render
  // un-enhanced and any captured open/scroll state is lost. Post-process on the
  // next frame (matching the eager rebuild paths), then re-apply the disclosure
  // state stashed with the group at defer time.
  const disclosure=group._deferredWorklogDisclosure;
  group._deferredWorklogDisclosure=null;
  if(typeof _postProcessWithAnchorSuppression==='function'
     && typeof requestAnimationFrame==='function'){
    requestAnimationFrame(()=>{
      _postProcessWithAnchorSuppression(group);
      if(disclosure&&disclosure.size&&typeof _restoreWorklogDetailDisclosureState==='function'){
        _restoreWorklogDetailDisclosureState(group, disclosure);
      }
    });
  }else if(disclosure&&disclosure.size&&typeof _restoreWorklogDetailDisclosureState==='function'){
    _restoreWorklogDetailDisclosureState(group, disclosure);
  }
  return true;
}
function _deferredWorklogRowsFromGroup(group){
  // Recover a settled worklog's rows from S.messages using the group's
  // disclosure key `anchor-scene:<rawIdx>`. Used after an HTML-cache restore
  // where the _deferredWorklogRows JS property was dropped. (#5839)
  const key=group&&group.getAttribute&&group.getAttribute('data-activity-disclosure-key');
  const m=key&&/^anchor-scene:(\d+)$/.exec(key);
  if(!m) return null;
  const msg=S.messages&&S.messages[Number(m[1])];
  const scene=msg&&msg._anchor_activity_scene;
  if(!scene) return null;
  return _anchorSceneRowsForRendering(scene,{settled:true});
}
function _rehydrateDeferredWorklogsFromCache(root){
  // After restoring a transcript from _sessionHtmlCache, deferred settled
  // worklogs carry data-worklog-rows-deferred="1" but lost their stashed rows
  // (JS properties don't survive innerHTML). Re-stash from the owning message so
  // the first expand materializes correctly. (#5839)
  if(!root||!root.querySelectorAll) return;
  root.querySelectorAll('[data-worklog-rows-deferred="1"]').forEach(group=>{
    if(group._deferredWorklogRows&&group._deferredWorklogRows.length) return;
    const rows=_deferredWorklogRowsFromGroup(group);
    if(rows&&rows.length) group._deferredWorklogRows=rows;
    else group.removeAttribute('data-worklog-rows-deferred'); // nothing to defer
  });
}
function _toggleActivityGroup(summary){
  const group=summary&&summary.closest?summary.closest('.agent-activity-group,.tool-call-group'):null;
  if(!group) return;
  const collapsed=group.classList.toggle('tool-call-group-collapsed');
  group.classList.toggle('open',!collapsed);
  summary.setAttribute('aria-expanded',String(!collapsed));
  // #5839: materialize deferred settled rows on first expand (lazy render).
  if(!collapsed) _materializeDeferredWorklogRows(group);
  _writeActivityDisclosureState(group.getAttribute('data-activity-disclosure-key'), !collapsed);
  if(typeof _onLiveActivityToggle==='function') _onLiveActivityToggle(group);
}
function _toggleToolWorklogGroup(summary){
  const group=summary&&summary.closest?summary.closest('.tool-worklog-tool-group,.tool-group'):null;
  if(group){
    const collapsed=group.classList.toggle('tool-worklog-tool-group-collapsed');
    group.classList.toggle('open',!collapsed);
    summary.setAttribute('aria-expanded',String(!collapsed));
    return;
  }
  return _toggleActivityGroup(summary);
}
function _finalizeLiveActivityDisclosureGroup(group){
  if(!group) return;
  const keepOpen=!!(
    group.querySelector&&group.querySelector('.tool-card.open,.thinking-card.open,.tool-group.open,.tool-worklog-tool-group.open')
  );
  const disclosureKey=group.getAttribute('data-activity-disclosure-key')||group.getAttribute('data-tool-worklog-key')||'';
  group.removeAttribute('data-live-activity-current');
  group.removeAttribute('data-live-tool-call-group');
  group.removeAttribute('data-live-tool-worklog-group');
  group.removeAttribute('data-live-anchor-scene-owner');
  group.classList.toggle('tool-call-group-collapsed', !keepOpen);
  group.classList.toggle('open', keepOpen);
  if(keepOpen&&disclosureKey) _writeActivityDisclosureState(disclosureKey, true);
  const summary=group.querySelector&&group.querySelector('.tool-worklog-summary,.tool-call-group-summary');
  if(summary){
    summary.removeAttribute('data-live-summary-static');
    summary.removeAttribute('aria-disabled');
    summary.disabled=false;
    summary.setAttribute('aria-expanded',keepOpen?'true':'false');
  }
  if(typeof _syncToolCallGroupSummary==='function') _syncToolCallGroupSummary(group);
}
function _worklogReasonHtmlFromAnchor(anchor, textOverride){
  if(!anchor||!anchor.matches||!anchor.matches('.assistant-segment')) return '';
  const body=anchor.querySelector&&anchor.querySelector('.msg-body');
  const hasOverride=arguments.length>1;
  const text=hasOverride?String(textOverride||''):((body?body.textContent:anchor.textContent)||'');
  if(!String(text||'').trim()) return '';
  if(String(text||'').trim()==='(empty)') return '';
  if(hasOverride) return _worklogReasonHtmlFromText(text);
  return body?body.innerHTML:esc(String(text||'').trim());
}
function _worklogReasonHtmlFromText(text){
  const clean=_sanitizeThinkingDisplayText(text);
  if(!String(clean||'').trim()) return '';
  if(String(clean||'').trim()==='(empty)') return '';
  return renderMd?renderMd(clean):esc(clean);
}
function _renderWorklogReasonInto(row, text){
  if(!row) return;
  const html=_worklogReasonHtmlFromText(text);
  row.innerHTML=html;
}
function _worklogReasonNodeFromText(text, attrs){
  if(window._showThinking===false) return null;
  const html=_worklogReasonHtmlFromText(text);
  if(!html) return null;
  const row=document.createElement('div');
  row.className='wl-reason';
  row.setAttribute('data-worklog-reason-source','reasoning');
  if(attrs&&attrs.active) row.setAttribute('data-worklog-reason-active','1');
  row.innerHTML=html;
  return row;
}
let _worklogAnchorKeySeq=0;
function _worklogReasonAnchorKey(anchor){
  if(!anchor||!anchor.dataset) return '';
  if(anchor.dataset.worklogAnchorKey) return anchor.dataset.worklogAnchorKey;
  const segmentSeq=anchor.getAttribute('data-live-segment-seq')||'';
  const burstId=anchor.getAttribute('data-activity-burst-id')||'';
  const msgIdx=anchor.getAttribute('data-msg-idx')||'';
  const raw=String(anchor.getAttribute('data-raw-text')||anchor.textContent||'').trim().slice(0,80);
  const key=segmentSeq
    ? `segment:${segmentSeq}`
    : msgIdx
    ? `msg:${msgIdx}`
    : burstId&&raw
    ? `burst:${burstId}:${raw}`
    : burstId
    ? `burst:${burstId}`
    : `node:${++_worklogAnchorKeySeq}`;
  anchor.dataset.worklogAnchorKey=key;
  return key;
}
function _syncWorklogReasonFromAnchor(group, anchor, displayTextOverride){
  const list=_toolWorklogListEl(group);
  if(!group||!list) return;
  const anchorKey=_worklogReasonAnchorKey(anchor);
  const selector=anchorKey?`:scope > .wl-reason[data-worklog-anchor-key="${CSS.escape(anchorKey)}"]`:':scope > .wl-reason[data-worklog-anchor-reason="1"]';
  // When reasoning/thinking display is turned off (#3903), do not render Worklog
  // reasoning rows on the live OR settled path — remove any existing one and bail
  // before building. (The gate must live here, in the actual render path, not in
  // the unused _worklogReasonNodeFromText helper.)
  if(window._showThinking===false){
    const existing=list.querySelector(selector);
    if(existing) existing.remove();
    return;
  }
  const html=arguments.length>2
    ? _worklogReasonHtmlFromAnchor(anchor, displayTextOverride)
    : _worklogReasonHtmlFromAnchor(anchor);
  let reason=list.querySelector(selector);
  if(!html){
    if(reason) reason.remove();
    return;
  }
  if(!reason){
    reason=document.createElement('div');
    reason.className='wl-reason';
    reason.setAttribute('data-worklog-anchor-reason','1');
    if(anchorKey) reason.setAttribute('data-worklog-anchor-key',anchorKey);
    list.appendChild(reason);
  }
  reason.innerHTML=html;
  if(anchor){
    anchor.classList.add('assistant-segment-worklog-source');
    anchor.setAttribute('aria-hidden','true');
    anchor.hidden=true;
  }
}
function ensureLiveWorklogContainer(blocks, opts){
  opts=opts||{};
  if(!blocks) return null;
  const activityKey=opts.activityKey||_activityKeyForLiveTurn();
  let worklog=activityKey
    ? blocks.querySelector(`.live-worklog[data-live-worklog-shell="1"][data-tool-worklog-key="${CSS.escape(activityKey)}"]`)
    : null;
  if(!worklog) worklog=blocks.querySelector('.live-worklog[data-live-worklog-shell="1"][data-live-activity-current="1"]');
  if(!worklog){
    worklog=document.createElement('div');
    worklog.className='live-worklog worklog';
    worklog.setAttribute('data-live-worklog-shell','1');
    worklog.setAttribute('data-live-tool-worklog-group','1');
    worklog.setAttribute('data-live-tool-call-group','1');
    worklog.setAttribute('data-live-activity-current','1');
    worklog.setAttribute('data-tool-worklog-group','1');
    worklog.setAttribute('data-tool-worklog-key',activityKey||'');
    worklog.innerHTML='<div class="tool-worklog-list"></div>';
    const anchor=opts.anchor||null;
    const footer=blocks.querySelector('#liveRunStatus');
    if(anchor&&anchor.parentElement===blocks) anchor.insertAdjacentElement('afterend',worklog);
    else if(footer&&footer.parentElement===blocks) blocks.insertBefore(worklog,footer);
    else blocks.appendChild(worklog);
  }else if(activityKey&&!worklog.getAttribute('data-tool-worklog-key')){
    worklog.setAttribute('data-tool-worklog-key',activityKey);
  }
  if(opts.anchor) _syncWorklogReasonFromAnchor(worklog, opts.anchor);
  _migrateLegacyLiveActivityGroupsToWorklog(blocks, worklog);
  _syncToolCallGroupSummary(worklog);
  return worklog;
}
function _migrateLegacyLiveActivityGroupsToWorklog(blocks, worklog){
  if(!blocks||!worklog) return;
  const list=_toolWorklogListEl(worklog);
  if(!list) return;
  const legacy=Array.from(blocks.querySelectorAll('.tool-worklog-group[data-live-tool-call-group="1"],.tool-call-group[data-live-tool-call-group="1"]'))
    .filter(group=>group!==worklog && !group.classList.contains('live-worklog'));
  for(const group of legacy){
    const oldList=_toolWorklogListEl(group);
    if(oldList){
      while(oldList.firstChild) list.appendChild(oldList.firstChild);
    }
    group.remove();
  }
}
function _appendWorklogReason(list, anchor){
  if(!list) return null;
  // Reasoning display off (#3903): never append a Worklog reasoning row.
  if(window._showThinking===false) return null;
  const html=_worklogReasonHtmlFromAnchor(anchor);
  if(!html) return null;
  const reason=document.createElement('div');
  reason.className='wl-reason';
  reason.setAttribute('data-worklog-anchor-reason','1');
  const anchorKey=_worklogReasonAnchorKey(anchor);
  if(anchorKey) reason.setAttribute('data-worklog-anchor-key',anchorKey);
  reason.innerHTML=html;
  list.appendChild(reason);
  if(anchor){
    anchor.classList.add('assistant-segment-worklog-source');
    anchor.setAttribute('aria-hidden','true');
    anchor.hidden=true;
  }
  return reason;
}
function _toolIdentity(tc){
  if(!tc) return '';
  const tid=tc.tid||tc.id||tc.tool_call_id||tc.tool_use_id||tc.call_id||'';
  if(tid) return `id:${tid}`;
  const args=tc.args&&typeof tc.args==='object'?tc.args:{};
  return [
    tc.assistant_msg_idx!==undefined?`a:${tc.assistant_msg_idx}`:'',
    tc.name||'tool',
    JSON.stringify(args),
    String(tc.snippet||tc.preview||'').slice(0,160),
  ].join('|');
}
function _toolDisclosureIdentity(tc){
  if(!tc) return '';
  const tid=tc.tid||tc.id||tc.tool_call_id||tc.tool_use_id||tc.call_id||'';
  if(tid) return `id:${tid}`;
  const stable=[
    tc.assistant_msg_idx!==undefined?`a:${tc.assistant_msg_idx}`:'',
    tc.name||'tool',
  ].join('\x1f');
  return stable.trim()?`derived:${_worklogDetailHashKey(stable)}`:'';
}
function _filterNewWorklogTools(cards, seenTools){
  const out=[];
  for(const tc of Array.from(cards||[]).filter(Boolean)){
    const key=_toolIdentity(tc);
    if(key&&seenTools&&seenTools.has(key)) continue;
    if(key&&seenTools) seenTools.add(key);
    out.push(tc);
  }
  return out;
}
function _anchorSceneToolRowLogicalKey(row){
  if(!row||row.role!=='tool') return '';
  const tool=(row.tool&&typeof row.tool==='object')?row.tool:{};
  const payload=(row.payload&&typeof row.payload==='object')?row.payload:{};
  const id=row.tool_call_id||tool.id||tool.tid||tool.tool_call_id||tool.tool_use_id||tool.call_id||
    payload.tid||payload.id||payload.tool_call_id||payload.tool_use_id||payload.call_id||'';
  return id?`call:${id}`:'';
}
function _anchorSceneMergeToolRows(prev, row){
  if(!prev) return row;
  const prevTool=(prev.tool&&typeof prev.tool==='object')?prev.tool:{};
  const nextTool=(row&&row.tool&&typeof row.tool==='object')?row.tool:{};
  const prevPayload=(prev.payload&&typeof prev.payload==='object')?prev.payload:{};
  const nextPayload=(row&&row.payload&&typeof row.payload==='object')?row.payload:{};
  const prevArgs=(prevTool.args&&typeof prevTool.args==='object')?prevTool.args:
    ((prevPayload.args&&typeof prevPayload.args==='object')?prevPayload.args:{});
  const nextArgs=(nextTool.args&&typeof nextTool.args==='object')?nextTool.args:
    ((nextPayload.args&&typeof nextPayload.args==='object')?nextPayload.args:{});
  const mergedPayload=Object.assign({},prevPayload,nextPayload);
  const mergedTool=Object.assign({},prevTool,nextTool);
  if(!Object.keys(nextArgs).length&&Object.keys(prevArgs).length) mergedTool.args=prevArgs;
  const prevPreview=String(prevTool.preview||prevPayload.preview||'').trim();
  const nextText=String(row&&row.text||'').trim();
  const nextSnippet=String(nextTool.snippet||nextPayload.snippet||nextPayload.result||nextPayload.output||'').trim();
  const nextStatus=String(row&&row.status||'').toLowerCase();
  const nextLooksLikeResult=!!nextSnippet||(
    !!nextText&&nextStatus&&nextStatus!=='running'&&nextStatus!=='pending'
  );
  if(prevPreview&&nextLooksLikeResult){
    mergedTool.preview=prevTool.preview||prevPayload.preview||prevPreview;
    if(!mergedPayload.preview) mergedPayload.preview=prevPayload.preview||prevPreview;
  }
  if(nextSnippet) mergedTool.snippet=nextTool.snippet||nextPayload.snippet||nextPayload.result||nextPayload.output||nextSnippet;
  return Object.assign({},prev,row,{
    row_id:prev.row_id||row.row_id,
    order_index:prev.order_index??row.order_index,
    payload:mergedPayload,
    tool:mergedTool,
  });
}
function _appendWorklogStep(group, anchor, cards, thinkingText, opts){
  const list=_toolWorklogListEl(group);
  if(!group||!list) return;
  let wroteProse=false;
  const seenReasons=opts&&opts.seenReasons;
  if(!opts||opts.includeAnchorReason!==false){
    const anchorKey=anchor&&anchor.dataset&&anchor.dataset.msgIdx?`anchor:${anchor.dataset.msgIdx}`:'';
    if(!anchorKey||!seenReasons||!seenReasons.has(anchorKey)){
      const reason=_appendWorklogReason(list, anchor);
      if(reason){
        wroteProse=true;
        if(anchorKey&&seenReasons) seenReasons.add(anchorKey);
      }
    }
  }
  if(thinkingText){
    const thinkingKey=(opts&&opts.thinkingKey)||`reason:${String(thinkingText).trim()}`;
    const thinkingDisclosureKey=(opts&&opts.thinkingDisclosureKey)||thinkingKey;
    if(!seenReasons||!seenReasons.has(thinkingKey)){
      const thinking=_thinkingActivityNode(thinkingText, false, thinkingDisclosureKey);
      if(thinking){
        list.appendChild(thinking);
        wroteProse=true;
        if(seenReasons) seenReasons.add(thinkingKey);
      }
    }
  }
  const toolCards=_filterNewWorklogTools(cards, opts&&opts.seenTools);
  if(toolCards.length){
    const last=list.lastElementChild;
    let tools=(!wroteProse&&last&&last.classList&&last.classList.contains('wl-step-tools')&&last.getAttribute('data-worklog-tools')==='1')
      ? last
      : null;
    if(!tools){
      tools=document.createElement('div');
      tools.className='wl-step-tools tool-worklog-tools';
      tools.setAttribute('data-worklog-tools','1');
      list.appendChild(tools);
    }
    for(const tc of toolCards) tools.appendChild(buildToolCard(tc));
    _syncToolRowsContainer(tools, !!(opts&&opts.live));
  }
}
function _anchorSceneRowsForRendering(scene, opts){
  const rows=Array.isArray(scene&&scene.activity_rows)?scene.activity_rows:[];
  const settled=!!(opts&&opts.settled);
  const live=!settled;
  const out=[];
  const byKey=new Map();
  const liveProseTextKeys=new Map();
  const proseTextKey=(value)=>String(value||'').replace(/\s+/g,' ').trim();
  const keyFor=(row)=>{
    if(!row) return '';
    if(row.role==='tool') return `tool:${_anchorSceneToolRowLogicalKey(row)||row.row_id||row.event_id||row.local_id||out.length}`;
    if(row.role==='prose') return `prose:${row.local_id||row.row_id||out.length}`;
    if(row.role==='thinking') return `thinking:${row.local_id||row.row_id||out.length}`;
    if(row.role==='lifecycle'){
      const source=String(row.source_event_type||'');
      if(source==='compressing'||source==='compressed') return 'lifecycle:compression';
      return `lifecycle:${source||row.local_id||row.row_id||out.length}`;
    }
    return `row:${row.row_id||out.length}`;
  };
  for(const row of rows){
    if(!row||typeof row!=='object') continue;
    if(row.role==='terminal'&&row.source_event_type==='done') continue;
    if(_anchorSceneIsSettledSuccessfulCompression(row,settled)) continue;
    const text=String(row.text||'').trim();
    if((row.role==='prose'||row.role==='thinking')&&!text) continue;
    const key=keyFor(row);
    if(byKey.has(key)){
      const index=byKey.get(key);
      if(live&&row.role==='prose'){
        const textKey=proseTextKey(text);
        const duplicateIndex=textKey?liveProseTextKeys.get(textKey):undefined;
        if(duplicateIndex!==undefined&&duplicateIndex!==index) continue;
        const previousTextKey=proseTextKey(out[index]&&out[index].text);
        if(previousTextKey&&previousTextKey!==textKey&&liveProseTextKeys.get(previousTextKey)===index){
          liveProseTextKeys.delete(previousTextKey);
        }
        if(textKey) liveProseTextKeys.set(textKey,index);
      }
      out[index]=row.role==='tool'?_anchorSceneMergeToolRows(out[index],row):row;
    }else{
      if(live&&row.role==='prose'){
        const textKey=proseTextKey(text);
        if(textKey&&liveProseTextKeys.has(textKey)) continue;
        if(textKey) liveProseTextKeys.set(textKey,out.length);
      }
      byKey.set(key,out.length);
      out.push(row);
    }
  }
  return out;
}
function _anchorSceneIsSettledSuccessfulCompression(row, settled){
  if(!settled||!row||row.role!=='lifecycle') return false;
  const source=String(row.source_event_type||'');
  if(source!=='compressing'&&source!=='compressed') return false;
  const status=String(row.status||'').toLowerCase();
  return !['error','failed','failure','compression_exhausted','degraded','interrupted','connection_lost'].includes(status);
}
function _anchorSceneToolCallFromRow(row, opts){
  const tool=(row&&row.tool&&typeof row.tool==='object')?row.tool:{};
  const payload=(row&&row.payload&&typeof row.payload==='object')?row.payload:{};
  const timestampSeconds=typeof _timestampSeconds==='function'?_timestampSeconds:function(value){
    const stamp=Number(value);
    return Number.isFinite(stamp)&&stamp>0?(stamp>1e12?stamp/1000:stamp):null;
  };
  const firstValidTimestampSeconds=typeof _firstValidTimestampSeconds==='function'
    ? _firstValidTimestampSeconds
    : function(...values){
        for(const value of values){
          const stamp=timestampSeconds(value);
          if(stamp) return stamp;
        }
        return null;
      };
  const rowTs=typeof _anchorSceneRowTimestampSeconds==='function'
    ? _anchorSceneRowTimestampSeconds(row)
    : firstValidTimestampSeconds(row&&row.created_at, row&&row.timestamp, row&&row.ts, row&&row.started_at, row&&row.completed_at);
  const id=tool.id||row.tool_call_id||payload.tid||payload.id||payload.tool_call_id||payload.tool_use_id||payload.call_id||'';
  const settled=!!(opts&&opts.settled);
  return {
    name:tool.name||payload.name||'tool',
    args:(tool.args&&typeof tool.args==='object')?tool.args:((payload.args&&typeof payload.args==='object')?payload.args:{}),
    command:tool.command||payload.command||payload.cmd||'',
    raw_command:tool.raw_command||payload.raw_command||'',
    preview:tool.preview||payload.preview||'',
    snippet:tool.snippet||payload.snippet||payload.result||payload.output||(
      row&&row.status!=='running'&&row.status!=='pending'?row.text:''
    )||'',
    done:settled?true:(tool.done!==null&&tool.done!==undefined?tool.done:(row.status!=='running'&&row.status!=='pending')),
    is_error:!!(tool.is_error||payload.is_error||row.status==='error'||row.status==='failed'),
    is_diff:!!(tool.is_diff||payload.is_diff||payload.isDiff),
    duration:tool.duration||payload.duration||payload.duration_seconds,
    started_at:firstValidTimestampSeconds(tool.started_at, payload.started_at, rowTs),
    created_at:firstValidTimestampSeconds(tool.created_at, payload.created_at, rowTs),
    timestamp:firstValidTimestampSeconds(tool.timestamp, payload.timestamp, rowTs),
    ts:firstValidTimestampSeconds(
      tool.ts,
      payload.ts,
      tool.timestamp,
      payload.timestamp,
      tool.created_at,
      payload.created_at,
      rowTs
    ),
    tid:id,
    id,
  };
}
function _anchorSceneRowTimestampSeconds(row){
  if(!row) return null;
  const timestampSeconds=typeof _timestampSeconds==='function'?_timestampSeconds:function(value){
    const stamp=Number(value);
    return Number.isFinite(stamp)&&stamp>0?(stamp>1e12?stamp/1000:stamp):null;
  };
  for(const key of ['created_at','timestamp','ts','started_at','completed_at']){
    const stamp=timestampSeconds(row[key]);
    if(stamp) return stamp;
  }
  return null;
}
function _anchorSceneNodeForRow(row, opts){
  const settled=!!(opts&&opts.settled);
  if(!row) return null;
  let node=null;
  if(row.role==='prose'){
    const text=String(row.text||'').trim();
    if(!text) return null;
    // Incremental live rendering: reuse a persistent smd node fed only the delta
    // instead of re-parsing the whole growing answer on every streamed frame
    // (O(n^2) -> O(n)). Settled rows and any failure fall through to the full
    // renderMd path below, which stays the source of truth for the final DOM.
    const proseKey=row.local_id||row.row_id||'';
    if(!settled && proseKey && typeof window.__anchorProseIncrementalNode==='function'){
      const inc=window.__anchorProseIncrementalNode(proseKey,text,{
        finalize:String(row.status||'').toLowerCase()==='completed',
      });
      // Route the incremental node through the shared row-decoration block below
      // (data-anchor-scene-row / -row-id / -row-role / -source-event-type) instead
      // of returning early — otherwise live incremental prose rows lose the
      // identity attributes the scene reconciler matches on. (Codex gate #5466)
      if(inc){ node=inc; }
    }
    if(!node){
      node=document.createElement('div');
      node.className='assistant-segment';
      node.setAttribute('data-anchor-scene-prose','1');
      node.dataset.rawText=text;
      node.innerHTML=`<div class="msg-body">${renderMd?renderMd(text):esc(text)}</div>`;
    }
  }else if(row.role==='thinking'){
    if(window._showThinking===false) return null;
    const text=String(row.text||row.thinking&&row.thinking.text||'').trim();
    if(!text) return null;
    node=_thinkingActivityNode(text, false, row.row_id||row.local_id||'anchor-thinking');
  }else if(row.role==='tool'){
    node=buildToolCard(_anchorSceneToolCallFromRow(row,opts));
  }else if(row.role==='lifecycle'){
    if(row.source_event_type==='compressing'||row.source_event_type==='compressed'){
      node=_autoCompressionWorklogNode({
        phase:settled||row.source_event_type==='compressed'?'done':'running',
        automatic:true,
        message:row.text||'Compressing context',
      });
    }else{
      node=_activityStatusNode({
        kind:settled?'done':'waiting',
        label:row.text||row.status||'Working',
        status:!settled&&row.status==='running'?'running':'done',
        id:row.row_id||row.local_id||'',
      });
    }
  }else if(row.role==='control'){
    node=_activityStatusNode({
      kind:settled?'done':'waiting',
      label:row.text||row.source_event_type||'Waiting',
      status:settled?'done':'running',
      id:row.row_id||row.local_id||'',
    });
  }else if(row.role==='terminal'){
    const status=String(row.status||row.source_event_type||'').trim();
    const isError=['error','failed','connection_lost','interrupted','compression_exhausted','tool_limit_reached','no_response'].includes(status);
    node=_activityStatusNode({
      kind:isError?'warning':'done',
      label:row.text||status||'Turn ended',
      status:settled?'done':(isError?'error':'done'),
      id:row.row_id||row.local_id||'',
    });
  }
  if(!node) return null;
  node.setAttribute('data-anchor-scene-row','1');
  node.setAttribute('data-anchor-row-id',String(row.row_id||row.local_id||''));
  if(row.local_id) node.setAttribute('data-anchor-local-id',String(row.local_id));
  node.setAttribute('data-anchor-row-role',String(row.role||'activity'));
  node.setAttribute('data-anchor-source-event-type',String(row.source_event_type||''));
  return node;
}
function _anchorSceneTransparentNodeForRow(row, opts){
  const settled=!!(opts&&opts.settled);
  const live=!!(opts&&opts.live);
  if(!row) return null;
  let node=null;
  const eventTs=typeof _anchorSceneRowTimestampSeconds==='function'?_anchorSceneRowTimestampSeconds(row):null;
  const meta={
    segmentSeq:row.segment_seq||row.segmentSeq||'',
    burstId:row.activity_burst_id||row.burst_id||row.burstId||'',
  };
  if(row.role==='prose'){
    // The settled assistant segment already owns the FINAL answer prose, so a
    // prose row whose text matches the final answer must be suppressed here to
    // avoid duplicating the answer. But INTERMEDIATE progress prose (the
    // between-tool narration from earlier rounds) is NOT the final answer and
    // belongs in the chronological transparent history — dropping it loses the
    // interleaving the user saw live (#4568: tools were restored but mid-turn
    // prose still vanished on reload). Render intermediate prose as an inline
    // assistant-segment (same shape _anchorSceneNodeForRow builds), skip only
    // the final-answer duplicate.
    const text=String(row.text||'').trim();
    if(!text) return null;
    const finalAnswer=String((opts&&opts.finalAnswer)||'').trim();
    if(opts&&opts.liveTokenFinalPrefixEligible&&_anchorSceneLiveTokenFinalPrefix(row,text,finalAnswer)) return null;
    if(finalAnswer&&_anchorSceneProseMatchesFinalAnswer(text,finalAnswer)) return null;
    node=_anchorSceneNodeForRow(row,{settled});
    if(!node) return null;
    node=_decorateTransparentEventRow(node,{type:'prose',text,preview:text,...meta});
  }else if(row.role==='thinking'){
    if(window._showThinking===false) return null;
    const text=String(row.text||row.thinking&&row.thinking.text||'').trim();
    if(!text) return null;
    node=_decorateTransparentEventRow(_thinkingActivityNode(text,false,row.row_id||row.local_id||'anchor-thinking'),{
      type:'thinking',
      text,
      preview:text,
      ts:eventTs,
      ...meta,
      live,
    });
  }else if(row.role==='tool'){
    const toolCall=_anchorSceneToolCallFromRow(row,{settled});
    node=_decorateTransparentEventRow(buildToolCard(toolCall),{
      type:'tool',
      name:toolCall&&toolCall.name,
      status:_transparentToolStatus(toolCall,settled),
      toolCall,
      ts:eventTs,
      ...meta,
      live,
      settled,
    });
  }else{
    node=_anchorSceneNodeForRow(row,{settled});
    node=_decorateTransparentEventRow(node,{
      type:String(row.role||'activity'),
      ...meta,
      live,
    });
  }
  if(!node) return null;
  node.setAttribute('data-anchor-scene-row','1');
  if(settled) node.setAttribute('data-anchor-settled-scene-row','1');
  if(live) node.setAttribute('data-anchor-live-scene-row','1');
  node.setAttribute('data-anchor-row-id',String(row.row_id||row.local_id||''));
  if(row.local_id) node.setAttribute('data-anchor-local-id',String(row.local_id));
  node.setAttribute('data-anchor-row-role',String(row.role||'activity'));
  node.setAttribute('data-anchor-source-event-type',String(row.source_event_type||''));
  if(opts&&opts.streamId) node.setAttribute('data-anchor-stream-id',String(opts.streamId));
  if(opts&&opts.sessionId) node.setAttribute('data-session-id',String(opts.sessionId));
  if(live) node.setAttribute('data-live-stream-owned','1');
  return node;
}
function _anchorSceneLiveTokenFinalPrefix(row, proseText, finalAnswer){
  if(!row||row.role!=='prose'||row.kind!=='process_prose') return false;
  if(String(row.source_event_type||'')!=='token') return false;
  if(!String(row.local_id||'').startsWith('live-prose:')) return false;
  const norm=(s)=>String(s||'').replace(/\s+/g,' ').trim().toLowerCase();
  const rowKey=norm(proseText), finalKey=norm(finalAnswer);
  return !!(rowKey&&finalKey&&rowKey.length<finalKey.length&&finalKey.startsWith(rowKey));
}
function _anchorSceneLastNonTerminalWorkRowIndex(rows){
  if(!Array.isArray(rows)) return -1;
  return rows.reduce((last,row,idx)=>(row&&row.role==='tool')?idx:last,-1);
}
// Whitespace-insensitive compare so a scene prose row that IS the final answer
// (possibly re-wrapped) is recognized and not duplicated against the segment.
// Codex #4568: the prefix tolerance must NOT be able to suppress a DISTINCT
// intermediate progress row that merely happens to be a prefix of the final
// answer (e.g. "I found the issue and I'm applying the fix now." when the final
// answer starts with that sentence). So require exact normalized equality, with
// a prefix tolerance allowed ONLY when the two strings are near-equal length
// (>=0.9 ratio, shorter >=80 chars) — i.e. genuine re-wrap/truncation of the
// SAME text, never a short intermediate sentence that prefixes a long answer.
function _anchorSceneProseMatchesFinalAnswer(proseText, finalAnswer){
  const norm=(s)=>String(s||'').replace(/\s+/g,' ').trim();
  const a=norm(proseText), b=norm(finalAnswer);
  if(!a||!b) return false;
  if(a===b) return true;
  if(!(a.startsWith(b)||b.startsWith(a))) return false;
  const shorter=Math.min(a.length,b.length), longer=Math.max(a.length,b.length);
  return shorter>=80 && (shorter/longer)>=0.9;
}
function _anchorSceneWorklogGroup(blocks, opts){
  if(!blocks) return null;
  const live=!!(opts&&opts.live);
  const activityKey=(opts&&opts.activityKey)||'anchor-scene';
  let group=blocks.querySelector(`.tool-worklog-group[data-anchor-scene-owner="1"][data-tool-worklog-key="${CSS.escape(activityKey)}"]`);
  if(!group){
    group=ensureActivityGroup(blocks,{
      // Respect callers that need the settled activity group open. Round 6:
      // pinned followers keep the just-settled worklog open so STREAM_DONE does
      // not collapse hundreds of px of live worklog and visibly clamp the pane.
      collapsed:(opts&&opts.collapsed!==undefined)?opts.collapsed:!live,
      live,
      activityKey,
      beforeAnchor:!!(opts&&opts.beforeAnchor),
      anchor:(opts&&opts.anchor)||null,
      turnDuration:opts&&opts.turnDuration,
      turnStartedAt:opts&&opts.turnStartedAt,
      syncAnchorReason:false,
    });
  }
  if(!group) return null;
  group.setAttribute('data-anchor-scene-owner','1');
  if(live) group.setAttribute('data-live-anchor-scene-owner','1');
  group.setAttribute('data-tool-worklog-key',activityKey);
  if(opts&&opts.streamId) group.setAttribute('data-anchor-stream-id',String(opts.streamId));
  if(opts&&opts.turnDuration!==undefined&&opts.turnDuration!==null) group.setAttribute('data-turn-duration',String(opts.turnDuration));
  if(opts&&opts.turnStartedAt!==undefined&&opts.turnStartedAt!==null) group.setAttribute('data-turn-started-at',String(opts.turnStartedAt));
  return group;
}
function _renderAnchorSceneRowsIntoWorklog(group, rows, opts){
  const list=_toolWorklogListEl(group);
  if(!group||!list) return false;
  list.innerHTML='';
  let wrote=false;
  let currentTools=null;
  for(const row of rows){
    const node=_anchorSceneNodeForRow(row,opts);
    if(!node) continue;
    if(row.role==='tool'){
      if(!currentTools){
        currentTools=document.createElement('div');
        currentTools.className='wl-step-tools tool-worklog-tools';
        currentTools.setAttribute('data-worklog-tools','1');
        list.appendChild(currentTools);
      }
      currentTools.appendChild(node);
    }else{
      currentTools=null;
      list.appendChild(node);
    }
    wrote=true;
  }
  if(wrote){
    _syncToolCallGroupSummary(group);
  }
  return wrote;
}
function _liveProcessedWorklogAnchorScore(group, index){
  if(!group) return -1;
  const hasRows=!!group.querySelector('.tool-card-row,.wl-reason,.agent-activity-thinking,[data-anchor-scene-row="1"]');
  const label=group.querySelector('.tool-worklog-label,.tool-call-group-label');
  const text=String(label&&label.textContent||'').trim();
  const hasElapsed=!!(group.getAttribute('data-active-turn-elapsed')||/\d/.test(text));
  let score=index;
  if(hasElapsed) score+=400;
  if(hasRows) score+=200;
  if(group.getAttribute('data-live-activity-current')==='1') score+=80;
  if(group.getAttribute('data-anchor-scene-owner')==='1') score+=40;
  if(group.getAttribute('data-live-tool-call-group')==='1') score+=20;
  return score;
}
function _dedupeLiveProcessedWorklogAnchors(turn){
  const blocks=_assistantTurnBlocks(turn||$('liveAssistantTurn'));
  if(!blocks) return null;
  const groups=Array.from(blocks.querySelectorAll(
    '.tool-worklog-group[data-tool-worklog-group="1"],'+
    '.tool-call-group[data-tool-worklog-group="1"],'+
    '.live-worklog[data-live-worklog-shell="1"]'
  )).filter(group=>group&&group.isConnected!==false);
  if(groups.length<=1) return groups[0]||null;
  let keep=groups[0];
  let keepScore=_liveProcessedWorklogAnchorScore(keep,0);
  groups.forEach((group,index)=>{
    const score=_liveProcessedWorklogAnchorScore(group,index);
    if(score>=keepScore){
      keep=group;
      keepScore=score;
    }
  });
  groups.forEach(group=>{
    if(group!==keep) group.remove();
  });
  if(keep&&typeof _syncToolCallGroupSummary==='function') _syncToolCallGroupSummary(keep);
  return keep;
}
function isLiveAnchorActivitySceneOwner(streamId){
  const turn=$('liveAssistantTurn');
  if(!turn) return false;
  const owner=turn.getAttribute('data-anchor-scene-live-owner')==='1'||
    !!turn.querySelector('[data-live-anchor-scene-owner="1"],[data-anchor-scene-row="1"]');
  if(!owner) return false;
  const current=turn.getAttribute('data-anchor-stream-id')||'';
  return !streamId||!current||String(streamId)===current;
}
function _projectLiveAnchorActivitySceneForStream(streamId, mode){
  const api=(typeof window!=='undefined')?window.AgyAssistantTurnAnchors:null;
  const map=(typeof window!=='undefined')?window._liveAnchorRegistries:null;
  const registry=map&&streamId?map.get(streamId):null;
  if(!api||!registry||typeof api.projectAssistantTurnAnchorActivityScene!=='function') return null;
  try{
    return api.projectAssistantTurnAnchorActivityScene(registry,{mode:mode||'compact_worklog'});
  }catch(err){
    if(typeof console!=='undefined'&&console.warn) console.warn('assistant turn anchor scene projection failed',err);
    return null;
  }
}
function _prepareLiveAnchorScrollRebuildGuard(scrollSnapshot){
  const messagesEl=$('messages');
  if(!messagesEl||!scrollSnapshot) return {readerAwayFromBottom:false,release:null};
  const beforeBottomDistance=Math.max(0,messagesEl.scrollHeight-messagesEl.scrollTop-messagesEl.clientHeight);
  // Only treat the reader as away if they were ALREADY in a non-follow state.
  // A pinned follower can transiently have bottomDistance>250 mid-render (the
  // assistant body grows before the anchor scene re-renders), so keying on a
  // raw scrollTop>0 here would mis-classify a pinned reader as unpinned and kill
  // auto-follow. Require an explicit unpin/non-pinned signal instead.
  const readerAwayFromBottom=beforeBottomDistance>250&&(_messageUserUnpinned||_scrollPinned===false);
  if(!readerAwayFromBottom) return {readerAwayFromBottom:false,release:null};
  scrollSnapshot.pinned=false;
  scrollSnapshot.userUnpinned=true;
  scrollSnapshot.bottom=beforeBottomDistance;
  _messageUserUnpinned=true;
  _scrollPinned=false;
  _nearBottomCount=0;
  const msgInner=$('msgInner');
  if(!msgInner||!msgInner.style) return {readerAwayFromBottom:true,release:null};
  const guardPreviousKey='liveAnchorScrollGuardPreviousMinHeight';
  let previousMinHeight=msgInner.style.minHeight||'';
  if(msgInner.dataset&&Object.prototype.hasOwnProperty.call(msgInner.dataset,guardPreviousKey)){
    previousMinHeight=msgInner.dataset[guardPreviousKey]||'';
  }else if(msgInner.dataset){
    msgInner.dataset[guardPreviousKey]=previousMinHeight;
  }
  const guardHeight=Math.max(messagesEl.scrollHeight,Number(scrollSnapshot.scrollHeight)||0);
  if(guardHeight>0) msgInner.style.minHeight=`${guardHeight}px`;
  return {
    readerAwayFromBottom:true,
    release:()=>{
      msgInner.style.minHeight=previousMinHeight;
      if(msgInner.dataset&&msgInner.dataset[guardPreviousKey]===previousMinHeight){
        delete msgInner.dataset[guardPreviousKey];
      }
    },
  };
}
function _restoreLiveAnchorScrollSnapshotAfterRebuild(scrollSnapshot, scrollRebuildGuard){
  if(!scrollSnapshot) return;
  const hasHeightGuard=!!(scrollRebuildGuard&&scrollRebuildGuard.release);
  // Pinned renders have no height guard to release, but still need the same
  // queued ownership check: a reader can provide real input before the frame
  // settles, and that input must win over the captured tail position.
  if(!hasHeightGuard&&scrollSnapshot.pinned!==true) return;
  requestAnimationFrame(()=>{
    if(hasHeightGuard) scrollRebuildGuard.release();
    // Only re-restore the unpinned snapshot if the reader is STILL unpinned at
    // rAF time. If they re-pinned between guard-engage and this frame, the
    // stale re-restore would yank them back off the bottom (Opus gate finding).
    if(scrollSnapshot.pinned===true||_messageUserUnpinned){
      _restoreMessageScrollSnapshotSameFrame(scrollSnapshot);
    }
  });
}
function _resetMismatchedLiveAssistantTurnForSession(turn, sessionId){
  const sid=String(sessionId||'');
  if(!turn||!sid||!turn.dataset) return false;
  const existingSid=String(turn.dataset.sessionId||'');
  if(!existingSid||existingSid===sid) return false;
  const blocks=typeof _assistantTurnBlocks==='function' ? _assistantTurnBlocks(turn) : turn;
  if(blocks){
    try{
      blocks.innerHTML='';
    }catch(_){
      while(blocks.firstChild) blocks.removeChild(blocks.firstChild);
    }
  }
  turn.dataset.sessionId=sid;
  return true;
}
function _liveAnchorReasoningRowForFallback(turn, opts){
  opts=opts||{};
  const blocks=typeof _assistantTurnBlocks==='function' ? _assistantTurnBlocks(turn) : turn;
  if(!turn||!blocks||!blocks.querySelectorAll) return null;
  const streamId=String(opts.streamId||S.activeStreamId||'');
  const sessionId=String(opts.sessionId||(S.session&&S.session.session_id)||'');
  const localId=String(opts.anchorReasoningLocalId||opts.localId||'').trim();
  if(!localId) return null;
  const rows=blocks.querySelectorAll(
    '[data-anchor-scene-row="1"][data-anchor-local-id]'
  );
  for(const row of Array.from(rows)){
    const anchorLocalId=String(row.getAttribute&&row.getAttribute('data-anchor-local-id')||'');
    if(anchorLocalId!==localId) continue;
    const rowRole=String(row.getAttribute&&row.getAttribute('data-anchor-row-role')||'');
    const rowSource=String(row.getAttribute&&row.getAttribute('data-anchor-source-event-type')||'');
    if(rowRole!=='thinking'&&rowSource!=='reasoning') continue;
    const rowStreamId=String(row.getAttribute&&row.getAttribute('data-anchor-stream-id')||'');
    if(rowStreamId&&streamId&&rowStreamId!==streamId) continue;
    const rowSessionId=String(row.getAttribute&&row.getAttribute('data-session-id')||'');
    if(rowSessionId&&sessionId&&rowSessionId!==sessionId) continue;
    return row;
  }
  return null;
}
function _updateLiveAnchorReasoningRowForFallback(turn, text, opts){
  const clean=_sanitizeThinkingDisplayText(text);
  if(!clean||window._showThinking===false) return false;
  const row=_liveAnchorReasoningRowForFallback(turn, opts);
  if(!row) return false;
  if(row.classList&&row.classList.contains('wl-reason')){
    if(typeof _renderWorklogReasonInto==='function') _renderWorklogReasonInto(row, clean);
    else row.textContent=clean;
    const group=row.closest&&row.closest('.tool-worklog-group,.tool-call-group,.live-worklog');
    if(group&&typeof _syncToolCallGroupSummary==='function') _syncToolCallGroupSummary(group);
  }else if(row.classList&&row.classList.contains('transparent-event-row')){
    _renderThinkingInto(row, clean);
    const eventAt=row.getAttribute&&row.getAttribute('data-event-at');
    const nextTs=typeof _firstValidTimestampSeconds==='function'
      ? _firstValidTimestampSeconds(opts&&opts.ts, opts&&opts.timestamp, opts&&opts.created_at, eventAt)
      : null;
    if(typeof _decorateTransparentEventRow==='function'){
      _decorateTransparentEventRow(row,{
        type:'thinking',
        text:clean,
        preview:clean,
        ts:nextTs||undefined,
        live:true,
        segmentSeq:opts&&opts.segmentSeq,
        burstId:opts&&opts.burstId,
      });
    }
  }else{
    _renderThinkingInto(row, clean);
  }
  if(turn&&typeof _syncTransparentEventControls==='function') _syncTransparentEventControls(turn);
  if(typeof scrollIfPinned==='function') scrollIfPinned();
  return true;
}
function renderLiveAnchorActivityScene(streamId, scene, opts){
  opts=opts||{};
  const requestedMode=opts.mode;
  const activeMode=chatActivityMode();
  // The USER's active activity-display mode is authoritative for what gets
  // painted. `requestedMode` (opts.mode) is only a fallback hint from callers
  // that hardcode {mode:'compact_worklog'} (appendLiveToolCard / ensureLiveWorklogShell
  // / appendLiveCompressionCard, etc.) — it must NEVER override the active mode, or a
  // transparent_stream turn gets a compact grouped-worklog frame forced onto it. That
  // regressed #5942 (grouped↔individual alternating) + #5943 (per-tick row rebuild /
  // flicker) when #5746's requestedMode-precedence landed: the good build always
  // checked isTransparentStream() FIRST and ignored the hint. Restore active-mode-wins:
  // honor requestedMode ONLY when there is no usable active mode.
  const knownMode=(m)=>m==='compact_worklog'||m==='transparent_stream'||m==='hide_all_activity';
  const sceneMode=knownMode(activeMode)?activeMode:(knownMode(requestedMode)?requestedMode:activeMode);
  if(sceneMode==='hide_all_activity') return false;
  const existingTurn=$('liveAssistantTurn');
  const requestedSessionId=String(opts.sessionId||'');
  const existingTurnSessionId=String(existingTurn&&existingTurn.dataset&&existingTurn.dataset.sessionId||'');
  if(existingTurn&&requestedSessionId&&existingTurnSessionId&&existingTurnSessionId!==requestedSessionId){
    if(!_resetMismatchedLiveAssistantTurnForSession(existingTurn, requestedSessionId)) return false;
  }
  if(sceneMode==='transparent_stream'){
    return _renderLiveAnchorActivitySceneTransparent(streamId,scene,opts);
  }
  if(typeof isSimplifiedToolCalling==='function'&&!isSimplifiedToolCalling()) return false;
  if(sceneMode!=='compact_worklog') return false;
  if(!S.session||!S.activeStreamId) return false;
  if(opts.sessionId&&S.session.session_id!==opts.sessionId) return false;
  if(streamId&&S.activeStreamId!==streamId) return false;
  const rows=_anchorSceneRowsForRendering(scene,{settled:false});
  $('emptyState').style.display='none';
  let turn=$('liveAssistantTurn');
  if(!turn){
    turn=_createAssistantTurn();
    turn.id='liveAssistantTurn';
    $('msgInner').appendChild(turn);
  }
  turn.setAttribute('data-anchor-scene-live-owner','1');
  turn.setAttribute('data-anchor-stream-id',String(streamId||''));
  // Re-stamp when reusing a turn restored or previously rendered in another mode.
  if(S.session) turn.dataset.sessionId=S.session.session_id;
  const blocks=_assistantTurnBlocks(turn);
  if(!blocks) return false;
  const liveDisclosureState=typeof _captureWorklogDetailDisclosureState==='function'
    ? _captureWorklogDetailDisclosureState(blocks)
    : null;
  const scrollSnapshot=_captureMessageScrollSnapshot();
  const scrollRebuildGuard=_prepareLiveAnchorScrollRebuildGuard(scrollSnapshot);
  blocks.querySelectorAll('[data-anchor-scene-owner="1"],[data-anchor-scene-row="1"]').forEach(el=>el.remove());
  blocks.querySelectorAll('.live-worklog[data-live-worklog-shell="1"],.tool-worklog-group[data-live-tool-call-group="1"],.tool-call-group[data-live-tool-call-group="1"],.tool-card-row[data-live-tid]:not(.transparent-event-row),.agent-activity-thinking[data-live-thinking="1"],.interim-collapse-toggle').forEach(el=>el.remove());
  blocks.querySelectorAll('[data-live-assistant="1"]').forEach(el=>{
    el.classList.add('assistant-segment-worklog-source');
    el.setAttribute('aria-hidden','true');
    el.hidden=true;
  });
  const group=_anchorSceneWorklogGroup(blocks,{
    live:true,
    collapsed:false,
    activityKey:`live:${streamId||S.activeStreamId||'anchor'}`,
    streamId:streamId||S.activeStreamId||'',
    turnStartedAt:S.session&&S.session.pending_started_at,
  });
  const ok=_renderAnchorSceneRowsIntoWorklog(group,rows,{live:true,settled:false});
  if(!ok){
    const list=_toolWorklogListEl(group);
    if(list) list.innerHTML='';
    _syncToolCallGroupSummary(group);
  }
  if(typeof _restoreWorklogDetailDisclosureState==='function') _restoreWorklogDetailDisclosureState(blocks, liveDisclosureState);
  if(typeof _startActivityElapsedTimer==='function') _startActivityElapsedTimer(group);
  _dedupeLiveProcessedWorklogAnchors(turn);
  if(typeof _moveLiveRunStatusToTurnEnd==='function') _moveLiveRunStatusToTurnEnd();
  _restoreMessageScrollSnapshotSameFrame(scrollSnapshot);
  _restoreLiveAnchorScrollSnapshotAfterRebuild(scrollSnapshot,scrollRebuildGuard);
  if(!scrollRebuildGuard.readerAwayFromBottom&&typeof scrollIfPinned==='function') scrollIfPinned();
  return true;
}
function _renderLiveAnchorActivitySceneTransparent(streamId, scene, opts){
  opts=opts||{};
  if(!S.session||!S.activeStreamId) return false;
  if(opts.sessionId&&S.session.session_id!==opts.sessionId) return false;
  if(streamId&&S.activeStreamId!==streamId) return false;
  const rows=_anchorSceneRowsForRendering(scene,{settled:false});
  if(!rows.length) return false;
  $('emptyState').style.display='none';
  let turn=$('liveAssistantTurn');
  if(!turn){
    turn=_createAssistantTurn();
    turn.id='liveAssistantTurn';
    $('msgInner').appendChild(turn);
  }
  turn.setAttribute('data-anchor-scene-live-owner','1');
  turn.setAttribute('data-anchor-stream-id',String(streamId||''));
  turn.setAttribute('data-live-assistant-turn','1');
  if(S.session) turn.dataset.sessionId=S.session.session_id;
  const blocks=_assistantTurnBlocks(turn);
  if(!blocks) return false;
  const scrollSnapshot=_captureMessageScrollSnapshot();
  const scrollRebuildGuard=_prepareLiveAnchorScrollRebuildGuard(scrollSnapshot);
  const activeStreamId = String(streamId || S.activeStreamId || '');
  const activeSessionId = String(S.session && S.session.session_id || '');
  const preserveByKey = new Map();
  blocks.querySelectorAll('.transparent-event-row[data-live-stream-owned="1"][data-anchor-row-id]').forEach(node=>{
    if(!node||!node.getAttribute) return;
    const rowStream = String(node.getAttribute('data-anchor-stream-id') || '');
    if(rowStream && rowStream !== activeStreamId) return;
    const rowSession = String(node.getAttribute('data-session-id') || '');
    if(rowSession && activeSessionId && rowSession !== activeSessionId) return;
    const key = _transparentLiveRowKey(node, activeStreamId);
    if(key && !preserveByKey.has(key)) preserveByKey.set(key, node);
  });
  blocks.querySelectorAll('[data-anchor-scene-owner="1"]').forEach(el=>el.remove());
  blocks.querySelectorAll('[data-anchor-scene-row="1"]').forEach(el=>{
    if(el.getAttribute('data-live-stream-owned') === '1'){
      const key = _transparentLiveRowKey(el, activeStreamId);
      if(key && preserveByKey.get(key) === el) return;
    }
    el.remove();
  });
  // Clear every legacy live activity surface this renderer can replace. The
  // anchor-scene rows are now the source of truth for visible live activity.
  blocks.querySelectorAll(
    '.live-worklog[data-live-worklog-shell="1"],'+
    '.tool-worklog-group[data-live-tool-call-group="1"],'+
    '.tool-call-group[data-live-tool-call-group="1"],'+
    '.tool-card-row[data-live-tid],'+
    '.agent-activity-thinking[data-live-thinking="1"],'+
    '.transparent-event-row[data-live-tid],'+
    '.interim-collapse-toggle'
  ).forEach(el=>el.remove());
  // Match the compact path: keep legacy live segments as hidden anchors so
  // stream-owned metadata survives while the anchor scene owns visible activity.
  blocks.querySelectorAll('[data-live-assistant="1"]').forEach(el=>{
    el.classList.add('assistant-segment-worklog-source');
    el.setAttribute('aria-hidden','true');
    el.hidden=true;
  });
  const liveFooter=blocks.querySelector('#liveRunStatus');
  const renderedRows=[];
  for(const row of rows){
    const rowEventTs=typeof _anchorSceneRowTimestampSeconds==='function'?_anchorSceneRowTimestampSeconds(row):null;
    const node=_anchorSceneTransparentNodeForRow(row,{
      live:true,
      settled:false,
      streamId:streamId||S.activeStreamId||'',
      sessionId:S.session&&S.session.session_id,
    });
    if(!node) continue;
    const key = _transparentLiveRowKey(node, activeStreamId);
    const existing = key ? preserveByKey.get(key) : null;
    const renderedNode = existing && _transparentLiveRowsCompatible(existing, node)
      ? _refreshTransparentLiveRow(existing, node, {
        preserveEventAt:!rowEventTs&&existing.getAttribute?existing.getAttribute('data-event-at'):null,
      })
      : node;
    if(existing) preserveByKey.delete(key);
    if(!renderedNode) continue;
    renderedRows.push(renderedNode);
  }
  const transparentLiveRowAlreadyPositioned=(node, expectedNextSibling)=>!!(
    node &&
    node.parentElement===blocks &&
    node.nextSibling===expectedNextSibling
  );
  let expectedNextSibling=(liveFooter&&liveFooter.parentElement===blocks) ? liveFooter : null;
  for(let i=renderedRows.length-1;i>=0;i--){
    const renderedNode=renderedRows[i];
    if(transparentLiveRowAlreadyPositioned(renderedNode,expectedNextSibling)){
      expectedNextSibling=renderedNode;
      continue;
    }
    if(expectedNextSibling&&expectedNextSibling.parentElement===blocks) blocks.insertBefore(renderedNode,expectedNextSibling);
    else blocks.appendChild(renderedNode);
    expectedNextSibling=renderedNode;
  }
  preserveByKey.forEach(stale=>stale.remove());
  if(renderedRows.length) _syncTransparentEventControls(turn);
  if(typeof _moveLiveRunStatusToTurnEnd==='function') _moveLiveRunStatusToTurnEnd();
  _restoreMessageScrollSnapshotSameFrame(scrollSnapshot);
  _restoreLiveAnchorScrollSnapshotAfterRebuild(scrollSnapshot,scrollRebuildGuard);
  if(!scrollRebuildGuard.readerAwayFromBottom&&typeof scrollIfPinned==='function') scrollIfPinned();
  return !!renderedRows.length;
}

function _transparentLiveRowKey(node, streamId){
  if(!node || !node.getAttribute) return '';
  const rowId = String(node.getAttribute('data-anchor-row-id') || '').trim();
  if(!rowId) return '';
  const rowStreamId = String(streamId || node.getAttribute('data-anchor-stream-id') || '').trim();
  const rowRole = String(node.getAttribute('data-anchor-row-role') || 'activity').trim();
  const rowSource = String(node.getAttribute('data-anchor-source-event-type') || '').trim();
  return `${rowStreamId}\u0000${rowId}\u0000${rowRole}\u0000${rowSource}`;
}

function _transparentLiveRowsCompatible(existing, candidate){
  if(!existing || !candidate) return false;
  return !!(
    existing.getAttribute('data-anchor-row-id') === candidate.getAttribute('data-anchor-row-id') &&
    existing.getAttribute('data-anchor-row-role') === candidate.getAttribute('data-anchor-row-role') &&
    existing.getAttribute('data-anchor-source-event-type') === candidate.getAttribute('data-anchor-source-event-type')
  );
}

function _transparentLiveRowAttributePairs(node){
  if(!node) return [];
  if(typeof node.getAttributeNames === 'function'){
    return node.getAttributeNames().map(name=>[name, node.getAttribute(name)]);
  }
  const attrs = node.attributes;
  if(!attrs || typeof attrs !== 'object') return [];
  if(typeof attrs.length === 'number'){
    const pairs = [];
    for(let i=0;i<attrs.length;i++){
      const attr = typeof attrs.item === 'function' ? attrs.item(i) : attrs[i];
      if(!attr || !attr.name) continue;
      pairs.push([attr.name, attr.value]);
    }
    return pairs;
  }
  return Object.keys(attrs).map(name=>[name, attrs[name]]);
}

function _transparentLiveRowInteractiveState(row){
  const card = row&&row.querySelector ? row.querySelector('.tool-card,.thinking-card') : null;
  const detail = row&&row.querySelector ? row.querySelector('.tool-card-detail') : null;
  return {
    expanded: !!((card&&card.classList&&card.classList.contains('open')) || (row&&row.getAttribute&&row.getAttribute('data-expanded')==='1')),
    detailMode: detail&&detail.getAttribute ? String(detail.getAttribute('data-transparent-detail-mode') || '') : '',
  };
}

function _rehydrateTransparentLiveRow(existing, node, preservedState){
  if(!existing) return;
  if(node && Object.prototype.hasOwnProperty.call(node, '_tcData')) existing._tcData = node._tcData;
  else if(Object.prototype.hasOwnProperty.call(existing, '_tcData')) delete existing._tcData;
  try{ delete node._tcData; }catch(_){}
  const header = existing.querySelector ? existing.querySelector('.tool-card-header,.thinking-card-header') : null;
  if(header){
    if(typeof _wireTransparentHeaderToggle === 'function') _wireTransparentHeaderToggle(header);
    if(typeof _attachCopyButton === 'function') _attachCopyButton(header);
  }
  const card = existing.querySelector ? existing.querySelector('.tool-card,.thinking-card') : null;
  if(card){
    if(typeof _setTransparentCardOpen === 'function') _setTransparentCardOpen(card, !!(preservedState&&preservedState.expanded));
    else if(card.classList&&typeof card.classList.toggle === 'function') card.classList.toggle('open', !!(preservedState&&preservedState.expanded));
  }
  const detail = existing.querySelector ? existing.querySelector('.tool-card-detail') : null;
  if(detail && preservedState && preservedState.detailMode){
    detail.setAttribute('data-transparent-detail-mode', preservedState.detailMode);
    detail.querySelectorAll('.transparent-detail-mode').forEach(el=>{
      const mode = String(el.getAttribute('data-mode') || '');
      if(el.classList && typeof el.classList.toggle === 'function') el.classList.toggle('active', mode===preservedState.detailMode);
    });
  }
}

function _refreshTransparentThinkingLiveRow(existing, node){
  if(!existing || !node || !existing.querySelector || !node.querySelector) return false;
  const existingType = String(existing.getAttribute('data-event-type') || '');
  const nodeType = String(node.getAttribute('data-event-type') || '');
  const existingIsThinking = existingType === 'thinking' || (existing.classList&&existing.classList.contains('transparent-thinking-event'));
  const nodeIsThinking = nodeType === 'thinking' || (node.classList&&node.classList.contains('transparent-thinking-event'));
  if(!existingIsThinking || !nodeIsThinking) return false;
  const existingPre = existing.querySelector('.thinking-card-body pre');
  const nodePre = node.querySelector('.thinking-card-body pre');
  if(!existingPre || !nodePre) return false;
  const nextText = String(nodePre.textContent || '');
  if(existingPre.textContent !== nextText) existingPre.textContent = nextText;
  const nodePreview = node.querySelector('.transparent-event-thinking-preview');
  const previewText = nodePreview ? String(nodePreview.textContent || '') : nextText;
  if(typeof _decorateTransparentEventRow === 'function'){
    const nodeStampSource = node.getAttribute ? String(node.getAttribute('data-event-at-source') || '') : '';
    const existingStamp = existing.getAttribute ? existing.getAttribute('data-event-at') : null;
    const nodeStamp = node.getAttribute ? node.getAttribute('data-event-at') : null;
    const nextStamp = nodeStampSource === 'event'
      ? (nodeStamp || existingStamp)
      : (existingStamp || nodeStamp);
    _decorateTransparentEventRow(existing,{
      type:'thinking',
      text:nextText,
      preview:previewText,
      ts:nextStamp||undefined,
      live:true,
    });
  }
  return true;
}

function _bindTransparentFadeCleanup(body){
  if(!body || body._transparentFadeCleanupBound || typeof body.addEventListener !== 'function') return;
  body._transparentFadeCleanupBound = true;
  body.addEventListener('animationend', e=>{
    const span = e.target;
    if(!span || !span.classList || !span.classList.contains('stream-fade-word')) return;
    // Keep the animated inline node stable for the lifetime of the live turn,
    // exactly like _streamFadeBindCleanup() in messages.js. Replacing each word
    // with a fresh text node makes native scroll anchoring choose a new anchor
    // while the transparent prose row is still growing, producing a visible
    // vertical bounce. Final settlement rebuilds plain persisted DOM.
    span.classList.remove('is-new');
    if(span.style) span.style.removeProperty('--stream-fade-ms');
  });
}

function _appendTransparentFadeText(body, text){
  if(!body) return;
  const value = String(text || '');
  if(!value) return;
  _bindTransparentFadeCleanup(body);
  const reduceMotion = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  const frag = document.createDocumentFragment();
  const wordRe = /(\S+)(\s*)/g;
  let last = 0, match, changed = false;
  while((match = wordRe.exec(value))){
    if(match.index > last) frag.appendChild(document.createTextNode(value.slice(last, match.index)));
    if(reduceMotion){
      frag.appendChild(document.createTextNode(match[1]));
    }else{
      const span = document.createElement('span');
      span.className = 'stream-fade-word is-new';
      span.textContent = match[1];
      frag.appendChild(span);
    }
    if(match[2]) frag.appendChild(document.createTextNode(match[2]));
    last = match.index + match[0].length;
    changed = true;
  }
  if(!changed) frag.appendChild(document.createTextNode(value));
  else if(last < value.length) frag.appendChild(document.createTextNode(value.slice(last)));
  body.appendChild(frag);
}

function _refreshTransparentFadeProseRow(existing, node, preservedState){
  let body = existing.querySelector ? existing.querySelector('.msg-body') : null;
  const nextText = String((node.dataset && node.dataset.rawText) || (node.textContent || ''));
  const currentText = String(existing.getAttribute('data-stream-fade-text') || (body && body.textContent) || '');
  const pairs = _transparentLiveRowAttributePairs(node);
  const kept = Object.create(null);
  for(const pair of pairs){
    const [name, value] = pair;
    kept[String(name)] = String(value ?? '');
  }
  for(const [name] of _transparentLiveRowAttributePairs(existing)){
    if(!Object.prototype.hasOwnProperty.call(kept, name)) existing.removeAttribute(name);
  }
  for(const pair of pairs){
    const [name, value] = pair;
    existing.setAttribute(name, value);
  }
  existing.className = node.className || '';
  if(!body){
    body = document.createElement('div');
    body.className = 'msg-body';
    existing.appendChild(body);
  }
  if(body.classList) body.classList.add('stream-fade-active');
  if(!nextText.startsWith(currentText)){
    body.textContent = '';
    existing.setAttribute('data-stream-fade-text', '');
    _appendTransparentFadeText(body, nextText);
  }else{
    _appendTransparentFadeText(body, nextText.slice(currentText.length));
  }
  existing.setAttribute('data-stream-fade-text', nextText);
  _rehydrateTransparentLiveRow(existing, node, preservedState);
  return existing;
}

function _refreshTransparentLiveRow(existing, node, opts){
  opts=opts||{};
  if(!existing || !node || !existing.getAttribute) return node;
  if(existing===node) return existing;
  const preservedState = _transparentLiveRowInteractiveState(existing);
  const candidateIsFadeProse = node.getAttribute('data-anchor-row-role') === 'prose' &&
    node.querySelector &&
    !!node.querySelector('.msg-body.stream-fade-active,.stream-fade-word');
  if(candidateIsFadeProse){
    return _refreshTransparentFadeProseRow(existing, node, preservedState);
  }
  const pairs = _transparentLiveRowAttributePairs(node);
  const kept = Object.create(null);
  for(const pair of pairs){
    const [name, value] = pair;
    kept[String(name)] = String(value ?? '');
  }
  for(const [name] of _transparentLiveRowAttributePairs(existing)){
    if(!Object.prototype.hasOwnProperty.call(kept, name)) existing.removeAttribute(name);
  }
  for(const pair of pairs){
    const [name, value] = pair;
    existing.setAttribute(name, value);
  }
  existing.className = node.className || '';
  if(_refreshTransparentThinkingLiveRow(existing, node)){
    _rehydrateTransparentLiveRow(existing, node, preservedState);
    return existing;
  }
  const newHtml = node.innerHTML || '';
  const htmlChanged = existing.innerHTML !== newHtml;
  if(htmlChanged) existing.innerHTML = newHtml;
  _rehydrateTransparentLiveRow(existing, node, preservedState);
  if(opts.preserveEventAt){
    const header = existing.querySelector ? existing.querySelector('.tool-card-header,.thinking-card-header') : null;
    if(header) _syncTransparentEventTimestamp(existing, header, {ts:opts.preserveEventAt, live:false});
  }
  return existing;
}
function _renderLiveAnchorActivitySceneForStream(streamId, sessionId, opts){
  const requestedMode=opts&&opts.mode;
  const activeMode=chatActivityMode();
  const mode=activeMode==='hide_all_activity'
    ? 'hide_all_activity'
    : (requestedMode==='compact_worklog'||requestedMode==='transparent_stream'||requestedMode==='hide_all_activity'
    ? requestedMode
    : activeMode);
  const scene=_projectLiveAnchorActivitySceneForStream(streamId,mode);
  if(!scene) return false;
  return renderLiveAnchorActivityScene(streamId,scene,{...(opts||{}),sessionId});
}
function _renderLiveAnchorActivitySceneSnapshotForStream(streamId, scene, sessionId, opts){
  if(!scene||scene.version!=='activity_scene_v1') return false;
  return renderLiveAnchorActivityScene(streamId,scene,{...(opts||{}),sessionId});
}
if(typeof window!=='undefined'){
  // Direct assignment, NOT a same-name wrapper: these are top-level `function`
  // declarations, which in a classic script are already window properties.
  // Re-exporting via `window.X = function(){ return X() }` reassigns that same
  // global property to the wrapper, so the inner call resolves to the wrapper
  // itself → infinite recursion (RangeError: Maximum call stack size exceeded)
  // on every live render / reattach / snapshot-restore path (#2715/#2771 class).
  window._renderLiveAnchorActivitySceneForStream=_renderLiveAnchorActivitySceneForStream;
  window._renderLiveAnchorActivitySceneSnapshotForStream=_renderLiveAnchorActivitySceneSnapshotForStream;
  window._projectLiveAnchorActivitySceneForStream=_projectLiveAnchorActivitySceneForStream;
  window.isLiveAnchorActivitySceneOwner=isLiveAnchorActivitySceneOwner;
}
function _anchorSceneSceneHasWorklogWorthyRows(scene){
  // Mirror of messages.js _anchorSceneHasWorklogWorthyRows for the RENDER side:
  // a settled scene that was persisted (or hydrated from the backend) before the
  // generation-side guard existed can still be all-prose. Such a scene must NOT be
  // promoted to a collapsed worklog at render time (it would hide the whole answer
  // and shrink the transcript at settle → bottom-pinned jump-back). Require at least
  // one tool/thinking/compression row. (defense-in-depth for already-persisted scenes)
  const rows=Array.isArray(scene&&scene.activity_rows)?scene.activity_rows:[];
  for(const row of rows){
    if(!row||typeof row!=='object') continue;
    const role=String(row.role||'');
    if(role==='tool'||role==='thinking') return true;
    if(role==='lifecycle'){
      const source=String(row.source_event_type||'');
      if(source==='compressing'||source==='compressed') return true;
    }
  }
  return false;
}
// #5941: an errored/failed turn's terminal_state. A turn that ended in a
// provider/agent failure but which DID produce assistant content (tool calls,
// reasoning) still folds that content into a collapsed worklog above the error
// card — so the user reads a lone error bubble as "nothing came back", even
// though the real response is one click away. These are the terminal states
// that must keep the produced content VISIBLE by default. `completed` (normal
// turn) and null are deliberately excluded, and `cancelled`/`interrupted`
// (user-initiated stops with their own dedicated cards + #5224 transcript
// preservation) are left to their existing behavior — this is scoped to the
// error/failure family the report is about.
const _ANCHOR_SCENE_ERRORED_TERMINAL_STATES=new Set([
  'error','no_response','degraded','connection_lost','tool_limit_reached','compression_exhausted',
]);
function _anchorSceneHasErroredTerminalState(scene){
  const state=String(scene&&scene.terminal_state||'').trim().toLowerCase();
  return _ANCHOR_SCENE_ERRORED_TERMINAL_STATES.has(state);
}
function _renderSettledAnchorSceneTransparentForMessage(message, segment, rawIdx){
  if(!message||!message._anchor_activity_scene||!segment) return false;
  if(!_anchorSceneSceneHasWorklogWorthyRows(message._anchor_activity_scene)) return false;
  const blocks=_assistantTurnBlocks(segment.closest('.assistant-turn'));
  if(!blocks) return false;
  const scene=message._anchor_activity_scene;
  const rows=_anchorSceneRowsForRendering(scene,{settled:true});
  if(!rows.length) return false;
  const lastNonTerminalWorkRowIndex=_anchorSceneLastNonTerminalWorkRowIndex(rows);
  // The assistant segment owns the final answer; pass it so intermediate prose
  // rows render but the final-answer-duplicate prose row is suppressed.
  const finalAnswer=String(
    (scene&&typeof scene.final_answer==='string'&&scene.final_answer)
    || _assistantAnchorSceneFinalAnswerText(message)
    || (typeof msgContent==='function'?msgContent(message):'')
    || ''
  );
  blocks.querySelectorAll('[data-anchor-settled-scene-row="1"],.transparent-event-row[data-anchor-scene-row="1"]').forEach(el=>el.remove());
  blocks.querySelectorAll('.transparent-earlier-steps[data-anchor-earlier-steps="1"]').forEach(el=>el.remove());
  blocks.querySelectorAll('.assistant-segment[data-msg-idx]').forEach(node=>{
    const idx=Number(node.getAttribute('data-msg-idx'));
    if(Number.isFinite(idx)&&idx<rawIdx){
      node.classList.add('assistant-segment-worklog-source');
      node.setAttribute('aria-hidden','true');
      node.hidden=true;
    }
  });
  // #5966: per-turn row cap. A reasoning-heavy settled turn can carry hundreds of
  // activity rows; rendering them all inline is the node-count half of the
  // Transparent-Stream memory blowup (detail-deferral above handles per-row
  // weight). Render only the last _TRANSPARENT_SETTLED_ROW_CAP rows and, when the
  // turn exceeds cap + slack, prepend a single "Show earlier steps (N)" affordance
  // that materializes the omitted prefix in place on click. Two exemptions keep
  // behavior identical where a cap would be wrong or unhelpful:
  //   • the JUST-SETTLED turn (its stream id matches the keep-open token) renders
  //     in full — capping it at STREAM_DONE would shrink the transcript and cause
  //     the backward-jump the keep-open token exists to prevent;
  //   • a turn already revealed this session (data flag) stays fully rendered
  //     across ordinary rebuilds / virtualize-out+in cycles.
  const turnEl=segment.closest('.assistant-turn');
  const streamId=String(message._anchor_stream_id||scene.stream_id||(scene.identity&&scene.identity.stream_id)||'');
  const justSettled=_shouldKeepSettledWorklogOpenForStreamSettle(streamId);
  // #5966 (Codex F3): revealed-state is authoritative from the persistent set
  // (survives cache round-trip / rebuild / switch-away), with the DOM flag as a
  // same-render fast path.
  const revealKey=_transparentRevealKey(S.session&&S.session.session_id, rawIdx);
  const alreadyRevealed=_transparentRevealedTurns.has(revealKey)
    || !!(turnEl&&turnEl.getAttribute('data-transparent-earlier-revealed')==='1');
  const cap=_TRANSPARENT_SETTLED_ROW_CAP;
  const slack=_TRANSPARENT_SETTLED_ROW_CAP_SLACK;
  let startIdx=0;
  if(!justSettled&&!alreadyRevealed&&rows.length>cap+slack){
    startIdx=rows.length-cap;
  }
  // Stash the TRUE tool-row count so "Trace: N tools" reflects the whole run even
  // while the prefix is capped; cleared on full reveal. (uncapped → remove it.)
  if(turnEl){
    if(startIdx>0){
      const totalTools=rows.filter(r=>String(r.role||'')==='tool').length;
      turnEl.setAttribute('data-transparent-total-tool-count',String(totalTools));
    }else{
      turnEl.removeAttribute('data-transparent-total-tool-count');
    }
  }
  const renderRowAt=(idx)=>{
    const row=rows[idx];
    const node=_anchorSceneTransparentNodeForRow(row,{settled:true,finalAnswer,liveTokenFinalPrefixEligible:idx>lastNonTerminalWorkRowIndex});
    if(!node) return null;
    // #5966 (Codex F2): stamp the OWNER message index so cache-round-trip recovery
    // resolves the correct scene in a multi-segment turn (the scene owner is often
    // NOT the turn's first assistant segment).
    node.setAttribute('data-anchor-owner-idx',String(rawIdx));
    if(segment.parentElement===blocks) blocks.insertBefore(node,segment);
    else blocks.appendChild(node);
    return node;
  };
  let wrote=false;
  // The "Show earlier steps" affordance sits ABOVE the retained rows (chronology:
  // the hidden steps came first). Insert it before rendering the retained tail so
  // it lands at the top of this turn's activity run.
  if(startIdx>0){
    const earlier=_buildTransparentEarlierStepsAffordance(startIdx);
    earlier.setAttribute('data-anchor-owner-idx',String(rawIdx));
    if(segment.parentElement===blocks) blocks.insertBefore(earlier,segment);
    else blocks.appendChild(earlier);
    // Reveal handler: materialize the omitted prefix in place, holding the reader's
    // viewport on the clicked affordance (insert grows content ABOVE it).
    earlier.addEventListener('click',()=>{
      _revealTransparentEarlierSteps(message,segment,rawIdx,earlier);
    });
    wrote=true;
  }
  for(let idx=startIdx;idx<rows.length;idx+=1){
    if(renderRowAt(idx)) wrote=true;
  }
  if(wrote){
    const turn=segment.closest('.assistant-turn');
    if(turn) _syncTransparentEventControls(turn);
  }
  return wrote;
}
// #5966 tunables. Cap chosen so a normal multi-tool turn (a handful to a couple
// dozen rows) is NEVER capped — only genuinely long reasoning runs are. Slack
// prevents a "Show 3 earlier steps" stub: only cap when the omitted prefix is
// worth its own row.
const _TRANSPARENT_SETTLED_ROW_CAP=30;
const _TRANSPARENT_SETTLED_ROW_CAP_SLACK=10;
// t() returns the key name itself for an unknown key, so `t(k)||literal` doesn't
// fall back. This resolves via t() only when the key is genuinely defined,
// otherwise uses the English literal — keeping the label correct before the
// locale keys are present in every bundle. (Fable UX i18n fast-follow.)
function _tOrDefault(key, literal, ...args){
  try{
    if(typeof t==='function'){
      const v=t(key, ...args);
      if(v && v!==key) return v;
    }
  }catch(_){ }
  return literal;
}
// A clean, in-flow affordance styled on the existing "Load earlier messages"
// pill (same visual language, so it reads as native). Shows the exact hidden
// count; the pill is the click target with a leading up-chevron.
function _buildTransparentEarlierStepsAffordance(hiddenCount){
  const el=document.createElement('div');
  el.className='transparent-earlier-steps';
  el.setAttribute('data-anchor-earlier-steps','1');
  el.setAttribute('data-anchor-scene-row','1');
  el.setAttribute('data-anchor-settled-scene-row','1');
  el.setAttribute('role','button');
  el.setAttribute('tabindex','0');
  el.setAttribute('data-earlier-count',String(hiddenCount));
  // i18n with English fallback, matching the sibling "Expand all"/"Collapse all"
  // controls' t() pattern. Keys live in the en locale (i18n.js); t() falls back to
  // en for other locales and to the key name if absent — so guard with a literal.
  const label=hiddenCount===1
    ? _tOrDefault('show_earlier_step_one','Show 1 earlier step')
    : _tOrDefault('show_earlier_steps','Show '+hiddenCount+' earlier steps',hiddenCount);
  el.setAttribute('aria-label',label);
  el.innerHTML=`<span class="transparent-earlier-steps-chevron">${li('chevron-up',13)}</span><span class="transparent-earlier-steps-label">${esc(label)}</span>`;
  el.addEventListener('keydown',(ev)=>{
    if(ev.key==='Enter'||ev.key===' '){ ev.preventDefault(); el.click(); }
  });
  return el;
}
// Materialize the omitted prefix rows for a capped settled transparent turn,
// preserving the reader's viewport position (rows are inserted ABOVE the clicked
// affordance, so without compensation the content below would jump down).
function _revealTransparentEarlierSteps(message, segment, rawIdx, affordanceEl){
  const turnEl=segment.closest('.assistant-turn');
  // #5966 (Codex F3): record the reveal in the PERSISTENT set (survives rebuild /
  // switch-away / cache round-trip) and invalidate this session's cached HTML so
  // the stored markup isn't re-served stale-capped.
  const revealKey=_transparentRevealKey(S.session&&S.session.session_id, rawIdx);
  _transparentRevealedTurns.add(revealKey);
  try{
    const sid=S.session&&S.session.session_id;
    if(sid&&_sessionHtmlCache&&typeof _sessionHtmlCache.delete==='function') _sessionHtmlCache.delete(sid);
  }catch(_){ }
  if(turnEl){
    turnEl.setAttribute('data-transparent-earlier-revealed','1');
    // Full run now mounted → drop the capped-count stash so the Trace label
    // recomputes from the (now complete) DOM.
    turnEl.removeAttribute('data-transparent-total-tool-count');
  }
  const msgsEl=$('messages');
  const prevScrollTop=msgsEl?msgsEl.scrollTop:0;
  const prevScrollHeight=msgsEl?msgsEl.scrollHeight:0;
  const scene=message&&message._anchor_activity_scene;
  const blocks=_assistantTurnBlocks(turnEl);
  if(!scene||!blocks){ if(affordanceEl) affordanceEl.remove(); return; }
  const rows=_anchorSceneRowsForRendering(scene,{settled:true})||[];
  const lastNonTerminalWorkRowIndex=_anchorSceneLastNonTerminalWorkRowIndex(rows);
  const finalAnswer=String(
    (scene&&typeof scene.final_answer==='string'&&scene.final_answer)
    || _assistantAnchorSceneFinalAnswerText(message)
    || (typeof msgContent==='function'?msgContent(message):'')
    || ''
  );
  // The affordance's data-count tells us how many prefix rows to build (the rows
  // rendered on the initial pass are the tail after that index).
  const hidden=Number(affordanceEl&&affordanceEl.getAttribute('data-earlier-count'))||0;
  const stopIdx=hidden>0?hidden:_computeTransparentHiddenPrefixCount(rows);
  const frag=document.createDocumentFragment();
  for(let idx=0;idx<stopIdx;idx+=1){
    const node=_anchorSceneTransparentNodeForRow(rows[idx],{settled:true,finalAnswer,liveTokenFinalPrefixEligible:idx>lastNonTerminalWorkRowIndex});
    if(node){ node.setAttribute('data-earlier-revealed','1'); frag.appendChild(node); }
  }
  // Insert the prefix where the affordance sits, then drop the affordance.
  if(affordanceEl&&affordanceEl.parentElement===blocks){
    blocks.insertBefore(frag,affordanceEl);
    affordanceEl.remove();
  }else{
    blocks.appendChild(frag);
  }
  if(turnEl) _syncTransparentEventControls(turnEl);
  // Hold the reader's position: rows landed above the old affordance point, so
  // add the height delta to scrollTop (the app's own load-earlier idiom).
  if(msgsEl){
    const delta=msgsEl.scrollHeight-prevScrollHeight;
    msgsEl.scrollTop=prevScrollTop+delta;
  }
}
// The initial capped render omits rows[0 .. rows.length-cap-1]; recompute that
// prefix length from the current scene so the reveal is exact even if the count
// attribute is missing (cache round-trip).
function _computeTransparentHiddenPrefixCount(rows){
  const cap=_TRANSPARENT_SETTLED_ROW_CAP;
  const slack=_TRANSPARENT_SETTLED_ROW_CAP_SLACK;
  return (rows.length>cap+slack)?(rows.length-cap):0;
}
// One-shot token: the stream id of the turn that JUST settled at STREAM_DONE.
// The keep-open exception applies to ONLY this one turn's settled render, then
// is cleared so every other (historical) settled worklog renders compact even
// while the reader is pinned. Set right before the STREAM_DONE
// renderMessages({preserveScroll:true}) call and cleared after the settled-scene
// render pass; null at all other times.
let _keepSettledWorklogOpenForStreamId=null;
function _shouldKeepSettledWorklogOpenForStreamSettle(streamId){
  // Round 6 scroll-jump guard: collapsing the JUST-settled live worklog into a
  // compact summary at STREAM_DONE shrinks the transcript by hundreds of px. The
  // resulting backward jump hits readers in TWO positions, so keep that one
  // worklog open for BOTH on the settle render — the live->settled DOM swap is
  // then height-stable and there is no shrink for any scroll path to mishandle:
  //
  //  1. PINNED follower at the live tail: the shrink lowers scrollHeight, the
  //     browser clamps scrollTop to the new max, and the viewport snaps upward
  //     even though pin state is correct.
  //  2. UNPINNED reader who scrolled UP to read inside the just-settled turn
  //     (the mobile "往回大跳" report, #MOBILESCROLL follow-up): the worklog sits
  //     ABOVE their viewport, so collapsing it pulls their content up to the top
  //     of the turn. On desktop overflow-anchor:none + the JS snapshot restore
  //     keep them put, but on mobile the CSS resting value is overflow-anchor:
  //     auto AND _fixMobileScrollJank() flips an inline overflow-anchor:none over
  //     the settle render — which is exactly the wrong state: native anchoring is
  //     suppressed during the one frame the unpinned reader needs it to absorb
  //     the above-viewport shrink, so the content leaps to the turn's top. Keeping
  //     the worklog open removes the shrink entirely, which fixes it for every
  //     device/anchor-mode combination instead of fighting the anchor engine.
  //
  // SCOPING: the exception is gated on the one-shot token matching this turn's
  // stream id, so it applies ONLY to the turn that just settled — not to every
  // historical settled worklog on every re-render (which would defeat the
  // compact-worklog default for past turns).
  return !!(streamId&&_keepSettledWorklogOpenForStreamId===streamId);
}
// One-shot token set/clear API used by the STREAM_DONE handler (messages.js):
// arm the keep-open exception for exactly the turn that just settled, render,
// then disarm so subsequent re-renders collapse historical worklogs as normal.
function _armKeepSettledWorklogOpen(streamId){
  _keepSettledWorklogOpenForStreamId=streamId?String(streamId):null;
}
function _disarmKeepSettledWorklogOpen(){
  _keepSettledWorklogOpenForStreamId=null;
}
function _assistantTurnHasVisibleRenderedSegment(turn){
  if(!turn||typeof turn.querySelectorAll!=='function') return null;
  for(const seg of turn.querySelectorAll('.assistant-segment')){
    if(seg.classList.contains('assistant-segment-worklog-source')) continue;
    if(seg.classList.contains('assistant-segment-anchor')) continue;
    if((seg.textContent||'').trim()) return true;
  }
  return false;
}
function _collapseJustSettledWorklogInPlace(streamId){
  // #6414: STREAM_DONE used to render the settled turn once with its Worklog
  // forced open, then immediately run a second full render only to collapse it.
  // That second `innerHTML=''` rebuild removes the Worklog the user just saw and
  // can expose a reset frame. Keep the canonical first render, then collapse its
  // one settled group in place. The rows are released after the group is hidden;
  // an expand still rebuilds them from the settled transcript via the normal
  // #5839 deferred-row path.
  const inner=$('msgInner');
  if(!inner||!streamId) return false;
  const group=Array.from(inner.querySelectorAll('[data-anchor-settled-scene-owner="1"]'))
    .filter(candidate=>candidate.getAttribute('data-anchor-stream-id')===String(streamId))
    .pop();
  if(!group) return false;
  const ownerTurn=typeof group.closest==='function'?group.closest('.assistant-turn'):null;
  const ownerHasVisibleSegment=_assistantTurnHasVisibleRenderedSegment(ownerTurn);
  if(ownerHasVisibleSegment===null) return false;
  const disclosureKey=group.getAttribute('data-activity-disclosure-key')||'';
  const savedDisclosure=_readActivityDisclosureState(disclosureKey);
  const rows=_deferredWorklogRowsFromGroup(group);
  if(!rows||!rows.length) return false;
  const match=/^anchor-scene:(\d+)$/.exec(disclosureKey);
  const message=match&&S.messages&&S.messages[Number(match[1])];
  const errored=!!(message&&message._anchor_activity_scene&&
    _anchorSceneHasErroredTerminalState(message._anchor_activity_scene));
  const keepOpen=!ownerHasVisibleSegment
    || savedDisclosure==='open'
    || (errored&&savedDisclosure!=='closed')
    || (_worklogDetailsExpandedDefault()&&savedDisclosure!=='closed');
  if(!keepOpen){
    const detailDisclosure=typeof _captureWorklogDetailDisclosureState==='function'
      ? _captureWorklogDetailDisclosureState(group)
      : null;
    group._deferredWorklogRows=rows;
    group._deferredWorklogDisclosure=detailDisclosure&&detailDisclosure.size
      ? detailDisclosure
      : null;
    group.setAttribute('data-worklog-rows-deferred','1');
    group.classList.add('tool-call-group-collapsed');
    group.classList.remove('open');
    const summary=group.querySelector('.tool-worklog-summary,.tool-call-group-summary');
    if(summary) summary.setAttribute('aria-expanded','false');
    _syncToolCallGroupSummary(group);
    requestAnimationFrame(()=>{
      if(!group.isConnected||!group.classList.contains('tool-call-group-collapsed')) return;
      if(group.getAttribute('data-worklog-rows-deferred')!=='1') return;
      const list=_toolWorklogListEl(group);
      if(list) list.replaceChildren();
    });
  }
  return true;
}
// True while a just-settled worklog is being force-rendered open (between
// _armKeepSettledWorklogOpen and _disarmKeepSettledWorklogOpen). renderMessages()
// consults this so it does NOT write the forced-open DOM into _sessionHtmlCache:
// the keep-open is a transient settle-frame device, and caching it would persist
// the forced-open worklog across session switches / restores, silently overriding
// a user-collapsed worklog. (#5260 gate-cert: keep-open must not leak into cache.)
function _isKeepSettledWorklogOpenArmed(){
  return _keepSettledWorklogOpenForStreamId!==null;
}
if(typeof window!=='undefined'){
  window._armKeepSettledWorklogOpen=_armKeepSettledWorklogOpen;
  window._disarmKeepSettledWorklogOpen=_disarmKeepSettledWorklogOpen;
}
function _renderSettledAnchorSceneForMessage(message, segment, rawIdx){
  if(!message||!message._anchor_activity_scene||!segment) return false;
  if(!_anchorSceneSceneHasWorklogWorthyRows(message._anchor_activity_scene)) return false;
  if(typeof isTransparentStream==='function'&&isTransparentStream()){
    return _renderSettledAnchorSceneTransparentForMessage(message,segment,rawIdx);
  }
  if(typeof isCompactWorklogMode==='function'&&!isCompactWorklogMode()) return false;
  const blocks=_assistantTurnBlocks(segment.closest('.assistant-turn'));
  if(!blocks) return false;
  const scene=message._anchor_activity_scene;
  const rows=_anchorSceneRowsForRendering(scene,{settled:true});
  if(!rows.length) return false;
  blocks.querySelectorAll('.assistant-segment[data-msg-idx]').forEach(node=>{
    const idx=Number(node.getAttribute('data-msg-idx'));
    if(Number.isFinite(idx)&&idx<rawIdx){
      node.classList.add('assistant-segment-worklog-source');
      node.setAttribute('aria-hidden','true');
      node.hidden=true;
    }
  });
  blocks.querySelectorAll('.tool-worklog-group:not([data-anchor-scene-owner="1"]),.tool-call-group:not([data-anchor-scene-owner="1"]),.agent-activity-thinking:not([data-anchor-scene-row="1"]),.wl-reason').forEach(el=>el.remove());
  const streamId=String(message._anchor_stream_id||scene.stream_id||scene.identity&&scene.identity.stream_id||'');
  const keepSettledWorklogOpen=_shouldKeepSettledWorklogOpenForStreamSettle(streamId);
  const activityKey=`anchor-scene:${rawIdx}`;
  if(streamId&&!_readActivityDisclosureState(activityKey)){
    _copyActivityDisclosureState(`live:${streamId}`, activityKey);
  }
  // #5941: an errored turn that produced assistant content (tool calls /
  // reasoning) must not hide that content behind a collapsed header — the user
  // reads a lone error card as "nothing came back". When the settled scene's
  // terminal_state is an error/failure (NOT a normal completion) keep the
  // worklog EXPANDED by default so the produced response stays visible. This
  // path is only reached for worklog-worthy scenes (the guard at the top
  // requires >=1 tool/thinking/compression row), so a genuinely-empty errored
  // turn — a real no_response with zero produced content — never gets here and
  // still shows only its error card, no phantom empty body. A user who has
  // explicitly collapsed THIS turn's worklog (saved 'closed' disclosure state)
  // is still respected, so the default-open never fights an intentional collapse.
  const erroredWorklogKeepOpen=_anchorSceneHasErroredTerminalState(scene)
    && _readActivityDisclosureState(activityKey)!=='closed';
  // keepSettledWorklogOpen forces collapsed:false for the ONE height-stable settle
  // render of the just-settled turn (no STREAM_DONE shrink jump) for both pinned
  // followers AND unpinned mid-turn readers. The keep-open is made genuinely
  // transient by the STREAM_DONE handler (messages.js): right after this render it
  // disarms the token and runs a scroll-PRESERVING collapse pass, so a worklog the
  // reader had manually collapsed returns to its copied disclosure state
  // (_copyActivityDisclosureState above) without the jump. While the token is
  // armed this forced-open DOM is also kept OUT of _sessionHtmlCache
  // (_isKeepSettledWorklogOpenArmed), so it never persists across restores.
  const group=_anchorSceneWorklogGroup(blocks,{
    live:false,
    collapsed:!(keepSettledWorklogOpen||erroredWorklogKeepOpen),
    beforeAnchor:true,
    anchor:segment,
    activityKey,
    streamId,
    turnDuration:message._turnDuration!==undefined&&message._turnDuration!==null?message._turnDuration:scene.turn_duration,
  });
  if(!group) return false;
  group.setAttribute('data-anchor-settled-scene-owner','1');
  // #5839: for a COLLAPSED settled worklog, defer building the row DOM until the
  // user first expands it. A reasoning-heavy turn can carry 80+ activity rows;
  // eagerly materializing them for every historical turn balloons the DOM and a
  // later synchronous layout (e.g. opening a dropdown) tips the tab into a
  // multi-GB freeze. The summary chip renders from data-turn-duration, not the
  // rows, so a deferred worklog still shows its "Processed in Xs" label. On
  // expand, _toggleActivityGroup materializes the stashed rows exactly once.
  const collapsed=group.classList.contains('tool-call-group-collapsed');
  if(collapsed){
    group._deferredWorklogRows=rows;
    group.setAttribute('data-worklog-rows-deferred','1');
    const list=_toolWorklogListEl(group);
    if(list) list.innerHTML='';
    _syncToolCallGroupSummary(group);
    return true;
  }
  group._deferredWorklogRows=null;
  group.removeAttribute('data-worklog-rows-deferred');
  return _renderAnchorSceneRowsIntoWorklog(group,rows,{settled:true});
}
function _syncLiveWorklogReasonsForAnchor(anchor, displayTextOverride){
  if(S.activeStreamId&&isLiveAnchorActivitySceneOwner(S.activeStreamId)) return;
  // Worklog reason-mirroring (folding intermediate prose into a top Worklog rail
  // and hiding the inline `assistant-segment` via `assistant-segment-worklog-source`
  // → display:none) is the Compact Worklog presentation (#3401). In Transparent
  // Stream mode prose must stay as visible, chronologically-placed inline segments
  // interleaved with tool rows — so do NOT build the worklog rail or hide the
  // inline segment here. Without this gate every round's prose mirror piles into
  // the single top rail while tool rows append at the bottom, so all prose bunches
  // above all tools during a live multi-round turn (#4096); it only self-heals when
  // the turn settles and renderMessages() rebuilds with the compact-only
  // `messageBelongsInWorklog` gate (which is already isCompactWorklogMode()-only).
  if(typeof isCompactWorklogMode==='function' && !isCompactWorklogMode()) return;
  if(!anchor||!anchor.matches||!anchor.matches('[data-live-assistant="1"]')) return;
  const blocks=anchor.parentElement;
  if(!blocks) return;
  const group=ensureLiveWorklogContainer(blocks,{
    activityKey:_activityKeyForLiveTurn(),
    anchor,
  });
  if(group) _syncWorklogReasonFromAnchor(group, anchor, displayTextOverride);
}
function _clearLiveActivityUserIntent(){
  _liveActivityUserExpanded = undefined;
}
function ensureActivityGroup(inner, opts){
  opts=opts||{};
  if(!inner) return null;
  const live=!!opts.live;
  const activityKey=opts.activityKey||(live?_activityKeyForLiveTurn():null);
  const burstId=opts.burstId!==undefined&&opts.burstId!==null?String(opts.burstId):'';
  const segmentSeq=opts.segmentSeq!==undefined&&opts.segmentSeq!==null?String(opts.segmentSeq):'';
  const liveSelectors=segmentSeq
    ? [
      `.tool-worklog-group[data-live-tool-worklog-group="1"][data-live-segment-seq="${CSS.escape(segmentSeq)}"]`,
      `.tool-call-group[data-live-tool-worklog-group="1"][data-live-segment-seq="${CSS.escape(segmentSeq)}"]`,
      `.tool-call-group[data-live-tool-call-group="1"][data-live-segment-seq="${CSS.escape(segmentSeq)}"]`,
    ]
    : burstId
    ? [
      `.tool-worklog-group[data-live-tool-worklog-group="1"][data-activity-burst-id="${CSS.escape(burstId)}"]`,
      `.tool-call-group[data-live-tool-worklog-group="1"][data-activity-burst-id="${CSS.escape(burstId)}"]`,
      `.tool-call-group[data-live-tool-call-group="1"][data-activity-burst-id="${CSS.escape(burstId)}"]`,
    ]
    : [
      '.tool-worklog-group[data-live-tool-worklog-group="1"][data-live-activity-current="1"]',
      '.tool-call-group[data-live-tool-worklog-group="1"][data-live-activity-current="1"]',
      '.tool-call-group[data-live-tool-call-group="1"][data-live-activity-current="1"]',
    ];
  let group;
  if(live){
    if(activityKey){
      group=inner.querySelector(`.tool-worklog-group[data-tool-worklog-key="${CSS.escape(activityKey)}"],.tool-call-group[data-tool-worklog-key="${CSS.escape(activityKey)}"]`);
    }
    if(!group){
      for(const sel of liveSelectors){
        group=inner.querySelector(sel);
        if(group) break;
      }
    }
  }else{
    if(activityKey){
      group=inner.querySelector(`.tool-worklog-group[data-agent-activity-group="1"][data-tool-worklog-group="1"][data-tool-worklog-key="${CSS.escape(activityKey)}"],.tool-call-group[data-agent-activity-group="1"][data-tool-worklog-group="1"][data-tool-worklog-key="${CSS.escape(activityKey)}"]`);
    }
    if(!group&&segmentSeq){
      group=inner.querySelector(`.tool-worklog-group[data-agent-activity-group="1"][data-tool-worklog-group="1"][data-live-segment-seq="${CSS.escape(segmentSeq)}"],.tool-call-group[data-agent-activity-group="1"][data-tool-worklog-group="1"][data-live-segment-seq="${CSS.escape(segmentSeq)}"]`);
    }
    if(!group&&burstId){
      group=inner.querySelector(`.tool-worklog-group[data-agent-activity-group="1"][data-tool-worklog-group="1"][data-activity-burst-id="${CSS.escape(burstId)}"],.tool-call-group[data-agent-activity-group="1"][data-tool-worklog-group="1"][data-activity-burst-id="${CSS.escape(burstId)}"]`);
    }
    if(!group&&activityKey){
      group=inner.querySelector(`.tool-worklog-group[data-tool-worklog-key="${CSS.escape(activityKey)}"],.tool-call-group[data-tool-worklog-key="${CSS.escape(activityKey)}"]`);
    }
    if(!group&&!activityKey){
      group=inner.querySelector('.tool-worklog-group[data-agent-activity-group="1"][data-tool-worklog-group="1"],.tool-call-group[data-agent-activity-group="1"][data-tool-worklog-group="1"],.tool-call-group[data-agent-activity-group="1"]:not([data-run-activity-group="1"])');
    }
  }
  if(!group && !activityKey && segmentSeq==="" && burstId){
    const candidates=live
      ? Array.from(inner.querySelectorAll('.tool-worklog-group[data-live-tool-worklog-group="1"],.tool-call-group[data-live-tool-worklog-group="1"],.tool-call-group[data-live-tool-call-group="1"]'))
      : Array.from(inner.querySelectorAll('.tool-worklog-group[data-agent-activity-group="1"],.tool-call-group[data-agent-activity-group="1"]:not([data-run-activity-group="1"])'));
    group=candidates.filter(el=>el.isConnected!==false).pop() || null;
  }
  if(!group){
    group=document.createElement('div');
    let collapsed=opts.collapsed!==false;
    if(window._worklogDetailsExpandedByDefault===true) collapsed=false;
    const savedState=_readActivityDisclosureState(activityKey);
    // Restore the user's explicit expand intent when recreating the live
    // activity group within the same turn (#1298), then let persisted chat/turn
    // state win across session switches and reloads. Saved closed-state should
    // override the default-expanded preference for settled groups the user has
    // explicitly collapsed.
    if(live && _liveActivityUserExpanded === true) collapsed=false;
    else if(live && _liveActivityUserExpanded === false) collapsed=true;
    if(live && savedState==='open') collapsed=false;
    else if(live && savedState==='closed') collapsed=true;
    group.className='agent-activity-group tool-worklog-group activity'+(collapsed?' tool-call-group-collapsed':'');
    group.setAttribute('data-tool-call-group','1');
    group.setAttribute('data-agent-activity-group','1');
    group.setAttribute('data-tool-worklog-group','1');
    group.setAttribute('data-tool-worklog-key',activityKey||'');
    if(activityKey) group.setAttribute('data-activity-disclosure-key',activityKey);
    if(live){
      group.setAttribute('data-live-tool-worklog-group','1');
      group.setAttribute('data-live-tool-call-group','1');
      group.setAttribute('data-live-activity-current','1');
    }
    if(burstId) group.setAttribute('data-activity-burst-id',burstId);
    if(segmentSeq) group.setAttribute('data-live-segment-seq',segmentSeq);
    group.classList.toggle('open',!collapsed);
    group.innerHTML=`<button type="button" class="tool-call-group-summary tool-worklog-summary activity-summary" aria-expanded="${collapsed?'false':'true'}" onclick="_toggleActivityGroup(this)"><span class="as-dot"></span><span class="tool-call-group-label tool-worklog-label as-text">Running</span><span class="tool-call-group-duration"></span><span class="tool-call-group-chevron as-caret">${li('chevron-right',12)}</span></button><div class="tool-call-group-body tool-worklog-body activity-body"><div class="worklog"><div class="tool-worklog-list"></div></div></div>`;
    const anchor=opts.anchor||null;
    if(anchor&&anchor.parentElement===inner){
      if(opts.beforeAnchor) inner.insertBefore(group, anchor);
      else anchor.insertAdjacentElement('afterend', group);
    }
    else inner.appendChild(group);
  }else if(activityKey&&!group.getAttribute('data-activity-disclosure-key')){
    group.setAttribute('data-activity-disclosure-key',activityKey);
  }
  if(burstId&&!group.getAttribute('data-activity-burst-id')) group.setAttribute('data-activity-burst-id',burstId);
  if(segmentSeq&&!group.getAttribute('data-live-segment-seq')) group.setAttribute('data-live-segment-seq',segmentSeq);
  if(!group.getAttribute('data-tool-worklog-key')&&activityKey) group.setAttribute('data-tool-worklog-key',activityKey);
  if(opts.turnDuration!==undefined&&opts.turnDuration!==null) group.setAttribute('data-turn-duration',String(opts.turnDuration));
  if(opts.turnStartedAt!==undefined&&opts.turnStartedAt!==null) group.setAttribute('data-turn-started-at',String(opts.turnStartedAt));
  const summary=group.querySelector('.tool-worklog-summary,.tool-call-group-summary');
  if(summary){
    summary.removeAttribute('data-live-summary-static');
    summary.removeAttribute('aria-disabled');
    summary.disabled=false;
  }
  const anchor=opts.anchor||null;
  if(anchor&&anchor.parentElement===inner&&group.parentElement===inner){
    if(opts.beforeAnchor){
      if(group.nextElementSibling!==anchor) inner.insertBefore(group,anchor);
    }else if(group.previousElementSibling!==anchor){
      anchor.insertAdjacentElement('afterend',group);
    }
  }
  if(anchor&&opts.syncAnchorReason!==false) _syncWorklogReasonFromAnchor(group, anchor);
  _syncToolCallGroupSummary(group);
  return group;
}
function normalizeLiveActivityGroupPlacement(turn){
  const blocks=_assistantTurnBlocks(turn);
  if(!blocks) return;
  // Compact Worklog only: this reorders `.tool-call-group`/`.tool-worklog-group`
  // containers, which exist solely on the Compact Worklog live path. Transparent
  // Stream renders tool rows as flat `.transparent-event-row`s and never builds
  // these group containers (see appendLiveToolCard's transparent branch), and the
  // worklog prose-rail is gated off in transparent mode (#4096), so the selector
  // below matches nothing and this is a no-op there. Kept implicit (empty match)
  // rather than an early return so reconnect/restore behavior is unchanged.
  const groups=Array.from(
    blocks.querySelectorAll('.tool-worklog-group[data-live-tool-worklog-group="1"],.tool-call-group[data-live-tool-worklog-group="1"],.tool-call-group[data-live-tool-call-group="1"]')
  );
  groups.sort((a,b)=>{
    const as=Number(a.getAttribute('data-live-segment-seq'));
    const bs=Number(b.getAttribute('data-live-segment-seq'));
    if(Number.isFinite(as)&&Number.isFinite(bs)&&as!==bs) return as-bs;
    const av=Number(a.getAttribute('data-activity-burst-id'));
    const bv=Number(b.getAttribute('data-activity-burst-id'));
    if(Number.isFinite(av)&&Number.isFinite(bv)&&av!==bv) return av-bv;
    return 0;
  });
  for(const group of groups){
    const burstId=group.getAttribute('data-activity-burst-id')||'';
    const segmentSeq=group.getAttribute('data-live-segment-seq')||'';
    const anchor=segmentSeq
      ? _findLiveAssistantAnchorForSegment(blocks, segmentSeq)
      : burstId
      ? _findLatestVisibleLiveAssistantByBurst(blocks, burstId)
      : _findLatestVisibleLiveAssistant(blocks);
    if(!anchor) continue;
    if(anchor&&group.previousElementSibling!==anchor) anchor.insertAdjacentElement('afterend',group);
    _syncWorklogReasonFromAnchor(group, anchor);
  }
}
function ensureRunActivityGroup(inner, opts){
  opts=opts||{};
  if(!inner) return null;
  let group=inner.querySelector('.tool-call-group[data-run-activity-group="1"]');
  if(!group){
    group=document.createElement('div');
    const collapsed=opts.collapsed!==false;
    group.className='tool-call-group agent-activity-group run-activity-group'+(collapsed?' tool-call-group-collapsed':' open');
    group.setAttribute('data-tool-call-group','1');
    group.setAttribute('data-agent-activity-group','1');
    group.setAttribute('data-run-activity-group','1');
    group.innerHTML=`<button type="button" class="tool-call-group-summary" aria-expanded="${collapsed?'false':'true'}" onclick="_toggleActivityGroup(this)"><span class="tool-call-group-chevron">${li('chevron-right',12)}</span><span class="tool-call-group-label">Running</span><span class="tool-call-group-duration"></span></button><div class="tool-call-group-body"></div>`;
    if(inner.firstChild) inner.insertBefore(group, inner.firstChild);
    else inner.appendChild(group);
  }
  if(opts.turnDuration!==undefined&&opts.turnDuration!==null) group.setAttribute('data-turn-duration',String(opts.turnDuration));
  if(opts.turnStartedAt!==undefined&&opts.turnStartedAt!==null) group.setAttribute('data-turn-started-at',String(opts.turnStartedAt));
  _setActivityElapsedStartedAt(group);
  _ensureLiveActivityBaseline(group);
  _syncToolCallGroupSummary(group);
  if(opts.live!==false) _startActivityElapsedTimer(group);
  return group;
}
// ── LiveFooter timer (module-level singleton) ──────────────────────────────
const _liveRunStatusTimers={};  // keyed by sessionId, max 1 active
let _liveRunStatusTokens=null;
let _liveRunStatusSessionId=null;
function _formatRunElapsed(seconds){
  const n=Number(seconds);
  if(!Number.isFinite(n)||n<0)return'00:00';
  const total=Math.max(0,Math.floor(n));
  if(total>=3600){
    const h=Math.floor(total/3600);
    const m=Math.floor((total%3600)/60);
    return h+'h '+String(m).padStart(2,'0')+'m';
  }
  const m=Math.floor(total/60);
  const s=total%60;
  return String(m).padStart(2,'0')+':'+String(s).padStart(2,'0');
}
function _moveLiveRunStatusToTurnEnd(el){
  el=el||$('liveRunStatus');
  if(!el) return null;
  const turn=$('liveAssistantTurn');
  const blocks=_assistantTurnBlocks(turn);
  if(blocks&&el.parentElement===blocks&&blocks.lastElementChild!==el) blocks.appendChild(el);
  return el;
}
function placeLiveRunStatusHost(){
  let el=$('liveRunStatus');
  if(!el){
    el=document.createElement('div');
    el.id='liveRunStatus';
    el.hidden=true;
  }
  let turn=$('liveAssistantTurn');
  if(!turn){
    turn=_createAssistantTurn();
    turn.id='liveAssistantTurn';
    if(S.session) turn.dataset.sessionId=S.session.session_id;
    const inner=$('msgInner');
    if(inner) inner.appendChild(turn);
  }
  const blocks=_assistantTurnBlocks(turn);
  if(blocks&&el.parentElement!==blocks) blocks.appendChild(el);
  el.className='live-run-status live-footer';
  return _moveLiveRunStatusToTurnEnd(el);
}
function showLiveRunStatus(sid,opts){
  if(typeof isCompactWorklogMode==='function'&&isCompactWorklogMode()){
    _liveRunStatusSessionId=sid;
    _liveRunStatusTokens=opts&&opts.tokens||null;
    const el=$('liveRunStatus');
    if(el){el.hidden=true;el.innerHTML='';}
    return;
  }
  const el=placeLiveRunStatusHost();
  if(!el)return;
  _liveRunStatusSessionId=sid;
  const startedAt=opts&&opts.startedAt||null;
  _liveRunStatusTokens=opts&&opts.tokens||null;
  el.hidden=false;
  _renderLiveRunStatusContent(el,startedAt);
  _startLiveRunStatusTimer(sid,startedAt);
}
function _renderLiveRunStatusContent(el,startedAt){
  if(!el)return;
  const now=Date.now()/1000;
  const elapsed=startedAt?Math.max(0,now-startedAt):0;
  const timeStr=_formatRunElapsed(elapsed);
  const tokens=_liveRunStatusTokens;
  el.innerHTML=`<span class="live-run-status-dot tool-card-running-dot"></span><span class="live-run-status-text lf-time">${timeStr}</span>${tokens?`<span class="lf-sep">·</span><span class="lf-tokens">${_fmtTokens(tokens)} tokens</span>`:''}<span class="lf-sep">·</span><span class="lf-status">Running</span>`;
}
function updateLiveRunStatus(opts){
  if(opts&&opts.sessionId&&_liveRunStatusSessionId&&opts.sessionId!==_liveRunStatusSessionId) return;
  if(opts&&opts.tokens!==undefined)_liveRunStatusTokens=opts.tokens;
  const el=$('liveRunStatus');
  if(el&&!el.hidden){
    _moveLiveRunStatusToTurnEnd(el);
    const timer=_liveRunStatusTimers[_liveRunStatusSessionId];
    const startedAt=timer&&timer.startedAt||null;
    _renderLiveRunStatusContent(el,startedAt);
  }
}
function _syncLiveRunStatusAfterRender(){
  const sid=S.session&&S.session.session_id;
  if(!sid||!S.activeStreamId||!S.busy) return;
  const timer=_liveRunStatusTimers[sid];
  const startedAt=(timer&&timer.startedAt)||((S.session&&S.session.pending_started_at)||Date.now()/1000);
  if(typeof isCompactWorklogMode==='function'&&isCompactWorklogMode()){
    const el=$('liveRunStatus');
    if(el){el.hidden=true;el.innerHTML='';}
    return;
  }
  const el=$('liveRunStatus');
  if(el&&el.isConnected&&!el.hidden){
    _moveLiveRunStatusToTurnEnd(el);
    _renderLiveRunStatusContent(el,startedAt);
    return;
  }
  showLiveRunStatus(sid,{startedAt,tokens:_liveRunStatusTokens});
}
function hideLiveRunStatus(sid){
  if(sid&&_liveRunStatusSessionId&&sid!==_liveRunStatusSessionId) return;
  const el=$('liveRunStatus');
  if(el){el.hidden=true;el.innerHTML='';}
  _clearLiveRunStatusTimer(sid||_liveRunStatusSessionId);
  _liveRunStatusTokens=null;
  _liveRunStatusSessionId=null;
}
function _startLiveRunStatusTimer(sid,startedAt){
  if(!sid)return;
  _clearLiveRunStatusTimer(sid);
  _liveRunStatusTimers[sid]={startedAt,interval:setInterval(()=>{
    const el=$('liveRunStatus');
    if(!el||el.hidden){_clearLiveRunStatusTimer(sid);return;}
    if(_liveRunStatusSessionId!==sid)return;
    _renderLiveRunStatusContent(el,startedAt);
  },1000)};
}
function _clearLiveRunStatusTimer(sid){
  const t=_liveRunStatusTimers[sid];
  if(t){clearInterval(t.interval);delete _liveRunStatusTimers[sid];}
}
function ensureRunActivityForCurrentTurn(){
  // Phase C: disabled — top live run Activity card removed
  return null;
}
function closeCurrentLiveActivityGroup(){
  const turn=$('liveAssistantTurn');
  if(!turn) return;
  turn.querySelectorAll('.tool-worklog-group[data-live-tool-call-group="1"][data-live-activity-current="1"],.tool-call-group[data-live-tool-call-group="1"][data-live-activity-current="1"]').forEach(group=>{
    group.removeAttribute('data-live-activity-current');
    _finalizeLiveActivityDisclosureGroup(group);
  });
}
function _compressionStateForCurrentSession(){
  const state=window._compressionUi;
  if(!state||!S.session||state.sessionId!==S.session.session_id) return null;
  return state;
}
function isCompressionUiRunning(){
  const state=_compressionStateForCurrentSession();
  const lock=_compressionSessionLock();
  return !!((state&&state.phase==='running') || (lock && S.session && lock===S.session.session_id));
}
// Restore the composer placeholder saved when auto-compaction started. Safe to
// call whenever compression leaves the running state, from any path (clear,
// non-running setCompressionUi, or a direct window._compressionUi=null in the
// SSE handler) — it no-ops when nothing was saved. (#3512)
function _restoreCompressionPlaceholder(){
  const _input=$('msg');
  if(_input&&typeof _compressionPlaceholderSaved==='string'){
    _input.placeholder=_compressionPlaceholderSaved;
  }
  _compressionPlaceholderSaved=null;
}
function clearCompressionUi(){
  window._compressionUi=null;
  _clearCompressionElapsedTimer();
  _setCompressionSessionLock(null);
  _restoreCompressionPlaceholder();
  renderCompressionUi();
}
function setCompressionUi(state){
  if(!state){
    clearCompressionUi();
    return;
  }
  const nextState={...state};
  if(nextState.automatic&&nextState.phase==='running'&&!_compressionElapsedStartedAt(nextState)){
    nextState.startedAt=Date.now()/1000;
  }
  window._compressionUi=nextState;
  if(nextState.sessionId) _setCompressionSessionLock(nextState.sessionId);
  if(nextState.automatic&&nextState.phase==='running'){
    _startCompressionElapsedTimer();
    const _input=$('msg');
    if(_input&&_compressionPlaceholderSaved===null){
      _compressionPlaceholderSaved=_input.placeholder;
      _input.placeholder=typeof t==='function'?t('composer_compression_will_queue')||'Type a message — it will queue and send after compression':'Type a message — it will queue and send after compression';
    }
  } else {
    _clearCompressionElapsedTimer();
    // Leaving the running state (e.g. setCompressionUi(done)) must restore the
    // placeholder too — not only clearCompressionUi(). (#3512 leak fix)
    _restoreCompressionPlaceholder();
  }
  renderCompressionUi();
}
function _compressionCardsHtml(state){
  if(!state) return '';
  if(state.automatic) return _autoCompressionCardsHtml(state);
  const cmdText=state.commandText||'/compress';
  const focusText=state.focusTopic?`${t('focus_label')}: ${state.focusTopic}`:'';
  const headerText=state.phase==='done'
    ? (state.summary?.headline||t('compress_complete_label'))
    : state.phase==='error'
      ? (state.errorText||t('compress_failed_label'))
      : (typeof state.beforeCount==='number' ? t('n_messages', state.beforeCount) : '');
  const statusBody=state.phase==='error'
    ? [state.errorText||t('compress_failed_label'), focusText].filter(Boolean).join('\n')
    : [t('compressing'), focusText].filter(Boolean).join('\n');
  const statusLabel=state.phase==='done'
    ? t('compress_complete_label')
    : state.phase==='error'
      ? t('compress_failed_label')
      : t('compress_running_label');
  const statusIcon=state.phase==='done'
    ? li('check',13)
    : state.phase==='error'
      ? li('x',13)
    : `<span class="tool-card-running-dot"></span>`;
  const doneCardHtml=state.phase==='done'
    ? _compressionStatusCardHtml({
        statusLabel,
        previewText: headerText,
        detail: [state.summary?.token_line, state.summary?.note, focusText].filter(Boolean).join('\n'),
        icon: statusIcon,
        open: true,
        variantClass: 'tool-card-compress-complete',
      })
    : '';
  const referenceHtml=(state.phase==='done'&&state.referenceText)
    ? _compressionReferenceCardHtml(state.referenceText, false)
    : '';
  return `
    <div class="tool-card-row compression-card-row" data-compression-card="1">
      <div class="tool-card tool-card-compress-command">
        <div class="tool-card-header" onclick="this.closest('.tool-card').classList.toggle('open')">
          <span class="tool-card-icon">${li('settings',13)}</span>
          <span class="tool-card-name">${esc(t('command_label'))}</span>
          <span class="tool-card-preview">${esc(cmdText)}</span>
        </div>
      </div>
    </div>
    <div class="tool-card-row compression-card-row" data-compression-card="1">
      ${state.phase==='done'
        ? doneCardHtml
        : _compressionStatusCardHtml({
            statusLabel,
            previewText: headerText,
            detail: statusBody,
            icon: statusIcon,
            open: false,
            variantClass: state.phase==='error'
              ? 'tool-card-compress-error'
              : 'tool-card-compress-running',
          })
      }
    </div>
    ${referenceHtml}`;
}
function _autoCompressionBaseDetail(state){
  const running=state&&state.phase==='running';
  if(running)return 'Compressing context';
  if(state&&state.phase==='done')return 'Context auto-compressed';
  return '';
}
function _autoCompressionPreviewText(state){
  const running=state&&state.phase==='running';
  if(running)return 'Compressing context';
  if(state&&state.phase==='done')return 'Context auto-compressed';
  return '';
}
function _autoCompressionDetailText(state){
  const running=state&&state.phase==='running';
  if(running)return '';
  return '';
}
function _autoCompressionCardsHtml(state){
  const preview=_autoCompressionPreviewText(state);
  const done=state&&state.phase==='done';
  return `
    <div class="tool-card-row compression-card-row auto-compression-divider-row auto-compression-inline-row" data-compression-card="1">
      <div class="auto-compression-divider auto-compression-inline${done?' auto-compression-divider-done':''}" aria-label="${esc(preview)}">
        <span class="auto-compression-divider-label">${done?li('file-text',13):li('loader',13)}${esc(preview)}</span>
      </div>
    </div>`;
}
function _autoCompressionWorklogNode(state){
  const row=document.createElement('div');
  row.className='tool-card-row compression-card-row auto-compression-divider-row auto-compression-inline-row';
  row.setAttribute('data-compression-card','1');
  const label=_autoCompressionPreviewText(state);
  const done=state&&state.phase==='done';
  row.innerHTML=`
    <div class="auto-compression-divider auto-compression-inline${done?' auto-compression-divider-done':''}" aria-label="${esc(label)}">
      <span class="auto-compression-divider-label">${done?li('file-text',13):li('loader',13)}${esc(label)}</span>
    </div>`;
  return row;
}
function _compressionCardsNode(state){
  const wrap=document.createElement('div');
  wrap.className='compression-turn';
  wrap.innerHTML=`<div class="compression-turn-blocks">${_compressionCardsHtml(state)}</div>`;
  return wrap;
}
function appendLiveCompressionCard(state){
  if(!S.session||!S.activeStreamId||!state) return false;
  if(isLiveAnchorActivitySceneOwner(S.activeStreamId)){
    return _renderLiveAnchorActivitySceneForStream(S.activeStreamId, S.session.session_id);
  }
  const scrollSnapshot=_captureMessageScrollSnapshot();
  let turn=$('liveAssistantTurn');
  if(!turn){
    turn=_createAssistantTurn();
    turn.id='liveAssistantTurn';
    if(S.session) turn.dataset.sessionId=S.session.session_id;
    $('msgInner').appendChild(turn);
  }
  const inner=_assistantTurnBlocks(turn);
  if(!inner) return false;
  closeCurrentLiveActivityGroup();
  if(state.automatic){
    const group=ensureLiveWorklogContainer(inner,{activityKey:_activityKeyForLiveTurn()});
    const list=_toolWorklogListEl(group);
    if(!group||!list) return false;
    const node=_autoCompressionWorklogNode(state);
    node.setAttribute('data-live-compression-card','1');
    node.setAttribute('data-compression-phase',String(state.phase||''));
    if(state.phase==='running'){
      const started=_compressionElapsedStartedAt(state)||Date.now()/1000;
      node.setAttribute('data-compression-started-at',String(started));
      node.setAttribute('data-compression-message',String(state.message||'Compressing context'));
      _startCompressionElapsedTimer();
    } else {
      node.removeAttribute('data-compression-started-at');
      node.removeAttribute('data-compression-message');
      const _activeCompState = _compressionStateForCurrentSession();
      if (!_activeCompState || !_activeCompState.automatic || _activeCompState.phase !== 'running') {
        _clearCompressionElapsedTimer();
      }
    }
    const existingRunning=group.querySelector('[data-live-compression-card="1"][data-compression-started-at]');
    const existingDone=Array.from(group.querySelectorAll('[data-live-compression-card="1"][data-compression-phase="done"]')).pop();
    const existing=state.phase==='running'?existingRunning:(existingRunning||existingDone);
    if(existing) existing.replaceWith(node);
    else list.appendChild(node);
    _syncToolCallGroupSummary(group);
    _moveLiveRunStatusToTurnEnd();
    _restoreMessageScrollSnapshotSameFrame(scrollSnapshot);
    if(typeof scrollIfPinned==='function') scrollIfPinned();
    return true;
  }
  const node=_compressionCardsNode(state);
  if(!node) return false;
  node.setAttribute('data-live-compression-card','1');
  if(state.automatic&&state.phase==='running'){
    const started=_compressionElapsedStartedAt(state)||Date.now()/1000;
    node.setAttribute('data-compression-started-at',String(started));
    node.setAttribute('data-compression-message',String(state.message||'Auto-compressing context...'));
    _startCompressionElapsedTimer();
  } else {
    // Completion or error: clear the elapsed-timer attributes so the
    // interval reader (_compressionLiveCardState) doesn't keep treating
    // the replaced card as a running compression (#2973).
    node.removeAttribute('data-compression-started-at');
    node.removeAttribute('data-compression-message');
    // Only clear the global timer when the *active* session has no running
    // compression.  An SSE completion for a background session must not
    // kill the timer that's driving the current session's display.
    const _activeCompState = _compressionStateForCurrentSession();
    if (!_activeCompState || !_activeCompState.automatic || _activeCompState.phase !== 'running') {
      _clearCompressionElapsedTimer();
    }
  }
  const existing=inner.querySelector('[data-live-compression-card="1"]');
  if(existing) existing.replaceWith(node);
  else inner.appendChild(node);
  _restoreMessageScrollSnapshotSameFrame(scrollSnapshot);
  if(typeof scrollIfPinned==='function') scrollIfPinned();
  return true;
}
function _isHandoffSummaryToolPayload(value){
  if(!value||typeof value!=='object'||Array.isArray(value)) return false;
  return value._handoff_summary_card === true;
}
function _parseHandoffSummaryPayload(content){
  if(!content) return null;
  if(typeof content==='object' && !Array.isArray(content)) return _isHandoffSummaryToolPayload(content)?content:null;
  if(typeof content!=='string') return null;
  try {
    const parsed=JSON.parse(content);
    return _isHandoffSummaryToolPayload(parsed)?parsed:null;
  } catch (e) {
    return null;
  }
}
function _handoffSummaryStateFromMessage(m){
  if(!m||m.role!=='tool') return null;
  const payload = _parseHandoffSummaryPayload(m.content);
  if(!payload) return null;
  if(String(payload.session_id||'') && S.session && String(m.session_id||'') && String(payload.session_id)!==String(S.session.session_id||'')) {
    return null;
  }
  const summary = String(payload.summary||'').trim();
  if(!summary) return null;
  return {
    phase: 'done',
    channel: payload.channel || null,
    rounds: Number.isFinite(payload.rounds)?payload.rounds:null,
    summary,
    fallback: !!payload.fallback,
    generatedAt: Number(payload.generated_at) || null,
  };
}
function _collectHandoffSummaryStates(messages){
  const states=[];
  if(!Array.isArray(messages)) return states;
  for(let i=0;i<messages.length;i++){
    const state=_handoffSummaryStateFromMessage(messages[i]);
    if(state) states.push({state, rawIdx:i});
  }
  return states;
}
function _isContextCompactionMessage(m){
  if(!m||!m.role||m.role==='tool') return false;
  const text=msgContent(m)||String(m.content||'');
  return _isContextCompactionText(text);
}
function _isContextCompactionText(text){
  return /^\s*\[context compaction/i.test(String(text||'')) || /^\s*context compaction/i.test(String(text||''));
}
function _isPreservedCompressionTaskListMarkerText(text){
  return /^\s*\[your active task list was preserved across context compression\]/i.test(String(text||''));
}
function _isPreservedCompressionTaskListMarkerOnlyText(text){
  return _isPreservedCompressionTaskListMarkerText(text)
    && !String(text||'')
      .replace(/^\s*\[your active task list was preserved across context compression\]\s*/i,'')
      .trim();
}
function _isPreservedCompressionTaskListMessage(m){
  if(!m||m.role!=='user') return false;
  const text=msgContent(m)||String(m.content||'');
  return /^\s*\[your active task list was preserved across context compression\]/i.test(text);
}
function _isMarkerOnlyAssistantCompressionMessage(m){
  if(!m||m.role!=='assistant') return false;
  const text=msgContent(m)||String(m.content||'');
  return _isPreservedCompressionTaskListMarkerOnlyText(text);
}
function _preservedCompressionTaskListPreview(text){
  const body=String(text||'')
    .replace(/^\s*\[your active task list was preserved across context compression\]\s*/i,'')
    .trim();
  return (body.split(/\n+/).map(line=>line.trim()).filter(Boolean).slice(0,2).join(' ') || t('preserved_task_list_label'));
}
function _compressionMessageAnchorKey(m){
  if(!m||!m.role||m.role==='tool') return null;
  let content='';
  try{
    content=String(msgContent(m)||'');
  }catch(_){
    content=String(m.content||'');
  }
  const norm=content.replace(/\s+/g,' ').trim().slice(0,160);
  const ts=m._ts||m.timestamp||null;
  const attachments=Array.isArray(m.attachments)?m.attachments.length:0;
  if(!norm && !attachments && !ts) return null;
  return {role:String(m.role||''), ts, text:norm, attachments};
}
function _compressionAnchorIndex(visWithIdx, anchorKey, fallbackIdx=null){
  if(anchorKey&&Array.isArray(visWithIdx)){
    for(let i=visWithIdx.length-1;i>=0;i--){
      const candidate=_compressionMessageAnchorKey(visWithIdx[i].m);
      if(!candidate) continue;
      const anchorTs=String(anchorKey.ts??'');
      const candidateTs=String(candidate.ts??'');
      if(
        candidate.role===String(anchorKey.role||'') &&
        (!anchorTs||!candidateTs||candidateTs===anchorTs) &&
        String(candidate.text||'')===String(anchorKey.text||'') &&
        Number(candidate.attachments||0)===Number(anchorKey.attachments||0)
      ){
        return i;
      }
    }
  }
  return typeof fallbackIdx==='number' ? fallbackIdx : null;
}
function _latestCompressionReferenceMessage(messages, summaryText=''){
  if(!Array.isArray(messages)||!messages.length) return {message:null, rawIdx:-1};
  const summaryNorm=String(summaryText||'').replace(/\s+/g,' ').trim();
  for(let i=messages.length-1;i>=0;i--){
    const m=messages[i];
    if(!_isContextCompactionMessage(m)) continue;
    if(!summaryNorm) return {message:m, rawIdx:i};
    let content='';
    try{
      content=String(msgContent(m)||'');
    }catch(_){
      content=String((m&&m.content)||'');
    }
    const contentNorm=content.replace(/\s+/g,' ').trim();
    if(contentNorm.includes(summaryNorm)) return {message:m, rawIdx:i};
  }
  return {message:null, rawIdx:-1};
}
function _shouldShowSettledCompressionReference(referenceText){
  return !!String(referenceText||'').trim() && !_isContextCompactionText(referenceText);
}
function _compressionReferenceCardHtml(text, open=false){
  const copy=_engineAwareCompressionCopy();
  const preview=text.split(/\n+/).filter(Boolean).slice(0,2).join(' ');
  return `
    <div class="tool-card-row compression-card-row" data-compression-card="1" data-raw-text="${esc(text)}">
      <div class="tool-card tool-card-compress-reference${open?' open':''}">
        <div class="tool-card-header" onclick="this.closest('.tool-card').classList.toggle('open')">
          <span class="tool-card-icon">${li('star',13)}</span>
          <span class="tool-card-name">${esc(copy.label)}</span>
          <span class="tool-card-preview">${esc(copy.preview)} · ${esc(preview)}</span>
          <span class="tool-card-toggle">${li('chevron-right',12)}</span>
          <button class="msg-copy-btn msg-action-btn tool-card-copy compression-reference-copy" title="${t('copy')}" onclick="copyMsg(this);event.stopPropagation()">${li('copy',13)}</button>
        </div>
        <div class="tool-card-detail">
          <div class="tool-card-result">
          <pre>${esc(text)}</pre>
        </div>
        </div>
      </div>
      
    </div>`;
}
function _preservedCompressionTaskListCardHtml(m, open=false){
  const text=msgContent(m)||String(m.content||'');
  return `
    <div class="tool-card-row compression-card-row" data-compression-card="1" data-raw-text="${esc(text)}">
      ${_compressionStatusCardHtml({
        statusLabel: t('preserved_task_list_label'),
        previewText: _preservedCompressionTaskListPreview(text),
        detail: text,
        icon: li('list-todo',13),
        open,
        variantClass: 'tool-card-compress-reference',
      })}
    </div>`;
}
function _preservedCompressionTaskListCardsHtml(messages){
  return (messages||[]).map(m=>_preservedCompressionTaskListCardHtml(m, false)).join('');
}
function _latestTodoToolItems(messages){
  for(let i=(messages||[]).length-1;i>=0;i--){
    const m=messages[i];
    if(!m||m.role!=='tool') continue;
    try{
      const payload=typeof m.content==='string'?JSON.parse(m.content):m.content;
      if(payload&&Array.isArray(payload.todos)) return payload.todos;
    }catch(_){ }
  }
  return null;
}
function _hasActiveTodoItems(items){
  return Array.isArray(items) && items.some(item=>{
    const status=String(item&&item.status||'').trim().toLowerCase();
    return status==='pending'||status==='in_progress';
  });
}
function _latestPreservedCompressionTaskListMessages(messages){
  const latest=[...(messages||[])].reverse().find(m=>_isPreservedCompressionTaskListMessage(m));
  if(!latest) return [];
  const latestTodos=_latestTodoToolItems(messages);
  if(Array.isArray(latestTodos) && !_hasActiveTodoItems(latestTodos)) return [];
  return [latest];
}
function _isSameLocalDay(dateA, dateB){
  return dateA.getFullYear()===dateB.getFullYear()
    && dateA.getMonth()===dateB.getMonth()
    && dateA.getDate()===dateB.getDate();
}
function _formatMessageFooterTimestamp(tsVal){
  if(!tsVal) return '';
  const date=new Date(tsVal*1000);
  const now=new Date();
  // Use _formatInServerTz when available — it correctly handles fractional-hour
  // offsets like India +0530 that Etc/GMT cannot express. Falls back to plain
  // toLocaleString when sessions.js hasn't loaded yet.
  const fmt=(typeof _formatInServerTz==='function')?_formatInServerTz:null;
  if(_isSameLocalDay(date, now)){
    const opts={hour:'2-digit', minute:'2-digit'};
    return fmt?fmt(date,opts):date.toLocaleTimeString([], opts);
  }
  const opts={month:'short', day:'numeric', hour:'numeric', minute:'2-digit'};
  return fmt?fmt(date,opts):date.toLocaleString([], opts);
}
function _compressionEngineForSession(){
  return String(
    (S.session&&(
      S.session.compression_anchor_engine
      || S.session.context_engine
    )) || 'compressor'
  ).trim().toLowerCase() || 'compressor';
}
function _compressionModeForSession(){
  return String(
    (S.session&&S.session.compression_anchor_mode) || 'summary_compaction'
  ).trim().toLowerCase() || 'summary_compaction';
}
function _engineAwareCompressionCopy(engine=_compressionEngineForSession(), mode=_compressionModeForSession()){
  if(engine==='lcm'||mode==='lossless_retrieval'){
    return {
      label:t('retrieval_context_label'),
      preview:t('retrieval_context_preview'),
    };
  }
  return {
    label:t('context_compaction_label'),
    preview:t('reference_only_label'),
  };
}
function _compressionStatusCardHtml({
  statusLabel,
  previewText,
  detail,
  icon,
  open=false,
  variantClass='',
}){
  const statusDetail = String(detail || '').trim();
  const hasBody = !!statusDetail;
  const openClass = open ? ' open' : '';
  const statusIcon = icon;
  const bodyHtml = hasBody ? `<div class="tool-card-detail"><div class="tool-card-result"><pre>${esc(statusDetail)}</pre></div></div>` : '';
  const toggleHtml = hasBody ? `<span class="tool-card-toggle">${li('chevron-right',12)}</span>` : '';
  return `
    <div class="tool-card ${variantClass}${openClass}">
      <div class="tool-card-header" onclick="this.closest('.tool-card').classList.toggle('open')">
        ${statusIcon}
        <span class="tool-card-name">${esc(statusLabel)}</span>
        <span class="tool-card-preview">${esc(previewText)}</span>
        ${toggleHtml}
      </div>
      ${bodyHtml}
    </div>`;
}
function _handoffStateForCurrentSession(){
  const state=window._handoffUi;
  if(!state||!S.session||state.sessionId!==S.session.session_id) return null;
  return state;
}
function clearHandoffUi(){
  window._handoffUi=null;
  _renderMessagesWithScrollSnapshot();
}
function setHandoffUi(state){
  if(!state){
    clearHandoffUi();
    return;
  }
  window._handoffUi={...state};
  _renderMessagesWithScrollSnapshot();
}
function _handoffCardsHtml(state){
  if(!state) return '';
  const channel=String(state.channel||'').trim();
  const label=channel?`${channel} handoff summary`:'Handoff summary';
  const isError=state.phase==='error';
  const isDone=state.phase==='done';
  const isFallback=!!state.fallback;
  const detail=isError
    ? String(state.errorText||'Could not generate summary. Please try again.')
    : isDone
      ? String(state.summary||'')
      : 'Generating handoff summary...';
  const meta=typeof state.rounds==='number'
    ? `${state.rounds} external conversation rounds`
    : '';
  const icon=isError
    ? li('x',13)
    : isDone
      ? li('check',13)
      : '<span class="tool-card-running-dot"></span>';
  const bodyHtml=isDone&&!isError
    ? (
      `${renderMd(detail)}${
        isFallback
          ? '<p class="handoff-summary-fallback-note">Fallback summary generated from recent turns; no model-based rewrite was used.</p>'
          : ''
      }`
    )
    : `<p>${esc(detail)}</p>`;
  return `
    <div class="tool-card-row compression-card-row handoff-card-row" data-compression-card="1" data-handoff-card="1">
      <div class="tool-card tool-card-handoff-summary${isError?' tool-card-compress-error':''} open">
        <div class="tool-card-header" onclick="this.closest('.tool-card').classList.toggle('open')">
          ${icon}
          <span class="tool-card-name">${esc(label)}</span>
          ${meta?`<span class="tool-card-preview">${esc(meta)}</span>`:''}
          <span class="tool-card-toggle">${li('chevron-right',12)}</span>
        </div>
        <div class="tool-card-detail">
          <div class="tool-card-result handoff-summary-body">${bodyHtml}</div>
        </div>
      </div>
    </div>`;
}
function _handoffCardsNode(state){
  const wrap=document.createElement('div');
  wrap.className='compression-turn handoff-turn';
  wrap.innerHTML=`<div class="compression-turn-blocks">${_handoffCardsHtml(state)}</div>`;
  return wrap;
}
function _contextCompactionMessageHtml(m, tsTitle='', preservedMessages=[]){
  const text=msgContent(m)||String(m.content||'');
  return `<div class="compression-turn"><div class="compression-turn-blocks">${_compressionReferenceCardHtml(text, false, tsTitle)}${_preservedCompressionTaskListCardsHtml(preservedMessages)}</div></div>`;
}
function renderCompressionUi(){
  const el=$('liveCompressionCards');
  if(!el) return;
  el.innerHTML='';
  el.style.display='none';
}
// Session render cache: avoids full markdown+DOM rebuild when switching back
// to a session whose rendered transcript inputs are unchanged.
// Keyed by session_id. Only used on cross-session navigation, never for
// in-session updates (new messages, edits, stream events).
const _sessionHtmlCache=new Map();
let _sessionHtmlCacheSid=null; // session_id currently rendered in the DOM
// #5966 (Codex F3): persist which capped Transparent-Stream turns the user has
// revealed, keyed by `${session_id}:${ownerRawIdx}`, so a switch-away/back or a
// normal rebuild does NOT silently re-cap a turn the user already expanded. The
// DOM `data-transparent-earlier-revealed` flag alone is lost across the
// _sessionHtmlCache innerHTML round-trip; this survives it. Reveal also
// invalidates that session's cached HTML so the stored markup isn't stale-capped.
const _transparentRevealedTurns=new Set();
function _transparentRevealKey(sessionId, ownerIdx){
  return String(sessionId||(S.session&&S.session.session_id)||'')+':'+String(ownerIdx);
}
function clearMessageRenderCache(){
  _clearRenderCache();
  _sessionHtmlCache.clear();
  _sessionHtmlCacheSid=null;
  clearVisibleMessageRowCache();
  _clearMessageVirtualHeightCache();
}

// #6999: feed a structured payload field's string form through the FNV-1a
// loop IN FULL, without materializing clipped copies or skipping the middle.
// The previous length+head+tail clip made same-length middle-only edits
// (tool arguments, attachment metadata, tool snippets, compression-anchor
// keys) produce identical signatures — a deterministic stale-cache collision
// in _sessionHtmlCache (cross-session navigation served old HTML). Hashing
// every character keeps the signature sensitive to ANY content change at a
// constant-allocation cost: strings are streamed char-by-char (no copy) and
// object fields are walked key-by-key so only scalar string forms are ever
// allocated — no integral JSON.stringify() of a whole payload, and no
// head/tail slice copies. _renderCacheKey's length+edges shortcut is only
// safe for the render-window geometry key, where equal span+edges means
// equal window; here equal signature must mean equal CONTENT.
function _addBoundedHash(add, value, depth){
  if(value==null){ add('null'); return; }
  const t=typeof value;
  if(t==='string'){ add(value.length); add(value); return; }
  if(t==='number'||t==='boolean'){ add(t); add(value); return; }
  if(t==='object'){ _hashObjectInto(add, value, (depth||0)+1); return; }
  add(t); add(String(value));
}
function _hashObjectInto(add, value, depth){
  if(value==null){ add('null'); return; }
  if(depth>64){
    // Pathological depth (e.g. a cyclic structure JSON.stringify would also
    // reject): serialize integrally so no field is silently dropped — the
    // exact same data still yields the exact same signature.
    try{ add(JSON.stringify(value)); }catch(e){ add('[unserializable]'); }
    return;
  }
  if(Array.isArray(value)){
    add('array'); add(value.length);
    for(let i=0;i<value.length;i++){ add(i); _addBoundedHash(add, value[i], depth); }
    return;
  }
  add('object');
  // #6999 re-gate: walk keys in INSERTION ORDER (never sorted) so the cache
  // signature equals the rendered projection — the tool-detail render paths
  // use Object.entries(tc.args), which preserves insertion order. Sorting here
  // gave opposite-insertion-order argument objects the same signature while
  // they render DIFFERENT HTML. The explicit per-key index is the
  // insertion-order discriminator: {alpha:A, beta:B} and {beta:B, alpha:A}
  // now hash differently.
  const keys=Object.keys(value);
  add(keys.length);
  for(let i=0;i<keys.length;i++){ add(keys[i]); add(i); _addBoundedHash(add, value[keys[i]], depth); }
}

function _messageRenderCacheSignature(){
  let hash=2166136261;
  function add(value){
    const s=String(value==null?'':value);
    for(let i=0;i<s.length;i++){
      hash^=s.charCodeAt(i);
      hash=Math.imul(hash,16777619)>>>0;
    }
    hash^=31;
    hash=Math.imul(hash,16777619)>>>0;
  }
  const messages=Array.isArray(S.messages)?S.messages:[];
  add(messages.length);
  for(const m of messages){
    if(!m||typeof m!=='object'){ add('missing'); continue; }
    add(m.role);add(m.timestamp);add(m._ts);add(m._error);add(m._statusCard);
    add(msgContent(m));
    if(Array.isArray(m.content)){
      add('content-array');
      m.content.forEach(part=>{
        if(!part||typeof part!=='object'){ add(part); return; }
        add(part.type);add(part.id);add(part.name);add(part.text);add(part.content);
      });
    }
    if(Array.isArray(m.tool_calls)){
      add('message-tool-calls');add(m.tool_calls.length);
      m.tool_calls.forEach(tc=>{
        add(tc&&tc.id);add(tc&&tc.name);add(tc&&tc.type);
        add(tc&&tc.function&&tc.function.name);
        // function.arguments is already a string — streamed in full, no copy.
        _addBoundedHash(add, tc&&tc.function&&tc.function.arguments);
      });
    }
    if(Array.isArray(m._partial_tool_calls)){
      add('partial-tool-calls');add(m._partial_tool_calls.length);
      m._partial_tool_calls.forEach(tc=>{add(tc&&tc.id);add(tc&&tc.name);add(tc&&tc.snippet);});
    }
    if(_messageHasReasoningPayload(m)) add(m.reasoning||m.thinking||m._reasoning||'reasoning');
    if(Array.isArray(m.attachments)) m.attachments.forEach(a=>_addBoundedHash(add, a));
  }
  const toolCalls=Array.isArray(S.toolCalls)?S.toolCalls:[];
  add('settled-tool-calls');add(toolCalls.length);
  toolCalls.forEach(tc=>{
    if(!tc||typeof tc!=='object'){ add(tc); return; }
    add(tc.tid);add(tc.id);add(tc.name);add(tc.done);add(tc.is_diff);add(tc.assistant_msg_idx);
    _addBoundedHash(add, tc.snippet);
    _addBoundedHash(add, tc.args||{});
  });
  if(S.session){
    add(S.session.message_count);add(S.session.updated_at);add(S.session.compression_anchor_visible_idx);
    _addBoundedHash(add, S.session.compression_anchor_message_key||null);
    add(S.session.compression_anchor_summary||'');
  }
  return `${messages.length}:${toolCalls.length}:${hash.toString(16)}`;
}

function _clipCliToolSnippet(text, maxLen=20000){
  const s=String(text||'');
  if(s.length<=maxLen) return s;
  return `${s.slice(0,maxLen)}\n\n... truncated ${s.length-maxLen} chars ...`;
}

function _cliToolResultText(raw){
  const s=String(raw||'');
  try{
    const rd=JSON.parse(s);
    if(rd && typeof rd==='object'){
      for(const key of ['output','result','error','content','diff','patch']){
        if(Object.prototype.hasOwnProperty.call(rd,key)){
          const v=rd[key];
          if(v==null) return '';
          return typeof v==='string' ? v : JSON.stringify(v,null,2);
        }
      }
    }
  }catch(e){}
  return s;
}

function _cliLooksLikePatchDiff(text){
  const s=String(text||'');
  if(!s) return false;
  if(/\*\*\* Begin Patch/.test(s)) return true;
  if(/^diff --git /m.test(s)) return true;
  if(/^@@\s/m.test(s)) return true;
  if(/(^|\n)---\s+/.test(s) && /(^|\n)\+\+\+\s+/.test(s)) return true;
  return false;
}

function _cliToolResultSnippet(raw){
  const fullText=_cliToolResultText(raw);
  if(_cliLooksLikePatchDiff(fullText)) return _clipCliToolSnippet(fullText);
  return String(fullText||'').slice(0,4000);
}

function _prefixedCliDiffLines(prefix, value){
  return String(value||'').split('\n').map(line=>`${prefix}${line}`).join('\n');
}

function _firstOwnedValue(obj, keys){
  for(const key of keys){
    if(obj && Object.prototype.hasOwnProperty.call(obj,key)) return obj[key];
  }
  return undefined;
}

function _cliPatchSnippetFromArgs(name, args){
  if(!args || typeof args!=='object') return '';
  const toolName=String(name||'').toLowerCase();
  for(const key of ['patch','diff']){
    const v=args[key];
    if(typeof v==='string' && v.trim()) return _clipCliToolSnippet(v);
  }
  for(const key of ['input','content']){
    const v=args[key];
    if(typeof v==='string' && _cliLooksLikePatchDiff(v)) return _clipCliToolSnippet(v);
  }
  const isEditLike=toolName==='apply_patch'
    || toolName==='patch'
    || toolName.includes('edit')
    || toolName==='replace'
    || toolName==='str_replace';
  if(!isEditLike) return '';
  const oldValue=_firstOwnedValue(args,['old_string','old_str','old','before']);
  const newValue=_firstOwnedValue(args,['new_string','new_str','new','after']);
  if(oldValue!==undefined || newValue!==undefined){
    const path=String(_firstOwnedValue(args,['file_path','path','filename'])||'');
    const lines=[];
    if(path) lines.push(path);
    if(oldValue!==undefined) lines.push(_prefixedCliDiffLines('-', oldValue));
    if(newValue!==undefined) lines.push(_prefixedCliDiffLines('+', newValue));
    return _clipCliToolSnippet(lines.join('\n'));
  }
  if(Array.isArray(args.edits)){
    const path=String(_firstOwnedValue(args,['file_path','path','filename'])||'');
    const chunks=[];
    if(path) chunks.push(path);
    args.edits.slice(0,5).forEach(edit=>{
      if(!edit || typeof edit!=='object') return;
      const before=_firstOwnedValue(edit,['old_string','old_str','old','before']);
      const after=_firstOwnedValue(edit,['new_string','new_str','new','after']);
      if(before!==undefined) chunks.push(_prefixedCliDiffLines('-', before));
      if(after!==undefined) chunks.push(_prefixedCliDiffLines('+', after));
    });
    if(chunks.length) return _clipCliToolSnippet(chunks.join('\n'));
  }
  return '';
}

function _cliToolCardSnippet(resultSnippet, patchSnippet){
  if(_cliLooksLikePatchDiff(resultSnippet)) return resultSnippet;
  if(!patchSnippet) return resultSnippet || '';
  const result=String(resultSnippet||'').trim();
  if(!result) return patchSnippet;
  const generic=/^(success|ok|done|done\.|exit code: 0)$/i.test(result);
  if(generic) return patchSnippet;
  return `${resultSnippet}\n\n${patchSnippet}`;
}

function _cliToolCardHasDiffSnippet(resultSnippet, patchSnippet){
  return !!patchSnippet || _cliLooksLikePatchDiff(resultSnippet);
}

function _assistantToolAnchorIdxForMessage(messages, rawIdx){
  const list=Array.isArray(messages)?messages:[];
  const current=list[rawIdx];
  if(_assistantMessageHasVisibleContent(current)) return rawIdx;
  if(_assistantReasoningPayloadText(current)) return rawIdx;
  for(let idx=rawIdx-1;idx>=0;idx--){
    if(_assistantMessageHasVisibleContent(list[idx])) return idx;
  }
  return rawIdx;
}
function _toolArgsSnapshot(args, limit){
  if(!args||typeof args!=='object'||Array.isArray(args)) return {};
  const max=Math.max(1,Number(limit)||6);
  const priority=[
    'query','search_query','searchQuery','pattern','q','keyword','keywords','term',
    'url','uri','command','cmd','path','file','file_path','filename','file_glob',
    'glob','offset','limit',
  ];
  // Content / diff-reconstruction keys must not be capped to the short
  // incidental-arg limit, or long commands/paths get cut and recovery-rebuilt
  // diffs (built from old_string/new_string/patch) break (#4928). Mirrors the
  // backend _TOOL_ARG_CONTENT_KEYS / _TOOL_ARG_CONTENT_CAP.
  const contentKeys=new Set(['command','cmd','script','code','patch','diff','old_string','new_string','content','path','file_path']);
  const CONTENT_CAP=4000;
  const keys=[
    ...priority.filter(k=>Object.prototype.hasOwnProperty.call(args,k)),
    ...Object.keys(args).filter(k=>!priority.includes(k)),
  ].slice(0,max);
  const out={};
  keys.forEach(k=>{
    const v=String(args[k]);
    const cap=contentKeys.has(String(k).toLowerCase())?CONTENT_CAP:120;
    let val=v.slice(0,cap)+(v.length>cap?'...':'');
    // Now that content args are retained up to 4000 chars (#4928), a secret on
    // a non-first line / past char 120 would otherwise reach the args block,
    // the Full tab, and clipboard copy unredacted. Redact at the snapshot so
    // every downstream renderer receives already-masked args (#4928 gate).
    if(typeof _redactToolTargetLabel==='function'){ try{ val=_redactToolTargetLabel(val); }catch(e){} }
    out[k]=val;
  });
  return out;
}

function _idLinkedHistoricalMessageText(message){
  if(!message||typeof message!=='object') return '';
  const content=message.content;
  if(typeof content==='string') return content;
  if(!Array.isArray(content)) return '';
  return content.filter(part=>part&&typeof part==='object'&&part.type==='text').map(part=>{
    if(!part||typeof part!=='object') return '';
    return String(part.text||part.content||'');
  }).join('\n');
}

function _idLinkedHistoricalMessageHasVisibleText(message){
  return _idLinkedHistoricalMessageText(message).trim()!=='';
}

function _idLinkedHistoricalMessageRef(message, rawIdx){
  if(message&&typeof message==='object'){
    for(const key of ['message_id','id','local_id']){
      const value=message[key];
      if(typeof value==='string'&&value.trim()) return value.trim();
      if(typeof value==='number'&&Number.isFinite(value)) return String(value);
    }
  }
  return `raw_idx:${rawIdx}`;
}

function _idLinkedHistoricalToolArguments(toolCall){
  if(!toolCall||typeof toolCall!=='object') return null;
  const fn=toolCall.function;
  if(!fn||typeof fn!=='object'||Array.isArray(fn)) return null;
  const raw=fn.arguments;
  if(raw===undefined||raw===null||raw==='') return null;
  if(raw&&typeof raw==='object'&&!Array.isArray(raw)) return raw;
  if(typeof raw!=='string') return null;
  try{
    const parsed=JSON.parse(raw);
    return parsed&&typeof parsed==='object'&&!Array.isArray(parsed)?parsed:null;
  }catch(e){
    return null;
  }
}

function _idLinkedHistoricalToolResultRaw(message){
  if(!message||typeof message!=='object') return null;
  const content=message.content;
  return typeof content==='string'?content:null;
}

function _idLinkedHistoricalRedactSnippet(value){
  let text=String(value||'');
  if(!text) return '';
  if(typeof _redactToolTargetLabel==='function'){
    try{text=_redactToolTargetLabel(text);}
    catch(e){}
  }
  return text;
}

function _idLinkedHistoricalHasVisibleSidecar(message){
  if(!message||typeof message!=='object') return false;
  const visibleKeys=['attachments','_attachments','_statusCard','status_card','statusCard','card','cards','artifact','artifacts','files','images','media'];
  for(const key of visibleKeys){
    if(!Object.prototype.hasOwnProperty.call(message,key)) continue;
    const value=message[key];
    if(value===undefined||value===null||value===false) continue;
    if(Array.isArray(value)&&value.length===0) continue;
    if(typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).length===0) continue;
    return true;
  }
  return false;
}

// Claim legacy settled ownership only when the transcript itself proves a
// complete, user-bounded declaration/result/final-answer chain.
function _idLinkedHistoricalTurnScene(messages, turnStart, turnEnd, options){
  const list=Array.isArray(messages)?messages:[];
  const start=Math.max(0,Number(turnStart)||0);
  const end=Math.min(list.length,Math.max(start,Number(turnEnd)||0));
  const opts=options&&typeof options==='object'?options:{};
  const sessionId=String(opts.sessionId||opts.session_id||'').trim();
  const api=(typeof window!=='undefined')?window.AgyAssistantTurnAnchors:null;
  if(!sessionId||!api||typeof api.projectAssistantTurnAnchorHistoricalTranscriptScene!=='function') return null;

  const declarations=[];
  const declarationIds=new Set();
  const declarationRefs=[];
  const visibleAssistantIndexes=[];
  const assistantIndexes=[];
  const resultsById=new Map();
  for(let rawIdx=start;rawIdx<end;rawIdx++){
    const message=list[rawIdx];
    if(!message||typeof message!=='object') continue;
    const role=message.role;
    if(role==='user'&&rawIdx===start) continue;
    if(message._anchor_activity_scene) return null;
    if(role==='assistant'){
      assistantIndexes.push(rawIdx);
      const hasVisibleText=_idLinkedHistoricalMessageHasVisibleText(message);
      const reasoningText=_assistantReasoningPayloadText(message);
      if(hasVisibleText) visibleAssistantIndexes.push(rawIdx);
      if(reasoningText) return null;
      if(_idLinkedHistoricalHasVisibleSidecar(message)) return null;
      if(Array.isArray(message._partial_tool_calls)&&message._partial_tool_calls.length) return null;
      if(Array.isArray(message.content)&&message.content.some(part=>part&&typeof part==='object'&&part.type==='tool_use')) return null;
      const toolCalls=Array.isArray(message.tool_calls)?message.tool_calls:[];
      if(toolCalls.length&&hasVisibleText) return null;
      if(!toolCalls.length){
        if(hasVisibleText) continue;
        return null;
      }
      if(message.content!==undefined&&message.content!==null&&message.content!=='') return null;
      const messageRef=_idLinkedHistoricalMessageRef(message,rawIdx);
      if(!declarationRefs.includes(messageRef)) declarationRefs.push(messageRef);
      for(const toolCall of toolCalls){
        const callId=String(toolCall&&toolCall.id||'').trim();
        const fn=toolCall&&toolCall.function;
        const name=String(fn&&fn.name||'').trim();
        const args=_idLinkedHistoricalToolArguments(toolCall);
        if(!callId||!name||args===null||declarationIds.has(callId)) return null;
        declarationIds.add(callId);
        declarations.push({callId,name,args,rawIdx,messageRef});
      }
      continue;
    }
    if(role!=='tool') return null;
    const callId=String(message.tool_call_id||'').trim();
    if(!callId||!declarationIds.has(callId)) return null;
    const matches=resultsById.get(callId)||[];
    matches.push({message,rawIdx});
    resultsById.set(callId,matches);
  }

  if(!declarations.length||visibleAssistantIndexes.length!==1) return null;
  const ownerIndex=visibleAssistantIndexes[0];
  if(ownerIndex!==assistantIndexes[assistantIndexes.length-1]) return null;
  const owner=list[ownerIndex];
  if(Array.isArray(owner.tool_calls)&&owner.tool_calls.length) return null;
  const ownerRef=_idLinkedHistoricalMessageRef(owner,ownerIndex);
  for(const declaration of declarations){
    const matches=resultsById.get(declaration.callId)||[];
    if(matches.length!==1||matches[0].rawIdx<=declaration.rawIdx||matches[0].rawIdx>=ownerIndex) return null;
  }
  if(resultsById.size!==declarations.length) return null;

  const sourceRefs=declarationRefs.concat(ownerRef).filter((value,index,array)=>array.indexOf(value)===index);
  const turnId=['historical',sessionId,declarationRefs[0],ownerRef].join(':');
  const activityEvents=[];
  for(let index=0;index<declarations.length;index++){
    const declaration=declarations[index];
    const resultEntry=resultsById.get(declaration.callId)[0];
    const args=_toolArgsSnapshot(declaration.args);
    const resultRaw=_idLinkedHistoricalToolResultRaw(resultEntry.message);
    if(resultRaw===null) return null;
    const resultSnippet=_idLinkedHistoricalRedactSnippet(_cliToolResultSnippet(resultRaw));
    const patchSnippet=_cliPatchSnippetFromArgs(declaration.name,args);
    const isDiff=_cliToolCardHasDiffSnippet(resultSnippet,patchSnippet);
    const snippet=_idLinkedHistoricalRedactSnippet(_cliToolCardSnippet(resultSnippet,patchSnippet));
    const status=String(resultEntry.message.status||'').trim().toLowerCase();
    const isError=resultEntry.message.is_error===true||status==='error'||status==='failed'||status==='failure';
    activityEvents.push({
      source_type:'tool_complete',
      seq:index+1,
      local_id:`historical:${declaration.messageRef}:tool:${declaration.callId}`,
      payload:{
        id:declaration.callId,
        tid:declaration.callId,
        tool_call_id:declaration.callId,
        name:declaration.name,
        args,
        command:String(args.command||args.cmd||''),
        snippet,
        done:true,
        is_error:isError,
        is_diff:isDiff,
        assistant_msg_idx:declaration.rawIdx,
      },
    });
  }
  let scene;
  try{
    scene=api.projectAssistantTurnAnchorHistoricalTranscriptScene({
      session_id:sessionId,
      turn_id:turnId,
      local_id:ownerRef,
      source_message_refs:sourceRefs,
      activity_events:activityEvents,
      settled_message:{role:'assistant',id:ownerRef,content:_idLinkedHistoricalMessageText(owner)},
    },{mode:opts.mode||'compact_worklog'});
  }catch(e){
    return null;
  }
  if(!scene||scene.version!=='activity_scene_v1'||scene.activity_rows.length!==declarations.length) return null;
  return {ownerIndex,scene};
}

function _hydrateIdLinkedHistoricalToolScenes(messages, options){
  const list=Array.isArray(messages)?messages:[];
  let turnStart=-1;
  let hydrated=0;
  const hydrateTurn=(turnEnd)=>{
    if(turnStart<0||turnEnd<=turnStart+1) return;
    let hydratedTurn;
    try{hydratedTurn=_idLinkedHistoricalTurnScene(list,turnStart,turnEnd,options);}
    catch(e){return;}
    if(!hydratedTurn) return;
    const owner=list[hydratedTurn.ownerIndex];
    try{owner._anchor_activity_scene=hydratedTurn.scene;}
    catch(e){return;}
    if(owner._anchor_activity_scene===hydratedTurn.scene) hydrated+=1;
  };
  for(let rawIdx=0;rawIdx<list.length;rawIdx++){
    const message=list[rawIdx];
    if(!message||message.role!=='user') continue;
    hydrateTurn(rawIdx);
    turnStart=rawIdx;
  }
  hydrateTurn(list.length);
  return hydrated;
}

function _captureMessageScrollSnapshot(){
  const el=$('messages');
  if(!el) return null;
  const bottom=Math.max(0,el.scrollHeight-el.scrollTop-el.clientHeight);
  const readerAwayFromBottom=bottom>250&&(
    _messageUserUnpinned ||
    _scrollPinned===false ||
    (typeof _recentMessageScrollIntent==='function'&&_recentMessageScrollIntent())
  );
  return {
    anchor:(typeof _captureMessageViewportAnchor==='function')?_captureMessageViewportAnchor():null,
    top:el.scrollTop,
    bottom,
    scrollHeight:el.scrollHeight,
    inputGeneration:typeof _messageScrollInputGeneration==='number' ? _messageScrollInputGeneration : 0,
    pinned:readerAwayFromBottom?false:_shouldFollowMessagesOnDomReplace(),
    userUnpinned:readerAwayFromBottom?true:_messageUserUnpinned,
  };
}
function _messageScrollSnapshotInputChanged(snapshot){
  if(!snapshot) return false;
  const captured=Number(snapshot.inputGeneration);
  const current=typeof _messageScrollInputGeneration==='number' ? _messageScrollInputGeneration : captured;
  return Number.isFinite(captured)&&Number.isFinite(current)&&current!==captured;
}
function _abandonMessageScrollSnapshot(){
  const el=$('messages');
  if(!el){
    _messageUserUnpinned=true;
    _scrollPinned=false;
    _nearBottomCount=0;
    return;
  }
  _lastScrollTop=el.scrollTop||0;
  _lastMessageClientHeight=el.clientHeight||0;
  // A generation mismatch abandons only the stale snapshot write. Reconcile
  // ownership from the live viewport so a reader who moved down to the true
  // bottom is immediately re-pinned instead of being stranded sticky-unpinned.
  const bottomDistance=el.scrollHeight-el.scrollTop-el.clientHeight;
  if(bottomDistance<=80){
    _messageUserUnpinned=false;
    _scrollPinned=true;
    _nearBottomCount=2;
  }else{
    _messageUserUnpinned=true;
    _scrollPinned=false;
    _nearBottomCount=0;
  }
}
function _restorePinnedMessageScrollSnapshot(snapshot){
  const el=$('messages');
  if(!el||!snapshot||snapshot.pinned!==true||snapshot.userUnpinned===true) return false;
  const maxTop=Math.max(0,el.scrollHeight-el.clientHeight);
  const bottom=Number(snapshot.bottom);
  const target=Number.isFinite(bottom)?maxTop-Math.max(0,bottom):maxTop;
  _programmaticScroll=true;_programmaticScrollSetAt=performance.now();
  el.scrollTop=Math.max(0,Math.min(target,maxTop));
  // Sync _lastScrollTop after programmatic restore so sticky-unpin does not false-trigger (#1731).
  _lastScrollTop=el.scrollTop;_lastMessageClientHeight=el.clientHeight;
  _messageUserUnpinned=false;
  _scrollPinned=true;
  _nearBottomCount=2;
  if(typeof _deferClearProgrammaticScroll==='function') _deferClearProgrammaticScroll();
  else requestAnimationFrame(()=>{ setTimeout(()=>{ _programmaticScroll=false; },0); });
  return true;
}
function _restoreMessageScrollSnapshot(snapshot){
  const el=$('messages');
  if(!el||!snapshot) return;
  const maxTop=Math.max(0,el.scrollHeight-el.clientHeight);
  // If the reader was following the live tail, preserve the tail-relative bottom
  // distance. Do not semantic-anchor to the first visible row: live Worklog/
  // activity rebuilds can remount an older top-of-viewport anchor and yank a
  // pinned streaming transcript upward. Semantic anchors remain for manual
  // unpinned reading positions below.
  if(typeof _messageScrollSnapshotInputChanged==='function'&&_messageScrollSnapshotInputChanged(snapshot)){
    if(typeof _abandonMessageScrollSnapshot==='function') _abandonMessageScrollSnapshot();
    return;
  }
  if(_restorePinnedMessageScrollSnapshot(snapshot)) return;
  let restoredViaAnchor=(snapshot.anchor&&typeof _restoreMessageViewportAnchor==='function')
    ? _restoreMessageViewportAnchor(snapshot.anchor,0)
    : false;
  if(!restoredViaAnchor&&typeof _remountMessageViewportAnchor==='function'&&_remountMessageViewportAnchor(snapshot.anchor)){
    restoredViaAnchor=(typeof _restoreMessageViewportAnchor==='function')
      ? _restoreMessageViewportAnchor(snapshot.anchor,0)
      : false;
  }
  if(!restoredViaAnchor){
    _programmaticScroll=true;_programmaticScrollSetAt=performance.now();
    el.scrollTop=Math.max(0,Math.min(Number(snapshot.top)||0,maxTop));
  }
  // Sync _lastScrollTop after programmatic restore so sticky-unpin does not false-trigger (#1731).
  _lastScrollTop=el.scrollTop;_lastMessageClientHeight=el.clientHeight;
  if(snapshot.userUnpinned===true){
    _messageUserUnpinned=true;
    _scrollPinned=false;
    _nearBottomCount=0;
  }else if(snapshot.pinned===true){
    _messageUserUnpinned=false;
    _scrollPinned=true;
    _nearBottomCount=2;
  }else{
    const bottomDistance=el.scrollHeight-el.scrollTop-el.clientHeight;
    if(bottomDistance>250){
      _messageUserUnpinned=true;
      _scrollPinned=false;
      _nearBottomCount=0;
    }else if(bottomDistance<=120){
      _messageUserUnpinned=false;
      _scrollPinned=true;
      _nearBottomCount=2;
    }
  }
  if(!restoredViaAnchor){
    if(typeof _deferClearProgrammaticScroll==='function') _deferClearProgrammaticScroll();
    else requestAnimationFrame(()=>{ setTimeout(()=>{ _programmaticScroll=false; },0); });
  }
}
/**
 * Mobile scroll-jank guard: temporarily disable overflow-anchor so
 * Chromium cannot re-anchor to the topmost row during the innerHTML=''
 * wipe-and-rebuild gap. The rAF callback restores CSS default afterward.
 */
// Mobile scroll jump-back root fix. On touch devices #messages rests at
// overflow-anchor:auto, so the browser's native scroll-anchoring engine
// re-compensates scrollTop in the LAYOUT phase whenever content above the
// viewport changes height — worklog live→settled collapse, tool-card inserts,
// media/katex reflow, virtual-scroll topPad recompute, the STREAM_DONE
// multi-render sequence. That compensation happens in the browser's layout step,
// INDEPENDENT of which frame our JS wrote scrollTop in, so per-write suppression
// (a single-rAF guard) could not reach it: the collapse/reflow lands a frame or
// two later, after the guard already released. Real mobile flight-recorder data
// (captured jumps with dTop -101/+350/+748/-400, call stack = rAF sampler only =
// NO JS frame) confirmed the compensation is the browser engine, not our scroll
// writes.
//
// Fix: DEFER the restore AND track CSS animations. Each call re-arms suppression
// and cancels any pending release, so a burst of renders (STREAM_DONE fires
// several back-to-back) shares ONE suppression window. The base window is two
// animation frames + a settle timeout, which covers churn that is NOT a CSS
// animation (virtual topPad recompute, image-decode, katex measure). But the
// dominant churn is CSS max-height collapse/expand animations on worklog rows —
// .activity-body (.34s), .tool-group-body (.3s), .tool-card-detail (.26s) — which
// run LONGER than a fixed window; a fixed window lifts mid-animation and the rest
// of the animation still jumps. So we also bind transitionrun/transitionend on
// #messages: an animation start holds suppression (cancels the pending release);
// an animation end schedules a short settle after the LAST one. Hard-capped so a
// looping transition can't pin overflow-anchor:none forever. Desktop rests at
// none (predicate false) → the whole guard is a no-op.
const _MOBILE_ANCHOR_BASE_SETTLE_MS=400;
const _MOBILE_ANCHOR_POST_TRANSITION_MS=90;
const _MOBILE_ANCHOR_MAX_HOLD_MS=1200;
let _mobileAnchorSuppressReleaseTimer=null;
let _mobileAnchorSuppressRafId=0;
let _mobileAnchorTransitionListenerBound=false;
let _mobileAnchorSuppressArmedAt=0;
// Independent hard-cap timer. Unlike the settle/rAF release (which the
// transitionrun handler CANCELS to hold across an animation), this one is NEVER
// cancelled by re-arm or by onRun — it is only ever cleared when suppression is
// actually lifted, and re-armed to a fresh deadline on each _fixMobileScrollJank
// call. This guarantees overflow-anchor returns to the mobile resting 'auto'
// even if EVERY transitionend/transitioncancel is missed (animation interrupted,
// element detached mid-transition, etc.) — the #5338 contract that mobile rests
// at 'auto' must hold no matter what. (Gate-cert defect: the previous
// _MOBILE_ANCHOR_MAX_HOLD_MS was only a guard clause inside onRun, so a missed
// transitionend pinned 'none' forever.)
let _mobileAnchorMaxHoldTimer=null;
function _liftMobileAnchorSuppression(el){
  if(_mobileAnchorSuppressReleaseTimer){ clearTimeout(_mobileAnchorSuppressReleaseTimer); _mobileAnchorSuppressReleaseTimer=null; }
  if(_mobileAnchorMaxHoldTimer){ clearTimeout(_mobileAnchorMaxHoldTimer); _mobileAnchorMaxHoldTimer=null; }
  if(_mobileAnchorSuppressRafId&&typeof cancelAnimationFrame==='function'){ cancelAnimationFrame(_mobileAnchorSuppressRafId); }
  _mobileAnchorSuppressRafId=0;
  // Only clear the inline value we set; a concurrent path may have legitimately
  // re-armed it (checked via the 'none' guard).
  if(el&&el.style&&el.style.overflowAnchor==='none') el.style.overflowAnchor='';
}
function _bindMobileAnchorTransitionExtender(el){
  if(_mobileAnchorTransitionListenerBound||!el||!el.addEventListener) return;
  _mobileAnchorTransitionListenerBound=true;
  // Only act while suppression is actually armed (inline 'none') and within the
  // hard cap, so we never pin overflow-anchor:none indefinitely.
  const onRun=(e)=>{
    if(!e||e.propertyName!=='max-height') return;
    if(el.style.overflowAnchor!=='none') return;
    if(_mobileAnchorSuppressArmedAt && (performance.now()-_mobileAnchorSuppressArmedAt)>_MOBILE_ANCHOR_MAX_HOLD_MS) return;
    // An animation is running — cancel the pending SETTLE release so we stay
    // suppressed until it ends (transitionend re-schedules the settle). The
    // independent max-hold timer is deliberately NOT cancelled here.
    if(_mobileAnchorSuppressReleaseTimer){ clearTimeout(_mobileAnchorSuppressReleaseTimer); _mobileAnchorSuppressReleaseTimer=null; }
    if(_mobileAnchorSuppressRafId&&typeof cancelAnimationFrame==='function'){ cancelAnimationFrame(_mobileAnchorSuppressRafId); }
    _mobileAnchorSuppressRafId=0;
  };
  const onEnd=(e)=>{
    if(!e||e.propertyName!=='max-height') return;
    if(el.style.overflowAnchor!=='none') return;
    // This animation ended; settle shortly after (another may still be running,
    // in which case its own transitionrun already cancelled this timer).
    if(_mobileAnchorSuppressReleaseTimer){ clearTimeout(_mobileAnchorSuppressReleaseTimer); }
    _mobileAnchorSuppressReleaseTimer=setTimeout(()=>{
      _mobileAnchorSuppressReleaseTimer=null;
      _liftMobileAnchorSuppression(el);
    },_MOBILE_ANCHOR_POST_TRANSITION_MS);
  };
  el.addEventListener('transitionrun',onRun,{passive:true});
  el.addEventListener('transitionstart',onRun,{passive:true});
  el.addEventListener('transitionend',onEnd,{passive:true});
  el.addEventListener('transitioncancel',onEnd,{passive:true});
}
window._fixMobileScrollJank=function _fixMobileScrollJank(){
  const el=document.getElementById('messages');
  if(!el) return;
  // Engage when the browser scroll-anchor layer is active (mobile auto), OR when
  // WE are already holding an inline suppression from a prior call in the same
  // burst. The predicate reads the COMPUTED value, which our own inline
  // overflow-anchor:none flips to 'none' — so on the 2nd..Nth call of a
  // STREAM_DONE burst the predicate would say false and short-circuit the re-arm
  // below, collapsing the whole "consecutive renders extend the window" behavior
  // to a single first-call window. Treat an inline 'none' WE set as still-armed
  // so re-arm actually runs. Desktop rests at computed 'none' with EMPTY inline,
  // so `alreadySuppressed` is false there and this stays a no-op. (Gate-cert
  // defect: re-arm was dead code without this.)
  const alreadySuppressed=el.style.overflowAnchor==='none';
  if(!alreadySuppressed && !_browserOverflowAnchorActive(el)) return;
  el.style.overflowAnchor='none';
  _bindMobileAnchorTransitionExtender(el);
  _mobileAnchorSuppressArmedAt=performance.now();
  // Re-arm: cancel any pending release so consecutive renders EXTEND, not shorten,
  // the suppression window (the STREAM_DONE settle fires renderMessages several
  // times back-to-back, plus a deferred postProcess reflow).
  if(_mobileAnchorSuppressReleaseTimer){ clearTimeout(_mobileAnchorSuppressReleaseTimer); _mobileAnchorSuppressReleaseTimer=null; }
  if(_mobileAnchorSuppressRafId&&typeof cancelAnimationFrame==='function'){ cancelAnimationFrame(_mobileAnchorSuppressRafId); }
  _mobileAnchorSuppressRafId=0;
  // Independent hard cap: (re)arm a release that NOTHING cancels except an actual
  // lift, so a missed transitionend can never pin 'none' past the cap.
  if(_mobileAnchorMaxHoldTimer){ clearTimeout(_mobileAnchorMaxHoldTimer); }
  _mobileAnchorMaxHoldTimer=setTimeout(()=>{
    _mobileAnchorMaxHoldTimer=null;
    _liftMobileAnchorSuppression(el);
  },_MOBILE_ANCHOR_MAX_HOLD_MS);
  const rafHop=(cb)=>{ if(typeof requestAnimationFrame==='function') return requestAnimationFrame(cb); return setTimeout(cb,16); };
  // Base window: two animation frames (paint + post-render reflow settle) THEN a
  // settle timeout. CSS max-height animations are covered by the transitionrun/
  // transitionend extender above; this floor covers non-animated churn.
  _mobileAnchorSuppressRafId=rafHop(()=>{
    _mobileAnchorSuppressRafId=rafHop(()=>{
      _mobileAnchorSuppressReleaseTimer=setTimeout(()=>{
        _mobileAnchorSuppressReleaseTimer=null;
        _liftMobileAnchorSuppression(el);
      },_MOBILE_ANCHOR_BASE_SETTLE_MS);
    });
  });
};

// Desktop stale-snapshot residue (issue #5637 follow-up). Reached only when
// _restoreMessageViewportAnchor already CONCEDED (anchor row unrecoverable by its
// per-tier lookup) and the desktop fallback would otherwise write the ABSOLUTE
// snapshot.top — which is stale once above-viewport content grew since capture,
// yanking a still reader backward. The correct hold is the app's own realign
// idiom: shift the CURRENT scrollTop by how far the anchor row moved since capture,
// `scrollTop += (currentOffset - capturedOffset)` (mirrors _restoreMessageViewportAnchor
// ui.js and _compensateScrollForMeasurementDelta). NOT `snapshot.top + delta`: a
// row's offset is scroll-relative (rect.top - containerRect.top = rowContentPos -
// scrollTop), so only a delta applied to the LIVE scrollTop holds the row put
// regardless of where scrollTop was carried to. Returns the realign delta (may be
// 0), or null when the anchor row can't be measured under the SAME per-tier guard
// _restoreMessageViewportAnchor uses (key -> sessionIdx, never the rawIdx
// degradation — rawIdx maps to a different message after a virtualization
// re-window, ui.js per-tier guard) so the caller can fall back to the topPad-delta
// idiom or keep raw rather than guessing.
function _desktopAnchorRealignDelta(container, anchor){
  if(!container||!anchor||typeof container.querySelector!=='function') return null;
  const capturedOffset=Number(anchor.topOffset);
  if(!Number.isFinite(capturedOffset)) return null;
  const anchorKey=String(anchor.key||'');
  let row=anchorKey
    ? Array.from(container.querySelectorAll('[data-message-anchor-key]')).find(el=>el&&el.dataset&&el.dataset.messageAnchorKey===anchorKey)
    : null;
  if(row&&row.getClientRects&&row.getClientRects().length===0) row=null;
  const sessionIdx=Number(anchor.sessionIdx);
  if(!row&&Number.isFinite(sessionIdx)) row=container.querySelector(`[data-session-msg-idx="${sessionIdx}"]`);
  // Per-tier guard mirror (ui.js _restoreMessageViewportAnchor): a genuinely-gone
  // anchor misses key AND sessionIdx -> concede (null). Do NOT degrade to rawIdx.
  if(!row) return null;
  if(typeof row.getBoundingClientRect!=='function') return null;
  if(row.getClientRects&&row.getClientRects().length===0) return null;
  const containerRect=container.getBoundingClientRect();
  const rect=row.getBoundingClientRect();
  const currentOffset=rect.top-containerRect.top;
  return currentOffset-capturedOffset;
}
function _restoreMessageScrollSnapshotSameFrame(snapshot){
  const el=$('messages');
  if(!el||!snapshot) return;
  // Same-frame live DOM updates (tool/worklog/activity rows) are the hot path for
  // streaming. Pinned followers must stay tail-relative here too; restoring the
  // semantic viewport anchor is only safe for explicitly unpinned readers.
  if(typeof _messageScrollSnapshotInputChanged==='function'&&_messageScrollSnapshotInputChanged(snapshot)){
    if(typeof _abandonMessageScrollSnapshot==='function') _abandonMessageScrollSnapshot();
    return;
  }
  if(_restorePinnedMessageScrollSnapshot(snapshot)) return;
  // A delayed rAF restore must not overwrite a position the reader changed
  // after capture. Recent-intent timestamps are lossy; the generation is
  // monotonic and therefore preserves snapshot ownership exactly.
  let restoredViaAnchor=(snapshot.anchor&&typeof _restoreMessageViewportAnchor==='function')
    ? _restoreMessageViewportAnchor(snapshot.anchor,0)
    : false;
  if(!restoredViaAnchor&&typeof _remountMessageViewportAnchor==='function'&&_remountMessageViewportAnchor(snapshot.anchor)){
    restoredViaAnchor=(typeof _restoreMessageViewportAnchor==='function')
      ? _restoreMessageViewportAnchor(snapshot.anchor,0)
      : false;
  }
  if(!restoredViaAnchor){
    const maxTop=Math.max(0,el.scrollHeight-el.clientHeight);
    const bottom=Number(snapshot.bottom);
    // Mobile/touch viewports have native overflow anchoring to hold an
    // unpinned reader across a rebuild. Desktop deliberately disables that
    // browser behavior, so it must continue into the explicit fallback below.
    const _fbTouchHold=(typeof _isTouchLikeMessageViewport==='function' && _isTouchLikeMessageViewport(el));
    // #5637: when the reader has scrolled UP into history (userUnpinned) and the
    // semantic anchor restore failed, do NOT snap scrollTop to the captured
    // ABSOLUTE snapshot.top. During streaming, the live activity-scene refresh
    // fires this every tick; above-viewport height keeps changing, so the old
    // absolute top no longer maps to the same content and the viewport is nudged
    // backward by an amount that grows with scrollHeight. Leaving scrollTop
    // untouched lets the browser's own scroll anchoring hold the reader's
    // position. Pinned / near-bottom readers still get the tail-relative restore
    // below (that path is correct and must run).
    if(snapshot.userUnpinned===true&&snapshot.pinned!==true&&_fbTouchHold){
      _lastScrollTop=el.scrollTop;_lastMessageClientHeight=el.clientHeight;
      _messageUserUnpinned=true;
      _scrollPinned=false;
      _nearBottomCount=0;
      return;
    }
    const target=(snapshot.pinned===true&&Number.isFinite(bottom))
      ? maxTop-Math.max(0,bottom)
      : Number(snapshot.top)||0;
    // Streaming stale-snapshot guard (issue #5637). The userUnpinned check above is
    // defeated when a live stream re-pins the state machine (a scrollHeight-collapse
    // scroll event flips userUnpinned back to false even though the reader is up in
    // history), so this absolute snapshot.top write still fires and yanks a still
    // reader — snapshot.top was captured before the streaming chunk grew above-viewport
    // height, so it is stale. Mirror the realign guard: if content grew since the
    // snapshot AND there is no recent real input intent AND the write would move
    // scrollTop non-trivially, refuse it and let the browser overflow-anchor hold.
    // Pinned tail-followers (target is bottom-relative, not snapshot.top) are
    // unaffected; an actively scrolling reader has intent and keeps the restore.
    //
    // Desktop guard (issue #5637 gate cert): like the realign guard, this refusal
    // only holds where the browser's native overflow-anchor layer is active (touch
    // viewports, `.messages` computes to `overflow-anchor:auto`). Desktop `.messages`
    // is `overflow-anchor:none`, so refusing the absolute fallback write there would
    // leave the reader unheld AND latch `_messageUserUnpinned=true`. Gate on
    // `_isTouchLikeMessageViewport` so desktop keeps its absolute snapshot.top restore.
    const _snapSH=Number(snapshot.scrollHeight);
    const _grewSinceSnap=Number.isFinite(_snapSH)&&_snapSH>0&&(el.scrollHeight-_snapSH)>4;
    const _fbActiveIntent=(typeof _recentMessageScrollIntent==='function' && _recentMessageScrollIntent())
      || (typeof _recentMessageTouchScrollIntent==='function' && _recentMessageTouchScrollIntent());
    if(_fbTouchHold && snapshot.pinned!==true && _grewSinceSnap && !_fbActiveIntent
       && Math.abs((Math.max(0,Math.min(target,maxTop)))-el.scrollTop)>8){
      _lastScrollTop=el.scrollTop;_lastMessageClientHeight=el.clientHeight;
      _messageUserUnpinned=true;
      _scrollPinned=false;
      _nearBottomCount=0;
      return;
    }
    // Desktop stale-snapshot residue fix (issue #5637 follow-up, PR #5742 round-3).
    // On desktop (overflow-anchor:none) the touch refusal above does NOT apply — the
    // reader must be actively held, so we write scrollTop. The ABSOLUTE snapshot.top is
    // stale once above-viewport content grew since capture. Use the app's own realign
    // idiom instead: shift the CURRENT scrollTop by how far the anchor row moved since
    // capture. `scrollTop += (currentOffset - capturedOffset)` holds the row put no
    // matter where scrollTop was carried (a row's offset is scroll-relative), which the
    // staged `snapshot.top + delta` cannot. No arbiter: the realign is a no-op when
    // already aligned (delta ~ 0) and heals when not. Only when the anchor row is
    // genuinely gone (per-tier lookup concedes, no rawIdx degradation) do we fall back
    // to the topPad-delta idiom, then to raw. Pinned/near-bottom readers took the
    // bottom-relative target above and never reach here as unpinned.
    let _fbTarget=Math.max(0,Math.min(target,maxTop));
    if(!_fbTouchHold && snapshot.pinned!==true){
      const _realign=_desktopAnchorRealignDelta(el, snapshot.anchor);
      if(_realign!==null){
        // Anchor row measurable: realign from the LIVE scrollTop (app idiom).
        _fbTarget=Math.max(0,Math.min(el.scrollTop+_realign, maxTop));
      }else{
        // Anchor row genuinely gone. Mirror the topPad-delta idiom the anchor already
        // carries (topPadBefore): shift by the growth of the virtual top spacer since
        // capture so the reader is held by the same amount the content above moved.
        const _padNow=(function(){
          const s=el.querySelector('[data-virtual-spacer="before"]');
          return s?(parseFloat(s.style.height||'0')||0):NaN;
        })();
        const _padBeforeRaw=snapshot.anchor&&snapshot.anchor.topPadBefore;
        const _padBefore=Number(_padBeforeRaw);
        // Require an ACTUAL captured topPadBefore (not null/undefined): Number(null) is 0,
        // which would otherwise add the ENTIRE current spacer height to scrollTop and fling
        // the reader far from their content (greptile P1). Only apply when it was really
        // captured; else keep the raw fallback target.
        if(_padBeforeRaw!=null&&Number.isFinite(_padNow)&&Number.isFinite(_padBefore)){
          _fbTarget=Math.max(0,Math.min(el.scrollTop+(_padNow-_padBefore), maxTop));
        }
        // else: no measurable anchor and no topPad geometry -> keep raw target.
      }
    }
    _programmaticScroll=true;_programmaticScrollSetAt=performance.now();
    el.scrollTop=_fbTarget;
  }
  _lastScrollTop=el.scrollTop;_lastMessageClientHeight=el.clientHeight;
  if(snapshot.pinned===true){
    _messageUserUnpinned=false;
    _scrollPinned=true;
    _nearBottomCount=2;
  }else if(snapshot.userUnpinned===true){
    _messageUserUnpinned=true;
    _scrollPinned=false;
    _nearBottomCount=0;
  }
  if(!restoredViaAnchor){
    if(typeof _deferClearProgrammaticScroll==='function') _deferClearProgrammaticScroll();
    else requestAnimationFrame(()=>{ setTimeout(()=>{ _programmaticScroll=false; },0); });
  }
}
function _renderMessagesWithScrollSnapshot(options){
  // Accept an optional pre-captured scroll snapshot via _prescrollSnapshot.
  // When provided, it is used INSTEAD of capturing a fresh one from the current
  // DOM state — essential for the STREAM_DONE collapse render: the caller has
  // already captured the snapshot from the LIVE DOM (before keep-open was armed),
  // and re-capturing from the intermediate expanded-worklog state would capture
  // stale anchors that no longer exist after the worklog collapses. (#6385)
  const scrollSnapshot=(options&&options._prescrollSnapshot)||_captureMessageScrollSnapshot();
  renderMessages({...(options||{}),preserveScroll:true});
  _restoreMessageScrollSnapshotSameFrame(scrollSnapshot);
}
let _assistantTurnAnchorSettledFinalAnswerWarned=false;
function _transparentStreamOrderedParts(message){
  if(typeof isTransparentStream==='function'&&!isTransparentStream()) return null;
  if(!message||message.role!=='assistant'||message._live||!Array.isArray(message.content)) return null;
  if(message._anchor_activity_scene) return null;
  const ordered=[];
  const messageTs=typeof _firstValidTimestampSeconds==='function'
    ? _firstValidTimestampSeconds(message._ts, message.timestamp, message.created_at)
    : (message._ts||message.timestamp||message.created_at);
  let hasText=false;
  let hasTool=false;
  for(const part of message.content){
    if(!part||typeof part!=='object') continue;
    if(part.type==='text'){
      const text=typeof part.text==='string'?part.text:(typeof part.content==='string'?part.content:'');
      if(!String(text||'').trim()) continue;
      ordered.push({kind:'text', text});
      hasText=true;
      continue;
    }
    if(part.type==='tool_use'){
      const toolUseId=String(part.id||'').trim();
      if(!toolUseId) return null;
      ordered.push({
        kind:'tool',
        toolUseId,
        name:part.name||'tool',
        input:(part.input&&typeof part.input==='object')?part.input:{},
        ts:part.ts,
        timestamp:part.timestamp,
        created_at:part.created_at,
        message_ts:messageTs,
      });
      hasTool=true;
    }
  }
  return hasText&&hasTool?ordered:null;
}
function _legacySettledFallbackHasToolMetadata(message){
  if(!message||message.role!=='assistant'||message._anchor_activity_scene) return false;
  return !!(
    (Array.isArray(message.tool_calls)&&message.tool_calls.length>0)||
    (Array.isArray(message._partial_tool_calls)&&message._partial_tool_calls.length>0)||
    (Array.isArray(message.content)&&message.content.some(part=>part&&typeof part==='object'&&part.type==='tool_use'))
  );
}
function _transparentOrderedDisplayText(text){
  return _stripWorkspaceDisplayPrefix(
    _stripAttachedFilesMarkerForDisplay(
      _stripLeadingAssistantThinkingMarkup(String(text||''))
    )
  );
}
function _collectToolResultSnippetsByTid(messages){
  const resultsByTid={};
  for(const message of (messages||[])){
    if(!message) continue;
    if(message.role==='tool'){
      const tid=message.tool_call_id||message.tool_use_id||'';
      if(tid) resultsByTid[tid]=_cliToolResultSnippet(message.content);
      continue;
    }
    if(!Array.isArray(message.content)) continue;
    for(const part of message.content){
      if(!part||typeof part!=='object'||part.type!=='tool_result') continue;
      const tid=part.tool_use_id||'';
      if(!tid) continue;
      const raw=typeof part.content==='string'
        ? part.content
        : Array.isArray(part.content)
          ? part.content.map(c=>c&&c.text?c.text:'').join('')
          : '';
      resultsByTid[tid]=_cliToolResultSnippet(raw);
    }
  }
  return resultsByTid;
}
function _transparentOrderedToolCall(part, rawIdx, toolCallsByTid, resultsByTid, persistedByTid, messageTs){
  const tid=String(part&&part.toolUseId||'').trim();
  const firstValidTimestampSeconds=typeof _firstValidTimestampSeconds==='function'
    ? _firstValidTimestampSeconds
    : function(...values){
        for(const value of values){
          const stamp=Number(value);
          if(Number.isFinite(stamp)&&stamp>0) return stamp>1e12?stamp/1000:stamp;
        }
        return null;
      };
  const messageStamp=firstValidTimestampSeconds(messageTs, part&&part.message_ts);
  const partStamp=firstValidTimestampSeconds(part&&part.ts, part&&part.timestamp, part&&part.created_at);
  const liveTool=tid&&toolCallsByTid&&toolCallsByTid.get(tid);
  if(liveTool){
    const next={...liveTool};
    const hasEventStamp=firstValidTimestampSeconds(next.ts, next.timestamp, next.created_at, next.started_at, next.completed_at);
    const fallbackStamp=partStamp||messageStamp;
    if(!hasEventStamp&&fallbackStamp){
      next.ts=fallbackStamp;
      next.timestamp=fallbackStamp;
      next.created_at=fallbackStamp;
    }
    const liveSnip=(resultsByTid&&resultsByTid[tid])||(persistedByTid&&persistedByTid[tid])||'';
    if(liveSnip){
      const patchSnippet=_cliPatchSnippetFromArgs(next.name||part.name||'tool', next.args||part.input||{});
      next.snippet=_cliToolCardSnippet(liveSnip,patchSnippet);
      next.is_diff=_cliToolCardHasDiffSnippet(liveSnip,patchSnippet);
    }
    if(next.done===undefined) next.done=true;
    return next;
  }
  const name=part&&part.name||'tool';
  const args=(part&&part.input&&typeof part.input==='object')?part.input:{};
  const patchSnippet=_cliPatchSnippetFromArgs(name,args);
  const resultSnippet=(resultsByTid&&tid&&resultsByTid[tid])||(persistedByTid&&tid&&persistedByTid[tid])||'';
  const fallbackStamp=partStamp||messageStamp;
  const primaryStamp=firstValidTimestampSeconds(part&&part.ts, part&&part.timestamp, part&&part.created_at, fallbackStamp);
  return {
    name,
    tid,
    id:tid,
    assistant_msg_idx:rawIdx,
    args:_toolArgsSnapshot(args),
    snippet:_cliToolCardSnippet(resultSnippet,patchSnippet),
    is_diff:_cliToolCardHasDiffSnippet(resultSnippet,patchSnippet),
    done:true,
    ts:primaryStamp||undefined,
    timestamp:primaryStamp||undefined,
    created_at:primaryStamp||undefined,
  };
}
function _assistantTurnAnchorSettledFinalAnswer(message, content, context){
  const sceneFinal=_assistantAnchorSceneFinalAnswerText(message);
  const effectiveContent=String(content||'').trim()?content:sceneFinal;
  try{
    const api=(typeof window!=='undefined')?window.AgyAssistantTurnAnchors:null;
    if(!api||typeof api.projectAssistantTurnAnchorSettledMessageFinalAnswer!=='function') return String(sceneFinal||'').trim()?sceneFinal:null;
    const result=api.projectAssistantTurnAnchorSettledMessageFinalAnswer(message,{
      session_id:context&&context.session_id,
      raw_idx:context&&context.raw_idx,
      content:effectiveContent,
    });
    const finalAnswer=result&&typeof result.final_answer==='string'?result.final_answer:'';
    return finalAnswer?finalAnswer:(String(sceneFinal||'').trim()?sceneFinal:null);
  }catch(err){
    if(!_assistantTurnAnchorSettledFinalAnswerWarned&&typeof console!=='undefined'&&console.warn){
      _assistantTurnAnchorSettledFinalAnswerWarned=true;
      console.warn('assistant turn anchor settled-final projection failed',err);
    }
    return null;
  }
}
// Re-anchor a pinned/tail-following reader to the settled bottom after a full
// renderMessages() rebuild, eliminating the one-frame mid-stream jitter. MUST be scheduled
// in a MICROTASK from the end of renderMessages (see the queueMicrotask call site), NOT run
// synchronously. Why: the mid-stream re-render bug is that renderMessages wipes #msgInner
// then rebuilds, and the pinned tail-follow path (scrollIfPinned → scrollToBottom) writes
// scrollTop while still INSIDE the render sync stack, where the browser reports a TRANSIENT
// scrollHeight a few px short of the settled value (layout is batched — every geometry read
// inside the sync stack returns the mid-settle height). So scrollToBottom lands scrollTop a
// little HIGH (short of the true tail); that intermediate is painted this frame and the
// settle rAF corrects it the next frame → a fast ~1-row back-and-forth bounce (~82px). A
// microtask runs AFTER the sync stack unwinds (layout has flushed, so scrollHeight/clientHeight
// are the settled values) but BEFORE the browser paints — so writing the now-correct settled
// max here lands the tail exactly and the short intermediate never reaches the screen. Only
// fires for a pre-wipe tail-follower left short of the settled max, so an unpinned reader
// parked in history is never moved (orthogonal to the unpinned jump-back class). The
// _programmaticScroll latch (armed at the wipe) keeps the scroll listener from misreading
// this write as a manual unpin. Idempotent: a no-op once scrollTop already equals the max.
function _reanchorPinnedTailAfterRender(wasNearTail){
  if(!wasNearTail) return;
  const el=$('messages');
  if(!el) return;
  const settledMax=Math.max(0, el.scrollHeight-el.clientHeight);
  if(el.scrollTop < settledMax-1){
    _programmaticScroll=true;_programmaticScrollSetAt=performance.now();
    el.scrollTop=settledMax;
    _lastScrollTop=el.scrollTop;_lastMessageClientHeight=el.clientHeight;
    _nearBottomCount=2;
    _scrollPinned=true;
  }
}
function _scrollAfterMessageRender(preserveScroll, scrollSnapshot){
  // Terminal stream renders can happen after S.activeStreamId is cleared.
  // In that case, preserveScroll asks the normal pin-state helper to decide:
  // pinned users stay at bottom; users who manually scrolled up get their
  // pre-render scrollTop restored after the DOM replacement.
  if(preserveScroll){
    const readerAwayFromBottom=!!(
      scrollSnapshot &&
      Number.isFinite(Number(scrollSnapshot.bottom)) &&
      Number(scrollSnapshot.bottom)>250
    );
    // Keep master's follow heuristic for pinned / still-near-bottom users:
    // _followMessagesAfterDomReplace() does a FORCED scrollToBottom() (synchronous
    // bottom write + forced settle), so the final settled response can't leave a
    // pinned reader a few lines short. Only genuinely-scrolled-up (unpinned, not
    // near bottom) users fall through to keep their position and get the
    // new-message cue. (Using scrollIfPinned() here instead would skip the forced
    // write unless distance>500 and let the DOM-rebuild scroll event cancel the
    // delayed settles — Codex CORE catch on #3631.)
    if(!readerAwayFromBottom && !_messageUserUnpinned && _followMessagesAfterDomReplace()) return;
    _restoreMessageScrollSnapshot(scrollSnapshot);
    _maybeShowNewMessageScrollCue(scrollSnapshot);
    return;
  }
  if(S.activeStreamId){
    // Mid-stream re-render (tool completion, activity-scene refresh, clarify echo).
    // renderMessages() wipes #msgInner (inner.innerHTML='') then rebuilds; that wipe
    // collapses scrollHeight toward the empty-table height, and the browser is FORCED
    // to clamp #messages.scrollTop down to the new (near-zero) max. For a reader who
    // scrolled UP into history (unpinned), scrollIfPinned() is a no-op — so it does NOT
    // undo that clamp, and the reader is stranded at the top (the scroll jump-back). The
    // wipe-to-empty clamp is a browser primitive (device-agnostic; JS never writes the
    // scrollTop), so the passive no-op cannot preserve position here. renderMessages()
    // captured a pre-wipe snapshot for exactly this case (its scrollSnapshot init fires
    // when _messageUserUnpinned), so restore the unpinned reader's viewport instead of
    // the no-op. Pinned/tail-following readers keep scrollIfPinned() (correct live-follow).
    if(_messageUserUnpinned && scrollSnapshot){
      _restoreMessageScrollSnapshot(scrollSnapshot);
      _maybeShowNewMessageScrollCue(scrollSnapshot);
      return;
    }
    scrollIfPinned();
    return;
  }
  // Manual unpin is sticky: once the reader scrolls away, automatic idle/non-
  // preserve re-renders must restore their viewport rather than clearing the
  // unpin state with scrollToBottom(). A fresh session load (not unpinned) still
  // lands at the bottom as expected. (Codex #4006 follow-up.)
  // renderMessages() captures the pre-wipe snapshot for this case too (see its
  // scrollSnapshot init), so restoring here lands the reader where they were.
  if(_messageUserUnpinned){
    _restoreMessageScrollSnapshot(scrollSnapshot);
    _maybeShowNewMessageScrollCue(scrollSnapshot);
    return;
  }
  scrollToBottom();
}

function _maybeRecoverVirtualizedBlankViewport(options, preserveScroll, virtualWindow){
  if(!preserveScroll||!virtualWindow||!virtualWindow.virtualized||!!(options&&options._virtualFallback)) return false;
  if(_messageViewportIntersectsRenderedRow()) return false;
  if(_sessionHtmlCacheSid&&S.session&&S.session.session_id===_sessionHtmlCacheSid){
    _sessionHtmlCache.delete(_sessionHtmlCacheSid);
  }
  _messageVirtualWindowKey='';
  renderMessages({preserveScroll:true,_virtualFallback:true});
  return true;
}

// #6345: parse the synthetic wakeup body back into display fields. Mirrors the
// two structured api/background_process.format_wakeup_prompt shapes (pinned by
// tests/test_background_process_wakeup_format.py); other event kinds return
// null and keep the raw-notice fallback.
function _parseProcessWakeupBody(text){
  const s=String(text||'');
  // Header groups are single-line by grammar; the output group captures the
  // rest verbatim (leading indentation / trailing blank lines preserved). The
  // watch suppression note is intentionally NOT split out of the output — real
  // process output can contain the identical text, so stripping it would drop
  // legitimate content (#6350 review finding 2). It rides along in `output`.
  let m=s.match(/^\[IMPORTANT: Background process ([^\n]*?) completed \(exit_code=([^)\n]*)\)\.\nCommand: ([^\n]*)\nOutput:\n([\s\S]*)\]$/);
  if(m) return {type:'completion',taskId:m[1],exitCode:m[2],command:m[3],output:m[4],pattern:null};
  m=s.match(/^\[IMPORTANT: Background process ([^\n]*?) matched watch pattern "(.*)"\.\nCommand: ([^\n]*)\nMatched output:\n([\s\S]*)\]$/);
  if(m) return {type:'watch_match',taskId:m[1],pattern:m[2],command:m[3],output:m[4],exitCode:null};
  return null;
}
// Server-stamped _wakeup_meta (authoritative when present) merged over the
// client parse; the output section only ever comes from the parse because the
// meta deliberately carries header fields only.
function _processWakeupInfo(m, text){
  const parsed=_parseProcessWakeupBody(text);
  const meta=(m&&m._wakeup_meta&&typeof m._wakeup_meta==='object')?m._wakeup_meta:null;
  if(!parsed&&!meta) return null;
  const pick=(metaKey,parsedKey)=>{
    if(meta&&meta[metaKey]!=null) return meta[metaKey];
    return parsed?parsed[parsedKey]:null;
  };
  return {
    type:String(pick('type','type')||''),
    taskId:String(pick('task_id','taskId')||''),
    command:String(pick('command','command')||''),
    exitCode:pick('exit_code','exitCode'),
    pattern:pick('pattern','pattern'),
    output:parsed?parsed.output:null,
  };
}
function _processWakeupCardHtml(info, rawText, extras){
  const isWatch=info.type==='watch_match';
  const exitStr=info.exitCode==null?'':String(info.exitCode);
  // Signal-killed processes report negative exit codes (subprocess returncode).
  const exitKnown=/^-?\d+$/.test(exitStr);
  const exitOk=exitStr==='0';
  let chip;
  if(isWatch){
    chip=`<span class="process-wakeup-chip watch" title="${esc(t('process_wakeup_matched'))}">${li('eye',11)}<code title="${esc(String(info.pattern||''))}">${esc(String(info.pattern||''))}</code></span>`;
  }else{
    const cls=exitOk?'ok':(exitKnown?'fail':'neutral');
    const icon=exitOk?li('check',11):(exitKnown?li('x',11):'');
    chip=`<span class="process-wakeup-chip ${cls}">${icon}<span>exit ${esc(exitStr||'?')}</span></span>`;
  }
  const cmdHtml=info.command?`<code class="process-wakeup-cmd" title="${esc(info.command)}">${esc(info.command)}</code>`:'';
  // Preserve output byte-for-byte for the <pre>; trim ONLY for the
  // empty/non-empty decision so leading indentation and trailing blank lines
  // survive (#6350 review finding 1).
  const outRaw=info.output!=null?String(info.output):String(rawText||'');
  const outHtml=outRaw.trim()?`<pre class="process-wakeup-text">${esc(outRaw)}</pre>`:'';
  const cmdRow=info.command?`<div class="process-wakeup-cmd-row"><code>${esc(info.command)}</code></div>`:'';
  // The collapsed watch chip truncates the pattern; surface the full,
  // wrapping value in the expanded detail so touch/keyboard users can read it
  // without relying on a hover tooltip (#6350 review finding 4).
  const patternRow=(isWatch&&info.pattern)?`<div class="process-wakeup-pattern-row"><span class="process-wakeup-detail-key">${esc(t('process_wakeup_matched'))}</span><code>${esc(String(info.pattern))}</code></div>`:'';
  return `<details class="process-wakeup-card"><summary class="process-wakeup-summary"><span class="process-wakeup-toggle">${li('chevron-right',12)}</span><span class="process-wakeup-label">${li('terminal',13)}<span>${esc(t('process_wakeup_label'))}</span></span>${cmdHtml}${chip}${extras.timeHtml||''}</summary><div class="process-wakeup-detail">${extras.filesHtml||''}${patternRow}${cmdRow}<div class="msg-body process-wakeup-body">${outHtml}</div>${extras.footHtml||''}</div></details>`;
}

function renderMessages(options){
  _lastMessageRenderAt=performance.now();
  const preserveScroll=!!(options&&options.preserveScroll);
  const virtualFallback=!!(options&&options._virtualFallback);
  // Capture the pre-wipe scroll position when preserving OR when the reader has
  // manually unpinned; both need to restore the reader's position after the DOM
  // rebuild rather than snap to the bottom. (Codex #4006 r3 follow-up.)
  const scrollSnapshot=(preserveScroll||_messageUserUnpinned)?_captureMessageScrollSnapshot():null;
  const inner=$('msgInner');
  const sid=S.session?S.session.session_id:null;
  if(!S.busy&&Array.isArray(S.messages)&&typeof _hydrateIdLinkedHistoricalToolScenes==='function'){
    const activityMode=typeof chatActivityMode==='function'?chatActivityMode():'compact_worklog';
    _hydrateIdLinkedHistoricalToolScenes(S.messages,{sessionId:sid,mode:activityMode});
  }
  const msgCount=S.messages.length;
  // During session switch, S.messages is intentionally cleared while the full
  // message fetch is still in flight. Other async updates can still call
  // renderMessages() in this window. Keep the existing loading placeholder.
  if(_loadingSessionId===sid&&msgCount===0&&inner) return;
  if(sid!==_messageRenderWindowSid) _resetMessageRenderWindow(sid);
  let cachedRenderSignature=null;
  const hasTransientTranscriptUi=!!(
    (window._compressionUi&&(!window._compressionUi.sessionId||window._compressionUi.sessionId===sid)) ||
    (window._handoffUi&&(!window._handoffUi.sessionId||window._handoffUi.sessionId===sid))
  );

  const preservedCompressionTaskMessages=_latestPreservedCompressionTaskListMessages(S.messages);
  const visWithIdx=_getVisibleMessagesWithIdx();
  $('emptyState').style.display=(visWithIdx.length||preservedCompressionTaskMessages.length)?'none':'';
  const virtualWindow=virtualFallback
    ? {virtualized:false,start:0,end:visWithIdx.length,topPad:0,bottomPad:0,total:visWithIdx.length,tailStart:visWithIdx.length}
    : _currentMessageVirtualWindow(visWithIdx,_messageVirtualKeepTailCount());
  const renderWindowKey=_messageVirtualWindowKeyFor(virtualWindow);
  const windowStart=virtualWindow.start;
  const windowEnd=virtualWindow.end;
  const renderHeadVisWithIdx=visWithIdx.slice(windowStart, windowEnd);
  const renderTailStart=virtualWindow.virtualized?Math.max(windowEnd, virtualWindow.tailStart):windowEnd;
  const renderTailVisWithIdx=virtualWindow.virtualized&&renderTailStart<visWithIdx.length
    ? visWithIdx.slice(renderTailStart)
    : [];
  const renderVisWithIdx=renderHeadVisWithIdx.concat(renderTailVisWithIdx);
  const renderVisibleIdxs=[
    ...renderHeadVisWithIdx.map((_,idx)=>windowStart+idx),
    ...renderTailVisWithIdx.map((_,idx)=>renderTailStart+idx),
  ];
  const headRenderCount=renderHeadVisWithIdx.length;

  // Fast path: switching back to a previously rendered session with same count.
  // Guard: sid !== _sessionHtmlCacheSid ensures in-session updates (edits,
  // new messages, tool_complete) always get a fresh rebuild.
  // Skip cache if this session is still streaming — the live smd parser writes
  // into a DOM node inside the cached subtree; serving cached HTML detaches it.
  // Also skip cache for transient transcript cards such as /compress and
  // cross-channel handoff summaries; otherwise the cached transcript returns
  // before those cards can be inserted.
  if(sid&&sid!==_sessionHtmlCacheSid&&!INFLIGHT[sid]&&!hasTransientTranscriptUi){
    const renderSignature=_messageRenderCacheSignature();
    cachedRenderSignature=renderSignature;
    const cached=_sessionHtmlCache.get(sid);
    if(cached&&cached.msgCount===msgCount&&cached.renderWindowKey===renderWindowKey&&cached.signature===renderSignature){
      inner.innerHTML=cached.html;
      _messageVirtualWindowKey=renderWindowKey;
      _sessionHtmlCacheSid=sid;
      _rehydrateTransparentStreamDom(inner);
      _rehydrateDeferredWorklogsFromCache(inner);
      _wireMessageWindowLoadEarlierButton();
      if(typeof _applySessionNavigationPrefs==='function') _applySessionNavigationPrefs();
      _scrollAfterMessageRender(preserveScroll, scrollSnapshot);
      if(_maybeRecoverVirtualizedBlankViewport(options, preserveScroll, virtualWindow)) return;
      _updateMessageVirtualMeasurements(renderVisWithIdx, renderVisibleIdxs, virtualWindow);
      requestAnimationFrame(()=>_postProcessWithAnchorSuppression(inner));
      if(typeof _initMediaPlaybackObserver==='function') _initMediaPlaybackObserver();
      if(typeof loadTodos==='function'&&document.getElementById('panelTodos')&&document.getElementById('panelTodos').classList.contains('active')){loadTodos();}
      return;
    }
  }
  // Mid-stream flicker fix (#3877): when a renderMessages() rebuild is reached
  // while THIS session is actively streaming (e.g. the clarify-response echo at
  // messages.js, or a CLI-import refresh), the `inner.innerHTML=''` below detaches
  // the live `#liveAssistantTurn` node — and the smd parser keeps writing into
  // that now-orphaned node, so the streamed text vanishes until the next stream
  // event rebuilds the turn ("disappears, then reappears"). Capture the live
  // turn's actual DOM node (not its HTML — the parser holds a live reference into
  // it) so it can be re-attached after the rebuild, keeping the parser target
  // connected and the streamed text visible. Only for the streaming session's own
  // live turn; never affects settled transcripts.
  let _preservedLiveTurn=null;
  if(sid&&INFLIGHT[sid]){
    const _lt=document.getElementById('liveAssistantTurn');
    if(_lt&&(!_lt.dataset||!_lt.dataset.sessionId||_lt.dataset.sessionId===sid)){
      // Live-turn preservation requires a PROVABLE live owner — never bare DOM
      // content. (#6948) The live turn is preserved across the wipe only while
      // (a) the stream is genuinely active (S.activeStreamId — the #3877
      // mid-stream flicker case this preserve was written for), or (b) the
      // current message projection (S.messages) still carries explicit
      // live-assistant evidence — a client-side _live / _activityBurstId /
      // _liveSegmentSeq marker merged in from the INFLIGHT tail or a server
      // journal snapshot (the reconnect / terminal-projection case). A settled
      // transcript has neither, so a contentful but DEAD live node (stream
      // ended — S.activeStreamId cleared — while INFLIGHT[sid] was not yet
      // cleaned) is no longer preserved: re-attaching it over the settled
      // transcript pinned a second copy of the same assistant message (#6948;
      // data was always clean — state.db, sidecar, and /api/session each hold
      // one row; the duplicate existed only in the rendered DOM). The #5390
      // blank-turn guard (对话消失) is preserved: a dead EMPTY shell has no live
      // projection either, so it is still dropped with the wipe instead of
      // pinning an avatar-only blank turn over the settled answer.
      const _hasLiveAssistantProjection=Array.isArray(S.messages)&&S.messages.some(m=>
        m&&m.role==='assistant'&&(m._live||m._activityBurstId!==undefined||m._liveSegmentSeq!==undefined)
      );
      if(S.activeStreamId || _hasLiveAssistantProjection){
        _preservedLiveTurn=_lt;
      }
    }
  }
  const compressionState=(()=>{
    let compressionState=_compressionStateForCurrentSession();
    if(!S.busy && compressionState && compressionState.automatic){
      window._compressionUi=null;
      _clearCompressionElapsedTimer();
      _setCompressionSessionLock(null);
      compressionState=null;
    }
    return compressionState;
  })();
  if(window._compressionUi && !compressionState) clearCompressionUi();
  const handoffState=_handoffStateForCurrentSession();
  if(window._handoffUi && !handoffState) window._handoffUi=null;
  const sessionCompressionAnchor=(
    S.session && typeof S.session.compression_anchor_visible_idx==='number'
  ) ? S.session.compression_anchor_visible_idx : null;
  const sessionCompressionAnchorKey=(
    S.session && S.session.compression_anchor_message_key && typeof S.session.compression_anchor_message_key==='object'
  ) ? S.session.compression_anchor_message_key : null;
  const sessionCompressionSummary=(
    S.session && typeof S.session.compression_anchor_summary==='string'
  ) ? S.session.compression_anchor_summary.trim() : '';
  const worklogDetailDisclosureState=_captureWorklogDetailDisclosureState(inner);
  _recycleStash.clear();
  if(_msgNodeRecycleEnabled){
    for(const child of Array.from(inner.children)){
      const key=child.dataset&&(child.dataset.recycleKey||child.dataset.msgIdx);
      if(!key) continue;
      if(child.id==='liveAssistantTurn'||child.querySelector&&child.querySelector('#liveAssistantTurn')) continue;
      _recycleStash.set(Number(key), child);
    }
  }
  // Mobile scroll-jank fix: temporarily disable overflow-anchor so Chromium
  // cannot re-anchor to the topmost row during the DOM wipe-and-rebuild gap.
  if(window._fixMobileScrollJank) window._fixMobileScrollJank();
  // Capture whether the reader was at/near the tail BEFORE the wipe. A tail-follower
  // hit by a mid-stream re-render gets a one-frame jitter: the wipe+rebuild lands the
  // sync scrollTop write against a transient layout whose above-viewport height is a
  // few px short of the settled value, so the browser clamps scrollTop a little high;
  // the settle rAF corrects it the next frame, producing a fast ~1-row back-and-forth
  // bounce. We remember the pre-wipe near-tail state here (geometry, not closure pin
  // flags — the wipe's clamp scroll event can transiently perturb those) so the tail
  // of renderMessages can re-anchor to the settled bottom before the intermediate is
  // painted. See _reanchorPinnedTailAfterRender + its queueMicrotask call site.
  const _preWipeNearTail=(()=>{
    const _m=$('messages');
    if(!_m) return false;
    return (_m.scrollHeight-_m.scrollTop-_m.clientHeight)<=8;
  })();
  // Pre-wipe capture: read the still-laid-out user rows' REAL heights before the wipe below
  // destroys them, and persist so the rebuild reserves the real off-screen height. This is
  // the non-virtualized analog of #5638's virtualized measure pass (which never runs when
  // _virtualizeTranscript===false). Without it, a fresh off-screen tall user row reserves
  // only the flat contain-intrinsic-size estimate, scrollHeight shrinks, and the browser
  // clamps scrollTop → the jump-back. Reading pre-wipe (not post-render) is what makes the
  // measurement reliable — the old elements have painted, so their rect height is real even
  // off-screen; a post-render read of a fresh off-screen row returns its collapsed reserve.
  if(typeof _rememberRenderedUserRowIntrinsicHeights==='function') _rememberRenderedUserRowIntrinsicHeights();
  // The DOM wipe can briefly collapse #msgInner to zero height, causing the
  // browser to clamp #messages.scrollTop to 0 and emit a scroll event.  That
  // event is a render artifact, not user intent; if the scroll listener sees it
  // with _programmaticScroll=false, it marks the reader manually unpinned and
  // the live reply stops following / appears to jump backward.
  _programmaticScroll=true;
  _programmaticScrollSetAt=performance.now();
  inner.innerHTML='';
  const compressionNode=compressionState?_compressionCardsNode(compressionState):null;
  const {message:referenceMessage, rawIdx:referenceMessageRawIdx}=_latestCompressionReferenceMessage(
    S.messages,
    sessionCompressionSummary
  );
  const referenceText=referenceMessage
    ? msgContent(referenceMessage)||String(referenceMessage.content||'')
    : sessionCompressionSummary;
  const referenceNode=(!compressionState && _shouldShowSettledCompressionReference(referenceText) && (sessionCompressionAnchor!==null || sessionCompressionAnchorKey || sessionCompressionSummary))
    ? (()=>{const row=document.createElement('div');row.innerHTML=`<div class="compression-turn"><div class="compression-turn-blocks">${_compressionReferenceCardHtml(referenceText,false)}${_preservedCompressionTaskListCardsHtml(preservedCompressionTaskMessages)}</div></div>`;return row.firstElementChild;})()
    : null;
  let preservedCompressionTaskCardsAttached=!!referenceNode;
  const preservedCompressionRawIdxs=[];
  let rawIdx=0;
  for(const m of S.messages){
    if(!m||!m.role||m.role==='tool'){rawIdx++;continue;}
    if(_isPreservedCompressionTaskListMessage(m)){preservedCompressionRawIdxs.push(rawIdx);rawIdx++;continue;}
    rawIdx++;
  }
  const firstRenderedRawIdx=renderVisWithIdx.length?renderVisWithIdx[0].rawIdx:Infinity;
  // #6999: the turn-content maps MUST see the FULL visWithIdx, not the
  // virtual render window. _assistantTurnFinalVisibleContentMap /
  // _assistantTurnVisibleContentMap derive the echo-strip context for a
  // rendered assistant row from ALL assistant siblings of its turn
  // (ui.js:10783-10830). Windowed-out siblings are still input context even
  // though their own rows are not read: cutting through an assistant run
  // loses the final/visible answer used to strip reasoning echoes, and
  // concatenating head+tail across an omitted user boundary would merge
  // distinct turns into one run (duplicate final-answer in Worklog/Thinking,
  // or later-turn prose used as an echo-strip input). Turn context must
  // always be complete — never cut mid-run, never head+tail with a gap.
  const assistantTurnFinalVisibleContentByRawIdx=_assistantTurnFinalVisibleContentMap(visWithIdx);
  const assistantTurnVisibleContentByRawIdx=_assistantTurnVisibleContentMap(visWithIdx);
  const hasServerOlder=!!(typeof _messagesTruncated!=='undefined' && _messagesTruncated && S.messages.length>0);
  const serverOlderCount=hasServerOlder&&Number.isFinite(Number(_oldestIdx))?Math.max(0,Number(_oldestIdx)):0;
  if(typeof _applySessionNavigationPrefs==='function') _applySessionNavigationPrefs();
  if(virtualWindow.virtualized&&virtualWindow.topPad>0){
    inner.appendChild(_messageVirtualSpacer(virtualWindow.topPad,'before'));
  }
  if(hasServerOlder){
    const indicator=document.createElement('button');
    indicator.type='button';
    indicator.id='loadOlderIndicator';
    indicator.className='load-older-indicator message-window-load-earlier';
    indicator.textContent=serverOlderCount>0
      ? `Load earlier messages (${serverOlderCount} older)`
      : (typeof t==='function'?t('load_older_messages'):'Load earlier messages');
    inner.appendChild(indicator);
    _wireMessageWindowLoadEarlierButton();
  }
  let lastUserRawIdx=-1;
  for(let i=visWithIdx.length-1;i>=0;i--){
    if(visWithIdx[i].m&&visWithIdx[i].m.role==='user'){
      lastUserRawIdx=visWithIdx[i].rawIdx;
      break;
    }
  }
  const insertionAnchorFull=_compressionAnchorIndex(
    visWithIdx,
    compressionState ? compressionState.anchorMessageKey : sessionCompressionAnchorKey,
    compressionState
      ? (typeof compressionState.anchorVisibleIdx==='number' ? compressionState.anchorVisibleIdx : compressionState.anchorRawIdx)
      : sessionCompressionAnchor
  );
  let insertionAnchor=null;
  if(typeof insertionAnchorFull==='number'){
    const hasVirtualRenderGap=renderVisibleIdxs.some((idx,pos)=>idx!==windowStart+pos);
    if(!hasVirtualRenderGap){
      if(insertionAnchorFull<windowStart) insertionAnchor=renderVisWithIdx.length?0:null;
      else if(insertionAnchorFull<windowStart+renderVisWithIdx.length) insertionAnchor=insertionAnchorFull-windowStart;
      else insertionAnchor=renderVisWithIdx.length?renderVisWithIdx.length-1:null;
    }else if(renderVisibleIdxs.length){
      let previousVisibleIdx=-1;
      for(let i=0;i<renderVisibleIdxs.length;i++){
        if(renderVisibleIdxs[i]<=insertionAnchorFull) previousVisibleIdx=i;
        else break;
      }
      insertionAnchor=previousVisibleIdx>=0?previousVisibleIdx:0;
    }else{
      insertionAnchor=null;
    }
  }
  let _prevSepKey=null;
  let currentAssistantTurn=null;
  // Only build question→assistant mapping for the visible window, not the
  // full visWithIdx.  The jump-to-question button is only rendered for
  // assistant messages that appear in the current render window anyway.
  const questionRawIdxByAssistantRawIdx=new Map();
  let lastQuestionRawIdx=-1;
  const renderedRawIdxs=new Set(renderVisWithIdx.map(e=>e.rawIdx));
  const renderableRawIdxs=new Set(visWithIdx.map(e=>e.rawIdx));
  for(const entry of visWithIdx){
    const role=entry&&entry.m&&entry.m.role;
    if(role==='user') lastQuestionRawIdx=entry.rawIdx;
    else if(role==='assistant'&&renderedRawIdxs.has(entry.rawIdx)) questionRawIdxByAssistantRawIdx.set(entry.rawIdx,lastQuestionRawIdx);
  }
  const assistantRawIdxByQuestionRawIdx=new Map();
  for(const [aIdx,qIdx] of questionRawIdxByAssistantRawIdx){
    if(!assistantRawIdxByQuestionRawIdx.has(qIdx)) assistantRawIdxByQuestionRawIdx.set(qIdx,aIdx);
  }
  // #3709 (defect B): build a per-turn combined visible-answer text so the
  // thinking echo-strip can de-dupe a thinking-only message (whose own visible
  // body is empty) against the answer prose carried by a SIBLING message in the
  // same turn. A turn = the run of assistant messages between two user messages.
  // Map every assistant rawIdx in a run to the run's combined visible text.
  const _turnVisibleTextByRawIdx=new Map();
  {
    let _run=[]; let _runText=[];
    const _flush=()=>{
      if(_run.length){
        const combined=_runText.join('\n\n');
        for(const ri of _run) _turnVisibleTextByRawIdx.set(ri, combined);
      }
      _run=[]; _runText=[];
    };
    for(const entry of renderVisWithIdx){
      const em=entry&&entry.m; const role=em&&em.role;
      if(role==='assistant'){
        _run.push(entry.rawIdx);
        // Visible prose = content with any leading <think>…</think> /channel-thought
        // block stripped (the same blocks the per-message extractor removes below).
        let vis=typeof em.content==='string'?em.content:'';
        vis=vis.replace(/^\s*<think>[\s\S]*?<\/think>\s*/,'')
               .replace(/^\s*<\|channel\|?>thought\n?[\s\S]*?<channel\|>\s*/,'')
               .replace(/^\s*<\|turn\|>thinking\n[\s\S]*?<turn\|>\s*/,'').trim();
        if(vis) _runText.push(vis);
      }else{
        _flush();
      }
    }
    _flush();
  }

  const assistantSegments=new Map();
  const assistantThinking=new Map();
  const userRows=new Map();
  // Only collect tool-call assistant indices for messages that are actually
  // rendered in the current window.  S.toolCalls can grow large in long turns,
  // but we only need the ones whose assistant_msg_idx falls inside the visible
  // range.
  const toolCallAssistantIdxs=new Set();
  if(Array.isArray(S.toolCalls)){
    for(const tc of S.toolCalls){
      if(!tc) continue;
      const idx=tc.assistant_msg_idx;
      if(idx!==undefined && renderedRawIdxs.has(idx)){
        toolCallAssistantIdxs.add(idx);
      }
    }
  }
  const transparentOrderedToolIds=new Set();
  const transparentOrderedToolCallsByTid=new Map();
  // These scans only feed the transparent-stream ordered render path; skip the
  // O(messages×parts) work entirely in other modes (Opus perf finding #4932).
  const _transparentModeActive=(typeof isTransparentStream==='function')&&isTransparentStream();
  const transparentPersistedSnippetByTid={};
  if(_transparentModeActive){
    if(Array.isArray(S.toolCalls)){
      for(const tc of S.toolCalls){
        if(!tc||typeof tc!=='object') continue;
        const tid=tc.tid||tc.id||tc.tool_call_id||tc.tool_use_id||tc.call_id||'';
        if(tid&&!transparentOrderedToolCallsByTid.has(tid)) transparentOrderedToolCallsByTid.set(tid,tc);
      }
    }
    // #4927 durable fallback: the ordered path must consult the persisted
    // session.tool_calls snippet by tid too, or a cold/paginated load where the
    // S.messages tool_result join misses renders an empty body — and its inline
    // card then suppresses the post-loop derived card that WOULD have recovered.
    try{
      const persisted=(S.session&&Array.isArray(S.session.tool_calls))?S.session.tool_calls:[];
      persisted.forEach(tc=>{
        if(!tc||typeof tc!=='object') return;
        const ptid=tc.tid||tc.id||tc.tool_call_id||tc.call_id||'';
        const psnip=tc.snippet||tc.result||tc.output||tc.preview||'';
        if(ptid&&psnip&&!transparentPersistedSnippetByTid[ptid]) transparentPersistedSnippetByTid[ptid]=String(psnip);
      });
    }catch(e){}
  }
  const transparentToolResultsByTid=_transparentModeActive?_collectToolResultSnippetsByTid(S.messages):{};
  const latestRenderedAssistantRawIdx=(()=>{
    for(let i=renderVisWithIdx.length-1;i>=0;i--){
      const entry=renderVisWithIdx[i];
      if(entry&&entry.m&&entry.m.role==='assistant'&&!entry.m._live) return entry.rawIdx;
    }
    return -1;
  })();
  // Windowed render loop replaces the legacy full loop:
  // for(let vi=0;vi<visWithIdx.length;vi++)
  for(let vi=0;vi<renderVisWithIdx.length;vi++){
    if(virtualWindow.virtualized&&virtualWindow.bottomPad>0&&vi===headRenderCount){
      // The virtual gap breaks assistant-turn adjacency. Reset the current
      // turn before rendering the always-visible tail so assistant segments do
      // not merge across the spacer boundary.
      currentAssistantTurn=null;
      inner.appendChild(_messageVirtualSpacer(virtualWindow.bottomPad,'after'));
    }
    const {m,rawIdx}=renderVisWithIdx[vi];
    const _tsSep=m._ts||m.timestamp;
    if(_tsSep){
      const _d=new Date(_tsSep*1000);
      const _key=_d.toDateString();
      if(_prevSepKey && _prevSepKey!==_key){
        const sep=document.createElement('div');
        sep.className='msg-date-sep';
        sep.textContent=_fmtDateSep(_d);
        inner.appendChild(sep);
      }
      _prevSepKey=_key;
    }
    let content=m.content||'';
    let thinkingText='';
    let orderedTransparentParts=_transparentStreamOrderedParts(m);
    if(Array.isArray(content)){
      content=content.filter(p=>p&&p.type==='text').map(p=>p.text||p.content||'').join('\n');
    }
    if(m.role==='assistant'&&!m._live&&typeof content==='string'){
      const anchorFinal=_assistantTurnAnchorSettledFinalAnswer(m, content, {
        session_id:sid,
        raw_idx:rawIdx,
      });
      if(anchorFinal!==null){
        content=anchorFinal;
        if(Array.isArray(orderedTransparentParts)){
          for(let i=orderedTransparentParts.length-1;i>=0;i--){
            if(orderedTransparentParts[i]&&orderedTransparentParts[i].kind==='text'){
              orderedTransparentParts[i]={...orderedTransparentParts[i], text:anchorFinal};
              break;
            }
          }
        }
      }
    }
    if(typeof content==='string'){
      if(typeof window!=='undefined'&&typeof window._extractInlineThinkingFromContentForRender==='function'){
        const split=window._extractInlineThinkingFromContentForRender(content, thinkingText);
        thinkingText=split.reasoning||thinkingText;
        content=split.content;
      }else if(!thinkingText){
        const thinkMatch=content.match(/^\s*<think>([\s\S]*?)<\/think>\s*/);
        if(thinkMatch){
          thinkingText=thinkMatch[1].trim();
          content=content.replace(/^\s*<think>[\s\S]*?<\/think>\s*/,'').trimStart();
        }
        if(!thinkingText){
          const gemmaMatch=content.match(/^\s*<\|channel\|?>thought\n?([\s\S]*?)<channel\|>\s*/);
          if(gemmaMatch){
            thinkingText=gemmaMatch[1].trim();
            content=content.replace(/^\s*<\|channel\|?>thought\n?[\s\S]*?<channel\|>\s*/,'').trimStart();
          }
        }
        if(!thinkingText){
          const gemmaTurnMatch=content.match(/^\s*<\|turn\|>thinking\n([\s\S]*?)<turn\|>\s*/);
          if(gemmaTurnMatch){
            thinkingText=gemmaTurnMatch[1].trim();
            content=content.replace(/^\s*<\|turn\|>thinking\n[\s\S]*?<turn\|>\s*/,'').trimStart();
          }
        }
      }
    }
    const isProcessWakeup=m&&m._source==='process_wakeup';
    const isUser=m.role==='user';
    if(!isUser&&_isMarkerOnlyAssistantCompressionMessage(m)){
      content='**Error:** No response received after context compression. Please retry.';
    }
    const displayContent=isUser?_stripAttachedFilesMarkerForDisplay(_stripWorkspaceDisplayPrefix(content)):content;
    const rowDisplayContent=displayContent;
    if(!isUser&&_isAssistantEmptyPlaceholderContent(m, displayContent)){
      content='';
    }
    if(!isUser&&(isCompactWorklogMode()||isTransparentStream())&&!thinkingText){
      const turnFinalVisibleContent=assistantTurnFinalVisibleContentByRawIdx.get(rawIdx)||'';
      const turnVisibleContents=assistantTurnVisibleContentByRawIdx.get(rawIdx)||[];
      thinkingText=_worklogReasoningTextFromMessage(m, rawIdx, toolCallAssistantIdxs, displayContent, turnFinalVisibleContent, turnVisibleContents);
    }
    const isLastAssistant=!isUser&&vi===renderVisWithIdx.length-1;
    const nextRendered=renderVisWithIdx[vi+1];
    const isTurnFinalAssistant=!isUser&&(!nextRendered||!nextRendered.m||nextRendered.m.role!=='assistant');
    let filesHtml='';
    if(m.attachments&&m.attachments.length){
      // Static regression tests intentionally look for msg-media-img/msg-file-badge near this branch.
      const _attachSid=(S.session&&S.session.session_id)||'';
      filesHtml=`<div class="msg-files">${m.attachments.map(f=>{
        const fLabel=typeof f==='string'?f:(f&&(f.name||f.filename||f.path))||'';
        const fname=String(fLabel).split('/').pop()||String(fLabel);
        // Use api/file/raw which resolves filename relative to the session workspace.
        const fileUrl='api/file/raw?session_id='+encodeURIComponent(_attachSid)+'&path='+encodeURIComponent(fname);
        return _renderAttachmentHtml(fname,fileUrl);
      }).join('')}</div>`;
    }
    let bodyHtml = _getCachedRender(displayContent, isUser);
    // Message-level media snapshots: settled assistant messages carry a
    // path→digest map (written at settle time) freezing the file bytes the
    // turn emitted. Stamp it AFTER the text-keyed render cache so identical
    // text with different snapshots (old/new comparison) never collides.
    if(!isUser && m && m._media_snapshots && typeof m._media_snapshots==='object'){
      bodyHtml = _stampMediaSnapshots(bodyHtml, m._media_snapshots);
    }
    if(!isUser&&m.provider_details){
      const summary=m.provider_details_label||'Provider details';
      bodyHtml += `<details class="provider-error-details"><summary>${esc(String(summary))}</summary><pre><code>${esc(String(m.provider_details))}</code></pre></details>`;
    }
    const recoveryPayload=(!isUser&&m._compressionRecovery)
      ? m._compressionRecovery
      : (!isUser&&isLastAssistant&&isTurnFinalAssistant&&typeof _activeCompressionRecoveryPayload==='function' ? _activeCompressionRecoveryPayload() : null);
    const recoveryHtml=recoveryPayload ? _compressionRecoveryHtml(recoveryPayload, (S.session&&S.session.session_id)||'') : '';
    if(recoveryHtml) bodyHtml += recoveryHtml;
    const statusHtml = (!isUser&&m._statusCard) ? _statusCardHtml(m._statusCard) : '';
    const isEditableUser=isUser&&rawIdx===lastUserRawIdx;
    const editBtn  = isEditableUser ? `<button class="msg-action-btn" title="${t('edit_message')}" onclick="editMessage(this)">${li('pencil',13)}</button>` : '';
    const undoBtn  = isLastAssistant ? `<button class="msg-action-btn" title="${t('undo_exchange')}" onclick="undoLastExchange()">${li('undo',13)}</button>` : '';
    const retryBtn = isLastAssistant ? `<button class="msg-action-btn" title="${t('regenerate')}" onclick="regenerateResponse(this)">${li('rotate-ccw',13)}</button>` : '';
    const copyBtn  = `<button class="msg-copy-btn msg-action-btn" title="${t('copy')}" onclick="copyMsg(this)">${li('copy',13)}</button>`;
    const readOnlySession=typeof _isReadOnlySession==='function'
      ? _isReadOnlySession(S.session)
      : !!(S.session&&(S.session.read_only||S.session.is_read_only));
    const branchableReadOnlySession=typeof _isBranchableReadOnlySession==='function'
      ? _isBranchableReadOnlySession(S.session)
      : false;
    const forkBtn  = (readOnlySession&&!branchableReadOnlySession) ? '' : `<button class="msg-action-btn" title="${t('fork_from_here')}" onclick="forkFromMessage(${rawIdx+1})">${li('git-branch',13)}</button>`;
    const ttsBtn   = !isUser ? `<button class="msg-action-btn msg-tts-btn" title="${t('tts_listen')||'Listen'}" onclick="speakMessage(this)">${li('volume-2',13)}</button>` : '';
    const memorizeBtn = (!isUser&&!m._live) ? `<button class="msg-action-btn msg-memorize-btn" title="Memorize to Knowledge Vault" onclick="memorizeMessage(this)">${li('bookmark',13)}</button>` : '';
    const tsVal=m._ts||m.timestamp;
    // _formatInServerTz handles fractional-hour offsets (India +0530 etc.)
    // correctly via offset arithmetic; bare toLocaleString is the browser-tz fallback.
    const _fmtSv=(typeof _formatInServerTz==='function')?_formatInServerTz:null;
    const tsTitle=tsVal?(_fmtSv?_fmtSv(new Date(tsVal*1000),{}):new Date(tsVal*1000).toLocaleString()):'';
    const tsTime=_formatMessageFooterTimestamp(tsVal);
    const timeHtml = tsTime ? `<span class="msg-time" title="${esc(tsTitle)}">${tsTime}</span>` : '';
    // #3114: show jump-to-question on every assistant message that has a
    // resolvable question target, not just the turn-final one. Multi-step
    // turns (tool_call -> assistant -> tool_call -> assistant) otherwise
    // strip the button from every intermediate assistant bubble and the
    // user loses the navigation affordance.
    const _qJumpTarget=(!isUser&&!m._live)?questionRawIdxByAssistantRawIdx.get(rawIdx):undefined;
    const questionJumpBtn = (_qJumpTarget!==undefined&&_qJumpTarget!==null)
      ? _questionJumpButtonHtml(_qJumpTarget, assistantRawIdxByQuestionRawIdx.get(_qJumpTarget)??rawIdx)
      : '';
    const footHtml = `<div class="msg-foot">${timeHtml}<span class="msg-actions">${editBtn}${ttsBtn}${forkBtn}${memorizeBtn}${copyBtn}${retryBtn}</span>${questionJumpBtn}</div>`;

    if(_isContextCompactionMessage(m)){
      continue;
    }

    if(isProcessWakeup){
      currentAssistantTurn=null;
      let row=_msgNodeRecycleEnabled?_recycleStash.get(rawIdx):null;
      if(row&&(!row.classList.contains('msg-row')||row.classList.contains('assistant-turn'))) row=null;
      const processText=String(rowDisplayContent||'').trim();
      const processFootHtml=`<div class="msg-foot">${timeHtml}<span class="msg-actions">${copyBtn}</span></div>`;
      // #6345: structured completions/watch-matches render as a collapsed
      // summary card; anything unparseable keeps the raw notice below so the
      // fallback is never worse than the old full-text dump.
      const wakeupInfo=_processWakeupInfo(m, processText);
      let noticeClass='process-wakeup-notice';
      let noticeInnerHtml;
      if(wakeupInfo){
        noticeClass+=' process-wakeup-notice-card';
        const exitStr=wakeupInfo.exitCode==null?'':String(wakeupInfo.exitCode);
        if(wakeupInfo.type==='completion'&&/^-?\d+$/.test(exitStr)&&exitStr!=='0') noticeClass+=' process-wakeup-fail';
        noticeInnerHtml=_processWakeupCardHtml(wakeupInfo, processText, {timeHtml, filesHtml, footHtml:`<div class="msg-foot"><span class="msg-actions">${copyBtn}</span></div>`});
      }else{
        const processTextHtml=processText?`<pre class="process-wakeup-text">${esc(processText)}</pre>`:'';
        noticeInnerHtml=`<div class="process-wakeup-label">${li('terminal',13)}<span>${esc(t('process_wakeup_label'))}</span></div>${filesHtml}<div class="msg-body process-wakeup-body">${processTextHtml}</div>${processFootHtml}`;
      }
      const nextRowHtml=`<div class="${noticeClass}">${noticeInnerHtml}</div>`;
      if(row){
        row.className='msg-row process-wakeup-row';
        row.id=_userMessageDomId(rawIdx);
        row.dataset.msgIdx=rawIdx;
        row.dataset.sessionMsgIdx=_messageSessionIndexForRawIdx(rawIdx);
        row.dataset.messageAnchorKey=_messageViewportAnchorKeyForMessage(m);
        row.dataset.role='process_wakeup';
        delete row.dataset.editing;
        // Compare against the HTML we last SET (expando), not live innerHTML:
        // a user-expanded <details> serializes an open attribute into
        // innerHTML, which would force a rebuild-and-collapse on every
        // streaming rerender. The expando comparison is
        // serialization-independent while still rebuilding when the markup
        // genuinely changes (locale/timestamp format); open state is
        // user-driven, so it is captured and restored across rebuilds.
        if(row.dataset.rawText!==processText||row._wakeupRenderedHtml!==nextRowHtml){
          const _priorCard=row.querySelector&&row.querySelector('details.process-wakeup-card');
          const _wasOpen=!!(_priorCard&&_priorCard.open);
          row.dataset.rawText=processText;
          row._wakeupRenderedHtml=nextRowHtml;
          row.innerHTML=nextRowHtml;
          if(_wasOpen){
            const _card=row.querySelector('details.process-wakeup-card');
            if(_card) _card.open=true;
          }
        }
      }else{
        row=document.createElement('div');
        row.className='msg-row process-wakeup-row';
        row.id=_userMessageDomId(rawIdx);
        row.dataset.msgIdx=rawIdx;
        row.dataset.sessionMsgIdx=_messageSessionIndexForRawIdx(rawIdx);
        row.dataset.messageAnchorKey=_messageViewportAnchorKeyForMessage(m);
        row.dataset.role='process_wakeup';
        row.dataset.rawText=processText;
        row._wakeupRenderedHtml=nextRowHtml;
        row.innerHTML=nextRowHtml;
      }
      inner.appendChild(row);
      userRows.set(rawIdx, row);
      continue;
    }

    if(isUser){
      currentAssistantTurn=null;
      let row=_msgNodeRecycleEnabled?_recycleStash.get(rawIdx):null;
      if(row&&(!row.classList.contains('msg-row')||row.classList.contains('assistant-turn'))) row=null;
      const newRawText=String(displayContent).trim();
      const nextRowHtml=`${filesHtml}<div class="msg-body">${bodyHtml}</div>${footHtml}`;
      if(row){
        row.className='msg-row';
        row.id=_userMessageDomId(rawIdx);
        row.dataset.msgIdx=rawIdx;
        row.dataset.sessionMsgIdx=_messageSessionIndexForRawIdx(rawIdx);
        row.dataset.messageAnchorKey=_messageViewportAnchorKeyForMessage(m);
        row.dataset.role='user';
        delete row.dataset.editing;
        if(row.dataset.rawText!==newRawText||row.innerHTML!==nextRowHtml){
          row.dataset.rawText=newRawText;
          row.innerHTML=nextRowHtml;
        }
      }else{
        row=document.createElement('div');
        row.className='msg-row';
        row.id=_userMessageDomId(rawIdx);
        row.dataset.msgIdx=rawIdx;
        row.dataset.sessionMsgIdx=_messageSessionIndexForRawIdx(rawIdx);
        row.dataset.messageAnchorKey=_messageViewportAnchorKeyForMessage(m);
        row.dataset.role='user';
        row.dataset.rawText=newRawText;
        row.innerHTML=nextRowHtml;
      }
      // Reserve this user row's real off-screen height up front so a wipe-and-rebuild
      // does not collapse scrollHeight to the flat 96px estimate (the collapse that
      // clamps/re-anchors the viewport on mobile — #5637/#5638, both jump classes). Uses
      // the remembered measured height when this row has been measured before, else a
      // content-length estimate; the measure pass refines it exactly next frame. The
      // typeof guard keeps renderMessages runnable in the node test harnesses that
      // extract it without this helper (they stub every collaborator by name).
      if(typeof _applyUserRowIntrinsicHeight==='function') _applyUserRowIntrinsicHeight(row, newRawText);
      inner.appendChild(row);
      userRows.set(rawIdx, row);
      continue;
    }

    if(!currentAssistantTurn){
      let recycled=_msgNodeRecycleEnabled?_recycleStash.get(rawIdx):null;
      if(recycled&&!recycled.classList.contains('assistant-turn')) recycled=null;
      if(recycled){
        const blocks=_assistantTurnBlocks(recycled);
        if(blocks) blocks.innerHTML='';
        for(const attr of _recycleResetAttrs) recycled.removeAttribute(attr);
        const role=recycled.querySelector('.msg-role.assistant');
        if(role) role.outerHTML=_assistantRoleHtml(tsTitle, isTpsDisplayEnabled()?_formatTurnTps(m._turnTps):'');
        currentAssistantTurn=recycled;
      }else{
        currentAssistantTurn=_createAssistantTurn(tsTitle, isTpsDisplayEnabled()?_formatTurnTps(m._turnTps):'');
      }
      currentAssistantTurn.dataset.role='assistant';
      if(S.session) currentAssistantTurn.dataset.sessionId=S.session.session_id;
      currentAssistantTurn.dataset.recycleKey=rawIdx;
      inner.appendChild(currentAssistantTurn);
    }
    _setLatestAssistantTurnLandmark(currentAssistantTurn, !m._live&&rawIdx===latestRenderedAssistantRawIdx);
    const seg=document.createElement('div');
    if(Array.isArray(orderedTransparentParts)&&orderedTransparentParts.length){
      const blocks=_assistantTurnBlocks(currentAssistantTurn);
      const sessionMsgIdx=_messageSessionIndexForRawIdx(rawIdx);
      const messageAnchorKey=_messageViewportAnchorKeyForMessage(m);
      const lastTextPartIdx=(()=>{
        for(let i=orderedTransparentParts.length-1;i>=0;i--){
          if(
            orderedTransparentParts[i]&&
            orderedTransparentParts[i].kind==='text'&&
            String(_transparentOrderedDisplayText(orderedTransparentParts[i].text)).trim()
          ) return i;
        }
        return -1;
      })();
      let firstSeg=null;
      if(thinkingText&&window._showThinking!==false){
        if((isCompactWorklogMode()||isTransparentStream())&&_assistantThinkingBelongsInWorklog(m, rawIdx, toolCallAssistantIdxs)) assistantThinking.set(rawIdx, thinkingText);
      }
      orderedTransparentParts.forEach((part, partIdx)=>{
        if(!part) return;
        if(part.kind==='tool'){
        const toolCall=_transparentOrderedToolCall(part, rawIdx, transparentOrderedToolCallsByTid, transparentToolResultsByTid, transparentPersistedSnippetByTid);
          const toolRow=_decorateTransparentEventRow(buildToolCard(toolCall),{
            type:'tool',
            name:toolCall&&toolCall.name,
            status:_transparentToolStatus(toolCall,true),
            toolCall,
            segmentSeq:toolCall&&toolCall.activitySegmentSeq,
            burstId:(toolCall&&toolCall.activityBurstId)||m._activityBurstId,
          });
          blocks.appendChild(toolRow);
          if(part.toolUseId) transparentOrderedToolIds.add(part.toolUseId);
          return;
        }
        const orderedSeg=document.createElement('div');
        const partDisplayText=_transparentOrderedDisplayText(part.text);
        if(!String(partDisplayText).trim()) return;
        orderedSeg.className='assistant-segment';
        orderedSeg.dataset.msgIdx=rawIdx;
        orderedSeg.dataset.sessionMsgIdx=sessionMsgIdx;
        orderedSeg.dataset.messageAnchorKey=messageAnchorKey;
        orderedSeg.dataset.rawText=String(partDisplayText||'').trim();
        if(m._activityBurstId!==undefined&&m._activityBurstId!==null) orderedSeg.setAttribute('data-activity-burst-id',String(m._activityBurstId));
        if(Number.isFinite(Number(m._liveSegmentSeq))) orderedSeg.setAttribute('data-live-segment-seq',String(Number(m._liveSegmentSeq)));
        if(_ERR_MSG_RE.test(String(partDisplayText||'').trim())) orderedSeg.dataset.error='1';
        if(!firstSeg&&thinkingText&&window._showThinking!==false&&!((isCompactWorklogMode()||isTransparentStream())&&_assistantThinkingBelongsInWorklog(m, rawIdx, toolCallAssistantIdxs))) orderedSeg.insertAdjacentHTML('beforeend', _thinkingCardHtml(thinkingText));
        const isLastTextPart=partIdx===lastTextPartIdx;
        const partBodyHtml=_getCachedRender(partDisplayText,false);
        // Message-level media snapshots: transparent ordered segments carry the
        // same per-message path→digest map as the main transcript; stamp it so
        // historical previews freeze (&snap=) instead of following overwrites.
        // Inlined in the template (no intermediate variable) so test-harness
        // block extraction of the ordered-segment slice stays self-contained.
        if(isLastTextPart&&statusHtml){
          orderedSeg.insertAdjacentHTML('beforeend', statusHtml);
        }
        orderedSeg.insertAdjacentHTML('beforeend', `${isLastTextPart?filesHtml:''}<div class="msg-body">${(typeof m!=='undefined'&&m&&m._media_snapshots&&typeof m._media_snapshots==='object')?_stampMediaSnapshots(partBodyHtml,m._media_snapshots):partBodyHtml}</div>${isLastTextPart?footHtml:''}`);
        blocks.appendChild(orderedSeg);
        if(!firstSeg) firstSeg=orderedSeg;
      });
      assistantSegments.set(rawIdx, firstSeg||null);
      continue;
    }
    seg.className='assistant-segment';
    seg.dataset.msgIdx=rawIdx;
    seg.dataset.sessionMsgIdx=_messageSessionIndexForRawIdx(rawIdx);
    seg.dataset.messageAnchorKey=_messageViewportAnchorKeyForMessage(m);
    seg.dataset.rawText=String(content).trim();
    if(m._activityBurstId!==undefined&&m._activityBurstId!==null) seg.setAttribute('data-activity-burst-id',String(m._activityBurstId));
    if(Number.isFinite(Number(m._liveSegmentSeq))) seg.setAttribute('data-live-segment-seq',String(Number(m._liveSegmentSeq)));
    const messageBelongsInWorklog=!S.busy&&isCompactWorklogMode()&&_assistantMessageBelongsInWorklog(m, rawIdx, toolCallAssistantIdxs, displayContent, {isTurnFinalAssistant});
    if(messageBelongsInWorklog){
      seg.classList.add('assistant-segment-worklog-source');
      seg.setAttribute('aria-hidden','true');
      seg.hidden=true;
    }
    if(m._live){
      currentAssistantTurn.id='liveAssistantTurn';
      // Stamp the session id on the live turn so finalizeThinkingCard()
      // and other late callbacks can verify they're operating on the
      // right session's DOM (the user may have switched tabs/sessions
      // while this stream is still streaming). See #1366.
      if(S.session) currentAssistantTurn.dataset.sessionId=S.session.session_id;
      seg.setAttribute('data-live-assistant','1');
    }
    if(_ERR_MSG_RE.test(String(content||'').trim())) seg.dataset.error='1';
    // A turn whose visible content is empty but which carries a separate
    // `reasoning` field (e.g. a run-journal-recovered anchor: empty content +
    // reasoning + `_recovered_from_run_journal`) extracts NO inline thinkingText
    // and would render no Thinking Card at all — collapsing to an empty hidden
    // anchor. A session made entirely of such rows then paints blank (only date
    // separators) — the #3875 reporter's exact case (Compact tool activity OFF,
    // i.e. legacy mode). Surface the message's reasoning payload as the Thinking
    // Card source for these empty-content turns so the turn is never blank.
    //
    // LEGACY-MODE ONLY (!isSimplifiedToolCalling()): the simplified/Worklog path
    // already derives reasoning above (line ~8149 via
    // _worklogReasoningTextFromMessage, which strips an exact visible-answer echo
    // so reasoning duplicating a sibling answer is not re-shown). Repopulating the
    // raw reasoning here would bypass that echo-strip and re-render the duplicate
    // as a Worklog Thinking card (Codex gate catch). In legacy mode there is no
    // Worklog folding, so the raw payload is the correct Thinking-card source.
    // Stays OUT of the inline-content `thinkingText` extraction block (#2565) and
    // only fires for empty-content/no-inline-thinking turns, so answer-bearing
    // messages are unchanged.
    if(!isUser&&!m._live&&!isSimplifiedToolCalling()&&!thinkingText&&!String(content||'').trim()&&!filesHtml&&!statusHtml){
      const _reasoningPayload=_assistantReasoningPayloadText(m);
      if(_reasoningPayload) thinkingText=_reasoningPayload;
    }
    if(thinkingText&&window._showThinking!==false){
      if((isCompactWorklogMode()||isTransparentStream())&&_assistantThinkingBelongsInWorklog(m, rawIdx, toolCallAssistantIdxs)) assistantThinking.set(rawIdx, thinkingText);
      else if(window._showThinking!==false) seg.insertAdjacentHTML('beforeend', _thinkingCardHtml(thinkingText));
    }
    const hasVisibleBody=!!(String(content||'').trim()||filesHtml||recoveryHtml);
    if(statusHtml){
      seg.insertAdjacentHTML('beforeend', statusHtml);
      if(hasVisibleBody) seg.insertAdjacentHTML('beforeend', `${filesHtml}<div class="msg-body">${bodyHtml}</div>${footHtml}`);
    }else if(hasVisibleBody){
      seg.insertAdjacentHTML('beforeend', `${filesHtml}<div class="msg-body">${bodyHtml}</div>${footHtml}`);
    }else if(!(thinkingText&&window._showThinking!==false&&!isSimplifiedToolCalling())){
      seg.classList.add('assistant-segment-anchor');
    }
    _assistantTurnBlocks(currentAssistantTurn).appendChild(seg);
    assistantSegments.set(rawIdx, seg);
  }

  function _insertCompressionLikeNode(node, anchorIndex){
    if(!node) return;
    const anchorIdx=anchorIndex===undefined?insertionAnchor:anchorIndex;
    if(anchorIdx!==null && renderVisWithIdx[anchorIdx]){
      const anchorRawIdx=renderVisWithIdx[anchorIdx].rawIdx;
      const anchorSeg=assistantSegments.get(anchorRawIdx);
      if(anchorSeg){
        const turn=anchorSeg.closest('.assistant-turn');
        const blocks=_assistantTurnBlocks(turn);
        if(blocks){
          blocks.appendChild(node);
          return;
        }
      }
      const userRow=userRows.get(anchorRawIdx);
      if(userRow && userRow.parentElement){
        userRow.parentElement.insertBefore(node, userRow.nextSibling);
        return;
      }
    }
    inner.appendChild(node);
  }
  function _insertCompressionLikeNodeByRawIdx(node, rawIdx){
    if(!node) return;
    if(rawIdx<firstRenderedRawIdx) return;
    if(!renderVisWithIdx.length){
      inner.appendChild(node);
      return;
    }
    let anchorIdx=null;
    for(let i=0;i<renderVisWithIdx.length;i++){
      if(renderVisWithIdx[i].rawIdx > rawIdx){
        anchorIdx=i;
        break;
      }
    }
    if(anchorIdx===null){
      inner.appendChild(node);
      return;
    }
    const anchorRawIdx=renderVisWithIdx[anchorIdx].rawIdx;
    const anchorSeg=assistantSegments.get(anchorRawIdx);
    if(anchorSeg){
      const turn=anchorSeg.closest('.assistant-turn');
      const blocks=_assistantTurnBlocks(turn);
      if(blocks){
        blocks.insertBefore(node, anchorSeg);
        return;
      }
      const turnParent=turn && turn.parentElement;
      if(turnParent){
        turnParent.insertBefore(node, turn);
        return;
      }
    }
    const userRow=userRows.get(anchorRawIdx);
    if(userRow && userRow.parentElement){
      userRow.parentElement.insertBefore(node, userRow);
      return;
    }
    inner.appendChild(node);
  }
  const preservedOnlyNode=(!preservedCompressionTaskCardsAttached&&(!referenceNode||compressionState)&&preservedCompressionTaskMessages.length)
    ? (()=>{const row=document.createElement('div');row.innerHTML=`<div class="compression-turn"><div class="compression-turn-blocks">${_preservedCompressionTaskListCardsHtml(preservedCompressionTaskMessages)}</div></div>`;return row.firstElementChild;})()
    : null;
  const preservedOnlyAnchor=preservedCompressionRawIdxs.length
    ? (()=>{let idx=null;for(let i=0;i<renderVisWithIdx.length;i++){if(renderVisWithIdx[i].rawIdx<preservedCompressionRawIdxs[0]) idx=i;}return idx;})()
    : null;
  const handoffSummaryStates=_collectHandoffSummaryStates(S.messages);

  _insertCompressionLikeNode(compressionNode);
  if(referenceNode&&referenceMessageRawIdx>=0) _insertCompressionLikeNodeByRawIdx(referenceNode, referenceMessageRawIdx);
  else _insertCompressionLikeNode(referenceNode);
  _insertCompressionLikeNode(preservedOnlyNode, preservedOnlyAnchor);
  _insertCompressionLikeNode(handoffState?_handoffCardsNode(handoffState):null, renderVisWithIdx.length?renderVisWithIdx.length-1:null);
  for(const entry of handoffSummaryStates){
    if(!entry||!entry.state) continue;
    if(entry.rawIdx<firstRenderedRawIdx) continue;
    _insertCompressionLikeNodeByRawIdx(_handoffCardsNode(entry.state), entry.rawIdx);
  }
  renderCompressionUi();
  const anchorOwnedAssistantRawIdxs=new Set();
  for(const [rawIdx,seg] of assistantSegments){
    const msg=S.messages[rawIdx];
    if(!msg||!msg._anchor_activity_scene||!seg) continue;
    const turn=seg.closest('.assistant-turn');
    if(!turn) continue;
    turn.querySelectorAll('.assistant-segment[data-msg-idx]').forEach(node=>{
      const idx=Number(node.getAttribute('data-msg-idx'));
      if(Number.isFinite(idx)) anchorOwnedAssistantRawIdxs.add(idx);
    });
  }
  // Insert settled tool call cards (history view only).
  // During live streaming, tool cards are rendered in #liveToolCards by the
  // tool SSE handler and never mixed into the message list until done fires.
  //
  // Fallback: if S.toolCalls is empty (sessions that predate session-level tool
  // tracking, or runs that didn't go through the normal streaming path), build
  // a display list from per-message tool_calls (OpenAI format) stored in each
  // assistant message. This covers the reload case described in issue #140.
  const hasMessageToolMetadata=!S.busy&&Array.isArray(S.messages)&&S.messages.some((m,rawIdx)=>
    !anchorOwnedAssistantRawIdxs.has(rawIdx)&&_legacySettledFallbackHasToolMetadata(m)
  );
  if(!S.busy && (hasMessageToolMetadata||!S.toolCalls||!S.toolCalls.length)){
    // Index tool outputs by tool_call_id / tool_use_id so the
    // fallback-built cards carry their result snippet (not just the command).
    // Without this step CLI-origin sessions reload with empty tool cards.
    const resultsByTid={};
    const fallbackToolSources=[];
    // Durable fallback: the persisted compact summary (session.tool_calls, built
    // by _extract_tool_calls_from_messages) carries a bounded result `snippet`
    // keyed by tid. On a cold/paginated load where the role:tool result-message
    // join below misses (id mismatch, recovery-rebuilt turn), use this so the
    // terminal output / diff body still renders instead of vanishing (#4927).
    const persistedSnippetByTid={};
    try{
      const persisted=(S.session&&Array.isArray(S.session.tool_calls))?S.session.tool_calls:[];
      persisted.forEach(tc=>{
        if(!tc||typeof tc!=='object') return;
        const ptid=tc.tid||tc.id||tc.tool_call_id||tc.call_id||'';
        const psnip=tc.snippet||tc.result||tc.output||tc.preview||'';
        if(ptid&&psnip&&!persistedSnippetByTid[ptid]) persistedSnippetByTid[ptid]=String(psnip);
      });
    }catch(e){}
    S.messages.forEach((m,rawIdx)=>{
      if(!m) return;
      // OpenAI / AGY format: role=tool with tool_call_id
      if(m.role==='tool'){
        const tid=m.tool_call_id||m.tool_use_id||'';
        if(tid) resultsByTid[tid]=_cliToolResultSnippet(m.content);
        return;
      }
      // Anthropic format: tool_result blocks inside a user message content array
      if(Array.isArray(m.content)){
        m.content.forEach(p=>{
          if(!p||typeof p!=='object'||p.type!=='tool_result') return;
          const tid=p.tool_use_id||'';
          if(!tid) return;
          const raw=typeof p.content==='string'?p.content
                   :Array.isArray(p.content)?p.content.map(c=>c&&c.text?c.text:'').join('')
                   :'';
          resultsByTid[tid]=_cliToolResultSnippet(raw);
        });
      }
      if(m.role==='assistant'){
        if(anchorOwnedAssistantRawIdxs.has(rawIdx)) return;
        if(_legacySettledFallbackHasToolMetadata(m)) fallbackToolSources.push({m,rawIdx});
      }
    });
    const derived=[];
    const liveToolMetadata=Array.isArray(S._settledLiveToolMetadata)
      ? S._settledLiveToolMetadata
      : (Array.isArray(S.toolCalls)?S.toolCalls:[]);
    const liveMetadataByTid=new Map();
    liveToolMetadata.forEach((tc,idx)=>{
      if(!tc||typeof tc!=='object') return;
      const tid=tc.tid||tc.id||tc.tool_call_id||tc.call_id||'';
      if(tid&&!liveMetadataByTid.has(tid)) liveMetadataByTid.set(tid,{tc,idx});
    });
    const usedLiveToolMetadata=new Set();
    const copyLiveToolMetadata=(next,name,tid)=>{
      let matchEntry=tid?liveMetadataByTid.get(tid):null;
      if(!matchEntry){
        const matchIdx=liveToolMetadata.findIndex((tc,i)=>tc&&!usedLiveToolMetadata.has(i)&&(!name||tc.name===name));
        if(matchIdx>=0) matchEntry={tc:liveToolMetadata[matchIdx],idx:matchIdx};
      }
      if(matchEntry){
        usedLiveToolMetadata.add(matchEntry.idx);
        const live=matchEntry.tc||{};
        for(const key of ['activityBurstId','duration','started_at']){
          if((next[key]===undefined||next[key]===null)&&live[key]!==undefined&&live[key]!==null) next[key]=live[key];
        }
      }
      return next;
    };
    fallbackToolSources.forEach(({m,rawIdx})=>{
      const assistantToolAnchorIdx=_assistantToolAnchorIdxForMessage(S.messages,rawIdx);
      // OpenAI format: top-level tool_calls field on the assistant message
      (m.tool_calls||[]).forEach(tc=>{
        if(!tc||typeof tc!=='object') return;
        const fn=tc.function||{};
        const name=fn.name||tc.name||'tool';
        let args={};
        try{ args=JSON.parse(fn.arguments||'{}'); }catch(e){}
        const tid=tc.id||tc.call_id||'';
        const patchSnippet=_cliPatchSnippetFromArgs(name,args);
        const resultSnippet=resultsByTid[tid]||persistedSnippetByTid[tid]||'';
        let argsSnap=_toolArgsSnapshot(args);
        derived.push(copyLiveToolMetadata({
          name,
          snippet:_cliToolCardSnippet(resultSnippet,patchSnippet),
          is_diff:_cliToolCardHasDiffSnippet(resultSnippet,patchSnippet),
          tid,
          assistant_msg_idx:assistantToolAnchorIdx,
          args:argsSnap,
          done:true,
        }, name, tid));
      });
      // WebUI partial/live format: _partial_tool_calls snapshots survive
      // interrupted or adapter-shaped settles even when session.tool_calls is empty.
      const partialToolCalls=Array.isArray(m._partial_tool_calls)?m._partial_tool_calls:[];
      partialToolCalls.forEach(tc=>{
        if(!tc||typeof tc!=='object') return;
        const fn=tc.function||{};
        const name=tc.name||fn.name||'tool';
        let args=tc.args||tc.input||{};
        if(!args||typeof args!=='object'){
          try{ args=JSON.parse(fn.arguments||'{}'); }catch(e){ args={}; }
        }else if(!Object.keys(args).length&&fn.arguments){
          try{ args=JSON.parse(fn.arguments||'{}'); }catch(e){}
        }
        const tid=tc.tid||tc.id||tc.tool_call_id||tc.call_id||'';
        const patchSnippet=_cliPatchSnippetFromArgs(name,args);
        const resultSnippet=resultsByTid[tid]||tc.snippet||tc.preview||persistedSnippetByTid[tid]||'';
        const argsSnap=_toolArgsSnapshot(args);
        derived.push(copyLiveToolMetadata({
          name,
          snippet:_cliToolCardSnippet(resultSnippet,patchSnippet),
          is_diff:_cliToolCardHasDiffSnippet(resultSnippet,patchSnippet),
          tid,
          assistant_msg_idx:assistantToolAnchorIdx,
          args:argsSnap,
          done:true,
        }, name, tid));
      });
      // Anthropic format: tool_use blocks inside assistant content array
      if(Array.isArray(m.content)){
        m.content.forEach(p=>{
          if(!p||typeof p!=='object'||p.type!=='tool_use') return;
          const name=p.name||'tool';
          const args=p.input||{};
          const tid=p.id||'';
          const patchSnippet=_cliPatchSnippetFromArgs(name,args);
          const resultSnippet=resultsByTid[tid]||persistedSnippetByTid[tid]||'';
          const argsSnap=_toolArgsSnapshot(args);
          derived.push(copyLiveToolMetadata({
            name,
            snippet:_cliToolCardSnippet(resultSnippet,patchSnippet),
            is_diff:_cliToolCardHasDiffSnippet(resultSnippet,patchSnippet),
            tid,
            assistant_msg_idx:assistantToolAnchorIdx,
            args:argsSnap,
            done:true,
          }, name, tid));
        });
      }
      // WebUI-internal partial tool calls captured on cancel/stop
      // (private shape: name/args/done/preview/snippet, no OpenAI envelope).
      if(Array.isArray(m._partial_tool_calls)){
        m._partial_tool_calls.forEach(tc=>{
          if(!tc||typeof tc!=='object') return;
          const name=tc.name||'tool';
          const args=tc.args||{};
          const tid=tc.id||tc.call_id||tc.tool_call_id||tc.tid||'';
          const patchSnippet=_cliPatchSnippetFromArgs(name,args);
          const resultSnippet=_cliToolResultSnippet(tc.snippet||tc.result||tc.output||tc.preview||'');
          const argsSnap=_toolArgsSnapshot(args,4);
          derived.push(copyLiveToolMetadata({
            name,
            snippet:_cliToolCardSnippet(resultSnippet,patchSnippet),
            is_diff:_cliToolCardHasDiffSnippet(resultSnippet,patchSnippet),
            tid,
            assistant_msg_idx:assistantToolAnchorIdx,
            args:argsSnap,
            done:true,
          }, name, tid));
        });
      }
    });
    if(derived.length) S.toolCalls=derived;
    if(S._settledLiveToolMetadata) S._settledLiveToolMetadata=null;
  }
  if(!S.busy || (S.toolCalls&&S.toolCalls.length)){
    // Rebuild settled tool/worklog/thinking nodes. The `|| (S.toolCalls.length)`
    // arm is REQUIRED, not just `!S.busy`: when renderMessages re-runs during an
    // active stream (e.g. switching back to an in-progress session, busy=true),
    // the earlier innerHTML wipe removed every settled turn's worklog above the
    // live turn. Gating purely on `!S.busy` skipped this rebuild while busy and
    // left those prior turns' tool cards gone until the stream finished (#3401
    // regression vs master; same content-loss-on-switch class as #3668). The
    // `:not([data-live-thinking="1"])` / live-card guards below keep the active
    // turn's own live nodes from being double-built.
    inner.querySelectorAll('.tool-worklog-group:not([data-compression-card]),.tool-call-group:not([data-compression-card]),.tool-card-row:not([data-compression-card]):not([data-event-type="tool"]),.agent-activity-thinking:not([data-live-thinking="1"]):not([data-event-type="thinking"]),.wl-reason[data-worklog-anchor-reason="1"],.wl-reason[data-worklog-reason-source="reasoning"]').forEach(el=>el.remove());
    const byActivity = new Map();
    const assistantIdxs=[...assistantSegments.keys()].sort((a,b)=>a-b);
    const _assistantAnchorForActivity=(aIdx,segmentSeq,burstId)=>{
      if(segmentSeq){
        for(const seg of assistantSegments.values()){
          if(seg&&seg.getAttribute('data-live-segment-seq')===String(segmentSeq)) return seg;
        }
      }
      const wantedBurst=burstId!==undefined&&burstId!==null&&String(burstId)!==''&&String(burstId)!=='0'?String(burstId):'';
      if(wantedBurst){
        for(const seg of assistantSegments.values()){
          if(seg&&seg.getAttribute('data-activity-burst-id')===wantedBurst) return seg;
        }
      }
      let anchorRow=assistantSegments.get(aIdx)||null;
      if(!anchorRow&&assistantIdxs.length){
        if(aIdx<assistantIdxs[0]) return null;
        const fallbackIdx=[...assistantIdxs].reverse().find(idx=>idx<=aIdx);
        anchorRow=fallbackIdx!==undefined?assistantSegments.get(fallbackIdx):assistantSegments.get(assistantIdxs[assistantIdxs.length-1]);
      }
      return anchorRow;
    };
    const _turnDurationForAnchor=(anchorRow)=>{
      if(!anchorRow) return undefined;
      const turn=anchorRow.closest('.assistant-turn');
      const blocks=_assistantTurnBlocks(turn);
      if(!blocks) return undefined;
      let duration;
      for(const seg of blocks.querySelectorAll('.assistant-segment')){
        const idx=Number(seg.dataset&&seg.dataset.msgIdx);
        const msg=Number.isFinite(idx)?S.messages[idx]:null;
        if(msg&&msg._turnDuration!==undefined) duration=msg._turnDuration;
      }
      return duration;
    };
    const durationAssignedTurns = new Set();
    const activityByTurn = new Map();
    const activityOrder = [];
    const ensureActivityBucket=(key,aIdx,segmentSeq,burstId)=>{
      if(!byActivity.has(key)){
        const entry={key,aIdx,segmentSeq:segmentSeq||'',burstId:burstId||'',cards:[],thinkingIdx:null,includeAnchorReason:false};
        byActivity.set(key,entry);
        activityOrder.push(entry);
      }
      return byActivity.get(key);
    };
    const normalizeToken=(value)=>{
      const hasValue=value!==undefined&&value!==null&&String(value)!==''&&String(value)!=='0';
      return hasValue?String(value):'';
    };
    const knownBurstIds=new Set();
    for(const s of assistantSegments.values()) if(s){const b=s.getAttribute('data-activity-burst-id');if(b)knownBurstIds.add(b);}
    for(const tc of (S.toolCalls||[])){
      if(!tc) continue;
      const tid=tc.tid||tc.id||tc.tool_call_id||tc.tool_use_id||tc.call_id||'';
      if(tid&&transparentOrderedToolIds.has(tid)) continue;
      const aIdx=tc.assistant_msg_idx!==undefined?parseInt(tc.assistant_msg_idx):-1;
      if(anchorOwnedAssistantRawIdxs.has(aIdx)) continue;
      if(virtualWindow.virtualized&&renderableRawIdxs.has(aIdx)&&!renderedRawIdxs.has(aIdx)) continue;
      const segmentSeq=normalizeToken(tc.activitySegmentSeq);
      const burstId=normalizeToken(tc.activityBurstId);
      const burstResolvable=burstId&&knownBurstIds.has(burstId);
      const key=segmentSeq?`segment:${segmentSeq}`:(burstResolvable?`burst:${burstId}`:`assistant:${aIdx}`);
      const entry=ensureActivityBucket(key,aIdx,segmentSeq,burstId);
      entry.cards.push(tc);
      entry.includeAnchorReason=true;
    }
    for(const aIdx of assistantThinking.keys()){
      if(anchorOwnedAssistantRawIdxs.has(aIdx)) continue;
      if(virtualWindow.virtualized&&renderableRawIdxs.has(aIdx)&&!renderedRawIdxs.has(aIdx)) continue;
      const seg=assistantSegments.get(aIdx);
      const segmentSeq=seg&&seg.getAttribute('data-live-segment-seq')||'';
      const burstId=seg&&seg.getAttribute('data-activity-burst-id')||'';
      const key=segmentSeq?`segment:${segmentSeq}`:(burstId?`burst:${burstId}`:`assistant:${aIdx}`);
      const entry=ensureActivityBucket(key,aIdx,segmentSeq,burstId);
      if(entry.thinkingIdx===null) entry.thinkingIdx=aIdx;
    }
    for(const [aIdx,seg] of assistantSegments){
      if(anchorOwnedAssistantRawIdxs.has(aIdx)) continue;
      if(!seg||!seg.classList||!seg.classList.contains('assistant-segment-worklog-source')) continue;
      if(virtualWindow.virtualized&&renderableRawIdxs.has(aIdx)&&!renderedRawIdxs.has(aIdx)) continue;
      if(!_worklogReasonHtmlFromAnchor(seg)) continue;
      const segmentSeq=seg&&seg.getAttribute('data-live-segment-seq')||'';
      const burstId=seg&&seg.getAttribute('data-activity-burst-id')||'';
      const key=segmentSeq?`segment:${segmentSeq}`:(burstId?`burst:${burstId}`:`assistant:${aIdx}`);
      const entry=ensureActivityBucket(key,aIdx,segmentSeq,burstId);
      entry.includeAnchorReason=true;
    }
    activityOrder.sort((a,b)=>{
      const anchorA=_assistantAnchorForActivity(a.aIdx,a.segmentSeq,a.burstId);
      const anchorB=_assistantAnchorForActivity(b.aIdx,b.segmentSeq,b.burstId);
      const idxA=(anchorA&&anchorA.parentElement)?Array.prototype.indexOf.call(anchorA.parentElement.children,anchorA):Number.MAX_SAFE_INTEGER;
      const idxB=(anchorB&&anchorB.parentElement)?Array.prototype.indexOf.call(anchorB.parentElement.children,anchorB):Number.MAX_SAFE_INTEGER;
      if(idxA!==idxB) return idxA-idxB;
      const seqA=a.segmentSeq!==''?Number(a.segmentSeq):Number.MAX_SAFE_INTEGER;
      const seqB=b.segmentSeq!==''?Number(b.segmentSeq):Number.MAX_SAFE_INTEGER;
      if(Number.isFinite(seqA)&&Number.isFinite(seqB)&&seqA!==seqB) return seqA-seqB;
      const burstA=a.burstId!==''?Number(a.burstId):Number.MAX_SAFE_INTEGER;
      const burstB=b.burstId!==''?Number(b.burstId):Number.MAX_SAFE_INTEGER;
      if(Number.isFinite(burstA)&&Number.isFinite(burstB)&&burstA!==burstB) return burstA-burstB;
      return a.aIdx-b.aIdx;
    });
    if(!isTransparentStream()){
      for(const entry of activityOrder){
        const {aIdx,segmentSeq,burstId,cards,thinkingIdx,includeAnchorReason}=entry;
        if(aIdx<assistantIdxs[0]) continue;
        const anchorRow=_assistantAnchorForActivity(aIdx,segmentSeq,burstId);
        if(!anchorRow) continue;
        const anchorParent=anchorRow.parentElement;
        const anchorReasonHtml=_worklogReasonHtmlFromAnchor(anchorRow);
        const thinkingText=thinkingIdx!==null?assistantThinking.get(thinkingIdx):'';
        if(!cards.length&&!anchorReasonHtml&&!thinkingText) continue;
        const anchorTurn=anchorRow.closest('.assistant-turn');
        if(!anchorTurn) continue;
        let state=activityByTurn.get(anchorTurn);
        if(!state){
          const includeTurnDuration=!durationAssignedTurns.has(anchorTurn);
          if(includeTurnDuration) durationAssignedTurns.add(anchorTurn);
          const activityKey=`assistant:${aIdx}`;
          const anchorIsWorklogSource=anchorRow.classList&&anchorRow.classList.contains('assistant-segment-worklog-source');
          const group=ensureActivityGroup(anchorParent,{
            collapsed:true,
            anchor:anchorRow,
            beforeAnchor:!!thinkingText&&!anchorIsWorklogSource,
            syncAnchorReason:anchorIsWorklogSource,
            activityKey,
            burstId:burstId||'',
            segmentSeq:segmentSeq||'',
            turnDuration:includeTurnDuration?_turnDurationForAnchor(anchorRow):undefined,
          });
          const list=_toolWorklogListEl(group);
          if(!list) continue;
          list.innerHTML='';
          state={group,cards:[],seenReasons:new Set(),seenTools:new Set()};
          activityByTurn.set(anchorTurn,state);
        }
        state.cards.push(...cards);
        _appendWorklogStep(state.group, anchorRow, cards, thinkingText, {
          live:false,
          includeAnchorReason:!!includeAnchorReason&&!!anchorReasonHtml,
          thinkingKey:thinkingText?`thinking:${_normalizeThinkingEchoCompare(thinkingText)}`:'',
          thinkingDisclosureKey:thinkingText?`thinking:${entry.key}`:'',
          seenReasons:state.seenReasons,
          seenTools:state.seenTools,
        });
      }
      activityByTurn.forEach(state=>{
        _syncToolCallGroupSummary(state.group);
      });
    }else{
      // ── transparent_stream path: individual expandable event rows ──
      const transparentInsertCursors=new Map();
      // Per-turn dedup of echoed thinking text — mirrors the compact-worklog
      // path's `seenReasons` Set (the transparent branch previously had none,
      // so the same echoed reasoning rendered twice, once out of chronological
      // position). Keyed by the assistant turn element. (Trifecta finding O-Bug1.)
      const transparentSeenThinking=new Map();
      for(const entry of activityOrder){
        const {aIdx,segmentSeq,burstId,cards,thinkingIdx,includeAnchorReason}=entry;
        const sourceMsg=aIdx>=0?S.messages[aIdx]:null;
        const event={
          ...entry,
          ts:sourceMsg&&((sourceMsg._ts!==undefined&&sourceMsg._ts!==null)?sourceMsg._ts:sourceMsg.timestamp),
          thinkingText:thinkingIdx!==null?assistantThinking.get(thinkingIdx):'',
        };
        if(aIdx<assistantIdxs[0]) continue;
        const anchorRow=_assistantAnchorForActivity(aIdx,segmentSeq,burstId);
        if(!anchorRow) continue;
        const anchorTurn=anchorRow.closest('.assistant-turn');
        const turn=anchorTurn;
        const blocks=_assistantTurnBlocks(anchorTurn);
        if(!anchorTurn||!blocks) continue;
        const anchorIsWorklogSource=anchorRow.classList&&anchorRow.classList.contains('assistant-segment-worklog-source');
        const insertAfterCursor=(row)=>{
          const cursor=transparentInsertCursors.get(anchorRow)||anchorRow;
          const ref=cursor&&cursor.parentElement===blocks?cursor.nextElementSibling:null;
          if(ref&&ref.parentElement===blocks) blocks.insertBefore(row,ref);
          else blocks.appendChild(row);
          transparentInsertCursors.set(anchorRow,row);
        };
        const insertBeforeAnchor=(row)=>{
          if(anchorRow&&anchorRow.parentElement===blocks) blocks.insertBefore(row,anchorRow);
          else blocks.appendChild(row);
        };
        if(event.thinkingText){
          const _thinkKey=typeof _normalizeThinkingEchoCompare==='function'
            ? _normalizeThinkingEchoCompare(event.thinkingText)
            : String(event.thinkingText).trim();
          let _seen=transparentSeenThinking.get(anchorTurn);
          if(!_seen){_seen=new Set();transparentSeenThinking.set(anchorTurn,_seen);}
          if(_thinkKey&&_seen.has(_thinkKey)){
            // Echoed reasoning already rendered for this turn — skip the duplicate.
          }else{
            if(_thinkKey)_seen.add(_thinkKey);
            const thinkingRow=_decorateTransparentEventRow(_thinkingActivityNode(event.thinkingText,false),{
              type:'thinking',
              text:event.thinkingText,
              preview:event.thinkingText,
              ts:event.ts,
              segmentSeq,
              burstId,
            });
            if(!anchorIsWorklogSource) insertBeforeAnchor(thinkingRow);
            else insertAfterCursor(thinkingRow);
          }
        }
        for(const toolCall of cards){
          event.toolCall=toolCall;
          const toolRow=_decorateTransparentEventRow(buildToolCard(event.toolCall),{
            type:'tool',
            name:event.toolCall&&event.toolCall.name,
            status:_transparentToolStatus(event.toolCall,true),
            toolCall:event.toolCall,
            ts:event.ts,
            segmentSeq,
            burstId,
          });
          insertAfterCursor(toolRow);
        }
        _syncTransparentEventControls(turn);
      }
    }
  }
  for(const [rawIdx,seg] of assistantSegments){
    const msg=S.messages[rawIdx];
    if(msg&&msg._anchor_activity_scene){
      _renderSettledAnchorSceneForMessage(msg, seg, rawIdx);
    }
  }
  _restoreWorklogDetailDisclosureState(inner, worklogDetailDisclosureState);
  // #5839 fix: deferred settled worklogs have no rows yet at restore time, so
  // the disclosure restore above can't reach their detail elements. Stash the
  // captured state on each still-deferred group; _materializeDeferredWorklogRows
  // re-applies it (key-scoped + idempotent) once the rows exist on expand.
  if(worklogDetailDisclosureState&&worklogDetailDisclosureState.size){
    inner.querySelectorAll('[data-worklog-rows-deferred="1"]').forEach(group=>{
      group._deferredWorklogDisclosure=worklogDetailDisclosureState;
    });
  }
  // Render per-turn duration and optional token usage on assistant messages.
  // Duration stays visible even when token usage is disabled, because it answers
  // the basic "how long did that turn take?" UX question. Only walk rendered
  // assistant segments so hidden messages above the DOM window cannot skew the
  // footer-to-message mapping.
  {
    const renderedAssistantIdxs=[...assistantSegments.keys()].sort((a,b)=>a-b);
    for(const mi of renderedAssistantIdxs){
      const msg=S.messages[mi]||{};
      if(msg.role!=='assistant') continue;
      const routing=msg._gatewayRouting||null;
      const gatewayText=_formatGatewayModelLabel(String(msg._usedModel||'').trim()||(S.session&&S.session.model)||'', '', routing);
      const failoverText=_gatewayRoutingFailoverText(routing);
      const modelWarningText=_gatewayModelWarningText(routing);
      const hasTurnUsage=!!msg._turnUsage;
      // The Worklog summary owns the "Done in …" duration whenever this
      // assistant message contributes tool or thinking detail to a folded
      // Worklog above the final answer.
      const compactWorklogForMessage=isCompactWorklogMode()&&(toolCallAssistantIdxs.has(mi)||assistantThinking.has(mi));
      const durationText=compactWorklogForMessage?'':_formatTurnDuration(msg._turnDuration);
      const usedModelText=_usedModelTurnChipLabel(msg);
      if(!hasTurnUsage&&!durationText&&!gatewayText&&!failoverText&&!modelWarningText&&!usedModelText) continue;
      const seg=assistantSegments.get(mi);
      const row=seg?seg.closest('.assistant-turn'):null;
      const footerRows=row?row.querySelectorAll('.msg-foot'):[];
      const targetFoot=footerRows.length?footerRows[footerRows.length-1]:null;
      if(!targetFoot||targetFoot.querySelector('.msg-usage-inline,.msg-duration-inline,.msg-gateway-inline,.gateway-failover-inline,.msg-model-warning-inline,.msg-used-model-inline')) continue;
      const fragments=[];
      if(modelWarningText){
        const warning=document.createElement('span');
        warning.className='msg-model-warning-inline';
        warning.textContent=modelWarningText;
        fragments.push(warning);
      }
      if(failoverText){
        const failover=document.createElement('span');
        failover.className='gateway-failover-inline';
        failover.textContent=failoverText;
        fragments.push(failover);
      }
      if(gatewayText){
        const gateway=document.createElement('span');
        gateway.className='msg-gateway-inline';
        gateway.textContent=gatewayText;
        fragments.push(gateway);
      }
      if(durationText){
        const duration=document.createElement('span');
        duration.className='msg-duration-inline';
        duration.textContent=`Done in ${durationText}`;
        fragments.push(duration);
      }
      // The transparent turn footer owns the model label (.lf-model) whenever
      // the turn has transparent event rows — skip the generic chip there so
      // exactly one model label renders per turn. Model sits after duration to
      // match the transparent footer order (elapsed · model · …).
      const _transparentFooterOwnsModel=usedModelText&&isTransparentStream()&&row&&(()=>{
        const blocks=_assistantTurnBlocks(row);
        return !!(blocks&&blocks.querySelector(':scope > .transparent-event-row'));
      })();
      if(usedModelText&&!_transparentFooterOwnsModel){
        const usedModel=document.createElement('span');
        usedModel.className='msg-used-model-inline';
        usedModel.textContent=usedModelText;
        // Preserve the full (uncompacted) model id on hover where available.
        const usedModelFull=String(msg._usedModel||'').trim();
        if(usedModelFull&&usedModelFull!==usedModelText) usedModel.title=usedModelFull;
        fragments.push(usedModel);
      }
      if(window._showTokenUsage&&hasTurnUsage){
        const usage=document.createElement('span');
        usage.className='msg-usage-inline';
        const inTok=msg._turnUsage.input_tokens||0;
        const outTok=msg._turnUsage.output_tokens||0;
        const cost=msg._turnUsage.estimated_cost;
        let text=`${_fmtTokens(inTok)} in · ${_fmtTokens(outTok)} out`;
        if(cost) text+=` · ~$${cost<0.01?cost.toFixed(4):cost.toFixed(2)}`;
        const cacheHitPct=msg._turnUsage.cache_hit_percent;
        if(cacheHitPct!=null) text+=` · ${t('usage_cached_percent',cacheHitPct)}`;
        usage.textContent=text;
        fragments.push(usage);
      }
      if(fragments.length){
        targetFoot.classList.add('msg-foot-with-usage');
        for(let i=fragments.length-1;i>=0;i--){
          // Guard: firstChild may be null (empty foot) or orphaned.
          const firstChild=targetFoot.firstChild;
          if(firstChild&&firstChild.parentNode===targetFoot) targetFoot.insertBefore(fragments[i], firstChild);
          else targetFoot.appendChild(fragments[i]);
        }
      }
    }
  }
  // Transparent mode per-turn wiring: collapsible AGY chat name tag, old-event
  // fading, and the bottom-of-turn footer (elapsed · tokens · TTFT · status).
  // Runs after the per-turn duration block above so the footer can reuse the
  // computed durationText / tokens / TTFT for each settled assistant turn.
  if(isTransparentStream()){
    for(const turn of inner.querySelectorAll('.assistant-turn')){
      if(turn.id==='liveAssistantTurn') continue;
      const blocks=_assistantTurnBlocks(turn);
      if(!blocks) continue;
      const hasTransparentRows=blocks.querySelector(':scope > .transparent-event-row');
      _wireTransparentTurnToggle(turn);
      // Restore collapse state from the map (survives DOM rebuild).
      const seg=turn.querySelector('.assistant-segment');
      if(seg&&sid){
        const mi=seg.getAttribute('data-msg-idx');
        if(mi!=null&&_transparentTurnCollapsedStates[`${sid}:${mi}`]){
          turn.setAttribute('data-transparent-turn-collapsed','1');
          const role=turn.querySelector('.msg-role.assistant');
          if(role) role.setAttribute('aria-expanded','false');
        }
      }
      _applyTransparentRowFading(turn);
      if(hasTransparentRows){
        // Read turn metadata from the final metadata-bearing assistant segment,
        // not querySelector's first match — a tool turn's activity segment
        // precedes the answer, and the metadata lives on the last message
        // (#6068 gate round 2: multi-segment turns lost the model label).
        const msg=_transparentTurnMetaMessage(turn);
        let durationText='';
        let modelText='';
        let modelTitle='';
        let ttftText='';
        let tokensText='';
        if(msg){
          if(msg._turnDuration!=null) durationText=_formatTurnDuration(msg._turnDuration);
          modelText=_usedModelTurnChipLabel(msg);
          if(modelText) modelTitle=String(msg._usedModel||'').trim();
          if(msg._firstTokenMs!=null) ttftText=_formatFirstToken(msg._firstTokenMs);
          if(msg._turnUsage){
            const inTok=msg._turnUsage.input_tokens||0;
            const outTok=msg._turnUsage.output_tokens||0;
            tokensText=`${_fmtTokens(inTok)} in · ${_fmtTokens(outTok)} out`;
          }
        }
        _renderTransparentTurnFooter(turn,{
          durationText,
          modelText,
          modelTitle,
          ttftText,
          tokensText,
          statusText: t('done')||'Done',
        });
      }else{
        // No transparent rows → no footer needed.
        _renderTransparentTurnFooter(turn,{});
      }
    }
  }
  // Fail-safe invariant (#3875): a settled assistant turn must never render with
  // ZERO visible content. The Worklog redesign (#3401) folds intermediate
  // assistant segments into a collapsed Worklog card and hides the source segment
  // (`assistant-segment-worklog-source` → display:none). That is correct WHEN the
  // turn also has a visible final answer. But when a turn's ONLY content is folded
  // into a collapsed Worklog (e.g. an autonomous/interrupted run whose final
  // assistant message is empty, or a reload where S.toolCalls didn't hydrate so the
  // worklog card built with no expandable tool steps), every segment is hidden and
  // the turn paints as nothing — leaving the transcript a bare stack of date
  // separators (#3875 brick). Reveal such turns so their content is never silently
  // swallowed: expand the turn's Worklog group(s) when the turn has no other
  // visible content. This NEVER touches a turn that has any visible segment, so the
  // intended collapsed-Worklog UX is preserved whenever a visible answer exists.
  // The live turn is excluded by its `liveAssistantTurn` id (it drives its own
  // state during a stream), so this sweep is safe to run even while busy — a
  // historical blank turn must not re-paint blank during a follow-up stream
  // (Opus advisor, stage-342).
  {
    const _turnHasVisibleContent=(turn)=>{
      if(typeof _assistantTurnHasVisibleRenderedSegment==='function'){
        return _assistantTurnHasVisibleRenderedSegment(turn)===true;
      }
      // Keep the extracted renderMessages test harness self-contained.
      if(!turn||typeof turn.querySelectorAll!=='function') return false;
      for(const seg of turn.querySelectorAll('.assistant-segment')){
        if(seg.classList.contains('assistant-segment-worklog-source')) continue;
        if(seg.classList.contains('assistant-segment-anchor')) continue;
        if((seg.textContent||'').trim()) return true;
      }
      return false;
    };
    for(const turn of inner.querySelectorAll('.assistant-turn')){
      if(turn.id==='liveAssistantTurn') continue; // live turn drives its own state
      if(_turnHasVisibleContent(turn)) continue;
      // No visible content — surface the folded Worklog so the turn isn't blank.
      const groups=turn.querySelectorAll('.tool-worklog-group,.tool-call-group');
      let revealed=false;
      for(const group of groups){
        if(!(group.textContent||'').trim()) continue; // empty group can't help
        if(group.classList.contains('tool-call-group-collapsed')){
          group.classList.remove('tool-call-group-collapsed');
          group.classList.add('open');
          const summary=group.querySelector('.tool-call-group-summary,.activity-summary');
          if(summary) summary.setAttribute('aria-expanded','true');
          // #5839: this turn is otherwise blank, so materialize any deferred
          // settled rows now that we're force-expanding the worklog to fill it.
          if(typeof _materializeDeferredWorklogRows==='function') _materializeDeferredWorklogRows(group);
        }
        // `revealed` means "this turn has a non-empty Worklog group that the user
        // can see" — NOT "we just expanded something". An already-open non-empty
        // group is itself visible (it slips past _turnHasVisibleContent only
        // because that check inspects .assistant-segment nodes, not group bodies),
        // so the turn isn't truly blank and the last-resort un-hide below is
        // unnecessary. Keep this assignment OUTSIDE the if(collapsed) branch.
        revealed=true;
      }
      // Last resort: no usable worklog group either, but hidden worklog-source
      // segments carry the real text — un-hide them so nothing is lost.
      if(!revealed){
        for(const seg of turn.querySelectorAll('.assistant-segment-worklog-source')){
          if(!(seg.textContent||'').trim()) continue;
          seg.classList.remove('assistant-segment-worklog-source');
          seg.removeAttribute('aria-hidden');
          seg.hidden=false;
        }
      }
    }
  }
  // Re-attach the preserved live turn (#3877). The rebuild above recreated a
  // live turn from S.messages, but the live assistant message's content lags the
  // stream (it is only persisted to S.messages on a throttled write-back) — so the
  // fresh node often shows LESS streamed text than the ORIGINAL node, which is
  // still referenced by the smd parser and holds the real in-progress reply. Swap
  // the preserved (parser) node back in so the parser target stays connected and
  // the visible text never blanks.
  //
  // The swap fires when the preserved node carries at least as much streamed text
  // as the rebuilt one (`_rebuiltLen <= _preservedLen`). The `<=` (not `<`) is
  // load-bearing: at the throttled-persist boundary the rebuilt turn's live
  // content can EQUAL the preserved length, and the old `<` guard then skipped the
  // swap — leaving the smd parser writing into the detached original node, which
  // is exactly the residual "disappears, then reappears" frame (#3877 reopen). On
  // a tie the preserved node is strictly preferable (it holds the live parser
  // reference; identical length means nothing is lost). When the rebuilt turn
  // genuinely has MORE content (e.g. a reconnect where S.messages caught up past
  // the parser), the guard correctly skips and lets the parser re-resolve to the
  // fuller node.
  //
  // Swap at the SEGMENT level — replace only the rebuilt live segment with the
  // preserved one — so a multi-segment turn (earlier settled segments + tool/
  // worklog groups built by the rebuild) keeps that rebuilt-only structure; a
  // whole-turn replaceWith would discard it when the preserved snapshot predates
  // those segments. Fall back to whole-turn replace only when the rebuilt turn has
  // no live segment to swap into. No-op for a settled turn or when nothing was
  // streaming.
  if(_preservedLiveTurn){
    const _rebuilt=document.getElementById('liveAssistantTurn');
    // Pick the PARSER-OWNED live segment, not just the first one. On reconnect /
    // post-tool activity boundaries a live turn can carry MULTIPLE
    // [data-live-assistant="1"] segments, and the smd parser writes into the
    // LAST (tail) one (see ensureAssistantRow in messages.js — it re-attaches to
    // the last live segment). Prefer the preserved segment whose
    // data-live-segment-seq matches the rebuilt tail (same logical segment), then
    // fall back to the last preserved live segment. Using querySelector() (first)
    // here would move the wrong segment and leave the parser-owned tail detached
    // in a multi-segment turn.
    const _rebuiltSegs=_rebuilt?_rebuilt.querySelectorAll('[data-live-assistant="1"]'):null;
    const _rebuiltSeg=(_rebuiltSegs&&_rebuiltSegs.length)?_rebuiltSegs[_rebuiltSegs.length-1]:null;
    const _preservedSegs=_preservedLiveTurn.querySelectorAll('[data-live-assistant="1"]');
    let _preservedSeg=_preservedSegs.length?_preservedSegs[_preservedSegs.length-1]:null;
    const _rebuiltSeq=_rebuiltSeg?_rebuiltSeg.getAttribute('data-live-segment-seq'):null;
    if(_rebuiltSeq){
      for(const _seg of _preservedSegs){
        if(_seg.getAttribute('data-live-segment-seq')===_rebuiltSeq){_preservedSeg=_seg;break;}
      }
    }
    const _preservedLen=_liveAssistantSegmentTextLength(_preservedSeg||_preservedLiveTurn);
    // Structural-block counts: a live turn can be AHEAD of S.messages with
    // Activity/tool/worklog blocks that haven't persisted yet — even with ZERO
    // streamed text (e.g. an Activity-only turn mid-tool-call). The text-length
    // gate alone would skip preservation in that case, so a scroll-triggered
    // rebuild on a long (virtualized) transcript could blink those live-only
    // blocks for a frame. Also restore when the preserved turn carries more
    // structure than the rebuilt (lagging-S.messages) turn. (#3714 ship-review)
    const _structuralCount=(turn)=> turn?turn.querySelectorAll(
      '[data-live-assistant="1"],.tool-call-group,.tool-card-row,'+
      '.tool-worklog-group,.live-worklog[data-live-worklog-shell="1"],'+
      '.wl-reason,.agent-activity-thinking,.thinking-card-row'
    ).length:0;
    const _preservedStructure=_structuralCount(_preservedLiveTurn);
    const _rebuiltStructure=_structuralCount(_rebuilt);
    if(_preservedLen>0 || _preservedStructure>_rebuiltStructure){
      const _rebuiltLen=_rebuilt?_liveAssistantSegmentTextLength(_rebuiltSeg||_rebuilt):-1;
      if(_rebuiltLen<=_preservedLen){
        // Decide segment-level vs whole-turn restore. Segment-level keeps the
        // rebuilt turn's structure (good when the rebuild is the structural
        // superset). But the whole premise here is that the live DOM can be
        // AHEAD of S.messages: a tool/worklog group can land in the live turn
        // between the last throttled persist and this rebuild, so the rebuilt
        // turn (built from the lagging S.messages) may have FEWER structural
        // blocks. In that case a segment-only swap would drop those live-only
        // blocks for a frame — so restore the WHOLE preserved turn instead.
        // Otherwise (rebuild has >= the preserved turn's structural blocks) do
        // the precise segment swap so rebuilt-only structure is kept.
        if(_rebuilt&&_rebuiltSeg&&_preservedSeg&&_rebuiltStructure>=_preservedStructure){
          // Rebuild is the structural superset — swap only the parser-owned
          // (tail) live segment, keeping rebuilt-only segments / tool groups.
          // (No dataset.sessionId stamp here: only the segment enters the DOM;
          // the rebuilt turn was already stamped at build time, see above.)
          _rebuiltSeg.replaceWith(_preservedSeg);
        }else if(_rebuilt){
          // Rebuilt turn lacks structure the live turn already has (live-only
          // tool card not yet persisted), or has no live segment to target —
          // restore the whole preserved turn so nothing the user saw vanishes.
          if(S.session) _preservedLiveTurn.dataset.sessionId=S.session.session_id;
          _rebuilt.replaceWith(_preservedLiveTurn);
        }else{
          if(S.session) _preservedLiveTurn.dataset.sessionId=S.session.session_id;
          inner.appendChild(_preservedLiveTurn);
        }
      }
    }
  }
  // Only force-scroll when not actively streaming — mid-stream re-renders
  // (tool completion, session switch) must not override the user's scroll position.
  // scrollIfPinned() respects _scrollPinned, so it's a no-op if user scrolled up.
  if(typeof _syncLiveRunStatusAfterRender==='function') _syncLiveRunStatusAfterRender();
  _scrollAfterMessageRender(preserveScroll, scrollSnapshot);
  if(_maybeRecoverVirtualizedBlankViewport(options, preserveScroll, virtualWindow)) return;
  // Apply syntax highlighting after DOM is built
  requestAnimationFrame(()=>_postProcessWithAnchorSuppression(inner));
  // Refresh todo panel if it's currently open
  if(typeof loadTodos==='function' && document.getElementById('panelTodos') && document.getElementById('panelTodos').classList.contains('active')){
    loadTodos();
  }
  // Apply persisted playback speed after media nodes are rendered.
  if(typeof _applyMediaPlaybackPreferences==='function') _applyMediaPlaybackPreferences(inner);
  // Populate session cache so switching back here skips a full rebuild.
  _sessionHtmlCacheSid=sid;
  // Skip caching while the just-settled keep-open token is armed: that render
  // force-opens the settled worklog for height-stability, and caching it would
  // persist the forced-open DOM across session switches / restores, overriding a
  // user-collapsed worklog. The follow-up collapse pass (after disarm) produces
  // the correct cacheable DOM on its own render. (#5260 gate-cert.) The typeof
  // guard keeps standalone renderMessages() test harnesses (which don't define
  // the helper) working — absent helper == not armed == cache normally.
  const _keepOpenArmed=(typeof _isKeepSettledWorklogOpenArmed==='function')&&_isKeepSettledWorklogOpenArmed();
  if(sid&&!INFLIGHT[sid]&&!hasTransientTranscriptUi&&!_keepOpenArmed){
    const _html=inner.innerHTML;
    // Only cache sessions with <300KB rendered HTML; evict oldest beyond 8 sessions.
    if(_html.length<300_000){
      const renderSignature=cachedRenderSignature===null?_messageRenderCacheSignature():cachedRenderSignature;
      _sessionHtmlCache.set(sid,{html:_html,msgCount,renderWindowKey,signature:renderSignature});
      if(_sessionHtmlCache.size>8){_sessionHtmlCache.delete(_sessionHtmlCache.keys().next().value);}
    }
  }
  _updateMessageVirtualMeasurements(renderVisWithIdx, renderVisibleIdxs, virtualWindow);
  // Kill the pinned/tail-follower mid-stream jitter. Schedule the re-anchor in a MICROTASK,
  // not synchronously: inside this render sync stack the browser still reports a transient
  // scrollHeight (layout is batched), so a synchronous re-anchor would read the SAME short
  // value scrollToBottom already clamped against and be a no-op. The microtask runs after the
  // stack unwinds (scrollHeight has flushed to the settled value) but before the browser
  // paints this frame, so writing the settled max lands the tail exactly and the ~1-row high
  // intermediate never reaches the screen. Only re-anchors a pre-wipe tail-follower left short
  // of the settled max — an unpinned reader parked in history is never moved (orthogonal to
  // the unpinned jump-back class). See _reanchorPinnedTailAfterRender for the full rationale.
  // (typeof guards mirror the _deferClearProgrammaticScroll call below so standalone
  // renderMessages() test harnesses that don't define these helpers still run.)
  if(typeof queueMicrotask==='function' && typeof _reanchorPinnedTailAfterRender==='function'){
    queueMicrotask(()=>_reanchorPinnedTailAfterRender(_preWipeNearTail));
  }
  _recycleStash.clear();
  if(typeof _deferClearProgrammaticScroll==='function') _deferClearProgrammaticScroll(160);
}


/* ── Global Window Exports ─────────────────────────────────────────────────── */
if (typeof window !== 'undefined') {
  window._attachProgressBar = typeof _attachProgressBar !== 'undefined' ? _attachProgressBar : undefined;
  window._setTransparentRowsExpanded = typeof _setTransparentRowsExpanded !== 'undefined' ? _setTransparentRowsExpanded : undefined;
  window._transparentTurnCollapsedStates = typeof _transparentTurnCollapsedStates !== 'undefined' ? _transparentTurnCollapsedStates : undefined;
  window._wireTransparentTurnToggle = typeof _wireTransparentTurnToggle !== 'undefined' ? _wireTransparentTurnToggle : undefined;
  window._applyTransparentRowFading = typeof _applyTransparentRowFading !== 'undefined' ? _applyTransparentRowFading : undefined;
  window._transparentTurnMetaMessage = typeof _transparentTurnMetaMessage !== 'undefined' ? _transparentTurnMetaMessage : undefined;
  window._transparentTurnFooterHtml = typeof _transparentTurnFooterHtml !== 'undefined' ? _transparentTurnFooterHtml : undefined;
  window._renderTransparentTurnFooter = typeof _renderTransparentTurnFooter !== 'undefined' ? _renderTransparentTurnFooter : undefined;
  window._liveActivityUserExpanded = typeof _liveActivityUserExpanded !== 'undefined' ? _liveActivityUserExpanded : undefined;
  window._activityDisclosureStoragePrefix = typeof _activityDisclosureStoragePrefix !== 'undefined' ? _activityDisclosureStoragePrefix : undefined;
  window._activityDisclosureStorageKey = typeof _activityDisclosureStorageKey !== 'undefined' ? _activityDisclosureStorageKey : undefined;
  window._readActivityDisclosureState = typeof _readActivityDisclosureState !== 'undefined' ? _readActivityDisclosureState : undefined;
  window._writeActivityDisclosureState = typeof _writeActivityDisclosureState !== 'undefined' ? _writeActivityDisclosureState : undefined;
  window._copyActivityDisclosureState = typeof _copyActivityDisclosureState !== 'undefined' ? _copyActivityDisclosureState : undefined;
  window._activityKeyForLiveTurn = typeof _activityKeyForLiveTurn !== 'undefined' ? _activityKeyForLiveTurn : undefined;
  window._onLiveActivityToggle = typeof _onLiveActivityToggle !== 'undefined' ? _onLiveActivityToggle : undefined;
  window._materializeDeferredWorklogRows = typeof _materializeDeferredWorklogRows !== 'undefined' ? _materializeDeferredWorklogRows : undefined;
  window._deferredWorklogRowsFromGroup = typeof _deferredWorklogRowsFromGroup !== 'undefined' ? _deferredWorklogRowsFromGroup : undefined;
  window._rehydrateDeferredWorklogsFromCache = typeof _rehydrateDeferredWorklogsFromCache !== 'undefined' ? _rehydrateDeferredWorklogsFromCache : undefined;
  window._toggleActivityGroup = typeof _toggleActivityGroup !== 'undefined' ? _toggleActivityGroup : undefined;
  window._toggleToolWorklogGroup = typeof _toggleToolWorklogGroup !== 'undefined' ? _toggleToolWorklogGroup : undefined;
  window._finalizeLiveActivityDisclosureGroup = typeof _finalizeLiveActivityDisclosureGroup !== 'undefined' ? _finalizeLiveActivityDisclosureGroup : undefined;
  window._worklogReasonHtmlFromAnchor = typeof _worklogReasonHtmlFromAnchor !== 'undefined' ? _worklogReasonHtmlFromAnchor : undefined;
  window._worklogReasonHtmlFromText = typeof _worklogReasonHtmlFromText !== 'undefined' ? _worklogReasonHtmlFromText : undefined;
  window._renderWorklogReasonInto = typeof _renderWorklogReasonInto !== 'undefined' ? _renderWorklogReasonInto : undefined;
  window._worklogReasonNodeFromText = typeof _worklogReasonNodeFromText !== 'undefined' ? _worklogReasonNodeFromText : undefined;
  window._worklogAnchorKeySeq = typeof _worklogAnchorKeySeq !== 'undefined' ? _worklogAnchorKeySeq : undefined;
  window._worklogReasonAnchorKey = typeof _worklogReasonAnchorKey !== 'undefined' ? _worklogReasonAnchorKey : undefined;
  window._syncWorklogReasonFromAnchor = typeof _syncWorklogReasonFromAnchor !== 'undefined' ? _syncWorklogReasonFromAnchor : undefined;
  window.ensureLiveWorklogContainer = typeof ensureLiveWorklogContainer !== 'undefined' ? ensureLiveWorklogContainer : undefined;
  window._migrateLegacyLiveActivityGroupsToWorklog = typeof _migrateLegacyLiveActivityGroupsToWorklog !== 'undefined' ? _migrateLegacyLiveActivityGroupsToWorklog : undefined;
  window._appendWorklogReason = typeof _appendWorklogReason !== 'undefined' ? _appendWorklogReason : undefined;
  window._toolIdentity = typeof _toolIdentity !== 'undefined' ? _toolIdentity : undefined;
  window._toolDisclosureIdentity = typeof _toolDisclosureIdentity !== 'undefined' ? _toolDisclosureIdentity : undefined;
  window._filterNewWorklogTools = typeof _filterNewWorklogTools !== 'undefined' ? _filterNewWorklogTools : undefined;
  window._anchorSceneToolRowLogicalKey = typeof _anchorSceneToolRowLogicalKey !== 'undefined' ? _anchorSceneToolRowLogicalKey : undefined;
  window._anchorSceneMergeToolRows = typeof _anchorSceneMergeToolRows !== 'undefined' ? _anchorSceneMergeToolRows : undefined;
  window._appendWorklogStep = typeof _appendWorklogStep !== 'undefined' ? _appendWorklogStep : undefined;
  window._anchorSceneRowsForRendering = typeof _anchorSceneRowsForRendering !== 'undefined' ? _anchorSceneRowsForRendering : undefined;
  window._anchorSceneIsSettledSuccessfulCompression = typeof _anchorSceneIsSettledSuccessfulCompression !== 'undefined' ? _anchorSceneIsSettledSuccessfulCompression : undefined;
  window._anchorSceneToolCallFromRow = typeof _anchorSceneToolCallFromRow !== 'undefined' ? _anchorSceneToolCallFromRow : undefined;
  window._anchorSceneRowTimestampSeconds = typeof _anchorSceneRowTimestampSeconds !== 'undefined' ? _anchorSceneRowTimestampSeconds : undefined;
  window._anchorSceneNodeForRow = typeof _anchorSceneNodeForRow !== 'undefined' ? _anchorSceneNodeForRow : undefined;
  window._anchorSceneTransparentNodeForRow = typeof _anchorSceneTransparentNodeForRow !== 'undefined' ? _anchorSceneTransparentNodeForRow : undefined;
  window._anchorSceneLiveTokenFinalPrefix = typeof _anchorSceneLiveTokenFinalPrefix !== 'undefined' ? _anchorSceneLiveTokenFinalPrefix : undefined;
  window._anchorSceneLastNonTerminalWorkRowIndex = typeof _anchorSceneLastNonTerminalWorkRowIndex !== 'undefined' ? _anchorSceneLastNonTerminalWorkRowIndex : undefined;
  window._anchorSceneProseMatchesFinalAnswer = typeof _anchorSceneProseMatchesFinalAnswer !== 'undefined' ? _anchorSceneProseMatchesFinalAnswer : undefined;
  window._anchorSceneWorklogGroup = typeof _anchorSceneWorklogGroup !== 'undefined' ? _anchorSceneWorklogGroup : undefined;
  window._renderAnchorSceneRowsIntoWorklog = typeof _renderAnchorSceneRowsIntoWorklog !== 'undefined' ? _renderAnchorSceneRowsIntoWorklog : undefined;
  window._liveProcessedWorklogAnchorScore = typeof _liveProcessedWorklogAnchorScore !== 'undefined' ? _liveProcessedWorklogAnchorScore : undefined;
  window._dedupeLiveProcessedWorklogAnchors = typeof _dedupeLiveProcessedWorklogAnchors !== 'undefined' ? _dedupeLiveProcessedWorklogAnchors : undefined;
  window.isLiveAnchorActivitySceneOwner = typeof isLiveAnchorActivitySceneOwner !== 'undefined' ? isLiveAnchorActivitySceneOwner : undefined;
  window._projectLiveAnchorActivitySceneForStream = typeof _projectLiveAnchorActivitySceneForStream !== 'undefined' ? _projectLiveAnchorActivitySceneForStream : undefined;
  window._prepareLiveAnchorScrollRebuildGuard = typeof _prepareLiveAnchorScrollRebuildGuard !== 'undefined' ? _prepareLiveAnchorScrollRebuildGuard : undefined;
  window._restoreLiveAnchorScrollSnapshotAfterRebuild = typeof _restoreLiveAnchorScrollSnapshotAfterRebuild !== 'undefined' ? _restoreLiveAnchorScrollSnapshotAfterRebuild : undefined;
  window._resetMismatchedLiveAssistantTurnForSession = typeof _resetMismatchedLiveAssistantTurnForSession !== 'undefined' ? _resetMismatchedLiveAssistantTurnForSession : undefined;
  window._liveAnchorReasoningRowForFallback = typeof _liveAnchorReasoningRowForFallback !== 'undefined' ? _liveAnchorReasoningRowForFallback : undefined;
  window._updateLiveAnchorReasoningRowForFallback = typeof _updateLiveAnchorReasoningRowForFallback !== 'undefined' ? _updateLiveAnchorReasoningRowForFallback : undefined;
  window.renderLiveAnchorActivityScene = typeof renderLiveAnchorActivityScene !== 'undefined' ? renderLiveAnchorActivityScene : undefined;
  window._renderLiveAnchorActivitySceneTransparent = typeof _renderLiveAnchorActivitySceneTransparent !== 'undefined' ? _renderLiveAnchorActivitySceneTransparent : undefined;
  window._transparentLiveRowKey = typeof _transparentLiveRowKey !== 'undefined' ? _transparentLiveRowKey : undefined;
  window._transparentLiveRowsCompatible = typeof _transparentLiveRowsCompatible !== 'undefined' ? _transparentLiveRowsCompatible : undefined;
  window._transparentLiveRowAttributePairs = typeof _transparentLiveRowAttributePairs !== 'undefined' ? _transparentLiveRowAttributePairs : undefined;
  window._transparentLiveRowInteractiveState = typeof _transparentLiveRowInteractiveState !== 'undefined' ? _transparentLiveRowInteractiveState : undefined;
  window._rehydrateTransparentLiveRow = typeof _rehydrateTransparentLiveRow !== 'undefined' ? _rehydrateTransparentLiveRow : undefined;
  window._refreshTransparentThinkingLiveRow = typeof _refreshTransparentThinkingLiveRow !== 'undefined' ? _refreshTransparentThinkingLiveRow : undefined;
  window._bindTransparentFadeCleanup = typeof _bindTransparentFadeCleanup !== 'undefined' ? _bindTransparentFadeCleanup : undefined;
  window._appendTransparentFadeText = typeof _appendTransparentFadeText !== 'undefined' ? _appendTransparentFadeText : undefined;
  window._refreshTransparentFadeProseRow = typeof _refreshTransparentFadeProseRow !== 'undefined' ? _refreshTransparentFadeProseRow : undefined;
  window._refreshTransparentLiveRow = typeof _refreshTransparentLiveRow !== 'undefined' ? _refreshTransparentLiveRow : undefined;
  window._renderLiveAnchorActivitySceneForStream = typeof _renderLiveAnchorActivitySceneForStream !== 'undefined' ? _renderLiveAnchorActivitySceneForStream : undefined;
  window._renderLiveAnchorActivitySceneSnapshotForStream = typeof _renderLiveAnchorActivitySceneSnapshotForStream !== 'undefined' ? _renderLiveAnchorActivitySceneSnapshotForStream : undefined;
  window._anchorSceneSceneHasWorklogWorthyRows = typeof _anchorSceneSceneHasWorklogWorthyRows !== 'undefined' ? _anchorSceneSceneHasWorklogWorthyRows : undefined;
  window._ANCHOR_SCENE_ERRORED_TERMINAL_STATES = typeof _ANCHOR_SCENE_ERRORED_TERMINAL_STATES !== 'undefined' ? _ANCHOR_SCENE_ERRORED_TERMINAL_STATES : undefined;
  window._anchorSceneHasErroredTerminalState = typeof _anchorSceneHasErroredTerminalState !== 'undefined' ? _anchorSceneHasErroredTerminalState : undefined;
  window._renderSettledAnchorSceneTransparentForMessage = typeof _renderSettledAnchorSceneTransparentForMessage !== 'undefined' ? _renderSettledAnchorSceneTransparentForMessage : undefined;
  window._TRANSPARENT_SETTLED_ROW_CAP = typeof _TRANSPARENT_SETTLED_ROW_CAP !== 'undefined' ? _TRANSPARENT_SETTLED_ROW_CAP : undefined;
  window._TRANSPARENT_SETTLED_ROW_CAP_SLACK = typeof _TRANSPARENT_SETTLED_ROW_CAP_SLACK !== 'undefined' ? _TRANSPARENT_SETTLED_ROW_CAP_SLACK : undefined;
  window._tOrDefault = typeof _tOrDefault !== 'undefined' ? _tOrDefault : undefined;
  window._buildTransparentEarlierStepsAffordance = typeof _buildTransparentEarlierStepsAffordance !== 'undefined' ? _buildTransparentEarlierStepsAffordance : undefined;
  window._revealTransparentEarlierSteps = typeof _revealTransparentEarlierSteps !== 'undefined' ? _revealTransparentEarlierSteps : undefined;
  window._computeTransparentHiddenPrefixCount = typeof _computeTransparentHiddenPrefixCount !== 'undefined' ? _computeTransparentHiddenPrefixCount : undefined;
  window._keepSettledWorklogOpenForStreamId = typeof _keepSettledWorklogOpenForStreamId !== 'undefined' ? _keepSettledWorklogOpenForStreamId : undefined;
  window._shouldKeepSettledWorklogOpenForStreamSettle = typeof _shouldKeepSettledWorklogOpenForStreamSettle !== 'undefined' ? _shouldKeepSettledWorklogOpenForStreamSettle : undefined;
  window._armKeepSettledWorklogOpen = typeof _armKeepSettledWorklogOpen !== 'undefined' ? _armKeepSettledWorklogOpen : undefined;
  window._disarmKeepSettledWorklogOpen = typeof _disarmKeepSettledWorklogOpen !== 'undefined' ? _disarmKeepSettledWorklogOpen : undefined;
  window._assistantTurnHasVisibleRenderedSegment = typeof _assistantTurnHasVisibleRenderedSegment !== 'undefined' ? _assistantTurnHasVisibleRenderedSegment : undefined;
  window._collapseJustSettledWorklogInPlace = typeof _collapseJustSettledWorklogInPlace !== 'undefined' ? _collapseJustSettledWorklogInPlace : undefined;
  window._isKeepSettledWorklogOpenArmed = typeof _isKeepSettledWorklogOpenArmed !== 'undefined' ? _isKeepSettledWorklogOpenArmed : undefined;
  window._renderSettledAnchorSceneForMessage = typeof _renderSettledAnchorSceneForMessage !== 'undefined' ? _renderSettledAnchorSceneForMessage : undefined;
  window._syncLiveWorklogReasonsForAnchor = typeof _syncLiveWorklogReasonsForAnchor !== 'undefined' ? _syncLiveWorklogReasonsForAnchor : undefined;
  window._clearLiveActivityUserIntent = typeof _clearLiveActivityUserIntent !== 'undefined' ? _clearLiveActivityUserIntent : undefined;
  window.ensureActivityGroup = typeof ensureActivityGroup !== 'undefined' ? ensureActivityGroup : undefined;
  window.normalizeLiveActivityGroupPlacement = typeof normalizeLiveActivityGroupPlacement !== 'undefined' ? normalizeLiveActivityGroupPlacement : undefined;
  window.ensureRunActivityGroup = typeof ensureRunActivityGroup !== 'undefined' ? ensureRunActivityGroup : undefined;
  window._liveRunStatusTimers = typeof _liveRunStatusTimers !== 'undefined' ? _liveRunStatusTimers : undefined;
  window._liveRunStatusTokens = typeof _liveRunStatusTokens !== 'undefined' ? _liveRunStatusTokens : undefined;
  window._liveRunStatusSessionId = typeof _liveRunStatusSessionId !== 'undefined' ? _liveRunStatusSessionId : undefined;
  window._formatRunElapsed = typeof _formatRunElapsed !== 'undefined' ? _formatRunElapsed : undefined;
  window._moveLiveRunStatusToTurnEnd = typeof _moveLiveRunStatusToTurnEnd !== 'undefined' ? _moveLiveRunStatusToTurnEnd : undefined;
  window.placeLiveRunStatusHost = typeof placeLiveRunStatusHost !== 'undefined' ? placeLiveRunStatusHost : undefined;
  window.showLiveRunStatus = typeof showLiveRunStatus !== 'undefined' ? showLiveRunStatus : undefined;
  window._renderLiveRunStatusContent = typeof _renderLiveRunStatusContent !== 'undefined' ? _renderLiveRunStatusContent : undefined;
  window.updateLiveRunStatus = typeof updateLiveRunStatus !== 'undefined' ? updateLiveRunStatus : undefined;
  window._syncLiveRunStatusAfterRender = typeof _syncLiveRunStatusAfterRender !== 'undefined' ? _syncLiveRunStatusAfterRender : undefined;
  window.hideLiveRunStatus = typeof hideLiveRunStatus !== 'undefined' ? hideLiveRunStatus : undefined;
  window._startLiveRunStatusTimer = typeof _startLiveRunStatusTimer !== 'undefined' ? _startLiveRunStatusTimer : undefined;
  window._clearLiveRunStatusTimer = typeof _clearLiveRunStatusTimer !== 'undefined' ? _clearLiveRunStatusTimer : undefined;
  window.ensureRunActivityForCurrentTurn = typeof ensureRunActivityForCurrentTurn !== 'undefined' ? ensureRunActivityForCurrentTurn : undefined;
  window.closeCurrentLiveActivityGroup = typeof closeCurrentLiveActivityGroup !== 'undefined' ? closeCurrentLiveActivityGroup : undefined;
  window._compressionStateForCurrentSession = typeof _compressionStateForCurrentSession !== 'undefined' ? _compressionStateForCurrentSession : undefined;
  window.isCompressionUiRunning = typeof isCompressionUiRunning !== 'undefined' ? isCompressionUiRunning : undefined;
  window._restoreCompressionPlaceholder = typeof _restoreCompressionPlaceholder !== 'undefined' ? _restoreCompressionPlaceholder : undefined;
  window.clearCompressionUi = typeof clearCompressionUi !== 'undefined' ? clearCompressionUi : undefined;
  window.setCompressionUi = typeof setCompressionUi !== 'undefined' ? setCompressionUi : undefined;
  window._compressionCardsHtml = typeof _compressionCardsHtml !== 'undefined' ? _compressionCardsHtml : undefined;
  window._autoCompressionBaseDetail = typeof _autoCompressionBaseDetail !== 'undefined' ? _autoCompressionBaseDetail : undefined;
  window._autoCompressionPreviewText = typeof _autoCompressionPreviewText !== 'undefined' ? _autoCompressionPreviewText : undefined;
  window._autoCompressionDetailText = typeof _autoCompressionDetailText !== 'undefined' ? _autoCompressionDetailText : undefined;
  window._autoCompressionCardsHtml = typeof _autoCompressionCardsHtml !== 'undefined' ? _autoCompressionCardsHtml : undefined;
  window._autoCompressionWorklogNode = typeof _autoCompressionWorklogNode !== 'undefined' ? _autoCompressionWorklogNode : undefined;
  window._compressionCardsNode = typeof _compressionCardsNode !== 'undefined' ? _compressionCardsNode : undefined;
  window.appendLiveCompressionCard = typeof appendLiveCompressionCard !== 'undefined' ? appendLiveCompressionCard : undefined;
  window._isHandoffSummaryToolPayload = typeof _isHandoffSummaryToolPayload !== 'undefined' ? _isHandoffSummaryToolPayload : undefined;
  window._parseHandoffSummaryPayload = typeof _parseHandoffSummaryPayload !== 'undefined' ? _parseHandoffSummaryPayload : undefined;
  window._handoffSummaryStateFromMessage = typeof _handoffSummaryStateFromMessage !== 'undefined' ? _handoffSummaryStateFromMessage : undefined;
  window._collectHandoffSummaryStates = typeof _collectHandoffSummaryStates !== 'undefined' ? _collectHandoffSummaryStates : undefined;
  window._isContextCompactionMessage = typeof _isContextCompactionMessage !== 'undefined' ? _isContextCompactionMessage : undefined;
  window._isContextCompactionText = typeof _isContextCompactionText !== 'undefined' ? _isContextCompactionText : undefined;
  window._isPreservedCompressionTaskListMarkerText = typeof _isPreservedCompressionTaskListMarkerText !== 'undefined' ? _isPreservedCompressionTaskListMarkerText : undefined;
  window._isPreservedCompressionTaskListMarkerOnlyText = typeof _isPreservedCompressionTaskListMarkerOnlyText !== 'undefined' ? _isPreservedCompressionTaskListMarkerOnlyText : undefined;
  window._isPreservedCompressionTaskListMessage = typeof _isPreservedCompressionTaskListMessage !== 'undefined' ? _isPreservedCompressionTaskListMessage : undefined;
  window._isMarkerOnlyAssistantCompressionMessage = typeof _isMarkerOnlyAssistantCompressionMessage !== 'undefined' ? _isMarkerOnlyAssistantCompressionMessage : undefined;
  window._preservedCompressionTaskListPreview = typeof _preservedCompressionTaskListPreview !== 'undefined' ? _preservedCompressionTaskListPreview : undefined;
  window._compressionMessageAnchorKey = typeof _compressionMessageAnchorKey !== 'undefined' ? _compressionMessageAnchorKey : undefined;
  window._compressionAnchorIndex = typeof _compressionAnchorIndex !== 'undefined' ? _compressionAnchorIndex : undefined;
  window._latestCompressionReferenceMessage = typeof _latestCompressionReferenceMessage !== 'undefined' ? _latestCompressionReferenceMessage : undefined;
  window._shouldShowSettledCompressionReference = typeof _shouldShowSettledCompressionReference !== 'undefined' ? _shouldShowSettledCompressionReference : undefined;
  window._compressionReferenceCardHtml = typeof _compressionReferenceCardHtml !== 'undefined' ? _compressionReferenceCardHtml : undefined;
  window._preservedCompressionTaskListCardHtml = typeof _preservedCompressionTaskListCardHtml !== 'undefined' ? _preservedCompressionTaskListCardHtml : undefined;
  window._preservedCompressionTaskListCardsHtml = typeof _preservedCompressionTaskListCardsHtml !== 'undefined' ? _preservedCompressionTaskListCardsHtml : undefined;
  window._latestTodoToolItems = typeof _latestTodoToolItems !== 'undefined' ? _latestTodoToolItems : undefined;
  window._hasActiveTodoItems = typeof _hasActiveTodoItems !== 'undefined' ? _hasActiveTodoItems : undefined;
  window._latestPreservedCompressionTaskListMessages = typeof _latestPreservedCompressionTaskListMessages !== 'undefined' ? _latestPreservedCompressionTaskListMessages : undefined;
  window._isSameLocalDay = typeof _isSameLocalDay !== 'undefined' ? _isSameLocalDay : undefined;
  window._formatMessageFooterTimestamp = typeof _formatMessageFooterTimestamp !== 'undefined' ? _formatMessageFooterTimestamp : undefined;
  window._compressionEngineForSession = typeof _compressionEngineForSession !== 'undefined' ? _compressionEngineForSession : undefined;
  window._compressionModeForSession = typeof _compressionModeForSession !== 'undefined' ? _compressionModeForSession : undefined;
  window._engineAwareCompressionCopy = typeof _engineAwareCompressionCopy !== 'undefined' ? _engineAwareCompressionCopy : undefined;
  window._compressionStatusCardHtml = typeof _compressionStatusCardHtml !== 'undefined' ? _compressionStatusCardHtml : undefined;
  window._handoffStateForCurrentSession = typeof _handoffStateForCurrentSession !== 'undefined' ? _handoffStateForCurrentSession : undefined;
  window.clearHandoffUi = typeof clearHandoffUi !== 'undefined' ? clearHandoffUi : undefined;
  window.setHandoffUi = typeof setHandoffUi !== 'undefined' ? setHandoffUi : undefined;
  window._handoffCardsHtml = typeof _handoffCardsHtml !== 'undefined' ? _handoffCardsHtml : undefined;
  window._handoffCardsNode = typeof _handoffCardsNode !== 'undefined' ? _handoffCardsNode : undefined;
  window._contextCompactionMessageHtml = typeof _contextCompactionMessageHtml !== 'undefined' ? _contextCompactionMessageHtml : undefined;
  window.renderCompressionUi = typeof renderCompressionUi !== 'undefined' ? renderCompressionUi : undefined;
  window._sessionHtmlCache = typeof _sessionHtmlCache !== 'undefined' ? _sessionHtmlCache : undefined;
  window._sessionHtmlCacheSid = typeof _sessionHtmlCacheSid !== 'undefined' ? _sessionHtmlCacheSid : undefined;
  window._transparentRevealedTurns = typeof _transparentRevealedTurns !== 'undefined' ? _transparentRevealedTurns : undefined;
  window._transparentRevealKey = typeof _transparentRevealKey !== 'undefined' ? _transparentRevealKey : undefined;
  window.clearMessageRenderCache = typeof clearMessageRenderCache !== 'undefined' ? clearMessageRenderCache : undefined;
  window._addBoundedHash = typeof _addBoundedHash !== 'undefined' ? _addBoundedHash : undefined;
  window._hashObjectInto = typeof _hashObjectInto !== 'undefined' ? _hashObjectInto : undefined;
  window._messageRenderCacheSignature = typeof _messageRenderCacheSignature !== 'undefined' ? _messageRenderCacheSignature : undefined;
  window._clipCliToolSnippet = typeof _clipCliToolSnippet !== 'undefined' ? _clipCliToolSnippet : undefined;
  window._cliToolResultText = typeof _cliToolResultText !== 'undefined' ? _cliToolResultText : undefined;
  window._cliLooksLikePatchDiff = typeof _cliLooksLikePatchDiff !== 'undefined' ? _cliLooksLikePatchDiff : undefined;
  window._cliToolResultSnippet = typeof _cliToolResultSnippet !== 'undefined' ? _cliToolResultSnippet : undefined;
  window._prefixedCliDiffLines = typeof _prefixedCliDiffLines !== 'undefined' ? _prefixedCliDiffLines : undefined;
  window._firstOwnedValue = typeof _firstOwnedValue !== 'undefined' ? _firstOwnedValue : undefined;
  window._cliPatchSnippetFromArgs = typeof _cliPatchSnippetFromArgs !== 'undefined' ? _cliPatchSnippetFromArgs : undefined;
  window._cliToolCardSnippet = typeof _cliToolCardSnippet !== 'undefined' ? _cliToolCardSnippet : undefined;
  window._cliToolCardHasDiffSnippet = typeof _cliToolCardHasDiffSnippet !== 'undefined' ? _cliToolCardHasDiffSnippet : undefined;
  window._assistantToolAnchorIdxForMessage = typeof _assistantToolAnchorIdxForMessage !== 'undefined' ? _assistantToolAnchorIdxForMessage : undefined;
  window._toolArgsSnapshot = typeof _toolArgsSnapshot !== 'undefined' ? _toolArgsSnapshot : undefined;
  window._idLinkedHistoricalMessageText = typeof _idLinkedHistoricalMessageText !== 'undefined' ? _idLinkedHistoricalMessageText : undefined;
  window._idLinkedHistoricalMessageHasVisibleText = typeof _idLinkedHistoricalMessageHasVisibleText !== 'undefined' ? _idLinkedHistoricalMessageHasVisibleText : undefined;
  window._idLinkedHistoricalMessageRef = typeof _idLinkedHistoricalMessageRef !== 'undefined' ? _idLinkedHistoricalMessageRef : undefined;
  window._idLinkedHistoricalToolArguments = typeof _idLinkedHistoricalToolArguments !== 'undefined' ? _idLinkedHistoricalToolArguments : undefined;
  window._idLinkedHistoricalToolResultRaw = typeof _idLinkedHistoricalToolResultRaw !== 'undefined' ? _idLinkedHistoricalToolResultRaw : undefined;
  window._idLinkedHistoricalRedactSnippet = typeof _idLinkedHistoricalRedactSnippet !== 'undefined' ? _idLinkedHistoricalRedactSnippet : undefined;
  window._idLinkedHistoricalHasVisibleSidecar = typeof _idLinkedHistoricalHasVisibleSidecar !== 'undefined' ? _idLinkedHistoricalHasVisibleSidecar : undefined;
  window._idLinkedHistoricalTurnScene = typeof _idLinkedHistoricalTurnScene !== 'undefined' ? _idLinkedHistoricalTurnScene : undefined;
  window._hydrateIdLinkedHistoricalToolScenes = typeof _hydrateIdLinkedHistoricalToolScenes !== 'undefined' ? _hydrateIdLinkedHistoricalToolScenes : undefined;
  window._captureMessageScrollSnapshot = typeof _captureMessageScrollSnapshot !== 'undefined' ? _captureMessageScrollSnapshot : undefined;
  window._messageScrollSnapshotInputChanged = typeof _messageScrollSnapshotInputChanged !== 'undefined' ? _messageScrollSnapshotInputChanged : undefined;
  window._abandonMessageScrollSnapshot = typeof _abandonMessageScrollSnapshot !== 'undefined' ? _abandonMessageScrollSnapshot : undefined;
  window._restorePinnedMessageScrollSnapshot = typeof _restorePinnedMessageScrollSnapshot !== 'undefined' ? _restorePinnedMessageScrollSnapshot : undefined;
  window._restoreMessageScrollSnapshot = typeof _restoreMessageScrollSnapshot !== 'undefined' ? _restoreMessageScrollSnapshot : undefined;
  window._MOBILE_ANCHOR_BASE_SETTLE_MS = typeof _MOBILE_ANCHOR_BASE_SETTLE_MS !== 'undefined' ? _MOBILE_ANCHOR_BASE_SETTLE_MS : undefined;
  window._MOBILE_ANCHOR_POST_TRANSITION_MS = typeof _MOBILE_ANCHOR_POST_TRANSITION_MS !== 'undefined' ? _MOBILE_ANCHOR_POST_TRANSITION_MS : undefined;
  window._MOBILE_ANCHOR_MAX_HOLD_MS = typeof _MOBILE_ANCHOR_MAX_HOLD_MS !== 'undefined' ? _MOBILE_ANCHOR_MAX_HOLD_MS : undefined;
  window._mobileAnchorSuppressReleaseTimer = typeof _mobileAnchorSuppressReleaseTimer !== 'undefined' ? _mobileAnchorSuppressReleaseTimer : undefined;
  window._mobileAnchorSuppressRafId = typeof _mobileAnchorSuppressRafId !== 'undefined' ? _mobileAnchorSuppressRafId : undefined;
  window._mobileAnchorTransitionListenerBound = typeof _mobileAnchorTransitionListenerBound !== 'undefined' ? _mobileAnchorTransitionListenerBound : undefined;
  window._mobileAnchorSuppressArmedAt = typeof _mobileAnchorSuppressArmedAt !== 'undefined' ? _mobileAnchorSuppressArmedAt : undefined;
  window._mobileAnchorMaxHoldTimer = typeof _mobileAnchorMaxHoldTimer !== 'undefined' ? _mobileAnchorMaxHoldTimer : undefined;
  window._liftMobileAnchorSuppression = typeof _liftMobileAnchorSuppression !== 'undefined' ? _liftMobileAnchorSuppression : undefined;
  window._bindMobileAnchorTransitionExtender = typeof _bindMobileAnchorTransitionExtender !== 'undefined' ? _bindMobileAnchorTransitionExtender : undefined;
  window._desktopAnchorRealignDelta = typeof _desktopAnchorRealignDelta !== 'undefined' ? _desktopAnchorRealignDelta : undefined;
  window._restoreMessageScrollSnapshotSameFrame = typeof _restoreMessageScrollSnapshotSameFrame !== 'undefined' ? _restoreMessageScrollSnapshotSameFrame : undefined;
  window._renderMessagesWithScrollSnapshot = typeof _renderMessagesWithScrollSnapshot !== 'undefined' ? _renderMessagesWithScrollSnapshot : undefined;
  window._assistantTurnAnchorSettledFinalAnswerWarned = typeof _assistantTurnAnchorSettledFinalAnswerWarned !== 'undefined' ? _assistantTurnAnchorSettledFinalAnswerWarned : undefined;
  window._transparentStreamOrderedParts = typeof _transparentStreamOrderedParts !== 'undefined' ? _transparentStreamOrderedParts : undefined;
  window._legacySettledFallbackHasToolMetadata = typeof _legacySettledFallbackHasToolMetadata !== 'undefined' ? _legacySettledFallbackHasToolMetadata : undefined;
  window._transparentOrderedDisplayText = typeof _transparentOrderedDisplayText !== 'undefined' ? _transparentOrderedDisplayText : undefined;
  window._collectToolResultSnippetsByTid = typeof _collectToolResultSnippetsByTid !== 'undefined' ? _collectToolResultSnippetsByTid : undefined;
  window._transparentOrderedToolCall = typeof _transparentOrderedToolCall !== 'undefined' ? _transparentOrderedToolCall : undefined;
  window._assistantTurnAnchorSettledFinalAnswer = typeof _assistantTurnAnchorSettledFinalAnswer !== 'undefined' ? _assistantTurnAnchorSettledFinalAnswer : undefined;
  window._reanchorPinnedTailAfterRender = typeof _reanchorPinnedTailAfterRender !== 'undefined' ? _reanchorPinnedTailAfterRender : undefined;
  window._scrollAfterMessageRender = typeof _scrollAfterMessageRender !== 'undefined' ? _scrollAfterMessageRender : undefined;
  window._maybeRecoverVirtualizedBlankViewport = typeof _maybeRecoverVirtualizedBlankViewport !== 'undefined' ? _maybeRecoverVirtualizedBlankViewport : undefined;
  window._parseProcessWakeupBody = typeof _parseProcessWakeupBody !== 'undefined' ? _parseProcessWakeupBody : undefined;
  window._processWakeupInfo = typeof _processWakeupInfo !== 'undefined' ? _processWakeupInfo : undefined;
  window._processWakeupCardHtml = typeof _processWakeupCardHtml !== 'undefined' ? _processWakeupCardHtml : undefined;
  window.renderMessages = typeof renderMessages !== 'undefined' ? renderMessages : undefined;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    _attachProgressBar,
    _setTransparentRowsExpanded,
    _transparentTurnCollapsedStates,
    _wireTransparentTurnToggle,
    _applyTransparentRowFading,
    _transparentTurnMetaMessage,
    _transparentTurnFooterHtml,
    _renderTransparentTurnFooter,
    _liveActivityUserExpanded,
    _activityDisclosureStoragePrefix,
    _activityDisclosureStorageKey,
    _readActivityDisclosureState,
    _writeActivityDisclosureState,
    _copyActivityDisclosureState,
    _activityKeyForLiveTurn,
    _onLiveActivityToggle,
    _materializeDeferredWorklogRows,
    _deferredWorklogRowsFromGroup,
    _rehydrateDeferredWorklogsFromCache,
    _toggleActivityGroup,
    _toggleToolWorklogGroup,
    _finalizeLiveActivityDisclosureGroup,
    _worklogReasonHtmlFromAnchor,
    _worklogReasonHtmlFromText,
    _renderWorklogReasonInto,
    _worklogReasonNodeFromText,
    _worklogAnchorKeySeq,
    _worklogReasonAnchorKey,
    _syncWorklogReasonFromAnchor,
    ensureLiveWorklogContainer,
    _migrateLegacyLiveActivityGroupsToWorklog,
    _appendWorklogReason,
    _toolIdentity,
    _toolDisclosureIdentity,
    _filterNewWorklogTools,
    _anchorSceneToolRowLogicalKey,
    _anchorSceneMergeToolRows,
    _appendWorklogStep,
    _anchorSceneRowsForRendering,
    _anchorSceneIsSettledSuccessfulCompression,
    _anchorSceneToolCallFromRow,
    _anchorSceneRowTimestampSeconds,
    _anchorSceneNodeForRow,
    _anchorSceneTransparentNodeForRow,
    _anchorSceneLiveTokenFinalPrefix,
    _anchorSceneLastNonTerminalWorkRowIndex,
    _anchorSceneProseMatchesFinalAnswer,
    _anchorSceneWorklogGroup,
    _renderAnchorSceneRowsIntoWorklog,
    _liveProcessedWorklogAnchorScore,
    _dedupeLiveProcessedWorklogAnchors,
    isLiveAnchorActivitySceneOwner,
    _projectLiveAnchorActivitySceneForStream,
    _prepareLiveAnchorScrollRebuildGuard,
    _restoreLiveAnchorScrollSnapshotAfterRebuild,
    _resetMismatchedLiveAssistantTurnForSession,
    _liveAnchorReasoningRowForFallback,
    _updateLiveAnchorReasoningRowForFallback,
    renderLiveAnchorActivityScene,
    _renderLiveAnchorActivitySceneTransparent,
    _transparentLiveRowKey,
    _transparentLiveRowsCompatible,
    _transparentLiveRowAttributePairs,
    _transparentLiveRowInteractiveState,
    _rehydrateTransparentLiveRow,
    _refreshTransparentThinkingLiveRow,
    _bindTransparentFadeCleanup,
    _appendTransparentFadeText,
    _refreshTransparentFadeProseRow,
    _refreshTransparentLiveRow,
    _renderLiveAnchorActivitySceneForStream,
    _renderLiveAnchorActivitySceneSnapshotForStream,
    _anchorSceneSceneHasWorklogWorthyRows,
    _ANCHOR_SCENE_ERRORED_TERMINAL_STATES,
    _anchorSceneHasErroredTerminalState,
    _renderSettledAnchorSceneTransparentForMessage,
    _TRANSPARENT_SETTLED_ROW_CAP,
    _TRANSPARENT_SETTLED_ROW_CAP_SLACK,
    _tOrDefault,
    _buildTransparentEarlierStepsAffordance,
    _revealTransparentEarlierSteps,
    _computeTransparentHiddenPrefixCount,
    _keepSettledWorklogOpenForStreamId,
    _shouldKeepSettledWorklogOpenForStreamSettle,
    _armKeepSettledWorklogOpen,
    _disarmKeepSettledWorklogOpen,
    _assistantTurnHasVisibleRenderedSegment,
    _collapseJustSettledWorklogInPlace,
    _isKeepSettledWorklogOpenArmed,
    _renderSettledAnchorSceneForMessage,
    _syncLiveWorklogReasonsForAnchor,
    _clearLiveActivityUserIntent,
    ensureActivityGroup,
    normalizeLiveActivityGroupPlacement,
    ensureRunActivityGroup,
    _liveRunStatusTimers,
    _liveRunStatusTokens,
    _liveRunStatusSessionId,
    _formatRunElapsed,
    _moveLiveRunStatusToTurnEnd,
    placeLiveRunStatusHost,
    showLiveRunStatus,
    _renderLiveRunStatusContent,
    updateLiveRunStatus,
    _syncLiveRunStatusAfterRender,
    hideLiveRunStatus,
    _startLiveRunStatusTimer,
    _clearLiveRunStatusTimer,
    ensureRunActivityForCurrentTurn,
    closeCurrentLiveActivityGroup,
    _compressionStateForCurrentSession,
    isCompressionUiRunning,
    _restoreCompressionPlaceholder,
    clearCompressionUi,
    setCompressionUi,
    _compressionCardsHtml,
    _autoCompressionBaseDetail,
    _autoCompressionPreviewText,
    _autoCompressionDetailText,
    _autoCompressionCardsHtml,
    _autoCompressionWorklogNode,
    _compressionCardsNode,
    appendLiveCompressionCard,
    _isHandoffSummaryToolPayload,
    _parseHandoffSummaryPayload,
    _handoffSummaryStateFromMessage,
    _collectHandoffSummaryStates,
    _isContextCompactionMessage,
    _isContextCompactionText,
    _isPreservedCompressionTaskListMarkerText,
    _isPreservedCompressionTaskListMarkerOnlyText,
    _isPreservedCompressionTaskListMessage,
    _isMarkerOnlyAssistantCompressionMessage,
    _preservedCompressionTaskListPreview,
    _compressionMessageAnchorKey,
    _compressionAnchorIndex,
    _latestCompressionReferenceMessage,
    _shouldShowSettledCompressionReference,
    _compressionReferenceCardHtml,
    _preservedCompressionTaskListCardHtml,
    _preservedCompressionTaskListCardsHtml,
    _latestTodoToolItems,
    _hasActiveTodoItems,
    _latestPreservedCompressionTaskListMessages,
    _isSameLocalDay,
    _formatMessageFooterTimestamp,
    _compressionEngineForSession,
    _compressionModeForSession,
    _engineAwareCompressionCopy,
    _compressionStatusCardHtml,
    _handoffStateForCurrentSession,
    clearHandoffUi,
    setHandoffUi,
    _handoffCardsHtml,
    _handoffCardsNode,
    _contextCompactionMessageHtml,
    renderCompressionUi,
    _sessionHtmlCache,
    _sessionHtmlCacheSid,
    _transparentRevealedTurns,
    _transparentRevealKey,
    clearMessageRenderCache,
    _addBoundedHash,
    _hashObjectInto,
    _messageRenderCacheSignature,
    _clipCliToolSnippet,
    _cliToolResultText,
    _cliLooksLikePatchDiff,
    _cliToolResultSnippet,
    _prefixedCliDiffLines,
    _firstOwnedValue,
    _cliPatchSnippetFromArgs,
    _cliToolCardSnippet,
    _cliToolCardHasDiffSnippet,
    _assistantToolAnchorIdxForMessage,
    _toolArgsSnapshot,
    _idLinkedHistoricalMessageText,
    _idLinkedHistoricalMessageHasVisibleText,
    _idLinkedHistoricalMessageRef,
    _idLinkedHistoricalToolArguments,
    _idLinkedHistoricalToolResultRaw,
    _idLinkedHistoricalRedactSnippet,
    _idLinkedHistoricalHasVisibleSidecar,
    _idLinkedHistoricalTurnScene,
    _hydrateIdLinkedHistoricalToolScenes,
    _captureMessageScrollSnapshot,
    _messageScrollSnapshotInputChanged,
    _abandonMessageScrollSnapshot,
    _restorePinnedMessageScrollSnapshot,
    _restoreMessageScrollSnapshot,
    _MOBILE_ANCHOR_BASE_SETTLE_MS,
    _MOBILE_ANCHOR_POST_TRANSITION_MS,
    _MOBILE_ANCHOR_MAX_HOLD_MS,
    _mobileAnchorSuppressReleaseTimer,
    _mobileAnchorSuppressRafId,
    _mobileAnchorTransitionListenerBound,
    _mobileAnchorSuppressArmedAt,
    _mobileAnchorMaxHoldTimer,
    _liftMobileAnchorSuppression,
    _bindMobileAnchorTransitionExtender,
    _desktopAnchorRealignDelta,
    _restoreMessageScrollSnapshotSameFrame,
    _renderMessagesWithScrollSnapshot,
    _assistantTurnAnchorSettledFinalAnswerWarned,
    _transparentStreamOrderedParts,
    _legacySettledFallbackHasToolMetadata,
    _transparentOrderedDisplayText,
    _collectToolResultSnippetsByTid,
    _transparentOrderedToolCall,
    _assistantTurnAnchorSettledFinalAnswer,
    _reanchorPinnedTailAfterRender,
    _scrollAfterMessageRender,
    _maybeRecoverVirtualizedBlankViewport,
    _parseProcessWakeupBody,
    _processWakeupInfo,
    _processWakeupCardHtml,
    renderMessages,
  };
}
