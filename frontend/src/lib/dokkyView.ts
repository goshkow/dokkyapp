/* Наглядные блоки в ответах режима кода: модель собирает их из готовых деталей dv-* (prompts/dokky_view.ru.txt),
   а здесь пропускается только эта разметка. Скриптов, стилей, ссылок, форм и чужих картинок нет: вид задают
   только наши классы, а расчёты считает свой разборщик выражений без eval. */
import DOMPurify from 'dompurify';
import { richMediaURL } from './rich';

export const VIEW_LANGUAGE = 'dokky-view';
export const VIEW_PENDING = 'dokky-view-pending';
const MAX_SOURCE = 60_000;
const MAX_EXPRESSION = 300;
const MAX_FIELDS = 24;
const MAX_OUTPUTS = 24;
// внешняя картинка - запрос наружу без клика, поэтому их немного
const MAX_IMAGES = 6;

const TAGS = ['div', 'section', 'span', 'p', 'b', 'strong', 'i', 'em', 'small', 'br', 'h3', 'h4', 'ul', 'ol', 'li',
  'table', 'thead', 'tbody', 'tr', 'th', 'td', 'img', 'figure', 'figcaption', 'label', 'input', 'output', 'hr'];
const ATTRS = ['class', 'data-tone', 'data-value', 'data-max', 'data-var', 'data-expr', 'data-digits', 'data-unit',
  'min', 'max', 'step', 'value', 'type', 'src', 'alt', 'title', 'colspan', 'rowspan'];

/** Детали конструктора: всё, что не отсюда, отбрасывается, чтобы вид оставался видом Докки. */
const CLASSES = new Set([
  'dv-card', 'dv-title', 'dv-text', 'dv-muted', 'dv-small', 'dv-grid', 'dv-grid-2', 'dv-grid-3',
  'dv-stats', 'dv-stat', 'dv-compare', 'dv-col', 'dv-pros', 'dv-cons', 'dv-ba', 'dv-before', 'dv-after', 'dv-label',
  'dv-steps', 'dv-recipe', 'dv-cover', 'dv-chips', 'dv-chip', 'dv-bars', 'dv-bar', 'dv-callout', 'dv-table',
  'dv-calc', 'dv-field', 'dv-value', 'dv-result', 'dv-kv', 'dv-badge', 'dv-big', 'dv-accent', 'dv-timeline', 'dv-event',
]);
const TONES = new Set(['accent', 'positive', 'danger', 'warning', 'neutral']);
const NAME = /^[a-z_][a-z0-9_]{0,31}$/i;

// ---------- выражения расчётов ----------

type Node = number | string | { op: string; args: Node[] } | { fn: string; args: Node[] };

const FUNCTIONS: Record<string, (...items: number[]) => number> = {
  abs: Math.abs, min: Math.min, max: Math.max, floor: Math.floor, ceil: Math.ceil, sqrt: (v) => (v >= 0 ? Math.sqrt(v) : NaN),
  pow: Math.pow, ln: (v) => (v > 0 ? Math.log(v) : NaN), log10: (v) => (v > 0 ? Math.log10(v) : NaN),
  round: (v, digits = 0) => { const scale = 10 ** Math.max(0, Math.min(10, Math.trunc(digits))); return Math.round(v * scale) / scale; },
  clamp: (v, low, high) => Math.max(low, Math.min(high, v)),
  // платёж по кредиту: ставка за период, число периодов, сумма
  pmt: (rate, periods, present) => (Math.abs(rate) < 1e-12 ? (periods ? present / periods : NaN) : (present * rate) / (1 - (1 + rate) ** -periods)),
  // будущая стоимость: ставка, периоды, взнос за период, начальная сумма
  fv: (rate, periods, payment, present = 0) => {
    if (Math.abs(rate) < 1e-12) return present + payment * periods;
    const growth = (1 + rate) ** periods;
    return present * growth + (payment * (growth - 1)) / rate;
  },
  if: (test, yes, no) => (test ? yes : no),
};

