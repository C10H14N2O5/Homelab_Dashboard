const SVG_NAMESPACE = 'http://www.w3.org/2000/svg';
const XLINK_NAMESPACE = 'http://www.w3.org/1999/xlink';
const XMLNS_NAMESPACE = 'http://www.w3.org/2000/xmlns/';
const MAX_RAW_SVG_BYTES = 64 * 1024;
const MAX_CLEAN_SVG_BYTES = 24 * 1024;
const MAX_SVG_ELEMENTS = 256;
const MAX_SVG_DEPTH = 16;
const MAX_SVG_TITLE_BYTES = 256;

const ALLOWED_ELEMENTS = new Set([
    'svg',
    'g',
    'path',
    'rect',
    'circle',
    'ellipse',
    'line',
    'polyline',
    'polygon',
    'defs',
    'lineargradient',
    'radialgradient',
    'stop',
    'clippath',
    'mask',
    'symbol'
]);

const GLOBAL_ATTRIBUTES = new Set([
    'id',
    'transform',
    'fill',
    'fill-opacity',
    'fill-rule',
    'stroke',
    'stroke-width',
    'stroke-opacity',
    'stroke-linecap',
    'stroke-linejoin',
    'stroke-miterlimit',
    'opacity',
    'color',
    'clip-path',
    'clip-rule',
    'mask',
    'vector-effect'
]);

const ELEMENT_ATTRIBUTES = new Map([
    ['svg', new Set(['viewbox', 'width', 'height', 'x', 'y', 'preserveaspectratio', 'focusable'])],
    ['g', new Set()],
    ['path', new Set(['d', 'pathlength'])],
    ['rect', new Set(['x', 'y', 'width', 'height', 'rx', 'ry', 'pathlength'])],
    ['circle', new Set(['cx', 'cy', 'r', 'pathlength'])],
    ['ellipse', new Set(['cx', 'cy', 'rx', 'ry', 'pathlength'])],
    ['line', new Set(['x1', 'y1', 'x2', 'y2', 'pathlength'])],
    ['polyline', new Set(['points', 'pathlength'])],
    ['polygon', new Set(['points', 'pathlength'])],
    ['defs', new Set()],
    ['lineargradient', new Set(['x1', 'y1', 'x2', 'y2', 'gradientunits', 'gradienttransform', 'spreadmethod', 'href'])],
    ['radialgradient', new Set(['cx', 'cy', 'r', 'fx', 'fy', 'fr', 'gradientunits', 'gradienttransform', 'spreadmethod', 'href'])],
    ['stop', new Set(['offset', 'stop-color', 'stop-opacity'])],
    ['clippath', new Set(['clippathunits'])],
    ['mask', new Set(['x', 'y', 'width', 'height', 'maskunits', 'maskcontentunits'])],
    ['symbol', new Set(['viewbox', 'preserveaspectratio'])]
]);

const ATTRIBUTE_CASE = new Map([
    ['viewbox', 'viewBox'],
    ['preserveaspectratio', 'preserveAspectRatio'],
    ['pathlength', 'pathLength'],
    ['gradienttransform', 'gradientTransform'],
    ['gradientunits', 'gradientUnits'],
    ['spreadmethod', 'spreadMethod'],
    ['clippathunits', 'clipPathUnits'],
    ['maskunits', 'maskUnits'],
    ['maskcontentunits', 'maskContentUnits']
]);

