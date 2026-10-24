"""Minimal checked 64-bit AvaSpec 9.14 adapter. DLL loads only on Connect."""

import ctypes as C
import os
from pathlib import Path
import struct
import time

import numpy as np

from .domain import Frame
from .paths import APP_ROOT


SDK_PATH = APP_ROOT / "avaspecx64.dll"
MAX_PIXELS = 4096  # SDK buffer contract; actual detector length is queried.


class AvantesError(RuntimeError):
    pass


class MeasurementCancelled(Exception):
    pass


class DeviceChoiceRequired(AvantesError):
    def __init__(self, devices):
        super().__init__("Select a spectrometer serial number in Acquisition to connect.")
        self.devices = devices


class Identity(C.Structure):
    _pack_ = 1
    _fields_ = [("serial", C.c_char * 10), ("name", C.c_char * 64), ("status", C.c_uint8)]


class MeasureConfig(C.Structure):
    _pack_ = 1
    _fields_ = [("start", C.c_uint16), ("stop", C.c_uint16), ("integration_ms", C.c_float),
               ("delay", C.c_uint32), ("averages", C.c_uint32), ("dynamic_dark", C.c_uint8),
               ("dark_forget", C.c_uint8), ("smooth_pixels", C.c_uint16), ("smooth_model", C.c_uint8),
               ("saturation_detection", C.c_uint8), ("trigger_mode", C.c_uint8),
               ("trigger_source", C.c_uint8), ("trigger_source_type", C.c_uint8),
               ("strobe", C.c_uint16), ("laser_delay", C.c_uint32), ("laser_width", C.c_uint32),
               ("laser_wavelength", C.c_float), ("store_to_ram", C.c_uint16)]


def check(code, function):
    if code != 0:
        raise AvantesError(f"{function} failed (SDK code {code}). Check the USB connection and whether AvaSoft is using the instrument.")


