# kn_mock_adapter MUST be imported first — it registers 'knplus' via @register_adapter.
# kn_adapter is the unregistered live adapter used by KnMockAdapter in production mode.
from .kn_mock_adapter import KnMockAdapter
from .kn_adapter import KnAdapter
