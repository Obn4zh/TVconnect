import argparse
import logging
import sys

from PySide6.QtWidgets import QApplication

from . import __version__
from .logging_setup import setup_logging
from .settings import load_settings


def main() -> int:
    ap = argparse.ArgumentParser(prog="tvconnect", description="TVconnect desktop client")
    ap.add_argument("-v", "--verbose", action="store_true", help="debug output to console")
    args, qt_args = ap.parse_known_args()

    setup_logging(args.verbose)
    log = logging.getLogger("tvconnect")
    log.info("TVconnect desktop %s, Python %s, %s", __version__, sys.version.split()[0], sys.platform)

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("TVconnect")
    app.setApplicationVersion(__version__)

    from .ui.main_window import MainWindow

    win = MainWindow(load_settings())
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
