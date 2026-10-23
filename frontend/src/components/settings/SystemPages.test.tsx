import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { OpenSourceLicenses } from './SystemPages';

vi.mock('../../state/data', () => ({ useData: () => ({}), saveSettings: vi.fn() }));
vi.mock('../../state/overlay', () => ({ confirmDialog: vi.fn(), guarded: (fn: unknown) => fn }));
vi.mock('../../lib/external', () => ({ openExternal: vi.fn(), SITE: 'https://example.com' }));
vi.mock('../../lib/glass', () => ({ glassActive: () => false, glassCapable: () => false, setGlassEnabled: vi.fn() }));
vi.mock('./GeneralPage', () => ({ saveKey: vi.fn() }));

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('лицензии в настройках', () => {
  it('загружает каталог по запросу и показывает текст как данные', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({
      limitations: ['Проверить сборку'], components: [{ id: 'react', name: 'React', version: '19', license: 'MIT', status: 'notice_collected', notices: [{ id: 'legal', name: 'LICENSE' }] }],
    }))).mockResolvedValueOnce(new Response(JSON.stringify({ text: '<img src=x onerror=alert(1)> MIT notice' })));
    vi.stubGlobal('fetch', fetch);
    render(<OpenSourceLicenses />);
    expect(fetch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Открыть лицензии' }));
    await screen.findByText('React 19 - MIT');
    expect(screen.getByText('Проверить сборку')).toBeTruthy();
    fireEvent.click(screen.getByText('LICENSE'));
    await waitFor(() => expect(screen.getByLabelText('Текст лицензии').textContent).toContain('<img'));
    expect(document.querySelector('img')).toBeNull();
  });
  it('показывает ошибку, позволяет повторить запрос и обрабатывает пустой каталог', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(new Response(JSON.stringify({ limitations: [], components: [] }))));
    render(<OpenSourceLicenses />);
    fireEvent.click(screen.getByRole('button', { name: 'Открыть лицензии' }));
    await screen.findByRole('alert');
    fireEvent.click(screen.getByRole('button', { name: 'Открыть лицензии' }));
    await screen.findByText('Каталог пуст');
  });
});
