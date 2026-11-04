import { cloudPath, patch } from '../../lib/api';
import { setCloudPreferences } from '../../state/cloudPrefs';
import { useData } from '../../state/data';
import { OptionCards, Pick, Remote, Section, type Say } from './parts';
import { ServerMemory, TelegramSync } from './ServerMemory';

interface Preferences { detail?: string; sources?: string; layout?: string; memory_enabled?: boolean; default_model?: string }

const DETAIL_SAMPLES = {
  brief: 'Взрослому нужно 7-9 часов сна.',
  standard: 'Взрослому нужно 7-9 часов сна. Режим важнее длительности: ложитесь и вставайте в одно время.',
  full: 'Взрослому нужно 7-9 часов сна. Режим важнее длительности: ложитесь и вставайте в одно время, даже в выходные. За час до сна уберите яркий свет и телефон.',
};

function SourcesSample({ mode }: { mode: 'both' | 'inline' | 'end' | 'none' }) {
  const chips = mode === 'both' || mode === 'inline';
  return (
    <>
      <p>Взрослому нужно 7-9 часов сна{chips && <sup>1</sup>}. Режим важнее длительности{chips && <sup>2</sup>}.</p>
      {(mode === 'both' || mode === 'end') && <p className="pref-answer-sources"><b>Источники</b><br />1. Минздрав России<br />2. Всемирная организация здравоохранения</p>}
    </>
  );
}

export function ConversationPage({ say }: { say: Say }) {
  const models = useData((s) => s.account?.bootstrap?.models ?? []);
  const chatModels = models.filter((model) => (model.roles ?? []).includes('chat'));
  return (
    <Remote<Preferences> path={cloudPath('preferences')} retryKey="conversation">
      {(value) => {
        setCloudPreferences(value as Record<string, unknown>);
        const savePref = async (body: Preferences) => { const saved = await patch<Record<string, unknown>>(cloudPath('preferences'), body); setCloudPreferences(saved); };
        return (
          <>
            <Section title="Ответы">
              <OptionCards
                title="Полнота ответов"
                detail="Выберите удобную длину ответа. Докки учтёт её и в боте, и здесь."
                options={[['brief', 'Кратко', 'Главное без подробных пояснений'], ['standard', 'Средне', 'Умеренная длина и нужные детали'], ['full', 'Подробно', 'Развёрнутый ответ с объяснениями']]}
                value={(value.detail || 'standard') as 'brief' | 'standard' | 'full'}
                save={(detail) => savePref({ detail })}
                say={say}
                preview={(id) => <p>{DETAIL_SAMPLES[id]}</p>}
              />
              <OptionCards
                title="Указание источников"
                detail="Где Докки указывает подтверждающие материалы. Настройка общая с ботом."
                options={[['both', 'В тексте и в конце', 'Короткие ссылки в ответе и общий список ниже'], ['inline', 'Только в тексте', 'Ссылки прямо рядом с подтверждёнными фактами'], ['end', 'Только в конце', 'Отдельный список использованных материалов'], ['none', 'Не указывать', 'Ссылки остаются, если нужны для вашего запроса']]}
                value={(value.sources || 'both') as 'both' | 'inline' | 'end' | 'none'}
                save={(sources) => savePref({ sources })}
                say={say}
                preview={(id) => <SourcesSample mode={id} />}
              />
              {value.layout !== undefined && (
                <OptionCards
                  title="Вид ответов"
                  detail="В боте Telegram ответы всегда обычным текстом: живой интерфейс работает на сайте и в приложении."
                  options={[['adaptive', 'Адаптивный', 'Докки сам добавляет калькуляторы, карты и схемы, когда с ними понятнее'], ['simple', 'Простой', 'Обычный текст и картинки, без интерактивных блоков']]}
                  value={(value.layout === 'simple' ? 'simple' : 'adaptive') as 'adaptive' | 'simple'}
                  save={(layout) => savePref({ layout })}
                  say={say}
                  preview={(id) => (id === 'adaptive'
                    ? <p><b>Сколько накопится</b><br />Ползунки меняют взнос и срок, сумма пересчитывается сразу.</p>
                    : <p>Вклад 10 000 ₽ в месяц за 20 лет при 7% годовых даст около 5,2 млн ₽.</p>)}
                />
              )}
            </Section>
            {chatModels.length > 0 && (
              <Section title="Режим по умолчанию">
                <Pick
                  title="Режим для новых чатов"
                  detail="Автоматический подбирает режим сам; быстрый отвечает скорее; улучшенный берёт задачи сложнее (если входит в тариф)."
                  options={chatModels.map((model) => [model.id, model.name.replaceAll('Dokky', 'Докки')] as [string, string])}
                  value={value.default_model || chatModels[0]?.id || 'dokky-default'}
                  save={(default_model) => savePref({ default_model })}
                  say={say}
                />
              </Section>
            )}
            <ServerMemory enabled={value.memory_enabled !== false} saveEnabled={(memory_enabled) => savePref({ memory_enabled })} say={say} />
            <TelegramSync say={say} />
          </>
        );
      }}
    </Remote>
  );
}
