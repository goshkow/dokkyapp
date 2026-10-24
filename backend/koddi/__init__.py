__version__ = "0.2.1"

# до любых запусков: на Windows дочерние процессы не открывают окон консоли
from koddi import hidden_process as _hidden_process  # noqa: E402

_hidden_process.install()
