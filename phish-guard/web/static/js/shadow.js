async function refreshShadow() {
  const button = document.getElementById('refresh-shadow');
  const rows = document.getElementById('shadow-rows');
  const status = document.getElementById('shadow-status');
  button.disabled = true;
  rows.replaceChildren();
  try {
    const response = await fetch('/api/v1/stats/shadow');
    if (!response.ok) throw new Error('读取失败');
    const data = await response.json();
    if (data.status !== 'available') throw new Error(data.message || '服务不可用');
    const names = {allow:'未发现明显风险', uncertain:'需复核', block:'高风险'};
    const levels = {high:'高风险信号',caution:'需核实',not_observed:'未发现信号'};
    const states = {completed:'已完成',degraded:'信息不完整',failed:'失败',skipped:'白名单跳过'};
    const reasons = {ORIGINAL_BLOCK_PRESERVED:'保留原高风险判定',
      LOCAL_PHISHTANK_MATCH:'本地钓鱼库命中',URL_AND_CREDENTIAL_RISK:'网址可疑且存在凭据风险',
      URL_MODEL_REVIEW:'URL 模型提示复核',CREDENTIAL_RISK_REVIEW:'表单凭据风险需核实',
      VISUAL_OR_CONTEXT_INCOMPLETE:'视觉或页面信息不完整',URL_UNAVAILABLE:'URL 分析不可用'};
    const originalReasons = {
      BRAND_UNCONFIRMED:'品牌未确认，需人工核对', NO_LOGO_REGION:'未找到可用 Logo',
      AMBIGUOUS_BRANDS:'品牌外观相近，无法可靠区分', MULTIPLE_BRANDS:'多个品牌，归属未确认',
      BRAND_CONFIRMED:'视觉品牌已确认', BRAND_DOMAIN_MISMATCH:'品牌与当前域名不符',
      CREDENTIAL_BRAND_UNLINKED:'尚未确认品牌与密码框的关联',
      INSUFFICIENT_CREDENTIAL_EVIDENCE:'未确认密码凭据索取',
      PASSWORD_INPUT_DETECTED:'存在密码输入框', EXTERNAL_FORM_ACTION:'表单指向外部域名',
      SUSPICIOUS_URL:'网址规则发现可疑特征', REVIEW_REQUIRED:'页面与网址信号需复核',
      GENERAL_RISK_SIGNALS:'通用检查发现需核实的信号',
      THREE_CONDITION_MATCH:'品牌仿冒及凭据关联条件成立',
      WHITELIST_MATCH:'命中明确域名白名单',
      NO_RELIABLE_PHISHING_SIGNAL:'本次完整检测未发现可靠钓鱼信号',
      INVALID_URL:'网址无效', PAGE_DOMAIN_MISMATCH:'页面域名与请求不一致',
      BROWSER_CONTEXT_MISSING:'浏览器上下文缺失', SCREENSHOT_ONLY:'仅有截图',
      IMAGE_DECODE_FAILED:'截图无效', VISUAL_ANALYSIS_FAILED:'视觉分析失败'
    };
    const explain = (codes, labels) => codes.map(code => (labels[code] || code)
      + (labels[code] ? ' ['+code+']' : '')).join('；');
    const similarity = value => typeof value === 'number' && Number.isFinite(value)
      ? value.toFixed(4) : '未记录';
    const rejectionNames = {THIRD_PARTY_CONTROL:'第三方登录/支付组件，不作为页面身份',
      AUXILIARY_TOOLBAR:'辅助工具栏，不作为页面身份', PAGE_BLOCK_ONLY:'页面块不足以确认品牌',
      LOW_SIMILARITY:'相似度不足', AMBIGUOUS_BRANDS:'品牌间距不足',
      ASPECT_RATIO_MISMATCH:'图形比例不符', FLAT_REGION:'缺少有效图像内容'};
    function brandDetails(visual) {
      if (!visual.score_kind) return ''; // Historical records have no saved detail.
      const domains = Array.isArray(visual.official_domains) ? visual.official_domains : [];
      const domainLabels = {match:'符合已配置正规域名', mismatch:'不属于该品牌的已配置正规域名',
        not_evaluated:'未核验：品牌或页面上下文未确认'};
      const lines = ['匹配相似度（非概率）：'+similarity(visual.similarity),
        '正规域名：'+(domains.join(' / ') || '未确认'),
        '当前域名：'+(visual.current_domain || '未记录'),
        '域名核验：'+(domainLabels[visual.domain_check] || '未记录')];
      if (visual.reference_version) lines.push('参考库版本：'+visual.reference_version);
      if (visual.threshold != null) lines.push('确认门槛：'+similarity(visual.threshold)
        +'；品牌间距：'+similarity(visual.brand_margin_required));
      for (const candidate of (visual.candidates || []).slice(0,3)) {
        lines.push('候选（不等于确认）：'+candidate.name+'；相似度 '+similarity(candidate.similarity)
          +'；候选域名 '+(candidate.domains || []).join(' / '));
        if (candidate.reference) lines.push('参考图：'+candidate.reference);
      }
      if (Array.isArray(visual.image_size)) lines.push('截图尺寸：'+visual.image_size.join(' × '));
      for (const region of (visual.regions || []).slice(0,10)) {
        lines.push('视觉区域 '+JSON.stringify(region.box)+'；来源 '+(region.source || '未记录')
          +'；'+(region.rejection ? (rejectionNames[region.rejection] || region.rejection)
            : '区域匹配通过（页面身份仍以总结果为准）'));
      }
      return '\n'+lines.join('\n');
    }
    for (const event of data.events) {
      const fusion = event.shadow_fusion, url = fusion.url_analysis || {};
      const visual = fusion.visual || {}, form = fusion.form_risk || {};
      const originalText = Array.isArray(fusion.original_reason_codes)
        ? (explain(fusion.original_reason_codes, originalReasons) || '未提供原因码')
        : '旧记录未保存原始原因';
      const fusionText = explain(Array.isArray(fusion.reason_codes) ? fusion.reason_codes : [], reasons)
        || (fusion.suggested_decision === fusion.enforced_decision ? '无新增，沿用原始判定' : '未提供原因码');
      const row = document.createElement('tr');
      const values = [
        new Date(event.ts * 1000).toLocaleString() + '\n' + event.url,
        url.status === 'completed' ? (url.ml_score*100).toFixed(1)+'%\n阈值 '+(url.threshold*100).toFixed(1)+'%'
          + (url.phishtank_match ? '\nPhishTank 命中' : '') : 'URL 分析不可用',
        (visual.brand || '未确认品牌') + '\n视觉流程：'+(states[visual.status] || '未知')
          + '\n视觉证据：'+(visual.evidence_status
            ? explain([visual.evidence_status], originalReasons)
            : visual.status === 'skipped' ? '白名单跳过视觉分析' : '未记录')
          + '\n通用/表单：'+(levels[form.level] || '未知') + brandDetails(visual),
        (names[fusion.enforced_decision] || '未知') + '\n原始原因：'+originalText,
        (names[fusion.suggested_decision] || '未知') + (fusion.status === 'partial' ? '（部分未完成）' : '')
          + '\n联合新增原因：'+fusionText
      ];
      for (const value of values) {
        const cell = document.createElement('td');
        cell.textContent = value;
        cell.style.cssText = 'white-space:pre-wrap;overflow-wrap:anywhere;max-width:360px';
        row.appendChild(cell);
      }
      rows.appendChild(row);
    }
    status.textContent = data.events.length ? '已显示最近 '+data.events.length+' 条。原拦截策略保持不变。' : '暂无联合检测记录。';
  } catch (error) {
    status.textContent = error.message;
  } finally { button.disabled = false; }
}
document.getElementById('refresh-shadow').addEventListener('click', refreshShadow);
refreshShadow();
