"""llm-eval-gate: an eval harness that measures its own judges before it gates.

A gate you have not calibrated is a coin flip wearing a lab coat. The package
is arranged so that the measurement layer (calibration.py) is not optional
decoration around the gate: the gate refuses to run until the measurement says
its threshold is larger than the panel's own variance.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
