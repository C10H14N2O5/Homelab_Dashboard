# Third-party notices

The project itself is licensed under the [MIT License](LICENSE). Third-party
components retain their own licenses.

## Windows executable

| Component | Version used for V3.5.0 | Purpose | License |
| --- | ---: | --- | --- |
| Python | 3.14.6 | Embedded interpreter and standard library | Python Software Foundation License Version 2 and bundled historical/third-party notices |
| PyInstaller | 6.21.0 | Build tool and executable bootloader | GPL-2.0-or-later with the PyInstaller Bootloader Exception |
| Pillow | 12.3.0 | Image validation, conversion, and thumbnails | MIT-CMU and notices for bundled image libraries |
| Send2Trash | 2.1.0 | Move deleted files to the Windows Recycle Bin | BSD-3-Clause |
| markdown-it-py | 4.0.0 | Parse Markdown references for Notes | MIT, with upstream markdown-it notice |
| mdurl | 0.1.2 | URL handling used by markdown-it-py | MIT, with upstream Node.js notice |

Complete license texts copied from the release build environment are included
under `licenses/`:

- [Python-3.14.6.txt](licenses/Python-3.14.6.txt)
- [PyInstaller-6.21.0.txt](licenses/PyInstaller-6.21.0.txt)
- [Pillow-12.3.0.txt](licenses/Pillow-12.3.0.txt)
- [Send2Trash-2.1.0.txt](licenses/Send2Trash-2.1.0.txt)
- [markdown-it-py-4.0.0.txt](licenses/markdown-it-py-4.0.0.txt)
- [markdown-it-py-4.0.0-LICENSE.markdown-it.txt](licenses/markdown-it-py-4.0.0-LICENSE.markdown-it.txt)
- [mdurl-0.1.2.txt](licenses/mdurl-0.1.2.txt)

PyInstaller's exception permits distributing executables it creates, including
non-free or commercial programs, subject to the exception's terms. The complete
upstream text, rather than this summary, controls.

## Browser assets

| Component | Version | Purpose | License text |
| --- | ---: | --- | --- |
| markdown-it | 15.0.2 | Notes Markdown rendering | [MIT](assets/vendor/markdown-it/LICENSE) |
| highlight.js | 11.12.0 | Notes syntax highlighting | [BSD-3-Clause](assets/vendor/highlightjs/LICENSE) |
| DOMPurify | 3.4.15 | Notes HTML sanitization | [Apache-2.0 or MPL-2.0](assets/vendor/dompurify/LICENSE) |

The [vendored dependency manifest](assets/vendor/notes-dependencies.json)
records each browser asset's source and SHA-256. These assets are served locally.

## External services are not bundled dependencies

The state service integrates with fixed Bestdori and Bing HTTPS endpoints. It
does not copy or relicense their services, metadata, or images. Card art and
daily wallpapers downloaded at runtime are outside the repository's MIT
license. See [ASSETS.md](ASSETS.md).
