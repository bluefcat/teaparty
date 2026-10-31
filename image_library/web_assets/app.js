/* MORI Studio -- no dependencies or remote scripts. All operations stay on localhost. */
(() => {
  'use strict';
  const token = document.querySelector('meta[name=mori-token]').content;
  const $ = id => document.getElementById(id);
  const icons = {
    gallery:'<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8.5" cy="8.5" r="1.6"/><path d="m3 17 5.4-5.4 3.5 3.5 3-3L21 18"/>',
    folder:'<path d="M3 6a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    palette:'<path d="M12 3a9 9 0 1 0 0 18h1.3a2 2 0 0 0 1.6-3.2 1.5 1.5 0 0 1 1.2-2.4H18a4 4 0 0 0 4-4A10 10 0 0 0 12 3z"/><circle cx="7" cy="11" r="1"/><circle cx="10" cy="7" r="1"/><circle cx="15" cy="7" r="1"/><circle cx="18" cy="11" r="1"/>',
    history:'<path d="M3 12a9 9 0 1 1 2.3 6M3 3v6h6M12 7v5l3 2"/>',
    settings:'<path d="M12 3 13.6 5l2.5-.2.7 2.5 2.2 1.4-1 2.3 1 2.3-2.2 1.4-.7 2.5-2.5-.2L12 19l-1.6-2-2.5.2-.7-2.5L5 13.3l1-2.3-1-2.3 2.2-1.4.7-2.5 2.5.2z" transform="translate(0 1)"/><circle cx="12" cy="12" r="3"/>',
    search:'<circle cx="11" cy="11" r="7"/><path d="m16 16 5 5"/>',plus:'<path d="M12 5v14M5 12h14"/>',x:'<path d="M5 5 19 19M19 5 5 19"/>',
    'arrow-left':'<path d="m15 18-6-6 6-6M9 12h12"/>','arrow-right':'<path d="m9 18 6-6-6-6M15 12H3"/>',
    refresh:'<path d="M20 7V3l-3 3a8 8 0 1 0 3 6M20 3v5h-5"/>',rotate:'<path d="M3 11a9 9 0 1 1 3 6M3 4v7h7"/>',
    download:'<path d="M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4"/>',external:'<path d="M14 3h7v7M21 3l-11 11M21 14v6H4V4h6"/>',
    layers:'<path d="m12 3 9 5-9 5-9-5 9-5zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',trash:'<path d="M4 7h16M9 7V4h6v3M7 7l1 14h8l1-14M10 11v6M14 11v6"/>',
    pencil:'<path d="m3 21 4-.9L21 6l-3-3L3.9 17.1zM16 5l3 3"/>',eraser:'<path d="m20 13-7-8a2 2 0 0 0-3 0L3 12a2 2 0 0 0 0 3l5 5h11M7 17l8-8"/>',check:'<path d="m4 12 5 5 11-11"/>'
  };
  const icon = name => `<svg viewBox="0 0 24 24" aria-hidden="true">${icons[name] || icons.gallery}</svg>`;
  document.querySelectorAll('[data-icon]').forEach(el => { el.innerHTML = icon(el.dataset.icon); });
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = n => Number(n||0).toLocaleString('ko-KR');
  const basename = path => String(path||'').split(/[\\/]/).filter(Boolean).pop() || String(path||'');
  const media = (id, full=false, stamp=0) => `/api/media/${id}?token=${encodeURIComponent(token)}&full=${full?1:0}&v=${stamp||0}`;
  const state = {
    query:'',mode:'tag',sort:'newest',digest:null,points:{},tolerance:35,
    paint:'#E8A5C8',tool:'draw',images:[],total:0,page:0,limit:100,loaded:false,loading:false,searchSeq:0,
    allTags:[],resolvedTags:[],selectedId:null,related:[],relatedSourceId:null,activeTab:'library',
    serviceState:null,revision:0,searchCache:new Map(),lastSearch:'',paintDown:false,collapsed:false,refreshAt:0,dismissedScanRevision:null,
  };
  // Image visit history is distinct from browser navigation history and analysis logs.
  // Store only metadata; pictures remain in the local managed image library.
  const VIEWED_KEY='tea-party-recent-image-visits-v1';
  const MAX_VIEWED=30;
  function readViewedHistory(){
    try {
      const entries=JSON.parse(localStorage.getItem(VIEWED_KEY)||'[]');
      if(!Array.isArray(entries))return [];
      return entries.filter(x=>x && Number.isSafeInteger(x.id) && x.id>0 && typeof x.name==='string')
        .slice(0,MAX_VIEWED);
    } catch(e) { return []; }
  }
  let viewedHistory=readViewedHistory();
  function persistViewedHistory(){
    try { localStorage.setItem(VIEWED_KEY,JSON.stringify(viewedHistory)); }
    catch(e) { console.warn('방문 기록을 저장하지 못했습니다:',e); }
  }
  function renderViewedHistory(){
    const list=$('sideHistory');
    list.replaceChildren();
    if(!viewedHistory.length){
      const empty=document.createElement('p');
      empty.className='side-visit-empty';
      empty.textContent='이미지를 열어보면 여기에 표시됩니다.';
      list.appendChild(empty);
      return;
    }
    for(const entry of viewedHistory){
      const button=document.createElement('button');
      button.type='button';button.className='side-visit';
      if(state.selectedId===entry.id)button.classList.add('is-current');
      button.dataset.visitedImage=entry.id;
      button.title=entry.name;
      const img=document.createElement('img');
      img.src=media(entry.id,false,entry.mtime);
      img.alt='';img.loading='lazy';
      img.addEventListener('error',()=>{img.hidden=true;});
      const text=document.createElement('span');
      text.className='side-visit-title';text.textContent=entry.name;
      button.append(img,text);list.appendChild(button);
    }
  }
  function recordImageVisit(row){
    const id=Number(row.id);
    if(!Number.isSafeInteger(id)||id<1)return;
    viewedHistory=[{id,name:String(row.name||`이미지 ${id}`),mtime:row.mtime||0},
      ...viewedHistory.filter(item=>item.id!==id)].slice(0,MAX_VIEWED);
    persistViewedHistory();renderViewedHistory();
  }
  function removeViewedImage(id){
    viewedHistory=viewedHistory.filter(item=>item.id!==Number(id));
    persistViewedHistory();renderViewedHistory();
  }
  let detailLoadVersion=0;
  function showDetailPlaceholder(message='이미지를 불러오는 중',failed=false){
    const image=$('detailImage');
    image.onload=null;image.onerror=null;image.hidden=true;image.removeAttribute('src');
    image.alt='';
    $('detailPlaceholder').setAttribute('aria-label',message);
    $('detailPlaceholder').classList.toggle('failed',failed);
    $('detailPlaceholder').hidden=false;
    image.parentElement.setAttribute('aria-busy',String(!failed));
  }
  let scrollBeforeDetail=0;
  let wheelHue=0, wheelWeights=[1,0,0], wheelDrag=null;
  let pollTimer;
  async function api(path, body=null, method='POST') {
    const r = await fetch(path, { method: body === null && method === 'GET' ? 'GET' : 'POST',
      headers: {'X-MORI-Token':token, ...(body!==null?{'Content-Type':'application/json'}:{})},
      ...(body!==null?{body:JSON.stringify(body)}:{}) });
    let data;
    try { data = await r.json(); } catch(e) { throw new Error('서버의 응답을 읽을 수 없습니다.'); }
    if (!r.ok || data.error) throw new Error(data.error || `오류 ${r.status}`);
    return data;
  }
  async function apiGet(path){return api(path,null,'GET');}
  function toast(message, type='success') {
    const node=document.createElement('div');node.className=`toast ${type}`;node.textContent=message;
    $('toasts').appendChild(node);setTimeout(()=>node.remove(),4300);
  }
  const fail = e => { console.error(e);toast(e.message||String(e),'error'); };
  function currentSnapshot(){
    return {query:state.query,mode:state.mode,sort:state.sort,digest:state.digest,
      points:JSON.parse(JSON.stringify(state.points)),tolerance:state.tolerance,
      selectedId:state.selectedId,relatedSourceId:state.relatedSourceId,
      scroll:state.selectedId?scrollBeforeDetail:window.scrollY,
      detailScroll:state.selectedId?window.scrollY:0,activeTab:state.activeTab};
  }
  // The browser owns back/forward navigation. Before leaving a view, update its
  // history entry; after synchronous navigation has changed state, push the new
  // view. No custom back/forward stack or Alt+arrow interception is necessary.
  let historyCommitPending=false;
  let restoringHistory=false;
  let restorationVersion=0;
  let scrollSaveTimer=null;
  function routeURL(snap){
    const url=new URL(window.location.href);
    url.searchParams.delete('view');url.searchParams.delete('image');
    if(snap.selectedId){url.searchParams.set('view','image');url.searchParams.set('image',String(snap.selectedId));}
    return url.pathname+url.search+url.hash;
  }
  function historyRecord(snap){return {mori:true,snapshot:snap};}
  function pushHistory(){
    if(restoringHistory)return;
    if(history.state?.mori)history.replaceState(historyRecord(currentSnapshot()),'',window.location.href);
    if(historyCommitPending)return;
    historyCommitPending=true;
    queueMicrotask(()=>{
      historyCommitPending=false;
      if(restoringHistory)return;
      const snap=currentSnapshot();
      history.pushState(historyRecord(snap),'',routeURL(snap));
    });
  }
  async function restoreBrowserView(snap){
    if(!snap)return;
    const version=++restorationVersion;
    restoringHistory=true;
    // Cancel any stale requests and discard transient drawers/dialogs.
    ++state.searchSeq;
    closeAllModals();
    state.query=snap.query||'';state.mode=snap.mode||'tag';
    state.sort=snap.sort||'newest';state.digest=snap.digest||null;
    state.points=structuredClone(snap.points||{});state.tolerance=snap.tolerance??35;
    state.selectedId=null;state.relatedSourceId=null;state.activeTab='library';
    $('searchInput').value=state.query;$('mode').value=state.mode;$('sortOrder').value=state.sort;
    $('tolerance').value=state.tolerance;$('toleranceValue').textContent=state.tolerance;
    renderPaint();restoreGallery();
    try{
      await doSearch();
      if(version!==restorationVersion)return;
      if(snap.relatedSourceId){
        const source=await apiGet(`/api/detail?id=${snap.relatedSourceId}`);
        if(version!==restorationVersion)return;
        state.relatedSourceId=snap.relatedSourceId;
        renderSimilarListing(source.similar.slice(0,24));
      }
      if(snap.selectedId){
        await openDetail(snap.selectedId,false);
        scrollBeforeDetail=snap.scroll||0;
        requestAnimationFrame(()=>window.scrollTo(0,snap.detailScroll||0));
      }else{
        requestAnimationFrame(()=>window.scrollTo(0,snap.scroll||0));
      }
    }finally{if(version===restorationVersion)restoringHistory=false;}
  }
  window.addEventListener('popstate',e=>{
    if(e.state?.mori)restoreBrowserView(e.state.snapshot).catch(fail);
  });
  window.addEventListener('scroll',()=>{
    if(restoringHistory||historyCommitPending)return;
    clearTimeout(scrollSaveTimer);
    scrollSaveTimer=setTimeout(()=>{
      if(history.state?.mori&&!restoringHistory&&!historyCommitPending)
        history.replaceState(historyRecord(currentSnapshot()),'',window.location.href);
    },120);
  },{passive:true});
  function searchPayload(page){
    return {query:state.query,mode:state.mode,points:Object.entries(state.points)
      .map(([pos,rgb])=>{const [x,y]=pos.split(',').map(Number);return {x,y,rgb};}),
      tolerance:state.tolerance,digest:state.digest,sort:state.sort,page,limit:state.limit};
  }
  const cacheKey=()=>JSON.stringify(searchPayload(0));
  async function doSearch({push=false,preserve=false,restoreScroll=null}={}){
    if(push)pushHistory();
    state.activeTab='library';state.relatedSourceId=null;setNav('library');
    state.query=$('searchInput').value.trim();state.mode=$('mode').value;
    if(!preserve){state.images=[];state.page=0;state.total=0;state.loaded=false;$('gallery').replaceChildren();}
    let seq=++state.searchSeq, key=cacheKey();
    state.loading=true;
    try{
      const cached=state.searchCache.get(key);
      const result=cached||await api('/api/search',searchPayload(0));
      if(seq!==state.searchSeq)return;
      if(!cached){state.searchCache.set(key,result);if(state.searchCache.size>24)state.searchCache.delete(state.searchCache.keys().next().value);}
      state.images=result.images;state.total=result.total;state.page=0;state.loaded=result.images.length>=result.total;
      state.allTags=result.tags||[];state.resolvedTags=result.resolved_tags||[];
      renderToolbar();
      if(state.digest){
        const count=result.duplicate_count??Math.max(0,result.total-1);
        $('pageSubtitle').textContent=count ? `크기나 압축 방식이 달라도 비슷한 이미지 ${fmt(count)}장이 발견되었습니다. (검토 후보)` : '현재 이미지 외에 일치하는 이미지가 없습니다.';
      }
      renderImages(true);renderEmpty(result.message);renderFooter();
      if(restoreScroll!==null)requestAnimationFrame(()=>window.scrollTo(0,restoreScroll));
      else if(!preserve)window.scrollTo({top:0,behavior:'instant'});
    }catch(e){if(seq===state.searchSeq){fail(e);renderEmpty('검색 중 문제가 발생했습니다.');}}
    finally{if(seq===state.searchSeq)state.loading=false;}
  }
  async function loadMore(force=false){
    if(state.loading||state.loaded||state.activeTab!=='library'||(state.selectedId&&!force))return;
    state.loading=true;
    const seq=state.searchSeq;const next=state.page+1;
    try{
      const result=await api('/api/search',searchPayload(next));
      if(seq!==state.searchSeq)return;
      state.images.push(...result.images);state.page=next;state.loaded=state.images.length>=result.total||!result.images.length;
      renderImages(false);
    }catch(e){fail(e);}finally{state.loading=false;}
  }
  function renderToolbar(){
    $('resultCount').textContent=`${fmt(state.total)} IMAGES`;
    $('resultWord').textContent=`검색 결과 ${fmt(state.total)}개`;
    $('pageTitle').textContent=state.digest?'같은 해시 이미지':(state.query?'검색 결과':'이미지 라이브러리');
    $('pageSubtitle').textContent=state.query?`“${state.query}”에 대한 결과입니다.`:'';
    $('sortOrder').value=state.sort;
    const filters=[];
    state.resolvedTags.forEach((tag,i)=>filters.push(`<span class="filter-chip ${i%3===2?'pink':''}">${esc(tag)} <button data-remove-tag="${esc(tag)}" aria-label="태그 제거">×</button></span>`));
    if(state.digest)filters.push('<span class="filter-chip">같은 해시 <button data-clear-digest aria-label="해시 초기화">×</button></span>');
    if(Object.keys(state.points).length)filters.push(`<span class="filter-chip">색상 ${Object.keys(state.points).length}칸 · 오차 ${state.tolerance} <button data-clear-painted aria-label="색상 초기화">×</button></span>`);
    $('activeFilters').innerHTML=filters.join('')||'<span class="soft-hint">태그 또는 색상으로 필터링해 보세요</span>';
    $('resetFilters').hidden=!(state.query||state.digest||Object.keys(state.points).length);
  }
  // Stable top-to-bottom masonry: native CSS columns reshuffle old images
  // whenever the next search page arrives. Keep permanent vertical columns
  // instead and reserve each photo's original aspect ratio before it loads.
  const masonryLayouts=new WeakMap();
  function masonryColumnCount(container){
    const width=container.clientWidth||900;
    // Keep the existing desktop column count and avoid columns shrinking in
    // number when a narrow viewport becomes slightly wider.
    if(width<245)return 1;
    if(width<445)return 2;
    if(width<640)return 3;
    if(width<835)return 4;
    return Math.max(4,Math.min(8,Math.floor((width+14)/(185+14))));
  }
  function arrangeMasonry(container, nodes, append=false){
    const count=masonryColumnCount(container);
    let layout=masonryLayouts.get(container);
    if(!append || !layout || layout.count!==count){
      const existing=append?[...container.querySelectorAll('.pin')]:[];
      container.replaceChildren();
      container.style.setProperty('--masonry-columns',String(count));
      const columns=Array.from({length:count},()=>{
        const col=document.createElement('div');col.className='masonry-column';container.appendChild(col);return col;
      });
      layout={count,columns,heights:Array(count).fill(0)};
      masonryLayouts.set(container,layout);
      nodes=[...existing,...nodes].sort((a,b)=>Number(a.dataset.order)-Number(b.dataset.order));
    }
    const columnWidth=Math.max(40,(container.clientWidth-(count-1)*14)/count);
    for(const pin of nodes){
      const shortest=layout.heights.indexOf(Math.min(...layout.heights));
      layout.columns[shortest].appendChild(pin);
      const aspect=Number(pin.dataset.aspect)||1;
      layout.heights[shortest]+=columnWidth/aspect+14;
    }
  }
  function reflowMasonry(container){
    if(!container || !container.querySelector('.pin'))return;
    if(masonryLayouts.get(container)?.count===masonryColumnCount(container))return;
    arrangeMasonry(container,[...container.querySelectorAll('.pin')],false);
  }
  function createPin(item,index,related=false){
    const pin=document.createElement('article');
    pin.className='pin placeholder'+(item.status==='error'?' pin-error':'');
    pin.tabIndex=0;pin.dataset.id=item.id;pin.dataset.order=index;
    pin.setAttribute('role','button');pin.setAttribute('aria-label',`${item.name} 상세 보기`);
    const width=Math.max(1,Number(item.width)||3),height=Math.max(1,Number(item.height)||4);
    pin.dataset.aspect=width/height;
    pin.innerHTML=`<img loading="${!related&&index<18?'eager':'lazy'}" decoding="async" width="${width}" height="${height}" src="${media(item.id,false,item.mtime)}" alt="${esc(item.name)}"><div class="pin-shade"><div class="pin-top">${item.duplicate?'<span>중복</span>':''}</div><div class="pin-bottom"><strong>${esc(item.name)}</strong><small>${related?`태그 유사도 ${Math.round(item.similarity)}%`:`${item.tags.length} tags · ${item.width} × ${item.height}`}</small></div></div><div class="pin-error-message" hidden><strong>미리보기를 열 수 없습니다</strong><small>${esc(item.name)}</small></div>`;
    const img=pin.querySelector('img');
    img.addEventListener('load',()=>{pin.classList.remove('placeholder');pin.classList.remove('image-failed');});
    img.addEventListener('error',()=>{pin.classList.remove('placeholder');pin.classList.add('image-failed');img.hidden=true;pin.querySelector('.pin-error-message').hidden=false;});
    if(img.complete&&img.naturalWidth)pin.classList.remove('placeholder');
    pin.addEventListener('click',()=>openDetail(item.id));
    pin.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();openDetail(item.id);}});
    pin.addEventListener('contextmenu',e=>{e.preventDefault();openDetail(item.id);});
    return pin;
  }
  function renderImages(reset=true){
    const list=reset?state.images:state.images.slice(state.page*state.limit);
    const offset=reset?0:state.page*state.limit;
    arrangeMasonry($('gallery'),list.map((item,i)=>createPin(item,offset+i)),!reset);
  }
  function renderRelatedImages(images){
    $('similarCount').textContent=`${images.length}장`;
    $('seeSimilar').hidden=!images.length;
    const related=$('similarGrid');
    arrangeMasonry(related,images.map((item,i)=>createPin(item,i,true)));
    if(!images.length)related.innerHTML='<p class="soft-hint">공통 태그가 있는 이미지가 없습니다.</p>';
  }
  function renderEmpty(message=''){
    $('emptyState').hidden=state.total>0||state.activeTab!=='library';
    if(state.total>0)return;
    const emptyLibrary=!(state.serviceState?.total);
    $('emptyTitle').textContent=emptyLibrary?'아직 여기에 이미지가 없습니다':'일치하는 이미지를 찾지 못했습니다';
    $('emptyDescription').textContent=message|| (emptyLibrary?'폴더를 추가하고 분석하면 이곳에 이미지 컬렉션이 만들어집니다.':'태그와 색상 조건을 조금 바꿔서 다시 검색해 보세요.');
    $('emptyAction').textContent=emptyLibrary?'+ 폴더 추가하기':'검색 조건 초기화';
  }
  function renderFooter(){$('sideCount').textContent=fmt(state.serviceState?.total||0);}
  function setNav(tab){
    document.querySelectorAll('.nav-item').forEach(el=>el.classList.toggle('active',el.dataset.nav===tab));
  }
  function restoreGallery(){ $('gallery').className='masonry';$('filterbar').style.display='flex'; }
  async function openDetail(id,push=true){
    const imageRequest=++detailLoadVersion;
    if(push)pushHistory();
    const row=state.images.find(x=>x.id===id);
    if(!state.selectedId)scrollBeforeDetail=window.scrollY;
    state.selectedId=id;
    $('detailModal').hidden=false;
    document.body.classList.add('detail-open');
    window.scrollTo({top:0,behavior:'instant'});
    showDetailPlaceholder();$('detailName').textContent='불러오는 중…';
    try{const result=await apiGet(`/api/detail?id=${id}`);if(state.selectedId!==id||imageRequest!==detailLoadVersion)return;
      const data=result.image;state.related=result.similar;
      recordImageVisit(data);
      const image=$('detailImage');
      image.onload=()=>{
        if(imageRequest!==detailLoadVersion||state.selectedId!==id)return;
        image.alt=data.name||'상세 이미지';image.hidden=false;
        $('detailPlaceholder').hidden=true;
        image.parentElement.setAttribute('aria-busy','false');
      };
      image.onerror=()=>{
        if(imageRequest!==detailLoadVersion||state.selectedId!==id)return;
        showDetailPlaceholder('이미지를 표시할 수 없습니다',true);
        $('detailStatus').textContent='원본 미리보기를 열지 못했습니다. 파일 경로를 확인해 주세요.';
      };
      image.src=media(id,true,data.mtime);
      $('detailName').textContent=data.name;
      $('detailMeta').textContent=`${fmt(data.width)} × ${fmt(data.height)}  ·  ${(data.size/1048576).toFixed(1)} MB  ·  ${data.path}`;
      $('detailTagCount').textContent=`${data.tags.length} TAGS`;
      $('detailTags').innerHTML=data.tags.map(tag=>`<span class="detail-tag ${tag==='중복'?'auto':''}"><button class="detail-tag-search" data-search-detail-tag="${esc(tag)}" title="${esc(tag)} 태그로 이미지 검색">${esc(tag)}</button>${tag!=='중복'?`<button class="detail-tag-remove" data-remove-detail-tag="${esc(tag)}" title="태그 제거" aria-label="태그 ${esc(tag)} 삭제">×</button>`:''}</span>`).join('');
      renderRelatedImages(result.similar.slice(0,24));
      $('detailStatus').textContent=data.duplicate?'중복 해시 감지됨':(data.status==='error'?'분석 오류: '+data.error:'원본 이미지는 변경되지 않습니다.');
      const index=state.images.findIndex(x=>x.id===id);
      $('detailPosition').textContent=index<0?'IMAGE DETAILS':`${index+1} / ${fmt(state.total)} IMAGES`;
      $('prevImage').disabled=index<=0;$('nextImage').disabled=index<0||index>=state.images.length-1;
      $('sameHash').disabled=!data.hash;
      $('newTags').value='';
    }catch(e){if(imageRequest!==detailLoadVersion)return;fail(e);closeDetail(false);}
  }
  function closeDetail(push=false){
    if($('detailModal').hidden)return;
    ++detailLoadVersion;
    if(push)pushHistory();state.selectedId=null;$('detailModal').hidden=true;
    document.body.classList.remove('detail-open');window.scrollTo(0,scrollBeforeDetail);
    renderViewedHistory();
  }
  async function neighbor(step){
    const i=state.images.findIndex(x=>x.id===state.selectedId);
    if(i<0)return;const target=i+step;if(target<0)return;
    if(target>=state.images.length){await loadMore(true);if(target>=state.images.length)return;}
    await openDetail(state.images[target].id);
  }
  async function addTags(){
    const tags=$('newTags').value.split(',').map(t=>t.trim()).filter(Boolean);
    if(!tags.length)return;
    try{const id=state.selectedId;await api('/api/edit_tags',{id,add:tags});
      invalidateSearch();await openDetail(id,false);toast('태그를 추가했습니다.');
    }catch(e){fail(e);}
  }
  async function searchDetailTag(tag){
    pushHistory();
    state.query=tag;state.mode='tag';state.digest=null;state.points={};
    $('searchInput').value=tag;$('mode').value='tag';renderPaint();
    closeDetail(false);restoreGallery();
    await doSearch();
  }
  async function removeTag(tag){
    if(!state.selectedId)return;
    try{const id=state.selectedId;await api('/api/edit_tags',{id,remove:[tag]});
      invalidateSearch();await openDetail(id,false);toast('태그를 제거했습니다.');
    }catch(e){fail(e);}
  }
  function invalidateSearch(){state.searchCache.clear();}
  async function deleteSelected(){
    const id=state.selectedId;if(!id)return;
    const item=state.images.find(x=>x.id===id);
    if(!window.confirm(`${item?.path||item?.name||id}\n\n프로그램에 저장된 이미지 복사본을 휴지통으로 이동할까요?\n이미지, 태그, 썸네일과 가져오기 기록이 함께 삭제됩니다.\n외부 원본 파일은 그대로 유지됩니다.`))return;
    try{await api('/api/delete',{id});removeViewedImage(id);toast('이미지를 휴지통으로 이동했습니다.');closeDetail();invalidateSearch();await doSearch({preserve:false});}
    catch(e){fail(e);}
  }
  async function launchOriginal(reveal){try{await api('/api/open',{id:state.selectedId,reveal});}catch(e){fail(e);}}
  async function sameHash(){
    const id=state.selectedId;const data=state.images.find(x=>x.id===id)|| (await apiGet(`/api/detail?id=${id}`)).image;
    if(!data?.hash)return;
    pushHistory();closeDetail();state.digest=data.hash;state.query='';$('searchInput').value='';
    state.points={};await doSearch();
  }
  function renderSimilarListing(best){
    restoreGallery();state.activeTab='library';setNav('library');
    state.images=best;state.total=best.length;state.loaded=true;state.page=0;
    renderImages(true);$('pageTitle').textContent='태그가 비슷한 이미지';$('resultCount').textContent=`${best.length} IMAGES`;
    $('pageSubtitle').textContent='현재 이미지와 공통 태그가 있는 이미지입니다.';
    $('resultWord').textContent=`유사 이미지 ${best.length}개`;$('emptyState').hidden=true;
  }
  async function seeSimilar(){
    if(!state.related.length)return;
    const id=state.selectedId,best=state.related.slice(0,24);
    pushHistory();closeDetail();state.relatedSourceId=id;
    renderSimilarListing(best);
  }
  function closeAllModals(){for(const id of ['detailModal','settingsModal','logModal','folderModal'])$(id).hidden=true;document.body.classList.remove('detail-open');closeColor();state.selectedId=null;document.body.style.overflow='';}
  function showModal(id){$(id).hidden=false;document.body.style.overflow='hidden';}
  function closeModal(id){$(id).hidden=true;document.body.style.overflow='';}
  function openColor(){$('colorOverlay').hidden=false;$('colorDrawer').classList.add('open');$('colorDrawer').setAttribute('aria-hidden','false');document.body.style.overflow='hidden';setNav('colors');}
  function closeColor(){$('colorOverlay').hidden=true;$('colorDrawer').classList.remove('open');$('colorDrawer').setAttribute('aria-hidden','true');document.body.style.overflow='';setNav(state.activeTab);}
  // An actual HSV hue ring + hue/white/black triangle, matching MORI's
  // original standard color picker instead of clipped CSS gradients.
  const WHEEL_SIZE=240, WHEEL_CENTER=120, WHEEL_OUTER=113, WHEEL_INNER=90;
  const TRIANGLE=[[203,120],[78,47],[78,193]]; // hue, white, black
  const clamp=(v,a=0,b=1)=>Math.max(a,Math.min(b,v));
  function pureHue(h){
    const k=(n)=>(n+h*6)%6;
    return [5,3,1].map(n=>Math.round(255*(1-clamp(Math.min(k(n),4-k(n),1)))));
  }
  function hexRGB(rgb){return '#'+rgb.map(v=>Math.round(v).toString(16).padStart(2,'0')).join('').toUpperCase();}
  function triangleWeights(x,y){
    const [a,b,c]=TRIANGLE;
    const den=(b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1]);
    const wa=((b[1]-c[1])*(x-c[0])+(c[0]-b[0])*(y-c[1]))/den;
    const wb=((c[1]-a[1])*(x-c[0])+(a[0]-c[0])*(y-c[1]))/den;
    return [wa,wb,1-wa-wb];
  }
  function wheelColor(){
    const hue=pureHue(wheelHue);
    return hexRGB(hue.map(v=>wheelWeights[0]*v+wheelWeights[1]*255));
  }
  function drawWheel(){
    const canvas=$('colorWheel'),ctx=canvas.getContext('2d',{alpha:true});
    if(!ctx)return;
    const image=ctx.createImageData(WHEEL_SIZE,WHEEL_SIZE),d=image.data;
    const hueRGB=pureHue(wheelHue);
    for(let y=0;y<WHEEL_SIZE;y++)for(let x=0;x<WHEEL_SIZE;x++){
      const dx=x+.5-WHEEL_CENTER,dy=y+.5-WHEEL_CENTER,r=Math.hypot(dx,dy);
      const i=(y*WHEEL_SIZE+x)*4;
      let rgb=null,alpha=255;
      if(r>=WHEEL_INNER-.7 && r<=WHEEL_OUTER+.7){
        const h=((Math.atan2(-dy,dx)/(Math.PI*2))%1+1)%1;
        rgb=pureHue(h);
        alpha=Math.round(255*clamp(Math.min(r-(WHEEL_INNER-.7),(WHEEL_OUTER+.7)-r)));
      }else{
        const w=triangleWeights(x+.5,y+.5);
        if(w.every(v=>v>=-0.005))rgb=hueRGB.map(v=>w[0]*v+w[1]*255);
      }
      if(rgb){d[i]=rgb[0];d[i+1]=rgb[1];d[i+2]=rgb[2];d[i+3]=alpha;}
    }
    ctx.putImageData(image,0,0);
    ctx.beginPath();ctx.moveTo(...TRIANGLE[0]);ctx.lineTo(...TRIANGLE[1]);ctx.lineTo(...TRIANGLE[2]);ctx.closePath();
    ctx.strokeStyle='#a5b6aa';ctx.lineWidth=1;ctx.stroke();
    const angle=-wheelHue*2*Math.PI,ringR=(WHEEL_INNER+WHEEL_OUTER)/2;
    const hx=WHEEL_CENTER+ringR*Math.cos(angle),hy=WHEEL_CENTER+ringR*Math.sin(angle);
    const tx=wheelWeights.reduce((v,w,i)=>v+w*TRIANGLE[i][0],0);
    const ty=wheelWeights.reduce((v,w,i)=>v+w*TRIANGLE[i][1],0);
    for(const [x,y] of [[hx,hy],[tx,ty]]){
      ctx.beginPath();ctx.arc(x,y,6.3,0,Math.PI*2);ctx.lineWidth=3;ctx.strokeStyle='#24362c';ctx.stroke();
      ctx.beginPath();ctx.arc(x,y,5.1,0,Math.PI*2);ctx.lineWidth=2.5;ctx.strokeStyle='#ffffff';ctx.stroke();
    }
  }
  function paintColor(color){
    if(!/^#[0-9a-fA-F]{6}$/.test(color))return;
    state.paint=color.toUpperCase();$('hexInput').value=state.paint;$('chosenSwatch').style.background=state.paint;
    const rgb=[1,3,5].map(i=>parseInt(color.slice(i,i+2),16)/255);
    const [r,g,b]=rgb,max=Math.max(...rgb),min=Math.min(...rgb),delta=max-min;
    if(delta>.0001){
      let h=0;
      if(max===r)h=((g-b)/delta)%6;
      else if(max===g)h=(b-r)/delta+2;
      else h=(r-g)/delta+4;
      wheelHue=((h/6)%1+1)%1;
    }
    const saturation=max?delta/max:0;
    wheelWeights=[max*saturation,max*(1-saturation),1-max];
    drawWheel();
    document.querySelectorAll('.quick-palette button').forEach(el=>el.classList.toggle('active',el.dataset.color.toUpperCase()===state.paint));
  }
  function setWheelFromPointer(e,mode){
    const rect=$('colorWheel').getBoundingClientRect(),x=(e.clientX-rect.left)*WHEEL_SIZE/rect.width,
      y=(e.clientY-rect.top)*WHEEL_SIZE/rect.height;
    const r=Math.hypot(x-WHEEL_CENTER,y-WHEEL_CENTER);
    if(!mode){const w=triangleWeights(x,y);mode=w.every(v=>v>=-0.025)?'triangle':(r>=WHEEL_INNER-9?'ring':null);}
    if(!mode)return null;
    if(mode==='ring'){
      wheelHue=((Math.atan2(WHEEL_CENTER-y,x-WHEEL_CENTER)/(2*Math.PI))%1+1)%1;
    }else{
      const w=triangleWeights(x,y).map(v=>Math.max(0,v));
      const sum=w.reduce((a,b)=>a+b,0);
      if(sum<=0)return mode;
      wheelWeights=w.map(v=>v/sum);
    }
    // Preserve hue even when the triangle points to pure white or black.
    const currentHue=wheelHue,weights=[...wheelWeights];
    paintColor(wheelColor());
    wheelHue=currentHue;wheelWeights=weights;drawWheel();
    return mode;
  }
  function initColors(){
    const palette=['#E8A5C8','#DF7B8E','#B8A2E9','#7E9BCB','#5EB5DD','#81C9A0','#D7D17D','#D89265','#788390','#2C3944','#FFFFFF','#272E29'];
    $('quickPalette').innerHTML=palette.map(c=>`<button data-color="${c}" style="background:${c}" title="${c}" aria-label="${c}"></button>`).join('');
    $('paintGrid').innerHTML=Array.from({length:64},(_,i)=>`<div class="paint-cell" data-cell="${i%8},${Math.floor(i/8)}" role="button" aria-label="${i%8+1}열 ${Math.floor(i/8)+1}행"></div>`).join('');
    paintColor(state.paint);renderPaint();
    $('quickPalette').addEventListener('click',e=>{const b=e.target.closest('[data-color]');if(b)paintColor(b.dataset.color);});
    $('hexInput').addEventListener('change',e=>{const c=e.target.value.startsWith('#')?e.target.value:'#'+e.target.value;if(/^#[0-9A-Fa-f]{6}$/.test(c))paintColor(c);else{toast('색상은 #RRGGBB 형식으로 입력해 주세요.','error');paintColor(state.paint);}});
    const wheel=$('colorWheel');
    wheel.addEventListener('pointerdown',e=>{e.preventDefault();wheelDrag=setWheelFromPointer(e,null);wheel.setPointerCapture(e.pointerId);});
    wheel.addEventListener('pointermove',e=>{if(wheelDrag)setWheelFromPointer(e,wheelDrag);});
    for(const event of ['pointerup','pointercancel','lostpointercapture'])wheel.addEventListener(event,()=>wheelDrag=null);
    wheel.addEventListener('keydown',e=>{
      if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key))return;
      e.preventDefault();wheelHue=(wheelHue+(e.key==='ArrowLeft'||e.key==='ArrowDown'?-1:1)*(e.shiftKey?10:2)/360+1)%1;
      const hue=wheelHue,weights=[...wheelWeights];paintColor(wheelColor());wheelHue=hue;wheelWeights=weights;drawWheel();
    });
    $('paintGrid').addEventListener('pointerdown',e=>{if(!e.target.closest('[data-cell]'))return;e.preventDefault();state.paintDown=true;paintCell(e.target);$('paintGrid').setPointerCapture(e.pointerId);});
    $('paintGrid').addEventListener('pointermove',e=>{if(!state.paintDown)return;const el=document.elementFromPoint(e.clientX,e.clientY);if(el&&el.dataset.cell)paintCell(el);});
    for(const ev of ['pointerup','pointercancel','pointerleave'])$('paintGrid').addEventListener(ev,()=>state.paintDown=false);
    $('paintGrid').addEventListener('contextmenu',e=>{e.preventDefault();const cell=e.target.closest('[data-cell]');if(cell){delete state.points[cell.dataset.cell];renderPaint();}});
    $('drawTool').onclick=()=>{state.tool='draw';$('drawTool').classList.add('selected');$('eraseTool').classList.remove('selected');};
    $('eraseTool').onclick=()=>{state.tool='erase';$('eraseTool').classList.add('selected');$('drawTool').classList.remove('selected');};
    $('clearColors').onclick=()=>{state.points={};renderPaint();};
    $('tolerance').oninput=e=>{state.tolerance=Number(e.target.value);$('toleranceValue').textContent=state.tolerance;};
    $('colorOnlyButton').onclick=()=>applyColor(true);
    $('applyColors').onclick=()=>applyColor(false);
  }
  function paintCell(el){
    const key=el.dataset.cell;if(!key)return;
    if(state.tool==='erase')delete state.points[key];
    else state.points[key]=[0,2,4].map(i=>parseInt(state.paint.slice(i+1,i+3),16));
    renderPaint();
  }
  function renderPaint(){
    document.querySelectorAll('.paint-cell').forEach(el=>{
      const rgb=state.points[el.dataset.cell];el.style.background=rgb?'rgb('+rgb.join(',')+')':'';
    });
    $('drawnCells').textContent=`${Object.keys(state.points).length}칸 선택됨`;
  }
  async function applyColor(colorOnly){
    if(!Object.keys(state.points).length){toast('8×8 캔버스에 색상을 칠해 주세요.','error');return;}
    pushHistory();if(colorOnly){state.query='';state.mode='tag';state.digest=null;$('searchInput').value='';$('mode').value='tag';}
    closeColor();closeDetail(false);restoreGallery();await doSearch();
  }
  function openSettings(){
    const s=state.serviceState?.settings||{};
    const map={checkpoint:'setCheckpoint',architecture:'setArchitecture',threshold:'setThreshold',device:'setDevice',batch_size:'setBatch',base_url:'setBaseUrl',api_model:'setApiModel'};
    for(const [key,id] of Object.entries(map))$(id).value=s[key]??'';
    $('setUseJson').checked=!!s.use_json;$('setApiKey').value='';showModal('settingsModal');
  }
  async function saveSettings(){
    const payload={checkpoint:$('setCheckpoint').value.trim(),architecture:$('setArchitecture').value,
      threshold:Number($('setThreshold').value),device:$('setDevice').value,batch_size:$('setBatch').value,
      base_url:$('setBaseUrl').value.trim(),api_model:$('setApiModel').value.trim(),use_json:$('setUseJson').checked,
      api_key:$('setApiKey').value.trim()};
    try{const data=await api('/api/settings',payload);state.serviceState.settings=data.settings;
      closeModal('settingsModal');toast('설정을 저장했습니다.');}catch(e){fail(e);}
  }
  function renderLogs(){
    const s=state.serviceState||{};const events=[...(s.events||[])].reverse();const errs=s.errors||[];
    $('logBody').innerHTML=(s.scan?.summary?`<div class="log-item"><time>최근 분석</time><div><strong>${esc(s.scan.summary)}</strong>${esc(s.scan.status)}</div></div>`:'')+
      events.map(ev=>`<div class="log-item"><time>${esc(ev.time)}</time><div><strong>${esc(ev.name)}</strong>${esc(ev.message)}</div></div>`).join('')+
      errs.map(t=>`<div class="log-item"><time>오류</time><div>${esc(t)}</div></div>`).join('')+
      (!events.length&&!errs.length?'<div class="log-empty">아직 분석 기록이 없습니다.</div>':'');
  }
  function openLogs(){renderLogs();showModal('logModal');}
  function openFolderDialog(){$('manualFolder').value='';showModal('folderModal');}
  async function chooseNative(){
    try{const data=await api('/api/choose_folder',{});if(data.folder)$('manualFolder').value=data.folder;
      else toast('폴더 선택이 취소되었습니다.','error');}
    catch(e){fail(e);toast('폴더 경로를 직접 입력해도 됩니다.','error');}
  }
  async function startScan(){
    const folder=$('manualFolder').value.trim();if(!folder){toast('이미지 폴더를 선택해 주세요.','error');return;}
    try{await api('/api/scan',{folder});closeModal('folderModal');state.digest=null;
      toast('폴더 분석을 시작했습니다.');await refreshState();restoreGallery();await doSearch();}
    catch(e){fail(e);}
  }
  async function uploadSingleImage(event){
    const input=event.target;
    const file=input.files?.[0];
    input.value='';
    if(!file)return;
    if(file.size>40*1024*1024){toast('이미지는 최대 40MB까지 추가할 수 있습니다.','error');return;}
    if(state.serviceState?.scan?.running){toast('분석이 끝난 후 이미지를 추가해 주세요.','error');return;}
    const button=$('addImage');button.disabled=true;
    try{
      const response=await fetch('/api/upload_image',{
        method:'POST',headers:{'X-MORI-Token':token,'X-File-Name':encodeURIComponent(file.name),
                                  'Content-Type':'application/octet-stream'},body:file});
      const data=await response.json();
      if(!response.ok||data.error)throw new Error(data.error||`이미지 업로드 실패 (${response.status})`);
      pushHistory();closeDetail(false);state.digest=null;state.query='';
      $('searchInput').value='';state.points={};renderPaint();restoreGallery();
      await refreshState();await doSearch();
      toast(data.duplicate ? '동일한 이미지는 이미 등록되어 있어 건너뛰었습니다.' : '내부 저장소에 이미지를 추가했습니다. 분석이 시작됩니다.');
    }catch(e){fail(e);}finally{button.disabled=false;}
  }
  const seconds=n=>{if(n==null)return '계산 중';const total=Math.max(0,Math.round(n)),mins=Math.floor(total/60),sec=total%60;return mins?`${mins}분 ${String(sec).padStart(2,'0')}초`:`${sec}초`;};
  async function refreshState(){
    try{
      const data=await apiGet('/api/state');const old=state.serviceState?.scan?.revision;
      state.serviceState=data;renderFooter();
      const scan=data.scan;const active=scan.running||scan.summary;
      if(scan.running)state.dismissedScanRevision=null;
      $('scanCard').hidden=!active||(!scan.running&&state.dismissedScanRevision===scan.revision);
      $('closeScan').hidden=scan.running||!scan.summary;
      $('scanTitle').textContent=scan.running?'폴더 분석 중':(scan.summary||'분석 결과');
      $('scanFolder').textContent=scan.running?basename(scan.folder):(scan.status||'분석 종료');
      $('scanCount').textContent=`${fmt(scan.completed)} / ${fmt(scan.total)} 장`;
      const pct=scan.total?Math.round(scan.completed/scan.total*100):0;
      $('scanPercent').textContent=`${pct}%`;$('scanProgress').style.width=`${pct}%`;
      $('scanElapsed').textContent=`경과 ${seconds(scan.elapsed)}`;
      $('scanRemaining').textContent=scan.running?`남은 시간 ${seconds(scan.remaining)}`:scan.status;
      $('stopScan').disabled=!scan.running;
      $('stopScan').style.display=scan.running?'':'none';
      // Refresh indexing results no more than once every few seconds; never interrupt a detail view.
      if(old!==undefined&&old!==scan.revision&&!state.selectedId&&scan.running&&Date.now()-state.refreshAt>3500){
        state.refreshAt=Date.now();invalidateSearch();await doSearch({preserve:true});
      }else if(old!==undefined&&old!==scan.revision&&!scan.running&&state.serviceState&&Date.now()-state.refreshAt>500){
        state.refreshAt=Date.now();invalidateSearch();if(!state.selectedId&&state.activeTab==='library')await doSearch({preserve:true});
      }
      if(!$('logModal').hidden)renderLogs();
    }catch(e){console.warn('MORI state refresh:',e.message);}
  }
  async function downloadExport(){
    try{
      const response=await fetch('/api/export',{headers:{'X-MORI-Token':token}});
      if(!response.ok){const data=await response.json();throw new Error(data.error||'내보내기 실패');}
      const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');
      a.href=url;a.download='image_captions.json';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);toast('태그 JSON 파일을 생성했습니다.');
    }catch(e){fail(e);}
  }
  function wire(){
    $('searchButton').onclick=()=>{if(state.selectedId)closeDetail(true);state.digest=null;restoreGallery();doSearch({push:true});};
    $('searchInput').addEventListener('keydown',e=>{if(e.key==='Enter'){$('searchButton').click();}});
    $('mode').onchange=()=>{state.mode=$('mode').value;const input=$('searchInput');input.placeholder=state.mode==='natural'?'예: 분홍색 머리에 기타를 연주하는 소녀':'태그로 찾아보세요. 예: 1girl, blue eyes';};
    $('sortOrder').onchange=e=>{pushHistory();state.sort=e.target.value;doSearch();};
    $('activeFilters').addEventListener('click',e=>{
      const tag=e.target.closest('[data-remove-tag]');
      if(tag){pushHistory();const name=tag.dataset.removeTag;state.query=state.query.split(',').map(x=>x.trim()).filter(x=>normalize(x)!==normalize(name)).join(', ');$('searchInput').value=state.query;doSearch();}
      if(e.target.matches('[data-clear-digest]')){pushHistory();state.digest=null;doSearch();}
      if(e.target.matches('[data-clear-painted]')){pushHistory();state.points={};renderPaint();doSearch();}
    });
    $('resetFilters').onclick=()=>{pushHistory();state.query='';state.digest=null;state.points={};$('searchInput').value='';renderPaint();doSearch();};
    $('exportButton').onclick=downloadExport;
    $('addFolder').onclick=openFolderDialog;
    $('addImage').onclick=()=>$('imageFileInput').click();
    $('imageFileInput').onchange=uploadSingleImage;$('emptyAction').onclick=()=>{if(state.serviceState?.total){pushHistory();state.query='';state.digest=null;state.points={};$('searchInput').value='';renderPaint();doSearch();}else openFolderDialog();};
    $('topSettings').onclick=openSettings;$('sideSettings').onclick=openSettings;$('brand-home').onclick=()=>{pushHistory();closeDetail(false);state.query='';state.digest=null;state.points={};$('searchInput').value='';restoreGallery();doSearch();};
    document.querySelectorAll('.nav-item').forEach(el=>el.onclick=()=>{
      const tab=el.dataset.nav;
      if(tab==='colors')return openColor();
      if(tab==='history')return openLogs();
      if(state.selectedId)closeDetail(true);
      if(state.activeTab!=='library'){restoreGallery();doSearch();}else window.scrollTo({top:0,behavior:'smooth'});
    });
    $('sideHistory').addEventListener('click',e=>{
      const button=e.target.closest('[data-visited-image]');
      if(button)openDetail(Number(button.dataset.visitedImage)).catch(fail);
    });
    document.addEventListener('click',e=>{const close=e.target.closest('[data-close]');if(close){const t=close.dataset.close;t==='color'?closeColor():closeModal(t==='settings'?'settingsModal':t==='log'?'logModal':'folderModal');}
    });
    $('colorOverlay').onclick=closeColor;
    $('settingsModal').addEventListener('click',e=>{if(e.target===$('settingsModal'))closeModal('settingsModal');});
    $('logModal').addEventListener('click',e=>{if(e.target===$('logModal'))closeModal('logModal');});
    $('folderModal').addEventListener('click',e=>{if(e.target===$('folderModal'))closeModal('folderModal');});
    $('prevImage').onclick=()=>neighbor(-1);$('nextImage').onclick=()=>neighbor(1);
    $('addTags').onclick=addTags;$('newTags').onkeydown=e=>{if(e.key==='Enter')addTags();};
    $('detailTags').addEventListener('click',e=>{
      const remove=e.target.closest('[data-remove-detail-tag]');
      if(remove){removeTag(remove.dataset.removeDetailTag);return;}
      const search=e.target.closest('[data-search-detail-tag]');
      if(search)searchDetailTag(search.dataset.searchDetailTag).catch(fail);
    });
    $('deleteImage').onclick=deleteSelected;$('openOriginal').onclick=()=>launchOriginal(false);
    $('openLocation').onclick=()=>launchOriginal(true);$('sameHash').onclick=sameHash;$('seeSimilar').onclick=seeSimilar;
    $('saveSettings').onclick=saveSettings;$('chooseNativeFolder').onclick=chooseNative;$('startFolderScan').onclick=startScan;
    $('openLog').onclick=openLogs;$('stopScan').onclick=async()=>{try{await api('/api/stop',{});toast('분석 중지를 요청했습니다.');}catch(e){fail(e);}};
    $('collapseScan').onclick=()=>{state.collapsed=!state.collapsed;$('scanCard').classList.toggle('compact',state.collapsed);$('collapseScan').textContent=state.collapsed?'+':'−';};
    $('closeScan').onclick=()=>{
      const scan=state.serviceState?.scan;
      if(!scan||scan.running)return;
      state.dismissedScanRevision=scan.revision;
      $('scanCard').hidden=true;
    };
    document.addEventListener('keydown',e=>{
      if(e.key==='Escape'){if($('colorDrawer').classList.contains('open'))closeColor();else if(!$('detailModal').hidden)closeDetail(true);else for(const id of ['settingsModal','logModal','folderModal'])if(!$(id).hidden)closeModal(id);}
      if(!$('detailModal').hidden && !e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey && !['INPUT','TEXTAREA'].includes(document.activeElement.tagName)){
        if(e.key==='ArrowLeft')neighbor(-1);if(e.key==='ArrowRight')neighbor(1);
      }
      if((e.ctrlKey||e.metaKey)&&e.key==='f'){e.preventDefault();$('searchInput').focus();}
    });
    const intersection = new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting))loadMore();},{rootMargin:'800px'});
    intersection.observe($('loadSentinel'));
    const masonryResize=new ResizeObserver(()=>{reflowMasonry($('gallery'));reflowMasonry($('similarGrid'));});
    masonryResize.observe($('gallery'));masonryResize.observe($('similarGrid'));
  }
  const normalize=t=>t.trim().toLowerCase().replaceAll('_',' ').replace(/\s+/g,' ');
  async function init(){
    wire();initColors();renderViewedHistory();
    history.scrollRestoration='manual';
    const saved=history.state?.mori?history.state.snapshot:null;
    await refreshState();
    if(saved)await restoreBrowserView(saved);else await doSearch();
    if(!history.state?.mori)history.replaceState(historyRecord(currentSnapshot()),'',routeURL(currentSnapshot()));
    pollTimer=setInterval(refreshState,1800);
  }
  init().catch(fail);
})();
