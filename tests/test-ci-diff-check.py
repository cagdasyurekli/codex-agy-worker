#!/usr/bin/env python3
"""Offline adversarial tests for the bounded Git cat-file batch reader."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import signal
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "scripts" / "ci_diff_check.py"
spec = importlib.util.spec_from_file_location("ci_diff_check", MODULE_PATH)
if spec is None or spec.loader is None:
    raise SystemExit(1)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

FAKE_GIT = r'''#!/usr/bin/python3
import os,signal,subprocess,sys,time

count_path=sys.argv[0]+".count"
try:
 count=int(open(count_path,encoding="ascii").read())
except (FileNotFoundError,ValueError):
 count=0
with open(count_path,"w",encoding="ascii") as stream:
 stream.write(str(count+1))
requests=[line.decode("ascii") for line in sys.stdin.buffer.read().splitlines()]
if not requests:
 raise SystemExit(2)
mode=requests[0][0]
def frame(oid,kind="blob",body=b"clean\n",size=None):
 if size is None: size=len(body)
 sys.stdout.buffer.write((oid+" "+kind+" "+str(size)+"\n").encode("ascii"))
 sys.stdout.buffer.write(body+b"\n")
if mode=="b":
 sys.stdout.write(requests[0]+" missing\n")
elif mode=="c":
 frame("d"*40)
elif mode=="d":
 frame(requests[0],"tree")
elif mode=="e":
 frame(requests[0],size=5)
elif mode=="f":
 for oid in reversed(requests): frame(oid)
elif mode=="1":
 sys.stdout.write(requests[0]+" blob 6\ncl")
elif mode=="2":
 frame(requests[0]); sys.stdout.write("extra")
elif mode=="3":
 frame(requests[0]); sys.stderr.write("x"*9000)
elif mode=="4":
 time.sleep(2)
elif mode=="5":
 ready=sys.argv[0]+".ready"; late=sys.argv[0]+".late"
 open(ready,"w",encoding="ascii").write("ready")
 child=os.fork()
 if child==0:
  signal.signal(signal.SIGTERM,signal.SIG_IGN)
  time.sleep(.6)
  open(late,"w",encoding="ascii").write("late")
  os._exit(0)
 signal.signal(signal.SIGTERM,signal.SIG_IGN)
 time.sleep(2)
elif mode=="6":
 sys.stdout.write("malformed\n")
elif mode=="7":
 sys.stdout.write(requests[0]+" blob 2097153\n")
elif mode=="8":
 frame(requests[0]); sys.stderr.write("x")
else:
 for oid in requests: frame(oid)
'''


class Failure(Exception):
    pass


passed = 0
failed = 0


def check(name: str, condition: bool) -> None:
    global passed, failed
    if condition:
        passed += 1
    else:
        failed += 1
        print("FAIL " + name)


def oid(prefix: str, index: int = 0) -> str:
    return prefix + f"{index:039x}"


def rejected(values: list[str], timeout: float = 1.0) -> bool:
    old_timeout = module.TIMEOUT_SECONDS
    module.TIMEOUT_SECONDS = timeout
    try:
        module._batch_blobs(values, time.monotonic() + timeout + 0.2)
    except module.CheckRejected:
        return True
    finally:
        module.TIMEOUT_SECONDS = old_timeout
    return False


with tempfile.TemporaryDirectory(prefix="agy-ci-batch-test.") as directory:
    fake = Path(directory) / "fake-git"
    fake.write_text(FAKE_GIT, encoding="ascii")
    fake.chmod(0o700)
    module.GIT = str(fake)

    result = module._batch_blobs([oid("a")], time.monotonic() + 2)
    check("valid frame", result == {oid("a"): b"clean\n"})
    check("missing object", rejected([oid("b")]))
    check("wrong object id", rejected([oid("c")]))
    check("wrong object type", rejected([oid("d")]))
    check("wrong object size", rejected([oid("e")]))
    check("wrong response order", rejected([oid("f"), oid("a", 1)]))
    check("truncated response", rejected([oid("1")]))
    check("extra response bytes", rejected([oid("2")]))
    check("stderr cap", rejected([oid("3")]))
    check("timeout", rejected([oid("4")], 0.15))
    check("malformed header", rejected([oid("6")]))
    check("oversized declaration", rejected([oid("7")]))
    check("bounded nonempty stderr", rejected([oid("8")]))

    count_path = Path(str(fake) + ".count")
    count_path.unlink(missing_ok=True)
    maximum = [oid("a", index) for index in range(module.MAX_PATHS)]
    started = time.monotonic()
    maximum_result = module._batch_blobs(maximum, time.monotonic() + 5)
    elapsed = time.monotonic() - started
    check(
        "one batch subprocess for max paths",
        len(maximum_result) == module.MAX_PATHS
        and count_path.read_text(encoding="ascii") == "1",
    )
    check("max paths comfortably bounded", elapsed < 5)

    late_path = Path(str(fake) + ".late")
    ready_path = Path(str(fake) + ".ready")
    for path in (late_path, ready_path):
        path.unlink(missing_ok=True)
    check("descendant timeout rejects", rejected([oid("5")], 0.15))
    time.sleep(0.7)
    check("descendant timeout leaves no late child", not late_path.exists())

    for path in (late_path, ready_path):
        path.unlink(missing_ok=True)
    old_timeout = module.TIMEOUT_SECONDS
    module.TIMEOUT_SECONDS = 2

    def send_term() -> None:
        deadline = time.monotonic() + 1
        while not ready_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        os.kill(os.getpid(), signal.SIGTERM)

    sender = threading.Thread(target=send_term)
    sender.start()
    interrupted = False
    try:
        module._batch_blobs([oid("5")], time.monotonic() + 2)
    except module.BatchInterrupted as exc:
        interrupted = exc.signum == signal.SIGTERM
    finally:
        module.TIMEOUT_SECONDS = old_timeout
    sender.join(timeout=1)
    time.sleep(0.7)
    check("signal propagates exact interruption", interrupted)
    check("signal cleanup leaves no late child", not late_path.exists())

print(f"ci diff batch offline tests: {passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
