/**
 * report.js — 智能报告生成页面
 */
const ReportPage = {
    _rendered: false,
    _reportId: null,
    _reportData: null,
    _estimateTimer: null,
    _estimateStartedAt: 0,
    _estimateSeconds: 60,
    _reportResumeKey: 'pharmacost.report.generating',
    _cancelRequested: false,
    _generationToken: 0,
    _templates: [],
    _historyPage: 1,
    _historyPageSize: 10,
    _historyTotalPages: 1,
    _generatingTaskDrafts: false,

    render() {
        const container = document.getElementById('page-report');
        // 产品和月份会在数据导入后重新加载。报告页此前只在首次渲染时
        // 读取一次 AppState，导致后续导入的月份无法出现在报告参数中。
        if (!AppState.selectorsReady) return;
        if (!this._rendered) {
            container.innerHTML = this.template();
            this.bindEvents();
            this._rendered = true;
            this.loadHistory();
            this.loadTemplates();
            this.resumeGeneratingReport();
        }
        this.syncSelectors();
    },

    syncSelectors() {
        const product = document.getElementById('reportProduct');
        const month = document.getElementById('reportMonth');
        if (!product || !month) return;
        const productOptions = AppState.products.map(item => {
            const name = Utils.productName(item);
            return `<option value="${Utils.escapeHtml(name)}">${Utils.escapeHtml(Utils.productLabel(item))}</option>`;
        }).join('');
        const monthOptions = AppState.months.map(item =>
            `<option value="${Utils.escapeHtml(item)}">${Utils.escapeHtml(item)}</option>`
        ).join('');
        if (product.innerHTML !== productOptions) product.innerHTML = productOptions;
        if (month.innerHTML !== monthOptions) month.innerHTML = monthOptions;
        product.value = AppState.currentProduct;
        month.value = AppState.currentMonth;
    },

    template() {
        return `
            <!-- Stepper -->
            <div class="stepper">
                <div class="step done"><div class="dot"><i data-lucide="check" style="width:16px;height:16px"></i></div><span>选择参数</span></div>
                <div class="step" id="step2"><div class="dot">2</div><span>生成报告</span></div>
                <div class="step" id="step3"><div class="dot">3</div><span>预览导出</span></div>
            </div>

            <div style="display:grid;grid-template-columns:1fr 2fr;gap:24px">
                <!-- Left: Report Config -->
                <div class="card report-config-card">
                    <div class="report-config-heading">
                        <div>
                            <h3 class="card-title">报告参数</h3>
                        </div>
                    </div>
                    <div class="report-config-fields">
                        <div class="form-group report-config-field">
                            <label>产品</label>
                            <select id="reportProduct">
                                ${AppState.products.map(p => {
                                    const name = Utils.productName(p);
                                    return `<option value="${name}" ${name === AppState.currentProduct ? 'selected' : ''}>${Utils.productLabel(p)}</option>`;
                                }).join('')}
                            </select>
                        </div>
                        <div class="form-group report-config-field">
                            <label>月份</label>
                            <select id="reportMonth">
                                ${AppState.months.map(m =>
                                    `<option value="${m}" ${m === AppState.currentMonth ? 'selected' : ''}>${m}</option>`
                                ).join('')}
                            </select>
                        </div>
                        <div class="form-group report-config-field">
                            <label>报告模板</label>
                            <select id="reportType">
                                <option value="monthly">月度成本分析</option>
                                <option value="quarterly">季度成本分析</option>
                                <option value="topic">专题分析</option>
                            </select>
                        </div>
                    </div>
                    <button id="btnGenerate" class="btn btn-primary report-generate-button">
                        <i data-lucide="sparkles" style="width:16px;height:16px"></i> 生成报告
                    </button>
                </div>

                <!-- Right: Report Preview -->
                <div class="card" id="reportResult">
                    <div class="card-header">
                        <h3 class="card-title">报告预览</h3>
                        <div style="display:flex;gap:8px">
                            <button class="btn btn-secondary" id="generate-report-tasks" disabled title="报告生成完成后，可使用整改任务清单生成草稿"><i data-lucide="list-plus" style="width:16px;height:16px"></i> 生成整改任务</button>
                            <button class="btn btn-secondary" id="download-report-word" disabled title="导出 Word 文档"><i data-lucide="file-text" style="width:16px;height:16px"></i> 导出 Word</button>
                            <button class="btn btn-secondary" id="download-report-pdf" disabled title="导出 PDF 文件"><i data-lucide="file-down" style="width:16px;height:16px"></i> 导出 PDF</button>
                        </div>
                    </div>
                    <div class="report-preview" id="reportPreviewContent">
                        <!-- Report sections will be rendered here after generation -->
                        <div style="text-align:center;padding:80px 0;color:var(--phc-ink-3)">
                            <i data-lucide="file-text" style="width:48px;height:48px;opacity:0.3"></i>
                            <p style="margin-top:12px;font-size:14px">选择参数后点击"生成报告"查看预览</p>
                        </div>
                    </div>
                </div>
            </div>

            <!-- History -->
            <div class="card" style="margin-top:24px">
                <div class="card-header">
                    <div><h3 class="card-title">历史报告</h3><small style="color:var(--phc-ink-3)">每次生成都会保留一条记录，可分页查看全部报告</small></div>
                </div>
                <div id="historyList">${Utils.inlineLoading('加载历史报告...')}</div>
            </div>
        `;
    },

    bindEvents() {
        document.getElementById('btnGenerate').addEventListener('click', () => this.startGenerate());
        document.getElementById('generate-report-tasks').addEventListener('click', () => this.generateReportTasks());
        document.getElementById('download-report-word').addEventListener('click', () => this.exportReport('docx'));
        document.getElementById('download-report-pdf').addEventListener('click', () => this.exportReport('pdf'));
        const product = document.getElementById('reportProduct');
        const month = document.getElementById('reportMonth');
        if (product) product.addEventListener('change', () => {
            AppState.currentProduct = product.value;
        });
        if (month) month.addEventListener('change', () => {
            AppState.currentMonth = month.value;
        });

        const historyList = document.getElementById('historyList');
        if (historyList) {
            historyList.addEventListener('click', event => {
                const deleteBtn = event.target.closest('[data-delete-task]');
                if (deleteBtn) {
                    this.deleteHistoryReport(deleteBtn.dataset.deleteTask);
                    return;
                }
                const pageButton = event.target.closest('[data-report-history-page]');
                if (pageButton && !pageButton.disabled) {
                    this.loadHistory(Number(pageButton.dataset.reportHistoryPage));
                }
            });
        }

    },

    async loadTemplates() {
        try {
            const data = await Utils.api('/api/settings/summary', { timeoutMs: 15000 });
            this._templates = (data.templates || []).filter(item => item.resource_id);
            const select = document.getElementById('reportType');
            if (!select || !this._templates.length) return;
            select.innerHTML = '<option value="monthly">月度成本分析</option><option value="quarterly">季度成本分析</option><option value="topic">专题分析</option><optgroup label="自定义模板">' +
                this._templates.map(item => `<option value="template:${Utils.escapeHtml(item.resource_id)}">${Utils.escapeHtml(item.name)}</option>`).join('') + '</optgroup>';
        } catch (error) {
            console.warn('报告模板读取失败', error);
        }
    },

    /* ============ 生成报告 ============ */
    async startGenerate() {
        const product = document.getElementById('reportProduct').value;
        const month = document.getElementById('reportMonth').value;
        const selectedType = document.getElementById('reportType').value;
        const templateId = selectedType.startsWith('template:') ? selectedType.slice('template:'.length) : '';
        const customTemplate = this._templates.find(item => item.resource_id === templateId);
        const reportType = customTemplate?.report_type || selectedType;

        if (!product || !month) {
            await AppDialog.alert({
                title: '请选择报告参数',
                message: '请选择产品和月份后再生成报告。',
                icon: 'circle-alert',
            });
            return;
        }

        const btn = document.getElementById('btnGenerate');
        btn.disabled = true;
        btn.innerHTML = '<div class="spinner-small" style="width:18px;height:18px;border-width:2px"></div> 生成中...';

        // 更新 stepper：step 2 变为 active
        document.getElementById('step2').className = 'step active';
        document.getElementById('step2').querySelector('.dot').textContent = '';
        document.getElementById('step2').querySelector('.dot').innerHTML = '<div class="spinner-small" style="width:14px;height:14px;border-width:2px"></div>';

        this._reportId = null;
        this._cancelRequested = false;
        const generationToken = ++this._generationToken;
        this._reportData = null;
        this.setDownloadState(false);
        this.setReportTaskGenerationState(false);

        // 清空报告预览区域
        const previewContent = document.getElementById('reportPreviewContent');
        previewContent.innerHTML = `
            <div style="text-align:center;padding:80px 0;color:var(--phc-ink-3)">
                <div class="spinner-small" style="width:32px;height:32px;border-width:3px;margin:0 auto"></div>
                <p data-report-progress style="margin-top:12px;font-size:14px">正在生成报告，请稍候...</p>
                <p data-report-estimate class="report-time-estimate">正在计算预计时间...</p>
                <button type="button" data-report-cancel class="btn btn-outline btn-sm report-cancel-button">中断生成</button>
            </div>
        `;
        this.startEstimateTimer();
        this.bindCancelButton();

        try {
            const resp = await fetch(`/api/report/generate?${Utils.qs({ product, month, report_type: reportType, template_id: templateId || undefined })}`, {
                method: 'POST'
            });
            const data = await resp.json();
            if (this._cancelRequested || generationToken !== this._generationToken) {
                if (data && data.task_id) this.cancelTaskOnServer(data.task_id);
                return;
            }
            if (!resp.ok || data.status === 'failed') {
                throw new Error(data.error || '报告生成失败');
            }
            this._reportId = data.task_id;
            this.setEstimateSeconds(data.estimate_seconds);
            this.persistGeneratingReport(data.task_id, product, month, reportType);
            this._reportData = data;
            if (data.status === 'completed') {
                this.onReportComplete(data);
            } else {
                await this.pollReportStatus(data.task_id, generationToken);
            }
        } catch (e) {
            if (this._cancelRequested || generationToken !== this._generationToken) return;
            this.onReportFailed(e.message || '报告生成失败');
        }
    },

    async pollReportStatus(taskId, generationToken = this._generationToken) {
        const previewContent = document.getElementById('reportPreviewContent');
        const startedAt = Date.now();
        const maxWaitMs = 10 * 60 * 1000;
        for (;;) {
            await new Promise(resolve => setTimeout(resolve, 1200));
            if (this._cancelRequested || generationToken !== this._generationToken) return;
            if (Date.now() - startedAt >= maxWaitMs) {
                throw new Error('报告生成超时，请检查服务状态后重试');
            }
            const data = await Utils.api(`/api/report/${encodeURIComponent(taskId)}/status`, { timeoutMs: 15000 });
            if (this._cancelRequested || generationToken !== this._generationToken) return;
            this._reportData = data;
            this.setEstimateSeconds(data.estimate_seconds);
            if (data.status === 'completed') {
                this.onReportComplete(data);
                return;
            }
            if (data.status === 'failed') throw new Error(data.error || '报告生成失败');
            if (previewContent) {
                const progress = previewContent.querySelector('[data-report-progress]');
                if (progress) progress.textContent = data.status === 'queued' ? '报告已排队，正在等待处理...' : '正在生成报告，请稍候...';
                this.updateEstimateText(data.status === 'queued');
            }
        }
    },

    onReportComplete(data = this._reportData || {}) {
        this._reportData = data;
        this.stopEstimateTimer();
        this.clearGeneratingReport();
        const btn = document.getElementById('btnGenerate');
        btn.disabled = false;
        btn.innerHTML = '<i data-lucide="sparkles" style="width:16px;height:16px"></i> 生成报告';

        // 更新 stepper：所有步骤完成
        const step2 = document.getElementById('step2');
        const step3 = document.getElementById('step3');
        if (step2) {
            step2.className = 'step done';
            step2.querySelector('.dot').innerHTML = '<i data-lucide="check" style="width:16px;height:16px"></i>';
        }
        if (step3) {
            step3.className = 'step done';
            step3.querySelector('.dot').innerHTML = '<i data-lucide="check" style="width:16px;height:16px"></i>';
        }

        // 激活下载按钮
        // 显示成功状态 + 报告预览
        const previewContent = document.getElementById('reportPreviewContent');
        const product = document.getElementById('reportProduct').value;
        const month = document.getElementById('reportMonth').value;

        const safe = value => Utils.escapeHtml(String(value ?? ''));
        const sections = (data.preview && data.preview.sections) || [];
        const references = (data.preview && data.preview.references) || [];
        const previewTitle = (data.preview && data.preview.title) || `${product}成本分析报告`;
        const pdfNotice = data.pdf_error
            ? `<div class="status-pill warning report-pdf-warning"><i data-lucide="triangle-alert" style="width:14px;height:14px"></i> PDF导出暂不可用，Word报告仍可下载</div>`
            : '';
        previewContent.scrollTop = 0;
        previewContent.innerHTML = `
            <div style="margin-bottom:16px">
                <span class="status-pill success"><i data-lucide="check-circle" style="width:14px;height:14px"></i> 报告生成成功</span>
                ${pdfNotice}
                <span style="margin-left:8px;font-size:12px;color:var(--phc-ink-3)">${safe(previewTitle)} · 产品：${safe(data.product || product)} · 月份：${safe(data.month || month)}</span>
            </div>
            ${sections.length ? sections.map(section => `<div class="report-section"><h4>${safe(section.title)}</h4><div class="report-markdown">${this._renderPreviewText(section.content)}</div></div>`).join('') : '<div class="empty-state"><p>报告已生成，但预览内容为空，请导出 Word 或 PDF 查看完整报告。</p></div>'}
            ${references.length ? `<div class="rag-citation report-rag-citation"><i data-lucide="book-open"></i><span>知识库来源：${references.map(safe).join('；')}</span></div>` : ''}
        `;
        this.setDownloadState(true, Boolean(data.pdf_path));
        this.setReportTaskGenerationState(this.getReportTaskCandidates().length > 0);

        // 重新初始化 lucide 图标
        if (window.lucide) lucide.createIcons();
        this.loadHistory(1);
    },

    _renderPreviewText(text) {
        const escaped = Utils.escapeHtml(String(text ?? ''));
        const inline = value => value
            .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
            .replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/g, '<strong>$1</strong>');
        const lines = escaped.split('\n');
        let html = '';
        let listType = null;
        let paragraph = [];
        let tableLines = [];

        const isTableRow = line => /^\|?.+\|.+\|?$/.test(line);
        // 模型有时省略 Markdown 的 |---| 分隔行；连续的多列管道行
        // 仍应按表格渲染，保持预览与 Word/PDF 导出一致。
        const isPipeDataRow = line => line.includes('|') && line.split('|').length >= 3;
        const isTableSeparator = line => /^\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?$/.test(line);
        const closeTable = () => {
            if (tableLines.length < 2) {
                tableLines = [];
                return;
            }
            const rows = tableLines.map(row => row.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map(cell => inline(cell.trim())));
            const head = rows[0];
            html += '<div class="markdown-table-wrap"><table><thead><tr>' + head.map(cell => `<th>${cell}</th>`).join('') + '</tr></thead><tbody>';
            rows.slice(1).forEach(row => {
                html += `<tr>${row.map(cell => `<td>${cell}</td>`).join('')}</tr>`;
            });
            html += '</tbody></table></div>';
            tableLines = [];
        };

        const closeParagraph = () => {
            if (paragraph.length) {
                html += `<p>${paragraph.join('<br>')}</p>`;
                paragraph = [];
            }
        };
        const closeList = () => {
            if (listType) {
                html += `</${listType}>`;
                listType = null;
            }
        };

        lines.forEach((rawLine, lineIndex) => {
            const line = rawLine.trim();
            if (!line) {
                // 保留表格中的空行，直到遇到下一条非管道文本；这样可
                // 处理模型在表格行之间插入空白行的常见输出格式。
                if (!tableLines.length) {
                    closeParagraph();
                    closeList();
                }
                return;
            }
            if (isTableRow(line)) {
                if (isTableSeparator(line) && tableLines.length) {
                    return;
                }
                let nextIndex = lineIndex + 1;
                while (nextIndex < lines.length && !lines[nextIndex].trim()) nextIndex++;
                const nextLine = (lines[nextIndex] || '').trim();
                if (tableLines.length || isTableSeparator(nextLine) ||
                    (isPipeDataRow(line) && isPipeDataRow(nextLine))) {
                    closeParagraph();
                    closeList();
                    tableLines.push(line);
                    return;
                }
            }
            if (tableLines.length) closeTable();
            const heading = line.match(/^#{1,6}\s+(.*)$/);
            if (heading) {
                closeParagraph();
                closeList();
                html += `<h5>${inline(heading[1])}</h5>`;
                return;
            }
            const bullet = line.match(/^•\s+(.*)$/) || line.match(/^[-*+]\s+(.*)$/);
            const numbered = line.match(/^(\d+(?:\.\d+){0,2})(?:\.\s+|\s+)(.+)$/);
            if (numbered) {
                closeParagraph();
                closeList();
                const level = numbered[1].split('.').length;
                const label = level === 1 ? `${numbered[1]}.` : numbered[1];
                html += `<div class="report-numbered-item level-${level}"><span class="report-numbered-label">${label}</span><span>${inline(numbered[2])}</span></div>`;
                return;
            }
            const ordered = line.match(/^\d+[)]\s+(.*)$/);
            const targetList = bullet ? 'ul' : (ordered ? 'ol' : null);
            const item = bullet ? bullet[1] : (ordered ? ordered[1] : null);
            if (targetList) {
                closeParagraph();
                if (listType !== targetList) {
                    closeList();
                    html += `<${targetList}>`;
                    listType = targetList;
                }
                html += `<li>${inline(item)}</li>`;
                return;
            }
            closeList();
            paragraph.push(inline(line));
        });
        closeParagraph();
        closeList();
        closeTable();
        return html || '<p></p>';
    },

    setDownloadState(enabled, pdfEnabled = enabled) {
        const states = {
            'download-report-word': enabled,
            'download-report-pdf': pdfEnabled,
        };
        Object.entries(states).forEach(([id, state]) => {
            const button = document.getElementById(id);
            if (button) button.disabled = !state;
        });
        const pdfButton = document.getElementById('download-report-pdf');
        if (pdfButton && enabled && !pdfEnabled) pdfButton.title = 'PDF导出不可用，请安装 reportlab 或配置 Word';
    },

    getReportTaskCandidates() {
        const candidates = this._reportData?.preview?.report_task_candidates;
        return Array.isArray(candidates)
            ? candidates.filter(item => item && typeof item === 'object' && String(item.task_title || '').trim())
            : [];
    },

    setReportTaskGenerationState(enabled, title) {
        const button = document.getElementById('generate-report-tasks');
        if (!button || this._generatingTaskDrafts) return;
        button.disabled = !enabled;
        button.title = title || (enabled
            ? '使用本报告的整改任务清单生成待审阅草稿'
            : '本报告未包含可用整改任务；历史报告可能缺少候选任务');
    },

    _reportTaskConclusion(candidates) {
        const findings = candidates.map(item => {
            const source = item?.source && typeof item.source === 'object' ? item.source : {};
            return String(source.finding || source.attribution_conclusion || '').trim();
        }).filter(Boolean);
        const titles = candidates.map(item => String(item.task_title || '').trim()).filter(Boolean);
        return [...new Set(findings)].join('\n') || `报告整改任务清单：${titles.join('；')}`;
    },

    async generateReportTasks() {
        if (this._generatingTaskDrafts) return;
        const candidates = this.getReportTaskCandidates();
        if (!candidates.length) {
            this.showReportTaskGenerationResult(false, '本报告未包含可用整改任务，无法生成草稿。');
            this.setReportTaskGenerationState(false);
            return;
        }
        const report = this._reportData || {};
        const product = String(report.product || document.getElementById('reportProduct')?.value || '').trim();
        const month = String(report.month || document.getElementById('reportMonth')?.value || '').trim();
        if (!product || !month) {
            this.showReportTaskGenerationResult(false, '报告参数缺失，请重新生成报告后再试。');
            return;
        }

        const button = document.getElementById('generate-report-tasks');
        this._generatingTaskDrafts = true;
        button.disabled = true;
        button.title = '正在准备整改任务草稿';
        button.innerHTML = '<div class="spinner-small" style="width:16px;height:16px;border-width:2px"></div> 生成中...';
        try {
            const data = await Utils.api('/api/rpa/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    product,
                    month,
                    analysis_scenario: 'report_generated_tasks',
                    attribution_conclusion: this._reportTaskConclusion(candidates).slice(0, 50000),
                    analysis_evidence: {
                        source: '报告整改任务清单',
                        report_title: report.preview?.title || '',
                        report_generated_at: report.preview?.generated_at || '',
                        report_task_candidates: candidates,
                    },
                    prebuilt_tasks: candidates,
                }),
                timeoutMs: 30000,
            });
            if (!data.tasks_generated || !window.TaskDraftDialog) {
                throw new Error('未生成可审阅的整改任务草稿');
            }
            const submitted = Number(data.submitted_tasks || candidates.length);
            const rejected = Number(data.rejected_tasks || 0);
            const rejectedDetail = rejected && Array.isArray(data.rejection_reasons)
                ? `；已跳过${rejected}项：${data.rejection_reasons.join('；')}` : '';
            this.showReportTaskGenerationResult(
                !rejected,
                `报告清单${submitted}项，已生成${data.tasks_generated}项草稿${rejectedDetail}`,
            );
            TaskDraftDialog.open(data.tasks || [], result => {
                this.showReportTaskGenerationResult(true, `已保存${result.tasks_saved || 0}项整改任务草稿，请前往模块四发送至 RPA。`);
            });
        } catch (error) {
            const invalid = String(error?.message || '').includes('HTTP 422');
            this.showReportTaskGenerationResult(false, invalid
                ? '报告整改任务不完整或不可执行，请重新生成报告后再试。'
                : '整改任务草稿生成失败，请稍后重试。');
        } finally {
            this._generatingTaskDrafts = false;
            if (button) {
                button.innerHTML = '<i data-lucide="list-plus" style="width:16px;height:16px"></i> 生成整改任务';
                this.setReportTaskGenerationState(this.getReportTaskCandidates().length > 0);
            }
            if (window.lucide) lucide.createIcons();
        }
    },

    showReportTaskGenerationResult(success, message) {
        document.getElementById('reportTaskGenerationToast')?.remove();
        const notice = document.createElement('div');
        notice.id = 'reportTaskGenerationToast';
        notice.className = `operation-toast${success ? '' : ' error'}`;
        notice.innerHTML = `<i data-lucide="${success ? 'check-circle' : 'alert-circle'}"></i><span>${Utils.escapeHtml(message)}${success ? ' <a href="#rpa">查看任务</a>' : ''}</span>`;
        document.body.appendChild(notice);
        if (window.lucide) lucide.createIcons();
        setTimeout(() => notice.remove(), 6000);
    },

    async exportReport(format) {
        try {
            await this.download(format);
        } catch (error) {
            if (error && error.name === 'AbortError') {
                return;
            }
            await AppDialog.alert({
                title: '报告下载失败',
                message: error.message || '报告下载失败，请稍后重试。',
                danger: true,
                icon: 'circle-alert',
            });
        }
    },

    async download(format) {
        if (!this._reportId) {
            throw new Error('请先生成报告');
        }

        const extension = format === 'pdf' ? 'pdf' : 'docx';
        const filename = this.getReportDownloadFilename(extension);
        const url = `/api/report/${encodeURIComponent(this._reportId)}/download?format=${format}`;
        const response = await fetch(url);

        if (!response.ok) {
            throw new Error('报告下载失败');
        }

        const blob = await response.blob();

        if (window.showSaveFilePicker) {
            const handle = await window.showSaveFilePicker({
                suggestedName: filename,
                types: [{
                    description: format === 'pdf' ? 'PDF 文件' : 'Word 文档',
                    accept: {
                        [format === 'pdf'
                            ? 'application/pdf'
                            : 'application/vnd.openxmlformats-officedocument.wordprocessingml.document']: [`.${extension}`]
                    }
                }]
            });
            const writable = await handle.createWritable();
            await writable.write(blob);
            await writable.close();
            return;
        }

        const link = document.createElement('a');
        const blobUrl = URL.createObjectURL(blob);
        link.href = blobUrl;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(blobUrl);
    },

    getReportDownloadFilename(extension) {
        const report = this._reportData || {};
        const cleanPart = (value, fallback) => String(value || fallback)
            .replace(/[\\/:*?"<>|]/g, '_')
            .replace(/\s+/g, ' ')
            .trim() || fallback;
        const product = cleanPart(report.product || document.getElementById('reportProduct')?.value, '产品');
        const month = cleanPart(report.month || document.getElementById('reportMonth')?.value, '月份');
        const reportId = cleanPart(this._reportId, '报告编号');
        return `成本分析报告_${product}_${month}_${reportId}.${extension}`;
    },


    onReportFailed(message) {
        this.stopEstimateTimer();
        this.clearGeneratingReport();
        const btn = document.getElementById('btnGenerate');
        btn.disabled = false;
        btn.innerHTML = '<i data-lucide="sparkles" style="width:16px;height:16px"></i> 生成报告';
        this.setReportTaskGenerationState(false);

        // stepper 恢复
        const step2 = document.getElementById('step2');
        if (step2) {
            step2.className = 'step';
            step2.querySelector('.dot').textContent = '2';
        }

        // 显示错误状态
        const previewContent = document.getElementById('reportPreviewContent');
        previewContent.innerHTML = `
            <div style="text-align:center;padding:40px 0">
                <span class="status-pill error"><i data-lucide="alert-circle" style="width:14px;height:14px"></i> 报告生成失败</span>
                <p style="margin-top:12px;font-size:13px;color:var(--phc-ink-3)">${message}</p>
                <button class="btn btn-secondary" style="margin-top:16px" onclick="ReportPage.startGenerate()"><i data-lucide="refresh-ccw" style="width:16px;height:16px"></i>重试</button>
            </div>
        `;
        if (window.lucide) lucide.createIcons();
    },

    /* ============ 加载历史报告 ============ */
    async loadHistory(page = this._historyPage) {
        const target = document.getElementById('historyList');
        if (!target) return;
        try {
            const requestedPage = Math.max(1, Number(page) || 1);
            const data = await Utils.api(`/api/report/history?${Utils.qs({ page: requestedPage, page_size: this._historyPageSize })}`);
            this._historyPage = Number(data.page || requestedPage);
            this._historyTotalPages = Number(data.total_pages || 1);
            target.innerHTML = data.items && data.items.length
                ? `${this._renderHistoryTable(data.items)}${this._renderHistoryPagination(data)}`
                : Utils.emptyState('', '暂无历史报告');
            if (window.lucide) lucide.createIcons();
        } catch (e) {
            target.innerHTML = Utils.emptyState('', '历史报告加载失败');
        }
    },

    _renderHistoryPagination(data) {
        const page = Number(data.page || 1);
        const totalPages = Math.max(1, Number(data.total_pages || 1));
        const total = Number(data.total || 0);
        const start = Math.max(1, Math.min(page - 2, totalPages - 4));
        const end = Math.min(totalPages, start + 4);
        let pageButtons = '';
        for (let number = start; number <= end; number += 1) {
            pageButtons += `<button type="button" class="btn btn-outline btn-sm report-history-page${number === page ? ' active' : ''}" data-report-history-page="${number}" ${number === page ? 'aria-current="page"' : ''}>${number}</button>`;
        }
        return `<div class="report-history-pagination">
            <span>共 ${total} 条 · 第 ${page} / ${totalPages} 页</span>
            <div class="report-history-page-buttons">
                <button type="button" class="btn btn-outline btn-sm" data-report-history-page="${page - 1}" ${page <= 1 ? 'disabled' : ''} aria-label="上一页"><i data-lucide="chevron-left"></i>上一页</button>
                ${pageButtons}
                <button type="button" class="btn btn-outline btn-sm" data-report-history-page="${page + 1}" ${page >= totalPages ? 'disabled' : ''} aria-label="下一页">下一页<i data-lucide="chevron-right"></i></button>
            </div>
        </div>`;
    },

    _renderHistoryTable(reports) {
        let html = `<table class="data-table"><thead><tr>
            <th>报告编号</th><th>产品</th><th>月份</th><th>状态</th><th>生成时间</th><th>操作</th>
        </tr></thead><tbody>`;
        reports.forEach(r => {
            const safe = value => Utils.escapeHtml(String(value ?? ''));
            const taskId = r.task_id || r.id;
            const encodedId = encodeURIComponent(taskId);
            const filenameBase = `成本分析报告_${String(r.product || '产品').replace(/[\\/:*?"<>|]/g, '_')}_${String(r.month || '月份').replace(/[\\/:*?"<>|]/g, '_')}_${String(taskId || '报告编号').replace(/[\\/:*?"<>|]/g, '_')}`;
            const pdfAction = r.pdf_path
                ? `<a href="/api/report/${encodedId}/download?format=pdf" class="btn btn-sm btn-outline" download="${safe(filenameBase)}.pdf">PDF</a>`
                : '<span class="btn btn-sm btn-outline" style="opacity:.55;cursor:not-allowed" title="PDF导出不可用">PDF不可用</span>';
            html += `<tr>
                <td>${safe(taskId)}</td>
                <td>${safe(r.product)}</td>
                <td>${safe(r.month)}</td>
                <td><span class="badge badge-success">已完成</span></td>
                <td>${safe(r.created_at || r.time)}</td>
                <td>
                    <a href="/api/report/${encodedId}/download?format=docx" class="btn btn-sm btn-outline" download="${safe(filenameBase)}.docx">Word</a>
                    ${pdfAction}
                    <button type="button" class="btn btn-sm btn-outline" data-delete-task="${safe(taskId)}" style="color:var(--phc-state-error);border-color:var(--phc-state-error)" aria-label="删除报告 ${safe(taskId)}" title="删除报告"><i data-lucide="trash-2" aria-hidden="true"></i><span>删除</span></button>
                </td>
            </tr>`;
        });
        html += '</tbody></table>';
        return html;
    },

    /* ============ 删除历史报告 ============ */
    async deleteHistoryReport(taskId) {
        if (!taskId) return;
        const confirmed = await AppDialog.confirm({
            title: '删除历史报告',
            message: '确认删除这份历史报告？',
            detail: '删除后将同时移除对应的 Word 和 PDF 文件，且无法恢复。',
            confirmText: '确认删除',
            danger: true,
            icon: 'trash-2',
        });
        if (!confirmed) return;

        try {
            const response = await fetch(`/api/report/${encodeURIComponent(taskId)}`, { method: 'DELETE' });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(data.detail || '删除历史报告失败');
            }
            if (this._reportId === taskId) {
                this.resetCurrentReport();
            }
            await this.loadHistory(this._historyPage);
        } catch (error) {
            await AppDialog.alert({
                title: '删除失败',
                message: error.message || '删除历史报告失败，请稍后重试。',
                danger: true,
                icon: 'circle-alert',
            });
        }
    },

    resetCurrentReport() {
        this.stopEstimateTimer();
        this.clearGeneratingReport();
        this._reportId = null;
        this._reportData = null;
        this.setDownloadState(false);
        this.setReportTaskGenerationState(false);

        const previewContent = document.getElementById('reportPreviewContent');
        if (previewContent) {
            previewContent.innerHTML = `
                <div style="text-align:center;padding:80px 0;color:var(--phc-ink-3)">
                    <i data-lucide="file-text" style="width:48px;height:48px;opacity:0.3"></i>
                    <p style="margin-top:12px;font-size:14px">当前报告已删除，选择参数后点击"生成报告"查看预览</p>
                </div>
            `;
            if (window.lucide) lucide.createIcons();
        }

        const step2 = document.getElementById('step2');
        const step3 = document.getElementById('step3');
        if (step2) {
            step2.className = 'step';
            step2.querySelector('.dot').textContent = '2';
        }
        if (step3) {
            step3.className = 'step';
            step3.querySelector('.dot').textContent = '3';
        }
    },

    startEstimateTimer(startedAt = Date.now(), estimateSeconds = this._estimateSeconds || 60) {
        this.stopEstimateTimer();
        this._estimateStartedAt = startedAt;
        this.setEstimateSeconds(estimateSeconds);
        this.updateEstimateText(false);
        this._estimateTimer = setInterval(() => this.updateEstimateText(false), 1000);
    },

    setEstimateSeconds(value) {
        const seconds = Number(value);
        if (Number.isFinite(seconds) && seconds >= 5) {
            this._estimateSeconds = Math.max(15, Math.min(900, Math.round(seconds)));
        } else if (!this._estimateSeconds || this._estimateSeconds < 5) {
            this._estimateSeconds = 60;
        }
    },

    updateEstimateText(queued = false) {
        const target = document.querySelector('[data-report-estimate]');
        if (!target) return;
        const elapsed = Math.max(0, Math.floor((Date.now() - this._estimateStartedAt) / 1000));
        const remaining = this._estimateSeconds - elapsed;
        if (remaining > 0) {
            target.textContent = queued
                ? `预计等待约${remaining}秒`
                : `预计还需约${remaining}秒`;
        } else {
            target.textContent = `已用时${elapsed}秒，仍在处理中`;
        }
    },

    stopEstimateTimer() {
        if (this._estimateTimer) {
            clearInterval(this._estimateTimer);
            this._estimateTimer = null;
        }
    },

    bindCancelButton() {
        const button = document.querySelector('[data-report-cancel]');
        if (button) button.onclick = () => this.cancelGeneratingReport();
    },

    async cancelTaskOnServer(taskId) {
        try {
            await fetch(`/api/report/${encodeURIComponent(taskId)}/cancel`, {
                method: 'POST',
                credentials: 'same-origin',
            });
        } catch (_) {
            // The visible cancellation state is already restored; a later
            // history cleanup can remove a task if the request was transiently unavailable.
        }
    },

    persistGeneratingReport(taskId, product, month, reportType) {
        sessionStorage.setItem(this._reportResumeKey, JSON.stringify({
            taskId, product, month, reportType,
            startedAt: this._estimateStartedAt,
            estimateSeconds: this._estimateSeconds,
        }));
    },

    clearGeneratingReport() {
        sessionStorage.removeItem(this._reportResumeKey);
    },

    async resumeGeneratingReport() {
        let saved;
        try { saved = JSON.parse(sessionStorage.getItem(this._reportResumeKey) || 'null'); } catch (_) { saved = null; }
        if (!saved?.taskId) return;
        this._cancelRequested = false;
        const generationToken = ++this._generationToken;
        this._reportId = saved.taskId;
        this._estimateStartedAt = saved.startedAt || Date.now();
        this.setEstimateSeconds(saved.estimateSeconds || 60);
        const btn = document.getElementById('btnGenerate');
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<div class="spinner-small" style="width:18px;height:18px;border-width:2px"></div> 生成中...';
        }
        const step2 = document.getElementById('step2');
        if (step2) {
            step2.className = 'step active';
            step2.querySelector('.dot').innerHTML = '<div class="spinner-small" style="width:14px;height:14px;border-width:2px"></div>';
        }
        const previewContent = document.getElementById('reportPreviewContent');
        if (!previewContent) return;
        previewContent.innerHTML = `<div style="text-align:center;padding:80px 0;color:var(--phc-ink-3)"><div class="spinner-small" style="width:32px;height:32px;border-width:3px;margin:0 auto"></div><p data-report-progress style="margin-top:12px;font-size:14px">正在生成报告，请稍候...</p><p data-report-estimate class="report-time-estimate"></p><button type="button" data-report-cancel class="btn btn-outline btn-sm report-cancel-button">中断生成</button></div>`;
        this.bindCancelButton();
        this.startEstimateTimer(this._estimateStartedAt, this._estimateSeconds);
        try { await this.pollReportStatus(saved.taskId, generationToken); } catch (error) {
            if (!this._cancelRequested) this.onReportFailed(error.message || '报告生成失败');
        }
    },

    async cancelGeneratingReport() {
        const taskId = this._reportId;
        this._cancelRequested = true;
        ++this._generationToken;
        const button = document.querySelector('[data-report-cancel]');
        if (button) { button.disabled = true; button.textContent = '正在中断...'; }
        if (!taskId) {
            this.stopEstimateTimer();
            this.clearGeneratingReport();
            this.resetToInitialReportState();
            return;
        }
        try {
            const response = await fetch(`/api/report/${encodeURIComponent(taskId)}/cancel`, { method: 'POST', credentials: 'same-origin' });
            const data = await response.json().catch(() => ({}));
            // A task removed by an earlier cancellation is already in the
            // desired terminal state; keep the UI idempotent on refresh/retry.
            if (!response.ok && response.status !== 404) throw new Error(data.detail || '中断报告生成失败');
            this.stopEstimateTimer();
            this.clearGeneratingReport();
            this.resetToInitialReportState();
        } catch (error) {
            this._cancelRequested = false;
            if (button) { button.disabled = false; button.textContent = '中断生成'; }
            await AppDialog.alert({
                title: '中断失败',
                message: error.message || '中断报告生成失败，请稍后重试。',
                danger: true,
                icon: 'circle-alert',
            });
        }
    },

    resetToInitialReportState() {
        this.stopEstimateTimer();
        this.clearGeneratingReport();
        this._reportId = null;
        this._reportData = null;
        this.setDownloadState(false);
        this.setReportTaskGenerationState(false);
        const btn = document.getElementById('btnGenerate');
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<i data-lucide="sparkles" style="width:16px;height:16px"></i> 生成报告';
        }
        const step2 = document.getElementById('step2');
        const step3 = document.getElementById('step3');
        if (step2) {
            step2.className = 'step';
            step2.querySelector('.dot').textContent = '2';
        }
        if (step3) {
            step3.className = 'step';
            step3.querySelector('.dot').textContent = '3';
        }
        const previewContent = document.getElementById('reportPreviewContent');
        if (previewContent) {
            previewContent.innerHTML = `
                <div style="text-align:center;padding:80px 0;color:var(--phc-ink-3)">
                    <i data-lucide="file-text" style="width:48px;height:48px;opacity:0.3"></i>
                    <p style="margin-top:12px;font-size:14px">选择参数后点击“生成报告”查看预览</p>
                </div>
            `;
        }
        if (window.lucide) lucide.createIcons();
    },
};
