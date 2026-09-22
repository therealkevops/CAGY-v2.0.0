/**
 * ui-scroll.js — Scroll Pinning, Anchor Management & Auto-Scroll
 *
 * Extracted from ui.js (Sprint F-M4+, ADR 016).
 */

// ── Scroll pinning ──────────────────────────────────────────────────────────
// When streaming, auto-scroll only while the user is following the live tail.
// Any manual scroll up sets a sticky unpinned flag until the user scrolls back
// to the bottom (near-bottom hysteresis on downward motion) or clicks ↓.
// Programmatic scrolls are ignored via _programmaticScroll. Fixes #1469 / #1360 / #1731.
// #6606 ownership: ui.js/messages.js load before boot.js, and boot.js only
// assigns window._autoScrollFollow after its awaited settings request. Every
// consumer in this file (scroll listener, settle paths, scrollIfPinned,
// DOM-replace gate) can run before that assignment — a bare read would throw
// ReferenceError. Establish the default synchronously here (single owner);
// boot.js overwrites it with the saved setting after hydration. The typeof
// guard preserves an explicit saved `false` if boot.js already ran.
if(typeof window._autoScrollFollow==='undefined'){ window._autoScrollFollow=true; }
let _scrollPinned=true;
let _programmaticScroll=false;
let _programmaticScrollSetAt=0;
let _programmaticScrollResetTimer=0;
const PROGRAMMATIC_SCROLL_VALID_MS=150;
function _freshProgrammaticScrollActive(){
  if(!_programmaticScroll) return false;
  const age=performance.now()-_programmaticScrollSetAt;
  if(!Number.isFinite(age)||age<0||age>PROGRAMMATIC_SCROLL_VALID_MS){
    _programmaticScroll=false;
    return false;
  }
  return true;
}
function _deferClearProgrammaticScroll(ms){clearTimeout(_programmaticScrollResetTimer);_programmaticScrollResetTimer=setTimeout(()=>{_programmaticScroll=false;},ms||80);}
let _messageJumpScrollGeneration=0;
let _messageJumpScrollOwner=null;
let _messageJumpScrollSettleTimer=0;
function _messageJumpSessionId(){
  if(typeof S!=='undefined'&&S.session&&S.session.session_id) return String(S.session.session_id);
  return '';
}
function _scheduleMessageJumpScrollReconcile(generation,ms){
  if(!_messageJumpScrollOwner||_messageJumpScrollOwner.generation!==generation) return;
  clearTimeout(_messageJumpScrollSettleTimer);
  _messageJumpScrollSettleTimer=setTimeout(()=>_finishMessageJumpScroll(generation),ms||220);
}
function _beginMessageJumpScroll(container){
  const previous=_messageJumpScrollOwner;
  const preserved=previous?previous.preserved:{
    scrollPinned:_scrollPinned,
    messageUserUnpinned:_messageUserUnpinned,
    nearBottomCount:_nearBottomCount,
  };
  clearTimeout(_messageJumpScrollSettleTimer);
  const generation=++_messageJumpScrollGeneration;
  _messageJumpScrollOwner={generation,container,sessionId:_messageJumpSessionId(),preserved};
  // While the jump owner is active, temporarily release the reader pin so a
  // live token arriving between smooth-scroll frames cannot let scrollIfPinned()
  // reclaim the bottom and snap the reader off the jump target (#6621). The
  // preserved snapshot above is what _finishMessageJumpScroll() reconciles
  // against once the jump settles.
  _scrollPinned=false;
  _messageUserUnpinned=true;
  _nearBottomCount=0;
  _programmaticScroll=true;
  _programmaticScrollSetAt=performance.now();
  _scheduleMessageJumpScrollReconcile(generation,300);
  return generation;
}
function _finishMessageJumpScroll(generation){
  const owner=_messageJumpScrollOwner;
  if(!owner||owner.generation!==generation) return;
  if(owner.sessionId!==_messageJumpSessionId()){
    _cancelMessageJumpScroll();
    return;
  }
  clearTimeout(_messageJumpScrollSettleTimer);
  _messageJumpScrollSettleTimer=0;
  const container=owner.container;
  const maxTop=Math.max(0,container.scrollHeight-container.clientHeight);
  const top=Math.max(0,Math.min(Number(container.scrollTop)||0,maxTop));
  const bottomDistance=maxTop-top;
  if(bottomDistance>80){
    _scrollPinned=false;
    _messageUserUnpinned=true;
    _nearBottomCount=0;
  }else{
    _scrollPinned=owner.preserved.scrollPinned;
    _messageUserUnpinned=owner.preserved.messageUserUnpinned;
    _nearBottomCount=owner.preserved.nearBottomCount;
  }
  _lastScrollTop=container.scrollTop;
  _lastMessageClientHeight=container.clientHeight;
  _messageJumpScrollOwner=null;
  _programmaticScroll=false;
  if(typeof _syncScrollToBottomCue==='function'){
    _syncScrollToBottomCue(!_scrollPinned&&bottomDistance>80,{newMessage:_newMessageCueVisible});
  }
  if(typeof _updateSessionStartJumpButton==='function') _updateSessionStartJumpButton();
  // An external-session refresh deferred while the reader was temporarily
  // unpinned for the jump window (#6621) would otherwise stay stranded once the
  // near-tail reconciliation restores follow mode. Flush it when the terminal
  // state is genuinely pinned to the tail.
  if(_scrollPinned&&!_messageUserUnpinned&&typeof _flushDeferredActiveSessionExternalRefresh==='function'){
    _flushDeferredActiveSessionExternalRefresh();
  }
}
function _cancelMessageJumpScroll(){
  ++_messageJumpScrollGeneration;
  clearTimeout(_messageJumpScrollSettleTimer);
  _messageJumpScrollSettleTimer=0;
  // _beginMessageJumpScroll temporarily unpins the reader for the ownership
  // window (#6621); a cancel that isn't a reconcile must put the pre-jump pin
  // state back, or the transient unpinned state would leak. Callers that want a
  // different terminal pin state (e.g. scrollToBottom) set it right after this.
  const owner=_messageJumpScrollOwner;
  if(owner&&owner.preserved){
    _scrollPinned=owner.preserved.scrollPinned;
    _messageUserUnpinned=owner.preserved.messageUserUnpinned;
    _nearBottomCount=owner.preserved.nearBottomCount;
  }
  _messageJumpScrollOwner=null;
  _programmaticScroll=false;
}
let _nearBottomCount=0;
let _lastScrollTop=null;
let _lastMessageClientHeight=null;   // #4702: track scroller height to ignore iOS portrait toolbar-settle reflows (a clientHeight increase fires a scroll event with decreased scrollTop that is NOT a user scroll)
// Sticky-unpin model (#3343 supersedes #3330's proximity re-pin): once the user
// scrolls up, streaming stops auto-following until they return to the bottom or
// click ↓. The upward-intent TIMEOUT mechanism (_lastMessageUpwardIntentMs /
// MESSAGE_UPWARD_INTENT_MS) is removed — sticky-unpin makes it unnecessary.
// Keep the non-message intent timestamp at -Infinity so load-time isn't read as
// intent (the #3330 follow-up fix); 0 would mark the first NON_MESSAGE_SCROLL_INTENT
// window after load as suppressed.
let _lastNonMessageScrollIntentMs=-Infinity;
let _messageUserUnpinned=false;
// A monotonic ownership token lets delayed restores distinguish reader input
// that happened after a snapshot from input that merely happened recently.
let _messageScrollInputGeneration=0;
let _bottomSettleToken=0;
let _settleRAF=0;
let _settleRO=null;
let _settleTimer=0;
let _settleFinalTimer=0;
const NON_MESSAGE_SCROLL_INTENT_SUPPRESS_MS=350;
let _touchStartY=null;
let _messageTouchScrollActive=false;
let _lastMessageTouchScrollIntentMs=-Infinity;
let _deferredOlderMessagesTimer=0;
const MESSAGE_TOUCH_SCROLL_SUPPRESS_MS=1200;
// #4970 review: track recent LOW-DELTA upward message-pane wheel intent separately from
// the decisive deltaY<-30 sticky-unpin threshold. A gentle trackpad wheel
// (deltaY:-5) is real user intent but never crosses -30, so without this the
// post-render artifact suppression would swallow it for the whole window.
const MESSAGE_WHEEL_INTENT_SUPPRESS_MS=1200;
let _lastMessageWheelIntentMs=-Infinity;
let _lastMessageScrollIntentMs=-Infinity;
// #4970 review (greptile P1): keyboard scrolling of the message pane (PageUp/Down,
// arrows, Space, Home/End) fires a native `scroll` event with NO wheel/touch/
// scrollbar/non-message intent. Without recording it, a keyboard scroll-up inside
// the post-render artifact window is swallowed and live-follow snaps the reader
// back to the bottom. Stamp a generic scroll-key intent so the suppression skips it.
const MESSAGE_KEY_SCROLL_INTENT_SUPPRESS_MS=1200;
let _lastMessageKeyScrollIntentMs=-Infinity;
let _newMessageCueVisible=false;
let _lastMessageRenderAt=-Infinity;
function _recentMessageRenderArtifactWindow(ms){
  return performance.now()-_lastMessageRenderAt<(ms||1400);
}
function _cancelBottomSettle(){ _cancelMessageJumpScroll(); _bottomSettleToken++; if(_settleRO){ _settleRO.disconnect(); _settleRO=null; } clearTimeout(_settleTimer); clearTimeout(_settleFinalTimer); cancelAnimationFrame(_settleRAF); }
function _markMessageTouchScrollIntent(active=true){
  _messageTouchScrollActive=!!active;
  _lastMessageTouchScrollIntentMs=performance.now();
}
function _recentMessageTouchScrollIntent(){
  return _messageTouchScrollActive || performance.now()-_lastMessageTouchScrollIntentMs<MESSAGE_TOUCH_SCROLL_SUPPRESS_MS;
}
// #4970: true when the reader recently made ANY upward message-pane wheel
// motion, including gentle low-delta trackpad wheels below the -30 sticky-unpin
// threshold. The post-render artifact suppression must NOT fire when this is
// true, otherwise a real gentle scroll-up right after a render gets swallowed.
function _recentMessageWheelIntent(){
  return performance.now()-_lastMessageWheelIntentMs<MESSAGE_WHEEL_INTENT_SUPPRESS_MS;
}
function _recentMessageScrollIntent(){
  // This manual-reader snapshot signal intentionally excludes the raw
  // touch/key recency helpers: those also record near-tail events for render
  // artifact suppression. Only this timestamp is guarded by bottom distance.
  return performance.now()-_lastMessageScrollIntentMs<MESSAGE_WHEEL_INTENT_SUPPRESS_MS
    || (typeof _scrollbarDragActive!=='undefined'&&!!_scrollbarDragActive);
}
// #4970 review (greptile P1): true when the reader recently used the keyboard to
// scroll the message pane. Keyboard scrolls fire a native scroll event with no
// wheel/touch intent, so the post-render artifact suppression must skip them.
function _recentMessageKeyScrollIntent(){
  return performance.now()-_lastMessageKeyScrollIntentMs<MESSAGE_KEY_SCROLL_INTENT_SUPPRESS_MS;
}
function _isMessageReaderUnpinned(){
  return !!_messageUserUnpinned;
}
function _olderMessagesPrefetchReady(){
  const el=document.getElementById('messages');
  if(!el) return false;
  const olderPrefetchPx=Math.max(600,el.clientHeight*1.5);
  return _isSessionEndlessScrollEnabled()&&el.scrollTop<olderPrefetchPx && typeof _messagesTruncated!=='undefined' && _messagesTruncated && typeof _loadOlderMessages==='function';
}
function _scheduleDeferredOlderMessagesLoad(){
  clearTimeout(_deferredOlderMessagesTimer);
  _deferredOlderMessagesTimer=setTimeout(()=>{
    _deferredOlderMessagesTimer=0;
    if(_recentMessageTouchScrollIntent()){
      _scheduleDeferredOlderMessagesLoad();
      return;
    }
    if(_olderMessagesPrefetchReady()) _loadOlderMessages();
  },MESSAGE_TOUCH_SCROLL_SUPPRESS_MS+50);
}
function _recordNonMessageScrollIntent(e){
  const el=document.getElementById('messages');
  const target=e&&e.target;
  if(!el||!target) return;
  if(!el.contains(target)){ _lastNonMessageScrollIntentMs=performance.now(); return; }
  // Capture the guards before cancelling the active owner: cancellation clears
  // the programmatic flag and jump owner, but a low-delta upward wheel must still
  // count as reader takeover when it interrupted an owned scroll.
  const wheelUp=typeof e.deltaY==='number'&&e.deltaY<0;
  const guardedWheelUp=wheelUp&&_freshProgrammaticScrollActive();
  const jumpScrollOwned=typeof _messageJumpScrollOwner!=='undefined'&&!!_messageJumpScrollOwner;
  if(e.type==='touchmove'||(typeof e.deltaY==='number'&&e.deltaY!==0)){
    if(typeof _messageScrollInputGeneration==='number') _messageScrollInputGeneration++;
    if(jumpScrollOwned||e.type==='touchmove'||(typeof e.deltaY==='number'&&e.deltaY< -30)||guardedWheelUp){
      if(typeof _cancelBottomSettle==='function') _cancelBottomSettle();
    }
  }
  // Any message-pane scroll input that interrupts an active jump owner is a
  // reader takeover, regardless of direction or the programmatic-latch age
  // (#6621): _cancelBottomSettle above restores the pre-jump snapshot, so
  // without this a gentle wheel-up OR wheel-down (or a touch scroll) after the
  // latch expires would leave the reader pinned and let the next token snap to
  // the bottom. Establish the unpinned reader-owned state explicitly; a reader
  // who wants the bottom re-pins by reaching it (<=80px) or pressing End.
  if(jumpScrollOwned&&(wheelUp||e.type==='touchmove'||(typeof e.deltaY==='number'&&e.deltaY!==0))){
    _messageUserUnpinned=true;
    _scrollPinned=false;
    _nearBottomCount=0;
  }
  if(typeof e.deltaY==='number'&&e.deltaY<0) _lastMessageWheelIntentMs=performance.now();
  // Keep e.deltaY< -30 as the ordinary direct sticky-unpin threshold.
  if(e.type==='touchmove'||(typeof e.deltaY==='number'&&e.deltaY< -30)||guardedWheelUp){
    if(e.type==='touchmove') _markMessageTouchScrollIntent(true);
    if((typeof e.deltaY==='number'&&e.deltaY< -30)||guardedWheelUp){
      _messageUserUnpinned=true;
      _nearBottomCount=0;
      _scrollPinned=false;
    } else if(e.type==='touchmove'&&_touchStartY!==null&&e.touches&&e.touches[0]){
      // Detect upward-scroll intent on touch: dragging the finger DOWN the
      // screen scrolls the content up into earlier history (scrollTop
      // decreases) — the same "user scrolled away" signal the wheel deltaY<0
      // branch and the scroll listener's movedUp branch use. dy>0 = finger
      // moved down = reveal earlier content = unpin.
      const dy=e.touches[0].clientY-_touchStartY;
      if(dy>8){
        _messageUserUnpinned=true;
        _nearBottomCount=0;
        _scrollPinned=false;
      }
    }
  }
  // #4970: record ANY upward message-pane wheel motion as recent wheel intent,
  // including gentle low-delta trackpad wheels (e.g. deltaY:-5) that never reach
  // the decisive -30 sticky-unpin threshold below. The post-render artifact
  // suppression consults _recentMessageWheelIntent() so it cannot swallow a real
  // gentle scroll-up. Ordinarily this does NOT unpin on its own: the <-30 branch
  // and the scroll listener's movedUp branch remain the stable threshold. The
  // exception is an active programmatic-scroll guard. That guard returns before
  // its listener can see the native scroll event, so even a small capture-phase
  // upward wheel input must immediately stop live-tail follow (#6414).
  if(e.type==='touchmove'||(typeof e.deltaY==='number'&&e.deltaY!==0)){
    const bottomDistance=el.scrollHeight-el.scrollTop-el.clientHeight;
    if(bottomDistance>120) _lastMessageScrollIntentMs=performance.now();
  }
}
function _recentNonMessageScrollIntent(){
  return performance.now()-_lastNonMessageScrollIntentMs<NON_MESSAGE_SCROLL_INTENT_SUPPRESS_MS;
}
function _setScrollToBottomCueText(btn, textKey, labelKey){
  if(!btn) return;
  const label=btn.querySelector('.session-jump-btn__text');
  if(label){
    label.setAttribute('data-i18n',textKey);
    label.textContent=(typeof t==='function')?t(textKey):label.textContent;
  }
  btn.setAttribute('data-i18n-aria-label',labelKey);
  btn.setAttribute('data-i18n-title',labelKey);
  const accessible=(typeof t==='function')?t(labelKey):btn.getAttribute('aria-label')||'';
  if(accessible){
    btn.setAttribute('aria-label',accessible);
    btn.setAttribute('title',accessible);
  }
}
function _syncScrollToBottomCue(show, opts){
  const btn=$('scrollToBottomBtn');
  if(!btn) return;
  const newMessage=!!(opts&&opts.newMessage);
  btn.classList.toggle('scroll-to-bottom-btn--new-message',newMessage);
  if(newMessage) _setScrollToBottomCueText(btn,'session_new_message','session_new_message_label');
  else _setScrollToBottomCueText(btn,'session_jump_end','session_jump_end_label');
  btn.style.display=show?'flex':'none';
}
function _showNewMessageScrollCue(){
  _newMessageCueVisible=true;
  _syncScrollToBottomCue(true,{newMessage:true});
}
function _clearNewMessageScrollCue(){
  _newMessageCueVisible=false;
  _syncScrollToBottomCue(false,{newMessage:false});
}
function _maybeShowNewMessageScrollCue(scrollSnapshot){
  const el=document.getElementById('messages');
  if(!el||!scrollSnapshot) return;
  const previousHeight=Number(scrollSnapshot.scrollHeight)||0;
  const distance=el.scrollHeight-el.scrollTop-el.clientHeight;
  if(el.scrollHeight>previousHeight+24 && distance>80) _showNewMessageScrollCue();
  else _syncScrollToBottomCue(distance>80,{newMessage:_newMessageCueVisible});
}
if(typeof document!=='undefined'){
  document.addEventListener('wheel',_recordNonMessageScrollIntent,{capture:true,passive:true});
  document.addEventListener('touchmove',_recordNonMessageScrollIntent,{capture:true,passive:true});
  document.addEventListener('touchstart',function(e){
    const el=document.getElementById('messages');
    if(e.touches&&e.touches[0]) _touchStartY=e.touches[0].clientY;
    if(el&&e.target&&el.contains(e.target)) _markMessageTouchScrollIntent(true);
  },{capture:true,passive:true});
  document.addEventListener('touchend',function(){ _touchStartY=null; if(_messageTouchScrollActive) _markMessageTouchScrollIntent(false); },{capture:true,passive:true});
  document.addEventListener('touchcancel',function(){ _touchStartY=null; if(_messageTouchScrollActive) _markMessageTouchScrollIntent(false); },{capture:true,passive:true});
}
// Reset hook for session-switch — called from sessions.js loadSession() to
// prevent the new chat's first scroll comparing against the previous chat's
// scrollTop (Opus stage-302 SHOULD-FIX, #1731 follow-up).
function _resetScrollDirectionTracker(){
  _cancelMessageJumpScroll();
  _clearNewMessageScrollCue();
  _lastScrollTop=null;
  _lastMessageClientHeight=null;
  _messageUserUnpinned=false;
  _scrollPinned=true;
  _nearBottomCount=0;
  _touchStartY=null;
  _messageTouchScrollActive=false;
  _lastMessageTouchScrollIntentMs=-Infinity;
  // #4970 review: also clear low-delta wheel intent on session switch, else a
  // gentle wheel in the previous chat leaves _recentMessageWheelIntent() true
  // into the new chat's first post-render window — the artifact then isn't
  // suppressed, falls into movedUp, and falsely unpins live-follow.
  _lastMessageWheelIntentMs=-Infinity;
  _lastMessageScrollIntentMs=-Infinity;
  // #4970 review (greptile P1): same hygiene for keyboard scroll intent.
  _lastMessageKeyScrollIntentMs=-Infinity;
  clearTimeout(_deferredOlderMessagesTimer);
  _deferredOlderMessagesTimer=0;
}
function _resetStreamScrollFollow(){
  // Cancel any in-flight jump owner FIRST: a new stream is a definitive
  // pin-to-follow, and _cancelMessageJumpScroll() restores the pre-jump snapshot
  // (#6621), so it must run BEFORE the pinned-state assignments below or it would
  // undo them and silently disable auto-follow for the new stream.
  _cancelBottomSettle();
  _clearNewMessageScrollCue();
  _messageUserUnpinned=false;
  _scrollPinned=true;
  _nearBottomCount=0;
  _lastScrollTop=null;
  // #4970 review: clear low-delta wheel intent on fresh stream start too, else a
  // gentle upward wheel within the prior 1200ms can under-suppress a genuine
  // no-intent render artifact and silently disable live follow for the new stream.
  _lastMessageWheelIntentMs=-Infinity;
  _lastMessageScrollIntentMs=-Infinity;
  // #4970 review (greptile P1): same hygiene for keyboard scroll intent.
  _lastMessageKeyScrollIntentMs=-Infinity;
}
if(typeof window!=='undefined'){
  window._resetScrollDirectionTracker=_resetScrollDirectionTracker;
  window._resetStreamScrollFollow=_resetStreamScrollFollow;
}

