# -*- coding: utf-8 -*-
"""Auto-start hook for DynamicCommandInput when FreeCAD GUI initializes."""

try:
    from PySide6 import QtCore
except ImportError:
    from PySide2 import QtCore


def _start_dynamic_input():
    import dynamic_input
    dynamic_input.install()


# Delay slightly so FreeCAD's main window and QApplication are fully ready
QtCore.QTimer.singleShot(1000, _start_dynamic_input)

