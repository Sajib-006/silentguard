"""Command line: ``python -m agentorchbench <command>``.

    python -m agentorchbench suites               # list the task suites
    python -m agentorchbench faults               # list the silent fault types
    python -m agentorchbench serve --domain bio   # start the services until Ctrl-C
"""
from __future__ import annotations

import argparse
import time

from . import FAULTS, SUITES, load_tasks, start_services


def main() -> None:
    ap = argparse.ArgumentParser(prog="agentorchbench")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("suites")
    sub.add_parser("faults")
    s = sub.add_parser("serve")
    s.add_argument("--domain", default="bio", choices=["bio", "retail"])
    s.add_argument("--worker", type=int, default=0)
    a = ap.parse_args()
    if a.cmd == "suites":
        for name, (_, desc) in SUITES.items():
            print(f"{name:10s} {len(load_tasks(name)):4d} tasks  {desc}")
    elif a.cmd == "faults":
        for name, desc in FAULTS.items():
            print(f"{name:16s} {desc}")
    else:
        with start_services(a.domain, a.worker) as svc:
            for name, port in svc.ports.items():
                print(f"{name:10s} http://127.0.0.1:{port}")
            print("Services are running. Press Ctrl-C to stop.")
            try:
                while True:
                    time.sleep(3600)
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    main()
