// Homepage search enhancement with a static Bing form fallback.
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
                }),
                Object.freeze({
                    id: 'bilibili',
                    name: '哔哩哔哩',
                    action: 'https://search.bilibili.com/all',
                    queryParameter: 'keyword',
                    symbol: '#search-logo-bilibili'
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

export function initializeSearch() {
    const homepageSearchForm = document.getElementById('homepage-search-form');
    try {
        return initializeHomepageSearch();
    } catch (error) {
        console.warn('搜索栏增强初始化失败，已保留基础 Bing 搜索。', error);
        if (homepageSearchForm) {
            restoreStaticBingSearch(
                homepageSearchForm,
                document.getElementById('homepage-search-input'),
                document.getElementById('search-engine-list')
            );
        }
        return null;
    }
}
