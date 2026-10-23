import { afterEach, describe, expect, it, vi } from 'vitest';
const network = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('../lib/api', () => ({ api: network.api }));
vi.mock('../lib/mirror', () => ({ isMirror: true, remote: (_name: string, local: unknown) => local, exposeStore: vi.fn(), exposeActions: vi.fn() }));
vi.mock('./chat', () => ({ send: vi.fn(), sendOrStop: vi.fn() }));
vi.mock('./codingSession', () => ({ recordCodingActivity: vi.fn() }));
vi.mock('./queue', () => ({ enqueueDraft: vi.fn(), interruptAndSend: vi.fn() }));
vi.mock('./voice', () => ({ cancelVoice: vi.fn(), stopVoice: vi.fn(), toggleVoice: vi.fn(), voiceLevels: vi.fn() }));
vi.mock('./overlay', () => ({ notify: vi.fn(), guarded: (fn: unknown) => fn }));
import { uploadFromField } from './field';
import { clearAttachments, composer, restorePastedText } from './composer';
afterEach(() => { clearAttachments(); composer.set({ text: '' }); vi.clearAllMocks(); });

describe('вставка из нативного поля', () => {
  const text = '# Запрос\n' + 'Текст\r\n'.repeat(1000);
  const file = () => new File([text], 'Вставленный текст.md');
  it('передаёт локальный исходник в главный вид и сохраняет обычный upload', async () => {
    network.api.mockResolvedValueOnce({ id: 12 });
    await uploadFromField([file()], false, text);
    expect(composer.get().attachments[0]?.pastedText).toBe(text);
    expect((network.api.mock.calls[0]?.[1].body as FormData).get('file')).toBeInstanceOf(File);
    restorePastedText({ attachmentId: 12 });
    expect(composer.get().text).toBe(text);
  });
  it('собирает вставку больше лимита relay без обрезки источника', async () => {
    const source = 'Большой текст\n'.repeat(300000);
    network.api.mockResolvedValueOnce({ id: 14 });
    await uploadFromField([new File([source], 'Вставленный текст.txt')], false, source);
    expect(composer.get().attachments[0]?.pastedText).toBe(source);
  });
  it('сохраняет источник при ошибке, а поздний ответ после восстановления игнорирует', async () => {
    network.api.mockRejectedValueOnce(new Error('Ошибка сети'));
    await uploadFromField([file()], true, text);
    expect(composer.get().uploads[0]?.error).toBe('Ошибка сети');
    restorePastedText({ uploadKey: composer.get().uploads[0]!.key });
    expect(composer.get().text).toBe(text);
    let resolve!: (value: unknown) => void;
    network.api.mockImplementationOnce(() => new Promise((yes) => { resolve = yes; }));
    const pending = uploadFromField([file()], false, text);
    await Promise.resolve();
    restorePastedText({ uploadKey: composer.get().uploads[0]!.key });
    resolve({ id: 13 });
    await pending;
    expect(composer.get().attachments).toHaveLength(0);
  });
});
