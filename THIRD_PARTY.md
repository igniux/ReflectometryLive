# Third-party materials

The MIT license covers the application's original code and original artwork.
The following materials retain their separate provenance and licenses.

## Optical data and published formulas

- `reflectometry_live/data/Si_Green_2008.yml` and `GaN_Lin_1993.yml` come from the
  [refractiveindex.info database](https://github.com/polyanskiy/refractiveindex.info-database).
  Their headers preserve its CC0/public-domain dedication and original scientific
  references. The GaN data were extracted from a figure.
- The silica, AlN and sapphire equations implement the published wavelength
  dependences cited in the README and `transparent_indices.json`.
- `AZ_ECI_3027_unexposed.json` records published MicroChemicals coefficients,
  source URL and processing limitations. The supplier PDF is not included.
  This provisional reference has not been validated against a measured resist
  sample in this project.

## Icons

- `assets/delete.svg` is adapted from Microsoft Fluent System Icons, under MIT.
  Preserve `assets/Fluent-Icons-LICENSE.txt` and `Fluent-Icons-NOTICE.txt`.
- The R/layer application mark, its PNG/ICO renderings and window-control SVGs
  in this publishing copy are original project artwork under MIT. The previously
  supplied R artwork is not used in this repository.

## Python and Qt dependencies

NumPy, SciPy, Matplotlib, PySide6/Qt, pyqtgraph, PyInstaller and their dependencies
have their own licenses. They are installed from the pinned requirements and
are not checked into Git. Windows builds collect dependency metadata/license
files into `_internal`; keep these with a redistributed application. The
project's MIT license does not replace dependency licenses.

## Avantes SDK and drivers

`avaspecx64.dll` and the USB drivers are vendor components, not included in this
source repository. Obtain them through [Avantes](https://www.avantes.com/products/software/).
The adapter was developed against x64 SDK 9.14.0.0; `sdk_manifest.json` records
the reference file's version and hash.

Setup accepts an explicit local SDK path. Runtime loads only the copy beside
the application. Windows builds omit the DLL by default. `Build.bat -IncludeSdk`
is available for authorized distributions; using that flag does not establish
redistribution permission. Check the vendor's terms before sharing the DLL.

## Regression spectra

The three spectra in `demo` and spectrum/metadata pairs under `tests/20261008*`
are included with the maintainer's permission for replay and regression testing.
They are measured data, not independent certified thickness standards. Personal
measurement collections, generated research reports and literature PDFs remain
outside this repository.
