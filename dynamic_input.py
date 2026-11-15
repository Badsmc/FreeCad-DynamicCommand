# -*- coding: utf-8 -*-
"""
FreeCAD Dynamic Command Input (AutoCAD-style Dynamic Input)
Multi-tap a trigger key (default: Space x2) anywhere in the 3D view / main window
to open a floating command search bar near the cursor.
Type `dc-settings` to open the settings dialog.
"""

import time

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets

import FreeCAD
import FreeCADGui


PREF_PATH = "User parameter:BaseApp/Preferences/DynamicCommandInput"
TAP_INTERVAL = 0.35  # seconds between consecutive taps
MAX_RESULTS = 15
SETTINGS_CMD_ID = "DC-Settings"

# Widgets where typing should behave normally
TEXT_INPUT_TYPES = (
    QtWidgets.QLineEdit,
    QtWidgets.QTextEdit,
    QtWidgets.QPlainTextEdit,
    QtWidgets.QAbstractSpinBox,
)


def load_settings():
    """Load plugin settings from FreeCAD User Parameters."""
    params = FreeCAD.ParamGet(PREF_PATH)
    return {
        "trigger_key": params.GetInt("TriggerKey", int(QtCore.Qt.Key_Space)),
        "trigger_key_name": params.GetString("TriggerKeyName", "Space"),
        "tap_count": max(1, min(5, params.GetInt("TapCount", 2))),
        "show_icons": params.GetBool("ShowIcons", True),
    }


def save_settings(settings):
    """Save plugin settings to FreeCAD User Parameters."""
    params = FreeCAD.ParamGet(PREF_PATH)
    params.SetInt("TriggerKey", int(settings["trigger_key"]))
    params.SetString("TriggerKeyName", str(settings["trigger_key_name"]))
    params.SetInt("TapCount", int(settings["tap_count"]))
    params.SetBool("ShowIcons", bool(settings["show_icons"]))


def _key_to_name(key):
    """Return a human-readable name for a Qt key code."""
    seq = QtGui.QKeySequence(key)
    name = seq.toString()
    if name:
        return name
    if key == QtCore.Qt.Key_Space:
        return "Space"
    if key == QtCore.Qt.Key_Shift:
        return "Shift"
    if key == QtCore.Qt.Key_Control:
        return "Ctrl"
    if key == QtCore.Qt.Key_Alt:
        return "Alt"
    return f"Key({key})"


def _is_text_input_focused():
    """Return True if the currently focused widget is a text input."""
    widget = QtWidgets.QApplication.focusWidget()
    if widget is None:
        return False
    if isinstance(widget, TEXT_INPUT_TYPES):
        return True
    if isinstance(widget, QtWidgets.QComboBox) and widget.isEditable():
        return True
    return False


def _get_command_metadata(cmd_name, load_icon=True):
    """Fetch display title, tooltip, and icon for a FreeCAD command."""
    menu_text = ""
    tooltip = ""
    icon = QtGui.QIcon()

    try:
        cmd = FreeCADGui.Command.get(cmd_name)
        if cmd is not None:
            info = cmd.getInfo()
            menu_text = (info.get("menuText") or "").replace("&", "")
            tooltip = info.get("toolTip") or ""
            if load_icon:
                pixmap = info.get("pixmap")
                if pixmap:
                    icon = FreeCADGui.getIcon(pixmap)
    except Exception:
        pass

    return menu_text, tooltip, icon


class KeyCaptureButton(QtWidgets.QPushButton):
    """Button that captures the next pressed key as the trigger key."""

    keyCaptured = QtCore.Signal(int, str) if hasattr(QtCore, "Signal") else QtCore.pyqtSignal(int, str)

    def __init__(self, key_code, key_name, parent=None):
        super().__init__(parent)
        self._key_code = key_code
        self._key_name = key_name
        self._capturing = False
        self.setText(self._key_name)
        self.clicked.connect(self._start_capture)

    def _start_capture(self):
        self._capturing = True
        self.setText("Press a key... (Esc to cancel)")
        self.setFocus()

    def keyPressEvent(self, event):
        if not self._capturing:
            super().keyPressEvent(event)
            return

        key = event.key()
        if key == QtCore.Qt.Key_Escape:
            self._capturing = False
            self.setText(self._key_name)
            return

        self._key_code = int(key)
        self._key_name = _key_to_name(key)
        self._capturing = False
        self.setText(self._key_name)
        self.keyCaptured.emit(self._key_code, self._key_name)


class SettingsDialog(QtWidgets.QDialog):
    """Settings dialog for DynamicCommandInput."""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FreeCad-DynamicCommand — Settings")
        self.setMinimumWidth(360)
        self._settings = dict(settings)
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()

        self.key_btn = KeyCaptureButton(
            self._settings["trigger_key"],
            self._settings["trigger_key_name"],
            self,
        )
        self.key_btn.keyCaptured.connect(self._on_key_captured)
        form.addRow("Trigger key:", self.key_btn)

        self.tap_spin = QtWidgets.QSpinBox(self)
        self.tap_spin.setRange(1, 5)
        self.tap_spin.setValue(self._settings["tap_count"])
        form.addRow("Tap count:", self.tap_spin)

        self.icons_check = QtWidgets.QCheckBox("Show command icons", self)
        self.icons_check.setChecked(self._settings["show_icons"])
        form.addRow("", self.icons_check)

        layout.addLayout(form)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_key_captured(self, key_code, key_name):
        self._settings["trigger_key"] = key_code
        self._settings["trigger_key_name"] = key_name

    def get_settings(self):
        self._settings["tap_count"] = self.tap_spin.value()
        self._settings["show_icons"] = self.icons_check.isChecked()
        return self._settings


