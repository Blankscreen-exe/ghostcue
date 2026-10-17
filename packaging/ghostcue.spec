# PyInstaller build of GhostCue: a folder holding GhostCue.exe, with no console window.
# Run packaging\build.ps1 rather than this directly; it also makes the zip and the installer.

import os
import re
from pathlib import Path

import comtypes.client
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules
from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                 VarFileInfo, VarStruct, VSVersionInfo)

ROOT = Path(SPECPATH).parent

# Window capture reads web pages through UI Automation, whose Python wrapper comtypes writes
# the first time it is used. Write it now and build it in, so the app never has to.
comtypes.client.GetModule("UIAutomationCore.dll")
VERSION = re.search(r'__version__ = "(.+?)"', (ROOT / "ghostcue" / "__init__.py").read_text()).group(1)
NUMBERS = (tuple(int(n) for n in VERSION.split(".")) + (0, 0, 0))[:4]

# Shown in the exe's Properties › Details.
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=NUMBERS, prodvers=NUMBERS),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "GhostCue"),
            StringStruct("FileDescription", "GhostCue"),
            StringStruct("FileVersion", VERSION),
            StringStruct("InternalName", "GhostCue"),
            StringStruct("OriginalFilename", "GhostCue.exe"),
            StringStruct("ProductName", "GhostCue"),
            StringStruct("ProductVersion", VERSION),
        ])]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

a = Analysis(
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(ROOT)],
    # The icon, and faster-whisper's voice detection model.
    datas=[(str(ROOT / "ghostcue" / "ghost.png"), "ghostcue")] + collect_data_files("faster_whisper"),
    binaries=collect_dynamic_libs("ctranslate2"),
    # Imported only inside functions, for the command the exe is started with.
    hiddenimports=["ghostcue.app", "ghostcue.teleprompter", "ghostcue.listen", "ghostcue.capture"]
                  + collect_submodules("comtypes.gen"),
    excludes=["PIL"],  # found when installed alongside, but nothing here uses it
)
# GHOSTCUE_CONSOLE=1 builds it with a console, unbuffered, to see errors and run `GhostCue.exe listen`.
CONSOLE = bool(os.environ.get("GHOSTCUE_CONSOLE"))

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [("u", None, "OPTION")] if CONSOLE else [],
    exclude_binaries=True,
    name="GhostCue",
    console=CONSOLE,
    icon=str(ROOT / "packaging" / "ghostcue.ico"),
    version=version_info,
)
coll = COLLECT(exe, a.binaries, a.datas, name="GhostCue")
