# Докки для macOS: сборка и DMG

Сейчас приложение собирается как `.app` с ad-hoc подписью и упаковывается в оформленный
DMG. Developer ID и нотаризации нет, поэтому скачанное приложение macOS по умолчанию
блокирует. Ниже - как всё устроено и что нужно для нормальной раздачи.

## Как собирается

- `scripts/build_macos.sh` собирает standalone `.app` через Nuitka с плагином PySide6 и
  кладёт внутрь Swift-помощник, интерфейс, переводы, промпты и миграции.
- `scripts/finalize_macos_app.py` дописывает `Info.plist`, включает headless Chromium для
  Playwright и подписывает bundle. Без `SIGN_IDENTITY` подпись ad-hoc (`-`), с ним добавляются
  `--options runtime --timestamp`. Тип сертификата скрипт не проверяет.
- Интерфейс (`web-next`) берётся готовым из Git, Vite при сборке приложения не запускается.
  Перед релизом пересоберите `frontend/` и проверьте, что результат закоммичен.
- Основное окно на macOS - нативный WKWebView (`desktop/native_web.py`). QtWebEngine нужен
  только Windows, но его модули пока всё равно попадают в bundle.

## Что не так с подписью

1. Нет Developer ID Application и нотаризации. Для раздачи нужен платный аккаунт Apple
   Developer Program, без него Gatekeeper не пустит приложение без ручного разрешения в
   настройках: начиная с Sequoia Control-click это уже не обходит.
2. Подпись идёт одним `codesign --force --deep`, и все вложенные процессы (main, Node, Swift
   helper, QtWebEngineProcess) получают одинаковые entitlements. У Chromium из
   `Contents/Resources/playwright-browsers/` остаётся родная ad-hoc подпись без entitlements.
   Для релиза нужна подпись изнутри наружу с отдельными правами на каждый исполняемый файл.
3. `LSMinimumSystemVersion` в `Info.plist` сейчас 12.0, но Qt в bundle собран под macOS 14.4,
   Swift-помощник - под 14.0. Реальный минимум надо просканировать по всем Mach-O и
   выставить честно. Computer Use отдельно проверяет macOS 14.
4. Зависимости ставятся из диапазонов `pyproject.toml`, lock-файла под macOS нет. Версия
   сборки зашита как `0.2.1`.

Даже с подписью останутся отдельные системные разрешения на микрофон, запись экрана и
управление компьютером: это другой механизм.

## Как выпустить с подписью

1. Собрать `.app` в новую папку, все правки plist и иконок сделать до подписи.
2. Подписать нативные библиотеки и вложенные исполняемые файлы изнутри наружу, потом сам
   `.app`: Developer ID Application, secure timestamp, Hardened Runtime. Entitlements -
   минимальные для каждого процесса, без `get-task-allow`. `--deep` оставить только для
   проверки.
3. Собрать DMG (см. ниже) и подписать его тем же сертификатом. Developer ID Installer нужен
   только для `.pkg`.
4. Отправить именно DMG в `notarytool`, дождаться `Accepted` и прочитать журнал. Если
   вышел таймаут, проверять тот же submission ID, а не слать файл заново.
5. Прикрепить билет, проверить его и посчитать SHA-256 уже после этого.

```bash
codesign --force --sign "$SIGN_IDENTITY" --timestamp "$DMG"
xcrun notarytool submit "$DMG" --keychain-profile dokky-notary --wait --output-format json
xcrun notarytool log "$SUBMISSION_ID" --keychain-profile dokky-notary "$NOTARY_LOG"
# дальше только после Accepted и просмотра журнала
xcrun stapler staple "$DMG"
xcrun stapler validate "$DMG"
codesign --verify --strict "$DMG"
spctl --assess --type open --context context:primary-signature --verbose=4 "$DMG"
shasum -a 256 "$DMG"
```

`dokky-notary` - профиль в Keychain, `SIGN_IDENTITY` - Developer ID Application. Секреты в Git
не кладём. Нотаризуется и стейплится внешний DMG, отдельная нотаризация `.app` не нужна.
Команды с `altool` не использовать: Apple отключила их в 2023.

## DMG

Образ делает `scripts/build_macos_dmg.py` через [dmgbuild](https://github.com/dmgbuild/dmgbuild)
(версия закреплена в `requirements-dmg.txt`). Фон рисует `scripts/make_macos_dmg_artwork.py`,
макет и шрифт лежат в `assets/dmg/`, подробности - в `assets/dmg/README.md`.

Макет: окно 800×560, значки по 112 pt, приложение слева `(220, 315)`, ссылка на `/Applications`
справа `(580, 315)`, между ними нарисованная стрелка. Ссылка - настоящий symlink, а не картинка
папки. Приложение копируется как есть, без повторной подписи. `hide_extensions` не
используется: `SetFile` добавлял FinderInfo и ломал strict-проверку подписи копии.

Файлы называются `Dokky-<версия>-macOS-arm64.dmg` и `Dokky-<версия>-macOS-x86_64.dmg`. Основной
executable сейчас arm64, не universal, так что Intel собирается отдельно.

Оформление проверено в настоящем Finder на одном Mac. Другие версии macOS, обычные (не
Retina) экраны и английская локализация не проверялись.

## Приёмка перед раздачей

- Инвентарь Mach-O: архитектуры, минимальная ОС, подпись, Team ID, timestamp, entitlements.
  Смотреть все slices, а не только main.
- `.app`: `codesign --verify --deep --strict`. Диагностика `syspolicy_check distribution` на
  скопированном приложении может ругаться на `Notary Ticket Missing`, даже когда нотаризован
  и застейплен сам DMG; это не значит, что Gatekeeper заблокирует.
- DMG: `stapler validate`, проверка подписи, `spctl --type open`.
- Главная проверка - чистая машина или снимок VM: скачать DMG в Safari, скопировать в
  «Программы», извлечь образ, открыть. Хватает обычного «Открыть», без «повреждено» и без
  похода в настройки. Повторить без сети до первого открытия, чтобы проверить stapling, и
  каждый раз откатывать снимок: Gatekeeper кэширует решения.
- Проверить минимально поддерживаемую и свежую macOS, Apple Silicon и Intel отдельно.
  Приложение должно открываться без Python, Node и Qt в системе. Проверить вход через
  `koddi://`, выход, запуск Playwright и Qt-помощников.
