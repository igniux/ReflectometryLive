# Qt / PySide6 notices for the Windows distribution

This application uses unmodified PySide6, Shiboken6 and dynamically loaded Qt
libraries under the LGPL version 3 option. Their licenses are separate from the
application's MIT license. Copies of LGPLv3 and its GPLv3 base license are in
this directory; retain them when redistributing the application.

The Windows ZIP keeps the libraries as separate files under `_internal`, rather
than embedding them into a single locked executable. You may inspect, modify,
debug and replace these libraries, including reverse engineering for the purpose
of debugging such modifications. Use libraries/bindings built for the same
Windows x64 architecture and compatible Python/Qt ABI. Replace the corresponding
DLLs and binding files in `_internal`; retain their relative paths, then run
the application normally. There is no application signature or integrity lock
preventing this. Alternatively, rebuild the MIT-licensed application from its
tagged public source against your modified libraries.

The first Windows release uses Qt/PySide6 6.11.2. The release page supplies
versioned source archives for the Qt modules actually bundled and for
PySide6/Shiboken6 alongside the Windows ZIP, with SHA-256 checksums. These are
unmodified upstream sources. Upstream build instructions and licenses are
contained in those archives.

Upstream projects:

- https://github.com/qt/qtbase/tree/v6.11.2
- https://github.com/qt/qtsvg/tree/v6.11.2
- https://github.com/qt/qtimageformats/tree/v6.11.2
- https://github.com/qt/qttranslations/tree/v6.11.2
- https://github.com/pyside/pyside-setup/tree/v6.11.2
- https://doc.qt.io/qt-6/build-sources.html
- https://doc.qt.io/qtforpython-6/building_from_source/index.html

Other runtime dependencies retain their own notices under `_internal` and
this directory. The package includes the Python runtime license and the
PyInstaller license/bootloader exception. The vendor Avantes SDK is not part
of the public Windows ZIP.
