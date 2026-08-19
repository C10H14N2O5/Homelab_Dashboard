import { createSafeSvgElement, sanitizeSvgMarkup, cardSvgLimits } from './card-svg.js';

const BLANK_CARD_ID = '__new_card__';
const MAX_CARDS = 32;
const CARD_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/;
const CONTROL_CHARACTER_PATTERN = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/;

function copy(value) {
    return JSON.parse(JSON.stringify(value));
}

function validationError(message, field) {
    const error = new Error(message);
    error.name = 'CardValidationError';
    error.field = field;
    return error;
}

function validateText(value, label, maximum, field) {
    const normalized = typeof value === 'string' ? value.trim() : '';
    if (!normalized) throw validationError(`请填写${label}。`, field);
    if (normalized.length > maximum || CONTROL_CHARACTER_PATTERN.test(normalized)) {
        throw validationError(`${label}不符合长度或字符限制。`, field);
    }
    return normalized;
}

function validateUrl(value, label, field, optional = false) {
    const normalized = typeof value === 'string' ? value.trim() : '';
    if (optional && normalized === '') return null;
    if (!normalized || normalized.length > 2048 || CONTROL_CHARACTER_PATTERN.test(normalized)) {
        throw validationError(`请填写有效的${label}。`, field);
    }
    let url;
    try {
        url = new URL(normalized);
    } catch (error) {
        throw validationError(`${label}不是有效网址。`, field);
    }
    if (url.protocol !== 'https:' || url.username !== '' || url.password !== '') {
        throw validationError(`${label}必须是不含账号信息的 HTTPS 链接。`, field);
    }
    return url.href;
}

function normalizeDraftCard(card, channel) {
    if (!card || typeof card !== 'object' || !CARD_ID_PATTERN.test(card.id || '')) {
        throw validationError('卡片 ID 无效，请重新载入服务器版本。', 'name');
    }
    let iconSvg;
    try {
        iconSvg = sanitizeSvgMarkup(card.iconSvg);
    } catch (error) {
        throw validationError(error.message || 'SVG 图标无效。', 'icon');
    }
    const normalized = {
        id: card.id,
        iconSvg,
        name: validateText(card.name, '显示名称', 80, 'name'),
        url: validateUrl(card.url, '跳转链接', 'url')
    };
    if (channel === 'services') {
        normalized.description = validateText(card.description, '描述 / 副标题', 160, 'description');
        normalized.probeUrl = validateUrl(card.probeUrl, '健康探测链接', 'probeUrl', true);
    }
    return normalized;
}

function makeBlankCard() {
    return { id: null, iconSvg: '', name: '', description: '', url: '', probeUrl: '' };
}

function blankHasContent(card) {
    return ['iconSvg', 'name', 'description', 'url', 'probeUrl'].some((key) => (
        typeof card[key] === 'string' && card[key].trim() !== ''
    ));
}

function generateCardId() {
    if (typeof globalThis.crypto?.randomUUID === 'function') return `card-${globalThis.crypto.randomUUID()}`;
    const random = Math.random().toString(36).slice(2, 12);
    return `card-${Date.now().toString(36)}-${random}`;
}

function cardsEqual(left, right) {
    return JSON.stringify(left) === JSON.stringify(right);
}

function buildChangeSummary(original, draft) {
    const originalById = new Map(original.map((card) => [card.id, card]));
    const draftById = new Map(draft.map((card) => [card.id, card]));
    const added = draft.filter((card) => !originalById.has(card.id)).length;
    const removed = original.filter((card) => !draftById.has(card.id)).length;
    const changed = draft.filter((card) => {
        const previous = originalById.get(card.id);
        return previous && !cardsEqual(previous, card);
    }).length;
    const originalOrder = original.filter((card) => draftById.has(card.id)).map((card) => card.id);
    const draftOrder = draft.filter((card) => originalById.has(card.id)).map((card) => card.id);
    const reordered = !cardsEqual(originalOrder, draftOrder);
    const parts = [`新增 ${added} 张`, `修改 ${changed} 张`, `删除 ${removed} 张`];
    if (reordered) parts.push('顺序已调整');
    return parts.join('，');
}

