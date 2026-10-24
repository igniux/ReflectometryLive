import ctypes as C
import os
from pathlib import Path
import threading

import numpy as np
import pytest

from reflectometry_live.avantes import (APP_ROOT, SDK_PATH, AvantesError, AvantesSource,
                                       Identity, MeasureConfig, MeasurementCancelled)
from reflectometry_live.domain import AcquisitionSettings


class FakeLibrary:
    def __init__(self):
        self.scope_error = 0
        self.no_devices = False
        self.ready = True
        self.closed = self.deactivated = self.stopped = False

    def AVS_Init(self, port):
        assert port == 0
        return 1
    def AVS_UpdateUSBDevices(self): return 0 if self.no_devices else 1
    def AVS_GetList(self, size, required, items):
        C.cast(required, C.POINTER(C.c_uint32))[0] = C.sizeof(Identity)
        items[0].serial, items[0].name, items[0].status = b"TEST2048", b"Simulated AvaSpec", 1
        return 1
    def AVS_Activate(self, identity): return 7
    def AVS_GetNumPixels(self, handle, pixels):
        C.cast(pixels, C.POINTER(C.c_uint16))[0] = 80
        return 0
    def AVS_GetLambda(self, handle, buffer):
        for i, wave in enumerate(np.linspace(400, 850, 80)): buffer[i] = wave
        return 0
    def AVS_GetVersionInfo(self, handle, fpga, firmware, library):
        fpga.value, firmware.value, library.value = b"FPGA", b"FW", b"9.14.0.0"
        return 0
    def AVS_UseHighResAdc(self, handle, enabled): return 0
    def AVS_PrepareMeasure(self, handle, config):
        self.config = MeasureConfig.from_buffer_copy(C.string_at(config, C.sizeof(MeasureConfig)))
        assert self.config.stop == 79
        assert self.config.dynamic_dark == self.config.trigger_mode == self.config.strobe == 0
        assert self.config.saturation_detection == 1
        return 0
    def AVS_Measure(self, handle, hwnd, count):
        assert hwnd is None and count == 1
        return 0
    def AVS_PollScan(self, handle): return self.ready
    def AVS_GetScopeData(self, handle, timestamp, counts):
        if self.scope_error: return self.scope_error
        C.cast(timestamp, C.POINTER(C.c_uint32))[0] = 2345
        for i in range(80): counts[i] = 100+i
        return 0
    def AVS_GetSaturatedPixels(self, handle, flags):
        flags[10] = 1
        return 0
    def AVS_StopMeasure(self, handle): self.stopped = True; return 0
    def AVS_Deactivate(self, handle): self.deactivated = True; return True
    def AVS_Done(self): self.closed = True; return 0


def test_sdk_structures_dynamic_pixel_count_and_release():
    assert C.sizeof(Identity) == 75
    assert C.sizeof(MeasureConfig) == 41
    lib = FakeLibrary()
    source = AvantesSource(library=lib)
    assert source.connect(AcquisitionSettings())["pixels"] == 80
    frame = source.read(threading.Event())
    assert len(frame.counts) == 80
    assert frame.saturated[10]
    assert frame.sdk_timestamp == 2345
    assert source.close() == []
    assert lib.closed and lib.deactivated


def test_sdk_errors_and_cancellation():
    lib = FakeLibrary()
    source = AvantesSource(library=lib)
    source.connect(AcquisitionSettings())
    lib.scope_error = -5
    with pytest.raises(AvantesError, match="SDK code -5"):
        source.read(threading.Event())
    assert lib.stopped
    lib.scope_error = 0
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(MeasurementCancelled): source.read(cancel)
    source.close()
    missing = FakeLibrary()
    missing.no_devices = True
    with pytest.raises(AvantesError, match="No Avantes USB"):
        AvantesSource(library=missing).connect(AcquisitionSettings())
    assert missing.closed


def test_filled_device_list_is_used_with_zero_success_status():
    class StatusLibrary(FakeLibrary):
        def AVS_GetList(self, *args):
            super().AVS_GetList(*args)
            return 0
    source = AvantesSource(library=StatusLibrary())
    assert source.connect(AcquisitionSettings())["serial"] == "TEST2048"
    source.close()


def test_timeout_releases_pending_measurement():
    lib = FakeLibrary()
    lib.ready = False
    source = AvantesSource(library=lib)
    source.connect(AcquisitionSettings())
    with pytest.raises(AvantesError, match="timed out"):
        source.read(threading.Event())
    assert lib.stopped
    source.close()


@pytest.mark.skipif(os.name != "nt" or not SDK_PATH.is_file(), reason="Optional local Windows vendor DLL is not installed")
def test_local_dll_from_unrelated_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    original_loader = C.WinDLL
    loaded = []
    def only_local(path, *args, **kwargs):
        assert Path(path).resolve() == SDK_PATH
        loaded.append(path)
        return original_loader(path, *args, **kwargs)
    monkeypatch.setattr(C, "WinDLL", only_local)
    source = AvantesSource()
    source._load()
    assert loaded == [str(APP_ROOT / "avaspecx64.dll")]
    assert source.lib.AVS_GetScopeData is not None
    source.close()