class DynamicInputPopup(QtWidgets.QFrame):
    """Floating AutoCAD-style command input popup with live fuzzy/token search."""

    def __init__(self, watcher, parent=None):
        super().__init__(
            parent,
            QtCore.Qt.Popup | QtCore.Qt.FramelessWindowHint | QtCore.Qt.NoDropShadowWindowHint,
        )
        self.setObjectName("DynamicInputPopup")
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, False)
        self.setMinimumWidth(420)
        self.setMaximumWidth(540)

        self._watcher = watcher
        self._commands_cache = []
        self._build_ui()
        self._apply_style()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self.input_edit = QtWidgets.QLineEdit(self)
        self.input_edit.setPlaceholderText("Command (e.g. partdesign_body, DC-Settings)...")
        self.input_edit.textChanged.connect(self._on_text_changed)
        self.input_edit.installEventFilter(self)
        layout.addWidget(self.input_edit)

        self.results_list = QtWidgets.QListWidget(self)
        self.results_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.results_list.setIconSize(QtCore.QSize(18, 18))
        self.results_list.itemActivated.connect(self._execute_item)
        self.results_list.itemClicked.connect(self._execute_item)
        layout.addWidget(self.results_list)

    def _apply_style(self):
        self.setStyleSheet(
            """
            #DynamicInputPopup {
                background-color: #1e222a;
                border: 1px solid #3e4451;
                border-radius: 6px;
            }
            QLineEdit {
                background-color: #282c34;
                color: #abb2bf;
                border: 1px solid #528bff;
                border-radius: 4px;
                padding: 6px 8px;
                font-size: 13px;
                selection-background-color: #3e4451;
            }
            QListWidget {
                background-color: #21252b;
                color: #abb2bf;
                border: 1px solid #3e4451;
                border-radius: 4px;
                outline: none;
                font-size: 12px;
            }
            QListWidget::item {
                padding: 5px 8px;
                border-radius: 3px;
            }
            QListWidget::item:selected {
                background-color: #323842;
                color: #ffffff;
            }
            QListWidget::item:hover {
                background-color: #2c313c;
            }
            """
        )

    def _refresh_commands_cache(self):
        """Load all currently registered FreeCAD commands plus built-in dc-settings."""
        show_icons = self._watcher.settings["show_icons"]
        raw_cmds = sorted(FreeCADGui.listCommands())
        cache = [
            {
                "name": SETTINGS_CMD_ID,
                "name_lower": SETTINGS_CMD_ID.lower(),
                "menu_text": "FreeCad-DynamicCommand Settings",
                "menu_lower": "freecad-dynamiccommand settings",
                "tooltip": "Configure trigger key, tap count, and command icons",
                "icon": QtGui.QIcon(),
            }
        ]
        for name in raw_cmds:
            menu_text, tooltip, icon = _get_command_metadata(name, load_icon=show_icons)
            cache.append({
                "name": name,
                "name_lower": name.lower(),
                "menu_text": menu_text,
                "menu_lower": menu_text.lower(),
                "tooltip": tooltip,
                "icon": icon,
            })
        self._commands_cache = cache

    def open_at_cursor(self):
        """Show the popup near the current mouse cursor position."""
        self._refresh_commands_cache()
        self.input_edit.clear()
        self._populate_results("")

        cursor_pos = QtGui.QCursor.pos()
        screen = QtWidgets.QApplication.screenAt(cursor_pos)
        if screen is None:
            screen = QtWidgets.QApplication.primaryScreen()
        geo = screen.availableGeometry()

        self.adjustSize()
        x = cursor_pos.x() + 12
        y = cursor_pos.y() + 12

        if x + self.width() > geo.right():
            x = max(geo.left(), geo.right() - self.width() - 8)
        if y + self.height() > geo.bottom():
            y = max(geo.top(), cursor_pos.y() - self.height() - 12)

        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self.input_edit.setFocus(QtCore.Qt.PopupFocusReason)

    def _on_text_changed(self, text):
        self._populate_results(text)

    def _populate_results(self, query):
        self.results_list.clear()
        q = query.strip().lower()
        show_icons = self._watcher.settings["show_icons"]

        if not q:
            matches = self._commands_cache[:MAX_RESULTS]
        else:
            tokens = [t for t in q.replace("_", " ").split() if t]
            scored = []
            for item in self._commands_cache:
                name_l = item["name_lower"]
                menu_l = item["menu_lower"]
                combined = f"{name_l} {menu_l}"

                if not all(tok in combined for tok in tokens):
                    continue

                if name_l.startswith(q):
                    score = 0
                elif q in name_l:
                    score = 1
                elif all(tok in name_l for tok in tokens):
                    score = 2
                else:
                    score = 3

                scored.append((score, len(name_l), item))

            scored.sort(key=lambda x: (x[0], x[1], x[2]["name_lower"]))
            matches = [entry[2] for entry in scored[:MAX_RESULTS]]

        for item in matches:
            label = item["name"]
            if item["menu_text"] and item["menu_text"].lower() != item["name_lower"]:
                label = f"{item['name']}  —  {item['menu_text']}"

            icon = item["icon"] if show_icons else QtGui.QIcon()
            list_item = QtWidgets.QListWidgetItem(icon, label)
            list_item.setData(QtCore.Qt.UserRole, item["name"])
            if item["tooltip"]:
                list_item.setToolTip(item["tooltip"])
            self.results_list.addItem(list_item)

        if self.results_list.count() > 0:
            self.results_list.setCurrentRow(0)
            row_height = self.results_list.sizeHintForRow(0)
            visible_rows = min(self.results_list.count(), 10)
            self.results_list.setFixedHeight(max(32, row_height * visible_rows + 8))
            self.results_list.show()
        else:
            self.results_list.hide()

        self.adjustSize()

    def _execute_item(self, item):
        if item is None:
            return
        cmd_name = item.data(QtCore.Qt.UserRole)
        self.hide()
        if cmd_name == SETTINGS_CMD_ID:
            self._watcher.open_settings()
            return
        if cmd_name:
            try:
                FreeCADGui.runCommand(cmd_name)
            except Exception as exc:
                FreeCAD.Console.PrintError(
                    f"[DynamicCommandInput] Failed to run '{cmd_name}': {exc}\n"
                )

    def eventFilter(self, obj, event):
        if obj is self.input_edit and event.type() == QtCore.QEvent.KeyPress:
            key = event.key()
            if key == QtCore.Qt.Key_Escape:
                self.hide()
                return True
            if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                current = self.results_list.currentItem()
                if current is not None:
                    self._execute_item(current)
                return True
            if key == QtCore.Qt.Key_Down:
                count = self.results_list.count()
                if count > 0:
                    row = (self.results_list.currentRow() + 1) % count
                    self.results_list.setCurrentRow(row)
                return True
            if key == QtCore.Qt.Key_Up:
                count = self.results_list.count()
                if count > 0:
                    row = (self.results_list.currentRow() - 1) % count
                    self.results_list.setCurrentRow(row)
                return True
            if key == QtCore.Qt.Key_Tab:
                current = self.results_list.currentItem()
                if current is not None:
                    cmd_name = current.data(QtCore.Qt.UserRole)
                    if cmd_name:
                        self.input_edit.setText(cmd_name)
                return True
        return super().eventFilter(obj, event)


