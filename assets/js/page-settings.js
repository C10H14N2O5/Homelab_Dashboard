export function initializePageSettings() {
    const container = document.getElementById('page-settings');
    const trigger = document.getElementById('page-settings-button');
    const menu = document.getElementById('page-settings-menu');
    if (!container || !trigger || !menu) throw new Error('网页设置菜单结构不完整。');
    const items = Array.from(menu.querySelectorAll('[role="menuitem"]'));
    if (items.length !== 2) throw new Error('网页设置菜单项结构不完整。');
    let pointerIsInside = false;

    function enabledItems() {
        return items.filter((item) => !item.disabled && item.getAttribute('aria-disabled') !== 'true');
    }

    function isOpen() {
        return !menu.hidden;
    }

    function focusTrigger() {
        if (trigger.isConnected) trigger.focus({ preventScroll: true });
    }

    function close(options = {}) {
        if (!isOpen()) {
            if (options.restoreFocus) focusTrigger();
            return false;
        }
        menu.hidden = true;
        container.dataset.menuOpen = 'false';
        trigger.setAttribute('aria-expanded', 'false');
        if (options.restoreFocus) focusTrigger();
        return true;
    }

    function open(options = {}) {
        const available = enabledItems();
        if (available.length === 0) return false;
        menu.hidden = false;
        container.dataset.menuOpen = 'true';
        trigger.setAttribute('aria-expanded', 'true');
        const target = options.last ? available[available.length - 1] : available[0];
        target.focus({ preventScroll: true });
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
        if (!item || item.disabled || item.getAttribute('aria-disabled') === 'true') return;
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

    close();
    return { close, focusTrigger, isOpen, open };
}
