// `todos` is the single source of truth for the Todos panel.  Any update
// goes through the `todo_state` SSE event (live) or session.todo_state
// (cold-load).  `todoStateMeta` doubles as a sentinel: while it is null
// no explicit signal has been seen, so loadTodos() falls back to the
// legacy reverse-scan over S.messages — that keeps new clients working
// against old servers (Phase 1 may not yet be deployed everywhere).
// See api/todo_state.py for the wire contract.
const S={session:null,messages:[],entries:[],busy:false,pendingFiles:[],toolCalls:[],activeStreamId:null,currentDir:'.',activeProfile:'default',activeProfileIsDefault:true,showHiddenWorkspaceFiles:false,todos:[],todoStateMeta:null,_pendingSessionToolsets:null};
if (typeof window !== 'undefined') window.S = S;
if (typeof global !== 'undefined') global.S = S;

function assistantDisplayName(){
  return window._botName||'AGY';
}
const INFLIGHT={};  // keyed by session_id while request in-flight
const SESSION_QUEUES={};  // keyed by session_id for queued follow-up turns
const MAX_UPLOAD_BYTES=((window.__AGY_CONFIG__&&window.__AGY_CONFIG__.maxUploadBytes)||(window.__HERMES_CONFIG__&&window.__HERMES_CONFIG__.maxUploadBytes))||20*1024*1024;
const MAX_UPLOAD_MB=Math.round(MAX_UPLOAD_BYTES/1024/1024);
// Tracks which session's queue to drain in setBusy(false).
// Set to activeSid just before setBusy(false) in done/error handlers so the
// queue drains the session that *finished*, not the one currently viewed.
// Single-shot: setBusy() reads and clears this on every call. Concurrent
// back-to-back stream completions would overwrite it, but HTTPServer is
// single-threaded so only one done event fires at a time in practice.
let _queueDrainSid=null;
const $=id=>document.getElementById(id);
const OFFLINE_RECHECK_MS=2500;
const OFFLINE_HEALTH_TIMEOUT_MS=10000;
const OFFLINE_FETCH_FAILURES_BEFORE_BANNER=2;
let _offlineVisible=false;
let _offlineReason='browser';
let _offlineProbeTimer=null;
let _offlineChecking=false;
let _offlineProbePromise=null;
let _offlineHealthProbePromise=null;
let _offlineFetchProbeFailures=0;
let _offlineRawFetch=null;
let _offlineFetchPatched=false;
function _browserReportsOnline(){return !('onLine' in navigator)||navigator.onLine!==false;}
function _offlineHealthUrl(){const url=new URL('health',document.baseURI||location.href);url.searchParams.set('offline_probe',String(Date.now()));return url.href;}
function _setOfflineChecking(checking){
  _offlineChecking=!!checking;
  const btn=$('offlineCheckNow');
  if(btn){btn.disabled=_offlineChecking;btn.textContent=_offlineChecking?t('offline_checking'):t('offline_check_now');}
}
function _renderOfflineBanner(){
  const banner=$('offlineBanner');
  if(!banner)return;
  const detail=$('offlineDetails');
  if(detail)detail.textContent=t(_offlineReason==='browser'?'offline_browser_detail':'offline_network_detail');
  const title=$('offlineTitle');
  if(title)title.textContent=t('offline_title');
  const auto=$('offlineAutorefresh');
  if(auto)auto.textContent=t('offline_autorefresh');
  _setOfflineChecking(_offlineChecking);
  banner.hidden=false;
  banner.classList.add('visible');
}
function _startOfflineProbeTimer(){
  if(_offlineProbeTimer)return;
  _offlineProbeTimer=setInterval(()=>{checkOfflineRecoveryNow();},OFFLINE_RECHECK_MS);
}
function _stopOfflineProbeTimer(){
  if(_offlineProbeTimer){clearInterval(_offlineProbeTimer);_offlineProbeTimer=null;}
}
function showOfflineBanner(reason){
  _offlineVisible=true;
  _offlineReason=reason||(_browserReportsOnline()?'network':'browser');
  _renderOfflineBanner();
  _startOfflineProbeTimer();
}
function isOfflineBannerVisible(){return _offlineVisible;}
function _hideOfflineBanner(){
  _offlineVisible=false;
  _stopOfflineProbeTimer();
  _setOfflineChecking(false);
  const banner=$('offlineBanner');
  if(banner){banner.classList.remove('visible');banner.hidden=true;}
}
async function _probeOfflineRecovery(){
  if(_offlineHealthProbePromise)return _offlineHealthProbePromise;
  _offlineHealthProbePromise=(async()=>{
    const fetcher=_offlineRawFetch||window.fetch.bind(window);
    // Bound the probe so a black-hole network (connected, server hung, packets
    // dropped) can't delay the banner past a few seconds — the probe now gates
    // the initial banner display on the offline-event/startup paths.
    let ctrl=null,timer=null;
    try{ctrl=(typeof AbortController!=='undefined')?new AbortController():null;}catch(_){ctrl=null;}
    if(ctrl)timer=setTimeout(()=>{try{ctrl.abort();}catch(_){}},OFFLINE_HEALTH_TIMEOUT_MS);
    try{
      const opts={cache:'no-store',credentials:'include'};
      if(ctrl)opts.signal=ctrl.signal;
      const res=await fetcher(_offlineHealthUrl(),opts);
      return !!(res&&res.ok);
    }catch(_){return false;}
    finally{if(timer)clearTimeout(timer);}
  })();
  try{return await _offlineHealthProbePromise;}
  finally{_offlineHealthProbePromise=null;}
}
async function _showOfflineBannerIfProbeFails(reason,opts){
  opts=opts||{};
  const visibleAtStart=_offlineVisible;
  const requireConsecutiveFailures=opts.requireConsecutiveFailures!==false;
  if(visibleAtStart)_setOfflineChecking(true);
  const ok=await _probeOfflineRecovery();
  if(visibleAtStart)_setOfflineChecking(false);
  if(ok){
    _offlineFetchProbeFailures=0;
    if(_offlineVisible){_stopOfflineProbeTimer();await _recoverFromOfflineSoftly();}
    return true;
  }
  if(!visibleAtStart&&requireConsecutiveFailures){
    _offlineFetchProbeFailures+=1;
    if(_offlineFetchProbeFailures<OFFLINE_FETCH_FAILURES_BEFORE_BANNER)return false;
  }
  showOfflineBanner(reason||(_browserReportsOnline()?'network':'browser'));
  return false;
}
async function checkOfflineRecoveryNow(){
  if(_offlineProbePromise)return _offlineProbePromise;
  _offlineProbePromise=(async()=>{
    if(!_offlineVisible)return false;
    _setOfflineChecking(true);
    const ok=await _probeOfflineRecovery();
    _setOfflineChecking(false);
    if(ok){_offlineFetchProbeFailures=0;if(!_offlineVisible)return true;_stopOfflineProbeTimer();await _recoverFromOfflineSoftly();return true;}
    showOfflineBanner(_browserReportsOnline()?'network':'browser');
    return false;
  })();
  try{return await _offlineProbePromise;}
  finally{_offlineProbePromise=null;}
}
// Recover from a transient "Connection lost" without a full page reload.
//
// The offline banner fires whenever a fetch/SSE errors — which Android does
// aggressively every time the PWA is backgrounded, even for a second. The old
// behaviour here was `window.location.reload()`: a hard cold boot that re-runs
// the whole app and re-pulls /api/sessions + /api/session, producing the
// multi-second "reload to see the conversation I was just in" flash on every
// resume. The reload was also intermittent (only when a request actually
// errored that time), matching the reported "sometimes it reloads, sometimes
// it doesn't".
//
// The server keeps the agent running and buffers stream events while no
// subscriber is attached (#2307), so a hard reload is never required to
// recover — we just need to reattach. This does the soft path: hide the
// banner, restart the gateway SSE (bfcache/background kills the connection),
// and re-fetch the active session so any messages that landed while we were
// away appear. A full reload is the fallback only if the soft path throws.
async function _recoverFromOfflineSoftly(){
  try{
    _hideOfflineBanner();
    if(typeof startGatewaySSE==='function') startGatewaySSE();
    if(S.session && typeof refreshSession==='function'){
      await refreshSession();
    }
    // After refreshSession() sets S.activeStreamId, reattach if a stream is live.
    // The server buffers events while no subscriber is attached (#2307/#3863).
    const sid=S.session&&S.session.session_id;
    const streamId=S.session&&S.session.active_stream_id;
    if(sid&&streamId&&typeof attachLiveStream==='function'){
      let status=null;
      try{
        status=await api(`/api/chat/stream/status?stream_id=${encodeURIComponent(streamId)}`);
      }catch(_){/* stream status check failed — leave session refreshed but don't reattach */}
      // Outside the probe's catch so an attachLiveStream throw reaches the
      // outer fallback (hard reload) instead of being silently swallowed.
      if(status&&status.active) attachLiveStream(sid,streamId,S.session.pending_attachments||[],{reconnecting:true});
    }
    return true;
  }catch(_){
    // Soft reattach failed (server mid-restart, session gone, etc.) — fall
    // back to the original hard reload so the user is never stuck offline.
    window.location.reload();
    return false;
  }
}
function _isAbortError(e){return !!(e&&(e.name==='AbortError'||e.code===20));}
function _patchOfflineFetch(){
  if(_offlineFetchPatched||typeof window.fetch!=='function')return;
  _offlineFetchPatched=true;
  _offlineRawFetch=window.fetch.bind(window);
  window.fetch=async function(...args){
    try{return await _offlineRawFetch(...args);}
    catch(e){
      if(!_isAbortError(e)&&(e instanceof TypeError||!_browserReportsOnline())){
        void _showOfflineBannerIfProbeFails(_browserReportsOnline()?'network':'browser');
      }
      throw e;
    }
  };
}
function initOfflineMonitor(){
  _patchOfflineFetch();
  window.addEventListener('offline',()=>{void _showOfflineBannerIfProbeFails('browser',{requireConsecutiveFailures:false});});
  window.addEventListener('online',()=>{if(_offlineVisible)checkOfflineRecoveryNow();});
  if(!_browserReportsOnline())void _showOfflineBannerIfProbeFails('browser',{requireConsecutiveFailures:false});
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',initOfflineMonitor,{once:true});
else initOfflineMonitor();
// Redirect to login when the server responds with 401 (auth session expired).
// Handles iOS PWA standalone mode and keeps subpath mounts like /hermes/ from
// escaping to the personal site root /login.
// #5578: on a login-shaped page, reload 'login' WITHOUT a next (avoid self-nesting).
function _redirectIfUnauth(res){if(res&&res.status===401){var _p=(window.location.pathname||'').replace(/\/+$/,'');if(/(?:^|\/)login$/.test(_p)){window.location.href='login';}else{window.location.href='login?next='+encodeURIComponent(window.location.pathname+window.location.search);}return true;}return false;}
function _getSessionQueue(sid, create=false){
  if(!sid) return [];
  if(!SESSION_QUEUES[sid]&&create) SESSION_QUEUES[sid]=[];
  return SESSION_QUEUES[sid]||[];
}
function _queueStorageKey(sid){
  return 'agy-queue-'+sid;
}
function _clearPersistedSessionQueue(sid){
  if(!sid) return;
  const key=_queueStorageKey(sid);
  try{sessionStorage.removeItem(key);}catch(_){}
  try{localStorage.removeItem(key);}catch(_){}
}
function _persistSessionQueueStorage(sid, queue){
  if(!sid) return;
  const q=Array.isArray(queue)?queue:[];
  if(!q.length){_clearPersistedSessionQueue(sid);return;}
  const key=_queueStorageKey(sid);
  let payload='[]';
  try{payload=JSON.stringify(q);}catch(_){return;}
  try{sessionStorage.setItem(key,payload);}catch(_){}
  try{localStorage.setItem(key,payload);}catch(_){}
}
function _readPersistedSessionQueue(sid){
  if(!sid) return [];
  const key=_queueStorageKey(sid);
  const read=(store)=>{
    try{
      const raw=store&&store.getItem?store.getItem(key):null;
      if(!raw) return null;
      const parsed=JSON.parse(raw);
      return Array.isArray(parsed)?parsed:null;
    }catch(_){return null;}
  };
  const sessionValue=read(sessionStorage);
  if(sessionValue&&sessionValue.length) return sessionValue;
  const localValue=read(localStorage);
  if(localValue&&localValue.length){
    try{sessionStorage.setItem(key,JSON.stringify(localValue));}catch(_){}
    return localValue;
  }
  return [];
}
function queueSessionMessage(sid, payload){
  if(!sid||!payload) return 0;
  const q=_getSessionQueue(sid,true);
  // Stamp created_at so the restore path can detect stale entries (agent already responded)
  const entry={...payload, _queued_at: Date.now()};
  q.push(entry);
  _persistSessionQueueStorage(sid,q);
  return q.length;
}
function shiftQueuedSessionMessage(sid){
  const q=_getSessionQueue(sid,false);
  if(!q.length) return null;
  const next=q.shift();
  if(!q.length){
    delete SESSION_QUEUES[sid];
    _clearPersistedSessionQueue(sid);
  } else {
    _persistSessionQueueStorage(sid,q);
  }
  return next;
}
function getQueuedSessionCount(sid){
  return _getSessionQueue(sid,false).length;
}
function _compressionSessionLock(){
  return window._compressionLockSid||null;
}
function _setCompressionSessionLock(sid){
  window._compressionLockSid=sid||null;
}
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function _matchBacktickFenceLine(line){
  const m=String(line||'').match(/^[ ]{0,3}(`{3,})([^`]*)$/);
  if(!m) return null;
  return {fence:m[1],len:m[1].length,info:(m[2]||'').trim()};
}
function _isBacktickFenceClose(line,minLen){
  const m=String(line||'').match(/^[ ]{0,3}(`{3,})[ \t]*$/);
  return !!(m&&m[1].length>=minLen);
}
/**
 * Render fenced code blocks inside user messages.
 * Extracts ```…``` fences, replaces them with placeholders,
 * escapes remaining text as plain HTML, then restores code blocks
 * with the same <pre><code> pipeline used by renderMd().
 * All non-fenced text stays escaped (no bold/italic/link interpretation).
 */

function _stripWorkspaceDisplayPrefix(text){
  // v1 sentinel format `[Workspace::v1: <escaped path>]\n` injected since #1918.
  // Legacy format `[Workspace: <path>]\n` may still be present in transcripts
  // saved before the v1 migration; fall through to the legacy regex when the
  // v1 strip didn't match. Mirrors the Python `include_legacy=True` branch in
  // api/streaming.py:_strip_workspace_prefix(). Per Opus advisor on stage-322.
  const value = String(text||'');
  const stripped = value.replace(/^\s*\[Workspace::v1:\s*(?:\\.|[^\]\\])+\]\s*/,'');
  if(stripped !== value) return stripped.trim();
  return value.replace(/^\s*\[Workspace:[^\]]+\]\s*/,'').trim();
}
function _renderUserFencedBlocks(text){
  const stash=[];
  const contextStash=[];
  const mathStash=[];
  const stashMath=(type,src)=>{mathStash.push({type,src});return '\x00UM'+(mathStash.length-1)+'\x00';};
  const sentContextHtml=(label,quoteText)=>{
    const safeLabel=String(label||'').trim()||'Context';
    const safeQuote=String(quoteText||'').replace(/\s+$/,'');
    return `<figure class="sent-selection-context" data-selected-context="1"><figcaption class="sent-selection-context-label">${esc(safeLabel)}</figcaption><blockquote class="sent-selection-context-quote">${esc(safeQuote)}</blockquote></figure>`;
  };
  const stashContext=(label,quote)=>{contextStash.push(sentContextHtml(label,quote));return '\x00UC'+(contextStash.length-1)+'\x00';};
  const stashSelectedContextBlocks=(value)=>{
    const lines=String(value||'').split('\n');
    const marker='<!-- agy-selected-context -->';
    const out=[];
    for(let i=0;i<lines.length;i++){
      const labelMatch=lines[i].match(/^\*\*([^\n]{1,200}):\*\*\s*$/);
      if(!labelMatch){out.push(lines[i]);continue;}
      const quoteLines=[];
      let j=i+1;
      if(lines[j]!==marker){out.push(lines[i]);continue;}
      j++;
      while(j<lines.length&&/^>/.test(lines[j])){
        quoteLines.push(lines[j].replace(/^>[ \t]?/,''));
        j++;
      }
      if(!quoteLines.length){out.push(lines[i]);continue;}
      out.push(stashContext(labelMatch[1], quoteLines.join('\n')));
      i=j-1;
    }
    return out.join('\n');
  };
  const restoreMath=html=>String(html||'').replace(/\x00UM(\d+)\x00/g,(_,i)=>{
    const item=mathStash[+i];
    if(!item) return '';
    if(item.type==='display') return `<div class="katex-block" data-katex="display">${esc(item.src)}</div>`;
    return `<span class="katex-inline" data-katex="inline">${esc(item.src)}</span>`;
  });
  let s=String(text||'');
  // Extract fenced code blocks FIRST so math regexes never run inside fenced
  // content. If math were stashed first, a user-typed code block containing
  // \[..\] / \(..\) / $$..$$ would be rendered as a KaTeX block inside
  // <pre><code> instead of as literal source. Mirrors renderMd()'s ordering.
  // CommonMark §4.5 line-anchored fence: the closing run must use at least
  // as many backticks as the opener, so inner triple-backtick fences remain content.
  s=s.replace(/(^|\n)[ ]{0,3}(`{3,})([^\n`]*)\n(?:([\s\S]*?)\n)?[ ]{0,3}\2`*[ \t]*(?=\n|$)/g,(_,lead,_fence,info,code)=>{
    const langInfo=(info||'').trim();
    const langMatch=langInfo.match(/^(\w[\w+-]*)$/);
    let lang=langMatch?(langMatch[1]||'').trim().toLowerCase():'';
    code=code||'';
    // Remove one trailing newline if present (the fence consumes its own)
    if(code.endsWith('\n')) code=code.slice(0,-1);
    const h=lang?`<div class="pre-header">${esc(lang)}</div>`:'';
    const langAttr=lang?` class="language-${esc(lang)}"`:'';
    const preClass=/^(md|markdown|mdx)$/.test(lang)?' class="md-source-block"':'';
    if(lang==='diff'||lang==='patch'){
      const colored=esc(code).split('\n').map(line=>{
        if(line.startsWith('@@')) return `<span class="diff-line diff-hunk">${line}</span>`;
        if(line.startsWith('+')) return `<span class="diff-line diff-plus">${line}</span>`;
        if(line.startsWith('-')) return `<span class="diff-line diff-minus">${line}</span>`;
        return `<span class="diff-line">${line}</span>`;
      }).join('\n');
      stash.push(`${h}<pre class="diff-block"><code${langAttr}>${colored}</code></pre>`);
    } else {
      stash.push(`${h}<pre${preClass}><code${langAttr}>${esc(code)}</code></pre>`);
    }
    return lead+'\x00UF'+(stash.length-1)+'\x00';
  });
  // Now stash math from the OUTSIDE-of-fence text. Display delimiters must
  // run before inline so $$..$$ isn't mis-parsed as $..$..$..$.
  s=s.replace(/\$\$([\s\S]+?)\$\$/g,(_,m)=>stashMath('display',m));
  s=s.replace(/\\\[([\s\S]+?)\\\]/g,(_,m)=>stashMath('display',m));
  s=s.replace(/\$([^\s$\n][^$\n]*?[^\s$\n]|\S)\$/g,(_,m)=>stashMath('inline',m));
  s=s.replace(/\\\((.+?)\\\)/g,(_,m)=>stashMath('inline',m));
  // Render selected-context payloads produced by Reply with selection as calm
  // quote cards in the sent user bubble. Keep ordinary user Markdown escaped;
  // only blocks carrying the internal marker get custom treatment.
  s=stashSelectedContextBlocks(s);
  // Escape remaining plain text and convert newlines to <br>
  s=esc(s).replace(/\n/g,'<br>');
  // Restore stashed code/context blocks, then math placeholders as KaTeX targets.
  s=s.replace(/\x00UF(\d+)\x00/g,(_,i)=>stash[+i]);
  s=s.replace(/\x00UC(\d+)\x00/g,(_,i)=>contextStash[+i]||'');
  s=restoreMath(s);
  return s;
}
function _statusCardHtml(card){
  card=card||{};
  const rows=Array.isArray(card.rows)?card.rows:[];
  const sessionId=String(card.sessionId||'');
  const shortSessionId=sessionId.length>22?`${sessionId.slice(0,10)}…${sessionId.slice(-8)}`:sessionId;
  const copyIcon=(typeof li==='function')?li('copy',13):'Copy';
  const copyBtn=sessionId
    ? `<button class="status-card-session-copy" type="button" data-copy-status-session="${esc(card.sessionId||'')}" title="${esc(t('copy'))}" onclick="copyStatusSessionId(this);event.stopPropagation()"><span>${esc(shortSessionId)}</span>${copyIcon}</button>`
    : '';
  const rowHtml=rows.map(row=>`
    <div class="status-card-row">
      <span class="status-card-label">${esc(row.label||'')}</span>
      <span class="status-card-value">${esc(row.value||'')}</span>
    </div>`).join('');
  return `<div class="status-card" data-status-card="1">
    <div class="status-card-head">
      <div class="status-card-title-wrap">
        <div class="status-card-title">${esc(card.title||t('status_heading'))}</div>
        <div class="status-card-subtitle">${esc(card.subtitle||'')}</div>
      </div>
      ${copyBtn}
    </div>
    <div class="status-card-grid">${rowHtml}</div>
  </div>`;
}

function _compressionRecoveryHtml(recovery, sessionId){
  if(!recovery||typeof recovery!=='object') return '';
  if(String(recovery.terminal_state||'')!=='compression_exhausted') return '';
  const action=String(recovery.recommended_action||'');
  if(action!=='start_focused_continuation') return '';
  const sid=String(recovery.source_session_id||sessionId||'');
  const title=String(recovery.title||'Context compression exhausted');
  const summary=String(recovery.summary||'Start a focused continuation, then describe the next narrow task.');
  const actionLabel=String(recovery.action_label||'Start focused continuation');
  const icon=(typeof li==='function')?li('git-branch',14):'';
  return `<div class="compression-recovery-card" data-compression-recovery-card="1">
    <div class="compression-recovery-copy">
      <div class="compression-recovery-title">${esc(title)}</div>
      <div class="compression-recovery-summary">${esc(summary)}</div>
    </div>
    <button class="compression-recovery-action" type="button" data-recovery-session-id="${esc(sid)}" onclick="startCompressionRecovery(this);event.stopPropagation()">${icon}<span>${esc(actionLabel)}</span></button>
  </div>`;
}

function _activeCompressionRecoveryPayload(){
  if(!S||!S.session) return null;
  const recovery=S.session.compression_recovery;
  if(recovery&&typeof recovery==='object'&&String(recovery.terminal_state||'')==='compression_exhausted') return recovery;
  // A cleared session-level recovery payload is authoritative. Only scan
  // message metadata for older sessions that never exposed this field.
  if(Object.prototype.hasOwnProperty.call(S.session,'compression_recovery')) return null;
  const messages=Array.isArray(S.messages)?S.messages:[];
  for(let i=messages.length-1;i>=0;i--){
    const msg=messages[i];
    const msgRecovery=msg&&msg._compressionRecovery;
    if(msgRecovery&&typeof msgRecovery==='object'&&String(msgRecovery.terminal_state||'')==='compression_exhausted') return msgRecovery;
  }
  return null;
}

function isGenericCompressionContinuationIntent(text){
  const raw=String(text||'').trim().toLowerCase();
  if(!raw) return false;
  const normalized=raw.replace(/[^\p{L}\p{N}]+/gu,' ').trim();
  const generic=new Set(['continue','continue please','go on','keep going','resume','proceed','carry on','继续','继续吧','接着','接着做','继续做','继续执行']);
  if(generic.has(normalized)) return true;
  const parts=normalized.split(/\s+/).filter(Boolean);
  return !!parts.length&&parts.length<=2&&parts.every(part=>generic.has(part));
}

function shouldInterceptCompressionRecoveryContinuation(text, files){
  const hasFiles=Array.isArray(files)&&files.length>0;
  if(hasFiles||!isGenericCompressionContinuationIntent(text)) return false;
  const recovery=_activeCompressionRecoveryPayload();
  return !!(recovery&&String(recovery.recommended_action||'')==='start_focused_continuation');
}

function showCompressionRecoveryContinuationHint(){
  const card=document.querySelector('[data-compression-recovery-card="1"]');
  if(card&&typeof card.scrollIntoView==='function'){
    try{card.scrollIntoView({block:'center',behavior:'smooth'});}catch(_){card.scrollIntoView();}
    const btn=card.querySelector('.compression-recovery-action');
    if(btn&&typeof btn.focus==='function') setTimeout(()=>btn.focus(),120);
  }
  if(typeof showToast==='function') showToast('This session exhausted context compression. Start a focused continuation, then describe the next narrow task.',4500,'warning');
}

async function startCompressionRecovery(btn){
  const sourceSid=String((btn&&btn.dataset&&btn.dataset.recoverySessionId)||(S.session&&S.session.session_id)||'').trim();
  if(!sourceSid) return;
  let retiredRecoveryCard=false;
  if(btn){btn.disabled=true;btn.classList.add('loading');}
  try{
    const data=await api('/api/session/compression-recovery/start',{method:'POST',body:JSON.stringify({session_id:sourceSid})});
    const sid=data&&data.session&&data.session.session_id;
    if(!sid) throw new Error('Compression recovery did not return a session.');
    try{localStorage.setItem('agy-webui-session',sid);}catch(_){}
    if(typeof loadSession==='function') await loadSession(sid,{preserveActiveInput:false});
    else if(data.session){S.session=data.session;if(typeof _adoptRegenerationRevision==='function')_adoptRegenerationRevision(data.session);S.messages=data.session.messages||[];syncTopbar();renderMessages();}
    if(typeof renderSessionList==='function') await renderSessionList();
    if(typeof _setActiveSessionUrl==='function') _setActiveSessionUrl(sid);
    if(typeof showToast==='function') showToast((data&&data.message)||'Started focused continuation.',3000,'success');
    const composer=$('msg');
    if(composer&&typeof composer.focus==='function') composer.focus();
  }catch(e){
    // A 409 means this session no longer has an active recovery action (the
    // session already moved on — e.g. a substantive prompt cleared it). The
    // persisted card in the transcript is stale, so retire it and show a neutral
    // note instead of a raw error. The server is authoritative on availability.
    if(e&&e.status===409){
      const staleCard=(btn&&btn.closest&&btn.closest('.compression-recovery-card'))
        ||document.querySelector('[data-compression-recovery-card="1"]');
      if(staleCard){
        staleCard.setAttribute('data-compression-recovery-consumed','1');
        const staleBtn=staleCard.querySelector('.compression-recovery-action');
        if(staleBtn){staleBtn.disabled=true;staleBtn.classList.remove('loading');}
        retiredRecoveryCard=true;
      }
      if(typeof showToast==='function') showToast('This conversation already moved on — the focused-continuation action is no longer available.',4000,'info');
      return;
    }
    if(typeof showToast==='function') showToast('Compression recovery failed: '+(e&&e.message||e),5000,'error');
  }finally{
    // Do NOT re-enable a button we deliberately retired in the 409 branch.
    if(btn){if(!retiredRecoveryCard) btn.disabled=false;btn.classList.remove('loading');}
  }
}

const MESSAGE_RENDER_WINDOW_DEFAULT=50;
const MESSAGE_VIRTUAL_THRESHOLD_ROWS=80;
const MESSAGE_VIRTUAL_BUFFER_PX=900;
const MESSAGE_VIRTUAL_DEFAULT_ROW_HEIGHTS={
  user:120,
  process_wakeup:96,
  assistant:160,
  tool_call:400,
  default:140,
};
function _messageVirtualDefaultHeightForRole(role){
  return MESSAGE_VIRTUAL_DEFAULT_ROW_HEIGHTS[
    role&&Object.prototype.hasOwnProperty.call(MESSAGE_VIRTUAL_DEFAULT_ROW_HEIGHTS,role)?role:'default'
  ];
}
const MESSAGE_VIRTUAL_MEASUREMENT_MAX_RERENDERS=2;
let _messageRenderWindowSid=null;
let _messageRenderWindowSize=MESSAGE_RENDER_WINDOW_DEFAULT;
let _messageVirtualHeightCache=[];
let _messageVirtualHeightCacheEntries=[];
let _messageVirtualHeightCacheLen=0;
let _messageVirtualHeightCacheSrc=null;
let _messageVirtualEstimatedRowHeight=_messageVirtualDefaultHeightForRole('default');
let _messageVirtualScrollRaf=0;
let _messageVirtualWindowKey='';
let _messageVirtualMeasurementCycleKey='';
let _messageVirtualMeasurementRetryCount=0;
let _messageVirtualScrollActive=false;
let _messageVirtualScrollSettleTimer=0;
let _messageVirtualDeferredMeasurement=null;
let _msgNodeRecycleEnabled=false;
const _recycleStash=new Map();
const _recycleResetAttrs=[
  'data-transparent-turn-collapsed',
  'data-transparent-turn-toggle-bound',
  'data-anchor-scene-live-owner',
  'data-anchor-stream-id',
  'data-latest-assistant-response',
  'role',
  'aria-label',
  // Defensive reset for legacy/restored shells that may still carry the fallback live-turn marker.
  'data-live-assistant-turn',
];
let _scrollbarDragActive=false;
function _markMessageVirtualScrollActive(){
  _messageVirtualScrollActive=true;
  clearTimeout(_messageVirtualScrollSettleTimer);
  _messageVirtualScrollSettleTimer=setTimeout(()=>{
    _messageVirtualScrollActive=false;
    if(_messageVirtualDeferredMeasurement){
      const deferred=_messageVirtualDeferredMeasurement;
      _messageVirtualDeferredMeasurement=null;
      _scheduleMessageVirtualMeasurementRefresh(deferred);
    }
  },150);
}
// Cached visWithIdx array — invalidated when S.messages.length changes.
let _visWithIdxCache=null;
let _visWithIdxCacheLen=0;
let _visWithIdxCacheSrc=null;  // S.messages reference — detects wholesale replacement with same length
function clearVisibleMessageRowCache(){
  _visWithIdxCache=null;
  _visWithIdxCacheLen=0;
  _visWithIdxCacheSrc=null;
}
function _clearMessageVirtualHeightCache(){
  _messageVirtualHeightCache=[];
  _messageVirtualHeightCacheEntries=[];
  _messageVirtualHeightCacheLen=0;
  _messageVirtualHeightCacheSrc=null;
  _messageVirtualEstimatedRowHeight=_messageVirtualDefaultHeightForRole('default');
  _messageVirtualWindowKey='';
  _messageVirtualMeasurementCycleKey='';
  _messageVirtualMeasurementRetryCount=0;
  _messageVirtualScrollActive=false;
  clearTimeout(_messageVirtualScrollSettleTimer);
  _messageVirtualScrollSettleTimer=0;
  _messageVirtualDeferredMeasurement=null;
  if(typeof _clearUserRowIntrinsicHeightCache==='function') _clearUserRowIntrinsicHeightCache();
}
function _resetMessageRenderWindow(sid){
  _messageRenderWindowSid=sid||null;
  _messageRenderWindowSize=MESSAGE_RENDER_WINDOW_DEFAULT;
  _cancelMessageVirtualizedRender();
  _clearRenderCache();
  clearVisibleMessageRowCache();
  _clearMessageVirtualHeightCache();
}
function _cancelMessageVirtualizedRender(){
  if(_messageVirtualScrollRaf){
    cancelAnimationFrame(_messageVirtualScrollRaf);
    _messageVirtualScrollRaf=0;
  }
}
function _messageIsRenderable(m){
  if(!m||!m.role||m.role==='tool') return false;
  if(m._source === 'process_wakeup') return !!(msgContent(m)||m.attachments?.length);
  if(_isContextCompactionMessage(m)||_isPreservedCompressionTaskListMessage(m)) return false;
  if(_isRecoveryControlMessage(m)) return false;
  const hasTc=Array.isArray(m.tool_calls)&&m.tool_calls.length>0;
  const hasTu=Array.isArray(m.content)&&m.content.some(p=>p&&p.type==='tool_use');
  const hasPartialTc=Array.isArray(m._partial_tool_calls)&&m._partial_tool_calls.length>0;
  const hasReasoningAnchor=hasTc||hasTu||_messageHasReasoningPayload(m);
  const hasAssistantVisibleAnchor=hasTc||hasTu||hasPartialTc||_messageHasReasoningPayload(m)||_assistantMessageHasVisibleContent(m);
  return !!(msgContent(m)||m._statusCard||m.attachments?.length||(m.role==='assistant'&&(hasReasoningAnchor||hasAssistantVisibleAnchor)));
}
function _getVisibleMessagesWithIdx(){
  if(!_visWithIdxCache || _visWithIdxCacheLen !== S.messages.length || _visWithIdxCacheSrc !== S.messages){
    const rebuilt=[];
    let rawIdx=0;
    for(const m of (S.messages||[])){
      if(_messageIsRenderable(m)) rebuilt.push({m,rawIdx});
      rawIdx++;
    }
    _visWithIdxCache=rebuilt;
    _visWithIdxCacheLen=S.messages.length;
    _visWithIdxCacheSrc=S.messages;
  }
  return _visWithIdxCache;
}
function _messageVirtualWindow(opts){
  const total=Math.max(0, Number(opts&&opts.total)||0);
  const threshold=Math.max(1, Number(opts&&opts.threshold)||MESSAGE_VIRTUAL_THRESHOLD_ROWS);
  const defaultHeight=Math.max(1, Number(opts&&opts.defaultHeight)||_messageVirtualDefaultHeightForRole('default'));
  const bufferPx=Math.max(0, Number(opts&&opts.bufferPx)||MESSAGE_VIRTUAL_BUFFER_PX);
  const viewportHeight=Math.max(defaultHeight, Number(opts&&opts.viewportHeight)||defaultHeight*6);
  const keepTailCount=Math.max(0, Number(opts&&opts.keepTailCount)||0);
  const tailStart=Math.max(0, total-keepTailCount);
  const heights=Array.isArray(opts&&opts.heights)?opts.heights:[];
  const roleForIdx=typeof (opts&&opts.roleForIdx)==='function'?opts.roleForIdx:null;
  const rowHeightFor=(idx)=>{
    const cached=Number(heights[idx]);
    if(Number.isFinite(cached)&&cached>0) return cached;
    return roleForIdx?Math.max(1,_messageVirtualDefaultHeightForRole(roleForIdx(idx))):defaultHeight;
  };
  if(total<=Math.max(threshold, keepTailCount)){
    return {virtualized:false,start:0,end:total,topPad:0,bottomPad:0,total,tailStart};
  }
  const scrollTop=Math.max(0, Number(opts&&opts.scrollTop)||0);
  const targetTop=Math.max(0, scrollTop-bufferPx);
  const targetBottom=scrollTop+viewportHeight+bufferPx;
  let start=0;
  let offset=0;
  while(start<tailStart&&offset+rowHeightFor(start)<=targetTop){
    offset+=rowHeightFor(start);
    start++;
  }
  if(start>=tailStart){
    return {virtualized:true,start:tailStart,end:tailStart,topPad:offset,bottomPad:0,total,tailStart};
  }
  let end=start;
  let cursor=offset;
  while(end<tailStart&&cursor<targetBottom){
    cursor+=rowHeightFor(end);
    end++;
  }
  if(end<=start) end=Math.min(total, start+1);
  let bottomPad=0;
  for(let i=end;i<tailStart;i++) bottomPad+=rowHeightFor(i);
  return {
    virtualized:true,
    start,
    end,
    topPad:offset,
    bottomPad,
    total,
    tailStart,
  };
}
function _messageVirtualSpacer(height, where){
  const spacer=document.createElement('div');
  spacer.className='message-virtual-spacer';
  spacer.dataset.virtualSpacer=where||'gap';
  spacer.setAttribute('aria-hidden','true');
  spacer.style.height=Math.max(0,Math.round(height||0))+'px';
  spacer.style.flex='0 0 auto';
  return spacer;
}
function _messageVirtualWindowKeyFor(windowMetrics){
  if(!windowMetrics) return '';
  return [
    windowMetrics.virtualized?1:0,
    windowMetrics.start,
    windowMetrics.end,
    Math.round(windowMetrics.topPad||0),
    Math.round(windowMetrics.bottomPad||0),
    windowMetrics.tailStart||0,
  ].join(':');
}
function _messageVirtualMeasurementCycleKeyFor(windowMetrics){
  if(!windowMetrics) return '';
  return [
    windowMetrics.virtualized?1:0,
    windowMetrics.start,
    windowMetrics.end,
    windowMetrics.tailStart||0,
  ].join(':');
}
function _scheduleMessageVirtualMeasurementRefresh(windowMetrics){
  if(_messageVirtualScrollActive){
    _messageVirtualDeferredMeasurement=windowMetrics;
    return;
  }
  const cycleKey=_messageVirtualMeasurementCycleKeyFor(windowMetrics);
  if(_messageVirtualMeasurementCycleKey!==cycleKey){
    _messageVirtualMeasurementCycleKey=cycleKey;
    _messageVirtualMeasurementRetryCount=0;
  }
  if(_messageVirtualMeasurementRetryCount>=MESSAGE_VIRTUAL_MEASUREMENT_MAX_RERENDERS) return;
  _messageVirtualMeasurementRetryCount++;
  requestAnimationFrame(()=>{ _scheduleMessageVirtualizedRender(true); });
}
function _markMessageVirtualMeasurementsSettled(windowMetrics){
  _messageVirtualMeasurementCycleKey=_messageVirtualMeasurementCycleKeyFor(windowMetrics);
  _messageVirtualMeasurementRetryCount=0;
}
function _messageVirtualHeightEntryMatches(previousEntry, nextEntry){
  return !!(
    previousEntry&&nextEntry&&
    previousEntry.m===nextEntry.m
  );
}
function _messageVirtualHeightPrefixEntryMatches(previousEntry, nextEntry){
  return !!(
    previousEntry&&nextEntry&&
    previousEntry.rawIdx===nextEntry.rawIdx&&
    _messageVirtualHeightEntryMatches(previousEntry, nextEntry)
  );
}
function _syncMessageVirtualHeightCache(visWithIdx){
  const nextEntries=Array.isArray(visWithIdx)
    ? visWithIdx.map(entry=>entry?{rawIdx:entry.rawIdx,m:entry.m}:entry)
    : [];
  if(
    _messageVirtualHeightCacheLen===S.messages.length &&
    _messageVirtualHeightCacheSrc===S.messages &&
    _messageVirtualHeightCacheEntries.length===nextEntries.length
  ) return;
  const previousEntries=Array.isArray(_messageVirtualHeightCacheEntries)?_messageVirtualHeightCacheEntries:[];
  const previousHeights=Array.isArray(_messageVirtualHeightCache)?_messageVirtualHeightCache.slice():[];
  let nextHeights=null;
  if(!previousEntries.length){
    nextHeights=new Array(nextEntries.length);
  }else if(!nextEntries.length){
    _clearMessageVirtualHeightCache();
    _messageVirtualHeightCacheLen=S.messages.length;
    _messageVirtualHeightCacheSrc=S.messages;
    return;
  }else{
    const sharedPrefix=Math.min(previousEntries.length,nextEntries.length);
    let prefixMatches=true;
    for(let i=0;i<sharedPrefix;i++){
      if(!_messageVirtualHeightPrefixEntryMatches(previousEntries[i], nextEntries[i])){
        prefixMatches=false;
        break;
      }
    }
    if(prefixMatches){
      nextHeights=previousHeights.slice(0, sharedPrefix);
      nextHeights.length=nextEntries.length;
    }else if(nextEntries.length>=previousEntries.length){
      const prependedCount=nextEntries.length-previousEntries.length;
      let suffixMatches=true;
      for(let i=0;i<previousEntries.length;i++){
        if(!_messageVirtualHeightEntryMatches(previousEntries[i], nextEntries[i+prependedCount])){
          suffixMatches=false;
          break;
        }
      }
      if(suffixMatches){
        nextHeights=new Array(nextEntries.length);
        for(let i=0;i<previousEntries.length;i++){
          nextHeights[prependedCount+i]=previousHeights[i];
        }
      }
    }
  }
  if(nextHeights===null){
    _clearMessageVirtualHeightCache();
    _messageVirtualHeightCache=new Array(nextEntries.length);
  }else{
    _messageVirtualHeightCache=nextHeights;
    _messageVirtualWindowKey='';
  }
  _messageVirtualHeightCacheEntries=nextEntries;
  _messageVirtualHeightCacheLen=S.messages.length;
  _messageVirtualHeightCacheSrc=S.messages;
}
function _messageVirtualRoleForEntry(entry){
  const m=entry&&entry.m;
  if(!m) return 'default';
  if(m._source === 'process_wakeup') return 'process_wakeup';
  if(m.role==='user') return 'user';
  if(m.role==='assistant'){
    if((Array.isArray(m.tool_calls)&&m.tool_calls.length>0)||
       (Array.isArray(m.content)&&m.content.some(p=>p&&p.type==='tool_use'))||
       (Array.isArray(m._partial_tool_calls)&&m._partial_tool_calls.length>0))
      return 'tool_call';
    return 'assistant';
  }
  return 'default';
}
function _currentMessageVirtualWindow(visWithIdx, keepTailCount){
  _syncMessageVirtualHeightCache(visWithIdx);
  const container=$('messages');
  // #4325 opt-out: when the user disables transcript virtualization, always
  // render the full transcript (no windowing). Mirrors the <=threshold path so
  // every downstream consumer (render, anchor, prepend-delta) treats it as a
  // plain non-virtualized list.
  if(typeof window!=='undefined' && window._virtualizeTranscript===false){
    const total=visWithIdx.length;
    const tailStart=Math.max(0, total-Math.max(0, Number(keepTailCount)||0));
    return {virtualized:false,start:0,end:total,topPad:0,bottomPad:0,total,tailStart};
  }
  return _messageVirtualWindow({
    total:visWithIdx.length,
    scrollTop:container?container.scrollTop:0,
    viewportHeight:container?container.clientHeight:(_messageVirtualEstimatedRowHeight*6),
    heights:_messageVirtualHeightCache,
    defaultHeight:_messageVirtualEstimatedRowHeight,
    roleForIdx:idx=>_messageVirtualRoleForEntry(visWithIdx[idx]),
    keepTailCount,
  });
}
function _messageVirtualPrependedHeightDelta(prependedRenderableCount){
  const count=Math.max(0, Number(prependedRenderableCount)||0);
  if(count<=0) return null;
  const visWithIdx=_getVisibleMessagesWithIdx();
  const virtualWindow=_currentMessageVirtualWindow(visWithIdx,_messageVirtualKeepTailCount());
  if(!virtualWindow||!virtualWindow.virtualized) return null;
  const limit=Math.min(count,_messageVirtualHeightCache.length);
  let total=0;
  for(let i=0;i<limit;i++){
    const cached=Number(_messageVirtualHeightCache[i]);
    total+=(Number.isFinite(cached)&&cached>0)?cached:_messageVirtualDefaultHeightForRole(_messageVirtualRoleForEntry(visWithIdx[i]));
  }
  return Math.max(0,Math.round(total));
}
function _messageVisibleIndexForRawIdx(rawIdx, visWithIdx){
  const list=Array.isArray(visWithIdx)?visWithIdx:_getVisibleMessagesWithIdx();
  for(let i=0;i<list.length;i++){
    if(list[i]&&list[i].rawIdx===rawIdx) return i;
  }
  return -1;
}
function _safeEncodeURIComponent(v){
  try{return encodeURIComponent(String(v));}
  catch(e){
    // encodeURIComponent threw URIError -> one or more lone UTF-16 surrogates.
    // Walk the string as UTF-16 code units: keep valid high(D800-DBFF) +
    // low(DC00-DFFF) pairs intact (so emoji survive) and drop lone surrogates.
    // No regex lookbehind/lookahead so this parses on every browser engine
    // (some older WebViews / Safari <16.4 don't support lookbehind in regex
    // literals, which would otherwise brick ui.js at parse time).
    const s=String(v);
    let cleaned='';
    for(let i=0;i<s.length;i++){
      const c=s.charCodeAt(i);
      if(c>=0xD800&&c<=0xDBFF){
        const n=(i+1<s.length)?s.charCodeAt(i+1):0;
        if(n>=0xDC00&&n<=0xDFFF){cleaned+=s[i]+s[i+1];i++;}
      }else if(c<0xDC00||c>0xDFFF){
        cleaned+=s[i];
      }
    }
    return encodeURIComponent(cleaned);
  }
}

function _messageViewportAnchorKeyForMessage(m){
  if(typeof _compressionMessageAnchorKey!=='function') return '';
  const key=_compressionMessageAnchorKey(m);
  if(!key) return '';
  return [key.role||'',key.ts??'',key.attachments??0,key.text||''].map(v=>_safeEncodeURIComponent(v)).join('|');
}
function _messageVisibleIndexForAnchorKey(anchorKey, visWithIdx){
  const key=String(anchorKey||'');
  if(!key) return -1;
  const list=Array.isArray(visWithIdx)?visWithIdx:_getVisibleMessagesWithIdx();
  for(let i=0;i<list.length;i++){
    if(list[i]&&_messageViewportAnchorKeyForMessage(list[i].m)===key) return i;
  }
  return -1;
}
function _messageSessionIndexBase(){
  const n=Number(typeof _oldestIdx!=='undefined'?_oldestIdx:0);
  return Number.isFinite(n)?Math.max(0,n):0;
}
function _messageSessionIndexForRawIdx(rawIdx){
  const n=Number(rawIdx);
  if(!Number.isFinite(n)) return null;
  return _messageSessionIndexBase()+n;
}
function _messageRawIdxForSessionIndex(sessionIdx){
  const n=Number(sessionIdx);
  if(!Number.isFinite(n)) return null;
  return n-_messageSessionIndexBase();
}
function _messageVirtualScrollTopForVisibleIdx(visWithIdx, visibleIdx, container){
  const idx=Math.max(0,Number(visibleIdx)||0);
  _syncMessageVirtualHeightCache(visWithIdx);
  const limit=Math.min(idx,_messageVirtualHeightCache.length);
  let offset=0;
  for(let i=0;i<limit;i++){
    const cached=Number(_messageVirtualHeightCache[i]);
    offset+=(Number.isFinite(cached)&&cached>0)?cached:_messageVirtualDefaultHeightForRole(_messageVirtualRoleForEntry(visWithIdx[i]));
  }
  const viewport=container?Math.max(0,Number(container.clientHeight)||0):0;
  return Math.max(0,Math.round(offset-(viewport*0.35)));
}
function _messageVirtualKeepTailCount(){
  return Math.min(_currentMessageRenderWindowSize(), MESSAGE_RENDER_WINDOW_DEFAULT);
}
function _captureMessageViewportAnchor(){
  const container=$('messages');
  if(!container) return null;
  const containerRect=container.getBoundingClientRect();
  const rows=Array.from(container.querySelectorAll('[data-msg-idx]'));
  for(const row of rows){
    const rawIdx=Number(row&&row.dataset&&row.dataset.msgIdx);
    if(!Number.isFinite(rawIdx)) continue;
    const rect=row.getBoundingClientRect();
    if(rect.bottom>containerRect.top+1){
      const sessionIdx=Number(row&&row.dataset&&row.dataset.sessionMsgIdx);
      // Record the current top-spacer (virtual topPad) height so the compensation
      // path can fall back to a topPad-delta shift when the anchor row itself is
      // recycled out of the render window after a measurement-driven re-render.
      const spacer=container.querySelector('[data-virtual-spacer="before"]');
      const topPadBefore=spacer?parseFloat(spacer.style.height||'0')||0:0;
      return {
        rawIdx,
        sessionIdx:Number.isFinite(sessionIdx)?sessionIdx:_messageSessionIndexForRawIdx(rawIdx),
        key:row&&row.dataset?String(row.dataset.messageAnchorKey||''):'',
        topOffset:rect.top-containerRect.top,
        topPadBefore,
        // Snapshot the scroll height at capture so a later realign can detect that
        // content grew between capture and restore — the streaming case where the
        // anchor's topOffset is stale and realigning to it would yank a still reader
        // backward (issue #5637).
        scrollHeightAtCapture:container.scrollHeight,
        inputGeneration:typeof _messageScrollInputGeneration==='number' ? _messageScrollInputGeneration : 0,
      };
    }
  }
  return null;
}
// Temporarily suppress the browser's native overflow-anchor on a scroll
// container so a JS scrollTop write is not double-compensated by the browser's
// own scroll-anchoring in the same frame. Returns a release fn that restores the
// prior inline value on the NEXT frame (after layout settles). No-op on desktop,
// where the resting computed value is already `none` (CSS hover/fine-pointer
// media query) — suppressing `none` changes nothing and the release restores the
// same empty inline value. Only mobile (resting `auto`) is actually affected,
// which is exactly where the double-compensation jump-back happens.
//
// Both this helper and _fixMobileScrollJank() gate on the SAME question — "is
// the browser's native scroll-anchor layer currently active on this element?" —
// routed through this one predicate so the two guards can't drift apart if the
// CSS media query ever changes (maintainer review on #5338). The computed-value
// test is more robust than a matchMedia('(hover:hover) and (pointer:fine)')
// check because it reflects the real resting value, including any inline
// override, not just the viewport media state.
function _browserOverflowAnchorActive(el){
  if(!el) return false;
  try{ return getComputedStyle(el).overflowAnchor==='auto'; }catch(_){ return false; }
}
// iOS/iPadOS WebKit detection for the issue #5637 stale-anchor hold gate. CSS
// overflow-anchor is INERT on iOS WebKit (see static/style.css — the mobile
// content-visibility block deliberately does NOT set overflow-anchor:none because
// it is a no-op on iOS and, on Android, re-opens the #4856/#5338 jump-to-top
// regression). So `overflow-anchor:auto` computes on `.messages` on iOS but the
// engine never actually holds the viewport there. The stale-anchor refusal relies
// on that engine to hold the reader, so it is only safe on Android (working
// overflow-anchor), NOT iOS — refusing on iOS leaves a scrolled-up reader unheld,
// the same class as the desktop regression, one platform over.
// Detection covers classic iPhone/iPod/iPad UAs AND iPadOS 13+, which reports a
// desktop 'MacIntel' platform but is distinguishable by touch support (a real Mac
// has maxTouchPoints 0). Excludes MSStream (old IE on Windows Phone false-matched
// 'like iPhone').
function _isIOSWebKit(){
  try{
    const nav=(typeof navigator!=='undefined')?navigator:null;
    if(!nav) return false;
    if(nav.MSStream) return false;
    const ua=String(nav.userAgent||'');
    if(/iP(ad|hone|od)/.test(ua)) return true;
    // iPadOS 13+ masquerades as macOS; a Mac has no touch, an iPad does.
    if(nav.platform==='MacIntel' && Number(nav.maxTouchPoints)>1) return true;
  }catch(_){}
  return false;
}
// Stable "native overflow-anchor holds this viewport" predicate for the issue
// #5637 stale-anchor hold gate. The two stale-anchor refusals below assume the
// browser's native overflow-anchor layer will hold the viewport once the JS
// restore is refused. That is only true where the engine ACTUALLY compensates:
//   - desktop (hover+fine-pointer): CSS keeps `.messages` at overflow-anchor:none
//     -> engine off -> refusing leaves nothing to hold the reader. Excluded via
//     matchMedia('(pointer:coarse)') being false.
//   - iOS WebKit: overflow-anchor is INERT (see _isIOSWebKit) even though it
//     computes to `auto` -> engine never holds -> refusing strands a scrolled-up
//     reader. Excluded via _isIOSWebKit().
//   - Android touch: overflow-anchor:auto AND the engine works -> refusing is safe,
//     native anchoring holds. This is the ONLY platform the refusal targets.
// We must NOT decide this with `_browserOverflowAnchorActive(#messages)` alone,
// because `_restoreMessageViewportAnchor` temporarily writes an inline
// `overflowAnchor:'none'` on #messages for its own scroll write and only restores
// it on the next frame; when the realign fires every live tick that inline 'none'
// persists across ticks, so a computed-value probe would read 'none' mid-realign
// and wrongly classify a touch device as "desktop", letting the stale realign
// through. A matchMedia('(pointer:coarse)') test reflects the input device and
// cannot be mutated by that inline override, so it stays steady mid-realign;
// desktop (fine pointer) stays false. Fall back to the computed-anchor probe when
// matchMedia is unavailable.
function _isTouchLikeMessageViewport(el){
  // iOS WebKit is touch (pointer:coarse) but overflow-anchor is inert there, so the
  // refusal's premise fails — treat it like desktop (keep the semantic realign).
  if(_isIOSWebKit()) return false;
  try{
    if(typeof matchMedia==='function' && matchMedia('(pointer:coarse)').matches) return true;
  }catch(_){}
  // Best-effort fallback for the (today essentially non-existent) no-matchMedia
  // environment: the computed-anchor probe can transiently read 'none' during a
  // realign burst (see comment above), so on such a touch device this could
  // re-admit the original yank. matchMedia('(pointer:coarse)') is universally
  // supported in every browser this UI targets, so the primary path is what runs.
  return _browserOverflowAnchorActive(el);
}
function _suppressBrowserOverflowAnchor(container){
  if(!container||!container.style) return null;
  // Only engage when the browser layer is actually active (auto). On desktop
  // (none) there is nothing to suppress.
  if(!_browserOverflowAnchorActive(container)) return null;
  const prevInline=container.style.overflowAnchor||'';
  container.style.overflowAnchor='none';
  let released=false;
  return function _release(){
    if(released) return;
    released=true;
    const restore=()=>{
      // Only restore if we still own the suppression (another render may have
      // re-set it); compare against the value we wrote.
      if(container.style.overflowAnchor==='none') container.style.overflowAnchor=prevInline;
    };
    if(typeof requestAnimationFrame==='function') requestAnimationFrame(restore);
    else restore();
  };
}
function _restoreMessageViewportAnchor(anchor, rawIdxDelta){
  const container=$('messages');
  if(!container||!anchor) return false;
  const anchorKey=String(anchor.key||'');
  const sessionIdx=Number(anchor.sessionIdx);
  const hasSessionIdx=Number.isFinite(sessionIdx);
  let row=anchorKey?Array.from(container.querySelectorAll('[data-message-anchor-key]')).find(el=>el&&el.dataset&&el.dataset.messageAnchorKey===anchorKey):null;
  if(row&&row.getClientRects&&row.getClientRects().length===0) row=null;
  // The anchor key is content-derived (role|ts|attachments|first-160-chars, built by
  // _messageViewportAnchorKeyForMessage) so it goes STALE while a live assistant
  // message is still streaming: every chunk that changes the first 160 chars
  // recomputes that row's data-message-anchor-key, so a snapshot captured mid-stream
  // no longer matches by key. We used to concede the moment the keyed lookup missed
  // (`if(!row&&anchorKey) return false`), and the caller then fell back to an ABSOLUTE
  // scrollTop=snapshot.top that does NOT compensate the above-viewport height growth
  // from that same streaming chunk — the residual DESKTOP scroll jump-back. (Desktop
  // rests at overflow-anchor:none, so #5392's mobile overflow-anchor guard is a no-op
  // here; this is a distinct code path.) The anchored row is still in the DOM under
  // its STABLE session-relative index, so recover it via sessionIdx before conceding.
  // A genuinely removed anchor (message compressed/deleted away) misses key AND
  // sessionIdx and still returns false. A missing sessionIdx is NOT degraded to the
  // window-relative rawIdx (which could resolve to a different message), preserving
  // the original per-tier guard.
  if(!row&&hasSessionIdx) row=container.querySelector(`[data-session-msg-idx="${sessionIdx}"]`);
  if(!row&&(anchorKey||hasSessionIdx)) return false;
  const targetIdx=Number(anchor.rawIdx)+Number(rawIdxDelta||0);
  if(!row&&Number.isFinite(targetIdx)) row=container.querySelector(`[data-msg-idx="${targetIdx}"]`);
  if(!row) return false;
  const containerRect=container.getBoundingClientRect();
  const rect=row.getBoundingClientRect();
  const targetTop=Number(anchor.topOffset)||0;
  // Streaming stale-anchor guard (issue #5637). During a live stream, content grows
  // ABOVE the viewport between anchor capture and this restore, so the anchor's
  // captured topOffset is stale and the realign delta becomes a spurious few-hundred-px
  // value that yanks a still reader backward. Detect it by content growth + absence of
  // real input intent — NOT by a scrollTop diff, because on an overflow-anchor:auto
  // container the browser itself moves scrollTop to compensate the growth (so a still
  // reader's scrollTop is not stationary). _recentMessage*ScrollIntent reflects genuine
  // touch/wheel/key input, which the browser's anchor layer never writes. If content
  // grew since capture AND there is no recent input intent AND the realign would move
  // scrollTop non-trivially, refuse it and let the browser overflow-anchor hold. An
  // actively scrolling reader (recent intent) keeps the legitimate realign; legacy
  // snapshots without the captured geometry keep prior behavior.
  //
  // Desktop guard (issue #5637 gate cert): the refusal is only safe where the
  // browser's native overflow-anchor layer can actually hold the viewport, i.e.
  // touch viewports where `.messages` computes to `overflow-anchor:auto`. On
  // hover+fine-pointer desktops `.messages` is `overflow-anchor:none`, so refusing
  // the realign would leave NOTHING to hold the reader after above-viewport growth
  // — the very yank this fixes on mobile, reintroduced on desktop. Gate the refusal
  // on `_isTouchLikeMessageViewport` so desktop keeps its semantic scrollTop realign.
  const _realignDelta=(rect.top-containerRect.top)-targetTop;
  const _shAtCap=Number(anchor.scrollHeightAtCapture);
  if(Number.isFinite(_shAtCap)){
    const _grewSinceCapture=(container.scrollHeight-_shAtCap)>4;
    const _activeIntent=(typeof _recentMessageScrollIntent==='function' && _recentMessageScrollIntent())
      || (typeof _recentMessageTouchScrollIntent==='function' && _recentMessageTouchScrollIntent());
    const _touchHold=(typeof _isTouchLikeMessageViewport==='function' && _isTouchLikeMessageViewport(container));
    if(_touchHold&&_grewSinceCapture&&!_activeIntent&&Math.abs(_realignDelta)>8){
      return false;
    }
  }
  _programmaticScroll=true;_programmaticScrollSetAt=performance.now();
  // Mobile-only jump fix: the resting overflow-anchor on .messages is `auto` on
  // touch devices (CSS media query keeps it `none` only for hover+fine-pointer
  // desktops). When we write scrollTop here to realign the anchor row, a mobile
  // browser's OWN overflow-anchor machinery ALSO shifts scrollTop in the same
  // frame if content height above the viewport changed — the two compensations
  // stack and yank the reader to an unrelated turn (the mobile jump-back). This is why the
  // bug is mobile-only and never reproduces on a desktop (none) browser. Suppress
  // the browser layer for this write; _releaseAnchorSuppression restores it next
  // frame. Desktop is already `none`, so this is a no-op there.
  const _releaseAnchorSuppression=(typeof _suppressBrowserOverflowAnchor==='function')
    ? _suppressBrowserOverflowAnchor(container) : null;
  container.scrollTop+=(rect.top-containerRect.top)-targetTop;
  if(_releaseAnchorSuppression) _releaseAnchorSuppression();
  if(typeof _deferClearProgrammaticScroll==='function') _deferClearProgrammaticScroll();
  else requestAnimationFrame(()=>{ setTimeout(()=>{ _programmaticScroll=false; },0); });
  return true;
}
let _messageViewportAnchorRemounting=false;
function _remountMessageViewportAnchor(anchor){
  const container=$('messages');
  if(!container||!anchor||_messageViewportAnchorRemounting) return false;
  const anchorKey=String(anchor.key||'');
  const visibleKeyNode=anchorKey
    ? Array.from(container.querySelectorAll('[data-message-anchor-key]')).find(node=>node&&node.dataset&&node.dataset.messageAnchorKey===anchorKey&&(!node.getClientRects||node.getClientRects().length>0))
    : null;
  if(visibleKeyNode) return true;
  const sessionIdx=Number(anchor.sessionIdx);
  const hasSessionIdx=Number.isFinite(sessionIdx);
  if(!anchorKey&&hasSessionIdx&&container.querySelector(`[data-session-msg-idx="${sessionIdx}"]`)) return true;
  const targetIdx=Number(anchor.rawIdx);
  if(!anchorKey&&!hasSessionIdx&&Number.isFinite(targetIdx)&&container.querySelector(`[data-msg-idx="${targetIdx}"]`)) return true;
  if(typeof _getVisibleMessagesWithIdx!=='function'||
     typeof _messageVisibleIndexForRawIdx!=='function'||
     typeof _messageVirtualScrollTopForVisibleIdx!=='function'||
     typeof renderMessages!=='function') return false;
  const visWithIdx=_getVisibleMessagesWithIdx();
  let visIdx=anchorKey?_messageVisibleIndexForAnchorKey(anchorKey,visWithIdx):-1;
  if(visIdx<0&&hasSessionIdx){
    const rawFromSession=_messageRawIdxForSessionIndex(sessionIdx);
    if(Number.isFinite(rawFromSession)) visIdx=_messageVisibleIndexForRawIdx(rawFromSession,visWithIdx);
  }
  if(visIdx<0&&Number.isFinite(targetIdx)) visIdx=_messageVisibleIndexForRawIdx(targetIdx,visWithIdx);
  if(visIdx<0) return false;
  // A virtualized anchor may be outside the current DOM. Scroll to its virtual
  // row and render once so the semantic restore below has a real target.
  _programmaticScroll=true;_programmaticScrollSetAt=performance.now();
  container.scrollTop=_messageVirtualScrollTopForVisibleIdx(visWithIdx,visIdx,container);
  _messageVirtualWindowKey='';
  _messageViewportAnchorRemounting=true;
  try{
    renderMessages({preserveScroll:true});
  }finally{
    _messageViewportAnchorRemounting=false;
    requestAnimationFrame(()=>{ setTimeout(()=>{ _programmaticScroll=false; },0); });
  }
  if(anchorKey){
    return !!Array.from(container.querySelectorAll('[data-message-anchor-key]')).find(node=>node&&node.dataset&&node.dataset.messageAnchorKey===anchorKey&&(!node.getClientRects||node.getClientRects().length>0));
  }
  if(hasSessionIdx) return !!container.querySelector(`[data-session-msg-idx="${sessionIdx}"]`);
  return Number.isFinite(targetIdx)&&!!container.querySelector(`[data-msg-idx="${targetIdx}"]`);
}
function _compensateScrollForMeasurementDelta(renderFn){
  const container=$('messages');
  if(!container) return renderFn();
  const anchorBefore=_captureMessageViewportAnchor();
  const scrollTopBefore=container.scrollTop;
  container.classList.add('vscroll-measuring');
  try{ renderFn(); }finally{ container.classList.remove('vscroll-measuring'); }
  if(!anchorBefore) return;
  if(scrollTopBefore<1){
    const spacer=container.querySelector('[data-virtual-spacer="before"]');
    if(!spacer||parseFloat(spacer.style.height||'0')<=0) return;
  }
  // Re-find the anchor row after the measurement-driven re-render. The primary
  // lookup is by rawIdx (the DOM index), but on a big virtualized session a large
  // scroll delta can RECYCLE the old anchor row out of the render window entirely
  // (verified via real-device telemetry: DOM collapsed to 1 row, scrollHeight
  // lurched by tens of thousands of px). The old code did `if(!row) return` here,
  // abandoning compensation → the full estimated↔measured height lurch hit
  // scrollTop uncompensated and threw the viewport to the top (the recurring
  // mobile scroll jump-back). Fall back to the stable sessionIdx anchor (captured in
  // _captureMessageViewportAnchor) before giving up, mirroring the "recover via
  // sessionIdx when the primary anchor key is gone" approach used elsewhere but for
  // the virtualization-measurement compensation path.
  let row=container.querySelector(`[data-msg-idx="${anchorBefore.rawIdx}"]`);
  if(!row&&Number.isFinite(Number(anchorBefore.sessionIdx))){
    row=container.querySelector(`[data-session-msg-idx="${anchorBefore.sessionIdx}"]`);
  }
  if(!row){
    // Anchor row is no longer rendered (recycled out of the virtual window). We
    // cannot measure its live offset, but we CAN keep the viewport visually
    // stable by compensating for the top-spacer (topPad) height change: the
    // whole reason scrollHeight lurched is that the estimated topPad was replaced
    // by a measured one. Shift scrollTop by that same delta so content under the
    // viewport does not appear to jump. Without this the browser lands at an
    // uncompensated absolute scrollTop against a wildly different scrollHeight.
    const spacerAfter=container.querySelector('[data-virtual-spacer="before"]');
    const topPadAfter=spacerAfter?parseFloat(spacerAfter.style.height||'0')||0:0;
    const topPadBefore=Number(anchorBefore.topPadBefore);
    if(Number.isFinite(topPadBefore)){
      const padDelta=topPadAfter-topPadBefore;
      if(Math.abs(padDelta)>=2){
        _programmaticScroll=true;_programmaticScrollSetAt=performance.now();
        container.scrollTop=Math.max(0,scrollTopBefore+padDelta);
        _lastScrollTop=container.scrollTop;
        _deferClearProgrammaticScroll();
      }
    }
    return;
  }
  const containerRect=container.getBoundingClientRect();
  const rowRect=row.getBoundingClientRect();
  const actualOffset=rowRect.top-containerRect.top;
  const delta=actualOffset-anchorBefore.topOffset;
  if(Math.abs(delta)<2) return;
  _programmaticScroll=true;_programmaticScrollSetAt=performance.now();
  container.scrollTop=scrollTopBefore+delta;
  _lastScrollTop=container.scrollTop;
  _deferClearProgrammaticScroll();
}
function _messageViewportIntersectsRenderedRow(){
  const container=$('messages');
  if(!container) return true;
  const containerRect=container.getBoundingClientRect();
  const rows=Array.from(container.querySelectorAll('[data-msg-idx]'));
  for(const row of rows){
    const rect=row.getBoundingClientRect();
    if(rect.bottom>containerRect.top+1&&rect.top<containerRect.bottom-1) return true;
  }
  return false;
}
// #5637/#5638 follow-up — kill the content-visibility scrollHeight collapse at its
// source. A virtualization wipe-and-rebuild recreates user rows as FRESH elements, which
// discards content-visibility:auto's last-remembered size, so an off-screen user row
// falls back to the flat `contain-intrinsic-size: auto 96px` estimate in the stylesheet.
// A tall user row (e.g. a long paste) then collapses scrollHeight by (realHeight-96px)
// the instant it's rebuilt off-screen, and the browser either force-clamps scrollTop
// (dTop≈dH layer-1 jump) or re-anchors to a far row (dTop≫dH browser re-anchor jump) —
// both mobile jump-back classes trace to this one collapse. Remember each user row's
// height keyed by its STABLE session-relative index so a rebuild reserves the real
// height, not 96px. Measured height (exact) wins; before a row is ever measured, a
// content-length estimate reserves the bulk so the fresh-element frame doesn't collapse
// either. Refreshed every measure pass, so edits self-heal. Desktop rests at
// content-visibility:visible (intrinsic-size ignored) → inert there, zero behavior change.
const _userRowIntrinsicHeightBySessionIdx=Object.create(null);
// Cleared on session switch alongside _messageVirtualHeightCache (both are
// per-session measured-height caches keyed by session-relative index). Without this,
// keys collide across sessions — _messageSessionIndexForRawIdx = _messageSessionIndexBase()
// + rawIdx and the base is 0 for the common non-offset session — so a new session's
// off-screen user rows would inherit the previous session's remembered heights and
// inflate scrollHeight until each is re-measured. Delete keys in place to keep the
// const binding stable for any closure that captured it.
function _clearUserRowIntrinsicHeightCache(){
  for(const k in _userRowIntrinsicHeightBySessionIdx) delete _userRowIntrinsicHeightBySessionIdx[k];
}
function _rememberUserRowIntrinsicHeight(sessionMsgIdx, height){
  const key=Number(sessionMsgIdx);
  if(!Number.isFinite(key)||!(height>0)) return;
  _userRowIntrinsicHeightBySessionIdx[key]=Math.round(height);
}
function _estimateUserRowIntrinsicHeight(rawText){
  const t=String(rawText||'');
  if(!t) return 96;
  // ~48 half-width chars/line at the mobile user-bubble width (≈90% of a phone viewport),
  // ~22px per line + ~24px row chrome; floored at the stylesheet's 96px so a short row never
  // reserves LESS than today (estimate can only add reserved height for tall rows, never
  // regress). CJK / full-width characters occupy ~2 columns each, so a Chinese/Japanese/
  // Korean paste wraps at ~24 chars/line — counting them as 1 badly UNDER-estimates the
  // height (a 3k-char CJK paste is ~2x taller than the naive length/48 guess). Weight wide
  // characters as 2 columns so the fresh-row reserve is close to reality even for a row the
  // reader has never scrolled into view (content-visibility:auto reports only the reserve
  // for a never-painted row, so a good estimate is the only backstop there). Uses a Unicode
  // range test (no \p{} — keep the RegExp engine-portable across the supported browsers).
  const explicitLines=(t.match(/\n/g)||[]).length+1;
  let columns=0;
  for(let i=0;i<t.length;i++){
    const c=t.charCodeAt(i);
    // CJK Unified + Ext-A, Hiragana/Katakana, Hangul, CJK symbols/punctuation, full-width forms.
    const wide=(c>=0x1100&&c<=0x115F)||(c>=0x2E80&&c<=0xA4CF)||(c>=0xAC00&&c<=0xD7A3)||
               (c>=0xF900&&c<=0xFAFF)||(c>=0xFE30&&c<=0xFE4F)||(c>=0xFF00&&c<=0xFF60)||(c>=0xFFE0&&c<=0xFFE6);
    columns+=wide?2:1;
  }
  const wrapLines=Math.ceil(columns/48);
  const lines=Math.max(explicitLines, wrapLines);
  return Math.max(96, Math.round(lines*22+24));
}
function _applyUserRowIntrinsicHeight(row, rawText){
  if(!row||!row.style||!row.dataset) return;
  const key=Number(row.dataset.sessionMsgIdx);
  const remembered=Number.isFinite(key)?Number(_userRowIntrinsicHeightBySessionIdx[key])||0:0;
  const estimate=_estimateUserRowIntrinsicHeight(rawText!=null?rawText:row.dataset.rawText);
  // Reserve the LARGER of the remembered measurement and the content estimate. A remembered
  // height can be a PARTIAL paint: a user row taller than the viewport that only ever had its
  // top slice scrolled through content-visibility:auto reports just the painted portion, not
  // its full height — persisting that would under-reserve and let scrollHeight collapse on the
  // next rebuild (the jump-back). Taking the max means a good estimate floors the reserve even
  // when the measurement under-read, while a full measurement (row shorter than the viewport,
  // fully painted) still wins when it exceeds the estimate.
  const h=Math.max(remembered, estimate);
  if(h>0) row.style.containIntrinsicSize='auto '+Math.round(h)+'px';
}
function _measureMessageVirtualRow(inner, entry){
  if(!inner||!entry) return 0;
  const primary=inner.querySelector(`[data-msg-idx="${entry.rawIdx}"]`);
  if(!primary) return 0;
  let totalHeight=Math.max(0, primary.getBoundingClientRect().height||0);
  if(primary.classList.contains('assistant-segment')){
    let sibling=primary.nextElementSibling;
    while(sibling){
      if(sibling.hasAttribute('data-msg-idx')) break;
      if(!(sibling.matches&&sibling.matches('.tool-call-group,.tool-card-row,.agent-activity-thinking,.thinking-card-row'))) break;
      totalHeight+=Math.max(0, sibling.getBoundingClientRect().height||0);
      sibling=sibling.nextElementSibling;
    }
  }
  // Persist the measured height so a later wipe-and-rebuild of this user row reserves its
  // real off-screen height instead of collapsing to the 96px estimate (the collapse that
  // clamps/re-anchors the viewport — #5637/#5638 mobile jump-back, both classes). The
  // typeof guard keeps _measureMessageVirtualRow runnable in the node test harnesses that
  // extract it without this helper (they stub every collaborator by name).
  if(totalHeight>0 && primary.dataset && primary.dataset.role==='user'
     && typeof _rememberUserRowIntrinsicHeight==='function'){
    _rememberUserRowIntrinsicHeight(primary.dataset.sessionMsgIdx, totalHeight);
    primary.style.containIntrinsicSize='auto '+Math.round(totalHeight)+'px';
  }
  return totalHeight;
}
function _updateMessageVirtualMeasurements(renderVisWithIdx, renderVisibleIdxs, virtualWindow){
  const inner=$('msgInner');
  if(!inner||!virtualWindow||!virtualWindow.virtualized||!renderVisWithIdx.length) return;
  let changed=false;
  let measuredCount=0;
  let measuredTotal=0;
  for(let vi=0;vi<renderVisWithIdx.length;vi++){
    const entry=renderVisWithIdx[vi];
    if(!entry) continue;
    const totalHeight=_measureMessageVirtualRow(inner, entry);
    if(totalHeight<=0) continue;
    const visibleIdx=Number(renderVisibleIdxs&&renderVisibleIdxs[vi]);
    if(!Number.isFinite(visibleIdx)) continue;
    if(Math.abs((Number(_messageVirtualHeightCache[visibleIdx])||0)-totalHeight)>1){
      _messageVirtualHeightCache[visibleIdx]=totalHeight;
      changed=true;
    }
    measuredTotal+=totalHeight;
    measuredCount++;
  }
  if(measuredCount>0){
    _messageVirtualEstimatedRowHeight=Math.max(60, Math.round(measuredTotal/measuredCount));
  }
  if(changed){
    _scheduleMessageVirtualMeasurementRefresh(virtualWindow);
  }else{
    _markMessageVirtualMeasurementsSettled(virtualWindow);
  }
}
// #5638 follow-up — the non-virtualized transcript path (the #4325 opt-out, where
// _virtualizeTranscript===false renders every row with no windowing) never runs the
// virtualized measure pass above, so a user row's real height is never remembered.
// content-visibility:auto on user rows then collapses a freshly-rebuilt off-screen tall
// user row to its flat contain-intrinsic-size estimate on every renderMessages() rebuild
// (each streaming frame does inner.innerHTML='' then rebuilds all rows as FRESH elements
// that have never painted at full size). scrollHeight shrinks by (realHeight-estimate),
// the browser force-clamps scrollTop, and the viewport jumps backward — the desktop/mobile
// jump-back, with JS=none because the clamp is the browser's own.
//
// The reliable moment to read a user row's REAL height is JUST BEFORE the wipe: the old
// rows are still in the DOM, laid out at full height (content-visibility:auto reports the
// true rect height once an element has painted, at any scroll position — verified: a tall
// off-screen user row still measures its real height pre-wipe). A POST-render read is
// unreliable because a freshly-rebuilt off-screen row reports its collapsed reserve, not
// its real size, so it would persist the wrong (small) value. Capture pre-wipe, keyed by
// the stable session-relative index, so the rebuild's _applyUserRowIntrinsicHeight reserves
// the real off-screen height and scrollHeight stays stable across the rebuild.
// Desktop rests at content-visibility:visible (intrinsic-size ignored) → inert there.
function _rememberRenderedUserRowIntrinsicHeights(){
  const container=$('messages');
  const inner=$('msgInner');
  if(!container||!inner) return;
  const rows=inner.querySelectorAll('.msg-row[data-role="user"][data-msg-idx]');
  if(!rows.length) return;
  const cRect=container.getBoundingClientRect();
  // Only trust a row that is currently WITHIN (or straddling) the viewport: such a row has
  // been painted at full size, so getBoundingClientRect().height is its REAL height. A row
  // that content-visibility:auto is skipping (fully off-screen and never painted this
  // session) reports only its contain-intrinsic-size reserve — persisting THAT would poison
  // the remembered height with the collapsed value and defeat the estimate backstop for a
  // never-seen row. The viewport intersection test is the reliable "has this row painted?"
  // signal (an off-screen row that WAS painted earlier keeps its real height too, but we
  // don't need it here — it either was captured on a prior in-view pass or the estimate
  // covers it). Small margin so a row just above/below the fold still counts as painted.
  const margin=Math.max(0, cRect.height||0);
  for(let i=0;i<rows.length;i++){
    const row=rows[i];
    if(!row||!row.dataset||!row.style) continue;
    const r=row.getBoundingClientRect();
    const measured=Math.max(0, r.height||0);
    if(!(measured>0)) continue;
    // In-viewport (with a one-screen margin) ⇒ painted ⇒ height is trustworthy — but only
    // for a row that FITS the viewport. A row taller than the viewport only ever paints the
    // intersecting slice under content-visibility:auto, so its measured height is a PARTIAL
    // value, not the full row. Floor every persisted height at the content estimate so a
    // partial paint can never lower the reserve below a reasonable full-row guess; a full
    // paint (short row) still wins when it exceeds the estimate.
    const inView=(r.bottom>=cRect.top-margin)&&(r.top<=cRect.bottom+margin);
    if(!inView) continue;
    const estimate=(typeof _estimateUserRowIntrinsicHeight==='function')
      ? _estimateUserRowIntrinsicHeight(row.dataset.rawText) : 0;
    const h=Math.max(measured, estimate);
    if(!(h>0)) continue;
    const key=Number(row.dataset.sessionMsgIdx);
    const remembered=Number.isFinite(key)?Number(_userRowIntrinsicHeightBySessionIdx[key])||0:0;
    // Keep the tallest reserve seen — a row mid-collapse (rebuild transient) can report a
    // shrunken size; never let that overwrite a good taller remembered value.
    if(h>=remembered && typeof _rememberUserRowIntrinsicHeight==='function'){
      _rememberUserRowIntrinsicHeight(row.dataset.sessionMsgIdx, h);
      row.style.containIntrinsicSize='auto '+Math.round(h)+'px';
    }
  }
}
function _scheduleMessageVirtualizedRender(force){
  const container=$('messages');
  const inner=$('msgInner');
  if(!container||!inner) return;
  const visWithIdx=_getVisibleMessagesWithIdx();
  const virtualWindow=_currentMessageVirtualWindow(visWithIdx,_messageVirtualKeepTailCount());
  const nextKey=_messageVirtualWindowKeyFor(virtualWindow);
  if(!force&&nextKey===_messageVirtualWindowKey) return;
  if(!virtualWindow.virtualized){
    _messageVirtualWindowKey=nextKey;
    return;
  }
  if(_messageVirtualScrollRaf) return;
  _messageVirtualScrollRaf=requestAnimationFrame(()=>{
    _messageVirtualScrollRaf=0;
    const liveVisWithIdx=_getVisibleMessagesWithIdx();
    const liveWindow=_currentMessageVirtualWindow(liveVisWithIdx,_messageVirtualKeepTailCount());
    const liveKey=_messageVirtualWindowKeyFor(liveWindow);
    if(!force&&liveKey===_messageVirtualWindowKey) return;
    if(_scrollbarDragActive){
      _programmaticScroll=true;
      _programmaticScrollSetAt=performance.now();
      _compensateScrollForMeasurementDelta(()=>{ renderMessages({ preserveScroll:true }); });
      _deferClearProgrammaticScroll();
      _messageVirtualWindowKey=liveKey;
      return;
    }
    _msgNodeRecycleEnabled=true;
    try{
      _compensateScrollForMeasurementDelta(()=>{ renderMessages({ preserveScroll:true }); });
    }
    finally{ _msgNodeRecycleEnabled=false; }
  });
}

// ── renderMd / _renderUserFencedBlocks cache ──────────────────────────────
// Long sessions re-render the same messages on every renderMessages() call.
// Cache the rendered HTML so unchanged messages skip the expensive regex
// pipeline entirely.  ~95% of messages are identical between renders.
const _renderCache = new Map();
const _renderCacheMax = 300;
function _clearRenderCache(){ _renderCache.clear(); }
function _renderCacheKey(text, isUser){
  // Fold render_user_markdown state into user-message keys so toggling the
  // setting invalidates cached plain-text renders (#3870).
  const p = isUser ? (window._renderUserMarkdown ? 'um' : 'u') : 'a';
  // Short content: use the full string as key (cheap Map lookup).
  // Long content: length + prefix + suffix is good enough — collisions on
  // 20-char prefix+suffix are vanishingly rare for chat messages.
  if(text.length <= 500) return p + ':' + text;
  return p + ':' + text.length + ':' + text.slice(0,20) + ':' + text.slice(-20);
}
function _getCachedRender(text, isUser){
  const key = _renderCacheKey(text, isUser);
  const hit = _renderCache.get(key);
  if(hit !== undefined) return hit;
  const rendered = isUser
    ? (window._renderUserMarkdown ? renderMd(text) : _renderUserFencedBlocks(text))
    : renderMd(_stripXmlToolCallsDisplay(String(text)));
  if(_renderCache.size > _renderCacheMax) _renderCache.clear();
  _renderCache.set(key, rendered);
  return rendered;
}
// ── Message-level media snapshot stamping ─────────────────────────────────
// /api/media serves a file's CURRENT bytes. Since ETag revalidation (#6922),
// an in-place overwrite (same filename) also rewrites every historical chat
// preview that referenced it — the old/new comparison is lost. At settle time
// the backend freezes the bytes of each local-file MEDIA: reference into a
// content-addressed store and stamps the message with
// `_media_snapshots: {path: digest}`. This helper rewrites the rendered HTML
// of ONE message to append `&snap=<digest>` to the matching /api/media URLs
// (and `data-snap` on lazy-preview placeholders), so old previews keep
// showing the file as it was when the message was emitted.
// Runs AFTER the text-keyed render cache: the cache stays pure-text, and each
// message stamps its own digests — two messages with identical text but
// different snapshots (exactly the old/new case) resolve independently.
function _stampMediaSnapshots(html, snaps){
  if(!html || !snaps || typeof snaps !== 'object') return html;
  let out = String(html);
  // Direct media URLs: rewrite each COMPLETE `path=` query value atomically.
  // Value-level parsing (decode the whole value, exact map lookup) instead of
  // substring split/join: when one path is a PREFIX of another
  // (/tmp/a.png vs /tmp/a.png.backup) the naive rewrite corrupts the longer
  // URL and drops its own digest. Matching is boundary-aware — the value runs
  // to the next `&` separator or an attribute quote/whitespace — so nothing
  // outside the value ever moves.
  out = out.replace(/api\/media[?&]path=([^&"'\s<>]+)/g, (match, encodedPath)=>{
    let decoded;
    try{ decoded = decodeURIComponent(encodedPath); }
    catch(e){ return match; }
    const digest = snaps[decoded];
    if(typeof digest === 'string' && /^[0-9a-f]{64}$/.test(digest)){
      return match + '&snap=' + digest;
    }
    return match;
  });
  // Lazy-preview placeholders: data-path="<html-escaped raw path>" — match the
  // complete attribute value, unescape HTML entities, then exact lookup (same
  // prefix-safety: a longer path's attribute can never be partially matched).
  const _unescapeHtml=(s)=>String(s||'').replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');
  out = out.replace(/data-path="([^"]*)"/g, (match, rawValue)=>{
    const decoded = _unescapeHtml(rawValue);
    const digest = snaps[decoded];
    if(typeof digest === 'string' && /^[0-9a-f]{64}$/.test(digest)){
      return match + ' data-snap="' + digest + '"';
    }
    return match;
  });
  return out;
}
function _currentMessageRenderWindowSize(){
  return Math.max(
    MESSAGE_RENDER_WINDOW_DEFAULT,
    Number(_messageRenderWindowSize)||MESSAGE_RENDER_WINDOW_DEFAULT
  );
}
function _messageRenderableMessageCount(){
  return _getVisibleMessagesWithIdx().length;
}
function _messageHiddenBeforeCount(){
  return Math.max(0,_messageRenderableMessageCount()-_currentMessageRenderWindowSize());
}
function _isSessionEndlessScrollEnabled(){
  return window._sessionEndlessScrollEnabled===true;
}
function _wireMessageWindowLoadEarlierButton(){
  const indicator=$('loadOlderIndicator');
  if(!indicator) return;
  indicator.onclick=()=>{
    if(typeof _loadOlderMessages==='function') _loadOlderMessages();
  };
}
function _isSessionJumpButtonsEnabled(){
  return window._sessionJumpButtonsEnabled===true;
}
function _applySessionNavigationPrefs(){
  const container=$('messages');
  if(container) container.classList.toggle('session-nav-enabled',_isSessionJumpButtonsEnabled());
  _updateSessionStartJumpButton();
}
function _updateSessionStartJumpButton(){
  const btn=$('jumpToSessionStartBtn');
  const container=$('messages');
  if(!btn||!container) return;
  if(!_isSessionJumpButtonsEnabled()){
    btn.style.display='none';
    return;
  }
  const hasSession=!!(S&&S.session&&S.messages&&S.messages.length);
  const awayFromStart=container.scrollTop>Math.max(240,container.clientHeight*0.35);
  const hasScrollableHistory=container.scrollHeight>container.clientHeight+Math.max(240,container.clientHeight*0.35);
  const canRevealStart=hasScrollableHistory||_messageHiddenBeforeCount()>0||!!(typeof _messagesTruncated!=='undefined'&&_messagesTruncated);
  btn.style.display=(hasSession&&canRevealStart&&awayFromStart)?'flex':'none';
}
async function jumpToSessionStart(){
  const container=$('messages');
  if(!container||!S.session) return;
  _scrollPinned=false;
  _messageUserUnpinned=true;
  _programmaticScroll=true;_programmaticScrollSetAt=performance.now();
  try{
    // During active streaming, skip full message load — API response won't
    // include live messages from the current turn, and replacing S.messages
    // would lose user/assistant/tool messages.
    if(!(S.busy||S.activeStreamId)){
      if(typeof _ensureAllMessagesLoaded==='function') await _ensureAllMessagesLoaded();
    }
    _messageRenderWindowSize=Math.max(_currentMessageRenderWindowSize(),_messageRenderableMessageCount());
    container.scrollTop=0;
    _messageVirtualWindowKey='';
    // During streaming, skip renderMessages — it rebuilds the DOM but tool card
    // insertion is blocked by !S.busy, losing Activity until "done" fires.
    if(!(S.busy||S.activeStreamId)){
      renderMessages({ preserveScroll:true });
    }
    requestAnimationFrame(()=>{
      container.scrollTop=0;
      _updateSessionStartJumpButton();
      _deferClearProgrammaticScroll();
    });
  }catch(e){
    console.warn('jumpToSessionStart failed:',e);
    _programmaticScroll=false;
  }
}

