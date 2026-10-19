from unittest.mock import MagicMock
import sys
_m = MagicMock()
def __getattr__(n): return getattr(_m, n)
