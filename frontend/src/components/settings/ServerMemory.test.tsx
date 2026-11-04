import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { recordsWord, ServerMemory, TelegramSync } from './ServerMemory';
import { asset } from './VoicePage';

const answer = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

afterEach(() => { vi.restoreAllMocks(); });

describe('память Докки в настройках', () => {
  it('на старом сайте остаётся только переключатель', async () => {
    const fetched = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => answer({ detail: 'нет' }, 404));
    render(<ServerMemory enabled saveEnabled={async () => undefined} say={() => undefined} />);
    await waitFor(() => expect(fetched).toHaveBeenCalled());
    expect(screen.getByRole('switch', { name: 'Запоминать новое' })).toBeTruthy();
    expect(screen.queryByText('Докки помнит')).toBeNull();
    expect(screen.queryByText('Перенести из другой нейросети')).toBeNull();
  });

  it('показывает записи по группам и считает их', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => answer({ memory: { facts: ['Живу в Екатеринбурге', ' '], preferences: ['Коротко'], goals: 'не список' } }));
    render(<ServerMemory enabled saveEnabled={async () => undefined} say={() => undefined} />);
    expect(await screen.findByText('Живу в Екатеринбурге')).toBeTruthy();
    expect(screen.getByText('Как вам отвечать')).toBeTruthy();
    expect(screen.queryByText('Ваши цели')).toBeNull();
    expect(screen.getByText('2')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Перенести/ }));
    expect(screen.getByRole('button', { name: 'Разобрать' })).toHaveProperty('disabled', true);
  });

  it('склоняет записи', () => {
    expect([1, 2, 5, 11, 21, 104].map(recordsWord)).toEqual(['запись', 'записи', 'записей', 'записей', 'запись', 'записи']);
  });
});

describe('чаты в Telegram', () => {
  it('переключатель скрыт, если сайт не ответил', async () => {
    const fetched = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => answer({}, 404));
    const { container } = render(<TelegramSync say={() => undefined} />);
    await waitFor(() => expect(fetched).toHaveBeenCalled());
    expect(container.textContent).toBe('');
  });
});

describe('файлы голосов', () => {
  it('идут через свой сервер и только по точному имени', () => {
    expect(asset('voices/abc-1.mp3')).toBe('/api/dokky/voice-assets/abc-1.mp3');
    expect(asset('voices/abc.webp')).toBe('/api/dokky/voice-assets/abc.webp');
    for (const bad of ['../secret.mp3', 'voices/../x.mp3', 'voices/x.svg', 'https://evil.test/voices/x.mp3', undefined]) expect(asset(bad)).toBeNull();
  });
});
