# Сторонние компоненты

Интерфейс Докки перенесён и доработан из проекта
[goshkow/dokky](https://github.com/goshkow/dokky). Этот файл не назначает лицензию самому
проекту, он только перечисляет чужое.

Локальные библиотеки идут со своими лицензиями:

| Компонент | Лицензия в репозитории |
| --- | --- |
| Marked | `backend/koddi/desktop/web/vendor/marked-LICENSE` |
| DOMPurify | `backend/koddi/desktop/web/vendor/purify-LICENSE` |
| Highlight.js | `backend/koddi/desktop/web/vendor/highlight-LICENSE` |
| Lucide | `backend/koddi/desktop/web/vendor/lucide-LICENSE` |
| Inter | `backend/koddi/desktop/web/assets/Inter-LICENSE` |
| Unbounded (подмножество кириллицы, SIL OFL 1.1) | `backend/koddi/desktop/web-next/assets/fonts/OFL-Unbounded.txt` |
| KaTeX 0.19.0 (MIT, шрифты KaTeX_* под SIL OFL) | `backend/koddi/desktop/web-next/assets/licenses/katex-LICENSE` |
| Leaflet 1.9.4 (BSD-2-Clause) | `backend/koddi/desktop/web-next/assets/licenses/leaflet-LICENSE` |

Плитки карты - © участники OpenStreetMap (ODbL). Приложение берёт их через свой
сервер-посредник и подписывает источник прямо на карте.

См. также `backend/koddi/desktop/web/vendor/THIRD_PARTY.md`. Python-зависимости описаны в
`pyproject.toml` и ставятся со своими лицензиями. Если раздаёте бинарную сборку, прикладывайте
их лицензионные файлы, включая условия PySide6/Qt и встроенного браузерного движка.

[Kilo Code](https://github.com/kilo-org/kilocode) был архитектурным референсом, цикл агента
написан с нуля. Код OpenCode, Aider, mini-SWE-agent, UFO, Terminator и OpenAI Codex не
переносился.

Часть стандартных навыков (проектирование API, архитектура микросервисов, RAG, продающие
тексты, оптимизация конверсии, постеры и визуальный дизайн) переписана по открытым навыкам
из jeffallan/claude-skills, coreyhaines31/marketingskills и anthropics/skills (через каталог
claudeskills.ru). Тексты заново написаны на русском под инструменты Докки. Исходные
лицензии и пометка об источниках лежат в `third_party/skill-sources`.

Каталог `backend/koddi/licenses/catalog.json` содержит версии и тексты лицензий зависимостей
из окружения сборки. Он показывается в Настройки -> О приложении -> Open Source Licenses и
пересобирается командой `python scripts/build_license_catalog.py` на целевой платформе.
Qt/Chromium, нативные библиотеки, браузер Playwright и готовую сборку под Windows нужно
сверять с тем, что реально попало в дистрибутив: каталог эту проверку не заменяет.
