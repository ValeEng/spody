"""Entry point: build the QApplication, show the main window, run the loop."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("SpOdy")
    app.setOrganizationName("SpOdy")
    app.setOrganizationDomain("spody")

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
