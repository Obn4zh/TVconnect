import logging
import logging.handlers
import sys

from .paths import log_dir


def setup_logging(verbose: bool = False) -> str:
    path = log_dir() / "tvconnect.log"
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s")

    fh = logging.handlers.RotatingFileHandler(path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    root.addHandler(fh)

    if sys.stderr is not None:  # None in windowed PyInstaller builds
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        ch.setLevel(logging.DEBUG if verbose else logging.INFO)
        root.addHandler(ch)

    def excepthook(exc_type, exc, tb):
        logging.getLogger("tvconnect").critical("Unhandled exception", exc_info=(exc_type, exc, tb))

    sys.excepthook = excepthook
    logging.getLogger("tvconnect").info("Logging to %s", path)
    return str(path)