function _userMessageDomId(rawIdx){
  return `msg-user-${rawIdx}`;
}

function _questionJumpButtonHtml(questionRawIdx, assistantRawIdx){
  if(typeof questionRawIdx!=='number'||questionRawIdx<0) return '';
  const label=t('jump_to_question')||'Response';
  const title=t('jump_to_question_label')||'Jump to the start of this response';
  const aIdx=(typeof assistantRawIdx==='number'&&assistantRawIdx>=0)?assistantRawIdx:-1;
  return `<button class="msg-question-jump-btn session-jump-btn session-jump-btn--inline" type="button" title="${esc(title)}" aria-label="${esc(title)}" onclick="jumpToTurnQuestion(${questionRawIdx},${aIdx})"><span aria-hidden="true">↑</span><span>${esc(label)}</span></button>`;
}

function _highlightQuestionRow(row){
  if(!row) return;
  row.classList.remove('msg-question-highlight');
  void row.offsetWidth;
  row.classList.add('msg-question-highlight');
  window.setTimeout(()=>row.classList.remove('msg-question-highlight'),1800);
}

async function jumpToTurnQuestion(questionRawIdx, assistantRawIdx){
  const container=$('messages');
  if(!container||typeof questionRawIdx!=='number'||questionRawIdx<0) return;
  const clampTargetScrollTop=(scrollTop)=>{
    const maxTop=Math.max(0,container.scrollHeight-container.clientHeight);
    const n=Number(scrollTop);
    return Math.max(0,Math.min(Number.isFinite(n)?n:container.scrollTop,maxTop));
  };
  const scrollToTarget=()=>{
    const hasAssistant=typeof assistantRawIdx==='number'&&assistantRawIdx>=0;
    if(hasAssistant){
      // A single assistant rawIdx can render multiple segment nodes — some hidden
      // (assistant-segment-worklog-source / assistant-segment-anchor are display:none).
      // scrollIntoView() on a hidden node silently no-ops, so only treat a VISIBLE
      // segment (getClientRects().length>0) as a successful target; otherwise fall
      // through to the question-row fallback rather than suppressing it. (#3934)
      const segs=container.querySelectorAll('[data-msg-idx="'+assistantRawIdx+'"]');
      for(const seg of segs){
        if(seg.getClientRects().length>0){
          seg.scrollIntoView({block:'start',behavior:'smooth'});
          return true;
        }
      }
    }
    const row=document.getElementById(_userMessageDomId(questionRawIdx));
    if(!row) return false;
    row.scrollIntoView({block:'center',behavior:'smooth'});
    _highlightQuestionRow(row);
    return true;
  };
  // Cancel load-time bottom settling before any visible or virtualized target
  // path can return. The jump owner keeps every native smooth-scroll frame out
  // of the manual-reader listener, then reconciles against the final geometry.
  _cancelBottomSettle();
  _beginMessageJumpScroll(container);
  if(scrollToTarget()) return;
  const visWithIdx=_getVisibleMessagesWithIdx();
  const visibleIdx=_messageVisibleIndexForRawIdx(questionRawIdx, visWithIdx);
  if(visibleIdx>=0){
    _programmaticScroll=true;_programmaticScrollSetAt=performance.now();
    const virtualTarget=clampTargetScrollTop(_messageVirtualScrollTopForVisibleIdx(visWithIdx, visibleIdx, container));
    container.scrollTop=virtualTarget;
    _messageVirtualWindowKey='';
    renderMessages({ preserveScroll:true });
    requestAnimationFrame(()=>{
      if(!scrollToTarget()&&_messageHiddenBeforeCount()>0){
        _messageRenderWindowSize=Math.max(_currentMessageRenderWindowSize(),_messageRenderableMessageCount());
        _messageVirtualWindowKey='';
        renderMessages({ preserveScroll:true });
        requestAnimationFrame(scrollToTarget);
      }
      _deferClearProgrammaticScroll();
    });
    return;
  }
  if(_messageHiddenBeforeCount()>0){
    _messageRenderWindowSize=Math.max(_currentMessageRenderWindowSize(),_messageRenderableMessageCount());
    _messageVirtualWindowKey='';
    renderMessages({ preserveScroll:true });
    requestAnimationFrame(scrollToTarget);
  }
}

