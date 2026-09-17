import { requestNotes, uploadNoteImage, newOperationId, moveInsertionOffset, formatSaveCountdown } from './notes-api.js';
import { createNotesDraftStore } from './notes-drafts.js';
import { createNotesMotion } from './notes-motion.js';
import { createNotesSelectionMotion } from './notes-selection-motion.js';
import { createDialogMotionController } from './dialog-motion.js';

const DEFAULT_SETTINGS = { autoSaveEnabled: true, autoSaveSeconds: 60, saveOnSwitch: true, collapseOnBlur: false };
const MAX_IMAGE_BYTES = 20 * 1024 * 1024;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const byId = (id) => document.getElementById(id);

export function initializeNotes() {
    const shell = byId('notes-shell');
    if (!shell) return null;
    const ui = Object.fromEntries(['open', 'panel', 'close', 'new', 'list', 'title', 'body', 'preview', 'empty', 'editor-pane', 'format-toolbar', 'mode-edit', 'mode-preview', 'save', 'save-status', 'countdown', 'notice', 'note-meta', 'conflict', 'uploads', 'keep-draft', 'image-input'].map((key) => [key, byId(`notes-${key}`)]));
    const tabId = newOperationId();
    const drafts = createNotesDraftStore(tabId);
    const notes = new Map();
    const activeTabs = new Map();
    const uploads = new Map();
    const failedToasts = new Set();
    const channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('homelab-notes-presence-v1') : null;
    const state = { open: false, loaded: false, loading: null, available: false, enabled: true, closing: false, selected: null, order: [], orderRevision: null, settings: { ...DEFAULT_SETTINGS }, status: {}, scope: location.origin, dirtySince: 0, saving: null, recoveryChecked: false, draftAvailable: true, suppressBlur: false, ordering: false, orderTimer: 0, orderGeneration: 0, uploadRunning: false, lastStatusRead: 0, lastRecoveryTry: 0 };
    const motion = createNotesMotion(shell, ui.open, ui.panel, () => { updateState(); renderList(); });
    const selectionMotion = createNotesSelectionMotion(ui.list, ui['editor-pane'], () => { updateState(); renderList(); });
    let selectionTargetId = null;
    let displayedNoteId = null;
    try { state.scope = localStorage.getItem('homelab-notes-collection-v1') || state.scope; } catch { /* Offline recovery uses the last verified collection when available. */ }
    let renderer;
    let renderToken = 0;
    let dragId;
    let pointerOutside = false;
    let writeSlots = 0;
    const writeWaiters = [];
    async function acquireWrite() {
        if (writeSlots >= 2) await new Promise((resolve) => writeWaiters.push(resolve));
        else writeSlots++;
        return () => { const next = writeWaiters.shift(); if (next) next(); else writeSlots--; };
    }
    const toast = document.createElement('div');
    toast.className = 'notes-toast'; toast.setAttribute('role', 'status'); toast.hidden = true; document.body.append(toast);
    let toastTimer;
    function notify(message, error = false, dedupe = true) {
        if (error && dedupe && failedToasts.has(message)) return;
        if (error) failedToasts.add(message);
        toast.textContent = message; toast.dataset.error = String(error); toast.hidden = false;
        clearTimeout(toastTimer); toastTimer = setTimeout(() => { toast.hidden = true; }, error ? 6500 : 3500);
    }
    function showNotice(message = '') { ui.notice.textContent = message; ui.notice.hidden = !message; }
    function isEditable() {
        const current = notes.get(state.selected);
        return state.available && !state.closing && !motion.isBusy() && !selectionMotion.isBusy() && (!current || (current.loaded && !current.deleting && !current.resolving));
    }
    function dirtyNotes() { return [...notes.values()].filter((note) => note.dirty); }
    function noteTasks(id) { return [...uploads.values()].filter((task) => task.noteId === id && task.status !== 'removed'); }
    function canAutoSave(note) { return !note.conflict && !noteTasks(note.id).some((task) => ['failed', 'checking'].includes(task.status)); }
    function hasPending() { return dirtyNotes().length > 0 || [...uploads.values()].some((task) => !['committed', 'removed'].includes(task.status)); }
    function displayTitle(note) { return note.title?.trim() || '未命名笔记'; }
    function rememberSelection() { try { localStorage.setItem('homelab-notes-selection-v1', state.selected || ''); } catch { /* Optional reading position. */ } }
    function announce() { channel?.postMessage({ type: 'alive', tabId, time: Date.now() }); }
    channel?.addEventListener('message', ({ data }) => {
        if (!data || data.tabId === tabId) return;
        if (data.type === 'alive') activeTabs.set(data.tabId, Date.now());
        if (data.type === 'query') announce();
        if (data.type === 'bye') activeTabs.delete(data.tabId);
    });
    announce();
    setInterval(announce, 10000);

    function updateState() {
        const dirty = dirtyNotes();
        const saveable = dirty.filter(canAutoSave);
        state.dirtySince = saveable.length ? Math.min(...saveable.map((note) => note.dirtySince || (note.dirtySince = Date.now()))) : 0;
        const uploading = [...uploads.values()].some((task) => ['queued', 'uploading', 'checking'].includes(task.status));
        let message = state.loaded ? '已保存到服务器' : '展开以连接笔记服务';
        if (!state.enabled) message = '笔记尚未启用';
        else if (!state.available && state.loaded) message = '连接不可用 · 只读';
        else if (state.closing) message = uploading ? '正在等待图片上传，完成后收起…' : '正在保存，完成后收起…';
        else if (selectionMotion.isBusy()) message = notes.get(state.selected)?.loaded ? '正在切换笔记…' : '正在读取正文…';
        else if (state.saving) message = uploading ? '等待图片上传 · 其他笔记正在保存' : '正在保存…';
        else if (dirty.length) message = `${dirty.length} 篇未同步${dirty.some((note) => note.conflict) ? ' · 有版本冲突' : dirty.every((note) => note.draftSavedGeneration === note.generation) ? ' · 已暂存本机' : ''}`;
        else if (state.status.backupPending) message = '已保存到服务器，备份待同步';
        ui['save-status'].textContent = message;
        ui['save-status'].dataset.warning = String(!state.available || Boolean(dirty.length) || Boolean(state.status.backupPending));
        const remaining = state.dirtySince + state.settings.autoSaveSeconds * 1000 - Date.now();
        ui.countdown.textContent = saveable.length && state.available && state.settings.autoSaveEnabled && !state.saving ? (uploading ? '等待图片上传' : `自动保存 ${formatSaveCountdown(remaining)}`) : '';
        ui.save.disabled = !state.available || state.closing || Boolean(state.saving) || !dirty.length;
        ui.new.disabled = !isEditable();
        ui.title.readOnly = !isEditable(); ui.body.readOnly = !isEditable();
        ui['mode-edit'].disabled = ui['mode-preview'].disabled = selectionMotion.isBusy();
        ui.preview.inert = selectionMotion.isBusy();
        ui.preview.querySelectorAll('.notes-task-checkbox').forEach((checkbox) => { checkbox.disabled = !isEditable(); });
        ui['format-toolbar'].querySelectorAll('button,select').forEach((control) => { control.disabled = !isEditable(); });
        ui.close.disabled = state.closing;
        byId('notes-conflict-copy').disabled = !isEditable();
        byId('notes-conflict-reload').disabled = !isEditable();
        shell.dataset.readonly = String(!isEditable());
        if (!dirty.length) state.dirtySince = 0;
    }

    function renderList() {
        const focus = document.activeElement;
        const focusedId = focus?.closest?.('[data-note-id]')?.dataset.noteId;
        const focusedAction = focus?.dataset.action;
        ui.list.replaceChildren(selectionMotion.highlight);
        const valid = state.order.filter((id) => notes.has(id));
        for (const [index, id] of valid.entries()) {
            const note = notes.get(id);
            const row = document.createElement('div'); row.className = 'notes-list-item'; row.dataset.noteId = id; row.dataset.active = String(state.selected === id);
            const handle = document.createElement('span'); handle.className = 'notes-drag-handle'; handle.textContent = '⠿'; handle.title = '拖拽排序'; handle.draggable = !note.isNew && isEditable(); handle.setAttribute('aria-hidden', 'true');
            handle.addEventListener('dragstart', (event) => { dragId = id; event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', id); });
            const select = document.createElement('button'); select.type = 'button'; select.className = 'notes-note-select'; select.setAttribute('aria-current', state.selected === id ? 'true' : 'false'); select.dataset.action = 'select';
            const title = document.createElement('span'); title.textContent = displayTitle(note); select.append(title);
            const sub = document.createElement('small'); sub.textContent = note.conflict ? '版本冲突' : note.dirty ? '未同步' : note.isNew ? '新笔记' : 'Markdown'; select.append(sub); select.addEventListener('click', () => selectNote(id));
            const actions = document.createElement('div'); actions.className = 'notes-row-actions';
            for (const [action, label, symbol] of [['up', '上移', '↑'], ['down', '下移', '↓'], ['delete', '删除', '×']]) {
                const button = document.createElement('button'); button.type = 'button'; button.textContent = symbol; button.title = `${label}“${displayTitle(note)}”`; button.setAttribute('aria-label', button.title); button.dataset.action = action;
                button.disabled = !isEditable() || (action !== 'delete' && (note.isNew || (action === 'up' ? index === 0 || notes.get(valid[index - 1])?.isNew : index === valid.length - 1)));
                button.addEventListener('click', () => action === 'delete' ? deleteNote(id) : moveNote(id, action === 'up' ? -1 : 1)); actions.append(button);
            }
            row.addEventListener('dragover', (event) => { if (dragId && dragId !== id && !note.isNew && isEditable()) { event.preventDefault(); event.dataTransfer.dropEffect = 'move'; } });
            row.addEventListener('drop', (event) => { event.preventDefault(); if (dragId && isEditable()) { const from = state.order.indexOf(dragId); const to = state.order.indexOf(id); state.order.splice(from, 1); state.order.splice(to, 0, dragId); scheduleOrder(); } dragId = null; });
            row.append(handle, select, actions); ui.list.append(row);
        }
        if (!valid.length) { const empty = document.createElement('p'); empty.className = 'notes-list-empty'; empty.textContent = state.loaded ? '还没有笔记' : '正在读取…'; ui.list.append(empty); }
        if (focusedId && focusedAction) ui.list.querySelector(`[data-note-id="${focusedId}"] [data-action="${focusedAction}"]`)?.focus({ preventScroll: true });
        selectionMotion.updateHighlight();
    }

    async function renderPreview(note) {
        const token = ++renderToken;
        renderer ||= import('./notes-markdown.js');
        try {
            const module = await renderer;
            if (token !== renderToken || state.selected !== note.id || note.mode !== 'preview') return;
            await module.renderNotesMarkdown(note.body || '', ui.preview, {
                readOnly: !isEditable(), onTaskToggle: ({ offset, checked }) => {
                    if (!isEditable() || state.selected !== note.id) return;
                    if (note.body[offset - 1] !== '[' || note.body[offset + 1] !== ']') return;
                    updateBody(note, note.body.slice(0, offset) + (checked ? 'x' : ' ') + note.body.slice(offset + 1));
                    renderPreview(note);
                }
            });
        } catch (error) { if (token === renderToken) ui.preview.textContent = note.body || ''; notify(`预览暂不可用：${error.message}`, true); }
    }

    function renderDetail(duringSwitch = false) {
        if (selectionMotion.isBusy() && !duringSwitch) {
            if (selectionTargetId === state.selected) { updateState(); return; }
            selectionMotion.cancel();
        }
        const note = notes.get(state.selected);
        displayedNoteId = note?.id || null;
        ui.empty.hidden = Boolean(note); ui['editor-pane'].hidden = !note;
        if (!note) { renderToken++; updateState(); return; }
        ui.title.value = note.title || ''; ui.body.value = note.body || '';
        ui['mode-edit'].setAttribute('aria-pressed', String(note.mode === 'edit'));
        ui['mode-preview'].setAttribute('aria-pressed', String(note.mode !== 'edit'));
        ui['format-toolbar'].hidden = note.mode !== 'edit'; ui.body.hidden = note.mode !== 'edit'; ui.preview.hidden = note.mode === 'edit';
        ui.conflict.hidden = !note.conflict;
        ui['note-meta'].textContent = !note.loaded ? '正在读取正文…' : note.isNew ? '新笔记 · 尚未保存' : note.dirty ? '有未同步修改' : note.updatedAt ? `更新于 ${new Date(note.updatedAt).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })}` : 'Markdown';
        let preview;
        if (note.mode !== 'edit') { preview = renderPreview(note).then(() => { if (state.selected === note.id) ui.preview.scrollTop = note.previewScrollTop || 0; }); }
        else { renderToken++; ui.body.scrollTop = note.scrollTop || 0; if (note.selection) ui.body.setSelectionRange(...note.selection); }
        renderUploads(); updateState();
        return preview;
    }

    async function fetchNote(id) {
        const { note: remote } = await requestNotes(`/${encodeURIComponent(id)}`);
        const local = notes.get(id);
        if (local?.dirty || local?.saving) {
            if (local.revision !== remote.revision && !local.saving) local.conflict = true;
            return local;
        }
        const note = { ...remote, dirty: false, generation: 0, isNew: false, mode: local?.mode || 'preview', scrollTop: local?.scrollTop || 0, loaded: true };
        notes.set(id, note); return note;
    }

    async function refresh() {
        if (state.loading) return state.loading;
        const selected = notes.get(state.selected);
        if (selected && displayedNoteId === selected.id) { selected.scrollTop = ui.body.scrollTop; selected.previewScrollTop = ui.preview.scrollTop; selected.selection = [ui.body.selectionStart, ui.body.selectionEnd]; }
        state.loading = (async () => {
            try {
                const results = await Promise.allSettled([requestNotes(), requestNotes('/status')]);
                const status = results[1].status === 'fulfilled' ? results[1].value : state.status;
                state.status = status; state.enabled = status.enabled !== false;
                if (status.collectionId) state.scope = status.collectionId;
                if (results[0].status === 'rejected') throw results[0].reason;
                const list = results[0].value;
                state.scope = list.collectionId || state.scope; state.available = state.enabled && status.primaryAvailable !== false;
                try { localStorage.setItem('homelab-notes-collection-v1', state.scope); } catch { /* Recovery still works within this page. */ }
                state.settings = { ...DEFAULT_SETTINGS, ...list.settings };
                const ids = list.order?.ids || list.notes.map((note) => note.id);
                const remoteIds = new Set(list.notes.map((note) => note.id));
                for (const remote of list.notes) {
                    const local = notes.get(remote.id);
                    if (!local) notes.set(remote.id, { ...remote, body: '', generation: 0, mode: 'preview', loaded: false, dirty: false });
                    else if (local.dirty || local.saving) {
                        if (!local.saving) {
                            if (local.unverifiedRecovery) { local.conflict = local.revision !== remote.revision; local.unverifiedRecovery = false; }
                            else if (local.revision !== remote.revision) local.conflict = true;
                        }
                    }
                    else if (local.revision !== remote.revision) { Object.assign(local, remote, { loaded: false }); if (state.selected === remote.id) updateState(); }
                }
                for (const [id, note] of notes) {
                    if (!remoteIds.has(id) && !note.isNew) {
                        if (note.dirty || note.saving) note.conflict = true;
                        else notes.delete(id);
                    }
                }
                const retained = state.order.filter((id) => notes.has(id) && !remoteIds.has(id));
                if (!state.orderTimer && !state.ordering) { state.order = [...retained, ...ids.filter((id) => notes.has(id)), ...list.notes.map((note) => note.id).filter((id) => !ids.includes(id))]; state.orderRevision = list.order?.revision || list.orderRevision; }
                if (!state.selected || !notes.has(state.selected)) {
                    let remembered; try { remembered = localStorage.getItem('homelab-notes-selection-v1'); } catch { /* Reading position is optional. */ }
                    state.selected = notes.has(remembered) ? remembered : state.order[0] || null;
                }
                if (state.selected && !notes.get(state.selected).loaded) await fetchNote(state.selected);
                state.loaded = true; showNotice(!state.enabled ? '笔记尚未启用，请在后端配置向导中配置笔记目录。' : !state.available ? '主存储暂不可用，当前仅可阅读与复制。' : '');
                failedToasts.clear(); renderList(); renderDetail();
                if (!state.recoveryChecked) { state.recoveryChecked = true; await offerDraftRecovery(); }
            } catch (error) {
                state.loaded = true; state.available = false; state.enabled = error.status !== 404;
                showNotice(error.status === 404 ? '当前后端未启用笔记或版本过旧，请检查后端配置。' : '笔记服务暂不可用，已保留当前内容；恢复连接后可继续编辑。');
                notify(error.message, true); updateState(); renderList();
                if (!state.recoveryChecked) { state.recoveryChecked = true; await offerDraftRecovery(); }
            } finally { state.loading = null; }
        })();
        return state.loading;
    }

    async function openPanel() {
        if (state.open || state.closing) return;
        state.open = true; shell.dataset.view = 'list';
        await Promise.all([motion.setOpen(true), refresh()]);
    }
    async function collapse() {
        state.open = false; state.closing = true; ui['keep-draft'].hidden = true; updateState();
        selectionMotion.cancel();
        await motion.setOpen(false);
        state.closing = false; updateState();
    }
    async function closePanel(fromOutside = false) {
        if (!state.open || state.closing) return;
        if (!hasPending()) { await collapse(); if (!fromOutside) ui.open.focus({ preventScroll: true }); return; }
        state.closing = true; renderList(); updateState();
        const result = await saveAll('close');
        if (!hasPending() && !result.failed) { await collapse(); notify('笔记已保存'); if (!fromOutside) ui.open.focus({ preventScroll: true }); }
        else { state.closing = false; ui['keep-draft'].hidden = !(await flushAllDrafts()); renderList(); renderDetail(); notify('尚有内容未同步，笔记面板已保留。', true); }
    }

    async function selectNote(id) {
        if (state.closing || !notes.has(id)) return;
        const previous = notes.get(state.selected);
        const detailHidden = !ui['editor-pane'].getClientRects().length;
        if (previous?.id === id && (selectionMotion.isBusy() || (previous.loaded && !detailHidden))) return;
        if (previous && displayedNoteId === previous.id) { previous.scrollTop = ui.body.scrollTop; previous.previewScrollTop = ui.preview.scrollTop; previous.selection = [ui.body.selectionStart, ui.body.selectionEnd]; }
        if (previous?.isNew && !previous.dirty && !noteTasks(previous.id).length && previous.id !== id) { notes.delete(previous.id); state.order = state.order.filter((item) => item !== previous.id); }
        state.selected = id; shell.dataset.view = 'detail'; rememberSelection();
        selectionTargetId = id;
        renderToken++;
        if (previous?.id !== id && state.settings.saveOnSwitch) saveAll('switch');
        const note = notes.get(id);
        // Fetch during fade-out; a late response may populate the cache but cannot reveal an obsolete selection.
        const loaded = note.loaded ? Promise.resolve() : fetchNote(id).catch((error) => {
            if (state.selected !== id) return;
            notify(error.message, true); if (error.status === 0 || error.status >= 500) state.available = false;
        });
        await selectionMotion.switchTo(async (current) => {
            await loaded;
            if (!current() || state.selected !== id) return;
            await renderDetail(true);
        }, { skipFadeOut: detailHidden });
    }
    function newNote() {
        if (!isEditable()) return;
        const previous = notes.get(state.selected);
        if (previous?.isNew && !previous.dirty && !noteTasks(previous.id).length) { shell.dataset.view = 'detail'; renderDetail(); ui.title.focus(); return; }
        if (state.settings.saveOnSwitch) saveAll('switch');
        const id = newOperationId(); notes.set(id, { id, title: '', body: '', revision: null, isNew: true, loaded: true, dirty: false, generation: 0, mode: 'edit' });
        state.order.unshift(id); state.selected = id; shell.dataset.view = 'detail'; rememberSelection(); renderList(); renderDetail(); ui.title.focus();
    }
    function markDirty(note) {
        if (!note.dirty) note.dirtySince = Date.now();
        note.dirty = true; note.generation++; note.draftSavedGeneration = -1;
        if (!state.dirtySince) state.dirtySince = Date.now();
        scheduleDraft(note); renderList(); updateState();
    }
    function updateBody(note, next) {
        for (const task of noteTasks(note.id)) if (['queued', 'uploading', 'checking', 'failed'].includes(task.status)) task.offset = moveInsertionOffset(note.body, next, task.offset);
        note.body = next; markDirty(note);
    }
    function draftRecord(note) {
        return { id: note.id, title: note.title, body: note.body, revision: note.revision, isNew: note.isNew, generation: note.generation, resolvesConflictId: note.resolvesConflictId, resolvesConflictGeneration: note.resolvesConflictGeneration, uploads: noteTasks(note.id).filter((task) => !['committed', 'removed'].includes(task.status)).map(({ id, status, offset, name, url, inserted }) => ({ id, status, offset, name, url, inserted })) };
    }
    async function flushDraft(note) {
        clearTimeout(note.draftTimer); clearTimeout(note.draftMaxTimer); note.draftTimer = 0; note.draftMaxTimer = 0;
        if (!note.dirty) return true;
        const generation = note.generation;
        try { await drafts.write(state.scope, draftRecord(note)); if (note.generation === generation) note.draftSavedGeneration = generation; state.draftAvailable = true; return note.generation === generation; }
        catch { state.draftAvailable = false; notify('本机恢复草稿暂存失败，请及时保存到服务器。', true); return false; }
    }
    function scheduleDraft(note) { clearTimeout(note.draftTimer); note.draftTimer = setTimeout(() => flushDraft(note), 500); if (!note.draftMaxTimer) note.draftMaxTimer = setTimeout(() => flushDraft(note), 2000); }
    async function flushAllDrafts() { const results = await Promise.all(dirtyNotes().map(flushDraft)); return results.every(Boolean); }

    async function saveNote(note) {
        if (note.saving) return note.saving;
        note.saving = (async () => {
            if (!note.dirty) return true;
            if (note.conflict || !state.available) return false;
            const tasks = noteTasks(note.id);
            await Promise.all(tasks.filter((task) => task.promise).map((task) => task.promise));
            if (noteTasks(note.id).some((task) => ['failed', 'checking', 'queued', 'uploading'].includes(task.status))) return false;
            if (!note.dirty) return true;
            const generation = note.generation;
            note.lastAttemptGeneration = generation;
            const snapshot = { expectedRevision: note.revision || null, operationId: newOperationId(), title: note.title, body: note.body };
            const releaseWrite = await acquireWrite();
            try {
                let result;
                try { result = await requestNotes(`/${note.id}`, 'PUT', snapshot); }
                catch (error) {
                    if (error.status !== 0) throw error;
                    // A timed-out write might have committed. Verify exact content before retrying.
                    try { const current = await requestNotes(`/${note.id}`); if (current.note.title === snapshot.title && current.note.body === snapshot.body) result = current; } catch { /* Keep original transport error if verification is unavailable. */ }
                    if (!result) throw error;
                }
                note.revision = result.note.revision; note.updatedAt = result.note.updatedAt; note.isNew = false; note.conflict = false;
                state.status.backupPending = result.backupPending ? Math.max(1, state.status.backupPending || 0) : state.status.backupPending;
                if (result.order && !state.orderTimer && !state.ordering) {
                    state.orderRevision = result.order.revision;
                    const retained = state.order.filter((id) => !result.order.ids.includes(id) && notes.has(id) && (notes.get(id).isNew || notes.get(id).dirty || notes.get(id).conflict));
                    state.order = [...retained, ...result.order.ids];
                }
                if (note.generation === generation) {
                    note.dirty = false; clearTimeout(note.draftTimer); clearTimeout(note.draftMaxTimer);
                    await drafts.remove(state.scope, note.id).catch(() => {});
                    for (const task of tasks) if (task.status === 'uploaded') {
                        if (Array.isArray(result.note.attachments) ? result.note.attachments.includes(task.id) : snapshot.body.includes(task.url)) task.status = 'committed';
                        else { task.status = 'removed'; requestNotes(`/uploads/${task.id}/discard`, 'POST', {}).catch((error) => notify(error.message, true)); uploads.delete(task.id); }
                    }
                } else { scheduleDraft(note); }
                if (note.resolvesConflictId) await resolveCopiedConflict(note);
                return true;
            } catch (error) {
                if ([410, 404].includes(error.status) || (error.status === 409 && ['revision_conflict', 'operation_conflict'].includes(error.payload.error))) note.conflict = true;
                else if (!error.status || error.status >= 500) state.available = false;
                note.error = error.message; notify(error.message, true); await flushDraft(note); return false;
            } finally { releaseWrite(); }
        })();
        try { return await note.saving; } finally { note.saving = null; renderList(); if (state.selected === note.id) { ui.conflict.hidden = !note.conflict; renderUploads(); } updateState(); }
    }
    async function saveAll(reason = 'manual') {
        if (state.saving) { await state.saving; if (reason === 'close' && dirtyNotes().some((note) => !note.conflict)) return saveAll(reason); return { failed: dirtyNotes().length }; }
        const batch = dirtyNotes().filter((note) => !note.deleting && !note.resolving && (reason !== 'auto' || canAutoSave(note)));
        if (!batch.length) return { failed: 0, saved: 0 };
        state.saving = (async () => {
            let saved = 0; let failed = 0;
            // Waiting uploads hold only their own note; ready text writes use two workers.
            const ready = batch.filter((note) => !noteTasks(note.id).some((task) => task.promise));
            const waiting = batch.filter((note) => !ready.includes(note));
            let index = 0;
            const record = async (note) => { if (await saveNote(note)) saved++; else failed++; };
            async function worker() { while (index < ready.length) await record(ready[index++]); }
            await Promise.all([worker(), worker(), ...waiting.map(record)]);
            if (reason === 'manual') notify(failed ? `已保存 ${saved} 篇，${failed} 篇尚未同步。` : `已保存 ${saved} 篇笔记${dirtyNotes().length ? '，仍有新修改待同步。' : ''}`, Boolean(failed), false);
            else if (failed) notify(`${failed} 篇笔记尚未同步，请处理后重试。`, true);
            return { saved, failed };
        })(); updateState();
        try { return await state.saving; } finally {
            state.saving = null;
            const remaining = dirtyNotes();
            // Failed batches wait one interval before retrying; unrelated new edits keep their first-dirty clock.
            const retryStart = Date.now();
            for (const note of batch) if (note.dirty && note.generation === note.lastAttemptGeneration) note.dirtySince = retryStart;
            state.dirtySince = remaining.length ? Math.min(...remaining.map((note) => note.dirtySince || retryStart)) : 0;
            updateState();
        }
    }

    async function deleteNote(id) {
        if (!isEditable()) return;
        const note = notes.get(id); state.suppressBlur = true;
        const confirmed = window.confirm(`删除“${displayTitle(note)}”？此操作无法撤销。`); state.suppressBlur = false;
        if (!confirmed) return;
        note.deleting = true; updateState(); renderList();
        try {
            if (note.saving) await note.saving;
            if (!note.isNew) { const result = await requestNotes(`/${id}`, 'DELETE', { expectedRevision: note.revision, operationId: newOperationId() }); if (result.order) state.orderRevision = result.order.revision; }
            for (const task of noteTasks(id)) await removeUpload(task);
            clearTimeout(note.draftTimer); clearTimeout(note.draftMaxTimer); await drafts.remove(state.scope, id).catch(() => {});
            const index = state.order.indexOf(id); state.order = state.order.filter((item) => item !== id); notes.delete(id);
            if (state.selected === id) { state.selected = state.order[Math.min(index, state.order.length - 1)] || null; if (state.selected && !notes.get(state.selected).loaded) await fetchNote(state.selected); }
            rememberSelection(); renderList(); renderDetail(); notify('笔记已删除');
        } catch (error) { if (error.status === 409) note.conflict = true; notify(error.message, true); renderDetail(); }
        finally { note.deleting = false; updateState(); renderList(); }
    }
    function moveNote(id, delta) { const index = state.order.indexOf(id); const target = index + delta; if (target < 0 || target >= state.order.length || !isEditable()) return; [state.order[index], state.order[target]] = [state.order[target], state.order[index]]; scheduleOrder(); }
    function scheduleOrder() { state.orderGeneration++; renderList(); clearTimeout(state.orderTimer); state.orderTimer = setTimeout(commitOrder, 250); }
    async function commitOrder() {
        state.orderTimer = 0; if (state.ordering) { scheduleOrder(); return; }
        state.ordering = true; const generation = state.orderGeneration;
        try { const result = await requestNotes('/order', 'PATCH', { expectedRevision: state.orderRevision, operationId: newOperationId(), ids: state.order.filter((id) => !notes.get(id)?.isNew) }); state.orderRevision = result.order.revision; }
        catch (error) { notify(error.status === 409 ? '其他页面已调整顺序，已重新读取服务器顺序。' : error.message, true); state.ordering = false; clearTimeout(state.orderTimer); state.orderTimer = 0; await refresh(); return; }
        finally { state.ordering = false; }
        if (state.orderGeneration !== generation) scheduleOrder();
    }

    function setMode(mode) { const note = notes.get(state.selected); if (!note) return; note.mode = mode; renderDetail(); }
    function insertFormat(kind, heading = 1) {
        const note = notes.get(state.selected); if (!note || !isEditable()) return;
        const start = ui.body.selectionStart; const end = ui.body.selectionEnd; const selected = note.body.slice(start, end);
        const wraps = { bold: ['**', '**', '粗体文字'], italic: ['*', '*', '斜体文字'], 'inline-code': ['`', '`', '代码'], link: ['[', '](https://example.com)', '链接文字'], code: ['\n```\n', '\n```\n', '代码'] };
        let replacement;
        if (wraps[kind]) { const [before, after, placeholder] = wraps[kind]; replacement = before + (selected || placeholder) + after; }
        else { const prefix = kind === 'heading' ? '#'.repeat(heading) + ' ' : kind === 'task' ? '- [ ] ' : kind === 'ordered' ? '1. ' : '- '; replacement = (start && note.body[start - 1] !== '\n' ? '\n' : '') + (selected || '内容').split('\n').map((line) => prefix + line).join('\n'); }
        const next = note.body.slice(0, start) + replacement + note.body.slice(end); updateBody(note, next); ui.body.value = next; ui.body.focus(); ui.body.setSelectionRange(start, start + replacement.length);
    }

    function createUpload(file, note, offset) {
        if (file.size > MAX_IMAGE_BYTES) { notify(`“${file.name}”超过 20 MiB。`, true, false); return; }
        if (!/\.(jpe?g|png|gif|webp|avif)$/i.test(file.name || '') && !/^image\/(jpeg|png|gif|webp|avif)$/.test(file.type)) { notify('仅支持 JPEG、PNG、GIF、WebP、AVIF 图片。', true); return; }
        let resolve;
        const task = { id: newOperationId(), noteId: note.id, name: file.name || '粘贴图片', file, offset, status: 'queued', progress: 0, promise: new Promise((done) => { resolve = done; }) };
        task.resolve = resolve; uploads.set(task.id, task); markDirty(note); renderUploads(); pumpUploads();
    }
    async function pumpUploads() {
        if (state.uploadRunning) return;
        state.uploadRunning = true;
        try {
            while (true) {
                const task = [...uploads.values()].find((item) => item.status === 'queued'); if (!task) break;
                task.status = 'uploading'; renderUploads(); let result;
                for (let attempt = 0; attempt < 2; attempt++) {
                    try { result = await uploadNoteImage(task, (progress) => { task.progress = progress; renderUploads(); }); break; }
                    catch (error) {
                        if (task.status === 'removed') break;
                        if (attempt === 0 && (!error.status || error.status >= 500)) {
                            try { const received = await requestNotes(`/uploads/${task.id}`); if (received.status === 'uploaded' && received.url) { result = received; break; } } catch { /* Stable upload ID makes the retry idempotent. */ }
                            await sleep(2000); continue;
                        }
                        task.error = error.message; task.status = 'failed'; notify(error.message, true);
                    }
                }
                if (task.status !== 'removed') {
                    if (result) finishUpload(task, result);
                    else task.status = 'failed';
                }
                task.resolve?.(); task.promise = null;
                const note = notes.get(task.noteId); if (note) scheduleDraft(note);
                renderUploads(); updateState();
            }
        } finally { state.uploadRunning = false; }
    }
    function finishUpload(task, result) {
        if (result.status !== 'uploaded' || !result.url) { task.status = 'checking'; task.error = '图片尚未确认，请重新核查。'; return; }
        task.status = 'uploaded'; task.url = result.url; task.progress = 100;
        const note = notes.get(task.noteId); if (!note || task.inserted) return;
        const alt = task.name.replace(/[\[\]\\\r\n]/g, ' ');
        const insertion = `\n![${alt}](${task.url})\n`;
        const offset = Math.min(task.offset, note.body.length);
        task.inserted = true; updateBody(note, note.body.slice(0, offset) + insertion + note.body.slice(offset));
        if (state.selected === note.id) { if (note.mode === 'edit') ui.body.value = note.body; else renderPreview(note); }
    }
    async function recheckUpload(task) {
        task.status = 'checking'; renderUploads();
        try {
            const result = await requestNotes(`/uploads/${task.id}`);
            if (result.status === 'uploaded' && result.url) finishUpload(task, result);
            else if (['missing', 'failed', 'removed', 'deleting'].includes(result.status)) { task.status = 'failed'; task.error = '服务器未保留这张图片，请重新选择或粘贴。'; }
            else { task.status = 'checking'; task.error = '服务器仍在处理，请稍后重新核查。'; }
        } catch (error) {
            if ([404, 410].includes(error.status)) { task.status = 'failed'; task.error = '服务器未收到，请重新选择或粘贴。'; }
            else { task.status = 'checking'; task.error = '连接不可用，暂未确认图片状态。'; }
        }
        const note = notes.get(task.noteId); if (note) scheduleDraft(note); renderUploads(); updateState();
    }
    async function removeUpload(task) {
        const wasCommitted = task.status === 'committed'; task.status = 'removed'; task.abort?.(); task.resolve?.(); task.promise = null;
        if (!wasCommitted) await requestNotes(`/uploads/${task.id}/discard`, 'POST', {}).catch((error) => notify(error.message, true));
        uploads.delete(task.id); const note = notes.get(task.noteId); if (note) scheduleDraft(note); renderUploads(); updateState();
    }
    function renderUploads() {
        ui.uploads.replaceChildren(); const tasks = noteTasks(state.selected).filter((task) => !['committed', 'uploaded'].includes(task.status)); ui.uploads.hidden = !tasks.length;
        for (const task of tasks) {
            const row = document.createElement('div'); row.className = 'notes-upload-item';
            const label = document.createElement('span'); label.textContent = `${task.name} · ${task.status === 'failed' ? task.error || '请重新选择图片' : task.status === 'checking' ? task.error || '正在核查' : task.status === 'queued' ? '等待上传' : `${task.progress}%`}`; row.append(label);
            if (task.status === 'checking') { const check = document.createElement('button'); check.type = 'button'; check.textContent = '重新核查'; check.disabled = state.closing; check.addEventListener('click', () => recheckUpload(task)); row.append(check); }
            if (task.status === 'failed') { const retry = document.createElement('button'); retry.type = 'button'; retry.textContent = task.file ? '重试' : '重新选择'; retry.disabled = !isEditable(); retry.addEventListener('click', () => { if (!task.file) { ui['image-input'].dataset.replace = task.id; ui['image-input'].click(); return; } task.status = 'queued'; task.promise = new Promise((resolve) => { task.resolve = resolve; }); renderUploads(); pumpUploads(); }); row.append(retry); }
            const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '移除'; remove.disabled = state.closing; remove.addEventListener('click', () => removeUpload(task)); row.append(remove); ui.uploads.append(row);
        }
    }

    function makeDialog(id, title, onRequestClose = null) {
        const dialog = document.createElement('dialog'); dialog.id = id; dialog.className = 'notes-dialog';
        const heading = document.createElement('div'); heading.className = 'notes-dialog-heading'; const h = document.createElement('h2'); h.id = `${id}-title`; h.textContent = title; dialog.setAttribute('aria-labelledby', h.id);
        const close = document.createElement('button'); close.type = 'button'; close.className = 'notes-icon-button'; close.textContent = '×'; close.setAttribute('aria-label', '关闭'); close.addEventListener('click', () => onRequestClose ? onRequestClose() : dialog.close()); heading.append(h, close);
        const content = document.createElement('div'); content.className = 'notes-dialog-content'; dialog.append(heading, content); document.body.append(dialog); return { dialog, content };
    }
    async function resolveCopiedConflict(copy) {
        const old = notes.get(copy.resolvesConflictId);
        delete copy.resolvesConflictId;
        if (!old || old.generation !== copy.resolvesConflictGeneration) return;
        old.resolving = true; updateState();
        clearTimeout(old.draftTimer); clearTimeout(old.draftMaxTimer); old.dirty = false;
        await drafts.remove(state.scope, old.id).catch(() => {});
        try { await fetchNote(old.id); }
        catch (error) { if ([404, 410].includes(error.status)) { notes.delete(old.id); state.order = state.order.filter((item) => item !== old.id); } else { old.loaded = false; old.body = ''; old.conflict = false; } }
        finally { old.resolving = false; updateState(); }
    }
    async function offerDraftRecovery() {
        await motion.settled();
        if (!state.open) { state.recoveryChecked = false; return; }
        try {
            channel?.postMessage({ type: 'query', tabId }); await sleep(300);
            const recoverable = (await drafts.list(state.scope)).filter((draft) => draft.tabId !== tabId && (!activeTabs.has(draft.tabId) || Date.now() - activeTabs.get(draft.tabId) > 30000));
            if (!recoverable.length) return;
            const { dialog, content } = makeDialog('notes-recovery-dialog', '发现本机恢复草稿');
            const intro = document.createElement('p'); intro.textContent = '这些内容尚未确认同步到服务器。恢复后仍需保存；关闭此窗口会保留草稿供下次处理。'; content.append(intro);
            for (const draft of recoverable) {
                const row = document.createElement('div'); row.className = 'notes-recovery-item'; const title = document.createElement('strong'); title.textContent = draft.title?.trim() || '未命名笔记';
                const time = document.createElement('small'); time.textContent = new Date(draft.savedAt).toLocaleString();
                const preview = document.createElement('p'); preview.textContent = draft.body.slice(0, 180) || '（空正文）';
                const restore = document.createElement('button'); restore.type = 'button'; restore.textContent = state.available ? '恢复草稿' : '查看草稿';
                restore.addEventListener('click', async () => {
                    restore.disabled = true;
                    let remote = notes.get(draft.id);
                    if (remote && !remote.loaded) { try { remote = await fetchNote(draft.id); } catch { /* Draft can still be viewed in read-only state. */ } }
                    if (remote?.dirty) { notify('当前窗口已有这篇笔记的未保存内容，请先处理后再恢复。', true, false); restore.disabled = false; return; }
                    const note = { ...draft, mode: 'edit', loaded: true, dirty: true, dirtySince: Date.now(), generation: 1, isNew: !remote && draft.isNew, unverifiedRecovery: !state.available, conflict: Boolean((remote && remote.revision !== draft.revision) || (!remote && !draft.isNew)) };
                    notes.set(note.id, note); if (!state.order.includes(note.id)) state.order.unshift(note.id); state.selected = note.id; state.dirtySince ||= Date.now(); shell.dataset.view = 'detail';
                    for (const oldTask of draft.uploads || []) {
                        const task = { ...oldTask, noteId: note.id, status: 'checking', progress: 0 }; uploads.set(task.id, task);
                        await recheckUpload(task);
                    }
                    if (await flushDraft(note)) await drafts.removeKey(draft.key);
                    row.remove(); renderList(); renderDetail(); if (!content.querySelector('.notes-recovery-item')) dialog.close();
                });
                const discard = document.createElement('button'); discard.type = 'button'; discard.textContent = '丢弃草稿'; discard.addEventListener('click', async () => { await drafts.removeKey(draft.key); row.remove(); if (!content.querySelector('.notes-recovery-item')) dialog.close(); });
                row.append(title, time, preview, restore, discard); content.append(row);
            }
            dialog.addEventListener('close', () => dialog.remove(), { once: true }); dialog.showModal();
        } catch { notify('本机恢复草稿不可用，请及时保存到服务器。', true); }
    }

    const settingsUI = makeDialog('notes-settings-dialog', '笔记设置', closeSettings);
    settingsUI.dialog.classList.add('notes-settings-dialog');
    const settingsMotion = createDialogMotionController(settingsUI.dialog, 300);
    let settingsReturnFocus = null;
    function closeSettings() {
        const target = settingsReturnFocus;
        return settingsMotion.hide('', () => { if (target?.isConnected) target.focus({ preventScroll: true }); });
    }
    settingsUI.dialog.addEventListener('cancel', (event) => { event.preventDefault(); void closeSettings(); });
    settingsUI.content.innerHTML = `<form id="notes-settings-form"><fieldset><legend>保存与交互</legend><label class="notes-setting-check"><input type="checkbox" name="autoSaveEnabled"> 定时自动保存</label><label class="notes-setting-number">保存间隔（秒）<input type="number" name="autoSaveSeconds" min="10" max="3600" step="1" required></label><label class="notes-setting-check"><input type="checkbox" name="saveOnSwitch"> 切换笔记时保存全部修改</label><label class="notes-setting-check"><input type="checkbox" name="collapseOnBlur"> 点击或聚焦面板外部时自动收起</label><p class="notes-helper">偏好在设备间共享。切换应用或浏览器标签页不会自动收起。</p></fieldset><fieldset><legend>存储与备份</legend><dl class="notes-storage-paths"><dt>主数据目录</dt><dd id="notes-primary-path">读取中…</dd><dt>图片与备份目录</dt><dd id="notes-backup-path">读取中…</dd></dl><p id="notes-backup-status" role="status"></p><p class="notes-helper">目录通过后端配置向导修改，重启后生效。</p><div class="notes-dialog-actions"><button type="button" id="notes-backup-retry">立即重试备份</button><button type="button" id="notes-backup-check">检查／恢复备份</button></div></fieldset><div class="notes-dialog-actions"><span id="notes-settings-feedback" role="status"></span><button type="submit" class="notes-primary">保存设置</button></div></form>`;
    const settingsForm = byId('notes-settings-form');
    async function openSettings(event) {
        if (settingsMotion.isOpen()) return;
        const trigger = event?.currentTarget || document.activeElement;
        settingsReturnFocus = trigger === byId('notes-settings-button') ? byId('page-settings-button') : trigger;
        settingsMotion.show();
        try {
            const [settings, status] = await Promise.allSettled([requestNotes('/settings'), requestNotes('/status')]);
            if (status.status === 'fulfilled') state.status = status.value;
            if (settings.status === 'fulfilled') state.settings = { ...DEFAULT_SETTINGS, ...settings.value.settings };
            populateSettings();
            settingsForm.querySelector('[type="submit"]').disabled = state.status.primaryAvailable === false || !state.status.enabled;
            if (settings.status === 'rejected') byId('notes-settings-feedback').textContent = settings.reason.message;
        }
        catch (error) { byId('notes-settings-feedback').textContent = error.message; }
    }
    function populateSettings() {
        for (const key of ['autoSaveEnabled', 'saveOnSwitch', 'collapseOnBlur']) settingsForm.elements[key].checked = state.settings[key];
        settingsForm.elements.autoSaveSeconds.value = state.settings.autoSaveSeconds;
        byId('notes-primary-path').textContent = state.status.paths?.primaryDir || '尚未配置'; byId('notes-backup-path').textContent = state.status.paths?.backupDir || '尚未配置';
        byId('notes-backup-status').textContent = state.status.backupError || (state.status.backupPending ? `${state.status.backupPending} 项备份待同步` : state.status.backupAvailable ? '备份目录可用' : '备份目录暂不可用');
        byId('notes-settings-feedback').textContent = '';
    }
    settingsForm.addEventListener('submit', async (event) => {
        event.preventDefault(); const button = settingsForm.querySelector('[type="submit"]'); button.disabled = true;
        try {
            const result = await requestNotes('/settings', 'PATCH', { expectedRevision: state.settings.revision, operationId: newOperationId(), autoSaveEnabled: settingsForm.elements.autoSaveEnabled.checked, autoSaveSeconds: Number(settingsForm.elements.autoSaveSeconds.value), saveOnSwitch: settingsForm.elements.saveOnSwitch.checked, collapseOnBlur: settingsForm.elements.collapseOnBlur.checked });
            state.settings = result.settings; byId('notes-settings-feedback').textContent = '设置已保存'; updateState();
        } catch (error) { byId('notes-settings-feedback').textContent = error.status === 409 ? '设置已在其他页面修改，请关闭后重新打开设置。' : error.message; }
        finally { button.disabled = false; }
    });
    byId('notes-backup-retry').addEventListener('click', async () => { try { await requestNotes('/backup/retry', 'POST', {}); byId('notes-backup-status').textContent = '已请求立即重试备份'; } catch (error) { byId('notes-backup-status').textContent = error.message; } });
    async function waitJob(jobId, onStatus) {
        for (;;) { const { job } = await requestNotes(`/jobs/${jobId}`); if (job.status === 'complete') return job.result; if (job.status === 'failed') throw new Error(job.message || '备份操作失败'); onStatus?.(job); await sleep(1000); }
    }
    byId('notes-backup-check').addEventListener('click', async () => {
        const button = byId('notes-backup-check'); button.disabled = true;
        const { dialog, content } = makeDialog('notes-reconcile-dialog', '检查／恢复备份'); const message = document.createElement('p'); message.textContent = '正在只读检查两处存储…'; message.setAttribute('role', 'status'); content.append(message); dialog.addEventListener('close', () => dialog.remove(), { once: true }); dialog.showModal();
        try {
            const check = await requestNotes('/reconcile/check', 'POST', {}); const result = await waitJob(check.job.id);
            const choices = [];
            message.textContent = result.items.length ? '仅执行所选操作。使用备份会替换对应主数据；删除记录不会自动恢复。' : '两处内容一致，无需恢复。';
            for (const item of result.items) {
                const row = document.createElement('label'); row.className = 'notes-reconcile-item'; const title = document.createElement('span'); title.textContent = `${item.title || item.id} · ${{ 'only-primary': '仅主目录存在', 'only-backup': '仅备份存在', different: '内容不同', deleted: '包含删除记录', corrupt: '内容损坏' }[item.type] || item.type}`;
                const select = document.createElement('select'); for (const [value, label] of [['', '保持不变'], ['use-primary', '使用主目录内容'], ['use-backup', '使用备份内容']]) { const option = document.createElement('option'); option.value = value; option.textContent = label; option.disabled = value === 'use-primary' ? Boolean(item.primaryCorrupt) : value === 'use-backup' && Boolean(item.backupCorrupt); select.append(option); }
                choices.push({ item, select }); row.append(title, select); content.append(row);
            }
            if (choices.length) {
                const apply = document.createElement('button'); apply.type = 'button'; apply.className = 'notes-primary'; apply.textContent = '应用所选操作'; content.append(apply);
                apply.addEventListener('click', async () => {
                    const selected = choices.filter(({ select }) => select.value).map(({ item, select }) => ({ id: item.id, action: select.value })); if (!selected.length) { message.textContent = '请先选择需要应用的操作。'; return; }
                    apply.disabled = true; choices.forEach(({ select }) => { select.disabled = true; });
                    try { const response = await requestNotes('/reconcile/apply', 'POST', { checkId: check.job.id, choices: selected }); const outcome = await waitJob(response.job.id); message.textContent = outcome.items.map((item) => `${item.id}：${item.status === 'applied' ? '已完成' : item.message || item.status}`).join('；'); await refresh(); }
                    catch (error) { message.textContent = error.message; }
                });
            }
        } catch (error) { message.textContent = error.message; }
        finally { button.disabled = false; }
    });

    ui.open.addEventListener('click', openPanel); ui.close.addEventListener('click', () => closePanel()); ui.new.addEventListener('click', newNote); ui.save.addEventListener('click', () => saveAll());
    ui['keep-draft'].addEventListener('click', async () => { if (await flushAllDrafts()) { await collapse(); notify('草稿保留在本机，尚未同步到服务器。'); } });
    byId('notes-settings-button').addEventListener('click', openSettings); byId('notes-settings-inline').addEventListener('click', openSettings);
    byId('notes-back').addEventListener('click', () => { shell.dataset.view = 'list'; if (state.settings.saveOnSwitch) saveAll('switch'); });
    ui['mode-edit'].addEventListener('click', () => setMode('edit')); ui['mode-preview'].addEventListener('click', () => setMode('preview'));
    ui.title.addEventListener('input', () => { const note = notes.get(state.selected); if (!note || !isEditable()) return; note.title = ui.title.value; markDirty(note); });
    ui.body.addEventListener('input', () => { const note = notes.get(state.selected); if (note && isEditable()) updateBody(note, ui.body.value); });
    ui['format-toolbar'].addEventListener('pointerdown', (event) => { if (event.target.closest('button')) event.preventDefault(); });
    ui['format-toolbar'].addEventListener('click', (event) => { const button = event.target.closest('[data-format]'); if (button) insertFormat(button.dataset.format); });
    byId('notes-heading-format').addEventListener('change', (event) => { if (event.target.value) insertFormat('heading', Number(event.target.value)); event.target.value = ''; });
    byId('notes-image').addEventListener('click', () => { if (!isEditable()) return; ui['image-input'].dataset.noteId = state.selected; ui['image-input'].dataset.offset = ui.body.selectionStart; state.suppressBlur = true; ui['image-input'].click(); setTimeout(() => { state.suppressBlur = false; }, 1000); });
    ui['image-input'].addEventListener('change', async () => {
        const replacement = uploads.get(ui['image-input'].dataset.replace); const note = notes.get(replacement?.noteId || ui['image-input'].dataset.noteId || state.selected);
        if (note && isEditable()) { const offset = replacement?.offset ?? Number(ui['image-input'].dataset.offset || ui.body.selectionStart); if (replacement) await removeUpload(replacement); for (const file of ui['image-input'].files) createUpload(file, note, offset); }
        ui['image-input'].value = ''; delete ui['image-input'].dataset.replace; state.suppressBlur = false;
    });
    ui.body.addEventListener('paste', (event) => {
        const files = [...(event.clipboardData?.items || [])].filter((item) => item.kind === 'file' && item.type.startsWith('image/')).map((item) => item.getAsFile()).filter(Boolean);
        if (files.length && isEditable()) { event.preventDefault(); const note = notes.get(state.selected); for (const file of files) createUpload(file, note, ui.body.selectionStart); }
    });
    byId('notes-conflict-copy').addEventListener('click', async () => {
        const old = notes.get(state.selected); if (!old || !isEditable()) return;
        const id = newOperationId(); const copy = { id, title: old.title, body: old.body, revision: null, isNew: true, dirty: false, generation: 0, loaded: true, mode: 'edit', resolvesConflictId: old.id, resolvesConflictGeneration: old.generation };
        notes.set(id, copy); state.order.unshift(id); state.selected = id;
        for (const task of noteTasks(old.id)) task.noteId = id;
        markDirty(copy); renderList(); renderDetail();
        if (await saveNote(copy)) {
            renderList(); renderDetail(); notify(old.dirty ? '草稿已另存为新笔记，原笔记后续输入仍保留。' : '草稿已另存为新笔记，原笔记已重新读取。');
        } else notify('新笔记草稿已保留，保存尚未完成。', true, false);
    });
    byId('notes-conflict-reload').addEventListener('click', async () => {
        const note = notes.get(state.selected); if (!note || !isEditable()) return;
        if (!window.confirm('放弃这篇笔记的本机修改，并读取服务器内容？')) return;
        const discardGeneration = note.generation;
        note.resolving = true; updateState();
        try {
            const result = await requestNotes(`/${note.id}`);
            if (note.generation !== discardGeneration) { notify('读取期间有新的输入，已保留本机修改，请重新选择处理方式。', true, false); return; }
            clearTimeout(note.draftTimer); clearTimeout(note.draftMaxTimer);
            for (const task of noteTasks(note.id)) { if (result.note.attachments?.includes(task.id)) task.status = 'committed'; else await removeUpload(task); }
            notes.set(note.id, { ...result.note, dirty: false, loaded: true, mode: 'preview', generation: 0 }); await drafts.remove(state.scope, note.id); renderList(); renderDetail();
        }
        catch (error) { if ([404, 410].includes(error.status) && note.generation === discardGeneration) { clearTimeout(note.draftTimer); clearTimeout(note.draftMaxTimer); note.dirty = false; for (const task of noteTasks(note.id)) await removeUpload(task); await drafts.remove(state.scope, note.id); notes.delete(note.id); state.order = state.order.filter((id) => id !== note.id); if (state.selected === note.id) state.selected = state.order[0] || null; await refresh(); } else notify(error.message, true); }
        finally { note.resolving = false; updateState(); }
    });
    shell.addEventListener('keydown', (event) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); saveAll(); } });
    document.addEventListener('pointerdown', (event) => { pointerOutside = !shell.contains(event.target); }, true);
    document.addEventListener('click', (event) => { if (state.open && state.settings.collapseOnBlur && !shell.contains(event.target) && !event.target.closest('dialog') && !document.querySelector('.notes-dialog[open]') && !state.suppressBlur) setTimeout(() => closePanel(true), 0); }, true);
    shell.addEventListener('focusout', (event) => {
        if (!event.relatedTarget || pointerOutside || state.suppressBlur) return;
        setTimeout(() => { if (state.open && state.settings.collapseOnBlur && document.hasFocus() && !shell.contains(document.activeElement) && !document.querySelector('.notes-dialog[open]')) closePanel(true); }, 0);
    });
    document.addEventListener('pointerup', () => { setTimeout(() => { pointerOutside = false; }, 0); }, true);
    document.addEventListener('visibilitychange', () => { if (document.hidden) flushAllDrafts(); else if (state.loaded) refresh(); });
    window.addEventListener('focus', () => { if (state.loaded && !document.hidden) refresh(); });
    window.addEventListener('beforeunload', (event) => { if (hasPending()) { flushAllDrafts(); event.preventDefault(); event.returnValue = ''; } });
    window.addEventListener('pagehide', () => { channel?.postMessage({ type: 'bye', tabId }); flushAllDrafts(); });
    setInterval(async () => {
        updateState();
        if (document.hidden) return;
        if (state.available && state.settings.autoSaveEnabled && state.dirtySince && dirtyNotes().some(canAutoSave) && Date.now() >= state.dirtySince + state.settings.autoSaveSeconds * 1000 && !state.saving) saveAll('auto');
        if (state.loaded && !state.available && Date.now() - state.lastRecoveryTry >= 15000) { state.lastRecoveryTry = Date.now(); refresh(); }
        if (state.open && state.available && (state.status.backupPending || [...uploads.values()].some((task) => task.status === 'uploading')) && Date.now() - state.lastStatusRead >= 5000) {
            state.lastStatusRead = Date.now(); try { state.status = await requestNotes('/status'); if (state.status.primaryAvailable === false) { state.available = false; renderDetail(); renderList(); } } catch { state.available = false; updateState(); }
        }
    }, 1000);
    setInterval(() => { for (const task of uploads.values()) if (task.status === 'uploaded') requestNotes(`/uploads/${task.id}/touch`, 'POST', {}).catch(() => {}); }, 30000);
    updateState();
    return { open: openPanel, refresh, saveAll };
}
