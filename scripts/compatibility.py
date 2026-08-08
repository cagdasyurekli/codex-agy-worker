#!/usr/bin/env python3
"""Repository compatibility entry point for the canonical portable runtime."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True


TARGET = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "agy-worker"
    / "runtime"
    / "scripts"
    / "compatibility.py"
)
os.execv(sys.executable, [sys.executable, "-B", str(TARGET), *sys.argv[1:]])
