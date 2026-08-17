        (function () {
            const root = document.documentElement;
            let introStarted = false;
            let introCleanupTimer = null;

            function finishIntro() {
                if (introCleanupTimer !== null) {
                    window.clearTimeout(introCleanupTimer);
                    introCleanupTimer = null;
                }
                root.classList.remove('intro-first', 'intro-play');
            }

            function playIntro() {
                if (introStarted || !root.classList.contains('intro-first')) return;
                introStarted = true;
                const dashboardFooter = document.querySelector('.dashboard-footer');
                if (dashboardFooter) {
                    dashboardFooter.addEventListener('animationend', (event) => {
                        if (event.target === dashboardFooter && event.animationName === 'copy-reveal') {
                            finishIntro();
                        }
                    }, { once: true });
                }
                root.classList.add('intro-play');
                introCleanupTimer = window.setTimeout(finishIntro, 1500);
                try {
                    sessionStorage.setItem('homelab-intro-seen', '1');
                } catch (error) {
                    // Storage can be unavailable in strict privacy modes; the page remains usable.
                }
            }

            if (root.classList.contains('intro-first')) {
                window.addEventListener('load', playIntro, { once: true });
                window.setTimeout(playIntro, 1800);
            }

            const pageBase = new URL('.', document.baseURI);
            const backgroundDirectory = new URL('backgrounds/', pageBase);
            const backgroundThumbnailDirectory = new URL('thumbnails/', backgroundDirectory);
            const defaultBackground = new URL('bg.jpg', backgroundDirectory);
            const defaultBackgroundName = 'bg.jpg';
            const backgroundPreferencesEndpoint = new URL('/api/background-preferences', document.baseURI);
            const backgroundInventoryEndpoint = new URL('/api/background-files', document.baseURI);
            const backgroundUploadEndpoint = new URL('/api/background-files/upload', document.baseURI);
            const backgroundRemoveEndpoint = new URL('/api/background-files/remove', document.baseURI);
            const bestdoriCharactersEndpoint = new URL('/api/bestdori/characters', document.baseURI);
            const bestdoriDrawEndpoint = new URL('/api/background-files/draw', document.baseURI);
            const bestdoriReconcileEndpoint = new URL('/api/bestdori/history/reconcile', document.baseURI);
            const bestdoriResetEndpoint = new URL('/api/bestdori/history/reset', document.baseURI);
            const serviceStatusEndpoint = new URL('/api/service-status', document.baseURI);
            const backgroundPreferencesCacheKey = 'homelab-background-state-cache-v1';
            const legacyBackgroundStorageKey = 'homelab-background';
            const maximumBackgroundResponseBytes = 512 * 1024;
            const maximumBackgroundInventoryItems = 512;
            const backgroundExtensionPattern = /\.(?:jpe?g|png|webp|avif)$/i;
            const backgroundModes = ['landscape', 'portrait'];
            const backgroundModeLabels = {
                landscape: '横屏',
                portrait: '竖屏'
            };
            const backgroundLibrarySources = ['ordinary', 'bestdori'];
            const backgroundLibraryLabels = {
                ordinary: '普通壁纸',
                bestdori: 'Bestdori 抽卡'
            };
            const backgroundLibraryExpanded = {
                ordinary: true,
                bestdori: true
            };
            const backgroundSectionTransitionFallbackMs = 400;
            const backgroundSectionTransitionCleanups = new Map();
            const orientationQuery = window.matchMedia('(orientation: portrait)');
            const backgroundLayer = document.querySelector('.background-layer');
            const backgroundPickerButton = document.getElementById('background-picker-button');
            const backgroundDialog = document.getElementById('background-dialog');
            const backgroundDialogCloseForm = backgroundDialog.querySelector('form[method="dialog"]');
            const backgroundGrid = document.getElementById('background-grid');
            const backgroundPickerStatus = document.getElementById('background-picker-status');
            const backgroundModeButtons = Array.from(backgroundDialog.querySelectorAll('[data-background-mode]'));
            const backgroundUploadButton = document.getElementById('background-upload-button');
            const backgroundUploadInput = document.getElementById('background-upload-input');
            const backgroundUploadDialog = document.getElementById('background-upload-dialog');
            const backgroundUploadSummary = document.getElementById('background-upload-summary');
            const backgroundUploadCount = document.getElementById('background-upload-count');
            const backgroundUploadProgress = document.getElementById('background-upload-progress');
            const backgroundUploadList = document.getElementById('background-upload-list');
            const backgroundUploadStatus = document.getElementById('background-upload-status');
            const backgroundUploadStop = document.getElementById('background-upload-stop');
            const backgroundUploadRetry = document.getElementById('background-upload-retry');
            const backgroundUploadDone = document.getElementById('background-upload-done');
            const backgroundDeleteButton = document.getElementById('background-delete-button');
            const backgroundOriginalLink = document.getElementById('background-original-link');
            const backgroundDeleteDialog = document.getElementById('background-delete-dialog');
            const backgroundDeleteTitle = document.getElementById('background-delete-title');
            const backgroundDeleteDescription = document.getElementById('background-delete-description');
            const backgroundDeleteStatus = document.getElementById('background-delete-status');
            const backgroundDeleteCancel = document.getElementById('background-delete-cancel');
            const backgroundDeleteConfirm = document.getElementById('background-delete-confirm');
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
            let backgroundScanSequence = 0;
            let backgroundScanController = null;
            let backgroundScanPending = false;
            let backgroundDialogAnimationToken = 0;
            let backgroundDialogCloseTimer = 0;
            let backgroundDisplaySequence = 0;
            let backgroundPreferencesReadSequence = 0;
            let backgroundPreferencesWriteSequence = 0;
            let backgroundWritePending = false;
            let backgroundUploadPending = false;
            let backgroundUploadSession = null;
            let backgroundUploadRequest = null;
            let backgroundDeletePending = false;
            let backgroundDrawPending = false;
            let cardDrawCharactersLoaded = false;
            let backgroundDeleteRequest = null;
            let backgroundDeleteReturnFocus = null;
            let backgroundPreferencesConnection = 'loading';
            let editingBackgroundModeTouched = false;

            function normalizeBackgroundUrl(value) {
                if (!value) return null;

                try {
                    const url = new URL(value, pageBase);
                    url.search = '';
                    url.hash = '';

                    if (url.origin !== location.origin || !url.pathname.startsWith(backgroundDirectory.pathname)) return null;
                    const relativePath = decodeURIComponent(url.pathname.slice(backgroundDirectory.pathname.length));
                    if (!relativePath || relativePath.includes('/') || !backgroundExtensionPattern.test(relativePath)) return null;
                    return url;
                } catch (error) {
                    return null;
                }
            }

            function backgroundFileName(url) {
                try {
                    return decodeURIComponent(url.pathname.split('/').pop());
                } catch (error) {
                    return url.pathname.split('/').pop();
                }
            }

            function normalizeBackgroundName(value) {
                if (
                    typeof value !== 'string'
                    || value.length === 0
                    || value.length > 255
                    || value.includes('/')
                    || value.includes('\\')
                    || /[\u0000-\u001f\u007f]/.test(value)
                    || !backgroundExtensionPattern.test(value)
                ) return null;

                return value;
            }

            function normalizeBackgroundThumbnailUrl(value) {
                if (typeof value !== 'string' || value.length === 0 || value.length > 1024) return null;

                try {
                    const url = new URL(value, backgroundDirectory);
                    if (
                        url.origin !== location.origin
                        || url.username
                        || url.password
                        || url.hash
                        || !url.pathname.startsWith(backgroundThumbnailDirectory.pathname)
                    ) return null;

                    const relativePath = decodeURIComponent(
                        url.pathname.slice(backgroundThumbnailDirectory.pathname.length)
                    );
                    if (
                        !relativePath
                        || relativePath.includes('/')
                        || relativePath.includes('\\')
                        || !/^[a-z0-9][a-z0-9._-]*\.webp$/i.test(relativePath)
                    ) return null;

                    const versionValues = url.searchParams.getAll('v');
                    const queryKeys = Array.from(url.searchParams.keys());
                    if (
                        queryKeys.length !== 1
                        || queryKeys[0] !== 'v'
                        || versionValues.length !== 1
                        || !/^[a-z0-9][a-z0-9._-]{0,127}$/i.test(versionValues[0])
                    ) return null;

                    return url;
                } catch (error) {
                    return null;
                }
            }

            async function readBoundedResponseText(response, maximumBytes = maximumBackgroundResponseBytes) {
                const declaredValue = response.headers && response.headers.get
                    ? response.headers.get('Content-Length')
                    : null;
                let declaredLength = null;
                if (declaredValue !== null) {
                    if (!/^\d+$/.test(declaredValue)) {
                        throw new Error('响应包含无效的 Content-Length');
                    }
                    declaredLength = Number(declaredValue);
                    if (!Number.isSafeInteger(declaredLength) || declaredLength > maximumBytes) {
                        throw new Error('响应内容超过安全上限');
                    }
                }

                if (response.body && typeof response.body.getReader === 'function') {
                    const reader = response.body.getReader();
                    const decoder = new TextDecoder('utf-8', { fatal: true });
                    let received = 0;
                    let text = '';
                    try {
                        while (true) {
                            const { done, value } = await reader.read();
                            if (done) break;
                            if (!(value instanceof Uint8Array)) {
                                throw new Error('响应流包含无效数据');
                            }
                            received += value.byteLength;
                            if (received > maximumBytes) {
                                await reader.cancel();
                                throw new Error('响应内容超过安全上限');
                            }
                            text += decoder.decode(value, { stream: true });
                        }
                        text += decoder.decode();
                        if (declaredLength !== null && received !== declaredLength) {
                            throw new Error('响应长度与 Content-Length 不一致');
                        }
                    } catch (error) {
                        try {
                            await reader.cancel();
                        } catch (cancelError) {
                            // The response may already be closed.
                        }
                        throw error;
                    } finally {
                        if (reader.releaseLock) reader.releaseLock();
                    }
                    return text;
                }

                if (declaredLength === null || typeof response.text !== 'function') {
                    throw new Error('当前浏览器无法安全读取无长度响应');
                }
                return response.text();
            }

            function normalizeBackgroundInventory(value) {
                if (
                    !value
                    || typeof value !== 'object'
                    || value.version !== 1
                    || !Array.isArray(value.backgrounds)
                    || value.backgrounds.length === 0
                    || value.backgrounds.length > maximumBackgroundInventoryItems
                    || !value.thumbnails
                    || typeof value.thumbnails !== 'object'
                ) return null;

                const thumbnailStats = {};
                for (const key of ['generated', 'reused', 'failed', 'deferred']) {
                    const count = key === 'deferred' && value.thumbnails[key] === undefined
                        ? 0
                        : value.thumbnails[key];
                    if (!Number.isSafeInteger(count) || count < 0) return null;
                    thumbnailStats[key] = count;
                }
                const total = value.total === undefined ? value.backgrounds.length : value.total;
                const truncated = value.truncated === undefined ? false : value.truncated;
                if (
                    (total !== null && (!Number.isSafeInteger(total) || total < value.backgrounds.length))
                    || typeof truncated !== 'boolean'
                ) return null;

                const seenNames = new Set();
                const backgrounds = [];
                for (const item of value.backgrounds) {
                    if (!item || typeof item !== 'object') return null;
                    const name = normalizeBackgroundName(item.name);
                    const nameKey = name && name.toLocaleLowerCase('en-US');
                    if (!name || seenNames.has(nameKey)) return null;

                    const url = backgroundUrlFromName(name);
                    if (!url) return null;

                    let thumbnailUrl = null;
                    if (item.thumbnail !== null) {
                        thumbnailUrl = normalizeBackgroundThumbnailUrl(item.thumbnail);
                        if (!thumbnailUrl) return null;
                    }

                    seenNames.add(nameKey);
                    backgrounds.push({ url, thumbnailUrl });
                }
                if (
                    Object.values(thumbnailStats).reduce((sum, count) => sum + count, 0)
                    !== backgrounds.length
                ) return null;

                return {
                    backgrounds,
                    thumbnails: thumbnailStats,
                    total,
                    truncated
                };
            }

            function validateBackgroundUploadFile(file) {
                const maximumBytes = 50 * 1024 * 1024;
                const supportedExtension = /\.(?:jpe?g|png|webp|avif)$/i;

                if (!file || typeof file.name !== 'string' || typeof file.size !== 'number') {
                    return '请选择一张图片。';
                }
                if (!supportedExtension.test(file.name)) {
                    return '仅支持 JPEG、PNG、WebP 或 AVIF 图片。';
                }
                if (file.size <= 0) {
                    return '不能上传空文件。';
                }
                if (file.size > maximumBytes) {
                    return '图片不能超过 50 MiB。';
                }
                return '';
            }

            function backgroundModesUsingFile(fileName, preferences) {
                if (typeof fileName !== 'string' || !preferences || typeof preferences !== 'object') return [];
                return ['landscape', 'portrait'].filter((mode) => preferences[mode] === fileName);
            }

            function isBestdoriBackgroundName(value) {
                const fileName = normalizeBackgroundName(value);
                return Boolean(
                    fileName
                    && /^.+_(?:三星|四星|五星)_.+_[1-9]\d*_(?:after_training|normal)(?: \((?:[2-9]|[1-9]\d+)\))?\.png$/u.test(fileName)
                );
            }

            function backgroundSourceForName(value) {
                return isBestdoriBackgroundName(value) ? 'bestdori' : 'ordinary';
            }

            function backgroundUrlFromName(value) {
                const fileName = normalizeBackgroundName(value);
                if (!fileName) return null;
                return normalizeBackgroundUrl(new URL(encodeURIComponent(fileName), backgroundDirectory));
            }

            function normalizeBackgroundPreferences(value) {
                if (!value || typeof value !== 'object' || value.version !== 1) return null;
                const landscape = normalizeBackgroundName(value.landscape);
                const portrait = normalizeBackgroundName(value.portrait);
                if (!landscape || !portrait) return null;

                return {
                    version: 1,
                    landscape,
                    portrait,
                    updatedAt: typeof value.updatedAt === 'string' ? value.updatedAt : ''
                };
            }

            function readBackgroundPreferencesCache() {
                try {
                    return normalizeBackgroundPreferences(JSON.parse(localStorage.getItem(backgroundPreferencesCacheKey) || 'null'));
                } catch (error) {
                    return null;
                }
            }

            function saveBackgroundPreferencesCache(preferences) {
                try {
                    localStorage.setItem(backgroundPreferencesCacheKey, JSON.stringify(preferences));
                } catch (error) {
                    console.warn('无法缓存共享背景状态。', error);
                }
            }

            function clearLegacyBackgroundPreference() {
                try {
                    localStorage.removeItem(legacyBackgroundStorageKey);
                } catch (error) {
                    // Storage failure does not affect the current page.
                }
            }

            function currentOrientationMode() {
                return orientationQuery.matches ? 'portrait' : 'landscape';
            }

            const cachedBackgroundPreferences = readBackgroundPreferencesCache();
            let backgroundPreferences = cachedBackgroundPreferences || {
                version: 1,
                landscape: defaultBackgroundName,
                portrait: defaultBackgroundName,
                updatedAt: ''
            };
            let activeBackgroundMode = currentOrientationMode();
            let editingBackgroundMode = activeBackgroundMode;

            function backgroundUrlForMode(mode) {
                return backgroundUrlFromName(backgroundPreferences[mode]) || defaultBackground;
            }

            let currentBackground = normalizeBackgroundUrl(root.dataset.background) || backgroundUrlForMode(activeBackgroundMode);

            function setBackgroundStyle(url) {
                root.style.setProperty('--background-image', `url("${url.href}")`);
                root.dataset.background = url.href;
                currentBackground = url;
            }

            function setBackgroundStatus(message, kind = '') {
                backgroundPickerStatus.textContent = message;
                if (kind) {
                    backgroundPickerStatus.dataset.kind = kind;
                } else {
                    delete backgroundPickerStatus.dataset.kind;
                }
            }

            function focusWithoutScroll(element) {
                if (!element || typeof element.focus !== 'function') return;
                try {
                    element.focus({ preventScroll: true });
                } catch (error) {
                    element.focus();
                }
            }

            function fetchWithTimeout(
                resource,
                options = {},
                timeoutMs = 5000,
                controller = null,
                transformResponse = null
            ) {
                const requestController = controller || (
                    typeof AbortController === 'function' ? new AbortController() : null
                );
                const requestOptions = { ...options };
                if (requestController) requestOptions.signal = requestController.signal;

                let timeout = 0;
                const timeoutPromise = new Promise((resolve, reject) => {
                    timeout = window.setTimeout(() => {
                        if (requestController) requestController.abort();
                        reject(new Error(`请求超时（${timeoutMs}ms）`));
                    }, timeoutMs);
                });

                const request = fetch(resource, requestOptions).then((response) => (
                    typeof transformResponse === 'function' ? transformResponse(response) : response
                ));
                return Promise.race([request, timeoutPromise])
                    .finally(() => window.clearTimeout(timeout));
            }

            function preloadBackground(url, timeoutMs = 8000) {
                return new Promise((resolve, reject) => {
                    const image = new Image();
                    let settled = false;
                    let timeout = 0;

                    const finish = (callback, value) => {
                        if (settled) return;
                        settled = true;
                        window.clearTimeout(timeout);
                        image.onload = null;
                        image.onerror = null;
                        callback(value);
                    };

                    image.decoding = 'async';
                    image.onload = () => finish(resolve, url);
                    image.onerror = () => finish(reject, new Error(`图片无法加载：${url.pathname}`));
                    timeout = window.setTimeout(() => {
                        finish(reject, new Error(`图片加载超时：${url.pathname}`));
                        image.src = 'data:,';
                    }, timeoutMs);
                    image.src = url.href;
                });
            }

            function backgroundManagerIsBusy() {
                return backgroundScanPending
                    || backgroundWritePending
                    || backgroundUploadPending
                    || backgroundDeletePending
                    || backgroundDrawPending;
            }

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

            function syncBackgroundOriginalLink() {
                const selectedName = normalizeBackgroundName(backgroundPreferences[editingBackgroundMode]);
                const selectedUrl = selectedName ? backgroundUrlFromName(selectedName) : null;
                if (!selectedUrl) {
                    backgroundOriginalLink.removeAttribute('href');
                    backgroundOriginalLink.setAttribute('aria-disabled', 'true');
                    backgroundOriginalLink.setAttribute('aria-label', '查看当前背景原图');
                    return;
                }

                backgroundOriginalLink.setAttribute('href', selectedUrl.href);
                backgroundOriginalLink.setAttribute('aria-disabled', 'false');
                backgroundOriginalLink.setAttribute(
                    'aria-label',
                    `在新标签页查看当前${backgroundModeLabels[editingBackgroundMode]}背景原图 ${selectedName}`
                );
            }

            function syncBackgroundControlsBusy() {
                const isBusy = backgroundManagerIsBusy();
                const managerOnline = backgroundPreferencesConnection === 'online';
                const selectedName = normalizeBackgroundName(backgroundPreferences[editingBackgroundMode]);
                const canDelete = managerOnline
                    && !isBusy
                    && Boolean(selectedName)
                    && selectedName.toLowerCase() !== defaultBackgroundName;

                backgroundGrid.setAttribute('aria-busy', String(isBusy));
                backgroundModeButtons.forEach((button) => {
                    button.disabled = isBusy;
                });
                backgroundGrid.querySelectorAll('.background-option').forEach((option) => {
                    option.disabled = isBusy;
                });
                backgroundUploadButton.disabled = isBusy || !managerOnline;
                backgroundUploadInput.disabled = isBusy || !managerOnline;
                backgroundDeleteButton.disabled = !canDelete;
                backgroundDrawButton.disabled = isBusy || !managerOnline;
                syncBackgroundOriginalLink();
                backgroundDeleteButton.setAttribute(
                    'aria-label',
                    canDelete ? `删除当前${backgroundModeLabels[editingBackgroundMode]}背景 ${selectedName}` : '删除当前背景'
                );
            }

            function updateBackgroundModeControls() {
                backgroundModeButtons.forEach((button) => {
                    button.setAttribute('aria-pressed', String(button.dataset.backgroundMode === editingBackgroundMode));
                });
            }

            function updateBackgroundSelection() {
                const selectedUrl = backgroundUrlForMode(editingBackgroundMode);
                backgroundGrid.querySelectorAll('.background-option').forEach((option) => {
                    const isSelected = option.dataset.url === selectedUrl.href;
                    option.setAttribute('aria-pressed', String(isSelected));
                    option.setAttribute(
                        'aria-label',
                        `将 ${backgroundFileName(new URL(option.dataset.url))} 设置为${backgroundModeLabels[editingBackgroundMode]}背景${isSelected ? '，当前已选' : ''}`
                    );
                });
                syncBackgroundControlsBusy();
            }

            async function applyDisplayedBackground(url, options = {}) {
                const candidate = normalizeBackgroundUrl(url);
                if (!candidate) {
                    if (options.announce) setBackgroundStatus('该图片地址无效，已保留当前背景。', 'error');
                    return false;
                }

                const displayId = ++backgroundDisplaySequence;
                backgroundLayer.classList.remove('is-changing');

                try {
                    if (options.announce) setBackgroundStatus(`正在加载 ${backgroundFileName(candidate)}…`);
                    await preloadBackground(candidate);
                    if (displayId !== backgroundDisplaySequence) return false;

                    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
                    const shouldAnimate = options.animate !== false && !reduceMotion && candidate.href !== currentBackground.href;
                    if (shouldAnimate) {
                        backgroundLayer.classList.add('is-changing');
                        await new Promise((resolve) => window.setTimeout(resolve, 150));
                        if (displayId !== backgroundDisplaySequence) return false;
                    }

                    setBackgroundStyle(candidate);
                    updateBackgroundSelection();

                    if (shouldAnimate) {
                        window.requestAnimationFrame(() => {
                            window.requestAnimationFrame(() => {
                                if (displayId === backgroundDisplaySequence) backgroundLayer.classList.remove('is-changing');
                            });
                        });
                    }
                    if (options.announce) {
                        setBackgroundStatus(options.successMessage || `已切换为 ${backgroundFileName(candidate)}`, 'success');
                    }
                    return true;
                } catch (error) {
                    if (displayId !== backgroundDisplaySequence) return false;
                    backgroundLayer.classList.remove('is-changing');
                    console.warn('背景切换失败。', error);
                    if (options.fallbackToDefault) {
                        setBackgroundStyle(defaultBackground);
                        updateBackgroundSelection();
                    }
                    if (options.announce) setBackgroundStatus('图片加载失败，已保留当前背景。', 'error');
                    return false;
                }
            }

            async function requestBackgroundPreferences(method = 'GET', body = null) {
                const headers = {
                    Accept: 'application/json',
                    'X-Requested-With': 'homelab-dashboard'
                };

                if (body) headers['Content-Type'] = 'application/json';

                const result = await fetchWithTimeout(backgroundPreferencesEndpoint.href, {
                    method,
                    cache: 'no-store',
                    credentials: 'same-origin',
                    headers,
                    body: body ? JSON.stringify(body) : null
                }, 5000, null, async (response) => {
                    if (!response.ok) throw new Error(`背景状态接口返回 ${response.status}`);
                    return {
                        response,
                        payload: await response.json()
                    };
                });

                const preferences = normalizeBackgroundPreferences(result.payload);
                if (!preferences) throw new Error('背景状态接口返回了无效数据');
                return preferences;
            }

            function acceptSharedBackgroundPreferences(preferences) {
                backgroundPreferences = preferences;
                backgroundPreferencesConnection = 'online';
                saveBackgroundPreferencesCache(preferences);
                clearLegacyBackgroundPreference();
                activeBackgroundMode = currentOrientationMode();
                updateBackgroundModeControls();
                updateBackgroundSelection();
            }

            async function loadSharedBackgroundPreferences() {
                const requestId = ++backgroundPreferencesReadSequence;
                try {
                    const preferences = await requestBackgroundPreferences();
                    if (requestId !== backgroundPreferencesReadSequence) return;
                    acceptSharedBackgroundPreferences(preferences);
                    await applyDisplayedBackground(backgroundUrlForMode(activeBackgroundMode), {
                        fallbackToDefault: true
                    });
                    if (requestId !== backgroundPreferencesReadSequence) return;
                    if (backgroundDialogIsOpen()) {
                        setBackgroundStatus(`已读取共享设置，正在配置${backgroundModeLabels[editingBackgroundMode]}背景。`, 'success');
                    }
                } catch (error) {
                    if (requestId !== backgroundPreferencesReadSequence) return;
                    backgroundPreferencesConnection = 'offline';
                    syncBackgroundControlsBusy();
                    console.warn('无法读取共享背景状态，继续使用本地缓存或默认背景。', error);
                    if (backgroundDialogIsOpen()) {
                        setBackgroundStatus('跨设备同步服务暂不可用，已继续使用最近缓存或默认背景。', 'error');
                    }
                }
            }

            async function reconcileSharedBackgroundPreference(mode, fileName, writeId, patchError) {
                const requestId = ++backgroundPreferencesReadSequence;
                try {
                    const preferences = await requestBackgroundPreferences();
                    if (writeId !== backgroundPreferencesWriteSequence || requestId !== backgroundPreferencesReadSequence) {
                        return false;
                    }

                    acceptSharedBackgroundPreferences(preferences);
                    await applyDisplayedBackground(backgroundUrlForMode(currentOrientationMode()), {
                        fallbackToDefault: true
                    });
                    if (writeId !== backgroundPreferencesWriteSequence || requestId !== backgroundPreferencesReadSequence) {
                        return false;
                    }

                    if (preferences[mode] === fileName) {
                        setBackgroundStatus(`连接中断后已核对：${backgroundModeLabels[mode]}背景已同步为 ${fileName}`, 'success');
                        return true;
                    }

                    setBackgroundStatus('当前读取仍为服务器原设置；原同步请求结果可能尚未确认，请稍后刷新核对。', 'error');
                    return false;
                } catch (reconcileError) {
                    if (writeId !== backgroundPreferencesWriteSequence || requestId !== backgroundPreferencesReadSequence) {
                        return false;
                    }
                    backgroundPreferencesConnection = 'offline';
                    syncBackgroundControlsBusy();
                    console.warn('共享背景设置失败，且无法向服务器核对最终状态。', patchError, reconcileError);
                    setBackgroundStatus('同步请求结果暂时无法确认；请恢复连接后刷新页面核对。', 'error');
                    return false;
                }
            }

            async function saveSharedBackgroundPreference(mode, url) {
                if (backgroundManagerIsBusy() || !backgroundModes.includes(mode)) return false;
                const candidate = normalizeBackgroundUrl(url);
                if (!candidate) {
                    setBackgroundStatus('该图片地址无效，未修改共享设置。', 'error');
                    return false;
                }

                const fileName = normalizeBackgroundName(backgroundFileName(candidate));
                if (!fileName) {
                    setBackgroundStatus('该图片文件名无效，未修改共享设置。', 'error');
                    return false;
                }

                backgroundWritePending = true;
                const writeId = ++backgroundPreferencesWriteSequence;
                syncBackgroundControlsBusy();
                setBackgroundStatus(`正在将 ${fileName} 同步为${backgroundModeLabels[mode]}背景…`);

                try {
                    try {
                        await preloadBackground(candidate);
                    } catch (imageError) {
                        console.warn('所选背景图片无法加载，未发送同步请求。', imageError);
                        setBackgroundStatus('所选图片无法加载，未修改共享设置。', 'error');
                        return false;
                    }

                    ++backgroundPreferencesReadSequence;
                    ++backgroundDisplaySequence;
                    backgroundLayer.classList.remove('is-changing');

                    let preferences;
                    try {
                        preferences = await requestBackgroundPreferences('PATCH', {
                            mode,
                            background: fileName
                        });
                    } catch (patchError) {
                        console.warn('共享背景写入请求失败，正在读取服务器状态进行核对。', patchError);
                        setBackgroundStatus('同步连接中断，正在核对服务器最终状态…');
                        return await reconcileSharedBackgroundPreference(mode, fileName, writeId, patchError);
                    }
                    if (writeId !== backgroundPreferencesWriteSequence) return false;
                    acceptSharedBackgroundPreferences(preferences);

                    activeBackgroundMode = currentOrientationMode();
                    const displayed = await applyDisplayedBackground(backgroundUrlForMode(activeBackgroundMode), {
                        fallbackToDefault: true
                    });
                    const storedName = preferences[mode];
                    if (!displayed && activeBackgroundMode === mode) {
                        setBackgroundStatus(`${backgroundModeLabels[mode]}设置已同步，但图片暂时无法加载。`, 'error');
                    } else if (storedName === fileName) {
                        setBackgroundStatus(`${backgroundModeLabels[mode]}背景已同步为 ${fileName}`, 'success');
                    } else {
                        setBackgroundStatus('服务器状态已更新，当前选择已按服务器结果刷新。', 'success');
                    }
                    return true;
                } finally {
                    backgroundWritePending = false;
                    if (backgroundDialogIsOpen() && !editingBackgroundModeTouched) {
                        setEditingBackgroundMode(currentOrientationMode(), false);
                    }
                    syncBackgroundControlsBusy();
                    updateBackgroundModeControls();
                    updateBackgroundSelection();
                    if (backgroundDialogIsOpen()) {
                        const focusTarget = Array.from(backgroundGrid.querySelectorAll('.background-option'))
                            .find((option) => option.dataset.url === candidate.href && !option.disabled);
                        focusWithoutScroll(focusTarget);
                    }
                }
            }

            function renderBackgroundOptions(backgrounds) {
                const fragment = document.createDocumentFragment();
                const groupedBackgrounds = {
                    ordinary: [],
                    bestdori: []
                };

                backgroundLibrarySources.forEach(cancelBackgroundSectionTransition);
                backgrounds.forEach((background) => {
                    groupedBackgrounds[
                        backgroundSourceForName(backgroundFileName(background.url))
                    ].push(background);
                });

                backgroundLibrarySources.forEach((source) => {
                    const section = document.createElement('section');
                    section.className = 'background-library-section';
                    section.dataset.backgroundSection = source;

                    const sectionContentId = `background-section-${source}`;
                    const toggle = document.createElement('button');
                    toggle.type = 'button';
                    toggle.className = 'background-library-toggle';
                    toggle.setAttribute('aria-controls', sectionContentId);
                    toggle.setAttribute('aria-expanded', String(backgroundLibraryExpanded[source]));

                    const title = document.createElement('span');
                    title.className = 'background-library-title';
                    title.textContent = backgroundLibraryLabels[source];

                    const count = document.createElement('span');
                    count.className = 'background-library-count';
                    count.textContent = `${groupedBackgrounds[source].length} 张`;

                    const chevron = document.createElement('span');
                    chevron.className = 'background-library-chevron';
                    chevron.setAttribute('aria-hidden', 'true');
                    chevron.textContent = '▼';
                    toggle.append(title, count, chevron);

                    const sectionContent = document.createElement('div');
                    sectionContent.id = sectionContentId;
                    sectionContent.className = 'background-section-collapse';
                    sectionContent.dataset.expanded = String(backgroundLibraryExpanded[source]);
                    sectionContent.setAttribute('aria-hidden', String(!backgroundLibraryExpanded[source]));
                    sectionContent.toggleAttribute('inert', !backgroundLibraryExpanded[source]);

                    const sectionClip = document.createElement('div');
                    sectionClip.className = 'background-section-clip';

                    const sectionGrid = document.createElement('div');
                    sectionGrid.className = 'background-section-grid';

                    groupedBackgrounds[source].forEach(({ url, thumbnailUrl }) => {
                        const option = document.createElement('button');
                        option.type = 'button';
                        option.className = 'background-option';
                        option.dataset.url = url.href;
                        option.dataset.backgroundSource = source;
                        option.setAttribute('aria-pressed', 'false');

                        const thumbnail = document.createElement('img');
                        const thumbnailSource = thumbnailUrl || url;
                        thumbnail.src = thumbnailSource.href;
                        thumbnail.alt = '';
                        thumbnail.loading = 'lazy';
                        thumbnail.decoding = 'async';
                        if (thumbnailSource.href !== url.href) {
                            thumbnail.addEventListener('error', () => {
                                thumbnail.src = url.href;
                            }, { once: true });
                        }

                        const label = document.createElement('span');
                        label.className = 'background-option-label';
                        label.textContent = backgroundFileName(url);

                        const check = document.createElement('span');
                        check.className = 'background-option-check';
                        check.setAttribute('aria-hidden', 'true');
                        check.textContent = '✓';

                        option.append(thumbnail, label);
                        if (source === 'bestdori') {
                            const sourceBadge = document.createElement('span');
                            sourceBadge.className = 'background-option-source';
                            sourceBadge.textContent = 'Bestdori';
                            option.append(sourceBadge);
                        }
                        option.append(check);
                        option.addEventListener('click', () => saveSharedBackgroundPreference(editingBackgroundMode, url));
                        sectionGrid.append(option);
                    });

                    if (groupedBackgrounds[source].length === 0) {
                        const empty = document.createElement('p');
                        empty.className = 'background-section-empty';
                        empty.textContent = source === 'bestdori'
                            ? '还没有通过 Bestdori 抽取卡面。'
                            : '还没有普通壁纸。';
                        sectionGrid.append(empty);
                    }

                    sectionClip.append(sectionGrid);
                    sectionContent.append(sectionClip);
                    toggle.addEventListener('click', () => {
                        setBackgroundSectionExpanded(source, !backgroundLibraryExpanded[source]);
                    });
                    section.append(toggle, sectionContent);
                    fragment.append(section);
                });

                backgroundGrid.replaceChildren(fragment);
                updateBackgroundSelection();
                syncBackgroundControlsBusy();
                return {
                    total: backgrounds.length,
                    ordinary: groupedBackgrounds.ordinary.length,
                    bestdori: groupedBackgrounds.bestdori.length
                };
            }

            function cancelBackgroundSectionTransition(source) {
                const cleanup = backgroundSectionTransitionCleanups.get(source);
                if (cleanup) cleanup();
            }

            function runAfterBackgroundSectionTransition(source, sectionContent, callback) {
                if (typeof callback !== 'function') return;
                if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
                    callback();
                    return;
                }

                let active = true;
                let timeoutId = 0;
                const onTransitionEnd = (event) => {
                    if (event.target === sectionContent && event.propertyName === 'grid-template-rows') {
                        finish();
                    }
                };
                const cleanup = () => {
                    if (!active) return;
                    active = false;
                    sectionContent.removeEventListener('transitionend', onTransitionEnd);
                    if (timeoutId) window.clearTimeout(timeoutId);
                    if (backgroundSectionTransitionCleanups.get(source) === cleanup) {
                        backgroundSectionTransitionCleanups.delete(source);
                    }
                };
                const finish = () => {
                    if (!active) return;
                    cleanup();
                    callback();
                };

                sectionContent.addEventListener('transitionend', onTransitionEnd);
                timeoutId = window.setTimeout(finish, backgroundSectionTransitionFallbackMs);
                backgroundSectionTransitionCleanups.set(source, cleanup);
            }

            function setBackgroundSectionExpanded(source, expanded, options = {}) {
                if (!backgroundLibrarySources.includes(source)) return false;
                const section = Array.from(backgroundGrid.querySelectorAll('[data-background-section]'))
                    .find((item) => item.dataset.backgroundSection === source);
                if (!section) return false;

                const toggle = section.querySelector('.background-library-toggle');
                const sectionContent = section.querySelector('.background-section-collapse');
                if (!toggle || !sectionContent) return false;

                const nextExpanded = Boolean(expanded);
                const changed = backgroundLibraryExpanded[source] !== nextExpanded;
                cancelBackgroundSectionTransition(source);
                if (changed) void sectionContent.offsetHeight;
                backgroundLibraryExpanded[source] = nextExpanded;
                toggle.setAttribute('aria-expanded', String(nextExpanded));
                sectionContent.dataset.expanded = String(nextExpanded);
                sectionContent.setAttribute('aria-hidden', String(!nextExpanded));
                sectionContent.toggleAttribute('inert', !nextExpanded);

                if (typeof options.afterTransition === 'function') {
                    if (changed) {
                        runAfterBackgroundSectionTransition(source, sectionContent, options.afterTransition);
                    } else {
                        options.afterTransition();
                    }
                }
                return true;
            }

            function focusBackgroundOption(fileName) {
                const candidate = backgroundUrlFromName(fileName);
                if (!candidate) return false;
                const option = Array.from(backgroundGrid.querySelectorAll('.background-option'))
                    .find((item) => item.dataset.url === candidate.href && !item.disabled);
                if (!option) return false;

                const focusOption = () => {
                    if (!option.isConnected || option.closest('[inert]')) return;
                    if (typeof option.scrollIntoView === 'function') {
                        option.scrollIntoView({ block: 'nearest', inline: 'nearest' });
                    }
                    focusWithoutScroll(option);
                };
                return setBackgroundSectionExpanded(backgroundSourceForName(fileName), true, {
                    afterTransition: focusOption
                });
            }

            function formatBackgroundUploadBytes(value) {
                if (!Number.isFinite(value) || value < 0) return '未知大小';
                if (value < 1024) return `${value} B`;
                if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
                return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
            }

            function createBackgroundUploadError(message, options = {}) {
                const error = new Error(message);
                error.isGlobal = options.isGlobal === true;
                error.isRetryable = options.isRetryable === true;
                error.isCancelled = options.isCancelled === true;
                return error;
            }

            function createBackgroundUploadItem(file, index) {
                const validationError = validateBackgroundUploadFile(file);
                return {
                    id: index + 1,
                    file,
                    name: file && typeof file.name === 'string' ? file.name : `文件 ${index + 1}`,
                    size: file && typeof file.size === 'number' ? file.size : 0,
                    uploadedBytes: 0,
                    progress: 0,
                    status: validationError ? 'skipped' : 'waiting',
                    message: validationError || '等待上传',
                    uploadedName: '',
                    retryable: false
                };
            }

            function backgroundUploadItemIsTerminal(item) {
                return ['success', 'skipped', 'failed', 'cancelled'].includes(item.status);
            }

            function backgroundUploadStatusLabel(status) {
                const labels = {
                    waiting: '等待上传',
                    uploading: '正在上传',
                    verifying: '服务端验证中',
                    success: '上传成功',
                    skipped: '已跳过',
                    failed: '上传失败',
                    cancelled: '已取消'
                };
                return labels[status] || '等待上传';
            }

            function renderBackgroundUploadItems(items) {
                backgroundUploadList.replaceChildren();
                items.forEach((item) => {
                    const row = document.createElement('li');
                    row.className = 'background-upload-item';
                    row.dataset.uploadId = String(item.id);

                    const head = document.createElement('div');
                    head.className = 'background-upload-item-head';
                    const name = document.createElement('span');
                    name.className = 'background-upload-file-name';
                    name.textContent = item.name;
                    name.title = item.name;
                    const size = document.createElement('span');
                    size.className = 'background-upload-file-size';
                    size.textContent = formatBackgroundUploadBytes(item.size);
                    head.append(name, size);

                    const progress = document.createElement('progress');
                    progress.className = 'background-upload-progress background-upload-item-progress';
                    progress.max = 100;
                    progress.value = item.progress;
                    progress.setAttribute('aria-label', `${item.name} 上传进度`);

                    const status = document.createElement('p');
                    status.className = 'background-upload-item-status';
                    row.append(head, progress, status);
                    backgroundUploadList.append(row);
                    updateBackgroundUploadItem(item);
                });
            }

            function updateBackgroundUploadItem(item) {
                const row = backgroundUploadList.querySelector(`[data-upload-id="${item.id}"]`);
                if (!row) return;
                row.dataset.status = item.status;
                const progress = row.querySelector('.background-upload-item-progress');
                const status = row.querySelector('.background-upload-item-status');
                if (progress) progress.value = item.progress;
                if (status) {
                    const prefix = backgroundUploadStatusLabel(item.status);
                    status.textContent = item.message ? `${prefix} · ${item.message}` : prefix;
                }
            }

            function backgroundUploadCounts(items) {
                return items.reduce((counts, item) => {
                    counts[item.status] = (counts[item.status] || 0) + 1;
                    return counts;
                }, {});
            }

            function updateBackgroundUploadSummary(kind = '') {
                const session = backgroundUploadSession;
                if (!session) return;
                const counts = backgroundUploadCounts(session.items);
                const processed = session.items.filter(backgroundUploadItemIsTerminal).length;
                const validItems = session.items.filter((item) => item.status !== 'skipped');
                const totalBytes = validItems.reduce((total, item) => total + item.size, 0);
                const completedBytes = validItems.reduce((total, item) => {
                    if (backgroundUploadItemIsTerminal(item)) return total + item.size;
                    return total + Math.min(item.size, item.uploadedBytes);
                }, 0);
                let percent = totalBytes > 0 ? Math.round((completedBytes / totalBytes) * 100) : 100;
                if (session.running && percent >= 100) percent = 99;
                if (!session.running && processed === session.items.length) percent = 100;

                backgroundUploadProgress.value = Math.max(0, Math.min(100, percent));
                backgroundUploadCount.textContent = `${processed} / ${session.items.length}`;
                backgroundUploadList.setAttribute('aria-busy', String(session.running));
                if (session.refreshing) {
                    backgroundUploadSummary.textContent = '正在刷新背景清单…';
                } else if (session.running) {
                    backgroundUploadSummary.textContent = '正在上传壁纸…';
                } else {
                    backgroundUploadSummary.textContent = `已上传 ${counts.success || 0}，跳过 ${counts.skipped || 0}，失败 ${counts.failed || 0}，取消 ${counts.cancelled || 0}`;
                }

                const retryable = session.items.filter(
                    (item) => item.retryable && ['failed', 'cancelled'].includes(item.status)
                ).length;
                const hasPendingUpload = session.items.some(
                    (item) => ['waiting', 'uploading', 'verifying'].includes(item.status)
                );
                backgroundUploadStop.disabled = !session.running || session.refreshing || !hasPendingUpload;
                backgroundUploadRetry.hidden = session.running || retryable === 0;
                backgroundUploadRetry.textContent = retryable > 0 ? `重试失败项（${retryable}）` : '重试失败项';
                backgroundUploadDone.disabled = session.running;

                if (kind) {
                    backgroundUploadStatus.dataset.kind = kind;
                } else {
                    delete backgroundUploadStatus.dataset.kind;
                }
            }

            function setBackgroundUploadStatus(message, kind = '') {
                backgroundUploadStatus.textContent = message;
                updateBackgroundUploadSummary(kind);
            }

            function backgroundUploadDialogIsOpen() {
                return backgroundUploadDialog.open || backgroundUploadDialog.hasAttribute('open');
            }

            function openBackgroundUploadDialog() {
                if (typeof backgroundUploadDialog.showModal === 'function') {
                    if (!backgroundUploadDialog.open) backgroundUploadDialog.showModal();
                } else {
                    backgroundUploadDialog.setAttribute('open', '');
                }
                window.requestAnimationFrame(() => {
                    window.requestAnimationFrame(() => {
                        if (!backgroundUploadDialogIsOpen()) return;
                        backgroundUploadDialog.classList.add('is-visible');
                        focusWithoutScroll(backgroundUploadSession?.running ? backgroundUploadStop : backgroundUploadDone);
                    });
                });
            }

            function closeBackgroundUploadDialog(returnValue = '') {
                if (!backgroundUploadDialogIsOpen() || backgroundUploadSession?.running) return false;
                backgroundUploadDialog.classList.remove('is-visible');
                if (typeof backgroundUploadDialog.close === 'function' && backgroundUploadDialog.open) {
                    backgroundUploadDialog.close(returnValue);
                } else {
                    backgroundUploadDialog.removeAttribute('open');
                }
                backgroundUploadSession = null;
                focusWithoutScroll(backgroundUploadButton);
                return true;
            }

            function requestBackgroundUpload(file, onProgress) {
                return new Promise((resolve, reject) => {
                    const endpoint = new URL(backgroundUploadEndpoint.href);
                    endpoint.searchParams.set('name', file.name);
                    const request = new XMLHttpRequest();
                    let settled = false;

                    const finish = (callback, value) => {
                        if (settled) return;
                        settled = true;
                        if (backgroundUploadRequest === request) backgroundUploadRequest = null;
                        callback(value);
                    };
                    const rejectWith = (message, options = {}) => {
                        finish(reject, createBackgroundUploadError(message, options));
                    };

                    backgroundUploadRequest = request;
                    request.open('POST', endpoint.href, true);
                    request.timeout = 60000;
                    request.withCredentials = true;
                    request.setRequestHeader('Accept', 'application/json');
                    request.setRequestHeader('Content-Type', 'application/octet-stream');
                    request.setRequestHeader('X-Requested-With', 'homelab-dashboard');
                    request.upload.addEventListener('progress', (event) => {
                        if (event.lengthComputable) onProgress(event.loaded, event.total, false);
                    });
                    request.upload.addEventListener('load', () => {
                        onProgress(file.size, file.size, true);
                    });
                    request.addEventListener('load', () => {
                        let payload = null;
                        try {
                            payload = request.responseText ? JSON.parse(request.responseText) : null;
                        } catch (error) {
                            // Status and response text still determine how the queue proceeds.
                        }

                        if (request.status >= 200 && request.status < 300) {
                            const uploadedName = normalizeBackgroundName(payload && payload.background);
                            if (!uploadedName) {
                                rejectWith('上传接口返回了无效文件名', { isGlobal: true });
                                return;
                            }
                            finish(resolve, { payload, uploadedName });
                            return;
                        }

                        const responseError = payload && payload.error;
                        const errorCode = typeof responseError === 'string'
                            ? responseError
                            : responseError && typeof responseError.code === 'string'
                                ? responseError.code
                                : payload && typeof payload.code === 'string' ? payload.code : '';
                        const detail = payload && (
                            payload.message
                            || (typeof responseError === 'string' ? responseError : '')
                            || errorCode
                        );
                        const quotaFull = request.status === 409 && errorCode === 'background_file_quota_exceeded';
                        const globalStatus = [401, 403, 404, 405, 502, 503, 504].includes(request.status);
                        const retryable = request.status === 500 || [502, 503, 504].includes(request.status);
                        const message = quotaFull
                            ? '背景目录已达到 512 张配额，剩余文件未上传。'
                            : typeof detail === 'string' && detail
                                ? detail
                                : `上传接口返回 ${request.status || '未知状态'}`;
                        rejectWith(message, {
                            isGlobal: quotaFull || globalStatus,
                            isRetryable: !quotaFull && retryable
                        });
                    });
                    request.addEventListener('error', () => {
                        rejectWith('无法连接背景管理服务，剩余文件未上传。', {
                            isGlobal: true,
                            isRetryable: true
                        });
                    });
                    request.addEventListener('timeout', () => {
                        rejectWith('上传请求超时，剩余文件未上传。', {
                            isGlobal: true,
                            isRetryable: true
                        });
                    });
                    request.addEventListener('abort', () => {
                        rejectWith('用户已停止上传。', {
                            isGlobal: true,
                            isRetryable: true,
                            isCancelled: true
                        });
                    });
                    request.send(file);
                });
            }

            async function executeBackgroundUploadQueue(items, uploadOne, onSettled, shouldStop) {
                let processed = 0;
                for (const item of items) {
                    if (shouldStop()) break;
                    let result = null;
                    let error = null;
                    try {
                        result = await uploadOne(item);
                    } catch (uploadError) {
                        error = uploadError;
                    }
                    await onSettled(item, result, error);
                    processed += 1;
                    if (error?.isGlobal) break;
                }
                return {
                    processed,
                    remaining: items.slice(processed)
                };
            }

            async function runBackgroundUploadQueue(items) {
                const session = backgroundUploadSession;
                if (!session || session.running || items.length === 0) return false;

                session.running = true;
                session.refreshing = false;
                session.stopRequested = false;
                session.stopReason = '';
                session.stopRetryable = false;
                backgroundUploadPending = true;
                syncBackgroundControlsBusy();
                setBackgroundUploadStatus('队列已开始，上传期间请保持此页面打开。');
                let requestStarted = false;

                try {
                    await executeBackgroundUploadQueue(
                        items.filter((item) => item.status === 'waiting'),
                        async (item) => {
                            item.status = 'uploading';
                            item.message = '0%';
                            item.uploadedBytes = 0;
                            item.progress = 0;
                            item.retryable = false;
                            updateBackgroundUploadItem(item);
                            updateBackgroundUploadSummary();
                            requestStarted = true;
                            return requestBackgroundUpload(item.file, (loaded, total, verifying) => {
                                item.uploadedBytes = Math.max(0, Math.min(item.size, loaded));
                                item.progress = total > 0
                                    ? Math.max(0, Math.min(100, Math.round((loaded / total) * 100)))
                                    : 0;
                                item.status = verifying ? 'verifying' : 'uploading';
                                item.message = verifying ? '已传输，正在校验图片并生成缩略图' : `${item.progress}%`;
                                updateBackgroundUploadItem(item);
                                updateBackgroundUploadSummary();
                            });
                        },
                        async (item, result, error) => {
                            if (!error) {
                            item.status = 'success';
                            item.progress = 100;
                            item.uploadedBytes = item.size;
                            item.uploadedName = result.uploadedName;
                            item.message = result.uploadedName === item.name
                                ? result.uploadedName
                                : `${item.name} → ${result.uploadedName}`;
                            session.lastUploadedName = result.uploadedName;
                            } else {
                                console.warn(`背景图片上传失败：${item.name}`, error);
                                item.status = error.isCancelled ? 'cancelled' : 'failed';
                                item.message = error.message || '未知错误';
                                item.retryable = error.isRetryable === true;
                                if (error.isGlobal) {
                                    session.stopRequested = true;
                                    session.stopReason = item.message;
                                    session.stopRetryable = error.isRetryable === true;
                                }
                            }
                            updateBackgroundUploadItem(item);
                            updateBackgroundUploadSummary(session.stopRequested ? 'error' : '');
                        },
                        () => session.stopRequested
                    );

                    if (session.stopRequested) {
                        session.items.forEach((item) => {
                            if (item.status !== 'waiting') return;
                            item.status = 'cancelled';
                            item.message = session.stopReason || '队列已停止';
                            item.retryable = session.stopRetryable;
                            updateBackgroundUploadItem(item);
                        });
                    }

                    if (requestStarted) {
                        session.refreshing = true;
                        updateBackgroundUploadSummary(session.stopRequested ? 'error' : '');
                        const counts = backgroundUploadCounts(session.items);
                        await scanBackgroundDirectory({
                            successMessage: `批量上传完成：成功 ${counts.success || 0}，跳过 ${counts.skipped || 0}，失败 ${counts.failed || 0}，取消 ${counts.cancelled || 0}；未更改当前背景。`
                        });
                    }
                } finally {
                    session.refreshing = false;
                    session.running = false;
                    backgroundUploadRequest = null;
                    backgroundUploadPending = false;
                    syncBackgroundControlsBusy();
                    const counts = backgroundUploadCounts(session.items);
                    const kind = (counts.failed || counts.skipped || counts.cancelled) ? 'error' : 'success';
                    setBackgroundUploadStatus(
                        `已上传 ${counts.success || 0} 张，跳过 ${counts.skipped || 0} 张，失败 ${counts.failed || 0} 张，取消 ${counts.cancelled || 0} 张。`,
                        kind
                    );
                    if (session.lastUploadedName) focusBackgroundOption(session.lastUploadedName);
                    focusWithoutScroll(backgroundUploadRetry.hidden ? backgroundUploadDone : backgroundUploadRetry);
                }
                return true;
            }

            async function uploadBackgroundFiles(files) {
                if (!Array.isArray(files) || files.length === 0) return false;
                if (backgroundPreferencesConnection !== 'online' || backgroundManagerIsBusy()) {
                    setBackgroundStatus('背景管理服务正忙或暂不可用，请稍后再试。', 'error');
                    return false;
                }

                const items = files.map(createBackgroundUploadItem);
                backgroundUploadSession = {
                    items,
                    running: false,
                    refreshing: false,
                    stopRequested: false,
                    stopReason: '',
                    stopRetryable: false,
                    lastUploadedName: ''
                };
                renderBackgroundUploadItems(items);
                setBackgroundUploadStatus('正在准备上传队列…');
                openBackgroundUploadDialog();

                const queued = items.filter((item) => item.status === 'waiting');
                if (queued.length === 0) {
                    setBackgroundUploadStatus('所选文件均未通过上传预检，请查看逐项原因。', 'error');
                    return false;
                }
                return runBackgroundUploadQueue(queued);
            }

            function stopBackgroundUploadQueue() {
                const session = backgroundUploadSession;
                if (!session?.running || session.refreshing) return;
                if (!window.confirm('停止当前上传并取消剩余队列？已经上传成功的壁纸会保留。')) return;
                session.stopRequested = true;
                session.stopReason = '用户已停止上传。';
                session.stopRetryable = true;
                backgroundUploadStop.disabled = true;
                setBackgroundUploadStatus('正在停止当前上传…', 'error');
                if (backgroundUploadRequest) backgroundUploadRequest.abort();
            }

            function retryBackgroundUploadFailures() {
                const session = backgroundUploadSession;
                if (!session || session.running) return;
                const retryItems = session.items.filter(
                    (item) => item.retryable && ['failed', 'cancelled'].includes(item.status)
                );
                retryItems.forEach((item) => {
                    item.status = 'waiting';
                    item.message = '等待重试';
                    item.progress = 0;
                    item.uploadedBytes = 0;
                    item.uploadedName = '';
                    item.retryable = false;
                    updateBackgroundUploadItem(item);
                });
                if (retryItems.length > 0) runBackgroundUploadQueue(retryItems);
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
                syncBackgroundControlsBusy();
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
                    syncBackgroundControlsBusy();
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
                if (backgroundManagerIsBusy() || backgroundPreferencesConnection !== 'online') {
                    setBackgroundStatus('背景管理服务正忙或暂不可用，请稍后再试。', 'error');
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
                if (backgroundManagerIsBusy() || backgroundPreferencesConnection !== 'online') {
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
                syncBackgroundControlsBusy();
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
                    setBackgroundStatus(message, 'error');
                    return false;
                } finally {
                    backgroundDrawPending = false;
                    syncBackgroundControlsBusy();
                    syncCardDrawControls();
                    if (succeeded) {
                        closeCardDrawDialog('drawn', false);
                        focusBackgroundOption(drawnName);
                    }
                }
            }

            async function reconcileBestdoriHistory() {
                if (backgroundManagerIsBusy()) return false;

                backgroundDrawPending = true;
                syncBackgroundControlsBusy();
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
                    syncBackgroundControlsBusy();
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
                if (backgroundManagerIsBusy()) return false;
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
                if (!cardHistoryResetDialogIsOpen() || backgroundManagerIsBusy()) return false;

                let succeeded = false;
                backgroundDrawPending = true;
                syncBackgroundControlsBusy();
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
                    syncBackgroundControlsBusy();
                    syncCardDrawControls();
                    syncCardHistoryResetControls();
                    if (succeeded) closeCardHistoryResetDialog('reset');
                }
            }

            function backgroundDeleteDialogIsOpen() {
                return backgroundDeleteDialog.open || backgroundDeleteDialog.hasAttribute('open');
            }

            function syncBackgroundDeleteDialogControls() {
                backgroundDeleteCancel.disabled = backgroundDeletePending;
                backgroundDeleteConfirm.disabled = backgroundDeletePending;
                backgroundDeleteConfirm.setAttribute('aria-busy', String(backgroundDeletePending));
            }

            function deletionImpactText(fileName, modes) {
                const modeText = modes.length
                    ? `${modes.map((mode) => backgroundModeLabels[mode]).join('、')}设置会回退到 ${defaultBackgroundName}。`
                    : '当前共享设置不会改变。';
                return `目标文件：“${fileName}”。${modeText}`;
            }

            function showBackgroundDeleteStage(stage, confirmationToken = '', expiresIn = 0) {
                if (!backgroundDeleteRequest) return;
                backgroundDeleteRequest.stage = stage;
                backgroundDeleteRequest.confirmationToken = confirmationToken;
                backgroundDeleteTitle.textContent = stage === 'permanent' ? '永久删除背景图片？' : '删除背景图片？';
                backgroundDeleteDescription.textContent = stage === 'permanent'
                    ? `无法将图片移入系统回收站。永久删除后无法恢复。${deletionImpactText(backgroundDeleteRequest.background, backgroundDeleteRequest.modes)}`
                    : `图片会先移入系统回收站。${deletionImpactText(backgroundDeleteRequest.background, backgroundDeleteRequest.modes)}`;
                backgroundDeleteConfirm.textContent = stage === 'permanent' ? '永久删除' : '移入回收站';
                backgroundDeleteStatus.textContent = stage === 'permanent'
                    ? `请再次确认永久删除${expiresIn > 0 ? `；确认将在 ${expiresIn} 秒后失效` : ''}。`
                    : '';
            }

            function closeBackgroundDeleteDialog(returnValue = '', restoreFocus = true) {
                if (backgroundDeletePending) return;
                const returnFocus = backgroundDeleteReturnFocus;
                backgroundDeleteDialog.classList.remove('is-visible');
                if (typeof backgroundDeleteDialog.close === 'function' && backgroundDeleteDialog.open) {
                    backgroundDeleteDialog.close(returnValue);
                } else {
                    backgroundDeleteDialog.removeAttribute('open');
                }
                backgroundDeleteRequest = null;
                backgroundDeleteReturnFocus = null;
                backgroundDeleteStatus.textContent = '';
                if (restoreFocus) {
                    window.requestAnimationFrame(() => focusWithoutScroll(returnFocus));
                }
            }

            function openBackgroundDeleteDialog() {
                const fileName = normalizeBackgroundName(backgroundPreferences[editingBackgroundMode]);
                const isBusy = backgroundManagerIsBusy();
                if (
                    !fileName
                    || fileName.toLowerCase() === defaultBackgroundName
                    || backgroundPreferencesConnection !== 'online'
                    || isBusy
                ) return false;

                backgroundDeleteReturnFocus = document.activeElement || backgroundDeleteButton;
                backgroundDeleteRequest = {
                    background: fileName,
                    modes: backgroundModesUsingFile(fileName, backgroundPreferences),
                    stage: 'trash',
                    confirmationToken: ''
                };
                showBackgroundDeleteStage('trash');
                syncBackgroundDeleteDialogControls();

                if (typeof backgroundDeleteDialog.showModal === 'function') {
                    if (!backgroundDeleteDialog.open) backgroundDeleteDialog.showModal();
                } else {
                    backgroundDeleteDialog.setAttribute('open', '');
                }

                const revealAndFocus = () => {
                    if (!backgroundDeleteDialogIsOpen()) return;
                    backgroundDeleteDialog.classList.add('is-visible');
                    focusWithoutScroll(backgroundDeleteConfirm);
                };
                if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
                    revealAndFocus();
                } else {
                    window.requestAnimationFrame(() => window.requestAnimationFrame(revealAndFocus));
                }
                return true;
            }

            async function requestBackgroundRemoval(request) {
                return fetchWithTimeout(backgroundRemoveEndpoint.href, {
                    method: 'POST',
                    cache: 'no-store',
                    credentials: 'same-origin',
                    headers: {
                        Accept: 'application/json',
                        'Content-Type': 'application/json',
                        'X-Requested-With': 'homelab-dashboard'
                    },
                    body: JSON.stringify(request)
                }, 10000, null, async (response) => {
                    let payload = null;
                    try {
                        payload = await response.json();
                    } catch (error) {
                        // A non-JSON intermediary error is reported using its HTTP status below.
                    }

                    const errorCode = payload && (
                        typeof payload.error === 'string'
                            ? payload.error
                            : payload.error && payload.error.code
                    );
                    if (response.status === 409 && errorCode === 'trash_unavailable') {
                        const confirmationToken = payload && payload.confirmationToken;
                        if (typeof confirmationToken !== 'string' || !confirmationToken) {
                            throw new Error('删除接口未返回有效的永久删除确认令牌');
                        }
                        return {
                            requiresPermanentConfirmation: true,
                            confirmationToken,
                            expiresIn: Number.isFinite(payload.expiresIn) ? payload.expiresIn : 0
                        };
                    }
                    if (!response.ok) {
                        const detail = payload && (
                            payload.message
                            || (typeof payload.error === 'string' ? payload.error : null)
                            || payload.code
                        );
                        throw new Error(typeof detail === 'string' ? detail : `删除接口返回 ${response.status}`);
                    }
                    return {
                        requiresPermanentConfirmation: false,
                        payload
                    };
                });
            }

            async function confirmBackgroundDeletion() {
                if (!backgroundDeleteRequest || backgroundDeletePending) return false;
                const request = backgroundDeleteRequest;
                const requestBody = { background: request.background };
                if (request.stage === 'permanent') {
                    if (!request.confirmationToken) {
                        backgroundDeleteStatus.textContent = '永久删除确认已失效，请取消后重试。';
                        return false;
                    }
                    requestBody.confirmationToken = request.confirmationToken;
                }

                backgroundDeletePending = true;
                syncBackgroundControlsBusy();
                syncBackgroundDeleteDialogControls();
                backgroundDeleteStatus.textContent = request.stage === 'permanent'
                    ? '正在永久删除图片…'
                    : '正在将图片移入回收站…';

                try {
                    const result = await requestBackgroundRemoval(requestBody);
                    if (result.requiresPermanentConfirmation) {
                        showBackgroundDeleteStage(
                            'permanent',
                            result.confirmationToken,
                            result.expiresIn
                        );
                        setBackgroundStatus('系统回收站不可用；如需继续，请在确认窗口中再次确认永久删除。', 'error');
                        return false;
                    }

                    const preferences = normalizeBackgroundPreferences(result.payload && result.payload.preferences);
                    if (!preferences) throw new Error('删除接口返回了无效背景设置');

                    ++backgroundPreferencesReadSequence;
                    acceptSharedBackgroundPreferences(preferences);
                    activeBackgroundMode = currentOrientationMode();
                    await applyDisplayedBackground(backgroundUrlForMode(activeBackgroundMode), {
                        fallbackToDefault: true
                    });

                    const deletedName = request.background;
                    const deletionLabel = request.stage === 'permanent' ? '已永久删除' : '已移入回收站';
                    backgroundDeletePending = false;
                    syncBackgroundControlsBusy();
                    syncBackgroundDeleteDialogControls();
                    closeBackgroundDeleteDialog('deleted', false);
                    await scanBackgroundDirectory({
                        successMessage: `${deletedName} ${deletionLabel}；相关设置已回退到 ${defaultBackgroundName}。`
                    });
                    focusBackgroundOption(backgroundPreferences[editingBackgroundMode]);
                    return true;
                } catch (error) {
                    console.warn('背景图片删除失败。', error);
                    const message = `删除失败：${error.message || '未知错误'}`;
                    backgroundDeleteStatus.textContent = message;
                    setBackgroundStatus(message, 'error');
                    return false;
                } finally {
                    if (backgroundDeletePending) {
                        backgroundDeletePending = false;
                        syncBackgroundControlsBusy();
                        syncBackgroundDeleteDialogControls();
                        focusWithoutScroll(backgroundDeleteConfirm);
                    }
                }
            }

            function mergeAndSortBackgrounds(backgrounds) {
                const merged = new Map();
                backgrounds.forEach((background) => {
                    merged.set(background.url.href, background);
                });
                const requiredBackgroundUrls = [
                    defaultBackground,
                    currentBackground,
                    ...backgroundModes.map((mode) => backgroundUrlForMode(mode))
                ];
                requiredBackgroundUrls.forEach((requiredUrl) => {
                    if (!merged.has(requiredUrl.href)) {
                        merged.set(requiredUrl.href, {
                            url: requiredUrl,
                            thumbnailUrl: null
                        });
                    }
                });

                const sorted = Array.from(merged.values()).sort((left, right) => {
                    if (left.url.href === defaultBackground.href) return -1;
                    if (right.url.href === defaultBackground.href) return 1;
                    return backgroundFileName(left.url).localeCompare(
                        backgroundFileName(right.url),
                        'zh-CN',
                        { numeric: true }
                    );
                });
                const requiredHrefs = new Set(requiredBackgroundUrls.map((url) => url.href));
                const requiredCount = sorted.filter((background) => requiredHrefs.has(background.url.href)).length;
                let remainingUnselectedSlots = Math.max(0, maximumBackgroundInventoryItems - requiredCount);
                return sorted.filter((background) => {
                    if (requiredHrefs.has(background.url.href)) return true;
                    if (remainingUnselectedSlots === 0) return false;
                    remainingUnselectedSlots -= 1;
                    return true;
                });
            }

            async function requestBackgroundInventory(controller) {
                return fetchWithTimeout(backgroundInventoryEndpoint.href, {
                    cache: 'no-store',
                    credentials: 'same-origin',
                    headers: {
                        Accept: 'application/json'
                    }
                }, 120000, controller, async (response) => {
                    if (!response.ok) throw new Error(`背景清单接口返回 ${response.status}`);
                    let payload;
                    try {
                        payload = JSON.parse(await readBoundedResponseText(response));
                    } catch (error) {
                        throw new Error('背景清单接口返回了非 JSON 响应');
                    }
                    const inventory = normalizeBackgroundInventory(payload);
                    if (!inventory) throw new Error('背景清单接口返回了无效数据');
                    return inventory;
                });
            }

            async function requestLegacyBackgroundListing(controller) {
                const result = await fetchWithTimeout(backgroundDirectory.href, {
                    cache: 'no-store',
                    credentials: 'same-origin'
                }, 5000, controller, async (response) => {
                    if (!response.ok) throw new Error(`目录请求返回 ${response.status}`);
                    return {
                        response,
                        listingText: await readBoundedResponseText(response)
                    };
                });

                const listing = new DOMParser().parseFromString(result.listingText, 'text/html');
                const discovered = new Map();
                listing.querySelectorAll('a[href]').forEach((anchor) => {
                    if (discovered.size >= maximumBackgroundInventoryItems) return;
                    try {
                        const url = normalizeBackgroundUrl(
                            new URL(anchor.getAttribute('href'), result.response.url)
                        );
                        if (url) {
                            discovered.set(url.href, {
                                url,
                                thumbnailUrl: null
                            });
                        }
                    } catch (error) {
                        // Ignore malformed links in the server-generated directory listing.
                    }
                });

                if (discovered.size === 0) throw new Error('目录列表中没有支持的图片链接');
                return Array.from(discovered.values());
            }

            function renderKnownBackgroundFallback() {
                const fallbackUrls = new Map([[defaultBackground.href, defaultBackground]]);
                if (currentBackground.href !== defaultBackground.href) {
                    fallbackUrls.set(currentBackground.href, currentBackground);
                }
                backgroundModes.forEach((mode) => {
                    const configuredUrl = backgroundUrlForMode(mode);
                    fallbackUrls.set(configuredUrl.href, configuredUrl);
                });
                return renderBackgroundOptions(
                    Array.from(fallbackUrls.values()).map((url) => ({
                        url,
                        thumbnailUrl: null
                    }))
                );
            }

            async function scanBackgroundDirectory(options = {}) {
                if (backgroundScanController) backgroundScanController.abort();
                const scanId = ++backgroundScanSequence;
                let controller = typeof AbortController === 'function' ? new AbortController() : null;
                backgroundScanController = controller;
                backgroundScanPending = true;
                syncBackgroundControlsBusy();
                setBackgroundStatus('正在核对背景清单与缩略图…');

                try {
                    let inventory;
                    try {
                        inventory = await requestBackgroundInventory(controller);
                    } catch (inventoryError) {
                        if (scanId !== backgroundScanSequence) return;
                        console.warn('背景清单接口不可用，正在使用原图目录列表降级。', inventoryError);

                        controller = typeof AbortController === 'function' ? new AbortController() : null;
                        backgroundScanController = controller;
                        try {
                            const legacyBackgrounds = await requestLegacyBackgroundListing(controller);
                            if (scanId !== backgroundScanSequence) return;
                            const backgrounds = mergeAndSortBackgrounds(legacyBackgrounds);
                            const counts = renderBackgroundOptions(backgrounds);
                            const inventorySummary = `共 ${counts.total} 张：普通壁纸 ${counts.ordinary} 张，Bestdori 抽卡 ${counts.bestdori} 张`;
                            const prefix = options.successMessage ? `${options.successMessage} ` : '';
                            setBackgroundStatus(
                                `${prefix}已进入原图加载降级模式；${inventorySummary}。`,
                                'error'
                            );
                            return;
                        } catch (listingError) {
                            if (scanId !== backgroundScanSequence) return;
                            console.warn('原图目录列表降级也失败。', listingError);
                            const counts = renderKnownBackgroundFallback();
                            setBackgroundStatus(
                                `已进入原图加载降级模式，但无法读取目录列表；仅保留 ${counts.total} 张已知背景（普通壁纸 ${counts.ordinary} 张，Bestdori 抽卡 ${counts.bestdori} 张）。请确认 Zoraxy 已开启 directory listing。`,
                                'error'
                            );
                            return;
                        }
                    }

                    if (scanId !== backgroundScanSequence) return;
                    const backgrounds = mergeAndSortBackgrounds(inventory.backgrounds);
                    const counts = renderBackgroundOptions(backgrounds);
                    const inventoryScope = inventory.truncated
                        ? (
                            inventory.total === null
                                ? `显示前 ${counts.total} 张`
                                : `显示 ${counts.total}/${inventory.total} 张`
                        )
                        : `共 ${counts.total} 张`;
                    const inventorySummary = `${inventoryScope}：普通壁纸 ${counts.ordinary} 张，Bestdori 抽卡 ${counts.bestdori} 张`;
                    const originalFallbackCount = backgrounds.filter(
                        (background) => !background.thumbnailUrl
                    ).length;
                    const thumbnailMessage = originalFallbackCount > 0
                        ? `，其中 ${originalFallbackCount} 张缩略图不可用，已单张使用原图`
                        : '';

                    if (options.successMessage) {
                        setBackgroundStatus(
                            `${options.successMessage} ${inventorySummary}${thumbnailMessage}。`,
                            originalFallbackCount > 0 ? 'error' : 'success'
                        );
                    } else if (backgroundPreferencesConnection === 'offline') {
                        setBackgroundStatus(
                            `${inventorySummary}${thumbnailMessage}，但跨设备同步服务暂不可用。`,
                            'error'
                        );
                    } else {
                        setBackgroundStatus(
                            `${inventorySummary}${thumbnailMessage}，正在设置${backgroundModeLabels[editingBackgroundMode]}背景。`,
                            originalFallbackCount > 0 ? 'error' : 'success'
                        );
                    }
                } finally {
                    if (scanId === backgroundScanSequence) {
                        backgroundScanController = null;
                        backgroundScanPending = false;
                        syncBackgroundControlsBusy();
                    }
                }
            }

            function setEditingBackgroundMode(mode, announce = true) {
                if (
                    !backgroundModes.includes(mode)
                    || backgroundManagerIsBusy()
                ) return false;
                editingBackgroundMode = mode;
                updateBackgroundModeControls();
                updateBackgroundSelection();
                if (announce) setBackgroundStatus(`请选择一张图片作为${backgroundModeLabels[mode]}背景。`);
                return true;
            }

            backgroundModeButtons.forEach((button) => {
                button.addEventListener('click', () => {
                    if (setEditingBackgroundMode(button.dataset.backgroundMode)) {
                        editingBackgroundModeTouched = true;
                    }
                });
            });

            backgroundUploadButton.addEventListener('click', () => {
                if (!backgroundUploadButton.disabled) backgroundUploadInput.click();
            });

            backgroundUploadInput.addEventListener('change', () => {
                const files = Array.from(backgroundUploadInput.files || []);
                backgroundUploadInput.value = '';
                if (files.length > 0) uploadBackgroundFiles(files);
            });

            backgroundUploadStop.addEventListener('click', stopBackgroundUploadQueue);
            backgroundUploadRetry.addEventListener('click', retryBackgroundUploadFailures);
            backgroundUploadDone.addEventListener('click', () => closeBackgroundUploadDialog('done'));
            backgroundUploadDialog.addEventListener('cancel', (event) => {
                event.preventDefault();
                if (backgroundUploadSession?.running) {
                    setBackgroundUploadStatus('上传期间请使用“停止上传”安全结束队列。', 'error');
                } else {
                    closeBackgroundUploadDialog('cancel');
                }
            });
            backgroundUploadDialog.addEventListener('click', (event) => {
                if (event.target !== backgroundUploadDialog) return;
                if (backgroundUploadSession?.running) {
                    setBackgroundUploadStatus('上传期间不能关闭进度窗口。', 'error');
                } else {
                    closeBackgroundUploadDialog('backdrop');
                }
            });
            backgroundUploadDialog.addEventListener('close', () => {
                backgroundUploadDialog.classList.remove('is-visible');
            });

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

            backgroundDeleteButton.addEventListener('click', openBackgroundDeleteDialog);
            backgroundDeleteCancel.addEventListener('click', () => closeBackgroundDeleteDialog('cancel'));
            backgroundDeleteConfirm.addEventListener('click', confirmBackgroundDeletion);

            backgroundDeleteDialog.addEventListener('cancel', (event) => {
                event.preventDefault();
                if (!backgroundDeletePending) closeBackgroundDeleteDialog('cancel');
            });

            backgroundDeleteDialog.addEventListener('click', (event) => {
                if (event.target === backgroundDeleteDialog && !backgroundDeletePending) {
                    closeBackgroundDeleteDialog('backdrop');
                }
            });

            function backgroundDialogIsOpen() {
                return backgroundDialog.open || backgroundDialog.hasAttribute('open');
            }

            function finishBackgroundDialogClose(returnValue = '') {
                window.clearTimeout(backgroundDialogCloseTimer);
                backgroundDialogCloseTimer = 0;
                backgroundDialog.classList.remove('is-visible', 'is-closing');

                if (typeof backgroundDialog.close === 'function' && backgroundDialog.open) {
                    backgroundDialog.close(returnValue);
                } else {
                    backgroundDialog.removeAttribute('open');
                    focusWithoutScroll(backgroundPickerButton);
                }
            }

            function closeBackgroundDialog(returnValue = '') {
                if (!backgroundDialogIsOpen() || backgroundDialog.classList.contains('is-closing')) return;

                const animationToken = ++backgroundDialogAnimationToken;
                const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
                if (reduceMotion) {
                    finishBackgroundDialogClose(returnValue);
                    return;
                }

                backgroundDialog.classList.add('is-closing');
                backgroundDialog.classList.remove('is-visible');

                const finishAfterTransition = (event) => {
                    if (event && (event.target !== backgroundDialog || event.propertyName !== 'opacity')) return;
                    if (animationToken !== backgroundDialogAnimationToken) return;
                    backgroundDialog.removeEventListener('transitionend', finishAfterTransition);
                    finishBackgroundDialogClose(returnValue);
                };

                backgroundDialog.addEventListener('transitionend', finishAfterTransition);
                backgroundDialogCloseTimer = window.setTimeout(finishAfterTransition, 300);
            }

            function openBackgroundDialog() {
                const animationToken = ++backgroundDialogAnimationToken;
                window.clearTimeout(backgroundDialogCloseTimer);
                backgroundDialogCloseTimer = 0;
                backgroundDialog.classList.remove('is-visible', 'is-closing');
                editingBackgroundModeTouched = false;
                setEditingBackgroundMode(currentOrientationMode(), false);

                if (typeof backgroundDialog.showModal === 'function') {
                    if (!backgroundDialog.open) backgroundDialog.showModal();
                } else {
                    backgroundDialog.setAttribute('open', '');
                }

                if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
                    backgroundDialog.classList.add('is-visible');
                } else {
                    window.requestAnimationFrame(() => {
                        window.requestAnimationFrame(() => {
                            if (animationToken === backgroundDialogAnimationToken && backgroundDialogIsOpen()) {
                                backgroundDialog.classList.add('is-visible');
                            }
                        });
                    });
                }

                scanBackgroundDirectory();
            }

            backgroundPickerButton.addEventListener('click', openBackgroundDialog);

            backgroundDialogCloseForm.addEventListener('submit', (event) => {
                event.preventDefault();
                closeBackgroundDialog(event.submitter?.value || '');
            });

            backgroundDialog.addEventListener('cancel', (event) => {
                event.preventDefault();
                closeBackgroundDialog('cancel');
            });

            backgroundDialog.addEventListener('click', (event) => {
                if (event.target === backgroundDialog) closeBackgroundDialog('backdrop');
            });

            backgroundDialog.addEventListener('close', () => {
                ++backgroundDialogAnimationToken;
                window.clearTimeout(backgroundDialogCloseTimer);
                backgroundDialogCloseTimer = 0;
                backgroundDialog.classList.remove('is-visible', 'is-closing');
            });

            function handleOrientationChange() {
                activeBackgroundMode = currentOrientationMode();
                if (
                    backgroundDialogIsOpen()
                    && !editingBackgroundModeTouched
                    && !backgroundManagerIsBusy()
                ) {
                    setEditingBackgroundMode(activeBackgroundMode, false);
                }
                applyDisplayedBackground(backgroundUrlForMode(activeBackgroundMode), {
                    fallbackToDefault: true
                });
            }

            if (typeof orientationQuery.addEventListener === 'function') {
                orientationQuery.addEventListener('change', handleOrientationChange);
            } else if (typeof orientationQuery.addListener === 'function') {
                orientationQuery.addListener(handleOrientationChange);
            }

            applyDisplayedBackground(backgroundUrlForMode(activeBackgroundMode), {
                animate: false,
                fallbackToDefault: true
            });
            syncBackgroundControlsBusy();
            loadSharedBackgroundPreferences();

            const SEARCH_ENGINE_STORAGE_KEY = 'homelab-search-engine-v1';
            const SEARCH_DEFAULT_PLACEHOLDER = '把问题丢给互联网';
            const SEARCH_ENGINE_SWITCH_DEBOUNCE_MS = 90;
            const SEARCH_ENGINE_TRACK_PHASE_MS = 90;
            const SEARCH_EMPTY_PLACEHOLDERS = Object.freeze([
                'Null 也算问题吗？',
                '关键词不会自己长出来哦',
                '这里是不是该写点啥？',
                '搜索框也需要一点线索',
                '互联网还在等你开口',
                '先给我一个关键词吧',
                '空气不能拿来搜索啦',
                '搜索引擎还没学会读心术'
            ]);
            const SEARCH_ENGINES = Object.freeze([
                Object.freeze({
                    id: 'bing',
                    name: 'Bing',
                    action: 'https://www.bing.com/search',
                    queryParameter: 'q',
                    symbol: '#search-logo-bing'
                }),
                Object.freeze({
                    id: 'google',
                    name: 'Google',
                    action: 'https://www.google.com/search',
                    queryParameter: 'q',
                    symbol: '#search-logo-google'
                }),
                Object.freeze({
                    id: 'duckduckgo',
                    name: 'DuckDuckGo',
                    action: 'https://duckduckgo.com/',
                    queryParameter: 'q',
                    symbol: '#search-logo-duckduckgo'
                }),
                Object.freeze({
                    id: 'baidu',
                    name: '百度',
                    action: 'https://www.baidu.com/s',
                    queryParameter: 'wd',
                    symbol: '#search-logo-baidu'
                })
            ]);

            function resolveSearchEngine(engineId) {
                return SEARCH_ENGINES.find((engine) => engine.id === engineId) || SEARCH_ENGINES[0];
            }

            function clearLegacySearchEnginePreference() {
                try {
                    window.localStorage.removeItem(SEARCH_ENGINE_STORAGE_KEY);
                } catch (error) {
                    // Storage cleanup failure must not prevent an in-memory Bing-first search session.
                }
            }

            function isSearchShortcutEditableTarget(target) {
                if (!target || typeof target.closest !== 'function') return false;
                return Boolean(target.closest(
                    'input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"], [role="combobox"]'
                ));
            }

            function shouldHandleSearchFocusShortcut(event, dialogOpen = false) {
                if (!event
                    || event.code !== 'Backquote'
                    || !event.altKey
                    || event.ctrlKey
                    || event.shiftKey
                    || event.metaKey
                    || event.isComposing
                    || event.repeat
                    || dialogOpen) {
                    return false;
                }
                if (typeof event.getModifierState === 'function' && event.getModifierState('AltGraph')) {
                    return false;
                }
                return !isSearchShortcutEditableTarget(event.target);
            }

            function handleHomepageSearchFocusShortcut(event, controller, dialogOpen = false) {
                if (!shouldHandleSearchFocusShortcut(event, dialogOpen)) return false;
                event.preventDefault();
                controller.setSearchMenuOpen(false, false);
                controller.setSearchExpanded(true, true, false);
                return true;
            }

            function createHomepageSearchController(elements, environment = {}) {
                const {
                    form,
                    input,
                    engineButton,
                    engineIdentity,
                    engineName,
                    submitButton,
                    menu,
                    options,
                    status,
                    currentEngineUses
                } = elements;
                const schedule = environment.schedule || ((callback, delay) => window.setTimeout(callback, delay));
                const cancelSchedule = environment.cancelSchedule || ((timer) => window.clearTimeout(timer));
                const activeElement = environment.activeElement || (() => document.activeElement);
                const reduceMotion = environment.reduceMotion
                    || (() => window.matchMedia('(prefers-reduced-motion: reduce)').matches);
                let currentEngine = SEARCH_ENGINES[0];
                let expanded = false;
                let menuOpen = false;
                let emptyFeedbackTimer = 0;
                let emptyFeedbackIndex = 0;
                let queuedEngineIndex = null;
                let queuedEngineStepDelta = 0;
                let queuedEngineDirection = 'next';
                let engineSwitchDebounceTimer = 0;
                let engineSwitchMidpointTimer = 0;
                let engineSwitchFinishTimer = 0;
                let engineSwitchRevision = 0;
                let engineSwitchQueueRevision = 0;

                function clearEngineSwitchTrackState() {
                    delete form.dataset.engineSwitchPhase;
                    delete form.dataset.engineSwitchDirection;
                }

                function cancelEngineSwitchAnimation() {
                    engineSwitchRevision += 1;
                    if (engineSwitchMidpointTimer) {
                        cancelSchedule(engineSwitchMidpointTimer);
                        engineSwitchMidpointTimer = 0;
                    }
                    if (engineSwitchFinishTimer) {
                        cancelSchedule(engineSwitchFinishTimer);
                        engineSwitchFinishTimer = 0;
                    }
                    clearEngineSwitchTrackState();
                    return engineSwitchRevision;
                }

                function cancelQueuedSearchEngineSwitch() {
                    engineSwitchQueueRevision += 1;
                    if (engineSwitchDebounceTimer) {
                        cancelSchedule(engineSwitchDebounceTimer);
                        engineSwitchDebounceTimer = 0;
                    }
                    queuedEngineIndex = null;
                    queuedEngineStepDelta = 0;
                    queuedEngineDirection = 'next';
                }

                function isEngineSwitchAnimationActive() {
                    return Boolean(engineSwitchMidpointTimer || engineSwitchFinishTimer);
                }

                function queuedEngineSwitchDirection() {
                    return queuedEngineStepDelta < 0
                        ? 'previous'
                        : queuedEngineStepDelta > 0
                            ? 'next'
                            : queuedEngineDirection;
                }

                function syncSearchEngineButtonState() {
                    if (expanded) {
                        engineButton.setAttribute(
                            'aria-label',
                            `切换搜索引擎，当前为 ${currentEngine.name}`
                        );
                        engineButton.setAttribute('aria-haspopup', 'listbox');
                        engineButton.setAttribute('aria-controls', 'search-engine-list');
                        engineButton.setAttribute('aria-expanded', String(menuOpen));
                    } else {
                        engineButton.setAttribute(
                            'aria-label',
                            `使用 ${currentEngine.name} 搜索，点击或按 Alt 加波浪号键展开搜索框`
                        );
                        engineButton.removeAttribute('aria-haspopup');
                        engineButton.setAttribute('aria-controls', 'homepage-search-input');
                        engineButton.setAttribute('aria-expanded', 'false');
                    }
                }

                function applySearchEngine(engineId) {
                    currentEngine = resolveSearchEngine(engineId);
                    form.setAttribute('action', currentEngine.action);
                    input.setAttribute('name', currentEngine.queryParameter);
                    form.dataset.currentEngine = currentEngine.id;
                    engineName.textContent = currentEngine.name;
                    submitButton.setAttribute('aria-label', `使用 ${currentEngine.name} 搜索`);
                    currentEngineUses.forEach((use) => use.setAttribute('href', currentEngine.symbol));
                    options.forEach((option) => {
                        const selected = option.dataset.searchEngine === currentEngine.id;
                        option.setAttribute('aria-selected', String(selected));
                        option.tabIndex = selected ? 0 : -1;
                    });
                    syncSearchEngineButtonState();
                    return currentEngine;
                }

                function completeSearchEngineSwitch(engineId, direction) {
                    const targetEngine = resolveSearchEngine(engineId);
                    const revision = cancelEngineSwitchAnimation();
                    if (targetEngine.id === currentEngine.id) return currentEngine;

                    if (reduceMotion()) {
                        const engine = applySearchEngine(targetEngine.id);
                        status.textContent = `已切换到 ${engine.name}。`;
                        cancelQueuedSearchEngineSwitch();
                        return engine;
                    }

                    form.dataset.engineSwitchDirection = direction;
                    form.dataset.engineSwitchPhase = 'out';
                    engineSwitchMidpointTimer = schedule(() => {
                        if (revision !== engineSwitchRevision) return;
                        engineSwitchMidpointTimer = 0;
                        const engine = applySearchEngine(targetEngine.id);
                        status.textContent = `已切换到 ${engine.name}。`;
                        form.dataset.engineSwitchPhase = 'position';
                        void engineIdentity.offsetWidth;
                        form.dataset.engineSwitchPhase = 'in';
                        engineSwitchFinishTimer = schedule(() => {
                            if (revision !== engineSwitchRevision) return;
                            engineSwitchFinishTimer = 0;
                            clearEngineSwitchTrackState();
                            if (engineSwitchDebounceTimer) return;
                            commitQueuedSearchEngineSwitch();
                        }, SEARCH_ENGINE_TRACK_PHASE_MS);
                    }, SEARCH_ENGINE_TRACK_PHASE_MS);
                    return targetEngine;
                }

                function commitQueuedSearchEngineSwitch() {
                    engineSwitchDebounceTimer = 0;
                    if (queuedEngineIndex === null) return currentEngine;
                    const targetEngine = SEARCH_ENGINES[queuedEngineIndex];
                    if (isEngineSwitchAnimationActive()) return targetEngine;
                    if (targetEngine.id === currentEngine.id) {
                        cancelQueuedSearchEngineSwitch();
                        return currentEngine;
                    }
                    return completeSearchEngineSwitch(
                        targetEngine.id,
                        queuedEngineSwitchDirection()
                    );
                }

                function queueSearchEngineSwitch(step) {
                    engineSwitchQueueRevision += 1;
                    const queueRevision = engineSwitchQueueRevision;
                    const currentIndex = SEARCH_ENGINES.findIndex((engine) => engine.id === currentEngine.id);
                    const baseIndex = queuedEngineIndex === null ? currentIndex : queuedEngineIndex;
                    queuedEngineIndex = (baseIndex + step + SEARCH_ENGINES.length) % SEARCH_ENGINES.length;
                    queuedEngineStepDelta += step;
                    queuedEngineDirection = step < 0 ? 'previous' : 'next';
                    if (engineSwitchDebounceTimer) cancelSchedule(engineSwitchDebounceTimer);
                    engineSwitchDebounceTimer = schedule(
                        () => {
                            if (queueRevision !== engineSwitchQueueRevision) return;
                            commitQueuedSearchEngineSwitch();
                        },
                        SEARCH_ENGINE_SWITCH_DEBOUNCE_MS
                    );
                }

                function handleSearchEngineShortcut(event) {
                    const isPrevious = event && event.code === 'KeyJ';
                    const isNext = event && event.code === 'KeyL';
                    if ((!isPrevious && !isNext)
                        || !event.altKey
                        || event.ctrlKey
                        || event.shiftKey
                        || event.metaKey
                        || event.isComposing
                        || activeElement() !== input
                        || (typeof event.getModifierState === 'function' && event.getModifierState('AltGraph'))) {
                        return false;
                    }
                    event.preventDefault();
                    queueSearchEngineSwitch(isPrevious ? -1 : 1);
                    return true;
                }

                function clearEmptySearchFeedback() {
                    if (emptyFeedbackTimer) {
                        cancelSchedule(emptyFeedbackTimer);
                        emptyFeedbackTimer = 0;
                    }
                    form.classList.remove('is-empty-search');
                    input.placeholder = SEARCH_DEFAULT_PLACEHOLDER;
                }

                function showEmptySearchFeedback() {
                    clearEmptySearchFeedback();
                    const feedback = SEARCH_EMPTY_PLACEHOLDERS[emptyFeedbackIndex];
                    emptyFeedbackIndex = (emptyFeedbackIndex + 1) % SEARCH_EMPTY_PLACEHOLDERS.length;
                    void form.offsetWidth;
                    form.classList.add('is-empty-search');
                    input.placeholder = feedback;
                    status.textContent = `请输入搜索内容。${feedback}`;
                    emptyFeedbackTimer = schedule(() => {
                        form.classList.remove('is-empty-search');
                        input.placeholder = SEARCH_DEFAULT_PLACEHOLDER;
                        emptyFeedbackTimer = 0;
                    }, 1800);
                }

                function setSearchExpanded(nextExpanded, focusInput = false, restoreFocus = false) {
                    expanded = Boolean(nextExpanded);
                    if (!expanded) {
                        cancelQueuedSearchEngineSwitch();
                        cancelEngineSwitchAnimation();
                    }
                    form.dataset.expanded = String(expanded);
                    if (!expanded && menuOpen) {
                        setSearchMenuOpen(false, false);
                    } else {
                        syncSearchEngineButtonState();
                    }
                    if (focusInput && expanded) {
                        input.focus({ preventScroll: true });
                    } else if (restoreFocus && !expanded) {
                        engineButton.focus({ preventScroll: true });
                    }
                    return expanded;
                }

                function setSearchMenuOpen(nextOpen, restoreFocus = false) {
                    if (nextOpen) {
                        cancelQueuedSearchEngineSwitch();
                        cancelEngineSwitchAnimation();
                    }
                    menuOpen = expanded && Boolean(nextOpen);
                    form.dataset.menuOpen = String(menuOpen);
                    menu.hidden = !menuOpen;
                    syncSearchEngineButtonState();
                    if (menuOpen) {
                        const selected = options.find((option) => option.dataset.searchEngine === currentEngine.id);
                        (selected || options[0]).focus({ preventScroll: true });
                    } else if (restoreFocus) {
                        engineButton.focus({ preventScroll: true });
                    }
                    return menuOpen;
                }

                function selectSearchEngine(engineId) {
                    cancelQueuedSearchEngineSwitch();
                    cancelEngineSwitchAnimation();
                    const engine = applySearchEngine(engineId);
                    setSearchMenuOpen(false, false);
                    setSearchExpanded(true, true, false);
                    status.textContent = `已切换到 ${engine.name}。`;
                    return engine;
                }

                function moveSearchMenuFocus(direction) {
                    const focusedIndex = Math.max(0, options.indexOf(activeElement()));
                    const nextIndex = direction === 'first'
                        ? 0
                        : direction === 'last'
                            ? options.length - 1
                            : (focusedIndex + direction + options.length) % options.length;
                    options[nextIndex].focus({ preventScroll: true });
                }

                function handleSearchMenuKeydown(event) {
                    if (event.key === 'ArrowDown') {
                        event.preventDefault();
                        moveSearchMenuFocus(1);
                    } else if (event.key === 'ArrowUp') {
                        event.preventDefault();
                        moveSearchMenuFocus(-1);
                    } else if (event.key === 'Home') {
                        event.preventDefault();
                        moveSearchMenuFocus('first');
                    } else if (event.key === 'End') {
                        event.preventDefault();
                        moveSearchMenuFocus('last');
                    } else if (event.key === 'Enter' || event.key === ' ') {
                        const focused = activeElement();
                        if (focused && focused.dataset && focused.dataset.searchEngine) {
                            event.preventDefault();
                            selectSearchEngine(focused.dataset.searchEngine);
                        }
                    } else if (event.key === 'Escape') {
                        event.preventDefault();
                        setSearchMenuOpen(false, true);
                    }
                }

                function handleSearchSubmit(event) {
                    cancelQueuedSearchEngineSwitch();
                    cancelEngineSwitchAnimation();
                    if (!input.value.trim()) {
                        event.preventDefault();
                        input.value = '';
                        setSearchExpanded(true, true, false);
                        showEmptySearchFeedback();
                        return false;
                    }
                    clearEmptySearchFeedback();
                    status.textContent = `正在使用 ${currentEngine.name} 搜索。`;
                    schedule(() => {
                        input.value = '';
                        setSearchMenuOpen(false, false);
                        setSearchExpanded(false, false, false);
                        status.textContent = '';
                    }, 0);
                    return true;
                }

                function collapseSearchForWindowBlur() {
                    const focused = activeElement();
                    if (focused && form.contains(focused) && typeof focused.blur === 'function') {
                        focused.blur();
                    }
                    setSearchMenuOpen(false, false);
                    setSearchExpanded(false, false, false);
                }

                return {
                    applySearchEngine,
                    setSearchExpanded,
                    setSearchMenuOpen,
                    selectSearchEngine,
                    handleSearchEngineShortcut,
                    handleSearchMenuKeydown,
                    handleSearchSubmit,
                    collapseSearchForWindowBlur,
                    isExpanded: () => expanded,
                    isMenuOpen: () => menuOpen,
                    currentEngine: () => currentEngine
                };
            }

            function restoreStaticBingSearch(form, input, menu) {
                form.removeAttribute('data-search-enhanced');
                form.removeAttribute('data-current-engine');
                form.dataset.expanded = 'false';
                form.dataset.menuOpen = 'false';
                form.noValidate = false;
                form.setAttribute('action', SEARCH_ENGINES[0].action);
                if (input) {
                    input.setAttribute('name', SEARCH_ENGINES[0].queryParameter);
                    input.removeAttribute('aria-keyshortcuts');
                }
                if (menu) {
                    menu.hidden = true;
                }
            }

            function initializeHomepageSearch() {
                const form = document.getElementById('homepage-search-form');
                if (!form) return null;
                const input = document.getElementById('homepage-search-input');
                const engineButton = document.getElementById('search-engine-button');
                const engineIdentity = form.querySelector('.search-engine-identity');
                const engineName = document.getElementById('search-engine-name');
                const submitButton = document.getElementById('search-submit-button');
                const menu = document.getElementById('search-engine-list');
                const status = document.getElementById('search-status');
                if (!input || !engineButton || !engineIdentity || !engineName || !submitButton || !menu || !status) {
                    throw new Error('搜索栏结构不完整');
                }
                const options = Array.from(menu.querySelectorAll('[data-search-engine]'));
                const currentEngineUses = Array.from(form.querySelectorAll('.search-current-engine-use'));
                if (options.length !== SEARCH_ENGINES.length || currentEngineUses.length !== 1) {
                    throw new Error('搜索栏结构不完整');
                }

                clearLegacySearchEnginePreference();
                const controller = createHomepageSearchController({
                    form,
                    input,
                    engineButton,
                    engineIdentity,
                    engineName,
                    submitButton,
                    menu,
                    options,
                    status,
                    currentEngineUses
                });
                controller.applySearchEngine(SEARCH_ENGINES[0].id);
                const handleGlobalSearchShortcut = (event) => {
                    handleHomepageSearchFocusShortcut(
                        event,
                        controller,
                        Boolean(document.querySelector('dialog[open]'))
                    );
                };

                engineButton.addEventListener('click', () => {
                    if (!controller.isExpanded()) {
                        controller.setSearchExpanded(true, true, false);
                        return;
                    }
                    controller.setSearchMenuOpen(!controller.isMenuOpen(), false);
                });
                options.forEach((option) => {
                    option.addEventListener('click', () => controller.selectSearchEngine(option.dataset.searchEngine));
                });
                menu.addEventListener('keydown', controller.handleSearchMenuKeydown);
                input.addEventListener('keydown', controller.handleSearchEngineShortcut);
                form.addEventListener('submit', controller.handleSearchSubmit);
                form.addEventListener('keydown', (event) => {
                    if (event.key !== 'Escape' || controller.isMenuOpen()) return;
                    event.preventDefault();
                    controller.setSearchExpanded(false, false, true);
                });
                form.addEventListener('focusout', () => {
                    window.setTimeout(() => {
                        if (!form.contains(document.activeElement)) {
                            controller.setSearchMenuOpen(false, false);
                            controller.setSearchExpanded(false, false, false);
                        }
                    }, 0);
                });
                document.addEventListener('pointerdown', (event) => {
                    if (!form.contains(event.target)) {
                        controller.setSearchMenuOpen(false, false);
                        controller.setSearchExpanded(false, false, false);
                    }
                });
                window.addEventListener('blur', controller.collapseSearchForWindowBlur);
                form.noValidate = true;
                form.dataset.searchEnhanced = 'true';
                input.setAttribute('aria-keyshortcuts', 'Alt+J Alt+L');
                document.addEventListener('keydown', handleGlobalSearchShortcut);
                return controller;
            }

            const homepageSearchForm = document.getElementById('homepage-search-form');
            try {
                initializeHomepageSearch();
            } catch (error) {
                console.warn('搜索栏增强初始化失败，已保留基础 Bing 搜索。', error);
                if (homepageSearchForm) {
                    restoreStaticBingSearch(
                        homepageSearchForm,
                        document.getElementById('homepage-search-input'),
                        document.getElementById('search-engine-list')
                    );
                }
            }

            const cards = Array.from(document.querySelectorAll('.service-card'));
            const quickLinks = Array.from(document.querySelectorAll('.quick-link-card'));
            const serviceCountLabel = document.getElementById('service-count-label');
            const refreshButton = document.getElementById('refresh-status');
            const lastChecked = document.getElementById('last-checked');
            const statusSummary = document.getElementById('status-summary');
            const dashboard = document.getElementById('dashboard');
            const channelSwitchAnchor = document.getElementById('channel-switch-anchor');
            const channelSwitch = document.getElementById('channel-switch');
            const channelSwitchLabel = document.getElementById('channel-switch-label');
            const channelSwitchFaceFront = document.getElementById('channel-switch-face-front');
            const channelSwitchFaceBack = document.getElementById('channel-switch-face-back');
            const channelSwitchMeasureServices = document.getElementById('channel-switch-measure-services');
            const channelSwitchMeasureQuickLinks = document.getElementById('channel-switch-measure-quick-links');
            const serviceChannelNote = document.getElementById('service-channel-note');
            const channelPanelStage = document.getElementById('channel-panel-stage');
            const channelPanelRotor = document.getElementById('channel-panel-rotor');
            const servicesPanel = document.getElementById('services-panel');
            const quickLinksPanel = document.getElementById('quick-links-panel');
            const serviceGrid = document.getElementById('service-grid');
            const quickLinksGrid = document.getElementById('quick-links-grid');
            const dashboardFooterBrand = document.querySelector('.dashboard-footer-brand');
            const channelMotionMedia = window.matchMedia('(min-width: 768px) and (prefers-reduced-motion: no-preference)');
            const channelMobileMotionMedia = window.matchMedia('(max-width: 767px) and (prefers-reduced-motion: no-preference)');
            const serviceChannelDescription = '状态表示入口节点经 Zoraxy 到上游的服务端链路，不代表应用内部健康。';
            const quickLinkChannelNotes = [
                {
                    prefix: '大家一起KIRAKIRA',
                    linkText: ' ☆ ',
                    suffix: 'DOKIDOKI ！',
                    href: 'https://www.bilibili.com/video/BV1uT7kz8Ee9'
                },
                { text: 'モカちゃんやっちゃうよ～' },
                {
                    prefix: 'まんまるお山に彩りを!',
                    linkText: '丸山彩',
                    suffix: 'でーす!',
                    href: 'https://www.bilibili.com/video/BV1mHb8zDESv'
                },
                {
                    prefix: 'POPIPA! ',
                    linkText: 'PIPOPA!',
                    suffix: ' POPIPAPAPIPOPA!',
                    href: 'https://www.bilibili.com/video/BV1H29SBCEvF'
                },
                { text: 'Happy! Lucky! Smile! Yeah!!' },
                {
                    prefix: 'あなた達、',
                    linkText: 'Roselia',
                    suffix: 'にすべてを賭ける覚悟はある?',
                    href: 'https://www.bilibili.com/video/BV1Km42157kE'
                },
                { text: '🤜😯👆と✋😃👌が🖐️😉🖐️わ😃👐グループ♡' },
                {
                    prefix: 'なんで「',
                    linkText: '春日影',
                    suffix: '」やったの？！！',
                    href: 'https://www.bilibili.com/video/BV14z42167Uj'
                },
                { text: '夢は、限りなく大きく！' }
            ];
            const channelStateCommitDelayMs = 200;
            const channelNoteExitMs = 180;
            const channelNoteEnterMs = 240;
            const channelSwitchFallbackMs = 1200;
            const channelMobileButtonExitMs = 160;
            const channelMobilePanelDelayMs = 120;
            const channelMobilePanelExitMs = 180;
            const channelMobileCardEnterMs = 280;
            const channelMobileCardStaggerMs = 25;
            let activeChannel = 'services';
            let quickLinkChannelNoteIndex = 0;
            let channelSwitchLocked = false;
            let pendingChannel = null;
            let pendingChannelNote = null;
            let channelStateCommitTimer = null;
            let channelNoteSwapTimer = null;
            let channelNoteEnterTimer = null;
            let channelFooterEnterTimer = null;
            let channelFooterEnterFrame = null;
            let channelSwitchFallbackTimer = null;
            let channelResetFrame = null;
            let channelMobileButtonTimer = null;
            let channelMobilePanelExitTimer = null;
            let channelMobilePanelSwapTimer = null;
            let channelMobileCompleteTimer = null;

            function syncQuickLinkDimensions() {
                const firstServiceCard = cards[0];
                if (!firstServiceCard || !serviceGrid || !quickLinksGrid || !channelPanelStage) return;
                const servicesWereHidden = servicesPanel.hidden;
                servicesPanel.hidden = false;
                try {
                    const cardWidth = firstServiceCard.getBoundingClientRect().width;
                    const serviceGridHeight = serviceGrid.getBoundingClientRect().height;
                    const computedStyle = window.getComputedStyle(serviceGrid);
                    const columnGap = Number.parseFloat(computedStyle.columnGap);
                    if (!Number.isFinite(cardWidth) || cardWidth <= 0
                        || !Number.isFinite(serviceGridHeight) || serviceGridHeight <= 0
                        || !Number.isFinite(columnGap) || columnGap < 0) return;
                    const quickLinkColumns = Math.min(5, quickLinks.length);
                    const quickLinksWidth = cardWidth * quickLinkColumns + columnGap * Math.max(0, quickLinkColumns - 1);
                    quickLinksGrid.style.setProperty('--quick-card-width', `${cardWidth}px`);
                    quickLinksGrid.style.setProperty('--quick-links-width', `${quickLinksWidth}px`);
                    channelPanelStage.style.setProperty('--services-panel-height', `${serviceGridHeight}px`);
                } finally {
                    servicesPanel.hidden = servicesWereHidden;
                }
            }

            function syncChannelSwitchDimensions() {
                if (!channelSwitchAnchor || !channelSwitchMeasureServices || !channelSwitchMeasureQuickLinks) return;
                const servicesWidth = channelSwitchMeasureServices.getBoundingClientRect().width;
                const quickLinksWidth = channelSwitchMeasureQuickLinks.getBoundingClientRect().width;
                if (!Number.isFinite(servicesWidth) || servicesWidth <= 0
                    || !Number.isFinite(quickLinksWidth) || quickLinksWidth <= 0) return;
                channelSwitchAnchor.style.setProperty('--channel-switch-services-width', `${servicesWidth}px`);
                channelSwitchAnchor.style.setProperty('--channel-switch-quick-links-width', `${quickLinksWidth}px`);
            }

            function channelMotionEnabled() {
                return dashboard.classList.contains('channel-motion-ready');
            }

            function channelMobileMotionEnabled() {
                return dashboard.classList.contains('channel-mobile-motion-ready');
            }

            function syncChannelPanelHeight() {
                if (!channelMotionEnabled()) return;
                const servicesWereHidden = servicesPanel.hidden;
                servicesPanel.hidden = false;
                try {
                    const servicesHeight = serviceGrid.getBoundingClientRect().height;
                    if (!Number.isFinite(servicesHeight) || servicesHeight <= 0) return;
                    channelPanelStage.style.setProperty('--channel-panel-height', `${servicesHeight}px`);
                } finally {
                    servicesPanel.hidden = servicesWereHidden;
                }
            }

            function configureChannelMotion() {
                if (channelSwitchLocked) finishChannelSwitch();
                syncChannelSwitchDimensions();
                syncQuickLinkDimensions();
                const motionEnabled = channelMotionMedia.matches;
                const mobileMotionEnabled = channelMobileMotionMedia.matches;
                dashboard.classList.toggle('channel-motion-ready', motionEnabled);
                dashboard.classList.toggle('channel-mobile-motion-ready', mobileMotionEnabled);
                if (motionEnabled) {
                    syncChannelPanelHeight();
                } else {
                    channelPanelStage.style.removeProperty('--channel-panel-height');
                }
                settleChannelPanels(activeChannel);
            }

            function channelLabel(channel) {
                return channel === 'quick-links' ? 'Quick Links' : 'Services';
            }

            function channelPanel(channel) {
                return channel === 'quick-links' ? quickLinksPanel : servicesPanel;
            }

            function nextQuickLinkChannelNote() {
                const note = quickLinkChannelNotes[quickLinkChannelNoteIndex];
                quickLinkChannelNoteIndex = (quickLinkChannelNoteIndex + 1) % quickLinkChannelNotes.length;
                return note;
            }

            function renderChannelNote(note) {
                if (typeof note === 'string' || note.text) {
                    serviceChannelNote.textContent = typeof note === 'string' ? note : note.text;
                    return;
                }
                const link = document.createElement('a');
                link.href = note.href;
                link.target = '_blank';
                link.rel = 'noopener noreferrer';
                link.textContent = note.linkText;
                serviceChannelNote.replaceChildren(note.prefix, link, note.suffix);
            }

            function channelCountText(channel) {
                return channel === 'quick-links'
                    ? `${quickLinks.length} 个快捷入口`
                    : `${cards.length} 个家庭服务入口`;
            }

            function clearChannelCopyClasses(elements) {
                elements.forEach((element) => {
                    element.classList.remove('is-channel-copy-exiting', 'is-channel-copy-entering');
                });
            }

            function beginChannelCopyEntry(elements) {
                elements.forEach((element) => {
                    element.classList.add('is-channel-copy-entering');
                    element.classList.remove('is-channel-copy-exiting');
                });
            }

            function transitionChannelCopy(note, channel) {
                serviceChannelNote.classList.remove('is-note-entering');
                serviceChannelNote.classList.add('is-note-exiting');
                serviceCountLabel.classList.remove('is-channel-copy-entering');
                serviceCountLabel.classList.add('is-channel-copy-exiting');
                refreshButton.toggleAttribute('inert', true);
                [refreshButton, dashboardFooterBrand, lastChecked].forEach((element) => {
                    element.classList.remove('is-channel-copy-entering');
                    element.classList.add('is-channel-copy-exiting');
                });
                channelNoteSwapTimer = window.setTimeout(() => {
                    channelNoteSwapTimer = null;
                    renderChannelNote(note);
                    serviceCountLabel.textContent = channelCountText(channel);
                    serviceChannelNote.classList.add('is-note-entering');
                    serviceChannelNote.classList.remove('is-note-exiting');
                    beginChannelCopyEntry([serviceCountLabel]);
                    channelNoteEnterTimer = window.setTimeout(() => {
                        channelNoteEnterTimer = null;
                        serviceChannelNote.classList.remove('is-note-entering');
                        clearChannelCopyClasses([serviceCountLabel]);
                    }, channelNoteEnterMs);
                }, channelNoteExitMs);
            }

            function revealServicesFooterCopy() {
                const elements = [refreshButton, dashboardFooterBrand, lastChecked];
                elements.forEach((element) => {
                    window.getComputedStyle(element).opacity;
                });
                channelFooterEnterFrame = window.requestAnimationFrame(() => {
                    channelFooterEnterFrame = null;
                    beginChannelCopyEntry(elements);
                    channelFooterEnterTimer = window.setTimeout(() => {
                        channelFooterEnterTimer = null;
                        clearChannelCopyClasses(elements);
                        refreshButton.removeAttribute('inert');
                    }, channelNoteEnterMs);
                });
            }

            function syncChannelButtonFaces(channel) {
                channelSwitchFaceFront.textContent = channelLabel(channel);
                channelSwitchFaceBack.textContent = channelLabel(channel === 'services' ? 'quick-links' : 'services');
            }

            function commitChannelChrome(channel, animateCopy = false) {
                const showQuickLinks = channel === 'quick-links';
                activeChannel = showQuickLinks ? 'quick-links' : 'services';
                dashboard.dataset.activeChannel = activeChannel;
                channelSwitchLabel.textContent = showQuickLinks ? 'Quick Links' : 'Services';
                channelSwitch.setAttribute('aria-pressed', showQuickLinks ? 'true' : 'false');
                channelSwitch.setAttribute('aria-label', showQuickLinks ? '切换到 Services' : '切换到 Quick Links');
                if (!animateCopy) serviceCountLabel.textContent = channelCountText(activeChannel);
                serviceChannelNote.hidden = false;
                refreshButton.hidden = showQuickLinks;
                dashboardFooterBrand.hidden = showQuickLinks;
                lastChecked.hidden = showQuickLinks;
                if (animateCopy) {
                    if (showQuickLinks) {
                        clearChannelCopyClasses([refreshButton, dashboardFooterBrand, lastChecked]);
                    } else {
                        revealServicesFooterCopy();
                    }
                } else {
                    refreshButton.toggleAttribute('inert', showQuickLinks);
                }
                if (!showQuickLinks) {
                    window.requestAnimationFrame(syncQuickLinkDimensions);
                }
            }

            function setChannelPanelInteractive(panel, interactive) {
                panel.setAttribute('aria-hidden', String(!interactive));
                panel.toggleAttribute('inert', !interactive);
            }

            function retainChannelSwitchFocus() {
                if (typeof channelSwitch.focus === 'function') {
                    channelSwitch.focus({ preventScroll: true });
                }
            }

            function settleChannelPanels(channel) {
                const activePanel = channelPanel(channel);
                const inactivePanel = channelPanel(channel === 'services' ? 'quick-links' : 'services');
                [servicesPanel, quickLinksPanel].forEach((panel) => {
                    panel.classList.remove(
                        'is-channel-outgoing',
                        'is-channel-incoming',
                        'is-channel-active',
                        'is-mobile-channel-outgoing',
                        'is-mobile-channel-incoming'
                    );
                });
                activePanel.hidden = false;
                activePanel.classList.add('is-channel-active');
                setChannelPanelInteractive(activePanel, true);
                inactivePanel.hidden = true;
                setChannelPanelInteractive(inactivePanel, false);
            }

            function clearChannelSwitchTimers() {
                [
                    channelStateCommitTimer,
                    channelNoteSwapTimer,
                    channelNoteEnterTimer,
                    channelFooterEnterTimer,
                    channelMobileButtonTimer,
                    channelMobilePanelExitTimer,
                    channelMobilePanelSwapTimer,
                    channelMobileCompleteTimer,
                    channelSwitchFallbackTimer
                ].forEach((timer) => {
                    if (timer !== null) window.clearTimeout(timer);
                });
                channelStateCommitTimer = null;
                channelNoteSwapTimer = null;
                channelNoteEnterTimer = null;
                channelFooterEnterTimer = null;
                channelMobileButtonTimer = null;
                channelMobilePanelExitTimer = null;
                channelMobilePanelSwapTimer = null;
                channelMobileCompleteTimer = null;
                if (channelFooterEnterFrame !== null) {
                    window.cancelAnimationFrame(channelFooterEnterFrame);
                    channelFooterEnterFrame = null;
                }
                channelSwitchFallbackTimer = null;
            }

            function finishDesktopChannelSwitch() {
                if (!channelSwitchLocked || !pendingChannel) return;
                const completedChannel = pendingChannel;
                const completedNote = pendingChannelNote;
                const noteWasPending = channelNoteSwapTimer !== null;
                clearChannelSwitchTimers();
                dashboard.classList.add('is-channel-resetting');
                commitChannelChrome(completedChannel);
                if (noteWasPending && completedNote) renderChannelNote(completedNote);
                serviceChannelNote.classList.remove('is-note-exiting', 'is-note-entering');
                serviceCountLabel.textContent = channelCountText(completedChannel);
                clearChannelCopyClasses([
                    serviceCountLabel,
                    refreshButton,
                    dashboardFooterBrand,
                    lastChecked
                ]);
                settleChannelPanels(completedChannel);
                syncChannelButtonFaces(completedChannel);
                channelSwitch.classList.remove(
                    'is-channel-flipping',
                    'is-switching-to-services',
                    'is-switching-to-quick-links'
                );
                channelPanelRotor.classList.remove('is-channel-flipping');
                dashboard.classList.remove('is-channel-switching');
                pendingChannel = null;
                pendingChannelNote = null;
                channelResetFrame = window.requestAnimationFrame(() => {
                    channelResetFrame = null;
                    dashboard.classList.remove('is-channel-resetting');
                    channelSwitchLocked = false;
                    channelSwitch.removeAttribute('aria-disabled');
                    retainChannelSwitchFocus();
                });
            }

            function finishMobileChannelSwitch() {
                if (!channelSwitchLocked || !pendingChannel) return;
                const completedChannel = pendingChannel;
                const completedNote = pendingChannelNote;
                const noteWasPending = channelNoteSwapTimer !== null;
                clearChannelSwitchTimers();
                commitChannelChrome(completedChannel);
                if (noteWasPending && completedNote) renderChannelNote(completedNote);
                serviceChannelNote.classList.remove('is-note-exiting', 'is-note-entering');
                serviceCountLabel.textContent = channelCountText(completedChannel);
                clearChannelCopyClasses([
                    serviceCountLabel,
                    refreshButton,
                    dashboardFooterBrand,
                    lastChecked
                ]);
                settleChannelPanels(completedChannel);
                syncChannelButtonFaces(completedChannel);
                channelSwitch.classList.remove(
                    'is-mobile-channel-fading-out',
                    'is-mobile-channel-fading-in',
                    'is-mobile-target-services',
                    'is-mobile-target-quick-links'
                );
                dashboard.classList.remove('is-mobile-channel-switching');
                pendingChannel = null;
                pendingChannelNote = null;
                channelSwitchLocked = false;
                channelSwitch.removeAttribute('aria-disabled');
                retainChannelSwitchFocus();
            }

            function finishChannelSwitch() {
                if (dashboard.classList.contains('is-mobile-channel-switching')) {
                    finishMobileChannelSwitch();
                    return;
                }
                finishDesktopChannelSwitch();
            }

            function setActiveChannel(channel) {
                const nextChannel = channel === 'quick-links' ? 'quick-links' : 'services';
                const note = nextChannel === 'quick-links'
                    ? nextQuickLinkChannelNote()
                    : serviceChannelDescription;
                commitChannelChrome(nextChannel);
                settleChannelPanels(nextChannel);
                syncChannelButtonFaces(nextChannel);
                renderChannelNote(note);
            }

            function beginAnimatedChannelSwitch(channel) {
                const nextChannel = channel === 'quick-links' ? 'quick-links' : 'services';
                const previousChannel = activeChannel;
                const outgoingPanel = channelPanel(previousChannel);
                const incomingPanel = channelPanel(nextChannel);
                pendingChannel = nextChannel;
                pendingChannelNote = nextChannel === 'quick-links'
                    ? nextQuickLinkChannelNote()
                    : serviceChannelDescription;
                channelSwitchLocked = true;
                channelSwitch.setAttribute('aria-disabled', 'true');
                retainChannelSwitchFocus();
                dashboard.classList.add('is-channel-switching');
                channelSwitchFaceFront.textContent = channelLabel(previousChannel);
                channelSwitchFaceBack.textContent = channelLabel(nextChannel);
                channelSwitch.classList.add(
                    'is-channel-flipping',
                    nextChannel === 'quick-links'
                        ? 'is-switching-to-quick-links'
                        : 'is-switching-to-services'
                );
                outgoingPanel.hidden = false;
                incomingPanel.hidden = false;
                setChannelPanelInteractive(outgoingPanel, false);
                setChannelPanelInteractive(incomingPanel, false);
                outgoingPanel.classList.add('is-channel-outgoing');
                outgoingPanel.classList.remove('is-channel-incoming');
                incomingPanel.classList.add('is-channel-incoming');
                incomingPanel.classList.remove('is-channel-active', 'is-channel-outgoing');
                channelPanelRotor.classList.add('is-channel-flipping');
                transitionChannelCopy(pendingChannelNote, nextChannel);
                channelStateCommitTimer = window.setTimeout(() => {
                    channelStateCommitTimer = null;
                    commitChannelChrome(nextChannel, true);
                }, channelStateCommitDelayMs);
                channelSwitchFallbackTimer = window.setTimeout(
                    finishChannelSwitch,
                    channelSwitchFallbackMs
                );
            }

            function channelMobileSwitchDuration(channel) {
                const incomingCardCount = channel === 'quick-links'
                    ? quickLinks.length
                    : cards.length;
                return channelMobilePanelDelayMs
                    + channelMobilePanelExitMs
                    + channelMobileCardEnterMs
                    + Math.max(0, incomingCardCount - 1) * channelMobileCardStaggerMs;
            }

            function beginMobileChannelSwitch(channel) {
                const nextChannel = channel === 'quick-links' ? 'quick-links' : 'services';
                const previousChannel = activeChannel;
                const outgoingPanel = channelPanel(previousChannel);
                const incomingPanel = channelPanel(nextChannel);
                pendingChannel = nextChannel;
                pendingChannelNote = nextChannel === 'quick-links'
                    ? nextQuickLinkChannelNote()
                    : serviceChannelDescription;
                channelSwitchLocked = true;
                channelSwitch.setAttribute('aria-disabled', 'true');
                retainChannelSwitchFocus();
                dashboard.classList.add('is-mobile-channel-switching');
                setChannelPanelInteractive(outgoingPanel, false);
                setChannelPanelInteractive(incomingPanel, false);
                channelSwitch.classList.add('is-mobile-channel-fading-out');
                transitionChannelCopy(pendingChannelNote, nextChannel);

                channelMobilePanelExitTimer = window.setTimeout(() => {
                    channelMobilePanelExitTimer = null;
                    outgoingPanel.classList.add('is-mobile-channel-outgoing');
                }, channelMobilePanelDelayMs);

                channelMobileButtonTimer = window.setTimeout(() => {
                    channelMobileButtonTimer = null;
                    syncChannelButtonFaces(nextChannel);
                    channelSwitch.classList.remove('is-mobile-channel-fading-out');
                    channelSwitch.classList.add(
                        'is-mobile-channel-fading-in',
                        nextChannel === 'quick-links'
                            ? 'is-mobile-target-quick-links'
                            : 'is-mobile-target-services'
                    );
                }, channelMobileButtonExitMs);

                channelStateCommitTimer = window.setTimeout(() => {
                    channelStateCommitTimer = null;
                    commitChannelChrome(nextChannel, true);
                }, channelStateCommitDelayMs);

                channelMobilePanelSwapTimer = window.setTimeout(() => {
                    channelMobilePanelSwapTimer = null;
                    outgoingPanel.hidden = true;
                    outgoingPanel.classList.remove('is-channel-active', 'is-mobile-channel-outgoing');
                    incomingPanel.classList.add('is-mobile-channel-incoming');
                    incomingPanel.hidden = false;
                }, channelMobilePanelDelayMs + channelMobilePanelExitMs);

                channelMobileCompleteTimer = window.setTimeout(() => {
                    channelMobileCompleteTimer = null;
                    finishMobileChannelSwitch();
                }, channelMobileSwitchDuration(nextChannel));

                channelSwitchFallbackTimer = window.setTimeout(
                    finishChannelSwitch,
                    channelSwitchFallbackMs
                );
            }

            function restoreStaticServicesChannel() {
                clearChannelSwitchTimers();
                if (channelResetFrame !== null) {
                    window.cancelAnimationFrame(channelResetFrame);
                    channelResetFrame = null;
                }
                quickLinkChannelNoteIndex = 0;
                channelSwitchLocked = false;
                pendingChannel = null;
                pendingChannelNote = null;
                activeChannel = 'services';
                if (dashboard) {
                    dashboard.dataset.activeChannel = 'services';
                    dashboard.classList.remove(
                        'channel-motion-ready',
                        'channel-mobile-motion-ready',
                        'is-channel-switching',
                        'is-mobile-channel-switching',
                        'is-channel-resetting'
                    );
                }
                if (channelPanelStage) channelPanelStage.style.removeProperty('--channel-panel-height');
                if (channelPanelRotor) channelPanelRotor.classList.remove('is-channel-flipping');
                if (servicesPanel && quickLinksPanel) settleChannelPanels('services');
                if (channelSwitchLabel) channelSwitchLabel.textContent = 'Services';
                if (channelSwitchFaceFront && channelSwitchFaceBack) syncChannelButtonFaces('services');
                if (channelSwitch) {
                    channelSwitch.disabled = true;
                    channelSwitch.classList.remove(
                        'is-channel-flipping',
                        'is-switching-to-services',
                        'is-switching-to-quick-links',
                        'is-mobile-channel-fading-out',
                        'is-mobile-channel-fading-in',
                        'is-mobile-target-services',
                        'is-mobile-target-quick-links'
                    );
                    channelSwitch.removeAttribute('aria-disabled');
                    channelSwitch.setAttribute('aria-pressed', 'false');
                    channelSwitch.setAttribute('aria-label', 'Quick Links 切换不可用');
                }
                if (serviceCountLabel) serviceCountLabel.textContent = `${cards.length} 个家庭服务入口`;
                if (serviceCountLabel && dashboardFooterBrand && lastChecked) {
                    clearChannelCopyClasses([
                        serviceCountLabel,
                        refreshButton,
                        dashboardFooterBrand,
                        lastChecked
                    ]);
                }
                if (serviceChannelNote) {
                    serviceChannelNote.hidden = false;
                    serviceChannelNote.classList.remove('is-note-exiting', 'is-note-entering');
                    serviceChannelNote.textContent = serviceChannelDescription;
                }
                if (refreshButton) {
                    refreshButton.hidden = false;
                    refreshButton.removeAttribute('inert');
                }
                if (dashboardFooterBrand) dashboardFooterBrand.hidden = false;
                if (lastChecked) lastChecked.hidden = false;
            }

            function initializeChannelSwitch() {
                const requiredElements = [
                    dashboard,
                    channelSwitchAnchor,
                    channelSwitch,
                    channelSwitchLabel,
                    channelSwitchFaceFront,
                    channelSwitchFaceBack,
                    channelSwitchMeasureServices,
                    channelSwitchMeasureQuickLinks,
                    serviceChannelNote,
                    channelPanelStage,
                    channelPanelRotor,
                    servicesPanel,
                    quickLinksPanel,
                    serviceGrid,
                    quickLinksGrid,
                    dashboardFooterBrand,
                    lastChecked
                ];
                if (requiredElements.some((element) => !element) || quickLinks.length !== 5) {
                    throw new Error('双频道静态结构不完整');
                }
                cards.forEach((card, index) => {
                    card.style.setProperty('--channel-card-index', String(index));
                });
                quickLinks.forEach((card, index) => {
                    card.style.setProperty('--channel-card-index', String(index));
                });
                setActiveChannel('services');
                configureChannelMotion();
                channelSwitch.disabled = false;
                channelSwitch.addEventListener('click', () => {
                    if (channelSwitchLocked) return;
                    if (channelMotionEnabled()) {
                        beginAnimatedChannelSwitch(
                            activeChannel === 'services' ? 'quick-links' : 'services'
                        );
                        return;
                    }
                    if (channelMobileMotionEnabled()) {
                        beginMobileChannelSwitch(
                            activeChannel === 'services' ? 'quick-links' : 'services'
                        );
                        return;
                    }
                    setActiveChannel(activeChannel === 'services' ? 'quick-links' : 'services');
                });
                channelPanelRotor.addEventListener('animationend', (event) => {
                    if (event.animationName === 'channel-flip-left') {
                        finishChannelSwitch();
                    }
                });
                window.addEventListener('resize', () => {
                    configureChannelMotion();
                });
                if (typeof channelMotionMedia.addEventListener === 'function') {
                    channelMotionMedia.addEventListener('change', configureChannelMotion);
                } else if (typeof channelMotionMedia.addListener === 'function') {
                    channelMotionMedia.addListener(configureChannelMotion);
                }
                if (typeof channelMobileMotionMedia.addEventListener === 'function') {
                    channelMobileMotionMedia.addEventListener('change', configureChannelMotion);
                } else if (typeof channelMobileMotionMedia.addListener === 'function') {
                    channelMobileMotionMedia.addListener(configureChannelMotion);
                }
            }

            function setCardState(card, state, label) {
                const stateElement = card.querySelector('.service-state');
                stateElement.dataset.state = state;
                stateElement.querySelector('.state-label').textContent = label;
            }

            function initializeServiceCards() {
                serviceCountLabel.textContent = `${cards.length} 个家庭服务入口`;
                cards.forEach((card, index) => {
                    card.style.setProperty('--card-index', String(index));
                });
            }

            function classifyServiceStatus(status) {
                if (Number.isInteger(status) && status >= 200 && status < 400) return 'online';
                if (Number.isInteger(status) && status >= 400 && status < 500) return 'warning';
                return 'offline';
            }

            function serviceStatusLabel(state, status = null, error = null) {
                if (error === 'redirect_rejected') return `重定向被拒绝 · HTTP ${status}`;
                if (error !== null && Number.isInteger(status)) return `探测失败 · HTTP ${status}`;
                if (state === 'online') return '服务端可达';
                if (state === 'warning') return `异常 · HTTP ${status}`;
                if (Number.isInteger(status)) return `不可用 · HTTP ${status}`;
                return '不可连接';
            }

            function normalizeServiceStatusResult(value, target) {
                if (!value || typeof value !== 'object' || value.target !== target) return null;
                const status = value.status;
                const latencyMs = value.latencyMs;
                const error = value.error;
                const validStatus = status === null || (Number.isInteger(status) && status >= 100 && status <= 599);
                const validLatency = typeof latencyMs === 'number' && Number.isFinite(latencyMs) && latencyMs >= 0;
                const validErrors = new Set([
                    'invalid_target',
                    'timeout',
                    'tls_error',
                    'redirect_rejected',
                    'connection_error',
                    'request_error'
                ]);
                if (!validStatus || !validLatency || (error !== null && !validErrors.has(error))) return null;
                if (status === null && error === null) return null;
                return { target, status, latencyMs, error };
            }

            async function refreshStatuses() {
                refreshButton.disabled = true;
                refreshButton.setAttribute('aria-busy', 'true');
                lastChecked.textContent = `正在检测 ${cards.length} 个入口…`;
                cards.forEach((card) => setCardState(card, 'checking', '检测中'));
                const targets = cards.map((card) => card.dataset.probeUrl || card.href);

                try {
                    const payload = await fetchWithTimeout(serviceStatusEndpoint.href, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-Requested-With': 'homelab-dashboard'
                        },
                        credentials: 'same-origin',
                        cache: 'no-store',
                        body: JSON.stringify({ targets })
                    }, 8000, null, async (response) => {
                        if (!response.ok) throw new Error(`状态 API 返回 HTTP ${response.status}`);
                        return response.json();
                    });
                    if (!payload || typeof payload !== 'object' || !Array.isArray(payload.results)
                        || payload.results.length !== targets.length) {
                        throw new Error('状态 API 返回结构不符合契约');
                    }
                    const results = payload.results.map((value, index) => {
                        const result = normalizeServiceStatusResult(value, targets[index]);
                        if (!result) throw new Error(`状态 API 第 ${index + 1} 项不符合契约`);
                        const state = result.error === null
                            ? classifyServiceStatus(result.status)
                            : 'offline';
                        setCardState(cards[index], state, serviceStatusLabel(state, result.status, result.error));
                        return { ...result, state };
                    });
                    const counts = results.reduce((summary, result) => {
                        summary[result.state] += 1;
                        return summary;
                    }, { online: 0, warning: 0, offline: 0 });

                    lastChecked.textContent = `刚刚更新 · ${counts.online}/${cards.length} 服务端可达 · ${counts.warning} 响应异常 · ${counts.offline} 不可用`;
                    statusSummary.textContent = `入口检测完成，${counts.online} 个服务端可达，${counts.warning} 个响应异常，${counts.offline} 个不可用。`;
                } catch (error) {
                    console.warn('状态检测服务不可用。', error);
                    cards.forEach((card) => setCardState(card, 'unknown', '状态未知'));
                    lastChecked.textContent = '状态检测服务不可用';
                    statusSummary.textContent = '状态检测服务不可用，所有入口状态未知；服务链接仍可正常使用。';
                } finally {
                    refreshButton.disabled = false;
                    refreshButton.setAttribute('aria-busy', 'false');
                }
            }

            initializeServiceCards();
            try {
                initializeChannelSwitch();
            } catch (error) {
                console.warn('双频道预览初始化失败，已保留 Services 默认面。', error);
                restoreStaticServicesChannel();
            }
            refreshButton.addEventListener('click', refreshStatuses);
            refreshStatuses();
        }());
