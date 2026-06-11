# -*- mode: python ; coding: utf-8 -*-
"""
RainDelay PyInstaller spec file.
Excludes unused Qt modules and other heavy libraries to minimize exe size.

Build with:
    pyinstaller RainDelay.spec --clean
"""

# Modules we actually use:
#   PyQt6.QtWidgets, QtCore, QtGui, QtMultimedia
# Everything else can be excluded.

EXCLUDED_QT_MODULES = [
    'PyQt6.QtWebEngine',
    'PyQt6.QtWebEngineCore',
    'PyQt6.QtWebEngineWidgets',
    'PyQt6.QtWebChannel',
    'PyQt6.Qt3DCore',
    'PyQt6.Qt3DRender',
    'PyQt6.Qt3DInput',
    'PyQt6.Qt3DLogic',
    'PyQt6.Qt3DAnimation',
    'PyQt6.Qt3DExtras',
    'PyQt6.QtBluetooth',
    'PyQt6.QtDesigner',
    'PyQt6.QtHelp',
    'PyQt6.QtNfc',
    'PyQt6.QtOpenGL',
    'PyQt6.QtOpenGLWidgets',
    'PyQt6.QtPositioning',
    'PyQt6.QtPrintSupport',
    'PyQt6.QtQml',
    'PyQt6.QtQuick',
    'PyQt6.QtQuick3D',
    'PyQt6.QtQuickWidgets',
    'PyQt6.QtRemoteObjects',
    'PyQt6.QtSensors',
    'PyQt6.QtSerialPort',
    'PyQt6.QtSpatialAudio',
    'PyQt6.QtSql',
    'PyQt6.QtSvg',
    'PyQt6.QtSvgWidgets',
    'PyQt6.QtTest',
    'PyQt6.QtTextToSpeech',
    'PyQt6.QtWebSockets',
    'PyQt6.QtXml',
    'PyQt6.QtDBus',
    'PyQt6.QtPdf',
    'PyQt6.QtPdfWidgets',
]

EXCLUDED_OTHER = [
    'pygame',
    'cv2',
    'opencv-python',
    'numpy',
    'numpy.core',
    'tkinter',
    '_tkinter',
    'unittest',
    'email',
    'html',
    'http',
    'xmlrpc',
    'pydoc',
    'doctest',
    'lib2to3',
    'multiprocessing',
]

ALL_EXCLUDES = EXCLUDED_QT_MODULES + EXCLUDED_OTHER

# Qt DLL binaries to exclude (substring match)
EXCLUDED_BINARIES = [
    'Qt6WebEngine',
    'Qt6Quick',
    'Qt6Qml',
    'Qt6Pdf',
    'Qt6Svg',
    'Qt6OpenGL',
    'Qt63D',
    'Qt6Designer',
    'Qt6Bluetooth',
    'Qt6Nfc',
    'Qt6Sensors',
    'Qt6SerialPort',
    'Qt6Sql',
    'Qt6Test',
    'Qt6RemoteObjects',
    'Qt6WebSockets',
    'Qt6WebChannel',
    'Qt6Positioning',
    'opengl32sw',
    'd3dcompiler',
    'libGLESv2',
    'libEGL',
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets'), ('sounds', 'sounds')],
    hiddenimports=['PyQt6.QtMultimedia'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=ALL_EXCLUDES,
    noarchive=False,
    optimize=0,
)

# Filter out large unused Qt DLLs from binaries
a.binaries = [
    b for b in a.binaries
    if not any(excl.lower() in b[0].lower() for excl in EXCLUDED_BINARIES)
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='RainDelay',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\raindelay.ico'],
)
