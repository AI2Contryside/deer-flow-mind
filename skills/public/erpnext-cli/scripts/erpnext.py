#!/usr/bin/env python3
"""Entry point for the `erpnext-cli` DeerFlow skill.

This file is a thin launcher. It puts the sibling ``erpnext_pkg/`` package on
``sys.path`` and dispatches to its Click root group. All business-process
logic lives in ``erpnext_pkg/`` (copied verbatim from the upstream
``cli-anything-erpnext`` package — see ``references/`` for the upstream
catalog).

Agents inside the DeerFlow sandbox invoke this script by absolute path:

    python /mnt/skills/public/erpnext-cli/scripts/erpnext.py --json \
        session status

    python /mnt/skills/public/erpnext-cli/scripts/erpnext.py --json \
        selling order-to-cash --customer "Alice Ltd" \
        --item "WIDGET-001:10:99.50"

Credentials come from env vars (``ERPNEXT_URL``, ``ERPNEXT_API_KEY``,
``ERPNEXT_API_SECRET``, ``ERPNEXT_USERNAME``, ``ERPNEXT_PASSWORD``,
``ERPNEXT_VERIFY_SSL``) or from a persistent session file. See SKILL.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the sibling ``erpnext_pkg`` importable without requiring a pip install.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from erpnext_pkg.cli import main  # noqa: E402 — path mutation must come first


if __name__ == "__main__":
    main()