const DASHBOARD_STATUS_TTL_MS=60000;
let _dashboardStatusCache=null;
let _dashboardStatusFetchedAt=0;
let _dashboardLastNonNeverMode='auto'; // Server-scoped dashboard config keeps this restore target session-global on purpose.
let _dashboardSettingsLoadSeq=0;
let _dashboardSettingsWriteSeq=0;

function _dashboardHostIsLoopback(host){
  // Canonical loopback classifier shared by the browser origin and the
  // resolved dashboard target. Normalizes brackets, case, zone ids, and a
  // terminal hostname dot; classifies IPv4 127/8, IPv6 ::1, IPv4-mapped IPv6
  // whose embedded IPv4 is 127/8, and localhost/.localhost names (RFC 6761).
  if(!host) return false;
  let h=String(host).replace(/^\[|\]$/g,'').toLowerCase();
  if(h.endsWith('.')) h=h.slice(0,-1);
  if(h==='localhost'||h.endsWith('.localhost')) return true;
  const ipv4=/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(h);
  if(ipv4){
    const octets=ipv4.slice(1).map(Number);
    return octets.every(o=>o>=0&&o<=255)&&octets[0]===127;
  }
  if(h.includes(':')){
    const zone=h.indexOf('%');
    if(zone!==-1) h=h.slice(0,zone);
    if(h==='::1'||h==='0:0:0:0:0:0:0:1') return true;
    const mapped=/^(?:::ffff:|0:0:0:0:0:ffff:)(.+)$/.exec(h);
    if(mapped){
      const tail=mapped[1];
      const dotted=/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(tail);
      if(dotted){
        const octets=dotted.slice(1).map(Number);
        return octets.every(o=>o>=0&&o<=255)&&octets[0]===127;
      }
      const hex=/^([0-9a-f]{1,4}):([0-9a-f]{1,4})$/.exec(tail);
      if(hex) return (parseInt(hex[1],16)>>>8)===127;
      return false;
    }
    return false;
  }
  return false;
}

function _dashboardIsBrowserLoopback(){
  return _dashboardHostIsLoopback(window.location.hostname||'');
}

function _dashboardUrlIsLoopback(url){
  if(!url) return false;
  try{
    return _dashboardHostIsLoopback(new URL(url).hostname);
  }catch(_){return false;}
}

function _normalizeDashboardEnabledMode(mode){
  return mode==='auto'||mode==='always'||mode==='never'?mode:'auto';
}

function _setDashboardModeForChip(mode){
  mode=_normalizeDashboardEnabledMode(mode);
  if(mode==='auto'||mode==='always') _dashboardLastNonNeverMode=mode;
}

function _getDashboardChipRestoreMode(){
  return _dashboardLastNonNeverMode||'auto';
}

