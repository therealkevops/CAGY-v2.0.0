/**
 * ui-model-picker.js — Model Picker, Provider Quota, Reasoning Chip & Toolsets.
 *
 * Extracted from ui.js as part of Sprint F-M2 (ADR 016).
 *
 * Provides:
 *   - Provider quota indicator (renderProviderQuotaIndicator, refreshProviderQuotaIndicator)
 *   - Smart model resolver and dropdown (populateModelDropdown, syncModelChip, toggleModelDropdown,
 *     renderModelDropdown, selectModelFromDropdown, closeModelDropdown)
 *   - Settings model picker (mountSettingsModelPicker, syncSettingsModelChip, etc.)
 *   - Model persistence (MODEL_STATE_KEY, _readPersistedModelState, _writePersistedModelState)
 *   - Pending session model tracking (_rememberPendingSessionModel, _readPendingSessionModel)
 *   - Reasoning effort chip (syncReasoningChip, fetchReasoningChip, toggleReasoningDropdown)
 *   - Session toolsets chip (syncToolsetsChip, toggleToolsetsDropdown, closeToolsetsDropdown)
 *   - Composer footer fit and mobile config (openMobileComposerConfig, openComposerContextMenu)
 *
 * Loaded before ui.js in index.html with defer.
 */

// ── Ambient provider quota indicator (#1766) ────────────────────────────────
let _providerQuotaRefreshInFlight=false;

function _formatQuotaMoneyShort(value){
  const n=Number(value);
  if(!Number.isFinite(n)) return '';
  if(Math.abs(n)>=100) return '$'+n.toFixed(0);
  if(Math.abs(n)>=10) return '$'+n.toFixed(1);
  return '$'+n.toFixed(2);
}
function _formatQuotaPercentShort(value){
  const n=Number(value);
  if(!Number.isFinite(n)) return '';
  return Math.max(0,Math.min(100,n)).toFixed(0)+'%';
}
function _providerQuotaIndicatorText(status){
  if(!status||status.status!=='available') return null;
  const provider=status.display_name||status.provider||'Provider';
  const accountLimits=status.account_limits||null;
  if(accountLimits&&Array.isArray(accountLimits.windows)&&accountLimits.windows.length){
    const w=accountLimits.windows.find(x=>x&&Number.isFinite(Number(x.remaining_percent)))||accountLimits.windows[0];
    const remaining=_formatQuotaPercentShort(w&&w.remaining_percent);
    if(remaining) return {label:remaining, title:provider+' — '+(status.message||'Provider usage loaded')+' — '+remaining+' remaining'};
  }
  const quota=status.quota||null;
  if(quota){
    const remaining=_formatQuotaMoneyShort(quota.limit_remaining);
    const used=_formatQuotaMoneyShort(quota.usage);
    const limit=_formatQuotaMoneyShort(quota.limit);
    if(remaining){
      const parts=[];
      if(used) parts.push('used '+used);
      if(limit) parts.push('limit '+limit);
      return {label:remaining, title:provider+' — '+(status.message||'Provider quota loaded')+(parts.length?' — '+parts.join(' · '):'')};
    }
  }
  return null;
}
function renderProviderQuotaIndicator(status){
  const chip=$('providerQuotaChip');
  const label=$('providerQuotaChipLabel');
  const mobileAction=$('composerMobileQuotaAction');
  const mobileLabel=$('composerMobileQuotaLabel');
  if(!chip||!label) return;
  // Hide entirely when the user has disabled the ambient quota chip in Settings.
  // Boot defaults this on; an explicit false preference suppresses it.
  if(window._showQuotaChip!==true){
    chip.hidden=true;
    label.textContent='';
    chip.removeAttribute('title');
    if(mobileAction){mobileAction.style.display='none';mobileAction.removeAttribute('title');}
    if(mobileLabel) mobileLabel.textContent='';
    return;
  }
  const text=_providerQuotaIndicatorText(status);
  if(!text||status.status!=='available'||(!status.quota&&!status.account_limits)){
    chip.hidden=true;
    label.textContent='';
    chip.removeAttribute('title');
    if(mobileAction){mobileAction.style.display='none';mobileAction.removeAttribute('title');}
    if(mobileLabel) mobileLabel.textContent='';
    return;
  }
  label.textContent=text.label;
  chip.title=text.title;
  chip.hidden=false;
  if(mobileAction){mobileAction.style.display='';mobileAction.title=text.title;}
  if(mobileLabel) mobileLabel.textContent=text.label;
}
async function refreshProviderQuotaIndicator(){
  // Short-circuit before the fetch when the chip is disabled — no point asking
  // the server for quota data the UI will throw away.
  if(window._showQuotaChip!==true){
    const chip=$('providerQuotaChip');
    if(chip){chip.hidden=true;chip.removeAttribute('title');}
    const mobileAction=$('composerMobileQuotaAction');
    if(mobileAction){mobileAction.style.display='none';mobileAction.removeAttribute('title');}
    const mobileLabel=$('composerMobileQuotaLabel');
    if(mobileLabel) mobileLabel.textContent='';
    return;
  }
  if(_providerQuotaRefreshInFlight) return;
  _providerQuotaRefreshInFlight=true;
  try{
    const status=await api('/api/provider/quota');
    renderProviderQuotaIndicator(status);
  }catch(_e){
    renderProviderQuotaIndicator(null);
  }finally{
    _providerQuotaRefreshInFlight=false;
  }
}
window.addEventListener('visibilitychange',()=>{
  if(document.visibilityState==='visible'&&typeof refreshProviderQuotaIndicator==='function') refreshProviderQuotaIndicator();
});

// Dynamic model labels -- populated by populateModelDropdown(), fallback to static map
let _dynamicModelLabels={};
window._configuredModelBadges=window._configuredModelBadges||{};
const MODEL_STATE_KEY='agy-webui-model-state';
const PENDING_SESSION_MODEL_PREFIX='agy-webui-pending-session-model:';
const PENDING_SESSION_MODEL_MAX_AGE_MS=10*60*1000;

