from odoo.addons.mml_freight.adapters.base_adapter import FreightAdapterBase


class DsvGenericAdapter(FreightAdapterBase):
    """Live DSV Generic adapter scaffold — requires API keys."""
    def request_quote(self, tender): raise NotImplementedError('Use x_dsv_environment=demo.')
    def create_booking(self, tender, quote): raise NotImplementedError
    def get_tracking(self, booking): raise NotImplementedError
