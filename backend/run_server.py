"""
Uvicorn entry point for PyInstaller packaging.
This file is referenced by apptrack_backend.spec as the analysis target.
When run as a standalone exe it starts the FastAPI backend on the given port.

Usage:
    apptrack_backend.exe [--port 8001] [--frontend-dist path/to/dist]
"""
import sys
import os
import argparse

def main():
    parser = argparse.ArgumentParser(description="AppTrack backend server")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--frontend-dist", default="",
                        help="Absolute path to the built React frontend dist/")
    args = parser.parse_args()

    if args.frontend_dist:
        os.environ["APPTRACK_FRONTEND_DIST"] = args.frontend_dist

    # When frozen by PyInstaller __file__ moves; fix PYTHONPATH so app/ is found
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS  # type: ignore[attr-defined]
        sys.path.insert(0, base)

    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