class AvantesSource:
    def __init__(self, serial=None, library=None):
        self.requested_serial = serial
        self.lib = library  # injectable API double for tests
        self.initialized = False
        self.handle = None
        self.dll_directory = None
        self.pending = False
        self.sequence = 0
        self.settings = None

    def _load(self):
        if self.lib is not None:
            return
        if os.name != "nt" or C.sizeof(C.c_void_p) != 8:
            raise AvantesError("Live acquisition requires 64-bit Python on Windows.")
        if not SDK_PATH.is_file():
            raise AvantesError(f"Missing local SDK: {SDK_PATH}. Install your Avantes x64 DLL here for USB acquisition. Replay is available from File → Open measurement.")
        data = SDK_PATH.read_bytes()
        pe = struct.unpack_from("<I", data, 0x3c)[0]
        if struct.unpack_from("<H", data, pe + 4)[0] != 0x8664:
            raise AvantesError("avaspecx64.dll must be a 64-bit x64 SDK DLL.")
        self.dll_directory = os.add_dll_directory(str(APP_ROOT))
        try:
            self.lib = C.WinDLL(str(SDK_PATH))
            specs = {
                "AVS_Init": (C.c_int, [C.c_int]),
                "AVS_UpdateUSBDevices": (C.c_int, []),
                "AVS_GetList": (C.c_int, [C.c_uint32, C.POINTER(C.c_uint32), C.POINTER(Identity)]),
                "AVS_Activate": (C.c_int, [C.POINTER(Identity)]),
                "AVS_Deactivate": (C.c_bool, [C.c_int]),
                "AVS_Done": (C.c_int, []),
                "AVS_GetNumPixels": (C.c_int, [C.c_int, C.POINTER(C.c_uint16)]),
                "AVS_GetLambda": (C.c_int, [C.c_int, C.POINTER(C.c_double)]),
                "AVS_UseHighResAdc": (C.c_int, [C.c_int, C.c_bool]),
                "AVS_PrepareMeasure": (C.c_int, [C.c_int, C.POINTER(MeasureConfig)]),
                "AVS_Measure": (C.c_int, [C.c_int, C.c_void_p, C.c_int16]),
                # The supplied 9.14 wrapper declares PollScan as a boolean.
                "AVS_PollScan": (C.c_bool, [C.c_int]),
                "AVS_GetScopeData": (C.c_int, [C.c_int, C.POINTER(C.c_uint32), C.POINTER(C.c_double)]),
                "AVS_GetSaturatedPixels": (C.c_int, [C.c_int, C.POINTER(C.c_uint8)]),
                "AVS_StopMeasure": (C.c_int, [C.c_int]),
                "AVS_GetVersionInfo": (C.c_int, [C.c_int, C.POINTER(C.c_char), C.POINTER(C.c_char), C.POINTER(C.c_char)]),
            }
            for name, (restype, argtypes) in specs.items():
                function = getattr(self.lib, name)
                function.restype, function.argtypes = restype, argtypes
        except Exception:
            self.dll_directory.close()
            self.dll_directory = None
            self.lib = None
            raise

    def connect(self, settings):
        self._load()
        try:
            count = self.lib.AVS_Init(0)
            if count < 0:
                raise AvantesError(f"USB initialization failed (SDK code {count}).")
            self.initialized = True
            count = self.lib.AVS_UpdateUSBDevices()
            if count < 0:
                raise AvantesError(f"USB device discovery failed (SDK code {count}).")
            if not count:
                raise AvantesError("No Avantes USB spectrometer found. Connect it and close AvaSoft before trying again.")
            size = max(count, 1)
            for _ in range(3):
                identities = (Identity * size)()
                required = C.c_uint32()
                ret = self.lib.AVS_GetList(C.sizeof(identities), C.byref(required), identities)
                if required.value > C.sizeof(identities):
                    size = (required.value + C.sizeof(Identity) - 1) // C.sizeof(Identity)
                    continue
                if ret < 0:
                    raise AvantesError(f"Device listing failed (SDK code {ret}).")
                break
            else:
                raise AvantesError("Instrument list changed repeatedly; reconnect and try again.")
            # The required-size output sizes the buffer. Use filled identities
            # rather than interpreting every nonnegative return as a count;
            # this also tolerates wrappers exposing a SUCCESS status of zero.
            identities = [item for item in identities if item.serial]
            devices = [{"serial": item.serial.decode("ascii", "replace"),
                        "name": item.name.decode("utf-8", "replace")} for item in identities]
            if not devices:
                raise AvantesError("No available device identity was returned by the SDK.")
            if self.requested_serial is None and len(devices) > 1:
                raise DeviceChoiceRequired(devices)
            selected = next((item for item in identities if self.requested_serial is None
                             or item.serial.decode("ascii", "replace") == self.requested_serial), None)
            if selected is None:
                raise AvantesError("The selected spectrometer is no longer connected.")
            self.handle = self.lib.AVS_Activate(C.byref(selected))
            if self.handle == 1000 or self.handle < 0:
                self.handle = None
                raise AvantesError("Cannot activate this spectrometer. It may be in use by AvaSoft or another program.")
            pixels = C.c_uint16()
            check(self.lib.AVS_GetNumPixels(self.handle, C.byref(pixels)), "AVS_GetNumPixels")
            if not 20 <= pixels.value <= MAX_PIXELS:
                raise AvantesError(f"Unsupported detector buffer size: {pixels.value}.")
            self.pixels = pixels.value
            wavelengths = (C.c_double * MAX_PIXELS)()
            check(self.lib.AVS_GetLambda(self.handle, wavelengths), "AVS_GetLambda")
            self.wave = np.ctypeslib.as_array(wavelengths)[:self.pixels].copy()
            if not np.isfinite(self.wave).all() or np.any(np.diff(self.wave) <= 0):
                raise AvantesError("The instrument wavelength calibration is invalid.")
            self.serial = selected.serial.decode("ascii", "replace")
            self.name = selected.name.decode("utf-8", "replace") or "Avantes AvaSpec"
            versions = [(C.c_char * 16)() for _ in range(3)]
            check(self.lib.AVS_GetVersionInfo(self.handle, *versions), "AVS_GetVersionInfo")
            self.versions = [v.value.decode("ascii", "replace") for v in versions]
            self.configure(settings)
            return {"serial": self.serial, "name": self.name, "pixels": self.pixels,
                    "source_kind": "Avantes USB", "sdk_version": self.versions[-1], "settings": self.settings,
                    "wavelength_min_nm": float(self.wave[0]), "wavelength_max_nm": float(self.wave[-1])}
        except Exception:
            self.close()
            raise

    def configure(self, settings):
        settings.validate()
        self.stop()
        check(self.lib.AVS_UseHighResAdc(self.handle, settings.high_res_adc), "AVS_UseHighResAdc")
        config = MeasureConfig()
        config.start, config.stop = 0, self.pixels - 1
        config.integration_ms, config.averages = settings.integration_ms, settings.averages
        config.smooth_pixels = settings.smoothing_pixels
        config.saturation_detection = 1
        # Dynamic dark, external trigger, strobe and RAM storage remain disabled.
        check(self.lib.AVS_PrepareMeasure(self.handle, C.byref(config)), "AVS_PrepareMeasure")
        self.settings = settings

    def read(self, cancel):
        if cancel.is_set():
            raise MeasurementCancelled()
        check(self.lib.AVS_Measure(self.handle, None, 1), "AVS_Measure")
        self.pending = True
        deadline = time.monotonic() + max(1.0, 2 * self.settings.integration_ms * self.settings.averages / 1000 + 0.5)
        try:
            while not self.lib.AVS_PollScan(self.handle):
                if cancel.wait(0.002):
                    raise MeasurementCancelled()
                if time.monotonic() >= deadline:
                    raise AvantesError("Measurement timed out. Check the USB connection.")
            counts, flags = (C.c_double * MAX_PIXELS)(), (C.c_uint8 * MAX_PIXELS)()
            timestamp = C.c_uint32()
            check(self.lib.AVS_GetScopeData(self.handle, C.byref(timestamp), counts), "AVS_GetScopeData")
            self.pending = False
            check(self.lib.AVS_GetSaturatedPixels(self.handle, flags), "AVS_GetSaturatedPixels")
            self.sequence += 1
            return Frame(self.wave, np.ctypeslib.as_array(counts)[:self.pixels],
                         np.ctypeslib.as_array(flags)[:self.pixels].astype(bool), self.serial,
                         self.settings, self.sequence, sdk_timestamp=timestamp.value)
        except Exception:
            self.stop()
            raise

    def stop(self):
        if self.pending and self.handle is not None:
            try:
                check(self.lib.AVS_StopMeasure(self.handle), "AVS_StopMeasure")
            finally:
                self.pending = False

    def close(self):
        errors = []
        try:
            self.stop()
        except Exception as error:
            errors.append(str(error))
        if self.handle is not None:
            try:
                if not self.lib.AVS_Deactivate(self.handle):
                    errors.append("SDK did not confirm device release.")
            except Exception as error:
                errors.append(str(error))
            self.handle = None
        if self.initialized:
            try:
                code = self.lib.AVS_Done()
                if code != 0:
                    errors.append(f"AVS_Done returned {code}.")
            except Exception as error:
                errors.append(str(error))
            self.initialized = False
        if self.dll_directory is not None:
            self.dll_directory.close()
            self.dll_directory = None
        return errors