/* ── Global Window Exports ─────────────────────────────────────────────────── */
if (typeof window !== 'undefined') {
  window._scrollPinned = typeof _scrollPinned !== 'undefined' ? _scrollPinned : undefined;
  window._programmaticScroll = typeof _programmaticScroll !== 'undefined' ? _programmaticScroll : undefined;
  window._programmaticScrollSetAt = typeof _programmaticScrollSetAt !== 'undefined' ? _programmaticScrollSetAt : undefined;
  window._programmaticScrollResetTimer = typeof _programmaticScrollResetTimer !== 'undefined' ? _programmaticScrollResetTimer : undefined;
  window.PROGRAMMATIC_SCROLL_VALID_MS = typeof PROGRAMMATIC_SCROLL_VALID_MS !== 'undefined' ? PROGRAMMATIC_SCROLL_VALID_MS : undefined;
  window._freshProgrammaticScrollActive = typeof _freshProgrammaticScrollActive !== 'undefined' ? _freshProgrammaticScrollActive : undefined;
  window._deferClearProgrammaticScroll = typeof _deferClearProgrammaticScroll !== 'undefined' ? _deferClearProgrammaticScroll : undefined;
  window._messageJumpScrollGeneration = typeof _messageJumpScrollGeneration !== 'undefined' ? _messageJumpScrollGeneration : undefined;
  window._messageJumpScrollOwner = typeof _messageJumpScrollOwner !== 'undefined' ? _messageJumpScrollOwner : undefined;
  window._messageJumpScrollSettleTimer = typeof _messageJumpScrollSettleTimer !== 'undefined' ? _messageJumpScrollSettleTimer : undefined;
  window._messageJumpSessionId = typeof _messageJumpSessionId !== 'undefined' ? _messageJumpSessionId : undefined;
  window._scheduleMessageJumpScrollReconcile = typeof _scheduleMessageJumpScrollReconcile !== 'undefined' ? _scheduleMessageJumpScrollReconcile : undefined;
  window._beginMessageJumpScroll = typeof _beginMessageJumpScroll !== 'undefined' ? _beginMessageJumpScroll : undefined;
  window._finishMessageJumpScroll = typeof _finishMessageJumpScroll !== 'undefined' ? _finishMessageJumpScroll : undefined;
  window._cancelMessageJumpScroll = typeof _cancelMessageJumpScroll !== 'undefined' ? _cancelMessageJumpScroll : undefined;
  window._nearBottomCount = typeof _nearBottomCount !== 'undefined' ? _nearBottomCount : undefined;
  window._lastScrollTop = typeof _lastScrollTop !== 'undefined' ? _lastScrollTop : undefined;
  window._lastMessageClientHeight = typeof _lastMessageClientHeight !== 'undefined' ? _lastMessageClientHeight : undefined;
  window._lastNonMessageScrollIntentMs = typeof _lastNonMessageScrollIntentMs !== 'undefined' ? _lastNonMessageScrollIntentMs : undefined;
  window._messageUserUnpinned = typeof _messageUserUnpinned !== 'undefined' ? _messageUserUnpinned : undefined;
  window._messageScrollInputGeneration = typeof _messageScrollInputGeneration !== 'undefined' ? _messageScrollInputGeneration : undefined;
  window._bottomSettleToken = typeof _bottomSettleToken !== 'undefined' ? _bottomSettleToken : undefined;
  window._settleRAF = typeof _settleRAF !== 'undefined' ? _settleRAF : undefined;
  window._settleRO = typeof _settleRO !== 'undefined' ? _settleRO : undefined;
  window._settleTimer = typeof _settleTimer !== 'undefined' ? _settleTimer : undefined;
  window._settleFinalTimer = typeof _settleFinalTimer !== 'undefined' ? _settleFinalTimer : undefined;
  window.NON_MESSAGE_SCROLL_INTENT_SUPPRESS_MS = typeof NON_MESSAGE_SCROLL_INTENT_SUPPRESS_MS !== 'undefined' ? NON_MESSAGE_SCROLL_INTENT_SUPPRESS_MS : undefined;
  window._touchStartY = typeof _touchStartY !== 'undefined' ? _touchStartY : undefined;
  window._messageTouchScrollActive = typeof _messageTouchScrollActive !== 'undefined' ? _messageTouchScrollActive : undefined;
  window._lastMessageTouchScrollIntentMs = typeof _lastMessageTouchScrollIntentMs !== 'undefined' ? _lastMessageTouchScrollIntentMs : undefined;
  window._deferredOlderMessagesTimer = typeof _deferredOlderMessagesTimer !== 'undefined' ? _deferredOlderMessagesTimer : undefined;
  window.MESSAGE_TOUCH_SCROLL_SUPPRESS_MS = typeof MESSAGE_TOUCH_SCROLL_SUPPRESS_MS !== 'undefined' ? MESSAGE_TOUCH_SCROLL_SUPPRESS_MS : undefined;
  window.MESSAGE_WHEEL_INTENT_SUPPRESS_MS = typeof MESSAGE_WHEEL_INTENT_SUPPRESS_MS !== 'undefined' ? MESSAGE_WHEEL_INTENT_SUPPRESS_MS : undefined;
  window._lastMessageWheelIntentMs = typeof _lastMessageWheelIntentMs !== 'undefined' ? _lastMessageWheelIntentMs : undefined;
  window._lastMessageScrollIntentMs = typeof _lastMessageScrollIntentMs !== 'undefined' ? _lastMessageScrollIntentMs : undefined;
  window.MESSAGE_KEY_SCROLL_INTENT_SUPPRESS_MS = typeof MESSAGE_KEY_SCROLL_INTENT_SUPPRESS_MS !== 'undefined' ? MESSAGE_KEY_SCROLL_INTENT_SUPPRESS_MS : undefined;
  window._lastMessageKeyScrollIntentMs = typeof _lastMessageKeyScrollIntentMs !== 'undefined' ? _lastMessageKeyScrollIntentMs : undefined;
  window._newMessageCueVisible = typeof _newMessageCueVisible !== 'undefined' ? _newMessageCueVisible : undefined;
  window._lastMessageRenderAt = typeof _lastMessageRenderAt !== 'undefined' ? _lastMessageRenderAt : undefined;
  window._recentMessageRenderArtifactWindow = typeof _recentMessageRenderArtifactWindow !== 'undefined' ? _recentMessageRenderArtifactWindow : undefined;
  window._cancelBottomSettle = typeof _cancelBottomSettle !== 'undefined' ? _cancelBottomSettle : undefined;
  window._markMessageTouchScrollIntent = typeof _markMessageTouchScrollIntent !== 'undefined' ? _markMessageTouchScrollIntent : undefined;
  window._recentMessageTouchScrollIntent = typeof _recentMessageTouchScrollIntent !== 'undefined' ? _recentMessageTouchScrollIntent : undefined;
  window._recentMessageWheelIntent = typeof _recentMessageWheelIntent !== 'undefined' ? _recentMessageWheelIntent : undefined;
  window._recentMessageScrollIntent = typeof _recentMessageScrollIntent !== 'undefined' ? _recentMessageScrollIntent : undefined;
  window._recentMessageKeyScrollIntent = typeof _recentMessageKeyScrollIntent !== 'undefined' ? _recentMessageKeyScrollIntent : undefined;
  window._isMessageReaderUnpinned = typeof _isMessageReaderUnpinned !== 'undefined' ? _isMessageReaderUnpinned : undefined;
  window._olderMessagesPrefetchReady = typeof _olderMessagesPrefetchReady !== 'undefined' ? _olderMessagesPrefetchReady : undefined;
  window._scheduleDeferredOlderMessagesLoad = typeof _scheduleDeferredOlderMessagesLoad !== 'undefined' ? _scheduleDeferredOlderMessagesLoad : undefined;
  window._recordNonMessageScrollIntent = typeof _recordNonMessageScrollIntent !== 'undefined' ? _recordNonMessageScrollIntent : undefined;
  window._recentNonMessageScrollIntent = typeof _recentNonMessageScrollIntent !== 'undefined' ? _recentNonMessageScrollIntent : undefined;
  window._setScrollToBottomCueText = typeof _setScrollToBottomCueText !== 'undefined' ? _setScrollToBottomCueText : undefined;
  window._syncScrollToBottomCue = typeof _syncScrollToBottomCue !== 'undefined' ? _syncScrollToBottomCue : undefined;
  window._showNewMessageScrollCue = typeof _showNewMessageScrollCue !== 'undefined' ? _showNewMessageScrollCue : undefined;
  window._clearNewMessageScrollCue = typeof _clearNewMessageScrollCue !== 'undefined' ? _clearNewMessageScrollCue : undefined;
  window._maybeShowNewMessageScrollCue = typeof _maybeShowNewMessageScrollCue !== 'undefined' ? _maybeShowNewMessageScrollCue : undefined;
  window._resetScrollDirectionTracker = typeof _resetScrollDirectionTracker !== 'undefined' ? _resetScrollDirectionTracker : undefined;
  window._resetStreamScrollFollow = typeof _resetStreamScrollFollow !== 'undefined' ? _resetStreamScrollFollow : undefined;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    _scrollPinned,
    _programmaticScroll,
    _programmaticScrollSetAt,
    _programmaticScrollResetTimer,
    PROGRAMMATIC_SCROLL_VALID_MS,
    _freshProgrammaticScrollActive,
    _deferClearProgrammaticScroll,
    _messageJumpScrollGeneration,
    _messageJumpScrollOwner,
    _messageJumpScrollSettleTimer,
    _messageJumpSessionId,
    _scheduleMessageJumpScrollReconcile,
    _beginMessageJumpScroll,
    _finishMessageJumpScroll,
    _cancelMessageJumpScroll,
    _nearBottomCount,
    _lastScrollTop,
    _lastMessageClientHeight,
    _lastNonMessageScrollIntentMs,
    _messageUserUnpinned,
    _messageScrollInputGeneration,
    _bottomSettleToken,
    _settleRAF,
    _settleRO,
    _settleTimer,
    _settleFinalTimer,
    NON_MESSAGE_SCROLL_INTENT_SUPPRESS_MS,
    _touchStartY,
    _messageTouchScrollActive,
    _lastMessageTouchScrollIntentMs,
    _deferredOlderMessagesTimer,
    MESSAGE_TOUCH_SCROLL_SUPPRESS_MS,
    MESSAGE_WHEEL_INTENT_SUPPRESS_MS,
    _lastMessageWheelIntentMs,
    _lastMessageScrollIntentMs,
    MESSAGE_KEY_SCROLL_INTENT_SUPPRESS_MS,
    _lastMessageKeyScrollIntentMs,
    _newMessageCueVisible,
    _lastMessageRenderAt,
    _recentMessageRenderArtifactWindow,
    _cancelBottomSettle,
    _markMessageTouchScrollIntent,
    _recentMessageTouchScrollIntent,
    _recentMessageWheelIntent,
    _recentMessageScrollIntent,
    _recentMessageKeyScrollIntent,
    _isMessageReaderUnpinned,
    _olderMessagesPrefetchReady,
    _scheduleDeferredOlderMessagesLoad,
    _recordNonMessageScrollIntent,
    _recentNonMessageScrollIntent,
    _setScrollToBottomCueText,
    _syncScrollToBottomCue,
    _showNewMessageScrollCue,
    _clearNewMessageScrollCue,
    _maybeShowNewMessageScrollCue,
    _resetScrollDirectionTracker,
    _resetStreamScrollFollow,
  };
}
