"""Post-migration for mml_freight 19.0.1.0.1.

Ensures the FreightService is registered in mml.registry after upgrade.

Why this is needed:
    post_init_hook only runs on fresh module install (-i), NOT on upgrade (-u).
    When upgrading an existing Odoo instance, the ir.config_parameter entry for
    'mml_registry.service.freight' may be absent or stale if the module was
    previously at a lower version, leaving mml.registry returning NullService
    for every call to service('freight').

    The registry.register() call writes the class path to ir.config_parameter
    so worker processes can re-hydrate it after fork via importlib.

    The folder is versioned 19.0.1.0.1 (one patch above the prior installed
    19.0.1.0.0) so Odoo's migration manager actually runs it on -u; a folder
    equal to the installed version is skipped.

Manual verification:
    SELECT value FROM ir_config_parameter
    WHERE key = 'mml_registry.service.freight';
    -- Expected: 'odoo.addons.mml_freight.services.freight_service::FreightService'
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Re-register FreightService in mml.registry.

    Args:
        cr: Odoo cursor (psycopg2 cursor wrapper).
        version: Module version string before the upgrade. None/empty means
            fresh install -- post_init_hook already handles that case, but
            calling register() again is harmless (idempotent).
    """
    from odoo import api, SUPERUSER_ID
    from odoo.addons.mml_freight.services.freight_service import FreightService

    env = api.Environment(cr, SUPERUSER_ID, {})
    env['mml.registry'].register('freight', FreightService)
    _logger.info(
        'mml_freight 19.0.1.0.1: re-registered FreightService in mml.registry'
    )
