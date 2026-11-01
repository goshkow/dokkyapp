import { describe, expect, it } from 'vitest';
import { matchRanges } from './chatSearch';

describe('подсветка поиска по чату', () => {
  it('находит без учёта регистра и не ломается на спецсимволах', () => {
    const node = document.createElement('div');
    node.innerHTML = '<p>Парсер дат: парсер (v2)</p><p>ПАРСЕР</p>';
    expect(matchRanges(node, 'парсер')).toHaveLength(3);
    expect(matchRanges(node, '(v2)').map((range) => range.toString())).toEqual(['(v2)']);
    expect(matchRanges(node, 'нет такого')).toEqual([]);
  });
});
