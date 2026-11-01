/* вопросы при входе: чьи данные на компьютере и нужен ли доступ с телефона; закрытое окно спросит снова при следующем запуске */
import { api, post, put } from '../lib/api';
import { loadLocal } from './data';
import { choiceDialog, notify } from './overlay';

let askedFor: string | null = null;
let remoteSettled = false;

const chatsWord = (count: number): string => {
  const tens = count % 100;
  const ones = count % 10;
  if (tens >= 11 && tens <= 14) return `${count} чатов`;
  if (ones === 1) return `${count} чат`;
  if (ones >= 2 && ones <= 4) return `${count} чата`;
  return `${count} чатов`;
};

async function askLocalData(): Promise<void> {
  const state = await api<{ ask: boolean; chats: number }>('/api/local-data');
  if (!state.ask) return;
  const found = state.chats ? `Здесь есть данные другого аккаунта Докки: ${chatsWord(state.chats)} и память проектов.` : 'Здесь есть данные другого аккаунта Докки: память проектов.';
  const answer = await choiceDialog(
    'Чьи это данные?',
    `${found} Можно забрать их себе или начать с чистого листа: тогда они останутся у прежнего аккаунта и вернутся при его входе. Папки проектов остаются в списке в любом случае.`,
    'Начать с чистого листа',
    'Забрать себе',
  );
  if (answer === null) return;
  await post('/api/local-data', { take: answer === 'alternative' });
  if (answer === 'alternative') await loadLocal(false);
}

/** Спрашивает один раз, перед первой сессией в режиме кода: раньше доступ с телефона не нужен.
 * false - человек отменил, отправлять не надо. Сбой вопроса не мешает отправить сообщение: спросим в следующий раз. */
export async function askRemoteAccess(): Promise<boolean> {
  if (remoteSettled) return true;
  try {
    return await askRemote();
  } catch (error) {
    notify((error as Error)?.message || 'Не удалось спросить про доступ с телефона', true);
    return true;
  }
}

async function askRemote(): Promise<boolean> {
  const state = await api<{ asked?: boolean }>('/api/remote-control');
  if (state.asked !== false) {
    remoteSettled = true;
    return true;
  }
  const answer = await choiceDialog(
    'Доступ с телефона',
    'Докки может показывать чаты этого компьютера на сайте и в приложении на телефоне: оттуда можно писать агенту и отвечать на его вопросы. Изменить это можно в настройках.',
    'Включить',
    'Не сейчас',
  );
  if (answer === null) return false;
  await put('/api/remote-control', { enabled: answer === 'accept' });
  remoteSettled = true;
  return true;
}

export async function askFirstRunQuestions(owner: string): Promise<void> {
  if (askedFor === owner) return;
  // другой аккаунт отвечает сам за себя
  if (askedFor !== null) remoteSettled = false;
  askedFor = owner;
  try {
    await askLocalData();
  } catch (error) {
    notify((error as Error)?.message || 'Не удалось открыть вопрос при входе', true);
  }
}
