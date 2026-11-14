"""Start and stop the AgentOrchBench services as local processes (no Docker needed).

The bio domain has eight services: transfer, registry, catalog, task, pipeline, publish, meta and
galaxy. The retail domain has four: orders, inventory, customers and tickets. Each service gets
port ``base + i + 1`` with ``base = 8000 + 10 * worker``, so several stacks can run side by side.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

from .paths import APIS

BIO = [("transfer", "transfer_api"), ("registry", "registry_api"), ("catalog", "catalog_api"),
       ("task", "task_api"), ("pipeline", "pipeline_api"), ("publish", "publish_api"),
       ("meta", "meta_api"), ("galaxy", "galaxy_api")]
RETAIL = ["orders", "inventory", "customers", "tickets"]


@dataclass
class Services:
    domain: str
    worker: int
    ports: dict = field(default_factory=dict)
    procs: list = field(default_factory=list, repr=False)
    workdir: Path | None = None

    def url(self, name: str) -> str:
        return f"http://127.0.0.1:{self.ports[name]}"

    def stop(self) -> None:
        for p in self.procs:
            p.terminate()
        for p in self.procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        self.procs.clear()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stop()


def start_services(domain: str = "bio", worker: int = 0, timeout: float = 60.0,
                   python: str | None = None) -> Services:
    """Start one stack and wait until every service answers ``/health``.

    Use it as a context manager so the processes stop at the end::

        with start_services("bio", worker=3) as svc:
            print(svc.url("catalog"))
    """
    python = python or sys.executable
    base = 8000 + 10 * worker
    workdir = Path(tempfile.mkdtemp(prefix=f"aob_w{worker}_"))
    svc = Services(domain, worker, workdir=workdir)
    names = [n for n, _ in BIO] if domain == "bio" else RETAIL
    for i, name in enumerate(names):
        port = base + i + 1
        env = dict(os.environ, PORT=str(port), DB_PATH=str(workdir / f"{name}.db"))
        if domain == "bio":
            cwd = APIS / dict(BIO)[name]
        else:
            cwd = APIS / "domain2_retail"
            env["ROLE"] = name
        log = open(workdir / f"{name}.log", "w")
        svc.procs.append(subprocess.Popen([python, "app.py"], cwd=cwd, env=env, stdout=log,
                                          stderr=subprocess.STDOUT))
        svc.ports[name] = port
    deadline = time.time() + timeout
    waiting = dict(svc.ports)
    while waiting and time.time() < deadline:
        for name, port in list(waiting.items()):
            try:
                if requests.get(f"http://127.0.0.1:{port}/health", timeout=2).ok:
                    waiting.pop(name)
            except requests.RequestException:
                pass
        time.sleep(0.5)
    if waiting:
        svc.stop()
        raise RuntimeError(f"services did not start: {sorted(waiting)}; logs in {workdir}")
    return svc
