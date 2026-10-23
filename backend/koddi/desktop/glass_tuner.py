"""Окно «Настройка стекла»: ползунки меняют вид Liquid Glass в запущенном приложении сразу.

Подбор нужен потому, что скриншоты показывают стекло неактивного окна, а на экране у окна в фокусе оно темнее.
Значения сохраняются в glass-look.json в папке данных и действуют при следующих запусках.
"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

from koddi.desktop import macos_glass


class _Row:
    """Ползунок со значением справа: целые шаги, пересчёт в настоящее число функцией scale."""

    def __init__(self, low: int, high: int, scale, fmt, on_change):
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(low, high)
        self.label = QLabel()
        self.label.setMinimumWidth(52)
        self.scale, self.fmt = scale, fmt
        self.slider.valueChanged.connect(lambda _v: (self._text(), on_change()))
        self.box = QHBoxLayout()
        self.box.addWidget(self.slider, 1)
        self.box.addWidget(self.label)

    def _text(self) -> None:
        self.label.setText(self.fmt(self.scale(self.slider.value())))

    def set(self, value: int) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(value)
        self.slider.blockSignals(False)
        self._text()

    def value(self):
        return self.scale(self.slider.value())


class GlassTuner(QDialog):
    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.setWindowTitle("Настройка стекла")
        self.setMinimumWidth(440)
        self.setWindowFlag(Qt.WindowType.Tool, True)
        self._loading = False

        self.theme = QComboBox()
        self.theme.addItems(["Тёмная тема", "Светлая тема"])
        self.theme.currentIndexChanged.connect(self._load)
        self.style = QComboBox()
        self.style.addItems(["Обычное стекло", "Прозрачное стекло"])
        self.style.currentIndexChanged.connect(self._changed)
        self.red = _Row(0, 255, lambda v: v / 255, lambda v: f"{round(v * 255)}", self._changed)
        self.green = _Row(0, 255, lambda v: v / 255, lambda v: f"{round(v * 255)}", self._changed)
        self.blue = _Row(0, 255, lambda v: v / 255, lambda v: f"{round(v * 255)}", self._changed)
        self.alpha = _Row(0, 100, lambda v: v / 100, lambda v: f"{round(v * 100)}%", self._changed)
        self.base = _Row(30, 150, lambda v: v / 100, lambda v: f"{round(v * 100)}%", self._changed)
        self.radius = _Row(8, 64, lambda v: float(v), lambda v: f"{round(v)} pt", self._changed)

        form = QFormLayout()
        form.addRow("Что настраиваем", self.theme)
        form.addRow("Стиль", self.style)
        form.addRow("Оттенок: красный", self.red.box)
        form.addRow("Оттенок: зелёный", self.green.box)
        form.addRow("Оттенок: синий", self.blue.box)
        form.addRow("Сила оттенка", self.alpha.box)
        form.addRow("Подложка под стеклом", self.base.box)
        form.addRow("Скругление окна", self.radius.box)

        hint = QLabel(
            "Изменения видны сразу. Сильный оттенок темнит или светлит стекло; подложка - цвет приложения под плитками "
            "(меньше 100% - темнее). Окно приложения должно быть в фокусе, чтобы вы видели то, что видит пользователь."
        )
        hint.setWordWrap(True)
        save = QPushButton("Сохранить")
        save.clicked.connect(self._save)
        reset = QPushButton("Сбросить")
        reset.clicked.connect(self._reset)
        copy = QPushButton("Копировать значения")
        copy.clicked.connect(self._copy)
        self.status = QLabel("")
        buttons = QHBoxLayout()
        for button in (save, reset, copy):
            buttons.addWidget(button)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addLayout(buttons)
        layout.addWidget(self.status)
        self._load()

    def _key(self) -> str:
        return "dark" if self.theme.currentIndex() == 0 else "light"

    def _load(self, *_args) -> None:
        item = macos_glass.SETTINGS[self._key()]
        self._loading = True
        try:
            self.style.setCurrentIndex(int(item["style"]))
            red, green, blue, alpha = item["tint"]
            self.red.set(round(red * 255))
            self.green.set(round(green * 255))
            self.blue.set(round(blue * 255))
            self.alpha.set(round(alpha * 100))
            self.base.set(round(item["base"] * 100))
            self.radius.set(round(macos_glass.SETTINGS["radius"]))
        finally:
            self._loading = False

    def _changed(self, *_args) -> None:
        if self._loading:
            return
        item = macos_glass.SETTINGS[self._key()]
        item["style"] = int(self.style.currentIndex())
        item["tint"] = [self.red.value(), self.green.value(), self.blue.value(), self.alpha.value()]
        item["base"] = self.base.value()
        macos_glass.SETTINGS["radius"] = self.radius.value()
        self.host.refresh_glass()
        self.status.setText("")

    def _save(self) -> None:
        try:
            macos_glass.save_settings()
            self.status.setText(f"Сохранено: {macos_glass.settings_path()}")
        except OSError as exc:
            self.status.setText(f"Не удалось сохранить: {exc}")

    def _reset(self) -> None:
        macos_glass.reset_settings()
        self._load()
        self.host.refresh_glass()
        self.status.setText("Возвращены стандартные значения")

    def _copy(self) -> None:
        QApplication.clipboard().setText(json.dumps(macos_glass.SETTINGS, ensure_ascii=False, indent=2))
        self.status.setText("Значения скопированы в буфер обмена")
