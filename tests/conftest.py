"""Pytest setup for the sonar-router tests.

Puts the skill's ``scripts/`` directory (so ``import route`` works) and this
``tests/`` directory (so ``import fixtures`` works) on ``sys.path``, regardless
of where pytest is invoked from. Run with: ``python -m pytest`` from the skill
root, or ``python -m pytest path/to/sonar-router/tests``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "scripts"

for _p in (_SCRIPTS, _HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
