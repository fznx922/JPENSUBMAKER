"""Entry point for the packaged app (PyInstaller)."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from jpensubmaker.cli import main
    sys.exit(main())