// ── Smart model resolver ────────────────────────────────────────────────────
// Finds the best matching option value in a <select> for a given model ID.
// Handles mismatches like 'claude-sonnet-4-6' vs 'anthropic/claude-sonnet-4.6'.
// When a preferred provider is supplied, duplicate normalized IDs prefer that
// provider's option so Settings/profile rehydration doesn't snap back to the
// first colliding entry.
function _getOptionProviderId(opt){
  if(!opt) return '';
  if(opt.dataset && opt.dataset.provider) return opt.dataset.provider;
  const group=opt.parentElement;
  if(group && group.tagName==='OPTGROUP' && group.dataset && group.dataset.provider){
    return group.dataset.provider;
  }
  const value=String(opt.value||'');
  if(value.startsWith('@') && value.includes(':')) return value.slice(1,value.lastIndexOf(':'));
  return '';
}
function _providerFromModelValue(modelId){
  const value=String(modelId||'').trim();
  if(value.startsWith('@')&&value.includes(':')) return value.slice(1,value.lastIndexOf(':'));
  return '';
}
function _modelPickerOptionIdentity(modelId, providerId){
  let value=String(modelId||'');
  const provider=String(providerId||'').trim();
  if(value.startsWith('@')&&value.includes(':')){
    const exactPrefix=provider ? `@${provider}:` : '';
    if(exactPrefix && value.toLowerCase().startsWith(exactPrefix.toLowerCase())){
      value=value.substring(exactPrefix.length);
    }else if(value.startsWith('@custom:')){
      const namedProvider=value.substring('@custom:'.length);
      const splitAt=namedProvider.indexOf(':');
      value=splitAt>=0 ? namedProvider.substring(splitAt+1) : namedProvider;
    }else{
      value=value.substring(value.indexOf(':')+1);
    }
  }
  return value.replace(/-/g,'.').toLowerCase();
}
function _deduplicateModelPickerOptions(sel,selectedValue){
  if(!sel||!sel.querySelectorAll) return 0;
  let removed=0;
  for(const group of sel.querySelectorAll('optgroup')){
    const options=Array.from(group.children||[]).filter(opt=>opt&&opt.tagName==='OPTION');
    const byIdentity=new Map();
    for(const opt of options){
      const identity=_modelPickerOptionIdentity(opt.value,_getOptionProviderId(opt));
      if(!identity) continue;
      if(!byIdentity.has(identity)) byIdentity.set(identity,[]);
      byIdentity.get(identity).push(opt);
    }
    for(const candidates of byIdentity.values()){
      if(candidates.length<2) continue;
      const selected=candidates.find(opt=>opt.value===selectedValue);
      const routable=candidates.find(opt=>String(opt.value||'').startsWith('@'));
      const survivor=selected||routable||candidates[0];
      for(const opt of candidates){
        if(opt===survivor) continue;
        group.removeChild(opt);
        removed++;
      }
    }
  }
  return removed;
}
function _providerSkipsModelMismatchWarning(providerId){
  const p=String(providerId||'').toLowerCase();
  return !p||p==='custom'||p.startsWith('custom:')||p==='openrouter';
}
function _providerDefersMissingModelFallback(providerId){
  const p=String(providerId||'').toLowerCase();
  // Named custom providers and OpenRouter can legitimately route vendor-prefixed
  // model IDs that are not present in the current static catalog. Do not
  // silently rewrite those sessions to the default just because the option has
  // not been hydrated yet (#2405).
  return p.startsWith('custom:')||p==='openrouter';
}
function _modelStateForSelect(sel, modelId){
  const value=String(modelId||'').trim();
  if(!value) return {model:'',model_provider:null};
  const explicitProvider=_providerFromModelValue(value);
  if(explicitProvider){
    const selected=sel&&sel.options
      ?Array.from(sel.options).find(o=>String(o.value||'')===value)
      :null;
    const routedModel=selected&&selected.dataset&&selected.dataset.model;
    // Read the provider from the matched option's authoritative data-provider
    // rather than re-parsing the value at its LAST colon: a colon-bearing model
    // id (e.g. model-a:free) synthesized as @custom:backup:model-a:free would
    // otherwise mis-parse to provider "custom:backup:model-a" (#6221 re-gate).
    const routedProvider=selected?String(_getOptionProviderId(selected)||'').trim():'';
    return {model:routedModel||value,model_provider:routedProvider||explicitProvider};
  }
  // Resolve the provider from the option whose VALUE matches the requested
  // model — never blindly from sel.selectedOptions[0] (#5567). During a profile
  // /tab switch or a model-list rebuild the dropdown transiently still has the
  // PREVIOUS profile's default option selected (e.g. an ollama model), so reading
  // selectedOptions[0] would stamp that foreign provider onto a model it doesn't
  // own — which is then persisted into the session's model_provider and re-sent
  // on every turn, bricking it with a "Provider 'X'…no API key" error for a
  // provider the session never used.
  let opt=null;
  const selected=sel&&sel.selectedOptions&&sel.selectedOptions[0];
  // Prefer the currently-selected option ONLY when it actually is the requested
  // model — this preserves the user's exact pick in the same-value/different-
  // provider collision case (two providers offering the same model id).
  if(selected&&String(selected.value||'')===value){
    opt=selected;
  }else if(sel&&sel.options){
    opt=Array.from(sel.options).find(o=>String(o.value||'')===value)||null;
  }
  const provider=String(_getOptionProviderId(opt)||'').trim();
  return {model:value,model_provider:(provider&&provider!=='default')?provider:null};
}
function _captureModelDropdownSelection(sel){
  if(!sel||!sel.value) return null;
  try{
    const state=_modelStateForSelect(sel,sel.value);
    if(state&&state.model) return state;
  }catch(_){}
  return {model:String(sel.value||''),model_provider:null};
}
function _modelProviderForSend(modelId){
  const sessionProvider=(S&&S.session&&S.session.model_provider)||null;
  if(sessionProvider) return sessionProvider;
  const model=String(modelId||'').trim();
  if(!model) return null;
  const explicitProvider=typeof _providerFromModelValue==='function'
    ? _providerFromModelValue(model)
    : '';
  if(explicitProvider) return explicitProvider;
  const sel=typeof $==='function' ? $('modelSelect') : null;
  if(sel&&String(sel.value||'').trim()===model&&typeof _modelStateForSelect==='function'){
    try{
      const dropdownState=_modelStateForSelect(sel,sel.value);
      if(dropdownState&&String(dropdownState.model||'').trim()===model){
        return dropdownState.model_provider||null;
      }
    }catch(_){}
  }
  if(typeof _readPersistedModelState==='function'){
    try{
      const persisted=_readPersistedModelState();
      if(persisted&&String(persisted.model||'').trim()===model){
        return persisted.model_provider||null;
      }
    }catch(_){}
  }
  return null;
}
function _reconcileModelDropdownSelection(sel,data,previousState,opts){
  if(!sel) return null;
  const activeSession=(typeof S!=='undefined'&&S&&S.session)?S.session:null;
  // Fresh boot is the only path where the profile/server default intentionally
  // beats a browser-persisted or static fallback value. Every other model-list
  // rebuild should preserve the loaded session model or the user's current
  // in-page selection when it still exists in the refreshed catalog.
  const shouldApplyBootDefault=!!(opts&&opts.preferProfileDefaultOnFreshBoot);

  // Helper: apply the requested model, but if it is missing from the current
  // catalog (cross-provider selection after a partial/timed-out rebuild), inject
  // it as a custom option instead of returning null and letting the browser
  // silently snap to the first <option>. _ensureModelOptionInDropdown already
  // tries _applyModelToDropdown first, so delegate to it (single scan) and keep
  // the plain-apply fallback for the unlikely case it is unavailable.
  const _applyOrEnsure = function(modelId, providerId) {
    if (typeof _ensureModelOptionInDropdown === 'function') {
      return _ensureModelOptionInDropdown(modelId, sel, providerId);
    }
    return _applyModelToDropdown(modelId, sel, providerId);
  };

  if(shouldApplyBootDefault && data&&data.default_model && !(activeSession&&activeSession.model)){
    return _applyOrEnsure(data.default_model, data.active_provider||null);
  }
  if(activeSession&&activeSession.model){
    return _applyOrEnsure(activeSession.model, activeSession.model_provider||null);
  }
  if(previousState&&previousState.model){
    return _applyOrEnsure(previousState.model, previousState.model_provider||null);
  }
  return null;
}
function _providerQualifiedModelValueForSelect(sel, modelId){
  return _modelStateForSelect(sel,modelId).model;
}
function _readPersistedModelState(){
  try{
    const raw=localStorage.getItem(MODEL_STATE_KEY);
    if(raw){
      const parsed=JSON.parse(raw);
      if(parsed&&parsed.model){
        return {
          model:String(parsed.model||''),
          model_provider:parsed.model_provider?String(parsed.model_provider):(_providerFromModelValue(parsed.model)||null),
        };
      }
    }
  }catch(_){}
  const legacy=localStorage.getItem('agy-webui-model');
  if(!legacy) return null;
  return {model:legacy,model_provider:_providerFromModelValue(legacy)||null};
}
function _writePersistedModelState(model, modelProvider){
  const value=String(model||'').trim();
  const provider=modelProvider?String(modelProvider).trim():(_providerFromModelValue(value)||null);
  if(!value){
    localStorage.removeItem('agy-webui-model');
    localStorage.removeItem(MODEL_STATE_KEY);
    return;
  }
  localStorage.setItem('agy-webui-model', value);
  try{
    localStorage.setItem(MODEL_STATE_KEY, JSON.stringify({model:value,model_provider:provider||null}));
  }catch(_){}
}
function _clearPersistedModelState(){
  localStorage.removeItem('agy-webui-model');
  localStorage.removeItem(MODEL_STATE_KEY);
}
function _pendingSessionModelKey(sessionId){
  return PENDING_SESSION_MODEL_PREFIX+String(sessionId||'');
}
function _rememberPendingSessionModel(sessionId, model, modelProvider){
  const sid=String(sessionId||'').trim();
  const value=String(model||'').trim();
  if(!sid||!value) return;
  const provider=modelProvider?String(modelProvider).trim():(_providerFromModelValue(value)||null);
  try{
    sessionStorage.setItem(_pendingSessionModelKey(sid), JSON.stringify({
      model:value,
      model_provider:provider||null,
      saved_at:Date.now(),
    }));
  }catch(_){}
}
function _readPendingSessionModel(sessionId){
  const sid=String(sessionId||'').trim();
  if(!sid) return null;
  try{
    const raw=sessionStorage.getItem(_pendingSessionModelKey(sid));
    if(!raw) return null;
    const parsed=JSON.parse(raw);
    const model=String(parsed&&parsed.model||'').trim();
    if(!model){
      sessionStorage.removeItem(_pendingSessionModelKey(sid));
      return null;
    }
    const savedAt=Number(parsed.saved_at||0);
    if(savedAt&&Date.now()-savedAt>PENDING_SESSION_MODEL_MAX_AGE_MS){
      sessionStorage.removeItem(_pendingSessionModelKey(sid));
      return null;
    }
    return {
      model,
      model_provider:parsed&&parsed.model_provider?String(parsed.model_provider):(_providerFromModelValue(model)||null),
    };
  }catch(_){
    try{sessionStorage.removeItem(_pendingSessionModelKey(sid));}catch(__){}
    return null;
  }
}
function _clearPendingSessionModel(sessionId){
  const sid=String(sessionId||'').trim();
  if(!sid) return;
  try{sessionStorage.removeItem(_pendingSessionModelKey(sid));}catch(_){}
}
// #5924: the recovery-send deliberate-pick signal. Returns {model, model_provider}
// ONLY when the active session's own model is a genuine non-default pick vs the
// profile default — the same signal send()'s persistent-pick path (_isCrossProviderPick)
// uses, generalized to same-provider non-default picks too. Used by the recovery
// paths (cmdRetry / submitEdit) to decide whether to re-arm the single-shot
// explicit-pick marker: the marker is consumed by the failed send before we reach
// recovery, so we can't read it back, and comparing _chatPayloadModel() to itself
// either false-negatives (an already-applied pick looks unchanged) or false-positives
// (provider inference manufactures a "change"). A non-default session model is the
// durable, inference-free evidence of a real pick. Returns null (no re-arm → the
// server's compatible-model resolution runs) when the session is on the default.
function _deliberateSessionModelPick(sessionId){
  if(!S.session||S.session.session_id!==sessionId) return null;
  const model=String(S.session.model||'').trim();
  if(!model) return null;
  // Require SESSION-OWNED provider evidence — a stored model_provider on the
  // session itself. Do NOT infer a provider from the model string: an
  // unreachable/renamed model like "@removed:mistral-large" with no stored
  // provider must NOT count as a deliberate pick (round-2/3 false-positive).
  const provider=S.session.model_provider?String(S.session.model_provider).trim():'';
  if(!provider) return null;
  // Require a KNOWN profile default to compare against. If we don't know the
  // default (empty window._defaultModel), we can't prove this is a non-default
  // pick, so fail closed → no re-arm (server compatible-model resolution runs).
  const defaultModel=(typeof window!=='undefined'&&window._defaultModel)?String(window._defaultModel):'';
  const activeProvider=(typeof window!=='undefined'&&window._activeProvider)?String(window._activeProvider):'';
  if(!defaultModel||!activeProvider) return null;
  // Non-default = a different model OR a different provider than the profile
  // default. A session sitting exactly on the profile default is NOT a pick.
  const isDefault=(model===defaultModel)&&(provider===activeProvider);
  if(isDefault) return null;
  return {model, model_provider:provider};
}
// #5924: re-arm the single-shot explicit-pick marker from a recovery pick, but
// ONLY if it's still safe at fire time. Guards the SILENT same-session race where
// the user changes the model DURING the recovery's awaits: (1) the session must
// still be the captured one; (2) the session's CURRENT model/provider must still
// equal the captured pick (a mid-flight change means the pick is stale — skip);
// (3) never clobber a NEWER pending marker (an onchange during the await already
// wrote the authoritative one). Returns true if it re-armed.
function _reArmRecoveryPick(sessionId, pick){
  if(!pick||!pick.model) return false;
  if(!S.session||S.session.session_id!==sessionId) return false;
  // Current session state must still match the captured pick (no mid-flight change).
  if(String(S.session.model||'')!==String(pick.model||'')
     ||String(S.session.model_provider||'')!==String(pick.model_provider||'')) return false;
  // Do not overwrite a newer marker written by an onchange during the await.
  if(typeof _readPendingSessionModel==='function'){
    const existing=_readPendingSessionModel(sessionId);
    if(existing&&existing.model
       &&(String(existing.model)!==String(pick.model)
          ||String(existing.model_provider||'')!==String(pick.model_provider||''))) return false;
  }
  if(typeof _rememberPendingSessionModel==='function'){
    _rememberPendingSessionModel(sessionId, pick.model, pick.model_provider);
    return true;
  }
  return false;
}
function _applyPendingSessionModelForSession(sessionId){
  if(!S.session||S.session.session_id!==sessionId) return false;
  const pending=_readPendingSessionModel(sessionId);
  if(!pending) return false;
  const sameModel=String(S.session.model||'')===pending.model;
  const sameProvider=String(S.session.model_provider||'')===String(pending.model_provider||'');
  if(sameModel&&sameProvider){
    _clearPendingSessionModel(sessionId);
    return false;
  }
  S.session.model=pending.model;
  S.session.model_provider=pending.model_provider||null;
  const retry=_persistSessionModelCorrection(pending.model,pending.model_provider||null,{propagateErrors:true});
  if(retry&&typeof retry.then==='function'){
    retry.then(()=>_clearPendingSessionModel(sessionId)).catch(()=>{});
  }
  return true;
}
function _findModelInDropdown(modelId, sel, preferredProviderId){
  if(!modelId||!sel) return null;
  const options=Array.from(sel.options);
  const opts=options.map(o=>o.value);
  // 0. Exact match — highest priority when it doesn't conflict with a
  // cross-provider preference (#3360, guarded for #1228/#1313).
  // When all models share the same provider (e.g. a custom proxy),
  // normalization can collapse distinct multi-slash IDs to the same key
  // and options.find() returns whichever appears first in the DOM instead
  // of the exact value.  But when the exact option belongs to a *different*
  // provider than the preferred one, we must fall through to the provider-
  // aware match so rehydration doesn't snap to the wrong provider row.
  if(opts.includes(modelId)){
    const exactOpt=options.find(o=>o.value===modelId);
    const exactProv=exactOpt?_getOptionProviderId(exactOpt).toLowerCase():'';
    const pref=String(preferredProviderId||'').toLowerCase();
    if(!pref || !exactProv || exactProv===pref) return modelId;
  }
  // 1. Restore lookup keeps the older hierarchy-preserving matcher instead of
  // the picker-dedup identity, so missing qualified models do not substitute a
  // different suffix-sharing sibling.
  const norm=s=>String(s||'')
    .toLowerCase()
    .replace(/^@([^:]+:)+/,'')
    .replace(/^[^/]+\//,'')
    .replace(/-/g,'.');
  const target=norm(modelId);
  let explicitProvider='';
  const rawModel=String(modelId||'');
  if(rawModel.startsWith('@')&&rawModel.includes(':')){
    explicitProvider=rawModel.slice(1,rawModel.lastIndexOf(':'));
  }
  const preferred=String(preferredProviderId||explicitProvider||'').toLowerCase();
  if(preferred){
    if(preferred==='custom'||preferred.startsWith('custom:')){
      // A slash is part of a custom endpoint's upstream model ID, not a
      // provider namespace. Match the exact routed ID (allowing only the
      // WebUI's @provider: wrapper and dash/dot spelling compatibility).
      const routeNorm=value=>{
        let routed=String(value||'');
        const prefix=`@${preferred}:`;
        if(routed.toLowerCase().startsWith(prefix)) routed=routed.slice(prefix.length);
        return routed.toLowerCase().replace(/-/g,'.');
      };
      const providerOptions=options.filter(o=>_getOptionProviderId(o).toLowerCase()===preferred);
      const providerMatch=providerOptions.find(o=>routeNorm(o.value)===routeNorm(rawModel));
      if(providerMatch) return providerMatch.value;
      // Legacy sessions may store only the bare suffix of a routed custom
      // option. Preserve #6195's provider-hinted repair, but only for an
      // explicit @provider: row; an unwrapped slash ID belongs to the active
      // endpoint and must not substitute for a distinct bare model.
      if(!rawModel.includes('/')&&!rawModel.startsWith('@')){
        const prefix=`@${preferred}:`;
        const suffixMatches=providerOptions.filter(o=>
          String(o.value||'').toLowerCase().startsWith(prefix)
          &&norm(o.value)===target
        );
        if(suffixMatches.length===1) return suffixMatches[0].value;
      }
      return null;
    }
    const providerMatch=options.find(o=>norm(o.value)===target&&_getOptionProviderId(o).toLowerCase()===preferred);
    if(providerMatch) return providerMatch.value;
  }
  // 2. Normalized match — but ONLY when unambiguous. If the bare id
  // matches across multiple provider groups AND no provider hint is
  // available, return null instead of snapping to the first group's
  // option. This prevents a deliberate non-default pick from reverting
  // to the default provider on re-render (#6195).
  const exact=opts.find(o=>norm(o)===target);
  if(exact){
    const normMatches=options.filter(o=>norm(o.value)===target);
    if(normMatches.length>1 && !preferred && !explicitProvider && !rawModel.includes('/')){
      return null;  // ambiguous bare id — caller must inject the correct option
    }
    return exact;
  }
  // If the request is provider-qualified (either explicit @provider:model or
  // a slash-qualified vendor/model id), do NOT fuzzy-match a sibling model
  // once exact/provider-aware lookup failed. Returning null lets the caller
  // preserve the raw typed value instead of snapping to the closest catalog
  // entry. This keeps uncatalogued models routable instead of silently turning
  // them into a nearby curated sibling.
  if(rawModel.startsWith('@')||rawModel.includes('/')) return null;
  // 3. Prefix/substring: require the candidate to start with the FULL normalized target
  // (not a truncated base). This avoids false matches like gpt.5.5 → gpt.5.4.mini (#1188).
  // Only fall back to the shorter base form if target itself is very short (a bare root
  // like "gpt" or "claude") where stripping would be a no-op anyway.
  const base=target.replace(/\.\d+$/,'');  // strip trailing version number
  const useBase=base.length<=4||base===target; // bare root — stripping changed nothing meaningful
  const prefixTarget=useBase?base:target;
  // When the typed target is a COMPLETE versioned name (ends in a digit, e.g.
  // "mimo-v2.5" → norm "mimo.v2.5"), a prefix hit on a longer option is only
  // legitimate if the extra text continues the VERSION ("." + digit, e.g.
  // mimo.v2 → mimo.v2.5...). If the extra text is a variant/tier suffix
  // ("." + non-digit, e.g. mimo.v2.5.pro from "mimo-v2.5-pro"), the user asked
  // for the base model that simply isn't in the catalog — do NOT silently snap
  // them to the -pro/-flash tier (and a different price tier). Let resolution
  // fall through to null so the caller reports no-match instead. (#3368)
  const targetEndsInVersion=/\d$/.test(target);
  const partial=opts.find(o=>{
    const no=norm(o);
    if(!no.startsWith(prefixTarget)) return false;
    if(targetEndsInVersion && no!==target){
      const rest=no.slice(target.length);
      // reject "." + non-digit (variant/tier suffix); allow "" or "." + digit (version continuation)
      if(rest && !/^\.\d/.test(rest)) return false;
    }
    return true;
  });
  return partial||null;
}

// Set the model picker to the best match for modelId.
// Returns the resolved value that was actually set, or null if nothing matched.
function _refreshOpenModelDropdown(){
  const dd=$('composerModelDropdown');
  if(dd&&dd.classList&&dd.classList.contains('open')&&typeof renderModelDropdown==='function'){
    renderModelDropdown();
    if(typeof _positionModelDropdown==='function') _positionModelDropdown();
  }
  const sdd=$('settingsModelDropdown');
  if(sdd&&sdd.classList&&sdd.classList.contains('open')&&typeof renderModelDropdown==='function'){
    // Re-rendering the OPEN settings picker (e.g. when a late live-model fetch
    // resolves) must not re-grab search focus on touch — same coarse-pointer rule
    // as openSettingsModelDropdown, or the mobile keyboard pops after opening.
    const _coarsePointer=(typeof window.matchMedia==='function')&&window.matchMedia('(pointer: coarse)').matches;
    renderModelDropdown({
      dropdownId:'settingsModelDropdown',
      selectId:'settingsModel',
      forceOpenKey:'settingsModel',
      closeDropdown:closeSettingsModelDropdown,
      selectModel:selectSettingsModelFromDropdown,
      scopeNoteText:t('settings_desc_model')||'Used for new conversations. Existing conversations keep their selected model.',
      autoFocusSearch:!_coarsePointer,
    });
  }
}
function _applyModelToDropdown(modelId, sel, preferredProviderId, opts){
  if(!modelId||!sel) return null;
  const isRichPickerSelect=sel.id==='modelSelect'||sel.id==='settingsModel';
  const currentState=(isRichPickerSelect&&typeof _modelStateForSelect==='function')
    ? _modelStateForSelect(sel, sel.value)
    : null;
  const resolved=_findModelInDropdown(modelId,sel,preferredProviderId);
  if(resolved){
    sel.value=resolved;
    const preferredProvider=String(preferredProviderId||'').trim().toLowerCase();
    if(preferredProvider&&sel.options){
      // Assigning select.value picks the first duplicate value. Restore the
      // provider-specific option that the caller matched (#6131).
      const preferredOption=Array.from(sel.options).find(o=>
        String(o.value||'')===String(resolved)
        && String(_getOptionProviderId(o)||'').trim().toLowerCase()===preferredProvider
      );
      if(preferredOption) preferredOption.selected=true;
    }
    if(isRichPickerSelect){
      const resolvedState=typeof _modelStateForSelect==='function'
        ? _modelStateForSelect(sel, resolved)
        : {model:resolved,model_provider:preferredProviderId||null};
      const pickerChanged= !!(opts&&opts.forceRefresh) || !currentState
        || String(currentState.model||'')!==String(resolvedState.model||'')
        || String(currentState.model_provider||'')!==String(resolvedState.model_provider||'');
      if(sel.id==='modelSelect'&&typeof syncModelChip==='function') syncModelChip();
      if(sel.id==='settingsModel'&&typeof syncSettingsModelChip==='function') syncSettingsModelChip();
      if(pickerChanged) _refreshOpenModelDropdown();
    }
    return resolved;
  }
  return null;
}
function _ensureModelOptionInDropdown(modelId, sel, preferredProviderId){
  if(!modelId||!sel) return null;
  if(typeof _deduplicateModelPickerOptions==='function') _deduplicateModelPickerOptions(sel,sel.value);
  const requestedProvider=String(preferredProviderId||_providerFromModelValue(modelId)||'').trim();
  const applied=_applyModelToDropdown(modelId,sel,requestedProvider||null);
  if(applied){
    const appliedState=typeof _modelStateForSelect==='function'
      ?_modelStateForSelect(sel,applied)
      :{model:applied,model_provider:null};
    if(!requestedProvider||String(appliedState&&appliedState.model_provider||'').toLowerCase()===requestedProvider.toLowerCase()) return applied;
  }
  const explicitPrefix=requestedProvider?`@${requestedProvider}:`:'';
  const rawModel=String(modelId||'');
  const bareModel=explicitPrefix&&rawModel.toLowerCase().startsWith(explicitPrefix.toLowerCase())
    ?rawModel.slice(explicitPrefix.length)
    :rawModel;
  const value=requestedProvider?`${explicitPrefix}${bareModel}`:rawModel;
  const opt=document.createElement('option');
  opt.value=value;
  opt.textContent=typeof getModelLabel==='function'?getModelLabel(modelId):modelId;
  opt.dataset.custom='1';
  const badge=(window._configuredModelBadges||{})[value];
  const rawBadge=(window._configuredModelBadges||{})[rawModel];
  if(badge&&badge.provider) opt.dataset.provider=badge.provider;
  if(rawBadge&&rawBadge.provider) opt.dataset.provider=rawBadge.provider;
  if(requestedProvider) opt.dataset.model=bareModel;
  const provider=requestedProvider||(badge&&badge.provider)||(rawBadge&&rawBadge.provider)||_providerFromModelValue(value)||'';
  if(provider) opt.dataset.provider=provider;
  sel.appendChild(opt);
  sel.value=value;
  if(sel.id==='modelSelect'){
    if(typeof syncModelChip==='function') syncModelChip();
    _refreshOpenModelDropdown();
  }
  if(sel.id==='settingsModel'){
    if(typeof syncSettingsModelChip==='function') syncSettingsModelChip();
    _refreshOpenModelDropdown();
  }
  return value;
}
function _modelStateFromAppliedDropdown(sel, modelValue){
  const state=(typeof _modelStateForSelect==='function')
    ? _modelStateForSelect(sel,modelValue)
    : {model:modelValue,model_provider:null};
  return {model:state.model||modelValue,model_provider:state.model_provider||null};
}
function _persistSessionModelCorrection(model, provider, opts){
  if(!S.session) return;
  const request=fetch(new URL('api/session/update',document.baseURI||location.href).href,{
    method:'POST',credentials:'include',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({session_id:S.session.id||S.session.session_id,model:model,model_provider:provider||null})
  });
  return opts&&opts.propagateErrors ? request : request.catch(()=>{});
}
let _modelDropdownRequestSeq=0;
let _modelCatalogFallbackRetried=false;

function _applySessionModelFallback(sel){
  if(!sel) return null;
  const configuredDefault=String(window._defaultModel||'').trim();
  if(configuredDefault){
    const appliedDefault=_applyModelToDropdown(configuredDefault,sel,window._activeProvider||null);
    if(appliedDefault) return _modelStateFromAppliedDropdown(sel,appliedDefault);
  }
  const first=sel.querySelector('optgroup > option, option');
  if(first){
    sel.value=first.value;
    if(sel.id==='modelSelect'){
      if(typeof syncModelChip==='function') syncModelChip();
      _refreshOpenModelDropdown();
    }
    return _modelStateFromAppliedDropdown(sel,first.value);
  }
  return null;
}

async function populateModelDropdown(opts={}){
  const sel=$('modelSelect');
  if(!sel) return;
  // `_activeProvider` is refreshed from the /api/models response below.
  if(typeof _modelDropdownRequestSeq!=='number') _modelDropdownRequestSeq=0;
  if(typeof _modelCatalogFallbackRetried!=='boolean') _modelCatalogFallbackRetried=false;
  const requestSeq=++_modelDropdownRequestSeq;
  try{
    const modelsUrl=new URL('api/models',document.baseURI||location.href);
    const requestedFreshness=opts&&opts.freshness?String(opts.freshness):'';
    if(opts&&opts.freshness) modelsUrl.searchParams.set('freshness',opts.freshness);
    const _modelsRes=await fetch(modelsUrl.href,{credentials:'include'});
    if(requestSeq!==_modelDropdownRequestSeq) return;
    const customRedirectIfUnauth=opts&&typeof opts.redirectIfUnauth==='function'?opts.redirectIfUnauth:null;
    if(customRedirectIfUnauth){
      if(customRedirectIfUnauth(_modelsRes)) return;
    }else if(_redirectIfUnauth(_modelsRes)) return;
    // `_activeProvider` is populated from the /api/models payload below.
    const data=await _modelsRes.json();
    if(requestSeq!==_modelDropdownRequestSeq) return;
    window._activeProvider=data.active_provider||null;
    window._defaultModel=data.default_model||null;
    window._configuredModelBadges=data.configured_model_badges||{};
    window._modelEndpointErrors={};
    // Keep g.extra_models label hydration in this function for /model and tail selections.

    const _synthGroupsFromConfigured=()=>{
      const badgeMap=window._configuredModelBadges||{};
      const grouped=new Map();
      const addModel=(providerId,modelId)=>{
        const pid=String(providerId||'configured').trim()||'configured';
        const mid=String(modelId||'').trim();
        if(!mid) return;
        if(!grouped.has(pid)) grouped.set(pid,[]);
        const arr=grouped.get(pid);
        if(arr.some(m=>m.id===mid)) return;
        arr.push({id:mid,label:getModelLabel(mid)});
      };

      for(const [modelId,badge] of Object.entries(badgeMap)){
        const mid=String(modelId||'').trim();
        // Prefer canonical IDs only; skip derived aliases such as
        // @provider:model and provider/model to avoid noisy duplicates.
        if(!mid||mid.startsWith('@')||mid.includes('/')) continue;
        const provider=(badge&&badge.provider)||'configured';
        addModel(provider,mid);
      }

      if(grouped.size===0&&data&&data.default_model){
        addModel(data.active_provider||'configured',data.default_model);
      }

      const groups=[];
      for(const [providerId,models] of grouped.entries()){
        const display=(String(providerId).startsWith('custom:')
          ? String(providerId).slice('custom:'.length)
          : String(providerId))||'Configured';
        groups.push({provider:display,provider_id:providerId,models});
      }
      return groups;
    };

    const usedConfiguredFallback=!(Array.isArray(data.groups)&&data.groups.length);
    const groups=usedConfiguredFallback
      ? _synthGroupsFromConfigured()
      : data.groups;
    const willRetry=usedConfiguredFallback && requestedFreshness!=='session_visit' && !_modelCatalogFallbackRetried;

    if(!groups.length){
      if(willRetry){
        _modelCatalogFallbackRetried=true;
        populateModelDropdown({...opts,freshness:'session_visit'}).catch(()=>{});
      }
      return; // no server groups and no configured fallback
    }
    const previousSelection=_captureModelDropdownSelection(sel);
    // Clear existing options
    sel.innerHTML='';
    _dynamicModelLabels={};
    for(const g of groups){
      const og=document.createElement('optgroup');
      og.label=g.provider;
      if(g.provider_id) og.dataset.provider=g.provider_id;
      if(g.models_endpoint_error){
        const errorKey=g.provider_id||g.provider||'';
        og.dataset.modelsEndpointError=JSON.stringify(g.models_endpoint_error);
        if(errorKey) window._modelEndpointErrors[errorKey]=g.models_endpoint_error;
      }
      for(const m of (Array.isArray(g.models)?g.models:[])){
        const opt=document.createElement('option');
        opt.value=m.id;
        opt.textContent=m.label;
        if(m && m.description) opt.dataset.desc=m.description;
        if(m && (m.supports_fast_tier === true || String(m.supports_fast_tier).toLowerCase()==='true')){
          opt.dataset.fast='1';
        }else if(m && (m.supports_fast_tier === false || String(m.supports_fast_tier).toLowerCase()==='false')){
          opt.dataset.fast='0';
        }
        og.appendChild(opt);
        _dynamicModelLabels[m.id]=m.label||m.id;
      }
      // Hydrate the label map from extra_models too (the catalog tail that
      // doesn't render as <option> entries when the picker is capped — see
      // _build_nous_featured_set in api/config.py for the rationale). This
      // keeps a model selected from the slash-command autocomplete or a
      // persisted-localStorage value renderable with its proper label
      // instead of falling back to the bare ID. #1567.
      if(Array.isArray(g.extra_models)){
        try{ og.dataset.extraModels=JSON.stringify(g.extra_models); }catch(_e){ og.dataset.extraModels='[]'; }
        for(const m of g.extra_models){
          if(m && m.id) _dynamicModelLabels[m.id]=m.label||m.id;
        }
      }
      sel.appendChild(og);
    }
    if(typeof _deduplicateModelPickerOptions==='function'){
      _deduplicateModelPickerOptions(sel,previousSelection&&previousSelection.model||'');
    }
    _reconcileModelDropdownSelection(sel,data,previousSelection,opts);
    if(typeof syncModelChip==='function') syncModelChip();
    const dd=$('composerModelDropdown');
    if(dd&&dd.classList.contains('open')&&typeof renderModelDropdown==='function'){
      renderModelDropdown();
      _positionModelDropdown();
    }
    // Kick off a background live-model fetch for the active provider.
    // This runs after the static list is already shown (no blocking flicker).
    if(data.active_provider && !willRetry) _fetchLiveModels(data.active_provider, sel, requestSeq);
    if(willRetry){
      _modelCatalogFallbackRetried=true;
      populateModelDropdown({...opts,freshness:'session_visit'}).catch(()=>{});
    }
  }catch(e){
    if(requestSeq!==_modelDropdownRequestSeq) return;
    // API unavailable -- keep the hardcoded HTML options as fallback
    console.warn('Failed to load models from server:',e.message);
    if(typeof syncModelChip==='function') syncModelChip();
  }
}

// Cache so we don't re-fetch on every page load
const _liveModelCache={};
// Tracks providers for which a live-model fetch is in flight.
// Used by syncTopbar() to defer model corrections until the fetch completes,
// preventing premature fallback to the first static model (#1169).
const _liveModelFetchPending=new Set();

function _addLiveModelsToSelect(provider, models, sel){
  if(!provider||!models||!models.length||!sel) return 0;
  const currentVal=sel.value;
  let providerGroup=null;
  for(const og of sel.querySelectorAll('optgroup')){
    if(og.dataset.provider&&og.dataset.provider===provider){
      providerGroup=og; break;
    }
    if(og.label&&og.label.toLowerCase().includes(provider.toLowerCase())){
      providerGroup=og; break;
    }
  }
  if(!providerGroup){
    providerGroup=document.createElement('optgroup');
    providerGroup.label=provider.charAt(0).toUpperCase()+provider.slice(1)+' (live)';
    providerGroup.dataset.provider=provider;
    sel.appendChild(providerGroup);
  }else if(!providerGroup.dataset.provider){
    providerGroup.dataset.provider=provider;
  }
  const existingIds=new Set([...sel.options].map(o=>o.value));
  const _ap=(window._activeProvider||'').toLowerCase();
  const _providerLower=String(provider||'').toLowerCase();
  const _isNamedCustomActiveProvider=_ap.startsWith('custom:');
  const _isPortalFetch=_ap && _ap!=='openrouter' && _ap!=='custom' && _ap!=='openai-codex' && (_providerLower===_ap||_isNamedCustomActiveProvider&&_providerLower===_ap);
  // Keep existingNorm.has( within the #907 source slice.
  const optionIdentity=typeof _modelPickerOptionIdentity==='function'
    ? (modelId,providerId)=>_modelPickerOptionIdentity(modelId,providerId)
    : (modelId,providerId)=>{
        let value=String(modelId||'');
        const provider=String(providerId||'').trim();
      if(value.startsWith('@')&&value.includes(':')){
        const exactPrefix=provider ? `@${provider}:` : '';
        if(exactPrefix && value.toLowerCase().startsWith(exactPrefix.toLowerCase())){
          value=value.substring(exactPrefix.length);
        }else if(value.startsWith('@custom:')){
          const namedProvider=value.substring('@custom:'.length);
          const splitAt=namedProvider.indexOf(':');
          value=splitAt>=0 ? namedProvider.substring(splitAt+1) : namedProvider;
        }else{
          value=value.substring(value.indexOf(':')+1);
        }
      }
        return value.split('/').pop().replace(/-/g,'.').toLowerCase();
      };
  const existingNorm=new Set([...sel.options].map(o=>optionIdentity(o.value,_getOptionProviderId(o))));
  let added=0;
  for(const m of models){
    let mid=m.id;
    if(_isPortalFetch && !mid.startsWith('@')){
      mid=`@${provider}:${mid}`;
    }
    if(existingIds.has(mid)) continue;
    const identity=optionIdentity(mid,provider);
    if(existingNorm.has(identity)){
      const sameGroup=Array.from(providerGroup.children||[]).find(o=>optionIdentity(o.value,_getOptionProviderId(o))===identity);
      if(sameGroup){
        const incomingRoutable=String(mid).startsWith('@');
        const existingRoutable=String(sameGroup.value||'').startsWith('@');
        if(!(!existingRoutable&&incomingRoutable)) continue; // let proxy replace catalog twin
      }
    }
    const opt=document.createElement('option');
    opt.value=mid;
    opt.textContent=m.label||m.id;
    opt.title='Live model — fetched from provider';
    opt.dataset.provider=provider;
    if(m && (m.supports_fast_tier === true || String(m.supports_fast_tier).toLowerCase()==='true')){
      opt.dataset.fast='1';
    }else if(m && (m.supports_fast_tier === false || String(m.supports_fast_tier).toLowerCase()==='false')){
      opt.dataset.fast='0';
    }
    providerGroup.appendChild(opt);
    existingIds.add(mid);
    existingNorm.add(identity);
    _dynamicModelLabels[mid]=m.label||m.id;
    added++;
  }
  if(typeof _deduplicateModelPickerOptions==='function') _deduplicateModelPickerOptions(sel,currentVal);
  const currentState=(currentVal&&typeof _modelStateForSelect==='function')
    ? _modelStateForSelect(sel, currentVal)
    : {model:currentVal||'', model_provider:(S.session&&S.session.model_provider)||null};
  const currentProvider=currentState&&currentState.model_provider||null;
  if(added>0 && currentVal) _applyModelToDropdown(currentVal, sel, currentProvider, {forceRefresh:true});
  // After live models are added, re-apply the session's model in case it was
  // absent from the static list and syncTopbar() fired before the live fetch
  // completed (#1169). This ensures the session model wins over any premature
  // fallback that may have set sel.value to the first available option.
  if(S.session && S.session.model && sel.id==='modelSelect'){
    const sessionProvider=S.session.model_provider||null;
    const sessionAlreadyRefreshed=added>0 && currentVal
      && String((currentState&&currentState.model)||'')===String(S.session.model||'')
      && String((currentState&&currentState.model_provider)||'')===String(sessionProvider||'');
    const reapplied=_applyModelToDropdown(S.session.model, sel, sessionProvider, {forceRefresh:added>0&&!sessionAlreadyRefreshed});
    if(reapplied && typeof syncModelChip==='function') syncModelChip();
  }
  return added;
}

async function _fetchLiveModels(provider, sel, requestSeq=null){
  if(!provider||!sel) return;
  if(requestSeq!==null&&requestSeq!==_modelDropdownRequestSeq) return;
  // Already fetched — apply cached models to this select element (#872)
  if(_liveModelCache[provider]){
    if(requestSeq!==null&&requestSeq!==_modelDropdownRequestSeq) return;
    const added=_addLiveModelsToSelect(provider,_liveModelCache[provider],sel);
    if(added>0 && typeof syncModelChip==='function') syncModelChip();
    return;
  }
  _liveModelFetchPending.add(provider);
  try{
    const url=new URL('api/models/live',document.baseURI||location.href);
    url.searchParams.set('provider',provider);
    const _liveRes=await fetch(url.href,{credentials:'include'});
    if(requestSeq!==null&&requestSeq!==_modelDropdownRequestSeq) return;
    if(_redirectIfUnauth(_liveRes)) return;
    const data=await _liveRes.json();
    if(requestSeq!==null&&requestSeq!==_modelDropdownRequestSeq) return;
    if(!data.models||!data.models.length) return;
    _liveModelCache[provider]=data.models;
    if(requestSeq!==null&&requestSeq!==_modelDropdownRequestSeq) return;
    const added=_addLiveModelsToSelect(provider,data.models,sel);
    if(added>0){
      if(typeof syncModelChip==='function') syncModelChip();
      console.debug('[agy] Live models loaded for',provider+':',added,'new models added');
    }
  }catch(e){
    console.debug('[agy] Live model fetch failed for',provider,e.message);
  }finally{
    _liveModelFetchPending.delete(provider);
  }
}

/**
 * Check if the given model ID belongs to a different provider than the one
 * currently configured in AGY. Returns a warning string if mismatched,
 * or null if the selection looks compatible.
 *
 * Provider detection is intentionally loose — we compare the model's slash
 * prefix (e.g. "openai/" from "openai/gpt-4o") against the active provider
 * name. Custom/local endpoints report active_provider='custom', a named
 * custom provider such as 'custom:zenmux', or the base_url hostname; skip the
 * check for those values to avoid false positives.
 */
function _checkProviderMismatch(modelId){
  const ap=(window._activeProvider||'').toLowerCase();
  if(_providerSkipsModelMismatchWarning(ap)) return null; // can't reliably check
  // @provider: prefixed IDs came from that provider's live model list — no mismatch possible
  if(modelId.startsWith('@')) return null;
  const slash=modelId.indexOf('/');
  if(slash<0) return null; // bare model name, no provider prefix
  const modelProvider=modelId.substring(0,slash).toLowerCase();
  // Normalise common aliases
  const aliases={'claude':'anthropic','gpt':'openai','gemini':'google'};
  const norm=p=>aliases[p]||p;
  if(norm(modelProvider)!==norm(ap)){
    return (window.t?window.t('provider_mismatch_warning',modelId,ap):
      `"${modelId}" may not work with your configured provider (${ap}). Send anyway or run \`agy model\` to switch.`);
  }
  return null;
}

function _selectedModelOption(){
  const sel=$('modelSelect');
  if(!sel) return null;
  return sel.options[sel.selectedIndex]||null;
}

function _normalizeConfiguredModelKey(modelId){
  let s=String(modelId||'').trim().toLowerCase();
  let strippedAtProvider=false;
  // Strip @provider: prefix (e.g., @custom:jingdong:GLM-5 -> jingdong:GLM-5).
  // Defensive: trailing-colon / trailing-slash falls back to the original key
  // so malformed configs don't collapse distinct ids to '' (matches backend _norm_model_id).
  if(s.startsWith('@')&&s.includes(':')){const ci=s.indexOf(':',1);const cand=s.slice(ci+1);strippedAtProvider=!!cand;s=cand||s;}
  // Skip slash-based stripping for URI-scheme IDs (e.g. gpt://folder/model)
  // whose slashes are path separators, not provider delimiters (#3429).
  const _hasScheme=/^[a-z][a-z0-9+.-]*:\/\//i.test(s);
  if(!_hasScheme){
    // Strip provider-qualified prefixes that contain colons before the first
    // slash (e.g. 'custom:llm-proxy/model' → 'model').  Without this, badge-
    // key variants like 'custom:llm-proxy/opencode_go/deepseek-v4-pro' and the
    // bare 'opencode_go/deepseek-v4-pro' produce different normalized keys and
    // aren't deduped in the configured section (#3360).
    if(!strippedAtProvider&&s.includes('/')&&s.indexOf(':')!==-1&&s.indexOf(':')<s.indexOf('/')){
      s=s.slice(s.indexOf('/')+1)||s;
    }
    // Strip only the first slash-segment (provider prefix), preserving any
    // remaining vendor hierarchy. Using split('/').pop() here previously
    // discarded ALL segments except the last, collapsing distinct multi-slash
    // IDs like 'vendor_a/deepseek-v4-pro' and 'vendor_b/deepseek/deepseek-v4-pro'
    // to the same key, causing badge misattribution and configured-entry
    // suppression (#3360).
    if(s.includes('/')) s=s.replace(/^[^/]+\//, '')||s;
  }
  return s.replace(/-/g,'.');
}

function _isEquivalentConfiguredModelEntry(modelId,badge,entries){
  const normalized=_normalizeConfiguredModelKey(modelId);
  const provider=String(badge&&badge.provider||'').toLowerCase();
  const matchingEntries=(entries||[]).filter(existing=>
    _normalizeConfiguredModelKey(existing.value)===normalized
  );
  if(matchingEntries.some(existing=>{
    const entryProvider=String(existing.providerId||'').toLowerCase();
    return !provider||!entryProvider||entryProvider===provider;
  })) return true;
  // @provider:model is an equivalent routing spelling only when an existing
  // picker row belongs to that same provider. This supports named custom
  // providers (@custom:name:model) without collapsing matching model IDs from
  // different providers.
  const rawId=String(modelId||'');
  const prefix=provider?`@${provider}:`:'';
  if(!prefix||!rawId.toLowerCase().startsWith(prefix)) return false;
  const routedId=rawId.slice(prefix.length);
  return (entries||[]).some(entry=>
    String(entry.providerId||'').toLowerCase()===provider
    &&_normalizeConfiguredModelKey(entry.value)===_normalizeConfiguredModelKey(routedId)
  );
}

function _getConfiguredModelBadge(modelId,badgeMap,providerId){
  const map=badgeMap||window._configuredModelBadges||{};
  if(!modelId||!map) return null;
  const provider=String(providerId||'').toLowerCase();
  const exact=map[modelId];
  if(exact && (!provider || !exact.provider || String(exact.provider).toLowerCase()===provider)) return exact;
  const targetNorm=_normalizeConfiguredModelKey(modelId);
  const matches=[];
  for(const [candidate,badge] of Object.entries(map)){
    if(_normalizeConfiguredModelKey(candidate)===targetNorm) matches.push(badge);
  }
  if(!matches.length) return null;
  if(provider){
    const providerMatch=matches.find(badge=>String(badge&&badge.provider||'').toLowerCase()===provider);
    if(providerMatch) return providerMatch;
    return matches.length===1 ? matches[0] : null;
  }
  return matches[0];
}

function _compactComposerModelChipLabel(modelId,labelText){
  const id=String(modelId||'').trim();
  const raw=String(labelText||'').trim();
  if(!raw) return getModelLabel(id);
  const idLower=id.toLowerCase();
  const rawLower=raw.toLowerCase();
  const slash=id.indexOf('/');
  if(slash>0){
    const provider=id.slice(0,slash).toLowerCase();
    if(rawLower.startsWith(provider+'/')){
      return raw.slice(provider.length+1).trim();
    }
  }
  if(id&&rawLower===idLower&&raw.includes('/')){
    return raw.slice(raw.indexOf('/')+1).trim();
  }
  if(raw.includes('/') && !/^[a-z][a-z0-9+.-]*:\/\//i.test(raw)){
    const parts=raw.split('/').map(s=>s.trim()).filter(Boolean);
    if(parts.length>=2){
      const tail=parts[parts.length-1];
      const tailLower=tail.toLowerCase();
      if(idLower && (tailLower===idLower || idLower.endsWith('/'+tailLower))) return tail;
      if(parts.length===2){
        const leadLower=parts[0].toLowerCase();
        if(tailLower.startsWith(leadLower+'-')) return tail;
      }
    }
  }
  return raw;
}

function syncModelChip(){
  const sel=$('modelSelect');
  const chip=$('composerModelChip');
  const label=$('composerModelLabel');
  const mobileLabel=$('composerMobileModelLabel');
  const mobileAction=$('composerMobileModelAction');
  const dd=$('composerModelDropdown');
  if(!sel||!chip||!label) return;
  // Don't show a model label until boot has finished loading to prevent flash of wrong default
  if(!S._bootReady){
    label.textContent='';
    if(mobileLabel) mobileLabel.textContent='';
    chip.title='Conversation model';
    return;
  }
  const opt=_selectedModelOption();
  const text=opt?opt.textContent:getModelLabel(sel.value||'');
  const compactText=_compactComposerModelChipLabel(sel.value||'', text);
  const gatewayRouting=_latestGatewayRoutingForSession(S.session);
  const displayText=_formatGatewayModelLabel(sel.value||'',compactText,gatewayRouting)||compactText;
  label.textContent=displayText;
  if(mobileLabel) mobileLabel.textContent=displayText;
  chip.title=gatewayRouting?`${sel.value||'Conversation model'} ${_gatewayRoutingLabel(gatewayRouting)}`:(sel.value||'Conversation model');
  chip.classList.toggle('active',!!(dd&&dd.classList.contains('open')));
  if(mobileAction) mobileAction.classList.toggle('active',!!(dd&&dd.classList.contains('open')));
}

// Remembers where #composerModelDropdown lives in the composer-footer so the
// phone path can move it to <body> and put it back exactly. Captured lazily on
// the first reparent (see _positionModelDropdown phone branch).
let _modelDropdownHome=null;

// Return the model dropdown into its original .composer-footer slot and clear
// every inline style the phone path wrote, so the desktop CSS (position:absolute
// anchored on the relatively-positioned .composer-footer) fully governs again.
// Safe to call when the element never moved — it just no-ops the reinsert.
function _restoreModelDropdownHome(){
  const dd=document.getElementById('composerModelDropdown');
  if(!dd) return;
  dd.classList.remove('model-dropdown--floating');
  dd.style.left='';
  dd.style.top='';
  dd.style.bottom='';
  dd.style.width='';
  dd.style.maxWidth='';
  dd.style.maxHeight='';
  if(_modelDropdownHome&&_modelDropdownHome.parent&&dd.parentNode!==_modelDropdownHome.parent){
    const ref=_modelDropdownHome.nextSibling;
    if(ref&&ref.parentNode===_modelDropdownHome.parent){
      _modelDropdownHome.parent.insertBefore(dd,ref);
    }else{
      _modelDropdownHome.parent.appendChild(dd);
    }
  }
}

function _positionModelDropdown(){
  const dd=$('composerModelDropdown');
  const chip=$('composerModelChip');
  const mobileAction=$('composerMobileModelAction');
  const footer=document.querySelector('.composer-footer');
  if(!dd||!footer) return;
  const panel=$('composerMobileConfigPanel');
  const anchor=(panel&&panel.classList.contains('open')&&mobileAction)?mobileAction:(chip&&chip.offsetParent?chip:mobileAction);
  if(!anchor) return;
  const isPhone=typeof window.matchMedia==='function'&&window.matchMedia('(max-width:640px)').matches;
  if(isPhone){
    // #6080: .composer-footer sets container-type:inline-size (and a
    // backdrop-filter under the Geist Contrast skin) — both establish a fixed
    // containing block, so a position:fixed dropdown left inside the footer
    // resolves against the FOOTER (bottom of screen) instead of the viewport
    // and lands below the fold. Reparent to <body> — exactly the working
    // #profileDropdown idiom — so position:fixed is viewport-relative on ALL
    // skins, then compute coordinates against the visual viewport.
    if(!_modelDropdownHome){
      _modelDropdownHome={parent:dd.parentNode,nextSibling:dd.nextSibling};
    }
    if(dd.parentNode!==document.body) document.body.appendChild(dd);
    dd.classList.add('model-dropdown--floating');
    const anchorRect=anchor.getBoundingClientRect();
    const visualViewport=window.visualViewport;
    const viewportWidth=Math.max(1,Number(visualViewport&&visualViewport.width)||window.innerWidth||1);
    const viewportHeight=Math.max(1,Number(visualViewport&&visualViewport.height)||window.innerHeight||1);
    const viewportTop=Math.max(0,Number(visualViewport&&visualViewport.offsetTop)||0);
    const viewportBottom=viewportTop+viewportHeight;
    const margin=8;
    const gap=6;
    const viewportLeft=Math.max(0,Number(visualViewport&&visualViewport.offsetLeft)||0);
    const viewportRight=viewportLeft+viewportWidth;
    const titlebar=document.querySelector('.app-titlebar');
    const titlebarBottom=titlebar&&typeof titlebar.getBoundingClientRect==='function'
      ? Number(titlebar.getBoundingClientRect().bottom)||0
      : 0;
    const contentTop=Math.max(viewportTop+margin,titlebarBottom+margin);
    const menuWidth=Math.max(1,viewportWidth-margin*2);
    const left=Math.max(viewportLeft+margin,Math.min(anchorRect.left,viewportRight-menuWidth-margin));
    dd.style.left=`${left}px`;
    dd.style.width=`${menuWidth}px`;
    dd.style.maxWidth=`${menuWidth}px`;
    dd.style.bottom='auto';
    const menuHeight=Math.max(dd.scrollHeight,dd.offsetHeight);
    const aboveSpace=Math.max(0,anchorRect.top-contentTop-gap-margin);
    const belowSpace=Math.max(0,viewportBottom-anchorRect.bottom-gap-margin);
    const openAbove=aboveSpace>=Math.min(menuHeight,belowSpace)||aboveSpace>=belowSpace;
    const availableHeight=Math.max(1,openAbove?aboveSpace:belowSpace);
    dd.style.maxHeight=`${availableHeight}px`;
    const visibleHeight=Math.min(menuHeight||availableHeight,availableHeight);
    const top=openAbove
      ? anchorRect.top-gap-visibleHeight
      : anchorRect.bottom+gap;
    dd.style.top=`${Math.max(contentTop,Math.min(top,viewportBottom-margin-visibleHeight))}px`;
    return;
  }
  // Desktop (>640px): keep the current master behaviour — an absolutely
  // positioned .composer-footer child. Restore the element into the footer (in
  // case a prior phone open moved it to <body>) and clear the phone inline
  // styles so the desktop CSS anchor is byte-for-byte identical to master.
  _restoreModelDropdownHome();
  const anchorRect=anchor.getBoundingClientRect();
  const footerRect=footer.getBoundingClientRect();
  let left=anchorRect.left-footerRect.left;
  const maxLeft=Math.max(0, footer.clientWidth-dd.offsetWidth);
  left=Math.max(0, Math.min(left, maxLeft));
  dd.style.left=`${left}px`;
}

function _readModelOverflowData(group){
  if(!group||!group.dataset||!group.dataset.extraModels) return [];
  try{
    const parsed=JSON.parse(group.dataset.extraModels);
    return Array.isArray(parsed)?parsed.filter(m=>m&&m.id):[];
  }catch(_e){
    return [];
  }
}

function _appendOverflowOptionsToGroup(group, extraModels){
  if(!group||!Array.isArray(extraModels)||!extraModels.length) return 0;
  // The selected model may already have been injected into the <select> (e.g. a
  // hidden overflow model picked from search via _ensureModelOptionInDropdown).
  // Appending it again here would create a duplicate row once the group expands,
  // so reuse/move any existing option with the same value instead of re-creating it. (#3691)
  const parentSelect=(group.parentNode&&group.parentNode.tagName==='SELECT')?group.parentNode:null;
  const existingByValue=new Map();
  if(parentSelect){
    for(const opt of Array.from(parentSelect.querySelectorAll('option'))){
      if(opt&&typeof opt.value==='string') existingByValue.set(opt.value,opt);
    }
  }
  let appended=0;
  for(const m of extraModels){
    if(!m||!m.id) continue;
    const existing=existingByValue.get(m.id);
    if(existing){
      // Move the already-present option into this group rather than duplicating it.
      if(existing.parentNode!==group) group.appendChild(existing);
      continue;
    }
    const opt=document.createElement('option');
    opt.value=m.id;
    opt.textContent=m.label||m.id;
    group.appendChild(opt);
    appended++;
  }
  if(group.dataset){
    group.dataset.extraModels='[]';
    group.dataset.overflowExpanded='1';
  }
  return appended;
}

function _mountSearchableModelSelect(opts={}){
  const root=opts.root;
  if(!root) return null;
  const choices=Array.isArray(opts.choices)
    ? opts.choices
      .map(choice=>choice&&choice.id?{id:String(choice.id),label:String(choice.label||choice.id)}:null)
      .filter(Boolean)
    : [];
  const selectedValue=String(opts.selectedValue||'');
  const onModelChange=typeof opts.onModelChange==='function' ? opts.onModelChange : ()=>{};
  const selectId=opts.selectId||'';
  const customInputId=opts.customInputId||'';
  const listedChoiceIds=new Set(choices.map(choice=>choice.id));
  const listedSelection=listedChoiceIds.has(selectedValue) ? selectedValue : '';
  const customSelection=listedSelection ? '' : selectedValue;
  let lastListedValue=listedSelection||(choices[0]?choices[0].id:'');
  root.innerHTML=
    `<div class="model-search-row">`+
      `<input class="model-search-input" type="text" placeholder="${esc(t('model_search_placeholder')||'Search models…')}" spellcheck="false" autocomplete="off">`+
      `<button class="model-search-clear" title="Clear search">${li('x',10)}</button>`+
    `</div>`+
    `<select ${selectId?`id="${esc(selectId)}"`:''}></select>`+
    `<div class="model-group model-custom-sep">${esc(t('model_custom_label')||'Custom model ID')}</div>`+
    `<div class="model-custom-row">`+
      `<input ${customInputId?`id="${esc(customInputId)}"`:''} class="model-custom-input" type="text" placeholder="${esc(t('model_custom_placeholder')||'e.g. openai/gpt-5.4')}" spellcheck="false" autocomplete="off">`+
      `<button class="model-custom-btn" title="Use this model">${li('plus',12)}</button>`+
    `</div>`;
  const searchInput=root.querySelector('.model-search-input');
  const clearButton=root.querySelector('.model-search-clear');
  const selectEl=selectId ? root.querySelector(`#${selectId}`) : root.querySelector('select');
  const customInput=customInputId ? root.querySelector(`#${customInputId}`) : root.querySelector('.model-custom-input');
  const customButton=root.querySelector('.model-custom-btn');
  if(!searchInput||!clearButton||!selectEl||!customInput||!customButton) return null;

  const noMatchesOption=document.createElement('option');
  noMatchesOption.value='';
  noMatchesOption.textContent='No matching models';
  noMatchesOption.disabled=true;
  noMatchesOption.hidden=true;
  selectEl.appendChild(noMatchesOption);

  for(const choice of choices){
    const option=document.createElement('option');
    option.value=choice.id;
    option.textContent=choice.label;
    selectEl.appendChild(option);
  }
  if(listedSelection){
    selectEl.value=listedSelection;
  }else if(customSelection){
    selectEl.selectedIndex=-1;
  }else if(choices.length){
    selectEl.value=choices[0].id;
    onModelChange(lastListedValue);
  }
  customInput.value=customSelection;

  const applyFilter=()=>{
    const needle=(searchInput.value||'').trim().toLowerCase();
    let visibleCount=0;
    for(const option of Array.from(selectEl.options)){
      if(option===noMatchesOption) continue;
      const haystack=`${option.textContent||''} ${option.value||''}`.toLowerCase();
      const visible=!needle||haystack.includes(needle);
      option.hidden=!visible;
      if(visible) visibleCount++;
    }
    noMatchesOption.hidden=visibleCount!==0;
  };

  const applyCustomSelection=()=>{
    onModelChange((customInput.value||'').trim());
  };

  searchInput.addEventListener('input', applyFilter);
  clearButton.addEventListener('click', ()=>{
    searchInput.value='';
    applyFilter();
    searchInput.focus();
  });
  selectEl.addEventListener('change', ()=>{
    customInput.value='';
    lastListedValue=selectEl.value||lastListedValue;
    onModelChange(lastListedValue);
  });
  customInput.addEventListener('input', ()=>{
    const value=(customInput.value||'').trim();
    if(value){
      selectEl.selectedIndex=-1;
      onModelChange(value);
      return;
    }
    customInput.value='';
    if(lastListedValue){
      selectEl.value=lastListedValue;
      onModelChange(lastListedValue);
      return;
    }
    onModelChange('');
  });
  customInput.addEventListener('keydown', (event)=>{
    if(event.key!=='Enter') return;
    event.preventDefault();
    applyCustomSelection();
  });
  customButton.addEventListener('click', (event)=>{
    event.preventDefault();
    applyCustomSelection();
  });
  applyFilter();
  return {searchInput,selectEl,customInput,customButton};
}

function renderModelDropdown(){
  const opts=arguments[0]||{};
  const dd=$(opts.dropdownId||'composerModelDropdown');
  const sel=$(opts.selectId||'modelSelect');
  if(!dd||!sel) return;
  if(typeof _deduplicateModelPickerOptions==='function') _deduplicateModelPickerOptions(sel,sel.value);
  // Whether the search input should auto-grab focus on (re-)render. Default true
  // preserves the composer picker's behavior exactly; the settings picker passes
  // false on coarse-pointer devices so opening it doesn't pop the mobile keyboard.
  const _autoFocusSearch=opts.autoFocusSearch!==false;
  const selectFromDropdown=typeof opts.selectModel==='function'
    ? opts.selectModel
    : (value,provider)=>selectModelFromDropdown(value,provider);
  const closeDropdown=typeof opts.closeDropdown==='function'
    ? opts.closeDropdown
    : closeModelDropdown;
  // Group(s) that must render OPEN even though they aren't the selected group —
  // set when the user expands a group's overflow via "Show more" so a later full
  // re-render doesn't re-collapse it (_groupOpenState is rebuilt per render, so
  // this cross-render intent persists on a global). Resolved as a function-local
  // so renderModelDropdown stays self-contained when eval'd in isolation (the
  // #3691 node test driver evals the function body without module scope).
  const _forceOpenGroups=(()=>{
    const _g=(typeof window!=='undefined')?window:(typeof globalThis!=='undefined'?globalThis:{});
    const key=opts.forceOpenKey||'composer';
    if(!_g.__modelGroupForceOpenByPicker) _g.__modelGroupForceOpenByPicker={};
    if(!_g.__modelGroupForceOpenByPicker[key]) _g.__modelGroupForceOpenByPicker[key]=new Set();
    return _g.__modelGroupForceOpenByPicker[key];
  })();
  const _modelData=[];
  const _groupMeta=new Map();
  const _groupOrder=[];
  const _badgeMap=window._configuredModelBadges||{};
  const _ensureGroupMeta=(groupKey,groupLabel,providerId,optgroup)=>{
    if(!_groupMeta.has(groupKey)){
      _groupMeta.set(groupKey,{
        key:groupKey,
        label:groupLabel||'',
        providerId:providerId||'',
        optgroup:optgroup||null,
        modelsEndpointError:null,
        modelCount:0,
        hiddenCount:0,
        endpointErrorOnly:false,
      });
      _groupOrder.push(groupKey);
    }
    return _groupMeta.get(groupKey);
  };
  const _vendorPrefix=(rawId)=>{
    const stripped=String(rawId||'').replace(/^@([^:]+:)+/,'');
    const slash=stripped.indexOf('/');
    return slash>0?stripped.slice(0,slash):'';
  };
  const SUB_GROUP_PROVIDERS=new Set(['openrouter','nous']);
  const SUB_GROUP_MIN_MODELS=8;
  for(const child of Array.from(sel.children)){
    if(child.tagName==='OPTGROUP'){
      const providerId=child.dataset&&child.dataset.provider?child.dataset.provider:'';
      const groupKey=providerId||child.label||`group-${_groupOrder.length}`;
      const groupMeta=_ensureGroupMeta(groupKey,child.label||'',providerId,child);
      let modelsEndpointError=null;
      if(child.dataset&&child.dataset.modelsEndpointError){
        try{ modelsEndpointError=JSON.parse(child.dataset.modelsEndpointError); }catch(_e){ modelsEndpointError=null; }
      }
      groupMeta.modelsEndpointError=modelsEndpointError;
      for(const opt of Array.from(child.children)){
        const rawValue=String(opt.value||'');
        const displayName=rawValue.startsWith('@custom:')
          ? getModelLabel(rawValue)
          : (opt.textContent||getModelLabel(rawValue));
        const entry={value:opt.value,name:esc(displayName),id:esc(opt.value),desc:(opt.dataset&&opt.dataset.desc)||'',group:child.label||'',groupKey,providerId,modelsEndpointError,badge:_getConfiguredModelBadge(opt.value,_badgeMap,providerId),hiddenByDefault:false};
        _modelData.push(entry);
        groupMeta.modelCount++;
      }
      for(const overflowModel of _readModelOverflowData(child)){
        const displayName=overflowModel.id.startsWith('@custom:')
          ? getModelLabel(overflowModel.id)
          : (overflowModel.label||getModelLabel(overflowModel.id));
        _modelData.push({
          value:overflowModel.id,
          name:esc(displayName),
          id:esc(overflowModel.id),
          group:child.label||'',
          groupKey,
          providerId,
          modelsEndpointError,
          badge:_getConfiguredModelBadge(overflowModel.id,_badgeMap,providerId),
          hiddenByDefault:true,
        });
        groupMeta.modelCount++;
        groupMeta.hiddenCount++;
      }
      if(modelsEndpointError && !child.children.length && !groupMeta.hiddenCount){
        groupMeta.endpointErrorOnly=true;
        _modelData.push({value:`__models_endpoint_error__:${providerId||child.label||''}`,name:'',id:'',group:child.label||'',groupKey,providerId,modelsEndpointError,endpointErrorOnly:true});
      }
    }
    if(child.tagName==='OPTION'){
      const groupKey='__ungrouped__';
      _ensureGroupMeta(groupKey,'','',null);
      const rawValue=String(child.value||'');
      const displayName=rawValue.startsWith('@custom:')
        ? getModelLabel(rawValue)
        : (child.textContent||getModelLabel(rawValue));
      _modelData.push({value:child.value,name:esc(displayName),id:esc(child.value),group:'',groupKey,providerId:'',badge:_getConfiguredModelBadge(child.value,_badgeMap),hiddenByDefault:false});
      _groupMeta.get(groupKey).modelCount++;
    }
  }
  for(const [modelId,badge] of Object.entries(_badgeMap)){
    if(_isEquivalentConfiguredModelEntry(modelId,badge,_modelData)) continue;
    _modelData.push({
      value:modelId,
      name:esc(getModelLabel(modelId)),
      id:esc(modelId),
      group:'',
      badge,
    });
  }
  // Create search input FIRST before filterModels definition
  const _scopeNote=document.createElement('div');
  _scopeNote.className='model-scope-note';
  _scopeNote.textContent=opts.scopeNoteText||(t('model_scope_advisory')||'Applies to this conversation from your next message.');
  const _searchRow=document.createElement('div');
  _searchRow.className='model-search-row';
  _searchRow.innerHTML=`<input class="model-search-input" type="text" placeholder="${esc(t('model_search_placeholder')||'Search models…')}" spellcheck="false" autocomplete="off"><button class="model-search-clear" title="Clear search">${li('x',10)}</button>`;
  const _si=_searchRow.querySelector('.model-search-input');
  const _sc=_searchRow.querySelector('.model-search-clear');
  // Create custom model section elements
  const _custSep=document.createElement('div');
  _custSep.className='model-group model-custom-sep';
  _custSep.textContent=t('model_custom_label')||'Custom model ID';
  const _custRow=document.createElement('div');
  _custRow.className='model-custom-row';
  _custRow.innerHTML=`<input class="model-custom-input" type="text" placeholder="${esc(t('model_custom_placeholder')||'e.g. openai/gpt-5.4')}" spellcheck="false" autocomplete="off"><button class="model-custom-btn" title="Use this model">${li('plus',12)}</button>`;
  const _ci=_custRow.querySelector('.model-custom-input');
  const _cb=_custRow.querySelector('.model-custom-btn');
  const _configuredRank=(badge)=>{
    if(!badge) return Number.POSITIVE_INFINITY;
    if(badge.role==='primary') return 0;
    if(badge.role==='fallback'){
      const m=String(badge.label||'').match(/fallback\s+(\d+)/i);
      return m?Number(m[1]):999;
    }
    return 500;
  };
  const _selectedModelState=(typeof _modelStateForSelect==='function')?_modelStateForSelect(sel,sel.value):{model:sel&&sel.value||'',model_provider:null};
  const _modelProviderForSelectedBadge=(m)=>{
    const _provider=String((m&&m.providerId)||(m&&m.badge&&m.badge.provider)||((typeof _providerFromModelValue==='function')?_providerFromModelValue(m&&m.value):'')||'').trim();
    return (_provider&&_provider!=='default')?_provider:null;
  };
  const _isSelectedModelRow=(m)=>String((m&&m.value)||'')===String((_selectedModelState&&_selectedModelState.model)||(sel&&sel.value)||'')&&String(_modelProviderForSelectedBadge(m)||'')===String((_selectedModelState&&_selectedModelState.model_provider)||'');
  const _selectedModelBadge=(m)=>_isSelectedModelRow(m)
    ?`<span class="model-opt-badge model-opt-badge--selected">${esc(t('model_badge_selected')||'Selected')}</span>`
    :'';
  const _renderProviderEndpointHint=(entry,parent)=>{
    if(!entry||!entry.label||!entry.modelsEndpointError) return;
    const hint=document.createElement('div');
    hint.className='model-provider-hint';
    hint.textContent=entry.modelsEndpointError.message||'Models endpoint could not be reached for this provider.';
    (parent||dd).appendChild(hint);
  };
  // Build a single model-option row (mirrors the main render loop's row markup),
  // used both by the main render and by the in-place overflow reveal below.
  const _buildModelRow=(m,withProviderChip)=>{
    const row=document.createElement('div');
    row.className='model-opt'+(_isSelectedModelRow(m)?' active':'');
    const badgeLabel=(m.badge&&(m.badge.label||(typeof m.badge==='string'?m.badge:'Configured')))||'Configured';
    const badgeRole=(m.badge&&m.badge.role)||'configured';
    const badgeHtml=m.badge?`<span class="model-opt-badge model-opt-badge--${esc(badgeRole)}">${esc(badgeLabel)}</span>`:'';
    const _plainGroup=m.group?String(m.group).replace(/\s*\(\d+\s+of\s+\d+\)\s*$/,''):'';
    const providerChip=(_plainGroup&&withProviderChip)?`<span class="model-opt-provider">${esc(_plainGroup)}</span>`:'';
    const subText=m.desc||(m.name!==m.id?m.id:'');
    const subHtml=subText?`<span class="model-opt-id">${esc(subText)}</span>`:'';
    row.innerHTML=`<div class="model-opt-top"><span class="model-opt-name">${esc(m.name)}</span>${badgeHtml}${_selectedModelBadge(m)}${providerChip}</div>${subHtml}`;
    row.onclick=()=>selectFromDropdown(m.value,m.providerId||(m.badge&&m.badge.provider)||null);
    return row;
  };
  const _expandOverflowGroup=(groupMetaEntry)=>{
    if(!groupMetaEntry||!groupMetaEntry.optgroup) return;
    const og=groupMetaEntry.optgroup;
    const groupKey=groupMetaEntry.key;
    const extraModels=_readModelOverflowData(og);
    // Nothing to reveal — no overflow tail advertised.
    if(!extraModels.length) return;
    // Append the overflow models to the source <select> so the dropdown's state
    // stays the source of truth (search, re-render, selection all see them).
    // NOTE: guard on extraModels.length (above), NOT on the append return value —
    // _appendOverflowOptionsToGroup returns the count of NEWLY-created <option>s
    // and returns 0 (while still clearing dataset.extraModels) when every overflow
    // model already existed as an option. Bailing on a 0 return would leave those
    // already-present-but-hidden rows unrevealed and the expander dead (#bug3).
    _appendOverflowOptionsToGroup(og,extraModels);
    // Full re-render fallback — the proven path. Used when the in-place reveal
    // can't run (minimal/headless DOM without CSS.escape/rAF/insertBefore, or any
    // unexpected failure). Produces the same end state: overflow appended,
    // expander gone, search term reapplied.
    const _fullReRender=()=>{
      const _term=(_si&&_si.value)||'';
      renderModelDropdown(opts);
      const ns=dd.querySelector('.model-search-input');
      if(ns){ ns.value=_term; (ns._listeners&&ns._listeners.input)?ns._listeners.input():ns.dispatchEvent(new Event('input')); }
    };
    // IN-PLACE reveal: build the newly-revealed rows and insert them directly into
    // the existing group wrapper (before the "Show more" expander), then remove
    // the expander. No full re-render — so the group stays open, every other
    // group keeps its collapsed/open state, and the scroll position is preserved.
    // The user lands on the first new row. Falls back to a full re-render if the
    // runtime lacks the DOM APIs this needs.
    const _canInPlace = typeof CSS!=='undefined' && CSS && typeof CSS.escape==='function'
      && typeof dd.querySelector==='function';
    if(!_canInPlace){ _fullReRender(); return; }
    let wrap, moreEl;
    try{
      wrap=dd.querySelector(`.model-group-body[data-group="${CSS.escape(groupKey)}"]`);
      moreEl=wrap?wrap.querySelector('.model-opt-more'):null;
    }catch(_){ _fullReRender(); return; }
    if(!wrap||!moreEl||typeof wrap.insertBefore!=='function'){
      _fullReRender();
      return;
    }
    try{
      const _plainLabel=String(groupMetaEntry.label||'').replace(/\s*\(\d+\s+of\s+\d+\)\s*$/,'');
      const _alreadyShown=new Set(Array.from(wrap.querySelectorAll('.model-opt .model-opt-id')).map(el=>el.textContent));
      let firstNewRow=null;
      for(const m of extraModels){
        if(!m||!m.id) continue;
        if(_alreadyShown.has(esc(m.id))) continue;
        const row=_buildModelRow({value:m.id,name:m.label||m.id,id:m.id,group:_plainLabel,groupKey,providerId:(og.dataset&&og.dataset.provider)||''},false);
        wrap.insertBefore(row,moreEl);
        if(!firstNewRow) firstNewRow=row;
      }
      // Sync the in-memory model data so a later _filterModels() re-render (e.g.
      // after a search is typed and cleared) keeps the group fully expanded
      // instead of snapping back to the capped view + a fresh "Show more". The
      // overflow rows were just appended to the live <select>, so flip their
      // _modelData entries to no-longer-hidden and zero the group's hidden count.
      for(const _md of _modelData){
        if(_md && _md.groupKey===groupKey && _md.hiddenByDefault){
          _md.hiddenByDefault=false;
        }
      }
      if(groupMetaEntry && typeof groupMetaEntry.hiddenCount==='number'){
        groupMetaEntry.hiddenCount=0;
      }
      // The group is now fully expanded — drop the "Show more" expander, and bump
      // the heading count to the full total. Also force the group OPEN (the user
      // just asked to see more of it) regardless of any prior collapsed state.
      moreEl.remove();
      wrap.style.display='';
      _forceOpenGroups.add(groupKey);
      const heading=wrap.previousElementSibling;
      if(heading&&heading.classList&&heading.classList.contains('model-group')){
        const _total=wrap.querySelectorAll('.model-opt').length;
        heading.textContent=_total>1?`${_plainLabel} (${_total})`:_plainLabel;
        heading.classList.add('collapsible','open');
      }
      // Scroll so the first newly-revealed row sits near the top of the dropdown
      // viewport — the user asked to "land on the new models" after Show more,
      // not be reset to the top of the list and not have it jump unpredictably.
      if(firstNewRow && typeof firstNewRow.offsetTop==='number' && typeof requestAnimationFrame==='function'){
        const _targetTop=Math.max(0,firstNewRow.offsetTop-48);
        const _doScroll=()=>{ try{ dd.scrollTop=_targetTop; }catch(_){} };
        _doScroll();                                   // immediate
        requestAnimationFrame(()=>{ _doScroll(); requestAnimationFrame(_doScroll); });
        if(typeof setTimeout==='function') setTimeout(_doScroll,80); // after any refocus settles
      }
    }catch(_err){
      // Any unexpected DOM failure — fall back to the proven full re-render so
      // the overflow still gets revealed.
      _fullReRender();
    }
  };
  // Collapsible group state — persists across _filterModels calls
  const _groupOpenState={};
  let _prevHasSearch=false;  // tracks search->empty transition to reset open-state
  let _groupWrappers={};
  // The group that owns the currently-selected model. Groups start COLLAPSED by
  // default (#4279); the selected provider's group is the one exception so the
  // user always sees their active model without expanding anything. (#4279 + UX)
  const _selectedGroupKey=(()=>{
    const _selVal=String((sel&&sel.value)||'');
    if(!_selVal) return null;
    const _hit=_modelData.find(m=>m&&!m.endpointErrorOnly&&_isSelectedModelRow(m)) || _modelData.find(m=>m&&!m.endpointErrorOnly&&String(m.value||'')===_selVal);
    return _hit?_hit.groupKey:null;
  })();
  const _makeModelRow=(m,shouldRenderHeading)=>{
    const row=document.createElement('div');
    row.className='model-opt'+(_isSelectedModelRow(m)?' active':'');
    const badgeLabel=(m.badge&&(m.badge.label||(typeof m.badge==='string'?m.badge:'Configured')))||'Configured';
    const badgeRole=(m.badge&&m.badge.role)||'configured';
    const badgeHtml=m.badge?`<span class="model-opt-badge model-opt-badge--${esc(badgeRole)}">${esc(badgeLabel)}</span>`:'';
    const _plainGroup=m.group?String(m.group).replace(/\s*\(\d+\s+of\s+\d+\)\s*$/,''):'';
    const _underOwnHeading=shouldRenderHeading&&!!(m.groupKey&&_groupWrappers[m.groupKey]);
    const providerChip=(_plainGroup&&!_underOwnHeading)?`<span class="model-opt-provider">${esc(_plainGroup)}</span>`:'';
    const subText=m.desc||(m.name!==m.id?m.id:'');
    const subHtml=subText?`<span class="model-opt-id">${esc(subText)}</span>`:'';
    row.innerHTML=`<div class="model-opt-top"><span class="model-opt-name">${esc(m.name)}</span>${badgeHtml}${_selectedModelBadge(m)}${providerChip}</div>${subHtml}`;
    row.onclick=()=>selectFromDropdown(m.value,m.providerId||(m.badge&&m.badge.provider)||null);
    return row;
  };
  const _filterModels=(term)=>{
    // Preserve focus across the re-render if the search input already had it — so a
    // touch user typing a query (where autoFocusSearch is suppressed to avoid the
    // initial keyboard pop) doesn't lose focus mid-word on each keystroke re-render.
    const _hadFocus=(typeof document!=='undefined')&&document.activeElement===_si;
    term=term.trim().toLowerCase();
    const hasSearch=!!term;
    // On a fresh search, expand all groups so every match is visible (#collapse).
    if(hasSearch) for(const k in _groupOpenState) _groupOpenState[k]=true;
    // When a search is CLEARED (search -> empty), reset the per-group open state
    // so the collapsed-except-selected default re-applies — otherwise every group
    // the search auto-expanded would stay open, defeating the collapse UX. Groups
    // the user explicitly expanded via "Show more" (_forceOpenGroups) and the
    // selected group remain open through the defaulting logic below.
    else if(_prevHasSearch){ for(const k in _groupOpenState) delete _groupOpenState[k]; }
    _prevHasSearch=hasSearch;
    const found=new Set();
    for(const m of _modelData){
      const name=m.name.toLowerCase();
      const id=m.id.toLowerCase();
      if(name.includes(term)||id.includes(term)){
        found.add(m.value);
      }
    }
    const matches=(m)=>!term||found.has(m.value);
    const configuredCandidates=_modelData
      .filter(m=>m.badge&&matches(m));
    const configuredBySemanticKey=new Map();
    const _configuredProviderKey=(m)=>String((m&&m.badge&&m.badge.provider)||_providerFromModelValue(m&&m.value)||'').toLowerCase();
    const _configuredModelKey=(m)=>_normalizeConfiguredModelKey(m&&m.value||'');
    const _configuredDisplayPriority=(m)=>{
      // Prefer plain IDs over provider-qualified aliases for readability.
      const v=String((m&&m.value)||'');
      if(v.startsWith('@')) return 0;
      if(v.includes('/')) return 1;
      return 2;
    };
    for(const candidate of configuredCandidates){
      const semanticKey=`${_configuredProviderKey(candidate)}::${_configuredModelKey(candidate)}`;
      const existing=configuredBySemanticKey.get(semanticKey);
      if(!existing){
        configuredBySemanticKey.set(semanticKey,candidate);
        continue;
      }
      const candidatePriority=_configuredDisplayPriority(candidate);
      const existingPriority=_configuredDisplayPriority(existing);
      if(candidatePriority>existingPriority){
        configuredBySemanticKey.set(semanticKey,candidate);
      }
    }
    const configuredModels=[...configuredBySemanticKey.values()]
      .sort((a,b)=>{
        const configuredRankA=_configuredRank(a.badge);
        const configuredRankB=_configuredRank(b.badge);
        if(configuredRankA!==configuredRankB) return configuredRankA-configuredRankB;
        return a.name.localeCompare(b.name);
      });
    const configuredIds=new Set(configuredModels.map(m=>m.value));
    const configuredSemanticKeys=new Set(configuredModels.map(m=>`${_configuredProviderKey(m)}::${_configuredModelKey(m)}`));
    const _effectiveHiddenCount=(groupKey)=>_modelData.filter(m=>
      m.groupKey===groupKey
      && m.hiddenByDefault
      && !configuredSemanticKeys.has(`${_configuredProviderKey(m)}::${_configuredModelKey(m)}`)
    ).length;
    dd.innerHTML='';
    dd.appendChild(_scopeNote);
    dd.appendChild(_searchRow);
    dd.appendChild(_custSep);
    dd.appendChild(_custRow);
    if(configuredModels.length){
      const configuredHeading=document.createElement('div');
      configuredHeading.className='model-group';
      configuredHeading.textContent=t('model_group_configured')||'Configured';
      dd.appendChild(configuredHeading);
      for(const m of configuredModels){
        const row=document.createElement('div');
        row.className='model-opt'+(_isSelectedModelRow(m)?' active':'');
        const badgeLabel=(m.badge&&(m.badge.label||(typeof m.badge==='string'?m.badge:'Configured')))||'Configured';
        const badgeRole=(m.badge&&m.badge.role)||'configured';
        const subText=m.desc||(m.name!==m.id?m.id:'');
        const subHtml=subText?`<span class="model-opt-id">${esc(subText)}</span>`:'';
        row.innerHTML=`<div class="model-opt-top"><span class="model-opt-name">${esc(m.name)}</span>${badgeHtml}${_selectedModelBadge(m)}</div>${subHtml}`;
        row.onclick=()=>selectFromDropdown(m.value,(m.badge&&m.badge.provider)||m.providerId||null);
        dd.appendChild(row);
      }
    }
    for(const groupKey of _groupOrder){
      const meta=_groupMeta.get(groupKey);
      if(!meta) continue;
      const hiddenCount=_effectiveHiddenCount(groupKey);
      const groupRows=_modelData.filter(m=>
        m.groupKey===groupKey
        && !configuredIds.has(m.value)
        && !m.endpointErrorOnly
        && matches(m)
        && (!m.hiddenByDefault || !!term)
      );
      const shouldRenderHeading=!!meta.label&&(groupRows.length||meta.endpointErrorOnly||(!term&&hiddenCount));
      if(shouldRenderHeading){
        const heading=document.createElement('div');
        heading.className='model-group';
        // When COLLAPSED (hiddenCount>0) keep the backend-decorated label verbatim
        // ("Nous (2 of 4)") so the overflow count shows. When EXPANDED, strip that
        // decoration and append the rendered-row count, otherwise the heading reads
        // "Nous (2 of 4) (4)" (double count). Count rendered rows, not modelCount,
        // so hoisted-configured models aren't double-counted. (#3691)
        const count=hiddenCount?0:groupRows.length;
        const _plainLabel=String(meta.label||'').replace(/\s*\(\d+\s+of\s+\d+\)\s*$/,'');
        heading.textContent=count>1?`${_plainLabel} (${count})`:meta.label;
        dd.appendChild(heading);
        const wrapper=document.createElement('div');
        wrapper.className='model-group-body';
        wrapper.dataset.group=groupKey;
        // A group carrying a provider endpoint-error hint must stay visible by
        // default — otherwise the "models endpoint unreachable" warning is hidden
        // inside a collapsed body and the user never sees it. (#2540 surface)
        const _hasEndpointError=!!(meta&&(meta.modelsEndpointError||meta.endpointErrorOnly));
        if(hasSearch) _groupOpenState[groupKey]=true;
        else if(_forceOpenGroups.has(groupKey)) _groupOpenState[groupKey]=true;
        else if(_hasEndpointError) _groupOpenState[groupKey]=true;
        else if(!(groupKey in _groupOpenState)) _groupOpenState[groupKey]=(groupKey===_selectedGroupKey);
        if(!_groupOpenState[groupKey]) wrapper.style.display='none';
        else heading.classList.add('open');
        heading.classList.add('collapsible');
        dd.appendChild(wrapper);
        _groupWrappers[groupKey]=wrapper;
        // Render the provider endpoint-error hint inside the collapsible group
        // so it collapses/expands with it (the group is force-opened above when
        // an error is present, so the hint stays visible by default).
        _renderProviderEndpointHint(meta,wrapper);
        heading.addEventListener('click',(e)=>{
          e.stopPropagation();
          const w=dd.querySelector(`.model-group-body[data-group="${CSS.escape(groupKey)}"]`);
          if(!w) return;
          const closed=w.style.display==='none';
          w.style.display=closed?'':'none';
          _groupOpenState[groupKey]=closed;
          // Keep the cross-render force-open intent in sync with manual toggles:
          // collapsing a previously overflow-expanded group should let it
          // re-collapse on the next render too.
          if(closed) _forceOpenGroups.add(groupKey); else _forceOpenGroups.delete(groupKey);
          heading.classList.toggle('open',closed);
        });
        const useSubGroups=(
          SUB_GROUP_PROVIDERS.has(meta.providerId) &&
          groupRows.length>=SUB_GROUP_MIN_MODELS
        );
        if(useSubGroups){
          const byPrefix=new Map();
          for(const m of groupRows){
            const pfx=_vendorPrefix(m.value)||'other';
            if(!byPrefix.has(pfx)) byPrefix.set(pfx,[]);
            byPrefix.get(pfx).push(m);
          }
          const sorted=[...byPrefix.entries()].sort((a,b)=>{
            if(a[0]==='other') return 1;
            if(b[0]==='other') return -1;
            return b[1].length-a[1].length;
          });
          for(const [pfx,pfxRows] of sorted){
            if(pfxRows.length>=2){
              const subKey=`${groupKey}::${pfx}`;
              if(!(subKey in _groupOpenState)) _groupOpenState[subKey]=true;
              if(hasSearch) _groupOpenState[subKey]=true;
              const subHeading=document.createElement('div');
              subHeading.className='model-group sub collapsible';
              subHeading.dataset.group=subKey;
              if(_groupOpenState[subKey]) subHeading.classList.add('open');
              subHeading.textContent=pfx;
              const subWrapper=document.createElement('div');
              subWrapper.className='model-group-body sub';
              subWrapper.dataset.group=subKey;
              if(!_groupOpenState[subKey]) subWrapper.style.display='none';
              subHeading.addEventListener('click',(e)=>{
                e.stopPropagation();
                const closed=subWrapper.style.display==='none';
                subWrapper.style.display=closed?'':'none';
                _groupOpenState[subKey]=closed;
                subHeading.classList.toggle('open',closed);
              });
              wrapper.appendChild(subHeading);
              wrapper.appendChild(subWrapper);
              for(const m of pfxRows) subWrapper.appendChild(_makeModelRow(m,shouldRenderHeading));
            } else {
              for(const m of pfxRows) wrapper.appendChild(_makeModelRow(m,shouldRenderHeading));
            }
          }
        } else {
          for(const m of groupRows) wrapper.appendChild(_makeModelRow(m,shouldRenderHeading));
        }
      } else {
        for(const m of groupRows) dd.appendChild(_makeModelRow(m,shouldRenderHeading));
      }
      if(!term&&hiddenCount){
        const showAll=document.createElement('div');
        showAll.className='model-opt-more';
        showAll.tabIndex=0;
        showAll.setAttribute('role','button');
        const _moreLabel=esc(t('model_show_all_models',hiddenCount)||`Show ${hiddenCount} more`);
        showAll.innerHTML=`<span class="model-opt-more-chevron" aria-hidden="true"></span><span class="model-opt-more-label">${_moreLabel}</span>`;
        const _doExpand=()=>{
          // The reveal itself (in-place row insert + open + scroll-to-new) is
          // handled by _expandOverflowGroup; just trigger it.
          _expandOverflowGroup(meta);
        };
        showAll.onclick=(e)=>{
          if(e&&typeof e.stopPropagation==='function') e.stopPropagation();
          _doExpand();
        };
        showAll.addEventListener('keydown',e=>{
          if(e.key==='Enter'||e.key===' '){
            e.preventDefault();
            _doExpand();
          }
        });
        // Keep the expander inside the collapsible group so it hides/shows with it.
        if(_groupWrappers[groupKey]) _groupWrappers[groupKey].appendChild(showAll);
        else dd.appendChild(showAll);
      }
    }
    if(term&&found.size===0){
      const noResult=document.createElement('div');
      noResult.className='model-search-no-results';
      noResult.textContent=t('model_search_no_results')||'No models found';
      noResult.style.padding='12px 14px';
      noResult.style.color='var(--muted)';
      noResult.style.textAlign='center';
      dd.appendChild(noResult);
    }
    if(_autoFocusSearch||_hadFocus) _si.focus();
  };
  _si.addEventListener('input',()=>_filterModels(_si.value));
  // Keyboard navigation through filtered model rows (#2791).
  const _visibleModelRows=()=>Array.from(dd.querySelectorAll('.model-opt,.model-opt-more')).filter(el=>{
    let node=el.parentElement;
    while(node&&node!==dd){
      if(node.classList.contains('model-group-body')&&node.style.display==='none') return false;
      node=node.parentElement;
    }
    return true;
  });
  const _activeRowIndex=(rows)=>rows.findIndex(r=>r.classList.contains('is-highlighted'));
  const _highlightRow=(rows,idx)=>{
    for(const r of rows) r.classList.remove('is-highlighted');
    if(idx<0||idx>=rows.length) return;
    const row=rows[idx];
    row.classList.add('is-highlighted');
    if(typeof row.scrollIntoView==='function') row.scrollIntoView({block:'nearest'});
  };
  _si.addEventListener('keydown',e=>{
    if(e.key==='Escape'){closeDropdown();return;}
    if(e.key==='ArrowDown'||e.key==='ArrowUp'||e.key==='Enter'){
      const rows=_visibleModelRows();
      if(!rows.length){if(e.key==='Enter') e.preventDefault();return;}
      const cur=_activeRowIndex(rows);
      if(e.key==='ArrowDown'){e.preventDefault();_highlightRow(rows,cur<0?0:Math.min(rows.length-1,cur+1));return;}
      if(e.key==='ArrowUp'){e.preventDefault();_highlightRow(rows,cur<=0?rows.length-1:cur-1);return;}
      if(e.key==='Enter'){
        e.preventDefault();
        const pick=cur>=0?rows[cur]:rows[0];
        if(pick) pick.click();
      }
    }
  });
  _si.addEventListener('click',e=>e.stopPropagation());
  _sc.onclick=()=>{ _si.value=''; _filterModels(''); _si.focus(); };
  _sc.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){ _si.value=''; _filterModels(''); _si.focus(); e.preventDefault(); }});
  const _applyCustom=()=>{const v=_ci.value.trim();if(!v)return;selectFromDropdown(v,null);_ci.value='';};
  _cb.onclick=_applyCustom;
  _ci.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();_applyCustom();}if(e.key==='Escape'){closeDropdown();}});
  _ci.addEventListener('click',e=>e.stopPropagation());
  dd.appendChild(_scopeNote);
  dd.appendChild(_searchRow);
  dd.appendChild(_custSep);
  dd.appendChild(_custRow);
  _filterModels('');
}

