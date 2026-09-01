export function initializePageSettings() {
    const container = document.getElementById('page-settings');
    const trigger = document.getElementById('page-settings-button');
    const menu = document.getElementById('page-settings-menu');
    if (!container || !trigger || !menu) throw new Error('网页设置菜单结构不完整。');
    const items = Array.from(menu.querySelectorAll('[role="menuitem"]'));
    if (items.length !== 2) throw new Error('网页设置菜单项结构不完整。');
    const closeFallbackMs = 220;
    let pointerIsInside = false;
    let motionToken = 0;
    let menuState = 'closed';
    let firstRevealFrame = 0;
    let secondRevealFrame = 0;
    let closeTimer = 0;
    let closeHandler = null;
    let pendingFocusTarget = null;

    function enabledItems() {
        return items.filter((item) => !item.disabled && item.getAttribute('aria-disabled') !== 'true');
    }

    function isOpen() {
        return menuState === 'opening' || menuState === 'open';
    }

    function focusTrigger() {
        if (trigger.isConnected) trigger.focus({ preventScroll: true });
    }

    function prefersReducedMotion() {
        return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    }

    function clearMotionWait() {
        if (firstRevealFrame) window.cancelAnimationFrame(firstRevealFrame);
        if (secondRevealFrame) window.cancelAnimationFrame(secondRevealFrame);
        firstRevealFrame = 0;
        secondRevealFrame = 0;
        if (closeHandler) menu.removeEventListener('transitionend', closeHandler);
        closeHandler = null;
        window.clearTimeout(closeTimer);
        closeTimer = 0;
    }

    function beginMotion() {
        motionToken += 1;
        clearMotionWait();
        return motionToken;
    }

    function finishClose(token) {
        if (token !== motionToken || menuState !== 'closing') return;
        clearMotionWait();
        pendingFocusTarget = null;
        menu.hidden = true;
        menuState = 'closed';
    }

    function close(options = {}) {
        if (menuState === 'closed') {
            if (options.restoreFocus) focusTrigger();
            return false;
        }
        if (menuState === 'closing') {
            if (options.restoreFocus) focusTrigger();
            return false;
        }
        const token = beginMotion();
        menuState = 'closing';
        pendingFocusTarget = null;
        container.dataset.menuOpen = 'false';
        trigger.setAttribute('aria-expanded', 'false');
        if (options.restoreFocus) focusTrigger();
        if (prefersReducedMotion()) {
            finishClose(token);
        } else {
            closeHandler = (event) => {
                if (event.target !== menu || event.propertyName !== 'opacity') return;
                finishClose(token);
            };
            menu.addEventListener('transitionend', closeHandler);
            closeTimer = window.setTimeout(() => finishClose(token), closeFallbackMs);
        }
        return true;
    }

    function open(options = {}) {
        const available = enabledItems();
        if (available.length === 0) return false;
        const target = options.last ? available[available.length - 1] : available[0];
        if (menuState === 'open') {
            target.focus({ preventScroll: true });
            return true;
        }
        if (menuState === 'opening') {
            pendingFocusTarget = target;
            return true;
        }
        const token = beginMotion();
        menuState = 'opening';
        pendingFocusTarget = target;
        menu.hidden = false;
        container.dataset.menuOpen = 'false';
        trigger.setAttribute('aria-expanded', 'true');

        const reveal = () => {
            secondRevealFrame = 0;
            if (token !== motionToken || menuState !== 'opening') return;
            menuState = 'open';
            container.dataset.menuOpen = 'true';
            const focusTarget = pendingFocusTarget;
            pendingFocusTarget = null;
            focusTarget?.focus({ preventScroll: true });
        };
        if (prefersReducedMotion()) {
            reveal();
        } else {
            firstRevealFrame = window.requestAnimationFrame(() => {
                firstRevealFrame = 0;
                secondRevealFrame = window.requestAnimationFrame(reveal);
            });
        }
        return true;
    }

    function moveFocus(direction) {
        const available = enabledItems();
        if (available.length === 0) return;
        const currentIndex = available.indexOf(document.activeElement);
        const baseIndex = currentIndex >= 0 ? currentIndex : 0;
        const nextIndex = (baseIndex + direction + available.length) % available.length;
        available[nextIndex].focus({ preventScroll: true });
    }

    trigger.addEventListener('click', () => {
        if (isOpen()) close({ restoreFocus: true });
        else open();
    });
    trigger.addEventListener('keydown', (event) => {
        if (event.key === 'ArrowDown') {
            event.preventDefault();
            open();
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            open({ last: true });
        } else if (event.key === 'Escape') {
            event.preventDefault();
            close({ restoreFocus: true });
        }
    });
    menu.addEventListener('keydown', (event) => {
        if (!isOpen()) {
            event.preventDefault();
            return;
        }
        if (event.key === 'ArrowDown') {
            event.preventDefault();
            moveFocus(1);
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            moveFocus(-1);
        } else if (event.key === 'Home') {
            event.preventDefault();
            enabledItems()[0]?.focus({ preventScroll: true });
        } else if (event.key === 'End') {
            event.preventDefault();
            const available = enabledItems();
            available[available.length - 1]?.focus({ preventScroll: true });
        } else if (event.key === 'Escape') {
            event.preventDefault();
            close({ restoreFocus: true });
        } else if (event.key === 'Tab') {
            close();
        }
    });
    // Let the menu item's dialog handler and native click activation finish first.
    // Edge can otherwise cancel a dialog opened while its triggering menu is hidden.
    menu.addEventListener('click', (event) => {
        const item = event.target.closest?.('[role="menuitem"]');
        if (!isOpen() || !item || item.disabled || item.getAttribute('aria-disabled') === 'true') return;
        window.setTimeout(() => close(), 0);
    });
    container.addEventListener('pointerdown', () => {
        pointerIsInside = true;
    }, true);
    container.addEventListener('focusout', () => {
        Promise.resolve().then(() => {
            if (!pointerIsInside && isOpen() && !container.contains(document.activeElement)) close();
        });
    });
    document.addEventListener('pointerdown', (event) => {
        if (isOpen() && !container.contains(event.target)) close();
    }, true);
    document.addEventListener('pointerup', () => {
        pointerIsInside = false;
    }, true);
    document.addEventListener('pointercancel', () => {
        pointerIsInside = false;
    }, true);
    window.addEventListener('resize', () => {
        close({ restoreFocus: menu.contains(document.activeElement) });
    });

    menu.hidden = true;
    container.dataset.menuOpen = 'false';
    trigger.setAttribute('aria-expanded', 'false');
    return { close, focusTrigger, isOpen, open };
}
