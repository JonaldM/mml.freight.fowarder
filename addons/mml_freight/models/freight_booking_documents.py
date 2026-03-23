import base64
import hashlib

from odoo import models
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class FreightBookingDocuments(models.Model):
    _inherit = 'freight.booking'

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

        self._attach_documents_to_pos(self.document_ids)
        self.message_post(
            body=f'{count} document(s) fetched from carrier and attached.',
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )
        return True

    def _attach_documents_to_pos(self, freight_docs):
        """Copy freight documents to all linked purchase orders as ir.attachment.

        For each freight.document in freight_docs, for each PO in self.po_ids:
        - Creates ir.attachment (res_model='purchase.order', res_id=po.id).
        - Skips if attachment with same filename already exists on that PO (idempotent).

        After all docs are attached, posts one chatter note per PO listing all doc types
        attached in this run. One message per fetch — no chatter spam.
        """
        if not freight_docs or not self.po_ids:
            return

        for po in self.po_ids:
            attached_types = []
            for fdoc in freight_docs:
                attachment = fdoc.attachment_id
                if not attachment:
                    continue
                existing = self.env['ir.attachment'].search([
                    ('res_model', '=', 'purchase.order'),
                    ('res_id', '=', po.id),
                    ('name', '=', attachment.name),
                ], limit=1)
                if existing:
                    continue
                self.env['ir.attachment'].create({
                    'name': attachment.name,
                    'type': 'binary',
                    'datas': attachment.datas,
                    'res_model': 'purchase.order',
                    'res_id': po.id,
                    'mimetype': attachment.mimetype or 'application/pdf',
                })
                attached_types.append(fdoc.doc_type)

            if attached_types:
                type_list = ', '.join(sorted(set(attached_types)))
                po.message_post(
                    body=(
                        f'{len(attached_types)} document(s) attached from freight booking '
                        f'{self.name}: {type_list}'
                    ),
                    message_type='comment',
                    subtype_xmlid='mail.mt_note',
                )

    def _auto_fetch_documents(self, doc_types=None):
        """Fetch documents silently — used by state triggers and cron.

        doc_types: list of doc_type strings to filter, or None = all types.
        Returns False silently when no documents are available.
        Does NOT raise UserError — callers handle failures.
        """
        self.ensure_one()
        registry = self.env['freight.adapter.registry']
        adapter = registry.get_adapter(self.carrier_id)
        if not adapter:
            return False

        docs = adapter.get_documents(self)
        if not docs:
            return False

        if doc_types is not None:
            docs = [d for d in docs if d.get('doc_type') in doc_types]
        if not docs:
            return False

        count = 0
        new_doc_records = self.env['freight.document']
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
            if not carrier_doc_ref:
                carrier_doc_ref = 'local:' + hashlib.sha256(
                    (doc_type + doc['filename']).encode('utf-8')
                ).hexdigest()[:32]

            existing_doc = self.document_ids.filtered(
                lambda d, dt=doc_type, ref=carrier_doc_ref:
                    d.doc_type == dt and d.carrier_doc_ref == ref
            )[:1]

            if existing_doc:
                existing_doc.attachment_id = attachment
                new_doc_records |= existing_doc
            else:
                new_record = self.env['freight.document'].create({
                    'booking_id':      self.id,
                    'doc_type':        doc_type,
                    'attachment_id':   attachment.id,
                    'carrier_doc_ref': carrier_doc_ref,
                })
                new_doc_records |= new_record

            if doc_type == 'pod':
                self.pod_attachment_id = attachment
            count += 1

        if count:
            self._attach_documents_to_pos(new_doc_records)
        return count > 0

    def _auto_fetch_invoice(self):
        """Fetch invoice silently — used by state triggers and cron.

        Returns False on no data or adapter unavailable.
        On exception: logs warning and posts chatter note on booking.
        Does NOT raise UserError — callers handle failures.
        """
        self.ensure_one()
        try:
            adapter = self.env['freight.adapter.registry'].get_adapter(self.carrier_id)
            if not adapter:
                return False
            invoice_data = adapter.get_invoice(self)
            if not invoice_data:
                return False
            curr = self.env['res.currency'].search(
                [('name', '=', invoice_data.get('currency', 'NZD'))], limit=1,
            ) or self.currency_id
            self.write({
                'actual_rate': invoice_data['amount'],
                'currency_id': curr.id if curr else self.currency_id.id,
            })
            inv_ref = invoice_data.get('carrier_invoice_ref', 'N/A')
            amount_str = f"{invoice_data['amount']:.2f} {invoice_data.get('currency', '')}"
            self.message_post(
                body=f'Freight cost confirmed: {amount_str} ({self.carrier_id.name} invoice {inv_ref})',
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )
            for po in self.po_ids:
                po.message_post(
                    body=f'Freight cost confirmed: {amount_str} ({self.carrier_id.name} invoice {inv_ref})',
                    message_type='comment',
                    subtype_xmlid='mail.mt_note',
                )
            return True
        except Exception as exc:
            _logger.warning(
                'Invoice fetch failed for booking %s: %s', self.name, exc,
            )
            self.message_post(
                body='Invoice fetch failed, will retry via cron.',
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )
            return False

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
        inv_ref = (
            invoice_data.get('carrier_invoice_ref')
            or invoice_data.get('dsv_invoice_id', 'N/A')
        )
        amount_str = f"{invoice_data['amount']:.2f} {invoice_data.get('currency', '')}"
        self.message_post(
            body=f'Freight cost confirmed: {amount_str} (invoice {inv_ref})',
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )
        for po in self.po_ids:
            po.message_post(
                body=(
                    f'Freight cost confirmed: {amount_str} '
                    f'({self.carrier_id.name} invoice {inv_ref})'
                ),
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )
        return True
