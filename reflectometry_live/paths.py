"""Application and bundled-resource locations for source and Windows EXE runs."""
from pathlib import Path
import sys

RESOURCE_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else RESOURCE_ROOT
ASSET_ROOT = RESOURCE_ROOT / "assets"
DEMO_ROOT = RESOURCE_ROOT / "demo"
