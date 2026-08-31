#!/usr/bin/env python3
"""Stable 1.1.22 CLI adapter for the shared models-capture operation."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from version_manifest_capture_runner import main_for_version


if __name__ == "__main__":
    os._exit(main_for_version("1.1.22", sys.argv[1:]))
