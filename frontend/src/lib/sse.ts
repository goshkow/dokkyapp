export interface SseEvent {
  event: string;
  id: string;
  data: Record<string, any>;
}

/** Читает поток server-sent events; завершает чтение и освобождает ридер при любом выходе. */
export async function* readSse(response: Response, signal: AbortSignal): AsyncGenerator<SseEvent> {
  if (!response.body) return;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (!signal.aborted) {
      const packet = await reader.read();
      if (packet.done) break;
      buffer += decoder.decode(packet.value, { stream: true });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() ?? '';
      for (const block of blocks) {
        const data = block
          .split(/\r?\n/)
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5).trimStart())
          .join('\n');
        if (!data) continue;
        let parsed: Record<string, any>;
        try {
          parsed = JSON.parse(data) as Record<string, any>;
        } catch {
          continue;
        }
        yield { event: /^event:\s*(.*)$/m.exec(block)?.[1] ?? '', id: /^id:\s*(.*)$/m.exec(block)?.[1] ?? '', data: parsed };
      }
    }
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
