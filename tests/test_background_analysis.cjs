const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../browser-extension/background.js'), 'utf8');
function harness(fetchImpl) {
  const listeners = [], requests = [];
  const event = {addListener() {}};
  vm.runInNewContext(source, {AbortController, setTimeout, clearTimeout,
    fetch: async (url, init) => { requests.push({url, init}); return fetchImpl(url, init); },
    chrome:{runtime:{onMessage:{addListener(fn){listeners.push(fn);}}},
      tabs:{onActivated:event,onUpdated:event,onRemoved:event}}});
  const sender={tab:{id:1},frameId:0,documentId:'doc',url:'http://example.test/'};
  const payload={url:sender.url,analysis_mode:'browser',screenshot_base64:'data:image/png;base64,test'};
  const send=(msg, s=sender)=>new Promise(resolve=>listeners[1](msg,s,resolve));
  return {requests,sender,payload,send};
}
test('external HTTP page analysis is sent only to fixed local endpoint, without redirects or cookies', async()=>{
  const h=harness(async()=>({ok:true,status:200,text:async()=>'{"decision":"uncertain"}'}));
  const r=await h.send({type:'ANALYZE',requestId:1,payload:h.payload,url:'https://attacker.invalid/'});
  assert.equal(r.status,200);
  assert.equal(h.requests[0].url,'http://127.0.0.1:8765/analyze');
  assert.equal(h.requests[0].init.redirect,'error');
  assert.equal(h.requests[0].init.credentials,'omit');
});
test('rejects subframes, forged page URLs and non-browser requests without network calls',async()=>{
  const h=harness(()=>assert.fail('must not fetch'));
  for(const [payload,sender] of [[h.payload,{...h.sender,frameId:1}],
    [{...h.payload,url:'https://other.test/'},h.sender],
    [{...h.payload,analysis_mode:'screenshot_only'},h.sender]]) {
    const r=await h.send({type:'ANALYZE',requestId:1,payload},sender);assert.ok(r.error);
  }
  assert.equal(h.requests.length,0);
});
test('cancellation is scoped to the requesting document',async()=>{
  const h=harness((u,init)=>new Promise((resolve,reject)=>init.signal.addEventListener('abort',()=>reject(Object.assign(new Error(),{name:'AbortError'})))));
  const pending=h.send({type:'ANALYZE',requestId:1,payload:h.payload});
  await h.send({type:'CANCEL_ANALYZE',requestId:1},{...h.sender,documentId:'other'});
  assert.equal(h.requests[0].init.signal.aborted,false);
  await h.send({type:'CANCEL_ANALYZE',requestId:1});
  assert.equal((await pending).error,'ANALYSIS_ABORTED');
});
test('HTTP failures and unavailable service never become successful verdicts',async()=>{
  const h=harness(async()=>({ok:false,status:503}));
  assert.equal((await h.send({type:'ANALYZE',requestId:1,payload:h.payload})).status,503);
  const offline=harness(async()=>{throw new Error('offline');});
  assert.equal((await offline.send({type:'ANALYZE',requestId:1,payload:offline.payload})).error,'LOCAL_SERVICE_UNREACHABLE');
});

test('model warmup bridge is fixed, credential-free and sends no page payload',async()=>{
  const h=harness(async()=>({ok:true,status:200,text:async()=>'{"models_ready":false}'}));
  const r=await h.send({type:'MODEL_STATUS',requestId:1,payload:{url:h.sender.url,analysis_mode:'browser'}});
  assert.equal(r.status,200);assert.equal(h.requests[0].url,'http://127.0.0.1:8765/warmup');
  assert.equal(h.requests[0].init.body,'{}');assert.equal(h.requests[0].init.credentials,'omit');assert.equal(h.requests[0].init.redirect,'error');
});

test('warmup bridge rejects forged document context',async()=>{
  const h=harness(()=>assert.fail('no fetch'));
  const r=await h.send({type:'MODEL_STATUS',requestId:1,payload:{url:'https://other.test',analysis_mode:'browser'}});
  assert.equal(r.error,'INVALID_ANALYSIS_PAYLOAD');
});

test('allows only fragment differences from the sender document URL and preserves the actual page URL',async()=>{
  const h=harness(async()=>({ok:true,status:200,text:async()=>'{"decision":"uncertain"}'}));
  const payload={...h.payload,url:h.payload.url+'#login'};
  const r=await h.send({type:'ANALYZE',requestId:1,payload});
  assert.equal(r.status,200);
  assert.equal(JSON.parse(h.requests[0].init.body).url,payload.url);
});

test('fragment handling does not allow forged origins, paths, queries or non-string URLs',async()=>{
  const h=harness(()=>assert.fail('must not fetch'));
  for(const url of ['https://other.test/#login','http://example.test/other#login',
    'http://example.test/?other=1#login',null,{},42]) {
    const r=await h.send({type:'ANALYZE',requestId:1,payload:{...h.payload,url}});
    assert.equal(r.error,'INVALID_ANALYSIS_PAYLOAD');
  }
  assert.equal(h.requests.length,0);
});