export function moveCardToTargetPosition(cards, sourceId, targetId) {
    if (!Array.isArray(cards) || sourceId === targetId) return false;
    const sourceIndex = cards.findIndex((card) => card.id === sourceId);
    const targetIndex = cards.findIndex((card) => card.id === targetId);
    if (sourceIndex < 0 || targetIndex < 0) return false;
    const [source] = cards.splice(sourceIndex, 1);
    cards.splice(targetIndex, 0, source);
    return true;
}

function serverValidationFeedback(error) {
    const detail = typeof error?.payload?.error === 'string' ? error.payload.error : '';
    const cardId = typeof error?.payload?.cardId === 'string'
        && CARD_ID_PATTERN.test(error.payload.cardId)
        ? error.payload.cardId
        : null;
    const contextualize = (feedback) => ({ ...feedback, cardId });
    if (/probe/i.test(detail)) {
        return contextualize({
            field: 'probeUrl',
            message: '健康探测目标必须是配置域名后缀下的单层 HTTPS 子域，并使用 443 端口；留空时会检查跳转链接。'
        });
    }
    if (/iconsvg|svg/i.test(detail)) {
        return contextualize({ field: 'icon', message: '服务器拒绝了 SVG 图标；请确认图标不含脚本、外部资源、动画或不支持的元素。' });
    }
    if (/description/i.test(detail)) {
        return contextualize({ field: 'description', message: '服务器拒绝了描述字段；请检查内容是否非空、已去除首尾空格且不超过 160 个字符。' });
    }
    if (/\bname\b/i.test(detail)) {
        return contextualize({ field: 'name', message: '服务器拒绝了显示名称；请检查内容是否非空、已去除首尾空格且不超过 80 个字符。' });
    }
    if (/\burl\b/i.test(detail)) {
        return contextualize({ field: 'url', message: '服务器拒绝了跳转链接；请使用不含账号信息、空格或反斜杠的有效 HTTPS 链接。' });
    }
    if (/card id/i.test(detail)) {
        return contextualize({ field: 'name', message: '卡片标识不符合服务器要求，请重新载入服务器版本后再编辑。' });
    }
    if (/at most 32|containing at most|cards must be an array/i.test(detail)) {
        return contextualize({ field: 'name', message: '当前频道的卡片数量超过服务器允许的 32 张上限。' });
    }
    return contextualize({ field: 'name', message: '服务器拒绝了卡片字段；请检查必填项、链接和 SVG 图标后重试。' });
}

