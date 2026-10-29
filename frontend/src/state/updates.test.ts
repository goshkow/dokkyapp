import { describe, expect, it } from 'vitest';
import { updateNotice, type UpdateState } from './updates';

const base: UpdateState = {
  status: 'available', current: '0.2.2', platform: 'macos-arm64', kind: 'dmg', method: 'bundle', auto_download: false,
  error: '', available: { version: '0.2.3', name: 'Dokky-0.2.3-macOS-arm64.dmg', size: 100 }, progress: null,
};

describe('updateNotice', () => {
  it('нет строки без найденной версии и в спокойных состояниях', () => {
    expect(updateNotice(null)).toBeNull();
    expect(updateNotice({ ...base, available: null })).toBeNull();
    for (const status of ['idle', 'checking', 'up_to_date', 'skipped', 'disabled'] as const) {
      expect(updateNotice({ ...base, status })).toBeNull();
    }
  });

  it('найденная версия ставится сама, где умеем, иначе ведёт в настройки', () => {
    expect(updateNotice(base)).toMatchObject({ title: 'Докки 0.2.3', hint: 'Обновить', action: 'download', tone: 'accent' });
    expect(updateNotice({ ...base, method: 'move_to_applications' })?.action).toBe('settings');
    expect(updateNotice({ ...base, method: 'manual' })?.action).toBe('settings');
  });

  it('загрузка показывает процент в границах 0..100', () => {
    expect(updateNotice({ ...base, status: 'downloading', progress: { done: 62, total: 100 } })).toMatchObject({ percent: 62, hint: '62%', tone: 'busy' });
    expect(updateNotice({ ...base, status: 'downloading', progress: { done: 5, total: 0 } })).toMatchObject({ percent: 0, hint: '' });
    expect(updateNotice({ ...base, status: 'downloading', progress: { done: 150, total: 100 } })?.percent).toBe(100);
  });

  it('готовая версия перезапускает, сбой предлагает повторить', () => {
    expect(updateNotice({ ...base, status: 'ready' })).toMatchObject({ action: 'restart', hint: 'Перезапустить' });
    expect(updateNotice({ ...base, status: 'error', error: 'нет сети' })).toMatchObject({ action: 'download', tone: 'danger' });
    expect(updateNotice({ ...base, status: 'installing' })?.action).toBeNull();
  });
});
