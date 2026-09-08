/** RPA任务管理：处理已进入RPA的任务并发送微信通知。 */
const RpaPage = {
    _rendered: false,
    _selectedTaskIds: new Set(),
    _tasksById: new Map(),
    _tablePages: {},
    _tablePageSizes: { rpaDraftTable: 10, rpaWechatTable: 10, rpaWechatSentTable: 10 },

    render() {
        const container = document.getElementById('page-rpa');
        if (!this._rendered) {
            container.innerHTML = this.template();
            this.bindEvents();
            this._rendered = true;
        }
        this.loadStats();
        this.loadTasks();
    },

    template() {
        return `
            <div class="page-header">
                <h2>${Icons.robot()} RPA任务管理</h2>
            <p>先将草稿发送至 RPA，再勾选未通知任务发送企业微信；所有任务均可删除。</p>
            </div>
            <section class="kpi-grid">
                <div class="card kpi-card"><div class="kpi-label">已生成任务</div><div class="kpi-value" id="statGenerated">--</div><span class="kpi-delta info" id="statGeneratedDelta">加载中</span></div>
                <div class="card kpi-card"><div class="kpi-label">已送达</div><div class="kpi-value" id="statDelivered">--</div><span class="kpi-delta info" id="statDeliveredDelta">加载中</span></div>
                <div class="card kpi-card"><div class="kpi-label">已确认</div><div class="kpi-value" id="statConfirmed">--</div><span class="kpi-delta info" id="statConfirmedDelta">加载中</span></div>
                <div class="card kpi-card"><div class="kpi-label">已完成</div><div class="kpi-value" id="statCompleted">--</div><span class="kpi-delta up" id="statCompletedDelta">加载中</span></div>
            </section>
            <div class="card">
                <div class="card-header">
                    <h3 class="card-title">整改任务列表</h3>
                </div>
                <div class="filter-bar" style="margin-bottom:16px">
                    <div class="field"><label>状态</label><select id="filterStatus"><option value="">全部</option><option value="draft">待派发</option><option value="sent">已发送</option><option value="received">已送达</option><option value="confirmed">已确认</option><option value="in_progress">处理中</option><option value="completed">已完成</option><option value="overdue">已逾期</option><option value="failed">失败</option></select></div>
                    <div class="field"><label>优先级</label><select id="filterPriority"><option value="">全部</option><option value="high">高</option><option value="medium">中</option><option value="low">低</option></select></div>
                    <div class="field"><label>产品</label><select id="filterProduct"><option value="">全部</option>${AppState.products.map(p => `<option value="${Utils.productName(p)}">${Utils.productLabel(p)}</option>`).join('')}</select></div>
                    <div class="field"><label>月份</label><select id="filterMonth"><option value="">全部</option>${AppState.months.map(m => `<option value="${m}">${m}</option>`).join('')}</select></div>
                    <button class="btn btn-primary btn-sm" id="btnFilter">筛选</button>
                </div>
                <div id="taskTable">${Utils.inlineLoading('加载任务列表...')}</div>
            </div>
        `;
    },

    bindEvents() {
        document.getElementById('btnFilter').addEventListener('click', () => this.loadTasks());
    },

    bindActionButtons() {
        document.getElementById('btnDispatchSelected')?.addEventListener('click', () => this.dispatchSelectedTasks());
        document.getElementById('btnNotifySelected')?.addEventListener('click', () => this.notifySelectedTasks());
    },

    bindPagination() {
        document.querySelectorAll('.rpa-page-size').forEach(select => select.addEventListener('change', event => {
            const tableId = event.target.dataset.tableId;
            this._tablePageSizes[tableId] = Number(event.target.value) || 10;
            this._tablePages[tableId] = 1;
            this.renderTaskTable([...this._tasksById.values()]);
        }));
        document.querySelectorAll('.rpa-page-btn').forEach(button => button.addEventListener('click', () => {
            if (button.disabled) return;
            this._tablePages[button.dataset.tableId] = Number(button.dataset.page) || 1;
            this.renderTaskTable([...this._tasksById.values()]);
        }));
    },

    async loadStats() {
        try {
            this.renderStats(await Utils.api('/api/rpa/stats'));
        } catch (error) {
            this.renderStats({ total: 0, draft: 0, delivered: 0, confirmed: 0, completed: 0, failed: 0 });
        }
    },

    renderStats(data) {
        const total = data.generated ?? data.total ?? 0;
        const draft = data.draft ?? 0;
        const delivered = data.delivered ?? 0;
        const confirmed = data.confirmed ?? 0;
        const completed = data.completed ?? 0;
        const failed = data.failed ?? 0;
        document.getElementById('statGenerated').textContent = total;
        document.getElementById('statDelivered').textContent = delivered;
        document.getElementById('statConfirmed').textContent = confirmed;
        document.getElementById('statCompleted').textContent = completed;
        document.getElementById('statGeneratedDelta').textContent = draft ? `${draft}项待派发` : '暂无待派发';
        document.getElementById('statDeliveredDelta').textContent = `送达率 ${total ? Math.round(delivered / total * 100) : 0}%`;
        document.getElementById('statConfirmedDelta').textContent = `确认率 ${total ? Math.round(confirmed / total * 100) : 0}%`;
        const completedDelta = document.getElementById('statCompletedDelta');
        completedDelta.textContent = failed ? `${failed}项异常` : `完成率 ${total ? Math.round(completed / total * 100) : 0}%`;
        completedDelta.className = `kpi-delta ${failed ? 'down' : 'up'}`;
    },

    async loadTasks() {
        const query = Utils.qs({
            status: document.getElementById('filterStatus').value,
            priority: document.getElementById('filterPriority').value,
            product: document.getElementById('filterProduct').value,
            month: document.getElementById('filterMonth').value,
        });
        try {
            const data = await Utils.api(`/api/rpa/tasks?${query}`);
            this.renderTaskTable(data.tasks || []);
        } catch (error) {
            this.renderTaskTable([]);
            this.showResult(false, '任务列表加载失败，请确认RPA服务已启动');
        }
    },

    renderTaskTable(tasks) {
        const target = document.getElementById('taskTable');
        this._tasksById = new Map(tasks.map(task => [String(task.task_id), task]));
        this._selectedTaskIds.forEach(taskId => {
            if (!this._tasksById.has(String(taskId))) this._selectedTaskIds.delete(taskId);
        });
        if (!tasks.length) {
            target.innerHTML = Utils.emptyState('', '暂无任务，请在成本看板或对标分析完成归因后生成任务');
            this._selectedTaskIds.clear();
            this.updateDispatchButton();
            return;
        }
        const statusMap = {
            draft: ['待派发', 'info'], sent: ['已发送', 'info'], received: ['已送达', 'info'],
            confirmed: ['已确认', 'warning'], in_progress: ['处理中', 'warning'], completed: ['已完成', 'success'],
            overdue: ['已逾期', 'error'], failed: ['失败', 'error'],
        };
        const priorityMap = { high: ['高', 'error'], medium: ['中', 'warning'], low: ['低', 'success'] };
        const pendingRpa = tasks.filter(task => task.status === 'draft');
        const pendingWechat = tasks.filter(task => task.status !== 'draft' && task.notification_status !== 'sent');
        const sentWechat = tasks.filter(task => task.notification_status === 'sent');
        const renderTable = (rows, tableId, selectLabel, selectable = true) => {
            if (!rows.length) return Utils.emptyState('', tableId === 'rpaDraftTable' ? '暂无待发送至 RPA 的任务' : (tableId === 'rpaWechatTable' ? '暂无待发送微信的任务' : '暂无已发送微信的任务'));
            const pageSize = this._tablePageSizes[tableId] || 10;
            const totalPages = Math.max(1, Math.ceil(rows.length / pageSize));
            const page = Math.min(Math.max(1, this._tablePages[tableId] || 1), totalPages);
            this._tablePages[tableId] = page;
            const visibleRows = rows.slice((page - 1) * pageSize, page * pageSize);
            let html = `<table class="data-table"><thead><tr>
                <th>${selectable ? `<input type="checkbox" id="${tableId}SelectAll" aria-label="全选${selectLabel}">` : ''}</th><th>任务标题</th><th>责任人</th><th>任务来源</th><th>优先级</th><th>截止日</th><th>RPA状态</th><th>微信状态</th><th>操作</th>
            </tr></thead><tbody>`;
            visibleRows.forEach(task => {
            const status = statusMap[task.status] || ['未知', 'info'];
            const priority = priorityMap[task.priority] || priorityMap.medium;
            // 已发送微信的任务不再参与通知选择，但仍保留删除操作。
            const rowSelectable = selectable && task.status !== 'failed' && task.notification_status !== 'sent';
            const selected = this._selectedTaskIds.has(task.task_id);
            const owner = `${task.assignee?.department || '--'} / ${task.assignee?.role || '--'}`;
            const source = task.source || {};
            const sourceName = source.analysis_scenario || '--';
            const conclusion = String(source.attribution_conclusion || '--').replace(/\s+/g, ' ').trim();
            const sourceText = `${sourceName}：${conclusion}`;
            const sourceSummary = conclusion.length > 32 ? `${conclusion.slice(0, 32)}...` : conclusion;
            const actions = `<button type="button" class="btn btn-outline btn-sm task-delete-btn" data-task-id="${Utils.escapeHtml(task.task_id)}" title="删除任务记录" aria-label="删除${Utils.escapeHtml(task.task_title)}" style="color:var(--phc-state-error);border-color:var(--phc-state-error)"><i data-lucide="trash-2" style="width:15px;height:15px"></i></button>`;
            html += `<tr data-task-status="${Utils.escapeHtml(task.status)}" data-notification-status="${Utils.escapeHtml(task.notification_status || 'not_sent')}">
                <td>${selectable ? `<input type="checkbox" class="task-selector" data-task-id="${Utils.escapeHtml(task.task_id)}" ${rowSelectable ? '' : 'disabled'} ${selected ? 'checked' : ''} aria-label="选择${Utils.escapeHtml(task.task_title)}">` : '<span aria-hidden="true">—</span>'}</td>
                <td><div style="font-weight:500">${Utils.escapeHtml(task.task_title)}</div><div style="font-family:monospace;font-size:12px;color:var(--phc-ink-3)">${Utils.escapeHtml(task.task_id)}</div></td>
                <td>${Utils.escapeHtml(owner)}</td>
                <td title="${Utils.escapeHtml(sourceText)}" style="max-width:220px"><div style="font-weight:500">${Utils.escapeHtml(sourceName)}</div><div style="font-size:12px;color:var(--phc-ink-3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:220px">${Utils.escapeHtml(sourceSummary)}</div></td>
                <td><span class="status-pill ${priority[1]}">${priority[0]}</span></td>
                <td>${Utils.escapeHtml(task.deadline || '--')}</td>
                <td><span class="status-pill ${status[1]}">${status[0]}</span></td>
                <td><span class="status-pill ${task.notification_status === 'sent' ? 'success' : (task.notification_status === 'failed' ? 'error' : 'info')}">${task.notification_status === 'sent' ? '已发送' : (task.notification_status === 'failed' ? '发送失败' : (task.status === 'draft' ? '待发送至 RPA' : '待发送'))}</span></td>
                <td>${actions}</td>
            </tr>`;
            });
            const pageOptions = [5, 10, 20, 50].map(size => `<option value="${size}" ${size === pageSize ? 'selected' : ''}>${size} 条</option>`).join('');
            const pages = Array.from({ length: totalPages }, (_, index) => index + 1).map(number => `<button type="button" class="btn btn-outline btn-sm rpa-page-btn${number === page ? ' active' : ''}" data-table-id="${tableId}" data-page="${number}">${number}</button>`).join('');
            return `<div class="rpa-table-scroll">${html}</tbody></table></div><div class="rpa-pagination"><label>每页<select class="rpa-page-size" data-table-id="${tableId}">${pageOptions}</select></label><span>第 ${page} / ${totalPages} 页，共 ${rows.length} 条</span><div class="rpa-page-buttons"><button type="button" class="btn btn-outline btn-sm rpa-page-btn" data-table-id="${tableId}" data-page="${page - 1}" ${page <= 1 ? 'disabled' : ''}>上一页</button>${pages}<button type="button" class="btn btn-outline btn-sm rpa-page-btn" data-table-id="${tableId}" data-page="${page + 1}" ${page >= totalPages ? 'disabled' : ''}>下一页</button></div></div>`;
        };
        target.innerHTML = `
            <section class="rpa-task-section"><div class="rpa-task-section-header"><div><h4>待发送至 RPA <span>${pendingRpa.length}</span></h4><p>勾选后提交到 RPA 服务，不发送微信</p></div><button class="btn btn-outline btn-sm" id="btnDispatchSelected" disabled title="勾选待派发草稿后可用"><i data-lucide="send" style="width:16px;height:16px"></i> 发送至 RPA</button></div><div id="rpaDraftTable">${renderTable(pendingRpa, 'rpaDraftTable', '待发送至 RPA 任务')}</div></section>
            <section class="rpa-task-section"><div class="rpa-task-section-header"><div><h4>待发送微信 <span>${pendingWechat.length}</span></h4><p>仅已发送至 RPA 且未通知的任务可勾选</p></div><button class="btn btn-primary btn-sm" id="btnNotifySelected" disabled title="勾选已发送至 RPA 且未通知的任务后可用"><i data-lucide="message-circle" style="width:16px;height:16px"></i> 发送微信</button></div><div id="rpaWechatTable">${renderTable(pendingWechat, 'rpaWechatTable', '待发送微信任务')}</div></section>
            <section class="rpa-task-section"><div class="rpa-task-section-header"><div><h4>已发送到微信 <span>${sentWechat.length}</span></h4><p>微信通知已完成，仅保留发送记录</p></div></div><div id="rpaWechatSentTable">${renderTable(sentWechat, 'rpaWechatSentTable', '已发送微信任务', false)}</div></section>`;
        this.bindActionButtons();
        this.bindTaskSelection();
        this.bindPagination();
        if (window.lucide) lucide.createIcons();
    },

    bindTaskSelection() {
        document.querySelectorAll('.task-selector').forEach(input => {
            input.addEventListener('change', event => {
                const taskId = event.target.dataset.taskId;
                if (event.target.checked) this._selectedTaskIds.add(taskId);
                else this._selectedTaskIds.delete(taskId);
                this.updateDispatchButton();
                this.syncSelectAll();
            });
        });
        document.querySelectorAll('[id$="SelectAll"]').forEach(selectAll => selectAll.addEventListener('change', event => {
            const section = selectAll.closest('.rpa-task-section');
            section?.querySelectorAll('.task-selector:not(:disabled)').forEach(input => {
                input.checked = event.target.checked;
                if (input.checked) this._selectedTaskIds.add(input.dataset.taskId);
                else this._selectedTaskIds.delete(input.dataset.taskId);
            });
            this.updateDispatchButton();
            this.syncSelectAll();
        }));
        document.querySelectorAll('.task-delete-btn').forEach(button => {
            button.addEventListener('click', () => this.deleteTask(button.dataset.taskId));
        });
        this.syncSelectAll();
        this.updateDispatchButton();
    },

    syncSelectAll() {
        document.querySelectorAll('[id$="SelectAll"]').forEach(selectAll => {
            const section = selectAll.closest('.rpa-task-section');
            const selectors = [...(section?.querySelectorAll('.task-selector:not(:disabled)') || [])];
            selectAll.checked = selectors.length > 0 && selectors.every(input => input.checked);
            selectAll.indeterminate = selectors.some(input => input.checked) && !selectAll.checked;
            selectAll.disabled = selectors.length === 0;
        });
    },

    updateDispatchButton() {
        const selectedTasks = [...this._selectedTaskIds]
            .map(taskId => this._tasksById.get(String(taskId)))
            .filter(Boolean);
        const drafts = selectedTasks.filter(task => task.status === 'draft');
        const notifyable = selectedTasks.filter(task => task.status !== 'draft' && task.status !== 'failed' && task.notification_status !== 'sent');
        const dispatchButton = document.getElementById('btnDispatchSelected');
        const notifyButton = document.getElementById('btnNotifySelected');
        if (dispatchButton) {
            dispatchButton.disabled = drafts.length === 0;
            dispatchButton.title = drafts.length
                ? `发送${drafts.length}项待派发任务至 RPA`
                : '勾选待派发草稿后可用';
        }
        if (notifyButton) {
            notifyButton.disabled = notifyable.length === 0;
            notifyButton.title = notifyable.length
                ? `发送${notifyable.length}项企业微信通知`
                : '仅已发送至 RPA 且微信未发送的任务可通知';
        }
    },

    async dispatchSelectedTasks() {
        const taskIds = [...this._selectedTaskIds].filter(taskId => {
            return this._tasksById.get(String(taskId))?.status === 'draft';
        });
        if (!taskIds.length) return;
        const button = document.getElementById('btnDispatchSelected');
        button.disabled = true;
        button.innerHTML = '<div class="spinner-small" style="width:16px;height:16px;border-width:2px"></div> RPA发送中...';
        try {
            const result = await Utils.api('/api/rpa/dispatch-selected', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ task_ids: taskIds }), timeoutMs: 30000,
            });
            this.showResult(result.tasks_dispatched > 0, `已发送${result.tasks_dispatched || 0}项整改任务至 RPA`);
            this._selectedTaskIds.clear();
            await Promise.all([this.loadStats(), this.loadTasks()]);
        } catch (error) {
            this.showResult(false, 'RPA派发失败，请检查服务状态');
        } finally {
            button.innerHTML = '<i data-lucide="send" style="width:16px;height:16px"></i> 发送至 RPA';
            this.updateDispatchButton();
            if (window.lucide) lucide.createIcons();
        }
    },

    async deleteTask(taskId) {
        if (!taskId || !window.confirm('删除该整改任务及其本地闭环记录？此操作不可恢复。')) return;
        const button = [...document.querySelectorAll('.task-delete-btn')].find(item => item.dataset.taskId === String(taskId));
        if (button) button.disabled = true;
        try {
            await Utils.api(`/api/rpa/tasks/${encodeURIComponent(taskId)}`, { method: 'DELETE' });
            this._selectedTaskIds.delete(taskId);
            this.showResult(true, '整改任务及本地闭环记录已删除');
            await Promise.all([this.loadStats(), this.loadTasks()]);
        } catch (error) {
            this.showResult(false, '任务删除失败，请稍后重试');
            if (button) button.disabled = false;
        }
    },

    async notifySelectedTasks() {
        const taskIds = [...this._selectedTaskIds].filter(taskId => {
            const task = this._tasksById.get(String(taskId));
            return task && task.status !== 'draft' && task.status !== 'failed' && task.notification_status !== 'sent';
        });
        if (!taskIds.length) return;
        const button = document.getElementById('btnNotifySelected');
        button.disabled = true;
        button.innerHTML = '<div class="spinner-small" style="width:16px;height:16px;border-width:2px"></div> 微信发送中...';
        try {
            const data = await Utils.api('/api/rpa/notify-selected', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ task_ids: taskIds }),
                timeoutMs: 30000,
            });
            const recipients = (data.results || []).map(item => item.notify_status?.wechat).filter(Boolean);
            const success = (data.notifications_sent || 0) > 0;
            const message = data.notifications_failed
                ? `成功发送${data.notifications_sent}项，失败${data.notifications_failed}项`
                : `已发送${data.notifications_sent}项微信通知${recipients.length ? `；${recipients.join('、')}` : ''}`;
            this.showResult(success, message);
            this._selectedTaskIds.clear();
            await Promise.all([this.loadStats(), this.loadTasks()]);
        } catch (error) {
            this.showResult(false, '微信通知失败，请检查 RPA 服务状态');
        } finally {
            button.innerHTML = '<i data-lucide="message-circle" style="width:16px;height:16px"></i> 发送微信';
            this.updateDispatchButton();
            if (window.lucide) lucide.createIcons();
        }
    },

    showResult(success, message) {
        document.getElementById('dispatchToast')?.remove();
        const toast = document.createElement('div');
        toast.id = 'dispatchToast';
        toast.className = `operation-toast${success ? '' : ' error'}`;
        toast.innerHTML = `<i data-lucide="${success ? 'check-circle' : 'alert-circle'}" style="width:18px;height:18px"></i><span>${Utils.escapeHtml(message)}</span>`;
        document.body.appendChild(toast);
        if (window.lucide) lucide.createIcons();
        setTimeout(() => toast.remove(), 6000);
    },
};