/** Разбор выражения: числа, имена полей, + - * / % ^, сравнения, скобки и функции из списка. */
export function parseExpression(source: string): Node {
  if (source.length > MAX_EXPRESSION) throw new Error('long');
  const tokens = source.match(/(?:\d+(?:\.\d+)?|\.\d+)(?:e[+-]?\d+)?|[a-z_][a-z0-9_]*|<=|>=|==|!=|[-+*/%^(),<>]|\S/gi) ?? [];
  let index = 0;
  const peek = () => tokens[index];
  const take = (expected?: string) => {
    const token = tokens[index];
    if (token === undefined || (expected && token !== expected)) throw new Error('syntax');
    index += 1;
    return token;
  };
  const binary = (next: () => Node, ops: string[]) => (): Node => {
    let left = next();
    while (ops.includes(peek() ?? '')) left = { op: take(), args: [left, next()] };
    return left;
  };
  const primary = (): Node => {
    const token = take();
    if (token === '(') { const inner = compare(); take(')'); return inner; }
    if (token === '-' || token === '+') return { op: token === '-' ? 'neg' : 'pos', args: [power()] };
    if (/^\.?\d/.test(token)) return Number(token);
    if (NAME.test(token)) {
      if (peek() !== '(') return token.toLowerCase();
      const name = token.toLowerCase();
      if (!Object.hasOwn(FUNCTIONS, name)) throw new Error('function');
      take('(');
      const args: Node[] = [];
      if (peek() !== ')') { args.push(compare()); while (peek() === ',') { take(); args.push(compare()); } }
      take(')');
      return { fn: name, args };
    }
    throw new Error('syntax');
  };
  // степень правоассоциативна: 2^3^2 = 2^9
  const power = (): Node => { const base = primary(); return peek() === '^' ? { op: take(), args: [base, unary()] } : base; };
  const unary = (): Node => (peek() === '-' || peek() === '+' ? { op: take() === '-' ? 'neg' : 'pos', args: [unary()] } : power());
  const product = binary(unary, ['*', '/', '%']);
  const sum = binary(product, ['+', '-']);
  const compare = binary(sum, ['<', '>', '<=', '>=', '==', '!=']);
  const tree = compare();
  if (index !== tokens.length) throw new Error('syntax');
  return tree;
}

export function evaluate(node: Node, scope: Record<string, number>): number {
  if (typeof node === 'number') return node;
  if (typeof node === 'string') return Object.hasOwn(scope, node) ? scope[node]! : NaN;
  const args = node.args.map((arg) => evaluate(arg, scope));
  if ('fn' in node) return FUNCTIONS[node.fn]!(...args);
  const [a = NaN, b = NaN] = args;
  switch (node.op) {
    case 'neg': return -a;
    case 'pos': return a;
    case '+': return a + b;
    case '-': return a - b;
    case '*': return a * b;
    case '/': return a / b;
    case '%': return a % b;
    case '^': return a ** b;
    case '<': return Number(a < b);
    case '>': return Number(a > b);
    case '<=': return Number(a <= b);
    case '>=': return Number(a >= b);
    case '==': return Number(a === b);
    case '!=': return Number(a !== b);
    default: return NaN;
  }
}

