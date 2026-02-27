"""CDK compatibility layer.

This module centralizes all imports from the Internet Computer CDK (currently Basilisk).
To switch CDKs, only this file needs to be modified.
"""

try:
    from basilisk import *  # noqa: F401, F403
    from basilisk import ic  # noqa: F401 - explicit for IDE support

    HAS_CDK = True
except ImportError:
    HAS_CDK = False
    ic = None  # type: ignore
