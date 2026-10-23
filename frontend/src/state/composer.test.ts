import { afterEach, describe, expect, it, vi } from 'vitest';
const network = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('../lib/api', () => ({ api: network.api }));
vi.mock('./overlay', () => ({ notify: vi.fn() }));
import { clearAttachments, composer, draftContent, restorePastedText, upload } from './composer';

afterEach(() => { clearAttachments(); composer.set({ text: '', focus: 0 }); vi.clearAllMocks(); });
const pasted = 'Исходник\r\n'.repeat(1000);
const file = () => new File([pasted], 'Вставленный текст.txt', { type: 'text/plain' });

describe('вставка во вложение и обратно', () => {
  it('сохраняет оригинал после загрузки и возвращает его ровно один раз', async () => {
    network.api.mockResolvedValueOnce({ id: 7 });
    composer.set({ text: 'Проверь' });
    await upload([file()], false, pasted);
    expect(draftContent()).toBe('Проверь');
    expect(composer.get().attachments[0]?.pastedText).toBe(pasted);
    restorePastedText({ attachmentId: 7 });
    restorePastedText({ attachmentId: 7 });
    expect(composer.get().text).toBe('Проверь\n\n' + pasted);
    expect(composer.get().attachments).toHaveLength(0);
    expect(composer.get().focus).toBe(1);
  });
  it('отмена до ответа сервера не возвращает вложение в черновик', async () => {
    let resolve!: (value: unknown) => void;
    network.api.mockImplementationOnce(() => new Promise((yes) => { resolve = yes; }));
    const pending = upload([file()], true, pasted);
    const job = composer.get().uploads[0]!;
    const signal = network.api.mock.calls[0]?.[1].signal;
    restorePastedText({ uploadKey: job.key });
    expect(signal.aborted).toBe(true);
    resolve({ id: 8 });
    await pending;
    expect(composer.get().text).toBe(pasted);
    expect(composer.get().attachments).toHaveLength(0);
    expect(composer.get().uploads).toHaveLength(0);
  });
  it('не теряет исходник при сетевой ошибке и отправляет только вложение', async () => {
    network.api.mockRejectedValueOnce(new Error('Сеть недоступна'));
    await upload([file()], false, pasted);
    expect(composer.get().uploads[0]?.error).toBe('Сеть недоступна');
    restorePastedText({ uploadKey: composer.get().uploads[0]!.key });
    expect(composer.get().text).toBe(pasted);
    composer.set({ text: '', attachments: [{ id: 9, pastedText: pasted }] });
    expect(draftContent()).toBe('Текст моего запроса во вложении.');
    composer.set({ attachments: [] });
    expect(draftContent()).toBe('');
  });
});
