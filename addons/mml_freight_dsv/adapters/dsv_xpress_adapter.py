from odoo.addons.mml_freight.adapters.base_adapter import FreightAdapterBase
from odoo.exceptions import UserError

_DSV_XPRESS_NOT_CONFIGURED = (
    'DSV XPress adapter is not yet configured. Contact your system administrator.'
)


class DsvXpressAdapter(FreightAdapterBase):
    """Live DSV XPress adapter scaffold — requires XPress credentials."""

    def request_quote(self, tender):
        raise UserError(_DSV_XPRESS_NOT_CONFIGURED)

    def create_booking(self, tender, quote):
        raise UserError(_DSV_XPRESS_NOT_CONFIGURED)

    def get_tracking(self, booking):
        raise UserError(_DSV_XPRESS_NOT_CONFIGURED)
