import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, LOCAL_FAILURE } from './api';

describe('ошибки запросов для человека', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('сбой fetch не показывает «Failed to fetch»', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    await expect(api('/x')).rejects.toMatchObject({ message: LOCAL_FAILURE, status: 0, code: 'local_unavailable' });
  });

  it('отмена запроса остаётся отменой', async () => {
    const abort = new DOMException('Aborted', 'AbortError');
    vi.stubGlobal('fetch', vi.fn(async () => { throw abort; }));
    await expect(api('/x')).rejects.toBe(abort);
  });

  it('страница ошибки вместо JSON не даёт код в тексте', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>Bad gateway</html>', { status: 502 })));
    const failure = await api('/x').catch((error: unknown) => error) as ApiError;
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure.status).toBe(502);
    expect(failure.message).not.toMatch(/\d{3}/);
  });
});
