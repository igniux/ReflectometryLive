"""Minimal live desktop view; device, fit and export work stay in workers."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import time

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, QRectF, QSize, QTimer, Qt, Slot, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                              QGridLayout, QHBoxLayout, QLabel, QMainWindow, QPushButton,
                              QScrollArea, QSplitter, QTabWidget, QToolButton, QVBoxLayout, QWidget)

from .domain import AcquisitionSettings, FitSettings, LayerSettings, combine_calibration
from .io import APP_ROOT, write_snapshot
from .paths import ASSET_ROOT, DEMO_ROOT
from .optics import model_description, optical_sources
from .panels import AcquisitionDialog, FittingDialog, SaveDialog, LayerEditor, DISPLAY
from .workers import AcquisitionWorker, Events, FitWorker
from . import theme


class MainWindow(QMainWindow):
    def __init__(self, replay_path=None, source_factory=None, preferences_path=None, initial_fit_settings=None):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        application = QApplication.instance()
        if os.name == "nt" and not application.property("reflectometry_fonts_loaded"):
            fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
            for name in ("segoeui.ttf", "segoeuib.ttf", "segoeuisb.ttf"):
                if (fonts/name).is_file():
                    QFontDatabase.addApplicationFont(str(fonts/name))
            application.setFont(QFont("Segoe UI", 10))
            application.setProperty("reflectometry_fonts_loaded", True)
        self.setWindowTitle("Reflectometry Live")
        self.setWindowIcon(QIcon(str(ASSET_ROOT/"reflectometry.ico")))
        self.resize(1320, 830)
        self.generation = self.fit_revision = 0
        self.first_paint_at = None
        self.connected = self.running = self.pending = self.capturing = self.closing = self.allow_close = False
        self.source_kind = "Avantes USB"
        self.connection_mode = "replay" if replay_path else "usb"
        self.auto_connect = True
        self.awaiting_device_choice = False
        self.last_connection_check = -float("inf")
        self.calibration = self.dark_capture = self.latest = self.last_raw = self.replay_path = None
        self.capture_kind = None
        self.resume_after_config = False
        self.fit_enabled = True
        self.fit_settings = FitSettings(model="stack", layers=(LayerSettings("GaN").with_estimate(500),), substrate="sapphire")
        self.acquisition_settings = AcquisitionSettings()
        self.preferences_path = None if preferences_path is False else Path(preferences_path or APP_ROOT/".cache"/"preferences.json")
        self.save_destination = APP_ROOT/"Measurements"
        self.save_name = ""
        self.preference_warning = ""
        self._load_preferences()
        self.usb_settings = self.acquisition_settings
        if initial_fit_settings is not None:
            self.fit_settings = initial_fit_settings.as_stack()
        self.fit_times = deque(maxlen=50)
        self.save_future = None
        self.exporter = ThreadPoolExecutor(max_workers=1, thread_name_prefix="Snapshot export")
        self.events = Events()
        self.events.event.connect(self.on_event)
        self.acquirer = AcquisitionWorker(self.events, **({"factory": source_factory} if source_factory else {}))
        self.fitter = FitWorker(self.events)
        self.model_timer = QTimer(self)
        self.model_timer.setSingleShot(True)
        self.model_timer.timeout.connect(self.apply_editor_stack)
        self._build_ui()
        self.acquirer.start()
        self.fitter.start()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.health_tick)
        self.timer.start(100)
        self.update_controls()
        if replay_path:
            self.set_replay_path(replay_path)
            QTimer.singleShot(0, self.connect_source)
        else:
            QTimer.singleShot(0, self.check_connection)

    def _load_preferences(self):
        if self.preferences_path is None or not self.preferences_path.exists():
            return
        try:
            data = json.loads(self.preferences_path.read_text(encoding="utf-8"))
            self.fit_settings = FitSettings.from_dict(data["fit_settings"]).as_stack()
            self.acquisition_settings = AcquisitionSettings(**data.get("acquisition_settings", {})).validate()
            self.save_destination = Path(data.get("save_destination", self.save_destination))
            self.save_name = data.get("save_name", "")
        except (KeyError, ValueError, TypeError, OSError) as error:
            self.preference_warning = f"Could not restore settings: {error}"

    def _save_preferences(self):
        if self.preferences_path is None:
            return
        try:
            self.preferences_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.preferences_path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"fit_settings": asdict(self.fit_settings),
                                              "acquisition_settings": asdict(self.usb_settings),
                                              "save_destination": str(self.save_destination), "save_name": self.save_name},
                                             indent=2, allow_nan=False), encoding="utf-8")
            temporary.replace(self.preferences_path)
        except OSError as error:
            self.statusBar().showMessage(f"Settings could not be saved: {error}", 8000)

    def _build_ui(self):
        theme.apply_dark_palette(QApplication.instance())
        self.setStyleSheet(theme.STYLESHEET + """
            QScrollArea { border: none; }
            QToolButton { border: none; background: transparent; padding: 3px; color: #99aab5; }
            QToolButton:hover { background: #46494e; border-radius: 3px; }
            QToolButton:disabled { color: #646464; }
            QToolButton[windowControl="true"] {
                background: transparent; border: none; border-radius: 0; padding: 0;
            }
            QToolButton[windowControl="true"]:hover { background: #46494e; }
            QToolButton[windowControl="true"]:pressed { background: #32353a; }
            QToolButton[windowControl="true"]:focus { border: 1px solid #99aab5; }
            QToolButton#windowCloseButton:hover { background: #c83232; }
            QToolButton#windowCloseButton:pressed { background: #961e1e; }
            QPushButton[calibrated="true"], QPushButton[calibrated="true"]:disabled {
                background: #243b29; border-color: #24ad11; color: #a5dfa0;
            }
            QPushButton[capturing="true"] { border-color: #ffff00; color: #ffff00; }
        """)
        pg.setConfigOptions(antialias=True, background=theme.PLOT_BACKGROUND, foreground=theme.TEXT)
        file_menu = self.menuBar().addMenu("File")
        self.open_action = file_menu.addAction("Open measurement…")
        self.open_action.setShortcut("Ctrl+O")
        self.open_action.triggered.connect(self.choose_replay)
        self.save_action = file_menu.addAction("Save measurement…")
        self.save_action.setShortcut("Ctrl+S")
        self.save_action.triggered.connect(self.save_measurement)
        file_menu.addSeparator()
        self.usb_action = file_menu.addAction("Use USB spectrometer")
        self.usb_action.triggered.connect(self.use_usb)
        self.disconnect_action = file_menu.addAction("Disconnect / pause connection checks")
        self.disconnect_action.triggered.connect(self.disconnect_source)
        file_menu.addSeparator()
        quit_action = file_menu.addAction("Exit")
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        measurement_menu = self.menuBar().addMenu("Measurement")
        self.pause_action = measurement_menu.addAction("Pause")
        self.pause_action.setShortcut("F7")
        self.pause_action.triggered.connect(lambda: self.stop_measurement() if self.running else self.start_measurement())
        settings_menu = self.menuBar().addMenu("Settings")
        self.acquisition_action = settings_menu.addAction("Acquisition…")
        self.acquisition_action.triggered.connect(self.open_acquisition_settings)
        self.fitting_action = settings_menu.addAction("Fitting / search bounds…")
        self.fitting_action.triggered.connect(self.open_fitting_settings)
        self.optical_action = settings_menu.addAction("Optical model and sources…")
        self.optical_action.triggered.connect(self.show_optical_sources)
        view_menu = self.menuBar().addMenu("View")
        self.fullscreen_action = view_menu.addAction("Fullscreen")
        self.fullscreen_action.setCheckable(True)
        self.fullscreen_action.setShortcut("F11")
        self.fullscreen_action.triggered.connect(self.toggle_fullscreen)
        presets = self.menuBar().addMenu("Stack")
        for label, materials, substrate in (
            ("GaN / sapphire", ("GaN",), "sapphire"),
            ("AlN / sapphire", ("AlN",), "sapphire"),
            ("SiO₂ / silicon", ("SiO2",), "Si"),
            ("SiO₂ / AlN / sapphire", ("SiO2", "AlN"), "sapphire"),
            ("AlN / SiO₂ / sapphire", ("AlN", "SiO2"), "sapphire"),
            ("SiO₂ / GaN / sapphire", ("SiO2", "GaN"), "sapphire"),
            ("SiO₂ / AlN / GaN / sapphire", ("SiO2", "AlN", "GaN"), "sapphire"),
            ("AZ 3027 / silicon", ("AZ3027E",), "Si"),
            ("AZ 3027 / GaN / sapphire", ("AZ3027E", "GaN"), "sapphire"),
        ):
            action = presets.addAction(label)
            action.triggered.connect(lambda checked=False, m=materials, s=substrate: self.set_preset(m, s))
        self._build_window_controls()
        central = QWidget()
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)
        outer.setContentsMargins(10, 10, 10, 4)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(splitter)
        sidebar = QWidget()
        controls = QVBoxLayout(sidebar)
        controls.setContentsMargins(6, 6, 14, 6)
        controls.setSpacing(16)
        title = QLabel("Reflectometry")
        title.setStyleSheet("font-size: 21px; font-weight: 600;")
        controls.addWidget(title)
        row = QHBoxLayout()
        self.dark_button = QPushButton("Dark")
        self.reference_button = QPushButton("Reference")
        self.dark_button.setToolTip("Block the probe light. Capture and average five spectra.")
        self.reference_button.setToolTip("Place the aluminium mirror. Capture and average five spectra.")
        self.dark_button.clicked.connect(lambda: self.capture("dark"))
        self.reference_button.clicked.connect(lambda: self.capture("reference"))
        row.addWidget(self.dark_button); row.addWidget(self.reference_button)
        controls.addLayout(row)
        self.calibration_label = QLabel("Dark and reference required")
        self.calibration_label.setWordWrap(True)
        controls.addWidget(self.calibration_label)
        self.layer_editor = LayerEditor(self.fit_settings.film_layers(), self.fit_settings.substrate_material(), sidebar)
        self.layer_editor.changed.connect(self.stack_changed)
        controls.addWidget(self.layer_editor)
        self.fit_status = QLabel("Checking for a USB spectrometer…")
        self.fit_status.setWordWrap(True)
        controls.addWidget(self.fit_status)
        self.rmse_label = QLabel("RMSE: —")
        controls.addWidget(self.rmse_label)
        controls.addStretch(1)
        self.save_button = QPushButton("Save measurement…")
        self.save_button.clicked.connect(self.save_measurement)
        controls.addWidget(self.save_button)
        self.device_selector = QComboBox()
        self.device_selector.addItem("Automatically select one instrument", None)
        self.device_selector.activated.connect(self.select_device)
        controls.addWidget(self.device_selector)
        self.device_label = QLabel("Checking USB…")
        self.device_label.setWordWrap(True)
        self.device_label.setStyleSheet("font-size: 11px;")
        controls.addWidget(self.device_label)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(sidebar)
        scroll.setMinimumWidth(470)
        splitter.addWidget(scroll)
        self.tabs = QTabWidget()
        live = QWidget()
        live_layout = QVBoxLayout(live)
        live_layout.setContentsMargins(4, 4, 4, 4)
        self.spectrum_plot = pg.PlotWidget()
        self.spectrum_plot.setLabel("bottom", "Wavelength", units="nm")
        self.spectrum_plot.setLabel("left", "Signal", units="counts")
        self.spectrum_plot.addLegend()
        self.measured_curve = self.spectrum_plot.plot(pen=pg.mkPen(theme.MEASURED, width=2), name="Measured")
        self.fit_curve = self.spectrum_plot.plot(pen=pg.mkPen(theme.FIT, width=2, style=Qt.PenStyle.DashLine), name="Model")
        self.residual_plot = pg.PlotWidget(title="Residual · measured − fitted")
        self.residual_plot.setLabel("bottom", "Wavelength", units="nm")
        self.residual_plot.setLabel("left", "Residual", units="pp")
        self.residual_plot.addItem(pg.InfiniteLine(pos=0, angle=0, pen=theme.BORDER))
        self.residual_curve = self.residual_plot.plot(pen=pg.mkPen(theme.MEASURED, width=1.3))
        self.residual_plot.setXLink(self.spectrum_plot)
        live_layout.addWidget(self.spectrum_plot, 4)
        live_layout.addWidget(self.residual_plot, 1)
        self.tabs.addTab(live, "Live spectrum")
        search = QWidget()
        search_layout = QVBoxLayout(search)
        profile_panel = QWidget()
        profile_layout = QGridLayout(profile_panel)
        self.profile_plots, self.profile_curves, self.profile_markers = [], [], []
        for i in range(4):
            plot = pg.PlotWidget()
            plot.setLabel("left", "RMSE", units="pp")
            self.profile_plots.append(plot)
            self.profile_curves.append(plot.plot(pen=pg.mkPen(theme.MEASURED, width=2), connect="finite"))
            marker = pg.InfiniteLine(angle=90, pen=pg.mkPen(theme.FIT, width=2))
            marker.hide(); plot.addItem(marker); self.profile_markers.append(marker)
            profile_layout.addWidget(plot, i//2, i%2)
        search_layout.addWidget(profile_panel, 1)
        self.map_plot = pg.PlotWidget()
        self.search_image = pg.ImageItem(axisOrder="col-major")
        self.search_image.setLookupTable(pg.colormap.get("viridis").getLookupTable())
        self.map_plot.addItem(self.search_image)
        self.map_marker = self.map_plot.plot(pen=None, symbol="+", symbolSize=15, symbolPen=theme.FIT)
        search_layout.addWidget(self.map_plot, 2)
        self.search_note = QLabel()
        self.search_note.setWordWrap(True); search_layout.addWidget(self.search_note)
        self.tabs.addTab(search, "Thickness search")
        for plot in (self.spectrum_plot, self.residual_plot, self.map_plot, *self.profile_plots):
            plot.showGrid(x=True, y=True, alpha=.15)
            for direction in ("bottom", "left"):
                axis = plot.getAxis(direction)
                axis.setSIPrefixEnableRanges([])
                axis.enableAutoSIPrefix(False)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([490, 950])
        self.rate_label = QLabel("Waiting for spectra")
        self.age_label = QLabel("")
        self.statusBar().addPermanentWidget(self.rate_label)
        self.statusBar().addPermanentWidget(self.age_label)
        self.statusBar().showMessage(self.preference_warning or "USB connection checks every 2 seconds")
        self.refresh_stack_display()

    def _build_window_controls(self):
        # Fullscreen hides the native caption, so keep its controls beside the menus.
        self.window_controls = QWidget(self)
        layout = QHBoxLayout(self.window_controls)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.window_minimize_button = QToolButton(self.window_controls)
        self.window_maximize_button = QToolButton(self.window_controls)
        self.window_close_button = QToolButton(self.window_controls)
        for button, name, icon, action in (
            (self.window_minimize_button, "Minimize", "window-minimize.svg", self.showMinimized),
            (self.window_maximize_button, "Maximize", "window-maximize.svg", self.toggle_maximized),
            (self.window_close_button, "Close", "window-close.svg", self.close),
        ):
            button.setProperty("windowControl", True)
            button.setFixedSize(46, 30)
            button.setIconSize(QSize(14, 14))
            button.setIcon(QIcon(str(ASSET_ROOT/icon)))
            button.setToolTip(name)
            button.setAccessibleName(name)
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            button.clicked.connect(action)
            layout.addWidget(button)
        self.window_close_button.setObjectName("windowCloseButton")
        self.window_close_button.setAccessibleDescription("Stop acquisition, finish any save and close the application.")
        self.menuBar().setCornerWidget(self.window_controls, Qt.Corner.TopRightCorner)
        self._sync_window_controls()

    def _sync_window_controls(self):
        self.window_controls.setVisible(self.isFullScreen())
        expanded = self.isFullScreen() or self.isMaximized()
        name = "Restore down" if expanded else "Maximize"
        self.window_maximize_button.setIcon(QIcon(str(ASSET_ROOT/(
            "window-restore.svg" if expanded else "window-maximize.svg"))))
        self.window_maximize_button.setToolTip(name)
        self.window_maximize_button.setAccessibleName(name)
        self.fullscreen_action.setChecked(self.isFullScreen())

    def toggle_maximized(self):
        self.showNormal() if self.isFullScreen() or self.isMaximized() else self.showMaximized()

    def refresh_stack_display(self):
        layers = self.fit_settings.film_layers()
        stack = " / ".join([DISPLAY[layer.material] for layer in layers]+[DISPLAY[self.fit_settings.substrate_material()]])
        self.spectrum_plot.setTitle(stack)
        for i, plot in enumerate(self.profile_plots):
            plot.setVisible(i < len(layers))
            if i < len(layers):
                plot.setTitle(f"{i+1}. {DISPLAY[layers[i].material]}")
                plot.setLabel("bottom", DISPLAY[layers[i].material]+" thickness", units="nm")
        self.map_plot.setVisible(len(layers) == 2)
        if len(layers) == 2:
            self.map_plot.setLabel("bottom", DISPLAY[layers[0].material]+" thickness", units="nm")
            self.map_plot.setLabel("left", DISPLAY[layers[1].material]+" thickness", units="nm")
        self.search_note.setText("Profiles minimize RMSE over the other layer's coarse grid." if len(layers) == 2 else
                                 "Other films are held at their fitted thickness for each profile. The multistart search samples the full bounds."
                                 if len(layers) > 2 else "The complete thickness interval is searched.")
        self.layer_editor.setToolTip(model_description(self.fit_settings)+"\nRMSE is signal error, not thickness uncertainty.")

    def stack_changed(self):
        self.fit_revision += 1
        self.fitter.clear()
        self.fit_enabled = False
        self.clear_measurement("Updating layer stack…")
        self.model_timer.start(180)

    def apply_editor_stack(self):
        try:
            settings = replace(self.fit_settings, model="stack", layers=self.layer_editor.layer_settings(),
                               substrate=self.layer_editor.substrate.currentData())
            if any(layer.material == "AZ3027E" for layer in settings.layers):
                settings = replace(settings, wavelength_min_nm=max(500, min(849, settings.wavelength_min_nm)),
                                   wavelength_max_nm=min(850, max(501, settings.wavelength_max_nm)))
                if settings.wavelength_max_nm <= settings.wavelength_min_nm:
                    settings = replace(settings, wavelength_min_nm=500, wavelength_max_nm=850)
                self.statusBar().showMessage("AZ 3027: using the 500–850 nm transparent-region reference; verify product designation.", 10000)
            settings.validate()
            self.fit_settings = settings
            self.fit_enabled = True
            self.refresh_stack_display()
            self._save_preferences()
            if self.last_raw is not None and self.calibration is not None:
                self.submit_fit(self.last_raw)
            else:
                self.fit_status.setText("Live spectra · capture dark and reference to fit.")
        except ValueError as error:
            self.fit_enabled = False
            self.clear_measurement(str(error))

    def set_fit_settings(self, settings):
        self.model_timer.stop()
        self.fit_settings = settings.as_stack()
        self.layer_editor.set_stack(self.fit_settings.layers, self.fit_settings.substrate)
        self.fit_revision += 1
        self.fitter.clear()
        self.fit_enabled = True
        self.clear_measurement("Fitting the selected stack…")
        self.refresh_stack_display()
        self._save_preferences()
        if self.last_raw is not None and self.calibration is not None:
            self.submit_fit(self.last_raw)

    def set_preset(self, materials, substrate):
        layers = tuple(LayerSettings(material).with_estimate(2700 if material == "AZ3027E" else 500) for material in materials)
        settings = replace(self.fit_settings, model="stack", layers=layers, substrate=substrate)
        if "AZ3027E" in materials:
            settings = replace(settings, wavelength_min_nm=500, wavelength_max_nm=850)
        self.set_fit_settings(settings)

    def open_acquisition_settings(self):
        dialog = AcquisitionDialog(self.acquisition_settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.apply_acquisition(dialog.values())
        dialog.deleteLater()

    def open_fitting_settings(self):
        dialog = FittingDialog(replace(self.fit_settings, layers=self.layer_editor.layer_settings(),
                                       substrate=self.layer_editor.substrate.currentData()), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.set_fit_settings(dialog.values())
        dialog.deleteLater()

    def show_optical_sources(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Optical model")
        dialog.setMinimumWidth(520)
        layout = QVBoxLayout(dialog)
        description = QLabel(model_description(self.fit_settings))
        description.setWordWrap(True); layout.addWidget(description)
        for material, url in optical_sources(self.fit_settings).items():
            label = QLabel(f'<a href="{url}">{DISPLAY.get(material, material)} — published optical data</a>')
            label.setOpenExternalLinks(True); layout.addWidget(label)
        note = QLabel("Silicon absorption is included. GaN uses the Lin ordinary-index curve (MBE-grown reference); visible absorption is neglected. "
                      "Nitride and sapphire axes are normal to the surface. MOCVD films and PECVD oxide may differ from these references. "
                      "Sapphire backside reflection is omitted.")
        if any(layer.material == "AZ3027E" for layer in self.fit_settings.film_layers()):
            note.setText(note.text()+" AZ 3027 uses a provisional AZ ECI 3027 unexposed Cauchy reference from MicroChemicals. "
                         "Confirm the bottle designation. Residual solvent can shift the index. Absorption is neglected only in the selected 500–850 nm interval.")
        note.setWordWrap(True); layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
        dialog.exec(); dialog.deleteLater()

    def update_controls(self):
        replay = self.connection_mode == "replay"
        available = self.connected and not self.pending and not self.closing
        dark_ready = self.dark_capture is not None or self.calibration is not None
        reference_ready = self.calibration is not None
        self.dark_button.setEnabled(available and not replay)
        self.reference_button.setEnabled(available and not replay and dark_ready)
        for button, ready, kind, name in ((self.dark_button, dark_ready, "dark", "Dark"),
                                         (self.reference_button, reference_ready, "reference", "Reference")):
            button.setText(name)
            capturing = self.capturing and self.capture_kind == kind
            if button.property("calibrated") != ready or button.property("capturing") != capturing:
                button.setProperty("calibrated", ready)
                button.setProperty("capturing", capturing)
                button.style().unpolish(button); button.style().polish(button)
        self.device_selector.setEnabled(not self.connected and not self.pending)
        self.device_selector.setVisible(not replay and self.awaiting_device_choice)
        for action in (self.open_action, self.usb_action, self.fitting_action):
            action.setEnabled(not self.pending and not self.closing)
        self.acquisition_action.setEnabled(not replay and not self.pending and not self.closing)
        self.disconnect_action.setEnabled(not self.pending and (self.connected or self.auto_connect))
        self.pause_action.setEnabled(available or self.capturing)
        self.pause_action.setText("Pause" if self.running else "Resume")
        save_enabled = self.latest is not None and self.save_future is None and not self.closing and self.fit_enabled
        self.save_button.setEnabled(save_enabled); self.save_action.setEnabled(save_enabled)

    def set_replay_path(self, path):
        self.replay_path = Path(path).resolve()

    def choose_replay(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Open a measurement", str(self.save_destination if self.save_destination.exists() else DEMO_ROOT),
                                                 "Reflectometry data (*.TXT *.txt *.csv *.json);;All files (*)")
        if filename:
            self.set_replay_path(filename)
            self.connection_mode = "replay"
            self.connect_source(replace=True)

    def use_usb(self):
        self.connection_mode = "usb"
        self.acquisition_settings = self.usb_settings
        self.auto_connect = True
        self.awaiting_device_choice = False
        self.device_selector.setCurrentIndex(0)
        self.connect_source(replace=True)

    def select_device(self, index):
        if self.device_selector.itemData(index) is not None:
            self.awaiting_device_choice = False
            self.connect_source()

    def check_connection(self):
        if (self.connection_mode == "usb" and self.auto_connect and not self.awaiting_device_choice
                and not self.connected and not self.pending and not self.closing
                and time.monotonic()-self.last_connection_check >= 2):
            self.connect_source()

    def request(self, kind, payload=None, bump=True):
        if bump:
            self.generation += 1
            self.fitter.clear()
        self.acquirer.submit(kind, payload, self.generation)

    def connect_source(self, replace=False):
        if (self.connected and not replace) or self.pending or self.closing:
            return
        descriptor = {"kind": "replay", "path": self.replay_path} if self.connection_mode == "replay" else {
            "kind": "avantes", "serial": self.device_selector.currentData()}
        if self.connection_mode == "replay" and self.replay_path is None:
            return
        self.pending = True
        self.running = False
        self.calibration = self.dark_capture = self.last_raw = None
        self.last_connection_check = time.monotonic()
        self.clear_measurement("Connecting…")
        self.request("connect", {"descriptor": descriptor, "settings": self.acquisition_settings})
        self.update_controls()

    def disconnect_source(self):
        self.auto_connect = False
        self.pending = True
        self.running = False
        self.clear_measurement("Disconnecting…")
        self.request("disconnect")
        self.update_controls()

    def start_measurement(self):
        if not self.connected or self.pending:
            return
        self.pending = True
        self.request("start")
        self.update_controls()

    def stop_measurement(self):
        if not self.connected:
            return
        self.pending = True
        self.running = False
        self.request("stop")
        self.update_controls()

    def capture(self, kind):
        if not self.connected or self.pending:
            return
        self.pending = self.capturing = True
        self.capture_kind = kind
        self.calibration = None
        if kind == "dark":
            self.dark_capture = None
        self.clear_measurement(f"Capturing {kind}…")
        self.calibration_label.setText(f"Capturing {kind} · five spectra")
        self.request("capture", kind)
        self.update_controls()

    def apply_acquisition(self, settings):
        settings.validate()
        changed = self.acquisition_settings.calibration_key() != settings.calibration_key()
        self.acquisition_settings = settings
        self.usb_settings = settings
        self._save_preferences()
        if changed:
            self.calibration = self.dark_capture = None
            self.calibration_label.setText("Exposure or processing changed · recapture calibration")
            self.clear_measurement("Fresh dark and reference required.")
        if self.connected and self.connection_mode != "replay":
            self.resume_after_config = self.running
            self.pending = True
            self.running = False
            self.request("configure", settings)
        self.update_controls()

    def submit_fit(self, frame):
        if not self.fit_enabled:
            return
        self.fitter.submit({"frame": frame, "calibration": self.calibration, "settings": self.fit_settings,
                            "generation": self.generation, "fit_revision": self.fit_revision})

    def clear_measurement(self, message):
        self.latest = None
        self.layer_editor.clear_results()
        self.rmse_label.setText("RMSE: —")
        self.fit_status.setText(message)
        self.fit_status.setStyleSheet("")
        self.fit_curve.clear(); self.residual_curve.clear()
        for curve, marker in zip(self.profile_curves, self.profile_markers):
            curve.clear(); marker.hide()
        self.search_image.clear(); self.map_marker.clear()
        self.save_button.setEnabled(False); self.save_action.setEnabled(False)

    def show_raw_frame(self, frame):
        values = frame.counts
        units = "counts"
        label = "Sample signal"
        if self.calibration is not None and self.calibration.compatible(frame):
            denominator = self.calibration.reference-self.calibration.dark
            usable = np.isfinite(denominator) & (denominator > 10)
            values = np.full(len(frame.counts), np.nan)
            np.divide(100*(frame.counts-self.calibration.dark), denominator, out=values, where=usable)
            units, label = "%", "Relative reflectance"
        self.spectrum_plot.setLabel("left", label, units=units)
        self.measured_curve.setData(frame.wavelength_nm, values, connect="finite")

    def save_measurement(self):
        if self.latest is None or self.save_future is not None:
            return
        frozen = self.latest
        dialog = SaveDialog(frozen, self.save_destination, self.save_name, self.spectrum_plot.grab(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            folder, name = dialog.values()
            self.save_destination, self.save_name = folder, name
            self._save_preferences()
            self.save_to(folder, name, snapshot=frozen)
        dialog.deleteLater()

    def save_to(self, folder, name, snapshot=None):
        snapshot = snapshot if snapshot is not None else self.latest
        if snapshot is None or self.save_future is not None:
            return None
        self.save_future = self.exporter.submit(write_snapshot, folder, name, *snapshot)
        self.update_controls()
        self.statusBar().showMessage("Saving measurement…")
        def finished(future):
            try:
                event = {"kind": "saved", "payload": str(future.result())}
            except Exception as error:
                event = {"kind": "save_error", "payload": str(error)}
            self.events.event.emit(event)
        self.save_future.add_done_callback(finished)
        return self.save_future

    @Slot(object)
    def on_event(self, event):
        kind, payload = event["kind"], event.get("payload")
        if kind in ("saved", "save_error"):
            self.save_future = None
            self.statusBar().showMessage(("Saved: " if kind == "saved" else "Save failed: ")+payload)
            self.update_controls()
            return
        if self.closing or event.get("generation") != self.generation:
            return
        if kind in ("fit", "fit_rejected") and event.get("fit_revision") != self.fit_revision:
            return
        if kind == "connected":
            self.connected = True
            self.pending = self.capturing = self.running = False
            self.source_kind = payload["source_kind"]
            self.acquisition_settings = payload["settings"]
            if self.connection_mode == "usb":
                self.usb_settings = self.acquisition_settings
            self.calibration = payload.get("calibration")
            self.dark_capture = self.last_raw = None
            self.fit_times.clear()
            identity = f"{self.source_kind} · {payload['serial']} · {payload['pixels']} pixels"
            self.device_label.setText(identity)
            self.device_label.setToolTip(identity+(f"\nSDK {payload['sdk_version']}" if "sdk_version" in payload else ""))
            if payload.get("fit_settings"):
                self.set_fit_settings(FitSettings.from_dict(payload["fit_settings"]))
            elif self.source_kind == "Replay" and self.replay_path and "Si-SiO2" in self.replay_path.name:
                # Known legacy demonstration filenames contain an unambiguous stack.
                self.set_fit_settings(FitSettings())
            self.calibration_label.setText("Calibration from saved file" if self.calibration else "Capture dark, then the mirror reference")
            self._save_preferences()
            self.start_measurement()  # Raw data are live immediately, before calibration.
        elif kind == "devices":
            self.awaiting_device_choice = True
            self.device_selector.clear()
            self.device_selector.addItem("Choose a spectrometer…", None)
            for device in payload:
                self.device_selector.addItem(f"{device['serial']} · {device['name']}", device["serial"])
        elif kind in ("error", "disconnected"):
            self.connected = self.running = self.pending = self.capturing = False
            self.calibration = self.dark_capture = self.last_raw = None
            self.clear_measurement(payload if kind == "error" else "Disconnected.")
            self.device_label.setText("Choose a spectrometer above" if self.awaiting_device_choice else
                                      "Checking USB every 2 seconds" if self.connection_mode == "usb" and self.auto_connect else "Not connected")
            self.calibration_label.setText("Dark and reference required")
            self.statusBar().showMessage(payload or "Disconnected")
        elif kind == "configured":
            self.acquisition_settings = payload
            self.pending = False
            if self.resume_after_config:
                self.resume_after_config = False
                self.start_measurement()
        elif kind == "started":
            self.pending = False
            self.running = True
            self.fit_times.clear()
            self.statusBar().showMessage(f"{self.source_kind} · live")
            if self.fit_enabled:
                self.fit_status.setText("Waiting for a fit" if self.calibration else "Live spectrum · calibration required for thickness")
        elif kind == "stopped":
            self.running = self.pending = self.capturing = False
            self.capture_kind = None
            self.calibration_label.setText("Calibration ready" if self.calibration else
                                           "Dark captured · reference required" if self.dark_capture else "Dark and reference required")
            self.statusBar().showMessage("Paused · the last completed measurement is available for saving")
        elif kind == "capturing":
            self.calibration_label.setText(f"Capturing {payload} · five spectra")
        elif kind == "captured":
            self.pending = self.capturing = False
            self.capture_kind = None
            if payload.kind == "dark":
                self.dark_capture = payload
                self.calibration_label.setText("Dark ready · position the mirror")
            else:
                try:
                    self.calibration = combine_calibration(self.dark_capture, payload)
                    self.calibration_label.setText("Dark and reference ready")
                except ValueError as error:
                    self.calibration = None
                    self.fit_status.setText(str(error))
        elif kind == "frame":
            self.last_raw = payload
            if self.calibration is not None and self.fit_enabled:
                if self.latest is None:
                    self.show_raw_frame(payload)
                self.submit_fit(payload)
            else:
                self.show_raw_frame(payload)
        elif kind == "fit_rejected":
            self.clear_measurement(payload[1])
            self.show_raw_frame(payload[0])
            self.fit_status.setStyleSheet(f"color: {theme.ERROR};")
        elif kind == "fit":
            frame, calibration, result = payload
            self.latest = payload
            self.fit_times.append(time.monotonic())
            self.layer_editor.show_results(result.layer_values(), result.status, result.layer_std_nm)
            self.fit_status.setText(("Replay · " if frame.source_kind == "Replay" else "")+result.status)
            candidate_lines = [result.message]
            for candidate in result.candidates:
                values = candidate.get("thicknesses_nm", [candidate.get("sio2_nm"), candidate.get("aln_nm")])
                details = " · ".join(f"{DISPLAY[layer.material]} {value:.1f} nm" for layer, value
                                     in zip(result.settings.film_layers(), values) if value is not None)
                candidate_lines.append(f"{details} · RMSE {candidate['rmse_pp']:.3f} pp")
            self.fit_status.setToolTip("\n".join(candidate_lines))
            style = f"color: {theme.GOOD if result.status == 'Good' else theme.WARNING};"
            if self.fit_status.styleSheet() != style:
                self.fit_status.setStyleSheet(style)
            self.rmse_label.setText(f"RMSE {result.rmse_pp:.3f} pp · {result.fit_time_ms:.0f} ms")
            self.spectrum_plot.setLabel("left", "Relative reflectance", units="%")
            self.measured_curve.setData(result.wavelength_nm, result.measured, connect="finite")
            self.fit_curve.setData(result.wavelength_nm, result.prediction)
            self.residual_curve.setData(result.wavelength_nm, result.residual)
            limit = max(.05, float(np.max(np.abs(result.residual)))*1.1)
            self.residual_plot.setYRange(-limit, limit, padding=0)
            axes = result.layer_grids_nm or (result.grid_nm, result.aln_grid_nm)
            profiles = result.layer_profiles_rmse_pp or (result.grid_rmse_pp, result.aln_grid_rmse_pp)
            for curve, marker, axis, error, value in zip(self.profile_curves, self.profile_markers, axes, profiles, result.layer_values()):
                curve.setData(axis, np.where(np.isfinite(error), error, np.nan), connect="finite")
                marker.setValue(float(value)); marker.show()
            if result.search_rmse_pp.ndim == 2 and result.search_rmse_pp.size:
                finite = result.search_rmse_pp[np.isfinite(result.search_rmse_pp)]
                ceiling = max(float(np.percentile(finite, 95)), result.rmse_pp+.1)
                self.search_image.setImage(np.nan_to_num(result.search_rmse_pp, posinf=ceiling), autoLevels=False)
                dx, dy = axes[0][1]-axes[0][0], axes[1][1]-axes[1][0]
                self.search_image.setRect(QRectF(axes[0][0]-dx/2, axes[1][0]-dy/2, dx*len(axes[0]), dy*len(axes[1])))
                self.search_image.setLevels((result.rmse_pp, ceiling))
                self.map_plot.setTitle(f"RMSE · purple {result.rmse_pp:.2f} → yellow {ceiling:.2f} pp")
                self.map_marker.setData([result.layer_values()[0]], [result.layer_values()[1]])
            self.spectrum_plot.setXRange(result.settings.wavelength_min_nm, result.settings.wavelength_max_nm, padding=.02)
        elif kind == "cleanup_warning":
            self.statusBar().showMessage(payload)
        self.update_controls()

    def health_tick(self):
        if self.closing:
            if not self.acquirer.is_alive() and not self.fitter.is_alive() and (self.save_future is None or self.save_future.done()):
                self.exporter.shutdown(wait=False)
                self.allow_close = True
                self.close()
            return
        now = time.monotonic()
        self.check_connection()
        while self.fit_times and now-self.fit_times[0] > 3:
            self.fit_times.popleft()
        if self.running and len(self.fit_times) >= 2:
            elapsed = self.fit_times[-1]-self.fit_times[0]
            self.rate_label.setText(f"{(len(self.fit_times)-1)/elapsed:.1f} fits/s")
        else:
            self.rate_label.setText("Live · raw spectrum" if self.running else "Paused" if self.connected else "Not connected")
        if self.latest is not None:
            age = now-self.latest[0].captured_at
            self.age_label.setText(f"Age {age:.2f} s" if self.running else "Frozen")
            limit = max(1., 2.5*(.2+self.acquisition_settings.integration_ms*self.acquisition_settings.averages/1000))
            if self.running and not self.capturing and age > limit:
                self.clear_measurement("Stale reading · waiting for a new fit")
                self.update_controls()
        else:
            self.age_label.setText(f"Spectrum age {now-self.last_raw.captured_at:.2f} s" if self.last_raw else "")

    def closeEvent(self, event):
        if self.allow_close:
            self.timer.stop(); self.model_timer.stop()
            event.accept()
            return
        event.ignore()
        if not self.closing:
            self.closing = True
            self.model_timer.stop()
            self.centralWidget().setEnabled(False)
            self.fit_status.setText("Stopping acquisition and finishing any export…")
            self.generation += 1
            self.acquirer.submit("shutdown", generation=self.generation)
            self.fitter.shutdown()

    def showEvent(self, event):
        super().showEvent(event)
        theme.dark_title_bar(self)
        self._sync_window_controls()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "window_maximize_button"):
            self._sync_window_controls()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.first_paint_at is None:
            self.first_paint_at = time.perf_counter()

    def toggle_fullscreen(self):
        self.showMaximized() if self.isFullScreen() else self.showFullScreen()
        self._sync_window_controls()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.isFullScreen():
            self.toggle_fullscreen()
            event.accept()
        else:
            super().keyPressEvent(event)
