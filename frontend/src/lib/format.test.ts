import { describe, expect, it } from 'vitest';
import { relativeTime, serverTime } from './format';

describe('время сервера', () => {
  it('SQLite без пояса читается как UTC, а не как местное время', () => {
    expect(serverTime('2026-10-17 10:00:00')).toBe(Date.UTC(2026, 9, 17, 10, 0, 0));
    expect(serverTime('2026-10-17T10:00:00+00:00')).toBe(Date.UTC(2026, 9, 17, 10, 0, 0));
    expect(serverTime('2026-10-17T10:00:00Z')).toBe(Date.UTC(2026, 9, 17, 10, 0, 0));
    expect(Number.isNaN(serverTime(''))).toBe(true);
  });
  it('свежий чат показывает «сейчас», а не часы часового пояса', () => {
    const now = Date.UTC(2026, 9, 17, 10, 0, 10);
    expect(relativeTime('2026-10-17 10:00:00', now)).toBe('сейчас');
    expect(relativeTime('2026-10-17 07:00:00', now)).toBe('3 ч');
  });
});

import { registerMedia, resolveMedia, mediaUrl } from './cloud';

describe('медиа сообщений формата app/1', () => {
  it('подставляет адреса картинок через локальный прокси и скрывает неизвестные при печати', () => {
    registerMedia([
      { id: 'photo_1', kind: 'image', file_id: 'file_abc', url: '/api/v1/files/file_abc/content', alt: 'Салон' },
      { id: 'ext_1', kind: 'image', url: '/api/v1/media/proxy?url=https%3A%2F%2Fexample.org%2Fa.png' },
      { id: 'yt_1', kind: 'video', provider: 'youtube', video_id: 'abc123', title: 'Ролик' },
    ]);
    expect(mediaUrl({ id: 'x', url: '/api/v1/files/file_abc/content' })).toContain('files/file_abc/content');
    expect(resolveMedia('Фото: ![Салон](dokky:media/photo_1)')).toBe('Фото: ![Салон](/__rich__/photo_1)');
    expect(resolveMedia('![](dokky:media/ext_1)')).toBe('![Изображение](/__rich__/ext_1)');
    expect(mediaUrl({ id: 'e', url: '/api/v1/media/proxy?url=https%3A%2F%2Fexample.org%2Fa.png' })).toBe('/api/dokky/media?url=https%3A%2F%2Fexample.org%2Fa.png');
    expect(resolveMedia('![Ролик](dokky:media/yt_1)')).toBe('![Ролик](https://www.youtube.com/watch?v=abc123)');
    expect(resolveMedia('![Новое](dokky:media/later)', true)).toBe('');
    expect(resolveMedia('![Новое](dokky:media/later)', false)).toBe('Новое');
  });
});
