/* Runtime settings dialog: managed imports never overwrite contest source files. */
(function () {
    'use strict';

    const Settings = {
        dialog: null,
        summary: null,
        resources: [],

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
            document.getElementById('settingsSaveModels')?.addEventListener('click', () => this.saveModels());
            document.getElementById('settingsResetModels')?.addEventListener('click', () => this.resetModels());
            document.getElementById('settingsRefreshResources')?.addEventListener('click', () => this.refreshResources());
            this.bindModelPresets();
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
                const [data, resourceData] = await Promise.all([
                    Utils.api('/api/settings/summary', { timeoutMs: 20000 }),
                    Utils.api('/api/settings/resources', { timeoutMs: 20000 }),
                ]);
                this.resources = resourceData.resources || [];
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
            this.renderModels(data.models || {});
            this.renderKnowledgeFiles(data.knowledge_files || []);
            this.renderTemplate(data.template || null);
            this.renderResources(this.resources);
            refreshIcons();
        },

        async refreshResources() {
            const button = document.getElementById('settingsRefreshResources');
            if (button) button.disabled = true;
            try {
                const data = await Utils.api('/api/settings/resources', { timeoutMs: 20000 });
                this.resources = data.resources || [];
                this.renderResources(this.resources);
                refreshIcons();
                this.message('资源版本已刷新', 'success');
            } catch (error) {
                this.message(`无法读取资源版本：${error.message || '请求失败'}`, 'error');
            } finally {
                if (button) button.disabled = false;
            }
        },

        modelPresets: {
            deepseek: { label: 'DeepSeek', url: 'https://api.deepseek.com', model: 'deepseek-chat' },
            mimo: { label: 'MiMo', url: 'https://api.xiaomimimo.com/v1', model: 'mimo-v2.5' },
            qwen: { label: '通义千问', url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus' },
            zhipu: { label: '智谱 GLM', url: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4-flash' },
            kimi: { label: 'Kimi', url: 'https://api.moonshot.cn/v1', model: 'moonshot-v1-8k' },
        },

        bindModelPresets() {
            ['Deepseek', 'Mimo'].forEach(slot => document.getElementById(`settings${slot}Preset`)?.addEventListener('change', event => {
                const preset = this.modelPresets[event.target.value]; if (!preset) return;
                document.getElementById(`settings${slot}Label`).value = preset.label;
                document.getElementById(`settings${slot}Url`).value = preset.url;
                document.getElementById(`settings${slot}Model`).value = preset.model;
            }));
        },

        renderModels(models) {
            const set = (id, value) => { const el = document.getElementById(id); if (el) el.value = value ?? ''; };
            const deepseek = models.deepseek || {}, mimo = models.mimo || {};
            set('settingsDeepseekLabel', deepseek.provider_label || 'DeepSeek'); set('settingsDeepseekUrl', deepseek.base_url); set('settingsDeepseekModel', deepseek.model);
            set('settingsMimoLabel', mimo.provider_label || 'MiMo'); set('settingsMimoUrl', mimo.base_url); set('settingsMimoModel', mimo.model);
            const ssl1 = document.getElementById('settingsDeepseekSsl'); if (ssl1) ssl1.checked = deepseek.verify_ssl !== false;
            const ssl2 = document.getElementById('settingsMimoSsl'); if (ssl2) ssl2.checked = mimo.verify_ssl !== false;
            const state1 = document.getElementById('settingsDeepseekKeyState'); if (state1) state1.textContent = deepseek.configured ? `当前：${deepseek.api_key}` : '当前未配置';
            const state2 = document.getElementById('settingsMimoKeyState'); if (state2) state2.textContent = mimo.configured ? `当前：${mimo.api_key}` : '当前未配置';
            set('settingsDeepseekKey', ''); set('settingsMimoKey', '');
        },

        async saveModels() {
            const get = id => document.getElementById(id);
            const payload = {
                deepseek_provider_label: get('settingsDeepseekLabel')?.value, deepseek_api_key: get('settingsDeepseekKey')?.value || '', deepseek_base_url: get('settingsDeepseekUrl')?.value, deepseek_model: get('settingsDeepseekModel')?.value, deepseek_verify_ssl: Boolean(get('settingsDeepseekSsl')?.checked),
                mimo_provider_label: get('settingsMimoLabel')?.value, mimo_api_key: get('settingsMimoKey')?.value || '', mimo_base_url: get('settingsMimoUrl')?.value, mimo_model: get('settingsMimoModel')?.value, mimo_verify_ssl: Boolean(get('settingsMimoSsl')?.checked) };
            await this.action('/api/settings/models', '模型配置已保存并生效', 'PUT', { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        },

        async resetModels() {
            if (!window.confirm('恢复后将删除系统设置中的模型覆盖配置，改用 app/.env。是否继续？')) return;
            await this.action('/api/settings/models', '模型配置已恢复为环境变量设置', { method: 'DELETE' });
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

        renderResources(resources) {
            const target = document.getElementById('settingsResourceList');
            if (!target) return;
            if (!resources.length) {
                target.innerHTML = this.empty('暂无资源版本，导入数据、知识文档或报告模板后将在这里显示历史');
                return;
            }
            const typeLabels = { data: '成本数据', knowledge: '知识文档', template: '报告模板' };
            const groups = new Map();
            resources.forEach(resource => {
                const groupKey = `${resource.resource_type}:${resource.logical_key}`;
                if (!groups.has(groupKey)) groups.set(groupKey, []);
                groups.get(groupKey).push(resource);
            });
            target.innerHTML = Array.from(groups.entries()).map(([groupKey, versions]) => {
                const [resourceType, logicalKey] = groupKey.split(':');
                const title = resourceType === 'data'
                    ? (this.summary?.data_files?.find(file => file.key === logicalKey)?.label || logicalKey)
                    : resourceType === 'template' ? '默认报告模板' : `知识文档：${logicalKey}`;
                return `<div class="settings-resource-group">
                  <div class="settings-resource-group-title"><span>${Utils.escapeHtml(typeLabels[resourceType] || resourceType)}</span><strong>${Utils.escapeHtml(title)}</strong></div>
                  <div class="settings-resource-versions">${versions.map(resource => this.resourceRow(resource)).join('')}</div>
                </div>`;
            }).join('');
        },

        resourceRow(resource) {
            const active = resource.status === 'active';
            const status = active ? '<span class="settings-resource-status active">当前使用</span>' : '<span class="settings-resource-status">历史版本</span>';
            const date = resource.published_at || resource.created_at || '';
            const hash = resource.sha256 ? `${resource.sha256.slice(0, 10)}...` : '';
            const validation = resource.validation?.valid === false ? '校验失败' : '已校验';
            const rollback = active ? '' : `<button class="settings-resource-button" type="button" title="将此版本恢复为当前版本" data-resource-action="rollback" data-resource-id="${Utils.escapeHtml(resource.resource_id)}"><i data-lucide="rotate-ccw"></i>回滚</button>`;
            const remove = active ? '' : `<button class="settings-resource-button danger" type="button" title="删除历史版本" data-resource-action="delete" data-resource-id="${Utils.escapeHtml(resource.resource_id)}"><i data-lucide="trash-2"></i>删除</button>`;
            return `<div class="settings-resource-row">
              <div class="settings-resource-version"><strong>v${resource.version}</strong>${status}</div>
              <div class="settings-resource-meta"><span>${Utils.escapeHtml(resource.filename || '')}</span><small>${Utils.escapeHtml(date)} · ${Utils.escapeHtml(hash)} · ${validation}</small></div>
              <div class="settings-resource-actions">${rollback}${remove}</div>
            </div>`;
        },

        fileRow(file, actions = '') {
            const state = file.imported ? '<span class="settings-file-badge imported">已导入</span>' : '<span class="settings-file-badge">默认</span>';
            const version = file.version ? ` · 版本 v${file.version}` : '';
            return `<div class="settings-file-row">
              <span class="settings-file-icon"><i data-lucide="file-text"></i></span>
              <div class="settings-file-meta"><div class="settings-file-name" title="${Utils.escapeHtml(file.name)}">${Utils.escapeHtml(file.label || file.name)}</div>
                <div class="settings-file-detail">${Utils.escapeHtml(file.name)} · ${this.size(file.size)} · ${Utils.escapeHtml(file.updated_at || '')}${version}</div></div>
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
            const resourceButton = event.target.closest('[data-resource-action]');
            if (resourceButton) {
                const action = resourceButton.dataset.resourceAction;
                const id = resourceButton.dataset.resourceId;
                const prompt = action === 'rollback' ? '确认将此历史版本恢复为当前版本？' : '确认删除此历史资源版本？删除后不可恢复。';
                if (!window.confirm(prompt)) return;
                const endpoint = `/api/settings/resources/${encodeURIComponent(id)}/${action}`;
                await this.action(endpoint, action === 'rollback' ? '资源已回滚' : '历史版本已删除', action === 'rollback' ? 'POST' : 'DELETE');
                return;
            }
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
                await this.refreshResourceListSilently();
                this.message(data.message || '文件导入成功', 'success');
                if (endpoint.includes('/data/')) await this.refreshApplicationData();
            } catch (error) {
                this.message(`导入失败：${error.message || '请求失败'}`, 'error');
            } finally {
                input.value = '';
            }
        },

        async action(endpoint, fallbackMessage, method = 'POST', requestOptions = {}) {
            if (method && typeof method === 'object') {
                requestOptions = method;
                method = requestOptions.method || 'POST';
            }
            this.message('正在处理...');
            try {
                const response = await fetch(endpoint, { ...requestOptions, method });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || data.message || `HTTP ${response.status}`);
                this.render(data.summary || await Utils.api('/api/settings/summary'));
                await this.refreshResourceListSilently();
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

        async refreshResourceListSilently() {
            try {
                const data = await Utils.api('/api/settings/resources', { timeoutMs: 20000 });
                this.resources = data.resources || [];
                this.renderResources(this.resources);
                refreshIcons();
            } catch (error) {
                console.warn('资源版本刷新失败', error);
            }
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