function _dashboardBrowserUrl(status){
  if(!status||!status.running) return '';
  if(status.browser_url||status.url){
    try{return new URL(status.browser_url||status.url).toString().replace(/\/$/,'');}
    catch(_){}
  }
  if(!status.port) return '';
  let source;
  try{source=new URL('http://127.0.0.1:'+status.port);}
  catch(_){return '';}
  const browserHost=window.location.hostname||source.hostname;
  const displayHost=browserHost.includes(':')&&!browserHost.startsWith('[')?'['+browserHost+']':browserHost;
  return source.protocol+'//'+displayHost+':'+status.port;
}
function _stripInlineEventHandlers(node){
  if(!node)return;
  const strip=el=>{
    Array.from(el.attributes||[]).forEach(attr=>{
      if(attr.name&&attr.name.toLowerCase().startsWith('on'))el.removeAttribute(attr.name);
    });
    if('onclick' in el)el.onclick=null;
    Array.from(el.children||[]).forEach(strip);
  };
  strip(node);
}
function _syncNavActionMirrors(){
  const rail=document.querySelector('.rail');
  const sidebar=document.querySelector('.sidebar-nav');
  if(!rail||!sidebar)return;
  const anchor=sidebar.querySelector('.dashboard-link,[data-dashboard-link]')||sidebar.querySelector('[data-panel="logs"]');
  const sources=Array.from(rail.querySelectorAll('.nav-tab:not([data-panel]):not([data-dashboard-link])')).filter(source=>source.id);
  const mirrors=Array.from(sidebar.querySelectorAll('[data-nav-action-mirror]'));
  const sourceIds=new Set(sources.map(source=>source.id));
  mirrors.forEach(mirror=>{
    if(!sourceIds.has(mirror.getAttribute('data-nav-action-mirror')))mirror.remove();
  });
  let next=anchor||null;
  sources.slice().reverse().forEach(source=>{
    const sourceVisible=(()=>{
      if(source.hidden||source.getAttribute('aria-hidden')==='true')return false;
      if(source.classList.contains('nav-tab-hidden'))return false;
      if(source.style&&(source.style.display==='none'||source.style.visibility==='hidden'))return false;
      if(typeof window!=='undefined'&&typeof window.getComputedStyle==='function'){
        const computed=window.getComputedStyle(source);
        if(computed&&(computed.display==='none'||computed.visibility==='hidden'))return false;
      }
      return true;
    })();
    let mirror=mirrors.find(el=>el.getAttribute('data-nav-action-mirror')===source.id);
    if(!mirror){
      mirror=source.cloneNode(true);
      _stripInlineEventHandlers(mirror);
      mirror.id=source.id+'Mobile';
      mirror.classList.remove('rail-btn');
      mirror.classList.add('has-tooltip--bottom');
      mirror.setAttribute('data-nav-action-mirror',source.id);
      mirror.addEventListener('click',e=>{
        e.preventDefault();
        if(mirror._navActionSource)mirror._navActionSource.click();
        if(typeof closeMobileSidebar==='function')closeMobileSidebar();
      });
    }else{
      mirror.innerHTML=source.innerHTML;
      _stripInlineEventHandlers(mirror);
    }
    if(mirror.parentNode!==sidebar||mirror.nextElementSibling!==next)sidebar.insertBefore(mirror,next);
    next=mirror;
    mirror._navActionSource=source;
    mirror.classList.toggle('nav-action-visible',sourceVisible);
    const label=source.getAttribute('data-tooltip')||source.getAttribute('aria-label')||'';
    if(label)mirror.setAttribute('data-label',label);
  });
}
function _initNavActionMirrors(){
  _syncNavActionMirrors();
  const rail=document.querySelector('.rail');
  if(rail&&window.MutationObserver)new MutationObserver(_syncNavActionMirrors).observe(rail,{
    childList:true,
    subtree:true,
    attributes:true,
    attributeFilter:['class','style','hidden','aria-hidden','data-tooltip','aria-label'],
  });
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',_initNavActionMirrors,{once:true});
else _initNavActionMirrors();
function _applyDashboardStatus(status){
  const running=!!(status&&status.running);
  const url=running?_dashboardBrowserUrl(status):'';
  const warning=running&&!_dashboardIsBrowserLoopback()&&_dashboardUrlIsLoopback(url)?t('dashboard_loopback_warning'):'';
  document.querySelectorAll('[data-dashboard-link]').forEach(btn=>{
    btn.classList.toggle('dashboard-link-visible',running);
    btn.classList.toggle('nav-action-visible',running);
    btn.style.display=running?'':'none';
    btn.dataset.dashboardUrl=url;
    const tipText=warning||t('tab_dashboard');
    if(btn.hasAttribute('data-tooltip')){
      // Sync the custom CSS tooltip and explicitly clear the native title so
      // the slow ~1.5s native browser tooltip does not co-fire alongside the
      // fast custom tooltip (#1775).
      btn.setAttribute('data-tooltip',tipText);
      if(btn.hasAttribute('title')) btn.removeAttribute('title');
    } else {
      btn.title=tipText;
    }
    btn.setAttribute('aria-label',tipText);
  });
}
async function refreshDashboardStatus(force=false){
  const now=Date.now();
  // Skip the interval-driven poll while the tab is hidden: the 60s interval
  // equals the cache TTL, so every background tick was a real /api/dashboard/status
  // fetch that never hit the cache — a needless wakeup on a tab nobody is
  // looking at (battery/CPU, #2476). Forced calls (settings save, init, the
  // visibilitychange catch-up) still run. A visible tab keeps its live status.
  if(!force&&typeof document!=='undefined'&&document.hidden){
    return _dashboardStatusCache;
  }
  if(!force&&_dashboardStatusCache&&(now-_dashboardStatusFetchedAt)<DASHBOARD_STATUS_TTL_MS){
    _applyDashboardStatus(_dashboardStatusCache);
    return _dashboardStatusCache;
  }
  try{
    const status=await api('/api/dashboard/status',{timeoutToast:false});
    _dashboardStatusCache=status||{running:false};
  }catch(_){
    _dashboardStatusCache={running:false};
  }
  _dashboardStatusFetchedAt=Date.now();
  _applyDashboardStatus(_dashboardStatusCache);
  return _dashboardStatusCache;
}
async function loadDashboardSettings(){
  const modeEl=$('settingsDashboardMode');
  const urlEl=$('settingsDashboardUrl');
  if(!modeEl&&!urlEl) return;
  const loadSeq=++_dashboardSettingsLoadSeq;
  const writeSeq=_dashboardSettingsWriteSeq;
  try{
    const cfg=await api('/api/dashboard/config');
    if(loadSeq!==_dashboardSettingsLoadSeq||writeSeq!==_dashboardSettingsWriteSeq) return;
    const mode=_normalizeDashboardEnabledMode(cfg&&cfg.enabled);
    if(modeEl) modeEl.value=mode;
    _setDashboardModeForChip(mode);
    if(urlEl) urlEl.value=cfg.url||'';
    if(typeof _renderTabVisibilityChips==='function') _renderTabVisibilityChips();
  }catch(_){/* leave defaults visible */}
}
async function saveDashboardSettings(opts){
  opts=opts||{};
  const modeEl=$('settingsDashboardMode');
  const urlEl=$('settingsDashboardUrl');
  const statusEl=$('settingsDashboardStatus');
  const payload={enabled:(modeEl&&modeEl.value)||'auto',url:(urlEl&&urlEl.value||'').trim()};
  _dashboardSettingsWriteSeq+=1;
  try{
    const saved=await api('/api/dashboard/config',{method:'POST',body:JSON.stringify(payload)});
    const mode=_normalizeDashboardEnabledMode(saved&&saved.enabled);
    if(modeEl) modeEl.value=mode;
    _setDashboardModeForChip(mode);
    if(urlEl) urlEl.value=saved.url||'';
    if(statusEl) statusEl.textContent='Dashboard link settings saved.';
    await refreshDashboardStatus(true);
    if(typeof _renderTabVisibilityChips==='function') _renderTabVisibilityChips();
  }catch(err){
    if(statusEl) statusEl.textContent='Dashboard link settings failed to save.';
    else if(typeof showToast==='function') showToast('Dashboard link settings failed to save.');
    try{await loadDashboardSettings();}catch(_){}
    if(opts.raiseOnError) throw err;
  }
}
function openAgyDashboard(event){
  if(event){event.preventDefault();event.stopPropagation();}
  const btn=event&&event.currentTarget?event.currentTarget:document.querySelector('[data-dashboard-link]');
  const url=(btn&&btn.dataset&&btn.dataset.dashboardUrl)||_dashboardBrowserUrl(_dashboardStatusCache);
  if(!url) return false;
  window.open(url,'_blank','noopener,noreferrer');
  return false;
}
function _initDashboardLinkProbe(){
  loadDashboardSettings();
  refreshDashboardStatus(true);
  setInterval(refreshDashboardStatus,DASHBOARD_STATUS_TTL_MS);
  // Catch up once when the tab becomes visible again, since the interval poll
  // was skipped while hidden and its cache is now stale.
  if(typeof document!=='undefined'&&typeof document.addEventListener==='function'){
    document.addEventListener('visibilitychange',()=>{
      if(!document.hidden) refreshDashboardStatus(true);
    });
  }
}
if(document.readyState==='complete'){
  _initDashboardLinkProbe();
}else{
  document.addEventListener('DOMContentLoaded',_initDashboardLinkProbe,{once:true});
}

// Image lightbox, Mermaid viewer, media players → ui-media-viewer.js

document.addEventListener('click', e => {
  if(!e.target || !e.target.closest) return;
  const sessionLink=e.target.closest('a.session-link[href]');
  if(sessionLink){
    const href=sessionLink.getAttribute('href')||'';
    const m=href.match(/(?:^|\/)session\/([^?#]+)/i);
    if(m&&typeof loadSession==='function'){
      e.preventDefault();
      try{loadSession(decodeURIComponent(m[1]));}catch(_){loadSession(m[1]);}
    }
    return;
  }
  const workspaceLink=e.target.closest('a[href^="#workspace="]');
  if(workspaceLink){
    e.preventDefault();
    const href=workspaceLink.getAttribute('href')||'';
    try{
      const rel=decodeURIComponent(href.slice('#workspace='.length));
      if(rel && typeof openArtifactPath==='function') openArtifactPath(rel);
    }catch(_){}
    return;
  }
  const vaultLink=e.target.closest('a[href^="#vault="]');
  if(vaultLink){
    e.preventDefault();
    const href=vaultLink.getAttribute('href')||'';
    try{
      const rel=decodeURIComponent(href.slice('#vault='.length));
      if(typeof switchPanel==='function') switchPanel('vault', {fromRailClick:true});
      if(rel && typeof loadVaultNote==='function') loadVaultNote(rel, true);
    }catch(_){}
    return;
  }
  const vaultInsertLink=e.target.closest('a[href^="#vault-insert="]');
  if(vaultInsertLink){
    e.preventDefault();
    const href=vaultInsertLink.getAttribute('href')||'';
    try{
      const payload=decodeURIComponent(href.slice('#vault-insert='.length));
      if(typeof insertVaultNoteIntoComposer==='function') insertVaultNoteIntoComposer(payload);
    }catch(_){}
    return;
  }
  const deepmodeLink=e.target.closest('a[href^="#deepmode="]');
  if(deepmodeLink){
    e.preventDefault();
    const href=deepmodeLink.getAttribute('href')||'';
    const action=href.slice('#deepmode='.length).trim();
    if(typeof cmdDeepMode==='function'){
      cmdDeepMode(action);
    }
    return;
  }
  const vaultCreateLink=e.target.closest('a[href^="#vault-create="]');
  if(vaultCreateLink){
    e.preventDefault();
    const href=vaultCreateLink.getAttribute('href')||'';
    try{
      const rawPayload=decodeURIComponent(href.slice('#vault-create='.length));
      let payload={};
      try{ payload=JSON.parse(rawPayload); }catch(_){ payload={title: rawPayload}; }
      if(typeof switchPanel==='function') switchPanel('vault', {fromRailClick:true});
      if(typeof promptCreateVaultNote==='function'){
        promptCreateVaultNote(payload.category || 'notes', payload.title || '', payload.space || '');
      }
    }catch(_){}
    return;
  }
  const vaultWeaveLink=e.target.closest('a[href^="#vault-weave="]');
  if(vaultWeaveLink){
    e.preventDefault();
    const href=vaultWeaveLink.getAttribute('href')||'';
    try{
      const rawPayload=decodeURIComponent(href.slice('#vault-weave='.length));
      let payload={};
      try{ payload=JSON.parse(rawPayload); }catch(_){ payload={path: rawPayload}; }
      if(payload.path){
        if(typeof showToast==='function') showToast(`Auto-weaving wikilinks for ${payload.path}...`);
        apiFetch('/api/vault/weave', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            path: payload.path,
            space: payload.space || ''
          })
        }).then(res => {
          if(res.ok){
            if(typeof showToast==='function') showToast(`Wove ${res.links_added} new wikilinks into ${payload.path}!`);
            if(typeof loadVaultNote==='function') loadVaultNote(payload.path, true);
          } else {
            if(typeof showToast==='function') showToast(`Weave failed: ${res.error || 'Unknown error'}`);
          }
        }).catch(err => {
          if(typeof showToast==='function') showToast(`Weave error: ${err.message}`);
        });
      }
    }catch(_){}
    return;
  }
  const vaultSearchLink=e.target.closest('a[href^="#vault-search="]');
  if(vaultSearchLink){
    e.preventDefault();
    const href=vaultSearchLink.getAttribute('href')||'';
    try{
      const query=decodeURIComponent(href.slice('#vault-search='.length));
      if(typeof cmdRecall==='function'){
        cmdRecall(query);
      } else if(typeof showToast==='function'){
        showToast(`Searching for: ${query}`);
      }
    }catch(_){}
    return;
  }
});

// Model picker, quota indicator, reasoning chip, toolsets → ui-model-picker.js

// Scroll pinning, anchor management, auto-scroll → ui-scroll.js

/* ── Pull-to-refresh for PWA standalone (Android) ── */
(function(){
  if(typeof document==='undefined') return;
  const isStandalone=window.navigator?.standalone||matchMedia('(display-mode:standalone),(display-mode:fullscreen)').matches;
  if(!isStandalone) return;
  const el=document.getElementById('messages');
  if(!el) return;
  let _ptrState=0; // 0=idle, 1=pulling, 2=ready
  let _ptrStartY=0;
  let _ptrCurrentY=0;
  const THRESHOLD=80;
  let _indicator=null;
  function _ptrCreateIndicator(){
    if(_indicator) return;
    _indicator=document.createElement('div');
    _indicator.className='pull-to-refresh-indicator';
    _indicator.innerHTML='<span class="ptr-icon">↓</span> <span class="ptr-text">Pull to refresh</span>';
    el.parentNode.insertBefore(_indicator,el);
  }
  function _ptrUpdate(progress){
    _ptrCreateIndicator();
    const pulling=progress<1;
    _indicator.classList.toggle('active',progress>0);
    const icon=_indicator.querySelector('.ptr-icon');
    const text=_indicator.querySelector('.ptr-text');
    if(icon) icon.classList.toggle('ready',!pulling);
    if(text) text.textContent=pulling?'Pull to refresh':'Release to refresh';
  }
  function _ptrReset(){
    _ptrState=0;
    _ptrStartY=0;
    _ptrCurrentY=0;
    if(_indicator) _indicator.classList.remove('active');
  }
  el.addEventListener('touchstart',function(e){
    if(el.scrollTop>0||_ptrState!==0) return;
    _ptrStartY=e.touches[0].clientY;
    _ptrState=1;
  },{passive:true});
  el.addEventListener('touchmove',function(e){
    if(_ptrState!==1) return;
    _ptrCurrentY=e.touches[0].clientY;
    const pull=_ptrCurrentY-_ptrStartY;
    if(pull<0){ _ptrReset(); return; }
    /* If not at the top, smooth-scroll to top first.
       Next pull gesture will trigger the refresh. */
    if(el.scrollTop>0){
      el.scrollTo({top:0,behavior:'smooth'});
      _ptrReset();
      return;
    }
    const progress=Math.min(pull/THRESHOLD,1);
    _ptrUpdate(progress);
    _ptrState=progress>=1?2:1;
    if(progress>0.3) e.preventDefault();
  },{passive:false});
  el.addEventListener('touchend',function(){
    if(_ptrState===2){
      if(typeof window.refreshSessionList==='function'){
        Promise.resolve(window.refreshSessionList('pull', {force:true, refreshActive:true})).catch(()=>{}).finally(_ptrReset);
      }else{
        window.location.reload();
      }
      return;
    }
    _ptrReset();
  },{passive:true});
  el.addEventListener('touchcancel',_ptrReset,{passive:true});
})();
(function(){
  const el=document.getElementById('messages');
  if(!el) return;
  el.addEventListener('pointerdown',(e)=>{
    if(e.target===el&&e.offsetX>=el.clientWidth){
      if(typeof _cancelBottomSettle==='function') _cancelBottomSettle();
      _scrollbarDragActive=true;
      if(typeof _messageScrollInputGeneration==='number') _messageScrollInputGeneration++;
    }
  },{passive:true});
  window.addEventListener('pointerup',()=>{
    if(!_scrollbarDragActive) return;
    _scrollbarDragActive=false;
    _scheduleMessageVirtualizedRender(true);
  },{passive:true});
  window.addEventListener('pointercancel',()=>{
    if(!_scrollbarDragActive) return;
    _scrollbarDragActive=false;
    _scheduleMessageVirtualizedRender(true);
  },{passive:true});
  window.addEventListener('blur',()=>{ _scrollbarDragActive=false; },{passive:true});
  document.addEventListener('visibilitychange',()=>{
    if(document.visibilityState==='hidden') _scrollbarDragActive=false;
  },{passive:true});
  // #4970 review (greptile P1): record keyboard-driven message-pane scrolling as
  // user intent. PageUp/PageDown, Arrow keys, Space/Shift+Space, Home/End scroll
  // the pane and fire a native scroll event with no wheel/touch intent — without
  // this stamp a keyboard scroll-up inside the post-render artifact window is
  // swallowed and live-follow snaps the reader back to the bottom. Only count it
  // when the scroll container (or a descendant) is the active/scrolling target,
  // not when typing in the composer or activating an in-transcript control.
  const _MESSAGE_SCROLL_KEYS=new Set([
    'PageUp','PageDown','ArrowUp','ArrowDown','Home','End','Spacebar',' ',
  ]);
  const _isMessageInteractiveKeyTarget=(node)=>{
    if(!node||!el.contains(node)) return false;
    if(node.tagName==='INPUT'||node.tagName==='TEXTAREA'||node.isContentEditable) return true;
    return !!(node.closest&&node.closest('button,a[href],select,summary,[role="button"],[role="tab"],[role="menuitem"],[contenteditable="true"]'));
  };
  document.addEventListener('keydown',(e)=>{
    if(!e||!_MESSAGE_SCROLL_KEYS.has(e.key)) return;
    const a=document.activeElement;
    const t=e.target;
    // Ignore keys aimed at editable fields (composer, inputs, contenteditable).
    if(a&&(a.tagName==='INPUT'||a.tagName==='TEXTAREA'||a.isContentEditable)) return;
    // Space/Spacebar activates focused transcript controls (buttons, role=button,
    // links, tabs) rather than scrolling. The listener is capture-phase, so target
    // handlers have not yet preventDefault()/stopPropagation()'d; inspect the
    // active/target control path directly.
    if((e.key===' '||e.key==='Spacebar')&&(_isMessageInteractiveKeyTarget(t)||_isMessageInteractiveKeyTarget(a))) return;
    // Count only when the message pane itself is the scroll target: it is focused,
    // contains the focus, or the pointer is over it (keyboard scroll w/o focus).
    if(a===el||el.contains(a)||el.matches(':hover')){
      if(typeof _cancelBottomSettle==='function') _cancelBottomSettle();
      const now=performance.now();
      if(typeof _messageScrollInputGeneration==='number') _messageScrollInputGeneration++;
      _lastMessageKeyScrollIntentMs=now;
      const bottomDistance=el.scrollHeight-el.scrollTop-el.clientHeight;
      if(bottomDistance>120) _lastMessageScrollIntentMs=now;
    }
  },{capture:true,passive:true});
  let _scrollRaf=0;
  el.addEventListener('scroll',()=>{
    _scheduleMessageVirtualizedRender();
    if(_messageJumpScrollOwner){
      _scheduleMessageJumpScrollReconcile(_messageJumpScrollOwner.generation);
      return;
    }
    if(_freshProgrammaticScrollActive()) return;
    _markMessageVirtualScrollActive();
    cancelAnimationFrame(_scrollRaf);
    _scrollRaf=requestAnimationFrame(()=>{
      const top=el.scrollTop;
      const bottomDistance=el.scrollHeight-top-el.clientHeight;
      const nearBottom=bottomDistance<250;
      // #4702: iOS Safari (esp. portrait) resolves its dynamic toolbar height
      // AFTER first paint. When the toolbar collapses the scroller GROWS
      // (clientHeight increases), which fires a scroll event with a DECREASED
      // scrollTop even though the user never scrolled. Without this guard that
      // reflow is misread as an upward scroll and falsely unpins a freshly-opened
      // session, stranding portrait readers at the top (sibling: #4701). On
      // desktop/landscape the scroller height is stable, so `grew` is always
      // false and behavior is byte-identical.
      const grew=_lastMessageClientHeight!==null&&el.clientHeight>_lastMessageClientHeight+1;
      _lastMessageClientHeight=el.clientHeight;
      const movedUp=!grew&&_lastScrollTop!==null&&top<_lastScrollTop-2;
      const movedDown=_lastScrollTop!==null&&top>_lastScrollTop+2;
      // Suppress the post-render scroll artifact: right after renderMessages()
      // rebuilds #msgInner, the browser can emit a non-user upward scroll event.
      // The typeof guards keep this branch inert in unit harnesses that inject
      // the listener body without these helpers (and short-circuit before any
      // call), while production evaluates the real intent/recency helpers.
      // #4970: also require no recent low-delta message-pane wheel intent, so a
      // gentle trackpad scroll-up (deltaY>-30) right after a render still unpins
      // instead of being swallowed for the artifact window.
      // #4970 review: and never suppress while a scrollbar drag is active — a
      // manual scrollbar-drag upward scroll inside the window is real intent.
      // typeof guard keeps the #4295 node harness (no _scrollbarDragActive
      // injected) inert via short-circuit.
      // #4970 review (greptile P1): likewise skip suppression when the reader
      // recently scrolled the pane with the keyboard — a keyboard scroll-up is
      // real intent that produces a native scroll event with no wheel/touch.
      if(movedUp
        && typeof _recentMessageRenderArtifactWindow==='function'
        && typeof _recentMessageTouchScrollIntent==='function'
        && typeof _recentNonMessageScrollIntent==='function'
        && typeof _recentMessageWheelIntent==='function'
        && typeof _recentMessageKeyScrollIntent==='function'
        && (typeof _scrollbarDragActive==='undefined' || !_scrollbarDragActive)
        && _recentMessageRenderArtifactWindow(1400)
        && !_recentMessageTouchScrollIntent()
        && !_recentNonMessageScrollIntent()
        && !_recentMessageWheelIntent()
        && !_recentMessageKeyScrollIntent()){
        _lastScrollTop=top;
        return;
      }
      _lastScrollTop=top;
      if(movedUp){
        _cancelBottomSettle();
        _nearBottomCount=0;
        _scrollPinned=false;
        _messageUserUnpinned=true;
      }else if(movedDown&&nearBottom){
        _nearBottomCount=_nearBottomCount+1;
        if(_nearBottomCount>=2){
          // Only re-pin when the reader has genuinely reached the true bottom
          // tail (<=80px). nearBottom spans a ~250px band, so proximity alone
          // must NOT clear the sticky unpin flag (#4295) — a reader scanning the
          // last lines mid-stream would otherwise get yanked back to the bottom.
          if(!_messageUserUnpinned||bottomDistance<=80){
            _messageUserUnpinned=false;
            _scrollPinned=true;
          }
          _nearBottomCount=0;
        }
      }else if(!_messageUserUnpinned){
        if(nearBottom){
          _nearBottomCount=_nearBottomCount+1;
          if(_nearBottomCount>=2){_scrollPinned=true;_nearBottomCount=0;}
        }else if(!movedUp && window._autoScrollFollow && _scrollPinned){
          // Content-grew-beneath-a-pinned-viewport case (NOT a user scroll-away).
          // During streaming on a tall transcript (esp. mobile, where chunks land
          // fast), new content increases scrollHeight under a stationary viewport,
          // so bottomDistance crosses the nearBottom threshold even though the
          // reader never scrolled (top did NOT move up, _messageUserUnpinned is
          // false). Previously this fell through to `_scrollPinned=false`, killing
          // auto-follow mid-stream: the follow writer and this listener then fought
          // frame-by-frame, the viewport stalled while content kept growing, and it
          // was progressively stranded mid-transcript (the "jump back" report).
          // Keep the pin and re-snap to the true bottom instead of unpinning.
          _nearBottomCount=0;
          if(typeof _setMessageScrollToBottom==='function') _setMessageScrollToBottom();
        }else{
          _nearBottomCount=0;
          _scrollPinned=false;
        }
      }else if(!nearBottom){
        _nearBottomCount=0;
        _scrollPinned=false;
      }
      if(nearBottom) _clearNewMessageScrollCue();
      const showBottomButton=!_scrollPinned && el.scrollHeight-top-el.clientHeight>80;
      _syncScrollToBottomCue(showBottomButton,{newMessage:_newMessageCueVisible});
      if(typeof _updateSessionStartJumpButton==='function') _updateSessionStartJumpButton();
      // Prefetch older messages before the reader hits the hard top. Prepending
      // then preserving scrollTop is seamless only if there is runway left for
      // the user's continued upward wheel/touch movement.
      const olderPrefetchPx=Math.max(600,el.clientHeight*1.5);
      if(_isSessionEndlessScrollEnabled()&&el.scrollTop<olderPrefetchPx && typeof _messagesTruncated!=='undefined' && _messagesTruncated && typeof _loadOlderMessages==='function'){
        if(_recentMessageTouchScrollIntent()) _scheduleDeferredOlderMessagesLoad();
        else _loadOlderMessages();
      }
    });
  });
})();
function _fmtTokens(n){if(!n||n<0)return'0';if(n>=1e6)return(n/1e6).toFixed(1)+'M';if(n>=1e3)return(n/1e3).toFixed(1)+'k';return String(n);}
function _formatTurnDuration(seconds){
  const n=Number(seconds);
  if(!Number.isFinite(n)||n<0)return'';
  const total=Math.max(0,Math.round(n));
  if(total<60)return`${total}s`;
  const h=Math.floor(total/3600);
  const m=Math.floor((total%3600)/60);
  const s=total%60;
  if(h)return`${h}h ${m}m`;
  return`${m}m ${s}s`;
}
function _formatFirstToken(ms){
  const n=Number(ms);
  if(!Number.isFinite(n)||n<0)return'';
  if(n<1000)return`${Math.round(n)}ms`;
  return`${(n/1000).toFixed(2)}s`;
}
function _formatActiveElapsedTimer(seconds){
  const n=Number(seconds);
  if(!Number.isFinite(n)||n<0)return'';
  const total=Math.max(0,Math.floor(n));
  const m=Math.floor(total/60);
  const s=total%60;
  return`${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
}
function _processedElapsedLabel(seconds){
  const text=_formatTurnDuration(seconds);
  return text?t('processed_elapsed',text):'';
}
const _COMPRESSION_ELAPSED_MAX_SECONDS=5*60;
let _compressionElapsedTimer=null;
function _compressionElapsedStartedAt(state){const n=Number(state&&state.startedAt);return Number.isFinite(n)&&n>0?n:null;}
function _compressionElapsedLabel(state){
  const started=_compressionElapsedStartedAt(state);
  if(!started)return'';
  const elapsed=Math.max(0,(Date.now()/1000)-started);
  if(elapsed>=_COMPRESSION_ELAPSED_MAX_SECONDS)return '5+ min';
  return _formatActiveElapsedTimer(elapsed);
}
function _compressionElapsedExpired(state){const started=_compressionElapsedStartedAt(state);return !!(started&&((Date.now()/1000)-started)>=_COMPRESSION_ELAPSED_MAX_SECONDS);}
function _compressionLiveCardNode(){return document.querySelector('[data-live-compression-card="1"][data-compression-started-at]');}
function _compressionLiveCardState(){
  const node=_compressionLiveCardNode();
  const started=Number(node&&node.getAttribute('data-compression-started-at'));
  if(!node||!S.session||!Number.isFinite(started)||started<=0)return null;
  return {sessionId:S.session.session_id,phase:'running',automatic:true,message:node.getAttribute('data-compression-message')||'Auto-compressing context...',startedAt:started};
}
function _updateCompressionElapsedCards(state){
  if(!state)return false;
  return false;
}
function _updateCompressionElapsedTimer(){
  const state=_compressionStateForCurrentSession()||_compressionLiveCardState();
  if(state&&state.automatic&&state.phase==='running'){
    _updateCompressionElapsedCards(state);
    if(_compressionElapsedExpired(state)) _clearCompressionElapsedTimer();
  }else _clearCompressionElapsedTimer();
}
function _startCompressionElapsedTimer(){if(!_compressionElapsedTimer)_compressionElapsedTimer=setInterval(_updateCompressionElapsedTimer,1000);}
function _clearCompressionElapsedTimer(){if(_compressionElapsedTimer){clearInterval(_compressionElapsedTimer);_compressionElapsedTimer=null;}}
let _activityElapsedTimer=null;
let _activityElapsedTimerGroup=null;
function _activityNowSeconds(){return Date.now()/1000;}
function _isActivityTimerGroup(group){
  return !!(group&&group.getAttribute('data-run-activity-group')==='1');
}
function _activityElapsedStartedAt(group){
  if(!group)return null;
  const raw=(group.dataset&&group.dataset.turnStartedAt!==undefined&&group.dataset.turnStartedAt!=='')
    ?group.dataset.turnStartedAt
    :(S.session&&S.session.pending_started_at);
  const started=Number(raw);
  return Number.isFinite(started)&&started>0?started:null;
}
function _activityElapsedLabel(group){
  const started=_activityElapsedStartedAt(group);
  if(!started)return'';
  return _formatActiveElapsedTimer(_activityNowSeconds()-started);
}
function _activityProcessedElapsedLabel(group){
  const started=_activityElapsedStartedAt(group);
  if(!started)return'';
  return _processedElapsedLabel(_activityNowSeconds()-started);
}
function _activitySettledProcessedLabel(group){
  let durationText=_formatTurnDuration(group&&group.dataset&&group.dataset.turnDuration);
  if(!durationText&&group){
    const durationEl=group.querySelector&&group.querySelector('.tool-call-group-duration');
    const legacy=String(durationEl&&durationEl.textContent||'').replace(/^\s*Done in\s+/i,'').trim();
    if(legacy) durationText=legacy;
  }
  return durationText?t('processed_elapsed',durationText):'';
}
function _activityMarkObserved(group, ts){
  if(!group||group.getAttribute('data-live-tool-call-group')!=='1')return;
  const stamp=Number(ts||_activityNowSeconds());
  if(Number.isFinite(stamp)&&stamp>0) group.setAttribute('data-last-activity-at',String(stamp));
}
function _activityLastObservedAge(group){
  const stamp=Number(group&&group.getAttribute('data-last-activity-at'));
  if(!Number.isFinite(stamp)||stamp<=0)return null;
  return Math.max(0,_activityNowSeconds()-stamp);
}
function _activityClockLabel(ts){
  const stamp=Number(ts||_activityNowSeconds());
  if(!Number.isFinite(stamp)||stamp<=0)return'';
  try{return new Date(stamp*1000).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'});}catch(_){return'';}
}
// Full date+time label for the worklog event-time tooltip (title attr). Guards the
// same valid-Date range as _timestampSeconds so a bad epoch never yields "Invalid
// Date" in the tooltip. (#5739)
function _activityFullClockLabel(ts){
  const stamp=Number(ts);
  if(!Number.isFinite(stamp)||stamp<=0||stamp>8.64e12)return'';
  try{
    const d=new Date(stamp*1000);
    if(isNaN(d.getTime()))return'';
    return d.toLocaleString([], {year:'numeric',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
  }catch(_){return'';}
}
function _timestampSeconds(value){
  if(value===undefined||value===null||value==='') return null;
  if(value instanceof Date){
    const stamp=value.getTime()/1000;
    return (Number.isFinite(stamp)&&stamp>0&&Math.abs(stamp)<=8.64e12)?stamp:null;
  }
  const numeric=Number(value);
  if(Number.isFinite(numeric)&&numeric>0){
    const stamp=numeric>1e12?numeric/1000:numeric;
    // Reject epochs outside JavaScript's valid Date range (±8.64e15 ms = ±8.64e12 s);
    // otherwise new Date(stamp*1000) yields "Invalid Date" and renders literally
    // (e.g. a garbage numeric timestamp like 1e20 passes finite/>0). (#5739 gate.)
    return (Number.isFinite(stamp)&&stamp>0&&stamp<=8.64e12)?stamp:null;
  }
  if(typeof value==='string'){
    const text=value.trim();
    if(!text||/^[+-]?(?:\d+\.?\d*|\.\d+)$/.test(text)) return null;
    const parsed=Date.parse(text);
    if(Number.isFinite(parsed)&&parsed>0){
      const stamp=parsed/1000;
      return stamp<=8.64e12?stamp:null;
    }
  }
  return null;
}
function _firstValidTimestampSeconds(...values){
  for(const value of values){
    const stamp=_timestampSeconds(value);
    if(stamp) return stamp;
  }
  return null;
}
function _transparentEventTimestampSeconds(row, opts){
  opts=opts||{};
  for(const key of ['ts','timestamp','created_at']){
    const stamp=_timestampSeconds(opts[key]);
    if(stamp) return stamp;
  }
  const toolCall=opts.toolCall||row&&row._tcData||null;
  if(toolCall&&typeof toolCall==='object'){
    for(const key of ['ts','timestamp','created_at','started_at','completed_at']){
      const stamp=_timestampSeconds(toolCall[key]);
      if(stamp) return stamp;
    }
  }
  if(row&&typeof row.getAttribute==='function'){
    for(const key of ['data-event-at','data-activity-at']){
      const stamp=_timestampSeconds(row.getAttribute(key));
      if(stamp) return stamp;
    }
  }
  if(opts.live===true) return _activityNowSeconds();
  return null;
}
function _syncTransparentEventTimestamp(row, header, opts){
  if(!row||!header) return null;
  opts=opts||{};
  const showEventTimestamp=!(typeof window!=='undefined'&&window._transparentEventTimestamps===false);
  const live=opts.live===true||row.getAttribute&&(
    row.getAttribute('data-live-tid')==='1'||
    row.getAttribute('data-live-thinking')==='1'||
    row.getAttribute('data-live-assistant')==='1'||
    row.getAttribute('data-live-stream-owned')==='1'
  );
  const explicitTs=_firstValidTimestampSeconds(opts.ts, opts.timestamp, opts.created_at);
  const toolCall=opts.toolCall||row&&row._tcData||null;
  const toolTs=toolCall&&typeof toolCall==='object'
    ? _firstValidTimestampSeconds(
      toolCall.ts,
      toolCall.timestamp,
      toolCall.created_at,
      toolCall.started_at,
      toolCall.completed_at
    )
    : null;
  const attrTs=row&&typeof row.getAttribute==='function'
    ? _firstValidTimestampSeconds(
      row.getAttribute('data-event-at'),
      row.getAttribute('data-activity-at')
    )
    : null;
  const ts=explicitTs||toolTs||attrTs||(live?_activityNowSeconds():null);
  const label=ts?_activityClockLabel(ts):'';
  let timeEl=header.querySelector('.transparent-event-time');
  if(!label){
    if(timeEl) timeEl.remove();
    row.removeAttribute('data-event-at');
    row.removeAttribute('data-event-at-source');
    return null;
  }
  const source=explicitTs||toolTs||attrTs?'event':'live';
  row.setAttribute('data-event-at',String(ts));
  row.setAttribute('data-event-at-source',source);
  if(!showEventTimestamp){
    if(timeEl) timeEl.remove();
    return null;
  }
  if(!timeEl){
    timeEl=document.createElement('span');
    timeEl.className='transparent-event-time';
  }
  timeEl.textContent=label;
  // Full date+time tooltip: the bare clock label is date-ambiguous when a settled
  // session is reviewed days later (or a run crosses midnight), and timing is the
  // whole point of this label. (#5739 Fable UX fix.)
  const fullLabel=_activityFullClockLabel(ts);
  if(fullLabel) timeEl.setAttribute('title',fullLabel); else timeEl.removeAttribute('title');
  timeEl.setAttribute('data-event-at',String(ts));
  timeEl.setAttribute('data-event-at-source',source);
  const anchor=header.querySelector('.transparent-event-status,.thinking-card-btn-row,.tool-card-toggle,.thinking-card-toggle');
  if(timeEl.parentNode!==header){
    if(anchor&&anchor.parentNode===header) header.insertBefore(timeEl,anchor);
    else header.appendChild(timeEl);
  }else if(anchor&&timeEl.nextSibling!==anchor){
    header.insertBefore(timeEl,anchor);
  }
  return timeEl;
}
function _activityStatusNode({kind='info',label='',detail='',status='done',ts=null,id=''}){
  const row=document.createElement('div');
  row.className=`agent-activity-status agent-activity-status-${kind} agent-activity-status-${status}`;
  if(id) row.setAttribute('data-activity-event-id',id);
  if(ts) row.setAttribute('data-activity-at',String(ts));
  const iconMap={run:li('play',13),model:li('bot',13),waiting:'<span class="tool-card-running-dot"></span>',thinking:li('lightbulb',13),tool:li('wrench',13),done:li('check',13),warning:li('alert-triangle',13)};
  row.innerHTML=`<span class="agent-activity-status-icon">${iconMap[kind]||li('clock',13)}</span><span class="agent-activity-status-copy"><span class="agent-activity-status-label">${esc(label)}</span>${detail?`<span class="agent-activity-status-detail">${esc(detail)}</span>`:''}</span><span class="agent-activity-status-time">${esc(_activityClockLabel(ts))}</span>`;
  return row;
}
function _appendActivityEvent(group, event){
  if(!group)return null;
  const body=group.querySelector('.tool-call-group-body');
  if(!body)return null;
  const eventId=event&&event.id;
  let row=eventId?body.querySelector(`.agent-activity-status[data-activity-event-id="${CSS.escape(eventId)}"]`):null;
  const next=_activityStatusNode(event||{});
  if(row){row.replaceWith(next);row=next;}
  else{body.appendChild(next);row=next;}
  _activityMarkObserved(group,event&&event.ts);
  return row;
}
function _ensureLiveActivityBaseline(group){
  if(!group||group.getAttribute('data-live-tool-call-group')!=='1')return;
  const started=_activityElapsedStartedAt(group)||_activityNowSeconds();
  if(!group.getAttribute('data-turn-started-at')) group.setAttribute('data-turn-started-at',String(started));
  if(!group.getAttribute('data-last-activity-at')) group.setAttribute('data-last-activity-at',String(started));
  _appendActivityEvent(group,{id:'run-started',kind:'run',label:'Run started',detail:'Observable activity will appear here as the agent works.',status:'done',ts:started});
  const modelLabel=(S.session&&S.session.model)?getModelLabel(S.session.model):'';
  if(modelLabel)_appendActivityEvent(group,{id:'run-model',kind:'model',label:`Model: ${modelLabel}`,detail:S.activeProfile&&S.activeProfile!=='default'?`Profile: ${S.activeProfile}`:'',status:'done',ts:started});
}
function _setActivityElapsedStartedAt(group){
  if(!group||group.getAttribute('data-live-tool-call-group')!=='1')return;
  const started=_activityElapsedStartedAt(group);
  if(started)group.setAttribute('data-turn-started-at',String(started));
}
function _updateActiveActivityElapsedTimer(){
  const group=_activityElapsedTimerGroup;
  if(!group||!group.isConnected||group.getAttribute('data-live-tool-call-group')!=='1'||group.getAttribute('data-live-activity-current')!=='1'){
    _clearActivityElapsedTimer();
    return;
  }
  const durationEl=group.querySelector('.tool-call-group-duration');
  const label=_activityElapsedLabel(group);
  const processedLabel=_activityProcessedElapsedLabel(group);
  if(label){
    group.setAttribute('data-active-turn-elapsed',label);
  }else{
    group.removeAttribute('data-active-turn-elapsed');
  }
  const labelEl=group.querySelector('.tool-worklog-label') || group.querySelector('.tool-call-group-label');
  if(labelEl&&processedLabel){
    labelEl.textContent=processedLabel;
    labelEl.setAttribute('data-sweep-label', processedLabel);
  }
  if(durationEl){
    durationEl.textContent='';
    durationEl.style.display='none';
  }
}
function _startActivityElapsedTimer(group){
  if(!group||group.getAttribute('data-live-tool-call-group')!=='1')return;
  _setActivityElapsedStartedAt(group);
  // Last-resort fallback for recovered live renders that arrive before session metadata.
  if(!group.getAttribute('data-turn-started-at')) group.setAttribute('data-turn-started-at',String(_activityNowSeconds()));
  if(_activityElapsedTimerGroup&&_activityElapsedTimerGroup!==group)_clearActivityElapsedTimer();
  _activityElapsedTimerGroup=group;
  _updateActiveActivityElapsedTimer();
  if(!_activityElapsedTimer)_activityElapsedTimer=setInterval(_updateActiveActivityElapsedTimer,1000);
}
function _clearActivityElapsedTimer(){
  if(_activityElapsedTimer){
    clearInterval(_activityElapsedTimer);
    _activityElapsedTimer=null;
  }
  if(_activityElapsedTimerGroup&&_activityElapsedTimerGroup.isConnected){
    _activityElapsedTimerGroup.removeAttribute('data-active-turn-elapsed');
    const durationEl=_activityElapsedTimerGroup.querySelector('.tool-call-group-duration');
    if(durationEl){durationEl.textContent='';durationEl.style.display='none';}
  }
  _activityElapsedTimerGroup=null;
}

const _MOBILE_CONFIG_BASE_LABEL='Workspace, model, quota, reasoning, and context settings';

function _setCtxCompressButton(btn,text){
  if(!btn)return;
  if(text){
    btn.style.display='';
    btn.textContent=text;
    btn.onclick=function(e){
      if(e)e.stopPropagation();
      const ta=$('msg');
      if(ta){ta.value='/compress ';ta.focus();autoResize();}
    };
  }else{
    btn.style.display='none';
    btn.textContent='';
    btn.onclick=null;
  }
}

function _syncMobileCtxDisplay(state){
  const mobileConfigBtn=$('composerMobileConfigBtn');
  const row=$('composerMobileContextAction');
  const usageLine=$('composerMobileContextUsage');
  const tokensLine=$('composerMobileContextTokens');
  const thresholdLine=$('composerMobileContextThreshold');
  const costLine=$('composerMobileContextCost');
  const compressBtn=$('composerMobileCtxCompressBtn');
  if(!state||!state.visible){
    if(row)row.style.display='none';
    if(mobileConfigBtn){
      mobileConfigBtn.setAttribute('aria-label',_MOBILE_CONFIG_BASE_LABEL);
      mobileConfigBtn.setAttribute('title',_MOBILE_CONFIG_BASE_LABEL);
    }
    _setCtxCompressButton(compressBtn,'');
    // Reset context ring to 0% to clear any stale values from previous sessions
    var arc = document.getElementById('ctx-arc');
    var num = document.getElementById('ctx-num');
    if (arc && num) {
      var circumference = 87.96;
      arc.setAttribute('stroke-dashoffset', circumference);
      num.textContent = '0';
      arc.setAttribute('stroke', '#22c55e');
    }
    return;
  }
  (function updateCtxRing(pct) {
    var arc = document.getElementById('ctx-arc');
    var num = document.getElementById('ctx-num');
    if (!arc || !num) return;
    var offset = 87.96 * (1 - Math.min(pct, 100) / 100);
    arc.setAttribute('stroke-dashoffset', offset);
    num.textContent = Math.round(pct);
    arc.setAttribute('stroke',
      pct <= 50 ? '#22c55e' : pct <= 85 ? '#f97316' : '#ef4444'
    );
  })(state.pct);
  if(mobileConfigBtn){
    mobileConfigBtn.setAttribute('aria-label',`${_MOBILE_CONFIG_BASE_LABEL}; ${state.label}`);
    mobileConfigBtn.setAttribute('title',`${_MOBILE_CONFIG_BASE_LABEL} \u00b7 ${state.label}`);
  }
  if(row){
    row.style.display='';
    row.setAttribute('aria-label',state.label);
    row.classList.toggle('ctx-mid',state.pct>50&&state.pct<=75);
    row.classList.toggle('ctx-high',state.pct>75);
  }
  if(usageLine)usageLine.textContent=state.usageText||'';
  if(tokensLine)tokensLine.textContent=state.tokensText||'';
  if(thresholdLine){
    if(state.thresholdText){
      thresholdLine.style.display='';
      thresholdLine.textContent=state.thresholdText;
    }else{
      thresholdLine.style.display='none';
      thresholdLine.textContent='';
    }
  }
  if(costLine){
    if(state.costText){
      costLine.style.display='';
      costLine.textContent=state.costText;
    }else{
      costLine.style.display='none';
      costLine.textContent='';
    }
  }
  _setCtxCompressButton(compressBtn,state.compressText||'');
}

function _mergeUsageForCtxIndicator(latest, fallback){
  const latestObj=(latest&&typeof latest==='object')?latest:{};
  const fallbackObj=(fallback&&typeof fallback==='object')?fallback:{};
  const merged={...latestObj};
  for(const field of [
    'input_tokens','output_tokens','estimated_cost',
    'cache_read_tokens','cache_write_tokens','cache_hit_percent',
    'turn_cache_hit_percent','duration_seconds','tps','gateway_routing',
  ]){
    if(merged[field]==null&&fallbackObj[field]!=null){
      merged[field]=fallbackObj[field];
    }
  }
  if(!(Number(latestObj.context_length)>0)&&Number(fallbackObj.context_length)>0){
    merged.context_length=fallbackObj.context_length;
  }
  for(const field of ['threshold_tokens','last_prompt_tokens']){
    if(latestObj[field]==null&&fallbackObj[field]!=null){
      merged[field]=fallbackObj[field];
    }
  }
  if(!Object.hasOwn(latestObj,'post_compression_context_tokens_estimate')&&fallbackObj.post_compression_context_tokens_estimate!=null){
    merged.post_compression_context_tokens_estimate=fallbackObj.post_compression_context_tokens_estimate;
  }
  return merged;
}

// Context usage indicator in composer footer
function _syncCtxIndicator(usage){
  const wrap=$('ctxIndicatorWrap');
  const el=$('ctxIndicator');
  if(!el)return;
  const ctxHidden=!!(window._composerControlVisibility&&window._composerControlVisibility.hide_composer_context);
  if(ctxHidden){
    if(wrap) wrap.style.display='none';
    _syncMobileCtxDisplay({visible:false});
    return;
  }
  // #1436: Use last_prompt_tokens only — NEVER fall back to cumulative
  // input_tokens for the "context window % used" calculation.  input_tokens
  // is summed across all turns, so dividing it by the context window gives a
  // nonsense percentage (often >100%) on long sessions.  When we have no
  // last-prompt data we render "·" + "tokens used" via the !hasPromptTok
  // branch below — honest "no data" instead of misleading "890% used".
  const postCompressionEstimate=Number(usage.post_compression_context_tokens_estimate)||0;
  const hasPostCompressionEstimate=postCompressionEstimate>0;
  const promptTok=usage.last_prompt_tokens||0;
  const contextPromptTok=hasPostCompressionEstimate?postCompressionEstimate:promptTok;
  const totalTok=(usage.input_tokens||0)+(usage.output_tokens||0);
  const cacheReadTok=usage.cache_read_tokens||0;
  const cacheWriteTok=usage.cache_write_tokens||0;
  // Default context window to 128K when not provided by backend
  const DEFAULT_CTX=128*1024;
  const ctxWindow=usage.context_length||DEFAULT_CTX;
  const cost=usage.estimated_cost;
  // Show indicator whenever we have any usage data (tokens or cost)
  if(!promptTok&&!totalTok&&!cost&&!cacheReadTok&&!cacheWriteTok){
    if(wrap) wrap.style.display='none';
    _syncMobileCtxDisplay({visible:false});
    return;
  }
  if(wrap){
    // Defensive reset: keep dynamic context display from being stuck hidden.
    wrap.classList.remove('composer-control-hidden');
    wrap.removeAttribute('aria-hidden');
    wrap.style.display='';
  }
  let hasPromptTok=!!promptTok;
  if(hasPostCompressionEstimate) hasPromptTok=true;
  const rawPct=hasPromptTok?Math.round((contextPromptTok/ctxWindow)*100):0;
  const pct=Math.min(100,rawPct);
  const overflowed=rawPct>100;
  const ring=$('ctxRingValue');
  const center=$('ctxPercent');
  const usageLine=$('ctxTooltipUsage');
  const tokensLine=$('ctxTooltipTokens');
  const thresholdLine=$('ctxTooltipThreshold');
  const costLine=$('ctxTooltipCost');
  if(ring){
    const circumference=61.261056745;
    ring.style.strokeDasharray=String(circumference);
    ring.style.strokeDashoffset=String(circumference*(1-pct/100));
  }
  if(center) center.textContent=hasPromptTok?String(pct):'\u00b7';
  const hasExplicitCtx=!!usage.context_length;
  el.classList.toggle('ctx-mid',pct>50&&pct<=75);
  el.classList.toggle('ctx-high',pct>75);
  // ── Compress affordance (#524) ──
  // Show a hint in the tooltip when context usage is high so users
  // discover /compress without having to know the slash command.
  const compressWrap=$('ctxTooltipCompress');
  const compressBtn=$('ctxCompressBtn');
  const compressText=pct>=75?t('ctx_compress_action'):(pct>=50?t('ctx_compress_hint'):'');
  if(compressWrap) compressWrap.style.display=compressText?'':'none';
  _setCtxCompressButton(compressBtn,compressText);
  const cacheHitPct=usage.cache_hit_percent;
  const cacheText=cacheHitPct!=null?t('usage_cache_hit_detail',cacheHitPct,_fmtTokens(cacheReadTok),_fmtTokens(cacheWriteTok)):'';
  const contextLabel=hasPostCompressionEstimate?'Estimated next model context':'Context window';
  let label=hasPromptTok?`${contextLabel} ${pct}% used`:`${_fmtTokens(totalTok)} tokens used`;
  if(!hasExplicitCtx&&hasPromptTok) label+=' (est. 128K)';
  if(cost) label+=` \u00b7 $${cost<0.01?cost.toFixed(4):cost.toFixed(2)}`;
  if(cacheText) label+=` \u00b7 ${cacheText}`;
  el.setAttribute('aria-label',label);
  const usageText=hasPromptTok?(overflowed?`${contextLabel}: ${rawPct}% used (context exceeded)`:`${contextLabel}: ${pct}% used (${100-pct}% left)`):`${_fmtTokens(totalTok)} tokens used`;
  const tokensText=hasPromptTok?`${contextLabel}: ${_fmtTokens(contextPromptTok)} / ${_fmtTokens(ctxWindow)} tokens used`:`In: ${_fmtTokens(usage.input_tokens||0)} \u00b7 Out: ${_fmtTokens(usage.output_tokens||0)}`;
  if(usageLine) usageLine.textContent=usageText;
  if(tokensLine) tokensLine.textContent=tokensText;
  const threshold=usage.threshold_tokens||0;
  let thresholdText='';
  if(thresholdLine){
    if(threshold&&ctxWindow){
      thresholdText=`Auto-compress at ${_fmtTokens(threshold)} (${Math.round(threshold/ctxWindow*100)}%)`;
      thresholdLine.style.display='';
      thresholdLine.textContent=thresholdText;
    }else{
      thresholdLine.style.display='none';
      thresholdLine.textContent='';
    }
  }
  let costText='';
  if(costLine){
    if(cost){
      costText=`Estimated cost: $${cost<0.01?cost.toFixed(4):cost.toFixed(2)}`;
      if(cacheText) costText+=` \u00b7 ${cacheText}`;
      costLine.style.display='';
      costLine.textContent=costText;
    }else if(cacheText){
      costText=cacheText;
      costLine.style.display='';
      costLine.textContent=costText;
    }else{
      costLine.style.display='none';
      costLine.textContent='';
    }
  }
  _syncMobileCtxDisplay({
    visible:true,
    hasPromptTok,
    pct,
    label,
    usageText,
    tokensText,
    thresholdText,
    costText,
    compressText
  });
}

// ── Touch support: toggle context tooltip on tap (#524) ──
// Hover/focus still exposes the compact tooltip, but a click/tap now opens the
// shared composer config menu used by the phone footer so the richer context
// details and compress action have one interaction path.
document.addEventListener('DOMContentLoaded',function(){
  const wrap=document.getElementById('ctxIndicatorWrap');
  const tooltip=document.getElementById('ctxTooltip');
  if(!wrap||!tooltip)return;
  const btn=document.getElementById('ctxIndicator');
  if(!btn)return;
  btn.addEventListener('click',openComposerContextMenu);
  // Close on outside tap
  document.addEventListener('click',function(){
    tooltip.classList.remove('ctx-tooltip-active');
    tooltip.setAttribute('aria-hidden','true');
  },{passive:true});
  // Prevent tooltip click from closing itself
  tooltip.addEventListener('click',function(e){e.stopPropagation();});
});

function _setMessageScrollToBottom(){
  const el=$('messages');
  if(!el) return;
  _programmaticScroll=true;_programmaticScrollSetAt=performance.now();
  el.scrollTop=el.scrollHeight;
  _lastScrollTop=el.scrollTop;_lastMessageClientHeight=el.clientHeight;
  _nearBottomCount=2;
  _scrollPinned=true;
  requestAnimationFrame(()=>{
    // Retry the bottom write on the next layout frame so a DOM rebuild that
    // grows the transcript after the first write doesn't strand a pinned
    // conversation mid-scroll (#3319). But by this frame the user may have
    // scrolled up — under the sticky-unpin model (#3343) _messageUserUnpinned
    // is the authoritative "user scrolled away" signal, so DON'T snap them back
    // or re-pin if so; only release the programmatic-scroll latch.
    if(_messageUserUnpinned || !_scrollPinned || _recentNonMessageScrollIntent()){
      _deferClearProgrammaticScroll();
      return;
    }
    el.scrollTop=el.scrollHeight;
    _lastScrollTop=el.scrollTop;_lastMessageClientHeight=el.clientHeight;
    _nearBottomCount=2;
    _scrollPinned=true;
    _deferClearProgrammaticScroll();
  });
}
function _isMessagePaneNearBottom(threshold=250){
  const el=$('messages');
  if(!el) return false;
  return el.scrollHeight-el.scrollTop-el.clientHeight<=threshold;
}
function _messageBottomDistance(){
  const el=$('messages');
  if(!el) return 0;
  return el.scrollHeight-el.scrollTop-el.clientHeight;
}
// #5514/#5515: when the composer grows (typing multiple rows, Shift+Enter, a
// multi-line paste / WisprFlow), the flex:1 `.messages` viewport shrinks by the
// same delta. A reader pinned to the bottom is then stranded Δpx above it — the
// transcript appears to "scroll up" one row per composer row, and (the #5515
// half) it reads as a random upward jump during normal use. autoResize() only
// resized the textarea; nothing re-pinned the transcript. Re-pin the bottom, but
// ONLY when the reader is genuinely still pinned (sticky-unpin model: honor
// _messageUserUnpinned so we never yank a reader who scrolled away, and never
// fight a stream that already unpinned). Cheap no-op when not pinned.
function _repinMessagesAfterComposerResize(){
  if(_messageUserUnpinned || !_scrollPinned) return;
  const el=$('messages');
  if(!el) return;
  // Already at/very near the bottom? nothing to do (avoids needless writes while
  // idle-reading a short conversation that isn't scrollable).
  if(_messageBottomDistance()<=1) return;
  if(typeof _setMessageScrollToBottom==='function') _setMessageScrollToBottom();
  else { el.scrollTop=el.scrollHeight; }
}
if(typeof window!=='undefined') window._repinMessagesAfterComposerResize=_repinMessagesAfterComposerResize;
function _shouldFollowMessagesOnDomReplace(){
  // Final stream settlement replaces the live DOM with persisted messages. Keep
  // following only for users who are still pinned or effectively at the tail.
  // A broad near-bottom window causes long answers/mobile readers who scroll up
  // a little to read mid-stream to get snapped back to the bottom on completion.
  return window._autoScrollFollow && !_messageUserUnpinned && (_scrollPinned || _isMessagePaneNearBottom(120));
}
function _followMessagesAfterDomReplace(){
  if(_shouldFollowMessagesOnDomReplace()){
    scrollToBottom();
    return true;
  }
  return false;
}
function _settleMessageScrollToBottom(force, explicit){
  // `explicit` = a user-invoked scroll-to-bottom (End button / scrollToBottom()).
  // When explicit, late-layout settling runs even if Auto-follow is OFF — the
  // setting only suppresses AUTOMATIC streaming follow, not a deliberate jump
  // to the bottom. (Codex #4006 r3.)
  // can grow the transcript after the first scroll write. Re-apply the bottom
  // position when content settles so late layout does not leave the viewport
  // above the real end. User scroll increments _bottomSettleToken and cancels.
  //
  // Firefox paints each scrollTop write as a visible reflow step. The old
  // rAF-polling approach read scrollHeight across frames — the read itself
  // forced a reflow in Firefox, causing visible jitter.
  //
  // ResizeObserver approach: the browser notifies us when the container
  // resizes (no scrollHeight polling needed). On each notification we write
  // scrollTop once via rAF (batches multiple resize callbacks per frame into
  // a single write). After 300ms of no resize events, the observer disconnects.
  const token=++_bottomSettleToken;
  cancelAnimationFrame(_settleRAF);
  if(_settleRO){ _settleRO.disconnect(); _settleRO=null; }
  clearTimeout(_settleTimer);
  clearTimeout(_settleFinalTimer);

  // Sync write anchors the viewport immediately.
  _setMessageScrollToBottom();

  if(force) return;

  const el=document.getElementById('messages');
  if(!el) return;
  // Observe the GROWING content node, not the scroll container. #messages is the
  // scroller but its box is fixed by the flex layout, so it never resizes — the
  // transcript grows inside #msgInner (.messages-inner). Observing #messages
  // would mean the callback never fires. (Codex review #2.)
  const observed=document.getElementById('msgInner')||el;

  // Instance-owned cleanup: close over THIS observer so a stale callback (from a
  // superseded settle) only ever disconnects its own observer, never the newer
  // active one that may now be in the global _settleRO. (Codex review #3.)
  const ro=new ResizeObserver(()=>{
    if(token!==_bottomSettleToken){ ro.disconnect(); if(_settleRO===ro) _settleRO=null; return; }
    if((!window._autoScrollFollow&&!explicit)||!_scrollPinned||_messageUserUnpinned||_recentNonMessageScrollIntent()){
      ro.disconnect(); if(_settleRO===ro) _settleRO=null;
      _programmaticScroll=false;
      return;
    }
    // Write scrollTop once per frame — ResizeObserver batches multiple
    // notifications per frame, so this is at most one write per frame.
    cancelAnimationFrame(_settleRAF);
    _settleRAF=requestAnimationFrame(()=>{
      if(token!==_bottomSettleToken) return;
      _setMessageScrollToBottom();
    });
    // After 300ms of quiet, disconnect — layout is stable.
    clearTimeout(_settleTimer);
    _settleTimer=setTimeout(()=>{
      if(token!==_bottomSettleToken) return;
      ro.disconnect(); if(_settleRO===ro) _settleRO=null;
      _setMessageScrollToBottom();
    },300);
  });
  _settleRO=ro;
  ro.observe(observed);
  // #4702: for an explicit (user/open) settle, also observe the SCROLLER itself.
  // On iOS the transcript content (#msgInner) may not resize, but the scroller
  // grows when the portrait toolbar collapses after first paint — observing both
  // re-anchors the bottom after that late viewport settle. Desktop never resizes
  // here, so this is a no-op off-mobile.
  if(explicit&&observed!==el){ try{ ro.observe(el); }catch(_){ } }

  // Static-content safety net: a fully-static response (no Prism/KaTeX/Mermaid/
  // late images) never resizes after the initial sync write, so the
  // ResizeObserver callback above never fires and its 300ms quiet-timer is never
  // armed. Arm a single 2s top-level fallback so a late settle still runs for
  // that case. The token check inside _settleFinalScroll makes this a no-op if a
  // newer settle started, and it self-skips if the user unpinned. (Review #2/#3.)
  clearTimeout(_settleFinalTimer);
  _settleFinalTimer=setTimeout(()=>{
    if(token!==_bottomSettleToken) return;
    ro.disconnect(); if(_settleRO===ro) _settleRO=null;
    if((!window._autoScrollFollow&&!explicit)||!_scrollPinned||_messageUserUnpinned||_recentNonMessageScrollIntent()){ _programmaticScroll=false; return; }
    _settleFinalScroll(token);
  },2000);
}

function _settleFinalScroll(token){
  if(token!==_bottomSettleToken) return;
  const el=document.getElementById('messages');
  if(!el){ _programmaticScroll=false; return; }
  if(_messageUserUnpinned||!_scrollPinned||_recentNonMessageScrollIntent()||_recentMessageTouchScrollIntent()){
    _programmaticScroll=false;
    return;
  }
  _programmaticScroll=true;_programmaticScrollSetAt=performance.now();
  el.scrollTop=el.scrollHeight;
  _lastScrollTop=el.scrollTop;_lastMessageClientHeight=el.clientHeight;
  _nearBottomCount=2;
  _scrollPinned=true;
  _deferClearProgrammaticScroll();
}
function scrollIfPinned(){
  if(!window._autoScrollFollow) return;
  // A jump-to-question owner is mid-flight: it deliberately holds the reader at
  // the jump target across smooth-scroll frames, so never let a live token
  // reclaim the bottom while it is active (#6621). _finishMessageJumpScroll()
  // reconciles the pin state once the jump settles.
  if(typeof _messageJumpScrollOwner!=='undefined'&&_messageJumpScrollOwner) return;
  if(_messageUserUnpinned){
    // Only scrollToBottom() cleared this flag, so one scroll-up permanently
    // killed auto-follow. Re-pin ONLY when the reader has genuinely returned to
    // the true bottom tail (<=80px), NOT on mere near-bottom proximity — the
    // #4295 invariant is that proximity alone (inside the ~250px band) must not
    // re-pin, or a reader scanning the last few lines gets yanked to the bottom
    // mid-stream. Also bail on ANY recent message-pane scroll intent (wheel,
    // key, touch) and non-message intent, so an active scroll-up near the tail
    // is never overridden. Uses the same _nearBottomCount debounce as the
    // scroll listener (~4859-4866).
    if(_recentNonMessageScrollIntent()||_recentMessageScrollIntent()||_recentMessageTouchScrollIntent()||_recentMessageWheelIntent()||_recentMessageKeyScrollIntent()){ _nearBottomCount=0; return; }
    if(_messageBottomDistance()>80){ _nearBottomCount=0; return; }
    _nearBottomCount=_nearBottomCount+1;
    if(_nearBottomCount<2) return;
    _nearBottomCount=0;
    _messageUserUnpinned=false;
    _scrollPinned=true;
  }
  if(!_scrollPinned) return;
  if(_recentNonMessageScrollIntent()) return;
  if(_messageBottomDistance()>500) _setMessageScrollToBottom();
  _settleMessageScrollToBottom(false);
}
function scrollToBottom(){
  // An explicit scroll-to-bottom (End button, or any definitive pin-to-bottom)
  // supersedes a pending jump-to-question reconciliation: cancel the active jump
  // owner first so its deferred _finishMessageJumpScroll() can't restore the
  // pre-jump unpinned snapshot and silently undo this pin (#6621). The jump path
  // itself never calls scrollToBottom(), and while a jump owner is active the
  // reader is unpinned so the internal auto-follow callers don't reach here.
  if(typeof _messageJumpScrollOwner!=='undefined'&&_messageJumpScrollOwner&&typeof _cancelMessageJumpScroll==='function') _cancelMessageJumpScroll();
  _clearNewMessageScrollCue();
  _scrollPinned=true;
  _messageUserUnpinned=false;
  // Write scrollTop once synchronously to anchor the viewport, then let
  // ResizeObserver settle handle any late layout growth (Prism, KaTeX,
  // Mermaid, images).  Using force=false so the observer runs — force=true
  // was skipping the observer and causing Firefox paint jumps when
  // renderMessages({preserveScroll:true}) + scrollToBottom() fired back-to-back.
  _setMessageScrollToBottom();
  _settleMessageScrollToBottom(false, true);
  _syncScrollToBottomCue(false,{newMessage:false});
  if(typeof _updateSessionStartJumpButton==='function') _updateSessionStartJumpButton();
  if(typeof _flushDeferredActiveSessionExternalRefresh==='function') _flushDeferredActiveSessionExternalRefresh();
}

function _fmtOllamaLabel(mid){
  const [namePart, ...variantParts] = mid.split(':');
  const variant = variantParts.join(':');
  const _fmt = (s) => {
    const tokens = s.replace(/[-_]/g, ' ').split(' ');
    return tokens.map(t => {
      const alphaOnly = t.replace(/\./g, '');
      if (t.length <= 3 && /^[a-zA-Z.]+$/.test(t)) return t.toUpperCase();
      if (/^\d/.test(alphaOnly)) return t.toUpperCase();
      return t.charAt(0).toUpperCase() + t.slice(1);
    }).join(' ');
  };
  let label = _fmt(namePart);
  if (variant) label += ' (' + _fmt(variant) + ')';
  return label;
}

// Bedrock cross-region inference routing heads. `global` belongs here too: the
// catalog in api/config.py ships six `global.anthropic.claude-*` IDs and the
// first-party routing notes treat that as the canonical Bedrock shape. Keep this
// set byte-identical to _regions in api/config.py — backend catalog labels and
// runtime picker fallback labels diverge otherwise.
const _BEDROCK_REGION_PREFIXES = new Set(['us', 'eu', 'apac', 'global', 'us-gov']);
// Vendor namespaces Bedrock/Vertex put in front of the real model id.
const _DOTTED_VENDOR_PREFIXES = new Set([
  'anthropic', 'amazon', 'meta', 'mistral', 'cohere', 'ai21',
  'stability', 'writer', 'deepseek', 'qwen', 'openai', 'google',
  // Bedrock foundation-model vendors added after the first pass. Without these,
  // real IDs rendered with the namespace intact ("Us.luma.ray 2",
  // "Twelvelabs.marengo Embed 2 7", "Ibm.granite 3 8B Instruct").
  'luma', 'twelvelabs', 'ibm', 'nvidia', 'snowflake',
]);
/** Drop a Bedrock/Vertex dotted routing+vendor prefix from a model id.
 *
 *  Only the documented shapes are stripped — `<region>.<vendor>.<model>` and
 *  `<vendor>.<model>` — plus a trailing `:<n>` provisioned-revision suffix.
 *  Anything else is returned unchanged, so `deepseek.v3`, `foo.bar.baz` and the
 *  version dot in `gpt-4.1` are never rewritten.
 *
 *  Mirrors _strip_dotted_provider_prefix() in api/config.py. */
function _stripDottedModelPrefix(bare){
  const value = String(bare || '');
  if (!value || !value.includes('.') || value.includes('://') || value.startsWith('@')) return value;
  const segs = value.split('.');
  let i = 0;
  if (segs.length - i >= 3 && _BEDROCK_REGION_PREFIXES.has((segs[i] || '').toLowerCase())
      && _DOTTED_VENDOR_PREFIXES.has((segs[i + 1] || '').toLowerCase())) i++;
  if (segs.length - i >= 2 && _DOTTED_VENDOR_PREFIXES.has((segs[i] || '').toLowerCase())) {
    // Dropping the vendor is only safe when what remains still names the model.
    // A bare version remainder (`deepseek.v3`) means the vendor WAS the name.
    const remainder = segs.slice(i + 1).join('.');
    if (!/^v?\d+(?:[.\-]\d+)*$/i.test(remainder)) i++;
  }
  if (i === 0) return value;
  return segs.slice(i).join('.').replace(/:\d+$/, '');
}
function getModelLabel(modelId){
  if(!modelId) return 'Unknown';
  const rawId=String(modelId||'');
  // Preserve custom gateway model IDs exactly as configured.
  // Examples:
  //   @custom:ai_gateway:Qwen3.6-35B-A3B -> Qwen3.6-35B-A3B
  //   @custom:qwen397b-64k               -> qwen397b-64k
  if(rawId.startsWith('@custom:')){
    const rest=rawId.slice('@custom:'.length);
    if(rest.includes(':')) return rest.slice(rest.lastIndexOf(':')+1)||rawId;
    if(rest.includes('/')) return rest.slice(rest.indexOf('/')+1)||rawId;
    return rest||rawId;
  }
  // Check dynamic labels first, then fall back to splitting the ID
  if(_dynamicModelLabels[modelId]) return _dynamicModelLabels[modelId];
  // Static fallback for common models
  const STATIC_LABELS={'openai/gpt-5.4-mini':'GPT-5.4 Mini','openai/gpt-4o':'GPT-4o','openai/o3':'o3','openai/o4-mini':'o4-mini','anthropic/claude-sonnet-4.6':'Sonnet 4.6','anthropic/claude-sonnet-4-5':'Sonnet 4.5','anthropic/claude-haiku-3-5':'Haiku 3.5','google/gemini-3.1-pro-preview':'Gemini 3.1 Pro','google/gemini-3-flash-preview':'Gemini 3 Flash','google/gemini-3.1-flash-lite-preview':'Gemini 3.1 Flash Lite','google/gemini-2.5-pro':'Gemini 2.5 Pro','google/gemini-2.5-flash':'Gemini 2.5 Flash','deepseek/deepseek-v4-flash':'DeepSeek V4 Flash','deepseek/deepseek-v4-pro':'DeepSeek V4 Pro','deepseek/deepseek-chat-v3-0324':'DeepSeek V3 (legacy)','meta-llama/llama-4-scout':'Llama 4 Scout'};
  if(STATIC_LABELS[modelId]) return STATIC_LABELS[modelId];
  // Safe Ollama-tag fallback: strip only the first slash-segment (provider
  // prefix) so multi-slash IDs preserve their vendor hierarchy (#3360).
  // URI-scheme ids (e.g. `gpt://${FOLDER}/deepseek-v4-flash/latest`, provider
  // `yandex:gpt`) must NOT be first-segment-stripped — `indexOf('/')` would
  // land inside the `://` and leave `/${FOLDER}/...` path junk (#3429). For a
  // `scheme://authority/path...` id, drop the scheme AND the authority, then
  // pick the model name from the PATH segments only. A version/channel tail
  // (`latest`/`stable`/numeric) is skipped only when a real model segment
  // precedes it — never promoting the authority or a container folder (#3429).
  let _last;
  const _uriMatch = /^[a-z][a-z0-9+.-]*:\/\/(.+)$/i.exec(modelId);
  if (_uriMatch) {
    const _all = _uriMatch[1].split('/').filter(Boolean);
    // _all[0] is the authority (folder/host); the model lives in the path tail.
    const _path = _all.slice(1);
    // A pure version/channel tail: named channels, or a bare version number
    // (`v4`, `1.2`, `20231231`) — NOT a mixed model name that merely starts
    // with a digit (`2026-model`, `4o-mini`), which must be kept as the label.
    const _isVersionTail = (s) => /^(latest|stable|current|default|v\d[\d.]*|\d[\d.]*)$/i.test(s);
    const _isPlaceholder = (s) => /\$\{[^}]*\}/.test(s);
    // Walk path segments right-to-left; the model name is the LAST segment that
    // is neither a version/channel tail (`latest`, `v4`, `1.2`) nor a `${...}`
    // env-var placeholder. Fall back to the last non-placeholder segment, then
    // the literal last segment. Never returns the authority (`_all[0]`).
    let _pick = '';
    let _lastUsable = '';
    for (let _i = _path.length - 1; _i >= 0; _i--) {
      const _seg = _path[_i];
      if (_isPlaceholder(_seg)) continue;
      if (!_lastUsable) _lastUsable = _seg;
      if (!_isVersionTail(_seg)) { _pick = _seg; break; }
    }
    // Fallbacks: the chosen non-version segment, else the last non-placeholder
    // path segment. NEVER the authority and NEVER a `${...}` placeholder — for
    // a degenerate id (`gpt://folder123`, `gpt://folder123/${MODEL}`) fall all
    // the way back to the raw id rather than leak the folder/host or env var.
    const _lastPath = _path[_path.length - 1] || '';
    _last = _pick || _lastUsable || (_lastPath && !_isPlaceholder(_lastPath) ? _lastPath : '') || modelId;
  } else {
    _last = modelId.includes('/') ? (modelId.slice(modelId.indexOf('/')+1) || modelId) : modelId;
  }
  // Strip @provider: prefix if present (e.g. @ollama-cloud:kimi-k2.6)
  if (_last.startsWith('@') && _last.includes(':')) _last = _last.split(':').slice(1).join(':');
  // Bedrock/Vertex ids carry a dotted region + vendor prefix and sometimes a
  // trailing `:<n>` version — `us.anthropic.claude-opus-5`,
  // `us.anthropic.claude-sonnet-4-5-20250929-v1:0`. Left intact, the dotted head
  // survives into the label as raw plumbing ("Us.anthropic.claude Opus 5" in the
  // turn footer).
  //
  // Only the documented `<region>.<vendor>.<model>` / `<vendor>.<model>` shapes
  // are stripped, via a CLOSED allow-list. A generic "drop leading letters-only
  // dot segments" loop rewrites any uncatalogued id — `deepseek.v3` became "V3"
  // and `foo.bar.baz` became "BAZ". Kept in lockstep with
  // _strip_dotted_provider_prefix() in api/config.py; the paired test
  // tests/test_dotted_model_label.py asserts both agree.
  const _stripped = _stripDottedModelPrefix(_last);
  if (_stripped !== _last) {
    _last = _stripped;
    // The normalized id is what the label tables are keyed on, so retry them —
    // `us.anthropic.claude-sonnet-4-5` should land on the same "Sonnet 4.5" as
    // `anthropic/claude-sonnet-4-5` rather than falling through to the raw id.
    if (_dynamicModelLabels[_last]) return _dynamicModelLabels[_last];
    if (STATIC_LABELS[_last]) return STATIC_LABELS[_last];
    if (STATIC_LABELS['anthropic/' + _last]) return STATIC_LABELS['anthropic/' + _last];
    // No table entry: prettify the Claude family the way the tables do — drop
    // the `claude-` vendor word, the `-YYYYMMDD` date-pin and `-v1` revision
    // (snapshot noise, not a name), then title-case. Bedrock is the only
    // dotted-prefix source here, so this stays scoped to that path.
    if (/^claude-/i.test(_last)) {
      _last = _last
        .replace(/^claude-/i, '')
        .replace(/-v\d+$/i, '')
        .replace(/-\d{8}$/, '')
        .replace(/-/g, ' ')
        .replace(/\b\w/g, c => c.toUpperCase())
        .trim();
    }
  }
  const looksLikeOllamaTag = /^[a-z0-9][\w.-]*:[\w.-]+$/i.test(_last);
  const atProvider=(rawId.startsWith('@')&&rawId.includes(':'))
    ? rawId.slice(1,rawId.indexOf(':')).toLowerCase()
    : '';
  const allowOllamaFormat=!atProvider||atProvider.startsWith('ollama');
  // Narrow: only apply Ollama formatter to IDs with explicit @ollama prefix or colon-tag format.
  // Avoids reformatting bare provider model IDs like claude-sonnet-4-6 or gpt-4o.
  const looksLikeBareOllamaId = modelId.startsWith('@ollama') || looksLikeOllamaTag;
  const ollamaLabel = _fmtOllamaLabel(_last);
  if (allowOllamaFormat && (modelId.startsWith('ollama/') || modelId.startsWith('@ollama') || looksLikeOllamaTag || looksLikeBareOllamaId) && ollamaLabel !== _last) {
    return ollamaLabel;
  }
  return _last || 'Unknown';
}

