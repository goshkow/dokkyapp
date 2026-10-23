import { describe, expect, it } from 'vitest';
import { DEFAULT_SCHEDULE, TEMPLATES, parseSchedule, scheduleString } from './automation';
import { groupSkills, type Skill } from '../state/skills';

describe('расписание автоматизации', () => {
  it('форма и строка переводятся друг в друга без потерь', () => {
    for (const value of ['manual', 'hourly', 'interval@90', 'daily@09:30', 'weekdays@08:00', 'weekly@5@17:00']) {
      expect(scheduleString(parseSchedule(value))).toBe(value);
    }
  });

  it('неверные значения формы заменяются безопасными', () => {
    expect(scheduleString({ ...DEFAULT_SCHEDULE, when: 'interval', minutes: '3' })).toBe('interval@15');
    expect(scheduleString({ ...DEFAULT_SCHEDULE, when: 'interval', minutes: '99999' })).toBe('interval@1440');
    expect(scheduleString({ ...DEFAULT_SCHEDULE, when: 'daily', time: '25:99' })).toBe('daily@09:00');
    expect(scheduleString({ ...DEFAULT_SCHEDULE, when: 'weekly', day: '9', time: '10:00' })).toBe('weekly@7@10:00');
    expect(parseSchedule('что-то странное')).toEqual(DEFAULT_SCHEDULE);
  });

  it('все заготовки дают допустимое расписание', () => {
    for (const template of TEMPLATES) expect(scheduleString(template.schedule)).toMatch(/^(manual|hourly|interval@\d+|daily@\d\d:\d\d|weekdays@\d\d:\d\d|weekly@[1-7]@\d\d:\d\d)$/);
  });
});

const skill = (id: number, name: string, category: string, description = ''): Skill => ({ id, name, category, description, builtin: category !== 'Мои навыки', workflow: {} });

describe('список навыков', () => {
  const items = [skill(1, 'Ревью кода', 'Код', 'Находит ошибки'), skill(2, 'Аудит безопасности', 'Безопасность'), skill(3, 'Мой навык', 'Мои навыки'), skill(4, 'Тесты', 'Код')];

  it('группирует по категориям, свои навыки первыми', () => {
    expect(groupSkills(items, '').map(([name]) => name)).toEqual(['Мои навыки', 'Безопасность', 'Код']);
    expect(groupSkills(items, '')[2]?.[1].map((item) => item.id)).toEqual([1, 4]);
  });

  it('ищет по названию, описанию и категории', () => {
    expect(groupSkills(items, 'ошибки').flatMap(([, list]) => list.map((item) => item.id))).toEqual([1]);
    expect(groupSkills(items, 'безопасн').flatMap(([, list]) => list.map((item) => item.id))).toEqual([2]);
    expect(groupSkills(items, 'ничего такого')).toEqual([]);
  });
});
