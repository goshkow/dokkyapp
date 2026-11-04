import { describe, expect, it, vi } from 'vitest';
import { nav, openChat, openPage, setChatLoadFailed, view } from './view';
import type { Chat } from '../lib/types';

vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));

describe('повторное открытие чата', () => {
  it('открытый чат не открывается заново и не копит историю, со страницы возвращается', () => {
    const chat = { id: 7, title: 'Чат', project_id: null } as unknown as Chat;
    openChat(chat);
    const { version } = view.get();
    const { index } = nav.get();
    openChat({ ...chat });
    openChat({ ...chat });
    expect(view.get().version).toBe(version);
    expect(nav.get().index).toBe(index);
    openPage('tasks' as never);
    openChat(chat);
    expect(view.get().page).toBe('chat');
    expect(view.get().chat?.id).toBe(7);
  });

  it('чат не загрузился: повторный клик пробует снова', () => {
    const chat = { id: 8, title: 'Чат', project_id: null } as unknown as Chat;
    openChat(chat);
    const { version } = view.get();
    setChatLoadFailed(() => true);
    openChat({ ...chat });
    setChatLoadFailed(() => false);
    expect(view.get().version).toBe(version + 1);
  });
});
