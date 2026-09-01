export function createDialogMotionController(dialog, closeFallbackMs) {
    let animationToken = 0;
    let closeTimer = 0;
    let closeHandler = null;
    let closePromise = null;
    let closeResolve = null;

    function isOpen() {
        return dialog.open || dialog.hasAttribute('open');
    }

    function isClosing() {
        return dialog.classList.contains('is-closing');
    }

    function clearCloseWait(settlePending = false) {
        if (closeHandler) dialog.removeEventListener('transitionend', closeHandler);
        closeHandler = null;
        window.clearTimeout(closeTimer);
        closeTimer = 0;
        if (!settlePending || !closeResolve) return;
        const resolve = closeResolve;
        closeResolve = null;
        closePromise = null;
        resolve(false);
    }

    function reset() {
        animationToken += 1;
        clearCloseWait(true);
        dialog.classList.remove('is-visible', 'is-closing');
    }

    function show(onVisible) {
        reset();
        const token = animationToken;
        if (typeof dialog.showModal === 'function') {
            if (!dialog.open) dialog.showModal();
        } else {
            dialog.setAttribute('open', '');
        }

        const reveal = () => {
            if (token !== animationToken || !isOpen() || isClosing()) return;
            dialog.classList.add('is-visible');
            onVisible?.();
        };
        if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
            reveal();
        } else {
            window.requestAnimationFrame(() => window.requestAnimationFrame(reveal));
        }
    }

    function hide(returnValue = '', restoreFocus = null) {
        if (!isOpen()) {
            restoreFocus?.();
            return Promise.resolve(false);
        }
        if (isClosing()) return closePromise || Promise.resolve(false);

        const token = ++animationToken;
        dialog.classList.add('is-closing');
        dialog.classList.remove('is-visible');
        closePromise = new Promise((resolve) => {
            closeResolve = resolve;
        });
        const pending = closePromise;

        const finishAfterTransition = (event) => {
            if (event && (event.target !== dialog || event.propertyName !== 'opacity')) return;
            if (token !== animationToken) return;
            if (closeHandler) dialog.removeEventListener('transitionend', closeHandler);
            closeHandler = null;
            window.clearTimeout(closeTimer);
            closeTimer = 0;
            const resolve = closeResolve;
            closeResolve = null;
            closePromise = null;
            dialog.classList.remove('is-visible', 'is-closing');
            if (typeof dialog.close === 'function' && dialog.open) {
                dialog.close(returnValue);
            } else {
                dialog.removeAttribute('open');
            }
            restoreFocus?.();
            resolve?.(true);
        };

        if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
            finishAfterTransition();
        } else {
            closeHandler = finishAfterTransition;
            dialog.addEventListener('transitionend', finishAfterTransition);
            closeTimer = window.setTimeout(finishAfterTransition, closeFallbackMs);
        }
        return pending;
    }

    dialog.addEventListener('close', () => {
        if (isOpen()) return;
        reset();
    });

    return { hide, isClosing, isOpen, show };
}
