/**
 * dashboard.js — 成本看板页面（白卡 SaaS 风格）
 * 适配后端真实数据结构：three-dim / trend / structure / waterfall /
 * material-detail / overhead-detail / labor-metrics / industry-bench
 */
const DashboardPage = {
    _rendered: false,
    _loadVersion: 0,
    _attributionRequestId: 0,
    _attributionRenderedVersion: 0,
    _attributionController: null,
    _attributionCache: new Map(),
    _currentAlerts: [],
    _taskGenerationContext: null,
    // v2 用于淘汰旧版本因超时产生的预设兜底文本缓存。
    _attributionStoragePrefix: 'pharmacost.dashboard.attribution.v2:',
    _heatmapResizeObserver: null,

    render() {
        const container = document.getElementById('page-dashboard');
        if (!this._rendered) {
            container.innerHTML = this.template();
            this.bindEvents();
            this._rendered = true;
        }
        this.syncSelectors();
        this.loadAll();
    },

    template() {
        return `
            <div class="filter-bar dashboard-filter" aria-label="看板筛选条件">
                <div class="field">
                    <label for="dashProduct">分析产品</label>
                    <select id="dashProduct"></select>
                </div>
                <div class="field">
                    <label for="dashMonth">会计月</label>
                    <select id="dashMonth"></select>
                </div>
            </div>

            <!-- KPI 指标卡 -->
            <section class="kpi-grid" id="dashMetrics">
                <div class="card kpi-card" id="cardOutput">
                    <div class="kpi-label">当月产量</div>
                    <div class="kpi-value" id="kpiOutput">--</div>
                    <div class="kpi-meta" id="kpiOutputMeta"></div>
                </div>
                <div class="card kpi-card" id="cardUnitCost">
                    <div class="kpi-label">单位成本</div>
                    <div class="kpi-value" id="kpiUnitCost">--</div>
                    <div class="kpi-meta" id="kpiUnitCostMeta"></div>
                </div>
                <div class="card kpi-card" id="cardMaterial">
                    <div class="kpi-label">直接材料</div>
                    <div class="kpi-value" id="kpiMaterial">--</div>
                    <div class="kpi-meta" id="kpiMaterialMeta"></div>
                </div>
                <div class="card kpi-card" id="cardTotal">
                    <div class="kpi-label">总成本</div>
                    <div class="kpi-value" id="kpiTotal">--</div>
                    <div class="kpi-meta" id="kpiTotalMeta"></div>
                </div>
            </section>

            <!-- 三维对比表 -->
            <div class="card" style="margin-bottom:24px">
                <div class="card-header">
                    <h3 class="card-title"><i data-lucide="table-2" style="width:16px;height:16px"></i> 三维成本对比</h3>
                </div>
                <div class="table-container" id="threeDimTable">${Utils.inlineLoading()}</div>
            </div>

            <div class="card" id="dashboardAlerts" style="margin-bottom:24px;display:none">
                <div class="card-header attribution-header">
                    <h3 class="card-title"><i data-lucide="triangle-alert" style="width:16px;height:16px"></i> 波动告警与归因分析</h3>
                    <div class="attribution-header-actions">
                        <button id="reanalyzeAttribution" type="button" class="btn btn-outline btn-sm" title="跳过缓存并重新生成归因分析"><i data-lucide="refresh-cw"></i><span>重新分析</span></button>
                        <button id="btnDashboardGenerateTasks" type="button" class="btn btn-outline btn-sm" disabled title="根据当前归因分析生成整改任务"><i data-lucide="list-plus"></i><span>生成整改任务</span></button>
                        <span id="attributionBadge" class="status-pill error" style="display:none">重点分析</span>
                    </div>
                </div>
                <div id="alertList"></div>
                <details id="attributionDetails" class="attribution-details" style="display:none">
                    <summary>展开完整归因分析</summary>
                    <div id="attributionText" style="margin-top:12px">${Utils.inlineLoading('生成归因分析...')}</div>
                </details>
                <div id="attributionSources" class="attribution-sources" style="display:none"></div>
            </div>

            <!-- 成本趋势 -->
            <section class="chart-row" style="margin-bottom:24px">
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title"><i data-lucide="trending-up" style="width:16px;height:16px"></i> 成本趋势（近6个月）</h3>
                    </div>
                    <div id="chartTrend" style="height:320px;width:100%"></div>
                </div>
            </section>

            <!-- 多产品交叉分析热力图 -->
            <section class="chart-row" style="margin-bottom:24px">
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title"><i data-lucide="grid-3x3" style="width:16px;height:16px"></i> 多产品成本热力图</h3>
                        <select id="heatmapMetric" class="selector" aria-label="热力图成本要素">
                            <option value="unit_cost">单位成本</option>
                            <option value="material_cost">直接材料</option>
                            <option value="labor_cost">直接人工</option>
                            <option value="overhead_cost">制造费用</option>
                        </select>
                    </div>
                    <div id="chartHeatmap" style="height:360px;width:100%"></div>
                </div>
            </section>

            <!-- 结构 + 瀑布图 -->
            <section class="chart-row cols-2" style="margin-bottom:24px">
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title"><i data-lucide="pie-chart" style="width:16px;height:16px"></i> 成本结构</h3>
                    </div>
                    <div id="chartStructure" style="height:320px;width:100%"></div>
                </div>
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title"><i data-lucide="bar-chart" style="width:16px;height:16px"></i> 成本瀑布图</h3>
                    </div>
                    <div id="chartWaterfall" style="height:320px;width:100%"></div>
                </div>
            </section>

            <!-- 原材料明细 + 制造费用明细 -->
            <div class="chart-row detail-cards" style="margin-bottom:24px">
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title"><i data-lucide="layers" style="width:16px;height:16px"></i> 原材料消耗明细</h3>
                    </div>
                    <div class="table-container" id="materialDetailTable">${Utils.inlineLoading()}</div>
                </div>
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title"><i data-lucide="factory" style="width:16px;height:16px"></i> 制造费用明细</h3>
                    </div>
                    <div class="table-container" id="overheadDetailTable">${Utils.inlineLoading()}</div>
                </div>
            </div>

            <!-- 人工指标卡片 -->
            <div class="card" style="margin-bottom:24px">
                <div class="card-header">
                    <h3 class="card-title"><i data-lucide="users" style="width:16px;height:16px"></i> 人工工时指标</h3>
                </div>
                <div class="kpi-grid" id="laborMetrics">${Utils.inlineLoading('加载人工指标...')}</div>
            </div>

            <!-- 行业对标 -->
            <section class="chart-row" style="margin-bottom:24px">
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title"><i data-lucide="bar-chart-2" style="width:16px;height:16px"></i> 行业成本对标</h3>
                    </div>
                    <div id="chartBench" style="height:380px;width:100%"></div>
                </div>
            </section>
        `;
    },

    bindEvents() {
        const product = document.getElementById('dashProduct');
        const month = document.getElementById('dashMonth');
        const reanalyze = document.getElementById('reanalyzeAttribution');
        const generateTasks = document.getElementById('btnDashboardGenerateTasks');
        if (product) product.addEventListener('change', () => {
            AppState.currentProduct = product.value;
            this.loadAll();
        });
        if (month) month.addEventListener('change', () => {
            AppState.currentMonth = month.value;
            this.loadAll();
        });
        if (reanalyze) reanalyze.addEventListener('click', () => this.reanalyzeAttribution());
        if (generateTasks) generateTasks.addEventListener('click', () => this.generateTasks());
    },

    syncSelectors() {
        const product = document.getElementById('dashProduct');
        const month = document.getElementById('dashMonth');
        if (!product || !month) return;
        const productOptions = AppState.products.map(item => {
            const name = Utils.productName(item);
            return `<option value="${Utils.escapeHtml(name)}">${Utils.escapeHtml(Utils.productLabel(item))}</option>`;
        }).join('');
        if (product.innerHTML !== productOptions) product.innerHTML = productOptions;
        const monthOptions = AppState.months.map(item => `<option value="${Utils.escapeHtml(item)}">${Utils.escapeHtml(item)}</option>`).join('');
        if (month.innerHTML !== monthOptions) month.innerHTML = monthOptions;
        product.value = AppState.currentProduct;
        month.value = AppState.currentMonth;
    },

    /* ============ 加载全部数据 ============ */
    async loadAll() {
        const version = ++this._loadVersion;
        const { product, month } = Utils.currentParams();
        if (this._attributionController) this._attributionController.abort();
        this._attributionController = new AbortController();
        const attributionRequestId = ++this._attributionRequestId;
        this._currentAlerts = [];
        this._taskGenerationContext = null;
        this.resetAsyncState();
        // 切换参数后先立即显示缓存或常规分析，避免等待三维接口/模型返回时出现空白。
        const attributionCache = this._getAttributionCache(`${product}::${month}`);
        this._attributionRenderedVersion = attributionCache ? version : 0;
        if (attributionCache) {
            this._currentAlerts = attributionCache.alerts || [];
            this.renderAttribution(attributionCache.analysis, Boolean(attributionCache.important || (attributionCache.alerts || []).length), attributionCache.ragSources || attributionCache.rag_sources || []);
            this.setTaskGenerationContext({
                product,
                month,
                conclusion: attributionCache.analysis,
                evidence: attributionCache.taskEvidence || { alerts: attributionCache.alerts || [] },
            });
        }
        // 归因接口与看板数据并行请求，不再等待三维表完成后才启动。
        void this.loadAttribution(product, month, [], version, attributionRequestId);
        this.loadThreeDim(product, month, version);   // 三维表 + 指标卡片
        this.loadTrend(product, version);
        this.loadHeatmap(version);
        this.loadStructure(product, month, version);
        this.loadWaterfall(product, month, version);
        this.loadMaterialDetail(product, month, version);
        this.loadOverheadDetail(product, month, version);
        this.loadLaborMetrics(product, month, version);
        this.loadIndustryBench(product, version);
    },

    isCurrent(version) {
        return version === this._loadVersion && AppState.currentPage === 'dashboard';
    },

    resetAsyncState() {
        const card = document.getElementById('dashboardAlerts');
        const list = document.getElementById('alertList');
        const full = document.getElementById('attributionText');
        const details = document.getElementById('attributionDetails');
        const badge = document.getElementById('attributionBadge');
        this.setTaskGenerationContext(null);
        if (card) card.style.display = '';
        if (list) list.innerHTML = '';
        if (full) full.innerHTML = Utils.inlineLoading('正在更新归因分析...');
        if (details) {
            details.style.display = '';
            details.open = true;
        }
        if (badge) badge.style.display = 'none';
        this.setReanalyzeState(false);
    },

    /* ============ 三维对比 + 指标卡片 ============ */
    async loadThreeDim(product, month, version) {
        try {
            const data = await Utils.api(`/api/dashboard/three-dim?${Utils.qs({ product, month })}`);
            if (!this.isCurrent(version)) return;
            if (data.error) throw new Error(data.error);
            this.renderThreeDimTable(data);
            this.renderKpiCards(data);
            this._currentAlerts = data.alerts || [];
            this.renderAlerts(data.alerts || []);
            // 归因请求并行执行；若缓存/模型已先返回，则不要用临时内容覆盖它。
            if (this._attributionRenderedVersion !== version) {
                const cached = this._getAttributionCache(`${product}::${month}`);
                if (cached) {
                    this._attributionRenderedVersion = version;
                    this.renderAttribution(cached.analysis, Boolean(cached.important || (cached.alerts || []).length), cached.ragSources || cached.rag_sources || []);
                }
            }
        } catch (e) {
            if (!this.isCurrent(version)) return;
            const mock = this._mockThreeDim();
            this.renderThreeDimTable(mock);
            this.renderKpiCards(mock);
            if (this._attributionRenderedVersion !== version) {
                const cached = this._getAttributionCache(`${product}::${month}`);
                if (cached) {
                    this._attributionRenderedVersion = version;
                    this._currentAlerts = cached.alerts || [];
                    this.renderAlerts(cached.alerts || []);
                    this.renderAttribution(cached.analysis, Boolean(cached.important || (cached.alerts || []).length), cached.ragSources || cached.rag_sources || []);
                }
            }
        }
    },

    async loadAttribution(product, month, alerts, version, requestId = this._attributionRequestId, force = false) {
        const cacheKey = `${product}::${month}`;
        const cached = this._getAttributionCache(cacheKey);
        if (!force && cached && this.isAttributionCurrent(version, requestId)) {
            this._attributionRenderedVersion = version;
            this.renderAlerts(cached.alerts || alerts);
            this.renderAttribution(cached.analysis, Boolean(cached.important || (cached.alerts || alerts).length), cached.ragSources || cached.rag_sources || []);
            return;
        }
        try {
            const data = await Utils.api(`/api/dashboard/attribution?${Utils.qs({ product, month, force: force ? 'true' : undefined })}`, {
                signal: this._attributionController && this._attributionController.signal,
                // 模型响应通常需要 15-30 秒，归因请求不应过早回退到预设模板。
                timeoutMs: 90000
            });
            if (!this.isAttributionCurrent(version, requestId)) return;
            const result = {
                analysis: data.analysis || this.regularAttribution(product, month),
                alerts: data.alerts || alerts,
                important: Boolean(data['重点分析'] || (data.alerts || alerts).length),
                source: data.analysis_source || 'ai',
                ragSources: data.rag_sources || [],
                taskEvidence: this.buildTaskEvidence(data),
            };
            if (result.source !== 'fallback') this._setAttributionCache(cacheKey, result);
            this._attributionRenderedVersion = version;
            this.renderAlerts(result.alerts);
            this.renderAttribution(result.analysis, result.important, result.ragSources);
            this.setTaskGenerationContext({ product, month, conclusion: result.analysis, evidence: result.taskEvidence });
        } catch (e) {
            if (e.name === 'AbortError') return;
            if (!this.isAttributionCurrent(version, requestId)) return;
            const result = {
                analysis: alerts.length ? '## 重点分析\n\n重点波动已告警，请结合采购价格、单耗和生产工艺参数进一步核查。' : this.regularAttribution(product, month),
                alerts,
                important: alerts.length > 0,
                source: 'fallback',
            };
            this._attributionRenderedVersion = version;
            this.renderAttribution(result.analysis, result.important, result.ragSources);
        }
    },

    isAttributionCurrent(version, requestId) {
        return this.isCurrent(version) && requestId === this._attributionRequestId;
    },

    async reanalyzeAttribution() {
        const button = document.getElementById('reanalyzeAttribution');
        if (button && button.disabled) return;
        const { product, month } = Utils.currentParams();
        const version = this._loadVersion;
        const cacheKey = `${product}::${month}`;
        this._deleteAttributionCache(cacheKey);
        this.setTaskGenerationContext(null);
        if (this._attributionController) this._attributionController.abort();
        this._attributionController = new AbortController();
        const requestId = ++this._attributionRequestId;
        this.setReanalyzeState(true);
        const full = document.getElementById('attributionText');
        const details = document.getElementById('attributionDetails');
        if (full) full.innerHTML = Utils.inlineLoading('正在重新生成归因分析...');
        if (details) { details.style.display = ''; details.open = true; }
        try {
            await this.loadAttribution(product, month, this._currentAlerts, version, requestId, true);
        } finally {
            if (requestId === this._attributionRequestId) this.setReanalyzeState(false);
        }
    },

    setReanalyzeState(loading) {
        const button = document.getElementById('reanalyzeAttribution');
        if (!button) return;
        button.disabled = loading;
        button.innerHTML = loading
            ? '<i data-lucide="loader-circle"></i><span>分析中...</span>'
            : '<i data-lucide="refresh-cw"></i><span>重新分析</span>';
        if (typeof refreshIcons === 'function') refreshIcons();
    },

    buildTaskEvidence(data) {
        const context = data.data || {};
        const dashboard = context.dashboard || {};
        return {
            alerts: data.alerts || [],
            rag_used: Boolean(data.rag_used),
            rag_sources: data.rag_sources || [],
            key_metrics: (dashboard.rows || []).map(row => ({
                metric: row.metric,
                current: row.current,
                mom_change: row.mom_change,
                budget_deviation: row.budget_deviation,
            })),
            waterfall: (context.waterfall?.items || []).map(item => ({
                name: item.name,
                value: item.value,
                contribution: item.contribution,
            })),
        };
    },

    setTaskGenerationContext(context) {
        this._taskGenerationContext = context;
        const button = document.getElementById('btnDashboardGenerateTasks');
        if (button) button.disabled = !String(context?.conclusion || '').trim();
    },

    async generateTasks() {
        const context = this._taskGenerationContext;
        const conclusion = String(context?.conclusion || '').trim();
        if (!conclusion) return;
        const month = String(context.month || AppState.currentMonth || '').trim().replace(/^(\d{4})-(\d)$/, '$1-0$2');
        const evidence = context.evidence && typeof context.evidence === 'object' && !Array.isArray(context.evidence)
            ? context.evidence : {};
        const button = document.getElementById('btnDashboardGenerateTasks');
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
                    analysis_scenario: 'dashboard_attribution',
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
                this.showTaskGenerationResult(true, `已根据成本看板归因生成${data.tasks_generated || 0}项整改任务。`);
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
        document.getElementById('dashboardTaskGenerationToast')?.remove();
        const notice = document.createElement('div');
        notice.id = 'dashboardTaskGenerationToast';
        notice.className = `operation-toast${success ? '' : ' error'}`;
        notice.innerHTML = `<i data-lucide="${success ? 'circle-check' : 'circle-alert'}"></i><span>${Utils.escapeHtml(message)}${success ? ' <a href="#rpa">查看任务</a>' : ''}</span>`;
        document.body.appendChild(notice);
        if (typeof refreshIcons === 'function') refreshIcons();
        setTimeout(() => notice.remove(), 6000);
    },

    _getAttributionCache(cacheKey) {
        const memoryValue = this._attributionCache.get(cacheKey);
        if (memoryValue) return memoryValue;
        try {
            const raw = sessionStorage.getItem(this._attributionStoragePrefix + encodeURIComponent(cacheKey));
            if (!raw) return null;
            const stored = JSON.parse(raw);
            if (!stored || typeof stored.analysis !== 'string' || !stored.analysis.trim()) return null;
            this._attributionCache.set(cacheKey, stored);
            return stored;
        } catch (e) {
            // 隐私模式或禁用存储时仍保留进程内缓存能力。
            return null;
        }
    },

    _setAttributionCache(cacheKey, value) {
        this._attributionCache.set(cacheKey, value);
        try {
            sessionStorage.setItem(
                this._attributionStoragePrefix + encodeURIComponent(cacheKey),
                JSON.stringify(value),
            );
        } catch (e) {
            // sessionStorage 不可用不影响看板展示。
        }
    },

    _deleteAttributionCache(cacheKey) {
        this._attributionCache.delete(cacheKey);
        try {
            sessionStorage.removeItem(this._attributionStoragePrefix + encodeURIComponent(cacheKey));
        } catch (e) {
            // sessionStorage 不可用时仅清理内存缓存。
        }
    },

    regularAttribution(product, month, data) {
        const rows = (data && data.rows) || [];
        const unit = rows.find(row => String(row.metric || '').includes('单位成本')) || {};
        const change = unit.mom_change === null || unit.mom_change === undefined ? '暂无环比数据' : `${unit.mom_change > 0 ? '+' : ''}${unit.mom_change}%`;
        return `## 常规分析\n\n${product} ${month}单位成本为${unit.current ?? '--'}元/盒，环比${change}。当前未发现超过阈值（±10%）的成本波动，建议持续跟踪材料价格、单耗、人工效率及产量摊薄效应。\n\n## 改进建议\n\n1. 采购部门持续跟踪主要原材料价格与供应商报价。\n2. 生产和财务部门按月复核单耗、工时及固定费用分摊变化。`;
    },

    renderAttribution(text, important, ragSources = []) {
        const full = document.getElementById('attributionText');
        const details = document.getElementById('attributionDetails');
        const badge = document.getElementById('attributionBadge');
        const sources = document.getElementById('attributionSources');
        if (!full || !details) return;
        const normalized = String(text || '').replace(/\r/g, '').trim() || '暂无归因分析';
        const sentences = normalized.split(/(?<=[。！？；])\s*|\n+/).map(s => s.trim()).filter(Boolean);
        full.innerHTML = this.markdownToHtml(normalized);
        details.style.display = '';
        // 短文本直接展开，长文本默认折叠，避免无阈值时正文不可见。
        details.open = sentences.length <= 2 && normalized.length <= 240;
        details.querySelector('summary').textContent = details.open ? '收起归因分析' : '展开完整归因分析';
        if (badge) badge.style.display = important ? '' : 'none';
        if (sources) {
            const uniqueSources = [...new Set((ragSources || []).map(source => String(source || '').trim()).filter(Boolean))];
            sources.style.display = uniqueSources.length ? '' : 'none';
            sources.innerHTML = uniqueSources.length
                ? `<span class="attribution-sources-label">知识库来源：</span>${uniqueSources.map(source => `<span class="attribution-source">${Utils.escapeHtml(source)}</span>`).join('')}`
                : '';
        }
    },

    markdownToHtml(markdown) {
        const lines = String(markdown || '').replace(/\r/g, '').split('\n');
        const out = [];
        let list = null;
        const inline = value => {
            let html = Utils.escapeHtml(value);
            html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
            html = html.replace(/__(.+?)__/g, '<strong>$1</strong>');
            html = html.replace(/`(.+?)`/g, '<code>$1</code>');
            return html;
        };
        const closeList = () => {
            if (list) { out.push(`</${list}>`); list = null; }
        };
        const tableCells = line => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => cell.trim());
        const isTableLine = line => line.includes('|') && tableCells(line).length >= 2;
        const isTableDivider = line => isTableLine(line) && tableCells(line).every(cell => /^:?-{3,}:?$/.test(cell));
        const renderTable = (headerLine, bodyLines) => {
            const headers = tableCells(headerLine);
            const rows = bodyLines.map(tableCells);
            const width = headers.length;
            const normalize = cells => [...cells.slice(0, width), ...Array(Math.max(0, width - cells.length)).fill('')];
            return `<div class="attribution-table-wrap"><table class="attribution-table"><thead><tr>${normalize(headers).map(cell => `<th>${inline(cell)}</th>`).join('')}</tr></thead><tbody>${rows.map(cells => `<tr>${normalize(cells).map(cell => `<td>${inline(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
        };
        for (let index = 0; index < lines.length; index += 1) {
            const line = lines[index].trim();
            if (!line) { closeList(); continue; }
            // 标准 Markdown 表格：表头下一行为 --- 分隔线，后续连续竖线行作为数据。
            if (isTableLine(line) && index + 1 < lines.length && isTableDivider(lines[index + 1].trim())) {
                closeList();
                const body = [];
                index += 2;
                while (index < lines.length && isTableLine(lines[index].trim()) && !isTableDivider(lines[index].trim())) {
                    body.push(lines[index].trim());
                    index += 1;
                }
                index -= 1;
                out.push(renderTable(line, body));
                continue;
            }
            const heading = line.match(/^(#{1,3})\s+(.+)$/);
            const bullet = line.match(/^[-*]\s+(.+)$/);
            const numbered = line.match(/^\d+[.)]\s+(.+)$/);
            if (heading) { closeList(); out.push(`<h${heading[1].length}>${inline(heading[2])}</h${heading[1].length}>`); continue; }
            if (bullet || numbered) {
                const tag = bullet ? 'ul' : 'ol';
                if (list !== tag) { closeList(); out.push(`<${tag}>`); list = tag; }
                out.push(`<li>${inline((bullet || numbered)[1])}</li>`);
                continue;
            }
            closeList();
            out.push(`<p>${inline(line)}</p>`);
        }
        closeList();
        return out.join('');
    },

    renderAlerts(alerts) {
        const card = document.getElementById('dashboardAlerts');
        const list = document.getElementById('alertList');
        if (!card || !list) return;
        card.style.display = '';
        list.innerHTML = alerts.length
            ? alerts.map(alert => `<div class="alert-item ${alert.direction === '↑' ? 'alert-up' : 'alert-down'}"><i data-lucide="triangle-alert"></i><span>${Utils.escapeHtml(alert.message || `${alert.metric || '指标'} 环比${alert.direction || ''}${Math.abs(alert.change || 0)}%`)}</span></div>`).join('')
            : '<div class="attribution-normal"><i data-lucide="circle-check"></i><span>本月未发现超过阈值的成本波动，以下为常规归因分析。</span></div>';
        if (typeof refreshIcons === 'function') refreshIcons();
    },

    _mockThreeDim() {
        return {
            rows: [
                { metric: '产量(盒)', current: 12500, last_month: 11800, mom_change: 5.93, last_year: 11000, yoy_change: 13.64, budget: 12000, budget_deviation: 4.17 },
                { metric: '单位成本(元/盒)', current: 3.76, last_month: 3.69, mom_change: 1.90, last_year: 3.33, yoy_change: 12.91, budget: 3.65, budget_deviation: 3.01 },
                { metric: '总成本(元)', current: 47000, last_month: 43542, mom_change: 7.93, last_year: 36630, yoy_change: 28.31, budget: 43800, budget_deviation: 7.31 },
                { metric: '直接材料(元/盒)', current: 2.41, last_month: 2.35, mom_change: 2.55, last_year: 2.10, yoy_change: 14.76, budget: 2.30, budget_deviation: 4.78 },
                { metric: '直接人工(元/盒)', current: 0.68, last_month: 0.65, mom_change: 4.62, last_year: 0.62, yoy_change: 9.68, budget: 0.70, budget_deviation: -2.86 },
                { metric: '制造费用(元/盒)', current: 0.52, last_month: 0.55, mom_change: -5.45, last_year: 0.48, yoy_change: 8.33, budget: 0.50, budget_deviation: 4.00 },
            ]
        };
    },

    /* ── KPI 卡片（白卡风格） ── */
    renderKpiCards(data) {
        const rows = data.rows || [];
        const find = (name) => rows.find(r => r.metric && r.metric.includes(name)) || {};

        const output = find('产量');
        const unit = find('单位成本');
        const mat = find('直接材料');
        const total = find('总成本');

        // 当月产量
        this._setKpi('kpiOutput', this._fmtKpiVal(output.current, '盒'), output.mom_change, false);
        // 单位成本（下降 = 好）
        this._setKpi('kpiUnitCost', this._fmtKpiVal(unit.current, '元'), unit.mom_change, true);
        // 直接材料（下降 = 好）
        this._setKpi('kpiMaterial', this._fmtKpiVal(mat.current, '元'), mat.mom_change, true);
        // 总成本 + 预算对比（下降 = 好）
        this._setKpiTotal('kpiTotal', this._fmtKpiVal(total.current, '元'), total.mom_change, total.budget);
    },

    _fmtKpiVal(val, unit) {
        if (val === null || val === undefined || isNaN(val)) return '--';
        const formatted = Utils.fmtNum(val, unit === '盒' ? 0 : 2);
        if (unit === '盒') {
            return `${formatted} <span style="font-size:14px;font-weight:400;color:var(--phc-ink-2)">盒</span>`;
        }
        return `¥${formatted}`;
    },

    _setKpi(id, valueHtml, momPct, costMetric) {
        const valEl = document.getElementById(id);
        const metaEl = document.getElementById(id + 'Meta');
        if (valEl) valEl.innerHTML = valueHtml;
        if (!metaEl) return;
        if (momPct === null || momPct === undefined || isNaN(momPct)) {
            metaEl.innerHTML = '';
            return;
        }
        // 成本指标：下降为好(.up 绿色)，上升为坏(.down 红色)
        // 产量指标：上升为好，下降为坏
        let cls;
        if (costMetric) {
            cls = momPct < 0 ? 'up' : (momPct > 0 ? 'down' : 'neutral');
        } else {
            cls = momPct > 0 ? 'up' : (momPct < 0 ? 'down' : 'neutral');
        }
        const arrow = momPct > 0 ? '↑' : (momPct < 0 ? '↓' : '→');
        const sign = momPct > 0 ? '+' : '';
        metaEl.innerHTML = `<span class="kpi-delta ${cls}">${arrow} ${sign}${momPct.toFixed(2)}% 环比</span>`;
    },

    _setKpiTotal(id, valueHtml, momPct, budget) {
        const valEl = document.getElementById(id);
        const metaEl = document.getElementById(id + 'Meta');
        if (valEl) valEl.innerHTML = valueHtml;
        if (!metaEl) return;
        let html = '';
        if (momPct !== null && momPct !== undefined && !isNaN(momPct)) {
            const cls = momPct < 0 ? 'up' : (momPct > 0 ? 'down' : 'neutral');
            const arrow = momPct > 0 ? '↑' : (momPct < 0 ? '↓' : '→');
            const sign = momPct > 0 ? '+' : '';
            html += `<span class="kpi-delta ${cls}">${arrow} ${sign}${momPct.toFixed(2)}% 环比</span>`;
        }
        if (budget !== null && budget !== undefined && !isNaN(budget)) {
            html += `<span style="font-size:12px;color:var(--phc-ink-3)">预算 ¥${Utils.fmtNum(budget, 0)}</span>`;
        }
        metaEl.innerHTML = html;
    },

    /* ── 三维对比表（白卡风格） ── */
    renderThreeDimTable(data) {
        const target = document.getElementById('threeDimTable');
        if (!target) return;
        const rows = data.rows || [];
        if (rows.length === 0) {
            target.innerHTML = Utils.emptyState('', '暂无三维对比数据');
            return;
        }
        // 把"单位成本/总成本"行排到表格底部
        const normalRows = rows.filter(r => !(r.metric && (r.metric.includes('单位成本') || r.metric.includes('总成本'))));
        const totalRows = rows.filter(r => r.metric && (r.metric.includes('单位成本') || r.metric.includes('总成本')));
        const orderedRows = [...normalRows, ...totalRows];

        let html = '<table class="data-table"><thead><tr>';
        ['成本项目', '当月实际', '上月实际', '环比变动', '去年同期', '同比变动', '本月预算', '预算偏差'].forEach(h => {
            html += `<th>${h}</th>`;
        });
        html += '</tr></thead><tbody>';
        orderedRows.forEach(r => {
            const isTotal = totalRows.includes(r);
            html += `<tr${isTotal ? ' class="total-row"' : ''}>`;
            html += `<td>${r.metric || ''}</td>`;
            html += `<td class="num">${Utils.fmtNum(r.current, 2)}</td>`;
            html += `<td class="num">${r.last_month !== null && r.last_month !== undefined ? Utils.fmtNum(r.last_month, 2) : '-'}</td>`;
            html += this._pctCell(r.mom_change);
            html += `<td class="num">${r.last_year !== null && r.last_year !== undefined ? Utils.fmtNum(r.last_year, 2) : '-'}</td>`;
            html += this._pctCell(r.yoy_change);
            html += `<td class="num">${r.budget !== null && r.budget !== undefined ? Utils.fmtNum(r.budget, 2) : '-'}</td>`;
            html += this._pctCell(r.budget_deviation);
            html += '</tr>';
        });
        html += '</tbody></table>';
        target.innerHTML = html;
    },

    /* 百分比单元格（已为百分数，如 1.90 => +1.90%） */
    _pctCell(val) {
        if (val === null || val === undefined || isNaN(val)) return '<td class="num">-</td>';
        const cls = val > 10 ? 'positive' : (val < -10 ? 'negative' : '');
        const sign = val > 0 ? '+' : '';
        return `<td class="num ${cls}">${sign}${val.toFixed(2)}%</td>`;
    },

    /* ============ 趋势图 ============ */
    async loadTrend(product, version) {
        try {
            const data = await Utils.api(`/api/dashboard/trend?${Utils.qs({ product })}`);
            if (!this.isCurrent(version)) return;
            this.renderTrendChart(data);
        } catch (e) {
            if (!this.isCurrent(version)) return;
            this.renderTrendChart(this._mockTrend());
        }
    },

    _mockTrend() {
        return {
            months: ['2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06'],
            material: [2.10, 2.18, 2.25, 2.30, 2.35, 2.41],
            labor: [0.62, 0.63, 0.64, 0.65, 0.65, 0.68],
            overhead: [0.48, 0.49, 0.50, 0.53, 0.55, 0.52],
            unit_cost: [3.33, 3.42, 3.51, 3.60, 3.69, 3.76]
        };
    },

    renderTrendChart(data) {
        const chart = Utils.getChart('chartTrend');
        if (!chart) return;
        const months = data.months || [];
        chart.setOption({
            tooltip: { trigger: 'axis', backgroundColor: 'rgba(255,255,255,0.96)', borderColor: '#e0e0e0', textStyle: { color: '#333' } },
            legend: { data: ['直接材料', '直接人工', '制造费用', '单位成本'], top: 0, right: 16 },
            grid: { left: 60, right: 30, top: 45, bottom: 30 },
            xAxis: { type: 'category', data: months, axisLabel: { color: '#616161' } },
            yAxis: { type: 'value', name: '元/盒', axisLabel: { color: '#616161' }, splitLine: { lineStyle: { type: 'dashed', color: '#eee' } } },
            series: [
                { name: '直接材料', type: 'line', data: data.material || [], smooth: true, lineStyle: { width: 2.5 }, itemStyle: { color: '#e53935' } },
                { name: '直接人工', type: 'line', data: data.labor || [], smooth: true, lineStyle: { width: 2.5 }, itemStyle: { color: '#1e88e5' } },
                { name: '制造费用', type: 'line', data: data.overhead || [], smooth: true, lineStyle: { width: 2.5 }, itemStyle: { color: '#fdd835' } },
                { name: '单位成本', type: 'line', data: data.unit_cost || [], smooth: true, lineStyle: { width: 3 }, itemStyle: { color: '#1a237e' } }
            ]
        });
    },

    async loadHeatmap(version) {
        try {
            const data = await Utils.api('/api/dashboard/heatmap');
            if (!this.isCurrent(version)) return;
            this._heatmapData = data;
            const metric = document.getElementById('heatmapMetric');
            if (metric && !metric.dataset.bound) {
                metric.dataset.bound = 'true';
                metric.addEventListener('change', () => this.renderHeatmap(this._heatmapData, metric.value));
            }
            if (typeof ResizeObserver !== 'undefined' && !this._heatmapResizeObserver) {
                const dom = document.getElementById('chartHeatmap');
                if (dom) {
                    this._heatmapResizeObserver = new ResizeObserver(() => {
                        if (this._heatmapData && dom.clientWidth > 0) {
                            const instance = echarts.getInstanceByDom(dom);
                            if (instance && !instance.isDisposed()) instance.resize();
                            else this.renderHeatmap(this._heatmapData, metric ? metric.value : 'unit_cost');
                        }
                    });
                    this._heatmapResizeObserver.observe(dom);
                }
            }
            this.renderHeatmap(data, metric ? metric.value : 'unit_cost');
        } catch (e) {
            if (!this.isCurrent(version)) return;
            const target = document.getElementById('chartHeatmap');
            if (target) target.innerHTML = Utils.emptyState('', '热力图数据暂不可用');
        }
    },

    renderHeatmap(data, metric, attempt = 0) {
        const dom = document.getElementById('chartHeatmap');
        if (!dom) return;
        // 页面刚切回时可能尚未完成布局，延迟初始化避免 ECharts 得到 0 宽度。
        if (dom.clientWidth === 0 && attempt < 30) {
            setTimeout(() => this.renderHeatmap(data, metric, attempt + 1), 100);
            return;
        }
        let chart = echarts.getInstanceByDom(dom);
        if (!chart || chart.isDisposed()) {
            chart = echarts.init(dom);
            AppState.echartsInstances.chartHeatmap = chart;
        }
        if (!chart || !data) return;
        const values = (data.values && data.values[metric]) || [];
        if (!values.length || !(data.months || []).length || !(data.products || []).length) {
            chart.clear();
            return;
        }
        const valid = values.map(item => item[2]).filter(value => value !== null && value !== undefined);
        const min = valid.length ? Math.min(...valid) : 0;
        const max = valid.length ? Math.max(...valid) : 1;
        chart.setOption({
            tooltip: {
                position: 'top',
                formatter: params => {
                    const value = params.value[2];
                    return `${data.products[params.value[1]]}<br>${data.months[params.value[0]]}<br>${(data.elements && data.elements[metric]) || metric}：${value == null ? '-' : Number(value).toFixed(2)} 元/盒`;
                }
            },
            grid: { left: 90, right: 35, top: 18, bottom: 65 },
            xAxis: { type: 'category', data: data.months || [], splitArea: { show: true }, axisLabel: { color: '#616161', rotate: 35 } },
            yAxis: { type: 'category', data: data.products || [], splitArea: { show: true }, axisLabel: { color: '#616161' } },
            visualMap: { min, max: max === min ? min + 1 : max, calculable: true, orient: 'horizontal', left: 'center', bottom: 0, textStyle: { color: '#616161' } },
            series: [{ type: 'heatmap', data: values, label: { show: true, formatter: p => p.value[2] == null ? '-' : Number(p.value[2]).toFixed(2), color: '#333', fontSize: 11 }, emphasis: { itemStyle: { shadowBlur: 8, shadowColor: 'rgba(0,0,0,0.25)' } } }]
        });
    },

    /* ============ 成本结构饼图 ============ */
    async loadStructure(product, month, version) {
        try {
            const data = await Utils.api(`/api/dashboard/structure?${Utils.qs({ product, month })}`);
            if (!this.isCurrent(version)) return;
            if (data.error) throw new Error(data.error);
            this.renderStructureChart({ items: data.data || data.items || [] });
        } catch (e) {
            if (!this.isCurrent(version)) return;
            this.renderStructureChart({
                items: [
                    { name: '直接材料', value: 2.41, ratio: 64.1 },
                    { name: '直接人工', value: 0.68, ratio: 18.1 },
                    { name: '制造费用', value: 0.52, ratio: 13.8 },
                ]
            });
        }
    },

    renderStructureChart(data) {
        const chart = Utils.getChart('chartStructure');
        if (!chart) return;
        const items = data.items || [];
        chart.setOption({
            tooltip: { trigger: 'item', formatter: '{b}: {c} 元 ({d}%)' },
            legend: { orient: 'vertical', left: 10, top: 'center' },
            color: ['#e53935', '#1e88e5', '#fdd835', '#43a047', '#8e24aa'],
            series: [{
                type: 'pie',
                radius: ['40%', '70%'],
                center: ['60%', '50%'],
                avoidLabelOverlap: true,
                label: { show: true, formatter: '{b}\n{d}%' },
                labelLine: { show: true },
                emphasis: { itemStyle: { shadowBlur: 10, shadowOffsetX: 0, shadowColor: 'rgba(0,0,0,0.2)' } },
                data: items.map(it => ({ name: it.name, value: it.value }))
            }]
        });
    },

    /* ============ 瀑布图 ============ */
    async loadWaterfall(product, month, version) {
        try {
            const data = await Utils.api(`/api/dashboard/waterfall?${Utils.qs({ product, month })}`);
            if (!this.isCurrent(version)) return;
            if (data.error) throw new Error(data.error);
            this.renderWaterfallChart(data);
        } catch (e) {
            if (!this.isCurrent(version)) return;
            this.renderWaterfallChart({
                base: 3.69,
                items: [
                    { name: '直接材料变动', value: 0.06 },
                    { name: '直接人工变动', value: 0.03 },
                    { name: '制造费用变动', value: -0.03 },
                    { name: '单位成本变动', value: 0.07, is_total: true }
                ]
            });
        }
    },

    renderWaterfallChart(data) {
        const chart = Utils.getChart('chartWaterfall');
        if (!chart) return;
        const items = data.items || [];
        const cats = items.map(i => i.name);
        const vals = items.map(i => i.value || 0);

        const transparent = [];
        const colored = [];
        let running = Number(data.base) || 0;
        for (let i = 0; i < vals.length; i++) {
            const value = Number(vals[i]) || 0;
            const isTotal = Boolean(items[i].is_total);
            // 负向变动使用“下降后平台 + 正柱高”，避免 ECharts 将负值从零轴绘制。
            const base = Number(data.base) || 0;
            const start = isTotal
                ? (value >= 0 ? base : base + value)
                : (value >= 0 ? running : running + value);
            transparent.push(start);
            colored.push(Math.abs(value));
            if (!isTotal) {
                running += value;
            }
        }
        const levels = [Number(data.base) || 0];
        let level = Number(data.base) || 0;
        items.forEach((item, index) => {
            if (!item.is_total) {
                level += Number(vals[index]) || 0;
                levels.push(level);
            }
        });
        const totalItem = items.find(item => item.is_total);
        if (totalItem) levels.push((Number(data.base) || 0) + (Number(totalItem.value) || 0));
        const low = Math.min(...levels);
        const high = Math.max(...levels);
        const span = Math.max(high - low, Math.max(Math.abs(Number(data.base) || 0) * 0.08, 0.12));
        const axisPad = Math.max(span * 0.18, 0.03);
        chart.setOption({
            tooltip: {
                trigger: 'axis', axisPointer: { type: 'shadow' },
                formatter: function (params) {
                    const actual = params.find(p => p.seriesName === '实际');
                    if (!actual) return '';
                    const item = items[actual.dataIndex] || {};
                    const contribution = item.contribution_pct === null || item.contribution_pct === undefined ? '' : `，贡献度 ${item.contribution_pct}%`;
                    const original = Number(item.value) || 0;
                    return `${params[0].name}: ${original > 0 ? '+' : ''}${original.toFixed(2)} 元/盒${contribution}`;
                }
            },
            grid: { left: 60, right: 30, top: 30, bottom: 60 },
            xAxis: { type: 'category', data: cats, axisLabel: { color: '#616161', interval: 0, rotate: 15 } },
            yAxis: {
                type: 'value', name: '元/盒', min: low - axisPad, max: high + axisPad,
                axisLabel: { color: '#616161', formatter: value => Number(value).toFixed(2) },
                splitLine: { lineStyle: { type: 'dashed', color: '#eee' } }
            },
            series: [
                { name: '辅助', type: 'bar', stack: 'waterfall', itemStyle: { borderColor: 'transparent', color: 'transparent' }, emphasis: { itemStyle: { borderColor: 'transparent', color: 'transparent' } }, data: transparent },
                {
                    name: '实际', type: 'bar', stack: 'waterfall',
                    data: colored.map((v, i) => ({
                        value: v,
                        itemStyle: {
                            color: (Number(items[i].value) || 0) >= 0 ? '#e53935' : '#43a047'
                        }
                    })),
                    label: {
                        show: true,
                        position: 'top',
                        formatter: (p) => {
                            const item = items[p.dataIndex] || {};
                            const original = Number(item.value) || 0;
                            const amount = (original > 0 ? '+' : '') + original.toFixed(2);
                            const contribution = item.contribution_pct === null || item.contribution_pct === undefined
                                ? '' : `\n${Number(item.contribution_pct).toFixed(1)}%`;
                            return amount + contribution;
                        },
                        fontSize: 12
                    }
                }
            ]
        });
    },

    /* ============ 原材料明细 ============ */
    async loadMaterialDetail(product, month, version) {
        try {
            const data = await Utils.api(`/api/dashboard/material-detail?${Utils.qs({ product, month })}`);
            if (!this.isCurrent(version)) return;
            this.renderMaterialTable(data.materials || []);
        } catch (e) {
            if (!this.isCurrent(version)) return;
            this.renderMaterialTable([
                { material_name: '金银花提取物', unit_cost: 0.78, total_cost: 97500, ratio: 32.0, prev_unit_cost: 0.74, mom_change: 5.4 },
                { material_name: '黄芩提取物', unit_cost: 0.49, total_cost: 61250, ratio: 20.1, prev_unit_cost: 0.48, mom_change: 2.1 },
                { material_name: '连翘提取物', unit_cost: 0.36, total_cost: 45000, ratio: 14.8, prev_unit_cost: 0.37, mom_change: -2.7 },
            ]);
        }
    },

    renderMaterialTable(materials) {
        const target = document.getElementById('materialDetailTable');
        if (!target) return;
        if (materials.length === 0) { target.innerHTML = Utils.emptyState('', '暂无原材料明细'); return; }
        let html = '<table class="data-table"><thead><tr>';
        ['物料名称', '单位成本(元/盒)', '总成本(元)', '占比', '上月单位成本', '环比变动'].forEach(h => { html += `<th>${h}</th>`; });
        html += '</tr></thead><tbody>';
        materials.forEach((m, ri) => {
            const mom = m.mom_change;
            const momCls = mom > 5 ? 'positive' : (mom < -5 ? 'negative' : '');
            html += `<tr${ri < 3 ? ' class="highlight"' : ''}>`;
            html += `<td${ri < 3 ? ' class="top-3"' : ''}>${ri < 3 ? Icons.trophy() : ''}${m.material_name || '--'}</td>`;
            html += `<td class="num">${Utils.fmtNum(m.unit_cost, 4)}</td>`;
            html += `<td class="num">${Utils.fmtNum(m.total_cost, 2)}</td>`;
            html += `<td class="num">${m.ratio !== null && m.ratio !== undefined ? m.ratio.toFixed(1) + '%' : '-'}</td>`;
            html += `<td class="num">${m.prev_unit_cost ? Utils.fmtNum(m.prev_unit_cost, 4) : '-'}</td>`;
            html += `<td class="num ${momCls}">${mom !== null && mom !== undefined ? (mom > 0 ? '+' : '') + mom.toFixed(2) + '%' : '-'}</td>`;
            html += '</tr>';
        });
        html += '</tbody></table>';
        target.innerHTML = html;
    },

    /* ============ 制造费用明细 ============ */
    async loadOverheadDetail(product, month, version) {
        try {
            const data = await Utils.api(`/api/dashboard/overhead-detail?${Utils.qs({ product, month })}`);
            if (!this.isCurrent(version)) return;
            this.renderOverheadTable(data.overheads || []);
        } catch (e) {
            if (!this.isCurrent(version)) return;
            this.renderOverheadTable([
                { category: '设备折旧', unit_cost: 0.15, total_cost: 18750, prev_unit_cost: 0.15, mom_change: 0 },
                { category: '能源动力', unit_cost: 0.10, total_cost: 12500, prev_unit_cost: 0.09, mom_change: 11.1 },
                { category: '维修保养', unit_cost: 0.07, total_cost: 8750, prev_unit_cost: 0.06, mom_change: 16.7 },
            ]);
        }
    },

    renderOverheadTable(overheads) {
        const target = document.getElementById('overheadDetailTable');
        if (!target) return;
        if (overheads.length === 0) { target.innerHTML = Utils.emptyState('', '暂无制造费用明细'); return; }
        const totalUc = overheads.reduce((s, o) => s + (o.unit_cost || 0), 0);
        let html = '<table class="data-table"><thead><tr>';
        ['费用项目', '单位费用(元/盒)', '费用总额(元)', '占比', '上月单位费用', '环比变动'].forEach(h => { html += `<th>${h}</th>`; });
        html += '</tr></thead><tbody>';
        overheads.forEach(o => {
            const mom = o.mom_change;
            const momCls = mom > 5 ? 'positive' : (mom < -5 ? 'negative' : '');
            const pct = totalUc ? (o.unit_cost / totalUc * 100).toFixed(1) : '0.0';
            html += '<tr>';
            html += `<td>${o.category || '--'}</td>`;
            html += `<td class="num">${Utils.fmtNum(o.unit_cost, 4)}</td>`;
            html += `<td class="num">${Utils.fmtNum(o.total_cost, 2)}</td>`;
            html += `<td class="num">${pct}%</td>`;
            html += `<td class="num">${o.prev_unit_cost ? Utils.fmtNum(o.prev_unit_cost, 4) : '-'}</td>`;
            html += `<td class="num ${momCls}">${mom !== null && mom !== undefined ? (mom > 0 ? '+' : '') + mom.toFixed(2) + '%' : '-'}</td>`;
            html += '</tr>';
        });
        html += '</tbody></table>';
        target.innerHTML = html;
    },

    /* ============ 人工指标 ============ */
    async loadLaborMetrics(product, month, version) {
        try {
            const data = await Utils.api(`/api/dashboard/labor-metrics?${Utils.qs({ product, month })}`);
            if (!this.isCurrent(version)) return;
            if (data.error) throw new Error(data.error);
            this.renderLaborMetrics(data.metrics || {}, data.raw || {});
        } catch (e) {
            if (!this.isCurrent(version)) return;
            this.renderLaborMetrics({}, {
                production: 12500, total_labor_cost: 8500, total_hours: 3200, worker_count: 20, work_days: 25
            });
        }
    },

    renderLaborMetrics(metrics, raw) {
        const target = document.getElementById('laborMetrics');
        if (!target) return;
        const items = [
            { label: '单位人工成本(元/盒)', key: 'unit_labor_cost', rawVal: raw.total_labor_cost && raw.production ? raw.total_labor_cost / raw.production : null },
            { label: '万件工时(时/万件)', key: 'labor_hours_per_10k', rawVal: raw.total_hours && raw.production ? raw.total_hours / raw.production * 10000 : null },
            { label: '平均时薪(元/时)', key: 'avg_hourly_wage', rawVal: raw.total_labor_cost && raw.total_hours ? raw.total_labor_cost / raw.total_hours : null },
            { label: '人均日产出(盒/人/天)', key: 'labor_efficiency', rawVal: raw.production && raw.worker_count && raw.work_days ? raw.production / (raw.worker_count * raw.work_days) : null },
        ];
        let html = '';
        items.forEach((it) => {
            const m = metrics[it.key] || {};
            const cur = m.current !== undefined ? m.current : it.rawVal;
            const change = m.change;
            const val = cur !== null && cur !== undefined ? Utils.fmtNum(cur, 2) : '--';
            let deltaHtml = '';
            if (change !== null && change !== undefined && !isNaN(change)) {
                // 人工成本/工时：下降为好；产出：上升为好
                const isEfficiency = it.key === 'labor_efficiency';
                let cls;
                if (isEfficiency) {
                    cls = change > 0 ? 'up' : (change < 0 ? 'down' : 'neutral');
                } else {
                    cls = change < 0 ? 'up' : (change > 0 ? 'down' : 'neutral');
                }
                const arrow = change > 0 ? '↑' : (change < 0 ? '↓' : '→');
                const sign = change > 0 ? '+' : '';
                deltaHtml = `<span class="kpi-delta ${cls}">${arrow} ${sign}${change.toFixed(2)}% 环比</span>`;
            } else {
                deltaHtml = '<span class="kpi-delta neutral">暂无环比</span>';
            }
            html += `
                <div class="card kpi-card">
                    <div class="kpi-label">${it.label}</div>
                    <div class="kpi-value">${val}</div>
                    <div class="kpi-meta">${deltaHtml}</div>
                </div>
            `;
        });
        target.innerHTML = html;
    },

    /* ============ 行业对标 ============ */
    async loadIndustryBench(product, version) {
        try {
            const data = await Utils.api(`/api/dashboard/industry-bench?${Utils.qs({ product })}`);
            if (!this.isCurrent(version)) return;
            this.renderBenchChart(data.benchmarks || data.overall || []);
        } catch (e) {
            if (!this.isCurrent(version)) return;
            this.renderBenchChart([
                { metric: '单位成本(元/盒)', p25: 3.20, p50: 3.55, p75: 3.90, factory_value: 3.76, evaluation: '偏高' },
                { metric: '直接材料(元/盒)', p25: 1.95, p50: 2.15, p75: 2.35, factory_value: 2.41, evaluation: '偏高' },
                { metric: '直接人工(元/盒)', p25: 0.60, p50: 0.72, p75: 0.85, factory_value: 0.68, evaluation: '合理' },
                { metric: '制造费用(元/盒)', p25: 0.45, p50: 0.55, p75: 0.65, factory_value: 0.52, evaluation: '合理' },
            ]);
        }
    },

    renderBenchChart(benchmarks) {
        const chart = Utils.getChart('chartBench');
        if (!chart) return;
        const cats = benchmarks.map(b => b.metric || '');
        chart.setOption({
            tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, backgroundColor: 'rgba(255,255,255,0.96)', borderColor: '#e0e0e0' },
            legend: { data: ['行业P25', '行业P50', '行业P75', '本厂水平'], top: 0, right: 16 },
            grid: { left: 70, right: 30, top: 45, bottom: 60 },
            xAxis: { type: 'category', data: cats, axisLabel: { color: '#616161', interval: 0, rotate: 15 } },
            yAxis: { type: 'value', name: '元/盒', axisLabel: { color: '#616161' }, splitLine: { lineStyle: { type: 'dashed', color: '#eee' } } },
            series: [
                { name: '行业P25', type: 'bar', data: benchmarks.map(b => this._num(b.p25)), itemStyle: { color: '#a5d6a7', borderRadius: [4, 4, 0, 0] } },
                { name: '行业P50', type: 'bar', data: benchmarks.map(b => this._num(b.p50)), itemStyle: { color: '#66bb6a', borderRadius: [4, 4, 0, 0] } },
                { name: '行业P75', type: 'bar', data: benchmarks.map(b => this._num(b.p75)), itemStyle: { color: '#388e3c', borderRadius: [4, 4, 0, 0] } },
                { name: '本厂水平', type: 'bar', data: benchmarks.map(b => this._num(b.factory_value)), itemStyle: { color: '#1a237e', borderRadius: [4, 4, 0, 0] } }
            ]
        });
    },

    _num(v) {
        const n = parseFloat(v);
        return isNaN(n) ? 0 : n;
    }
};
