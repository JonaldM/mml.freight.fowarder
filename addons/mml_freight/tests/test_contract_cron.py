# addons/mml_freight/tests/test_contract_cron.py
import datetime
from odoo.tests.common import TransactionCase
from odoo import fields


class TestContractCron(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        prod = cls.env['product.product'].create({
            'name': 'Test Freight Cron',
            'type': 'service',
        })
        cls.nzd = cls.env['res.currency'].search([('name', '=', 'NZD')], limit=1) or cls.env.company.currency_id
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Cron',
            'product_id': prod.id,
            'delivery_type': 'fixed',
        })

    def _make_contract(self, days_remaining=60, utilization_pct=30):
        today = fields.Date.today()
        end = today + datetime.timedelta(days=days_remaining)
        committed = 20.0
        c = self.env['freight.carrier.contract'].create({
            'name': f'DSV Cron {days_remaining}d',
            'carrier_id': self.carrier.id,
            'date_start': today,
            'date_end': end,
            'commitment_unit': 'teu',
            'committed_quantity': committed,
            'contracted_rate': 2500.0,
            'contracted_rate_currency_id': self.nzd.id,
        })
        utilized = committed * utilization_pct / 100
        if utilized > 0:
            self.env['freight.booking'].create({
                'carrier_id': self.carrier.id,
                'currency_id': self.nzd.id,
                'transport_mode': 'sea_fcl',
                'contract_id': c.id,
                'unit_quantity': utilized,
                'state': 'confirmed',
            })
        return c

    def test_cron_posts_alert_for_underutilized_near_expiry(self):
        """Contract <50% util and <90 days remaining gets a chatter alert."""
        c = self._make_contract(days_remaining=60, utilization_pct=30)
        initial_msg_count = len(c.message_ids)
        self.env['freight.carrier.contract'].cron_contract_pace_alert()
        c.invalidate_recordset()
        self.assertGreater(len(c.message_ids), initial_msg_count)

    def test_cron_no_alert_when_well_utilized(self):
        """Contract with >=50% util should NOT get an alert."""
        c = self._make_contract(days_remaining=60, utilization_pct=60)
        initial_msg_count = len(c.message_ids)
        self.env['freight.carrier.contract'].cron_contract_pace_alert()
        c.invalidate_recordset()
        self.assertEqual(len(c.message_ids), initial_msg_count)

    def test_cron_no_alert_when_plenty_of_time(self):
        """Contract with >90 days remaining should NOT get alert even if underutilized."""
        c = self._make_contract(days_remaining=120, utilization_pct=10)
        initial_msg_count = len(c.message_ids)
        self.env['freight.carrier.contract'].cron_contract_pace_alert()
        c.invalidate_recordset()
        self.assertEqual(len(c.message_ids), initial_msg_count)
