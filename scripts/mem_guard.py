#!/usr/bin/env python3
"""Run a command under a peak-RSS cap (watchdog, kills on exceed).

Usage:
    python3 scripts/mem_guard.py [--cap-mb N] -- <cmd> [args...]

Default cap: LEASH_MEM_CAP_MB env or 4096 MiB. Exceeding the cap
SIGKILLs the child tree and prints "memory cap" to stderr, exiting
with 137. This is the user-session safety net for test runs
(pytest, benchmarks, bootstrap) so a runaway child cannot eat the
host — see scripts/benchmark.py for the in-harness variant.
"""
import os
import subprocess
import sys
import time


def parse_args(argv):
    cap_mb = int(os.environ.get("LEASH_MEM_CAP_MB", "4096"))
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--cap-mb", "--cap") and i + 1 < len(argv):
            cap_mb = int(argv[i + 1])
            i += 2
        elif a == "--":
            return cap_mb, argv[i + 1:]
        elif a.startswith("--cap-mb="):
            cap_mb = int(a.split("=", 1)[1])
            i += 1
        else:
            return cap_mb, argv[i:]
    return cap_mb, []


def vmrss_kb(pid):
    try:
        with open(f"/proc/{pid}/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except (FileNotFoundError, PermissionError, IndexError, ValueError):
        return None
    return None


def tree_rss_kb(root_pid):
    """Sum VmRSS of root + descendants (best effort via /proc)."""
    total = 0
    try:
        pids = [int(d) for d in os.listdir("/proc") if d.isdigit()]
    except OSError:
        return vmrss_kb(root_pid) or 0
    children = {}
    for p in pids:
        try:
            with open(f"/proc/{p}/stat", "rb") as fh:
                data = fh.read().decode("utf-8", errors="replace")
            # fields after comm: (state, ppid, ...)
            rparen = data.rsplit(")", 1)[-1].split()
            ppid = int(rparen[1])
        except Exception:
            continue
        children.setdefault(ppid, []).append(p)
    stack = [root_pid]
    seen = set()
    while stack:
        p = stack.pop()
        if p in seen:
            continue
        seen.add(p)
        v = vmrss_kb(p)
        if v:
            total += v
        stack.extend(children.get(p, []))
    return total


def main():
    cap_mb, cmd = parse_args(sys.argv[1:])
    if not cmd:
        print("usage: mem_guard.py [--cap-mb N] -- <cmd> [args...]", file=sys.stderr)
        return 2
    cap_bytes = cap_mb << 20
    print(f"[mem_guard] cap={cap_mb}MiB cmd={' '.join(cmd[:6])}", flush=True)
    proc = subprocess.Popen(cmd)
    peak = 0
    blew = False
    while True:
        rc = proc.poll()
        rss = tree_rss_kb(proc.pid)
        if rss is not None and rss > peak:
            peak = rss
        if rss is not None and (rss << 10) > cap_bytes:
            print(f"[mem_guard] EXCEEDED {rss}KB > {cap_mb}MiB, killing {proc.pid}", file=sys.stderr, flush=True)
            blew = True
            try:
                import signal
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            break
        if rc is not None:
            break
        time.sleep(0.02)
    try:
        proc.wait()
    except Exception:
        pass
    print(f"[mem_guard] peak={peak}KB rc={proc.returncode}", flush=True)
    if blew:
        return 137
    return proc.returncode or 0


if __name__ == "__main__":
    sys.exit(main())
