// Services and Quick Links channel switching and transition handling.
export function initializeChannelSwitch() {
            let cards = Array.from(document.querySelectorAll('.service-card'));
            let quickLinks = Array.from(document.querySelectorAll('.quick-link-card'));
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
            let controllerReady = false;

            function syncQuickLinkDimensions() {
                const firstServiceCard = cards[0];
                if (!serviceGrid || !quickLinksGrid || !channelPanelStage) return;
                quickLinksGrid.style.removeProperty('--quick-card-width');
                quickLinksGrid.style.removeProperty('--quick-links-width');
                channelPanelStage.style.removeProperty('--services-panel-height');
                const servicesWereHidden = servicesPanel.hidden;
                servicesPanel.hidden = false;
                try {
                    const serviceGridHeight = serviceGrid.getBoundingClientRect().height;
                    if (Number.isFinite(serviceGridHeight) && serviceGridHeight > 0) {
                        channelPanelStage.style.setProperty('--services-panel-height', `${serviceGridHeight}px`);
                    }
                    if (!firstServiceCard || quickLinks.length === 0) return;
                    const cardWidth = firstServiceCard.getBoundingClientRect().width;
                    const computedStyle = window.getComputedStyle(serviceGrid);
                    const columnGap = Number.parseFloat(computedStyle.columnGap);
                    if (!Number.isFinite(cardWidth) || cardWidth <= 0
                        || !Number.isFinite(columnGap) || columnGap < 0) return;
                    const quickLinkColumns = Math.min(5, quickLinks.length);
                    const quickLinksWidth = cardWidth * quickLinkColumns + columnGap * Math.max(0, quickLinkColumns - 1);
                    quickLinksGrid.style.setProperty('--quick-card-width', `${cardWidth}px`);
                    quickLinksGrid.style.setProperty('--quick-links-width', `${quickLinksWidth}px`);
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
                const quickLinksWereHidden = quickLinksPanel.hidden;
                servicesPanel.hidden = false;
                quickLinksPanel.hidden = false;
                try {
                    const servicesHeight = serviceGrid.getBoundingClientRect().height;
                    const quickLinksHeight = quickLinksGrid.getBoundingClientRect().height;
                    const panelHeight = Math.max(
                        Number.isFinite(servicesHeight) ? servicesHeight : 0,
                        Number.isFinite(quickLinksHeight) ? quickLinksHeight : 0
                    );
                    if (panelHeight <= 0) {
                        channelPanelStage.style.removeProperty('--channel-panel-height');
                        return;
                    }
                    channelPanelStage.style.setProperty('--channel-panel-height', `${panelHeight}px`);
                } finally {
                    servicesPanel.hidden = servicesWereHidden;
                    quickLinksPanel.hidden = quickLinksWereHidden;
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

            function initializeChannelSwitchController() {
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
                if (requiredElements.some((element) => !element)) {
                    throw new Error('双频道页面结构不完整');
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

            function resyncCards() {
                cards = Array.from(document.querySelectorAll('#service-grid .service-card'));
                quickLinks = Array.from(document.querySelectorAll('#quick-links-grid .quick-link-card'));
                cards.forEach((card, index) => {
                    card.style.setProperty('--channel-card-index', String(index));
                });
                quickLinks.forEach((card, index) => {
                    card.style.setProperty('--channel-card-index', String(index));
                });
                if (!controllerReady) return;
                if (channelSwitchLocked) finishChannelSwitch();
                serviceCountLabel.textContent = channelCountText(activeChannel);
                window.requestAnimationFrame(() => {
                    configureChannelMotion();
                });
            }

    try {
        initializeChannelSwitchController();
        controllerReady = true;
    } catch (error) {
        console.warn('双频道预览初始化失败，已保留 Services 默认面。', error);
        restoreStaticServicesChannel();
    }
    return {
        resync: resyncCards,
        getActiveChannel() {
            return activeChannel;
        }
    };
}
