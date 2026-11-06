import { afterEach, describe, expect, it } from 'vitest';
import { evaluate, parseExpression, renderView, viewPrepare } from './dokkyView';
import { markdownHooks, renderMarkdown } from './markdown';

const calc = (source: string, scope: Record<string, number> = {}) => evaluate(parseExpression(source), scope);

describe('выражения расчёта', () => {
  it('считает арифметику, степень, сравнения и функции', () => {
    expect(calc('2 + 3 * 4')).toBe(14);
    expect(calc('(2 + 3) * 4')).toBe(20);
    expect(calc('2 ^ 3 ^ 2')).toBe(512);
    expect(calc('-2 ^ 2')).toBe(-4);
    expect(calc('10 % 4')).toBe(2);
    expect(calc('1.5 * 2')).toBe(3);
    expect(calc('.5 * 2')).toBe(1);
    // запятая без пробела - разделитель аргументов, а не дробь
    expect(calc('max(1,2)')).toBe(2);
    expect(calc('clamp(v,0,100)', { v: 150 })).toBe(100);
    expect(calc('round(-0.2)')).toBe(-0);
    expect(calc('if(x > 5, 1, 0)', { x: 7 })).toBe(1);
    expect(calc('round(pmt(rate / 12, years * 12, sum))', { rate: 0.12, years: 1, sum: 120000 })).toBe(10662);
    expect(calc('round(fv(0, 12, 1000, 500))')).toBe(12500);
  });

  it('не знает чужих имён и не исполняет код', () => {
    expect(Number.isNaN(calc('missing + 1'))).toBe(true);
    expect(() => parseExpression('alert(1)')).toThrow();
    expect(() => parseExpression('constructor(1)')).toThrow();
    expect(() => parseExpression('x.constructor')).toThrow();
    expect(() => parseExpression('1 +')).toThrow();
    expect(() => parseExpression('1'.repeat(400))).toThrow();
    // имя из прототипа объекта не становится значением
    expect(Number.isNaN(calc('toString', {}))).toBe(true);
  });
});

describe('разметка блока', () => {
  it('оставляет только детали конструктора', () => {
    const view = renderView(`
      <div class="dv-card evil" data-tone="accent" style="position:fixed" onclick="alert(1)">
        <h3 class="dv-title">Итог</h3>
        <a href="https://evil.example">ссылка</a>
        <script>alert(1)</script>
        <form><button>Отправить</button></form>
        <iframe src="https://evil.example"></iframe>
        <p class="dv-muted" data-tone="purple">текст</p>
      </div>`)!;
    const card = view.querySelector('div')!;
    expect(view.className).toBe('dv');
    expect(card.className).toBe('dv-card');
    expect(card.getAttribute('style')).toBeNull();
    expect(card.getAttribute('onclick')).toBeNull();
    expect(view.querySelector('a, script, form, button, iframe')).toBeNull();
    expect(view.querySelector('p')!.hasAttribute('data-tone')).toBe(false);
    expect(view.textContent).toContain('ссылка');
  });

  it('картинки только https через прокси или вложения проекта', () => {
    const view = renderView(`
      <img src="https://img.example/a.jpg" alt="фото">
      <img src="http://img.example/b.jpg">
      <img src="javascript:alert(1)">
      <img src="/api/attachments/7/content">
      <img src="/api/settings">`)!;
    const sources = [...view.querySelectorAll('img')].map((image) => image.getAttribute('src'));
    expect(sources).toEqual([`/api/dokky/media?url=${encodeURIComponent('https://img.example/a.jpg')}`, '/api/attachments/7/content']);
  });

  it('ползунок пересчитывает итог, полоса получает долю', () => {
    const view = renderView(`
      <div class="dv-calc">
        <label class="dv-field">Сумма <span class="dv-value"></span><input type="range" data-var="sum" min="0" max="1000" step="10" value="200"></label>
        <label class="dv-field">Чужое <input type="text" data-var="x"></label>
        <div class="dv-result"><output data-expr="sum * 2" data-digits="0" data-unit="₽"></output></div>
      </div>
      <div class="dv-bars"><div class="dv-bar" data-value="30" data-max="60"><span>A</span><span>30</span></div></div>`)!;
    const output = view.querySelector('output')!;
    expect(output.textContent).toBe('400 ₽');
    expect(view.querySelector('input[type="text"]')).toBeNull();
    const range = view.querySelector<HTMLInputElement>('input[type="range"]')!;
    range.value = '300';
    range.dispatchEvent(new Event('input'));
    expect(output.textContent).toBe('600 ₽');
    expect(view.querySelector('.dv-value')!.textContent).toBe('300');
    expect(view.querySelector<HTMLElement>('.dv-bar')!.style.getPropertyValue('--dv-part')).toBe('0.5');
  });

  it('ползунок без max и step получает разумные границы, картинок не больше шести', () => {
    const view = renderView(`
      <div class="dv-calc"><label class="dv-field">Доля <span class="dv-value"></span><input type="range" data-var="x" min="1" value="5"></label>
      <div class="dv-result"><output data-expr="x - 5" data-digits="0"></output></div></div>
      ${'<img src="https://img.example/a.jpg">'.repeat(9)}`)!;
    const range = view.querySelector<HTMLInputElement>('input')!;
    expect([range.min, range.max, range.step]).toEqual(['1', '100', '0.99']);
    expect(view.querySelector('output')!.textContent).toBe('0');
    expect(view.querySelectorAll('img')).toHaveLength(6);
  });

  it('пустая или огромная разметка остаётся кодом', () => {
    expect(renderView('   ')).toBeNull();
    expect(renderView('<script>alert(1)</script>')).toBeNull();
    expect(renderView('<p>' + 'я'.repeat(70_000) + '</p>')).toBeNull();
  });
});

describe('в ответе', () => {
  afterEach(() => { delete markdownHooks.viewsEnabled; });

  it('недописанный блок не показывается сырым', () => {
    const open = 'Смотрите:\n\n```dokky-view\n<div class="dv-card">';
    expect(viewPrepare(open, true)).toBe('Смотрите:\n\n```dokky-view-pending\n```');
    expect(viewPrepare(open, false)).toBe(open);
    const done = `${open}\n</div>\n\`\`\`\nГотово`;
    expect(viewPrepare(done, true)).toBe(done);
    // слово внутри другого блока кода не прячет ответ
    const doc = 'Пример:\n\n```md\n```dokky-view\n';
    expect(viewPrepare(doc, true)).toBe(doc);
  });

  it('в режиме кода блок собирается, вне его остаётся кодом', () => {
    const source = 'Итог:\n\n```dokky-view\n<div class="dv-stats"><div class="dv-stat"><strong>42</strong><span>теста</span></div></div>\n```';
    const target = document.createElement('div');
    renderMarkdown(target, source, false);
    expect(target.querySelector('.dv')).toBeNull();
    expect(target.querySelector('pre code')?.textContent).toContain('dv-stats');
    markdownHooks.viewsEnabled = () => true;
    renderMarkdown(target, source, false);
    expect(target.querySelector('.dv .dv-stat strong')?.textContent).toBe('42');
    expect(target.querySelector('pre')).toBeNull();
    renderMarkdown(target, 'Итог:\n\n```dokky-view\n<div class="dv-card">', false, { streaming: true });
    expect(target.querySelector('.dv-pending')?.textContent).toBe('Собираю наглядный блок…');
  });
});
