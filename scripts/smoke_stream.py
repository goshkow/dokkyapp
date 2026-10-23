"""Режим разработки: живой текст, журнал и план не прыгают во время работы агента.

Сценарии идут на настоящем интерфейсе с синтетическими сообщениями, без запросов к ИИ.
"""

import os
import socket
import tempfile
import threading
import time

os.environ["KODDI_DATA_DIR"] = tempfile.mkdtemp(prefix="koddi-stream-")
os.environ["KODDI_INTERNAL_TEST"] = "1"

import uvicorn
from playwright.sync_api import sync_playwright

from koddi.api import create_app

SETUP = r"""
() => {
  window.scheduleChatSync = () => { setTimeout(() => { if (window.__list) renderChatMessages(window.__list); }, 60); };
  window.koddiProductScope = 'development';
  state.chat = {id: 1}; state.page = 'chat'; state.followFeed = true;
  document.querySelector('main').classList.add('chat-page');
  clearFeed();
  window.at = s => new Date(Date.parse('2026-10-16T10:00:00Z') + s * 1000).toISOString();
  window.tool = (id, name, args, seq, sec, done = true, result = {ok: true}) => [
    {event: 'tool.call', sequence: seq, created_at: at(sec), data: {id, name, arguments: args}},
    ...(done ? [{event: 'tool.result', sequence: seq + 0.5, created_at: at(sec + 1), data: {id, name, arguments: args, result}}] : []),
  ];
  window.turn = status => ({id: 1, status, created_at: at(0), timeline: [{id: 1, sequence: 1, created_at: at(0), kind: 'agent.step', payload: {text: 'Думаю…'}}]});
  window.msg = (id, content, meta) => ({id, role: 'assistant', content, created_at: at(meta.sequence || 0), metadata: {turn_id: 1, turn: turn(meta.turnStatus || 'running'), ...meta}});
  window.user = {id: 100, role: 'user', content: 'Сделай сравнение по реальным запросам аккаунта', metadata: {}};
  window.pos = el => { const feed = document.querySelector('#feed'); return Math.round(el.getBoundingClientRect().top - feed.getBoundingClientRect().top + feed.scrollTop); };
  window.frame = () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  window.wait = ms => new Promise(r => setTimeout(r, ms));
}
"""

FIRST_STEP = r"""async () => {
  const out = {};
  window.__list = [user, msg(2, '', {status: 'running', sequence: 2})];
  renderChatMessages(window.__list);
  const text = 'Сейчас изучу структуру проекта и найду реальные запросы аккаунта, чтобы сравнить цену и кэш.';
  for (let i = 0; i < text.length; i += 14) { queueStreamDelta(1, {message_id: 2, content: text.slice(i, i + 14), offset: i}); await wait(30); }
  await wait(900);
  const body = document.querySelector('[data-message-id="2"] .message-body');
  window.__el2 = body; out.top = pos(body);
  const done = msg(2, text, {status: 'completed', channel: 'commentary', sequence: 2, events: [...tool('t1', 'file.read', {path: 'a.py'}, 3, 3), ...tool('t2', 'directory.list', {path: '.'}, 5, 5)]});
  window.__list = [user, done]; renderChatMessages(window.__list);
  await frame();
  out.same = window.__el2.isConnected && !!window.__el2.closest('.local-work');
  out.after = pos(window.__el2);
  return out;
}"""

SECOND_STEP = r"""async () => {
  const out = {};
  const first = msg(2, 'Сейчас изучу структуру проекта.', {status: 'completed', channel: 'commentary', sequence: 2, events: [...tool('t1', 'file.read', {path: 'a.py'}, 3, 3)]});
  window.__list = [user, first, msg(3, '', {status: 'running', sequence: 8})]; renderChatMessages(window.__list);
  const text = 'Нашёл логи. Теперь читаю запросы и считаю стоимость для каждой модели отдельно.';
  for (let i = 0; i < text.length; i += 10) { queueStreamDelta(1, {message_id: 3, content: text.slice(i, i + 10), offset: i}); await wait(30); }
  await wait(800);
  const row = [...document.querySelectorAll('.local-work .journal-commentary')].at(-1);
  out.inJournal = !!row; out.standalone = !!document.querySelector('#feed > [data-message-id="3"]');
  window.__el3 = row; out.before = pos(row);
  const done = msg(3, text, {status: 'completed', channel: 'commentary', sequence: 8, events: [...tool('t3', 'shell.run', {command: 'python build.py'}, 9, 9, false)]});
  window.__list = [user, first, done]; renderChatMessages(window.__list);
  await frame();
  out.same = window.__el3.isConnected; out.after = pos(window.__el3);
  return out;
}"""

