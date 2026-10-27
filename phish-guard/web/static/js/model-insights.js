(() => {
    const set = (id, text) => { document.getElementById(id).textContent = text; };
    const pct = n => typeof n === 'number' ? (n * 100).toFixed(2) + '%' : '不可计算';
    const pp = n => (n >= 0 ? '+' : '') + (n * 100).toFixed(2) + ' 个百分点';
    const labels = {
        has_obfuscated_brand_host: '域名中的数字仿冒品牌', url_length: '网址长度',
        domain_entropy: '域名字符复杂度', url_entropy: '网址字符复杂度', path_entropy: '路径字符复杂度',
        path_length: '路径长度', subdomain_length: '子域名长度', num_subdomains: '子域名层数',
        query_length: '查询参数长度', query_value_length_avg: '参数值平均长度',
        longest_token_length: '最长片段长度', has_suspicious_path: '登录等路径关键词',
        is_suspicious_tld: '训练规则中的风险后缀', path_token_count: '路径片段数',
        uses_https: '是否使用 HTTPS', num_digits: '数字个数', brand_mismatch: '品牌与域名不一致',
        has_verify_keyword: '验证类关键词', num_slashes: '斜线个数', path_depth: '路径深度',
        consecutive_chars_max: '最长连续重复字符数', num_query_params: '查询参数个数'
    };
    window.clearExplanation = () => { const panel = document.getElementById('prediction-explanation'); if (panel) panel.hidden = true; };
    window.renderExplanation = result => {
        const panel = document.getElementById('prediction-explanation');
        if (!panel) return;
        panel.hidden = false;
        const explanation = result.explanation;
        const top = document.getElementById('contribution-top');
        const rest = document.getElementById('contribution-rest');
        top.replaceChildren(); rest.replaceChildren();
        if (!explanation || !Array.isArray(explanation.features)) {
            set('explanation-context', '当前结果没有可用的模型解释。');
            set('explanation-equation', ''); set('explanation-source', '');
            return;
        }
        const features = [...explanation.features].sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution));
        const total = features.reduce((sum, item) => sum + item.contribution, 0);
        const remainder = features.slice(10).reduce((sum, item) => sum + item.contribution, 0);
        set('explanation-context', `网址：${result.url} · 模型 ${explanation.model_version} · ${explanation.specialist_active ? '基础模型 + 结构专用模型（权重 ' + pct(explanation.specialist_weight) + '）' : '仅基础模型'} · 判定阈值 ${pct(explanation.threshold)}`);
        set('explanation-equation', `基准分数 ${pct(explanation.baseline)} + 全部贡献 ${pp(total)} = 本次 ML 风险分数 ${pct(explanation.score)}；其余特征合计 ${pp(remainder)}。`);
        set('explanation-source', result.phishtank_match
            ? '本次还命中了 PhishTank 历史库，数据库命中独立影响最终判定，不计入下方特征贡献。模型分数不是已校准的真实概率。'
            : '本次最终判定来自模型阈值；未命中 PhishTank 历史库不代表安全。贡献仅解释 ML 分数，不能证明网站真实身份。');
        const max = Math.max(...features.map(f => Math.abs(f.contribution)), 1e-12);
        features.forEach((feature, index) => {
            const row = document.createElement('tr');
            const name = document.createElement('td');
            name.textContent = labels[feature.name] || feature.name;
            const code = document.createElement('small');
            code.className = 'd-block text-muted';
            code.textContent = feature.name;
            if (labels[feature.name]) name.append(code);
            const value = document.createElement('td');
            value.textContent = typeof feature.value === 'boolean' ? (feature.value ? '是' : '否')
                : Number.isInteger(feature.value) ? String(feature.value) : Number(feature.value).toFixed(3);
            const contribution = document.createElement('td');
            contribution.textContent = `${feature.contribution > 0 ? '提高' : feature.contribution < 0 ? '降低' : '无影响'} ${pp(feature.contribution)}`;
            contribution.style.color = feature.contribution > 0 ? '#a34100' : '#176493';
            const chart = document.createElement('td');
            const bar = document.createElement('div');
            bar.style.cssText = `height:10px;border-radius:4px;width:${Math.abs(feature.contribution) / max * 100}%;background:${feature.contribution > 0 ? '#d97706' : '#0284c7'}`;
            bar.setAttribute('aria-hidden', 'true');
            chart.append(bar); row.append(name, value, contribution, chart);
            (index < 10 ? top : rest).append(row);
        });
    };
    async function loadEvaluation() {
        const panel = document.getElementById('model-evaluation');
        if (!panel) return;
        const button = document.getElementById('refresh-evaluation');
        button.disabled = true;
        document.getElementById('evaluation-content').hidden = true;
        set('evaluation-status', '正在读取已加载模型的指标…');
        try {
            const response = await fetch('/api/v1/stats/model');
            if (!response.ok) throw new Error('HTTP ' + response.status);
            const data = await response.json();
            if (data.status !== 'available') throw new Error(data.message || '暂无评估数据');
            set('evaluation-status', `模型 ${data.version} · 判定阈值 ${pct(data.threshold)}`);
            for (const [id, value] of Object.entries({
                'metric-recall': data.recall, 'metric-f1': data.f1, 'metric-precision': data.precision,
                'metric-fpr': data.false_positive_rate
            })) set(id, pct(value));
            for (const [id, value] of Object.entries({
                'cm-tn': data.confusion_matrix[0][0], 'cm-fp': data.confusion_matrix[0][1],
                'cm-fn': data.confusion_matrix[1][0], 'cm-tp': data.confusion_matrix[1][1]
            })) set(id, value.toLocaleString());
            set('evaluation-samples', `共 ${data.samples.toLocaleString()} 个样本：正常 ${data.normal_samples.toLocaleString()}，钓鱼 ${data.phishing_samples.toLocaleString()}。训练/测试域名重叠：${data.domain_overlap == null ? '未提供' : data.domain_overlap}。划分：${data.split_method}`);
            set('evaluation-notes', data.notes);
            document.getElementById('evaluation-content').hidden = false;
        } catch (error) {
            set('evaluation-status', '评估数据暂不可用：' + error.message);
        } finally { button.disabled = false; }
    }
    document.getElementById('refresh-evaluation')?.addEventListener('click', loadEvaluation);
    loadEvaluation();
})();