function format(value: number, digits: number, unit: string): string {
  if (!Number.isFinite(value)) return '-';
  // округлённое -0,4 не должно показываться как «-0»
  if (Math.abs(value) < 0.5 * 10 ** -digits) value = 0;
  const text = value.toLocaleString('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return unit ? `${text} ${unit}` : text;
}

const number = (value: string | null, fallback: number): number => {
  if (value === null || !value.trim()) return fallback;
  const parsed = Number(value.replace(',', '.'));
  return Number.isFinite(parsed) ? parsed : fallback;
};

/** Поля расчёта: каждое поле задаёт переменную, каждый вывод пересчитывается при движении ползунка. */
function wireCalc(root: HTMLElement): void {
  const inputs = [...root.querySelectorAll<HTMLInputElement>('input[data-var]')];
  // лишние поля и выводы убираются, а не остаются мёртвыми
  inputs.splice(MAX_FIELDS).forEach((input) => input.remove());
  const outputs: Array<{ node: HTMLOutputElement; tree: Node; digits: number; unit: string }> = [];
  const found = [...root.querySelectorAll<HTMLOutputElement>('output[data-expr]')];
  found.splice(MAX_OUTPUTS).forEach((node) => node.remove());
  for (const node of found) {
    try {
      outputs.push({
        node,
        tree: parseExpression(node.dataset.expr ?? ''),
        digits: Math.max(0, Math.min(6, Math.trunc(number(node.dataset.digits ?? null, 0)))),
        unit: (node.dataset.unit ?? '').slice(0, 12),
      });
    } catch {
      node.textContent = '-';
    }
  }
  const values: Record<string, number> = {};
  const show = (input: HTMLInputElement) => {
    const out = input.closest('.dv-field')?.querySelector<HTMLElement>('.dv-value');
    if (out) out.textContent = format(values[input.dataset.var!.toLowerCase()] ?? NaN, Math.max(0, Math.min(6, Math.trunc(number(input.dataset.digits ?? null, 0)))), (input.dataset.unit ?? '').slice(0, 12));
  };
  const recompute = () => {
    for (const input of inputs) values[input.dataset.var!.toLowerCase()] = number(input.value, 0);
    for (const output of outputs) output.node.textContent = format(evaluate(output.tree, values), output.digits, output.unit);
  };
  for (const input of inputs) {
    input.addEventListener('input', () => { recompute(); show(input); });
  }
  recompute();
  inputs.forEach(show);
}

// ---------- разметка ----------

function cleanNode(node: Element): void {
  const kept = [...node.classList].filter((name) => CLASSES.has(name));
  if (kept.length) node.setAttribute('class', kept.join(' '));
  else node.removeAttribute('class');
  const tone = node.getAttribute('data-tone');
  if (tone !== null && !TONES.has(tone)) node.removeAttribute('data-tone');
  const name = node.getAttribute('data-var');
  if (name !== null && !NAME.test(name)) node.removeAttribute('data-var');
}

function cleanInput(input: HTMLInputElement): boolean {
  if (!['range', 'number'].includes(input.type) || !input.dataset.var) return false;
  input.removeAttribute('name');
  const min = number(input.getAttribute('min'), 0);
  const max = number(input.getAttribute('max'), 100);
  if (!(max > min)) return false;
  input.min = String(min);
  input.max = String(max);
  input.step = String(Math.abs(number(input.getAttribute('step'), (max - min) / 100)) || 1);
  input.value = String(Math.min(max, Math.max(min, number(input.getAttribute('value'), min))));
  input.setAttribute('aria-label', input.closest('label')?.textContent?.trim().slice(0, 80) || input.dataset.var);
  return true;
}

/** Картинки: вложения проекта как есть, адреса https через защищённый прокси приложения; прочее убирается. */
function cleanImage(image: HTMLImageElement): boolean {
  const src = image.getAttribute('src') ?? '';
  if (/^\/api\/attachments\/\d+\/content$/.test(src)) return true;
  try {
    const url = new URL(src);
    if (url.protocol !== 'https:' || url.username || url.password) return false;
    image.src = richMediaURL(url.href);
  } catch {
    return false;
  }
  image.loading = 'lazy';
  image.decoding = 'async';
  image.referrerPolicy = 'no-referrer';
  image.alt = image.alt.slice(0, 200);
  return true;
}

/** Полоса диаграммы: доля из data-value и data-max, ширина только через свою переменную, без стилей модели. */
function sizeBar(bar: HTMLElement): void {
  const value = number(bar.dataset.value ?? null, NaN);
  const max = number(bar.dataset.max ?? null, NaN);
  if (!Number.isFinite(value)) return;
  const part = Number.isFinite(max) && max > 0 ? value / max : value / 100;
  bar.style.setProperty('--dv-part', String(Math.max(0, Math.min(1, part))));
}

/** Блок из разметки модели. null - разметка пустая или слишком большая: тогда остаётся обычный код. */
export function renderView(source: string): HTMLElement | null {
  if (!source.trim() || source.length > MAX_SOURCE) return null;
  const clean = DOMPurify.sanitize(source, {
    ALLOWED_TAGS: TAGS,
    ALLOWED_ATTR: ATTRS,
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
  });
  // разбор в отдельном документе без окна: там картинка не грузится, пока адрес не проверен и не переписан.
  // фрагмент DOMPurify сразу принадлежит странице, и браузер успел бы запросить сырой src
  const inert = new DOMParser().parseFromString(`<body>${clean}</body>`, 'text/html').body;
  let images = 0;
  for (const image of inert.querySelectorAll('img')) if (images >= MAX_IMAGES || !cleanImage(image)) image.remove(); else images += 1;
  const root = document.createElement('div');
  root.className = 'dv';
  root.append(...[...inert.childNodes].map((node) => document.importNode(node, true)));
  for (const node of root.querySelectorAll('*')) cleanNode(node);
  for (const input of root.querySelectorAll('input')) if (!cleanInput(input)) input.remove();
  for (const bar of root.querySelectorAll<HTMLElement>('.dv-bar')) sizeBar(bar);
  if (!root.textContent?.trim() && !root.querySelector('img')) return null;
  if (root.querySelector('output[data-expr]')) wireCalc(root);
  return root;
}

/** Пока блок пишется, вместо сырой разметки - заглушка; закрытый блок уже собирается целиком. */
export function viewPrepare(source: string, streaming: boolean): string {
  if (!streaming) return source;
  // открытый забор - последний ```-строка с нечётным счётом; заглушка только если это наш блок
  const fences = [...source.matchAll(/^ {0,3}```([^\n`]*)$/gm)];
  if (fences.length % 2 === 0) return source;
  const open = fences[fences.length - 1]!;
  if (open[1]!.trim() !== VIEW_LANGUAGE) return source;
  return `${source.slice(0, open.index)}\`\`\`${VIEW_PENDING}\n\`\`\``;
}
