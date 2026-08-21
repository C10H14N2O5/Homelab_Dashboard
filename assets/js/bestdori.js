import { fetchWithTimeout } from './core-http.js';

// Bestdori card draw and draw-history maintenance controller.
export function createBestdoriController(context) {
    const {
        clearBackgroundResultPresentation,
        focusWithoutScroll,
        isManagerBusy,
        isManagerOnline,
        normalizeBackgroundName,
        presentBackgroundResults,
        scanBackgroundDirectory,
        setManagerStatus,
        syncManagerControls
    } = context;
    const bestdoriCharactersEndpoint = new URL('/api/bestdori/characters', document.baseURI);
    const bestdoriDrawEndpoint = new URL('/api/background-files/draw', document.baseURI);
    const bestdoriReconcileEndpoint = new URL('/api/bestdori/history/reconcile', document.baseURI);
    const bestdoriResetEndpoint = new URL('/api/bestdori/history/reset', document.baseURI);
    const backgroundDrawButton = document.getElementById('background-draw-button');
    const cardDrawDialog = document.getElementById('card-draw-dialog');
    const cardDrawClose = document.getElementById('card-draw-close');
    const cardDrawForm = document.getElementById('card-draw-form');
    const cardDrawModeInputs = Array.from(cardDrawForm.querySelectorAll('input[name="card-draw-mode"]'));
    const cardDrawRarityInputs = Array.from(cardDrawForm.querySelectorAll('input[name="card-draw-rarity"]'));
    const cardCharacterSelect = document.getElementById('card-character-select');
    const cardDrawStatus = document.getElementById('card-draw-status');
    const cardDrawSubmit = document.getElementById('card-draw-submit');
    const cardHistoryReconcile = document.getElementById('card-history-reconcile');
    const cardHistoryReset = document.getElementById('card-history-reset');
    const cardHistoryResetDialog = document.getElementById('card-history-reset-dialog');
    const cardHistoryResetStatus = document.getElementById('card-history-reset-status');
    const cardHistoryResetCancel = document.getElementById('card-history-reset-cancel');
    const cardHistoryResetConfirm = document.getElementById('card-history-reset-confirm');
    let backgroundDrawPending = false;
    let cardDrawCharactersLoaded = false;

            function bestdoriErrorMessage(payload, status, fallbackLabel) {
                const error = payload && payload.error;
                const errorCode = typeof error === 'string'
                    ? error
                    : error && typeof error.code === 'string' ? error.code : '';
                const knownMessages = {
                    bestdori_operation_in_progress: '另一个抽卡或记录维护操作正在进行，请稍后再试。',
                    bestdori_card_pool_exhausted: '当前筛选条件下没有尚未抽取的卡面，请更换角色或星级，或确认后清空抽卡记录。',
                    bestdori_history_unavailable: '抽卡记录不可用或已损坏，请检查服务日志并恢复 BestdoriCardDraws.json。',
                    bestdori_unavailable: '暂时无法连接 Bestdori，请稍后再试。',
                    bestdori_invalid_image: 'Bestdori 返回的卡面图片无效或不受支持。'
                };
                if (Object.prototype.hasOwnProperty.call(knownMessages, errorCode)) {
                    return knownMessages[errorCode];
                }
                const detail = payload && (
                    payload.message
                    || (typeof error === 'string' ? error : error && (error.message || error.code))
                    || payload.code
                );
                return typeof detail === 'string' && detail
                    ? detail
                    : `${fallbackLabel}接口返回 ${status}`;
            }

            function buildBestdoriDrawRequest(mode, rarities, characterId = null) {
                const normalizedRarities = Array.from(new Set(
                    Array.isArray(rarities)
                        ? rarities.map(Number).filter((rarity) => [3, 4, 5].includes(rarity))
                        : []
                )).sort((left, right) => left - right);
                if (normalizedRarities.length === 0) throw new Error('请至少选择一个星级。');
                if (mode !== 'character' && mode !== 'random') throw new Error('请选择有效的抽取模式。');

                const request = { mode, rarities: normalizedRarities };
                if (mode === 'character') {
                    const normalizedCharacterId = Number(characterId);
                    if (!Number.isInteger(normalizedCharacterId) || normalizedCharacterId < 1 || normalizedCharacterId > 40) {
                        throw new Error('请选择一个有效角色。');
                    }
                    request.characterId = normalizedCharacterId;
                }
                return request;
            }

            async function requestBestdoriJson(endpoint, body = null, timeoutMs = 10000) {
                const headers = {
                    Accept: 'application/json',
                    'X-Requested-With': 'homelab-dashboard'
                };
                if (body !== null) headers['Content-Type'] = 'application/json';

                return fetchWithTimeout(endpoint.href, {
                    method: body === null ? 'GET' : 'POST',
                    cache: 'no-store',
                    credentials: 'same-origin',
                    headers,
                    body: body === null ? null : JSON.stringify(body)
                }, timeoutMs, null, async (response) => {
                    let payload = null;
                    try {
                        payload = await response.json();
                    } catch (error) {
                        // Keep the HTTP status useful if an intermediary returned non-JSON.
                    }
                    if (!response.ok) {
                        throw new Error(bestdoriErrorMessage(payload, response.status, 'Bestdori'));
                    }
                    if (!payload || typeof payload !== 'object') {
                        throw new Error('Bestdori 接口返回了无效数据');
                    }
                    return payload;
                });
            }

            function cardDrawDialogIsOpen() {
                return cardDrawDialog.open || cardDrawDialog.hasAttribute('open');
            }

            function selectedCardDrawMode() {
                return cardDrawModeInputs.find((input) => input.checked)?.value || '';
            }

            function selectedCardDrawRarities() {
                return cardDrawRarityInputs
                    .filter((input) => input.checked)
                    .map((input) => Number(input.value));
            }

            function setCardDrawStatus(message, kind = '') {
                cardDrawStatus.textContent = message;
                if (kind) {
                    cardDrawStatus.dataset.kind = kind;
                } else {
                    delete cardDrawStatus.dataset.kind;
                }
            }

            function resetCardDrawStatus() {
                if (selectedCardDrawRarities().length === 0) {
                    setCardDrawStatus('请至少选择一个星级。', 'error');
                    return;
                }

                if (selectedCardDrawMode() === 'random') {
                    setCardDrawStatus('全随机模式会在所选星级范围内抽取未重复卡面。');
                    return;
                }

                if (cardDrawCharactersLoaded) {
                    setCardDrawStatus(
                        `已读取 ${cardCharacterSelect.options.length} 个角色，请选择抽取条件。`,
                        'success'
                    );
                    return;
                }

                setCardDrawStatus('请选择角色与至少一个星级。');
            }

            function syncCardDrawControls() {
                const mode = selectedCardDrawMode();
                const hasRarity = selectedCardDrawRarities().length > 0;
                const hasCharacter = Number.isInteger(Number(cardCharacterSelect.value))
                    && Number(cardCharacterSelect.value) >= 1
                    && Number(cardCharacterSelect.value) <= 40;
                const inputs = [...cardDrawModeInputs, ...cardDrawRarityInputs];

                inputs.forEach((input) => {
                    input.disabled = backgroundDrawPending;
                });
                cardCharacterSelect.disabled = backgroundDrawPending
                    || mode !== 'character'
                    || !cardDrawCharactersLoaded;
                cardDrawClose.disabled = backgroundDrawPending;
                cardDrawForm.setAttribute('aria-busy', String(backgroundDrawPending));
                cardDrawSubmit.disabled = backgroundDrawPending
                    || !hasRarity
                    || (mode === 'character' && !hasCharacter);
                cardDrawSubmit.setAttribute('aria-busy', String(backgroundDrawPending));
                cardHistoryReconcile.disabled = backgroundDrawPending;
                cardHistoryReset.disabled = backgroundDrawPending;
            }

            async function loadBestdoriCharacters() {
                if (cardDrawCharactersLoaded || backgroundDrawPending) return cardDrawCharactersLoaded;

                backgroundDrawPending = true;
                syncManagerControls();
                syncCardDrawControls();
                setCardDrawStatus('正在读取角色列表…');

                try {
                    const payload = await requestBestdoriJson(bestdoriCharactersEndpoint, null, 15000);
                    const characters = Array.isArray(payload.characters)
                        ? payload.characters.filter((character) => (
                            character
                            && Number.isInteger(Number(character.id))
                            && Number(character.id) >= 1
                            && Number(character.id) <= 40
                            && typeof character.name === 'string'
                            && character.name
                        ))
                        : [];
                    if (characters.length === 0) throw new Error('角色接口没有返回可用角色');

                    const fragment = document.createDocumentFragment();
                    characters
                        .sort((left, right) => Number(left.id) - Number(right.id))
                        .forEach((character) => {
                            const option = document.createElement('option');
                            option.value = String(Number(character.id));
                            option.textContent = character.name;
                            fragment.append(option);
                        });
                    cardCharacterSelect.replaceChildren(fragment);
                    cardDrawCharactersLoaded = true;
                    resetCardDrawStatus();
                    return true;
                } catch (error) {
                    console.warn('Bestdori 角色列表读取失败。', error);
                    cardCharacterSelect.replaceChildren();
                    const option = document.createElement('option');
                    option.value = '';
                    option.textContent = '角色列表暂不可用';
                    cardCharacterSelect.append(option);
                    setCardDrawStatus(`角色列表读取失败：${error.message || '未知错误'}；仍可使用全随机模式。`, 'error');
                    return false;
                } finally {
                    backgroundDrawPending = false;
                    syncManagerControls();
                    syncCardDrawControls();
                }
            }

            function closeCardDrawDialog(returnValue = '', restoreFocus = true) {
                if (!cardDrawDialogIsOpen() || backgroundDrawPending) return false;
                cardDrawDialog.classList.remove('is-visible');
                if (typeof cardDrawDialog.close === 'function' && cardDrawDialog.open) {
                    cardDrawDialog.close(returnValue);
                } else {
                    cardDrawDialog.removeAttribute('open');
                }
                resetCardDrawStatus();
                if (restoreFocus) focusWithoutScroll(backgroundDrawButton);
                return true;
            }

            async function openCardDrawDialog() {
                if (isManagerBusy() || !isManagerOnline()) {
                    setManagerStatus('背景管理服务正忙或暂不可用，请稍后再试。', 'error');
                    return false;
                }

                if (typeof cardDrawDialog.showModal === 'function') {
                    if (!cardDrawDialog.open) cardDrawDialog.showModal();
                } else {
                    cardDrawDialog.setAttribute('open', '');
                }
                const reveal = () => {
                    if (cardDrawDialogIsOpen()) cardDrawDialog.classList.add('is-visible');
                };
                if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
                    reveal();
                } else {
                    window.requestAnimationFrame(() => window.requestAnimationFrame(reveal));
                }

                syncCardDrawControls();
                resetCardDrawStatus();
                if (!cardDrawCharactersLoaded) await loadBestdoriCharacters();
                if (cardDrawDialogIsOpen()) {
                    const focusTarget = selectedCardDrawMode() === 'character' && !cardCharacterSelect.disabled
                        ? cardCharacterSelect
                        : cardDrawModeInputs[0];
                    focusWithoutScroll(focusTarget);
                }
                return true;
            }

            async function drawBestdoriCard() {
                if (isManagerBusy() || !isManagerOnline()) {
                    setCardDrawStatus('背景管理服务正忙或暂不可用，请稍后再试。', 'error');
                    return false;
                }

                let request;
                try {
                    request = buildBestdoriDrawRequest(
                        selectedCardDrawMode(),
                        selectedCardDrawRarities(),
                        cardCharacterSelect.value
                    );
                } catch (error) {
                    setCardDrawStatus(error.message, 'error');
                    syncCardDrawControls();
                    return false;
                }

                let drawnName = '';
                let succeeded = false;
                backgroundDrawPending = true;
                clearBackgroundResultPresentation();
                syncManagerControls();
                syncCardDrawControls();
                setCardDrawStatus('正在从 Bestdori 抽取并下载卡面，请稍候…');

                try {
                    const payload = await requestBestdoriJson(bestdoriDrawEndpoint, request, 90000);
                    drawnName = normalizeBackgroundName(
                        payload.background && payload.background.background
                    ) || '';
                    if (!drawnName) throw new Error('抽卡接口返回了无效背景文件名');

                    const card = payload.card && typeof payload.card === 'object' ? payload.card : {};
                    const characterName = typeof card.characterName === 'string' ? card.characterName : '未知角色';
                    const rarity = [3, 4, 5].includes(Number(card.rarity)) ? `${Number(card.rarity)} 星` : '未知星级';
                    await scanBackgroundDirectory({
                        successMessage: `已抽到 ${characterName} · ${rarity}，下载为 ${drawnName}；当前背景未更改。`
                    });
                    succeeded = true;
                    return true;
                } catch (error) {
                    console.warn('Bestdori 抽卡失败。', error);
                    const message = `抽卡失败：${error.message || '未知错误'}`;
                    setCardDrawStatus(message, 'error');
                    setManagerStatus(message, 'error');
                    return false;
                } finally {
                    backgroundDrawPending = false;
                    syncManagerControls();
                    syncCardDrawControls();
                    if (succeeded) {
                        closeCardDrawDialog('drawn', false);
                        presentBackgroundResults({ createdNames: [drawnName] });
                    }
                }
            }

            async function reconcileBestdoriHistory() {
                if (isManagerBusy()) return false;

                backgroundDrawPending = true;
                syncManagerControls();
                syncCardDrawControls();
                setCardDrawStatus('正在核对抽卡记录与本地图片…');
                try {
                    const payload = await requestBestdoriJson(bestdoriReconcileEndpoint, {}, 30000);
                    const removed = Number.isInteger(Number(payload.removed)) ? Number(payload.removed) : 0;
                    const remaining = Number.isInteger(Number(payload.remaining)) ? Number(payload.remaining) : 0;
                    setCardDrawStatus(`已清理 ${removed} 条失效记录，当前保留 ${remaining} 条。图片未被删除。`, 'success');
                    return true;
                } catch (error) {
                    console.warn('Bestdori 抽卡记录清理失败。', error);
                    setCardDrawStatus(`清理失效记录失败：${error.message || '未知错误'}`, 'error');
                    return false;
                } finally {
                    backgroundDrawPending = false;
                    syncManagerControls();
                    syncCardDrawControls();
                    focusWithoutScroll(cardHistoryReconcile);
                }
            }

            function cardHistoryResetDialogIsOpen() {
                return cardHistoryResetDialog.open || cardHistoryResetDialog.hasAttribute('open');
            }

            function syncCardHistoryResetControls() {
                cardHistoryResetCancel.disabled = backgroundDrawPending;
                cardHistoryResetConfirm.disabled = backgroundDrawPending;
                cardHistoryResetConfirm.setAttribute('aria-busy', String(backgroundDrawPending));
            }

            function openCardHistoryResetDialog() {
                if (isManagerBusy()) return false;
                cardHistoryResetStatus.textContent = '';
                if (typeof cardHistoryResetDialog.showModal === 'function') {
                    if (!cardHistoryResetDialog.open) cardHistoryResetDialog.showModal();
                } else {
                    cardHistoryResetDialog.setAttribute('open', '');
                }
                cardHistoryResetDialog.classList.add('is-visible');
                syncCardHistoryResetControls();
                focusWithoutScroll(cardHistoryResetConfirm);
                return true;
            }

            function closeCardHistoryResetDialog(returnValue = '', restoreFocus = true) {
                if (!cardHistoryResetDialogIsOpen() || backgroundDrawPending) return false;
                cardHistoryResetDialog.classList.remove('is-visible');
                if (typeof cardHistoryResetDialog.close === 'function' && cardHistoryResetDialog.open) {
                    cardHistoryResetDialog.close(returnValue);
                } else {
                    cardHistoryResetDialog.removeAttribute('open');
                }
                cardHistoryResetStatus.textContent = '';
                if (restoreFocus) focusWithoutScroll(cardHistoryReset);
                return true;
            }

            async function resetBestdoriHistory() {
                if (!cardHistoryResetDialogIsOpen() || isManagerBusy()) return false;

                let succeeded = false;
                backgroundDrawPending = true;
                syncManagerControls();
                syncCardDrawControls();
                syncCardHistoryResetControls();
                cardHistoryResetStatus.textContent = '正在清空防重复记录…';
                try {
                    const payload = await requestBestdoriJson(bestdoriResetEndpoint, {}, 30000);
                    const removed = Number.isInteger(Number(payload.removed)) ? Number(payload.removed) : 0;
                    setCardDrawStatus(`已清空 ${removed} 条抽卡记录。已下载图片未被删除。`, 'success');
                    succeeded = true;
                    return true;
                } catch (error) {
                    console.warn('Bestdori 抽卡记录重置失败。', error);
                    cardHistoryResetStatus.textContent = `清空失败：${error.message || '未知错误'}`;
                    return false;
                } finally {
                    backgroundDrawPending = false;
                    syncManagerControls();
                    syncCardDrawControls();
                    syncCardHistoryResetControls();
                    if (succeeded) closeCardHistoryResetDialog('reset');
                }
            }
            backgroundDrawButton.addEventListener('click', openCardDrawDialog);
            cardDrawClose.addEventListener('click', () => closeCardDrawDialog('close'));
            cardDrawForm.addEventListener('submit', (event) => {
                event.preventDefault();
                drawBestdoriCard();
            });
            cardDrawModeInputs.forEach((input) => {
                input.addEventListener('change', () => {
                    syncCardDrawControls();
                    resetCardDrawStatus();
                });
            });
            cardDrawRarityInputs.forEach((input) => {
                input.addEventListener('change', () => {
                    syncCardDrawControls();
                    resetCardDrawStatus();
                });
            });
            cardCharacterSelect.addEventListener('change', () => {
                syncCardDrawControls();
                resetCardDrawStatus();
            });
            cardHistoryReconcile.addEventListener('click', reconcileBestdoriHistory);
            cardHistoryReset.addEventListener('click', openCardHistoryResetDialog);

            cardDrawDialog.addEventListener('cancel', (event) => {
                event.preventDefault();
                if (!backgroundDrawPending) closeCardDrawDialog('cancel');
            });
            cardDrawDialog.addEventListener('click', (event) => {
                if (event.target === cardDrawDialog && !backgroundDrawPending) {
                    closeCardDrawDialog('backdrop');
                }
            });
            cardDrawDialog.addEventListener('close', () => {
                cardDrawDialog.classList.remove('is-visible');
            });

            cardHistoryResetCancel.addEventListener('click', () => closeCardHistoryResetDialog('cancel'));
            cardHistoryResetConfirm.addEventListener('click', resetBestdoriHistory);
            cardHistoryResetDialog.addEventListener('cancel', (event) => {
                event.preventDefault();
                if (!backgroundDrawPending) closeCardHistoryResetDialog('cancel');
            });
            cardHistoryResetDialog.addEventListener('click', (event) => {
                if (event.target === cardHistoryResetDialog && !backgroundDrawPending) {
                    closeCardHistoryResetDialog('backdrop');
                }
            });
            cardHistoryResetDialog.addEventListener('close', () => {
                cardHistoryResetDialog.classList.remove('is-visible');
                cardHistoryResetStatus.textContent = '';
            });


    return {
        isBusy: () => backgroundDrawPending,
        syncAvailability(isBusy, managerOnline) {
            backgroundDrawButton.disabled = isBusy || !managerOnline;
        }
    };
}
