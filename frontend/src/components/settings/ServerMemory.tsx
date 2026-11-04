import { ChevronRight, Download } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api, cloudPath, del, post, put } from '../../lib/api';
import { confirmDialog } from '../../state/overlay';
import { FillTrash } from '../ui/DokkyIcons';
import { Action, Row, Section, Toggle, type Say } from './parts';

type MemoryData = Partial<Record<'facts' | 'preferences' | 'goals' | 'constraints' | 'frequent_topics', unknown>>;
interface ImportItem { category: 'facts' | 'preferences' | 'goals' | 'constraints'; text: string }
interface ImportResult { items?: ImportItem[]; duplicates?: number; rejected?: number; over_limit?: number }

const GROUPS: Array<[keyof MemoryData, string]> = [['facts', 'О вас'], ['preferences', 'Как вам отвечать'], ['goals', 'Ваши цели'], ['constraints', 'Что важно учитывать']];
const LABELS: Record<ImportItem['category'], string> = { facts: 'Факт', preferences: 'Предпочтение', goals: 'Цель', constraints: 'Ограничение' };
const MAX_TEXT = 30000;
const PROMPT = `Собери всё, что ты помнишь обо мне из сохранённой памяти и наших прошлых разговоров, чтобы я мог перенести это в другого ассистента.

Перечисли каждый пункт отдельной строкой:
- кто я: имя, чем занимаюсь, где живу, языки;
- работа и проекты, их текущее состояние;
- устройства и сервисы, которыми пользуюсь;
- как мне удобно получать ответы: тон, длина, формат;
- мои долгосрочные цели и ограничения.

Пиши только то, что знаешь точно, без догадок и пояснений. Не указывай пароли, ключи доступа, номера документов и банковских карт.`;

const list = (memory: MemoryData, key: keyof MemoryData): string[] => {
  const value = memory[key];
  return Array.isArray(value) ? value.map(String).filter((item) => item.trim()) : [];
};
// считаем только то, что показываем: иначе «Очистить» могла бы стоять над пустым списком
const countOf = (memory: MemoryData) => GROUPS.reduce((sum, [key]) => sum + list(memory, key).length, 0);
export function recordsWord(count: number): string {
  const tens = count % 100, ones = count % 10;
  if (tens >= 11 && tens <= 14) return 'записей';
  if (ones === 1) return 'запись';
  return ones >= 2 && ones <= 4 ? 'записи' : 'записей';
}

function skippedText(result: ImportResult): string {
  const parts = [
    result.duplicates ? `уже в памяти: ${result.duplicates}` : '',
    result.rejected ? `отсеяно как лишнее или небезопасное: ${result.rejected}` : '',
    result.over_limit ? `не поместилось: ${result.over_limit}` : '',
  ].filter(Boolean);
  return parts.length ? `${parts.join(', ')}.`.replace(/^./, (letter) => letter.toUpperCase()) : '';
}

/** Перенос из другой нейросети: промпт, ответ, проверка найденного. До подтверждения ничего не сохраняется. */
function MemoryImport({ done, say }: { done: (memory: MemoryData) => void; say: Say }) {
  const [text, setText] = useState('');
  const [review, setReview] = useState<ImportResult | null>(null);
  const [picked, setPicked] = useState<boolean[]>([]);
  const [busy, setBusy] = useState(false);
  const items = review?.items ?? [];
  const run = (action: () => Promise<void>) => {
    setBusy(true);
    action().catch((error: Error) => say(error.message)).finally(() => setBusy(false));
  };
  const parse = () => run(async () => {
    const result = await post<ImportResult>(cloudPath('memory/import/parse'), { text });
    setReview(result);
    setPicked((result.items ?? []).map(() => true));
  });
  const apply = () => run(async () => {
    const chosen = items.filter((_, index) => picked[index]);
    const result = await post<{ added?: number; memory?: MemoryData }>(cloudPath('memory/import/apply'), { items: chosen });
    say(`Добавлено: ${result.added ?? chosen.length}`);
    done(result.memory ?? (await api<{ memory: MemoryData }>(cloudPath('memory'))).memory);
  });
  if (review) {
    return (
      <div className="memory-import">
        {items.length === 0 ? <p className="memory-empty">Новых записей не нашлось.</p> : (
          <ul className="memory-import-list" aria-label="Что будет добавлено">
            {items.map((item, index) => (
              <li key={`${item.category}-${index}`}>
                <label>
                  <input type="checkbox" checked={Boolean(picked[index])} onChange={(event) => setPicked(picked.map((value, at) => (at === index ? event.target.checked : value)))} />
                  <span>{item.text}<small>{LABELS[item.category] ?? item.category}</small></span>
                </label>
              </li>
            ))}
          </ul>
        )}
        {skippedText(review) && <p className="pref-hint">{skippedText(review)}</p>}
        <div className="memory-add">
          <button type="button" className="pref-button" disabled={busy} onClick={() => setReview(null)}>Назад</button>
          {items.length > 0 && <button type="button" className="pref-button primary" disabled={busy || !picked.some(Boolean)} onClick={apply}>Добавить в память</button>}
        </div>
      </div>
    );
  }
  return (
    <div className="memory-import">
      <p className="pref-hint"><b>1. Скопируйте промпт.</b> Вставьте его в ChatGPT или другую нейросеть, где уже есть память о вас.</p>
      <textarea className="pref-textarea" readOnly rows={4} value={PROMPT} aria-label="Промпт для другой нейросети" />
      <div className="memory-add">
        <button type="button" className="pref-button" onClick={() => { void navigator.clipboard.writeText(PROMPT).then(() => say('Промпт скопирован'), () => say('Не удалось скопировать')); }}>Скопировать промпт</button>
      </div>
      <p className="pref-hint"><b>2. Вставьте ответ.</b> Докки разобьёт его на отдельные записи. Пароли и ключи отсеиваются, до вашего подтверждения ничего не сохраняется.</p>
      <textarea className="pref-textarea" rows={6} maxLength={MAX_TEXT} value={text} disabled={busy} placeholder="Ответ нейросети" aria-label="Ответ нейросети" onChange={(event) => setText(event.target.value)} />
      <div className="pref-block-bar">
        <span className="pref-count">{text.length.toLocaleString('ru-RU')} из {MAX_TEXT.toLocaleString('ru-RU')}</span>
        <button type="button" className="pref-button primary" disabled={busy || !text.trim()} onClick={parse}>Разобрать</button>
      </div>
    </div>
  );
}

