import { describe, expect, it } from 'vitest';
import { paletteResults, paletteScore, type PaletteItem } from './palette';

const items: PaletteItem[] = [
  { key: 'a', kind: 'Команда', title: 'Новый чат' },
  { key: 'b', kind: 'Чат', title: 'Чиним новый парсер' },
  { key: 'c', kind: 'Настройки', title: 'Общие', keywords: 'оформление тема тёмная' },
  { key: 'd', kind: 'Проект', title: 'dokky' },
];

describe('быстрый переход', () => {
  it('начало названия важнее середины, ключевые слова работают', () => {
    expect(paletteResults(items, 'нов').map((item) => item.key)).toEqual(['a', 'b']);
    expect(paletteResults(items, 'тема')[0]?.key).toBe('c');
    expect(paletteResults(items, 'dok чужое')).toEqual([]);
  });

  it('буквы вразбивку находят, но слабее точного совпадения', () => {
    expect(paletteScore(items[0] as PaletteItem, 'нвч')).toBe(0.5);
    expect(paletteScore(items[0] as PaletteItem, 'новый')).toBe(4);
  });

  it('пустой запрос: команды и последние чаты, без настроек', () => {
    expect(paletteResults(items, '  ').map((item) => item.kind)).toEqual(['Команда', 'Чат']);
  });
});
