# addons/mml_freight/tests/test_contract_aware_selection.py
from odoo.tests.common import TransactionCase
from odoo import fields


class TestContractAwareSelection(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        prod = cls.env['product.product'].create({
            'name': 'Test Freight CA',
            'type': 'service',
        })
        cls.nzd = cls.env['res.currency'].search([('name', '=', 'NZD')], limit=1) or cls.env.company.currency_id
        cls.carrier_dsv = cls.env['delivery.carrier'].create({
            'name': 'DSV CA',
            'product_id': prod.id,
            'delivery_type': 'fixed',
        })
        cls.carrier_kn = cls.env['delivery.carrier'].create({
            'name': 'KN CA',
            'product_id': prod.id,
            'delivery_type': 'fixed',
        })
        today = fields.Date.today()
        cls.contract = cls.env['freight.carrier.contract'].create({
            'name': 'DSV CA Contract',
            'carrier_id': cls.carrier_dsv.id,
            'date_start': today,
            'date_end': today.replace(year=today.year + 1),
            'commitment_unit': 'teu',
            'committed_quantity': 20.0,
            'contracted_rate': 3000.0,
            'contracted_rate_currency_id': cls.nzd.id,
        })
        partner = cls.env['res.partner'].create({'name': 'CA Supplier'})
        po = cls.env['purchase.order'].create({'partner_id': partner.id})
        cls.tender = cls.env['freight.tender'].create({
            'po_ids': [(4, po.id)],
            'company_id': cls.env.company.id,
            'currency_id': cls.nzd.id,
            'state': 'quoted',
            'selection_mode': 'contract_aware',
        })
        cls.q_dsv = cls.env['freight.tender.quote'].create({
            'tender_id': cls.tender.id,
            'carrier_id': cls.carrier_dsv.id,
            'state': 'received',
            'currency_id': cls.nzd.id,
            'transport_mode': 'sea_fcl',
            'base_rate': 3200.0,
            'estimated_transit_days': 14,
        })
        cls.q_kn = cls.env['freight.tender.quote'].create({
            'tender_id': cls.tender.id,
            'carrier_id': cls.carrier_kn.id,
            'state': 'received',
            'currency_id': cls.nzd.id,
            'transport_mode': 'sea_fcl',
            'base_rate': 2800.0,
            'estimated_transit_days': 16,
        })

    def _reset_tender(self):
        self.tender.write({
            'state': 'quoted',
            'selected_quote_id': False,
            'has_opportunity_cost_alert': False,
            'opportunity_cost_nzd': 0.0,
            'selection_reason': False,
        })

    def test_contract_aware_selects_dsv(self):
        """Contract carrier (DSV) selected even though K+N is cheaper on market."""
        self._reset_tender()
        self.tender.action_auto_select()
        self.assertEqual(self.tender.selected_quote_id, self.q_dsv)

    def test_contract_aware_sets_opportunity_cost_alert(self):
        """When contract carrier costs more than market, alert flag should be set."""
        self._reset_tender()
        self.tender.action_auto_select()
        self.assertTrue(self.tender.has_opportunity_cost_alert)
        self.assertGreater(self.tender.opportunity_cost_nzd, 0)

    def test_contract_aware_no_alert_when_contract_cheaper(self):
        """No alert when contract rate is below market."""
        self._reset_tender()
        self.q_dsv.write({'base_rate': 2500.0})
        self.tender.action_auto_select()
        self.assertFalse(self.tender.has_opportunity_cost_alert)
        self.q_dsv.write({'base_rate': 3200.0})  # restore

    def test_contract_aware_falls_back_to_cheapest_when_no_commitment(self):
        """When contract is exhausted, fall back to cheapest market quote."""
        self._reset_tender()
        booking = self.env['freight.booking'].create({
            'carrier_id': self.carrier_dsv.id,
            'currency_id': self.nzd.id,
            'transport_mode': 'sea_fcl',
            'contract_id': self.contract.id,
            'unit_quantity': 20.0,
        })
        booking.write({'state': 'confirmed'})
        self.contract.invalidate_recordset()
        self.tender.quote_line_ids.invalidate_recordset()
        self.tender.action_auto_select()
        self.assertEqual(self.tender.selected_quote_id, self.q_kn)
        self.assertFalse(self.tender.has_opportunity_cost_alert)
        # Clean up
        booking.write({'state': 'cancelled'})

    def test_new_fields_on_tender(self):
        """Tender has has_opportunity_cost_alert and opportunity_cost_nzd fields."""
        self.assertIsNotNone(self.tender.has_opportunity_cost_alert)
        self.assertIsNotNone(self.tender.opportunity_cost_nzd)
