# Сборка под Windows

Описано, как собрать проверяемый кандидат релиза. Готовый EXE - это кандидат для закрытых
тестов, а не публичный релиз: подписи, установщика и канала обновлений пока нет.

## Чистое окружение
Нужны Windows x64, Python 3.11 и установленный MSVC/Windows SDK. Точная версия
компилятора/SDK фиксируется вместе с результатом сборки. Не использовать личный
профиль приложения и не включать его в архив.

1. Создать и активировать `.venv` в чистом checkout нужного commit.
2. Установить согласованные версии bootstrap-инструментов. Текущий рабочий
   setuptools 65.5.0 не соответствует build-system >=69 и не годится как
   подтверждение воспроизводимой чистой сборки. Nuitka-кандидат собирается
   напрямую, без этого backend; это ограничение записано в inventory.
3. Установить `requirements-windows-py311.lock` с `--require-hashes`.
   Lock предназначен для Windows/Python 3.11, включая Python dev-инструменты,
   но не заменяет фиксацию компилятора, SDK, Python и bootstrap.
4. Установить сам проект без повторного разрешения зависимостей и скрытого
   build isolation. Любое несоответствие версиям исправлять до сборки, а не
   отключением проверок.

## Проверки
```powershell
python -m pip check
python -m pytest -q
python -m ruff check backend tests scripts --select E4,E7,E9,F
python scripts/check_repository.py --working-tree
python scripts/smoke_web.py
python scripts/smoke_dokky.py
python scripts/smoke_queue.py
python scripts/smoke_layout.py
python scripts/smoke_native.py --windows
```

Тесты используют временные профили и тестовые генераторы. Они не подтверждают
реальную авторизацию сервера или качество конкретной модели. Платные E2E
выполняются отдельно по явно согласованному бюджету.

## Сборка кандидата
```powershell
python scripts/release_inventory.py --hash-lock
python -m pip_audit -r requirements-windows-py311.lock --no-deps --disable-pip --progress-spinner off
python scripts/build_with_budget.py --cpu-percent 75 --output dist_koddi_release_candidate --compiler msvc
python scripts/verify_release_candidate.py dist_koddi_release_candidate/web_host.dist
python scripts/smoke_package.py dist_koddi_release_candidate/web_host.dist/DokkyApp.exe
```

`smoke_package.py` сначала проверяет основной React-интерфейс в EXE, затем агентный
сценарий на `KODDI_UI=legacy` (он написан под функции прежнего интерфейса).

Использовать новый output, чтобы сохранить предыдущую рабочую сборку.
Запуск EXE из standalone-папки требует соседних файлов, не только DokkyApp.exe.
После сборки проверить исходные хеши и содержимое embedded web/migrations,
записать SHA256 EXE и итог packaged smoke. Не подменять неуспешную проверку
запуском старого EXE.

`verify_release_candidate.py` сверяет набор и хеши web/migrations/prompts/locales,
отклоняет ссылки и узнаваемые пользовательские данные, формирует хеши всех
файлов поставки. Доверенный набор CA certifi не считается приватным ключом.
Это проверка состава, не проверка подписи издателя или полноты лицензий.

`dist_release_evidence` содержит Python SBOM, лицензии, версии, source hashes
и отчёт аудита. Эти данные описывают окружение сборки и не являются полным
перечнем бинарных компонентов Qt/Chromium/JS. Их лицензии и состав сверяются
отдельно до публикации.

## Установщик и переносная версия Windows
Раскладка одна и та же у установленной и у переносной версии - так её проще обновлять:
```
Dokky.exe       маленький запускальщик: смотрит в current.json и запускает нужную версию
current.json    {"v":1,"version":"0.2.0","dir":"app-0.2.0","previous":null}
app-0.2.0\      вся сборка
updates\        сюда будут падать обновления
.koddi-data\    только у переносной версии: чаты и настройки рядом с программой
```
- `python scripts/build_installer.py --dist <папка с DokkyApp.exe> --out dist_release` собирает
  `Dokky-<версия>-Windows-x64-Setup.exe`, `Dokky-<версия>-Windows-x64-portable.zip` и
  `SHA256SUMS-windows.txt`. Для CI шаги можно гонять по одному: `tree` раскладывает файлы,
  потом их подписывают, `package` упаковывает, `sums` пересчитывает суммы после подписи.
- Нужен .NET SDK. Сам установщик и `Dokky.exe` работают на .NET Framework 4.8, который уже
  есть в Windows 10 и 11, так что ставить ничего не надо. Программа лежит архивом в хвосте
  exe установщика (`installer/windows/Payload.cs`). Упаковщиков вроде UPX нет специально -
  на них часто ругаются антивирусы.
- По умолчанию ставим в `%LOCALAPPDATA%\Programs\Dokky` только для текущего пользователя,
  без прав администратора. В Program Files тоже можно: установщик спросит права и поставит
  для всех, но обновляться такая установка будет только новым установщиком.
- Ярлыки, «Установленные приложения», автозапуск и ссылки `koddi://` ведут на `Dokky.exe`.
- Повторная установка кладёт новую версию рядом и только потом переключает `current.json`.
  Удаление убирает только своё: `Dokky.exe`, `current.json`, папки `app-*` и `updates\`.
  Чаты и настройки в `%LOCALAPPDATA%\Koddi` остаются, если не попросить их удалить.
- `scripts/smoke_installer.py <setup.exe>` проверяет установку, обновление поверх, удаление
  и запуск через `Dokky.exe`. Под администратором - ещё и установку в Program Files.
- Подпись в CI включается секретами `WINDOWS_SIGNING_CERT` и `WINDOWS_SIGNING_PASSWORD`
  (`scripts/sign_windows.ps1`). Без подписи SmartScreen будет предупреждать.

## Чего ещё не хватает для полноценного выпуска
- Подписи: на Windows - сертификата для exe, на macOS - Developer ID и нотаризации.
  Без них система при первом запуске предупреждает.
- Автообновления внутри приложения. Раскладка под него уже готова, но сама проверка и
  установка обновлений пока выключены.
- Ручной проверки на чистых Windows 10/11 и macOS разных версий: установка, удаление,
  обновление поверх.
