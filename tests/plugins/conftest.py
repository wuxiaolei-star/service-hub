from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).parents[2] / "packages" / "nc-to-shp-plugin"
sys.path.insert(0, str(PLUGIN_ROOT / "src"))
sys.path.insert(0, str(PLUGIN_ROOT))
