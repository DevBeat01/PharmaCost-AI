/* Runtime settings dialog: managed imports never overwrite contest source files. */
(function () {
    'use strict';

    const Settings = {
        dialog: null,
        summary: null,
        resources: [],
        knowledgePollTimer: null,
        messageTimer: null,
        watchedKnowledgeTaskId: null,

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
            document.getElementById('settingsSaveModels')?.addEventListener('click', () => this.saveModels());
            document.getElementById('settingsResetModels')?.addEventListener('click', () => this.resetModels());
            document.getElementById('settingsRefreshResources')?.addEventListener('click', () => this.refreshResources());
            this.bindModelPresets();
            document.getElementById('settingsKnowledgeUpload')?.addEventListener('change', event => this.upload('/api/settings/knowledge', event.target));
            document.getElementById('settingsTemplateUpload')?.addEventListener('change', event => this.uploadTemplate(event.target, 'add'));
            this.dialog?.addEventListener('change', event => {
                const input = event.target;
                if (input.matches('[data-data-upload]')) this.upload(`/api/settings/data/${encodeURIComponent(input.dataset.dataUpload)}?mode=${encodeURIComponent(input.dataset.uploadMode || 'replace')}`, input);
                if (input.matches('[data-template-replace]')) this.uploadTemplate(input, 'replace');
            });
            this.dialog?.addEventListener('click', event => this.handleClick(event));
        },

        async open() {
            this.dialog.hidden = false;
            document.body.style.overflow = 'hidden';
            this.message('正在读取系统设置...');
            await this.refresh();
            this.startKnowledgePolling();
            document.getElementById('settingsClose')?.focus();
        },

        close() {
            this.dialog.hidden = true;
            document.body.style.overflow = '';
            this.stopKnowledgePolling();
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
            const build = rag.build || {};
            const status = document.getElementById('settingsSystemStatus');
            if (status) {
                const buildStatus = build.status || 'idle';
                const ragText = buildStatus === 'queued' ? '知识库正在排队'
                    : buildStatus === 'running' ? '知识库正在构建'
                    : buildStatus === 'failed' ? '知识库重构失败，继续使用上一版'
                    : rag.embedding_model_ready ? `知识库已就绪 · ${rag.index_chunks || 0} 个片段`
                    : '知识库嵌入模型未就绪';
                const ragClass = ['queued', 'running'].includes(buildStatus) ? 'running' : buildStatus === 'failed' ? 'failed' : '';
                status.innerHTML = [
                    ['database', system.data_loaded ? '成本数据已加载' : '成本数据待加载', ''],
                    [['queued', 'running'].includes(buildStatus) ? 'loader-circle' : buildStatus === 'failed' ? 'circle-alert' : 'brain-circuit', ragText, ragClass],
                    ['message-square-text', system.text_model || '文本模型未配置', ''],
                ].map(([icon, text, className]) => `<span class="settings-status-item ${className}"><i data-lucide="${icon}"></i>${Utils.escapeHtml(text)}</span>`).join('');
            }
            this.renderDataFiles(data.data_files || []);
            this.renderModels(data.models || {});
            this.renderKnowledgeFiles(data.knowledge_files || []);
            this.renderKnowledgeBuild(build, rag);
            this.renderTemplate(data.templates || [], data.template || null);
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
                this.markThinkingPending(slot);
            }));
            ['Deepseek', 'Mimo'].forEach(slot => {
                ['Label', 'Url', 'Model'].forEach(field => document.getElementById(`settings${slot}${field}`)?.addEventListener('input', () => this.markThinkingPending(slot)));
            });
        },

        markThinkingPending(slot) {
            const input = document.getElementById(`settings${slot}Thinking`);
            const hint = document.getElementById(`settings${slot}ThinkingHint`);
            if (input) { input.disabled = true; input.dataset.capability = 'unknown'; }
            if (hint) hint.textContent = '模型信息已变化，保存后自动检测能力';
        },

        renderModels(models) {
            const set = (id, value) => { const el = document.getElementById(id); if (el) el.value = value ?? ''; };
            const deepseek = models.deepseek || {}, mimo = models.mimo || {};
            set('settingsDeepseekLabel', deepseek.provider_label || 'DeepSeek'); set('settingsDeepseekUrl', deepseek.base_url); set('settingsDeepseekModel', deepseek.model);
            set('settingsMimoLabel', mimo.provider_label || 'MiMo'); set('settingsMimoUrl', mimo.base_url); set('settingsMimoModel', mimo.model);
            const ssl1 = document.getElementById('settingsDeepseekSsl'); if (ssl1) ssl1.checked = deepseek.verify_ssl !== false;
            const ssl2 = document.getElementById('settingsMimoSsl'); if (ssl2) ssl2.checked = mimo.verify_ssl !== false;
            this.renderThinking('Deepseek', deepseek);
            this.renderThinking('Mimo', mimo);
            const state1 = document.getElementById('settingsDeepseekKeyState'); if (state1) state1.textContent = deepseek.configured ? `当前：${deepseek.api_key}` : '当前未配置';
            const state2 = document.getElementById('settingsMimoKeyState'); if (state2) state2.textContent = mimo.configured ? `当前：${mimo.api_key}` : '当前未配置';
            set('settingsDeepseekKey', ''); set('settingsMimoKey', '');
        },

        renderThinking(slot, model) {
            const input = document.getElementById(`settings${slot}Thinking`);
            const hint = document.getElementById(`settings${slot}ThinkingHint`);
            const capability = model.thinking_capability || 'unknown';
            if (input) {
                input.checked = Boolean(model.thinking_enabled);
                input.disabled = capability !== 'configurable';
                input.dataset.capability = capability;
            }
            if (hint) hint.textContent = model.thinking_hint || '能力尚未检测';
        },

        async saveModels() {
            const get = id => document.getElementById(id);
            const payload = {
                deepseek_provider_label: get('settingsDeepseekLabel')?.value, deepseek_api_key: get('settingsDeepseekKey')?.value || '', deepseek_base_url: get('settingsDeepseekUrl')?.value, deepseek_model: get('settingsDeepseekModel')?.value, deepseek_verify_ssl: Boolean(get('settingsDeepseekSsl')?.checked), deepseek_thinking_enabled: Boolean(get('settingsDeepseekThinking')?.checked),
                mimo_provider_label: get('settingsMimoLabel')?.value, mimo_api_key: get('settingsMimoKey')?.value || '', mimo_base_url: get('settingsMimoUrl')?.value, mimo_model: get('settingsMimoModel')?.value, mimo_verify_ssl: Boolean(get('settingsMimoSsl')?.checked), mimo_thinking_enabled: Boolean(get('settingsMimoThinking')?.checked) };
            await this.action('/api/settings/models', '模型配置已保存并生效', 'PUT', { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        },

        async resetModels() {
            const confirmed = await this.confirm({
                title: '恢复模型默认配置',
                message: '恢复后将删除系统设置中的模型覆盖配置，改用 app/.env 中的配置。',
                detail: '当前模型接口、模型名称和证书设置可能发生变化。',
                confirmText: '恢复默认',
                icon: 'rotate-ccw',
            });
            if (!confirmed) return;
            await this.action('/api/settings/models', '模型配置已恢复为环境变量设置', { method: 'DELETE' });
        },

        renderDataFiles(files) {
            const target = document.getElementById('settingsDataFiles');
            if (!target) return;
            target.innerHTML = files.map(file => this.fileRow(file, `
                <label class="settings-action-button settings-upload-button" title="替换当前 CSV">
                  <i data-lucide="replace"></i>替换<input data-data-upload="${Utils.escapeHtml(file.key)}" data-upload-mode="replace" type="file" accept=".csv" hidden>
                </label>
                <label class="settings-action-button settings-upload-button" title="新增并合并 CSV">
                  <i data-lucide="plus"></i>新增<input data-data-upload="${Utils.escapeHtml(file.key)}" data-upload-mode="append" type="file" accept=".csv" hidden>
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

        renderKnowledgeBuild(build = {}, index = {}) {
            const target = document.getElementById('settingsKnowledgeBuildStatus');
            if (!target) return;
            const status = build.status || 'idle';
            const chunks = Number(build.chunk_count || index.index_chunks || 0);
            if (['queued', 'running'].includes(status)) {
                this.watchedKnowledgeTaskId = build.task_id || this.watchedKnowledgeTaskId;
                target.className = 'settings-knowledge-build running';
                target.innerHTML = `<i data-lucide="loader-circle"></i><span>${status === 'queued' ? '文档变更已保存，知识库正在排队…' : '知识库正在解析文档并更新索引…'}${build.pending ? '（检测到新变更，完成后将再次更新）' : ''}</span>`;
            } else if (status === 'failed') {
                target.className = 'settings-knowledge-build failed';
                target.innerHTML = `<i data-lucide="circle-alert"></i><span>重构失败，继续使用上一版知识库${build.error ? `：${Utils.escapeHtml(build.error)}` : ''}</span><button class="settings-action-button settings-knowledge-retry" type="button" data-knowledge-retry><i data-lucide="refresh-cw"></i>重试</button>`;
            } else if (status === 'completed') {
                target.className = 'settings-knowledge-build';
                target.innerHTML = `<i data-lucide="circle-check"></i><span>知识库已就绪 · ${chunks} 个片段</span>`;
            } else {
                target.className = 'settings-knowledge-build';
                target.innerHTML = '<span>新增或删除知识文档后，系统会自动更新知识库。</span>';
            }
        },

        renderTemplate(templates, current) {
            const target = document.getElementById('settingsTemplateFile');
            if (!target) return;
            target.innerHTML = (templates || []).map(template => {
                const file = { name: template.filename || template.name, label: template.name, size: 0, updated_at: template.published_at || '', imported: !template.is_default, version: template.version, deletable: !template.is_default };
                const replace = `<label class="settings-action-button settings-upload-button" title="替换此模板"><i data-lucide="replace"></i>替换<input data-template-replace="${Utils.escapeHtml(template.resource_id || '')}" data-template-name="${Utils.escapeHtml(template.name)}" data-template-type="${Utils.escapeHtml(template.report_type)}" type="file" accept=".docx" hidden></label>`;
                const remove = template.is_default ? (template.resource_id ? this.deleteButton('template', '', '删除自定义报告模板并恢复默认模板？') : '') : this.deleteButton('template-id', template.resource_id, `删除模板“${template.name}”？`);
                return this.fileRow(file, `${replace}${remove}`);
            }).join('') || this.empty('未找到报告模板');
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
                    : resourceType === 'template'
                        ? (this.summary?.templates?.find(item => item.logical_key === logicalKey)?.name || logicalKey)
                        : `知识文档：${logicalKey}`;
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
            if (event.target.closest('[data-knowledge-retry]')) {
                await this.retryKnowledgeBuild();
                return;
            }
            const button = event.target.closest('[data-delete-type]');
            const resourceButton = event.target.closest('[data-resource-action]');
            if (resourceButton) {
                const action = resourceButton.dataset.resourceAction;
                const id = resourceButton.dataset.resourceId;
                const confirmed = await this.confirm(action === 'rollback' ? {
                    title: '回滚资源版本',
                    message: '确认将此历史版本恢复为当前使用版本？',
                    detail: '当前版本会保留在历史记录中，可以再次回滚。',
                    confirmText: '确认回滚',
                    icon: 'rotate-ccw',
                } : {
                    title: '删除历史资源版本',
                    message: '确认删除此历史资源版本？',
                    detail: '删除后不可恢复，当前使用版本不会受到影响。',
                    confirmText: '确认删除',
                    danger: true,
                    icon: 'trash-2',
                });
                if (!confirmed) return;
                // Rollback is an action endpoint, while deletion is the
                // resource endpoint itself: DELETE /resources/{resource_id}.
                const endpoint = action === 'rollback'
                    ? `/api/settings/resources/${encodeURIComponent(id)}/rollback`
                    : `/api/settings/resources/${encodeURIComponent(id)}`;
                await this.action(endpoint, action === 'rollback' ? '资源已回滚' : '历史版本已删除', action === 'rollback' ? 'POST' : 'DELETE');
                return;
            }
            if (!button) return;
            const type = button.dataset.deleteType;
            const isRestoreDefault = type === 'data' || type === 'template';
            const confirmed = await this.confirm({
                title: isRestoreDefault ? '恢复默认配置' : '删除导入文件',
                message: button.dataset.confirm || '确认删除该导入文件？',
                detail: isRestoreDefault ? '当前导入文件将移除，系统会恢复使用默认文件。' : '删除后不可恢复。',
                confirmText: isRestoreDefault ? '恢复默认' : '确认删除',
                danger: true,
                icon: 'trash-2',
            });
            if (!confirmed) return;
            const value = button.dataset.deleteValue;
            const endpoint = type === 'template-id' ? `/api/settings/template/${encodeURIComponent(value)}` : type === 'template' ? '/api/settings/template' : `/api/settings/${type}/${encodeURIComponent(value)}`;
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
                if (endpoint.includes('/settings/knowledge')) {
                    this.watchedKnowledgeTaskId = data.knowledge_build?.task_id || null;
                    this.message('文档已保存，知识库正在更新…');
                    this.startKnowledgePolling();
                } else {
                    this.message(data.message || '文件导入成功', 'success');
                }
                if (endpoint.includes('/data/')) await this.refreshApplicationData();
            } catch (error) {
                this.message(`导入失败：${error.message || '请求失败'}`, 'error');
            } finally {
                input.value = '';
            }
        },

        async uploadTemplate(input, mode) {
            const file = input.files?.[0];
            if (!file) return;
            const defaultName = input.dataset.templateName || file.name.replace(/\.docx$/i, '');
            const formValue = await this.prompt({
                title: mode === 'add' ? '新增报告模板' : '替换报告模板',
                message: '设置模板名称和报告类型。',
                fields: [{ name: 'display_name', label: '模板名称', value: defaultName, required: true }, ...(input.dataset.templateType ? [] : [{ name: 'report_type', label: '报告类型', value: 'monthly', options: [['monthly', '月度报告'], ['quarterly', '季度报告'], ['topic', '专题报告']] }])],
                confirmText: mode === 'add' ? '新增模板' : '替换模板',
            });
            if (!formValue) { input.value = ''; return; }
            const name = formValue.display_name;
            if (!name) { input.value = ''; return; }
            const reportType = input.dataset.templateType || formValue.report_type || 'monthly';
            if (!['monthly', 'quarterly', 'topic'].includes(reportType)) {
                this.message('模板操作失败：报告类型必须是 monthly、quarterly 或 topic', 'error');
                input.value = '';
                return;
            }
            const form = new FormData();
            form.append('file', file);
            let endpoint = `/api/settings/template?mode=${mode}&display_name=${encodeURIComponent(name)}&report_type=${encodeURIComponent(reportType)}`;
            if (input.dataset.templateReplace) endpoint += `&template_id=${encodeURIComponent(input.dataset.templateReplace)}`;
            this.message(`正在处理 ${file.name}...`);
            try {
                const response = await fetch(endpoint, { method: 'POST', body: form });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || data.message || `HTTP ${response.status}`);
                this.render(data.summary || await Utils.api('/api/settings/summary'));
                await this.refreshResourceListSilently();
                this.message(data.message || '模板操作成功', 'success');
            } catch (error) {
                this.message(`模板操作失败：${error.message || '请求失败'}`, 'error');
            } finally { input.value = ''; }
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
                if (endpoint.includes('/settings/knowledge/') || endpoint.includes('/rebuild-knowledge')) {
                    this.watchedKnowledgeTaskId = data.knowledge_build?.task_id || null;
                    this.message(endpoint.includes('/rebuild-knowledge') ? '知识库正在重新构建…' : '文档已删除，知识库正在更新…');
                    this.startKnowledgePolling();
                } else {
                    this.message(data.message || fallbackMessage, 'success');
                }
                if (endpoint.includes('data')) await this.refreshApplicationData();
            } catch (error) {
                this.message(`操作失败：${error.message || '请求失败'}`, 'error');
            }
        },

        async refreshApplicationData() {
            if (typeof DashboardPage !== 'undefined') DashboardPage.clearAttributionCache();
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

        startKnowledgePolling() {
            if (this.knowledgePollTimer || !this.dialog || this.dialog.hidden) return;
            this.pollKnowledgeStatus();
            this.knowledgePollTimer = window.setInterval(() => this.pollKnowledgeStatus(), 2000);
        },

        stopKnowledgePolling() {
            if (this.knowledgePollTimer) window.clearInterval(this.knowledgePollTimer);
            this.knowledgePollTimer = null;
        },

        async pollKnowledgeStatus() {
            if (!this.dialog || this.dialog.hidden) return;
            try {
                const data = await Utils.api('/api/settings/knowledge/status', { timeoutMs: 10000 });
                const build = data.knowledge_build || {};
                const index = data.index || {};
                if (this.summary?.system?.rag) {
                    Object.assign(this.summary.system.rag, index, { build });
                }
                this.renderKnowledgeBuild(build, index);
                const currentStatus = build.status || 'idle';
                const statusContainer = document.getElementById('settingsSystemStatus');
                if (statusContainer && this.summary) this.render(this.summary);
                if (currentStatus === 'completed' && this.watchedKnowledgeTaskId === build.task_id) {
                    this.watchedKnowledgeTaskId = null;
                    this.message(`知识库重构完成 · ${Number(build.chunk_count || index.index_chunks || 0)} 个片段`, 'success');
                } else if (currentStatus === 'failed' && this.watchedKnowledgeTaskId === build.task_id) {
                    this.watchedKnowledgeTaskId = null;
                    this.message(`知识库重构失败：${build.error || '未知错误'}，当前继续使用上一版索引`, 'error');
                }
            } catch (error) {
                console.warn('知识库状态读取失败', error);
            }
        },

        async retryKnowledgeBuild() {
            await this.action('/api/settings/rebuild-knowledge?trigger=retry', '知识库重建任务已启动');
        },

        confirm(options = {}) {
            return new Promise(resolve => {
                const dialog = this.createModal({
                    ...options,
                    role: 'alertdialog',
                    fields: [],
                });
                const finish = value => {
                    document.removeEventListener('keydown', onKey);
                    dialog.remove();
                    resolve(value);
                };
                const onKey = event => {
                    if (event.key === 'Escape') finish(false);
                    if (event.key === 'Enter' && !event.target.matches('button')) finish(true);
                };
                document.addEventListener('keydown', onKey);
                dialog.querySelector('[data-settings-cancel]').addEventListener('click', () => finish(false));
                dialog.querySelector('[data-settings-confirm]').addEventListener('click', () => finish(true));
                dialog.addEventListener('click', event => { if (event.target === dialog) finish(false); });
                dialog.querySelector('[data-settings-cancel]').focus();
            });
        },

        prompt(options = {}) {
            return new Promise(resolve => {
                const dialog = this.createModal({ ...options, role: 'dialog' });
                const finish = value => {
                    document.removeEventListener('keydown', onKey);
                    dialog.remove();
                    resolve(value);
                };
                const onKey = event => { if (event.key === 'Escape') finish(null); };
                document.addEventListener('keydown', onKey);
                dialog.querySelector('[data-settings-cancel]').addEventListener('click', () => finish(null));
                dialog.querySelector('[data-settings-confirm]').addEventListener('click', () => {
                    const values = {};
                    let valid = true;
                    dialog.querySelectorAll('[data-settings-field]').forEach(field => {
                        values[field.name] = field.value.trim();
                        if (field.required && !values[field.name]) {
                            field.setAttribute('aria-invalid', 'true');
                            valid = false;
                        } else field.removeAttribute('aria-invalid');
                    });
                    if (valid) finish(values);
                });
                dialog.addEventListener('click', event => { if (event.target === dialog) finish(null); });
                dialog.querySelector('[data-settings-field]')?.focus();
            });
        },

        createModal(options = {}) {
            document.getElementById('settingsActionDialog')?.remove();
            const esc = value => Utils.escapeHtml(value ?? '');
            const fields = (options.fields || []).map(field => {
                const control = field.options
                    ? `<select data-settings-field name="${esc(field.name)}"${field.required ? ' required' : ''}>${field.options.map(([value, label]) => `<option value="${esc(value)}" ${value === field.value ? 'selected' : ''}>${esc(label)}</option>`).join('')}</select>`
                    : `<input data-settings-field name="${esc(field.name)}" value="${esc(field.value)}"${field.required ? ' required' : ''}>`;
                return `<label class="settings-modal-field"><span>${esc(field.label)}</span>${control}</label>`;
            }).join('');
            const dialog = document.createElement('div');
            dialog.id = 'settingsActionDialog';
            dialog.className = 'settings-action-dialog-backdrop';
            dialog.innerHTML = `<section class="settings-action-dialog" role="${options.role || 'dialog'}" aria-modal="true" aria-labelledby="settingsActionDialogTitle">
                <div class="settings-action-dialog-icon ${options.danger ? 'danger' : ''}"><i data-lucide="${esc(options.icon || (options.fields?.length ? 'file-edit' : 'triangle-alert'))}"></i></div>
                <div class="settings-action-dialog-content"><h3 id="settingsActionDialogTitle">${esc(options.title || '确认操作')}</h3><p>${esc(options.message || '')}</p>${options.detail ? `<small>${esc(options.detail)}</small>` : ''}${fields}</div>
                <div class="settings-action-dialog-actions"><button type="button" class="btn btn-outline" data-settings-cancel>取消</button><button type="button" class="btn ${options.danger ? 'btn-danger' : 'btn-primary'}" data-settings-confirm>${esc(options.confirmText || '确认')}</button></div>
            </section>`;
            document.body.appendChild(dialog);
            if (typeof refreshIcons === 'function') refreshIcons();
            return dialog;
        },

        message(text, type = '') {
            const target = document.getElementById('settingsMessage');
            if (!target) return;
            if (this.messageTimer) window.clearTimeout(this.messageTimer);
            this.messageTimer = null;
            target.textContent = text;
            target.className = `settings-dialog-footer ${type}`;
            if (text && type === 'success') {
                this.messageTimer = window.setTimeout(() => {
                    if (target.textContent === text) {
                        target.textContent = '';
                        target.className = 'settings-dialog-footer';
                    }
                    this.messageTimer = null;
                }, 5000);
            }
        },
    };

    window.Settings = Settings;
    document.addEventListener('DOMContentLoaded', () => Settings.init());
})();
