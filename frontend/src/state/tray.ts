import { announceTray, type TrayChat, type TrayPayload } from '../lib/bridge';
import type { Limits } from '../lib/types';
import { chatActivity } from './chatActivity';
import { data } from './data';
import { limitsStore } from './limits';
import { planName, plansStore, type Catalog } from './plans';
import { ui } from './ui';

const seconds = (value: string | null): number | null => {
  const at = value ? Date.parse(value) : NaN;
  return Number.isFinite(at) ? Math.round(at / 1000) : null;
};

/** Лимиты для меню значка в трее: те же проценты, что у плашки внизу меню; у Free проценты не показываются. */
export function trayPayload(limits: Limits | null, catalog: Catalog | null): TrayPayload {
  if (!limits) return { plan: null, window: null, total: null };
  const free = limits.plan === 'free';
  return {
    plan: planName(limits.plan, catalog) || null,
    window: !free && limits.windowRemaining != null
      ? { remaining: limits.windowRemaining, hours: limits.windowHours, resets_at: seconds(limits.windowResetsAt) }
      : null,
    total: !free && limits.remaining != null ? { remaining: limits.remaining, resets_at: seconds(limits.expiresAt) } : null,
  };
}

/** Чаты для меню трея, как отметки в списке чатов: ждёт ответа, работает, готово (последние); больше трёх меню прячет под «Ещё N». */
export function trayChats(
  activity: Record<string, 'working' | 'question'>,
  done: Record<string, true>,
  titles: Map<string, string>,
): TrayChat[] {
  const entry = (id: string, mark: TrayChat['mark']): TrayChat | null =>
    (/^[0-9]+$/.test(id) && titles.has(id) ? { id: Number(id), title: titles.get(id) ?? '', mark } : null);
  const busy = Object.entries(activity).map(([id, mark]) => entry(id, mark));
  // готовые идут в порядке отметки: последние - самые свежие
  const finished = Object.keys(done).filter((id) => !activity[id]).reverse().map((id) => entry(id, 'done'));
  const order = { question: 0, working: 1, done: 2 } as const;
  return [...busy, ...finished].filter((item): item is TrayChat => item !== null).sort((a, b) => order[a.mark] - order[b.mark]).slice(0, 12);
}

let sent = '';
function send(): void {
  const { activity, done } = chatActivity.get();
  const titles = new Map(data.get().localChats.map((chat) => [String(chat.id), chat.title || 'Новый чат']));
  const payload = JSON.stringify({
    ...trayPayload(limitsStore.get().value, plansStore.get().catalog),
    theme: ui.get().theme === 'light' ? 'light' : 'dark',
    chats: trayChats(activity, done, titles),
  });
  if (payload !== sent && announceTray(payload)) sent = payload;
}

/** Меню трея обновляется при каждом новом чтении `/subscription` и ответе модели с процентами. */
export function startTray(): void {
  limitsStore.subscribe(send);
  plansStore.subscribe(send);
  chatActivity.subscribe(send);
  data.subscribe(send);
  ui.subscribe(send);
  if (window.koddiWindowBridge) send();
  else addEventListener('koddi-bridge', send, { once: true });
}
