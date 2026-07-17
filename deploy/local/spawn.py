#!/usr/bin/env python3
"""Detach-and-exec launcher for `make up`.

Puts the child in its OWN session (os.setsid) with no controlling terminal, so
it keeps running after `make up` returns — exactly like `docker compose up -d`.
A plain `nohup ... &` only ignores SIGHUP; a fresh session also survives the
parent's process-group teardown. stdout/stderr go to the given logfile.

Usage: spawn.py <logfile> <cmd> [args...]
       (cmd may be `env VAR=val ... /path/to/bin` — env is applied by exec'ing env)
"""
import os
import sys

logfile = sys.argv[1]
argv = sys.argv[2:]
if not argv:
    sys.stderr.write("spawn.py: no command\n")
    sys.exit(2)

os.setsid()  # new session + process group; detached from the parent's group
fd = os.open(logfile, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
os.dup2(fd, 1)
os.dup2(fd, 2)
devnull = os.open(os.devnull, os.O_RDONLY)
os.dup2(devnull, 0)
os.execvp(argv[0], argv)
