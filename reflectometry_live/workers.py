"""Serialized acquisition and a latest-frame fitting worker; neither touches widgets."""

import queue
import threading
import time

import numpy as np
from PySide6.QtCore import QObject, Signal

from .avantes import DeviceChoiceRequired, MeasurementCancelled
from .domain import CalibrationCapture
from .sources import create_source


class Events(QObject):
    event = Signal(object)


class AcquisitionWorker(threading.Thread):
    def __init__(self, events, factory=create_source, period=0.2):
        super().__init__(name="Avantes acquisition", daemon=True)
        self.events = events
        self.factory = factory
        self.period = period
        self.commands = queue.Queue()
        self.cancel = threading.Event()
        self.source = None
        self.running = False
        self.generation = 0
        self.quitting = False
        self.next_frame = 0.0

    def submit(self, kind, payload=None, generation=0):
        self.commands.put((kind, payload, generation))
        self.cancel.set()

    def emit(self, kind, payload=None):
        self.events.event.emit({"kind": kind, "payload": payload, "generation": self.generation})

    def _disconnect(self):
        self.running = False
        if self.source is not None:
            try:
                errors = self.source.close()
                if errors:
                    self.emit("cleanup_warning", "; ".join(errors))
            except Exception as error:
                self.emit("cleanup_warning", str(error))
            finally:
                self.source = None

    def _command(self, kind, payload, generation):
        self.generation = generation
        self.cancel.clear()
        if kind == "shutdown":
            self.quitting = True
            self._disconnect()
            self.emit("closed")
        elif kind == "disconnect":
            self._disconnect()
            self.emit("disconnected")
        elif kind == "connect":
            self._disconnect()
            self.source = self.factory(payload["descriptor"])
            info = self.source.connect(payload["settings"])
            self.emit("connected", info)
        elif kind == "configure":
            self.running = False
            if self.source is None:
                raise ValueError("Connect an instrument first.")
            self.source.configure(payload)
            self.emit("configured", payload)
        elif kind == "start":
            if self.source is None:
                raise ValueError("Connect an instrument first.")
            self.running = True
            self.next_frame = time.monotonic()
            self.emit("started")
        elif kind == "stop":
            self.running = False
            if self.source is not None:
                self.source.stop()
            self.emit("stopped")
        elif kind == "capture":
            if self.source is None:
                raise ValueError("Connect an instrument first.")
            was_running = self.running
            self.running = False
            self.emit("capturing", payload)
            frames = []
            for _ in range(5):
                if self.cancel.is_set():
                    raise MeasurementCancelled()
                frames.append(self.source.read(self.cancel))
            if self.cancel.is_set():
                raise MeasurementCancelled()
            counts = np.stack([frame.counts for frame in frames])
            if not np.isfinite(counts).all():
                raise ValueError("Calibration capture contains nonfinite detector counts.")
            capture = CalibrationCapture(payload, frames[-1], counts.mean(axis=0),
                                         counts.std(axis=0, ddof=1),
                                         np.any([frame.saturated for frame in frames], axis=0))
            self.emit("captured", capture)
            self.running = was_running
            self.next_frame = time.monotonic()

    def run(self):
        try:
            while not self.quitting:
                try:
                    timeout = max(0, min(0.05, self.next_frame - time.monotonic())) if self.running else 0.1
                    command = self.commands.get(timeout=timeout)
                except queue.Empty:
                    command = None
                try:
                    if command is not None:
                        self._command(*command)
                    elif self.running and time.monotonic() >= self.next_frame:
                        self.cancel.clear()
                        if not self.commands.empty():
                            continue
                        scheduled = self.next_frame
                        frame = self.source.read(self.cancel)
                        if not self.cancel.is_set():
                            self.emit("frame", frame)
                        # Keep a phase-aligned cadence. Starting each deadline
                        # from the delayed wake-up time accumulates Windows timer
                        # jitter and steadily reduces the actual update rate.
                        self.next_frame = max(scheduled + self.period, time.monotonic())
                except MeasurementCancelled:
                    # The queued command decides whether to stop or resume. In
                    # particular a calibration capture must remember live mode.
                    pass
                except DeviceChoiceRequired as error:
                    self._disconnect()
                    self.emit("devices", error.devices)
                    self.emit("error", str(error))
                except Exception as error:
                    self._disconnect()
                    self.emit("error", str(error))
        finally:
            self._disconnect()


class FitWorker(threading.Thread):
    def __init__(self, events):
        super().__init__(name="Thickness fitting", daemon=True)
        self.events = events
        self.jobs = queue.Queue(maxsize=1)
        self.quitting = threading.Event()

    def submit(self, job):
        while True:
            try:
                self.jobs.put_nowait(job)
                return
            except queue.Full:
                try:
                    self.jobs.get_nowait()
                except queue.Empty:
                    pass

    def clear(self):
        try:
            self.jobs.get_nowait()
        except queue.Empty:
            pass

    def shutdown(self):
        self.quitting.set()
        self.submit(None)

    def run(self):
        # Raw acquisition and the initial window must not wait for SciPy.
        # Initialize the numerical engine only for the first calibrated frame.
        fitter = None
        while not self.quitting.is_set():
            job = self.jobs.get()
            if job is None:
                break
            try:
                if fitter is None:
                    from .fitting import CachedFitter
                    fitter = CachedFitter()
                    if self.quitting.is_set():
                        break
                    # Imports can take longer than an acquisition period.
                    # Start with the newest job, not the one held during imports.
                    try:
                        job = self.jobs.get_nowait()
                    except queue.Empty:
                        pass
                    if job is None:
                        break
                result = fitter.process(job["frame"], job["calibration"], job["settings"])
                payload = (job["frame"], job["calibration"], result)
                kind = "fit"
            except Exception as error:
                payload, kind = (job["frame"], str(error)), "fit_rejected"
            if not self.quitting.is_set():
                self.events.event.emit({"kind": kind, "payload": payload, "generation": job["generation"],
                                        "fit_revision": job["fit_revision"]})
