"""Ограниченный платный прогон текущего runtime в изолированной копии проекта.

Запускается только явно: --confirm-live. Вход читается без изменения исходного
профиля; refresh/logout запрещены. Результаты и копия проекта остаются для проверки.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm-live', action='store_true')
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--resume', type=Path)
    parser.add_argument('--mode', choices=['light', 'pro', 'max'], default='pro')
    parser.add_argument('--seconds', type=int, default=300)
    parser.add_argument('--max-requests', type=int, default=10)
    args = parser.parse_args()
    if not args.confirm_live:
        parser.error('Нужен --confirm-live; запросы расходуют лимит Докки')
    from koddi.config import data_dir
    original = data_dir()
    from koddi.dokky.client import CredentialStore, DokkyError, dokky

    class ReadLogin(CredentialStore):
        @property
        def path(self):
            return original / ('dokky-session.dpapi' if os.name == 'nt' else 'dokky-session.keychain')

        def clear(self):
            pass

        def save(self, value):
            raise RuntimeError('Тест не меняет исходный вход')

    from koddi.storage.db import get_setting
    expected_user = get_setting('dokky_user', {})
    expected_owner = str(expected_user.get('id') or expected_user.get('user_id') or '')
    login = ReadLogin().load()
    if not login or login.get('expires_at', 0) <= time.time() + 90:
        raise RuntimeError('Для теста нужен действующий вход; refresh не выполнялся')
    output = args.resume.resolve() if args.resume else Path(tempfile.mkdtemp(prefix='dokky-live-existing-')).resolve()
    profile, workspace = output / 'profile', output / 'workspace'
    profile.mkdir(exist_ok=True)
    workspace.mkdir(exist_ok=True)
    os.environ['KODDI_DATA_DIR'] = str(profile)
    os.environ.pop('KODDI_INTERNAL_TEST', None)
    logging.disable(logging.CRITICAL)
    import koddi.storage.db as storage
    storage.DB_PATH = profile / 'koddi.sqlite3'
    storage.init_db()

    class MemoryLogin:
        def load(self):
            import sqlite3
            with sqlite3.connect((original / 'koddi.sqlite3').as_uri() + '?mode=ro', uri=True) as conn:
                row = conn.execute("SELECT value_json FROM settings WHERE key='dokky_user'").fetchone()
            live_user = json.loads(row[0]) if row else {}
            if str(live_user.get('id') or live_user.get('user_id') or '') != expected_owner:
                raise DokkyError('Аккаунт изменился; тест остановлен', 409, 'session_changed')
            fresh = ReadLogin().load()
            if fresh:
                login.update(fresh)
            return dict(login)

        def clear(self):
            login.clear()

        def save(self, value):
            raise RuntimeError('Обновление токенов во время приёмки запрещено')

    async def no_refresh(*_a, **_kw):
        raise DokkyError('Срок входа истёк: тест остановлен без обновления токенов', 401, 'acceptance_token_expired')

    dokky.store = MemoryLogin()
    dokky.tokens = no_refresh
    report = {'mode': args.mode, 'source': args.source.name, 'runs': [], 'ok': False, 'max_requests_per_turn': args.max_requests,
              'seconds_per_turn': args.seconds, 'cash_cost': None, 'live_profile_modified': False}
    if args.resume and (output / 'report.json').exists():
        prior = json.loads((output / 'report.json').read_text(encoding='utf-8'))
        report['prior_attempts'] = prior.get('prior_attempts', []) + prior.get('runs', [])
    sessions = []
    try:
        from koddi.logs.service import RAW_SECRETS
        # только исходники страницы, никаких .env, БД, конфигураций входа или пользовательских данных
        paths = ['src/site.py', 'src/templates/login.html', 'src/templates/search.html', 'src/static/design.css', 'src/static/theme.js']
        hashes = {}
        for relative in paths:
            source = args.source / relative
            content = source.read_text(encoding='utf-8')
            if source.suffix == '.py':
                import io
                import tokenize
                content = tokenize.untokenize(token._replace(string='# комментарий исключён из приёмочного снимка') if token.type == tokenize.COMMENT and any(pattern.search(token.string) for pattern in RAW_SECRETS) else token for token in tokenize.generate_tokens(io.StringIO(content).readline))
            if any(pattern.search(content) for pattern in RAW_SECRETS):
                raise RuntimeError(f'Источник требует ручной проверки секретов: {relative}')
            if source.suffix == '.py':
                import ast
                import re
                for assignment in ast.walk(ast.parse(content)):
                    if isinstance(assignment, (ast.Assign, ast.AnnAssign)) and isinstance(assignment.value, ast.Constant) and isinstance(assignment.value.value, str) and len(assignment.value.value) >= 12:
                        targets = assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
                        if any(isinstance(target, ast.Name) and re.search(r'(?i)(password|secret|api_key|access_token|refresh_token)$', target.id) for target in targets):
                            raise RuntimeError(f'Требует проверки строкового секрета: {relative}')
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if not args.resume:
                target.write_text(content, encoding='utf-8')
            hashes[relative] = hashlib.sha256(source.read_bytes()).hexdigest()
        logo = 'src/static/vendor/dokki-mark.png'
        target = workspace / logo
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.source / logo, target)
        for directory in ['src/static/vendor/fonts', 'src/static/vendor/lucide']:
            shutil.copytree(args.source / directory, workspace / directory, dirs_exist_ok=True)
        (workspace / 'README.md').write_text('Изолированный снимок существующего FastAPI проекта Докки Studio. Рабочие исходники - src/site.py, шаблоны src/templates, стили src/static/design.css. Интеграции и данные здесь намеренно отсутствуют. Проверка маршрута - AST, страницы - локальный HTTP и браузер. Не запускать весь site.py и не устанавливать зависимости. Не изменять вход и существующие маршруты.\n', encoding='utf-8')
        (workspace / 'AGENTS.md').write_text('Комментарии кратко по-русски. Переиспользуй текущие шаблоны, стили и логотип. Изучай только файлы задачи. Не читай за пределами workspace. Не устанавливай зависимости, не обращайся в интернет и не запускай shell. Синтаксис проверяй verification.run.\n', encoding='utf-8')
        await dokky.bootstrap()
        from koddi.policy.preferences import save_reasoning_mode
        save_reasoning_mode(args.mode)
        storage.set_setting('permission_profile:' + str(storage.get_setting('dokky_user', {})['id']), 'trusted')
        storage.set_setting('coding_keepalive:' + str(storage.get_setting('dokky_user', {})['id']), False)
        storage.set_setting('computer_use_enabled:' + str(storage.get_setting('dokky_user', {})['id']), False)
        from koddi.projects import add_project
        project = add_project(str(workspace))
        from koddi.dokky import coding
        from koddi.agents.orchestrator import AgentOrchestrator
        with storage.db() as conn:
            existing_chat = conn.execute("SELECT id FROM chats ORDER BY id DESC LIMIT 1").fetchone() if args.resume else None
            chat_id = existing_chat[0] if existing_chat else conn.execute("INSERT INTO chats(project_id,title,mode,model,owner_id) VALUES (?,?,'development','dokky-code',?)", (project['id'], 'Приёмка существующего проекта', coding.owner())).lastrowid
        session = await coding.ensure(chat_id)
        sessions.append(session['session']['id'])
        if session['session']['reasoning_mode'] != args.mode:
            raise RuntimeError('Сервер не подтвердил выбранный режим')
        report['gateway_model'] = 'dokky-code'
        safe = {'plan.update', 'task.complete', 'project.instructions', 'directory.list', 'directory.tree', 'file.read', 'file.read_many', 'file.read_range', 'file.read_chunk', 'file.metadata', 'file.create', 'file.write_patch', 'file.edit', 'file.search_replace', 'patch.apply', 'patch.preview', 'search.text', 'search.files', 'verification.run', 'tool.output_read', 'git.diff', 'git.status', 'vision.inspect', 'code.outline', 'secret.scan', 'diff.review', 'safety.review'}
        prompts = [
            'Добавь к существующему проекту отдельный аккуратный адаптивный лендинг /landing для Докки Studio. Используй текущий логотип и фирменные стили, сохрани вход и все существующие маршруты. Создай отдельный HTML шаблон и при необходимости отдельный CSS, подключи GET /landing в src/site.py. Достаточно hero, трёх преимуществ и ссылки на вход. README описывает изоляцию: интеграции отсутствуют, весь site.py запускать не надо, не устанавливай зависимости и не используй shell/сеть. Проверь синтаксис изменённого Python через verification.run. Закончи кратким итогом.',
            'Доработай созданный лендинг: добавь в конце раздел FAQ с двумя раскрывающимися вопросами на нативных details/summary и подписью раздела «Частые вопросы». Сохрани маршрут и существующие секции. Не исследуй заново весь проект, работай с ранее созданной страницей. Проверь результат и закончи кратко. Команды и интернет не нужны.',
        ]
        if args.resume:
            prompts[0] = 'Продолжи предыдущую остановленную тестовым бюджетом задачу. Шаблон landing.html, стили landing.css и маршрут уже созданы. Проверь их, проверь синтаксис src/site.py через verification.run, заверши план и кратко сообщи результат. Не создавай всё заново. Не запускай команды/интернет. README описывает изоляцию.'
        for index, prompt in enumerate(prompts):
            cancellation = threading.Event()
            run = {'case': index + 1, 'tools': [], 'first_write_seconds': None, 'status': 'unknown'}
            report['runs'].append(run)
            started = time.monotonic()
            stream = AgentOrchestrator().run(chat_id=chat_id, project_id=project['id'], workspace=workspace, user_message=prompt, mode='development', cancel_event=cancellation)
            try:
                async with asyncio.timeout(args.seconds):
                    async for event in stream:
                        kind = event.splitlines()[0][7:]
                        payload = json.loads(event.split('data: ', 1)[1])
                        if kind == 'agent.step' and payload.get('step', 0) > args.max_requests:
                            raise RuntimeError('Достигнут бюджет запросов теста')
                        if kind == 'tool.call':
                            name = payload.get('name')
                            if name not in safe:
                                raise RuntimeError(f'Тест остановлен до действия за пределами приёмки: {name}')
                            run['tools'].append(name)
                            print(f"Задача {index+1}: {name}, {time.monotonic()-started:.1f} с", flush=True)
                        if kind == 'tool.result' and payload.get('result', {}).get('change_set_id') and run['first_write_seconds'] is None:
                            run['first_write_seconds'] = round(time.monotonic() - started, 3)
                        if kind == 'agent.input_required':
                            if payload.get('kind') != 'approval' or payload.get('name') not in safe:
                                raise RuntimeError('Тест требует непредусмотренного ввода')
                            with storage.db() as conn:
                                conn.execute('UPDATE agent_requests SET response_json=? WHERE id=?', (json.dumps({'approved': True}), payload['id']))
                        if kind in {'turn.failed', 'turn.cancelled', 'turn.completed'}:
                            run['status'] = kind[5:]
                            if kind == 'turn.failed':
                                run['error_code'] = payload.get('code')
                                run['error'] = str(payload.get('error'))[:250]
            except (RuntimeError, TimeoutError) as exc:
                run['failure'] = str(exc)
            finally:
                cancellation.set()
                await stream.aclose()
                run['seconds'] = round(time.monotonic() - started, 3)
            with storage.db() as conn:
                row = conn.execute('SELECT metrics_json FROM turn_metrics ORDER BY turn_id DESC LIMIT 1').fetchone()
                run['metrics'] = json.loads(row[0]) if row else None
            files = list((workspace / 'src/templates').glob('*landing*.html'))
            import ast
            import re
            from bs4 import BeautifulSoup
            route_source = (workspace / 'src/site.py').read_text(encoding='utf-8')
            ast.parse(route_source)
            route = bool(re.search(r'@\w+\.get\([\"\']/landing[\"\']', route_source))
            html = files[0].read_text(encoding='utf-8') if len(files) == 1 else ''
            soup = BeautifulSoup(html, 'html.parser')
            run['acceptance'] = {'route': route, 'page': bool(soup.find('h1')), 'login_link': bool(soup.find('a', href=re.compile('login|^/$'))), 'logo': bool(soup.find('img', src=re.compile('dokki-mark'))), 'python_syntax': True}
            if index:
                run['acceptance']['faq'] = len(soup.select('details > summary')) >= 2 and 'Частые вопросы' in soup.get_text()
            if run['status'] != 'completed' or not all(run['acceptance'].values()):
                break
        report['source_unchanged'] = all(hashlib.sha256((args.source / relative).read_bytes()).hexdigest() == digest for relative, digest in hashes.items())
        report['ok'] = len(report['runs']) == 2 and all(run['status'] == 'completed' and all(run.get('acceptance', {}).values()) for run in report['runs'])
    except (DokkyError, RuntimeError) as exc:
        report['failure'] = f'{type(exc).__name__}: {exc}'[:300]
    finally:
        report['server_cleanup_ok'] = True
        with storage.db() as conn:
            sessions.extend(json.loads(row[0])['id'] for row in conn.execute('SELECT session_json FROM coding_sessions'))
        for session_id in set(sessions):
            try:
                await dokky.request('DELETE', '/coding/sessions/' + session_id, params={'confirm': 'true'})
            except DokkyError as exc:
                if exc.status != 404:
                    report['server_cleanup_ok'] = False
            except Exception:
                report['server_cleanup_ok'] = False
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Отчёт:', output / 'report.json', flush=True)
        print(json.dumps({key: value for key, value in report.items() if key not in {'runs', 'prior_attempts'}}, ensure_ascii=False), flush=True)
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
