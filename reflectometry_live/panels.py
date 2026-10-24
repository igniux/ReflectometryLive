"""Compact layer editor and focused measurement/settings dialogs."""
from dataclasses import replace
from pathlib import Path
import math

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                              QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                              QPushButton, QScrollArea, QSpinBox, QToolButton, QVBoxLayout, QWidget)

from .domain import AcquisitionSettings, LayerSettings
from .paths import ASSET_ROOT
from .io import snapshot_folder_name
from . import theme

DISPLAY = {"SiO2": "SiO₂", "AlN": "AlN", "GaN": "GaN", "AZ3027E": "AZ 3027", "sapphire": "Sapphire", "Si": "Silicon"}
UNCERTAINTY_NOTE = ("Approximate 1σ statistical fit error in nm, including fitted amplitude and offset. "
                    "Assumes independent residual noise and the selected optical model. "
                    "Excludes refractive-index, calibration and model errors; this is not total measurement accuracy.")


def number(value, minimum=0, maximum=100000, decimals=1, suffix=""):
    spin = QDoubleSpinBox()
    spin.setRange(minimum, maximum)
    spin.setDecimals(decimals)
    spin.setValue(value)
    spin.setSuffix(suffix)
    spin.setKeyboardTracking(False)
    return spin


class FilmRow(QWidget):
    changed = Signal()
    def __init__(self, layer, parent=None):
        super().__init__(parent)
        self.layer = layer
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.material = QComboBox()
        for material in ("GaN", "AlN", "SiO2", "AZ3027E"):
            self.material.addItem(DISPLAY[material], material)
        self.material.setCurrentIndex(self.material.findData(layer.material))
        self.material.setMinimumWidth(112)
        self.material.setToolTip("AZ 3027: provisional AZ ECI 3027 unexposed reference; confirm the bottle designation. Uses 500–850 nm.")
        self.estimate = number(layer.estimate_nm)
        self.estimate.setFixedWidth(82)
        self.estimate.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.estimate.setToolTip("Rough thickness in nm. Enter or leave this field to refit.")
        self.fitted = QLabel("—")
        self.fitted.setFixedWidth(75)
        self.fitted.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.fitted.setStyleSheet(f"color: {theme.TEXT}; font-size: 16px; font-weight: 600;")
        self.uncertainty = QLabel("—")
        self.uncertainty.setFixedWidth(72)
        self.uncertainty.setToolTip(UNCERTAINTY_NOTE)
        self.uncertainty.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        arrows = QVBoxLayout()
        arrows.setContentsMargins(0, 0, 0, 0)
        arrows.setSpacing(0)
        self.move_up, self.move_down, self.remove_button = QToolButton(), QToolButton(), QToolButton()
        for button in (self.move_up, self.move_down):
            button.setFixedSize(20, 16)
        self.move_up.setArrowType(Qt.ArrowType.UpArrow)
        self.move_down.setArrowType(Qt.ArrowType.DownArrow)
        self.move_up.setToolTip("Move film towards the exposed surface")
        self.move_down.setToolTip("Move film towards the substrate")
        self.remove_button.setIcon(QIcon(str(ASSET_ROOT/"delete.svg")))
        self.remove_button.setIconSize(QSize(18, 18))
        self.remove_button.setFixedSize(24, 28)
        self.remove_button.setToolTip("Remove film")
        self.move_up.setAccessibleName("Move layer up")
        self.move_down.setAccessibleName("Move layer down")
        self.remove_button.setAccessibleName("Remove layer")
        arrows.addWidget(self.move_up)
        arrows.addWidget(self.move_down)
        layout.addWidget(self.material, 1)
        layout.addWidget(self.estimate)
        layout.addWidget(self.fitted)
        layout.addWidget(self.uncertainty)
        layout.addLayout(arrows)
        layout.addWidget(self.remove_button)
        self.material.currentIndexChanged.connect(self._material_changed)
        self.estimate.valueChanged.connect(self._estimate_changed)

    def _material_changed(self):
        self.layer = replace(self.layer, material=self.material.currentData(), bounds_manual=False).with_estimate(self.estimate.value())
        self.changed.emit()

    def _estimate_changed(self):
        self.layer = self.layer.with_estimate(self.estimate.value())
        self.changed.emit()

    def show_result(self, value=None, status="", uncertainty=None):
        self.fitted.setText(f"{value:.1f}" if value is not None and status not in ("Ambiguous", "Boundary") else "—")
        style = (f"color: {theme.GOOD if status == 'Good' else theme.WARNING if value is not None else theme.TEXT};"
                 "font-size: 16px; font-weight: 600;")
        if self.fitted.styleSheet() != style:
            self.fitted.setStyleSheet(style)
        self.fitted.setToolTip(f"Candidate {value:.2f} nm · {status}" if value is not None else "Waiting for a fit")
        usable = value is not None and status == "Good" and uncertainty is not None and math.isfinite(uncertainty)
        self.uncertainty.setText(f"± {uncertainty:.2g}" if usable and uncertainty > 1e-6 else "± <0.001" if usable else "—")
        self.uncertainty.setStyleSheet(f"color: {theme.GOOD if usable else theme.TEXT}; font-size: 13px;")