/** Память Докки на сервере. На сайте старой версии есть только переключатель: остальное просто не покажем. */
export function ServerMemory({ enabled, saveEnabled, say }: { enabled: boolean; saveEnabled: (value: boolean) => Promise<unknown>; say: Say }) {
  const [memory, setMemory] = useState<MemoryData | null>(null);
  const [importing, setImporting] = useState(false);
  useEffect(() => {
    let alive = true;
    api<{ memory: MemoryData }>(cloudPath('memory')).then((value) => { if (alive) setMemory(value.memory ?? {}); }).catch(() => undefined);
    return () => { alive = false; };
  }, []);
  const count = memory ? countOf(memory) : 0;
  const clear = async () => {
    if (!(await confirmDialog('Очистить память Докки?', `Удалится ${count} ${recordsWord(count)}. Это нельзя отменить.`, 'Очистить'))) return;
    try {
      await del(cloudPath('memory'));
      setMemory({});
      say('Память очищена');
    } catch (error) {
      say((error as Error).message);
    }
  };
  return (
    <Section title="Память">
      {memory && (
        <div className="memory-sum">
          <small>Докки помнит</small>
          <span><b>{count}</b> {recordsWord(count)} о вас</span>
        </div>
      )}
      <Toggle title="Запоминать новое" detail="Уже сохранённое останется, пока вы его не очистите." checked={enabled} save={saveEnabled} say={say} />
      {memory && (
        <>
          <Row title="Перенести из другой нейросети" detail="ChatGPT, Gemini и другие.">
            <button type="button" className="pref-button" aria-expanded={importing} onClick={() => setImporting(!importing)}><Download aria-hidden="true" /><span>{importing ? 'Свернуть' : 'Перенести'}</span><ChevronRight className="pref-chevron" data-open={importing || undefined} aria-hidden="true" /></button>
          </Row>
          {importing && <MemoryImport say={say} done={(next) => { setMemory(next); setImporting(false); }} />}
          {count > 0 && GROUPS.map(([key, title]) => {
            const items = list(memory, key);
            return items.length > 0 && (
              <Row key={key} title={title} stack>
                <ul className="memory-list" aria-label={title}>{items.map((item, index) => <li key={`${index}-${item}`}><span className="memory-text">{item}</span></li>)}</ul>
              </Row>
            );
          })}
          {count > 0 && <Action title="Очистить память" detail={`Удалит ${count} ${recordsWord(count)}.`} label="Очистить" danger icon={<FillTrash aria-hidden="true" />} onClick={() => { void clear(); }} />}
        </>
      )}
    </Section>
  );
}

/** Чаты в Telegram: переключатель появляется, только если сайт отвечает на запрос. */
export function TelegramSync({ say }: { say: Say }) {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  useEffect(() => {
    let alive = true;
    api<{ enabled: boolean }>(cloudPath('sync')).then((value) => { if (alive) setEnabled(value.enabled === true); }).catch(() => undefined);
    return () => { alive = false; };
  }, []);
  if (enabled === null) return null;
  return (
    <Section title="Чаты">
      <Toggle title="Чаты в Telegram" detail="Синхронизация истории общения с Докки в Telegram." checked={enabled} save={async (value) => { await put(cloudPath('sync'), { enabled: value }); setEnabled(value); }} say={say} />
    </Section>
  );
}