const LOCAL_REFERENCE_ATTRIBUTES = new Set(['fill', 'stroke', 'clip-path', 'mask']);
const COLOR_ATTRIBUTES = new Set(['fill', 'stroke', 'color', 'stop-color']);
const SAFE_ID_PATTERN = /^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$/;
const LOCAL_URL_PATTERN = /^url\(\s*#([A-Za-z_][A-Za-z0-9_.:-]{0,127})\s*\)$/i;
const SAFE_COLOR_PATTERN = /^(?:none|currentColor|transparent|inherit|[A-Za-z]+|#[0-9A-Fa-f]{3,8}|(?:rgb|rgba|hsl|hsla)\([0-9eE+.,%\s-]+\))$/;
const CONTROL_CHARACTER_PATTERN = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/;

function utf8ByteLength(value) {
    if (typeof TextEncoder === 'function') return new TextEncoder().encode(value).byteLength;
    return unescape(encodeURIComponent(value)).length;
}

function svgError(message) {
    const error = new Error(message);
    error.name = 'CardSvgError';
    return error;
}

function parseSvg(svgMarkup) {
    if (typeof svgMarkup !== 'string' || svgMarkup.trim() === '') {
        throw svgError('请选择 SVG 图标。');
    }
    if (utf8ByteLength(svgMarkup) > MAX_RAW_SVG_BYTES) {
        throw svgError('SVG 原始文件不能超过 64 KiB。');
    }
    if (/<!DOCTYPE|<!ENTITY/i.test(svgMarkup)) {
        throw svgError('SVG 不能包含 DTD 或实体声明。');
    }

    const parsed = new DOMParser().parseFromString(svgMarkup, 'image/svg+xml');
    if (parsed.querySelector('parsererror')) throw svgError('SVG 文件格式无效。');
    const root = parsed.documentElement;
    if (!root || (root.namespaceURI !== SVG_NAMESPACE && root.namespaceURI !== null)
        || root.localName.toLowerCase() !== 'svg') {
        throw svgError('文件根元素必须是 SVG。');
    }
    return root;
}

function isXmlnsDeclaration(attribute) {
    return attribute.namespaceURI === XMLNS_NAMESPACE || attribute.name === 'xmlns';
}

function normalizeInputAttribute(attribute) {
    if (isXmlnsDeclaration(attribute)) return null;
    if (attribute.namespaceURI !== null) {
        if (attribute.namespaceURI !== XLINK_NAMESPACE || attribute.localName !== 'href') {
            throw svgError('SVG 属性不能使用其它命名空间。');
        }
        return { name: 'href', outputName: 'href' };
    }
    return {
        name: attribute.localName.toLowerCase(),
        outputName: attribute.name
    };
}

function validateTitleText(value) {
    if (typeof value !== 'string' || value.trim() === '') {
        throw svgError('SVG <title> 必须包含非空纯文本。');
    }
    if (CONTROL_CHARACTER_PATTERN.test(value)) {
        throw svgError('SVG <title> 含有控制字符。');
    }
    if (utf8ByteLength(value) > MAX_SVG_TITLE_BYTES) {
        throw svgError('SVG <title> 文本不能超过 256 字节。');
    }
}

function validateTitleElement(element, root) {
    if (element.parentElement !== root) {
        throw svgError('SVG <title> 必须是根 <svg> 的直接子元素。');
    }
    if (Array.from(element.attributes).some((attribute) => !isXmlnsDeclaration(attribute))) {
        throw svgError('SVG <title> 不能包含属性。');
    }
    if (element.children.length !== 0) {
        throw svgError('SVG <title> 只能包含纯文本。');
    }
    const supportedNodeTypes = new Set([Node.TEXT_NODE, Node.CDATA_SECTION_NODE, Node.COMMENT_NODE]);
    if (Array.from(element.childNodes).some((child) => !supportedNodeTypes.has(child.nodeType))) {
        throw svgError('SVG <title> 包含不支持的节点。');
    }
    validateTitleText(element.textContent || '');
}

function collectIdsAndCheckShape(root) {
    const ids = new Set();
    let elementCount = 0;
    let drawableCount = 0;
    let titleCount = 0;

    function visit(element, depth) {
        elementCount += 1;
        if (elementCount > MAX_SVG_ELEMENTS) throw svgError('SVG 元素数量不能超过 256 个。');
        if (depth > MAX_SVG_DEPTH) throw svgError('SVG 元素嵌套不能超过 16 层。');
        if (element.namespaceURI !== SVG_NAMESPACE && element.namespaceURI !== null) {
            throw svgError('SVG 不能包含其它命名空间。');
        }
        const localName = element.localName.toLowerCase();
        if (element.localName === 'title') {
            titleCount += 1;
            if (titleCount > 1) throw svgError('SVG 最多只能包含一个 <title>。');
            validateTitleElement(element, root);
            return;
        }
        if (!ALLOWED_ELEMENTS.has(localName)) throw svgError(`SVG 不允许使用 <${element.localName}> 元素。`);
        if (['path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon'].includes(localName)) {
            drawableCount += 1;
        }

        const id = element.getAttribute('id');
        if (id !== null) {
            if (!SAFE_ID_PATTERN.test(id)) throw svgError('SVG 内部 ID 格式无效。');
            if (ids.has(id)) throw svgError(`SVG 内部 ID 重复：${id}`);
            ids.add(id);
        }
        Array.from(element.children).forEach((child) => visit(child, depth + 1));
    }

    visit(root, 1);
    if (drawableCount === 0) throw svgError('SVG 必须至少包含一个可绘制图形。');
    return ids;
}

function validateAttributeValue(name, value, ids) {
    if (CONTROL_CHARACTER_PATTERN.test(value)) throw svgError(`SVG 属性 ${name} 含有控制字符。`);
    if (/javascript\s*:|data\s*:|https?\s*:|\/\//i.test(value)) {
        throw svgError(`SVG 属性 ${name} 不能引用外部资源。`);
    }

    if (name === 'href') {
        if (!value.startsWith('#') || !SAFE_ID_PATTERN.test(value.slice(1)) || !ids.has(value.slice(1))) {
            throw svgError('SVG href 只能引用图标内部已存在的 ID。');
        }
        return;
    }

    if (LOCAL_REFERENCE_ATTRIBUTES.has(name) && /url\s*\(/i.test(value)) {
        const reference = value.match(LOCAL_URL_PATTERN);
        if (!reference || !ids.has(reference[1])) {
            throw svgError(`SVG 属性 ${name} 只能引用图标内部已存在的 ID。`);
        }
        return;
    }

    if ((name === 'clip-path' || name === 'mask') && value !== 'none') {
        throw svgError(`SVG 属性 ${name} 必须引用图标内部 ID。`);
    }

    if (COLOR_ATTRIBUTES.has(name) && !SAFE_COLOR_PATTERN.test(value)) {
        throw svgError(`SVG 属性 ${name} 使用了不支持的颜色。`);
    }

    if (/url\s*\(/i.test(value)) throw svgError(`SVG 属性 ${name} 不能包含资源引用。`);
}

function copyElement(source, ids, depth = 1) {
    const localName = source.localName.toLowerCase();
    const target = document.createElementNS(SVG_NAMESPACE, source.localName);

    Array.from(source.attributes).forEach((attribute) => {
        const normalized = normalizeInputAttribute(attribute);
        if (normalized === null) return;
        const { name, outputName } = normalized;
        if (attribute.localName === 'style') return;
        if (name === 'class' || name.startsWith('on')) throw svgError(`SVG 不允许使用 ${attribute.name} 属性。`);
        if (!GLOBAL_ATTRIBUTES.has(name) && !ELEMENT_ATTRIBUTES.get(localName)?.has(name)) {
            throw svgError(`SVG 不允许在 <${source.localName}> 使用 ${attribute.name} 属性。`);
        }
        if (name === 'href' && !['lineargradient', 'radialgradient'].includes(localName)) {
            throw svgError('SVG href 只允许用于渐变的内部继承。');
        }
        validateAttributeValue(name, attribute.value, ids);
        target.setAttribute(ATTRIBUTE_CASE.get(name) || outputName, attribute.value);
    });

    Array.from(source.childNodes).forEach((child) => {
        if (child.nodeType === Node.ELEMENT_NODE) {
            if (child.localName === 'title') return;
            target.append(copyElement(child, ids, depth + 1));
            return;
        }
        if (child.nodeType === Node.TEXT_NODE) {
            if ((child.textContent || '').trim() !== '') throw svgError('SVG 图形元素不能包含文本内容。');
            return;
        }
        if (child.nodeType !== Node.COMMENT_NODE) throw svgError('SVG 包含不支持的节点。');
    });

    return target;
}

function sanitizeToElement(svgMarkup) {
    const parsedRoot = parseSvg(svgMarkup);
    const ids = collectIdsAndCheckShape(parsedRoot);
    const cleanRoot = copyElement(parsedRoot, ids);
    const cleanMarkup = new XMLSerializer().serializeToString(cleanRoot);
    if (utf8ByteLength(cleanMarkup) > MAX_CLEAN_SVG_BYTES) {
        throw svgError('清理后的 SVG 不能超过 24 KiB。');
    }
    return { element: cleanRoot, markup: cleanMarkup };
}

export function sanitizeSvgMarkup(svgMarkup) {
    return sanitizeToElement(svgMarkup).markup;
}

export function createSafeSvgElement(svgMarkup) {
    return sanitizeToElement(svgMarkup).element;
}

export const cardSvgLimits = Object.freeze({
    rawBytes: MAX_RAW_SVG_BYTES,
    cleanBytes: MAX_CLEAN_SVG_BYTES,
    elements: MAX_SVG_ELEMENTS,
    depth: MAX_SVG_DEPTH
});
