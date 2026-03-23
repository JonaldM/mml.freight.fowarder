from odoo import models
import logging

_logger = logging.getLogger(__name__)


class FreightBooking3pl(models.Model):
    _inherit = 'freight.booking'

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
                po = self.env['purchase.order'].browse(msg.ref_id)
                xml = doc.build_outbound(self, action='create', po=po)
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
                    body=f'Failed to build Mainfreight inward order payload for message {msg.id}: {e}. '
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
