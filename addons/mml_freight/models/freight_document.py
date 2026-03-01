from odoo import models, fields

DOC_TYPES = [
    ('label', 'Shipping Label'),
    ('pod', 'Proof of Delivery'),
    ('invoice', 'Freight Invoice'),
    ('customs', 'Customs Document'),
    ('other', 'Other'),
]


class FreightDocument(models.Model):
    _name = 'freight.document'
    _description = 'Freight Booking — Document'
    _order = 'id'

    _sql_constraints = [
        (
            'unique_booking_doc',
            'UNIQUE(booking_id, doc_type, carrier_doc_ref)',
            'A document with this type and carrier reference already exists for this booking.',
        ),
    ]

    booking_id = fields.Many2one(
        'freight.booking', required=True, ondelete='cascade', index=True,
    )
    doc_type = fields.Selection(DOC_TYPES, string='Type', required=True, default='other')
    attachment_id = fields.Many2one('ir.attachment', string='Attachment', ondelete='set null')
    carrier_doc_ref = fields.Char('Carrier Doc Ref')
