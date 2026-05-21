"""GEOS eval harness package.

This package exists to keep individual modules small (no ~3500-line wall).
Importing this package runs the env-var aliasing that legacy callers depend on
(see :mod:`runner._env_bootstrap`).
"""

from . import _env_bootstrap  # noqa: F401  -- side-effect import

__all__ = ["_env_bootstrap"]
