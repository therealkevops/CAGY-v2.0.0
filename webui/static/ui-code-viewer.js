/**
 * ui-code-viewer.js — Code Block Viewer: JSON/YAML default-view, PDF inline preview, HTML sandboxed iframe
 *
 * Extracted from ui.js (Sprint F-M4+, ADR 016).
 */

// ── JSON/YAML structured code-block default-view configuration (#484) ──
// Read the user's configured default-view mode for valid JSON/YAML fenced
// blocks. Falls back to 'auto' for any missing/invalid value so the renderer
// stays safe before settings load and in non-browser test contexts.
function _structuredCodeMode(){
  const m=(typeof window!=='undefined')?window._structuredCodeDefaultView:undefined;
  return (m==='on'||m==='off'||m==='auto')?m:'auto';
}
// Read the configured 'auto'-mode line threshold, clamped to a sane integer
// range. Invalid/missing values fall back to 10 (the original hardcoded value).
function _structuredCodeThreshold(){
  const raw=(typeof window!=='undefined')?window._structuredCodeAutoTreeLines:undefined;
  const n=parseInt(raw,10);
  return (Number.isFinite(n)&&n>=1&&n<=1000)?n:10;
}
// Pure decision helper: should a structured block default to Tree view?
// Factored out so the (mode, threshold, lineCount) contract is unit-testable.
//   mode 'on'   => always Tree
//   mode 'off'  => always Raw
//   mode 'auto' => Tree only when lineCount >= threshold (threshold sanitized,
//                  fallback 10)
function _structuredCodeShowTree(mode,threshold,lineCount){
  if(mode==='on') return true;
  if(mode==='off') return false;
  const th=(Number.isFinite(threshold)&&threshold>=1&&threshold<=1000)?threshold:10;
  return lineCount>=th;
}

function initTreeViews(container){
  const root=container||document;
  root.querySelectorAll('.code-tree-wrap:not([data-tree-init])').forEach(wrap=>{
    const rawText=wrap.dataset.raw;
    const lang=wrap.dataset.lang;
    let parsed=null;
    let parseFailed=false;
    // Try JSON parse
    try{ parsed=JSON.parse(rawText); }catch(e){ parseFailed=(lang==='json'); }
    // YAML: lazy-load js-yaml if needed
    if(!parsed && lang==='yaml'){
      if(typeof jsyaml!=='undefined'){
        try{ parsed=jsyaml.load(rawText); }catch(e){ parseFailed=true; }
      }else{
        // Defer: remove init marker so we retry after load.
        // Note: if CDN load fails, s.onerror does NOT call back —
        // the wrap stays un-initialised (raw view only), which is safe.
        wrap.removeAttribute('data-tree-init');
        _loadJsyamlThen(initTreeViews);
        return;
      }
    }
    // Mark as initialised only after we've committed to a render decision
    wrap.setAttribute('data-tree-init','1');
    if(!parsed || typeof parsed!=='object'){
      // No tree view for non-object values or unparseable content. LLMs often
      // emit JSON fragments (a bare "key": "val" line, snippets with ..., etc.)
      // that legitimately fail JSON.parse; surfacing a "parse failed" note for
      // those was pure noise. The block still renders as syntax-highlighted raw,
      // so just fall through silently. (parseFailed is retained for clarity.)
      void parseFailed;
      return; // leave as raw view
    }
    const lineCount=rawText.split('\n').length;
    // Default view is user-configurable (#484 follow-up). 'on' => always Tree,
    // 'off' => always Raw, 'auto' => Tree only when the block is >= the
    // configured line threshold (default 10, preserving the original behavior).
    // The per-block Raw/Tree toggle below always remains available regardless.
    const showTree=_structuredCodeShowTree(_structuredCodeMode(),_structuredCodeThreshold(),lineCount);
    // Build tree DOM
    const treeDiv=document.createElement('div');
    treeDiv.className='tree-view'+(showTree?'':' tree-hidden');
    treeDiv.appendChild(_buildTreeDOM(parsed, 0));
    // Toggle button in header
    const header=wrap.querySelector('.pre-header');
    if(header){
      const toggle=document.createElement('button');
      toggle.className='tree-toggle-btn';
      toggle.textContent=showTree?t('raw_view'):t('tree_view');
      toggle.onclick=(e)=>{
        e.stopPropagation();
        const isTreeHidden=treeDiv.classList.contains('tree-hidden');
        treeDiv.classList.toggle('tree-hidden',!isTreeHidden);
        const rawPre=wrap.querySelector('.tree-raw-view');
        if(rawPre) rawPre.style.display=isTreeHidden?'none':'';
        toggle.textContent=isTreeHidden?t('raw_view'):t('tree_view');
      };
      header.style.display='flex';
      header.style.justifyContent='space-between';
      header.style.alignItems='center';
      header.appendChild(toggle);
    }
    if(!showTree){
      const rawPre=wrap.querySelector('.tree-raw-view');
      if(rawPre) rawPre.style.display='';
    } else {
      const rawPre=wrap.querySelector('.tree-raw-view');
      if(rawPre) rawPre.style.display='none';
    }
    wrap.appendChild(treeDiv);
  });
}

function _buildTreeDOM(val, depth){
  const el=document.createElement('div');
  el.className='tree-node';
  if(val===null){ el.innerHTML=`<span class="tree-val tree-null">null</span>`; return el; }
  if(typeof val==='boolean'){ el.innerHTML=`<span class="tree-val tree-bool">${val}</span>`; return el; }
  if(typeof val==='number'){ el.innerHTML=`<span class="tree-val tree-num">${val}</span>`; return el; }
  if(typeof val==='string'){ el.innerHTML=`<span class="tree-val tree-str">&quot;${esc(val)}&quot;</span>`; return el; }
  if(Array.isArray(val)){
    el.classList.add('tree-array');
    const collapsed=depth>=2;
    const header=document.createElement('span');
    header.className='tree-collapsible';
    header.innerHTML=(collapsed?'▸ ': '▾ ')+`<span class="tree-bracket">[</span><span class="tree-count">${val.length}</span><span class="tree-bracket">]</span>`;
    const body=document.createElement('div');
    body.className='tree-children'+(collapsed?' tree-collapsed':'');
    val.forEach((item,i)=>{
      const child=document.createElement('div');
      child.className='tree-item';
      child.appendChild(_buildTreeDOM(item, depth+1));
      if(i<val.length-1) child.innerHTML+='<span class="tree-comma">,</span>';
      body.appendChild(child);
    });
    el.appendChild(header);
    el.appendChild(body);
    header.onclick=(()=>{const c=body.classList.contains('tree-collapsed'); body.classList.toggle('tree-collapsed'); header.innerHTML=(c?'▾ ':'▸ ')+`<span class="tree-bracket">[</span><span class="tree-count">${val.length}</span><span class="tree-bracket">]</span>`;});
    return el;
  }
  if(typeof val==='object'){
    el.classList.add('tree-object');
    const keys=Object.keys(val);
    const collapsed=depth>=2;
    const header=document.createElement('span');
    header.className='tree-collapsible';
    header.innerHTML=(collapsed?'▸ ': '▾ ')+`<span class="tree-bracket">{</span><span class="tree-count">${keys.length}</span><span class="tree-bracket">}</span>`;
    const body=document.createElement('div');
    body.className='tree-children'+(collapsed?' tree-collapsed':'');
    keys.forEach((key,i)=>{
      const child=document.createElement('div');
      child.className='tree-item';
      child.innerHTML=`<span class="tree-key">&quot;${esc(key)}&quot;</span><span class="tree-colon">: </span>`;
      child.appendChild(_buildTreeDOM(val[key], depth+1));
      if(i<keys.length-1) child.innerHTML+='<span class="tree-comma">,</span>';
      body.appendChild(child);
    });
    el.appendChild(header);
    el.appendChild(body);
    header.onclick=(()=>{const c=body.classList.contains('tree-collapsed'); body.classList.toggle('tree-collapsed'); header.innerHTML=(c?'▾ ':'▸ ')+`<span class="tree-bracket">{</span><span class="tree-count">${keys.length}</span><span class="tree-bracket">}</span>`;});
    return el;
  }
  el.innerHTML=`<span class="tree-val">${esc(String(val))}</span>`;
  return el;
}

function addCopyButtons(container){
  const el=container||$('msgInner');
  if(!el) return;
  el.querySelectorAll('pre > code').forEach(codeEl=>{
    const pre=codeEl.parentElement;
    const header=pre.previousElementSibling;
    if(pre.querySelector('.code-copy-btn')||(header&&header.classList.contains('pre-header')&&header.querySelector('.code-copy-btn'))) return;
    const btn=document.createElement('button');
    btn.className='code-copy-btn';
    btn.textContent=t('copy');
    btn.onclick=(e)=>{
      e.stopPropagation();
      _copyText(codeEl.textContent).then(()=>{
        btn.textContent=t('copied');
        setTimeout(()=>{btn.textContent=t('copy');},1500);
      }).catch(()=>{btn.textContent=t('copy_failed');setTimeout(()=>{btn.textContent=t('copy');},1500);});
    };
    if(header&&header.classList.contains('pre-header')){
      header.style.display='flex';
      header.style.justifyContent='space-between';
      header.style.alignItems='center';
      header.appendChild(btn);
    }else{
      pre.style.position='relative';
      btn.style.cssText='position:absolute;top:6px;right:6px;';
      pre.appendChild(btn);
    }
  });
}

