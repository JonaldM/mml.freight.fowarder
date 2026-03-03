from odoo import models, fields, api


class PurchaseOrderDsv(models.Model):
    _inherit = 'purchase.order'

    x_dsv_booking_id = fields.Many2one(
        'freight.booking',
        string='Active DSV Booking',
        compute='_compute_dsv_booking',
        store=False,
    )

    @api.depends()  # Recomputes on record load only. Booking visibility refreshes on PO reload.
    def _compute_dsv_booking(self):
        for po in self:
            po.x_dsv_booking_id = self.env['freight.booking'].search([
                ('po_ids', 'in', po.id),
                ('carrier_id.delivery_type', 'in', ('dsv_generic', 'dsv_xpress')),
                ('state', 'not in', ('delivered', 'cancelled', 'received')),
            ], limit=1)

    def action_open_dsv_doc_upload(self):
        """Open the Send Documents to DSV wizard."""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Send Documents to DSV',
            'res_model': 'freight.dsv.doc.upload.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_po_id': self.id,
                'active_id': self.id,
            },
        }
