import { useData, saveSettings } from '../../state/data';
import type { AppSettings } from '../../lib/types';
import { Pick, Row, Section, Segmented, TextRow, Toggle, type Say } from './parts';
import { glassActive, glassCapable, setGlassEnabled } from '../../lib/glass';
import { isMac, osName } from '../../lib/platform';

export const saveKey = <K extends keyof AppSettings>(key: K, convert: (value: never) => AppSettings[K] = (value) => value as AppSettings[K]) =>
  (value: unknown) => saveSettings({ [key]: convert(value as never) } as Partial<AppSettings>);

const CODE_SAMPLE = `function greet(name: string) {
  return \`Привет, \${name}!\`; // 0O 1lI
}`;

export function GeneralPage({ say }: { say: Say }) {
  const settings = useData((s) => s.settings);
  return (
    <>
      <Section title="Внешний вид">
        <Segmented title="Оформление" detail={`«Как в системе» следует настройке ${osName()}.`} options={[['system', 'Как в системе'], ['dark', 'Тёмное'], ['light', 'Светлое']]} value={settings.theme ?? 'dark'} save={saveKey('theme')} say={say} />
        {glassCapable() && <Toggle
          title="Liquid Glass"
          detail="Нативное стекло macOS 26 для меню и панелей."
          checked={glassActive()}
          save={async (value) => setGlassEnabled(value)}
          say={say}
        />}
        <Pick title="Поверхность" detail="Цвет фона: как раньше, темнее чат или темнее боковое меню." options={[['classic', 'Как раньше'], ['chat-dark', 'Тёмный чат'], ['side-dark', 'Тёмное меню']]} value={settings.ui_surface} save={saveKey('ui_surface')} say={say} />
        <Segmented title="Шрифт интерфейса" detail="Для меню, панелей и сообщений." options={[['inter', 'Inter'], ['system', 'Как в системе']]} value={settings.ui_font ?? 'inter'} save={saveKey('ui_font')} say={say} />
        <Pick title="Размер текста" options={[8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 20].map((size) => [size, `${size} px`] as [number, string])} value={settings.text_size} save={saveKey('text_size', (value: string) => Number(value))} say={say} />
        <Segmented title="Ширина чата" options={[['compact', 'Узкая'], ['standard', 'Обычная'], ['wide', 'Широкая']]} value={settings.chat_width} save={saveKey('chat_width')} say={say} />
        <Pick title="Анимации" detail="Учитывается системная настройка уменьшения движения." options={[['system', 'Как в системе'], ['full', 'Включены'], ['reduced', 'Минимальные']]} value={settings.ui_motion} save={saveKey('ui_motion')} say={say} />
      </Section>
      <Section title="Код">
        <TextRow
          title="Шрифт кода"
          detail={`Моноширинный шрифт для кода и терминала. Название должно совпадать с установленным в системе.`}
          value={settings.code_font}
          placeholder="напр. JetBrains Mono"
          save={saveKey('code_font')}
          say={say}
          width={220}
        />
        <Row title="Предпросмотр" stack>
          <pre className="pref-preview" aria-label="Образец шрифта кода"><code>{CODE_SAMPLE}</code></pre>
        </Row>
      </Section>
      <Section title="Ввод">
        <Toggle title="Отправка по Enter" detail={`Shift+Enter добавляет строку. При отключении сообщение отправляется по ${isMac() ? "⌘Enter" : "Ctrl+Enter"}.`} checked={settings.enter_sends !== false} save={saveKey('enter_sends')} say={say} />
      </Section>
    </>
  );
}
