"""Common in-environment runner for Python Service Hub plugins."""

__version__ = "0.1.0"

from .execution import run_job
from .result_writer import write_result_atomic

__all__ = ["run_job", "write_result_atomic"]