class LayerEditor(QWidget):
    changed = Signal()
    def __init__(self, layers, substrate, parent=None):
        super().__init__(parent)
        self.rows = []
        self._loading = False
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(0, 0, 0, 0)
        self.outer.setSpacing(8)
        heading = QHBoxLayout()
        heading.addWidget(QLabel("Material"), 1)
        estimate = QLabel("Estimate · nm"); estimate.setFixedWidth(82)
        fitted = QLabel("Fit · nm"); fitted.setFixedWidth(75)
        uncertainty = QLabel("± fit σ · nm"); uncertainty.setFixedWidth(72)
        uncertainty.setToolTip(UNCERTAINTY_NOTE)
        heading.addWidget(estimate); heading.addWidget(fitted); heading.addWidget(uncertainty); heading.addSpacing(50)
        self.outer.addLayout(heading)
        self.outer.addWidget(QLabel("Air · exposed surface ↓"))
        self.films = QWidget()
        self.film_layout = QVBoxLayout(self.films)
        self.film_layout.setContentsMargins(0, 0, 0, 0)
        self.film_layout.setSpacing(4)
        self.outer.addWidget(self.films)
        row = QHBoxLayout()
        self.substrate = QComboBox()
        for value in ("sapphire", "Si"):
            self.substrate.addItem(DISPLAY[value], value)
        row.addWidget(self.substrate, 1)
        note = QLabel("substrate"); note.setAlignment(Qt.AlignmentFlag.AlignRight)
        row.addWidget(note)
        self.outer.addLayout(row)
        self.substrate.currentIndexChanged.connect(self._emit_changed)
        self.set_stack(layers, substrate)

    def _emit_changed(self):
        if not self._loading:
            self.clear_results()
            self.changed.emit()

    def set_stack(self, layers, substrate):
        self._loading = True
        while self.film_layout.count():
            item = self.film_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.rows = []
        self._add_button(0, len(layers))
        for index, layer in enumerate(layers):
            row = FilmRow(layer, self.films)
            row.changed.connect(self._emit_changed)
            row.move_up.setEnabled(index > 0)
            row.move_down.setEnabled(index < len(layers)-1)
            row.remove_button.setEnabled(len(layers) > 1)
            row.move_up.clicked.connect(lambda checked=False, i=index: self.move(i, -1))
            row.move_down.clicked.connect(lambda checked=False, i=index: self.move(i, 1))
            row.remove_button.clicked.connect(lambda checked=False, i=index: self.remove(i))
            self.rows.append(row)
            self.film_layout.addWidget(row)
            self._add_button(index+1, len(layers))
        self.substrate.setCurrentIndex(self.substrate.findData(substrate))
        self._loading = False

    def _add_button(self, index, count):
        button = QPushButton("+ Add layer")
        button.setStyleSheet("QPushButton { border: none; background: transparent; padding: 2px; font-size: 11px; }"
                            f"QPushButton:hover {{ color: {theme.INPUT_TEXT}; background: {theme.INPUT}; }}")
        button.setEnabled(count < 4)
        button.setToolTip("Live fitting supports up to four films" if count >= 4 else "Insert a film here, above the substrate")
        button.clicked.connect(lambda checked=False: self.add_layer(index))
        self.film_layout.addWidget(button)

    def layer_settings(self):
        return tuple(row.layer for row in self.rows)

    def add_layer(self, index, material="SiO2"):
        layers = list(self.layer_settings())
        if len(layers) >= 4:
            return
        layers.insert(index, LayerSettings(material).with_estimate(2700 if material == "AZ3027E" else 500))
        self.set_stack(layers, self.substrate.currentData())
        self.changed.emit()

    def remove(self, index):
        layers = list(self.layer_settings())
        if len(layers) > 1:
            layers.pop(index)
            self.set_stack(layers, self.substrate.currentData())
            self.changed.emit()

    def move(self, index, direction):
        layers = list(self.layer_settings())
        target = index+direction
        if 0 <= target < len(layers):
            layers[index], layers[target] = layers[target], layers[index]
            self.set_stack(layers, self.substrate.currentData())
            self.changed.emit()

    def clear_results(self):
        for row in self.rows:
            row.show_result()

    def show_results(self, values, status, uncertainties=()):
        for index, (row, value) in enumerate(zip(self.rows, values)):
            row.show_result(float(value), status, uncertainties[index] if index < len(uncertainties) else None)