async function selectModelFromDropdown(value){
  const preferredProviderId=arguments[1];
  const sel=$('modelSelect');
  if(!sel) { closeModelDropdown(); return; }
  const provider=String(preferredProviderId||'').trim()||null;
  const currentState=(typeof _modelStateForSelect==='function')
    ? _modelStateForSelect(sel, sel.value)
    : {model:sel.value,model_provider:null};
  const sameModel=String(currentState.model||'')===String(value||'');
  const sameProvider=String(currentState.model_provider||'')===String(provider||'');
  if(sameModel&&sameProvider){ closeModelDropdown(); return; }
  // Resolve the provider-specific option so duplicate bare IDs (e.g. gpt-5.5
  // under OpenAI Codex vs OpenRouter) update session model_provider correctly.
  if(typeof _ensureModelOptionInDropdown==='function'){
    _ensureModelOptionInDropdown(value, sel, provider);
  }else{
    sel.value=value;
  }
  syncModelChip();
  closeModelDropdown();
  if(typeof sel.onchange==='function') await sel.onchange();
}

async function toggleModelDropdown(){
  const dd=$('composerModelDropdown');
  const chip=$('composerModelChip');
  const sel=$('modelSelect');
  if(!dd||!chip||!sel) return;
  const open=dd.classList.contains('open');
  if(open){closeModelDropdown(); return;}
  if(typeof closeProfileDropdown==='function') closeProfileDropdown();
  if(typeof closeWsDropdown==='function') closeWsDropdown();
  if(typeof closeReasoningDropdown==='function') closeReasoningDropdown();
  if(typeof closeToolsetsDropdown==='function') closeToolsetsDropdown();
  if(typeof window._ensureModelDropdownReady==='function'){
    const ready=window._ensureModelDropdownReady();
    if(ready&&typeof ready.catch==='function') ready.catch(()=>{});
  }
  if(dd.classList.contains('open')) return;
  renderModelDropdown();
  dd.classList.add('open');
  _positionModelDropdown();
  const activeRow=dd.querySelector('.model-opt.active');
  if(activeRow&&typeof activeRow.scrollIntoView==='function') activeRow.scrollIntoView({block:'nearest'});
  chip.classList.add('active');
  const mobileAction=$('composerMobileModelAction');
  if(mobileAction) mobileAction.classList.add('active');
}

