/* Independent experiment. Ordinary DOM permissions. No field values. */
(() => {
 if(window.__step10Evidence)return;
 const lifecycle=crypto.randomUUID(),observers=new Map(),frames=new Map();
 let version=0,collections=0,events=0,dead=false,trackingIncomplete=false;
 const bump=()=>{version++;events++;};
 const digest=async s=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s)))).map(x=>x.toString(16).padStart(2,'0')).join('');
 function discover(){
  const found=new Set(),newFrames=new Set();trackingIncomplete=false;
  function walk(root,depth){
   if(found.has(root))return;if(depth>3||found.size>=8){trackingIncomplete=true;return;}found.add(root);
   if(!observers.has(root)){const ob=new MutationObserver(rs=>{if(rs.length)bump();});ob.observe(root,{subtree:true,childList:true,attributes:true,characterData:true});observers.set(root,ob);}
   const nodes=Array.from(root.querySelectorAll('*'));if(nodes.length>512)trackingIncomplete=true;
   for(const e of nodes.slice(0,512)){
    if(e.tagName==='IFRAME'){newFrames.add(e);if(!frames.has(e)){e.addEventListener('load',bump);frames.set(e,true);}try{if(e.contentWindow.document)walk(e.contentWindow.document,depth+1);}catch{}}
    if(e.shadowRoot)walk(e.shadowRoot,depth+1);
   }
  }
  walk(document,0);
  for(const [root,ob] of observers)if(!found.has(root)){if(ob.takeRecords().length)bump();ob.disconnect();observers.delete(root);bump();}
  for(const f of frames.keys())if(!newFrames.has(f)){f.removeEventListener('load',bump);frames.delete(f);}
 }
 function drain(){for(const ob of observers.values())if(ob.takeRecords().length)bump();}
 addEventListener('pagehide',()=>{dead=true;bump();});addEventListener('pageshow',e=>{if(e.persisted){dead=false;bump();}});
 addEventListener('popstate',bump);addEventListener('hashchange',bump);addEventListener('resize',bump);addEventListener('scroll',bump,true);
 window.__step10Evidence={snapshot:async()=>{
  const t=performance.now();discover();drain();const startVersion=version;
  const dom=window.__step9Capture();
  const canonical={url:location.href,title:document.title,viewport:{width:innerWidth,height:innerHeight,scrollX,scrollY},document_token:dom.document_token,scopes:dom.scopes,boundaries:dom.boundaries,limits_exceeded:dom.limits_exceeded};
  const fingerprint=await digest(JSON.stringify(canonical));drain();
  return {lifecycle,collection:++collections,version:startVersion,end_version:version,fingerprint,unstable_during_read:startVersion!==version,dead,unavailable:dom.boundaries.filter(b=>b.status==='unavailable').map(b=>({kind:b.kind,html_id:b.html_id,reason:b.reason})),incomplete:trackingIncomplete||dom.limits_exceeded||dom.scopes.some(s=>s.limits_exceeded),dom,observer_roots:observers.size,mutation_batches:events,collection_ms:performance.now()-t};
 }};
 discover();
})();
