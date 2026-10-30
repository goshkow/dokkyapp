__version__ = "0.2.4"

# до любых запусков: на Windows дочерние процессы не открывают окон консоли
from koddi import hidden_process as _hidden_process  # noqa: E402

_hidden_process.install()

# сборка Nuitka выкидывает numpy и pygments, а импорт выкинутого модуля падает не ImportError:
# pytesseract, PIL и httpx ловят только ImportError, и ocr.run ломался. Пустая запись даёт обычный ImportError
if "__compiled__" in globals():
    import sys as _sys

    for _name in ("numpy", "pygments"):
        _sys.modules.setdefault(_name, None)  # type: ignore[assignment]
