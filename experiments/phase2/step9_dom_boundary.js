/* Ordinary page-context traversal only: no CDP piercing or cross-origin frame evaluation. */
(() => {
 if(window.__step9Capture)return;
 const token=crypto.randomUUID(),ids=new WeakMap();let count=0,sequence=0;
 const id=x=>{if(!ids.has(x))ids.set(x,token+'/node-'+(++count));return ids.get(x);};
 function text(el){const w=el.ownerDocument.createTreeWalker(el,NodeFilter.SHOW_TEXT);let n,out='';while((n=w.nextNode()))if(!n.parentElement.closest('input,textarea,select,script,style,[contenteditable]'))out+=' '+n.nodeValue;return out.replace(/\s+/g,' ').trim().slice(0,1024);}
 function visible(el){const s=el.ownerDocument.defaultView.getComputedStyle(el),r=el.getBoundingClientRect();return !el.closest('[hidden]')&&el.type!=='hidden'&&s.display!=='none'&&s.visibility!=='hidden'&&r.width>0&&r.height>0;}
 function scope(root,kind){
  const allFields=Array.from(root.querySelectorAll('input')),allForms=Array.from(root.querySelectorAll('form'));
  const fields=allFields.slice(0,128).map(e=>({element_id:id(e),html_id:e.id||null,type:e.type,form_id:e.form?id(e.form):null,visible:visible(e),connected:e.isConnected,labels:Array.from(e.labels||[]).map(l=>({element_id:id(l),for_attribute:l.getAttribute('for'),text:text(l)})),value_collected:false}));
  return {root_id:id(root),kind,document_id:id(root.nodeType===9?root:root.ownerDocument),forms:allForms.slice(0,32).map(f=>({form_id:id(f),html_id:f.id||null,fields:fields.filter(x=>x.form_id===id(f)).map(x=>x.element_id),has_visible_password:fields.some(x=>x.form_id===id(f)&&x.type==='password'&&x.visible)})),fields,headings:Array.from(root.querySelectorAll('h1,h2,h3')).slice(0,64).map(n=>({element_id:id(n),text:text(n),form_id:n.closest('form')?id(n.closest('form')):null})),limits_exceeded:allFields.length>128||allForms.length>32};
 }
 window.__step9Capture=()=>{
  const scopes=[],boundaries=[],seen=new Set();let capped=false;
  function visit(root,kind,depth){
   if(seen.has(root))return;if(depth>3||scopes.length>=8){capped=true;return;}seen.add(root);scopes.push(scope(root,kind));
   const elements=Array.from(root.querySelectorAll('*'));if(elements.length>512)capped=true;
   for(const el of elements.slice(0,512)){
    if(el.tagName==='IFRAME'){
     const entry={element_id:id(el),html_id:el.id||null,kind:'iframe',src_attribute:el.getAttribute('src'),sandbox:el.getAttribute('sandbox')};
     try{const doc=el.contentWindow.document;if(!doc)throw Error('no current document');entry.status='available_same_origin';entry.child_document_id=id(doc);visit(doc,'same_origin_iframe',depth+1);}
     catch(e){entry.status='unavailable';entry.reason=e.name==='SecurityError'?'SAME_ORIGIN_POLICY':'DOCUMENT_UNAVAILABLE';entry.error_name=e.name;entry.fields=null;}
     boundaries.push(entry);
    }
    if(el.shadowRoot){boundaries.push({element_id:id(el),html_id:el.id||null,kind:'shadow',status:'available_open_shadow',root_id:id(el.shadowRoot)});visit(el.shadowRoot,'open_shadow',depth+1);}
    else if(el.hasAttribute('data-boundary-host'))boundaries.push({element_id:id(el),html_id:el.id||null,kind:'shadow_host_declared_by_fixture',status:'unavailable',reason:'NO_OBSERVABLE_OPEN_ROOT',mode_from_observation:null,fields:null});
   }
  }
  visit(document,'top_document',0);
  return {capture:++sequence,document_token:token,scopes,boundaries,limits_exceeded:capped,guards:window.__step9Guards,permission_model:'ordinary top-page DOM and permitted same-origin documents; no automation frame traversal, attachShadow interception, or closed-root handle',values_collected:false};
 };
})();
