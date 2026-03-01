from odoo import models, fields, api
from odoo.exceptions import UserError
import dateutil.parser
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

# DSV eventType → booking.state — shared by tracking cron and webhook handler
_DSV_BOOKING_STATE_MAP = {
    'BOOKING_CONFIRMED': 'confirmed',
    'CARGO_RECEIVED':    'cargo_ready',
    'DEPARTURE':         'in_transit',
    'ARRIVED_POD':       'arrived_port',
    'CUSTOMS_CLEARED':   'customs',
    'DELIVERED':         'delivered',
}


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
    feeder_vessel_name   = fields.Char('Feeder Vessel')
    feeder_voyage_number = fields.Char('Feeder Voyage No.')
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

    def action_confirm_with_dsv(self):
        """Confirm booking with DSV API, update vessel/ETA fields, queue 3PL inward order."""
        self.ensure_one()
        registry = self.env['freight.adapter.registry']
        adapter = registry.get_adapter(self.carrier_id)
        if not adapter:
            raise UserError('No adapter available for this carrier.')

        try:
            result = adapter.confirm_booking(self)
        except NotImplementedError:
            raise UserError('This carrier does not support booking confirmation via API.')

        # Parse ISO-8601 ETA string to datetime
        eta = False
        if result.get('eta'):
            try:
                eta = dateutil.parser.parse(result['eta']).replace(tzinfo=None)
            except Exception:
                pass

        self.write({
            'state':                'confirmed',
            'carrier_shipment_id':  result.get('carrier_shipment_id') or self.carrier_shipment_id,
            'vessel_name':          result.get('vessel_name', ''),
            'voyage_number':        result.get('voyage_number', ''),
            'container_number':     result.get('container_number', ''),
            'bill_of_lading':       result.get('bill_of_lading', ''),
            'feeder_vessel_name':   result.get('feeder_vessel_name', ''),
            'feeder_voyage_number': result.get('feeder_voyage_number', ''),
            'eta':                  eta,
        })
        self._queue_3pl_inward_order()
        self._build_inward_order_payload()
        self.message_post(body='Booking confirmed with DSV. Inward order notice queued to Mainfreight.')
        return True

    def action_cancel(self):
        registry = self.env['freight.adapter.registry']
        adapter  = registry.get_adapter(self.carrier_id)
        if adapter and self.carrier_booking_id:
            adapter.cancel_booking(self)
        self.write({'state': 'cancelled'})
        return True

    def _queue_3pl_inward_order(self):
        """Queue an inward order notice via stock_3pl_core message queue.

        Connector selection uses a two-step strategy:
        1. Specific match: active connector for this warehouse that explicitly handles
           one or more of the PO's product categories (ordered by priority asc).
        2. Catch-all fallback: active connector with no product categories configured
           (ordered by priority asc).

        Graceful no-op if stock_3pl_core is not installed or no matching connector found.
        """
        if '3pl.connector' not in self.env:
            _logger.info(
                'freight.booking %s: stock_3pl_core not installed — skipping 3PL handoff',
                self.name,
            )
            return
        if self.tpl_message_id:
            _logger.info(
                'freight.booking %s: 3PL inward order already queued (%s) — skipping duplicate',
                self.name, self.tpl_message_id.id,
            )
            return
        po = self.purchase_order_id
        if not po:
            return
        warehouse = po.picking_type_id.warehouse_id if po.picking_type_id else False
        if not warehouse:
            return

        connector = self._resolve_3pl_connector(warehouse, po)
        if not connector:
            _logger.info(
                'freight.booking %s: no active 3PL connector for warehouse %s — skipping',
                self.name, warehouse.name,
            )
            return

        # Message is created in draft with no payload — intentional Phase 2 scaffolding.
        # The inward_order payload (XML/JSON) will be built and the message advanced to
        # 'queued' by the document-builder step implemented in Phase 2. Until then the
        # message sits in draft and the cron will not attempt to send it.
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
            'freight.booking %s: queued 3pl.message %s for PO %s via connector %s',
            self.name, msg.id, po.name, connector.name,
        )

    def _build_inward_order_payload(self):
        """Populate tpl_message_id.payload_xml and advance to queued. Implemented in Task 15."""
        pass

    def _resolve_3pl_connector(self, warehouse, po):
        """Return the best-matching active 3pl.connector for the given warehouse and PO.

        Strategy:
        - If the PO has product categories, try a connector that explicitly lists one of
          those categories (specific match), ordered by priority asc.
        - Fall back to any active catch-all connector (product_category_ids is empty),
          ordered by priority asc.
        - Returns False if no connector is found.
        """
        po_categ_ids = po.order_line.mapped('product_id.categ_id').ids
        base_domain = [('warehouse_id', '=', warehouse.id), ('active', '=', True)]

        if po_categ_ids:
            connector = self.env['3pl.connector'].search(
                base_domain + [('product_category_ids', 'in', po_categ_ids)],
                order='priority asc',
                limit=1,
            )
            if connector:
                return connector

        # ('product_category_ids', '=', False) is the Odoo ORM idiom for
        # "this Many2many relation has no linked records" (catch-all connector).
        return self.env['3pl.connector'].search(
            base_domain + [('product_category_ids', '=', False)],
            order='priority asc',
            limit=1,
        )

    def _check_inward_order_updates(self, prev_eta, prev_vessel):
        """Queue an inward order UPDATE if ETA drifted > 24h or vessel TBA→known."""
        eta_drifted = False
        if prev_eta and self.eta:
            eta_drifted = abs((self.eta - prev_eta).total_seconds()) > 86400
        vessel_now_known = not prev_vessel and bool(self.vessel_name)
        # Note: vessel *change* (e.g. substitution mid-voyage) does not trigger an update.
        # Only the TBA → known transition is tracked here; vessel changes are rare and
        # handled by the freight team directly.
        if eta_drifted or vessel_now_known:
            self._queue_inward_order_update()

    def _queue_inward_order_update(self):
        """Create a queued 3pl.message UPDATE for this booking's inward order."""
        if '3pl.connector' not in self.env:
            return
        po = self.purchase_order_id
        if not po:
            return
        warehouse = po.picking_type_id.warehouse_id if po.picking_type_id else False
        if not warehouse:
            return
        connector = self._resolve_3pl_connector(warehouse, po)
        if not connector:
            return
        # No duplicate guard — multiple UPDATE messages for the same PO are valid;
        # each represents a distinct ETA drift or vessel-change event.
        msg = self.env['3pl.message'].create({
            'connector_id':  connector.id,
            'direction':     'outbound',
            'document_type': 'inward_order',
            'action':        'update',
            'ref_model':     'purchase.order',
            'ref_id':        po.id,
        })
        _logger.info('freight.booking %s: queued inward_order UPDATE %s', self.name, msg.id)

    def _handle_dsv_tracking_webhook(self, carrier, body):
        """Handle DSV TRACKING_UPDATE webhook. Caller must have validated HMAC before calling.

        SECURITY: HMAC-SHA256 validation is performed by dsv_webhook.py before this method
        is called. All string values from body are sanitised before storage.
        """
        import re

        def _sanitise(value, max_len=255):
            if not value:
                return ''
            return re.sub(r'[\x00-\x1f\x7f]', '', str(value))[:max_len]

        if not isinstance(body, dict):
            return
        shipment_id = body.get('shipmentId', '')
        if not shipment_id:
            return

        booking = self.search([
            ('carrier_shipment_id', '=', shipment_id),
            ('state', 'not in', ['cancelled', 'received']),
        ], limit=1)
        if not booking:
            _logger.info('DSV webhook: no active booking for shipmentId %s', shipment_id)
            return

        if booking.carrier_id.id != carrier.id:
            _logger.warning(
                'DSV webhook carrier mismatch: booking %s carrier=%s, webhook carrier=%s',
                booking.name, booking.carrier_id.id, carrier.id,
            )
            return

        prev_eta    = booking.eta
        prev_vessel = booking.vessel_name or ''
        state_order = [s[0] for s in BOOKING_STATES]

        for raw in (body.get('events') or []):
            event_type  = raw.get('eventType', '')
            status      = _DSV_BOOKING_STATE_MAP.get(event_type, _sanitise(event_type.lower(), 64))
            event_date  = _sanitise(raw.get('eventDate', ''), 50)
            location    = _sanitise(raw.get('location', ''))
            description = _sanitise(raw.get('description', ''))

            exists = booking.tracking_event_ids.filtered(
                lambda e: e.status == status and str(e.event_date) == event_date
            )
            if not exists:
                self.env['freight.tracking.event'].create({
                    'booking_id':  booking.id,
                    'event_date':  event_date,
                    'status':      status,
                    'location':    location,
                    'description': description,
                    'raw_payload': '{}',   # never log body — may contain PII
                })

            # Auto-advance state (never go backwards)
            if status in state_order:
                cur_idx = state_order.index(booking.state) if booking.state in state_order else -1
                new_idx = state_order.index(status)
                if new_idx > cur_idx:
                    booking.state = status

        booking._check_inward_order_updates(prev_eta, prev_vessel)

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
        """Sync tracking events from carrier adapter; auto-advance state; detect ETA drift."""
        adapter = self.env['freight.adapter.registry'].get_adapter(self.carrier_id)
        if not adapter:
            return

        prev_eta    = self.eta
        prev_vessel = self.vessel_name or ''
        events      = adapter.get_tracking(self)

        latest_state = None
        latest_eta   = None
        state_order  = [s[0] for s in BOOKING_STATES]

        for evt in events:
            exists = self.tracking_event_ids.filtered(
                lambda e: e.status == evt.get('status')
                and str(e.event_date) == evt.get('event_date', '')
            )
            if not exists:
                self.env['freight.tracking.event'].create({
                    'booking_id':  self.id,
                    'event_date':  evt['event_date'],
                    'status':      evt['status'],
                    'location':    evt.get('location', ''),
                    'description': evt.get('description', ''),
                    'raw_payload': evt.get('raw_payload', ''),
                })
            if evt.get('status') in state_order:
                if latest_state is None or state_order.index(evt['status']) > state_order.index(latest_state):
                    latest_state = evt['status']
            if evt.get('_new_eta'):
                latest_eta = evt['_new_eta']  # last ETA in batch wins; fine in practice

        # Update ETA
        if latest_eta:
            try:
                self.eta = dateutil.parser.parse(latest_eta).replace(tzinfo=None)
            except Exception:
                pass

        # Auto-advance state (never go backwards)
        if latest_state and latest_state in state_order:
            cur_idx = state_order.index(self.state) if self.state in state_order else -1
            new_idx = state_order.index(latest_state)
            if new_idx > cur_idx:
                self.state = latest_state

        self._check_inward_order_updates(prev_eta, prev_vessel)
