import logging

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

INCOTERMS_BUYER = {'EXW', 'FCA', 'FOB', 'FAS'}
INCOTERMS_SELLER = {'CFR', 'CIF', 'CPT', 'CIP', 'DAP', 'DPU', 'DDP'}

FREIGHT_RESPONSIBILITY = [
    ('buyer', 'Buyer (MML arranges)'),
    ('seller', 'Seller (Supplier arranges)'),
    ('na', 'Not Applicable'),
]

MODE_PREFERENCES = [
    ('any', 'Any'),
    ('sea', 'Sea'),
    ('air', 'Air'),
    ('road', 'Road'),
]


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    freight_responsibility = fields.Selection(
        FREIGHT_RESPONSIBILITY,
        string='Freight Responsibility',
        compute='_compute_freight_responsibility',
        store=True,
        readonly=False,
    )
    freight_tender_id = fields.Many2one(
        'freight.tender', string='Freight Tender', ondelete='set null',
    )
    freight_booking_id = fields.Many2one(
        'freight.booking',
        related='freight_tender_id.booking_id',
        string='Freight Booking',
        readonly=True,
    )
    freight_status = fields.Selection(
        related='freight_tender_id.booking_id.state',
        string='Freight Status',
        readonly=True,
    )
    freight_cost = fields.Monetary(
        related='freight_tender_id.booking_id.booked_rate',
        string='Freight Cost',
        readonly=True,
        currency_field='currency_id',
    )
    freight_carrier_name = fields.Char(
        related='freight_tender_id.booking_id.carrier_id.name',
        string='Freight Carrier',
        readonly=True,
    )
    freight_tracking_url = fields.Char(
        related='freight_tender_id.booking_id.carrier_tracking_url',
        string='Tracking URL',
        readonly=True,
    )
    freight_eta = fields.Datetime(
        related='freight_tender_id.booking_id.eta',
        string='ETA',
        readonly=True,
    )
    cargo_ready_date = fields.Date('Cargo Ready Date')
    required_delivery_date = fields.Date('Required at Warehouse')
    freight_mode_preference = fields.Selection(MODE_PREFERENCES, default='any')
    tender_count = fields.Integer(compute='_compute_tender_count')

    @api.depends('incoterm_id', 'incoterm_id.code')
    def _compute_freight_responsibility(self):
        for po in self:
            code = po.incoterm_id.code if po.incoterm_id else False
            if not code:
                po.freight_responsibility = 'na'
            elif code in INCOTERMS_BUYER:
                po.freight_responsibility = 'buyer'
            elif code in INCOTERMS_SELLER:
                po.freight_responsibility = 'seller'
            else:
                po.freight_responsibility = 'na'

    def _compute_tender_count(self):
        # M1: use read_group to avoid N+1 search_count queries
        groups = self.env['freight.tender'].read_group(
            [('purchase_order_id', 'in', self.ids)],
            ['purchase_order_id'],
            ['purchase_order_id'],
        )
        counts = {g['purchase_order_id'][0]: g['purchase_order_id_count'] for g in groups}
        for po in self:
            po.tender_count = counts.get(po.id, 0)

    def action_view_freight_tenders(self):
        self.ensure_one()
        return {
            'name': 'Freight Tenders',
            'type': 'ir.actions.act_window',
            'res_model': 'freight.tender',
            'view_mode': 'list,form',
            'domain': [('purchase_order_id', '=', self.id)],
            'context': {'default_purchase_order_id': self.id},
        }

    def _populate_tender_packages(self, tender):
        """Create freight.tender.package lines from PO order lines."""
        warned = []
        fractional = []
        vals_list = []
        for line in self.order_line:
            tmpl = line.product_id.product_tmpl_id
            weight = (tmpl.x_freight_weight if tmpl else 0.0) or 0.0
            length = (tmpl.x_freight_length if tmpl else 0.0) or 0.0
            width  = (tmpl.x_freight_width  if tmpl else 0.0) or 0.0
            height = (tmpl.x_freight_height if tmpl else 0.0) or 0.0
            qty = line.product_qty
            if qty != round(qty):
                fractional.append(f'{line.product_id.name or "Unknown"} ({qty} → {round(qty)})')
            if not (weight and length and width and height):
                warned.append(line.product_id.name or 'Unknown')
            vals_list.append({
                'tender_id':    tender.id,
                'product_id':   line.product_id.id,
                'description':  line.product_id.name or '',
                'quantity':     round(qty),
                'weight_kg':    weight,
                'length_cm':    length,
                'width_cm':     width,
                'height_cm':    height,
                'is_dangerous': tmpl.x_dangerous_goods if tmpl else False,
                'hs_code':      getattr(line.product_id, 'hs_code', '') or '',  # may come from mml_edi
            })
        if vals_list:
            self.env['freight.tender.package'].create(vals_list)
        if warned:
            tender.message_post(
                body=(
                    f'Product(s) missing freight dimensions — package lines populated with zeros, '
                    f'please update before requesting quotes: {", ".join(warned)}'
                )
            )
        if fractional:
            tender.message_post(
                body=(
                    f'Product(s) with fractional quantities rounded to integers for freight packages: '
                    f'{", ".join(fractional)}. Verify package counts are correct.'
                )
            )

    def action_request_freight_tender(self):
        """Open a new freight tender linked to this PO."""
        self.ensure_one()
        if self.freight_tender_id:
            raise UserError(
                'A freight tender already exists for this order (%s). '
                'Archive it before creating a new one.' % self.freight_tender_id.name
            )
        tender = self.env['freight.tender'].create({
            'purchase_order_id': self.id,
            'company_id': self.company_id.id,
            'origin_partner_id': self.partner_id.id,
            'origin_country_id': self.partner_id.country_id.id if self.partner_id.country_id else False,
            'incoterm_id': self.incoterm_id.id if self.incoterm_id else False,
            'requested_pickup_date': self.cargo_ready_date,
            'requested_delivery_date': self.required_delivery_date,
            'goods_value': self.amount_untaxed,
            'currency_id': self.currency_id.id,
            'freight_mode_preference': self.freight_mode_preference or 'any',
        })
        self.freight_tender_id = tender
        self._populate_tender_packages(tender)
        return {
            'name': 'Freight Tender',
            'type': 'ir.actions.act_window',
            'res_model': 'freight.tender',
            'res_id': tender.id,
            'view_mode': 'form',
        }

    def button_confirm(self):
        """Override: auto-create freight tender when buyer controls the freight leg."""
        result = super().button_confirm()
        for po in self.filtered(lambda p: p.freight_responsibility == 'buyer'
                                          and not p.freight_tender_id):
            po._auto_create_freight_tender()
        return result

    def _auto_create_freight_tender(self):
        """Create a freight tender and fan out quote requests. Errors post to chatter."""
        self.ensure_one()

        # I3: skip if there are no order lines — nothing meaningful to quote
        if not self.order_line:
            _logger.info('Auto-tender skipped for PO %s: no order lines', self.name)
            return

        # C2: tender creation is a configuration-level operation — let UserError propagate
        # so Odoo surfaces the user-readable message in a dialog.
        try:
            self.action_request_freight_tender()   # creates tender + populates packages
        except UserError:
            raise
        except Exception as e:
            _logger.error(
                'Auto-tender failed for PO %s: %s', self.name, e, exc_info=True,
            )
            self.message_post(
                body=(
                    f'Auto freight tender failed: {e}. '
                    f'Please create a tender manually from the Freight tab.'
                ),
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )
            return

        # C2: quote fanout is best-effort — swallow failures so PO confirm always succeeds
        tender = self.freight_tender_id
        if tender:
            try:
                tender.action_request_quotes()
            except UserError as e:
                _logger.warning(
                    'Auto-tender PO %s: no eligible carriers — %s', self.name, e,
                )
                self.message_post(
                    body=(
                        f'Freight tender created but no eligible carriers are configured: {e}. '
                        f'Please set up freight carriers with Auto-Tender enabled and request quotes manually.'
                    ),
                    message_type='comment',
                    subtype_xmlid='mail.mt_note',
                )
            except Exception as e:
                _logger.error(
                    'Auto-tender PO %s: quote fanout failed — %s', self.name, e,
                    exc_info=True,
                )
                self.message_post(
                    body=(
                        f'Freight tender created but quote requests failed: {e}. '
                        f'The tender has been created — please request quotes manually from the Freight tab.'
                    ),
                    message_type='comment',
                    subtype_xmlid='mail.mt_note',
                )
