import os
from pathlib import Path

os.environ.setdefault(
    "HUB_CONFIG_PATH",
    str(Path(__file__).parent / "fixtures" / "hub.yaml"),
)
