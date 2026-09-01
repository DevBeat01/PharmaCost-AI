/**
 * app.js — SPA 路由、全局状态、工具函数
 */
(function () {
    'use strict';

    /* ============ 全局状态 ============ */
    const AppState = {
        currentPage: 'dashboard',
        // 初始化即提供可用的演示参数，避免产品接口慢/失败时其他模块出现空表单。
        currentProduct: '银黄口服液',
        currentMonth: '2026-06',
        products: ['银黄口服液', '板蓝根颗粒', '六味地黄胶囊'],
        months: ['2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06'],
        productSpecs: {},
        echartsInstances: {},   // 页面内 ECharts 实例缓存
    };

    /* ============ 工具函数 ============ */
    const Utils = {
        /**
         * HTML转义 — 防止XSS
         */
        escapeHtml(str) {
            if (str === null || str === undefined) return '';
            const div = document.createElement('div');
            div.textContent = String(str);
            return div.innerHTML;
        },

        /**
         * 从产品项中提取名称（兼容字符串与 {name, spec} 对象两种形态）
         */
        productName(p) {
            if (p === null || p === undefined) return '';
            return typeof p === 'object' ? (p.name || '') : String(p);
        },

        /**
         * 生成产品下拉项的显示文本（含规格）
         */
        productLabel(p) {
            if (p === null || p === undefined) return '';
            if (typeof p === 'object') {
                return p.spec ? `${p.name}（${p.spec}）` : (p.name || '');
            }
            const spec = AppState.productSpecs ? AppState.productSpecs[p] : '';
            return spec ? `${p}（${spec}）` : String(p);
        },

        /**
         * fetch 封装，返回解析后的 JSON
         */
        async api(url, options = {}) {
            const { timeoutMs = 12000, signal: externalSignal, ...fetchOptions } = options;
            const controller = new AbortController();
            const abortExternal = () => controller.abort();
            if (externalSignal) {
                if (externalSignal.aborted) controller.abort();
                else externalSignal.addEventListener('abort', abortExternal, { once: true });
            }
            const timer = setTimeout(() => controller.abort(), timeoutMs);
            try {
                const resp = await fetch(url, { ...fetchOptions, signal: controller.signal });
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                return await resp.json();
            } catch (e) {
                console.error('[API Error]', url, e);
                // 外部取消代表用户切换了产品/月，不应显示旧请求的错误或兜底结果。
                if (e.name === 'AbortError' && externalSignal?.aborted) throw e;
                if (e.name === 'AbortError') throw new Error(`API请求超时（${timeoutMs}ms）`);
                throw e;
            } finally {
                clearTimeout(timer);
                if (externalSignal) externalSignal.removeEventListener('abort', abortExternal);
            }
        },

        /**
         * 显示全局 loading
         */
        showLoading(text) {
            const el = document.getElementById('loadingOverlay');
            el.querySelector('.loading-text').textContent = text || '数据加载中...';
            el.style.display = 'flex';
        },

        hideLoading() {
            document.getElementById('loadingOverlay').style.display = 'none';
        },

        /**
         * 生成内联加载指示器
         */
        inlineLoading(text) {
            return `<div class="inline-loading"><div class="spinner-small"></div><span>${text || '加载中...'}</span></div>`;
        },

        /**
         * 空状态（使用 Lucide 图标）
         */
        emptyState(_, text) {
            return `<div class="empty-state"><i data-lucide="inbox" style="width:48px;height:48px;color:var(--phc-ink-3);opacity:0.4"></i><div class="empty-text" style="margin-top:12px;color:var(--phc-ink-3);font-size:14px">${Utils.escapeHtml(text)}</div></div>`;
        },

        /**
         * 格式化数字（千分位 + 小数位）
         */
        fmtNum(val, digits) {
            if (val === null || val === undefined || isNaN(val)) return '-';
            digits = digits !== undefined ? digits : 2;
            return Number(val).toLocaleString('zh-CN', {
                minimumFractionDigits: digits,
                maximumFractionDigits: digits
            });
        },

        /**
         * 格式化百分比
         */
        fmtPct(val) {
            if (val === null || val === undefined || isNaN(val)) return '-';
            const pct = (Number(val) * 100).toFixed(2);
            const sign = pct > 0 ? '+' : '';
            return sign + pct + '%';
        },

        /**
         * 变动 CSS class
         */
        changeClass(val) {
            if (val === null || val === undefined || isNaN(val)) return 'flat';
            const v = Number(val);
            if (v > 0.001) return 'up';
            if (v < -0.001) return 'down';
            return 'flat';
        },

        /**
         * 变动箭头
         */
        changeArrow(val) {
            if (val === null || val === undefined || isNaN(val)) return '';
            const v = Number(val);
            if (v > 0.001) return '↑';
            if (v < -0.001) return '↓';
            return '→';
        },

        /**
         * 安全销毁 ECharts 实例
         */
        disposeChart(key) {
            if (AppState.echartsInstances[key]) {
                AppState.echartsInstances[key].dispose();
                delete AppState.echartsInstances[key];
            }
        },

        /**
         * 初始化或获取 ECharts 实例
         */
        getChart(domId) {
            const el = document.getElementById(domId);
            if (!el) return null;
            let inst = echarts.getInstanceByDom(el);
            if (inst) {
                inst.dispose();
            }
            inst = echarts.init(el);
            AppState.echartsInstances[domId] = inst;
            return inst;
        },

        /**
         * 获取当前选择的参数对象
         */
        currentParams() {
            return {
                product: AppState.currentProduct,
                month: AppState.currentMonth
            };
        },

        /**
         * 构建查询字符串
         */
        qs(obj) {
            return Object.entries(obj)
                .filter(([, v]) => v !== undefined && v !== null && v !== '')
                .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
                .join('&');
        }
    };

    /* ============ SVG 图标库（GMP工业线条风格，24x24 viewBox） ============ */
    const Icons = {
        _svg(content, size) {
            const s = size || 14;
            return `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;">${content}</svg>`;
        },
        // 卡片区标题图标
        table()   { return this._svg('<rect x="3" y="4" width="18" height="16" rx="1"/><line x1="3" y1="10" x2="21" y2="10"/><line x1="12" y1="4" x2="12" y2="20"/>'); },
        chart()   { return this._svg('<line x1="4" y1="20" x2="20" y2="20"/><rect x="5" y="12" width="3" height="8"/><rect x="10.5" y="8" width="3" height="12"/><rect x="16" y="4" width="3" height="16"/>'); },
        pie()     { return this._svg('<path d="M21 12a9 9 0 1 1-9-9"/><path d="M21 12H12V3"/>'); },
        waterfall(){ return this._svg('<path d="M4 18V6M8 15V9M12 12v-3M16 14V8M20 16V10" stroke-width="3"/><line x1="3" y1="20" x2="21" y2="20"/>'); },
        material(){ return this._svg('<path d="M3 7l9-4 9 4-9 4-9-4z"/><path d="M3 12l9 4 9-4M3 17l9 4 9-4"/>'); },
        factory() { return this._svg('<path d="M2 20h20V10l-6 4V10l-5 3V10L5 13v4L2 13v7z"/><line x1="8" y1="20" x2="8" y2="16"/><line x1="13" y1="20" x2="13" y2="16"/><line x1="18" y1="20" x2="18" y2="16"/>'); },
        labor()   { return this._svg('<circle cx="12" cy="7" r="3"/><path d="M5 21c0-4 3-7 7-7s7 3 7 7"/><line x1="9" y1="13" x2="15" y2="13"/>'); },
        bench()   { return this._svg('<path d="M4 20V8M20 20V4M10 20v-6M14 20V10"/><line x1="2" y1="20" x2="22" y2="20"/><rect x="3" y="8" width="4" height="12" fill="currentColor" opacity="0.2"/><rect x="16" y="4" width="4" height="16" fill="currentColor" opacity="0.2"/>'); },
        // 状态/告警
        warn()    { return this._svg('<path d="M12 3L2 20h20L12 3z"/><line x1="12" y1="10" x2="12" y2="14"/><circle cx="12" cy="17" r="0.6" fill="currentColor"/>'); },
        down()    { return this._svg('<line x1="12" y1="5" x2="12" y2="17"/><polyline points="6 11 12 17 18 11"/>'); },
        // 空状态
        empty()   { return this._svg('<rect x="3" y="5" width="18" height="14" rx="2"/><line x1="3" y1="10" x2="21" y2="10"/><path d="M9 15l2 2 4-4"/>', 44); },
        // 动作按钮
        report()  { return this._svg('<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="9" y1="13" x2="15" y2="13"/><line x1="9" y1="17" x2="13" y2="17"/>'); },
        gear()    { return this._svg('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>'); },
        write()   { return this._svg('<path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>'); },
        ok()      { return this._svg('<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>'); },
        eye()     { return this._svg('<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>'); },
        history() { return this._svg('<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><polyline points="3 3 3 8 8 8"/><polyline points="12 7 12 12 15 14"/>'); },
        download(){ return this._svg('<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>', 16); },
        search()  { return this._svg('<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>'); },
        scale()   { return this._svg('<line x1="4" y1="20" x2="20" y2="20"/><path d="M5 20V10a7 7 0 0 1 14 0v10"/><line x1="9" y1="13" x2="15" y2="13"/>'); },
        robot()   { return this._svg('<rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><path d="M12 7v4"/><circle cx="8" cy="16" r="1" fill="currentColor"/><circle cx="16" cy="16" r="1" fill="currentColor"/>'); },
        bolt()    { return this._svg('<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>'); },
        close()   { return this._svg('<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>'); },
        // 小尺寸空状态图标（用于emptyState）
        emptysm() { return this._svg('<rect x="3" y="5" width="18" height="14" rx="2"/><line x1="3" y1="10" x2="21" y2="10"/><path d="M9 15l2 2 4-4"/>', 40); },
        // 排序
        trophy()  { return '<span style="display:inline-block;width:14px;height:14px;vertical-align:-2px;background:repeating-linear-gradient(45deg,#d4610a,#d4610a 2px,#ef7c26 2px,#ef7c26 4px);-webkit-mask:linear-gradient(#000,#000);mask:linear-gradient(#000,#000);clip-path:polygon(50% 0,61% 35%,98% 35%,68% 57%,79% 91%,50% 70%,21% 91%,32% 57%,2% 35%,39% 35%);"></span> ' },
    };

    /* ============ 页面标题映射 ============ */
    const pageTitles = {
        dashboard: { title: '成本看板', subtitle: '实时掌握制药成本全貌' },
        report: { title: '智能报告', subtitle: '一键生成成本分析报告' },
        benchmark: { title: '对标分析', subtitle: '成本差异分解与AI归因' },
        rpa: { title: 'RPA整改', subtitle: '自动化任务管理与闭环' }
    };

    /* ============ 路由 ============ */
    const Router = {
        pages: ['dashboard', 'report', 'benchmark', 'rpa'],

        init() {
            window.addEventListener('hashchange', () => this.handleRoute());
            document.querySelectorAll('.nav-item').forEach(item => {
                item.addEventListener('click', (e) => {
                    e.preventDefault();
                    const page = item.dataset.page;
                    window.location.hash = page;
                });
            });
            this.handleRoute();
        },

        handleRoute() {
            let hash = window.location.hash.replace('#', '') || 'dashboard';
            if (!this.pages.includes(hash)) hash = 'dashboard';
            this.navigate(hash);
        },

        navigate(page) {
            document.querySelectorAll('.nav-item').forEach(item => {
                item.classList.toggle('active', item.dataset.page === page);
            });
            document.querySelectorAll('.page-container').forEach(el => {
                el.classList.toggle('active', el.id === 'page-' + page);
            });
            AppState.currentPage = page;

            // 更新页面标题
            const info = pageTitles[page] || pageTitles.dashboard;
            const titleEl = document.getElementById('pageTitle');
            const subtitleEl = document.getElementById('pageSubtitle');
            if (titleEl) titleEl.textContent = info.title;
            if (subtitleEl) subtitleEl.textContent = info.subtitle;

            this.renderPage(page);

            // 重新初始化 Lucide 图标（页面内容更新后需要）
            setTimeout(refreshIcons, 50);
        },

        renderPage(page) {
            switch (page) {
                case 'dashboard':
                    if (typeof DashboardPage !== 'undefined') DashboardPage.render();
                    break;
                case 'report':
                    if (typeof ReportPage !== 'undefined') ReportPage.render();
                    break;
                case 'benchmark':
                    if (typeof BenchmarkPage !== 'undefined') BenchmarkPage.render();
                    break;
                case 'rpa':
                    if (typeof RpaPage !== 'undefined') RpaPage.render();
                    break;
            }
        }
    };

    /* ============ 选择器管理 ============ */
    const Selectors = {
        async loadProducts() {
            try {
                const data = await Utils.api('/api/products');
                AppState.products = data.products || [];
                AppState.months = data.months || [];
            } catch (e) {
                // 使用默认值
                AppState.products = ['银黄口服液', '板蓝根颗粒', '六味地黄胶囊'];
                AppState.months = ['2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06'];
            }

            // 缓存产品规格映射 {name -> spec}，便于全项目复用
            AppState.productSpecs = {};
            AppState.products.forEach(p => {
                const name = Utils.productName(p);
                if (typeof p === 'object' && p.spec) {
                    AppState.productSpecs[name] = p.spec;
                }
            });

            // 设置默认值（始终存储为名称字符串，避免 [object Object]）
            AppState.currentProduct = Utils.productName(AppState.products[0]) || '银黄口服液';
            AppState.currentMonth = AppState.months[AppState.months.length - 1] || '2026-06';

            this.populateSelects();
        },

        populateSelects() {
            const prodSel = document.getElementById('globalProduct');
            const monthSel = document.getElementById('globalMonth');

            prodSel.innerHTML = AppState.products.map(p => {
                const name = Utils.productName(p);
                return `<option value="${name}" ${name === AppState.currentProduct ? 'selected' : ''}>${Utils.productLabel(p)}</option>`;
            }).join('');

            monthSel.innerHTML = AppState.months.map(m =>
                `<option value="${m}" ${m === AppState.currentMonth ? 'selected' : ''}>${m}</option>`
            ).join('');

            prodSel.addEventListener('change', () => {
                AppState.currentProduct = prodSel.value;
                Router.renderPage(AppState.currentPage);
            });

            monthSel.addEventListener('change', () => {
                AppState.currentMonth = monthSel.value;
                Router.renderPage(AppState.currentPage);
            });
        }
    };

    /* ============ 窗口 resize 时重绘图表 ============ */
    window.addEventListener('resize', () => {
        Object.values(AppState.echartsInstances).forEach(chart => {
            if (chart && !chart.isDisposed()) chart.resize();
        });
    });

    /* ============ 暴露到全局 ============ */
    window.AppState = AppState;
    window.Utils = Utils;
    window.Router = Router;
    window.Selectors = Selectors;
    window.Icons = Icons;

    /* ============ 启动 ============ */
    document.addEventListener('DOMContentLoaded', async () => {
        Utils.showLoading('系统初始化中...');
        // 路由必须先启动，避免产品接口异常时整个 SPA 被锁在当前模块。
        Router.init();
        // 路由和页面骨架已就绪，立即解除全屏遮罩；各页面自行显示局部加载状态。
        Utils.hideLoading();
        try {
            await Selectors.loadProducts();
        } finally {
            Utils.hideLoading();
            // 产品数据到达后刷新当前页面，补齐页面级下拉框和真实数据。
            Router.renderPage(AppState.currentPage);
            setTimeout(refreshIcons, 50);
        }
    });

})();

/* ============ Initialize Lucide Icons ============ */
function refreshIcons() {
    if (window.lucide) lucide.createIcons();
}
// Call after initial render
setTimeout(refreshIcons, 100);
