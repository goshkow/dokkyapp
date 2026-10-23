import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { ApprovalPreview, previewAction } from './ApprovalPreview';
afterEach(cleanup);

describe('подробности подтверждения', () => {
  it('показывает команду обычным текстом, без JSON и исполнения HTML', () => {
    const { container } = render(<ApprovalPreview id="1" name="shell.run" args={{ command: 'pytest tests/test_runs.py -q\necho "<img src=x onerror=alert(1)>"' }} />);
    expect(screen.getByText(/pytest tests/).textContent).toContain('pytest tests/test_runs.py -q');
    expect(container.querySelector('img')).toBeNull();
    expect(container.textContent).not.toContain('"command":');
  });
  it('маскирует явные credentials и ограничивает длинное содержимое', () => {
    const preview = previewAction('shell.run', { command: 'API_KEY="private-value" pytest\n'.repeat(1000) })!;
    expect(preview.truncated).toBe(true);
    expect(preview.text).not.toContain('private-value');
    expect(preview.text).toContain('[скрыто]');
    expect(preview.text.length).toBeLessThanOrEqual(20000);
  });
  it('показывает настоящий unified diff через общий компонент', () => {
    const { container } = render(<ApprovalPreview id="2" name="patch.apply" args={{ patch: '--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n' }} />);
    expect(container.querySelector('.diff-line.added')?.textContent).toContain('new');
    expect(container.querySelector('.diff-line.removed')?.textContent).toContain('old');
    expect(container.querySelector('#diff-proposed-2')).toBeTruthy();
  });
});
