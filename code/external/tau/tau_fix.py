#!/usr/bin/env python3
"""tau-bench with the harness fix: a reply that contains a tool call is stored in the history only
up to the end of that call (harness_fix.cut_after_first_call). tau_arms3.run_episode3 (which runs
every arm, old and new) is rebuilt from its own source with that single line changed; the frozen
files are not edited. Usage: same arguments as tau_arms3.py."""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "toolmaze"))
import tau_arms3 as T3                                      # noqa: E402
from harness_fix import cut_after_first_call              # noqa: E402

_src = inspect.getsource(T3.run_episode3)
_old = '            msgs.append({"role": "assistant", "content": text})\n'
assert _src.count(_old) == 1, "run_episode3 changed; fix not applied"
_src = _src.replace(_old, '            msgs.append({"role": "assistant", "content": cut_after_first_call(text) if call else text})\n')
T3.__dict__["cut_after_first_call"] = cut_after_first_call
exec(compile(_src, "tau_fix:run_episode3", "exec"), T3.__dict__)

if __name__ == "__main__":
    T3.main()
