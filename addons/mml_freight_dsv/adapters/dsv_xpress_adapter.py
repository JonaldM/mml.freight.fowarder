from odoo.addons.mml_freight.adapters.base_adapter import FreightAdapterBase


class DsvXpressAdapter(FreightAdapterBase):
    """Live DSV XPress adapter scaffold — requires XPress credentials."""
    def request_quote(self, tender): raise NotImplementedError('Use x_dsv_environment=demo.')
    def create_booking(self, tender, quote): raise NotImplementedError
    def get_tracking(self, booking): raise NotImplementedError