function closeModelDropdown(){
  const dd=$('composerModelDropdown');
  const chip=$('composerModelChip');
  const mobileAction=$('composerMobileModelAction');
  if(dd) dd.classList.remove('open');
  if(chip) chip.classList.remove('active');
  if(mobileAction) mobileAction.classList.remove('active');
  // If the phone path reparented the menu onto <body>, put it back in the
  // footer and clear the fixed-position inline styles so the DOM returns to its
  // baseline shape and the next desktop open anchors correctly (#6080).
  if(typeof _restoreModelDropdownHome==='function') _restoreModelDropdownHome();
}

function closeSettingsModelDropdown(){
  const dd=$('settingsModelDropdown');
  const chip=$('settingsModelChip');
  if(dd) dd.classList.remove('open');
  if(chip){
    chip.classList.remove('active');
    chip.setAttribute('aria-expanded','false');
  }
}

function syncSettingsModelChip(){
  const sel=$('settingsModel');
  const chip=$('settingsModelChip');
  if(!sel||!chip) return;
  const opt=sel.selectedOptions&&sel.selectedOptions[0];
  const text=(opt&&opt.textContent)||getModelLabel(sel.value||'')||t('settings_label_model')||'Default Model';
  chip.textContent=text;
  chip.title=sel.value||text;
}