FINAL_STEP = r"""async () => {
  const out = {};
  const first = msg(2, 'Сейчас изучу структуру проекта.', {status: 'completed', channel: 'commentary', sequence: 2, events: [...tool('t1', 'file.read', {path: 'a.py'}, 3, 3)]});
  const act = msg(3, 'Теперь считаю.', {status: 'completed', channel: 'commentary', sequence: 8, events: [...tool('t3', 'shell.run', {command: 'python build.py'}, 9, 9), ...tool('t4', 'file.create', {path: 'report.md'}, 11, 11, true, {ok: true, added_lines: 203, removed_lines: 0})]});
  window.__list = [user, first, act, msg(4, '', {status: 'running', sequence: 14})]; renderChatMessages(window.__list);
  const text = '## Итог\n\nDeepSeek в режиме Low **заметно дешевле**: кэш попадает чаще.\n\n- Luna: 1.8\n- DeepSeek: 0.4';
  for (let i = 0; i < text.length; i += 12) { queueStreamDelta(1, {message_id: 4, content: text.slice(i, i + 12), offset: i}); await wait(30); }
  await wait(900);
  window.__el4 = [...document.querySelectorAll('.local-work .journal-commentary')].at(-1);
  const fin = msg(4, text, {status: 'completed', channel: 'final', sequence: 14, turnStatus: 'completed'});
  fin.metadata.turn.completed_at = at(20);
  window.__list = [user, first, act, fin]; renderChatMessages(window.__list);
  await wait(1200);
  const body = document.querySelector('[data-message-id="4"] .message-body');
  out.sameNode = body === window.__el4;
  out.summary = [...document.querySelectorAll('.journal-completed > summary')].map(s => s.textContent.trim());
  out.segments = [...document.querySelectorAll('.journal-segment .journal-summary-copy')].map(s => s.textContent);
  out.badge = [...document.querySelectorAll('.journal-badge:not([hidden])')].map(s => s.textContent);
  out.heading = !!body.querySelector('h2');
  return out;
}"""

STOPPED = r"""async () => {
  clearFeed(); state.chat = {id: 1}; state.page = 'chat';
  const stopped = msg(2, 'Начинаю.', {status: 'completed', channel: 'commentary', sequence: 2, turnStatus: 'cancelled', events: [...tool('t1', 'file.read', {path: 'a.py'}, 3, 3)]});
  stopped.metadata.turn.completed_at = at(9);
  renderChatMessages([user, stopped]); await frame();
  return {collapsed: !!document.querySelector('.journal-completed'), outcome: document.querySelector('.journal-outcome')?.textContent || ''};
}"""

MANY_ACTIONS = r"""async () => {
  clearFeed(); state.chat = {id: 1}; state.page = 'chat';
  let events = [];
  for (let i = 0; i < 60; i++) {
    events = [...events, ...tool('c' + i, i % 3 ? 'file.read' : 'shell.run', {path: 'f' + i + '.py', command: 'cmd ' + i}, 3 + i * 2, 3 + i)];
    renderChatMessages([user, msg(2, '', {status: 'running', sequence: 2, kind: 'activity', events})]);
    if (i % 10 === 0) await frame();
  }
  return {journals: document.querySelectorAll('.local-work').length, segments: document.querySelectorAll('.journal-segment').length, nodes: document.querySelectorAll('#feed *').length};
}"""


def main():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900}, bypass_csp=True)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/ui/", wait_until="domcontentloaded")
        page.wait_for_function("window.koddiProductScope === 'development' && !document.body.classList.contains('booting')")
        page.wait_for_timeout(400)
        page.evaluate(SETUP)

        # 1. первый шаг печатается обычным текстом и затем переезжает в журнал тем же узлом
        step = page.evaluate(FIRST_STEP)
        assert step["same"] and abs(step["top"] - step["after"]) < 2, step

        # 2. следующий шаг печатается сразу в журнале, а не отдельным блоком
        step = page.evaluate(SECOND_STEP)
        assert step["inJournal"] and not step["standalone"] and step["same"], step
        assert abs(step["before"] - step["after"]) < 2, step
        running = page.locator('.journal-segment[data-journal-active="true"]')
        assert running.locator(".journal-summary-copy").inner_text() == "Выполняется команда"
        first_time = running.locator(".journal-duration").inner_text()
        page.wait_for_timeout(2300)
        assert running.locator(".journal-duration").inner_text() != first_time, "секундомер не идёт"
        left_of_chevron = page.evaluate(
            "(() => { const s = document.querySelector('.journal-segment[data-journal-active=true] > summary'); return s.querySelector('.journal-duration').getBoundingClientRect().left < s.querySelector('svg').getBoundingClientRect().left; })()"
        )
        assert left_of_chevron, "время должно стоять слева от стрелки"

        # 3. итог: временный текст журнала становится ответом без перерисовки
        step = page.evaluate(FINAL_STEP)
        assert step["sameNode"] and step["heading"], step
        assert step["summary"] == ["Работал 20 сек."], step
        assert step["segments"] == ["Прочитан файл: a.py", "Создал 1 файл, выполнил 1 команду"], step
        assert step["badge"] == ["+203−0"], step

        # 4. остановленная работа не сворачивается и показывает итог
        step = page.evaluate(STOPPED)
        assert not step["collapsed"] and step["outcome"].startswith("Работа остановлена"), step

        # 5. поток из многих действий не плодит узлы и не дублирует журнал
        count = page.evaluate(MANY_ACTIONS)
        assert count["journals"] == 1 and count["segments"] == 1 and count["nodes"] < 80, count
        assert not errors, errors
        browser.close()
    print("Проверка устойчивости потока и журнала пройдена")


if __name__ == "__main__":
    main()
