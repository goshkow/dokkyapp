# Оформление DMG Докки

Макет 800×560 pt нарисован в `scripts/make_macos_dmg_artwork.py`: светлый фон,
синяя стрелка, существующий значок `assets/dokkyapp-512.png`, текст Unbounded.
Генеративные изображения не использовались. В SVG текст переведён в контуры;
PNG 800×560 и 1600×1120 имеют разрешения 72 и 144 dpi. `dmgbuild` объединяет их
в многостраничный TIFF с правильным размером для Retina.

Расположение настоящих значков Finder хранится в `layout.json`. Ссылка
«Программы» ведёт на `/Applications`. Не устанавливайте `hide_extensions` на
`.app`: SetFile добавляет FinderInfo, из-за которого строгая проверка подписи
скопированного bundle в этой среде перестаёт проходить. Finder скрывает `.app`
согласно своим обычным настройкам.

## Шрифт

Unbounded из [официального репозитория](https://github.com/googlefonts/unbounded),
коммит `f3ec43228a864a72487e41552e2140efab9884ea`,
`fonts/variable/Unbounded[wght].ttf`, SIL Open Font License 1.1.
Оригинальная лицензия находится в `fonts/OFL.txt`; файл шрифта не изменялся.

SHA-256 `fonts/Unbounded.ttf`:
`323b511be380c8d474ef030686b71aedde501f8d9cd46da558b7c40454372c3f`.

## Воспроизведение

Рисование использует существующий PySide6 из окружения проекта; упаковщик
устанавливается отдельно и не добавляется в runtime приложения:

```bash
.venv/bin/python scripts/make_macos_dmg_artwork.py
.venv/bin/python -m venv build_dmg_tools/venv
build_dmg_tools/venv/bin/python -m pip install -r requirements-dmg.txt
build_dmg_tools/venv/bin/python scripts/build_macos_dmg.py \
  'dist_macos/Докки.app' \
  --output dist_dmg/Dokky-0.2.0-macOS-arm64-design.dmg
```

Выходной файл и JSON должны иметь новые имена: существующие артефакты не
перезаписываются. Скрипт проверяет подпись исходной и скопированной `.app`,
хеш executable, ссылку «Программы» и целостность DMG. Оригинальная `.app`
не меняется. Рядом создаётся JSON с версией, архитектурой и SHA-256 образа.

Упаковка не выполняет Developer ID signing, notarization или публикацию.
Для выпуска необходимы шаги из `docs/MACOS_DISTRIBUTION.md`. Образ с текущей
ad-hoc сборкой предназначен для приёмки оформления и состава поставки.
