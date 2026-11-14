#!/usr/bin/env python3
"""Run a ToolMaze study with the harness fix (harness_fix.py) applied.
Usage: fixed.py tm_arms|tm_arms2|tm_arms3 <that script's arguments>
The fix is patched in before the study module is imported, then the module's own main() runs."""
import importlib
import sys

import harness_fix  # noqa: F401  (patches tm_arms.ArmAgent.step)

mod = sys.argv[1]
sys.argv = [mod + ".py"] + sys.argv[2:]
importlib.import_module(mod).main()
