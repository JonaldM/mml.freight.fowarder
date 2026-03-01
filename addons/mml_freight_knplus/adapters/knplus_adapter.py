# Backwards-compatibility shim.
# Registration and logic have moved to kn_mock_adapter.py (registered) and kn_adapter.py (live).
from .kn_adapter import KnAdapter as KnplusAdapter  # noqa: F401
