import { describe, expect, it } from 'vitest';
import { openSettings, settingsUi } from './settingsUi';

describe('переходы настроек', () => {
  it('прежняя ссылка на использование открывает подписку и оплату', () => {
    openSettings('usage');
    expect(settingsUi.get().section).toBe('billing');
  });
});
