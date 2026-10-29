import { describe, expect, it } from 'vitest';
import { formatPolicy, parsePolicy } from './QualitySettings';

describe('политика слоёв', () => {
  it('разбирает слои от внутреннего к внешнему и пути вне проверки', () => {
    const policy = parsePolicy('# комментарий\ndomain: src/domain/**\ninfra: src/infra/**, src/api/**\nignore: tests/**', null);
    expect(policy).toEqual({
      layers: [{ name: 'domain', paths: ['src/domain/**'] }, { name: 'infra', paths: ['src/infra/**', 'src/api/**'] }],
      ignore: ['tests/**'], allow_type_imports: false,
    });
    expect(parsePolicy(formatPolicy(policy), policy)).toEqual(policy);
  });

  it('пустое поле выключает проверку, один слой и строка без масок отклоняются', () => {
    expect(parsePolicy('  \n', null)).toBeNull();
    expect(() => parsePolicy('domain: src/**', null)).toThrow('два слоя');
    expect(() => parsePolicy('domain: src/**\ninfra:', null)).toThrow('Строка 2');
  });

  it('сохраняет настройку импорта типов из прежней политики', () => {
    expect(parsePolicy('a: x/**\nb: y/**', { layers: [], allow_type_imports: true })?.allow_type_imports).toBe(true);
  });
});