function selectSettingsModelFromDropdown(value,preferredProviderId){
  const sel=$('settingsModel');
  if(!sel){closeSettingsModelDropdown();return;}
  const provider=String(preferredProviderId||'').trim()||null;
  if(typeof _ensureModelOptionInDropdown==='function'){
    _ensureModelOptionInDropdown(value,sel,provider);
  }else{
    sel.value=value;
    if(typeof syncSettingsModelChip==='function') syncSettingsModelChip();
  }
  closeSettingsModelDropdown();
  try{
    if(typeof Event==='function') sel.dispatchEvent(new Event('change',{bubbles:true}));
    else if(typeof sel.onchange==='function') sel.onchange();
  }catch(_){}
}

function openSettingsModelDropdown(){
  const dd=$('settingsModelDropdown');
  const sel=$('settingsModel');
  const chip=$('settingsModelChip');
  if(!dd||!sel) return;
  // Auto-focus the search on desktop only. On touch (coarse pointer) grabbing focus
  // pops the on-screen keyboard the instant the chip is tapped — the composer picker
  // doesn't do it either, so match that behavior on touch. Computed before render so
  // renderModelDropdown's own initial focus is suppressed too (not just the outer one).
  const _coarsePointer=(typeof window.matchMedia==='function')&&window.matchMedia('(pointer: coarse)').matches;
  renderModelDropdown({
    dropdownId:'settingsModelDropdown',
    selectId:'settingsModel',
    forceOpenKey:'settingsModel',
    closeDropdown:closeSettingsModelDropdown,
    selectModel:selectSettingsModelFromDropdown,
    scopeNoteText:t('settings_desc_model')||'Used for new conversations. Existing conversations keep their selected model.',
    autoFocusSearch:!_coarsePointer,
  });
  dd.classList.add('open');
  if(chip){
    chip.classList.add('active');
    chip.setAttribute('aria-expanded','true');
  }
  if(!_coarsePointer){
    setTimeout(()=>{
      const input=dd.querySelector('.model-search-input');
      if(input) input.focus();
    },0);
  }
}

