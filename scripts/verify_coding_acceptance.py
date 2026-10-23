"""Независимая локальная проверка результата ограниченного live-прогона."""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit


def verify(output: Path, source: Path) -> dict:
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient
    from playwright.sync_api import sync_playwright
    import uvicorn
    import socket

    workspace = output / 'workspace'
    original = ast.parse((source / 'src/site.py').read_text(encoding='utf-8'))
    changed = ast.parse((workspace / 'src/site.py').read_text(encoding='utf-8'))
    functions = {node.name: node for node in changed.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for node in original.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert ast.dump(node) == ast.dump(functions[node.name]), f'Изменена прежняя функция {node.name}'
    assert all((source / relative).read_bytes() == (workspace / relative).read_bytes() for relative in ['src/templates/login.html', 'src/templates/search.html', 'src/static/design.css', 'src/static/theme.js'])
    route = next(node for node in functions.values() if any(isinstance(decorator, ast.Call) and decorator.args and isinstance(decorator.args[0], ast.Constant) and decorator.args[0].value == '/landing' for decorator in node.decorator_list))
    calls = [node for node in ast.walk(route) if isinstance(node, ast.Call)]
    assert all(isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name) and (call.func.value.id, call.func.attr) in {('app', 'get'), ('templates', 'TemplateResponse')} for call in calls)
    assert len(route.body) == 1 and isinstance(route.body[0], ast.Return)
    page_html = (workspace / 'src/templates/landing.html').read_text(encoding='utf-8')
    assert '{{' not in page_html and '{%' not in page_html
    assert all(not isinstance(node, (ast.Import, ast.ImportFrom, ast.Lambda, ast.ListComp, ast.DictComp, ast.GeneratorExp)) for node in ast.walk(route))
    app = FastAPI()
    namespace = {'__builtins__': {}, 'app': app, 'Request': Request, 'HTMLResponse': HTMLResponse, 'templates': Jinja2Templates(directory=str(workspace / 'src/templates'))}
    # исполняется только проверенный однооператорный обработчик, весь проект с интеграциями не импортируется
    exec(compile(ast.Module(body=[route], type_ignores=[]), 'acceptance-route', 'exec'), namespace)
    app.mount('/static', StaticFiles(directory=str(workspace / 'src/static')), name='static')
    response = TestClient(app).get('/landing')
    assert response.status_code == 200 and 'Частые вопросы' in response.text
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
    worker = threading.Thread(target=server.run, daemon=True)
    worker.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started
    results = {'http_route': True, 'existing_functions_unchanged': True, 'existing_templates_and_styles_unchanged': True, 'screens': []}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for width, height in [(1360, 900), (390, 844)]:
                page = browser.new_page(viewport={'width': width, 'height': height})
                errors, failures = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('response', lambda response: failures.append(response.status) if response.status >= 400 else None)
                page.route('**/*', lambda route: route.continue_() if urlsplit(route.request.url).netloc == f'127.0.0.1:{port}' else route.abort())
                page.goto(f'http://127.0.0.1:{port}/landing', wait_until='networkidle')
                assert page.locator('h1').is_visible()
                assert page.locator('details > summary').count() >= 2
                page.locator('details > summary').first.click()
                assert page.locator('details').first.get_attribute('open') is not None
                assert page.locator('img').first.evaluate('node => node.complete && node.naturalWidth > 0')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                assert not errors, errors
                assert not failures, failures
                screenshot = output / f'landing-{width}.png'
                page.screenshot(path=str(screenshot), full_page=True)
                results['screens'].append({'width': width, 'no_overflow': True, 'logo_loaded': True, 'faq_opens': True, 'page_errors': 0, 'failed_assets': 0, 'screenshot': screenshot.name})
                page.close()
            browser.close()
    finally:
        server.should_exit = True
        worker.join(timeout=10)
    (output / 'independent-checks.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.output.resolve(), args.source.resolve()), ensure_ascii=False))
