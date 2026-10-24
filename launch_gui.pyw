"""Console-free startup with an error log and graphical startup error reporting."""

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
import sys
import traceback
import time


def launch():
    startup_started = time.perf_counter()
    from reflectometry_live.paths import APP_ROOT
    root = APP_ROOT
    log_path = root / ".cache" / "launch.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8", buffering=1) as log:
            with redirect_stdout(log), redirect_stderr(log):
                print(f"\n{datetime.now(timezone.utc).isoformat()} · {sys.executable}")
                try:
                    from run import main
                    return main(startup_started=startup_started)
                except Exception:
                    traceback.print_exc()
                    raise
    except Exception as error:
        import ctypes
        recovery = "Extract the complete application folder again." if getattr(sys, "frozen", False) else "Run Setup.bat if dependencies are missing."
        ctypes.windll.user32.MessageBoxW(
            None, f"Unable to start Reflectometry Live.\n\n{error}\n\nDetails: {log_path}\n{recovery}",
            "Reflectometry Live", 0x10,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(launch())
