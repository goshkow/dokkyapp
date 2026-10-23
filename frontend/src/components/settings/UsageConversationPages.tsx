import { cloudPath, patch } from '../../lib/api';
import { setCloudPreferences } from '../../state/cloudPrefs';
import { useData } from '../../state/data';
import { Pick, Remote, Section, Toggle, type Say } from './parts';

interface Preferences { detail?: string; sources?: string; memory_enabled?: boolean; default_model?: string }

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
              <Pick title="Полнота ответов" detail="Кратко, средне или подробно." options={[['brief', 'Кратко'], ['standard', 'Средне'], ['full', 'Подробно']]} value={value.detail || 'standard'} save={(detail) => savePref({ detail })} say={say} />
              <Pick title="Указание источников" detail="Где показывать ссылки на материалы." options={[['both', 'В тексте и в конце'], ['inline', 'Только в тексте'], ['end', 'Только в конце'], ['none', 'Скрыть']]} value={value.sources || 'both'} save={(sources) => savePref({ sources })} say={say} />
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
            <Section title="Память">
              <Toggle title="Память Докки" detail="Докки учитывает сохранённые предпочтения и полезные сведения в других чатах. Сведения хранятся на сервере аккаунта." checked={Boolean(value.memory_enabled)} save={(memory_enabled) => savePref({ memory_enabled })} say={say} />
            </Section>
          </>
        );
      }}
    </Remote>
  );
}
