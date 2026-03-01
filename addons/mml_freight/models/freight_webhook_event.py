from odoo import models, fields


class FreightWebhookEvent(models.Model):
    """Deduplication log for inbound carrier webhook payloads.

    Pattern copied from stock_3pl_core.3pl.message.source_hash. The unique
    constraint on (carrier_id, source_hash) is the primary deduplication
    mechanism — the application-level search-before-create is the fast path,
    the DB constraint is the safety net for race conditions.
    """
    _name = 'freight.webhook.event'
    _description = 'Freight Webhook Event (deduplication log)'
    _order = 'received_at desc'

    _sql_constraints = [
        (
            'unique_carrier_event',
            'UNIQUE(carrier_id, source_hash)',
            'This webhook payload has already been processed for this carrier.',
        ),
    ]

    carrier_id = fields.Many2one(
        'delivery.carrier', required=True, ondelete='cascade', index=True,
    )
    source_hash = fields.Char('Payload SHA-256', required=True, index=True)
    event_type = fields.Char('Event Type')
    received_at = fields.Datetime('Received At', default=fields.Datetime.now)
