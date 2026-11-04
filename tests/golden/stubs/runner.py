"""Run an engine's real CLI with the network blocked and external services faked.

    python runner.py <stub_name> <engine_script.py> [engine args...]

``<stub_name>`` selects ``tests/golden/stubs/stub_<stub_name>.py``, which must define
``install(ctx)``.  The runner then:

* blocks every socket connection, so an unstubbed request fails loudly instead of
  reaching the internet;
* lets the stub replace ``urllib.request.urlopen`` (and fake optional packages);
* executes the engine as ``__main__`` exactly like ``python engine.py``;
* writes every recorded request to ``$USIPHUS_STUB_LOG`` (JSON) even if the engine exits non-zero.
"""
from __future__ import annotations

import importlib.util
import json
import os
import runpy
import socket
import sys
from pathlib import Path

STUBS = Path(__file__).resolve().parent
ROOT = STUBS.parents[2]


class NetworkBlocked(RuntimeError):
    """Raised when an engine tries to open a real network connection."""


def _blocked(*_args, **_kwargs):
    raise NetworkBlocked("network access is blocked in golden tests; stub the request instead")


class Context:
    def __init__(self) -> None:
        self.requests: list[dict] = []


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: runner.py <stub_name> <script> [args...]", file=sys.stderr)
        return 64
    stub_name, script, args = argv[0], Path(argv[1]).resolve(), argv[2:]
    socket.socket.connect = _blocked          # type: ignore[assignment]
    socket.create_connection = _blocked       # type: ignore[assignment]
    socket.getaddrinfo = _blocked             # type: ignore[assignment]
    sys.path.append(str(STUBS))               # lets stub modules import _common / fake_genanki
    spec = importlib.util.spec_from_file_location(f"golden_stub_{stub_name}", STUBS / f"stub_{stub_name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = Context()
    module.install(ctx)
    sys.path.insert(0, str(script.parent))
    sys.argv = [str(script), *args]
    code = 0
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        if isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
    finally:
        log = os.environ.get("USIPHUS_STUB_LOG")
        if log:
            Path(log).write_text(json.dumps(ctx.requests, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                                 encoding="utf-8", newline="\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