let _mermaidLoading=false;
let _mermaidReady=false;

function loadDiffInline(container){
  const DIFF_MAX_SIZE=512*1024; // 512 KB cap for inline diff rendering
  const root=container||document;
  root.querySelectorAll('.diff-inline-load:not([data-loaded])').forEach(el=>{
    el.setAttribute('data-loaded','1');
    const path=el.dataset.path;
    const snapQuery=_mediaSnapQuery(el);
    apiFetch('api/media?path='+encodeURIComponent(path)+snapQuery)
      .then(text=>{
        if(text.length>DIFF_MAX_SIZE){
          el.outerHTML=`<div class="diff-inline-error">${esc(path.split('/').pop())}<br><span style="color:var(--muted);font-size:12px">${t('diff_too_large')}</span></div>`;
          return;
        }
        const lines=text.split('\n').map(line=>{
          const e=esc(line);
          if(e.startsWith('@@')) return `<span class="diff-line diff-hunk">${e}</span>`;
          if(e.startsWith('+')) return `<span class="diff-line diff-plus">${e}</span>`;
          if(e.startsWith('-')) return `<span class="diff-line diff-minus">${e}</span>`;
          return `<span class="diff-line">${e}</span>`;
        }).join('\n');
        el.outerHTML=`<div class="diff-inline"><div class="pre-header">${esc(path.split('/').pop())}</div><pre class="diff-block"><code>${lines}</code></pre></div>`;
      })
      .catch(()=>{
        el.outerHTML=`<div class="diff-inline-error">${esc(path.split('/').pop())}<br><span style="color:var(--muted);font-size:12px">${t('diff_error')}</span></div>`;
      });
  });
}

const CSV_MAX_SIZE=256*1024; // 256 KB cap for inline CSV rendering

function _mediaSessionQuery(){
  const mediaSessionId=(typeof S!=='undefined'&&S&&S.session&&S.session.session_id)?String(S.session.session_id):'';
  return mediaSessionId?'&session_id='+encodeURIComponent(mediaSessionId):'';
}

// Message-level media snapshots: lazy preview loaders (pdf/html/csv/diff/
// excalidraw) build their fetch URL from data-path at load time. The stamping
// pass in _stampMediaSnapshots carries the content digest in data-snap;
// append it here so the preview shows the file as the message emitted it.
function _mediaSnapQuery(el){
  const snap=el&&el.dataset?el.dataset.snap:'';
  return (snap&&/^[0-9a-f]{64}$/.test(snap))?('&snap='+snap):'';
}

function _csvMediaUrl(path, opts={}){
  let url='api/media?path='+encodeURIComponent(path)+_mediaSessionQuery();
  if(opts.snap) url+='&snap='+encodeURIComponent(opts.snap);
  if(opts.download) url+='&download=1';
  return url;
}

