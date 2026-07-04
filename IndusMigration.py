"""
Indus Migration Tool — single entry point.
Double-click IndusMigration.exe (or run: python IndusMigration.py) to launch.

Migrates data from the desktop Indus ERP database (source) to the web Indus
ERP database (target). Opens on the dual-connection screen.
"""

import sys
from PyQt6.QtWidgets import QApplication
import ui.style as style
from ui.connection_window import ConnectionWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Indus Migration Tool")
    app.setOrganizationName("Indas Analytics Pvt. Ltd.")
    style.apply(app)
    window = ConnectionWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
