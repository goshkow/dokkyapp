/* Расписание автоматизаций в виде формы и обратно. Строки совпадают с backend/koddi/automations/schedule.py. */

export type When = 'manual' | 'hourly' | 'interval' | 'daily' | 'weekdays' | 'weekly';

export interface ScheduleForm {
  when: When;
  time: string;
  day: string;
  minutes: string;
}

export const DEFAULT_SCHEDULE: ScheduleForm = { when: 'manual', time: '09:00', day: '1', minutes: '60' };

export const WHEN_LABELS: Array<[When, string]> = [
  ['manual', 'Только вручную'],
  ['hourly', 'Каждый час'],
  ['interval', 'Каждые N минут'],
  ['daily', 'Каждый день'],
  ['weekdays', 'По будням'],
  ['weekly', 'Раз в неделю'],
];

export const DAY_LABELS = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];

export function scheduleString(form: ScheduleForm): string {
  const time = /^([01]\d|2[0-3]):[0-5]\d$/.test(form.time) ? form.time : '09:00';
  switch (form.when) {
    case 'hourly': return 'hourly';
    case 'interval': return `interval@${Math.max(15, Math.min(1440, Math.round(Number(form.minutes)) || 60))}`;
    case 'daily': return `daily@${time}`;
    case 'weekdays': return `weekdays@${time}`;
    case 'weekly': return `weekly@${Math.max(1, Math.min(7, Number(form.day) || 1))}@${time}`;
    default: return 'manual';
  }
}

export function parseSchedule(value: string): ScheduleForm {
  const [kind, first, second] = String(value).split('@');
  if (kind === 'hourly') return { ...DEFAULT_SCHEDULE, when: 'hourly' };
  if (kind === 'interval') return { ...DEFAULT_SCHEDULE, when: 'interval', minutes: first || '60' };
  if (kind === 'daily' || kind === 'weekdays') return { ...DEFAULT_SCHEDULE, when: kind, time: first || '09:00' };
  if (kind === 'weekly') return { ...DEFAULT_SCHEDULE, when: 'weekly', day: first || '1', time: second || '09:00' };
  return DEFAULT_SCHEDULE;
}

export type Kind = 'agent' | 'command' | 'tool';

export interface Template {
  id: string;
  title: string;
  detail: string;
  kind: Kind;
  text: string;
  tool?: string;
  schedule: ScheduleForm;
}

/** Готовые заготовки: человек выбирает проект, проверяет текст и сохраняет. */
export const TEMPLATES: Template[] = [
  {
    id: 'morning', title: 'Утренний обзор проекта', kind: 'agent', schedule: { ...DEFAULT_SCHEDULE, when: 'weekdays', time: '09:00' },
    detail: 'По будням агент смотрит последние изменения и пишет, что нового и что выглядит рискованным.',
    text: 'Изучи последние изменения проекта за сутки (git.log и git.diff). Кратко опиши: что изменилось, что выглядит рискованным, какие тесты стоит прогнать. Ничего не меняй и не коммить.',
  },
  {
    id: 'tests', title: 'Ежедневный запуск тестов', kind: 'command', schedule: { ...DEFAULT_SCHEDULE, when: 'daily', time: '12:00' },
    detail: 'Раз в день запускает команду тестов проекта и показывает, прошли ли они.',
    text: 'pytest -q',
  },
  {
    id: 'health', title: 'Проверка состояния проекта', kind: 'tool', tool: 'diagnostics.collect', schedule: { ...DEFAULT_SCHEDULE, when: 'daily', time: '10:00' },
    detail: 'Собирает диагностику: версии инструментов, состояние git, зависимости.',
    text: '',
  },
  {
    id: 'report', title: 'Отчёт за неделю', kind: 'agent', schedule: { ...DEFAULT_SCHEDULE, when: 'weekly', day: '5', time: '17:00' },
    detail: 'В пятницу вечером агент собирает итоги недели по истории изменений.',
    text: 'Подготовь отчёт за прошедшую неделю по истории git: что сделано, что осталось открытым, куда стоит обратить внимание на следующей неделе. Сохрани его в файл reports/неделя.md внутри проекта, если такая папка существует, иначе только покажи текстом.',
  },
];

/** Подсказки для описания автоматизации словами при создании с ИИ. */
export const AI_EXAMPLES = [
  'Каждое утро по будням проверять, что тесты проходят, и писать, что сломалось',
  'Раз в неделю искать устаревшие зависимости и предлагать обновления',
  'Каждый вечер собирать список изменений за день по git',
];
