# Build only through scripts/build_beta.ps1 in the scheduled build slot.
# PyInstaller's Qt hooks discover imported QtCore/QtGui/QtWidgets and shiboken;
# never collect_all(PySide6) or copy the developer environment/data tree.
import os
from pathlib import Path
import runpy
import sys

root = Path(SPECPATH).resolve().parent
policy = runpy.run_path(str(root / "scripts" / "check_beta_release.py"))
profile = os.environ.get("ROXY_BETA_PROFILE", "beta")
if profile not in {"beta", "study"}:
    raise SystemExit("Unknown build profile")
entry = root / ("roxy_study_main.py" if profile == "study" else "roxy_main.py")
if not entry.is_file():
    raise SystemExit("Beta entry missing: roxy_main.py; integrate the approved entry first.")
run_root = Path(os.environ["ROXY_BETA_RUN_ROOT"]).resolve()
if not run_root.is_dir():
    raise SystemExit("Dedicated build directory missing.")

# urllib HTTPS reaches _ssl. For Conda Python inspect that extension's PE import
# table, and add ONLY its two linked OpenSSL DLLs (not Library/bin wholesale).
binaries = []
import _ssl
import pefile
pe = pefile.PE(_ssl.__file__, fast_load=True)
pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
ssl_names = {item.dll.decode("ascii") for item in getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])
             if item.dll.decode("ascii").lower().startswith(("libssl", "libcrypto"))}
pe.close()
for name in sorted(ssl_names):
    candidates = [Path(_ssl.__file__).parent / name, Path(sys.base_prefix) / "DLLs" / name,
                  Path(sys.base_prefix) / "Library" / "bin" / name]
    found = next((path for path in candidates if path.is_file()), None)
    if found is None:
        raise SystemExit("Missing linked SSL dependency: " + name)
    binaries.append((str(found), "."))

a = Analysis(
    [str(entry)], pathex=[str(root)], binaries=binaries,
    datas=policy["build_datas"](root),
    hiddenimports=["_ssl"],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["pytest", "unittest", "IPython", "matplotlib", "numpy", "tkinter",
              "PyQt5", "PyQt6", "PySide2", "server"],
    noarchive=False, optimize=0,
)
# PNG is built into QtGui. qjpeg/qwebp/qsvg preserve the settings image
# selection formats; no preview/offscreen/direct2d or PDF image plugins.
allowed_plugins = policy["QT_PLUGINS"]
a.binaries = [item for item in a.binaries
              if Path(item[0]).name.lower() not in {"qt6pdf.dll", "qt6pdfwidgets.dll", "qt6virtualkeyboard.dll"}
              and ("/plugins/" not in item[0].replace("\\", "/")
                   or item[0].replace("\\", "/") in allowed_plugins)]
policy["write_analysis"](root, run_root, a, ssl_names)

pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="RoxyPlan",
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False, icon=None,
          contents_directory="_internal")
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="RoxyPlan")
