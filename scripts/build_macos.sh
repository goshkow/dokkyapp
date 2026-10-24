#!/usr/bin/env bash
# Сборка Докки.app через Nuitka на macOS: scripts/build_macos.sh [папка_сборки]
# Подпись: SIGN_IDENTITY="Developer ID Application: ..." (по умолчанию подпись без сертификата).
set -euo pipefail

cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python3}"
OUT="${1:-dist_macos}"
JOBS="${JOBS:-$(( $(sysctl -n hw.ncpu) * 3 / 4 ))}"
[ "$JOBS" -ge 1 ] || JOBS=1
VERSION="$("$PYTHON" -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
export NUITKA_CACHE_DIR="${NUITKA_CACHE_DIR:-${TMPDIR:-/tmp}/koddi-nuitka-cache}"

case "$OUT" in
  /*|*..*) echo "Папка сборки должна быть внутри проекта" >&2; exit 1 ;;
esac

# значок: Liquid Glass (.icon -> Assets.car + .icns) через actool из Xcode 26+; без него - обычный .icns из PNG
ICON_DIR="$(mktemp -d)"
ASSETS_CAR=""
if xcrun actool assets/DokkyApp.icon --compile "$ICON_DIR" --output-format human-readable-text \
    --output-partial-info-plist "$ICON_DIR/partial.plist" --app-icon DokkyApp --include-all-app-icons \
    --target-device mac --minimum-deployment-target 12.0 --platform macosx >/dev/null 2>&1 \
    && [ -f "$ICON_DIR/DokkyApp.icns" ] && [ -f "$ICON_DIR/Assets.car" ]; then
  ICNS="$ICON_DIR/DokkyApp.icns"
  ASSETS_CAR="$ICON_DIR/Assets.car"
else
  echo "actool с поддержкой .icon недоступен (нужен Xcode 26+): значок без Liquid Glass" >&2
  ICONSET="$ICON_DIR/DokkyApp.iconset"
  mkdir -p "$ICONSET"
  for size in 16 32 64 128 256 512; do
    sips -z "$size" "$size" assets/dokkyapp-512.png --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  done
  for size in 16 32 128 256; do
    cp "$ICONSET/icon_$((size * 2))x$((size * 2)).png" "$ICONSET/icon_${size}x${size}@2x.png"
  done
  cp "$ICONSET/icon_512x512.png" "$ICONSET/icon_512x512@2x.png"
  rm "$ICONSET/icon_64x64.png"
  ICNS="$ICON_DIR/DokkyApp.icns"
  iconutil -c icns "$ICONSET" -o "$ICNS"
fi

# русские системные пункты меню (Скрыть, Завершить, Службы) берутся из словаря Qt
QT_RU="$("$PYTHON" -c "from PySide6.QtCore import QLibraryInfo as L; print(L.path(L.LibraryPath.TranslationsPath))")/qtbase_ru.qm"
[ -f "$QT_RU" ] || { echo "Не найден qtbase_ru.qm" >&2; exit 1; }

rm -rf "$OUT/DokkyApp.app" "$OUT/Докки.app" "$OUT/web_host.app" "$OUT/web_host.dist"

"$PYTHON" scripts/build_computer_helper.py

# pygments и numpy тянутся только из консольной утилиты httpx и необязательных импортов PIL и pytesseract,
# а это пятая часть всего C-кода; LTO на скомпилированном Python почти ничего не даёт, а линковку тормозит
"$PYTHON" -m nuitka \
  --standalone \
  --macos-create-app-bundle \
  --macos-app-mode=gui \
  --macos-app-icon="$ICNS" \
  --macos-app-name="Докки" \
  --macos-app-version="$VERSION" \
  --macos-signed-app-name=com.goshkow.dokky \
  --macos-app-protected-resource="NSMicrophoneUsageDescription:Голосовой ввод в чате" \
  --assume-yes-for-downloads \
  --enable-plugin=pyside6 \
  --jobs="$JOBS" \
  --output-dir="$OUT" \
  --output-filename=DokkyApp \
  --lto=no \
  --include-package=koddi \
  --nofollow-import-to=httpx._main \
  --nofollow-import-to=pygments \
  --nofollow-import-to=numpy \
  --include-package-data=certifi \
  --include-module=_socket \
  --include-module=_ssl \
  --include-module=select \
  --include-module=unicodedata \
  --include-data-files="$QT_RU=qt_translations/qtbase_ru.qm" \
  --include-data-dir=backend/koddi/computer_use/native=koddi/computer_use/native \
  --include-data-dir=backend/koddi/licenses=koddi/licenses \
  --include-data-dir=prompts=prompts \
  --include-data-dir=locales=locales \
  --include-data-dir=backend/koddi/desktop/web=koddi/desktop/web \
  --include-data-dir=backend/koddi/desktop/web-next=koddi/desktop/web-next \
  --include-data-dir=backend/koddi/storage/migrations=koddi/storage/migrations \
  backend/koddi/desktop/web_host.py

# Nuitka называет бандл по главному файлу (web_host.app), приводим к DokkyApp.app
BUILT="$(find "$OUT" -maxdepth 1 -name '*.app' -print -quit)"
[ -n "$BUILT" ] || { echo "Nuitka не создал .app" >&2; exit 1; }
# папка называется по-русски: Dock и Finder берут имя из неё
APP="$OUT/Докки.app"
[ "$BUILT" = "$APP" ] || mv "$BUILT" "$APP"

mkdir -p "$APP/Contents/MacOS/koddi/computer_use/native"
cp backend/koddi/computer_use/native/dokky-computer-helper "$APP/Contents/MacOS/koddi/computer_use/native/"

"$PYTHON" scripts/finalize_macos_app.py "$APP" --identity "${SIGN_IDENTITY:--}" ${ASSETS_CAR:+--assets-car "$ASSETS_CAR"}
echo "Собрано: $APP"
