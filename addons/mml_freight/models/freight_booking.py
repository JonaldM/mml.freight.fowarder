import base64
import hashlib
import psycopg2

from odoo import models, fields, api
from odoo.exceptions import UserError
from odoo.addons.mml_freight.models.freight_adapter_registry import FreightAdapterRegistry  # noqa: F401 — imported so tests can patch via this module's namespace
import datetime
import re
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

    def action_fetch_label(self):
        """Fetch the shipping label PDF from the carrier adapter and attach it to this booking.

        Creates or updates a freight.document record with doc_type='label' (idempotent).
        Posts a chatter note on success. Raises UserError if the adapter returns no bytes.
        """
        self.ensure_one()
        registry = self.env['freight.adapter.registry']
        adapter = registry.get_adapter(self.carrier_id)
        if not adapter:
            raise UserError('No adapter available for this carrier.')

        label_bytes = adapter.get_label(self)
        if not label_bytes:
            raise UserError(
                'Could not fetch label from carrier. '
                'Ensure the booking has been confirmed and a carrier booking reference is set.'
            )

        if self.label_attachment_id:
            self.label_attachment_id.unlink()

        attachment = self.env['ir.attachment'].create({
            'name': f'label_{self.name}.pdf',
            'type': 'binary',
            'datas': base64.b64encode(label_bytes).decode(),
            'res_model': 'freight.booking',
            'res_id': self.id,
            'mimetype': 'application/pdf',
        })
        self.label_attachment_id = attachment

        # Idempotent: update existing label doc or create a new one
        existing_label_doc = self.document_ids.filtered(lambda d: d.doc_type == 'label')
        if existing_label_doc:
            existing_label_doc[:1].attachment_id = attachment
        else:
            self.env['freight.document'].create({
                'booking_id': self.id,
                'doc_type': 'label',
                'attachment_id': attachment.id,
            })

        self.message_post(
            body='Shipping label fetched and attached.',
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )
        return True

    def action_fetch_documents(self):
        """Fetch all available documents from the carrier adapter and attach them to this booking.

        For each document returned by the adapter:
        - Creates an ir.attachment with the file bytes.
        - Idempotent upsert of freight.document: matched on (doc_type, carrier_doc_ref).
          If carrier_doc_ref is empty, a stable synthetic ref is generated via
          sha256(doc_type + filename) so repeated fetches update rather than insert.
        - If doc_type is 'pod', updates pod_attachment_id (no unlinking — multiple PODs
          can legitimately exist for partial deliveries).

        Posts a chatter note on success. Raises UserError if no documents are available.
        """
        self.ensure_one()
        registry = self.env['freight.adapter.registry']
        adapter = registry.get_adapter(self.carrier_id)
        if not adapter:
            raise UserError('No adapter available for this carrier.')

        docs = adapter.get_documents(self)
        if not docs:
            raise UserError(
                'No documents available from carrier. '
                'Ensure the booking has been confirmed and documents have been issued.'
            )

        count = 0
        for doc in docs:
            attachment = self.env['ir.attachment'].create({
                'name': doc['filename'],
                'type': 'binary',
                'datas': base64.b64encode(doc['bytes']).decode(),
                'res_model': 'freight.booking',
                'res_id': self.id,
                'mimetype': 'application/pdf',
            })

            carrier_doc_ref = doc.get('carrier_doc_ref', '') or ''
            doc_type = doc['doc_type']

            # Generate a stable synthetic ref when carrier provides none.
            # Ensures the DB UNIQUE(booking_id, doc_type, carrier_doc_ref) constraint
            # treats repeated fetches of the same file as updates, not inserts.
            if not carrier_doc_ref:
                carrier_doc_ref = 'local:' + hashlib.sha256(
                    (doc_type + doc['filename']).encode('utf-8')
                ).hexdigest()[:32]

            # Idempotent upsert: match on (doc_type, carrier_doc_ref)
            existing_doc = self.document_ids.filtered(
                lambda d, dt=doc_type, ref=carrier_doc_ref:
                    d.doc_type == dt and d.carrier_doc_ref == ref
            )[:1]

            if existing_doc:
                existing_doc.attachment_id = attachment
            else:
                self.env['freight.document'].create({
                    'booking_id':      self.id,
                    'doc_type':        doc_type,
                    'attachment_id':   attachment.id,
                    'carrier_doc_ref': carrier_doc_ref,
                })

            if doc_type == 'pod':
                self.pod_attachment_id = attachment

            count += 1

        self.message_post(
            body=f'{count} document(s) fetched from carrier and attached.',
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )
        return True

    def action_fetch_invoice(self):
        """Fetch freight invoice from carrier and update actual_rate."""
        self.ensure_one()
        adapter = self.env['freight.adapter.registry'].get_adapter(self.carrier_id)
        if not adapter:
            raise UserError('No adapter available for this carrier.')
        invoice_data = adapter.get_invoice(self)
        if not invoice_data:
            raise UserError('No invoice available for this shipment yet. Try again later.')
        curr = self.env['res.currency'].search(
            [('name', '=', invoice_data.get('currency', 'NZD'))], limit=1,
        ) or self.currency_id
        self.write({
            'actual_rate': invoice_data['amount'],
            'currency_id': curr.id if curr else self.currency_id.id,
        })
        self.message_post(
            body=(
                f"Freight invoice fetched: {invoice_data['amount']:.2f} "
                f"{invoice_data.get('currency', '')} "
                f"(DSV Invoice #{invoice_data.get('dsv_invoice_id', 'N/A')})"
            )
        )
        return True

    def _get_freight_cost_product(self):
        """Return the configured freight cost product for landed cost creation."""
        param = self.env['ir.config_parameter'].sudo().get_param(
            'mml_freight.freight_cost_product_id'
        )
        if param:
            try:
                product = self.env['product.product'].browse(int(param))
                if product.exists():
                    return product
            except (ValueError, TypeError):
                pass
        return self.env['product.product'].search(
            [('name', '=', 'Freight Cost'), ('type', '=', 'service')], limit=1,
        )

    def action_create_landed_cost(self):
        """Create a stock.landed.cost from this booking's actual_rate and open it.

        Collects done incoming receipts from all linked purchase orders so that a
        consolidated multi-PO booking is covered by a single landed cost entry.
        Odoo's split_method (by_weight / by_value) then apportions the freight
        cost across the individual product moves.
        """
        self.ensure_one()
        if not self.actual_rate:
            raise UserError(
                'Set the actual freight rate (Fetch Invoice or enter manually) '
                'before creating a landed cost.'
            )
        if self.landed_cost_id:
            raise UserError(
                'A landed cost already exists for this booking (%s). '
                'Open it from the Financials group.' % self.landed_cost_id.name
            )
        if 'stock.landed.cost' not in self.env:
            raise UserError(
                'stock.landed.cost model not available. '
                'Ensure the stock_account (or stock_landed_costs) module is installed.'
            )
        if not self.po_ids:
            raise UserError('No purchase orders linked to this booking.')
        receipts = self.po_ids.mapped('picking_ids').filtered(
            lambda p: p.state == 'done' and p.picking_type_code == 'incoming'
        )
        if not receipts:
            po_names = ', '.join(self.po_ids.mapped('name'))
            raise UserError(
                'No validated receipts found for %s. '
                'Receive the goods before creating a landed cost.' % po_names
            )
        freight_product = self._get_freight_cost_product()
        if not freight_product:
            raise UserError(
                'No freight cost product configured. '
                'Set system parameter mml_freight.freight_cost_product_id, '
                'or create a service product named "Freight Cost".'
            )
        account = (
            freight_product.categ_id.property_account_expense_categ_id
            if freight_product.categ_id else False
        )
        landed_cost = self.env['stock.landed.cost'].create({
            'picking_ids':    [(4, r.id) for r in receipts],
            'vendor_bill_id': self.invoice_id.id if self.invoice_id else False,
            'cost_lines': [(0, 0, {
                'product_id':   freight_product.id,
                'name':         f'Freight — {self.name}',
                'price_unit':   self.actual_rate,
                'split_method': 'by_weight',
                'account_id':   account.id if account else False,
            })],
        })
        self.landed_cost_id = landed_cost
        self.message_post(
            body=(
                f'Landed cost created: {landed_cost.name} '
                f'({self.actual_rate:.2f} {self.currency_id.name}) '
                f'covering {len(receipts)} receipt(s) across {len(self.po_ids)} PO(s). '
                f'Validate the landed cost to apply freight to product valuations.'
            )
        )
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stock.landed.cost',
            'res_id': landed_cost.id,
            'view_mode': 'form',
        }

    def _queue_3pl_inward_order(self):
        """Queue one inward_order notice per linked PO via stock_3pl_core message queue.

        Consolidated bookings cover multiple POs. Mainfreight receives one inward order
        per PO because each PO maps to its own Odoo stock receipt (picking). This method
        iterates po_ids and creates one 3pl.message per PO, skipping any PO that already
        has a create-type inward_order message (idempotency guard per PO, not per booking).

        Connector selection per PO uses a two-step strategy:
        1. Specific match: active connector for the PO's warehouse that handles one or
           more of the PO's product categories (ordered by priority asc).
        2. Catch-all fallback: active connector with no product categories configured.

        Graceful no-op if stock_3pl_core is not installed or no matching connector found.
        """
        if '3pl.connector' not in self.env:
            _logger.info(
                'freight.booking %s: stock_3pl_core not installed — skipping 3PL handoff',
                self.name,
            )
            return
        if not self.po_ids:
            return

        for po in self.po_ids:
            # Per-PO idempotency: skip if a create-type inward_order already exists for this PO.
            existing = self.env['3pl.message'].search([
                ('ref_model', '=', 'purchase.order'),
                ('ref_id', '=', po.id),
                ('document_type', '=', 'inward_order'),
                ('action', '=', 'create'),
            ], limit=1)
            if existing:
                _logger.info(
                    'freight.booking %s: inward_order already queued for PO %s (%s) — skipping',
                    self.name, po.name, existing.id,
                )
                continue

            warehouse = po.picking_type_id.warehouse_id if po.picking_type_id else False
            if not warehouse:
                _logger.info(
                    'freight.booking %s: PO %s has no warehouse — skipping 3PL handoff for this PO',
                    self.name, po.name,
                )
                continue

            connector = self._resolve_3pl_connector(warehouse, po)
            if not connector:
                _logger.info(
                    'freight.booking %s: no active 3PL connector for warehouse %s (PO %s) — skipping',
                    self.name, warehouse.name, po.name,
                )
                continue

            msg = self.env['3pl.message'].create({
                'connector_id':  connector.id,
                'direction':     'outbound',
                'document_type': 'inward_order',
                'action':        'create',
                'ref_model':     'purchase.order',
                'ref_id':        po.id,
            })
            _logger.info(
                'freight.booking %s: queued 3pl.message %s for PO %s via connector %s',
                self.name, msg.id, po.name, connector.name,
            )

    def _build_inward_order_payload(self):
        """Build inward order XML for every draft inward_order message linked to this booking's POs.

        For each linked PO, finds the draft create-type 3pl.message and advances it to
        'queued' with the XML payload. A consolidated booking produces one message per PO.
        """
        self.ensure_one()
        if not self.po_ids:
            return
        if '3pl.message' not in self.env:
            return

        # Try to load InwardOrderDocument from stock_3pl_mainfreight
        try:
            from odoo.addons.stock_3pl_mainfreight.document.inward_order import InwardOrderDocument
        except ImportError:
            _logger.info(
                'freight.booking %s: stock_3pl_mainfreight not installed — skipping payload build',
                self.name,
            )
            return

        draft_messages = self.env['3pl.message'].search([
            ('ref_model', '=', 'purchase.order'),
            ('ref_id', 'in', self.po_ids.ids),
            ('document_type', '=', 'inward_order'),
            ('action', '=', 'create'),
            ('state', '=', 'draft'),
        ])
        if not draft_messages:
            return

        for msg in draft_messages:
            connector = msg.connector_id
            if not connector:
                continue
            try:
                doc = InwardOrderDocument(connector, self.env)
                xml = doc.build_outbound(self, action='create')
                msg.write({'payload_xml': xml, 'state': 'queued'})
                _logger.info(
                    'freight.booking %s: inward order payload built, message %s queued (PO id=%s)',
                    self.name, msg.id, msg.ref_id,
                )
            except Exception as e:
                _logger.error(
                    'freight.booking %s: failed to build payload for message %s: %s',
                    self.name, msg.id, e,
                )
                self.message_post(
                    body=f'⚠️ Failed to build Mainfreight inward order payload for message {msg.id}: {e}. '
                         f'Manual intervention required.',
                    message_type='comment',
                    subtype_xmlid='mail.mt_note',
                )

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
        """Create a queued 3pl.message UPDATE for each linked PO's inward order.

        No duplicate guard — multiple UPDATE messages per PO are valid; each represents
        a distinct ETA drift or vessel-change event.
        """
        if '3pl.connector' not in self.env:
            return
        for po in self.po_ids:
            warehouse = po.picking_type_id.warehouse_id if po.picking_type_id else False
            if not warehouse:
                continue
            connector = self._resolve_3pl_connector(warehouse, po)
            if not connector:
                continue
            msg = self.env['3pl.message'].create({
                'connector_id':  connector.id,
                'direction':     'outbound',
                'document_type': 'inward_order',
                'action':        'update',
                'ref_model':     'purchase.order',
                'ref_id':        po.id,
            })
            _logger.info(
                'freight.booking %s: queued inward_order UPDATE %s for PO %s',
                self.name, msg.id, po.name,
            )

    def _handle_dsv_invoice_webhook(self, carrier, body):
        """Handle DSV Invoice webhook notification. Fetches invoice via API and updates actual_rate.

        Called by dsv_webhook.py when eventType == 'Invoice'. Carrier ID validation is done
        by the controller before this is called.
        """
        if not isinstance(body, dict):
            return
        shipment_id = body.get('shipmentId', '')
        if not shipment_id:
            return
        booking = self.search([
            ('carrier_shipment_id', '=', shipment_id),
            ('carrier_id', '=', carrier.id),
            ('state', 'not in', ['cancelled', 'received']),
        ], limit=1)
        if not booking:
            _logger.info('DSV invoice webhook: no active booking for shipmentId %s', shipment_id)
            return
        registry = self.env['freight.adapter.registry']
        adapter = registry.get_adapter(carrier)
        if not adapter:
            _logger.warning('DSV invoice webhook: no adapter for carrier %s', carrier.id)
            return
        invoice_data = adapter.get_invoice(booking)
        if not invoice_data:
            _logger.info('DSV invoice webhook: get_invoice returned None for booking %s', booking.name)
            return
        # Idempotency guard: skip write and chatter if actual_rate already matches.
        # Prevents duplicate chatter notes on DSV webhook retries.
        if booking.actual_rate and abs(booking.actual_rate - invoice_data['amount']) < 0.01:
            _logger.info(
                'DSV invoice webhook: actual_rate already matches (%.2f) for booking %s — skipping',
                booking.actual_rate, booking.name,
            )
            return
        curr = self.env['res.currency'].search(
            [('name', '=', invoice_data.get('currency', 'NZD'))], limit=1,
        ) or booking.currency_id
        booking.write({
            'actual_rate': invoice_data['amount'],
            'currency_id': curr.id if curr else booking.currency_id.id,
        })
        booking.message_post(
            body=(
                f"DSV invoice webhook: actual rate updated to "
                f"{invoice_data['amount']:.2f} {invoice_data.get('currency', '')} "
                f"(DSV Invoice #{invoice_data.get('dsv_invoice_id', 'N/A')})"
            )
        )

    def _handle_dsv_tracking_webhook(self, carrier, body):
        """Handle DSV TRACKING_UPDATE webhook. Caller must have validated HMAC before calling.

        SECURITY: HMAC-SHA256 validation is performed by dsv_webhook.py before this method
        is called. All string values from body are sanitised before storage.
        """
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
            raw_date_str = raw.get('eventDate', '')
            try:
                event_dt = dateutil.parser.parse(raw_date_str).replace(tzinfo=None)
            except Exception:
                event_dt = None
            location    = _sanitise(raw.get('location', ''))
            description = _sanitise(raw.get('description', ''))

            if event_dt is None:
                _logger.warning(
                    'DSV webhook: unparseable eventDate %r for shipment %s event %r — skipped',
                    raw_date_str, shipment_id, event_type,
                )
                continue

            exists = booking.tracking_event_ids.filtered(
                lambda e, s=status, dt=event_dt: e.status == s and e.event_date == dt
            )
            if not exists:
                self.env['freight.tracking.event'].create({
                    'booking_id':  booking.id,
                    'event_date':  event_dt,
                    'status':      status,
                    'location':    location,
                    'description': description,
                    'raw_payload': 'redacted — PII',   # body never stored
                })

            # Auto-advance state (never go backwards)
            if status in state_order:
                cur_idx = state_order.index(booking.state) if booking.state in state_order else -1
                new_idx = state_order.index(status)
                if new_idx > cur_idx:
                    booking.state = status

        booking._check_inward_order_updates(prev_eta, prev_vessel)

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

    @api.model
    def cron_sync_tracking(self):
        """Cron: sync tracking for all active bookings.

        Guard against concurrent cron runs: invalidate_recordset() + state re-check
        before processing each booking. Pattern copied from stock_3pl_core
        _process_outbound_queue. Prevents redundant DSV API calls when two cron
        instances overlap.
        """
        active_states = ['confirmed', 'cargo_ready', 'picked_up', 'in_transit', 'arrived_port', 'customs']
        bookings = self.search([('state', 'in', active_states)])
        for booking in bookings:
            # Re-read from DB — another cron instance or user action may have
            # changed state since the initial search().
            booking.invalidate_recordset()
            if booking.state not in active_states:
                _logger.info(
                    'cron_sync_tracking: skipping booking %s (state=%s, changed since fetch)',
                    booking.name, booking.state,
                )
                continue
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
            raw_date_str = evt.get('event_date', '')
            try:
                event_dt = dateutil.parser.parse(raw_date_str).replace(tzinfo=None)
            except Exception:
                event_dt = None

            if event_dt is None:
                continue

            evt_status = evt.get('status', '')
            exists = self.tracking_event_ids.filtered(
                lambda e, s=evt_status, dt=event_dt: e.status == s and e.event_date == dt
            )
            if not exists:
                self.env['freight.tracking.event'].create({
                    'booking_id':  self.id,
                    'event_date':  event_dt,
                    'status':      evt_status,
                    'location':    evt.get('location', ''),
                    'description': evt.get('description', ''),
                    'raw_payload': 'redacted — PII',
                })
            if evt_status in state_order:
                if latest_state is None or state_order.index(evt_status) > state_order.index(latest_state):
                    latest_state = evt_status
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
