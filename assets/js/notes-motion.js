const EASING = 'cubic-bezier(0.2, 0.8, 0.2, 1)';
const RESIZE_DURATION = 300;
const FADE_DURATION = 120;

// Geometry and visibility are independent of fetching notes or saving drafts.
export function createNotesMotion(shell, capsule, panel, onChange = () => {}) {
    const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
    const animations = new Set();
    let revision = 0;
    let busy = false;
    let targetOpen = false;
    let pending = Promise.resolve();
    shell.dataset.motion = 'closed';
    panel.inert = true;

    function cancelAnimations() {
        for (const animation of animations) animation.cancel();
        animations.clear();
    }

    function clearGeometry() {
        for (const key of ['width', 'height', 'borderRadius', 'backgroundColor']) shell.style[key] = '';
        for (const element of [capsule, panel]) {
            for (const key of ['width', 'height', 'opacity']) element.style[key] = '';
        }
    }

    function finish() {
        cancelAnimations();
        shell.dataset.open = String(targetOpen);
        shell.dataset.motion = targetOpen ? 'open' : 'closed';
        clearGeometry();
        capsule.hidden = targetOpen;
        panel.hidden = !targetOpen;
        capsule.inert = targetOpen;
        panel.inert = !targetOpen;
        capsule.setAttribute('aria-expanded', String(targetOpen));
        busy = false;
        onChange();
    }

    function measure(open) {
        const beforeOpen = shell.dataset.open;
        const beforeMotion = shell.dataset.motion;
        const inline = shell.getAttribute('style');
        shell.style.width = shell.style.height = shell.style.borderRadius = shell.style.backgroundColor = '';
        shell.dataset.open = String(open);
        shell.dataset.motion = open ? 'open' : 'closed';
        const { width, height } = shell.getBoundingClientRect();
        const style = getComputedStyle(shell);
        const result = { width, height, backgroundColor: style.backgroundColor,
            radius: open ? style.borderTopLeftRadius : `${height / 2}px`,
            border: parseFloat(style.borderLeftWidth) + parseFloat(style.borderRightWidth) };
        shell.dataset.open = beforeOpen;
        shell.dataset.motion = beforeMotion;
        if (inline === null) shell.removeAttribute('style');
        else shell.setAttribute('style', inline);
        return result;
    }

    async function animate(element, from, to, duration, token, delay = 0) {
        if (token !== revision) return false;
        const animation = element.animate([from, to], { duration, delay, easing: EASING, fill: 'both' });
        animations.add(animation);
        // Also complete when a browser suspends animation events in a hidden tab.
        const fallback = setTimeout(() => { if (token === revision) animation.finish(); }, delay + duration + 100);
        try {
            await animation.finished;
            if (token !== revision) return false;
            Object.assign(element.style, to);
            return true;
        } catch { return false; }
        finally { clearTimeout(fallback); animations.delete(animation); animation.cancel(); }
    }

    function setOpen(open) {
        if (targetOpen === open && (busy || shell.dataset.motion === (open ? 'open' : 'closed'))) return pending;
        const token = ++revision;
        const box = shell.getBoundingClientRect();
        const style = getComputedStyle(shell);
        const from = { width: `${box.width}px`, height: `${box.height}px`,
            borderRadius: `${Math.min(parseFloat(style.borderTopLeftRadius), box.height / 2)}px`,
            backgroundColor: style.backgroundColor };
        const contentOpacity = panel.hidden ? '0' : getComputedStyle(panel).opacity;
        cancelAnimations();
        targetOpen = open;
        const closed = measure(false);
        const expanded = measure(true);
        Object.assign(shell.style, from);
        shell.dataset.open = String(open);
        capsule.setAttribute('aria-expanded', String(open));
        busy = true;
        capsule.inert = panel.inert = true;
        onChange();
        if (reducedMotion.matches || typeof shell.animate !== 'function') {
            finish();
            pending = Promise.resolve();
            return pending;
        }

        panel.style.width = `${expanded.width - expanded.border}px`;
        panel.style.height = `${expanded.height - expanded.border}px`;
        capsule.style.width = `${closed.width - closed.border}px`;
        pending = (async () => {
            if (open) {
                shell.dataset.motion = 'expanding';
                panel.hidden = false;
                panel.style.opacity = '0';
                await Promise.all([
                    animate(shell, from, { width: `${expanded.width}px`, height: `${expanded.height}px`,
                        borderRadius: expanded.radius, backgroundColor: expanded.backgroundColor }, RESIZE_DURATION, token),
                    animate(capsule, { opacity: '1' }, { opacity: '0' }, FADE_DURATION, token),
                ]);
                if (token !== revision) return;
                capsule.hidden = true;
                shell.dataset.motion = 'revealing';
                await animate(panel, { opacity: '0' }, { opacity: '1' }, FADE_DURATION, token);
            } else {
                shell.dataset.motion = 'fading';
                await animate(panel, { opacity: contentOpacity }, { opacity: '0' }, FADE_DURATION, token);
                if (token !== revision) return;
                panel.hidden = true;
                capsule.hidden = false;
                shell.dataset.motion = 'collapsing';
                await Promise.all([
                    animate(shell, from, { width: `${closed.width}px`, height: `${closed.height}px`,
                        borderRadius: closed.radius, backgroundColor: closed.backgroundColor }, RESIZE_DURATION, token),
                    animate(capsule, { opacity: '0' }, { opacity: '1' }, FADE_DURATION, token, RESIZE_DURATION - FADE_DURATION),
                ]);
            }
            if (token === revision) finish();
        })();
        return pending;
    }

    function settleOnEnvironmentChange() {
        if (!busy) return;
        revision++;
        finish();
    }
    window.addEventListener('resize', settleOnEnvironmentChange);
    reducedMotion.addEventListener('change', settleOnEnvironmentChange);
    return { setOpen, isBusy: () => busy, settled: () => pending };
}
