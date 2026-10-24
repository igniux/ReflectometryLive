Reflectometry Live 1.3.5 - Windows x64

Double-click ReflectometryLive.exe. No Python installation or batch file is needed.
The application opens fullscreen; use F11 or Esc to leave fullscreen.
Custom minimize, restore and close buttons appear at the top right in fullscreen.
Outside fullscreen, use the standard Windows title-bar buttons.
Restore down returns to a resizable window. Minimizing keeps
acquisition running; Close stops workers and finishes any pending save.
File > Exit (Ctrl+Q) stops acquisition and closes cleanly.

Keep this entire folder together, including _internal and license notices.
Public builds omit the optional vendor SDK by default. Replay works without it.
For USB acquisition, obtain the Avantes x64 SDK (tested with 9.14), place your
authorized avaspecx64.dll beside the EXE, and install the Avantes USB driver.
The SDK is loaded only from the application folder.

Use a writable folder such as Documents; settings/logs are stored in .cache,
and the default save destination is Measurements beside this executable.
Dark/reference calibration must be captured again after connecting a device.

Save measurement creates just spectrum.csv and fit.png in a folder named with
local capture time, optional wafer number and film thicknesses from top down.
Example: 20261009T130811 2D4JAA157 SiO2 497 AlN 580
The CSV contains raw/calibrated data, fitted curve, residuals and an embedded
settings/calibration record for reopening it without separate files.

Software and optical data run offline. The included demo spectra can be opened
from File > Open measurement without a connected spectrometer.

Layer estimates are editable. A good fit displays approximate statistical fit
error; this excludes refractive-index, calibration and model errors. AZ 3027
uses the provisional unexposed AZ ECI 3027 reference over 500-850 nm.

The application has no code-signing certificate in this local build.
Third-party libraries and their license notices are included in _internal.
