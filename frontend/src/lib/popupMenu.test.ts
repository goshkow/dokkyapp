import { afterEach, describe, expect, it, vi } from 'vitest';
import { popupMenu } from './bridge';

afterEach(() => {
  delete window.koddiWindowBridge;
  delete window.koddiPopupResult;
});

describe('системное меню хоста', () => {
  it('без моста сразу закрыто без выбора', async () => {
    await expect(popupMenu({ x: 1, y: 2 }, [{ value: 'a', label: 'A' }], 'a')).resolves.toBeNull();
  });

  it('отправляет пункты и ждёт ответ со своим номером', async () => {
    const sent: string[] = [];
    window.koddiWindowBridge = { adjustWidth: vi.fn(), command: vi.fn(), popupMenu: (payload: string) => { sent.push(payload); } };
    const first = popupMenu({ x: -5, y: 40 }, [{ value: 'ask', label: 'Вручную' }, { value: 'full', label: 'Разрешить всё' }], 'full');
    const second = popupMenu({ x: 0, y: 0 }, [{ value: 'pro', label: 'Pro' }], 'pro');
    const [a, b] = sent.map((item) => JSON.parse(item));
    expect(a).toMatchObject({ x: 0, y: 40, selected: 'full', items: [{ value: 'ask' }, { value: 'full' }] });
    window.koddiPopupResult?.(b.id, null);
    window.koddiPopupResult?.(a.id, 'ask');
    await expect(first).resolves.toBe('ask');
    await expect(second).resolves.toBeNull();
  });
});
