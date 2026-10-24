/* Шрифт кода: «как в системе» или один из известных моноширинных, установленных на этом компьютере. */

export const SYSTEM_CODE_FONT = 'system';

/** Стек «как в системе»: SF Mono / Menlo на Mac, Cascadia / Consolas на Windows. */
export const SYSTEM_STACK = 'ui-monospace, "SF Mono", Menlo, Monaco, "Cascadia Mono", Consolas, "Liberation Mono", monospace';

const KNOWN = [
  'SF Mono', 'Menlo', 'Monaco', 'JetBrains Mono', 'Fira Code', 'Fira Mono', 'Cascadia Code', 'Cascadia Mono', 'Consolas',
  'Source Code Pro', 'IBM Plex Mono', 'Roboto Mono', 'Ubuntu Mono', 'Hack', 'Inconsolata', 'Iosevka', 'Victor Mono',
  'Geist Mono', 'Monaspace Neon', 'DejaVu Sans Mono', 'Lucida Console', 'Courier New',
];

/** Имя из настроек в CSS: кавычки и прочее лишнее вырезается, запасные шрифты остаются. */
export function codeFontStack(name: string): string {
  const clean = name.replace(/[^A-Za-z0-9 ._,-]/g, '').trim();
  if (!clean || clean === SYSTEM_CODE_FONT || clean === 'monospace') return SYSTEM_STACK;
  return `"${clean}", ${SYSTEM_STACK}`;
}

let cached: string[] | null = null;

/** Какие из известных шрифтов есть в системе: ширина строки меняется, только если шрифт реально подставился. */
export function installedCodeFonts(): string[] {
  if (cached) return cached;
  const context = typeof document !== 'undefined' ? document.createElement('canvas').getContext('2d') : null;
  if (!context) return (cached = []);
  const sample = 'mmmmmmmmmmlli10OO@#{}';
  const width = (font: string) => {
    context.font = `72px ${font}`;
    return context.measureText(sample).width;
  };
  const bases = ['monospace', 'serif', 'sans-serif'].map((base) => [base, width(base)] as const);
  cached = KNOWN.filter((name) => bases.some(([base, size]) => width(`"${name}", ${base}`) !== size));
  return cached;
}
