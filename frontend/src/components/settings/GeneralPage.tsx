import { useData, saveSettings } from '../../state/data';
import type { AppSettings } from '../../lib/types';
import hljs from 'highlight.js/lib/common';
import { Pick, Row, Section, Segmented, Toggle, type Say } from './parts';
import { glassActive, glassCapable, setGlassEnabled } from '../../lib/glass';
import { isMac, osName } from '../../lib/platform';
import { codeFontStack, installedCodeFonts, SYSTEM_CODE_FONT } from '../../lib/codeFonts';

export const saveKey = <K extends keyof AppSettings>(key: K, convert: (value: never) => AppSettings[K] = (value) => value as AppSettings[K]) =>
  (value: unknown) => saveSettings({ [key]: convert(value as never) } as Partial<AppSettings>);

const CODE_SAMPLE = `// 0O 1lI {} [] => !== ===
function greet(name: string): string {
  const count = 42;
  return \`Привет, \${name}! Задач: \${count}\`;
}`;

// образец постоянный, а highlight.js сам экранирует текст, поэтому вставлять его как HTML безопасно
const SAMPLE_HTML = hljs.highlight(CODE_SAMPLE, { language: 'typescript', ignoreIllegals: true }).value;

/** Системный вариант, найденные в системе шрифты и сохранённый ранее, даже если его уже удалили. */
function codeFontOptions(saved: string): Array<[string, string]> {
  const fonts = installedCodeFonts();
  const options: Array<[string, string]> = [[SYSTEM_CODE_FONT, 'Как в системе'], ...fonts.map((name) => [name, name] as [string, string])];
  if (saved && saved !== SYSTEM_CODE_FONT && !fonts.includes(saved)) options.push([saved, `${saved} (не найден)`]);
  return options;
}

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
        <Pick
          title="Шрифт кода"
          detail="Для блоков кода, изменений в файлах и терминала. В списке только шрифты, установленные на этом компьютере."
          options={codeFontOptions(settings.code_font)}
          value={settings.code_font || SYSTEM_CODE_FONT}
          optionStyle={(name) => ({ fontFamily: codeFontStack(name) })}
          save={saveKey('code_font')}
          say={say}
        />
        <Row title="Предпросмотр" stack>
          <pre className="pref-preview" aria-label="Образец шрифта кода"><code dangerouslySetInnerHTML={{ __html: SAMPLE_HTML }} /></pre>
        </Row>
      </Section>
      <Section title="Ввод">
        <Segmented
          title="Отправка сообщения"
          detail={settings.enter_sends !== false ? 'Shift+Enter переносит строку.' : 'Enter и Shift+Enter переносят строку.'}
          options={[['enter', 'Enter'], ['mod', isMac() ? '⌘Enter' : 'Ctrl+Enter']]}
          value={settings.enter_sends !== false ? 'enter' : 'mod'}
          save={saveKey('enter_sends', (value: string) => value === 'enter')}
          say={say}
        />
      </Section>
    </>
  );
}
