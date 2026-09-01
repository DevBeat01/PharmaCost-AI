/* Runtime settings dialog: managed imports never overwrite contest source files. */
(function () {
    'use strict';

    const Settings = {
        dialog: null,
        summary: null,

        init() {
            this.dialog = document.getElementById('settingsDialog');
            document.getElementById('settingsButton')?.addEventListener('click', () => this.open());
            document.getElementById('settingsClose')?.addEventListener('click', () => this.close());
            this.dialog?.addEventListener('click', event => {
                if (event.target === this.dialog) this.close();
            });
            document.addEventListener('keydown', event => {
                if (event.key === 'Escape' && this.dialog && !this.dialog.hidden) this.close();
            });
            document.getElementById('settingsReloadData')?.addEventListener('click', () => this.action('/api/settings/reload-data', '成本数据已重新加载'));
            document.getElementById('settingsRebuildKnowledge')?.addEventListener('click', () => this.action('/api/settings/rebuild-knowledge', '知识库重建任务已启动'));
            document.getElementById('settingsKnowledgeUpload')?.addEventListener('change', event => this.upload('/api/settings/knowledge', event.target));
            document.getElementById('settingsTemplateUpload')?.addEventListener('change', event => this.upload('/api/settings/template', event.target));
            this.dialog?.addEventListener('change', event => {
                const input = event.target;
                if (input.matches('[data-data-upload]')) this.upload(`/api/settings/data/${encodeURIComponent(input.dataset.dataUpload)}`, input);
            });
            this.dialog?.addEventListener('click', event => this.handleClick(event));
        },

        async open() {
            this.dialog.hidden = false;
            document.body.style.overflow = 'hidden';
            this.message('正在读取系统设置...');
            await this.refresh();
            document.getElementById('settingsClose')?.focus();
        },

        close() {
            this.dialog.hidden = true;
            document.body.style.overflow = '';
        },

        async refresh() {
            try {
                const data = await Utils.api('/api/settings/summary', { timeoutMs: 20000 });
                this.render(data);
                this.message('');
            } catch (error) {
                this.message(`无法读取设置：${error.message || '请求失败'}`, 'error');
            }
        },

        render(data) {
            this.summary = data;
            const system = data.system || {};
            const rag = system.rag || {};
            const status = document.getElementById('settingsSystemStatus');
            if (status) {
                status.innerHTML = [
                    ['database', system.data_loaded ? '成本数据已加载' : '成本数据待加载'],
                    ['brain-circuit', rag.embedding_model_ready ? `知识库就绪 · ${rag.index_chunks || 0} 个片段` : '知识库嵌入模型未就绪'],
                    ['message-square-text', system.text_model || '文本模型未配置'],
                ].map(([icon, text]) => `<span class="settings-status-item"><i data-lucide="${icon}"></i>${Utils.escapeHtml(text)}</span>`).join('');
            }
            this.renderDataFiles(data.data_files || []);
            this.renderKnowledgeFiles(data.knowledge_files || []);
            this.renderTemplate(data.template || null);
            refreshIcons();
        },

        renderDataFiles(files) {
            const target = document.getElementById('settingsDataFiles');
            if (!target) return;
            target.innerHTML = files.map(file => this.fileRow(file, `
                <label class="settings-action-button settings-upload-button" title="导入 CSV 覆盖文件">
                  <i data-lucide="upload"></i>导入<input data-data-upload="${Utils.escapeHtml(file.key)}" type="file" accept=".csv" hidden>
                </label>
                ${file.deletable ? this.deleteButton('data', file.key, `删除导入的“${file.label}”并恢复默认数据？`) : ''}
            `)).join('') || this.empty('暂无数据文件');
        },

        renderKnowledgeFiles(files) {
            const target = document.getElementById('settingsKnowledgeFiles');
            if (!target) return;
            target.innerHTML = files.map(file => this.fileRow(file, file.deletable
                ? this.deleteButton('knowledge', file.name, `删除已导入知识文档“${file.name}”？`) : '')).join('') || this.empty('暂无知识文档');
        },

        renderTemplate(file) {
            const target = document.getElementById('settingsTemplateFile');
            if (!target) return;
            target.innerHTML = file ? this.fileRow(file, file.deletable
                ? this.deleteButton('template', '', '删除自定义报告模板并恢复默认模板？') : '') : this.empty('未找到报告模板');
        },

        fileRow(file, actions = '') {
            const state = file.imported ? '<span class="settings-file-badge imported">已导入</span>' : '<span class="settings-file-badge">默认</span>';
            return `<div class="settings-file-row">
              <span class="settings-file-icon"><i data-lucide="file-text"></i></span>
              <div class="settings-file-meta"><div class="settings-file-name" title="${Utils.escapeHtml(file.name)}">${Utils.escapeHtml(file.label || file.name)}</div>
                <div class="settings-file-detail">${Utils.escapeHtml(file.name)} · ${this.size(file.size)} · ${Utils.escapeHtml(file.updated_at || '')}</div></div>
              ${state}${actions}
            </div>`;
        },

        deleteButton(type, value, confirmText) {
            return `<button class="settings-delete-button" type="button" title="删除导入文件" aria-label="删除导入文件" data-delete-type="${type}" data-delete-value="${Utils.escapeHtml(value)}" data-confirm="${Utils.escapeHtml(confirmText)}"><i data-lucide="trash-2"></i></button>`;
        },

        empty(text) {
            return `<div class="settings-file-row"><span class="settings-file-detail">${Utils.escapeHtml(text)}</span></div>`;
        },

        size(bytes) {
            const value = Number(bytes || 0);
            return value >= 1024 * 1024 ? `${(value / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(value / 1024))} KB`;
        },

        async handleClick(event) {
            const button = event.target.closest('[data-delete-type]');
            if (!button) return;
            if (!window.confirm(button.dataset.confirm || '确认删除该导入文件？')) return;
            const type = button.dataset.deleteType;
            const value = button.dataset.deleteValue;
            const endpoint = type === 'template' ? '/api/settings/template' : `/api/settings/${type}/${encodeURIComponent(value)}`;
            await this.action(endpoint, '文件已删除并恢复默认配置', 'DELETE');
        },

        async upload(endpoint, input) {
            const file = input.files?.[0];
            if (!file) return;
            const form = new FormData();
            form.append('file', file);
            this.message(`正在导入 ${file.name}...`);
            try {
                const response = await fetch(endpoint, { method: 'POST', body: form });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || data.message || `HTTP ${response.status}`);
                this.render(data.summary || await Utils.api('/api/settings/summary'));
                this.message(data.message || '文件导入成功', 'success');
                if (endpoint.includes('/data/')) await this.refreshApplicationData();
            } catch (error) {
                this.message(`导入失败：${error.message || '请求失败'}`, 'error');
            } finally {
                input.value = '';
            }
        },

        async action(endpoint, fallbackMessage, method = 'POST') {
            this.message('正在处理...');
            try {
                const response = await fetch(endpoint, { method });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || data.message || `HTTP ${response.status}`);
                this.render(data.summary || await Utils.api('/api/settings/summary'));
                this.message(data.message || fallbackMessage, 'success');
                if (endpoint.includes('data')) await this.refreshApplicationData();
            } catch (error) {
                this.message(`操作失败：${error.message || '请求失败'}`, 'error');
            }
        },

        async refreshApplicationData() {
            await Selectors.loadProducts();
            Router.renderPage(AppState.currentPage);
        },

        message(text, type = '') {
            const target = document.getElementById('settingsMessage');
            if (!target) return;
            target.textContent = text;
            target.className = `settings-dialog-footer ${type}`;
        },
    };

    window.Settings = Settings;
    document.addEventListener('DOMContentLoaded', () => Settings.init());
})();