/** 任务生成后的审阅窗口：先修改和选择任务，再保存为草稿。 */
const TaskDraftDialog = {
    _onDispatched: null,

    open(tasks, onDispatched) {
        this.close();
        const drafts = (tasks || []).filter(task => task?.status === 'draft');
        if (!drafts.length) return;
        this._drafts = drafts;
        this._onDispatched = onDispatched;
        const priorityOptions = value => ['high', 'medium', 'low'].map(priority => {
            const label = { high: '高', medium: '中', low: '低' }[priority];
            return `<option value="${priority}" ${priority === value ? 'selected' : ''}>${label}</option>`;
        }).join('');
        const rows = drafts.map(task => `
            <tr data-task-id="${Utils.escapeHtml(task.task_id)}">
                <td><input type="checkbox" class="draft-dialog-selector" aria-label="选择${Utils.escapeHtml(task.task_title)}"></td>
                <td class="draft-title-cell"><textarea class="draft-field" data-field="task_title" rows="2" aria-label="任务标题">${Utils.escapeHtml(task.task_title)}</textarea></td>
                <td class="draft-owner-fields">
                    <input class="draft-field" data-field="assignee_name" value="${Utils.escapeHtml(task.assignee?.name || '')}" placeholder="姓名" aria-label="责任人姓名">
                    <input class="draft-field" data-field="assignee_department" value="${Utils.escapeHtml(task.assignee?.department || '')}" placeholder="部门" aria-label="责任部门">
                    <input class="draft-field" data-field="assignee_role" value="${Utils.escapeHtml(task.assignee?.role || '')}" placeholder="岗位" aria-label="责任岗位">
                </td>
                <td><select class="draft-field" data-field="priority" aria-label="优先级">${priorityOptions(task.priority || 'medium')}</select></td>
                <td><input type="date" class="draft-field" data-field="deadline" value="${Utils.escapeHtml(task.deadline || '')}" aria-label="截止日"></td>
            </tr>`).join('');
        const dialog = document.createElement('div');
        dialog.id = 'taskDraftDialog';
        dialog.className = 'task-draft-dialog-backdrop';
        dialog.innerHTML = `
            <section class="task-draft-dialog" role="dialog" aria-modal="true" aria-labelledby="taskDraftDialogTitle">
                <header class="task-draft-dialog-header">
                    <div><h3 id="taskDraftDialogTitle">整改任务审阅</h3><p>修改并勾选需要保存的任务草稿，发送 RPA 和微信请在模块四处理</p></div>
                    <button type="button" class="header-icon-btn" id="taskDraftDialogClose" aria-label="关闭" title="关闭"><i data-lucide="x"></i></button>
                </header>
                <div class="task-draft-dialog-body">
                    <div class="task-draft-dialog-toolbar"><label><input type="checkbox" id="selectAllDraftTasks"> 全选</label><span id="taskDraftDialogMessage">请选择要发送的任务</span></div>
                    <div class="data-table-wrapper"><table class="data-table task-draft-dialog-table"><thead><tr><th>选择</th><th>任务标题</th><th>责任人</th><th>优先级</th><th>截止日</th></tr></thead><tbody>${rows}</tbody></table></div>
                </div>
                <footer class="task-draft-dialog-footer">
                    <button type="button" class="btn btn-primary" id="btnDialogSave"><i data-lucide="save" style="width:16px;height:16px"></i> 生成选中草稿</button>
                </footer>
            </section>`;
        document.body.appendChild(dialog);
        dialog.querySelector('#taskDraftDialogClose').addEventListener('click', () => this.close());
        dialog.querySelector('#selectAllDraftTasks').addEventListener('change', event => {
            dialog.querySelectorAll('.draft-dialog-selector').forEach(input => { input.checked = event.target.checked; });
            dialog.querySelector('#taskDraftDialogMessage').textContent = event.target.checked
                ? `已选择${dialog.querySelectorAll('.draft-dialog-selector').length}项任务` : '请选择要发送的任务';
        });
        dialog.querySelectorAll('.draft-dialog-selector').forEach(input => input.addEventListener('change', () => {
            const selectors = [...dialog.querySelectorAll('.draft-dialog-selector')];
            const selectAll = dialog.querySelector('#selectAllDraftTasks');
            selectAll.checked = selectors.length > 0 && selectors.every(item => item.checked);
            selectAll.indeterminate = selectors.some(item => item.checked) && !selectAll.checked;
            dialog.querySelector('#taskDraftDialogMessage').textContent = `已选择${selectors.filter(item => item.checked).length}项任务`;
        }));
        dialog.querySelector('#btnDialogSave').addEventListener('click', () => this.saveSelectedDrafts(dialog));
        if (window.lucide) lucide.createIcons();
    },

    async saveSelectedDrafts(dialog) {
        const rows = [...dialog.querySelectorAll('tbody tr')].filter(row => row.querySelector('.draft-dialog-selector').checked);
        const message = dialog.querySelector('#taskDraftDialogMessage');
        if (!rows.length) {
            message.textContent = '请至少选择一项任务。';
            return;
        }
        const button = dialog.querySelector('#btnDialogSave');
        button.disabled = true;
        button.innerHTML = '<div class="spinner-small" style="width:16px;height:16px;border-width:2px"></div> 保存中...';
        try {
            const selectedTasks = rows.map(row => {
                const original = (this._drafts || []).find(task => String(task.task_id) === String(row.dataset.taskId)) || {};
                return {
                    ...original,
                    task_title: row.querySelector('[data-field="task_title"]').value.trim(),
                    assignee: {
                        name: row.querySelector('[data-field="assignee_name"]').value.trim(),
                        department: row.querySelector('[data-field="assignee_department"]').value.trim(),
                        role: row.querySelector('[data-field="assignee_role"]').value.trim(),
                    },
                    priority: row.querySelector('[data-field="priority"]').value,
                    deadline: row.querySelector('[data-field="deadline"]').value,
                };
            });
            const result = await Utils.api('/api/rpa/save-drafts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tasks: selectedTasks }),
                timeoutMs: 30000,
            });
            const success = result.tasks_saved > 0;
            if (success) {
                const onDispatched = this._onDispatched;
                this.close();
                onDispatched?.(result);
            } else {
                message.textContent = '未保存任务，请至少选择一项有效任务。';
            }
        } catch (error) {
            message.textContent = '草稿保存失败，请检查必填项。';
        } finally {
            if (document.body.contains(dialog)) {
                button.disabled = false;
                button.innerHTML = '<i data-lucide="save" style="width:16px;height:16px"></i> 生成选中草稿';
                if (window.lucide) lucide.createIcons();
            }
        }
    },

    close() {
        document.getElementById('taskDraftDialog')?.remove();
        this._onDispatched = null;
        this._drafts = null;
    },
};

window.TaskDraftDialog = TaskDraftDialog;