class DarkDialog(QDialog):
    def showEvent(self, event):
        super().showEvent(event)
        theme.dark_title_bar(self)


class AcquisitionDialog(DarkDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Acquisition settings")
        self.setMinimumWidth(370)
        form = QFormLayout(self)
        self.integration = number(settings.integration_ms, .01, 600000, 2, " ms")
        self.averages = QSpinBox(); self.averages.setRange(1, 100000); self.averages.setValue(settings.averages)
        self.smoothing = QSpinBox(); self.smoothing.setRange(0, 100); self.smoothing.setValue(settings.smoothing_pixels)
        self.adc = QCheckBox("High-resolution ADC"); self.adc.setChecked(settings.high_res_adc)
        form.addRow("Integration", self.integration)
        form.addRow("Scan averaging", self.averages)
        form.addRow("Smoothing pixels", self.smoothing)
        form.addRow(self.adc)
        note = QLabel("Exposure, ADC or smoothing changes require fresh dark and reference.")
        note.setWordWrap(True); form.addRow(note)
        self.error = QLabel(); self.error.setWordWrap(True); form.addRow(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.check); buttons.rejected.connect(self.reject); form.addRow(buttons)

    def values(self):
        return AcquisitionSettings(self.integration.value(), self.averages.value(), self.smoothing.value(), self.adc.isChecked()).validate()

    def check(self):
        try:
            self.values(); self.accept()
        except ValueError as error:
            self.error.setText(str(error))


class FittingDialog(DarkDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        # Permit fixing an interval that became invalid after changing a film
        # material (for example GaN with a former silicon-only UV interval).
        self.settings = settings if settings.model == "stack" else settings.as_stack()
        self.setWindowTitle("Fitting settings")
        self.setMinimumWidth(460)
        form = QFormLayout(self)
        self.low = number(settings.wavelength_min_nm, 250, 1450, 1, " nm")
        self.high = number(settings.wavelength_max_nm, 250, 1450, 1, " nm")
        self.rmse = number(settings.max_rmse_pp, .01, 100, 2, " pp")
        form.addRow("Wavelength from", self.low); form.addRow("Wavelength to", self.high)
        form.addRow("Poor-fit RMSE", self.rmse)
        self.bounds = []
        for index, layer in enumerate(self.settings.layers):
            auto = QCheckBox("Follow estimate"); auto.setChecked(not layer.bounds_manual)
            low = number(layer.minimum_nm); high = number(layer.maximum_nm)
            fields = QHBoxLayout(); fields.addWidget(low); fields.addWidget(QLabel("to")); fields.addWidget(high)
            form.addRow(f"{index+1}. {DISPLAY[layer.material]} · nm", fields)
            form.addRow(auto)
            low.setEnabled(layer.bounds_manual); high.setEnabled(layer.bounds_manual)
            auto.toggled.connect(lambda checked, lo=low, hi=high: (lo.setEnabled(not checked), hi.setEnabled(not checked)))
            self.bounds.append((auto, low, high))
        note = QLabel("Fitted thicknesses assume the published material indices. Three or four films use a sampled multistart search.")
        note.setWordWrap(True); form.addRow(note)
        self.error = QLabel(); self.error.setWordWrap(True); form.addRow(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.check); buttons.rejected.connect(self.reject); form.addRow(buttons)

    def values(self):
        layers = []
        for layer, (auto, low, high) in zip(self.settings.layers, self.bounds):
            changed = replace(layer, bounds_manual=not auto.isChecked(), minimum_nm=low.value(), maximum_nm=high.value())
            layers.append(changed.with_estimate(changed.estimate_nm) if auto.isChecked() else changed)
        return replace(self.settings, wavelength_min_nm=self.low.value(), wavelength_max_nm=self.high.value(),
                       max_rmse_pp=self.rmse.value(), layers=tuple(layers)).validate()

    def check(self):
        try:
            self.values(); self.accept()
        except ValueError as error:
            self.error.setText(str(error))


class SaveDialog(DarkDialog):
    def __init__(self, snapshot, destination, default_name="", preview=None, parent=None):
        super().__init__(parent)
        self.snapshot = snapshot
        self.setWindowTitle("Save measurement")
        self.setMinimumWidth(510)
        layout = QVBoxLayout(self)
        title = QLabel("Save this measurement")
        title.setStyleSheet("font-size: 22px; font-weight: 600; padding: 8px 0;")
        layout.addWidget(title)
        frame, calibration, result = snapshot
        pieces = []
        for i, (layer, value) in enumerate(zip(result.settings.film_layers(), result.layer_values())):
            error = result.layer_std_nm[i] if i < len(result.layer_std_nm) else float("nan")
            uncertainty = f" ± {error:.2g}" if math.isfinite(error) and error > 1e-6 else " ± <0.001" if math.isfinite(error) else ""
            pieces.append(f"{DISPLAY[layer.material]} {value:.1f}{uncertainty} nm")
        summary = "  ·  ".join(pieces)
        label = QLabel(summary); label.setWordWrap(True)
        label.setStyleSheet(f"font-size: 17px; color: {theme.GOOD if result.status == 'Good' else theme.WARNING};")
        layout.addWidget(label)
        label.setToolTip(UNCERTAINTY_NOTE)
        details = QLabel(f"{result.status} · RMSE {result.rmse_pp:.3f} pp\n{frame.timestamp_utc} · {frame.device_serial}")
        details.setWordWrap(True); layout.addWidget(details)
        if preview is not None:
            image = QLabel(); image.setPixmap(preview.scaledToWidth(475, Qt.TransformationMode.SmoothTransformation)); layout.addWidget(image)
        form = QFormLayout()
        self.name = QLineEdit(default_name); self.name.setPlaceholderText("Optional wafer / sample number")
        self.destination = QLineEdit(str(destination))
        browse = QPushButton("Browse…"); browse.clicked.connect(self.browse)
        row = QHBoxLayout(); row.addWidget(self.destination, 1); row.addWidget(browse)
        form.addRow("Wafer (optional)", self.name); form.addRow("Save to", row)
        self.folder_preview = QLabel(); self.folder_preview.setWordWrap(True)
        self.folder_preview.setStyleSheet("font-size: 11px;")
        form.addRow("Folder", self.folder_preview); layout.addLayout(form)
        self.name.textChanged.connect(self.update_folder_preview)
        self.update_folder_preview()
        note = QLabel("One CSV and one PNG with highlighted fitted thicknesses will be saved. This captured spectrum stays fixed while live acquisition continues.")
        note.setWordWrap(True); layout.addWidget(note)
        self.error = QLabel(); self.error.setWordWrap(True); layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setStyleSheet(
            "QPushButton { background: #243b29; border-color: #24ad11; color: #dcdcdc; font-weight: 600; min-width: 76px; }")
        buttons.accepted.connect(self.check); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self.name.setFocus()

    def browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Measurement destination", self.destination.text())
        if folder:
            self.destination.setText(folder)

    def values(self):
        if not self.destination.text().strip():
            raise ValueError("Choose a destination folder.")
        return Path(self.destination.text()).expanduser(), self.name.text().strip()

    def update_folder_preview(self):
        frame, _, result = self.snapshot
        self.folder_preview.setText(snapshot_folder_name(self.name.text(), frame, result))

    def check(self):
        try:
            self.values(); self.accept()
        except ValueError as error:
            self.error.setText(str(error))
