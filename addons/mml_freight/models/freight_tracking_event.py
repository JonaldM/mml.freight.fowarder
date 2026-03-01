from odoo import models, fields


class FreightTrackingEvent(models.Model):
    _name = 'freight.tracking.event'
    _description = 'Freight Booking — Tracking Event'
    _order = 'event_date desc'

    _sql_constraints = [
        (
            'unique_booking_event',
            'UNIQUE(booking_id, event_date, status)',
            'A tracking event with this status and date already exists for this booking.',
        ),
    ]

    booking_id = fields.Many2one(
        'freight.booking', required=True, ondelete='cascade', index=True,
    )
    event_date = fields.Datetime('Event Date', required=True)
    status = fields.Char('Status', required=True)
    location = fields.Char('Location')
    description = fields.Char('Description')
    raw_payload = fields.Text('Raw Payload')
