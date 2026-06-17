"""Version helpers for context-budgeter."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version

_DISTRIBUTION_NAME = "context-budgeter"

try:
    __version__ = _distribution_version(_DISTRIBUTION_NAME)
except PackageNotFoundError:
    __version__ = "0.1.1"
