# Reflectometry Live

[![Windows tests](https://github.com/Igniux/ReflectometryLive/actions/workflows/windows-tests.yml/badge.svg)](https://github.com/Igniux/ReflectometryLive/actions/workflows/windows-tests.yml)

A Windows desktop application for live thin-film thickness fitting with Avantes
USB spectrometers. It displays spectra immediately after connection and fits
ordered films on silicon or sapphire using wavelength-dependent optical data.

**Current application version: 1.3.5.** Source code is MIT licensed; bundled
scientific data and third-party components retain their own terms. See
[LICENSE](LICENSE) and [THIRD_PARTY.md](THIRD_PARTY.md).

<img src="assets/reflectometry.png" width="96" alt="Reflectometry Live application mark">

## Features

- Editable stacks of one to four films: GaN, AlN, SiO₂ and provisional AZ 3027
  photoresist, on sapphire or silicon. Layer order is top to bottom.
- Independent acquisition and fitting workers, with a newest-frame queue to
  prevent display lag. The nominal target is five updates per second when the
  acquisition settings and stack search permit it; this is not a guarantee for
  every instrument or search range.
- Five-spectrum dark/reference captures, live measured/fitted curves, residuals,
  search diagnostics, fit quality and conditional statistical fit errors.
- Automatic USB discovery, a compact dark interface and fullscreen window
  controls. F11 toggles fullscreen; Escape returns to a maximized window.
- Replay of Avantes exports and saved snapshots without hardware or a vendor SDK.
- Frozen measurement snapshots containing exactly one CSV and one PNG.

## Running a Windows release

If a Windows binary release is available under
[Releases](https://github.com/Igniux/ReflectometryLive/releases), download the
**application ZIP**, extract it to a writable folder and run
`ReflectometryLive.exe`. Keep its complete folder, including `_internal` and
license notices. GitHub's automatically generated source archives are not built
applications. No Python installation is needed for the Windows application ZIP.

The target is Windows 10/11 x64 on Intel/AMD computers. Testing has been on
Windows 11 x64; other systems and spectrometer models require verification.
Internet is not needed for fitting or replay.

The proprietary Avantes DLL is omitted from public builds by default. For USB
operation, install the Avantes driver and place an authorized x64
`avaspecx64.dll` beside the executable. The adapter was developed with SDK
**9.14.0.0**. Close AvaSoft or other programs using the instrument before
connecting. Replay does not require the DLL or USB driver.

## Running from source

Use **64-bit Python 3.14**, the tested interpreter for the pinned scientific
dependencies. From the repository folder on Windows:

```powershell
.\Setup.bat
.\Launch.bat
```

Setup creates `.venv`, installs `requirements.txt` and generates a local
`Launch.lnk` shortcut. The shortcut is machine-specific and is not stored in
Git. The setup step needs internet for dependency installation.

To select an interpreter explicitly:

```powershell
.\Setup.bat -PythonExe 'C:\Python314\python.exe'
```

Replay works without an SDK. For USB acquisition, obtain the SDK from
[Avantes](https://www.avantes.com/products/software/) and copy it explicitly:

```powershell
.\Setup.bat -SdkSource 'C:\AvantesSDK\avaspecx64.dll'
```

Runtime uses only the DLL in the application folder. It does not search another
laboratory program's installation or the working directory for the SDK.

To try an included measured spectrum:

```powershell
.\.venv\Scripts\python.exe .\run.py --replay '.\demo\Si-SiO2 pirmas_1212121U1.TXT'
```

The three [demo spectra](demo/README.md) and two original test captures are
included with the maintainer's permission. Personal measurements, generated
research reports, environments and built executables are excluded from Git.

## Measuring and saving

1. Connect the spectrometer over USB. Device checks run every two seconds, and
   raw spectra start after connection. For several devices, select a serial.
2. Block the light and click **Dark**. Position the aluminium mirror and click
   **Reference**. Each capture averages five spectra and turns its button green.
3. Select the films and substrate, arranged from exposed surface downwards.
   Enter approximate film thicknesses in nm. Add, reorder or remove film rows as
   needed. Material and estimate changes apply automatically.
4. Watch the fit status, residuals and thickness search. Rough estimates are
   seeds rather than fixed thicknesses. Poor, ambiguous, boundary or stale
   results should not be interpreted as reliable measurements.
5. Click **Save measurement** (Ctrl+S). Enter an optional wafer identifier and
   destination. The dialog freezes the completed frame while acquisition
   continues.

Settings for acquisition and fitting are in the menu. Defaults are 6 ms
integration, 11-scan averaging, no additional smoothing and 400–850 nm fitting.
Exposure, ADC or processing changes invalidate the relevant calibration.

The relative signal is `100 × (sample − dark) / (reference − dark)`; a positive
amplitude scale and constant offset are fitted. These nuisance parameters do not
establish absolute spectral calibration of the mirror.

Each snapshot contains:

| File | Contents |
| --- | --- |
| `spectrum.csv` | Counts, calibration, relative signal, fitted curve, residuals and masks, with an embedded settings/results record |
| `fit.png` | Highlighted thickness results above the spectrum and residual plots |

Folder names use local capture time, optional wafer ID, then films from top to
bottom with rounded thicknesses: `20261009T130811 W12 SiO2 497 AlN 580`.
Existing folders are never overwritten. The CSV alone can be reopened with
**File → Open measurement** (Ctrl+O). Older CSV/metadata snapshots remain readable.

Preferences and logs are stored in `.cache`; the default snapshot destination
is `Measurements` beside the source launcher or executable. Use a writable
folder. Calibration is recaptured after physical device reconnection.

## Optical model and limitations

The model assumes air / ordered coherent films / a semi-infinite substrate at
normal incidence. GaN, AlN and sapphire are treated as c-plane, with the crystal
axis perpendicular to the surface. Sapphire backside reflection is omitted.

| Material | Bundled wavelength dependence |
| --- | --- |
| SiO₂ | Malitson fused-silica Sellmeier equation |
| Silicon | Green complex refractive index, including absorption |
| AlN | Pastrňák–Roskovcová ordinary Sellmeier equation |
| Sapphire | Malitson–Dodge ordinary Sellmeier equation |
| GaN | Lin ordinary-index table; visible absorption neglected |
| AZ 3027 | Provisional MicroChemicals AZ ECI 3027 unbleached Cauchy reference |

GaN fitting is restricted to 369–991.7 nm. The reference is an MBE-grown film,
not a measured dispersion of each MOCVD wafer. PECVD oxide and grown films can
differ from the published optical constants.

The resist option is intended for soft-baked, unexposed material, but uses the
supplier's solvent-free **AZ ECI 3027** reference. Product designation and bake
conditions require confirmation. Fitting is limited to 500–850 nm with k=0
assumed there. No measured resist spectrum has validated this reference yet.

One/two-film searches evaluate the full bounded coarse grid and refine distinct
minima. Three/four-film fitting uses bounded Sobol sampling with multistart
refinement; it is not exhaustive. Broad searches can exceed the cache budget.
Good starting estimates and correct material order matter.

**The displayed ± is a conditional local 1σ statistical fit error**, assuming
independent residual noise. It includes fitted-parameter coupling but excludes
refractive-index, calibration and model errors. Correlated residuals can make it
optimistic. RMSE is signal error in percentage points, not thickness uncertainty.
Small error bars or agreement with an earlier reflectometry value do not by
themselves validate absolute accuracy or the selected physical stack.

## Tests and Windows builds

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

Tests cover published-model numerical checks, the approved measured regressions,
synthetic stacks, calibration failures, simulated SDK errors, replay, UI state,
saving/reopening and clean shutdown. The real-DLL loading test skips when the
optional SDK is absent. Simulation and replay do not verify physical USB timing
or experimental thickness accuracy.

GitHub Actions runs these tests on Windows x64 with Python 3.14 on every push
and pull request, without a vendor SDK or instrument. Test reports are retained
as workflow artifacts. An SDK-free checkout is expected to skip the one optional
real-DLL loading test; fake-SDK and numerical tests still run.

To build the windowed executable:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\Build.bat
```

Builds go to `dist` and generate a local `Reflectometry Live.lnk`. Existing
release folders are preserved. By default, the vendor DLL is **not** bundled;
an authorized private distribution can use `Build.bat -IncludeSdk` after
installing the local SDK. That switch does not grant redistribution rights.

The SDK-free package works in replay; users add their own authorized DLL and
driver for USB operation. The build includes Python, Qt, numerical libraries,
optical data, icons and dependency metadata in `_internal`. Keep all supporting
files with the EXE, and distribute the full clean folder as a ZIP.

`validation/check_exe.ps1` exercises the packaged application in replay from an
unrelated working directory. It checks SDK loading only when the optional DLL
is installed. `validation/check_startup.ps1` measures startup with simulated raw
acquisition. Reports are local generated files, not version-controlled data.

## Optical-source references

- [Malitson (1965), silica](https://doi.org/10.1364/JOSA.55.001205).
- [Green (2008), silicon](https://doi.org/10.1016/j.solmat.2008.06.009).
- [Pastrňák–Roskovcová (1966), AlN](https://doi.org/10.1002/pssb.19660140127).
- [Sapphire dataset and provenance](https://raw.githubusercontent.com/polyanskiy/refractiveindex.info-database/master/database/data/main/Al2O3/nk/Malitson-o.yml).
- [Lin et al. (1993), ordinary GaN](https://doi.org/10.1049/el:19931172).
- [MicroChemicals, optical parameters of photoresists](https://www.microchemicals.com/dokumente/application_notes/photoresists_optical_parameters.pdf).
