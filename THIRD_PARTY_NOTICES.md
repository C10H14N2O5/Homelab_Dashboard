# Third-party notices

The project itself is licensed under the [MIT License](LICENSE). The Windows
executable is built with the following third-party components, which retain
their own licenses.

| Component | Version used for V2.6.2 | Purpose | License |
| --- | ---: | --- | --- |
| Python | 3.14.6 | Embedded interpreter and standard library | Python Software Foundation License Version 2 and bundled historical/third-party notices |
| PyInstaller | 6.21.0 | Build tool and executable bootloader | GPL-2.0-or-later with the PyInstaller Bootloader Exception |
| Pillow | 12.3.0 | Image validation, conversion, and thumbnails | MIT-CMU and notices for bundled image libraries |
| Send2Trash | 2.1.0 | Move deleted files to the Windows Recycle Bin | BSD-3-Clause |

Complete license texts copied from the release build environment are included
under `licenses/`:

- `licenses/Python-3.14.6.txt`
- `licenses/PyInstaller-6.21.0.txt`
- `licenses/Pillow-12.3.0.txt`
- `licenses/Send2Trash-2.1.0.txt`

PyInstaller's exception permits distributing executables it creates, including
non-free or commercial programs, subject to the exception's terms. The complete
upstream text, rather than this summary, controls.

## External services are not bundled dependencies

The state service integrates with fixed Bestdori HTTPS endpoints. It does not
copy or relicense the Bestdori service, catalog, metadata, or card images. Card
art downloaded at runtime is outside the repository's MIT license; see
[ASSETS.md](ASSETS.md).
