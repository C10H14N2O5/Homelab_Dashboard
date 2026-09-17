const EASING = 'cubic-bezier(0.2, 0.8, 0.2, 1)';

export function createNotesSelectionMotion(list, pane, onChange) {
    const reduced = matchMedia('(prefers-reduced-motion: reduce)');
    const highlight = document.createElement('div');
    highlight.className = 'notes-list-highlight';
    highlight.setAttribute('aria-hidden', 'true');
    highlight.hidden = true;
    list.dataset.selectionMotion = 'true';
    let highlightAnimation;
    let highlightTarget;
    let fadeAnimation;
    let revision = 0;
    let busy = false;
    pane.dataset.switchPhase = 'idle';

    function updateHighlight(animate = true) {
        const row = list.querySelector('[data-active="true"]');
        if (!row || !row.offsetWidth || !list.clientWidth) {
            highlightAnimation?.cancel();
            highlightTarget = null;
            highlight.hidden = true;
            return;
        }
        const target = { transform: `translate(${row.offsetLeft}px, ${row.offsetTop}px)`, width: `${row.offsetWidth}px`, height: `${row.offsetHeight}px` };
        if (animate && highlightTarget && Object.keys(target).every((key) => target[key] === highlightTarget[key])) return;
        const style = getComputedStyle(highlight);
        const from = { transform: style.transform, width: style.width, height: style.height };
        const canAnimate = animate && highlightTarget && !highlight.hidden && !reduced.matches && typeof highlight.animate === 'function';
        highlightAnimation?.cancel();
        highlight.hidden = false;
        Object.assign(highlight.style, target);
        highlightTarget = target;
        if (canAnimate) {
            highlightAnimation = highlight.animate([from, target], { duration: 280, easing: EASING });
        }
    }

    async function fade(opacity, duration, token) {
        const from = getComputedStyle(pane).opacity;
        fadeAnimation?.cancel();
        pane.style.opacity = String(opacity);
        if (reduced.matches || typeof pane.animate !== 'function' || Math.abs(Number(from) - opacity) < 0.01) return;
        const animation = pane.animate([{ opacity: from }, { opacity }], { duration, easing: EASING });
        fadeAnimation = animation;
        const timer = setTimeout(() => { if (token === revision) animation.finish(); }, duration + 100);
        try { await animation.finished; } catch { /* A newer selection owns the pane. */ }
        finally {
            clearTimeout(timer);
            animation.cancel();
            if (fadeAnimation === animation) fadeAnimation = null;
        }
    }

    function finish() {
        busy = false;
        pane.style.opacity = '';
        pane.dataset.switchPhase = 'idle';
        pane.removeAttribute('aria-busy');
        onChange();
    }

    async function switchTo(renderTarget, { skipFadeOut = false } = {}) {
        const token = ++revision;
        const current = () => token === revision;
        busy = true;
        pane.dataset.switchPhase = 'fading-out';
        pane.setAttribute('aria-busy', 'true');
        onChange();
        try {
            if (skipFadeOut) { fadeAnimation?.cancel(); pane.style.opacity = '0'; }
            else await fade(0, 120, token);
            if (!current()) return;
            pane.dataset.switchPhase = 'loading';
            await renderTarget(current);
            if (!current()) return;
            pane.dataset.switchPhase = 'fading-in';
            await fade(1, 160, token);
        } finally { if (current()) finish(); }
    }

    function cancel() {
        if (!busy) return;
        revision++;
        fadeAnimation?.cancel();
        finish();
    }

    new ResizeObserver(() => updateHighlight(false)).observe(list);
    reduced.addEventListener('change', () => {
        if (reduced.matches) fadeAnimation?.finish();
        updateHighlight(false);
    });
    return { highlight, updateHighlight, switchTo, cancel, isBusy: () => busy };
}
