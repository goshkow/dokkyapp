import { beforeEach, describe, expect, it } from 'vitest';
import { clearSubagents, subagentRuns, trackSubagent } from './subagents';

describe('ход сабагента', () => {
  beforeEach(() => clearSubagents());

  it('считает действия и помнит последнее', () => {
    trackSubagent({ call_id: 'd1', event: 'started', agent: 'explore', title: 'где вход' });
    trackSubagent({ call_id: 'd1', event: 'tool', name: 'file.read', arguments: { path: 'src/app/login.ts' } });
    trackSubagent({ call_id: 'd1', event: 'tool', name: 'search.text', arguments: { query: 'login' } });
    expect(subagentRuns.get().runs.d1).toMatchObject({ agent: 'explore', title: 'где вход', actions: 2, last: 'Ищу в файлах проекта' });
    trackSubagent({ call_id: 'd1', event: 'finished', status: 'completed' });
    expect(subagentRuns.get().runs.d1?.status).toBe('completed');
    trackSubagent({ event: 'tool', name: 'file.read' });
    expect(Object.keys(subagentRuns.get().runs)).toEqual(['d1']);
  });
});
