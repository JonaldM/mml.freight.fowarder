import psycopg2
import datetime

from odoo import models, fields, api
from odoo.exceptions import UserError
from odoo.addons.mml_freight.models.freight_adapter_registry import FreightAdapterRegistry  # noqa: F401 — imported so tests can patch via this module's namespace
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
    po_ids = fields.Many2many(
        'purchase.order',
        'freight_booking_purchase_order_rel',
        'booking_id', 'purchase_order_id',
        string='Purchase Orders',
    )

    carrier_booking_id = fields.Char('Carrier Booking Ref', tracking=True)
    carrier_shipment_id = fields.Char('Carrier Shipment ID')
    carrier_tracking_url = fields.Char('Tracking URL')

    currency_id = fields.Many2one('res.currency', required=True)
    booked_rate = fields.Monetary('Booked Rate', currency_field='currency_id')
    actual_rate = fields.Monetary('Actual Rate', currency_field='currency_id')
    invoice_id = fields.Many2one('account.move', string='Freight Invoice', ondelete='set null')
    landed_cost_id = fields.Many2one(
        'stock.landed.cost', string='Landed Cost', ondelete='set null', readonly=True,
    )

    current_status = fields.Char(
        'Current Status', compute='_compute_current_status', store=True,
    )
    eta = fields.Datetime('ETA')
    actual_pickup_date = fields.Datetime('Actual Pickup')
    actual_delivery_date = fields.Datetime('Actual Delivery')

    transit_days_actual = fields.Float(
        'Actual Transit Days',
        compute='_compute_transit_kpis',
        store=True,
        digits=(6, 1),
        help='Days between actual pickup and actual delivery.',
    )
    on_time = fields.Boolean(
        'On Time',
        compute='_compute_transit_kpis',
        store=True,
        help='True when actual delivery <= ETA (or requested delivery date if no ETA).',
    )

    transport_mode = fields.Selection(TRANSPORT_MODES)

    # Contract commitment tracking
    contract_id = fields.Many2one(
        'freight.carrier.contract',
        string='Carrier Contract',
        ondelete='set null',
        index=True,
        help='Contract this booking counts against. Set at booking time when contract_aware tender selection is used.',
    )
    unit_quantity = fields.Float(
        'Contract Units',
        digits=(10, 3),
        help='Quantity consumed against the contract (TEU, kg, or shipments).',
    )
    unit_type = fields.Selection(
        [('teu', 'TEU'), ('weight_kg', 'Weight (kg)'), ('shipment_count', 'Shipments')],
        string='Unit Type',
        compute='_compute_unit_type',
        store=True,
        help='Mirrors the contract commitment_unit for the active transport mode.',
    )

    vessel_name = fields.Char('Vessel')
    voyage_number = fields.Char('Voyage No.')
    container_number = fields.Char('Container No.')
    bill_of_lading = fields.Char('Bill of Lading')
    feeder_vessel_name   = fields.Char('Feeder Vessel')
    feeder_voyage_number = fields.Char('Feeder Voyage No.')
    awb_number = fields.Char('AWB No.')

    # Related origin/destination fields (denormalised from tender for list/kanban)
    origin_country_id = fields.Many2one(
        'res.country', related='tender_id.origin_country_id',
        string='Origin Country', store=True, readonly=True,
    )
    dest_country_id = fields.Many2one(
        'res.country', related='tender_id.dest_country_id',
        string='Destination Country', store=True, readonly=True,
    )
    dest_port = fields.Char(
        related='tender_id.dest_port', string='Dest. Port', store=True, readonly=True,
    )

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
                vals['name'] = self.env['ir.sequence'].sudo().next_by_code('freight.booking') or 'New'
        return super().create(vals_list)

    @api.depends('tracking_event_ids.status', 'tracking_event_ids.event_date')
    def _compute_current_status(self):
        for b in self:
            latest = b.tracking_event_ids.sorted('event_date', reverse=True)
            b.current_status = latest[0].status if latest else ''

    @api.depends('transport_mode')
    def _compute_unit_type(self):
        mode_map = {
            'sea_fcl': 'teu',
            'sea_lcl': 'weight_kg',
            'air': 'weight_kg',
            'road': 'shipment_count',
            'rail': 'shipment_count',
            'express': 'shipment_count',
        }
        for b in self:
            b.unit_type = mode_map.get(b.transport_mode or '', 'shipment_count')

    @api.depends('actual_pickup_date', 'actual_delivery_date', 'eta',
                 'tender_id.requested_delivery_date')
    def _compute_transit_kpis(self):
        for booking in self:
            pickup   = booking.actual_pickup_date
            delivery = booking.actual_delivery_date

            if pickup and delivery:
                delta = delivery - pickup
                booking.transit_days_actual = max(0.0, delta.total_seconds() / 86400)
            else:
                booking.transit_days_actual = 0.0

            if not delivery:
                booking.on_time = False
            elif booking.eta:
                booking.on_time = delivery <= booking.eta
            else:
                req = booking.tender_id.requested_delivery_date
                if req:
                    # Convert date to datetime at end-of-day for fair comparison
                    req_dt = datetime.datetime.combine(req, datetime.time(23, 59, 59))
                    booking.on_time = delivery <= req_dt
                else:
                    booking.on_time = False

    def action_confirm(self):
        self.ensure_one()
        self.write({'state': 'confirmed'})
        self.env['mml.event'].emit(
            'freight.booking.confirmed',
            quantity=1,
            billable_unit='freight_booking',
            res_model=self._name,
            res_id=self.id,
            source_module='mml_freight',
            payload={
                'booking_ref': self.name,
                'carrier': self.carrier_id.name if self.carrier_id else '',
            },
        )
        self._queue_3pl_inward_order()
        self._build_inward_order_payload()
        return True

    def action_confirm_with_dsv(self):
        """Confirm booking with DSV API, update vessel/ETA fields, queue 3PL inward order."""
        self.ensure_one()
        if self.state != 'draft':
            raise UserError('Booking must be in Draft state to confirm with carrier.')
        # Pessimistic lock — prevents double-click making two DSV API calls for the same booking.
        try:
            self.env.cr.execute(
                'SELECT id FROM freight_booking WHERE id = %s FOR UPDATE NOWAIT', [self.id]
            )
        except psycopg2.errors.LockNotAvailable:
            raise UserError(
                'Another operation is in progress for this booking. Please try again.'
            )
        # Re-check state after acquiring lock (another process may have changed state).
        self.invalidate_recordset()
        if self.state != 'draft':
            raise UserError('Booking state changed — please refresh and try again.')
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
        self.env['mml.event'].emit(
            'freight.booking.confirmed',
            quantity=1,
            billable_unit='freight_booking',
            res_model=self._name,
            res_id=self.id,
            source_module='mml_freight',
            payload={
                'booking_ref': self.name,
                'carrier': self.carrier_id.name if self.carrier_id else '',
            },
        )
        self._queue_3pl_inward_order()
        self._build_inward_order_payload()
        self.message_post(body='Booking confirmed with DSV. Inward order notice queued to Mainfreight.')
        return True

    def action_cancel(self):
        self.ensure_one()
        registry = self.env['freight.adapter.registry']
        adapter  = registry.get_adapter(self.carrier_id)
        if adapter and self.carrier_booking_id:
            adapter.cancel_booking(self)
        self.write({'state': 'cancelled'})
        return True

    def write(self, vals):
        """Override write to trigger document fetch on key state transitions.

        arrived_port → fetch customs, packing_list, label documents
        delivered    → fetch all document types + freight invoice

        API failures post a chatter warning but never block the state transition.
        The cron safety net will retry any failed fetches.
        """
        prev_states = {rec.id: rec.state for rec in self}

        if self.env.context.get('_auto_fetch_in_progress'):
            return super().write(vals)

        result = super().write(vals)

        new_state = vals.get('state')
        if new_state not in ('arrived_port', 'delivered'):
            return result

        for rec in self:
            prev = prev_states.get(rec.id)
            if prev == new_state:
                continue  # no real transition

            if new_state == 'arrived_port':
                try:
                    rec.with_context(_auto_fetch_in_progress=True)._auto_fetch_documents(
                        doc_types=['customs', 'packing_list', 'label'],
                    )
                except Exception as exc:
                    _logger.warning(
                        'Auto-fetch documents failed on transition to %s for booking %s: %s',
                        new_state, rec.name, exc,
                    )
                    rec.message_post(
                        body=f'Auto-fetch failed on transition to {new_state}, will retry via cron.',
                        message_type='comment',
                        subtype_xmlid='mail.mt_note',
                    )
            elif new_state == 'delivered':
                try:
                    rec.with_context(_auto_fetch_in_progress=True)._auto_fetch_documents(
                        doc_types=None,
                    )
                except Exception as exc:
                    _logger.warning(
                        'Auto-fetch documents failed on transition to %s for booking %s: %s',
                        new_state, rec.name, exc,
                    )
                    rec.message_post(
                        body=f'Auto-fetch failed on transition to {new_state}, will retry via cron.',
                        message_type='comment',
                        subtype_xmlid='mail.mt_note',
                    )
                try:
                    rec.with_context(_auto_fetch_in_progress=True)._auto_fetch_invoice()
                except Exception as exc:
                    _logger.warning(
                        'Auto-fetch invoice failed on transition to %s for booking %s: %s',
                        new_state, rec.name, exc,
                    )
                    rec.message_post(
                        body='Invoice fetch failed on delivery transition, will retry via cron.',
                        message_type='comment',
                        subtype_xmlid='mail.mt_note',
                    )

        return result
