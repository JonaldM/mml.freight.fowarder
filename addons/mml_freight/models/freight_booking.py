from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)

BOOKING_STATES = [
    ('draft', 'Draft'),
    ('confirmed', 'Confirmed'),
    ('cargo_ready', 'Cargo Ready'),
    ('picked_up', 'Picked Up'),
    ('in_transit', 'In Transit'),
    ('arrived_port', 'Arrived at Port'),
    ('customs', 'Customs Clearance'),
    ('delivered', 'Delivered'),
    ('received', 'Received at Warehouse'),
    ('cancelled', 'Cancelled'),
    ('error', 'Error'),
]

TRANSPORT_MODES = [
    ('road', 'Road'),
    ('air', 'Air'),
    ('sea_lcl', 'Sea LCL'),
    ('sea_fcl', 'Sea FCL'),
    ('rail', 'Rail'),
    ('express', 'Express'),
]


class FreightBooking(models.Model):
    _name = 'freight.booking'
    _description = 'Freight Booking'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name desc'

    name = fields.Char('Reference', readonly=True, default='New', copy=False)
    state = fields.Selection(
        BOOKING_STATES, default='draft', required=True, tracking=True,
    )
    tender_id = fields.Many2one('freight.tender', ondelete='restrict', index=True)
    carrier_id = fields.Many2one('delivery.carrier', required=True, ondelete='restrict')
    purchase_order_id = fields.Many2one('purchase.order', ondelete='restrict', index=True)

    tpl_message_id = fields.Many2one(
        '3pl.message', string='3PL Message', ondelete='set null', readonly=True,
    )

    carrier_booking_id = fields.Char('Carrier Booking Ref', tracking=True)
    carrier_shipment_id = fields.Char('Carrier Shipment ID')
    carrier_tracking_url = fields.Char('Tracking URL')

    currency_id = fields.Many2one('res.currency', required=True)
    booked_rate = fields.Monetary('Booked Rate', currency_field='currency_id')
    actual_rate = fields.Monetary('Actual Rate', currency_field='currency_id')
    invoice_id = fields.Many2one('account.move', string='Freight Invoice', ondelete='set null')

    current_status = fields.Char(
        'Current Status', compute='_compute_current_status', store=True,
    )
    eta = fields.Datetime('ETA')
    actual_pickup_date = fields.Datetime('Actual Pickup')
    actual_delivery_date = fields.Datetime('Actual Delivery')

    transport_mode = fields.Selection(TRANSPORT_MODES)
    vessel_name = fields.Char('Vessel')
    voyage_number = fields.Char('Voyage No.')
    container_number = fields.Char('Container No.')
    bill_of_lading = fields.Char('Bill of Lading')
    awb_number = fields.Char('AWB No.')

    tracking_event_ids = fields.One2many(
        'freight.tracking.event', 'booking_id', string='Tracking Events',
    )
    document_ids = fields.One2many('freight.document', 'booking_id', string='Documents')
    label_attachment_id = fields.Many2one(
        'ir.attachment', string='Label', ondelete='set null',
    )
    pod_attachment_id = fields.Many2one(
        'ir.attachment', string='POD', ondelete='set null',
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('freight.booking') or 'New'
        return super().create(vals_list)

    @api.depends('tracking_event_ids.status', 'tracking_event_ids.event_date')
    def _compute_current_status(self):
        for b in self:
            latest = b.tracking_event_ids.sorted('event_date', reverse=True)
            b.current_status = latest[0].status if latest else ''

    def action_confirm(self):
        self.write({'state': 'confirmed'})
        self._queue_3pl_inward_order()
        return True

    def action_cancel(self):
        self.write({'state': 'cancelled'})
        return True

    def _queue_3pl_inward_order(self):
        """Queue an inward order notice via stock_3pl_core message queue.

        Graceful no-op if stock_3pl_core is not installed or no connector
        is configured for the purchase order's warehouse.
        """
        if 'stock_3pl_core' not in self.env.registry._init_modules:
            _logger.info(
                'freight.booking %s: stock_3pl_core not installed — skipping 3PL handoff',
                self.name,
            )
            return
        po = self.purchase_order_id
        if not po:
            return
        warehouse = po.picking_type_id.warehouse_id if po.picking_type_id else False
        if not warehouse:
            return
        connector = self.env['3pl.connector'].search([
            ('warehouse_id', '=', warehouse.id),
            ('active', '=', True),
        ], limit=1)
        if not connector:
            _logger.info(
                'freight.booking %s: no active 3PL connector for warehouse %s — skipping',
                self.name, warehouse.name,
            )
            return
        msg = self.env['3pl.message'].create({
            'connector_id': connector.id,
            'direction': 'outbound',
            'document_type': 'inward_order',
            'action': 'create',
            'ref_model': 'purchase.order',
            'ref_id': po.id,
        })
        self.tpl_message_id = msg
        _logger.info(
            'freight.booking %s: queued 3pl.message %s for PO %s',
            self.name, msg.id, po.name,
        )

    def _handle_dsv_tracking_webhook(self, carrier, body):
        """Handle DSV tracking webhook payload.

        SECURITY: Caller (dsv_webhook.py) MUST validate HMAC signature before invoking this.
        When implementing: validate booking belongs to this carrier before writing any fields;
        sanitise all string values from body before storing (max length, strip control chars).
        """
        _logger.info('DSV tracking webhook received for carrier %s', carrier.id)

    @api.model
    def cron_sync_tracking(self):
        """Cron: sync tracking for all active bookings."""
        active_states = ['confirmed', 'cargo_ready', 'picked_up', 'in_transit', 'arrived_port', 'customs']
        bookings = self.search([('state', 'in', active_states)])
        for booking in bookings:
            try:
                booking._sync_tracking()
            except Exception as e:
                _logger.error('Tracking sync failed for booking %s: %s', booking.name, e)

    def _sync_tracking(self):
        """Sync tracking events from carrier adapter."""
        adapter = self.env['freight.adapter.registry'].get_adapter(self.carrier_id)
        if not adapter:
            return
        events = adapter.get_tracking(self)
        for evt in events:
            existing = self.tracking_event_ids.filtered(
                lambda e: e.status == evt.get('status') and str(e.event_date) == evt.get('event_date', '')
            )
            if not existing:
                self.env['freight.tracking.event'].create({
                    'booking_id': self.id,
                    'event_date': evt['event_date'],
                    'status': evt['status'],
                    'location': evt.get('location', ''),
                    'description': evt.get('description', ''),
                    'raw_payload': evt.get('raw_payload', ''),
                })
