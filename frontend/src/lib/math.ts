/* Формулы ответов Докки: KaTeX загружается при первой формуле, а не при запуске приложения. */
let loading: Promise<typeof import('katex').default> | null = null;

function load(): Promise<typeof import('katex').default> {
  loading ??= Promise.all([import('katex'), import('katex/dist/katex.min.css')]).then(([module]) => module.default);
  return loading;
}

/** Заменяет текст формул (`.rich-math`, `.rich-math-inline`) готовой вёрсткой; ошибка в формуле оставляет её исходный текст. */
export async function renderMath(root: ParentNode): Promise<void> {
  const nodes = [...root.querySelectorAll<HTMLElement>('.rich-math:not([data-math]), .rich-math-inline:not([data-math])')];
  if (!nodes.length) return;
  for (const node of nodes) node.dataset.math = 'pending';
  let katex: Awaited<ReturnType<typeof load>>;
  try {
    katex = await load();
  } catch {
    for (const node of nodes) node.dataset.math = 'failed';
    return;
  }
  for (const node of nodes) {
    if (!node.isConnected) continue;
    const formula = node.textContent ?? '';
    try {
      katex.render(formula, node, {
        displayMode: node.classList.contains('rich-math'),
        throwOnError: true,
        trust: false,
        strict: 'ignore',
        maxExpand: 1000,
        maxSize: 10,
        output: 'htmlAndMathml',
      });
      node.dataset.math = 'done';
    } catch {
      node.textContent = formula;
      node.dataset.math = 'failed';
    }
  }
}
