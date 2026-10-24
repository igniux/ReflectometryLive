"""Palette taken from the user's SHG Portable spectrometer program."""

from PySide6.QtGui import QColor, QPalette


BACKGROUND = "#282b30"       # rgb(40, 43, 48): previous QMainWindow
BUTTON = "#3c3f44"           # rgb(60, 63, 68): previous QPushButton
HOVER = "#46494e"            # rgb(70, 73, 78): previous button hover
INPUT = "#32353a"            # rgb(50, 53, 58): previous QLineEdit
TEXT = "#99aab5"             # rgb(153, 170, 181): previous labels/buttons
INPUT_TEXT = "#dcdcdc"       # rgb(220, 220, 220): previous input text
BORDER = "#646464"           # rgb(100, 100, 100): previous widget borders
STOP = "#c83232"             # rgb(200, 50, 50): previous Stop button
STOP_HOVER = "#dc4646"       # rgb(220, 70, 70): previous Stop hover
STOP_BORDER = "#961e1e"      # rgb(150, 30, 30)
PLOT_BACKGROUND = "#000000"  # previous pyqtgraph default background
MEASURED = "#ffff00"         # previous plot.py: pen='y'
FIT = "#24ad11"              # previous progress bar green
GOOD = FIT
WARNING = MEASURED
ERROR = STOP_HOVER


STYLESHEET = """
    QWidget { background-color: #282b30; color: #99aab5; }
    QMainWindow, QScrollArea, QStatusBar { background-color: #282b30; }
    QLabel { color: #99aab5; background: transparent; }
    QGroupBox { background-color: #282b30; color: #99aab5;
                border: 1px solid #646464; border-radius: 5px;
                margin-top: 13px; padding: 13px 9px 8px 9px; font-weight: 600; }
    QGroupBox::title { subcontrol-origin: margin; left: 12px; }
    QPushButton { padding: 8px 12px; background-color: #3c3f44; color: #99aab5;
                  border: 1px solid #646464; border-radius: 5px; }
    QPushButton:hover { background-color: #46494e; color: white; }
    QPushButton:pressed { background-color: #32353a; }
    QPushButton:disabled { background-color: #282b30; color: #646464; border-color: #46494e; }
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
        background-color: #32353a; color: #dcdcdc; border: 1px solid #646464;
        border-radius: 3px; padding: 5px; min-height: 20px;
        selection-background-color: #46494e; selection-color: white;
    }
    QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {
        background-color: #282b30; color: #646464; border-color: #46494e;
    }
    QComboBox QAbstractItemView { background-color: #32353a; color: #dcdcdc;
                                selection-background-color: #46494e; selection-color: white; }
    QCheckBox { background: transparent; }
    QCheckBox:disabled { color: #646464; }
    QTabWidget::pane { border: 1px solid #646464; background-color: #282b30; }
    QTabBar::tab { background-color: #32353a; color: #99aab5; padding: 8px 12px;
                  border: 1px solid #646464; }
    QTabBar::tab:selected { background-color: #3c3f44; color: #dcdcdc; }
    QTabBar::tab:hover { background-color: #46494e; color: white; }
    QScrollBar:vertical { background: #282b30; width: 12px; border: none; }
    QScrollBar:horizontal { background: #282b30; height: 12px; border: none; }
    QScrollBar::handle { background: #3c3f44; border-radius: 5px; min-height: 24px; min-width: 24px; }
    QScrollBar::handle:hover { background: #46494e; }
    QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
    QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
    QSplitter::handle { background-color: #646464; }
    QToolTip { background: #32353a; color: #dcdcdc; border: 1px solid #646464; }
"""


def apply_dark_palette(application):
    # Fusion respects palette colours for controls and Qt file dialogs on Windows.
    application.setStyle("Fusion")
    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window: BACKGROUND,
        QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: INPUT,
        QPalette.ColorRole.AlternateBase: BUTTON,
        QPalette.ColorRole.ToolTipBase: INPUT,
        QPalette.ColorRole.ToolTipText: INPUT_TEXT,
        QPalette.ColorRole.Text: INPUT_TEXT,
        QPalette.ColorRole.Button: BUTTON,
        QPalette.ColorRole.ButtonText: TEXT,
        QPalette.ColorRole.BrightText: "#fffff0",
        QPalette.ColorRole.Link: FIT,
        QPalette.ColorRole.Highlight: HOVER,
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.PlaceholderText: TEXT,
    }
    for role, value in colors.items():
        palette.setColor(role, QColor(value))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(BORDER))
    application.setPalette(palette)


def dark_title_bar(window):
    """Ask Windows to match the native caption to the application's dark palette."""
    import ctypes
    import os
    from PySide6.QtWidgets import QApplication
    if os.name != "nt" or QApplication.platformName() != "windows":
        return
    try:
        enabled = ctypes.c_int(1)
        function = ctypes.WinDLL("dwmapi").DwmSetWindowAttribute
        function.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
        function.restype = ctypes.c_long
        function(int(window.winId()), 20, ctypes.byref(enabled), ctypes.sizeof(enabled))
        # Explicit caption colours also match the app when the OS uses light mode.
        for attribute, color in ((35, BACKGROUND), (36, TEXT), (34, BORDER)):
            colorref = ctypes.c_uint32(int(color[1:3], 16) | (int(color[3:5], 16) << 8)
                                      | (int(color[5:7], 16) << 16))
            function(int(window.winId()), attribute, ctypes.byref(colorref), ctypes.sizeof(colorref))
    except OSError:
        pass  # Older Windows builds can still use the dark widget palette.
