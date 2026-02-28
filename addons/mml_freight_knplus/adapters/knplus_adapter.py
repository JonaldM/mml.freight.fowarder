from odoo.addons.mml_freight.adapters.base_adapter import FreightAdapterBase
from odoo.addons.mml_freight.models.freight_adapter_registry import register_adapter


@register_adapter('knplus')
class KnplusAdapter(FreightAdapterBase):
    """K+N stub — correct interface, NotImplementedError on all methods."""

    def request_quote(self, tender):
        raise NotImplementedError('K+N quote not implemented. Disable auto_tender on K+N carriers.')

    def create_booking(self, tender, quote):
        raise NotImplementedError('K+N booking not implemented.')

    def get_tracking(self, booking):
        raise NotImplementedError('K+N tracking not implemented.')
