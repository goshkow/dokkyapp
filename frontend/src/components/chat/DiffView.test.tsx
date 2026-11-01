import { cleanup } from '@testing-library/react';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { parseUnifiedDiff } from '../../lib/diff';
import { DiffView } from './DiffView';

const diff = '--- a/a.txt\n+++ b/a.txt\n@@ -1,2 +1,2 @@\n a\n-b\n+c\n';

afterEach(cleanup);

describe('откат куска в DiffView', () => {
  it('кнопка отдаёт файл и заголовок куска', () => {
    const files = parseUnifiedDiff(diff, [{ path: 'a.txt', before_hash: 'x', after_hash: 'y' }]);
    const onRevert = vi.fn();
    render(<DiffView files={files} id="1" onRevert={onRevert} />);
    fireEvent.click(screen.getByRole('button', { name: 'Откатить' }));
    expect(onRevert.mock.calls[0]?.[0]).toMatchObject({ path: 'a.txt' });
    expect(onRevert.mock.calls[0]?.[1]).toBe('@@ -1,2 +1,2 @@');
  });

  it('созданный файл и просмотр без отката - без кнопки', () => {
    const created = parseUnifiedDiff(diff, [{ path: 'a.txt', before_hash: null, after_hash: 'y' }]);
    const { rerender } = render(<DiffView files={created} id="1" onRevert={vi.fn()} />);
    expect(screen.queryByRole('button', { name: 'Откатить' })).toBeNull();
    rerender(<DiffView files={parseUnifiedDiff(diff, [{ path: 'a.txt', before_hash: 'x', after_hash: 'y' }])} id="1" />);
    expect(screen.queryByRole('button', { name: 'Откатить' })).toBeNull();
  });
});
