/**
 * benchmark.js — 对标分析页面（3步流程）
 */
const BenchmarkPage = {
    _rendered: false,
    _taskGenerationContext: null,

    render() {
        const container = document.getElementById('page-benchmark');
        if (!this._rendered) {
            container.innerHTML = this.template();
            this.bindEvents();
            this._rendered = true;
        }
        // 每次进入页面重新加载所有步骤
        this.syncSelectors();
        this.loadStep();
    },

    syncSelectors() {
        const product = document.getElementById('benchProduct');
        const month = document.getElementById('benchMonth');
        if (product) product.value = AppState.currentProduct;
        if (month) month.value = AppState.currentMonth;
    },

    template() {
        return `
            <div class="page-header">
                <h2>${Icons.scale()} 成本对标分析</h2>
                <p>两厂成本差异分解、归因分析及AI诊断</p>
            </div>

            <!-- Filter bar -->
            <div class="filter-bar">
                <div class="field">
                    <label>对标对象</label>
                    <select id="benchProduct">
                        ${AppState.products.map(p => {
                            const name = Utils.productName(p);
                            return `<option value="${name}" ${name === AppState.currentProduct ? 'selected' : ''}>${Utils.productLabel(p)}</option>`;
                        }).join('')}
                    </select>
                </div>
                <div class="field">
                    <label>月份</label>
                    <select id="benchMonth">
                        ${AppState.months.map(m =>
                            `<option value="${m}" ${m === AppState.currentMonth ? 'selected' : ''}>${m}</option>`
                        ).join('')}
                    </select>
                </div>
            </div>

            <!-- 3 Step cards -->
            <section class="kpi-grid cols-3">
                <div class="card bench-step" id="step-diff">
                    <div class="step-header">
                        <div class="step-num">1</div>
                        <h4 class="step-title">差异总览</h4>
                    </div>
                    <p class="step-desc" id="step1Desc">正在准备差异数据...</p>
                </div>
                <div class="card bench-step" id="step-breakdown">
                    <div class="step-header">
                        <div class="step-num">2</div>
                        <h4 class="step-title">结构分解</h4>
                    </div>
                    <p class="step-desc" id="step2Desc">等待差异分析完成...</p>
                </div>
                <div class="card bench-step" id="step-attribution">
                    <div class="step-header">
                        <div class="step-num">3</div>
                        <h4 class="step-title">AI 归因</h4>
                    </div>
                    <p class="step-desc" id="step3Desc">等待结构分解完成...</p>
                </div>
            </section>

            <!-- Diff table -->
            <section class="card" style="margin-bottom:24px">
                <div class="card-header"><h3 class="card-title">对标差异明细</h3></div>
                <div id="diffTable">${Utils.inlineLoading('加载差异数据...')}</div>
            </section>

            <section class="card" style="margin-bottom:24px">
                <div class="card-header"><h3 class="card-title">差异结构树</h3></div>
                <div id="structureTree">${Utils.inlineLoading('加载结构树...')}</div>
            </section>

            <!-- Chart + AI Insights -->
            <section class="chart-row benchmark-insights-stack" style="grid-template-columns:minmax(0, 1fr) !important;">
                <div class="card">
                    <div class="card-header"><h3 class="card-title">成本贡献分解</h3></div>
                    <div id="chartContribution" style="height:300px;width:100%"></div>
                </div>
                <div class="card">
                    <div class="card-header attribution-header">
                        <h3 class="card-title"><i data-lucide="sparkles" style="width:16px;height:16px"></i> AI 归因洞察</h3>
                        <div class="attribution-header-actions">
                            <button id="btnBenchReanalyze" type="button" class="btn btn-outline btn-sm" title="重新生成对标归因"><i data-lucide="refresh-cw"></i><span>重新分析</span></button>
                            <button id="btnBenchmarkGenerateTasks" type="button" class="btn btn-outline btn-sm" disabled title="根据当前对标归因生成整改任务"><i data-lucide="list-plus"></i><span>生成整改任务</span></button>
                            <span id="benchmarkAttributionBadge" class="status-pill error" style="display:none">重点分析</span>
                        </div>
                    </div>
                    <div id="benchmarkAttributionSummary" class="attribution-summary">${Utils.inlineLoading('提取差异摘要...')}</div>
                    <details id="benchmarkAttributionDetails" class="attribution-details" style="display:none">
                        <summary>展开完整归因分析</summary>
                        <div id="attributionContent" style="margin-top:12px">${Utils.inlineLoading('AI分析中...')}</div>
                    </details>
                </div>
            </section>
        `;
    },

    bindEvents() {
        const product = document.getElementById('benchProduct');
        const month = document.getElementById('benchMonth');
        const refresh = () => {
            AppState.currentProduct = product.value;
            AppState.currentMonth = month.value;
            this.loadStep();
        };
        if (product) product.addEventListener('change', refresh);
        if (month) month.addEventListener('change', refresh);
        const reanalyze = document.getElementById('btnBenchReanalyze');
        if (reanalyze) reanalyze.addEventListener('click', () => this.loadStep(true));
        const generateTasks = document.getElementById('btnBenchmarkGenerateTasks');
        if (generateTasks) generateTasks.addEventListener('click', () => this.generateTasks());
    },

    /* ============ 加载所有步骤（依次） ============ */
    loadStep(forceAttribution = false) {
        const product = document.getElementById('benchProduct').value;
        const month = document.getElementById('benchMonth').value;
        const params = { product, month };
        this.setTaskGenerationContext(null);

        document.getElementById('step1Desc').textContent = '正在加载差异数据...';
        document.getElementById('step2Desc').textContent = '等待差异分析完成...';
        document.getElementById('step3Desc').textContent = '等待结构分解完成...';

        // 重置步骤卡片状态
        document.getElementById('step-diff').classList.remove('done');
        document.getElementById('step-breakdown').classList.remove('done');
        document.getElementById('step-attribution').classList.remove('done');
        document.getElementById('diffTable').innerHTML = Utils.inlineLoading('加载差异数据...');
        document.getElementById('structureTree').innerHTML = Utils.inlineLoading('加载结构树...');
        document.getElementById('attributionContent').innerHTML = Utils.inlineLoading('AI分析中...');
        document.getElementById('benchmarkAttributionSummary').innerHTML = Utils.inlineLoading('提取差异摘要...');
        document.getElementById('benchmarkAttributionDetails').style.display = 'none';

        this.loadDiff(params, forceAttribution);
    },

    /* ============ Step 1: 差异总览 ============ */
    async loadDiff(params, forceAttribution = false) {
        try {
            const data = await Utils.api(`/api/benchmark/diff?${Utils.qs(params)}`);
            if (data.error) throw new Error(data.error);
            this.renderDiffTable(data);
        } catch (e) {
            this.renderError('diffTable', `差异数据加载失败：${e.message || '请稍后重试'}`);
            document.getElementById('step1Desc').textContent = '差异分析失败';
            return;
        }
        // Step1完成后，启动Step2
        document.getElementById('step1Desc').textContent = `差异分析完成，两厂成本差异${this._lastDiffTotal || '显著'}`;
        document.getElementById('step-diff').classList.add('done');
        document.getElementById('step2Desc').textContent = '正在加载结构分解...';
        this.loadBreakdown(params, forceAttribution);
    },

    renderError(id, message) {
        const target = document.getElementById(id);
        if (target) target.innerHTML = `<div class="empty-state"><p>${message}</p></div>`;
    },

    renderDiffTable(data) {
        const target = document.getElementById('diffTable');
        if (!target) return;
        const rows = data.rows || [];
        if (rows.length === 0) { target.innerHTML = Utils.emptyState('', '暂无对标差异数据'); return; }
        const f1 = data.factory1_name || '中药一厂';
        const f2 = data.factory2_name || '中药二厂';
        const headers = ['成本项目', f1, f2, '差异', '差异率(%)', '方向'];

        // 把"单位成本/总成本"行排到表格底部
        const normalRows = rows.filter(r => !(r.dimension && (r.dimension.includes('单位成本') || r.dimension.includes('总成本'))));
        const totalRows = rows.filter(r => r.dimension && (r.dimension.includes('单位成本') || r.dimension.includes('总成本')));
        const orderedRows = [...normalRows, ...totalRows];

        let html = '<div class="data-table-wrapper"><table class="data-table"><thead><tr>';
        headers.forEach(h => { html += `<th>${h}</th>`; });
        html += '</tr></thead><tbody>';
        orderedRows.forEach(r => {
            const isTotal = totalRows.includes(r);
            const rate = r.diff_rate;
            const dirLower = (r.direction || '').toLowerCase();
            const isHigh = r.direction && r.direction.includes('高');
            html += `<tr${isTotal ? ' class="total-row"' : ''}>`;
            html += `<td>${r.dimension || ''}</td>`;
            html += `<td class="num">${Utils.fmtNum(r.factory1, 2)}</td>`;
            html += `<td class="num">${Utils.fmtNum(r.factory2, 2)}</td>`;
            html += `<td class="num">${r.diff_amount > 0 ? '+' : ''}${Utils.fmtNum(r.diff_amount, 2)}</td>`;
            html += `<td class="num">${rate > 0 ? '+' : ''}${rate.toFixed(2)}%</td>`;
            html += `<td style="text-align:center"><span class="status-pill ${isHigh ? 'warning' : 'success'}">${r.direction || '--'}</span></td>`;
            html += '</tr>';
        });
        html += '</tbody></table></div>';
        target.innerHTML = html;

        // 记录总差异用于步骤描述
        const unitRow = rows.find(r => r.dimension && r.dimension.includes('单位成本'));
        this._lastDiffTotal = unitRow ? `为 ${unitRow.diff_amount > 0 ? '+' : ''}${Utils.fmtNum(unitRow.diff_amount, 2)} 元/盒` : '显著';
    },

    /* ============ Step 2: 因素贡献 ============ */
    async loadBreakdown(params, forceAttribution = false) {
        try {
            const [data, tree] = await Promise.all([
                Utils.api(`/api/benchmark/breakdown?${Utils.qs(params)}`, { timeoutMs: 30000 }),
                Utils.api(`/api/benchmark/structure?${Utils.qs(params)}`, { timeoutMs: 30000 }),
            ]);
            if (data.error) throw new Error(data.error);
            if (tree.error) throw new Error(tree.error);
            this.renderContributionChart(data);
            this.renderStructureTree(tree);
        } catch (e) {
            this.renderError('structureTree', `结构分解加载失败：${e.message || '请稍后重试'}`);
            document.getElementById('step2Desc').textContent = '结构分解失败';
            return;
        }
        // Step2完成后，启动Step3
        document.getElementById('step2Desc').textContent = '结构分解完成，材料贡献占比最高';
        document.getElementById('step-breakdown').classList.add('done');
        document.getElementById('step3Desc').textContent = '正在进行AI归因分析...';
        this.loadAttribution(params, forceAttribution);
    },

    renderStructureTree(data) {
        const target = document.getElementById('structureTree');
        if (!target) return;
        const root = data.tree || {};
        const money = value => Utils.fmtNum(value, 2);
        const materialRows = children => (children || []).map(child => `
            <tr>
                <td>${Utils.escapeHtml(child.name || '')}</td>
                <td class="num">${money(child.unit_cost)} 元/盒</td>
                <td class="num">${money(child.total_cost)} 元</td>
                <td class="num">${money(child.ratio)}%</td>
            </tr>`).join('');
        const nodes = (root.children || []).map(node => {
            const isMaterial = node.name === '直接材料';
            return `<details class="benchmark-factor-card ${isMaterial ? 'is-material' : ''}" ${isMaterial ? 'open' : ''}>
                <summary class="benchmark-factor-head">
                    <div class="benchmark-factor-left">
                        <span class="benchmark-tree-arrow" aria-hidden="true">▶</span>
                        <div class="benchmark-factor-title"><span class="benchmark-tree-level">成本要素</span><h4>${Utils.escapeHtml(node.name || '')}</h4></div>
                    </div>
                    <div class="benchmark-factor-metrics">
                        <span>差异 <strong>${node.diff_amount > 0 ? '+' : ''}${money(node.diff_amount)} 元/盒</strong></span>
                        <span>贡献 <strong>${money(node.contribution)}%</strong></span>
                    </div>
                </summary>
                ${isMaterial && node.children && node.children.length ? `
                    <div class="benchmark-material-panel">
                        <div class="benchmark-material-title">原材料明细（中药一厂）</div>
                        <div class="data-table-wrapper"><table class="data-table benchmark-material-table">
                            <thead><tr><th>原材料</th><th>单位成本</th><th>总成本</th><th>材料占比</th></tr></thead>
                            <tbody>${materialRows(node.children)}</tbody>
                        </table></div>
                    </div>` : `<div class="benchmark-factor-empty">暂无可下钻明细</div>`}
            </details>`;
        }).join('');
        target.innerHTML = `<div class="benchmark-tree-root">
            <div><span class="benchmark-tree-level">总差异</span><strong>${root.name || '单位成本差异'}</strong></div>
            <span class="num">${root.diff_amount > 0 ? '+' : ''}${money(root.diff_amount)} 元/盒</span>
        </div><div class="benchmark-factor-list">${nodes}</div>`;
    },

    renderContributionChart(data) {
        const chart = Utils.getChart('chartContribution');
        if (!chart) return;
        const breakdown = data.breakdown || [];
        const factors = breakdown.map(b => b.dimension || '');
        const values = breakdown.map(b => b.contribution || 0);
        chart.setOption({
            tooltip: {
                trigger: 'axis',
                axisPointer: { type: 'shadow' },
                backgroundColor: 'rgba(255,255,255,0.96)',
                borderColor: '#e0e0e0',
                formatter: function (params) {
                    const p = params[0];
                    const idx = p.dataIndex;
                    const item = breakdown[idx] || {};
                    return `${p.name}<br/>贡献度: ${p.value > 0 ? '+' : ''}${p.value.toFixed(1)}%<br/>差异额: ${item.diff_amount > 0 ? '+' : ''}${(item.diff_amount || 0).toFixed(2)} 元/盒`;
                }
            },
            grid: { left: 100, right: 40, top: 30, bottom: 40 },
            xAxis: {
                type: 'value',
                name: '贡献度(%)',
                axisLabel: { color: '#616161', formatter: '{value}%' },
                splitLine: { lineStyle: { type: 'dashed', color: '#eee' } }
            },
            yAxis: { type: 'category', data: factors, axisLabel: { color: '#616161' } },
            series: [{
                type: 'bar',
                data: values.map(v => ({
                    value: v,
                    itemStyle: {
                        color: v > 0 ? '#e53935' : '#43a047',
                        borderRadius: v > 0 ? [0, 4, 4, 0] : [4, 0, 0, 4]
                    }
                })),
                label: {
                    show: true,
                    position: 'outside',
                    formatter: function (p) { return (p.value > 0 ? '+' : '') + p.value.toFixed(1) + '%'; },
                    fontSize: 12,
                    color: '#333'
                },
                barWidth: '45%'
            }]
        });
    },

    /* ============ Step 3: AI归因 ============ */
    async loadAttribution(params, force = false) {
        const target = document.getElementById('attributionContent');
        if (!target) return;
        target.innerHTML = Utils.inlineLoading('AI正在分析中，请稍候...');

        try {
            const data = await Utils.api(`/api/benchmark/attribution?${Utils.qs({ ...params, force: force ? 'true' : undefined })}`, {
                timeoutMs: 90000,
            });
            if (data.error) throw new Error(data.error);
            this.renderAttribution({
                attribution: data.attribution,
                diff_data: data.diff_data,
                suggestions: data.suggestions,
                ragSources: data.rag_sources,
            });
            this.setTaskGenerationContext({
                product: data.product || params.product,
                month: data.month || params.month,
                conclusion: this.analysisWithoutSuggestions(data.attribution),
                evidence: this.buildTaskEvidence(data),
            });
        } catch (e) {
            this.renderError('attributionContent', `AI归因不可用：${e.message || '请稍后重试'}`);
            this.renderError('benchmarkAttributionSummary', 'AI归因暂时不可用，请稍后重试。');
            document.getElementById('step3Desc').textContent = 'AI归因不可用';
            return;
        }
        // Step3完成后更新描述
        setTimeout(() => {
            document.getElementById('step3Desc').textContent = 'AI归因分析完成，已识别3项关键因素';
            document.getElementById('step-attribution').classList.add('done');
        }, 1500);
    },

    sanitizeAttributionMarkdown(value) {
        const lines = String(value || '').replace(/\r/g, '').replace(/```[\s\S]*?```/g, '').replace(/```/g, '').split('\n');
        const isTableRow = line => line.includes('|') && line.split('|').filter(cell => cell.trim()).length >= 2;
        const isTableDivider = line => /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
        const plain = text => String(text || '')
            .replace(/\*\*(.*?)\*\*/g, '$1')
            .replace(/__(.*?)__/g, '$1')
            .replace(/`([^`]+)`/g, '$1')
            .replace(/\*+/g, '')
            .replace(/_+/g, '');
        const output = [];
        for (const line of lines) {
            if (isTableDivider(line)) continue;
            if (isTableRow(line)) {
                const cells = line.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map(cell => plain(cell).trim()).filter(Boolean);
                if (cells.length >= 2) output.push(`- ${cells[0]}：${cells.slice(1).join('；')}`);
                continue;
            }
            output.push(plain(line));
        }
        return output.join('\n').replace(/\n{3,}/g, '\n\n').trim();
    },

    analysisWithoutSuggestions(value) {
        const text = this.sanitizeAttributionMarkdown(value);
        const suggestionHeading = /^\s*#{1,2}\s*改进建议\s*$/m;
        const match = suggestionHeading.exec(text);
        return (match ? text.slice(0, match.index) : text).trim();
    },

    renderAttribution(data) {
        const target = document.getElementById('attributionContent');
        const summary = document.getElementById('benchmarkAttributionSummary');
        const details = document.getElementById('benchmarkAttributionDetails');
        const badge = document.getElementById('benchmarkAttributionBadge');
        if (!target || !summary || !details) return;

        // 模型正文可能包含建议，页面只用一次结构化建议清单承载，避免重复。
        const attribution = this.analysisWithoutSuggestions(data.attribution || '暂无分析结果');
        const diffData = data.diff_data || {};
        const diffRows = diffData.rows || [];
        const suggestions = data.suggestions || [];
        const ragSources = Array.isArray(data.ragSources) ? data.ragSources.filter(Boolean) : [];
        const unit = diffRows.find(row => String(row.dimension || '').includes('单位成本')) || {};
        const keyRows = diffRows.filter(row => Math.abs(Number(row.diff_rate || 0)) >= 5 && !String(row.dimension || '').includes('产量'));
        const main = keyRows.sort((a, b) => Math.abs(b.diff_amount || 0) - Math.abs(a.diff_amount || 0))[0];
        const unitText = unit.diff_amount === undefined
            ? '单位成本暂无对标数据'
            : `单位成本${unit.direction || ''} ${Math.abs(unit.diff_amount).toFixed(2)} 元/盒（${unit.diff_rate > 0 ? '+' : ''}${Number(unit.diff_rate).toFixed(2)}%）`;
        const driverText = main
            ? `主要驱动因素为${main.dimension}，差异${main.diff_amount > 0 ? '+' : ''}${Number(main.diff_amount).toFixed(2)} 元/盒。`
            : '当前未识别超过 5% 的成本要素差异。';
        summary.innerHTML = `<strong>${unitText}</strong><span>${driverText}</span>`;
        badge.style.display = keyRows.length ? '' : 'none';

        const markdownToHtml = (typeof DashboardPage !== 'undefined' && DashboardPage.markdownToHtml)
            ? DashboardPage.markdownToHtml.bind(DashboardPage)
            : value => `<p>${Utils.escapeHtml(String(value || ''))}</p>`;
        let html = markdownToHtml(attribution);
        if (suggestions.length) {
            html += '<h2>改进建议</h2><ol class="benchmark-suggestion-list">';
            suggestions.forEach(item => {
                const suggestionHtml = markdownToHtml(this.sanitizeAttributionMarkdown(item.suggestion || ''));
                html += `<li><div class="benchmark-suggestion-content">${suggestionHtml}</div><span class="benchmark-suggestion-meta">责任部门：${Utils.escapeHtml(item.department || '--')} · 优先级：${Utils.escapeHtml(item.priority || '--')} · 截止：${Utils.escapeHtml(item.deadline || '--')}</span></li>`;
            });
            html += '</ol>';
        }
        if (ragSources.length) {
            html += `<div class="rag-citation"><i data-lucide="book-open"></i><span>知识库参考：${ragSources.map(source => Utils.escapeHtml(String(source))).join('；')}</span></div>`;
        }
        target.innerHTML = html;
        details.style.display = '';
        const normalized = String(attribution || '').trim();
        const sentences = normalized.split(/(?<=[。！？；])\s*|\n+/).filter(Boolean);
        details.open = sentences.length <= 2 && normalized.length <= 240;
        details.querySelector('summary').textContent = details.open ? '收起归因分析' : '展开完整归因分析';
    },

    buildTaskEvidence(data) {
        return {
            rag_used: Boolean(data.rag_used),
            rag_sources: data.rag_sources || [],
            benchmark_differences: (data.diff_data?.rows || []).map(row => ({
                dimension: row.dimension,
                diff_amount: row.diff_amount,
                diff_rate: row.diff_rate,
                direction: row.direction,
            })),
            improvement_suggestions: (data.suggestions || []).map(item => ({
                department: item.department,
                priority: item.priority,
                suggestion: item.suggestion,
                source_dimension: item.source_dimension,
                deadline: item.deadline,
            })),
        };
    },

    setTaskGenerationContext(context) {
        this._taskGenerationContext = context;
        const button = document.getElementById('btnBenchmarkGenerateTasks');
        if (button) button.disabled = !String(context?.conclusion || '').trim();
    },

    async generateTasks() {
        const context = this._taskGenerationContext;
        const conclusion = String(context?.conclusion || '').trim();
        if (!conclusion) return;
        const month = String(context.month || AppState.currentMonth || '').trim().replace(/^(\d{4})-(\d)$/, '$1-0$2');
        const evidence = context.evidence && typeof context.evidence === 'object' && !Array.isArray(context.evidence)
            ? context.evidence : {};
        const button = document.getElementById('btnBenchmarkGenerateTasks');
        if (!button) return;
        button.disabled = true;
        button.innerHTML = '<i data-lucide="loader-circle"></i><span>生成中...</span>';
        try {
            const data = await Utils.api('/api/rpa/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    product: String(context.product || AppState.currentProduct || '').trim(),
                    month,
                    analysis_scenario: 'benchmark_attribution',
                    attribution_conclusion: conclusion.slice(0, 50000),
                    analysis_evidence: evidence,
                }),
                timeoutMs: 120000,
            });
            if (window.TaskDraftDialog) {
                this.showTaskGenerationResult(true, `已生成${data.tasks_generated || 0}项候选任务，请在窗口中选择要保存的草稿。`);
                TaskDraftDialog.open(data.tasks || [], result => {
                    this.showTaskGenerationResult(true, `已生成${result.tasks_saved || 0}项整改任务草稿，请前往模块四发送至 RPA。`);
                });
            } else {
                this.showTaskGenerationResult(true, `已根据成本对标归因生成${data.tasks_generated || 0}项整改任务。`);
            }
        } catch (error) {
            this.showTaskGenerationResult(false, error.message.includes('HTTP 422')
                ? '归因上下文无效，请重新分析后再生成任务。'
                : '整改任务生成失败，请稍后重试。');
        } finally {
            button.innerHTML = '<i data-lucide="list-plus"></i><span>生成整改任务</span>';
            button.disabled = !String(this._taskGenerationContext?.conclusion || '').trim();
            if (typeof refreshIcons === 'function') refreshIcons();
        }
    },

    showTaskGenerationResult(success, message) {
        document.getElementById('benchmarkTaskGenerationToast')?.remove();
        const notice = document.createElement('div');
        notice.id = 'benchmarkTaskGenerationToast';
        notice.className = `operation-toast${success ? '' : ' error'}`;
        notice.innerHTML = `<i data-lucide="${success ? 'circle-check' : 'circle-alert'}"></i><span>${Utils.escapeHtml(message)}${success ? ' <a href="#rpa">查看任务</a>' : ''}</span>`;
        document.body.appendChild(notice);
        if (typeof refreshIcons === 'function') refreshIcons();
        setTimeout(() => notice.remove(), 6000);
    }
};