function buildCsvTablePreview(path, text, downloadUrl=''){
  if(typeof text!=='string') return {errorKey:'csv_error'};
  if(text.length>CSV_MAX_SIZE) return {errorKey:'csv_too_large'};
  const rows=text.replace(/\r\n/g,'\n').replace(/\r/g,'\n').split('\n').filter(r=>r.trim());
  if(rows.length<2) return {errorKey:'csv_no_data'};
  // Auto-detect separator (comma, semicolon, tab)
  // Heuristic: uses the first separator found in the header row. Edge case:
  // quoted fields containing commas without non-quoted commas in the header
  // could cause misdetection — acceptable trade-off for a preview renderer.
  const firstLine=rows[0];
  const separators=[',',';','\t'];
  const sep=separators.find(s=>firstLine.includes(s))||',';
  const headers=rows[0].split(sep).map(c=>c.trim().replace(/^["']|["']$/g,''));
  const bodyRows=rows.slice(1).map(r=>'<tr>'+r.split(sep).map(c=>`<td>${esc(c.trim().replace(/^["']|["']$/g,''))}</td>`).join('')+'</tr>').join('');
  const headerRow=headers.map(h=>`<th>${esc(h)}</th>`).join('');
  const fname=path.split('/').pop()||path;
  const downloadLink=downloadUrl
    ? `<a class="csv-download-link msg-media-link" href="${esc(downloadUrl)}" download="${esc(fname)}">📎 ${esc(fname)}</a>`
    : '';
  return {
    html:`<div class="csv-table-wrap"><div class="pre-header csv-preview-header"><span class="csv-preview-title">${esc(fname)} <span style="opacity:.5;font-size:11px">${t('csv_header_note')}</span></span>${downloadLink}</div><table class="csv-table"><thead><tr>${headerRow}</tr></thead><tbody>${bodyRows}</tbody></table></div>`,
  };
}

function _csvPreviewErrorHtml(path, errorKey){
  const fname=path.split('/').pop()||path;
  const downloadUrl=_csvMediaUrl(path,{download:true});
  return `<div class="diff-inline-error">${esc(fname)}<br><a class="msg-media-link" href="${esc(downloadUrl)}" download="${esc(fname)}">📎 ${esc(fname)}</a><br><span style="color:var(--muted);font-size:12px">${t(errorKey)}</span></div>`;
}

function loadCsvInline(container){
  const root=container||document;
  root.querySelectorAll('.csv-inline-load:not([data-loaded])').forEach(el=>{
    el.setAttribute('data-loaded','1');
    const path=el.dataset.path;
    const snap=_mediaSnapQuery(el).replace(/^&snap=/,'');
    const mediaUrl=_csvMediaUrl(path,{snap:snap||undefined});
    const downloadUrl=_csvMediaUrl(path,{download:true,snap:snap||undefined});
    apiFetch(mediaUrl)
      .then(text=>{
        const preview=buildCsvTablePreview(path, text, downloadUrl);
        el.outerHTML=preview.html||_csvPreviewErrorHtml(path, preview.errorKey||'csv_error');
      })
      .catch(()=>{
        el.outerHTML=_csvPreviewErrorHtml(path, 'csv_error');
      });
  });
}

function loadExcalidrawInline(container){
  const EXCALIDRAW_MAX_SIZE=512*1024; // 512 KB cap
  const root=container||document;
  root.querySelectorAll('.excalidraw-inline-load:not([data-loaded])').forEach(el=>{
    el.setAttribute('data-loaded','1');
    const path=el.dataset.path;
    const snapQuery=_mediaSnapQuery(el);
    fetch('api/media?path='+encodeURIComponent(path)+snapQuery)
      .then(r=>{if(!r.ok) throw new Error(r.status);return r.text();})
      .then(text=>{
        if(text.length>EXCALIDRAW_MAX_SIZE){
          el.outerHTML=`<div class="diff-inline-error">${esc(path.split('/').pop())}<br><span style="color:var(--muted);font-size:12px">${t('excalidraw_too_large')}</span></div>`;
          return;
        }
        // Validate it looks like Excalidraw JSON
        let data;
        try{data=JSON.parse(text);}catch(e){
          el.outerHTML=`<div class="diff-inline-error">${esc(path.split('/').pop())}<br><span style="color:var(--muted);font-size:12px">${t('excalidraw_invalid')}</span></div>`;
          return;
        }
        if(!data.type||data.type!=='excalidraw'){
          el.outerHTML=`<div class="diff-inline-error">${esc(path.split('/').pop())}<br><span style="color:var(--muted);font-size:12px">${t('excalidraw_invalid')}</span></div>`;
          return;
        }
        const fname=esc(path.split('/').pop());
        const downloadUrl='api/media?path='+encodeURIComponent(path)+'&download=1';
        el.outerHTML=`<div class="excalidraw-embed-wrap" title="${t('excalidraw_simplified')}">
  <div class="msg-artifact-header">
    <span class="msg-media-label">${t('excalidraw_label')}</span>
    <a class="excalidraw-open-link" href="${downloadUrl}" download="${fname}">${t('excalidraw_download')} ${fname}</a>
  </div>
  <div class="excalidraw-canvas" data-excalidraw='${esc(text)}'></div>
</div>`;
        // Lazy-init Excalidraw render after DOM insertion
        requestAnimationFrame(()=>_renderExcalidrawCanvases());
      })
      .catch(()=>{
        el.outerHTML=`<div class="diff-inline-error">${esc(path.split('/').pop())}<br><span style="color:var(--muted);font-size:12px">${t('excalidraw_error')}</span></div>`;
      });
  });
}

let _excalidrawScriptLoaded=false;
function _renderExcalidrawCanvases(){
  document.querySelectorAll('.excalidraw-canvas:not([data-rendered])').forEach(el=>{
    el.setAttribute('data-rendered','1');
    const dataStr=el.getAttribute('data-excalidraw');
    if(!dataStr) return;
    // Render a simple SVG preview using the Excalidraw elements
    try{
      const data=JSON.parse(dataStr);
      const elements=data.elements||[];
      if(!elements.length){el.innerHTML=`<div class="excalidraw-empty">${t('excalidraw_empty')}</div>`;return;}
      // Calculate bounds
      let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
      elements.forEach(el=>{
        const b=[el.x||0,el.y||0,(el.x||0)+(el.width||0),(el.y||0)+(el.height||0)];
        minX=Math.min(minX,b[0]);minY=Math.min(minY,b[1]);
        maxX=Math.max(maxX,b[2]);maxY=Math.max(maxY,b[3]);
      });
      const pad=20;minX-=pad;minY-=pad;maxX+=pad;maxY+=pad;
      const w=Math.max(maxX-minX,200);const h=Math.max(maxY-minY,150);
      // SVG attributes are rendered via innerHTML below, so attacker-controlled
      // values from JSON (e.g. strokeColor='red"/><script>...') would break out
      // of the attribute. Escape strings; coerce numerics.
      const _sa=v=>String(v==null?'':v).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      const _num=(v,fb)=>{const n=Number(v);return Number.isFinite(n)?n:fb;};
      const svgParts=[`<svg xmlns="http://www.w3.org/2000/svg" viewBox="${_num(minX,0)} ${_num(minY,0)} ${_num(w,200)} ${_num(h,150)}" class="excalidraw-svg">`];
      elements.forEach(el=>{
        const stroke=_sa(el.strokeColor||'#1e1e1e');
        const fill=_sa(el.backgroundColor||'transparent');
        const sw=_num(el.strokeWidth,2);
        const x=_num(el.x,0),y=_num(el.y,0),w=_num(el.width,0),h=_num(el.height,0);
        if(el.type==='rectangle'){
          svgParts.push(`<rect x="${x}" y="${y}" width="${w}" height="${h}" stroke="${stroke}" stroke-width="${sw}" fill="${fill}" rx="${el.roundness?.type===3?8:0}"/>`);
        }else if(el.type==='diamond'){
          const cx=x+w/2,cy=y+h/2;
          svgParts.push(`<polygon points="${cx},${y} ${x+w},${cy} ${cx},${y+h} ${x},${cy}" stroke="${stroke}" stroke-width="${sw}" fill="${fill}"/>`);
        }else if(el.type==='ellipse'){
          svgParts.push(`<ellipse cx="${x+w/2}" cy="${y+h/2}" rx="${w/2}" ry="${h/2}" stroke="${stroke}" stroke-width="${sw}" fill="${fill}"/>`);
        }else if(el.type==='line'){
          const pts=(el.points||[]).filter(p=>Array.isArray(p)&&p.length>=2);
          if(!pts.length) return;
          let d=`M ${_num(x+_num(pts[0][0],0),0)} ${_num(y+_num(pts[0][1],0),0)}`;
          for(let i=1;i<pts.length;i++) d+=` L ${_num(x+_num(pts[i][0],0),0)} ${_num(y+_num(pts[i][1],0),0)}`;
          svgParts.push(`<path d="${d}" stroke="${stroke}" stroke-width="${sw}" fill="none" stroke-linecap="round" stroke-linejoin="round"/>`);
        }else if(el.type==='arrow'){
          const pts=(el.points||[]).filter(p=>Array.isArray(p)&&p.length>=2);
          if(!pts.length) return;
          let d=`M ${_num(x+_num(pts[0][0],0),0)} ${_num(y+_num(pts[0][1],0),0)}`;
          for(let i=1;i<pts.length;i++) d+=` L ${_num(x+_num(pts[i][0],0),0)} ${_num(y+_num(pts[i][1],0),0)}`;
          svgParts.push(`<path d="${d}" stroke="${stroke}" stroke-width="${sw}" fill="none" stroke-linecap="round" stroke-linejoin="round" marker-end="url(#arrowhead)"/>`);
        }else if(el.type==='text'){
          const fontSize=_num(el.fontSize,20);
          const txt=String(el.text==null?'':el.text);
          const lines=txt.split('\n');
          lines.forEach((line,i)=>{
            svgParts.push(`<text x="${x}" y="${y+i*fontSize*1.2+fontSize}" fill="${stroke}" font-size="${fontSize}" font-family="Virgil, Segoe UI Emoji, sans-serif">${esc(line)}</text>`);
          });
        }else if(el.type==='draw'){
          const pts=(el.points||[]).filter(p=>Array.isArray(p)&&p.length>=2);
          if(pts.length>1){
            let d=`M ${_num(x+_num(pts[0][0],0),0)} ${_num(y+_num(pts[0][1],0),0)}`;
            for(let i=1;i<pts.length;i++) d+=` L ${_num(x+_num(pts[i][0],0),0)} ${_num(y+_num(pts[i][1],0),0)}`;
            svgParts.push(`<path d="${d}" stroke="${stroke}" stroke-width="${sw}" fill="none" stroke-linecap="round" stroke-linejoin="round"/>`);
          }
        }
        // Unknown element types (e.g. image, frame, group, freedraw) are
        // silently skipped to avoid breaking the render. This is a simplified
        // SVG preview, not a pixel-identical Excalidraw canvas reproduction.
      });
      // Arrow marker definition
      svgParts.unshift(`<defs><marker id="arrowhead" markerWidth="10" markerHeight="7" refX="10" refY="3.5" orient="auto"><polygon points="0 0, 10 3.5, 0 7" fill="#1e1e1e"/></marker></defs>`);
      svgParts.push('</svg>');
      el.innerHTML=svgParts.join('');
    }catch(e){
      el.innerHTML=`<div class="excalidraw-empty">${t('excalidraw_render_error')}</div>`;
    }
  });
}

// ── PDF inline preview (first page) ────────────────────────────────────────
// NOTE: PDF.js is loaded from CDN (jsdelivr). Offline/air-gapped deployments
// will not get inline previews; the 15 s fallback timeout degrades to a
// download link in that case. The 4 MB size cap is checked client-side after
// the full buffer is received — ideally the server would enforce it before
// streaming (out of scope for this client-side PR).
let _pdfjsReady=false, _pdfjsLoading=false;
function loadPdfInline(container){
  const PDF_MAX_SIZE=4*1024*1024; // 4 MB cap for inline PDF preview
  const root=container||document;
  root.querySelectorAll('.pdf-preview-load:not([data-loaded])').forEach(el=>{
    el.setAttribute('data-loaded','1');
    const path=el.dataset.path;
    const fname=path.split('/').pop()||path;
    const mediaSessionId=(typeof S!=='undefined'&&S&&S.session&&S.session.session_id)?String(S.session.session_id):'';
    const snapQuery=_mediaSnapQuery(el);
    const publicMediaUrl='api/media?path='+encodeURIComponent(path);
    const mediaUrl=publicMediaUrl+(mediaSessionId?'&session_id='+encodeURIComponent(mediaSessionId):'')+snapQuery;
    const loadPdf=(pdfjsLib)=>{
      fetch(mediaUrl)
        .then(r=>{if(!r.ok) throw new Error(r.status); return r.arrayBuffer();})
        .then(buf=>{
          if(buf.byteLength>PDF_MAX_SIZE){
            const dlUrl=publicMediaUrl+'&download=1'+snapQuery;
            el.outerHTML=`<div class="pdf-preview-fallback"><a class="msg-media-link" href="${dlUrl}" download="${esc(fname)}">📎 ${esc(fname)}</a><br><span style="color:var(--muted);font-size:12px">${t('pdf_too_large')}</span></div>`;
            return;
          }
          return pdfjsLib.getDocument({data:buf, isEvalSupported:false}).promise;
        })
        .then(pdf=>{
          if(!pdf) return;
          const dlUrl=publicMediaUrl+'&download=1'+snapQuery;
          const total=pdf.numPages;
          const pagesLabel=total>1?` · ${total} pages`:'';
          const wrap=document.createElement('div');
          wrap.className='pdf-preview-wrap';
          wrap.innerHTML=`<div class="pdf-preview-header"><span>📄 ${esc(fname)}${pagesLabel}</span><a href="${dlUrl}" download="${esc(fname)}" class="pdf-download-link">${t('pdf_download')} ↓</a></div><div class="pdf-preview-body"></div>`;
          const body=wrap.querySelector('.pdf-preview-body');
          el.replaceWith(wrap);
          // Render every page (capped) sequentially to limit memory; the
          // canvases stack vertically in the scrollable preview body.
          const MAX_PAGES=20;
          const n=Math.min(total,MAX_PAGES);
          if(total>MAX_PAGES){
            const notice=document.createElement('div');
            notice.className='pdf-preview-truncated';
            notice.textContent=t('pdf_truncated',MAX_PAGES,total);
            body.appendChild(notice);
          }
          // On a per-page failure, skip that page and continue so one malformed
          // page can't silently halt the preview or surface an unhandled
          // promise rejection (renderPage runs outside the outer .catch chain).
          const renderPage=(i)=>{
            if(i>n) return;
            pdf.getPage(i).then(page=>{
              const canvas=document.createElement('canvas');
              const scale=1.5;
              const viewport=page.getViewport({scale});
              canvas.width=viewport.width;
              canvas.height=viewport.height;
              canvas.className='pdf-preview-canvas';
              // Attach only after a successful render, so a render rejection
              // (corrupt page data, null 2d context) can't leave a blank canvas
              // behind — the .catch then simply skips to the next page.
              return page.render({canvasContext:canvas.getContext('2d'),viewport}).promise.then(()=>{ body.appendChild(canvas); });
            }).then(()=>renderPage(i+1)).catch(()=>renderPage(i+1));
          };
          renderPage(1);
        })
        .catch(()=>{
          const dlUrl=publicMediaUrl+'&download=1'+snapQuery;
          el.outerHTML=`<div class="pdf-preview-fallback"><a class="msg-media-link" href="${dlUrl}" download="${esc(fname)}">📎 ${esc(fname)}</a><br><span style="color:var(--muted);font-size:12px">${t('pdf_error')}</span></div>`;
        });
    };
    if(_pdfjsReady){
      loadPdf(window._pdfjsLib);
    } else if(!_pdfjsLoading){
      _pdfjsLoading=true;
      const _pdfSrc='https://cdn.jsdelivr.net/npm/pdfjs-dist@4.9.155/build/pdf.min.mjs';
      const _pdfWorker='https://cdn.jsdelivr.net/npm/pdfjs-dist@4.9.155/build/pdf.worker.min.mjs';
      const _pdfBlob=new Blob([`import*as p from'${_pdfSrc}';p.GlobalWorkerOptions.workerSrc='${_pdfWorker}';window._pdfjsLib=p;window._pdfjsReady=true;window.dispatchEvent(new Event('pdfjs-ready'));`],{type:'application/javascript'});
      const s=document.createElement('script');
      s.type='module';
      const _pdfBlobUrl=URL.createObjectURL(_pdfBlob);
      s.src=_pdfBlobUrl;
      s.onload=()=>URL.revokeObjectURL(_pdfBlobUrl);
      document.head.appendChild(s);
      window.addEventListener('pdfjs-ready',()=>{ _pdfjsReady=true; loadPdf(window._pdfjsLib); },{once:true});
      setTimeout(()=>{
        if(!_pdfjsReady){
          const dlUrl=publicMediaUrl+'&download=1'+snapQuery;
          if(el.parentNode){
            el.outerHTML=`<div class="pdf-preview-fallback"><a class="msg-media-link" href="${dlUrl}" download="${esc(fname)}">📎 ${esc(fname)}</a><br><span style="color:var(--muted);font-size:12px">${t('pdf_error')}</span></div>`;
          }
        }
      },15000);
    } else {
      window.addEventListener('pdfjs-ready',()=>{ loadPdf(window._pdfjsLib); },{once:true});
    }
  });
}

// ── HTML inline preview (sandboxed iframe) ─────────────────────────────────
function loadHtmlInline(container){
  const HTML_MAX_SIZE=256*1024; // 256 KB cap for inline HTML preview
  const root=container||document;
  root.querySelectorAll('.html-preview-load:not([data-loaded])').forEach(el=>{
    el.setAttribute('data-loaded','1');
    const path=el.dataset.path;
    const fname=path.split('/').pop()||path;
    const mediaSessionId=(typeof S!=='undefined'&&S&&S.session&&S.session.session_id)?String(S.session.session_id):'';
    const snapQuery=_mediaSnapQuery(el);
    const publicMediaUrl='api/media?path='+encodeURIComponent(path);
    const mediaUrl=publicMediaUrl+(mediaSessionId?'&session_id='+encodeURIComponent(mediaSessionId):'')+snapQuery;
    apiFetch(mediaUrl, {cache:'no-store'})
      .then(html=>{
        if(html.length>HTML_MAX_SIZE){
          const openUrl=publicMediaUrl+'&inline=1'+snapQuery;
          el.outerHTML=`<div class="html-preview-fallback"><a class="msg-media-link" href="${openUrl}" target="_blank" rel="noopener">📎 ${esc(fname)}</a><br><span style="color:var(--muted);font-size:12px">${t('html_too_large')}</span></div>`;
          return;
        }
        const openUrl=publicMediaUrl+'&inline=1'+snapQuery;
        const safeHtml=html.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        el.outerHTML=`<div class="html-preview-wrap"><div class="html-preview-header"><span>${t('html_sandbox_label')}</span><a href="${openUrl}" target="_blank" rel="noopener" class="html-open-link">${t('html_open_full')} ↗</a></div><iframe srcdoc="${safeHtml}" sandbox="allow-scripts" class="html-preview-iframe" loading="lazy"></iframe></div>`;
      })
      .catch(()=>{
        const dlUrl=publicMediaUrl+'&download=1'+snapQuery;
        el.outerHTML=`<div class="html-preview-fallback"><a class="msg-media-link" href="${dlUrl}" download="${esc(fname)}">📎 ${esc(fname)}</a><br><span style="color:var(--muted);font-size:12px">${t('html_error')}</span></div>`;
      });
  });
}

function _cleanMermaidDOM(id, fbId) {
  if (!id) return;
  const ids = [id, 'd' + id, fbId, fbId ? 'd' + fbId : null].filter(Boolean);
  ids.forEach(x => {
    const el = document.getElementById(x);
    if (el) el.remove();
  });
  try {
    const escFn = (window.CSS && CSS.escape) ? CSS.escape : (s => s.replace(/[^a-zA-Z0-9_-]/g, '\\$&'));
    const safeId = escFn(id);
    const selectors = [
      `body > #${safeId}`,
      `body > #d${safeId}`,
      `body > [id^="d${safeId}"]`,
      `body > [id^="${safeId}"]`,
      'body > svg.error-icon',
      'body > .error-icon',
      'body > svg[id^="m-"]'
    ];
    if (fbId) {
      const safeFb = escFn(fbId);
      selectors.push(`body > #${safeFb}`, `body > #d${safeFb}`, `body > [id^="d${safeFb}"]`, `body > [id^="${safeFb}"]`);
    }
    document.querySelectorAll(selectors.join(',')).forEach(el => el.remove());
  } catch (_) {}
}

function _isMermaidErrorSvg(svg) {
  if (!svg || typeof svg !== 'string') return true;
  return svg.includes('Syntax error in text') || svg.includes('error-icon') || svg.includes('error-text');
}

function renderMermaidBlocks(container){
  const root=container||document;
  try {
    document.querySelectorAll('body > [id^="dm-"], body > svg[id^="m-"], body > .error-icon, body > svg.error-icon, body > [id*="-fb"]').forEach(el => el.remove());
  } catch (_) {}
  const blocks=root.querySelectorAll('.mermaid-block:not([data-rendered])');
  if(!blocks.length) return;
  if(!_mermaidReady){
    if(typeof mermaid !== 'undefined'){
      try {
        mermaid.initialize({startOnLoad:false,theme:document.documentElement.classList.contains('dark')?'dark':'default',themeVariables:{
          fontFamily:'inherit',fontSize:'14px',
          primaryColor:'#4a6fa5',primaryTextColor:'#e2e8f0',lineColor:'#718096',
          secondaryColor:'#2d3748',tertiaryColor:'#1a202c',primaryBorderColor:'#4a5568',
        }});
        _mermaidReady=true;
        renderMermaidBlocks(container);
      } catch(e){}
      return;
    }
    if(!_mermaidLoading){
      _mermaidLoading=true;
      const script=document.createElement('script');
      script.src='static/vendor/mermaid/mermaid.min.js';
      script.onload=()=>{
        if(typeof mermaid!=='undefined'){
          try {
            mermaid.initialize({startOnLoad:false,theme:document.documentElement.classList.contains('dark')?'dark':'default',themeVariables:{
              fontFamily:'inherit',fontSize:'14px',
              primaryColor:'#4a6fa5',primaryTextColor:'#e2e8f0',lineColor:'#718096',
              secondaryColor:'#2d3748',tertiaryColor:'#1a202c',primaryBorderColor:'#4a5568',
            }});
            _mermaidReady=true;
            renderMermaidBlocks();
          } catch(e){}
        }
      };
      script.onerror=()=>{
        _mermaidLoading=false;
      };
      document.head.appendChild(script);
    }
    return;
  }
  blocks.forEach(async(block)=>{
    block.dataset.rendered='true';
    let code=(block.textContent || '').trim();
    const id=block.dataset.mermaidId||('m-'+Math.random().toString(36).slice(2));

    // Convert legacy graph TD/LR/BT to modern flowchart syntax which supports complex subgraphs
    if (/^graph\s+(TD|TB|BT|RL|LR)\b/i.test(code)) {
      code = code.replace(/^graph\s+/i, 'flowchart ');
    }

    // Auto-quote pipe labels containing unquoted parentheses, brackets, or arrows (which break Mermaid lexer)
    code = code.replace(/\|([^|\r\n]+)\|/g, (match, label) => {
      const trimmed = label.trim();
      if ((trimmed.includes('(') || trimmed.includes(')') || trimmed.includes('[') || trimmed.includes(']') || trimmed.includes('->')) &&
          !(trimmed.startsWith('"') && trimmed.endsWith('"'))) {
        return `|"${trimmed.replace(/"/g, "'")}"|`;
      }
      return match;
    });

    let renderedOk = false;
    try{
      const res=await mermaid.render(id,code);
      if(res && res.svg && !_isMermaidErrorSvg(res.svg)){
        block.innerHTML=res.svg;
        const renderedSvg = block.querySelector('svg');
        if(renderedSvg) _mountMermaidViewer(renderedSvg, {mode:'inline'});
        block.classList.add('mermaid-rendered');
        renderedOk = true;
      }
    }catch(e){}
    _cleanMermaidDOM(id);

    if(!renderedOk){
      // Fallback: clean connections pointing directly to container subgraphs
      const fbId = id + '-fb';
      try {
        let fallbackCode = code
          .replace(/-->\|([^|]+)\|\s+([A-Za-z0-9_]+_Account|[A-Za-z0-9_]+_Cluster|[A-Za-z0-9_]+_Plane)\b/g, '-->|$1| $2_Node')
          .replace(/([A-Za-z0-9_]+_Cluster)\s+<-->\|([^|]+)\|\s+([A-Za-z0-9_]+)/g, 'DSF <-->|$2| $3');
        const res=await mermaid.render(fbId, fallbackCode);
        if(res && res.svg && !_isMermaidErrorSvg(res.svg)){
          block.innerHTML=res.svg;
          const renderedSvg = block.querySelector('svg');
          if(renderedSvg) _mountMermaidViewer(renderedSvg, {mode:'inline'});
          block.classList.add('mermaid-rendered');
          renderedOk = true;
        }
      } catch(e2){}
      _cleanMermaidDOM(id, fbId);
    }

    if(!renderedOk){
      _cleanMermaidDOM(id, id + '-fb');
      // Fall back to showing as a clean formatted code block.
      block.classList.remove('mermaid-block');
      block.classList.add('prewrap');
      block.innerHTML=`<div class="pre-header">mermaid</div><pre><code>${esc(code)}</code></pre>`;
    }
  });
}

let _katexLoading=false;
let _katexReady=false;

function _isStreamingEquationPending(el,root){
  const tagName=(el&&el.tagName||'').toLowerCase();
  if(tagName!=='equation-block'&&tagName!=='equation-inline') return false;
  // streaming-markdown fills custom equation elements while the parser owns the
  // open node. If the equation is currently the last descendant of the live
  // assistant body, we cannot tell whether more TeX is still coming. Skip it
  // during live debounce passes so a partial source is not permanently marked
  // data-rendered before the final parser_end flush.
  let node=el;
  while(node&&node!==root){
    if(node.nextSibling) return false;
    node=node.parentNode;
  }
  return Boolean(node===root);
}

function renderKatexBlocks(container,options){
  const root=container||document;
  const streaming=Boolean(options&&options.streaming);
  const blocks=root.querySelectorAll(
    '.katex-block:not([data-rendered]),.katex-inline:not([data-rendered]),'+
    'equation-block:not([data-rendered]),equation-inline:not([data-rendered])'
  );
  if(!blocks.length) return;
  if(!_katexReady){
    if(!_katexLoading){
      _katexLoading=true;
      const script=document.createElement('script');
      script.src='static/vendor/katex/0.16.22/katex.min.js';
      script.integrity='sha384-cMkvdD8LoxVzGF/RPUKAcvmm49FQ0oxwDF3BGKtDXcEc+T1b2N+teh/OJfpU0jr6';
      script.crossOrigin='anonymous';
      script.onload=()=>{
        if(typeof katex!=='undefined'){
          _katexReady=true;
          renderKatexBlocks();
        }
      };
      document.head.appendChild(script);
    }
    return;
  }
  blocks.forEach(el=>{
    if(streaming&&_isStreamingEquationPending(el,root)) return;
    el.dataset.rendered='true';
    const src=el.textContent||'';
    const tagName=(el.tagName||'').toLowerCase();
    const displayMode=el.dataset.katex==='display'||tagName==='equation-block';
    try{
      katex.render(src,el,{
        displayMode,
        throwOnError:false,
        trust:false,
        strict:'ignore',
      });
    }catch(e){
      // Leave as raw text in a code span on failure
      el.outerHTML=`<code>${esc(src)}</code>`;
    }
  });
}

function _thinkingMarkup(text=''){
  const clean=_sanitizeThinkingDisplayText(text);
  const openClass=_worklogDetailsExpandedDefault()?' open':'';
  return (clean&&String(clean).trim())
    ? `<div class="thinking-card${openClass}"><div class="thinking-card-header" onclick="this.parentElement.classList.toggle('open')"><span class="thinking-card-icon">${li('lightbulb',14)}</span><span class="thinking-card-label">${t('thinking')}</span><span class="thinking-card-toggle">${li('chevron-right',12)}</span></div><div class="thinking-card-body"><pre>${esc(String(clean).trim())}</pre></div></div>`
    : `<div class="thinking"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div>`;
}
function _renderThinkingInto(row,text=''){
  if(!row) return;
  const clean=_sanitizeThinkingDisplayText(text);
  if(!clean){
    row.innerHTML=_thinkingMarkup(text);
    return;
  }
  const pre=row.querySelector('.thinking-card-body pre');
  if(pre){
    pre.textContent=clean;
    return;
  }
  row.innerHTML=_thinkingMarkup(text);
}
function finalizeThinkingCard(){
  // Guard: only finalize thinking card if we're looking at the session that started it.
  // Without this check, switching tabs while a stream is running causes finalizeThinkingCard
  // to remove/modify the thinking card DOM of the wrong session — the card belongs to the
  // stream that started it, not the session currently displayed.
  const _guardTurn = $('liveAssistantTurn');
  if(_guardTurn && S.session && _guardTurn.dataset.sessionId !== S.session.session_id) return;
  if(isTransparentStream()){
    const row=$('thinkingRow');
    if(row){
      row.removeAttribute('id');
      row.removeAttribute('data-thinking-active');
      row.removeAttribute('data-live-thinking');
    }
    return;
  }
  if(!isSimplifiedToolCalling()){
    const row=$('thinkingRow');
    if(!row) return;
    // If the row is still just a spinner (no thinking content rendered),
    // remove it entirely — it's the initial waiting dots.
    const hasContent=!!row.querySelector('.thinking-card');
    if(!hasContent && row.getAttribute('data-thinking-active')==='1'){
      row.remove();
      return;
    }
    // If the user was watching (scroll pinned = at bottom), scroll the thinking
    // card back to the top so the completed response is visible underneath without
    // the thinking content blocking it. If they scrolled up to read history,
    // leave their scroll position intact.
    if(_scrollPinned){
      const body=row&&row.querySelector('.thinking-card-body');
      if(body) body.scrollTop=0;
    }
    row.removeAttribute('id');
    row.removeAttribute('data-thinking-active');
    return;
  }
  const turn=$('liveAssistantTurn');
  const group=turn&&turn.querySelector('.live-worklog[data-live-tool-call-group="1"],.tool-worklog-group[data-live-tool-call-group="1"],.tool-call-group[data-live-tool-call-group="1"]');
  if(group){
    const activeReason=turn.querySelector('.wl-reason[data-worklog-reason-active="1"]');
    if(activeReason) activeReason.removeAttribute('data-worklog-reason-active');
    turn.querySelectorAll('.agent-activity-thinking[data-thinking-active="1"]').forEach(active=>{
      active.removeAttribute('data-thinking-active');
      active.removeAttribute('data-live-thinking');
    });
    _syncToolCallGroupSummary(group);
  }
}
function appendThinking(text='', options){
  // Guard: ignore if session was switched during an async SSE stream.
  // The old stream's reasoning events can still fire after switch;
  // without this check they would pollute the new session's DOM.
  options=options||{};
  const allowPendingPlaceholder=!!(options&&options.pending===true);
  const anchorRenderFallback=!!(options&&options.anchorRenderFallback===true);
  if(typeof isFinalAnswerOnlyMode==='function'&&isFinalAnswerOnlyMode()) return;
  if(!S.session||(!S.activeStreamId&&!allowPendingPlaceholder)) return;
  if(options.sessionId&&String(options.sessionId)!==String(S.session.session_id||'')) return;
  if(options.streamId&&String(options.streamId)!==String(S.activeStreamId||'')) return;
  const existingLiveTurn=$('liveAssistantTurn');
  if(anchorRenderFallback&&existingLiveTurn&&existingLiveTurn.dataset&&
      existingLiveTurn.dataset.sessionId&&
      String(existingLiveTurn.dataset.sessionId)!==String(S.session.session_id||'')){
    if(!_resetMismatchedLiveAssistantTurnForSession(existingLiveTurn, S.session.session_id)) return;
  }
  if(anchorRenderFallback&&existingLiveTurn&&_updateLiveAnchorReasoningRowForFallback(existingLiveTurn,text,options)) return;
  if(!allowPendingPlaceholder&&!anchorRenderFallback&&isLiveAnchorActivitySceneOwner(S.activeStreamId)){
    _renderLiveAnchorActivitySceneForStream(S.activeStreamId, S.session.session_id);
    return;
  }
  const empty=$('emptyState');
  if(empty) empty.style.display='none';
  if(!isSimplifiedToolCalling()){
    let row=$('thinkingRow');
    if(!row){
      row=document.createElement('div');
      row.id='thinkingRow';
      row.className='thinking-card-row';
      const inner=$('msgInner');
      if(inner) inner.appendChild(row);
    }
    row.setAttribute('data-thinking-active','1');
    _renderThinkingInto(row,text);
    if(typeof scrollIfPinned==='function') scrollIfPinned();
    return;
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
  if(!blocks) return;
  const clean=_sanitizeThinkingDisplayText(text);
  if(clean&&window._showThinking!==false){
    const segmentSeq=options.segmentSeq!==undefined&&options.segmentSeq!==null?String(options.segmentSeq):'';
    const burstId=options.burstId!==undefined&&options.burstId!==null?String(options.burstId):'';
    const thinkingKey=String(options.thinkingKey||(
      segmentSeq?`segment:${segmentSeq}`:
      burstId?`burst:${burstId}`:
      'turn'
    ));
    if(isTransparentStream()){
      let row=blocks.querySelector(`.agent-activity-thinking[data-live-thinking="1"][data-live-thinking-key="${CSS.escape(thinkingKey)}"]`);
      if(!row){
        row=_thinkingActivityNode(clean, false);
        row.id='thinkingRow';
        row.setAttribute('data-live-thinking','1');
        row.setAttribute('data-live-thinking-key',thinkingKey);
        if(segmentSeq) row.setAttribute('data-live-segment-seq',segmentSeq);
        if(burstId) row.setAttribute('data-activity-burst-id',burstId);
        blocks.querySelectorAll('.agent-activity-thinking[data-thinking-active="1"]').forEach(el=>{
          if(el!==row){
            el.removeAttribute('id');
            el.removeAttribute('data-thinking-active');
            el.removeAttribute('data-live-thinking');
          }
        });
        row.setAttribute('data-thinking-active','1');
        const liveFooter=blocks.querySelector('#liveRunStatus');
        if(liveFooter&&liveFooter.parentElement===blocks) blocks.insertBefore(row,liveFooter);
        else blocks.appendChild(row);
      }else{
        _renderThinkingInto(row, clean);
      }
      row.id='thinkingRow';
      row.setAttribute('data-thinking-active','1');
      const existingEventAt=row.getAttribute('data-event-at');
      const nextTs=_firstValidTimestampSeconds(
        options.ts,
        options.timestamp,
        options.created_at,
        existingEventAt
      );
      _decorateTransparentEventRow(row,{
        type:'thinking',
        text:clean,
        preview:clean,
        ts:nextTs||undefined,
        live:true,
        segmentSeq,
        burstId,
      });
      _syncTransparentEventControls(turn);
      if(typeof scrollIfPinned==='function') scrollIfPinned();
      return;
    }
    const group=ensureLiveWorklogContainer(blocks,{
      activityKey:options.activityKey||(S.activeStreamId?'live:'+S.activeStreamId:null),
    });
    const list=_toolWorklogListEl(group);
    if(list){
      let row=list.querySelector(`.agent-activity-thinking[data-live-thinking="1"][data-live-thinking-key="${CSS.escape(thinkingKey)}"]`);
      if(!row){
        row=_thinkingActivityNode(clean, false, thinkingKey);
        row.setAttribute('data-live-thinking','1');
        row.setAttribute('data-live-thinking-key',thinkingKey);
        if(segmentSeq) row.setAttribute('data-live-segment-seq',segmentSeq);
        if(burstId) row.setAttribute('data-activity-burst-id',burstId);
        list.querySelectorAll('.agent-activity-thinking[data-thinking-active="1"]').forEach(el=>{
          if(el!==row){
            el.removeAttribute('data-thinking-active');
            el.removeAttribute('data-live-thinking');
          }
        });
        row.setAttribute('data-thinking-active','1');
        list.appendChild(row);
      }else{
        _renderThinkingInto(row, clean);
      }
      row.setAttribute('data-thinking-active','1');
      _syncToolCallGroupSummary(group);
    }
  }
  if(typeof scrollIfPinned==='function') scrollIfPinned();
}
function updateThinking(text='', options){appendThinking(text, options);}
function removeThinking(){
  if(isTransparentStream()){
    const liveTurn=$('liveAssistantTurn');
    const blocks=_assistantTurnBlocks(liveTurn);
    if(blocks) blocks.querySelectorAll('.agent-activity-thinking[data-thinking-active="1"]').forEach(row=>{
      row.removeAttribute('id');
      row.removeAttribute('data-thinking-active');
      row.removeAttribute('data-live-thinking');
    });
    if(liveTurn&&blocks&&!blocks.children.length) liveTurn.remove();
    return;
  }
  if(!isSimplifiedToolCalling()){
    const el=$('thinkingRow');
    if(el) el.remove();
    const liveTurn=$('liveAssistantTurn');
    const blocks=_assistantTurnBlocks(liveTurn);
    if(liveTurn&&blocks&&!blocks.children.length) liveTurn.remove();
    return;
  }
  const turn=$('liveAssistantTurn');
  const blocks=_assistantTurnBlocks(turn);
  if(blocks) blocks.querySelectorAll('.agent-activity-thinking:not([data-anchor-scene-row="1"])').forEach(el=>el.remove());
  if(blocks) blocks.querySelectorAll('.wl-reason[data-worklog-anchor-reason="1"],.wl-reason[data-worklog-reason-source="reasoning"]').forEach(el=>el.remove());
  if(blocks) blocks.querySelectorAll('.live-worklog[data-live-worklog-shell="1"],.tool-worklog-group[data-live-tool-call-group="1"]:not([data-anchor-scene-owner="1"]),.tool-call-group[data-live-tool-call-group="1"]:not([data-anchor-scene-owner="1"]),.tool-call-group[data-agent-activity-group="1"]:not([data-anchor-scene-owner="1"])').forEach(group=>{
    _syncToolCallGroupSummary(group);
    if(!group.querySelector('.tool-card-row,.agent-activity-thinking,.wl-reason')){
      if(typeof _clearActivityElapsedTimer==='function') _clearActivityElapsedTimer();
      group.remove();
    }
  });
  if(turn&&blocks&&!blocks.children.length) turn.remove();
}

function fileIcon(name, type){
  if(type==='dir') return li('folder',14);
  const e=fileExt(name);
  if(IMAGE_EXTS.has(e)) return li('image',14);
  if(MD_EXTS.has(e))    return li('file-text',14);
  if(typeof DOWNLOAD_EXTS!=='undefined'&&DOWNLOAD_EXTS.has(e)) return li('download',14);
  if(e==='.py')   return li('file-code',14);
  if(e==='.js'||e==='.ts'||e==='.jsx'||e==='.tsx') return li('zap',14);
  if(e==='.json'||e==='.yaml'||e==='.yml'||e==='.toml') return li('settings',14);
  if(e==='.sh'||e==='.bash') return li('terminal',14);
  if(e==='.pdf') return li('download',14);
  return li('file-text',14);
}

function renderBreadcrumb(){
  const bar=$('breadcrumbBar');
  const upBtn=$('btnUpDir');
  if(!bar)return;
  if(S.currentDir==='.'){
    bar.style.display='none';
    if(upBtn)upBtn.style.display='none';
    return;
  }
  bar.style.display='flex';
  if(upBtn)upBtn.style.display='';
  bar.innerHTML='';
  // Root segment
  const root=document.createElement('span');
  root.className='breadcrumb-seg breadcrumb-link';
  root.textContent='~';
  root.onclick=()=>loadDir('.');
  _bindWorkspaceMoveDropTarget(root,'.');
  _bindWorkspaceOsUploadDropTarget(root,'.');
  bar.appendChild(root);
  // Path segments
  const parts=S.currentDir.split('/');
  let accumulated='';
  for(let i=0;i<parts.length;i++){
    const sep=document.createElement('span');
    sep.className='breadcrumb-sep';sep.textContent='/';
    bar.appendChild(sep);
    accumulated+=(accumulated?'/':'')+parts[i];
    const seg=document.createElement('span');
    seg.textContent=parts[i];
    if(i<parts.length-1){
      seg.className='breadcrumb-seg breadcrumb-link';
      const target=accumulated;
      seg.onclick=()=>loadDir(target);
      _bindWorkspaceMoveDropTarget(seg,target);
      _bindWorkspaceOsUploadDropTarget(seg,target);
    } else {
      seg.className='breadcrumb-seg breadcrumb-current';
    }
    bar.appendChild(seg);
  }
}

const WORKSPACE_HIDDEN_FILE_NAMES=new Set([
  '.DS_Store','._.DS_Store','.AppleDouble','.Spotlight-V100','.Trashes','.fseventsd',
  'Thumbs.db','Desktop.ini','ehthumbs.db','$RECYCLE.BIN',
  '.directory','.git','.svn','.hg','node_modules','__pycache__',
  '.pytest_cache','.mypy_cache','.ruff_cache','.tox','.venv','venv'
]);
const WORKSPACE_HIDDEN_FILE_PREFIXES=['._','.Trash-'];
function _workspaceShouldHideEntry(item){
  if(!item||S.showHiddenWorkspaceFiles)return false;
  const name=String(item.name||'');
  if(!name)return false;
  if(WORKSPACE_HIDDEN_FILE_NAMES.has(name))return true;
  return WORKSPACE_HIDDEN_FILE_PREFIXES.some(prefix=>name.startsWith(prefix));
}
function _visibleWorkspaceEntries(entries){
  const list=Array.isArray(entries)?entries:[];
  return S.showHiddenWorkspaceFiles?list:list.filter(item=>!_workspaceShouldHideEntry(item));
}
const WORKSPACE_SORT_KEYS=['name-asc','name-desc','created-desc','modified-desc'];
const WORKSPACE_SORT_DEFAULT='name-asc';
function _normalizeWorkspaceSortKey(value){
  return WORKSPACE_SORT_KEYS.includes(value)?value:WORKSPACE_SORT_DEFAULT;
}
function _workspaceEntryRank(item){
  const rank=item&&item.workspace_sort_rank;
  return rank===0||rank===1||rank===2?rank:1;
}
function _workspaceEntryTimestampKey(item,field){
  const raw=item&&item[field];
  if(typeof raw==='string'){
    const value=raw.trim();
    if(!/^[+-]?\d+$/.test(value))return null;
    const negative=value[0]==='-';
    const digits=value.replace(/^[+-]/,'').replace(/^0+(?=\d)/,'');
    if(digits==='0')return '0';
    return negative?'-'+digits:digits;
  }
  if(typeof raw==='number') return Number.isInteger(raw)?String(raw):null;
  if(typeof raw==='bigint') return String(raw);
  return null;
}
function _compareWorkspaceTimestampDesc(a,b,field){
  const av=_workspaceEntryTimestampKey(a,field),bv=_workspaceEntryTimestampKey(b,field);
  if(av==null&&bv==null)return 0;
  if(av==null)return 1;
  if(bv==null)return -1;
  const an=av[0]==='-',bn=bv[0]==='-';
  if(an!==bn)return an?1:-1;
  const aa=an?av.slice(1):av,bb=bn?bv.slice(1):bv;
  if(aa.length!==bb.length)return an?aa.length-bb.length:bb.length-aa.length;
  return aa===bb?0:(an?(aa<bb?-1:1):(aa<bb?1:-1));
}
function _workspaceSortComparator(key){
  if(key==='name-desc') return (a,b)=>String(b.name||'').toLowerCase().localeCompare(String(a.name||'').toLowerCase());
  const field=key==='created-desc'?'birthtime_ns':'mtime_ns';
  return (a,b)=>_compareWorkspaceTimestampDesc(a,b,field);
}
function _workspaceCreatedSortAvailable(){return !!(S.session&&S.session.workspace&&S._workspaceBirthtimeSeen);}
function _effectiveWorkspaceSortKey(){
  const key=_normalizeWorkspaceSortKey(S.workspaceSortKey);
  return key==='created-desc'&&!_workspaceCreatedSortAvailable()?WORKSPACE_SORT_DEFAULT:key;
}
function _workspaceEntriesForRender(entries){
  const list=_visibleWorkspaceEntries(entries);
  const key=_effectiveWorkspaceSortKey();
  if(key===WORKSPACE_SORT_DEFAULT)return list;
  const cmp=_workspaceSortComparator(key);
  return list.slice().sort((a,b)=>{
    const rank=_workspaceEntryRank(a)-_workspaceEntryRank(b);
    return rank!==0?rank:cmp(a,b);
  });
}
function _resetWorkspaceBirthtimeSupport(scope=''){
  S._workspaceBirthtimeSeen=false;
  S._workspaceBirthtimeWorkspace=String(scope||'');
  _syncWorkspacePrefsIndicators();
  _syncWorkspaceSortMenuState();
}
function _syncWorkspaceBirthtimeSupportScope(scope=''){
  const next=String(scope||'');
  if(S._workspaceBirthtimeWorkspace!==next) _resetWorkspaceBirthtimeSupport(next);
}
function _noteWorkspaceBirthtimeSupport(entries){
  if(S._workspaceBirthtimeSeen)return;
  if((Array.isArray(entries)?entries:[]).some(e=>_workspaceEntryTimestampKey(e,'birthtime_ns')!==null)){
    S._workspaceBirthtimeSeen=true;
    S._workspaceBirthtimeWorkspace=String((S.session&&S.session.workspace)||S._workspaceBirthtimeWorkspace||'');
    _syncWorkspacePrefsIndicators();
    _syncWorkspaceSortMenuState();
  }
}
function _syncWorkspacePrefsIndicators(ind=$('workspaceHiddenIndicator'),dot=$('workspacePrefsDot')){
  if(ind){
    if(S.showHiddenWorkspaceFiles){ind.hidden=false;ind.removeAttribute('hidden');}
    else{ind.hidden=true;ind.setAttribute('hidden','');}
  }
  if(dot){
    const active=S.showHiddenWorkspaceFiles||_effectiveWorkspaceSortKey()!==WORKSPACE_SORT_DEFAULT;
    if(active){dot.hidden=false;dot.removeAttribute('hidden');}
    else{dot.hidden=true;dot.setAttribute('hidden','');}
  }
}
function _syncWorkspaceSortMenuState(menu=_workspacePrefsMenu){
  if(!menu||!menu.querySelectorAll)return;
  const active=_effectiveWorkspaceSortKey();
  const createdOk=_workspaceCreatedSortAvailable();
  menu.querySelectorAll('.workspace-prefs-item--radio').forEach(row=>{
    const input=row&&row.querySelector?row.querySelector('input[name="workspaceSortKey"]'):null;
    if(!input)return;
    const checked=input.value===active;
    const disabled=input.value==='created-desc'&&!createdOk;
    input.checked=checked;
    input.disabled=disabled;
    row.setAttribute('aria-checked',checked?'true':'false');
    row.setAttribute('aria-disabled',disabled?'true':'false');
    if(row.classList&&row.classList.toggle) row.classList.toggle('is-disabled',disabled);
    if(input.value==='created-desc'){
      const copy=row.querySelector('.workspace-prefs-copy');
      let meta=row.querySelector('.workspace-prefs-meta');
      if(disabled){
        if(!meta&&copy&&typeof document!=='undefined'){
          meta=document.createElement('span');
          meta.className='workspace-prefs-meta';
          copy.appendChild(meta);
        }
        if(meta)meta.textContent=typeof t==='function'?t('workspace_sort_created_unavailable'):'Creation time is not reported by this server or platform.';
      }else if(meta)meta.remove();
    }
  });
  if(menu===_workspacePrefsMenu&&typeof _workspacePrefsAnchor!=='undefined'&&_workspacePrefsAnchor&&typeof _positionWorkspacePrefsMenu==='function') _positionWorkspacePrefsMenu(_workspacePrefsAnchor);
}
function _syncWorkspaceHiddenToggle(){
  const el=$('workspaceShowHiddenFiles');
  if(el)el.checked=!!S.showHiddenWorkspaceFiles;
  _syncWorkspacePrefsIndicators($('workspaceHiddenIndicator'),$('workspacePrefsDot'));
}
function toggleWorkspaceHiddenFiles(value){
  S.showHiddenWorkspaceFiles=!!value;
  try{localStorage.setItem('agy-workspace-show-hidden-files',S.showHiddenWorkspaceFiles?'1':'0');}catch(_){}
  _syncWorkspaceHiddenToggle();
  renderFileTree();
}
try{S.showHiddenWorkspaceFiles=localStorage.getItem('agy-workspace-show-hidden-files')==='1';}catch(_){}
try{S.workspaceSortKey=_normalizeWorkspaceSortKey(localStorage.getItem('agy-workspace-sort-key'));}catch(_){S.workspaceSortKey=WORKSPACE_SORT_DEFAULT;}
function setWorkspaceSortKey(value){
  S.workspaceSortKey=_normalizeWorkspaceSortKey(value);
  try{localStorage.setItem('agy-workspace-sort-key',S.workspaceSortKey);}catch(_){ }
  _syncWorkspacePrefsIndicators();
  _syncWorkspaceSortMenuState();
  renderFileTree();
}


/* ── Global Window Exports ─────────────────────────────────────────────────── */
if (typeof window !== 'undefined') {
  window._structuredCodeMode = typeof _structuredCodeMode !== 'undefined' ? _structuredCodeMode : undefined;
  window._structuredCodeThreshold = typeof _structuredCodeThreshold !== 'undefined' ? _structuredCodeThreshold : undefined;
  window._structuredCodeShowTree = typeof _structuredCodeShowTree !== 'undefined' ? _structuredCodeShowTree : undefined;
  window.initTreeViews = typeof initTreeViews !== 'undefined' ? initTreeViews : undefined;
  window._buildTreeDOM = typeof _buildTreeDOM !== 'undefined' ? _buildTreeDOM : undefined;
  window.addCopyButtons = typeof addCopyButtons !== 'undefined' ? addCopyButtons : undefined;
  window._mermaidLoading = typeof _mermaidLoading !== 'undefined' ? _mermaidLoading : undefined;
  window._mermaidReady = typeof _mermaidReady !== 'undefined' ? _mermaidReady : undefined;
  window.loadDiffInline = typeof loadDiffInline !== 'undefined' ? loadDiffInline : undefined;
  window.CSV_MAX_SIZE = typeof CSV_MAX_SIZE !== 'undefined' ? CSV_MAX_SIZE : undefined;
  window._mediaSessionQuery = typeof _mediaSessionQuery !== 'undefined' ? _mediaSessionQuery : undefined;
  window._mediaSnapQuery = typeof _mediaSnapQuery !== 'undefined' ? _mediaSnapQuery : undefined;
  window._csvMediaUrl = typeof _csvMediaUrl !== 'undefined' ? _csvMediaUrl : undefined;
  window.buildCsvTablePreview = typeof buildCsvTablePreview !== 'undefined' ? buildCsvTablePreview : undefined;
  window._csvPreviewErrorHtml = typeof _csvPreviewErrorHtml !== 'undefined' ? _csvPreviewErrorHtml : undefined;
  window.loadCsvInline = typeof loadCsvInline !== 'undefined' ? loadCsvInline : undefined;
  window.loadExcalidrawInline = typeof loadExcalidrawInline !== 'undefined' ? loadExcalidrawInline : undefined;
  window._excalidrawScriptLoaded = typeof _excalidrawScriptLoaded !== 'undefined' ? _excalidrawScriptLoaded : undefined;
  window._renderExcalidrawCanvases = typeof _renderExcalidrawCanvases !== 'undefined' ? _renderExcalidrawCanvases : undefined;
  window._pdfjsReady = typeof _pdfjsReady !== 'undefined' ? _pdfjsReady : undefined;
  window.loadPdfInline = typeof loadPdfInline !== 'undefined' ? loadPdfInline : undefined;
  window.loadHtmlInline = typeof loadHtmlInline !== 'undefined' ? loadHtmlInline : undefined;
  window._cleanMermaidDOM = typeof _cleanMermaidDOM !== 'undefined' ? _cleanMermaidDOM : undefined;
  window._isMermaidErrorSvg = typeof _isMermaidErrorSvg !== 'undefined' ? _isMermaidErrorSvg : undefined;
  window.renderMermaidBlocks = typeof renderMermaidBlocks !== 'undefined' ? renderMermaidBlocks : undefined;
  window._katexLoading = typeof _katexLoading !== 'undefined' ? _katexLoading : undefined;
  window._katexReady = typeof _katexReady !== 'undefined' ? _katexReady : undefined;
  window._isStreamingEquationPending = typeof _isStreamingEquationPending !== 'undefined' ? _isStreamingEquationPending : undefined;
  window.renderKatexBlocks = typeof renderKatexBlocks !== 'undefined' ? renderKatexBlocks : undefined;
  window._thinkingMarkup = typeof _thinkingMarkup !== 'undefined' ? _thinkingMarkup : undefined;
  window._renderThinkingInto = typeof _renderThinkingInto !== 'undefined' ? _renderThinkingInto : undefined;
  window.finalizeThinkingCard = typeof finalizeThinkingCard !== 'undefined' ? finalizeThinkingCard : undefined;
  window.appendThinking = typeof appendThinking !== 'undefined' ? appendThinking : undefined;
  window.updateThinking = typeof updateThinking !== 'undefined' ? updateThinking : undefined;
  window.removeThinking = typeof removeThinking !== 'undefined' ? removeThinking : undefined;
  window.fileIcon = typeof fileIcon !== 'undefined' ? fileIcon : undefined;
  window.renderBreadcrumb = typeof renderBreadcrumb !== 'undefined' ? renderBreadcrumb : undefined;
  window.WORKSPACE_HIDDEN_FILE_NAMES = typeof WORKSPACE_HIDDEN_FILE_NAMES !== 'undefined' ? WORKSPACE_HIDDEN_FILE_NAMES : undefined;
  window.WORKSPACE_HIDDEN_FILE_PREFIXES = typeof WORKSPACE_HIDDEN_FILE_PREFIXES !== 'undefined' ? WORKSPACE_HIDDEN_FILE_PREFIXES : undefined;
  window._workspaceShouldHideEntry = typeof _workspaceShouldHideEntry !== 'undefined' ? _workspaceShouldHideEntry : undefined;
  window._visibleWorkspaceEntries = typeof _visibleWorkspaceEntries !== 'undefined' ? _visibleWorkspaceEntries : undefined;
  window.WORKSPACE_SORT_KEYS = typeof WORKSPACE_SORT_KEYS !== 'undefined' ? WORKSPACE_SORT_KEYS : undefined;
  window.WORKSPACE_SORT_DEFAULT = typeof WORKSPACE_SORT_DEFAULT !== 'undefined' ? WORKSPACE_SORT_DEFAULT : undefined;
  window._normalizeWorkspaceSortKey = typeof _normalizeWorkspaceSortKey !== 'undefined' ? _normalizeWorkspaceSortKey : undefined;
  window._workspaceEntryRank = typeof _workspaceEntryRank !== 'undefined' ? _workspaceEntryRank : undefined;
  window._workspaceEntryTimestampKey = typeof _workspaceEntryTimestampKey !== 'undefined' ? _workspaceEntryTimestampKey : undefined;
  window._compareWorkspaceTimestampDesc = typeof _compareWorkspaceTimestampDesc !== 'undefined' ? _compareWorkspaceTimestampDesc : undefined;
  window._workspaceSortComparator = typeof _workspaceSortComparator !== 'undefined' ? _workspaceSortComparator : undefined;
  window._workspaceCreatedSortAvailable = typeof _workspaceCreatedSortAvailable !== 'undefined' ? _workspaceCreatedSortAvailable : undefined;
  window._effectiveWorkspaceSortKey = typeof _effectiveWorkspaceSortKey !== 'undefined' ? _effectiveWorkspaceSortKey : undefined;
  window._workspaceEntriesForRender = typeof _workspaceEntriesForRender !== 'undefined' ? _workspaceEntriesForRender : undefined;
  window._resetWorkspaceBirthtimeSupport = typeof _resetWorkspaceBirthtimeSupport !== 'undefined' ? _resetWorkspaceBirthtimeSupport : undefined;
  window._syncWorkspaceBirthtimeSupportScope = typeof _syncWorkspaceBirthtimeSupportScope !== 'undefined' ? _syncWorkspaceBirthtimeSupportScope : undefined;
  window._noteWorkspaceBirthtimeSupport = typeof _noteWorkspaceBirthtimeSupport !== 'undefined' ? _noteWorkspaceBirthtimeSupport : undefined;
  window._syncWorkspacePrefsIndicators = typeof _syncWorkspacePrefsIndicators !== 'undefined' ? _syncWorkspacePrefsIndicators : undefined;
  window._syncWorkspaceSortMenuState = typeof _syncWorkspaceSortMenuState !== 'undefined' ? _syncWorkspaceSortMenuState : undefined;
  window._syncWorkspaceHiddenToggle = typeof _syncWorkspaceHiddenToggle !== 'undefined' ? _syncWorkspaceHiddenToggle : undefined;
  window.toggleWorkspaceHiddenFiles = typeof toggleWorkspaceHiddenFiles !== 'undefined' ? toggleWorkspaceHiddenFiles : undefined;
  window.setWorkspaceSortKey = typeof setWorkspaceSortKey !== 'undefined' ? setWorkspaceSortKey : undefined;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    _structuredCodeMode,
    _structuredCodeThreshold,
    _structuredCodeShowTree,
    initTreeViews,
    _buildTreeDOM,
    addCopyButtons,
    _mermaidLoading,
    _mermaidReady,
    loadDiffInline,
    CSV_MAX_SIZE,
    _mediaSessionQuery,
    _mediaSnapQuery,
    _csvMediaUrl,
    buildCsvTablePreview,
    _csvPreviewErrorHtml,
    loadCsvInline,
    loadExcalidrawInline,
    _excalidrawScriptLoaded,
    _renderExcalidrawCanvases,
    _pdfjsReady,
    loadPdfInline,
    loadHtmlInline,
    _cleanMermaidDOM,
    _isMermaidErrorSvg,
    renderMermaidBlocks,
    _katexLoading,
    _katexReady,
    _isStreamingEquationPending,
    renderKatexBlocks,
    _thinkingMarkup,
    _renderThinkingInto,
    finalizeThinkingCard,
    appendThinking,
    updateThinking,
    removeThinking,
    fileIcon,
    renderBreadcrumb,
    WORKSPACE_HIDDEN_FILE_NAMES,
    WORKSPACE_HIDDEN_FILE_PREFIXES,
    _workspaceShouldHideEntry,
    _visibleWorkspaceEntries,
    WORKSPACE_SORT_KEYS,
    WORKSPACE_SORT_DEFAULT,
    _normalizeWorkspaceSortKey,
    _workspaceEntryRank,
    _workspaceEntryTimestampKey,
    _compareWorkspaceTimestampDesc,
    _workspaceSortComparator,
    _workspaceCreatedSortAvailable,
    _effectiveWorkspaceSortKey,
    _workspaceEntriesForRender,
    _resetWorkspaceBirthtimeSupport,
    _syncWorkspaceBirthtimeSupportScope,
    _noteWorkspaceBirthtimeSupport,
    _syncWorkspacePrefsIndicators,
    _syncWorkspaceSortMenuState,
    _syncWorkspaceHiddenToggle,
    toggleWorkspaceHiddenFiles,
    setWorkspaceSortKey,
  };
}
