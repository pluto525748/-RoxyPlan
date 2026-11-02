"""Keep the Qt native dependency closure of the retained application modules.

Runs during PyInstaller analysis only. No installed/old-release file is deleted.
QPainter/QWidgets are raster-only in this product; the optional Mesa software
OpenGL fallback is excluded with the unreferenced Qt modules.
"""
from pathlib import Path
import json


def system_runtime_prerequisite(name):
    name = Path(name).name.lower()
    return name.startswith(("api-ms-win-", "vcruntime", "msvcp")) or name == "ucrtbase.dll"


def imports(path):
    import pefile
    pe = pefile.PE(str(path), fast_load=True)
    try:
        pe.parse_data_directories(directories=[
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]])
        return {entry.dll.decode("ascii").lower() for attr in
                ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT")
                for entry in getattr(pe, attr, [])}
    finally:
        pe.close()


def trim_qt_binaries(binaries, report):
    qt = {Path(dest).name.lower(): (dest, source, kind) for dest, source, kind in binaries
          if Path(dest).name.lower().startswith("qt6") and dest.lower().endswith(".dll")}
    roots = [row for row in binaries if Path(row[0]).name.lower() not in qt
             and Path(row[0]).name.lower() != "opengl32sw.dll"
             and not system_runtime_prerequisite(row[0])]
    needed, edges = set(), {}
    pending = list(roots)
    while pending:
        dest, source, kind = pending.pop()
        if Path(source).suffix.lower() not in {".dll", ".pyd", ".exe"}:
            continue
        linked = imports(source)
        edges[dest.replace("\\", "/")] = sorted(linked)
        for name in linked & qt.keys():
            if name not in needed:
                needed.add(name)
                pending.append(qt[name])
    forbidden = {"qt6pdf.dll", "qt6pdfwidgets.dll", "qt6virtualkeyboard.dll"}
    if needed & forbidden:
        raise ValueError("A retained module requires excluded Qt component: " + str(sorted(needed & forbidden)))
    kept = roots + [row for name, row in qt.items() if name in needed]
    removed = sorted(set(row[0] for row in binaries) - {row[0] for row in kept})
    Path(report).write_text(json.dumps({"kept_qt_dlls": sorted(needed), "removed": removed,
        "native_import_edges": edges, "scope": "PE import and delay-import closure; dynamic loading verified separately",
        "system_prerequisites": "Windows 10/11 x64 UCRT and Microsoft Visual C++ v14 x64 runtime installed by user from Microsoft; runtime/installer not redistributed",
        "software_opengl": "not used by raster-only QWidget/QPainter application; omitted, not removed from environment"}, indent=2), encoding="utf-8")
    return kept