function toggleSettingsModelDropdown(){
  const dd=$('settingsModelDropdown');
  if(dd&&dd.classList.contains('open')){closeSettingsModelDropdown();return;}
  openSettingsModelDropdown();
}

function mountSettingsModelPicker(){
  const chip=$('settingsModelChip');
  const sel=$('settingsModel');
  if(!chip||!sel) return;
  syncSettingsModelChip();
  if(!chip._settingsModelPickerBound){
    chip._settingsModelPickerBound=true;
    chip.addEventListener('click',e=>{
      e.preventDefault();
      e.stopPropagation();
      toggleSettingsModelDropdown();
    });
    chip.addEventListener('keydown',e=>{
      if(e.key==='Enter'||e.key===' '||e.key==='ArrowDown'){
        e.preventDefault();
        toggleSettingsModelDropdown();
      }
    });
  }
}

document.addEventListener('click',e=>{
  if(
    !e.target.closest('#composerModelChip') &&
    !e.target.closest('#composerMobileModelAction') &&
    !e.target.closest('#composerModelDropdown')
  ) closeModelDropdown();
  if(
    !e.target.closest('#settingsModelChip') &&
    !e.target.closest('#settingsModel') &&
    !e.target.closest('#settingsModelDropdown')
  ) closeSettingsModelDropdown();
});
window.addEventListener('resize',()=>{
  const dd=$('composerModelDropdown');
  if(dd&&dd.classList.contains('open')) _positionModelDropdown();
  // Keep the reasoning dropdown aligned under its chip when the window
  // resizes while open — same pattern as the model dropdown above.
  const rdd=$('composerReasoningDropdown');
  if(rdd&&rdd.classList.contains('open')&&typeof _positionReasoningDropdown==='function'){
    _positionReasoningDropdown();
  }
});

// visualViewport resize/scroll fire on mobile when the on-screen keyboard opens
// or the URL bar collapses/expands — the phone dropdown is fixed to the visual
// viewport, so it must be re-measured against the new offsets. Coalesce with rAF
// so a burst of scroll/resize events triggers at most one reposition per frame.
let _modelDropdownRepositionScheduled=false;
function _repositionOpenModelDropdown(){
  const dd=$('composerModelDropdown');
  if(!(dd&&dd.classList.contains('open'))||_modelDropdownRepositionScheduled) return;
  _modelDropdownRepositionScheduled=true;
  requestAnimationFrame(()=>{
    _modelDropdownRepositionScheduled=false;
    const openDd=$('composerModelDropdown');
    if(openDd&&openDd.classList.contains('open')) _positionModelDropdown();
  });
}
if(window.visualViewport){
  window.visualViewport.addEventListener('resize',_repositionOpenModelDropdown);
  window.visualViewport.addEventListener('scroll',_repositionOpenModelDropdown);
}

// ── Fit-based composer footer collapse ──────────────────────────────────────
// Stage classes on .composer-footer:
//   (none) full labels · .cf-icons icon chips · .cf-icons.cf-burger hamburger.
let _composerFitScheduled=false;
let _composerFitResizeObserver=null;
let _composerFitMutationObserver=null;
let _composerFitObservedFooter=null;
let _composerFitResizeListenerBound=false;

function _fitComposerFooter(){
  const footer=document.querySelector('.composer-footer');
  if(!footer) return;
  const left=footer.querySelector('.composer-left');
  if(!left) return;
  if(!left.clientWidth) return;
  const overflows=function(){return left.scrollWidth>left.clientWidth+1;};
  footer.classList.remove('cf-icons','cf-burger');
  if(!overflows()) return;
  footer.classList.add('cf-icons');
  if(!overflows()) return;
  footer.classList.add('cf-burger');
}
window._fitComposerFooter=_fitComposerFooter;

function _scheduleComposerFit(){
  if(_composerFitScheduled) return;
  _composerFitScheduled=true;
  requestAnimationFrame(function(){
    _composerFitScheduled=false;
    try{_fitComposerFooter();}catch(_){ }
  });
}
window._scheduleComposerFit=_scheduleComposerFit;

function _initComposerFooterFit(){
  const footer=document.querySelector('.composer-footer');
  const left=footer&&footer.querySelector('.composer-left');
  if(!footer||!left) return;
  _scheduleComposerFit();
  if(_composerFitObservedFooter===footer) return;
  if(_composerFitResizeObserver){try{_composerFitResizeObserver.disconnect();}catch(_){ }}
  if(_composerFitMutationObserver){try{_composerFitMutationObserver.disconnect();}catch(_){ }}
  _composerFitResizeObserver=null;
  _composerFitMutationObserver=null;
  _composerFitObservedFooter=footer;
  if(window.ResizeObserver){
    try{
      _composerFitResizeObserver=new ResizeObserver(_scheduleComposerFit);
      _composerFitResizeObserver.observe(footer);
      // Also observe the left control group directly: the footer's outer width
      // may not change when right-side controls (status/context chips) appear or
      // resize, but that shrinks .composer-left's available room and must
      // retrigger a refit. (Codex gate #4657.)
      if(left && left!==footer){try{_composerFitResizeObserver.observe(left);}catch(_){ }}
    }catch(_){ }
  }
  if(window.MutationObserver){
    try{
      _composerFitMutationObserver=new MutationObserver(_scheduleComposerFit);
      _composerFitMutationObserver.observe(left,{
        childList:true,subtree:true,characterData:true,
        attributes:true,attributeFilter:['class','style','hidden']
      });
    }catch(_){ }
  }
  if(!_composerFitResizeListenerBound){
    window.addEventListener('resize',_scheduleComposerFit);
    _composerFitResizeListenerBound=true;
  }
}
window._initComposerFooterFit=_initComposerFooterFit;

if(document.readyState==='loading'){
  document.addEventListener('DOMContentLoaded',_initComposerFooterFit);
}else{
  _initComposerFooterFit();
}

// ── Reasoning effort chip ────────────────────────────────────────────────────
let _currentReasoningEffort=null;
let _currentReasoningEffortsSupported=null;
// Whether the model accepts the thinking on/off toggle when supported_efforts
// is empty (GLM-4.5–5.1 on native zai). Undefined = unknown, treated as true
// so the chip stays visible by default (prior behavior).
let _currentReasoningToggleSupported=undefined;
let _profileTransitionReasoningContext=null;

function _normalizeReasoningEffort(eff){
  return String(eff||'').trim().toLowerCase();
}

function _formatReasoningEffortLabel(effort){
  if(effort==='none') return 'None';
  if(!effort) return 'Default';
  if(effort==='minimal') return 'Minimal';
  if(effort==='low') return 'Low';
  if(effort==='medium') return 'Medium';
  if(effort==='high') return 'High';
  if(effort==='xhigh') return 'XHigh';
  if(effort==='max') return 'Max';
  return effort.charAt(0).toUpperCase()+effort.slice(1);
}

function _reasoningEffortContext(){
  const transition=_profileTransitionReasoningContext;
  const session=S&&S.session;
  if(transition&&(!session||session.profile!==transition.profile)){
    const ctx={};
    if(transition.model) ctx.model=transition.model;
    if(transition.provider) ctx.provider=transition.provider;
    return ctx;
  }
  const sel=$('modelSelect');
  const model=(S&&S.session&&S.session.model)||(sel&&sel.value)||'';
  let provider=(S&&S.session&&S.session.model_provider)||'';
  if(!provider&&sel&&model&&typeof _modelStateForSelect==='function'){
    provider=_modelStateForSelect(sel, model).model_provider||'';
  }
  const ctx={};
  if(model) ctx.model=model;
  if(provider) ctx.provider=provider;
  return ctx;
}

function _reasoningEffortQuery(){
  const params=new URLSearchParams(_reasoningEffortContext());
  const qs=params.toString();
  return qs?('?'+qs):'';
}

function _applyReasoningOptions(supportedEfforts){
  const dd=$('composerReasoningDropdown');
  if(!dd) return;
  const supported=new Set(Array.isArray(supportedEfforts)?supportedEfforts:[]);
  dd.querySelectorAll('.reasoning-option').forEach(function(opt){
    const effort=opt.dataset.effort;
    // 'none' (turn thinking off) and '' (Default = clear override, provider
    // default = thinking on) are meta-options outside the effort ladder. They
    // are always shown so a thinking-toggle-only model (GLM-4.5–5.1 on native
    // zai, where the ladder is empty) still has an operable two-state control:
    // Default (on) + None (off). Without the Default option the toggle is
    // one-way off-only — the user can disable thinking but cannot re-enable it.
    // (#6219 round-3)
    if(effort==='none'||effort===''){
      opt.style.display='';
      return;
    }
    if(!supported.size){
      opt.style.display='none';
      return;
    }
    opt.style.display=supported.has(effort)?'':'none';
  });
}

function _applyReasoningChip(eff){
  const meta=arguments[1]||null;
  const effort=_normalizeReasoningEffort(eff);
  _currentReasoningEffort=effort;
  if(meta&&Array.isArray(meta.supported_efforts)){
    _currentReasoningEffortsSupported=meta.supported_efforts;
  }
  // supports_thinking_toggle: the model accepts the thinking on/off toggle even
  // when the effort ladder is empty (GLM-4.5–5.1 on native zai accept
  // `thinking: {"type": ...}` but NOT `reasoning_effort`). Without honoring this
  // flag, returning an empty supported_efforts hides the entire chip and
  // silently regresses the working thinking on/off control for those models.
  // Default true preserves prior behavior when the field is absent.
  if(meta&&typeof meta.supports_thinking_toggle==='boolean'){
    _currentReasoningToggleSupported=meta.supports_thinking_toggle;
  }
  const wrap=$('composerReasoningWrap');
  const label=$('composerReasoningLabel');
  const chip=$('composerReasoningChip');
  const mobileLabel=$('composerMobileReasoningLabel');
  const mobileAction=$('composerMobileReasoningAction');
  if(!wrap||!label) return;
  const supportedEfforts=(typeof _currentReasoningEffortsSupported==='undefined')
    ?null
    :_currentReasoningEffortsSupported;
  const toggleSupported=(typeof _currentReasoningToggleSupported==='undefined')
    ?true
    :_currentReasoningToggleSupported;
  const hasEffortLadder=Array.isArray(supportedEfforts)
    ?supportedEfforts.length>0
    :true;
  // Show the chip if there is an effort ladder OR a thinking toggle is still
  // available. Only hide when the model supports neither.
  const supports=hasEffortLadder||toggleSupported;
  if(!supports){
    wrap.style.display='none';
    if(mobileAction) mobileAction.style.display='none';
    return;
  }
  wrap.style.display='';
  if(mobileAction) mobileAction.style.display='';
  if(typeof _applyReasoningOptions==='function') _applyReasoningOptions(supportedEfforts);
  const text=_formatReasoningEffortLabel(effort);
  label.textContent=text;
  if(mobileLabel) mobileLabel.textContent=text;
  if(chip){
    const inactive=!effort||effort==='none';
    chip.classList.toggle('inactive',inactive);
    const labelText='Reasoning effort: '+text;
    chip.title=labelText;
    chip.setAttribute('aria-label',labelText);
  }
  if(mobileAction) mobileAction.classList.toggle('inactive',!effort||effort==='none');
  _highlightReasoningOption(effort);
}

// Tracks the model/provider identity of the last reasoning fetch so routine
// topbar syncs can serve the cached chip state instead of re-hitting the
// network. null = never fetched.
let _lastReasoningFetchKey=null;
// Monotonic dispatch counter. Each fetchReasoningChip() increments it and the
// async handlers capture their own value; a response (success OR failure) only
// applies if it is still the most recent dispatch. This defeats out-of-order
// resolution even when two fetches share the same model/provider key (e.g. a
// profile switch that resets the cache and refetches the same default model but
// a different agent.reasoning_effort) — #4650 review.
let _reasoningFetchSeq=0;

function fetchReasoningChip(keyOverride){
  // Set the cache key OPTIMISTICALLY before the request so rapid routine syncs
  // while this GET is in flight short-circuit instead of re-dispatching (that
  // in-flight window is exactly where the #4650 storm lived).
  const key=keyOverride===undefined?_reasoningEffortQuery():keyOverride;
  const seq=++_reasoningFetchSeq;
  _lastReasoningFetchKey=key;
  api('/api/reasoning'+key).then(function(st){
    // Ignore a stale/superseded response: only the most recent dispatch may
    // apply, so an older in-flight GET can't poison the current chip (#4650).
    if(seq!==_reasoningFetchSeq) return;
    _applyReasoningChip((st&&st.reasoning_effort)||'', st||{});
  }).catch(function(){
    // Same staleness guard on failure: a stale error must neither hide the chip
    // nor clear a newer fetch's key. Only the latest dispatch clears the key so
    // routine syncs retry after a genuine transient failure.
    if(seq!==_reasoningFetchSeq) return;
    _lastReasoningFetchKey=null;
    _applyReasoningChip('', {supported_efforts:[], supports_thinking_toggle:false});
  });
}

function refreshProfileTransitionReasoningChip(model, provider){
  _profileTransitionReasoningContext={profile:(S&&S.activeProfile)||'default',model,provider};
  _currentReasoningEffort=null;
  _currentReasoningEffortsSupported=null;
  _currentReasoningToggleSupported=undefined;
  _lastReasoningFetchKey=null;
  ++_reasoningFetchSeq;
  _applyReasoningChip('', {supported_efforts:[], supports_thinking_toggle:false});
  const params=new URLSearchParams();
  if(model) params.set('model',model);
  if(provider) params.set('provider',provider);
  fetchReasoningChip(params.size?'?'+params.toString():undefined);
}

function clearProfileTransitionReasoningContext(){
  _profileTransitionReasoningContext=null;
}

function syncReasoningChip(){
  // #4650: syncTopbar() calls this on every routine UI refresh, and during
  // streaming those fire at high frequency. Before a9ce2889 this served the
  // cached _currentReasoningEffort after the first load; that commit made it
  // refetch unconditionally to refresh supported-efforts after a model switch,
  // which turned ordinary syncs into a GET /api/reasoning storm (one per token).
  // Restore the cache short-circuit but keep a9ce2889's intent: only hit the
  // network when nothing is cached yet OR the model/provider identity changed
  // since the last fetch (the only inputs that change /api/reasoning's answer).
  // The user-pick and model-switch paths still update the cache directly.
  const key=_reasoningEffortQuery();
  // Short-circuit on the KEY alone: if a fetch for this exact model/provider has
  // already been dispatched (in-flight) or completed, do not dispatch another —
  // this is what stops the #4650 storm, including the COLD-cache window where
  // _currentReasoningEffort is still null between the first dispatch and its
  // response (10 syncs before the first GET resolves must produce ONE request,
  // not ten). Apply the cached chip only once we actually have an effort value.
  if(_lastReasoningFetchKey===key){
    if(_currentReasoningEffort!==null) _applyReasoningChip(_currentReasoningEffort);
    return;
  }
  fetchReasoningChip();
}

function _highlightReasoningOption(effort){
  const dd=$('composerReasoningDropdown');
  if(!dd) return;
  dd.querySelectorAll('.reasoning-option').forEach(function(opt){
    opt.classList.toggle('selected',opt.dataset.effort===effort);
  });
}