class TriggerWatcher(QtCore.QObject):
    """Global event filter that opens DynamicInputPopup on N consecutive key taps."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = load_settings()
        self._tap_times = []
        self._key_is_down = False
        self._popup = None

    def _get_popup(self):
        if self._popup is None:
            main_win = FreeCADGui.getMainWindow()
            self._popup = DynamicInputPopup(self, main_win)
        return self._popup

    def open_settings(self):
        main_win = FreeCADGui.getMainWindow()
        dlg = SettingsDialog(self.settings, main_win)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            self.settings = dlg.get_settings()
            save_settings(self.settings)
            self._tap_times.clear()

    def eventFilter(self, obj, event):
        etype = event.type()
        if etype == QtCore.QEvent.KeyRelease:
            if event.key() == self.settings["trigger_key"] and not event.isAutoRepeat():
                self._key_is_down = False
            return False

        if etype == QtCore.QEvent.KeyPress:
            if event.key() == self.settings["trigger_key"] and not event.isAutoRepeat():
                if self._key_is_down:
                    return False
                self._key_is_down = True

                if self._popup is not None and self._popup.isVisible():
                    return False
                if _is_text_input_focused():
                    return False

                now = time.monotonic()
                tap_count = self.settings["tap_count"]
                self._tap_times = [t for t in self._tap_times if now - t <= TAP_INTERVAL]
                self._tap_times.append(now)

                if len(self._tap_times) >= tap_count:
                    self._tap_times.clear()
                    QtCore.QTimer.singleShot(0, self._get_popup().open_at_cursor)
                    return True
        return False


_WATCHER_ATTR = "_dynamic_command_input_watcher"


def install():
    """Install the trigger watcher on QApplication (idempotent)."""
    app = QtWidgets.QApplication.instance()
    if app is None:
        return False

    if getattr(app, _WATCHER_ATTR, None) is not None:
        return True

    watcher = TriggerWatcher(app)
    app.installEventFilter(watcher)
    setattr(app, _WATCHER_ATTR, watcher)
    key_name = watcher.settings["trigger_key_name"]
    taps = watcher.settings["tap_count"]
    FreeCAD.Console.PrintMessage(
        f"[FreeCad-DynamicCommand] Enabled ({key_name} x{taps} to open, type 'DC-Settings' for settings).\n"
    )
    return True
