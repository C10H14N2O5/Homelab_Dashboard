import { initializeBackgroundManager } from './background-manager.js';
import { initializeSearch } from './search.js';
import { initializeChannelSwitch } from './channel-switch.js';
import { initializeServiceStatus } from './service-status.js';
import { initializeCardManager } from './card-manager.js';
import { initializePageSettings } from './page-settings.js';
import { initializeFaviconManager } from './favicon-manager.js';
import { initializeNotes } from './notes-manager.js';

function initializeIntro() {
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
}

function initializeFeature(label, initializer) {
    try {
        return initializer();
    } catch (error) {
        console.warn(`${label}初始化失败，已保留静态页面降级。`, error);
        return null;
    }
}

initializeFeature('页面入场', initializeIntro);
initializeFeature('背景管理', initializeBackgroundManager);
initializeFeature('搜索栏增强', initializeSearch);
const pageSettingsController = initializeFeature('网页设置菜单', initializePageSettings);
initializeFeature('笔记', initializeNotes);
const restorePageSettingsFocus = () => {
    if (pageSettingsController) pageSettingsController.focusTrigger();
    else document.getElementById('page-settings-button')?.focus({ preventScroll: true });
};
initializeFeature('网站图标', () => initializeFaviconManager({
    restoreFocus: restorePageSettingsFocus
}));
initializeFeature('页面图标', () => initializeFaviconManager({
    kind: 'page-icon',
    restoreFocus: restorePageSettingsFocus
}));

async function initializeDashboardNavigation() {
    let cardManager = null;
    try {
        // Card data must settle before channel sizing and service probes capture the DOM.
        cardManager = await initializeCardManager({
            editorRestoreFocus: restorePageSettingsFocus
        });
    } catch (error) {
        console.warn('卡片数据初始化失败，已保留静态卡片降级。', error);
    }

    const channelController = initializeFeature('双频道预览', initializeChannelSwitch);
    const serviceStatusController = initializeFeature('服务状态', initializeServiceStatus);
    if (cardManager) {
        cardManager.subscribe(() => {
            channelController?.resync();
            serviceStatusController?.resync();
        });
    }
}

initializeDashboardNavigation();