function toggleReasoningDropdown(){
  const dd=$('composerReasoningDropdown');
  const chip=$('composerReasoningChip');
  if(!dd||!chip) return;
  const open=dd.classList.contains('open');
  if(open){closeReasoningDropdown();return;}
  if(typeof closeProfileDropdown==='function') closeProfileDropdown();
  if(typeof closeWsDropdown==='function') closeWsDropdown();
  closeModelDropdown();
  if(typeof closeToolsetsDropdown==='function') closeToolsetsDropdown();
  _highlightReasoningOption(_currentReasoningEffort);
  dd.classList.add('open');
  _positionReasoningDropdown();
  chip.classList.add('active');
  const mobileAction=$('composerMobileReasoningAction');
  if(mobileAction) mobileAction.classList.add('active');
}

function _positionReasoningDropdown(){
  const dd=$('composerReasoningDropdown');
  const chip=$('composerReasoningChip');
  const mobileAction=$('composerMobileReasoningAction');
  const footer=document.querySelector('.composer-footer');
  if(!dd||!chip||!footer) return;
  const panel=$('composerMobileConfigPanel');
  const anchor=(panel&&panel.classList.contains('open')&&mobileAction)?mobileAction:chip;
  const chipRect=anchor.getBoundingClientRect();
  const footerRect=footer.getBoundingClientRect();
  let left=chipRect.left-footerRect.left;
  const maxLeft=Math.max(0,footer.clientWidth-dd.offsetWidth);
  left=Math.max(0,Math.min(left,maxLeft));
  dd.style.left=`${left}px`;
}

function closeReasoningDropdown(){
  const dd=$('composerReasoningDropdown');
  const chip=$('composerReasoningChip');
  const mobileAction=$('composerMobileReasoningAction');
  if(dd) dd.classList.remove('open');
  if(chip) chip.classList.remove('active');
  if(mobileAction) mobileAction.classList.remove('active');
}

document.addEventListener('click',function(e){
  if(
    !e.target.closest('#composerReasoningChip') &&
    !e.target.closest('#composerMobileReasoningAction') &&
    !e.target.closest('#composerReasoningDropdown')
  ) closeReasoningDropdown();
  if(e.target.closest('.reasoning-option')){
    const opt=e.target.closest('.reasoning-option');
    const effort=opt&&opt.dataset.effort;
    // NOTE: effort may be the empty string for the "Default" option (clears
    // the override). Check option presence, not truthiness — `if(effort)` would
    // silently ignore the Default click and leave the toggle one-way off-only.
    // (#6219 round-3)
    if(opt){
      const payload=Object.assign({effort:effort},_reasoningEffortContext());
      api('/api/reasoning',{method:'POST',body:JSON.stringify(payload)})
        .then(function(st){
          // For Default (effort=''), the returned reasoning_effort is '' (clear)
          // — display 'Default' rather than an empty toast.
          const display=(st&&st.reasoning_effort)||effort||'Default';
          _applyReasoningChip((st&&st.reasoning_effort)||effort, st||{});
          showToast('🧠 Reasoning effort set to '+display);
        })
        .catch(function(){showToast('🧠 Failed to set effort');});
      closeReasoningDropdown();
    }
  }
});

// ── Session toolsets chip (#493) ───────────────────────────────────────────
let _currentSessionToolsets = null; // null = active profile defaults, array = custom list
let _toolsetsCatalog = null;

function _applyToolsetsChip(toolsets) {
  _currentSessionToolsets = toolsets;
  const wrap = $('composerToolsetsWrap');
  const label = $('composerToolsetsLabel');
  const chip = $('composerToolsetsChip');
  if (!wrap || !label) return;
  // Visibility is controlled entirely by responsive CSS — the chip shows only
  // at wide composer-footer widths (>= 1100px container query). At narrower
  // widths the layout is too cramped (model + reasoning + profile + workspace
  // + context-ring + send) to add another chip. Cleared inline style so the
  // CSS @container query is the single source of truth. State is still
  // tracked so /api/session/toolsets continues to work for cron/scripted
  // callers regardless of UI visibility. (#1431)
  wrap.style.display = '';
  const hasCustom = Array.isArray(toolsets) && toolsets.length > 0;
  const isStaged = hasCustom
    && typeof S !== 'undefined'
    && S
    && !S.session
    && Array.isArray(S._pendingSessionToolsets);
  if (hasCustom) {
    const stagedSuffix = isStaged ? ' (staged)' : '';
    label.textContent = toolsets.join(', ') + stagedSuffix;
    chip.classList.add('has-custom');
    chip.title = t('session_toolsets') + ': ' + toolsets.join(', ') + stagedSuffix;
  } else {
    label.textContent = t('session_toolsets_profile_defaults');
    chip.classList.remove('has-custom');
    chip.title = t('session_toolsets') + ': ' + t('session_toolsets_profile_defaults');
  }
}

function _syncToolsetsChip() {
  if (typeof S === 'undefined' || !S || !S.session) {
    const stagedToolsets = (typeof S !== 'undefined' && S && Array.isArray(S._pendingSessionToolsets))
      ? S._pendingSessionToolsets
      : null;
    _applyToolsetsChip(stagedToolsets);
    return;
  }
  _applyToolsetsChip(S.session.enabled_toolsets || null);
}

function syncToolsetsChip() {
  _syncToolsetsChip();
}

function _normalizeToolsetsCatalog(payload) {
  const servers = payload && Array.isArray(payload.servers) ? payload.servers : [];
  const seen = new Set();
  const names = [];
  servers.forEach(function(server) {
    const name = String((server && server.name) || '').trim();
    if (!name || seen.has(name)) return;
    seen.add(name);
    names.push(name);
  });
  return names;
}

function _loadToolsetsCatalog() {
  if (Array.isArray(_toolsetsCatalog)) return Promise.resolve(_toolsetsCatalog);
  return api('/api/mcp/servers')
    .then(function(payload) {
      _toolsetsCatalog = _normalizeToolsetsCatalog(payload);
      return _toolsetsCatalog;
    })
    .catch(function() {
      _toolsetsCatalog = false;
      return [];
    });
}

function invalidateToolsetsCatalog(payload) {
  _toolsetsCatalog = payload && Array.isArray(payload.servers) ? _normalizeToolsetsCatalog(payload) : null;
}
if (typeof window !== 'undefined') window.invalidateToolsetsCatalog = invalidateToolsetsCatalog;

function _toolsetsInputList(input) {
  if (!input) return [];
  return input.value.split(',').map(s => s.trim()).filter(Boolean);
}

function _ensureToolsetsPresetSection() {
  const dd = $('composerToolsetsDropdown');
  if (!dd) return null;
  let section = $('toolsetsPresetSections');
  if (section) return section;
  section = document.createElement('div');
  section.id = 'toolsetsPresetSections';
  section.className = 'toolsets-preset-sections';
  const inputRow = dd.querySelector('.toolsets-dropdown-input-row');
  if (inputRow) dd.insertBefore(section, inputRow);
  else dd.appendChild(section);
  return section;
}

function _appendToolsetsLabel(section, text) {
  const label = document.createElement('div');
  label.className = 'toolsets-dropdown-desc';
  label.textContent = text;
  section.appendChild(label);
}

function _renderToolsetsPresetSections(opts) {
  const state = opts && opts.state;
  const input = opts && opts.input;
  const section = _ensureToolsetsPresetSection();
  if (!section || !state || !input) return;
  const selected = _toolsetsInputList(input);
  const selectedSet = new Set(selected);
  const hasCustom = selected.length > 0;
  state.textContent = hasCustom
    ? '🔧 ' + selected.join(', ')
    : '👤 ' + t('session_toolsets_profile_defaults');

  section.innerHTML = '';
  const defaultsBtn = document.createElement('button');
  defaultsBtn.type = 'button';
  defaultsBtn.id = 'toolsetsProfileDefaultsBtn';
  defaultsBtn.className = 'toolsets-action-btn toolsets-clear-btn';
  defaultsBtn.textContent = t('session_toolsets_use_profile_defaults');
  section.appendChild(defaultsBtn);

  _appendToolsetsLabel(section, t('session_toolsets_configured_servers'));
  if (_toolsetsCatalog === null) {
    _appendToolsetsLabel(section, t('session_toolsets_loading_servers'));
    return;
  }
  if (_toolsetsCatalog === false) {
    _appendToolsetsLabel(section, t('mcp_load_failed'));
    return;
  }
  if (!Array.isArray(_toolsetsCatalog) || !_toolsetsCatalog.length) {
    _appendToolsetsLabel(section, t('session_toolsets_no_configured_servers'));
    return;
  }
  _toolsetsCatalog.forEach(function(name) {
    const row = document.createElement('label');
    row.className = 'toolsets-server-option';
    row.style.display = 'flex';
    row.style.alignItems = 'center';
    row.style.gap = '6px';
    row.style.margin = '4px 0';
    row.style.fontSize = '12px';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.className = 'toolsets-server-checkbox';
    checkbox.value = name;
    checkbox.checked = selectedSet.has(name);
    row.appendChild(checkbox);
    row.appendChild(document.createTextNode(name));
    section.appendChild(row);
  });
}

function _populateToolsetsDropdown() {
  const desc = $('toolsetsDropdownDesc');
  const state = $('toolsetsDropdownState');
  const input = $('toolsetsInput');
  const applyBtn = $('toolsetsApplyBtn');
  const clearBtn = $('toolsetsClearBtn');
  if (!desc || !state || !input) return;
  desc.textContent = t('session_toolsets_desc');
  if (applyBtn) applyBtn.textContent = t('session_toolsets_apply');
  if (clearBtn) clearBtn.textContent = t('session_toolsets_clear');
  input.placeholder = t('session_toolsets_placeholder');
  // Escape key handler for toolsets input
  input.onkeydown = function(e) { if(e.key === 'Escape') closeToolsetsDropdown(); };
  input.oninput = function() { _renderToolsetsPresetSections({ state, input }); };
  const hasCustom = Array.isArray(_currentSessionToolsets) && _currentSessionToolsets.length > 0;
  if (hasCustom) {
    input.value = _currentSessionToolsets.join(', ');
  } else {
    input.value = '';
  }
  _renderToolsetsPresetSections({ state, input });
}

function _positionToolsetsDropdown() {
  const dd = $('composerToolsetsDropdown');
  const chip = $('composerToolsetsChip');
  const footer = document.querySelector('.composer-footer');
  if (!dd || !chip || !footer) return;
  // Defense: if the chip has been hidden by responsive CSS (e.g. resize across
  // 1100px container threshold while dropdown was open), don't try to anchor
  // to a zero-rect element — close the dropdown instead. (#1431)
  if (chip.offsetParent === null) { closeToolsetsDropdown(); return; }
  const chipRect = chip.getBoundingClientRect();
  const footerRect = footer.getBoundingClientRect();
  let left = chipRect.left - footerRect.left;
  const maxLeft = Math.max(0, footer.clientWidth - dd.offsetWidth);
  left = Math.max(0, Math.min(left, maxLeft));
  dd.style.left = left + 'px';
}

function toggleToolsetsDropdown() {
  const dd = $('composerToolsetsDropdown');
  const chip = $('composerToolsetsChip');
  if (!dd || !chip) return;
  // Don't open when the chip itself is hidden by responsive CSS (#1431).
  // offsetParent === null catches display:none on the element or any ancestor.
  if (chip.offsetParent === null) return;
  const open = dd.classList.contains('open');
  if (open) { closeToolsetsDropdown(); return; }
  if (typeof closeProfileDropdown === 'function') closeProfileDropdown();
  if (typeof closeWsDropdown === 'function') closeWsDropdown();
  closeModelDropdown();
  if (typeof closeReasoningDropdown === 'function') closeReasoningDropdown();
  _syncToolsetsChip();
  _populateToolsetsDropdown();
  _loadToolsetsCatalog().then(function() {
    const stillOpen = dd && dd.classList.contains('open');
    if (stillOpen) {
      const state = $('toolsetsDropdownState');
      const input = $('toolsetsInput');
      _renderToolsetsPresetSections({ state, input });
    }
  });
  dd.classList.add('open');
  _positionToolsetsDropdown();
  chip.classList.add('active');
  // Focus the input after a tick so the layout has settled
  setTimeout(() => { const inp = $('toolsetsInput'); if (inp) inp.focus(); }, 50);
}

function closeToolsetsDropdown() {
  const dd = $('composerToolsetsDropdown');
  const chip = $('composerToolsetsChip');
  if (dd) dd.classList.remove('open');
  if (chip) chip.classList.remove('active');
}

function _applySessionToolsets(toolsets) {
  if (typeof S === 'undefined' || !S) return;
  if (!S.session) {
    S._pendingSessionToolsets = toolsets;
    _applyToolsetsChip(toolsets);
    if (Array.isArray(toolsets) && toolsets.length) {
      showToast('🔧 ' + t('session_toolsets_applied') + ': ' + toolsets.join(', '));
    } else {
      showToast('🌍 ' + t('session_toolsets_cleared'));
    }
    return;
  }
  const sid = S.session.session_id;
  api('/api/session/toolsets', {
    method: 'POST',
    body: JSON.stringify({ session_id: sid, toolsets: toolsets })
  })
    .then(function(r) {
      if (r && r.ok) {
        S.session.enabled_toolsets = r.enabled_toolsets || null;
        _applyToolsetsChip(r.enabled_toolsets || null);
        if (r.enabled_toolsets && r.enabled_toolsets.length) {
          showToast('🔧 ' + t('session_toolsets_applied') + ': ' + r.enabled_toolsets.join(', '));
        } else {
          showToast('🌍 ' + t('session_toolsets_cleared'));
        }
      } else {
        showToast(t('session_toolsets_failed') + (r && r.error ? r.error : 'Unknown error'), 3000, 'error');
      }
    })
    .catch(function(err) {
      showToast(t('session_toolsets_failed') + (err.message || err), 3000, 'error');
    });
}

// Click-outside handler for toolsets dropdown
document.addEventListener('click', function(e) {
  if (
    !e.target.closest('#composerToolsetsChip') &&
    !e.target.closest('#composerToolsetsDropdown')
  ) closeToolsetsDropdown();
  // Active profile defaults button
  if (e.target.closest('#toolsetsProfileDefaultsBtn')) {
    _applySessionToolsets(null);
    closeToolsetsDropdown();
    return;
  }
  // Apply button
  if (e.target.closest('#toolsetsApplyBtn')) {
    const input = $('toolsetsInput');
    if (!input) return;
    const raw = input.value.trim();
    if (!raw) {
      showToast(t('session_toolsets_desc'), 2000);
      return;
    }
    const toolsets = raw.split(',').map(s => s.trim()).filter(Boolean);
    if (toolsets.length === 0) {
      showToast(t('session_toolsets_desc'), 2000);
      return;
    }
    _applySessionToolsets(toolsets);
    closeToolsetsDropdown();
  }
  // Clear button
  if (e.target.closest('#toolsetsClearBtn')) {
    _applySessionToolsets(null);
    closeToolsetsDropdown();
  }
});

document.addEventListener('change', function(e) {
  if (!e.target.closest('#toolsetsPresetSections')) return;
  if (!e.target.classList.contains('toolsets-server-checkbox')) return;
  const input = $('toolsetsInput');
  const state = $('toolsetsDropdownState');
  if (!input) return;
  const checked = Array.from(document.querySelectorAll('#toolsetsPresetSections .toolsets-server-checkbox:checked'))
    .map(el => String(el.value || '').trim())
    .filter(Boolean);
  const catalogSet = new Set(Array.isArray(_toolsetsCatalog) ? _toolsetsCatalog : []);
  const manual = _toolsetsInputList(input).filter(name => !catalogSet.has(name));
  input.value = checked.concat(manual).join(', ');
  _renderToolsetsPresetSections({ state, input });
});

// Position toolsets dropdown on resize, OR close it if the chip is no longer
// visible (e.g. resize crossed the 1100px container threshold while dropdown
// was open — the wrap is hidden by CSS but the dropdown sibling stays open
// without an anchor). (#1431)
window.addEventListener('resize', () => {
  const dd = $('composerToolsetsDropdown');
  if (!dd || !dd.classList.contains('open')) return;
  const chip = $('composerToolsetsChip');
  if (!chip || chip.offsetParent === null) { closeToolsetsDropdown(); return; }
  _positionToolsetsDropdown();
});

function _syncMobileComposerConfigButton(open){
  const btn=$('composerMobileConfigBtn');
  if(!btn) return;
  btn.classList.toggle('active',!!open);
  btn.setAttribute('aria-expanded',open?'true':'false');
}

function closeMobileComposerConfig(){
  const panel=$('composerMobileConfigPanel');
  if(panel) panel.classList.remove('open');
  _syncMobileComposerConfigButton(false);
  if(typeof closeWsDropdown==='function') closeWsDropdown();
}

function openMobileComposerConfig(){
  const panel=$('composerMobileConfigPanel');
  if(!panel) return;
  if(typeof closeProfileDropdown==='function') closeProfileDropdown();
  if(typeof closeWsDropdown==='function') closeWsDropdown();
  closeModelDropdown();
  closeReasoningDropdown();
  if(typeof closeToolsetsDropdown==='function') closeToolsetsDropdown();
  panel.classList.add('open');
  _syncMobileComposerConfigButton(true);
}

function toggleMobileComposerConfig(){
  const panel=$('composerMobileConfigPanel');
  if(!panel) return;
  const open=panel.classList.contains('open');
  if(open){
    closeMobileComposerConfig();
    closeModelDropdown();
    closeReasoningDropdown();
    if(typeof closeToolsetsDropdown==='function') closeToolsetsDropdown();
    return;
  }
  openMobileComposerConfig();
}

function openComposerContextMenu(e){
  if(e){
    e.preventDefault();
    e.stopPropagation();
  }
  const tooltip=$('ctxTooltip');
  if(tooltip){
    tooltip.classList.remove('ctx-tooltip-active');
    tooltip.setAttribute('aria-hidden','true');
  }
  openMobileComposerConfig();
}
window.openComposerContextMenu=openComposerContextMenu;

document.addEventListener('click',function(e){
  if(
    e.target.closest('#composerMobileConfigBtn') ||
    e.target.closest('#composerMobileConfigPanel') ||
    e.target.closest('#composerWsDropdown') ||
    e.target.closest('#composerModelDropdown') ||
    e.target.closest('#composerReasoningDropdown')
  ) return;
  closeMobileComposerConfig();
});

document.addEventListener('keydown',function(e){
  if(e.key!=='Escape') return;
  const panel=$('composerMobileConfigPanel');
  if(!panel||!panel.classList.contains('open')) return;
  e.preventDefault();
  closeMobileComposerConfig();
  if(typeof closeWsDropdown==='function') closeWsDropdown();
  closeModelDropdown();
  closeReasoningDropdown();
});

window.addEventListener('resize',function(){
  if(window.matchMedia && !window.matchMedia('(max-width: 640px)').matches){
    closeMobileComposerConfig();
    closeModelDropdown();
    closeReasoningDropdown();
    if(typeof closeWsDropdown==='function') closeWsDropdown();
  }
});


