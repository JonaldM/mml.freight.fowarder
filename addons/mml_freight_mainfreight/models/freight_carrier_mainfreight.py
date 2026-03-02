import logging

from odoo import models, fields

_logger = logging.getLogger(__name__)


class FreightCarrierMainfreight(models.Model):
    _inherit = 'delivery.carrier'

    # --- API credentials ---
    x_mf_api_key = fields.Char(
        'Mainfreight API Key',
        groups='stock.group_stock_manager',
        password=True,
        help='API key from developer.mainfreight.com registration. '
             'Used in Authorization: Secret {api_key} header.',
    )
    x_mf_customer_code = fields.Char(
        'Customer Code',
        groups='stock.group_stock_manager',
        help='Mainfreight customer account code (e.g. MMLCONS). '
             'Required on all Warehousing and Tracking API calls.',
    )
    x_mf_warehouse_code = fields.Char(
        'Default Warehouse Code',
        default='AKL',
        groups='stock.group_stock_manager',
        help='Mainfreight warehouse code (e.g. AKL, CHC). '
             'Confirm exact code with your Mainfreight account manager.',
    )
    x_mf_environment = fields.Selection(
        [('uat', 'UAT (apitest.mainfreight.com)'), ('production', 'Production (api.mainfreight.com)')],
        default='uat',
        groups='stock.group_stock_manager',
        help='UAT → test environment, no real shipments affected. '
             'Production → live Mainfreight API.',
    )
    x_mf_webhook_secret = fields.Char(
        'Webhook Signing Secret',
        groups='stock.group_stock_manager',
        password=True,
        help='Shared secret used to validate incoming Mainfreight webhook signatures. '
             'When set, the webhook controller validates the X-MF-Secret header against this value. '
             'Leave blank only during initial onboarding — set before go-live. '
             'Generate with: python -c "import secrets; print(secrets.token_hex(32))"',
    )

    def cron_mf_tracking_poll(self):
        """Cron: poll Mainfreight Tracking API for active A&O bookings.

        Note: the generic cron_sync_tracking() in freight.booking covers all
        carriers including Mainfreight — this method is provided for a
        Mainfreight-specific scheduled action if finer control is needed
        (e.g. different polling interval for A&O vs domestic).
        """
        active_states = ['confirmed', 'cargo_ready', 'picked_up', 'in_transit', 'arrived_port', 'customs']
        bookings = self.env['freight.booking'].search([
            ('carrier_id.delivery_type', '=', 'mainfreight'),
            ('state', 'in', active_states),
        ])
        for booking in bookings:
            booking.invalidate_recordset()
            if booking.state not in active_states:
                _logger.info(
                    'cron_mf_tracking_poll: skipping %s (state changed since fetch)',
                    booking.name,
                )
                continue
            try:
                booking._sync_tracking()
            except Exception as exc:
                _logger.error(
                    'cron_mf_tracking_poll: tracking sync failed for %s: %s',
                    booking.name, exc,
                )
