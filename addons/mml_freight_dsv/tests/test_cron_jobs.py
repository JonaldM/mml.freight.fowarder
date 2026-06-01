from odoo.tests.common import TransactionCase


class TestCronJobs(TransactionCase):
    def test_tracking_cron(self): self.env['freight.booking'].cron_sync_tracking()
    def test_token_cron(self): self.env['delivery.carrier'].cron_refresh_dsv_tokens()
    def test_cron_records_installed(self):
        # Tracking cron is defined in mml_freight/data/ir_cron.xml
        c1 = self.env.ref('mml_freight.cron_freight_tracking_sync', raise_if_not_found=False)
        # Token refresh cron is defined in mml_freight_dsv/data/ir_cron.xml — use
        # the dsv module prefix, not mml_freight (wrong prefix caused 'Token cron missing').
        c2 = self.env.ref('mml_freight_dsv.cron_dsv_token_refresh', raise_if_not_found=False)
        self.assertTrue(c1, 'Tracking cron missing')
        self.assertTrue(c2, 'Token cron missing')