export function initializeCardEditor(manager) {
    const launchButton = document.getElementById('card-editor-button');
    const dialog = document.getElementById('card-editor-dialog');
    const closeButton = document.getElementById('card-editor-close');
    const form = document.getElementById('card-editor-form');
    const strip = document.getElementById('card-editor-strip');
    const sortHint = document.getElementById('card-editor-sort-hint');
    const selectionLabel = document.getElementById('card-editor-selection-label');
    const status = document.getElementById('card-editor-status');
    const iconPreview = document.getElementById('card-icon-preview');
    const iconSelect = document.getElementById('card-icon-select');
    const iconInput = document.getElementById('card-icon-input');
    const nameInput = document.getElementById('card-name-input');
    const descriptionField = document.getElementById('card-description-field');
    const descriptionInput = document.getElementById('card-description-input');
    const urlInput = document.getElementById('card-url-input');
    const probeField = document.getElementById('card-probe-field');
    const probeInput = document.getElementById('card-probe-input');
    const deleteButton = document.getElementById('card-delete-button');
    const saveButton = document.getElementById('card-save-button');
    const confirmDialog = document.getElementById('card-confirm-dialog');
    const confirmTitle = document.getElementById('card-confirm-title');
    const confirmDescription = document.getElementById('card-confirm-description');
    const confirmCancel = document.getElementById('card-confirm-cancel');
    const confirmAccept = document.getElementById('card-confirm-accept');
    const dashboard = document.getElementById('dashboard');
    const channelSwitch = document.getElementById('channel-switch');
    const required = [
        launchButton, dialog, closeButton, form, strip, sortHint, selectionLabel, status,
        iconPreview, iconSelect, iconInput, nameInput, descriptionField, descriptionInput,
        urlInput, probeField, probeInput, deleteButton, saveButton, confirmDialog,
        confirmTitle, confirmDescription, confirmCancel, confirmAccept, dashboard, channelSwitch
    ];
    if (required.some((element) => !element)) throw new Error('卡片编辑器页面结构不完整。');

    let channel = 'services';
    let revision = 0;
    let originalCards = [];
    let draftCards = [];
    let blankCard = makeBlankCard();
    let selectedId = BLANK_CARD_ID;
    let busy = false;
    let draggedId = null;
    let dirty = false;
    let confirmResolver = null;

    function setStatus(message, kind = '') {
        status.textContent = message;
        if (kind) status.dataset.kind = kind;
        else delete status.dataset.kind;
    }

    function canSortWithMouse() {
        return window.matchMedia('(min-width: 768px) and (hover: hover) and (pointer: fine)').matches;
    }

    function currentCard() {
        if (selectedId === BLANK_CARD_ID) return blankCard;
        return draftCards.find((card) => card.id === selectedId) || null;
    }

    function computeDirty() {
        dirty = !cardsEqual(originalCards, draftCards) || blankHasContent(blankCard);
        dialog.dataset.dirty = dirty ? 'true' : 'false';
        return dirty;
    }

    function clearInvalidFields() {
        [nameInput, descriptionInput, urlInput, probeInput].forEach((input) => input.removeAttribute('aria-invalid'));
        iconSelect.removeAttribute('aria-invalid');
    }

    function focusValidationField(field) {
        const fields = {
            icon: iconSelect,
            name: nameInput,
            description: descriptionInput,
            url: urlInput,
            probeUrl: probeInput
        };
        const target = fields[field] || nameInput;
        target.setAttribute('aria-invalid', 'true');
        target.focus({ preventScroll: false });
    }

    function renderPreview(card) {
        iconPreview.replaceChildren();
        if (!card?.iconSvg) return;
        try {
            iconPreview.append(createSafeSvgElement(card.iconSvg));
        } catch (error) {
            setStatus(error.message || 'SVG 图标无效。', 'error');
        }
    }

    function renderSelectionFields() {
        const card = currentCard();
        const isBlank = selectedId === BLANK_CARD_ID;
        if (!card) return;
        clearInvalidFields();
        nameInput.value = card.name || '';
        descriptionInput.value = card.description || '';
        urlInput.value = card.url || '';
        probeInput.value = card.probeUrl || '';
        const isService = channel === 'services';
        descriptionField.hidden = !isService;
        descriptionInput.disabled = !isService;
        descriptionInput.required = isService;
        probeField.hidden = !isService;
        probeInput.disabled = !isService;
        deleteButton.disabled = isBlank || busy;
        selectionLabel.textContent = isBlank
            ? (draftCards.length >= MAX_CARDS ? '已达到 32 张上限。' : '正在填写一张新卡片。')
            : `正在编辑：${card.name || '未命名卡片'}`;
        renderPreview(card);
    }

    function createStripIcon(card) {
        const icon = document.createElement('span');
        icon.className = 'card-editor-option-icon';
        icon.setAttribute('aria-hidden', 'true');
        if (card.iconSvg) {
            try {
                icon.append(createSafeSvgElement(card.iconSvg));
            } catch (error) {
                icon.textContent = '?';
            }
        }
        return icon;
    }

    function focusSelectedStripOption() {
        const selectedItem = selectedId === BLANK_CARD_ID
            ? strip.querySelector('[data-add-card="true"]')
            : Array.from(strip.querySelectorAll('.card-editor-option')).find((item) => (
                item.dataset.cardId === selectedId
            ));
        selectedItem?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        selectedItem?.querySelector('.card-editor-option-select')?.focus({ preventScroll: true });
    }

    function selectCard(id) {
        if (id === BLANK_CARD_ID && draftCards.length >= MAX_CARDS) {
            setStatus('当前频道已达到 32 张卡片上限。', 'error');
            return;
        }
        selectedId = id;
        renderStrip();
        renderSelectionFields();
        setStatus(id === BLANK_CARD_ID ? '填写所有必填字段后会自动生成新的末尾空白卡。' : '所有修改会在本页暂存。');
        focusSelectedStripOption();
    }

    function clearDropTargets() {
        strip.querySelectorAll('.is-drop-target').forEach((item) => item.classList.remove('is-drop-target'));
    }

    function finishDrag() {
        draggedId = null;
        clearDropTargets();
        strip.querySelectorAll('.card-editor-option').forEach((item) => {
            item.draggable = false;
            item.classList.remove('is-dragging');
        });
    }

    function renderStrip() {
        const fragment = document.createDocumentFragment();
        draftCards.forEach((card) => {
            const item = document.createElement('div');
            item.className = 'card-editor-option';
            item.dataset.cardId = card.id;
            item.setAttribute('role', 'option');
            item.setAttribute('aria-selected', String(selectedId === card.id));
            item.draggable = false;

            const handle = document.createElement('button');
            handle.className = 'card-editor-drag-handle';
            handle.type = 'button';
            handle.setAttribute('aria-label', `拖动 ${card.name || '未命名卡片'} 调整顺序`);
            handle.title = '按住并拖到目标卡片上以占用其位置';
            handle.addEventListener('pointerdown', (event) => {
                if (event.pointerType !== 'mouse' || !canSortWithMouse() || busy) return;
                item.draggable = true;
            });
            handle.addEventListener('pointerup', () => {
                if (!draggedId) item.draggable = false;
            });

            const select = document.createElement('button');
            select.className = 'card-editor-option-select';
            select.type = 'button';
            select.append(createStripIcon(card));
            const name = document.createElement('span');
            name.className = 'card-editor-option-name';
            name.textContent = card.name || '未命名卡片';
            select.append(name);
            select.addEventListener('click', () => selectCard(card.id));

            item.append(handle, select);
            item.addEventListener('dragstart', (event) => {
                if (!item.draggable || !canSortWithMouse()) {
                    event.preventDefault();
                    return;
                }
                draggedId = card.id;
                selectedId = card.id;
                item.classList.add('is-dragging');
                event.dataTransfer.effectAllowed = 'move';
                event.dataTransfer.setData('text/plain', card.id);
            });
            item.addEventListener('dragover', (event) => {
                if (!draggedId || draggedId === card.id) return;
                event.preventDefault();
                event.dataTransfer.dropEffect = 'move';
                clearDropTargets();
                item.classList.add('is-drop-target');
            });
            item.addEventListener('drop', (event) => {
                if (!draggedId || draggedId === card.id) return;
                event.preventDefault();
                const sourceId = draggedId;
                if (!moveCardToTargetPosition(draftCards, sourceId, card.id)) return;
                selectedId = sourceId;
                computeDirty();
                finishDrag();
                renderStrip();
                renderSelectionFields();
                setStatus('排序已暂存，保存后才会同步到服务器。');
            });
            item.addEventListener('dragend', finishDrag);
            fragment.append(item);
        });

        const addItem = document.createElement('div');
        addItem.className = 'card-editor-option';
        addItem.dataset.addCard = 'true';
        addItem.setAttribute('role', 'option');
        addItem.setAttribute('aria-selected', String(selectedId === BLANK_CARD_ID));
        const addSelect = document.createElement('button');
        addSelect.className = 'card-editor-option-select';
        addSelect.type = 'button';
        addSelect.disabled = draftCards.length >= MAX_CARDS;
        addSelect.textContent = draftCards.length >= MAX_CARDS ? '已达 32 张上限' : '＋ 新增卡片';
        addSelect.addEventListener('click', () => selectCard(BLANK_CARD_ID));
        addItem.append(addSelect);
        fragment.append(addItem);
        strip.replaceChildren(fragment);
    }

    function promoteBlankIfComplete() {
        if (selectedId !== BLANK_CARD_ID || draftCards.length >= MAX_CARDS) return;
        const candidate = { ...blankCard, id: generateCardId() };
        let normalized;
        try {
            normalized = normalizeDraftCard(candidate, channel);
        } catch (error) {
            return;
        }
        draftCards.push(normalized);
        blankCard = makeBlankCard();
        selectedId = normalized.id;
        computeDirty();
        renderStrip();
        renderSelectionFields();
        setStatus('新卡片已加入草稿，末尾已补充新的空白卡。', 'success');
    }

    function updateSelectedField(field, value) {
        const card = currentCard();
        if (!card || busy) return;
        card[field] = value;
        const inputByField = { name: nameInput, description: descriptionInput, url: urlInput, probeUrl: probeInput };
        inputByField[field]?.removeAttribute('aria-invalid');
        if (selectedId !== BLANK_CARD_ID) {
            selectionLabel.textContent = `正在编辑：${card.name.trim() || '未命名卡片'}`;
            if (field === 'name') renderStrip();
        }
        computeDirty();
        promoteBlankIfComplete();
    }

    function settleConfirmation(result) {
        if (!confirmResolver) return;
        const resolve = confirmResolver;
        confirmResolver = null;
        confirmDialog.close();
        resolve(result);
    }

    function askConfirmation(title, description, confirmLabel, destructive = false) {
        if (confirmResolver) settleConfirmation(false);
        confirmTitle.textContent = title;
        confirmDescription.textContent = description;
        confirmAccept.textContent = confirmLabel;
        if (destructive) confirmAccept.dataset.kind = 'danger';
        else delete confirmAccept.dataset.kind;
        confirmDialog.showModal();
        confirmCancel.focus({ preventScroll: true });
        return new Promise((resolve) => {
            confirmResolver = resolve;
        });
    }

    async function requestClose() {
        if (busy) return;
        if (computeDirty()) {
            const discard = await askConfirmation(
                '放弃未保存的更改？',
                '本次增加、修改、删除和排序草稿都会被丢弃。',
                '放弃更改',
                true
            );
            if (!discard) return;
        }
        dirty = false;
        dialog.close();
        launchButton.focus({ preventScroll: true });
    }

    function setBusy(nextBusy) {
        busy = nextBusy;
        saveButton.disabled = nextBusy;
        iconSelect.disabled = nextBusy;
        [nameInput, descriptionInput, urlInput, probeInput].forEach((input) => {
            input.disabled = nextBusy || (input === descriptionInput && channel !== 'services')
                || (input === probeInput && channel !== 'services');
        });
        deleteButton.disabled = nextBusy || selectedId === BLANK_CARD_ID;
        form.setAttribute('aria-busy', String(nextBusy));
    }

    function loadChannelFromState(state, requestedChannel) {
        channel = requestedChannel;
        const channelState = state.channels[channel];
        revision = channelState.revision;
        originalCards = copy(channelState.cards);
        draftCards = copy(channelState.cards);
        blankCard = makeBlankCard();
        selectedId = draftCards[0]?.id || BLANK_CARD_ID;
        dirty = false;
        dialog.dataset.cardChannel = channel;
        document.getElementById('card-editor-title').textContent = channel === 'services'
            ? '修改 Services 卡片'
            : '修改 Quick Links 卡片';
        sortHint.textContent = canSortWithMouse()
            ? '用鼠标拖动左侧 3×2 点阵手柄到目标卡片上，即可占用该卡片的位置。'
            : '当前设备可增删改卡片；排序需在桌面端使用鼠标完成。';
        renderStrip();
        renderSelectionFields();
        computeDirty();
        setStatus(`已载入服务器修订 ${revision}，最多可保存 32 张卡片。`);
    }

    async function openEditor() {
        if (busy) return;
        if (dashboard.classList.contains('is-channel-switching')
            || dashboard.classList.contains('is-mobile-channel-switching')
            || channelSwitch.getAttribute('aria-disabled') === 'true') {
            window.alert('频道正在切换，请稍后再打开卡片编辑器。');
            return;
        }
        launchButton.disabled = true;
        launchButton.setAttribute('aria-busy', 'true');
        try {
            const state = await manager.ensureServerState();
            const requestedChannel = dashboard.dataset.activeChannel === 'quick-links' ? 'quickLinks' : 'services';
            loadChannelFromState(state, requestedChannel);
            dialog.showModal();
            window.requestAnimationFrame(() => {
                strip.querySelector('[aria-selected="true"] .card-editor-option-select')?.focus({ preventScroll: true });
            });
        } catch (error) {
            console.warn('无法打开卡片编辑器。', error);
            window.alert('卡片服务器当前不可用，无法安全保存更改。页面中的导航卡片仍可继续使用。');
        } finally {
            launchButton.disabled = false;
            launchButton.setAttribute('aria-busy', 'false');
        }
    }

    async function deleteSelectedCard() {
        if (busy || selectedId === BLANK_CARD_ID) return;
        const index = draftCards.findIndex((card) => card.id === selectedId);
        if (index < 0) return;
        const card = draftCards[index];
        const confirmed = await askConfirmation(
            `删除“${card.name}”？`,
            '删除只会先写入本页草稿；仍需点击“保存全部更改”才会同步到服务器。',
            '删除卡片',
            true
        );
        if (!confirmed) return;
        draftCards.splice(index, 1);
        selectedId = draftCards[index]?.id || draftCards[index - 1]?.id || BLANK_CARD_ID;
        computeDirty();
        renderStrip();
        renderSelectionFields();
        setStatus('删除已暂存；关闭编辑器可撤回，保存后才会同步。');
    }

    function validateAllDrafts() {
        if (blankHasContent(blankCard)) {
            const candidate = { ...blankCard, id: generateCardId() };
            try {
                const normalized = normalizeDraftCard(candidate, channel);
                if (draftCards.length >= MAX_CARDS) throw validationError('当前频道已达到 32 张卡片上限。', 'name');
                draftCards.push(normalized);
                blankCard = makeBlankCard();
                selectedId = normalized.id;
            } catch (error) {
                selectedId = BLANK_CARD_ID;
                renderStrip();
                renderSelectionFields();
                throw error;
            }
        }
        const normalized = draftCards.map((card) => {
            try {
                return normalizeDraftCard(card, channel);
            } catch (error) {
                selectedId = card.id;
                renderStrip();
                renderSelectionFields();
                throw error;
            }
        });
        const ids = new Set(normalized.map((card) => card.id));
        if (ids.size !== normalized.length) throw validationError('卡片 ID 重复，请重新载入服务器版本。', 'name');
        return normalized;
    }

    async function saveDraft(event) {
        event.preventDefault();
        if (busy) return;
        clearInvalidFields();
        let normalized;
        try {
            normalized = validateAllDrafts();
        } catch (error) {
            setStatus(error.message || '请检查卡片字段。', 'error');
            focusValidationField(error.field);
            return;
        }
        draftCards = normalized;
        computeDirty();
        if (!dirty) {
            setStatus('当前没有需要保存的更改。');
            return;
        }
        const summary = buildChangeSummary(originalCards, draftCards);
        const confirmed = await askConfirmation(
            '保存当前频道的全部更改？',
            `${summary}。保存会整批替换当前频道的服务器卡片。`,
            '确认保存'
        );
        if (!confirmed) return;
        setBusy(true);
        setStatus('正在保存并等待服务器确认…');
        try {
            await manager.saveChannel(channel, revision, draftCards);
            dirty = false;
            dialog.close();
            launchButton.focus({ preventScroll: true });
        } catch (error) {
            if (error.name === 'CardRevisionConflictError') {
                setStatus('服务器数据已在其它页面更新，当前草稿尚未丢失。', 'error');
                setBusy(false);
                const reload = await askConfirmation(
                    '服务器版本发生冲突',
                    '重新载入会放弃当前草稿并使用服务器最新版本；取消可继续查看当前草稿。',
                    '放弃草稿并重新载入',
                    true
                );
                if (reload) {
                    try {
                        const latest = await manager.reloadServerState();
                        loadChannelFromState(latest, channel);
                        setStatus('已重新载入服务器最新版本。', 'success');
                    } catch (reloadError) {
                        setStatus('重新载入服务器版本失败，当前草稿仍保留。', 'error');
                    }
                }
                return;
            }
            if (error.status === 400) {
                const feedback = serverValidationFeedback(error);
                setBusy(false);
                if (feedback.cardId && draftCards.some((card) => card.id === feedback.cardId)) {
                    selectCard(feedback.cardId);
                }
                setStatus(feedback.message, 'error');
                focusValidationField(feedback.field);
                return;
            }
            console.warn('保存卡片失败。', error);
            setStatus('保存失败，当前草稿和选择均已保留；请检查服务器后重试。', 'error');
        } finally {
            setBusy(false);
        }
    }

    async function importSvgFile() {
        const file = iconInput.files?.[0];
        const targetId = selectedId;
        const targetCard = currentCard();
        const targetName = targetId === BLANK_CARD_ID ? '新卡片' : (targetCard?.name || '未命名卡片');
        iconInput.value = '';
        if (!file || !targetCard) return;
        if (file.size <= 0 || file.size > cardSvgLimits.rawBytes) {
            setStatus('SVG 文件必须大于 0 字节且不超过 64 KiB。', 'error');
            focusValidationField('icon');
            return;
        }
        try {
            const markup = await file.text();
            const cleanMarkup = sanitizeSvgMarkup(markup);
            const targetStillExists = targetId === BLANK_CARD_ID
                ? targetCard === blankCard
                : draftCards.includes(targetCard);
            if (!targetStillExists) {
                setStatus('读取完成时原卡片已不在草稿中，因此没有应用该 SVG。', 'error');
                return;
            }
            targetCard.iconSvg = cleanMarkup;
            iconSelect.removeAttribute('aria-invalid');
            if (selectedId === targetId) renderPreview(targetCard);
            const restoreStripFocus = strip.contains(document.activeElement);
            renderStrip();
            computeDirty();
            setStatus(`SVG 已通过安全检查并绑定到“${targetName}”。`, 'success');
            if (targetId === BLANK_CARD_ID && selectedId === BLANK_CARD_ID) promoteBlankIfComplete();
            if (restoreStripFocus) focusSelectedStripOption();
        } catch (error) {
            setStatus(error.message || '无法读取 SVG 文件。', 'error');
            if (selectedId === targetId) focusValidationField('icon');
        }
    }

    launchButton.addEventListener('click', openEditor);
    closeButton.addEventListener('click', requestClose);
    deleteButton.addEventListener('click', deleteSelectedCard);
    form.addEventListener('submit', saveDraft);
    iconSelect.addEventListener('click', () => iconInput.click());
    iconInput.addEventListener('change', importSvgFile);
    nameInput.addEventListener('input', () => updateSelectedField('name', nameInput.value));
    descriptionInput.addEventListener('input', () => updateSelectedField('description', descriptionInput.value));
    urlInput.addEventListener('input', () => updateSelectedField('url', urlInput.value));
    probeInput.addEventListener('input', () => updateSelectedField('probeUrl', probeInput.value));
    confirmCancel.addEventListener('click', () => settleConfirmation(false));
    confirmAccept.addEventListener('click', () => settleConfirmation(true));
    confirmDialog.addEventListener('cancel', (event) => {
        event.preventDefault();
        settleConfirmation(false);
    });
    dialog.addEventListener('cancel', (event) => {
        event.preventDefault();
        requestClose();
    });
    dialog.addEventListener('click', (event) => {
        if (event.target === dialog) requestClose();
    });
    window.addEventListener('beforeunload', (event) => {
        if (!dialog.open || !computeDirty()) return;
        event.preventDefault();
        event.returnValue = '';
    });
}
