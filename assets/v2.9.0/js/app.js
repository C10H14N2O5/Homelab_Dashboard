import { initializeBackgroundManager } from './background-manager.js';
import { initializeSearch } from './search.js';
import { initializeChannelSwitch } from './channel-switch.js';
import { initializeServiceStatus } from './service-status.js';

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
initializeFeature('双频道预览', initializeChannelSwitch);
initializeFeature('服务状态', initializeServiceStatus);
