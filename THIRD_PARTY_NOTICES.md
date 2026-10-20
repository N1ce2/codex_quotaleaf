# Third-party software notices

codex_quotaleaf is licensed under GNU GPL version 3. See LICENSE.
The Windows executable includes unmodified third-party runtime components.

| Component | License / source |
| --- | --- |
| Python 3.11 | PSF License and included notices; https://www.python.org/downloads/source/ |
| PyQt5 5.15.11 | GPLv3; copyright Riverbank Computing Limited; https://pypi.org/project/PyQt5/5.15.11/#files |
| Qt 5.15.2 | LGPLv3 / GPLv3 and third-party notices; copyright The Qt Company and contributors; https://download.qt.io/archive/qt/5.15/5.15.2/single/ |
| PyQt5-sip 12.20.0 | BSD-2-Clause; https://pypi.org/project/PyQt5-sip/12.20.0/#files |
| PyInstaller 6.22.0 bootloader | GPL-2.0-or-later with bootloader exception; https://pyinstaller.org/en/stable/license.html |

The release archive includes license texts in `licenses/`, this notice, and
the GPLv3 project license. Project source and build instructions are supplied
alongside the binary at https://github.com/N1ce2/codex_quotaleaf/releases.
Qt is dynamically linked. To use modified Qt libraries, rebuild from the
provided source/build specification with your compatible modified Qt runtime.
Upstream source archives for the exact runtime versions are linked above.

This project does not bundle OpenAI Codex or any account credentials.
Codex must be installed and signed into by the recipient separately.
