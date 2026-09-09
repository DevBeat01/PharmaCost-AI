/**
 * report.js — 智能报告生成页面
 */
const ReportPage = {
    _rendered: false,
    _reportId: null,
    _reportData: null,

    render() {
        const container = document.getElementById('page-report');
        if (!this._rendered) {
            container.innerHTML = this.template();
            this.bindEvents();
            this._rendered = true;
            this.loadHistory();
        }
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
                    <h3 class="card-title">历史报告</h3>
                </div>
                <div id="historyList">${Utils.inlineLoading('加载历史报告...')}</div>
            </div>
        `;
    },

    bindEvents() {
        document.getElementById('btnGenerate').addEventListener('click', () => this.startGenerate());
        document.getElementById('download-report-word').addEventListener('click', () => this.exportReport('docx'));
        document.getElementById('download-report-pdf').addEventListener('click', () => this.exportReport('pdf'));

        const historyList = document.getElementById('historyList');
        if (historyList) {
            historyList.addEventListener('click', event => {
                const deleteBtn = event.target.closest('[data-delete-task]');
                if (deleteBtn) {
                    this.deleteHistoryReport(deleteBtn.dataset.deleteTask);
                }
            });
        }

    },

    /* ============ 生成报告 ============ */
    async startGenerate() {
        const product = document.getElementById('reportProduct').value;
        const month = document.getElementById('reportMonth').value;
        const reportType = document.getElementById('reportType').value;

        if (!product || !month) {
            alert('请选择产品和月份');
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
        this._reportData = null;
        this.setDownloadState(false);

        // 清空报告预览区域
        const previewContent = document.getElementById('reportPreviewContent');
        previewContent.innerHTML = `
            <div style="text-align:center;padding:80px 0;color:var(--phc-ink-3)">
                <div class="spinner-small" style="width:32px;height:32px;border-width:3px;margin:0 auto"></div>
                <p data-report-progress style="margin-top:12px;font-size:14px">正在生成报告...</p>
            </div>
        `;

        try {
            const resp = await fetch(`/api/report/generate?${Utils.qs({ product, month, report_type: reportType })}`, {
                method: 'POST'
            });
            const data = await resp.json();
            if (!resp.ok || data.status === 'failed') {
                throw new Error(data.error || '报告生成失败');
            }
            this._reportId = data.task_id;
            this._reportData = data;
            if (data.status === 'completed') {
                this.onReportComplete(data);
            } else {
                await this.pollReportStatus(data.task_id);
            }
        } catch (e) {
            this.onReportFailed(e.message || '报告生成失败');
        }
    },

    async pollReportStatus(taskId) {
        const previewContent = document.getElementById('reportPreviewContent');
        const startedAt = Date.now();
        const maxWaitMs = 10 * 60 * 1000;
        for (;;) {
            await new Promise(resolve => setTimeout(resolve, 1200));
            if (Date.now() - startedAt >= maxWaitMs) {
                throw new Error('报告生成超时，请检查服务状态后重试');
            }
            const data = await Utils.api(`/api/report/${encodeURIComponent(taskId)}/status`, { timeoutMs: 15000 });
            this._reportData = data;
            if (data.status === 'completed') {
                this.onReportComplete(data);
                return;
            }
            if (data.status === 'failed') throw new Error(data.error || '报告生成失败');
            if (previewContent) previewContent.querySelector('p').textContent = data.status === 'queued' ? '报告已排队，正在等待处理...' : '正在生成报告，请稍候...';
        }
    },

    onReportComplete(data = this._reportData || {}) {
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
        previewContent.scrollTop = 0;
        previewContent.innerHTML = `
            <div style="margin-bottom:16px">
                <span class="status-pill success"><i data-lucide="check-circle" style="width:14px;height:14px"></i> 报告生成成功</span>
                <span style="margin-left:8px;font-size:12px;color:var(--phc-ink-3)">${safe(previewTitle)} · 产品：${safe(data.product || product)} · 月份：${safe(data.month || month)}</span>
            </div>
            ${sections.length ? sections.map(section => `<div class="report-section"><h4>${safe(section.title)}</h4><div class="report-markdown">${this._renderPreviewText(section.content)}</div></div>`).join('') : '<div class="empty-state"><p>报告已生成，但预览内容为空，请导出 Word 或 PDF 查看完整报告。</p></div>'}
            ${references.length ? `<div class="rag-citation report-rag-citation"><i data-lucide="book-open"></i><span>知识库来源：${references.map(safe).join('；')}</span></div>` : ''}
        `;
        this.setDownloadState(true);

        // 重新初始化 lucide 图标
        if (window.lucide) lucide.createIcons();
        this.loadHistory();
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
            const ordered = line.match(/^\d+[.)]\s+(.*)$/);
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

    setDownloadState(enabled) {
        ['download-report-word', 'download-report-pdf'].forEach(id => {
            const button = document.getElementById(id);
            if (button) button.disabled = !enabled;
        });
    },

    async exportReport(format) {
        try {
            await this.download(format);
        } catch (error) {
            if (error && error.name === 'AbortError') {
                return;
            }
            alert(error.message || '报告下载失败');
        }
    },

    async download(format) {
        if (!this._reportId) {
            throw new Error('请先生成报告');
        }

        const extension = format === 'pdf' ? 'pdf' : 'docx';
        const url = `/api/report/${encodeURIComponent(this._reportId)}/download?format=${format}`;
        const response = await fetch(url);

        if (!response.ok) {
            throw new Error('报告下载失败');
        }

        const blob = await response.blob();

        if (window.showSaveFilePicker) {
            const handle = await window.showSaveFilePicker({
                suggestedName: `成本分析报告_${this._reportId}.${extension}`,
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
        link.download = `成本分析报告_${this._reportId}.${extension}`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(blobUrl);
    },


    onReportFailed(message) {
        const btn = document.getElementById('btnGenerate');
        btn.disabled = false;
        btn.innerHTML = '<i data-lucide="sparkles" style="width:16px;height:16px"></i> 生成报告';

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
    async loadHistory() {
        const target = document.getElementById('historyList');
        if (!target) return;
        try {
            const data = await Utils.api('/api/report/history?limit=20');
            target.innerHTML = data.items && data.items.length
                ? this._renderHistoryTable(data.items)
                : Utils.emptyState('', '暂无历史报告');
            if (window.lucide) lucide.createIcons();
        } catch (e) {
            target.innerHTML = Utils.emptyState('', '历史报告加载失败');
        }
    },

    _renderHistoryTable(reports) {
        let html = `<table class="data-table"><thead><tr>
            <th>报告编号</th><th>产品</th><th>月份</th><th>状态</th><th>生成时间</th><th>操作</th>
        </tr></thead><tbody>`;
        reports.forEach(r => {
            const safe = value => Utils.escapeHtml(String(value ?? ''));
            const taskId = r.task_id || r.id;
            const encodedId = encodeURIComponent(taskId);
            html += `<tr>
                <td>${safe(taskId)}</td>
                <td>${safe(r.product)}</td>
                <td>${safe(r.month)}</td>
                <td><span class="badge badge-success">已完成</span></td>
                <td>${safe(r.created_at || r.time)}</td>
                <td>
                    <a href="/api/report/${encodedId}/download?format=docx" class="btn btn-sm btn-outline" download>Word</a>
                    <a href="/api/report/${encodedId}/download?format=pdf" class="btn btn-sm btn-outline" download>PDF</a>
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
        const confirmed = window.confirm('删除后将同时移除对应的 Word 和 PDF 文件，是否继续？');
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
            await this.loadHistory();
        } catch (error) {
            alert(error.message || '删除历史报告失败');
        }
    },

    resetCurrentReport() {
        this._reportId = null;
        this._reportData = null;
        this.setDownloadState(false);

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
    }
};
