"""Create a sharing archive from an explicit allowlist, never from a project tree."""
import hashlib
from pathlib import Path
import sys
import zipfile
from app_runtime import VERSION


def main():
    root=Path(__file__).resolve().parent
    packages=Path(sys.prefix)/'Lib/site-packages'
    python_license=next((Path(sys.base_prefix)/name for name in ('LICENSE_PYTHON.txt','LICENSE.txt') if (Path(sys.base_prefix)/name).is_file()),Path(sys.base_prefix)/'LICENSE.txt')
    files={
        'codex_quotaleaf.exe':root/'dist/codex_quotaleaf.exe',
        'README.md':root/'README.md',
        'LICENSE':root/'LICENSE',
        'THIRD_PARTY_NOTICES.md':root/'THIRD_PARTY_NOTICES.md',
        'licenses/Qt-LICENSE.txt':packages/'PyQt5_Qt5-5.15.2.dist-info/LICENSE',
        'licenses/SIP-LICENSE.txt':packages/'pyqt5_sip-12.20.0.dist-info/licenses/LICENSE',
        'licenses/PyInstaller-COPYING.txt':packages/'pyinstaller-6.22.0.dist-info/licenses/COPYING.txt',
        'licenses/Python-LICENSE.txt':python_license,
    }
    missing=[name for name,path in files.items() if not path.is_file()]
    if missing:raise SystemExit('Missing package inputs: '+', '.join(missing))
    archive=root/'dist/codex_quotaleaf-windows-x64.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as bundle:
        for name,path in files.items():bundle.write(path,name)
    sums=[]
    source=root/f'dist/codex_quotaleaf-source-v{VERSION}.zip'
    for path in [files['codex_quotaleaf.exe'],archive]+([source] if source.is_file() else []):
        sums.append(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.name)
    (root/'dist/SHA256SUMS.txt').write_text('\n'.join(sums)+'\n','utf-8')
    print(archive.name,archive.stat().st_size)


if __name__=='__main__':main()
