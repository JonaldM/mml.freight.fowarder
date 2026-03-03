import base64
import logging
from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# DSV document type codes accepted by the Upload API
DSV_DOC_TYPES = [
    ('INV', 'Commercial Invoice'),
    ('PKL', 'Packing List'),
    ('CUS', 'Customs / Quarantine'),
    ('HAZ', 'Dangerous Goods'),
    ('GDS', 'Other Goods Doc'),
]

# Maps DSV upload type code back to freight.document.doc_type
_DSV_TYPE_TO_DOC_TYPE = {
    'INV': 'invoice',
    'PKL': 'packing_list',
    'CUS': 'customs',
    'HAZ': 'other',
    'GDS': 'other',
}

# Max file size DSV accepts (bytes)
_MAX_FILE_SIZE = 3 * 1024 * 1024  # 3 MB

# Keyword → DSV type detection rules (checked in order, first match wins)
_KEYWORD_TYPE_MAP = [
    (['pi', 'proforma', 'invoice', 'commercial'], 'INV'),
    (['packing', 'pkl'], 'PKL'),
    (['quarantine', 'quar', 'phyto', 'biosecurity'], 'CUS'),
    (['dangerous', 'dg', 'haz', 'msds'], 'HAZ'),
]


def detect_dsv_type(filename):
    """Detect DSV document type from filename. Returns 'GDS' as fallback.

    Case-insensitive. First matching keyword group wins.
    """
    name = (filename or '').lower()
    for keywords, dsv_type in _KEYWORD_TYPE_MAP:
        if any(kw in name for kw in keywords):
            return dsv_type
    return 'GDS'


class FreightDsvDocUploadWizardLine(models.TransientModel):
    _name = 'freight.dsv.doc.upload.wizard.line'
    _description = 'DSV Document Upload — Line'

    wizard_id = fields.Many2one(
        'freight.dsv.doc.upload.wizard', required=True, ondelete='cascade',
    )
    attachment_id = fields.Many2one('ir.attachment', required=True, ondelete='cascade')
    filename = fields.Char(related='attachment_id.name', readonly=True)
    file_size = fields.Integer(related='attachment_id.file_size', readonly=True)
    dsv_type = fields.Selection(DSV_DOC_TYPES, string='Document Type', required=True)
    include = fields.Boolean('Upload', default=True)
    size_warning = fields.Boolean(compute='_compute_size_warning', store=False)

    @api.depends('file_size')
    def _compute_size_warning(self):
        for line in self:
            line.size_warning = (line.file_size or 0) > _MAX_FILE_SIZE


class FreightDsvDocUploadWizard(models.TransientModel):
    _name = 'freight.dsv.doc.upload.wizard'
    _description = 'Send Documents to DSV'

    po_id = fields.Many2one('purchase.order', required=True, readonly=True)
    booking_id = fields.Many2one('freight.booking', compute='_compute_booking', store=False)
    line_ids = fields.One2many(
        'freight.dsv.doc.upload.wizard.line', 'wizard_id', string='Documents',
    )

    @api.depends('po_id')
    def _compute_booking(self):
        for w in self:
            w.booking_id = w.po_id.x_dsv_booking_id

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        po_id = self.env.context.get('default_po_id') or self.env.context.get('active_id')
        if not po_id:
            return res
        attachments = self.env['ir.attachment'].search([
            ('res_model', '=', 'purchase.order'),
            ('res_id', '=', po_id),
        ])
        lines = []
        for att in attachments:
            oversized = (att.file_size or 0) > _MAX_FILE_SIZE
            lines.append({
                'attachment_id': att.id,
                'dsv_type': detect_dsv_type(att.name or ''),
                'include': not oversized,
            })
        res['line_ids'] = [(0, 0, line) for line in lines]
        res['po_id'] = po_id
        return res

    def action_upload(self):
        """Upload selected documents to DSV and log results on PO chatter."""
        booking = self.booking_id
        if not booking:
            raise UserError(
                'No active DSV booking found for this purchase order. '
                'Create and confirm a freight booking first.'
            )
        registry = self.env['freight.adapter.registry']
        adapter = registry.get_adapter(booking.carrier_id)
        if not adapter:
            raise UserError(f'No adapter registered for carrier {booking.carrier_id.name}.')

        results = []
        for line in self.line_ids.filtered('include'):
            att = line.attachment_id
            if (att.file_size or 0) > _MAX_FILE_SIZE:
                size_mb = (att.file_size or 0) / (1024 * 1024)
                results.append(
                    f'✗ {att.name} — Skipped (file is {size_mb:.1f} MB, limit is 3 MB)'
                )
                continue
            try:
                file_bytes = base64.b64decode(att.datas or b'')
            except Exception as e:
                results.append(f'✗ {att.name} — Could not read file: {e}')
                continue
            try:
                ref = adapter.upload_document(booking, att.name, file_bytes, line.dsv_type)
            except Exception as e:
                _logger.error('DSV doc upload exception for %s: %s', att.name, e, exc_info=True)
                ref = None

            label = dict(DSV_DOC_TYPES).get(line.dsv_type, line.dsv_type)
            if ref is not None:
                self.env['freight.document'].create({
                    'booking_id': booking.id,
                    'doc_type': _DSV_TYPE_TO_DOC_TYPE.get(line.dsv_type, 'other'),
                    'attachment_id': att.id,
                    'carrier_doc_ref': ref,
                    'uploaded_to_carrier': True,
                })
                results.append(f'✓ {att.name} → {label}')
            else:
                results.append(f'✗ {att.name} → {label} (upload failed)')

        body = (
            f'<b>Documents sent to DSV</b> (booking {booking.name}):<br/>'
            + '<br/>'.join(f'&nbsp;&nbsp;{r}' for r in results)
        )
        self.po_id.message_post(body=body)
        return {'type': 'ir.actions.act_window_close'}
