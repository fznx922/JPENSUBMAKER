# PyInstaller spec — builds dist/JPENSubMaker/ with two launchers sharing one runtime:
#   JPENSubMaker.exe  the app (no console window)
#   jpensub-cli.exe   the same program with a console, for --cli batch runs and --selftest
#
#   pyinstaller packaging/jpensubmaker.spec --noconfirm
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

ROOT = Path(SPECPATH).parent
ICON = str(ROOT / "packaging" / "app.ico")

datas, binaries, hiddenimports = [], [], ["_cffi_backend"]
for pkg in ("faster_whisper", "ctranslate2", "imageio_ffmpeg", "soundcard", "onnxruntime"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
# CUDA 12 cuBLAS + cuDNN 9 from the nvidia-* wheels; kept under nvidia/<pkg>/bin so hardware.prepare_cuda finds them
for pkg in ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_runtime", "nvidia.cuda_nvrtc"):
    try:
        binaries += collect_dynamic_libs(pkg)
    except Exception as e:  # noqa: BLE001 — not installed (e.g. a CPU-only build)
        print(f"note: {pkg} not bundled ({e})")

EXCLUDES = [
    "tkinter", "matplotlib", "IPython", "jupyter", "notebook", "pandas", "scipy", "torch", "torchaudio",
    "tensorflow", "qwen_asr", "transformers", "gradio",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQml",
    "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtPdf",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors",
    "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech",
    "PySide6.QtHttpServer", "PySide6.QtWebSockets",
]

a = Analysis(
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)

gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="JPENSubMaker", icon=ICON, console=False, upx=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="jpensub-cli", icon=ICON, console=True, upx=False)
coll = COLLECT(gui, cli, a.binaries, a.datas, name="JPENSubMaker", upx=False)