/* ── Global Window Exports ─────────────────────────────────────────────────── */
if (typeof window !== 'undefined') {
  window._providerQuotaRefreshInFlight = typeof _providerQuotaRefreshInFlight !== 'undefined' ? _providerQuotaRefreshInFlight : undefined;
  window._formatQuotaMoneyShort = typeof _formatQuotaMoneyShort !== 'undefined' ? _formatQuotaMoneyShort : undefined;
  window._formatQuotaPercentShort = typeof _formatQuotaPercentShort !== 'undefined' ? _formatQuotaPercentShort : undefined;
  window._providerQuotaIndicatorText = typeof _providerQuotaIndicatorText !== 'undefined' ? _providerQuotaIndicatorText : undefined;
  window.renderProviderQuotaIndicator = typeof renderProviderQuotaIndicator !== 'undefined' ? renderProviderQuotaIndicator : undefined;
  window.refreshProviderQuotaIndicator = typeof refreshProviderQuotaIndicator !== 'undefined' ? refreshProviderQuotaIndicator : undefined;
  window._dynamicModelLabels = typeof _dynamicModelLabels !== 'undefined' ? _dynamicModelLabels : undefined;
  window.MODEL_STATE_KEY = typeof MODEL_STATE_KEY !== 'undefined' ? MODEL_STATE_KEY : undefined;
  window.PENDING_SESSION_MODEL_PREFIX = typeof PENDING_SESSION_MODEL_PREFIX !== 'undefined' ? PENDING_SESSION_MODEL_PREFIX : undefined;
  window.PENDING_SESSION_MODEL_MAX_AGE_MS = typeof PENDING_SESSION_MODEL_MAX_AGE_MS !== 'undefined' ? PENDING_SESSION_MODEL_MAX_AGE_MS : undefined;
  window._getOptionProviderId = typeof _getOptionProviderId !== 'undefined' ? _getOptionProviderId : undefined;
  window._providerFromModelValue = typeof _providerFromModelValue !== 'undefined' ? _providerFromModelValue : undefined;
  window._modelPickerOptionIdentity = typeof _modelPickerOptionIdentity !== 'undefined' ? _modelPickerOptionIdentity : undefined;
  window._deduplicateModelPickerOptions = typeof _deduplicateModelPickerOptions !== 'undefined' ? _deduplicateModelPickerOptions : undefined;
  window._providerSkipsModelMismatchWarning = typeof _providerSkipsModelMismatchWarning !== 'undefined' ? _providerSkipsModelMismatchWarning : undefined;
  window._providerDefersMissingModelFallback = typeof _providerDefersMissingModelFallback !== 'undefined' ? _providerDefersMissingModelFallback : undefined;
  window._modelStateForSelect = typeof _modelStateForSelect !== 'undefined' ? _modelStateForSelect : undefined;
  window._captureModelDropdownSelection = typeof _captureModelDropdownSelection !== 'undefined' ? _captureModelDropdownSelection : undefined;
  window._modelProviderForSend = typeof _modelProviderForSend !== 'undefined' ? _modelProviderForSend : undefined;
  window._reconcileModelDropdownSelection = typeof _reconcileModelDropdownSelection !== 'undefined' ? _reconcileModelDropdownSelection : undefined;
  window._providerQualifiedModelValueForSelect = typeof _providerQualifiedModelValueForSelect !== 'undefined' ? _providerQualifiedModelValueForSelect : undefined;
  window._readPersistedModelState = typeof _readPersistedModelState !== 'undefined' ? _readPersistedModelState : undefined;
  window._writePersistedModelState = typeof _writePersistedModelState !== 'undefined' ? _writePersistedModelState : undefined;
  window._clearPersistedModelState = typeof _clearPersistedModelState !== 'undefined' ? _clearPersistedModelState : undefined;
  window._pendingSessionModelKey = typeof _pendingSessionModelKey !== 'undefined' ? _pendingSessionModelKey : undefined;
  window._rememberPendingSessionModel = typeof _rememberPendingSessionModel !== 'undefined' ? _rememberPendingSessionModel : undefined;
  window._readPendingSessionModel = typeof _readPendingSessionModel !== 'undefined' ? _readPendingSessionModel : undefined;
  window._clearPendingSessionModel = typeof _clearPendingSessionModel !== 'undefined' ? _clearPendingSessionModel : undefined;
  window._deliberateSessionModelPick = typeof _deliberateSessionModelPick !== 'undefined' ? _deliberateSessionModelPick : undefined;
  window._reArmRecoveryPick = typeof _reArmRecoveryPick !== 'undefined' ? _reArmRecoveryPick : undefined;
  window._applyPendingSessionModelForSession = typeof _applyPendingSessionModelForSession !== 'undefined' ? _applyPendingSessionModelForSession : undefined;
  window._findModelInDropdown = typeof _findModelInDropdown !== 'undefined' ? _findModelInDropdown : undefined;
  window._refreshOpenModelDropdown = typeof _refreshOpenModelDropdown !== 'undefined' ? _refreshOpenModelDropdown : undefined;
  window._applyModelToDropdown = typeof _applyModelToDropdown !== 'undefined' ? _applyModelToDropdown : undefined;
  window._ensureModelOptionInDropdown = typeof _ensureModelOptionInDropdown !== 'undefined' ? _ensureModelOptionInDropdown : undefined;
  window._modelStateFromAppliedDropdown = typeof _modelStateFromAppliedDropdown !== 'undefined' ? _modelStateFromAppliedDropdown : undefined;
  window._persistSessionModelCorrection = typeof _persistSessionModelCorrection !== 'undefined' ? _persistSessionModelCorrection : undefined;
  window._modelDropdownRequestSeq = typeof _modelDropdownRequestSeq !== 'undefined' ? _modelDropdownRequestSeq : undefined;
  window._modelCatalogFallbackRetried = typeof _modelCatalogFallbackRetried !== 'undefined' ? _modelCatalogFallbackRetried : undefined;
  window._applySessionModelFallback = typeof _applySessionModelFallback !== 'undefined' ? _applySessionModelFallback : undefined;
  window.populateModelDropdown = typeof populateModelDropdown !== 'undefined' ? populateModelDropdown : undefined;
  window._liveModelCache = typeof _liveModelCache !== 'undefined' ? _liveModelCache : undefined;
  window._liveModelFetchPending = typeof _liveModelFetchPending !== 'undefined' ? _liveModelFetchPending : undefined;
  window._addLiveModelsToSelect = typeof _addLiveModelsToSelect !== 'undefined' ? _addLiveModelsToSelect : undefined;
  window._fetchLiveModels = typeof _fetchLiveModels !== 'undefined' ? _fetchLiveModels : undefined;
  window._checkProviderMismatch = typeof _checkProviderMismatch !== 'undefined' ? _checkProviderMismatch : undefined;
  window._selectedModelOption = typeof _selectedModelOption !== 'undefined' ? _selectedModelOption : undefined;
  window._normalizeConfiguredModelKey = typeof _normalizeConfiguredModelKey !== 'undefined' ? _normalizeConfiguredModelKey : undefined;
  window._isEquivalentConfiguredModelEntry = typeof _isEquivalentConfiguredModelEntry !== 'undefined' ? _isEquivalentConfiguredModelEntry : undefined;
  window._getConfiguredModelBadge = typeof _getConfiguredModelBadge !== 'undefined' ? _getConfiguredModelBadge : undefined;
  window._compactComposerModelChipLabel = typeof _compactComposerModelChipLabel !== 'undefined' ? _compactComposerModelChipLabel : undefined;
  window.syncModelChip = typeof syncModelChip !== 'undefined' ? syncModelChip : undefined;
  window._modelDropdownHome = typeof _modelDropdownHome !== 'undefined' ? _modelDropdownHome : undefined;
  window._restoreModelDropdownHome = typeof _restoreModelDropdownHome !== 'undefined' ? _restoreModelDropdownHome : undefined;
  window._positionModelDropdown = typeof _positionModelDropdown !== 'undefined' ? _positionModelDropdown : undefined;
  window._readModelOverflowData = typeof _readModelOverflowData !== 'undefined' ? _readModelOverflowData : undefined;
  window._appendOverflowOptionsToGroup = typeof _appendOverflowOptionsToGroup !== 'undefined' ? _appendOverflowOptionsToGroup : undefined;
  window._mountSearchableModelSelect = typeof _mountSearchableModelSelect !== 'undefined' ? _mountSearchableModelSelect : undefined;
  window.renderModelDropdown = typeof renderModelDropdown !== 'undefined' ? renderModelDropdown : undefined;
  window.selectModelFromDropdown = typeof selectModelFromDropdown !== 'undefined' ? selectModelFromDropdown : undefined;
  window.toggleModelDropdown = typeof toggleModelDropdown !== 'undefined' ? toggleModelDropdown : undefined;
  window.closeModelDropdown = typeof closeModelDropdown !== 'undefined' ? closeModelDropdown : undefined;
  window.closeSettingsModelDropdown = typeof closeSettingsModelDropdown !== 'undefined' ? closeSettingsModelDropdown : undefined;
  window.syncSettingsModelChip = typeof syncSettingsModelChip !== 'undefined' ? syncSettingsModelChip : undefined;
  window.selectSettingsModelFromDropdown = typeof selectSettingsModelFromDropdown !== 'undefined' ? selectSettingsModelFromDropdown : undefined;
  window.openSettingsModelDropdown = typeof openSettingsModelDropdown !== 'undefined' ? openSettingsModelDropdown : undefined;
  window.toggleSettingsModelDropdown = typeof toggleSettingsModelDropdown !== 'undefined' ? toggleSettingsModelDropdown : undefined;
  window.mountSettingsModelPicker = typeof mountSettingsModelPicker !== 'undefined' ? mountSettingsModelPicker : undefined;
  window._modelDropdownRepositionScheduled = typeof _modelDropdownRepositionScheduled !== 'undefined' ? _modelDropdownRepositionScheduled : undefined;
  window._repositionOpenModelDropdown = typeof _repositionOpenModelDropdown !== 'undefined' ? _repositionOpenModelDropdown : undefined;
  window._composerFitScheduled = typeof _composerFitScheduled !== 'undefined' ? _composerFitScheduled : undefined;
  window._composerFitResizeObserver = typeof _composerFitResizeObserver !== 'undefined' ? _composerFitResizeObserver : undefined;
  window._composerFitMutationObserver = typeof _composerFitMutationObserver !== 'undefined' ? _composerFitMutationObserver : undefined;
  window._composerFitObservedFooter = typeof _composerFitObservedFooter !== 'undefined' ? _composerFitObservedFooter : undefined;
  window._composerFitResizeListenerBound = typeof _composerFitResizeListenerBound !== 'undefined' ? _composerFitResizeListenerBound : undefined;
  window._fitComposerFooter = typeof _fitComposerFooter !== 'undefined' ? _fitComposerFooter : undefined;
  window._scheduleComposerFit = typeof _scheduleComposerFit !== 'undefined' ? _scheduleComposerFit : undefined;
  window._initComposerFooterFit = typeof _initComposerFooterFit !== 'undefined' ? _initComposerFooterFit : undefined;
  window._currentReasoningEffort = typeof _currentReasoningEffort !== 'undefined' ? _currentReasoningEffort : undefined;
  window._currentReasoningEffortsSupported = typeof _currentReasoningEffortsSupported !== 'undefined' ? _currentReasoningEffortsSupported : undefined;
  window._currentReasoningToggleSupported = typeof _currentReasoningToggleSupported !== 'undefined' ? _currentReasoningToggleSupported : undefined;
  window._profileTransitionReasoningContext = typeof _profileTransitionReasoningContext !== 'undefined' ? _profileTransitionReasoningContext : undefined;
  window._normalizeReasoningEffort = typeof _normalizeReasoningEffort !== 'undefined' ? _normalizeReasoningEffort : undefined;
  window._formatReasoningEffortLabel = typeof _formatReasoningEffortLabel !== 'undefined' ? _formatReasoningEffortLabel : undefined;
  window._reasoningEffortContext = typeof _reasoningEffortContext !== 'undefined' ? _reasoningEffortContext : undefined;
  window._reasoningEffortQuery = typeof _reasoningEffortQuery !== 'undefined' ? _reasoningEffortQuery : undefined;
  window._applyReasoningOptions = typeof _applyReasoningOptions !== 'undefined' ? _applyReasoningOptions : undefined;
  window._applyReasoningChip = typeof _applyReasoningChip !== 'undefined' ? _applyReasoningChip : undefined;
  window._lastReasoningFetchKey = typeof _lastReasoningFetchKey !== 'undefined' ? _lastReasoningFetchKey : undefined;
  window._reasoningFetchSeq = typeof _reasoningFetchSeq !== 'undefined' ? _reasoningFetchSeq : undefined;
  window.fetchReasoningChip = typeof fetchReasoningChip !== 'undefined' ? fetchReasoningChip : undefined;
  window.refreshProfileTransitionReasoningChip = typeof refreshProfileTransitionReasoningChip !== 'undefined' ? refreshProfileTransitionReasoningChip : undefined;
  window.clearProfileTransitionReasoningContext = typeof clearProfileTransitionReasoningContext !== 'undefined' ? clearProfileTransitionReasoningContext : undefined;
  window.syncReasoningChip = typeof syncReasoningChip !== 'undefined' ? syncReasoningChip : undefined;
  window._highlightReasoningOption = typeof _highlightReasoningOption !== 'undefined' ? _highlightReasoningOption : undefined;
  window.toggleReasoningDropdown = typeof toggleReasoningDropdown !== 'undefined' ? toggleReasoningDropdown : undefined;
  window._positionReasoningDropdown = typeof _positionReasoningDropdown !== 'undefined' ? _positionReasoningDropdown : undefined;
  window.closeReasoningDropdown = typeof closeReasoningDropdown !== 'undefined' ? closeReasoningDropdown : undefined;
  window._currentSessionToolsets = typeof _currentSessionToolsets !== 'undefined' ? _currentSessionToolsets : undefined;
  window._toolsetsCatalog = typeof _toolsetsCatalog !== 'undefined' ? _toolsetsCatalog : undefined;
  window._applyToolsetsChip = typeof _applyToolsetsChip !== 'undefined' ? _applyToolsetsChip : undefined;
  window._syncToolsetsChip = typeof _syncToolsetsChip !== 'undefined' ? _syncToolsetsChip : undefined;
  window.syncToolsetsChip = typeof syncToolsetsChip !== 'undefined' ? syncToolsetsChip : undefined;
  window._normalizeToolsetsCatalog = typeof _normalizeToolsetsCatalog !== 'undefined' ? _normalizeToolsetsCatalog : undefined;
  window._loadToolsetsCatalog = typeof _loadToolsetsCatalog !== 'undefined' ? _loadToolsetsCatalog : undefined;
  window.invalidateToolsetsCatalog = typeof invalidateToolsetsCatalog !== 'undefined' ? invalidateToolsetsCatalog : undefined;
  window._toolsetsInputList = typeof _toolsetsInputList !== 'undefined' ? _toolsetsInputList : undefined;
  window._ensureToolsetsPresetSection = typeof _ensureToolsetsPresetSection !== 'undefined' ? _ensureToolsetsPresetSection : undefined;
  window._appendToolsetsLabel = typeof _appendToolsetsLabel !== 'undefined' ? _appendToolsetsLabel : undefined;
  window._renderToolsetsPresetSections = typeof _renderToolsetsPresetSections !== 'undefined' ? _renderToolsetsPresetSections : undefined;
  window._populateToolsetsDropdown = typeof _populateToolsetsDropdown !== 'undefined' ? _populateToolsetsDropdown : undefined;
  window._positionToolsetsDropdown = typeof _positionToolsetsDropdown !== 'undefined' ? _positionToolsetsDropdown : undefined;
  window.toggleToolsetsDropdown = typeof toggleToolsetsDropdown !== 'undefined' ? toggleToolsetsDropdown : undefined;
  window.closeToolsetsDropdown = typeof closeToolsetsDropdown !== 'undefined' ? closeToolsetsDropdown : undefined;
  window._applySessionToolsets = typeof _applySessionToolsets !== 'undefined' ? _applySessionToolsets : undefined;
  window._syncMobileComposerConfigButton = typeof _syncMobileComposerConfigButton !== 'undefined' ? _syncMobileComposerConfigButton : undefined;
  window.closeMobileComposerConfig = typeof closeMobileComposerConfig !== 'undefined' ? closeMobileComposerConfig : undefined;
  window.openMobileComposerConfig = typeof openMobileComposerConfig !== 'undefined' ? openMobileComposerConfig : undefined;
  window.toggleMobileComposerConfig = typeof toggleMobileComposerConfig !== 'undefined' ? toggleMobileComposerConfig : undefined;
  window.openComposerContextMenu = typeof openComposerContextMenu !== 'undefined' ? openComposerContextMenu : undefined;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    _providerQuotaRefreshInFlight,
    _formatQuotaMoneyShort,
    _formatQuotaPercentShort,
    _providerQuotaIndicatorText,
    renderProviderQuotaIndicator,
    refreshProviderQuotaIndicator,
    _dynamicModelLabels,
    MODEL_STATE_KEY,
    PENDING_SESSION_MODEL_PREFIX,
    PENDING_SESSION_MODEL_MAX_AGE_MS,
    _getOptionProviderId,
    _providerFromModelValue,
    _modelPickerOptionIdentity,
    _deduplicateModelPickerOptions,
    _providerSkipsModelMismatchWarning,
    _providerDefersMissingModelFallback,
    _modelStateForSelect,
    _captureModelDropdownSelection,
    _modelProviderForSend,
    _reconcileModelDropdownSelection,
    _providerQualifiedModelValueForSelect,
    _readPersistedModelState,
    _writePersistedModelState,
    _clearPersistedModelState,
    _pendingSessionModelKey,
    _rememberPendingSessionModel,
    _readPendingSessionModel,
    _clearPendingSessionModel,
    _deliberateSessionModelPick,
    _reArmRecoveryPick,
    _applyPendingSessionModelForSession,
    _findModelInDropdown,
    _refreshOpenModelDropdown,
    _applyModelToDropdown,
    _ensureModelOptionInDropdown,
    _modelStateFromAppliedDropdown,
    _persistSessionModelCorrection,
    _modelDropdownRequestSeq,
    _modelCatalogFallbackRetried,
    _applySessionModelFallback,
    populateModelDropdown,
    _liveModelCache,
    _liveModelFetchPending,
    _addLiveModelsToSelect,
    _fetchLiveModels,
    _checkProviderMismatch,
    _selectedModelOption,
    _normalizeConfiguredModelKey,
    _isEquivalentConfiguredModelEntry,
    _getConfiguredModelBadge,
    _compactComposerModelChipLabel,
    syncModelChip,
    _modelDropdownHome,
    _restoreModelDropdownHome,
    _positionModelDropdown,
    _readModelOverflowData,
    _appendOverflowOptionsToGroup,
    _mountSearchableModelSelect,
    renderModelDropdown,
    selectModelFromDropdown,
    toggleModelDropdown,
    closeModelDropdown,
    closeSettingsModelDropdown,
    syncSettingsModelChip,
    selectSettingsModelFromDropdown,
    openSettingsModelDropdown,
    toggleSettingsModelDropdown,
    mountSettingsModelPicker,
    _modelDropdownRepositionScheduled,
    _repositionOpenModelDropdown,
    _composerFitScheduled,
    _composerFitResizeObserver,
    _composerFitMutationObserver,
    _composerFitObservedFooter,
    _composerFitResizeListenerBound,
    _fitComposerFooter,
    _scheduleComposerFit,
    _initComposerFooterFit,
    _currentReasoningEffort,
    _currentReasoningEffortsSupported,
    _currentReasoningToggleSupported,
    _profileTransitionReasoningContext,
    _normalizeReasoningEffort,
    _formatReasoningEffortLabel,
    _reasoningEffortContext,
    _reasoningEffortQuery,
    _applyReasoningOptions,
    _applyReasoningChip,
    _lastReasoningFetchKey,
    _reasoningFetchSeq,
    fetchReasoningChip,
    refreshProfileTransitionReasoningChip,
    clearProfileTransitionReasoningContext,
    syncReasoningChip,
    _highlightReasoningOption,
    toggleReasoningDropdown,
    _positionReasoningDropdown,
    closeReasoningDropdown,
    _currentSessionToolsets,
    _toolsetsCatalog,
    _applyToolsetsChip,
    _syncToolsetsChip,
    syncToolsetsChip,
    _normalizeToolsetsCatalog,
    _loadToolsetsCatalog,
    invalidateToolsetsCatalog,
    _toolsetsInputList,
    _ensureToolsetsPresetSection,
    _appendToolsetsLabel,
    _renderToolsetsPresetSections,
    _populateToolsetsDropdown,
    _positionToolsetsDropdown,
    toggleToolsetsDropdown,
    closeToolsetsDropdown,
    _applySessionToolsets,
    _syncMobileComposerConfigButton,
    closeMobileComposerConfig,
    openMobileComposerConfig,
    toggleMobileComposerConfig,
    openComposerContextMenu,
  };
}
