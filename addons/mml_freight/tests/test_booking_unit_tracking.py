# addons/mml_freight/tests/test_booking_unit_tracking.py
from odoo.tests.common import TransactionCase
from odoo import fields

class TestBookingUnitTracking(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.nzd = cls.env['res.currency'].search([('name', '=', 'NZD')], limit=1) or cls.env.company.currency_id
        prod = cls.env['product.product'].create({
            'name': 'Test Freight Service BUT',
            'type': 'service',
        })
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Track',
            'product_id': prod.id,
            'delivery_type': 'fixed',
        })
        today = fields.Date.today()
        cls.contract = cls.env['freight.carrier.contract'].create({
            'name': 'DSV Track Contract',
            'carrier_id': cls.carrier.id,
            'date_start': today,
            'date_end': today.replace(year=today.year + 1),
            'commitment_unit': 'teu',
            'committed_quantity': 20.0,
            'contracted_rate': 2500.0,
            'contracted_rate_currency_id': cls.nzd.id,
        })

    def _make_booking(self, **kwargs):
        defaults = {
            'carrier_id': self.carrier.id,
            'currency_id': self.nzd.id,
            'transport_mode': 'sea_fcl',
        }
        defaults.update(kwargs)
        return self.env['freight.booking'].create(defaults)

    def test_booking_has_unit_fields(self):
        b = self._make_booking()
        self.assertIsNotNone(b.unit_quantity)
        self.assertIsNotNone(b.unit_type)
        self.assertFalse(b.contract_id)

    def test_unit_type_default_sea_fcl(self):
        b = self._make_booking(transport_mode='sea_fcl')
        self.assertEqual(b.unit_type, 'teu')

    def test_unit_type_air(self):
        b = self._make_booking(transport_mode='air')
        self.assertEqual(b.unit_type, 'weight_kg')

    def test_unit_type_road(self):
        b = self._make_booking(transport_mode='road')
        self.assertEqual(b.unit_type, 'shipment_count')

    def test_contract_id_linkable(self):
        b = self._make_booking(contract_id=self.contract.id, unit_quantity=2.0)
        self.assertEqual(b.contract_id, self.contract)
        self.assertEqual(b.unit_quantity, 2.0)
