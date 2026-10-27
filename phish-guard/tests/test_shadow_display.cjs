// Execute the production renderer with a small DOM stub; no service or browser.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../web/static/js/shadow.js'), 'utf8');

async function render(fusion) {
  const element = () => ({children:[],style:{},textContent:'',
    appendChild(child) { this.children.push(child); },
    replaceChildren() { this.children=[]; }, addEventListener() {}});
  const nodes = Object.fromEntries(['refresh-shadow','shadow-rows','shadow-status'].map(id=>[id,element()]));
  const context = vm.createContext({Date, document:{
    getElementById:id=>nodes[id], createElement:element},
    fetch:async()=>({ok:true,json:async()=>({status:'available',
      events:[{ts:1,url:'https://www.iana.org/help/example-domains',shadow_fusion:fusion}]})})});
  await vm.runInContext(source,context);
  return {cells:nodes['shadow-rows'].children[0]?.children.map(c=>c.textContent),
    status:nodes['shadow-status'].textContent};
}
function record(extra={}) {
  return {status:'completed',enforced_decision:'uncertain',suggested_decision:'uncertain',
    url_analysis:{status:'completed',ml_score:.037,threshold:.4},
    visual:{status:'completed',brand:null,evidence_status:'BRAND_UNCONFIRMED'},
    form_risk:{level:'not_observed'},original_reason_codes:['BRAND_UNCONFIRMED'],reason_codes:[],
    ...extra};
}
test('IANA review displays original brand reason independently of completed workflow and low URL score',async()=>{
  const {cells}=await render(record());
  assert.match(cells[1],/3.7%/);
  assert.match(cells[2],/视觉流程：已完成/);
  assert.match(cells[2],/视觉证据：品牌未确认.*BRAND_UNCONFIRMED/);
  assert.match(cells[3],/需复核\n原始原因：品牌未确认/);
  assert.match(cells[4],/联合新增原因：无新增，沿用原始判定/);
});
test('high URL reasons remain separate and do not relabel the original decision',async()=>{
  const {cells}=await render(record({reason_codes:['URL_MODEL_REVIEW']}));
  assert.match(cells[3],/BRAND_UNCONFIRMED/);
  assert.doesNotMatch(cells[3],/URL_MODEL_REVIEW/);
  assert.match(cells[4],/需复核.*\n联合新增原因：URL 模型提示复核/);
});
test('older records explicitly show unavailable original evidence without inventing a cause',async()=>{
  const old=record(); delete old.original_reason_codes; delete old.visual.evidence_status;
  const {cells}=await render(old);
  assert.match(cells[3],/旧记录未保存原始原因/);
  assert.match(cells[2],/视觉证据：未记录/);
  assert.doesNotMatch(cells.join('\n'),/BRAND_UNCONFIRMED/);
});
test('partial results and unknown reason text are rendered as text without dropping the record',async()=>{
  const {cells,status}=await render(record({status:'partial',visual:{status:'failed'},
    original_reason_codes:['<img src=x onerror=alert(1)>'],reason_codes:['URL_UNAVAILABLE']}));
  assert.match(cells[3],/<img src=x onerror=alert\(1\)>/);
  assert.match(cells[4],/部分未完成/);
  assert.match(cells[4],/URL 分析不可用/);
  assert.match(status,/已显示最近 1 条/);
});

test('confirmed brand shows actual similarity, domains and pixel evidence without probability claims',async()=>{
  const {cells}=await render(record({visual:{status:'completed',brand:'微信',evidence_status:'BRAND_CONFIRMED',
    score_kind:'similarity_not_probability', similarity:.95678,official_domains:['wechat.com','weixin.qq.com'],
    current_domain:'127.0.0.1',domain_check:'mismatch',reference_version:'actual-index',threshold:.9,
    brand_margin_required:.04,image_size:[1280,900],
    candidates:[{name:'微信',similarity:.95678,domains:['wechat.com'],reference:'logos/wechat.png'}],
    regions:[{box:[10,20,90,100],source:'dom_image',rejection:null}]}}));
  assert.match(cells[2],/匹配相似度（非概率）：0.9568/);
  assert.match(cells[2],/正规域名：wechat.com \/ weixin.qq.com/);
  assert.match(cells[2],/当前域名：127.0.0.1/);
  assert.match(cells[2],/域名核验：不属于该品牌/);
  assert.match(cells[2],/参考图：logos\/wechat.png/);
  assert.match(cells[2],/视觉区域 \[10,20,90,100\]/);
});
test('unconfirmed candidates and third party controls remain explicitly diagnostic',async()=>{
  const {cells}=await render(record({visual:{status:'completed',brand:null,score_kind:'similarity_not_probability',
    similarity:null,official_domains:[],domain_check:'not_evaluated',
    candidates:[{name:'PayPal',similarity:.99,domains:['paypal.com'],reference:'<svg onload=alert(1)>'}],
    regions:[{box:[1,2,3,4],source:'third_party_control',rejection:'THIRD_PARTY_CONTROL'}]}}));
  assert.match(cells[2],/^未确认品牌/);
  assert.match(cells[2],/正规域名：未确认/);
  assert.match(cells[2],/候选（不等于确认）：PayPal/);
  assert.match(cells[2],/第三方登录\/支付组件，不作为页面身份/);
  assert.match(cells[2],/<svg onload=alert\(1\)>/);
});
