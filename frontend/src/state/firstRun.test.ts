import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.fn();
const put = vi.fn();
const post = vi.fn();
const choiceDialog = vi.fn();
const notify = vi.fn();

vi.mock('../lib/api', () => ({ api, put, post }));
vi.mock('./data', () => ({ loadLocal: vi.fn() }));
vi.mock('./overlay', () => ({ choiceDialog, notify }));

async function fresh() {
  vi.resetModules();
  return import('./firstRun');
}

describe('доступ с телефона', () => {
  beforeEach(() => vi.clearAllMocks());

  it('вход про него не спрашивает', async () => {
    api.mockResolvedValue({ ask: false, chats: 0 });
    const { askFirstRunQuestions } = await fresh();
    await askFirstRunQuestions('me');
    expect(api).toHaveBeenCalledTimes(1);
    expect(api).toHaveBeenCalledWith('/api/local-data');
    expect(choiceDialog).not.toHaveBeenCalled();
  });

  it('ответ сразу включает доступ, и второй раз не спрашивает', async () => {
    api.mockResolvedValue({ asked: false });
    choiceDialog.mockResolvedValue('accept');
    const { askRemoteAccess } = await fresh();
    await askRemoteAccess();
    expect(put).toHaveBeenCalledWith('/api/remote-control', { enabled: true });
    await askRemoteAccess();
    expect(choiceDialog).toHaveBeenCalledTimes(1);
    expect(api).toHaveBeenCalledTimes(1);
  });

  it('отказ оставляет выключенным', async () => {
    api.mockResolvedValue({ asked: false });
    choiceDialog.mockResolvedValue('alternative');
    const { askRemoteAccess } = await fresh();
    await askRemoteAccess();
    expect(put).toHaveBeenCalledWith('/api/remote-control', { enabled: false });
  });

  it('отмена не отправляет и спросит в следующий раз', async () => {
    api.mockResolvedValue({ asked: false });
    choiceDialog.mockResolvedValue(null);
    const { askRemoteAccess } = await fresh();
    await expect(askRemoteAccess()).resolves.toBe(false);
    expect(put).not.toHaveBeenCalled();
    choiceDialog.mockResolvedValue('alternative');
    await expect(askRemoteAccess()).resolves.toBe(true);
    expect(choiceDialog).toHaveBeenCalledTimes(2);
  });

  it('сбой не мешает отправке, вопрос будет снова', async () => {
    api.mockRejectedValueOnce(new Error('Нет связи'));
    const { askRemoteAccess } = await fresh();
    await expect(askRemoteAccess()).resolves.toBe(true);
    expect(notify).toHaveBeenCalled();
    api.mockResolvedValue({ asked: false });
    choiceDialog.mockResolvedValue('accept');
    await expect(askRemoteAccess()).resolves.toBe(true);
    expect(choiceDialog).toHaveBeenCalledTimes(1);
  });

  it('уже решённый вопрос не открывается', async () => {
    api.mockResolvedValue({ asked: true });
    const { askRemoteAccess } = await fresh();
    await askRemoteAccess();
    expect(choiceDialog).not.toHaveBeenCalled();
  });
});
