import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { view } from '../../state/view';
import { useContextBreakdown } from './ContextCard';

const api = vi.hoisted(() => vi.fn());
vi.mock('../../lib/api', async (original) => ({ ...(await original<object>()), api }));

afterEach(() => { cleanup(); api.mockReset(); });

const breakdown = (used: number) => ({ window: 1000, used, compact_at: null, exact: null, rows: [] });

describe('разбивка контекста', () => {
  it('поздний ответ прошлого чата не попадает в новый', async () => {
    let finishOld: (value: unknown) => void = () => undefined;
    api.mockImplementation((path: string) => (path.includes('/1/')
      ? new Promise((resolve) => { finishOld = resolve; })
      : Promise.resolve(breakdown(200))));
    act(() => view.set({ chat: { id: 1 } as never }));
    const { result } = renderHook(() => useContextBreakdown(true, false));
    act(() => view.set({ chat: { id: 2 } as never }));
    await waitFor(() => expect(result.current.value?.used).toBe(200));
    await act(async () => { finishOld(breakdown(900)); });
    expect(result.current.value?.used).toBe(200);
  });
});