function _gatewayProviderName(provider){
  const text=String(provider||'').trim();
  if(!text)return'';
  return text.replace(/^custom:/,'').replace(/[-_]/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
}
function _gatewayRoutingLabel(routing){
  if(!routing)return'';
  const provider=_gatewayProviderName(routing.used_provider||routing.provider);
  return provider?`via ${provider}`:'';
}
function _formatGatewayModelLabel(modelId,labelText,routing){
  if(!routing)return'';
  const usedModel=String(routing.used_model||'').trim();
  const base=usedModel
    ?_compactComposerModelChipLabel(usedModel,getModelLabel(usedModel))
    :_compactComposerModelChipLabel(modelId,labelText||getModelLabel(modelId));
  const via=_gatewayRoutingLabel(routing);
  return via?`${base} ${via}`:base;
}
function _usedModelTurnChipLabel(msg){
  if(!msg)return'';
  // Gateway turns own their model label via _formatGatewayModelLabel (which
  // falls back to msg._usedModel when routing omits used_model), so suppress
  // the additive chip whenever routing metadata is present — not only when
  // routing.used_model is set — to guarantee one model label per turn.
  if(msg._gatewayRouting)return'';
  const usedModel=String(msg._usedModel||'').trim();
  if(!usedModel)return'';
  return _compactComposerModelChipLabel(usedModel,getModelLabel(usedModel));
}
function _gatewayRoutingFailoverText(routing){
  if(!routing||!routing.has_failover)return'';
  const attempts=Array.isArray(routing.routing)?routing.routing:[];
  const providers=attempts.map(a=>_gatewayProviderName(a&&a.provider)).filter(Boolean);
  const unique=[];providers.forEach(p=>{if(!unique.includes(p))unique.push(p);});
  if(unique.length>=2)return`Failover: ${unique[0]} → ${unique[unique.length-1]}`;
  const from=_gatewayProviderName(routing.requested_provider);
  const to=_gatewayProviderName(routing.used_provider);
  if(from&&to&&from!==to)return`Failover: ${from} → ${to}`;
  return'Gateway failover detected';
}
function _gatewayModelWarningText(routing){
  if(!routing||!routing.model_changed)return'';
  const requested=getModelLabel(routing.requested_model||'requested model');
  const used=getModelLabel(routing.used_model||'served model');
  return`Model switched: ${requested} → ${used}`;
}
function _latestGatewayRoutingForSession(session){
  if(!session)return null;
  if(session.gateway_routing)return session.gateway_routing;
  const history=Array.isArray(session.gateway_routing_history)?session.gateway_routing_history:[];
  return history.length?history[history.length-1]:null;
}

function _stripXmlToolCallsDisplay(s){
  // Strip <function_calls>...</function_calls> blocks emitted by DeepSeek and
  // similar models in their raw response text.  These are processed separately
  // as tool calls; leaving them in the content causes them to render visibly
  // in the settled chat bubble.  (#702)
  // Also handles DSML-prefixed variants from DeepSeek/Bedrock, including
  // spacing variants like "<｜DSML |function_calls" and truncated prefixes.
  if(!s) return s;
  const lo=String(s).toLowerCase();
  if(lo.indexOf('function_calls')===-1 && lo.indexOf('dsml')===-1) return s;
  // Support both plain <function_calls> and DSML-prefixed variants.
  s=s.replace(/<(?:\s*｜\s*DSML\s*[｜|]\s*)?function_calls>[\s\S]*?<\/(?:\s*｜\s*DSML\s*[｜|]\s*)?function_calls>/gi,'');
  // Also remove truncated opening tags (missing closing ">" at stream tail).
  s=s.replace(/<(?:\s*｜\s*DSML\s*[｜|]\s*)?function_calls(?:>|$)[\s\S]*$/i,'');
  // Remove malformed DSML tag fragments like "<｜DSML |" that can leak in tokens.
  s=s.replace(/<\s*｜\s*DSML\s*[｜|]\s*/gi,'');
  return s.trim();
}

function _sanitizeThinkingDisplayText(text){
  const stripped=_stripXmlToolCallsDisplay(String(text||''));
  return stripped.trim();
}

function _normalizeThinkingEchoCompare(text){
  return String(text||'').replace(/\s+/g,' ').trim();
}

function _stripVisibleAssistantEchoFromThinking(thinkingText, ...visibleTexts){
  const clean=_sanitizeThinkingDisplayText(thinkingText);
  const thinkingNorm=_normalizeThinkingEchoCompare(clean);
  if(!thinkingNorm) return '';
  for(const visibleText of visibleTexts){
    const visibleNorm=_normalizeThinkingEchoCompare(visibleText);
    if(visibleNorm&&visibleNorm===thinkingNorm) return '';
  }
  return clean;
}

function renderMd(raw){
  let s=(raw||'').replace(/\r\n/g,'\n').replace(/\r/g,'\n');
  // ── Entity decode: must run FIRST so &gt; lines become > for the blockquote
  // pre-pass below. LLMs sometimes emit HTML-entity-encoded output; without this
  // a blockquote sent as "&gt; text" would never be recognised as a blockquote.
  s=s.replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&').replace(/&quot;/g,'"').replace(/&#39;/g,"'");
  // ── Blockquote pre-pass (must run BEFORE every other markdown pass) ────────
  // Group consecutive >-prefixed lines, strip the > prefix from each line,
  // recursively render the stripped content with the full pipeline, and
  // replace the group with a stash token. This is the only way fenced code,
  // headings, hr, and ordered lists inside a blockquote can render correctly:
  // the per-line passes downstream don't know about > prefixes, and by the
  // time the blockquote handler used to run those passes had already mangled
  // the >-prefixed lines.
  //
  // Walks lines (instead of using a single regex) so >-prefixed lines that
  // sit inside a non-blockquote fenced block (e.g. a shell prompt in a
  // ```bash``` example) are not miscaptured as a blockquote.
  const _bq_stash=[];
  s=(function _applyBlockquotes(input){
    const lines=input.split('\n');
    const out=[];
    let inFence=false;     // inside a non-blockquote backtick fence
    let fenceLen=0;
    let bqStart=-1;
    const flush=(end)=>{
      if(bqStart<0) return;
      // Strip "> " prefix (and bare ">" → empty) from each line
      const stripped=lines.slice(bqStart,end).map(l=>l.replace(/^> ?/,'')).join('\n');
      // Recursive call: full pipeline on stripped content. Handles fenced
      // code, headings, hr, ordered/unordered lists, nested blockquotes
      // (>>) — anything that renderMd handles at the top level.
      const rendered=renderMd(stripped);
      _bq_stash.push('<blockquote>'+rendered+'</blockquote>');
      // Surround the token with blank lines so the paragraph splitter
      // isolates it as its own chunk (otherwise the token gets wrapped
      // in <p>...<br> with adjacent text, producing invalid HTML).
      out.push('');
      out.push('\x00Q'+(_bq_stash.length-1)+'\x00');
      out.push('');
      bqStart=-1;
    };
    for(let i=0;i<lines.length;i++){
      const line=lines[i];
      if(inFence){
        out.push(line);
        if(_isBacktickFenceClose(line,fenceLen)){inFence=false;fenceLen=0;}
        continue;
      }
      const fenceOpen=_matchBacktickFenceLine(line);
      if(fenceOpen){
        flush(i);
        out.push(line);
        inFence=true;
        fenceLen=fenceOpen.len;
        continue;
      }
      if(/^>/.test(line)){
        if(bqStart<0) bqStart=i;
      } else {
        flush(i);
        out.push(line);
      }
    }
    flush(lines.length);
    return out.join('\n');
  })(s);
  // ── MEDIA: token stash (must run first, before any other processing) ───────
  // Detect MEDIA:<path-or-url> tokens emitted by the agent (e.g. screenshots,
  // generated images) and replace them with inline <img> or download links.
  // Stashed so the path/URL is never processed as markdown.
  const media_stash=[];
  s=s.replace(/MEDIA:([^\s\)\]]+)/g,(_,raw_ref)=>{
    media_stash.push(raw_ref);
    return '\x00D'+(media_stash.length-1)+'\x00';
  });
  // ── End MEDIA stash ─────────────────────────────────────────────────────────
  // Pre-pass: decode HTML entities first so markdown processing works correctly.
  // This prevents double-escaping when LLM outputs entities like &lt; &gt; &amp;
  const decode=s=>s.replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&').replace(/&quot;/g,'"').replace(/&#39;/g,"'");
  s=decode(s);
  // Pre-pass: convert safe inline HTML tags the model may emit into their
  // markdown equivalents so the pipeline can render them correctly.
  // Only runs OUTSIDE fenced code blocks and backtick spans (stash + restore).
  // Unsafe tags (anything not in the allowlist) are left as-is and will be
  // HTML-escaped by esc() when they reach an innerHTML assignment -- no XSS risk.
  // Fence stash: protect code blocks and backtick spans from all further processing.
  // Must run BEFORE math_stash so $..$ inside code spans is not extracted as math.
  // Split into fenced blocks (\x00P — kept stashed until after all markdown passes)
  // and inline backtick spans (\x00F — restored before bold/italic so **`code`** works).
  // Fenced blocks are converted to <pre><code> here so their content is HTML-escaped
  // and never exposed to list/heading/table regexes that could corrupt the layout.
  // Fixes #1154: diff/patch lines inside fenced blocks (e.g. + added, - removed)
  // were matching the unordered-list regex and injecting <ul>/<li> inside <pre>,
  // breaking </pre> closure and corrupting all subsequent message rendering.
  const _preBlock_stash=[];
  const fence_stash=[];
  // CommonMark §4.5: opening fence must start a line (with up to 3 spaces of indent)
  // and closing fence must start a line with the same backtick char and at least
  // as many backticks as the opener. Without line/fence-length anchoring, a literal
  // ``` inside a code block (e.g. a nested markdown example) terminates the outer
  // block at the wrong place, leaking content into the markdown stream where
  // bold/italic/inline-code passes corrupt it. Fixes #1438 and #1696.
  s=s.replace(/(^|\n)[ ]{0,3}(`{3,})([^\n`]*)\n(?:([\s\S]*?)\n)?[ ]{0,3}\2`*[ \t]*(?=\n|$)/g,(_,lead,_fence,info,code)=>{
    const langInfo=(info||'').trim();
    const langMatch=langInfo.match(/^(\w[\w+-]*)$/);
    const lang=langMatch?(langMatch[1]||'').trim().toLowerCase():'';
    code=code||'';
    const codeLines=code.split('\n');
    const firstCodeLine=codeLines.find(line=>line.trim())||'';
    const looksLikeLineNumberedToolOutput=/^\s*\d+\|/.test(firstCodeLine);
    const firstMermaidLine=codeLines.map(line=>line.trim()).find(line=>line&&!line.startsWith('%%'))||'';
    const looksLikeMermaidStart=firstMermaidLine==='---'||/^(graph|flowchart|sequenceDiagram|classDiagram|classDiagram-v2|stateDiagram|stateDiagram-v2|erDiagram|journey|gantt|pie|gitGraph|mindmap|timeline|quadrantChart|requirementDiagram|C4Context|C4Container|C4Component|C4Dynamic|c4Context|c4Container|c4Component|c4Dynamic|sankey-beta|block-beta|packet-beta|xychart-beta|kanban|architecture-beta)\b/.test(firstMermaidLine);
    if((lang==='mermaid'||(!lang&&looksLikeMermaidStart))&&!looksLikeLineNumberedToolOutput){
      const id='mermaid-'+Math.random().toString(36).slice(2,10);
      _preBlock_stash.push(`<div class="mermaid-block" data-mermaid-id="${id}">${esc(code.trim())}</div>`);
    } else {
      const h=lang?`<div class="pre-header">${esc(lang)}</div>`:'';
      const langAttr=lang?` class="language-${esc(lang)}"`:'';
      const preClass=/^(md|markdown|mdx)$/.test(lang)?' class="md-source-block"':'';
      // For diff/patch blocks, wrap each line in a colored span
      if(lang==='diff'||lang==='patch'){
        const colored=esc(code.replace(/\n$/,'')).split('\n').map(line=>{
          if(line.startsWith('@@')) return `<span class="diff-line diff-hunk">${line}</span>`;
          if(line.startsWith('+')) return `<span class="diff-line diff-plus">${line}</span>`;
          if(line.startsWith('-')) return `<span class="diff-line diff-minus">${line}</span>`;
          return `<span class="diff-line">${line}</span>`;
        }).join('\n');
        _preBlock_stash.push(`${h}<pre class="diff-block"><code${langAttr}>${colored}</code></pre>`);
      // For JSON/YAML blocks, add tree-view placeholder with raw data
      } else if(lang==='json'||lang==='yaml'){
        const rawCode=esc(code.replace(/\n$/,''));
        // Encode newlines as &#10; to prevent HTML attribute normalization
        // (browsers collapse \n to spaces inside attribute values).
        const rawAttr=rawCode.replace(/"/g,'&quot;').replace(/\n/g,'&#10;');
        const blockId='tree-'+Math.random().toString(36).slice(2,10);
        _preBlock_stash.push(`<div class="code-tree-wrap" data-raw="${rawAttr}" data-lang="${lang}" id="${blockId}">${h}<pre class="tree-raw-view"><code${langAttr}>${rawCode}</code></pre></div>`);
      // CSV blocks → render as styled table
      } else if(lang==='csv'){
        const rows=code.replace(/\n$/,'').split('\n').filter(r=>r.trim());
        if(rows.length>=2){
          const headers=rows[0].split(',').map(c=>c.trim());
          const body=rows.slice(1).map(r=>'<tr>'+r.split(',').map(c=>`<td>${esc(c.trim())}</td>`).join('')+'</tr>').join('');
          _preBlock_stash.push(`${h}<div class="csv-table-wrap"><table class="csv-table"><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table></div>`);
        } else {
          _preBlock_stash.push(`${h}<pre${preClass}><code${langAttr}>${esc(code.replace(/\n$/,''))}</code></pre>`);
        }
      } else {
        _preBlock_stash.push(`${h}<pre${preClass}><code${langAttr}>${esc(code.replace(/\n$/,''))}</code></pre>`);
      }
    }
    return lead+'\x00P'+(_preBlock_stash.length-1)+'\x00';
  });
  s=s.replace(/`([^`\n]+)`/g,(_,c)=>{fence_stash.push('<code>'+esc(c)+'</code>');return '\x00F'+(fence_stash.length-1)+'\x00';});
  // Math stash: protect $$..$$ and $..$ from markdown processing
  // Runs AFTER fence_stash so backtick code spans protect their dollar-sign contents
  const math_stash=[];
  // Display math: $$...$$ and \[...\] (must come before inline to avoid mis-parsing)
  s=s.replace(/\$\$([\s\S]+?)\$\$/g,(_,m)=>{math_stash.push({type:'display',src:m});return '\x00M'+(math_stash.length-1)+'\x00';});
  // Match a single literal backslash before the display delimiter (the common LLM form).
  s=s.replace(/\\\[([\s\S]+?)\\\]/g,(_,m)=>{math_stash.push({type:'display',src:m});return '\x00M'+(math_stash.length-1)+'\x00';});
  // Inline math: $...$ — require non-space/non-digit at opening boundary to avoid
  // false positives on currency like "$1,000 xuống ~$95" or "costs $5 and $10".
  // Aligns with smd's se() guard which also rejects $ followed by digits.
  s=s.replace(/\$([^\s$\d\n][^$\n]*?[^\s$\n]|[^\s\d])\$/g,(_,m)=>{if(m.includes(' | '))return '\$'+m+'\$';math_stash.push({type:'inline',src:m});return '\x00M'+(math_stash.length-1)+'\x00';});
  // Also stash \(...\) LaTeX delimiters.
  // Match a single literal backslash before the delimiter (the common LLM form).
  s=s.replace(/\\\((.+?)\\\)/g,(_,m)=>{math_stash.push({type:'inline',src:m});return '\x00M'+(math_stash.length-1)+'\x00';});
  // Safe tag → markdown equivalent (these produce the same output as **text** etc.)
  // Stash raw <pre> blocks so the inline <code> rewrite below does not run
  // inside them. Running that rewrite in <pre> content can introduce stray
  // backticks for multiline code and break subsequent code-box rendering.
  const rawPreStash=[];
  s=s.replace(/(<pre\b[^>]*>[\s\S]*?<\/pre>)/gi,m=>{rawPreStash.push(m);return `\x00R${rawPreStash.length-1}\x00`;});
  // Bare file:// artifact links → media. Some gateway/tool surfaces emit bare
  // file:// links for local artifacts instead of MEDIA: tokens; browser clients
  // cannot open the server filesystem directly, so route them through /api/media.
  // Runs AFTER fenced-block (\x00P), inline-code (\x00F), AND raw-<pre> (\x00R)
  // stashing so a file:// inside any code/preformatted region stays literal text
  // (#3219/#3234). Only bare URLs (line-start or whitespace-delimited) match, so
  // normal [label](file://...) markdown anchors keep the link path below.
  s=s.replace(/(^|\s)(file:\/\/[^\s<>"')\]]+)/g,(_,lead,raw_ref)=>{
    media_stash.push(raw_ref);
    return lead+'\x00D'+(media_stash.length-1)+'\x00';
  });
  s=s.replace(/<strong>([\s\S]*?)<\/strong>/gi,(_,t)=>'**'+t+'**');
  s=s.replace(/<b>([\s\S]*?)<\/b>/gi,(_,t)=>'**'+t+'**');
  s=s.replace(/<em>([\s\S]*?)<\/em>/gi,(_,t)=>'*'+t+'*');
  s=s.replace(/<i>([\s\S]*?)<\/i>/gi,(_,t)=>'*'+t+'*');
  s=s.replace(/<code>([^<]*?)<\/code>/gi,(_,t)=>'`'+t+'`');
  s=s.replace(/<br\s*\/?>/gi,'\n');
  // ── Glued-bold-heading lift (issue #1446) ────────────────────────────────
  // LLMs in thinking/reasoning mode frequently emit a "section header" glued
  // to the end of the previous paragraph with no whitespace, like:
  //
  //   Para 1 text.**Heading to Para 2**
  //
  //   Para 2 text.**Heading to Para 3**
  //
  // CommonMark renders that correctly as paragraph-end inline bold, but the
  // visual effect is a run-on label rather than a section break. Lift the
  // glued bold into its own paragraph when it follows a sentence terminator
  // and is followed by a blank line.
  //
  // Constraints (avoid false positives):
  //   - Trigger only on a sentence terminator (.!?) IMMEDIATELY before `**`
  //     (no space) — that pattern is almost always a glued heading, not
  //     intentional emphasis.
  //   - Inner text length ≤ 80 chars — long bold runs are usually emphasis
  //     prose, not headings.
  //   - Trailing `\n\n` required — preserves mid-paragraph emphasis like
  //     "this is **important**." untouched.
  //   - Inner text must not contain newlines or `*` (single-line bold only).
  //   - Runs after fenced code, math, and raw <pre> are stashed, so code
  //     content is protected (see pipeline notes).
  s=s.replace(/([.!?])\*\*([^*\n]{1,80})\*\*\n\n/g,'$1\n\n**$2**\n\n');
  // Inline backtick spans: restore <code> tags produced in the stash callback above.
  // Must happen BEFORE bold/italic so **`code`** → <strong><code>code</code></strong>.
  s=s.replace(/\x00F(\d+)\x00/g,(_,i)=>fence_stash[+i]);
  // inlineMd: process bold/italic/code/links within a single line of text.
  // Used inside list items and blockquotes where the text may already contain
  // HTML from the pre-pass → bold pipeline, so we cannot call esc() directly.
  function inlineMd(t){
    // Stash backtick code spans first so bold/italic never esc() their content
    const _code_stash=[];
    t=t.replace(/`([^`\n]+)`/g,(_,x)=>{_code_stash.push(`<code>${esc(x)}</code>`);return `\x00C${_code_stash.length-1}\x00`;});
    t=t.replace(/\*\*\*(.+?)\*\*\*/g,(_,x)=>`<strong><em>${esc(x)}</em></strong>`);
    t=t.replace(/\*\*(.+?)\*\*/g,(_,x)=>`<strong>${esc(x)}</strong>`);
    t=t.replace(/\*([^*\n]+)\*/g,(_,x)=>`<em>${esc(x)}</em>`);
    // Strikethrough: ~~text~~ → <del>text</del>
    t=t.replace(/~~(.+?)~~/g,(_,x)=>`<del>${esc(x)}</del>`);
    // #487: Image pass — runs while code stash is active so ![x](url) inside
    // backticks stays protected as a \x00C token and is never rendered as <img>.
    // Must run before _code_stash restore and before _link_stash so the image
    // is not consumed by the [label](url) link regex.
    t=t.replace(/!\[([^\]]*)\]\(((?:https?:\/\/|file:\/\/|data:image\/)[^\)]+)\)/g,(_,alt,url)=>(typeof _mdImageHtml==='function')?_mdImageHtml(alt,url):`<img src="${url.replace(/"/g,'%22')}" alt="${esc(alt)}" class="msg-media-img" loading="lazy">`);
    // Stash rendered <img> tags so autolink never matches URLs inside src=
    const _img_stash=[];
    t=t.replace(/(<img\b[^>]*>)/g,m=>{_img_stash.push(m);return `\x00G${_img_stash.length-1}\x00`;});
    t=t.replace(/\x00C(\d+)\x00/g,(_,i)=>_code_stash[+i]);
    // Stash [label](url) links before autolink so the URL in href= is not re-linked
    const _link_stash=[];
    t=t.replace(/\[([^\]]+)\]\(((?:https?:\/\/|file:\/\/|workspace:\/\/|session:\/\/|vault:\/\/|vault-insert:\/\/|vault-create:\/\/|vault-weave:\/\/|vault-search:\/\/|deepmode:\/\/|#deepmode=|mailto:|tel:|message:)[^\s\)]+)\)/g,(_,lb,u)=>{_link_stash.push(_markdownAnchor(lb,u));return `\x00L${_link_stash.length-1}\x00`;});
    t=t.replace(/(https?:\/\/[^\s<>"')\]\uFF09]+)/g,(url)=>{const trail=url.match(/[.,;:!?)\uFF09\uFF0C\uFF1B\uFF1A\uFF01\uFF1F\u3001\u3002]$/)?url.slice(-1):'';const clean=trail?url.slice(0,-1):url;return `<a href="${clean}" target="_blank" rel="noopener">${esc(clean)}</a>${trail}`;});
    t=t.replace(/\x00L(\d+)\x00/g,(_,i)=>_link_stash[+i]);
    t=t.replace(/\x00G(\d+)\x00/g,(_,i)=>_img_stash[+i]);
    // Escape any plain text that isn't already wrapped in a tag we produced
    // by escaping bare < > that are not part of our own tags
    const SAFE_INLINE=/^<\/?(strong|em|del|code|a|img)([\s>]|$)/i;
    t=t.replace(/<\/?[a-z][^>]*>/gi,tag=>SAFE_INLINE.test(tag)?tag:esc(tag));
    return t;
  }
  // Stash <code> tags from the backtick pass above so the outer bold/italic
  // regexes don't esc() their content (e.g. **`code`** → <strong><code>code</code></strong>)
  const _ob_stash=[];
  s=s.replace(/(<code\b[^>]*>[\s\S]*?<\/code>)/g,m=>{_ob_stash.push(m);return `\x00O${_ob_stash.length-1}\x00`;});
  s=s.replace(/\*\*\*(.+?)\*\*\*/g,(_,t)=>`<strong><em>${esc(t)}</em></strong>`);
  s=s.replace(/\*\*(.+?)\*\*/g,(_,t)=>`<strong>${esc(t)}</strong>`);
  s=s.replace(/\*([^*\n]+)\*/g,(_,t)=>`<em>${esc(t)}</em>`);
  s=s.replace(/~~(.+?)~~/g,(_,t)=>`<del>${esc(t)}</del>`);
  s=s.replace(/\x00O(\d+)\x00/g,(_,i)=>_ob_stash[+i]);
  s=s.replace(/^###### (.+)$/gm,(_,t)=>`<h6>${inlineMd(t)}</h6>`).replace(/^##### (.+)$/gm,(_,t)=>`<h5>${inlineMd(t)}</h5>`).replace(/^#### (.+)$/gm,(_,t)=>`<h4>${inlineMd(t)}</h4>`).replace(/^### (.+)$/gm,(_,t)=>`<h3>${inlineMd(t)}</h3>`).replace(/^## (.+)$/gm,(_,t)=>`<h2>${inlineMd(t)}</h2>`).replace(/^# (.+)$/gm,(_,t)=>`<h1>${inlineMd(t)}</h1>`);
  s=s.replace(/^---+$/gm,'<hr>');
  // (Blockquotes are handled by the pre-pass at the top of renderMd, before
  // fence_stash. The per-line passes below never see > prefixes.)
  function _renderListBlock(lines, ordered){
    const marker=ordered?'\\d+\\. ':'[-*+] ';
    let html=ordered?'<ol>':'<ul>';
    let item=null;
    const flush=()=>{
      if(!item) return;
      const body=item.parts.join('\n').trim();
      const text=body;
      let inner;
      if(!ordered && /^\[x\] /i.test(text)) inner='<span class="task-done">✅</span> '+inlineMd(text.slice(4));
      else if(!ordered && /^\[ \] /.test(text)) inner='<span class="task-todo">☐</span> '+inlineMd(text.slice(4));
      else inner=inlineMd(text);
      const valueAttr=item.value!==null?` value="${item.value}"`:'';
      const styleAttr=item.indent?` style="margin-left:16px"`:'';
      html+=`<li${valueAttr}${styleAttr}>${inner}</li>`;
      item=null;
    };
    for(const raw of lines){
      const line=String(raw||'');
      const nested=line.match(new RegExp(`^ {2,}(${marker})(.*)$`));
      if(nested){
        flush();
        item={indent:true,value:ordered?parseInt(nested[1],10):null,parts:[nested[2]]};
        continue;
      }
      const top=line.match(new RegExp(`^(?:  )?(${marker})(.*)$`));
      if(top){
        flush();
        item={indent:false,value:ordered?parseInt(top[1],10):null,parts:[top[2]]};
        continue;
      }
      if(!item) continue;
      item.parts.push(line.replace(/^ {2,}/,'').trim());
    }
    flush();
    return html+(ordered?'</ol>':'</ul>');
  }
  function _renderLists(src, ordered){
    const lines=src.split('\n');
    const out=[];
    const topRe=ordered?/^(?:  )?\d+\. /:/^(?:  )?[-*+] /;
    const nestedRe=ordered?/^ {2,}\d+\. /:/^ {2,}[-*+] /;
    const contRe=/^ {2,}\S/;
    let i=0;
    while(i<lines.length){
      if(!topRe.test(lines[i])){
        out.push(lines[i]);
        i++;
        continue;
      }
      const block=[lines[i]];
      i++;
      while(i<lines.length){
        const line=lines[i];
        if(topRe.test(line)||nestedRe.test(line)||contRe.test(line)){
          block.push(line);
          i++;
          continue;
        }
        if(!line.trim()){
          const next=lines[i+1]||'';
          if(topRe.test(next)||nestedRe.test(next)||contRe.test(next)){
            i++;
            continue;
          }
        }
        break;
      }
      out.push(_renderListBlock(block,ordered));
    }
    return out.join('\n');
  }
  // Preserve continuation lines, nested indentation, and LaTeX placeholder lines
  // inside list items without changing the wider markdown pipeline.
  s=_renderLists(s,false);
  // Ordered-list parsing intentionally runs on the post-unordered string; the
  // unordered pass emits <ul> HTML that cannot satisfy the ordered-item regex.
  // Keep continuation lines attached to their item and preserve explicit
  // numbering via value= even when blank lines split the markdown.
  s=_renderLists(s,true);
  // Tables: | col | col | header row followed by | --- | --- | separator then data rows
  // NOTE: table pass runs BEFORE outer link pass so [label](url) in table cells
  // is handled by inlineMd() only — prevents double-linking.
  s=s.replace(/((?:^ {0,3}\|.+\|[ \t]*\n?)+)/gm,block=>{
    const rows=block.trim().split('\n').filter(r=>r.trim());
    if(rows.length<2)return block;
    const isSep=r=>/^\|[\s|:-]+\|$/.test(r.trim());
    if(!isSep(rows[1]))return block;
    // _protectPipes: temporarily swap pipes inside matching bracket pairs for a
    // sentinel before split('|'), then restore. Iterates until no more matches
    // so all pipes inside one pair are caught.
    // Note: both opening and closing brace literals in the character classes
    // are written as hex escapes (\x7b and \x7d) so the JS source contains no
    // bare brace glyphs that would confuse the brace-counting extractFunc in
    // tests/test_renderer_js_behaviour.py. Regex semantics are identical.
    // Bracket set is paren / square / curly only -- NOT angle brackets, since
    // angle brackets are overwhelmingly comparison operators in real LLM table
    // output (`| x < 5 | y > 10 |`) and treating them as a pair collapses cells.
    const _protectPipes=r=>{let prev;do{prev=r;r=r.replace(/([([\x7b][^)\]\x7d]*)[|]([^)\]\x7d]*[)\]\x7d])/g,(_,a,b)=>a+'\x00PIPE\x00'+b);r=r.replace(/(<code>[^<]*)[|]([^<]*<\/code>)/g,(_,a,b)=>a+'\x00PIPE\x00'+b);}while(r!==prev);return r;};
    const _restorePipes=s=>s.replace(/\x00PIPE\x00/g,'|');
    const parseRow=r=>{r=_protectPipes(r);return r.trim().replace(/^\|/,'').replace(/\|$/,'').split('|').map(c=>`<td>${inlineMd(_restorePipes(c.trim()))}</td>`).join('');};
    const parseHeader=r=>{r=_protectPipes(r);return r.trim().replace(/^\|/,'').replace(/\|$/,'').split('|').map(c=>`<th>${inlineMd(_restorePipes(c.trim()))}</th>`).join('');};
    const header=`<tr>${parseHeader(rows[0])}</tr>`;
    const body=rows.slice(2).map(r=>`<tr>${parseRow(r)}</tr>`).join('');
    // Surround with blank lines so the final paragraph splitter treats the
    // generated table as its own block even when the regex consumes one of the
    // markdown block's trailing newlines.
    return `\n\n<table><thead>${header}</thead><tbody>${body}</tbody></table>\n\n`;
  });
  // #487: Outer image pass — handles ![alt](url) in plain paragraphs (outside tables/lists).
  // Runs AFTER the table pass (images in table cells are handled by inlineMd() above).
  // Runs BEFORE the outer [label](url) link pass so the image is not consumed as a plain link.
  s=s.replace(/!\[([^\]]*)\]\(((?:https?:\/\/|file:\/\/|data:image\/)[^\)]+)\)/g,(_,alt,url)=>(typeof _mdImageHtml==='function')?_mdImageHtml(alt,url):`<img src="${url.replace(/"/g,'%22')}" alt="${esc(alt)}" class="msg-media-img" loading="lazy">`);
  // Outer link pass for labeled links in plain paragraphs (outside table cells).
  // Runs AFTER the table pass so table cells are processed by inlineMd() only.
  // Stash existing <a> tags first to avoid re-linking already-linked URLs.
  const _a_stash=[];
  s=s.replace(/(<a\b[^>]*>[\s\S]*?<\/a>)/g,m=>{_a_stash.push(m);return `\x00A${_a_stash.length-1}\x00`;});
  s=s.replace(/\[([^\]]+)\]\(((?:https?:\/\/|file:\/\/|workspace:\/\/|session:\/\/|vault:\/\/|vault-insert:\/\/|vault-create:\/\/|vault-weave:\/\/|vault-search:\/\/|deepmode:\/\/|#deepmode=|mailto:|tel:|message:)[^\s\)]+)\)/g,(_,label,url)=>_markdownAnchor(label,url));
  s=s.replace(/\x00A(\d+)\x00/g,(_,i)=>_a_stash[+i]);
  // Restore raw <pre> only after markdown rewrites so literal preformatted
  // content stays placeholder-protected, then let the sanitizer normalize tags.
  s=s.replace(/\x00R(\d+)\x00/g,(_,i)=>rawPreStash[+i]);
  // Sanitize any remaining HTML tags.  The renderer intentionally returns
  // HTML and inserts it with innerHTML later, so tag names alone are not enough:
  // raw/model-provided HTML like <img onerror=...> or <a href="javascript:...">
  // must lose executable attributes and dangerous schemes while preserving the
  // small set of attributes generated by this markdown pipeline.
  // Reference only — documents the allowed tag set. Superseded by _tag() allowlists.
  // Tests verify this list is complete; _tag() enforces it.
  const SAFE_TAGS=/^<\/?(?:strong|em|del|code|pre|h[1-6]|ul|ol|li|table|thead|tbody|tr|th|td|hr|blockquote|p|br|a|div|span|img)([\s>]|$)/i;
  function _safeAttrValue(v){
    return String(v||'').replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&amp;/g,'&').trim();
  }
  function _markdownHref(raw){
    const href=String(raw||'').replace(/"/g,'%22');
    if(/^session:\/\//i.test(href)){
      const sid=href.replace(/^session:\/\//i,'').split(/[?#]/)[0];
      try{
        const decoded=decodeURIComponent(sid);
        if(typeof _sessionUrlForSid==='function') return _sessionUrlForSid(decoded);
        return 'session/'+encodeURIComponent(decoded);
      }catch(_){
        return 'session/'+encodeURIComponent(sid);
      }
    }
    if(/^workspace:\/\//i.test(href)){
      try{
        const rel=decodeURIComponent(href.replace(/^workspace:\/\//i,'')).replace(/^~\//,'').replace(/^\.\//,'');
        return '#workspace='+encodeURIComponent(rel);
      }catch(_){
        return '#';
      }
    }
    if(/^vault:\/\//i.test(href)){
      try{
        const rel=decodeURIComponent(href.replace(/^vault:\/\//i,'')).replace(/^knowledge\//,'');
        return '#vault='+encodeURIComponent(rel);
      }catch(_){
        return '#';
      }
    }
    if(/^vault-insert:\/\//i.test(href)){
      try{
        const payload=decodeURIComponent(href.replace(/^vault-insert:\/\//i,''));
        return '#vault-insert='+encodeURIComponent(payload);
      }catch(_){
        return '#';
      }
    }
    if(/^vault-create:\/\//i.test(href)){
      try{
        const payload=decodeURIComponent(href.replace(/^vault-create:\/\//i,''));
        return '#vault-create='+encodeURIComponent(payload);
      }catch(_){
        return '#';
      }
    }
    if(/^vault-weave:\/\//i.test(href)){
      try{
        const payload=decodeURIComponent(href.replace(/^vault-weave:\/\//i,''));
        return '#vault-weave='+encodeURIComponent(payload);
      }catch(_){
        return '#';
      }
    }
    if(/^vault-search:\/\//i.test(href)){
      try{
        const payload=decodeURIComponent(href.replace(/^vault-search:\/\//i,''));
        return '#vault-search='+encodeURIComponent(payload);
      }catch(_){
        return '#';
      }
    }
    if(/^deepmode:\/\//i.test(href)){
      try{
        const payload=decodeURIComponent(href.replace(/^deepmode:\/\//i,''));
        return '#deepmode='+encodeURIComponent(payload);
      }catch(_){
        return '#';
      }
    }
    if(/^file:\/\//i.test(href)){
      try{
        const path=decodeURIComponent(href.replace(/^file:\/\//i,''));
        return 'api/media?path='+encodeURIComponent(path)+'&inline=1';
      }catch(_){
        return 'api/media?path='+encodeURIComponent(href.replace(/^file:\/\//i,''))+'&inline=1';
      }
    }
    return href;
  }
  function _isInternalSessionHref(raw){
    const href=String(raw||'').trim();
    if(/^session\/[^?#]+/i.test(href)) return true;
    try{
      const base=(typeof document!=='undefined'&&document.baseURI)||
        (typeof window!=='undefined'&&window.location&&window.location.href)||
        'http://localhost/';
      const url=new URL(href,base);
      const baseUrl=new URL(base,base);
      if(url.origin!==baseUrl.origin) return false;
      const basePath=baseUrl.pathname.replace(/(?:index\.html)?$/,'').replace(/\/[^/]*$/,'/');
      const root=basePath.endsWith('/')?basePath:basePath+'/';
      return url.pathname.startsWith(root+'session/')||url.pathname.startsWith('/session/');
    }catch(_){
      return false;
    }
  }
  function _isSafeLabelInline(tag){
    return /^<\/?(strong|em|del|code)([\s>]|$)/i.test(tag);
  }
  function _markdownLabelHtml(label){
    const _label_stash=[];
    const tokenized=String(label||'').replace(/<\/?[a-z][^>]*>/gi,tag=>{
      if(!_isSafeLabelInline(tag)) return tag;
      _label_stash.push(tag);
      return `\x00H${_label_stash.length-1}\x00`;
    });
    return esc(tokenized).replace(/\x00H(\d+)\x00/g,(_,i)=>_label_stash[+i]);
  }
  function _markdownAnchor(label,rawUrl){
    const href=_markdownHref(rawUrl);
    const internal=/^session:\/\//i.test(String(rawUrl||'')) || _isInternalSessionHref(href);
    return `<a${internal?' class="session-link"':''} href="${href}"${internal?'':' target="_blank" rel="noopener"'}>${_markdownLabelHtml(label)}</a>`;
  }
  function _isSafeUrl(v, img){
    const raw=_safeAttrValue(v);
    const compact=raw.replace(/[\u0000-\u001f\u007f\s]+/g,'').toLowerCase();
    if(!compact) return false;
    // data:image/* is permitted for <img> only, validated by the shared strict
    // predicate. Every other
    // data: scheme stays blocked for both anchors and images.
    if(/^data:/i.test(compact)) return !!(img && typeof _isSafeDataImageUri==='function' && _isSafeDataImageUri(raw));
    if(/^(javascript|vbscript):/i.test(compact)) return false;
    if(/^https?:\/\//i.test(raw)) return true;
    if(/^(mailto:|tel:|message:)/i.test(raw)) return true;
    if(img && /^api\//i.test(raw)) return true;
    if(!img && (/^api\//i.test(raw) || /^#/.test(raw) || _isInternalSessionHref(raw))) return true;
    return false;
  }
  function _attrs(raw){
    const out={};
    String(raw||'').replace(/([a-zA-Z0-9:_-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>`]+)))?/g,(_,k,dq,sq,bare)=>{
      out[String(k).toLowerCase()]=dq!==undefined?dq:(sq!==undefined?sq:(bare!==undefined?bare:''));
      return '';
    });
    return out;
  }
  function _cls(v, allowed){
    const got=String(v||'').split(/\s+/).filter(c=>allowed.includes(c));
    return got.length?` class="${esc(got.join(' '))}"`:'';
  }
  function _tag(tag){
    const m=String(tag||'').match(/^<\s*(\/)?\s*([a-zA-Z][\w:-]*)([\s\S]*?)(\/)?\s*>$/);
    if(!m) return esc(tag);
    const closing=!!m[1];
    const name=m[2].toLowerCase();
    const rawAttrs=m[3]||'';
    const plain=['strong','em','del','pre','h1','h2','h3','h4','h5','h6','ul','ol','table','thead','tbody','tr','th','td','blockquote','p','br','hr'];
    if(closing) return plain.includes(name)||['a','div','span','li','code'].includes(name)?`</${name}>`:'';
    if(name==='code'){
      const a=_attrs(rawAttrs);
      const cls=/^language-[a-z0-9_+-]+$/i.test(a.class||'')?` class="${esc(a.class)}"`:'';
      return `<code${cls}>`;
    }
    if(plain.includes(name)) return `<${name}>`;
    const a=_attrs(rawAttrs);
    if(name==='li'){
      const value=/^\d+$/.test(a.value||'')?` value="${esc(a.value)}"`:'';
      const style=(a.style||'').replace(/\s+/g,'').toLowerCase()==='margin-left:16px'?` style="margin-left:16px"`:'';
      return `<li${value}${style}>`;
    }
    if(name==='span'){
      return `<span${_cls(a.class,['task-done','task-todo','katex-inline'])}${a['data-katex']==='inline'?' data-katex="inline"':''}>`;
    }
    if(name==='div'){
      const cls=_cls(a.class,['pre-header','mermaid-block','katex-block']);
      const mermaid=a['data-mermaid-id']?` data-mermaid-id="${esc(a['data-mermaid-id'])}"`:'';
      const katex=a['data-katex']==='display'?' data-katex="display"':'';
      return `<div${cls}${mermaid}${katex}>`;
    }
    if(name==='a'){
      if(!_isSafeUrl(a.href,false)) return '<a>';
      const target=a.target==='_blank'?' target="_blank"':'';
      const rel=a.rel==='noopener'?' rel="noopener"':'';
      const cls=_cls(a.class,['msg-media-link','skill-linked-file','skill-file-back','session-link']);
      const download=a.download?` download="${esc(a.download)}"`:'';
      return `<a${cls} href="${esc(_safeAttrValue(a.href))}"${target}${rel}${download}>`;
    }
    if(name==='img'){
      if(!_isSafeUrl(a.src,true)) return '';
      const cls=_cls(a.class,['msg-media-img']);
      const alt=` alt="${esc(_safeAttrValue(a.alt||''))}"`;
      const loading=a.loading==='lazy'?' loading="lazy"':'';
      return `<img${cls} src="${esc(_safeAttrValue(a.src))}"${alt}${loading}>`;
    }
    return '';
  }
  s=s.replace(/<\/?[a-z][^>]*>/gi,tag=>_tag(tag));
  // Incomplete raw tags must not survive until paragraph wrapping, where the
  // renderer's generated </p> could provide a closing ">" and turn them into
  // executable HTML in innerHTML (for example: <img src=x onerror=...//).
  s=s.replace(/<[a-zA-Z][\w:-]*[^>\n]*$/gm,tag=>esc(tag));
  // Autolink: convert plain URLs to clickable links.
  // Stash <a>, <img> and <pre> blocks so autolink never runs inside them.
  const _al_stash=[];
  s=s.replace(/(<a\b[^>]*>[\s\S]*?<\/a>|<img\b[^>]*>|<pre\b[^>]*>[\s\S]*?<\/pre>)/g,m=>{_al_stash.push(m);return `\x00B${_al_stash.length-1}\x00`;});
  s=s.replace(/(https?:\/\/[^\s<>"')\]\uFF09]+)/g,(url)=>{
    // Strip trailing punctuation that was likely not part of the URL.
    // CJK full-width punctuation (）。，；：！？、) is included because LLMs
    // frequently use full-width delimiters in Chinese/Japanese text.
    const trail=url.match(/[.,;:!?)]$/)||url.match(/[\uFF09\uFF0C\uFF1B\uFF1A\uFF01\uFF1F\u3001\u3002]$/)?url.slice(-1):'';
    const clean=trail?url.slice(0,-1):url;
    return `<a href="${clean}" target="_blank" rel="noopener">${esc(clean)}</a>${trail}`;
  });
  s=s.replace(/\x00B(\d+)\x00/g,(_,i)=>_al_stash[+i]);
  // Restore math stash → katex placeholder spans/divs
  // These will be rendered by renderKatexBlocks() after DOM insertion
  s=s.replace(/\x00M(\d+)\x00/g,(_,i)=>{
    const item=math_stash[+i];
    if(item.type==='display'){
      return `<div class="katex-block" data-katex="display">${esc(item.src)}</div>`;
    }
    return `<span class="katex-inline" data-katex="inline">${esc(item.src)}</span>`;
  });
  // Restore fenced block stash (\x00P) → <pre><code> HTML.
  // Happens AFTER all markdown passes (lists, headings, tables, etc.) so
  // diff/patch content inside code blocks is never misinterpreted as markdown.
  // The _pre_stash below then protects these blocks from paragraph splitting.
  s=s.replace(/\x00P(\d+)\x00/g,(_,i)=>_preBlock_stash[+i]);
  // Stash rendered <pre> blocks (with optional pre-header div) and mermaid/katex
  // divs before paragraph splitting so \n inside code blocks is never replaced
  // with <br>. Token \x00E (next free after B D F G L M C O A).
  // Fixes #745: code blocks collapse to single line when not preceded by blank line.
  const _pre_stash=[];
  // #1463 / #1618: regex must match <pre> with ANY attributes — PR #484 added
  // <pre class="tree-raw-view"> for JSON/YAML and <pre class="diff-block"> for
  // diff/patch which the literal-<pre> shape missed. Newlines inside those
  // blocks were falling through to the paragraph wrap below and getting
  // converted to <br>, causing the YAML/JSON/diff collapse. PR #1516's CSS
  // fix targeted the wrong layer (Prism token white-space) — by the time it
  // ran, the \n had already been replaced. The CSS rule is kept as defense
  // in depth.
  s=s.replace(/(<div class="pre-header">[\s\S]*?<\/div>)?<pre[^>]*>[\s\S]*?<\/pre>|<div class="(mermaid-block|katex-block)"[\s\S]*?<\/div>/g,m=>{
    _pre_stash.push(m);
    return '\x00E'+(_pre_stash.length-1)+'\x00';
  });
  const parts=s.split(/\n{2,}/);
  s=parts.map(p=>{p=p.trim();if(!p)return '';if(/^<(h[1-6]|ul|ol|table|pre|hr|blockquote)|^\x00[EQ]/.test(p))return p;return `<p>${p.replace(/\n/g,'<br>')}</p>`;}).join('\n');
  s=s.replace(/\x00E(\d+)\x00/g,(_,i)=>_pre_stash[+i]);
  // ── Restore MEDIA stash → inline images or download links ─────────────────
  s=s.replace(/\x00D(\d+)\x00/g,(_,i)=>_inlineMediaHtmlForRef(media_stash[+i]));

  // ── End MEDIA restore ──────────────────────────────────────────────────────
  // Restore blockquote stash. Done last so the inner HTML (already produced
  // by the recursive renderMd in the pre-pass) is dropped into the final
  // string verbatim — no further passes can mangle it.
  s=s.replace(/\x00Q(\d+)\x00/g,(_,i)=>_bq_stash[+i]);
  return s;
}

function _stripAttachedFilesMarkerForDisplay(text){
  return String(text||'').replace(/\n\n\[Attached files: [^\]]+\]$/,'').trim();
}

function setStatus(t){
  if(!t)return;
  showToast(t, 4000);
}

// ── Chat composer actions, status, and busy state extracted to ui-composer.js ──
// setComposerStatus, lockComposerForClarify, unlockComposerForClarify,
// getComposerPrimaryAction, updateSendBtn, handleComposerPrimaryAction, and
// setBusy are centralized in webui/static/ui-composer.js.


// ── Queue chip display (Codex Desktop pattern) ─────────────────────────────
// Queued messages appear as chips inside #queueChips (above the textarea)
// while pending. When the session fires the queued message it becomes a
// normal user bubble in the chat — the chip is removed at drain time.
const _queueRenderKeys={};  // per-session fingerprint to avoid redundant rebuilds
const _queueCollapsed={};   // per-session: true when user explicitly collapsed the card
let _queueRenderEpoch=0;
function _clearQueueCardDisplay(sid){
  const card=document.getElementById('queueCard');
  const chips=document.getElementById('queueChips');
  if(sid) delete _queueRenderKeys[sid];
  if(card) card.classList.remove('visible');
  if(chips){
    const _chips=chips;
    const _card=card;
    const _sid=String(sid||'');
    const _epoch=_chips.getAttribute('data-queue-render-epoch')||'';
    setTimeout(()=>{
      if((_card&&!_card.classList.contains('visible'))||!_card){
        if((_chips.getAttribute('data-queue-render-sid')||'')===_sid&&(_chips.getAttribute('data-queue-render-epoch')||'')===_epoch){
          _chips.innerHTML='';
        }
      }
    },360);
  }
  const _msgsEl=document.getElementById('messages');
  if(_msgsEl) _msgsEl.classList.remove('queue-open');
  _updateQueuePill(sid,0);
}

function _renderQueueChips(sid){
  const card=document.getElementById('queueCard');
  const inner=document.getElementById('queueChips');
  if(!card||!inner) return;
  const q=_getSessionQueue(sid,false);
  const key=q.map(e=>{const t=e&&(e.text||e.message||e.content||'');return(e&&e._queued_at||0)+':'+t.length+':'+t.slice(0,20);}).join('|');
  if(key===(_queueRenderKeys[sid]||'')&&key!='') return;
  // Skip re-render if user is actively editing inside the queue panel
  if(inner.contains(document.activeElement)&&document.activeElement!==inner) return;
  _queueRenderKeys[sid]=key;
  inner.setAttribute('data-queue-render-sid',sid);
  inner.setAttribute('data-queue-render-epoch',String(++_queueRenderEpoch));
  inner.innerHTML='';
  if(!q.length){
    card.classList.remove('visible');
    const _msgs=document.getElementById('messages');
    if(_msgs) _msgs.classList.remove('queue-open');
    return;
  }
  // Respect user-collapsed state — don't reopen if user explicitly hid the card
  if(_queueCollapsed[sid]){
    // Update chips content without showing card (so data is fresh if user re-expands)
    inner.innerHTML='';
    // fall through to render rows into inner but skip making card visible
  } else {
    card.classList.add('visible');
  }
  // Push messages area up so content isn't hidden behind the flyout
  const _msgs=document.getElementById('messages');
  if(_msgs&&!_queueCollapsed[sid]){
    _msgs.classList.add('queue-open');
    // Measure after 350ms transition completes (not mid-animation — height would be wrong)
    setTimeout(()=>{
      if(!card.classList.contains('visible')) return;
      const h=card.getBoundingClientRect().height;
      if(h>0) _msgs.style.setProperty('--queue-card-height', h+'px');
      if(typeof scrollIfPinned==='function') scrollIfPinned();
    }, 360);
  }

  function _saveAndRefresh(){
    const liveQ=_getSessionQueue(sid,false);
    if(!liveQ.length){delete SESSION_QUEUES[sid];_clearPersistedSessionQueue(sid);}
    else{SESSION_QUEUES[sid]=[...liveQ];_persistSessionQueueStorage(sid,liveQ);}
    delete _queueRenderKeys[sid];
    updateQueueBadge(sid);
  }

  // Header (2+ items)
  if(q.length>1){
    const header=document.createElement('div');
    header.className='queue-card-header';
    const lbl=document.createElement('span');
    lbl.textContent=typeof t==='function'?t('queued_count',q.length):(q.length===1?'1 queued':`${q.length} queued`);
    lbl.title='Sends automatically after the current response completes';
    const actions=document.createElement('span');
    actions.className='queue-card-header-actions';
    const hasFiles=q.some(e=>e&&Array.isArray(e.files)&&e.files.length>0);
    const mergeBtn=document.createElement('button');
    mergeBtn.className='queue-card-btn';
    mergeBtn.title='Combine all into one message'+(hasFiles?' — attachments will be removed':'');
    mergeBtn.innerHTML=li('layers',12)+'Combine';
    mergeBtn.onclick=()=>{
      const _doMerge=(snapshot)=>{
        const combined=snapshot.map(e=>e&&(e.text||e.message||e.content||'')).filter(Boolean).join('\n\n');
        const liveQ=_getSessionQueue(sid,false);
        const first=snapshot.find(e=>e)||{};
        const firstFiles=(snapshot.find(e=>e&&Array.isArray(e.files)&&e.files.length)||{files:[]}).files;
        liveQ.length=0;liveQ.push({text:combined,files:firstFiles,model:first.model||'',model_provider:first.model_provider||null,_queued_at:Date.now()});
        SESSION_QUEUES[sid]=liveQ;
        _persistSessionQueueStorage(sid,liveQ);
        delete _queueRenderKeys[sid];
        updateQueueBadge(sid);
      };
      if(hasFiles){
        if(typeof showToast==='function') showToast('Attachments on queued items will be removed',2600,'warning');
      }
      // Merge from current live queue (no delay — snapshot + defer caused data-loss races)
      _doMerge([..._getSessionQueue(sid,false)]);
    };
    const clearBtn=document.createElement('button');
    clearBtn.className='queue-card-icon-btn';
    clearBtn.title='Clear all queued messages';
    clearBtn.setAttribute('aria-label','Clear all queued messages');
    clearBtn.innerHTML=li('x',13);
    clearBtn.onclick=()=>{q.length=0;_saveAndRefresh();};
    actions.appendChild(mergeBtn);
    actions.appendChild(clearBtn);
    // Hide button — collapses flyout entirely; queue pill re-shows it
    const hideBtn=document.createElement('button');
    hideBtn.className='queue-card-icon-btn';
    hideBtn.title='Hide queue (click the queue pill to show again)';
    hideBtn.setAttribute('aria-label','Hide queue panel');
    hideBtn.innerHTML=li('chevron-down',14);
    hideBtn.onclick=()=>{
      _queueCollapsed[sid]=true;
      card.classList.remove('visible');
      // Read live count at click time (not stale closure q)
      _updateQueuePill(sid,_getSessionQueue(sid,false).length);
    };
    actions.appendChild(hideBtn);
    header.appendChild(lbl);
    header.appendChild(actions);
    inner.appendChild(header);
  }

  let _dragTs=null;  // use _queued_at timestamp — survives re-renders, not an index
  q.forEach((entry,i)=>{
    const _entryTs=entry&&entry._queued_at;
    const entryText=entry&&(entry.text||entry.message||entry.content||'');
    const _files=entry&&Array.isArray(entry.files)?entry.files.filter(Boolean):[];
    const row=document.createElement('div');
    row.className='queue-card-row';
    row.setAttribute('role','listitem');
    row.setAttribute('draggable','true');
    row.ondragstart=(e)=>{if(_entryTs==null) return;_dragTs=_entryTs;row.style.opacity='.4';e.dataTransfer.effectAllowed='move';};
    row.ondragend=()=>{row.style.opacity='';};
    row.ondragover=(e)=>{e.preventDefault();row.style.background='var(--hover-bg)';};
    row.ondragleave=()=>{row.style.background='';};
    row.ondrop=(e)=>{
      e.preventDefault();row.style.background='';
      if(_dragTs!=null&&_dragTs!==_entryTs){
        const fromIdx=q.findIndex(e=>e&&e._queued_at===_dragTs);
        if(fromIdx!==-1&&fromIdx!==i){const moved=q.splice(fromIdx,1)[0];q.splice(i,0,moved);}
        _dragTs=null;_saveAndRefresh();
      }
    };
    // Drag handle
    const drag=document.createElement('span');
    drag.className='queue-card-drag';
    drag.setAttribute('aria-hidden','true');
    drag.innerHTML=typeof li==='function'?li('list-todo',13):'≡';
    // Inline-editable text
    const msgSpan=document.createElement('span');
    msgSpan.className='queue-card-text';
    msgSpan.setAttribute('contenteditable','true');
    msgSpan.setAttribute('role','textbox');
    msgSpan.setAttribute('aria-label','Queued message — edit in place');
    msgSpan.textContent=entryText||(_files.length?'':'—');
    msgSpan.setAttribute('draggable','false');
    msgSpan.onfocus=()=>{msgSpan.style.overflow='auto';msgSpan.style.whiteSpace='pre-wrap';msgSpan.style.textOverflow='clip';};
    msgSpan.onblur=()=>{
      msgSpan.style.overflow='';msgSpan.style.whiteSpace='';msgSpan.style.textOverflow='';
      const newText=msgSpan.textContent.trim();
      if(newText===''&&!_files.length){ msgSpan.textContent=entryText||'—'; return; }
      if(newText!==entryText){
        const liveQ=_getSessionQueue(sid,false);
        const idx=_entryTs!=null?liveQ.findIndex(e=>e&&e._queued_at===_entryTs):i;
        if(idx!==-1){
          liveQ[idx]={...liveQ[idx],text:newText};
          _persistSessionQueueStorage(sid,liveQ);
          delete _queueRenderKeys[sid];
          updateQueueBadge(sid);
        }
      }
    };
    msgSpan.onkeydown=(e)=>{if(e.key==='Enter'){e.preventDefault();msgSpan.blur();}if(e.key==='Escape'){msgSpan.textContent=entryText||'—';msgSpan.blur();}};
    // Compact badges (files, model, profile)
    const badges=document.createElement('span');
    badges.className='queue-card-badges';
    if(_files.length>0){
      const fb=document.createElement('span');
      fb.className='queue-card-file-badge';
      fb.title=_files.map(f=>f&&f.name||'file').join(', ');
      fb.innerHTML=li('paperclip',11)+_files.length;
      badges.appendChild(fb);
    }
    const _model=entry&&entry.model;
    if(_model){
      const mb=document.createElement('span');
      mb.title='Model: '+_model;
      // Use the app's friendly label system if available
      const _modelLabel=(typeof _dynamicModelLabels!=='undefined'&&_dynamicModelLabels[_model])
        ||_model.split('/').pop().replace(/^(gpt-|claude-3\.?5?-|claude-|gemini-)/,'').replace(/-\d{4}-\d{2}-\d{2}$/,'').slice(0,12);
      mb.textContent=_modelLabel;
      badges.appendChild(mb);
    }
    // Profile badge removed — drain cannot server-switch profiles so badge was misleading
    // Delete button
    const delBtn=document.createElement('button');
    delBtn.className='queue-card-icon-btn';
    delBtn.setAttribute('aria-label',typeof t==='function'?t('queued_cancel'):'Remove queued message');
    delBtn.setAttribute('draggable','false');
    delBtn.title='Remove from queue';
    delBtn.innerHTML=li('x',13);
    delBtn.onclick=()=>{
      const liveQ=_getSessionQueue(sid,false);
      const idx=_entryTs!=null?liveQ.findIndex(e=>e&&e._queued_at===_entryTs):i;
      if(idx!==-1) liveQ.splice(idx,1);
      if(!liveQ.length){delete SESSION_QUEUES[sid];_clearPersistedSessionQueue(sid);}
      else{SESSION_QUEUES[sid]=[...liveQ];_persistSessionQueueStorage(sid,liveQ);}
      delete _queueRenderKeys[sid];
      updateQueueBadge(sid);
    };
    row.appendChild(drag);
    row.appendChild(msgSpan);
    if(badges.childNodes.length) row.appendChild(badges);
    row.appendChild(delBtn);
    inner.appendChild(row);
  });
}

function _updateQueuePill(sid,count){
  const pill=document.getElementById('queuePill');
  if(!pill) return;
  const pillOuter=pill.parentElement;  // .queue-pill-outer — same wrapper as .queue-card
  const card=document.getElementById('queueCard');
  const flyoutVisible=card&&card.classList.contains('visible');
  if(count>0&&!flyoutVisible){
    const label=typeof t==='function'?t('queued_count',count):(count===1?'1 queued':`${count} queued`);
    pill.innerHTML=(typeof li==='function'?li('list-todo',12):'')+
      `<span class="queue-pill-count">${label}</span>`+
      `<span class="queue-pill-chevron">`+(typeof li==='function'?li('chevron-up',12):'▲')+`</span>`;
    pill.title='Show queued messages';
    if(pillOuter) pillOuter.classList.add('show');
    pill.onclick=()=>{
      delete _queueCollapsed[sid];
      const c=document.getElementById('queueCard');
      if(c){
        c.classList.add('visible');
        setTimeout(()=>{
          const firstFocusable=c.querySelector('.queue-card-text, .queue-card-icon-btn');
          if(firstFocusable) firstFocusable.focus();
        }, 360);
      }
      if(pillOuter) pillOuter.classList.remove('show');
      if(typeof scrollIfPinned==='function') scrollIfPinned();
    };
  } else {
    if(pillOuter) pillOuter.classList.remove('show');
    pill.onclick=null;
  }
}

function updateQueueBadge(sessionId){
  const sid=sessionId||(S.session&&S.session.session_id);
  const count=sid?getQueuedSessionCount(sid):0;
  if(count>0&&S.session&&sid===S.session.session_id){
    _renderQueueChips(sid);
    // If card is visible, hide pill. If card is collapsed, update pill count.
    const _cardEl=document.getElementById('queueCard');
    _updateQueuePill(sid,(_cardEl&&_cardEl.classList.contains('visible'))?0:count);
  } else {
    // Always clean up per-session data
    if(sid){delete _queueRenderKeys[sid];delete _queueCollapsed[sid];}
    // Only wipe global DOM if this is the currently active session
    const isActive=S.session&&sid===S.session.session_id;
    if(isActive){
      _clearQueueCardDisplay(sid);
    }
  }
}
// ── Shared notifications, toasts, and dialogs extracted to ui-notifications.js ──
// TOAST_DEFAULT_MS, TOAST_ERROR_DEFAULT_MS, showToast, dismissToast, copyToastText,
// clearToastDismissTimer, setToastDismissTimer, showConfirmDialog, showPromptDialog,
// and showAlertDialog are centralized in webui/static/ui-notifications.js.



function _copyText(text){
  if(navigator.clipboard && window.isSecureContext){
    return navigator.clipboard.writeText(text).catch(()=>{
      // Fallback if clipboard API fails (e.g. permissions)
      return _fallbackCopy(text);
    });
  }
  return _fallbackCopy(text);
}
function _fallbackCopy(text){
  return new Promise((resolve,reject)=>{
    const ta=document.createElement('textarea');
    ta.value=text;ta.style.cssText='position:fixed;left:0;top:0;width:2em;height:2em;padding:0;border:none;outline:none;box-shadow:none;background:transparent;z-index:-1';
    document.body.appendChild(ta);
    ta.focus();ta.select();
    try{document.execCommand('copy');resolve();}
    catch(e){reject(e);}
    finally{document.body.removeChild(ta);}
  });
}
function copyStatusSessionId(btn){
  const text=btn&&btn.getAttribute('data-copy-status-session');
  if(!text)return;
  _copyText(text).then(()=>{
    const orig=btn.innerHTML;
    btn.innerHTML=(typeof li==='function')?li('check',13):t('copied');
    btn.classList.add('copied');
    setTimeout(()=>{btn.innerHTML=orig;btn.classList.remove('copied');},1500);
  }).catch(()=>showToast(t('copy_failed')));
}
function copyMsg(btn){
  const row=btn.closest('[data-raw-text]');
  const text=row?row.dataset.rawText:'';
  if(!text)return;
  _copyText(text).then(()=>{
    const orig=btn.innerHTML;btn.innerHTML=li('check',13);btn.style.color='var(--blue)';
    setTimeout(()=>{btn.innerHTML=orig;btn.style.color='';},1500);
  }).catch(()=>showToast(t('copy_failed')));
}

async function memorizeMessage(btn){
  if(!btn) return;
  const row=btn.closest('[data-raw-text]');
  let text=row?row.dataset.rawText:'';
  if(!text){
    const msgRow=btn.closest('.msg-row');
    const body=msgRow?msgRow.querySelector('.msg-body'):null;
    text=body?body.innerText:'';
  }
  text=(text||'').trim();
  if(!text){
    showToast('No message content to memorize', 2500, 'warning');
    return;
  }
  const orig=btn.innerHTML;
  btn.innerHTML=li('loader',13);
  btn.classList.add('loading');
  try {
    const data=await apiFetch('/api/vault/memorize', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text})
    });
    if(data.ok){
      btn.innerHTML=li('check',13);
      btn.style.color='var(--accent, #10b981)';
      showToast(`🧠 Memorized: ${data.title||data.rel_path}`, 4000, 'success');
      if(typeof loadVault==='function'){
        loadVault().catch(()=>{});
      }
      setTimeout(()=>{
        btn.innerHTML=orig;
        btn.style.color='';
        btn.classList.remove('loading');
      }, 2000);
    } else {
      throw new Error(data.error||'Failed to memorize note');
    }
  } catch(err){
    btn.innerHTML=orig;
    btn.classList.remove('loading');
    showToast(`⚠️ Memorize error: ${err.message}`, 4000, 'error');
  }
}
function _copyThinkingText(btn){
  const card=btn&&btn.closest?btn.closest('.thinking-card'):null;
  if(!card)return;
  const pre=card.querySelector('.thinking-card-body pre');
  const text=pre?pre.textContent:'';
  if(!text)return;
  _copyText(text).then(()=>{
    const orig=btn.innerHTML;
    btn.innerHTML=li('check',12);
    btn.style.color='var(--accent)';
    setTimeout(()=>{btn.innerHTML=orig;btn.style.color='';},1500);
  }).catch(()=>showToast(t('copy_failed')));
}

// TTS (speakMessage, stopTTS, autoReadLastAssistant) → ui-tts.js

// ── Edit + Regenerate ──

function editMessage(btn) {
  if(S.busy) return;
  const row = btn.closest('[data-msg-idx]');
  if(!row) return;
  const msgIdx = parseInt(row.dataset.msgIdx, 10);
  const originalText = row.dataset.rawText || '';
  const body = row.querySelector('.msg-body');
  if(!body || row.dataset.editing) return;
  row.dataset.editing = '1';

  // Replace msg-body with an editable textarea
  const ta = document.createElement('textarea');
  ta.className = 'msg-edit-area';
  ta.value = originalText;
  body.replaceWith(ta);
  // Resize after DOM insertion so scrollHeight is correct
  requestAnimationFrame(() => { autoResizeTextarea(ta); ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); });
  ta.addEventListener('input', () => autoResizeTextarea(ta));

  // Action bar below the textarea
  const bar = document.createElement('div');
  bar.className = 'msg-edit-bar';
  bar.innerHTML = `<button class="msg-edit-send">Send edit</button><button class="msg-edit-cancel">Cancel</button>`;
  ta.after(bar);

  bar.querySelector('.msg-edit-send').onclick = async () => {
    const newText = ta.value.trim();
    if(!newText) return;
    await submitEdit(msgIdx, newText);
  };
  bar.querySelector('.msg-edit-cancel').onclick = () => cancelEdit(row, originalText, body);

  ta.addEventListener('keydown', e => {
    if(e.key==='Enter' && !e.shiftKey) { if(window._isImeEnter&&window._isImeEnter(e)) return; e.preventDefault(); bar.querySelector('.msg-edit-send').click(); }
    if(e.key==='Escape') { e.preventDefault(); cancelEdit(row, originalText, body); }
  });
}

function cancelEdit(row, originalText, originalBody) {
  delete row.dataset.editing;
  const ta = row.querySelector('.msg-edit-area');
  const bar = row.querySelector('.msg-edit-bar');
  if(ta) ta.replaceWith(originalBody);
  if(bar) bar.remove();
}

// autoResizeTextarea is centralized in webui/static/ui-composer.js


async function submitEdit(msgIdx, newText) {
  if(!S.session || S.busy) return;
  const initialSid = S.session.session_id;
  const absoluteKeepCount = _oldestIdx + msgIdx;
  // #5924: capture the deliberate-pick signal up front (pre-network), scoped to
  // initialSid — a non-default session model (vs profile default), which is
  // inference-free and survives the failed send's marker consumption. See
  // _deliberateSessionModelPick. null → no re-arm → server resolution runs.
  const _recoveryPick=_deliberateSessionModelPick(initialSid);
  if(typeof _ensureAllMessagesLoaded==='function'){
    await _ensureAllMessagesLoaded();
  }
  if(!S.session || S.session.session_id !== initialSid) return;
  try {
    await api('/api/session/truncate', {method:'POST', body:JSON.stringify({
      session_id: initialSid,
      keep_count: absoluteKeepCount
    })});
    // #5924 SILENT-race guard: a session switch during the truncate await must not
    // let this recovery apply session A's intent (truncate/re-arm/send) to the
    // newly-visible session.
    if(!S.session || S.session.session_id !== initialSid) return;
    S.messages = S.messages.slice(0, absoluteKeepCount);
    renderMessages();
    $('msg').value = newText;
    // #5924 (Facet 1 + Facet 4): edit-resubmit is a recovery send. Re-arm the
    // Re-arm the single-shot explicit-pick marker from the captured non-default
    // pick — only if still safe at fire time (session unchanged, current model
    // still matches, no newer onchange marker to clobber). See _reArmRecoveryPick.
    _reArmRecoveryPick(initialSid, _recoveryPick);
    await send();
  } catch(e) { setStatus(t('edit_failed') + e.message); }
}

async function regenerateResponse(btn) {
  if(!S.session || S.busy) return;
  const row=btn&&btn.closest&&btn.closest('[data-msg-idx]');
  if(!row)return;
  const clickedAbsoluteIndex=_oldestIdx+parseInt(row.dataset.msgIdx,10);
  const initialSid = S.session.session_id;
  if(typeof _ensureAllMessagesLoaded==='function'){
    await _ensureAllMessagesLoaded();
  }
  if(!S.session || S.session.session_id !== initialSid) return;
  if(!S.session.regeneration_revision){ setStatus(t('regen_failed')); return; }
  let latestAssistantIndex=-1;
  for(let i=S.messages.length-1;i>=0;i--){
    if(S.messages[i]?.role==='assistant'){latestAssistantIndex=i;break;}
  }
  if(clickedAbsoluteIndex!==latestAssistantIndex){
    setStatus(t('regen_failed'));
    return;
  }
  try {
    await startRegeneration(initialSid, S.session.regeneration_revision);
  } catch(e) { setStatus(t('regen_failed') + e.message); }
}

// postProcessRenderedMessages() runs one frame AFTER the render + JS scroll
// restore (it is scheduled via requestAnimationFrame). It performs syntax
// highlighting, inline diff/csv/pdf/html/excalidraw hydration, mermaid/katex
// rendering — all of which can CHANGE the height of rows above the viewport.
//
// On mobile the scroller rests at overflow-anchor:auto, so any above-viewport
// height change in this post-render frame makes the browser's native anchor
// engine compensate scrollTop a SECOND time — after the JS restore already
// settled the reader's position — yanking them to an unrelated turn ("往回大跳").
// The synchronous _fixMobileScrollJank / _suppressBrowserOverflowAnchor guards
// only cover the render frame itself; they have already released by the time
// this rAF fires. Wrap the post-process (and the media-reflow frame right after
// it) in the same suppression so the browser layer cannot re-anchor during the
// async settle window. Desktop rests at `none`, so this is a no-op there.
function _postProcessWithAnchorSuppression(container){
  const scroller=$('messages');
  const release=(scroller&&typeof _suppressBrowserOverflowAnchor==='function')
    ? _suppressBrowserOverflowAnchor(scroller) : null;
  try{
    postProcessRenderedMessages(container);
  }finally{
    // Hold suppression across ONE more frame so late media/layout reflow
    // (image decode, katex/mermaid measure) cannot re-anchor either, then let
    // _suppressBrowserOverflowAnchor's own rAF-deferred restore run.
    if(release){
      if(typeof requestAnimationFrame==='function') requestAnimationFrame(release);
      else release();
    }
  }
}
function postProcessRenderedMessages(container) {
  highlightCode(container);
  addCopyButtons(container);
  loadDiffInline(container);
  loadCsvInline(container);
  loadExcalidrawInline(container);
  loadPdfInline(container);
  loadHtmlInline(container);
  renderMermaidBlocks(container);
  renderKatexBlocks(container);
  initTreeViews(container);
}

function highlightCode(container) {
  // Apply Prism.js syntax highlighting only to *new* code blocks.
  // Previously every renderMessages() called Prism.highlightAllUnder() which
  // re-scanned and re-highlighted every <pre> in the container — expensive in
  // long sessions with dozens of code blocks.  Now we only touch blocks that
  // don't already have the data-highlighted marker.
  if(typeof Prism === 'undefined') return;
  const el = container || $('msgInner');
  if(!el) return;
  // Prefer per-element highlight (avoids the full DOM walk of highlightAllUnder)
  const blocks = el.querySelectorAll('pre code:not([data-highlighted])');
  if(blocks.length === 0) return;
  for(let i = 0; i < blocks.length; i++){
    const block = blocks[i];
    if(typeof Prism.highlightElement === 'function') Prism.highlightElement(block);
    block.dataset.highlighted = '1';
  }
}

// Lazy load js-yaml for YAML tree view support
let _jsyamlLoading=false;
function _loadJsyamlThen(cb){
  if(typeof jsyaml!=='undefined'){ cb(); return; }
  if(_jsyamlLoading){ setTimeout(()=>_loadJsyamlThen(cb),100); return; }
  _jsyamlLoading=true;
  const s=document.createElement('script');
  s.src='static/vendor/js-yaml/4.1.0/js-yaml.min.js';
  s.integrity='sha384-+pxiN6T7yvpryuJmE1gM9PX7yQit15auDb+ZwwvJOd/4be2Cie5/IuVXgQb/S9du';
  s.crossOrigin='anonymous';
  s.onload=()=>{ _jsyamlLoading=false; cb(); };
  s.onerror=()=>{ _jsyamlLoading=false; }; // CDN blocked, fall back to raw
  document.head.appendChild(s);
}

// JSON/YAML/PDF/HTML code-block viewers → ui-code-viewer.js

// ── Workspace preferences kebab menu (#1793 UX refinement) ───────────────
// The "Show hidden files" toggle used to live as a permanent inline row
// below the breadcrumb bar. That ate ~32px of vertical space on every
// panel view (root, subdir, file preview), even though the toggle is a
// set-once preference — most users flip it once or never. Moving the
// control into a kebab dropdown reclaims the space; the small "(hidden
// files visible)" indicator on the heading reflects the non-default state
// so the affordance isn't lost.
let _workspacePrefsMenu = null;
let _workspacePrefsAnchor = null;
function _closeWorkspacePrefsMenu(){
  if(_workspacePrefsMenu){ _workspacePrefsMenu.remove(); _workspacePrefsMenu=null; }
  if(_workspacePrefsAnchor){
    _workspacePrefsAnchor.classList.remove('active');
    _workspacePrefsAnchor.setAttribute('aria-expanded','false');
    _workspacePrefsAnchor=null;
  }
}
function _positionWorkspacePrefsMenu(anchorEl){
  if(!_workspacePrefsMenu||!anchorEl) return;
  const rect=anchorEl.getBoundingClientRect();
  const menuW=Math.min(260, Math.max(220, _workspacePrefsMenu.scrollWidth||220));
  let left=rect.right-menuW;
  if(left<8) left=8;
  if(left+menuW>window.innerWidth-8) left=window.innerWidth-menuW-8;
  let top=rect.bottom+6;
  const menuH=_workspacePrefsMenu.offsetHeight||0;
  if(top+menuH>window.innerHeight-8 && rect.top>menuH+12) top=rect.top-menuH-6;
  if(top<8) top=8;
  _workspacePrefsMenu.style.left=left+'px';
  _workspacePrefsMenu.style.top=top+'px';
}
function _buildWorkspacePrefsMenu(){
  const menu=document.createElement('div');
  menu.className='workspace-prefs-menu open';
  menu.setAttribute('role','menu');
  const group=document.createElement('div');
  group.className='workspace-prefs-group';
  group.setAttribute('role','group');
  const groupLabel=(typeof t==='function'?t('workspace_sort_by'):'Sort by');
  group.setAttribute('aria-label',groupLabel);
  const head=document.createElement('div');
  head.className='workspace-prefs-grouplabel';
  head.textContent=groupLabel;
  group.appendChild(head);
  const createdOk=_workspaceCreatedSortAvailable();
  const active=_effectiveWorkspaceSortKey();
  [['name-asc','workspace_sort_name_asc'],['name-desc','workspace_sort_name_desc'],['created-desc','workspace_sort_created_desc'],['modified-desc','workspace_sort_modified_desc']].forEach(([key,i18nKey])=>{
    const disabled=key==='created-desc'&&!createdOk;
    const row=document.createElement('label');
    row.className='workspace-prefs-item workspace-prefs-item--radio'+(disabled?' is-disabled':'');
    row.setAttribute('role','menuitemradio');
    row.setAttribute('aria-checked',active===key?'true':'false');
    row.setAttribute('aria-disabled',disabled?'true':'false');
    row.innerHTML='<input type="radio" name="workspaceSortKey" value="'+esc(key)+'" id="workspaceSort_'+esc(key)+'"'+(disabled?' disabled':'')+' onchange="setWorkspaceSortKey(this.value)">'+
      '<span class="workspace-prefs-copy"><span class="workspace-prefs-name">'+esc(typeof t==='function'?t(i18nKey):key)+'</span>'+
      (disabled?'<span class="workspace-prefs-meta">'+esc(typeof t==='function'?t('workspace_sort_created_unavailable'):'Creation time is not reported by this server or platform.')+'</span>':'')+'</span>';
    const input=row.querySelector('input');
    if(input)input.checked=active===key;
    group.appendChild(row);
  });
  menu.appendChild(group);
  const sep=document.createElement('div');
  sep.className='workspace-prefs-sep';
  menu.appendChild(sep);
  // The checkbox keeps id="workspaceShowHiddenFiles" so existing call
  // sites (and the existing test_issue1793_file_tree_cruft_filter test)
  // can find it the same way as before. Only the parent container moves.
  const labelTxt = (typeof t==='function' ? t('workspace_show_hidden_files') : 'Show hidden files');
  const descTxt  = (typeof t==='function' ? t('workspace_show_hidden_files_desc') : 'Include .DS_Store, .git, node_modules, and other hidden / system files in the file tree.');
  const row=document.createElement('label');
  row.className='workspace-prefs-item';
  row.setAttribute('role','menuitemcheckbox');
  row.innerHTML=
    '<input type="checkbox" id="workspaceShowHiddenFiles" '+
    'onchange="toggleWorkspaceHiddenFiles(this.checked)">'+
    '<span class="workspace-prefs-copy">'+
      '<span class="workspace-prefs-name">'+esc(labelTxt)+'</span>'+
      '<span class="workspace-prefs-meta">'+esc(descTxt)+'</span>'+
    '</span>';
  const cb=row.querySelector('input');
  if(cb) cb.checked=!!S.showHiddenWorkspaceFiles;
  menu.appendChild(row);
  return menu;
}
function toggleWorkspacePrefsMenu(e){
  if(e&&e.preventDefault) e.preventDefault();
  if(e&&e.stopPropagation) e.stopPropagation();
  // Anchor preference: the kebab button. The indicator chip can also open
  // the same menu (click on "(hidden visible)"), but anchor positioning
  // always references the kebab so the menu lands in the same place.
  const anchor=$('btnWorkspacePrefs')||(e&&e.currentTarget)||null;
  if(_workspacePrefsMenu&&_workspacePrefsAnchor===anchor){ _closeWorkspacePrefsMenu(); return; }
  _closeWorkspacePrefsMenu();
  const menu=_buildWorkspacePrefsMenu();
  document.body.appendChild(menu);
  _workspacePrefsMenu=menu;
  _workspacePrefsAnchor=anchor;
  if(anchor){ anchor.classList.add('active'); anchor.setAttribute('aria-expanded','true'); }
  _positionWorkspacePrefsMenu(anchor);
}
document.addEventListener('click',e=>{
  if(!_workspacePrefsMenu) return;
  if(_workspacePrefsMenu.contains(e.target)) return;
  if(_workspacePrefsAnchor&&_workspacePrefsAnchor.contains(e.target)) return;
  // Indicator chip is also an opener — clicking it should toggle, not close.
  const ind=$('workspaceHiddenIndicator');
  if(ind&&ind.contains(e.target)) return;
  _closeWorkspacePrefsMenu();
});
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'&&_workspacePrefsMenu) _closeWorkspacePrefsMenu();
});
window.addEventListener('resize',()=>{
  if(_workspacePrefsMenu&&_workspacePrefsAnchor) _positionWorkspacePrefsMenu(_workspacePrefsAnchor);
});

if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',_syncWorkspaceHiddenToggle);
else _syncWorkspaceHiddenToggle();

function bindWorkspaceHeadingActions(){
  const heading=$('workspacePanelHeading');
  if(!heading||heading.dataset.bound==='1')return;
  heading.dataset.bound='1';
  const goRoot=()=>{
    if(S.session&&S.session.workspace) loadDir('.');
  };
  heading.onclick=goRoot;
  heading.onkeydown=(e)=>{
    if(!(S.session&&S.session.workspace)) return;
    if(e.key==='Enter'||e.key===' '){
      e.preventDefault();
      goRoot();
    }
  };
  heading.oncontextmenu=(e)=>{
    if(!(S.session&&S.session.workspace)) return;
    e.preventDefault();
    e.stopPropagation();
    _showWorkspaceRootContextMenu(e);
  };
  _syncWorkspaceHeadingState();
}

function _syncWorkspaceHeadingState(){
  const heading=$('workspacePanelHeading');
  if(!heading) return;
  const enabled=!!(S.session&&S.session.workspace);
  heading.classList.toggle('workspace-panel-heading--enabled',enabled);
  if(enabled){
    heading.setAttribute('role','button');
    heading.setAttribute('tabindex','0');
    heading.setAttribute('aria-disabled','false');
    heading.title='Workspace root';
  } else {
    heading.removeAttribute('role');
    heading.removeAttribute('tabindex');
    heading.setAttribute('aria-disabled','true');
    heading.title=t('no_workspace');
  }
}
if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',bindWorkspaceHeadingActions);
else bindWorkspaceHeadingActions();

function _workspaceContextMenuItem(label, onClick, opts={}){
  const item=document.createElement('div');
  item.textContent=label;
  item.style.cssText='padding:7px 14px;cursor:pointer;font-size:13px;color:'+(opts.danger?'var(--error,#e94560)':'var(--text)')+';';
  item.onmouseenter=()=>item.style.background='var(--hover-bg)';
  item.onmouseleave=()=>item.style.background='';
  item.onclick=onClick;
  return item;
}

function _copyTextWithFallback(text, successMsg, failurePrefix){
  const done=()=>showToast(successMsg);
  const fail=(err)=>showToast(failurePrefix+(err&&err.message?err.message:String(err||'')));
  if(navigator.clipboard&&navigator.clipboard.writeText){
    return navigator.clipboard.writeText(text).then(done).catch(err=>{
      const ta=document.createElement('textarea');
      ta.value=text;
      ta.style.cssText='position:fixed;left:-9999px;top:-9999px;';
      document.body.appendChild(ta);
      ta.select();
      let copied=false;
      try{copied=document.execCommand('copy');}catch(_){}
      ta.remove();
      if(copied) done(); else fail(err);
    });
  }
  const ta=document.createElement('textarea');
  ta.value=text;
  ta.style.cssText='position:fixed;left:-9999px;top:-9999px;';
  document.body.appendChild(ta);
  ta.select();
  let copied=false;
  try{copied=document.execCommand('copy');}catch(err){ta.remove();fail(err);return Promise.resolve();}
  ta.remove();
  if(copied) done(); else fail('clipboard unavailable');
  return Promise.resolve();
}

function _workspaceCreateTargetLabel(targetDir){
  return targetDir && targetDir !== '.' ? targetDir : t('workspace_root');
}

function _workspaceJoinTargetPath(targetDir, name){
  const cleanName=String(name||'').trim();
  if(!cleanName) return '';
  return (!targetDir||targetDir==='.') ? cleanName : `${targetDir}/${cleanName}`;
}

function _showWorkspaceRootContextMenu(e){
  document.querySelectorAll('.file-ctx-menu').forEach(el=>el.remove());
  const menu=document.createElement('div');
  menu.className='file-ctx-menu workspace-root-ctx-menu';
  menu.style.cssText='position:fixed;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:6px 0;z-index:9999;min-width:160px;box-shadow:0 4px 16px rgba(0,0,0,.35);';
  const vw=window.innerWidth,vh=window.innerHeight;
  menu.style.left=(e.clientX+160>vw?e.clientX-170:e.clientX)+'px';
  menu.style.top=(e.clientY+80>vh?e.clientY-80:e.clientY)+'px';

  menu.appendChild(_workspaceContextMenuItem(t('new_file'),async()=>{
    menu.remove();
    await promptNewFile('.');
  }));

  menu.appendChild(_workspaceContextMenuItem(t('new_folder'),async()=>{
    menu.remove();
    await promptNewFolder('.');
  }));

  const createSep=document.createElement('hr');
  createSep.style.cssText='border:none;border-top:1px solid var(--border);margin:4px 0;';
  menu.appendChild(createSep);

  menu.appendChild(_workspaceContextMenuItem(t('reveal_in_finder'),async()=>{
    menu.remove();
    try{await api('/api/file/reveal',{method:'POST',body:JSON.stringify({session_id:S.session.session_id,path:'.'})});}
    catch(err){showToast(t('reveal_failed')+(err.message||err));}
  }));

  menu.appendChild(_workspaceContextMenuItem(t('open_in_vscode'),async()=>{
    menu.remove();
    try{await api('/api/file/open-vscode',{method:'POST',body:JSON.stringify({session_id:S.session.session_id,path:'.'})});}
    catch(err){showToast(t('open_in_vscode_failed')+(err.message||err));}
  }));

  menu.appendChild(_workspaceContextMenuItem(t('copy_file_path'),async()=>{
    menu.remove();
    try{
      const r=await api('/api/file/path',{method:'POST',body:JSON.stringify({session_id:S.session.session_id,path:'.'})});
      await _copyTextWithFallback((r&&r.path)||'.',t('path_copied'),t('path_copy_failed'));
    }catch(err){showToast(t('path_copy_failed')+(err.message||err));}
  }));

  document.body.appendChild(menu);
  const dismiss=()=>{menu.remove();document.removeEventListener('click',dismiss);};
  setTimeout(()=>document.addEventListener('click',dismiss),0);
}

// Track expanded directories for tree view
if(!S._expandedDirs) S._expandedDirs=new Set();
// Cache of fetched directory contents: path -> entries[]
if(!S._dirCache) S._dirCache={};

function renderFileTree(){
  const box=$('fileTree');
  // #5657: capture the scroll position before wiping the container. box.innerHTML=''
  // detaches every row, collapsing scrollHeight so the browser clamps scrollTop to 0;
  // without this, every expand/collapse, breadcrumb nav, refresh, and hidden-files
  // toggle that re-runs renderFileTree() teleports the reader back to the top of a
  // long tree. Restored only after the normal render tail below — the two early-return
  // paths (no-workspace hides the box; empty-dir has nothing to scroll) legitimately
  // reset. A plain scrollTop restore suffices here: expand/collapse insert/remove rows
  // BELOW the clicked disclosure, so the clicked row keeps its offset from the top (no
  // getBoundingClientRect anchor delta needed — that's only for prepend-above cases).
  const prevScrollTop=box?box.scrollTop:0;
  box.innerHTML='';
  // Cache current dir entries
  S._dirCache[S.currentDir||'.']=S.entries;
  // Show empty-state when no workspace is set or the directory is empty (#703)
  const emptyEl=$('wsEmptyState');
  const hasWorkspace=!!(S.session&&S.session.workspace);
  if(!hasWorkspace){
    _syncWorkspaceBirthtimeSupportScope('');
    if(emptyEl){emptyEl.textContent=t('workspace_empty_no_path');emptyEl.style.display='flex';}
    box.style.display='none';
    return;
  }
  _noteWorkspaceBirthtimeSupport(S.entries);
  if(emptyEl) emptyEl.style.display='none';
  box.style.display='';
  const visibleEntries=_workspaceEntriesForRender(S.entries);
  if(!visibleEntries.length){
    if(emptyEl){emptyEl.textContent=t('workspace_empty_dir');emptyEl.style.display='flex';}
    return;
  }
  _renderTreeItems(box, visibleEntries, 0);
  // #5657: restore the pre-wipe scroll position now that the tree is tall again.
  if(box) box.scrollTop=prevScrollTop;
}

let _wsActiveDragPath=null;
let _wsActiveDragType=null;
function _setWsDragData(e,item){
  e.dataTransfer.setData('application/ws-path',item.path);
  e.dataTransfer.setData('application/ws-type',item.type);
  e.dataTransfer.setData('text/plain',item.path);
  _wsActiveDragPath=item.path;
  _wsActiveDragType=item.type;
}
function _clearWsDragData(){
  _wsActiveDragPath=null;
  _wsActiveDragType=null;
}
// Window-level fallback cleanup: if a workspace drag is abandoned without the
// row's ondragend firing (drag cancelled, dropped outside any target, tab
// blurred/hidden mid-drag), the active-drag flag must not survive — otherwise a
// later FOREIGN text/plain drag could be misread as a workspace move.
if(typeof window!=='undefined'&&!window._wsDragCleanupBound){
  window._wsDragCleanupBound=true;
  window.addEventListener('dragend',_clearWsDragData,true);
  // Defer the drop cleanup a tick: this capture-phase window listener fires
  // BEFORE the target element's ondrop, so clearing synchronously here would
  // wipe _wsActiveDragPath before _isWorkspaceTreeMoveDrag()/_wsDragSrcPath()
  // run in the target handler — re-breaking the macOS stripped-MIME move. The
  // setTimeout lets the real drop handler complete, then clears the lingering flag.
  window.addEventListener('drop',()=>setTimeout(_clearWsDragData,0),true);
  window.addEventListener('pagehide',_clearWsDragData);
  window.addEventListener('blur',_clearWsDragData);
}
function _isWorkspaceTreeMoveDrag(e){
  if(e.dataTransfer&&e.dataTransfer.types&&e.dataTransfer.types.includes('Files')) return false;
  if(e.dataTransfer&&e.dataTransfer.types&&e.dataTransfer.types.includes('application/ws-path')) return true;
  // Stripped-MIME (macOS WebKit) fallback: accept text/plain ONLY while a
  // workspace drag is genuinely in flight. dragover/drop events can't read the
  // payload, so gate on the active flag alone here; the drop handler additionally
  // proves text/plain === _wsActiveDragPath before performing the move.
  return !!(_wsActiveDragPath&&e.dataTransfer&&e.dataTransfer.types&&e.dataTransfer.types.includes('text/plain'));
}
function _wsDragSrcPath(e){
  const custom=e.dataTransfer.getData('application/ws-path');
  if(custom) return custom;
  // Stripped-MIME fallback: only trust the active flag when the drop's own
  // text/plain matches it. A foreign text/plain drag (different/empty content)
  // must NOT resolve to our tracked workspace path even if the flag lingered.
  const plain=e.dataTransfer.getData('text/plain')||'';
  if(_wsActiveDragPath&&plain===_wsActiveDragPath) return _wsActiveDragPath;
  return '';
}
function _wsDragSrcType(e){
  const custom=e.dataTransfer.getData('application/ws-type');
  if(custom) return custom;
  return _wsActiveDragType||'file';
}

function _workspaceParentDir(relPath){
  if(!relPath||relPath==='.')return '.';
  const idx=relPath.lastIndexOf('/');
  return idx===-1?'.':relPath.substring(0,idx);
}

function _clearWorkspaceMoveDragOver(){
  document.querySelectorAll('.file-item.drag-over,.breadcrumb-seg.drag-over').forEach(el=>el.classList.remove('drag-over'));
}

function _remapWorkspaceCachesAfterMove(oldPath,newPath,isDir){
  if(isDir&&S._expandedDirs){
    if(S._expandedDirs.has(oldPath)){
      S._expandedDirs.delete(oldPath);
      S._expandedDirs.add(newPath);
    }
    for(const expandedPath of [...S._expandedDirs]){
      if(expandedPath.startsWith(oldPath+'/')){
        S._expandedDirs.delete(expandedPath);
        S._expandedDirs.add(newPath+expandedPath.slice(oldPath.length));
      }
    }
    if(S._dirCache[oldPath]){
      S._dirCache[newPath]=S._dirCache[oldPath];
      delete S._dirCache[oldPath];
    }
    for(const cachePath of Object.keys(S._dirCache)){
      if(cachePath.startsWith(oldPath+'/')){
        const remapped=newPath+cachePath.slice(oldPath.length);
        S._dirCache[remapped]=S._dirCache[cachePath];
        delete S._dirCache[cachePath];
      }
    }
    if(typeof _saveExpandedDirs==='function')_saveExpandedDirs();
  }
  delete S._dirCache[_workspaceParentDir(oldPath)];
  delete S._dirCache[_workspaceParentDir(newPath)];
  if(typeof _previewCurrentPath!=='undefined'&&_previewCurrentPath){
    if(_previewCurrentPath===oldPath)_previewCurrentPath=newPath;
    else if(_previewCurrentPath.startsWith(oldPath+'/'))_previewCurrentPath=newPath+_previewCurrentPath.slice(oldPath.length);
  }
}

async function _performWorkspaceMove(srcPath,destDir,isDir){
  if(!S.session||!srcPath)return;
  const normDest=destDir||'.';
  if(srcPath===normDest)return;
  if(normDest.startsWith(srcPath+'/'))return;
  if(_workspaceParentDir(srcPath)===normDest)return;
  try{
    const data=await api('/api/file/move',{method:'POST',body:JSON.stringify({
      session_id:S.session.session_id,path:srcPath,dest_dir:normDest
    })});
    const movedName=data.new_path.includes('/')?data.new_path.slice(data.new_path.lastIndexOf('/')+1):data.new_path;
    showToast((t('moved_to')||'Moved to ')+movedName);
    _remapWorkspaceCachesAfterMove(data.old_path||srcPath,data.new_path||srcPath,isDir);
    await loadDir(S.currentDir);
    if(typeof refreshOpenPreviewIfMutated==='function')await refreshOpenPreviewIfMutated();
  }catch(err){
    showToast((t('move_failed')||'Move failed: ')+err.message,5000,'error');
  }
}

function _bindWorkspaceMoveDropTarget(el,destDir){
  el.ondragenter=(e)=>{
    if(!_isWorkspaceTreeMoveDrag(e))return;
    e.preventDefault();e.stopPropagation();
    el.classList.add('drag-over');
  };
  el.ondragover=(e)=>{
    if(!_isWorkspaceTreeMoveDrag(e))return;
    e.preventDefault();e.stopPropagation();
    e.dataTransfer.dropEffect='move';
    el.classList.add('drag-over');
  };
  el.ondragleave=(e)=>{
    if(el.contains(e.relatedTarget))return;
    el.classList.remove('drag-over');
  };
  el.ondrop=async(e)=>{
    if(!_isWorkspaceTreeMoveDrag(e))return;
    e.preventDefault();e.stopPropagation();
    el.classList.remove('drag-over');
    try{
      const srcPath=_wsDragSrcPath(e);
      if(!srcPath)return;
      const srcType=_wsDragSrcType(e);
      await _performWorkspaceMove(srcPath,destDir,srcType==='dir');
    }finally{
      _clearWsDragData();
    }
  };
}

function elideMiddle(str, maxLen = 60) {
  if (str.length <= maxLen) return str;
  const half = Math.floor((maxLen - 3) / 2);
  return str.slice(0, half) + '...' + str.slice(str.length - half);
}

function _renderTreeItems(container, entries, depth){
  for(const item of entries){
    const el=document.createElement('div');el.className='file-item';
    el.style.paddingLeft=(8+depth*16)+'px';
    el.setAttribute('draggable','true');
    el.dataset.wsType=item.type;
    el.oncontextmenu=(e)=>{
      const grant=typeof _workspaceEscapeGrantForPath==='function' ? _workspaceEscapeGrantForPath(item.path) : null;
      const isDirRow=item.type==='dir'||(item.type==='symlink'&&item.is_dir);
      if(grant&&!isDirRow){e.preventDefault();e.stopPropagation();return;}
      e.preventDefault();e.stopPropagation();_showFileContextMenu(e,item);
    };
    el.ondragstart=(e)=>{_setWsDragData(e,item);e.dataTransfer.effectAllowed='copy';el.classList.add('dragging');};
    el.ondragend=()=>{el.classList.remove('dragging');_clearWorkspaceMoveDragOver();_clearWsDragData();};

    const isLk = item.type === 'symlink';
    const isExternalLink = isLk && item.target_outside_workspace;
    const escapeGrant = typeof _workspaceEscapeGrantForPath === 'function' ? _workspaceEscapeGrantForPath(item.path) : null;
    const exactEscapeGrant = typeof _workspaceEscapeExactGrant === 'function' ? _workspaceEscapeExactGrant(item.path) : null;
    const isReadOnlyEscape = !!escapeGrant;
    const isNestedEscape = !!escapeGrant && !exactEscapeGrant;
    // External symlinks are display-only: not expandable, not openable.
    // The read gate (safe_resolve_ws) still blocks navigation through them.
    const isDirLike = !isExternalLink && (item.type === 'dir' || (isLk && item.is_dir));
    const isFileLike = !isExternalLink && !isDirLike;
    el.dataset.wsIsDir = String(isDirLike);
    if(isExternalLink || isReadOnlyEscape){el.removeAttribute('draggable');el.ondragstart=null;}

    if(isDirLike){
      // Toggle arrow for directories
      const arrow=document.createElement('span');
      arrow.className='file-tree-toggle';
      const isExpanded=S._expandedDirs.has(item.path);
      arrow.textContent=isExpanded?'\u25BE':'\u25B8';
      el.appendChild(arrow);
    }else{
      // Keep file icons aligned with sibling directories that occupy this
      // slot with the expand/collapse toggle. #2554
      const spacer=document.createElement('span');
      spacer.className='file-tree-toggle-placeholder';
      spacer.setAttribute('aria-hidden','true');
      el.appendChild(spacer);
    }

    // Icon
    const iconEl=document.createElement('span');
    iconEl.className='file-icon';
    iconEl.innerHTML = isExternalLink
      ? li('external-link', 14)
      : isDirLike
        ? (isLk ? li('link', 14) : li('folder', 14))
        : (isLk ? li('link', 14) : fileIcon(item.name, item.type));
    el.appendChild(iconEl);

    // Name
    const nameEl=document.createElement('span');
    nameEl.className='file-name';nameEl.textContent=item.name;
    // Tooltip only on FILES — dblclick renames them. On directories, dblclick
    // navigates into the folder; rename lives in the right-click context menu
    // (the "Double-click to rename" hint here would be misleading). #1710.
    if(isLk && item.target)
      nameEl.title = t('symlink_link_to').replace('{target}', () => elideMiddle(item.target));
    else if(isExternalLink)
      nameEl.title = (typeof isReadOnlyEscape!=='undefined'
        ? isReadOnlyEscape
        : (typeof _workspaceEscapeGrantForPath==='function' ? !!_workspaceEscapeGrantForPath(item.path) : false))
        ? t('external_link_read_only')
        : t('external_link_open_confirm');
    else if(typeof isReadOnlyEscape!=='undefined'
      ? isReadOnlyEscape
      : (typeof _workspaceEscapeGrantForPath==='function' ? !!_workspaceEscapeGrantForPath(item.path) : false))
      nameEl.title = t('external_link_read_only');
    else if(!isDirLike)
      nameEl.title = t('double_click_rename');
    const nameIsReadOnlyEscape=typeof isReadOnlyEscape!=='undefined'
      ? isReadOnlyEscape
      : (typeof _workspaceEscapeGrantForPath==='function' ? !!_workspaceEscapeGrantForPath(item.path) : false);
    // Single-click opens (file) or expand-toggles (dir) but is debounced 300ms so a
    // double-click can cancel it and trigger rename instead. Without the debounce, the
    // click bubbles to el.onclick before dblclick can fire — that's #1698. Without the
    // restored activation, single-click on the filename does nothing — that's #1707.
    let _nameClickTimer=null;
    nameEl.onclick=(e)=>{
      e.stopPropagation();
      if(_nameClickTimer){clearTimeout(_nameClickTimer);_nameClickTimer=null;}
      _nameClickTimer=setTimeout(()=>{
        _nameClickTimer=null;
        // Delegate to the row's existing single-click handler (openFile / dir toggle).
        if(typeof el.onclick==='function')el.onclick(e);
      },300);
    };
    nameEl.ondblclick=(e)=>{
      e.stopPropagation();
      if(_nameClickTimer){clearTimeout(_nameClickTimer);_nameClickTimer=null;}
      // For directories, double-click navigates (breadcrumb view)
      if(isDirLike){loadDir(item.path);return;}
      // Escape-root rows remain browse-only, nested escape rows stay display-only.
      if(nameIsReadOnlyEscape){
        if(isExternalLink){if(typeof el.onclick==='function')el.onclick(e);return;}
        openFile(item.path);
        return;
      }
      const inp=document.createElement('input');
      inp.className='file-rename-input';inp.value=item.name;
      inp.onclick=(e2)=>e2.stopPropagation();
      const finish=async(save)=>{
        inp.onblur=null;
        if(save){
          const newName=inp.value.trim();
          if(newName&&newName!==item.name){
            try{
              await api('/api/file/rename',{method:'POST',body:JSON.stringify({
                session_id:S.session.session_id,path:item.path,new_name:newName
              })});
              showToast(t('renamed_to')+newName);
              // Update expanded dirs cache key if renaming a directory
              if(isDirLike&&S._expandedDirs){
                S._expandedDirs.delete(item.path);
                const parent=item.path.includes('/')?item.path.substring(0,item.path.lastIndexOf('/')):'.';
                const newPath=parent==='.'?newName:parent+'/'+newName;
                S._expandedDirs.add(newPath);
                if(S._dirCache[item.path]){S._dirCache[newPath]=S._dirCache[item.path];delete S._dirCache[item.path];}
                if(typeof _saveExpandedDirs==='function')_saveExpandedDirs();
              }
              // Invalidate cache and re-render
              delete S._dirCache[S.currentDir];
              await loadDir(S.currentDir);
            }catch(err){showToast(t('rename_failed')+err.message);}
          }
        }
        inp.replaceWith(nameEl);
      };
      inp.onkeydown=(e2)=>{
        if(e2.key==='Enter'){
          if(window._isImeEnter&&window._isImeEnter(e2)){return;}
          e2.preventDefault();
          finish(true);
        }
        if(e2.key==='Escape'){e2.preventDefault();finish(false);}
      };
      inp.onblur=()=>finish(false);
      nameEl.replaceWith(inp);
      setTimeout(()=>{inp.focus();inp.select();},10);
    };
    el.appendChild(nameEl);

    // Size -- for real files and symlinks that resolve to files
    if(isFileLike&&item.size){
      const sizeEl=document.createElement('span');
      sizeEl.className='file-size';
      sizeEl.textContent=`${(item.size/1024).toFixed(1)}k`;
      el.appendChild(sizeEl);
    }

    // Delete button -- for file-like rows and directory-like rows
    if(isFileLike){
      if(!isReadOnlyEscape){
        const del=document.createElement('button');
        del.className='file-del-btn';del.title=t('delete_title');del.textContent='\u00d7';
        del.onclick=async(e)=>{e.stopPropagation();await deleteWorkspaceFile(item.path,item.name);};
        el.appendChild(del);
      }
    }else if(isDirLike&& !isReadOnlyEscape){
      const del=document.createElement('button');
      del.className='file-del-btn';del.title=t('delete_title');del.textContent='\u00d7';
      del.onclick=async(e)=>{e.stopPropagation();await deleteWorkspaceDir(item.path,item.name);};
      el.appendChild(del);
    }

    if(isDirLike){
      if(!isReadOnlyEscape){
        _bindWorkspaceMoveDropTarget(el,item.path);
        _bindWorkspaceOsUploadDropTarget(el,item.path);
      }
      // Single-click toggles expand/collapse
      el.onclick=async(e)=>{
        e.stopPropagation();
        if(S._expandedDirs.has(item.path)){
          S._expandedDirs.delete(item.path);
          if(typeof _saveExpandedDirs==='function')_saveExpandedDirs();
          renderFileTree();
        }else{
          S._expandedDirs.add(item.path);
          if(typeof _saveExpandedDirs==='function')_saveExpandedDirs();
          // Fetch children if not cached
          if(!S._dirCache[item.path]){
            try{
              const data=await api(_workspaceRouteForPath(item.path, 'list'));
              S._dirCache[item.path]=data.entries||[];
            }catch(e2){S._dirCache[item.path]=[];}
          }
          renderFileTree();
        }
      };
    }else if(isExternalLink){
      // Display-only: the link points outside the workspace. We do NOT disclose
      // the resolved outside path (#4581 hardening) and do NOT recursively
      // authorize nested escape rows under an already-authorized external root.
      el.onclick=async(e)=>{
        e.stopPropagation();
        if(isNestedEscape){
          await showConfirmDialog({
            title:item.name,
            message:t('external_link_read_only'),
            confirmLabel:t('dialog_confirm_btn'),
            danger:false,
            hideCancel:true,
            focusCancel:false,
          });
          return;
        }
        const grant = await authorizeWorkspaceEscapeNavigation(item);
        if(!grant) return;
        if(grant.isDir) await loadDir(item.path);
        else await openFile(item.path);
      };
    }else{
      el.onclick=async()=>openFile(item.path);
    }

    container.appendChild(el);

    // Render children if directory is expanded
    if(isDirLike&&S._expandedDirs.has(item.path)){
      const children=_workspaceEntriesForRender(S._dirCache[item.path]||[]);
      if(children.length){
        _renderTreeItems(container, children, depth+1);
      }else{
        const empty=document.createElement('div');
        empty.className='file-item file-empty';
        empty.style.paddingLeft=(8+(depth+1)*16)+'px';
        empty.textContent=t('empty_dir');
        container.appendChild(empty);
      }
    }
  }
}

async function deleteWorkspaceDir(relPath, name){
  if(!S.session)return;
  if(typeof _workspacePathIsReadOnly==='function'&&_workspacePathIsReadOnly(relPath)){
    showToast(t('external_link_read_only'), 2000);
    return;
  }
  const ok=await showConfirmDialog({title:t('delete_dir_confirm',name),message:'',confirmLabel:'Delete',danger:true,focusCancel:true});
  if(!ok)return;
  try{
    await api('/api/file/delete',{method:'POST',body:JSON.stringify({session_id:S.session.session_id,path:relPath,recursive:true})});
    showToast(t('deleted')+name);
    // Remove from expanded dirs cache
    if(S._expandedDirs){S._expandedDirs.delete(relPath);if(typeof _saveExpandedDirs==='function')_saveExpandedDirs();}
    delete S._dirCache[relPath];
    await loadDir(S.currentDir);
  }catch(e){setStatus(t('delete_failed')+e.message);}
}

function _showFileContextMenu(e, item){
  document.querySelectorAll('.file-ctx-menu').forEach(el=>el.remove());
  const menu=document.createElement('div');
  menu.className='file-ctx-menu';
  menu.style.cssText='position:fixed;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:6px 0;z-index:9999;min-width:140px;box-shadow:0 4px 16px rgba(0,0,0,.35);';
  // Keep menu within viewport
  const vw=window.innerWidth,vh=window.innerHeight;
  menu.style.left=(e.clientX+140>vw?e.clientX-150:e.clientX)+'px';
  menu.style.top=(e.clientY+100>vh?e.clientY-100:e.clientY)+'px';
  const isDirLike=item.type==='dir'||(item.type==='symlink'&&item.is_dir);
  const targetDir=isDirLike ? item.path : _workspaceParentDir(item.path);
  const isReadOnlyEscape=typeof _workspaceEscapeGrantForPath==='function' ? !!_workspaceEscapeGrantForPath(item.path) : false;

  if(!isReadOnlyEscape){
    menu.appendChild(_workspaceContextMenuItem(t('new_file'),async()=>{
      menu.remove();
      await promptNewFile(targetDir);
    }));

    menu.appendChild(_workspaceContextMenuItem(t('new_folder'),async()=>{
      menu.remove();
      await promptNewFolder(targetDir);
    }));

    const createSep=document.createElement('hr');
    createSep.style.cssText='border:none;border-top:1px solid var(--border);margin:4px 0;';
    menu.appendChild(createSep);

    // Rename
    const renameItem=document.createElement('div');
    renameItem.textContent=t('rename_title');
    renameItem.style.cssText='padding:7px 14px;cursor:pointer;font-size:13px;color:var(--text);';
    renameItem.onmouseenter=()=>renameItem.style.background='var(--hover-bg)';
    renameItem.onmouseleave=()=>renameItem.style.background='';
    renameItem.onclick=()=>{menu.remove();_inlineRenameFileItem(item);};
    menu.appendChild(renameItem);

    // Reveal in File Manager
    const revealItem=document.createElement('div');
    revealItem.textContent=t('reveal_in_finder');
    revealItem.style.cssText='padding:7px 14px;cursor:pointer;font-size:13px;color:var(--text);';
    revealItem.onmouseenter=()=>revealItem.style.background='var(--hover-bg)';
    revealItem.onmouseleave=()=>revealItem.style.background='';
    revealItem.onclick=async()=>{menu.remove();try{await api('/api/file/reveal',{method:'POST',body:JSON.stringify({session_id:S.session.session_id,path:item.path})});}catch(err){showToast(t('reveal_failed')+(err.message||err));}};
    menu.appendChild(revealItem);

    // Open in VS Code (#2735)
    const vscodeItem=document.createElement('div');
    vscodeItem.textContent=t('open_in_vscode');
    vscodeItem.style.cssText='padding:7px 14px;cursor:pointer;font-size:13px;color:var(--text);';
    vscodeItem.onmouseenter=()=>vscodeItem.style.background='var(--hover-bg)';
    vscodeItem.onmouseleave=()=>vscodeItem.style.background='';
    vscodeItem.onclick=async()=>{menu.remove();try{await api('/api/file/open-vscode',{method:'POST',body:JSON.stringify({session_id:S.session.session_id,path:item.path})});}catch(err){showToast(t('open_in_vscode_failed')+(err.message||err));}};
    menu.appendChild(vscodeItem);

    // Copy file path — resolves the absolute on-disk path on the server (so the
    // user gets the full /home/.../workspace/foo.py rather than the relative
    // path the file tree shows) and writes it to the OS clipboard. Useful for
    // pasting into terminals, editors, or other apps without taking the slower
    // Reveal-in-Finder round trip.
    const copyPathItem=document.createElement('div');
    copyPathItem.textContent=t('copy_file_path');
    copyPathItem.style.cssText='padding:7px 14px;cursor:pointer;font-size:13px;color:var(--text);';
    copyPathItem.onmouseenter=()=>copyPathItem.style.background='var(--hover-bg)';
    copyPathItem.onmouseleave=()=>copyPathItem.style.background='';
    copyPathItem.onclick=async()=>{
      menu.remove();
      try{
        const r=await api('/api/file/path',{method:'POST',body:JSON.stringify({session_id:S.session.session_id,path:item.path})});
        const abs=(r&&r.path)||item.path;
        try{
          await navigator.clipboard.writeText(abs);
          showToast(t('path_copied'));
        }catch(clipErr){
          const ta=document.createElement('textarea');
          ta.value=abs;
          ta.style.cssText='position:fixed;left:-9999px;top:-9999px;';
          document.body.appendChild(ta);
          ta.select();
          let copied=false;
          try{copied=document.execCommand('copy');}catch(_){}
          ta.remove();
          if(copied) showToast(t('path_copied'));
          else showToast(t('path_copy_failed')+(clipErr&&clipErr.message?clipErr.message:String(clipErr)));
        }
      }catch(err){
        showToast(t('path_copy_failed')+(err.message||err));
      }
    };
    menu.appendChild(copyPathItem);

    const copyRelPathItem=document.createElement('div');
    copyRelPathItem.textContent=t('copy_relative_path');
    copyRelPathItem.style.cssText='padding:7px 14px;cursor:pointer;font-size:13px;color:var(--text);';
    copyRelPathItem.onmouseenter=()=>copyRelPathItem.style.background='var(--hover-bg)';
    copyRelPathItem.onmouseleave=()=>copyRelPathItem.style.background='';
    copyRelPathItem.onclick=async()=>{
      menu.remove();
      try{
        const rel=_normalizeWorkspaceRelPath(item.path)||item.path;
        await _copyTextWithFallback(rel,t('path_copied'),t('path_copy_failed'));
      }catch(err){
        showToast(t('path_copy_failed')+(err.message||err));
      }
    };
    menu.appendChild(copyRelPathItem);
  }

  if(isDirLike){
    const dlItem=document.createElement('div');
    dlItem.textContent=t('download_folder');
    dlItem.style.cssText='padding:7px 14px;cursor:pointer;font-size:13px;color:var(--text);';
    dlItem.onmouseenter=()=>dlItem.style.background='var(--hover-bg)';
    dlItem.onmouseleave=()=>dlItem.style.background='';
    dlItem.onclick=()=>{
      menu.remove();
      const rel='/api/folder/download?session_id='+encodeURIComponent(S.session.session_id)
              + '&path='+encodeURIComponent(item.path||'');
      window.location.href=new URL(rel.slice(1), document.baseURI||location.href).href;
    };
    menu.appendChild(dlItem);
  }

  if(!isReadOnlyEscape){
    const sep=document.createElement('hr');
    sep.style.cssText='border:none;border-top:1px solid var(--border);margin:4px 0;';
    menu.appendChild(sep);
    const delItem=document.createElement('div');
    delItem.textContent=t('delete_title');
    delItem.style.cssText='padding:7px 14px;cursor:pointer;font-size:13px;color:var(--error,#e94560);';
    delItem.onmouseenter=()=>delItem.style.background='var(--hover-bg)';
    delItem.onmouseleave=()=>delItem.style.background='';
    delItem.onclick=()=>{menu.remove();if(isDirLike)deleteWorkspaceDir(item.path,item.name);else deleteWorkspaceFile(item.path,item.name);};
    menu.appendChild(delItem);
  }

  document.body.appendChild(menu);
  const dismiss=()=>{menu.remove();document.removeEventListener('click',dismiss);};
  setTimeout(()=>document.addEventListener('click',dismiss),0);
}

async function _inlineRenameFileItem(item){
  if(!S.session)return;
  if(typeof _workspacePathIsReadOnly==='function'&&_workspacePathIsReadOnly(item.path)){
    showToast(t('external_link_read_only'), 2000);
    return;
  }
  const isDirLike=item.type==='dir'||(item.type==='symlink'&&item.is_dir);
  // Pre-fill the input with the current name and select just the stem
  // (everything before the last '.') so the user can immediately retype the
  // basename while preserving the extension — matches macOS Finder. For
  // directories or names with no '.', the helper selects the full value.
  // `selectStem` also handles dotfiles ('.gitignore') by full-selecting.
  const newName=await showPromptDialog({
    message:t('rename_prompt'),
    value:item.name,
    confirmLabel:t('rename_title'),
    selectStem:!isDirLike,
    selectAll:isDirLike
  });
  if(!newName||newName===item.name)return;
  try{
    await api('/api/file/rename',{method:'POST',body:JSON.stringify({session_id:S.session.session_id,path:item.path,new_name:newName})});
    showToast(t('renamed_to')+newName);
    // Update expanded dirs cache key if renaming a directory
    if(isDirLike&&S._expandedDirs){
      S._expandedDirs.delete(item.path);
      const parent=item.path.includes('/')?item.path.substring(0,item.path.lastIndexOf('/')):'.';
      const newPath=parent==='.'?newName:parent+'/'+newName;
      S._expandedDirs.add(newPath);
      if(S._dirCache[item.path]){S._dirCache[newPath]=S._dirCache[item.path];delete S._dirCache[item.path];}
      if(typeof _saveExpandedDirs==='function')_saveExpandedDirs();
    }
    delete S._dirCache[S.currentDir];
    await loadDir(S.currentDir);
  }catch(err){showToast(t('rename_failed')+err.message);}
}

async function deleteWorkspaceFile(relPath, name){
  if(!S.session)return;
  if(typeof _workspacePathIsReadOnly==='function'&&_workspacePathIsReadOnly(relPath)){
    showToast(t('external_link_read_only'), 2000);
    return;
  }
  const _delFile=await showConfirmDialog({title:t('delete_confirm',name),message:'',confirmLabel:'Delete',danger:true,focusCancel:true});
  if(!_delFile) return;
  try{
    await api('/api/file/delete',{method:'POST',body:JSON.stringify({session_id:S.session.session_id,path:relPath})});
    showToast(t('deleted')+name);
    // Close preview if we just deleted the viewed file
    if($('previewPathText').textContent===relPath)$('btnClearPreview').onclick();
    await loadDir(S.currentDir);
  }catch(e){setStatus(t('delete_failed')+e.message);}
}

async function promptNewFile(targetDir = S.currentDir || '.'){
  if(!S.session){
    const ws=(typeof S._profileDefaultWorkspace==='string'&&S._profileDefaultWorkspace)||'';
    if(!ws) return;
    try{
      // System-minted session (#6022): explicit worktree:false — creating a
      // file from a blank page must not inherit the config worktree default.
      const r=await api('/api/session/new',{method:'POST',body:JSON.stringify({workspace:ws,worktree:false})});
      if(r&&r.session){S._pendingSessionToolsets=null;S.session=r.session;if(typeof _adoptRegenerationRevision==='function') _adoptRegenerationRevision(r.session);S.messages=[];syncTopbar();renderMessages();await renderSessionList();}
    }catch(e){setStatus(t('create_failed')+e.message);return;}
  }
  if(!S.session)return;
  if(typeof _workspacePathIsReadOnly==='function'&&_workspacePathIsReadOnly(targetDir)){
    showToast(t('external_link_read_only'), 2000);
    return;
  }
  const targetLabel=_workspaceCreateTargetLabel(targetDir);
  const name=await showPromptDialog({
    title:t('new_file_prompt_title', targetLabel),
    placeholder:'filename.txt',
    confirmLabel:t('create')
  });
  if(!name||!name.trim()) return;
  const relPath=_workspaceJoinTargetPath(targetDir,name);
  try{
    await api('/api/file/create',{method:'POST',body:JSON.stringify({session_id:S.session.session_id,path:relPath,content:''})});
    showToast(t('created')+name.trim());
    delete S._dirCache[targetDir || '.'];
    await loadDir(S.currentDir);
    openFile(relPath);
  }catch(e){setStatus(t('create_failed')+e.message);}
}

async function promptNewFolder(targetDir = S.currentDir || '.'){
  if(!S.session){
    const ws=(typeof S._profileDefaultWorkspace==='string'&&S._profileDefaultWorkspace)||'';
    if(!ws) return;
    try{
      // System-minted session (#6022): explicit worktree:false — creating a
      // folder from a blank page must not inherit the config worktree default.
      const r=await api('/api/session/new',{method:'POST',body:JSON.stringify({workspace:ws,worktree:false})});
      if(r&&r.session){S._pendingSessionToolsets=null;S.session=r.session;if(typeof _adoptRegenerationRevision==='function') _adoptRegenerationRevision(r.session);S.messages=[];syncTopbar();renderMessages();await renderSessionList();}
    }catch(e){setStatus(t('folder_create_failed')+e.message);return;}
  }
  if(!S.session)return;
  if(typeof _workspacePathIsReadOnly==='function'&&_workspacePathIsReadOnly(targetDir)){
    showToast(t('external_link_read_only'), 2000);
    return;
  }
  const targetLabel=_workspaceCreateTargetLabel(targetDir);
  const name=await showPromptDialog({
    title:t('new_folder_prompt_title', targetLabel),
    placeholder:'folder-name',
    confirmLabel:t('create')
  });
  if(!name||!name.trim()) return;
  const relPath=_workspaceJoinTargetPath(targetDir,name);
  try{
    await api('/api/file/create-dir',{method:'POST',body:JSON.stringify({session_id:S.session.session_id,path:relPath})});
    showToast(t('folder_created')+name.trim());
    delete S._dirCache[targetDir || '.'];
    await loadDir(S.currentDir);
    const absPath=S.session.workspace?(targetDir==='.'?`${S.session.workspace}/${name.trim()}`:`${S.session.workspace}/${targetDir}/${name.trim()}`):null;
    if(absPath){
      const addAsSpace=await showConfirmDialog({
        title:t('folder_add_as_space_title'),
        message:t('folder_add_as_space_msg'),
        confirmLabel:t('folder_add_as_space_btn'),
        cancelLabel:t('status_no'),
        focusCancel:true
      });
      if(addAsSpace){
        try{
          const data=await api('/api/workspaces/add',{method:'POST',body:JSON.stringify({path:absPath})});
          if(typeof _workspaceList!=='undefined')_workspaceList=data.workspaces||_workspaceList||[];
          if(typeof renderWorkspacesPanel==='function')renderWorkspacesPanel(_workspaceList);
          showToast(t('workspace_added'));
        }catch(e2){setStatus((t('error_prefix')||'Error: ')+e2.message);}
      }
    }
  }catch(e){setStatus(t('folder_create_failed')+e.message);}
}

function renderTray(){ // non-media files use paperclip chip
  const tray=$('attachTray');tray.innerHTML='';
  if(!S.pendingFiles.length){tray.classList.remove('has-files');updateSendBtn();return;}
  tray.classList.add('has-files');
  updateSendBtn();
  S.pendingFiles.forEach((f,i)=>{
    const chip=document.createElement('div');chip.className='attach-chip';
    const mediaKind=_mediaKindForName(f.name);
    if(_IMAGE_EXTS.test(f.name)||mediaKind==='audio'||mediaKind==='video'){
      const blobUrl=URL.createObjectURL(f);
      chip.className='attach-chip attach-chip--media attach-chip--'+mediaKind; // attach-chip--audio attach-chip--video
      chip.dataset.blobUrl=blobUrl;
      if(mediaKind==='image'){
        chip.innerHTML=`<img class="attach-thumb" src="${esc(blobUrl)}" alt="${esc(f.name)}" title="${esc(f.name)}"><button title="${t('remove_title')}">${li('x',12)}</button>`;
      } else if(_SVG_EXTS.test(f.name)){
        chip.innerHTML=`<img class="attach-thumb attach-thumb--svg" src="${esc(blobUrl)}" alt="${esc(f.name)}" title="${esc(f.name)}"><button title="${t('remove_title')}">${li('x',12)}</button>`;
      } else if(mediaKind==='audio'){
        chip.innerHTML=`<span class="attach-chip-media">🎵 ${esc(f.name)}</span><audio controls preload="metadata" src="${esc(blobUrl)}"></audio><button title="${t('remove_title')}">${li('x',12)}</button>`;
      } else if(mediaKind==='video'){
        chip.innerHTML=`<span class="attach-chip-media">🎬 ${esc(f.name)}</span><video controls preload="metadata" src="${esc(blobUrl)}"></video><button title="${t('remove_title')}">${li('x',12)}</button>`;
      }
    } else {
      chip.innerHTML=`${li('paperclip',12)} ${esc(f.name)} <button title="${t('remove_title')}">${li('x',12)}</button>`;
    }
    chip.querySelector('button').onclick=()=>{
      // Revoke blob URL to avoid memory leak before removing
      if(chip.dataset.blobUrl) URL.revokeObjectURL(chip.dataset.blobUrl);
      S.pendingFiles.splice(i,1);renderTray();
    };
    tray.appendChild(chip);
  });
}
function _uploadTooLargeMessage(file){
  const fileSizeMb=Math.ceil(((file&&file.size)||0)/1024/1024);
  return t('upload_too_large',MAX_UPLOAD_MB,fileSizeMb);
}
function _showUploadTooLarge(file){
  const message=`${t('upload_failed')}${file&&file.name?file.name:'file'} \u2014 ${_uploadTooLargeMessage(file)}`;
  if(typeof setStatus==='function')setStatus(`\u274c ${message}`);
  else if(typeof showToast==='function')showToast(message,5000,'error');
}
function addFiles(files){
  for(const f of files){
    if(f&&f.size>MAX_UPLOAD_BYTES){_showUploadTooLarge(f);continue;}
    if(!S.pendingFiles.find(p=>p.name===f.name))S.pendingFiles.push(f);
  }
  renderTray();
}
const _uploadPendingFilesProgressBySession=new Map();
function _uploadPendingFilesCurrentSession(sessionId){
  return !!(!sessionId||(S.session&&S.session.session_id===sessionId));
}
function _uploadPendingFilesHideProgressBar(){
  const bar=$('uploadBar');const barWrap=$('uploadBarWrap');
  if(!bar||!barWrap)return;
  barWrap.classList.remove('active');
  bar.style.width='0%';
  if(barWrap.dataset)delete barWrap.dataset.uploadSessionId;
}
function _uploadPendingFilesShowProgressBar(owner,percent){
  const bar=$('uploadBar');const barWrap=$('uploadBarWrap');
  if(!bar||!barWrap)return;
  if(barWrap.dataset)barWrap.dataset.uploadSessionId=owner;
  barWrap.classList.add('active');
  bar.style.width=`${Math.max(0,Math.min(100,Number(percent)||0))}%`;
}
function _uploadPendingFilesSyncProgressForSession(sessionId){
  const owner=String(sessionId||'');
  const state=owner?_uploadPendingFilesProgressBySession.get(owner):null;
  if(state){_uploadPendingFilesShowProgressBar(owner,state.percent);return;}
  _uploadPendingFilesHideProgressBar();
}
function _uploadPendingFilesUpdateProgress(sessionId,percent){
  const bar=$('uploadBar');const barWrap=$('uploadBarWrap');
  if(!bar||!barWrap)return;
  const owner=String(sessionId||'');
  const activeForOwner=barWrap.dataset&&barWrap.dataset.uploadSessionId===owner;
  if(percent===null){
    if(owner)_uploadPendingFilesProgressBySession.delete(owner);
    if(activeForOwner){
      _uploadPendingFilesHideProgressBar();
    }
    return;
  }
  const clamped=Math.max(0,Math.min(100,Number(percent)||0));
  if(owner)_uploadPendingFilesProgressBySession.set(owner,{percent:clamped});
  if(!_uploadPendingFilesCurrentSession(sessionId)){
    if(activeForOwner)_uploadPendingFilesHideProgressBar();
    return;
  }
  _uploadPendingFilesShowProgressBar(owner,clamped);
}
async function uploadPendingFiles(options={}){
  const opts=options||{};
  const pendingFiles=Array.isArray(opts.files)?opts.files.filter(Boolean):[...(S.pendingFiles||[])];
  const sessionId=String(opts.sessionId||(S.session&&S.session.session_id)||'');
  if(!pendingFiles.length||!sessionId)return[];
  const clearPending=!(opts&&opts.clearPending===false);
  const names=[];let failures=0;
  _uploadPendingFilesUpdateProgress(sessionId,0);
  const total=pendingFiles.length;
  for(let i=0;i<total;i++){
    const f=pendingFiles[i];
    try{
      if(f&&f.size>MAX_UPLOAD_BYTES)throw new Error(_uploadTooLargeMessage(f));
      const fd=new FormData();
      fd.append('session_id',sessionId);fd.append('file',f,f.name);
      const isArchive=_ARCHIVE_EXTS.test(f.name);
      const url=new URL(isArchive?'api/upload/extract':'api/upload',document.baseURI||location.href).href;
      const res=await fetch(url,{method:'POST',credentials:'include',body:fd});
      if(_redirectIfUnauth(res)) return;
      if(!res.ok){const err=await res.text();throw new Error(err);}
      const data=await res.json();
      if(data.error)throw new Error(data.error);
      if(isArchive){
        names.push({name: data.dest, path: data.dest, extracted: data.extracted});
        if(typeof loadDir==='function'&&_uploadPendingFilesCurrentSession(sessionId))loadDir(S.currentDir||'.');
      }else{
        names.push({name: data.filename, path: data.path, mime: data.mime, size: data.size, is_image: !!data.is_image});
      }
    }catch(e){failures++;setStatus(`\u274c ${t('upload_failed')}${f.name} \u2014 ${e.message}`);}
    _uploadPendingFilesUpdateProgress(sessionId,Math.round((i+1)/total*100));
  }
  _uploadPendingFilesUpdateProgress(sessionId,null);
  if(clearPending&&_uploadPendingFilesCurrentSession(sessionId)){S.pendingFiles=[];renderTray();}
  else if(typeof renderTray==='function'&&_uploadPendingFilesCurrentSession(sessionId))renderTray();
  if(failures===total&&total>0)throw new Error(t('all_uploads_failed',total));
  // Show extraction summary
  const extracted=names.filter(n=>n.extracted);
  if(extracted.length)showToast(t('archive_extracted',extracted.reduce((s,n)=>s+n.extracted,0),extracted.length));
  return names;
}
