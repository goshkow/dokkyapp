# Докки

Настольный клиент Докки и локальная среда разработки с серверным AI. Бэкенд на
Python/FastAPI крутит агентный цикл и локальные инструменты, интерфейс на
React/TypeScript живёт в окне PySide6. Код общий для Windows и macOS, всё платформенное
(окно, хранение токена, оболочка агента, автозапуск) лежит в отдельных модулях.

Лицензия: [PolyForm Noncommercial 1.0.0](LICENSE) - исходники открыты для чтения, личного
и некоммерческого использования и правок, коммерческое использование без разрешения автора
не допускается.

Сторонние компоненты и их лицензии: [THIRD_PARTY.md](THIRD_PARTY.md), в самом приложении -
Настройки -> О приложении -> Open Source Licenses.

## Запуск

Нужны Windows или macOS 12+, Python 3.11+ и аккаунт Докки.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m playwright install chromium
koddi
```

На macOS то же самое:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m playwright install chromium
koddi
```

В приложении нажмите **Войти с помощью Докки** и закончите вход в браузере. Свои серверы
моделей, ручной ввод ключа и локальные LLM не поддерживаются.

Настройки, история разработки и токен лежат вне исходников: `%LOCALAPPDATA%\Koddi` на
Windows (токен под DPAPI) и `~/Library/Application Support/Koddi` на macOS (ключ шифрования
токена - файл `dokky-session.key` с правами 0600).

Чтобы посмотреть интерфейс в браузере, без окна:

```bash
koddi-backend
```

Дальше откройте `http://127.0.0.1:8765/ui/`. Бэкенд слушайте только на loopback: у его
инструментов есть доступ к выбранным рабочим папкам.

## Что умеет

- Серверные чаты Докки: поиск, потоковые ответы, источники, вложения, библиотека.
- Отдельные локальные проекты и чаты разработки.
- Свой агентный цикл с native tool calls, без жёсткого лимита шагов.
- Чтение, поиск и правка файлов, команды, проверки, Git, браузер, веб-поиск.
- Планирование без записи файлов и запуск сохранённого плана.
- Разрешения, остановка задачи, журнал действий, просмотр изменений и откат.
- Правила `AGENTS.md`, MCP, навыки, память проекта, сжатие длинной истории.
- Computer Use: включается отдельно в Настройки -> Система, каждое действие подтверждается.
  На macOS нужна версия 14+ и разрешения Accessibility и Screen Recording для нативного
  помощника (собрать его: `python scripts/build_computer_helper.py`). Linux не поддерживается.

Ограничения контекста и генерации задаёт сервер Докки.

## Проверки

```bash
python -m pytest -q
python -m ruff check backend tests scripts
cd frontend && npm ci --ignore-scripts && npm run typecheck && npm test && npm run build
python scripts/smoke_next.py
python scripts/smoke_web.py
```

`smoke_web.py` поднимает отдельный бэкенд с временным профилем и тестовым AI.
`smoke_dokky.py` гоняет облачный интерфейс на `http://127.0.0.1:8878/ui/` с подменёнными
ответами API, платных запросов не шлёт. `smoke_live_plan.py` запускается только вручную: он
ходит в настоящий AI под вашим аккаунтом и тратит лимиты.

## Сборка

Windows:

```powershell
python scripts/build_with_budget.py --cpu-percent 75 --output dist_koddi_test
python scripts/smoke_package.py dist_koddi_test/web_host.dist/DokkyApp.exe
```

Лимит CPU действует на всё дерево компилятора (Windows Job Object). Подробности:
[docs/RELEASE_BUILD.md](docs/RELEASE_BUILD.md).

Установщик и переносная версия из готовой сборки (нужен .NET SDK):

```powershell
python scripts/build_installer.py --dist dist_koddi_test/web_host.dist --out dist_release
python scripts/smoke_installer.py dist_release/Dokky-0.2.0-Windows-x64-Setup.exe
```

Получатся `Dokky-0.2.0-Windows-x64-Setup.exe` и `Dokky-0.2.0-Windows-x64-portable.zip`.
Внутри обоих одна и та же раскладка: маленький `Dokky.exe`, который запускает текущую версию
из папки `app-0.2.0`, - так приложение потом сможет обновляться без прав администратора.

macOS:

```bash
python -m playwright install chromium-headless-shell
scripts/build_macos.sh dist_macos
```

Получится `dist_macos/Докки.app` (Nuitka). Без `SIGN_IDENTITY` подпись ad-hoc, и скачанное
приложение придётся разрешать вручную в настройках macOS. Для нормальной раздачи нужен
Developer ID и нотаризация, подробности в [docs/MACOS_DISTRIBUTION.md](docs/MACOS_DISTRIBUTION.md).

DMG из готового `.app`:

```bash
python3 -m venv build_dmg_tools/venv
build_dmg_tools/venv/bin/python -m pip install -r requirements-dmg.txt
build_dmg_tools/venv/bin/python scripts/build_macos_dmg.py \
  'dist_macos/Докки.app' --output dist_dmg/Dokky-0.2.0-macOS-arm64.dmg
```

Макет и перерисовка фона - в [assets/dmg](assets/dmg/README.md). Приложение в образ кладётся
как есть, без повторной подписи и нотаризации.

## Релиз

Версия задаётся в одном месте - `pyproject.toml`. Чтобы выпустить релиз, поднимите её,
допишите [CHANGELOG.md](CHANGELOG.md), дождитесь зелёных «Проверок» на main и запушьте тег:

```bash
git tag v0.2.0
git push origin v0.2.0
```

Тег запускает «Release Windows» и «Release macOS». Тесты там уже не гоняются, это работа
«Проверок». Релизные сборки собирают установщик, переносную версию и DMG под arm64 и x86_64,
проверяют установщик и кладут всё в один пре-релиз с текстом из
CHANGELOG. Тег, который не совпадает с версией, сборку остановит. Оба workflow можно
запустить и руками из Actions - тогда по умолчанию получится черновик.

Если в секретах репозитория есть `WINDOWS_SIGNING_CERT` (PFX в base64) и
`WINDOWS_SIGNING_PASSWORD`, exe подписываются. Без них всё собирается без подписи.

## Репозиторий

В Git лежат исходники, тесты, ресурсы интерфейса и скрипты сборки. Кэши, зависимости,
локальные профили, базы, токены, логи и сборки в него не попадают. Перед пушем:

```bash
python scripts/check_repository.py
```

Он смотрит версии файлов из индекса Git и ищет узнаваемые форматы секретов. Это не замена
ручной проверке `git diff --cached`.

Перед сборкой обновите каталог лицензий зависимостей: `python scripts/build_license_catalog.py`.
Про безопасность и разрешения агента: [SECURITY.md](SECURITY.md).
