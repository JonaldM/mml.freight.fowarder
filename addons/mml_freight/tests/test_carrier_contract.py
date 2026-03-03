from odoo.tests.common import TransactionCase
from odoo import fields


class TestCarrierContract(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        prod = cls.env['product.product'].create({
            'name': 'Test Freight Service',
            'type': 'service',
        })
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Test',
            'product_id': prod.id,
            'delivery_type': 'fixed',
        })
        cls.nzd = cls.env['res.currency'].search([('name', '=', 'NZD')], limit=1) or cls.env.company.currency_id

    def _make_contract(self, **kwargs):
        today = fields.Date.today()
        defaults = {
            'name': 'DSV 2026 FCL',
            'carrier_id': self.carrier.id,
            'date_start': today,
            'date_end': today.replace(year=today.year + 1),
            'commitment_unit': 'teu',
            'committed_quantity': 20.0,
            'contracted_rate': 2500.0,
            'contracted_rate_currency_id': self.nzd.id,
        }
        defaults.update(kwargs)
        return self.env['freight.carrier.contract'].create(defaults)

    def test_create_contract(self):
        c = self._make_contract()
        self.assertEqual(c.name, 'DSV 2026 FCL')
        self.assertEqual(c.committed_quantity, 20.0)
        self.assertEqual(c.commitment_unit, 'teu')

    def test_is_active_true(self):
        c = self._make_contract()
        self.assertTrue(c.is_active)

    def test_is_active_false_future(self):
        today = fields.Date.today()
        c = self._make_contract(
            date_start=today.replace(year=today.year + 1),
            date_end=today.replace(year=today.year + 2),
        )
        self.assertFalse(c.is_active)

    def test_is_active_false_expired(self):
        today = fields.Date.today()
        c = self._make_contract(
            date_start=today.replace(year=today.year - 2),
            date_end=today.replace(year=today.year - 1),
        )
        self.assertFalse(c.is_active)

    def _make_booking_for_contract(self, contract, unit_quantity=1.0, state='confirmed'):
        """Helper: create a freight.booking linked to a contract in a given state."""
        carrier = contract.carrier_id
        nzd = self.env['res.currency'].search([('name', '=', 'NZD')], limit=1) or self.env.company.currency_id
        b = self.env['freight.booking'].create({
            'carrier_id': carrier.id,
            'currency_id': nzd.id,
            'transport_mode': 'sea_fcl',
            'contract_id': contract.id,
            'unit_quantity': unit_quantity,
        })
        b.write({'state': state})
        return b

    def test_utilized_zero_when_no_bookings(self):
        c = self._make_contract()
        self.assertAlmostEqual(c.utilized_quantity, 0.0)
        self.assertAlmostEqual(c.remaining_quantity, 20.0)
        self.assertAlmostEqual(c.utilization_pct, 0.0)

    def test_utilized_counts_confirmed_booking(self):
        c = self._make_contract()
        self._make_booking_for_contract(c, unit_quantity=5.0, state='confirmed')
        c.invalidate_recordset()
        self.assertAlmostEqual(c.utilized_quantity, 5.0)
        self.assertAlmostEqual(c.remaining_quantity, 15.0)
        self.assertAlmostEqual(c.utilization_pct, 25.0)

    def test_utilized_counts_delivered_booking(self):
        c = self._make_contract()
        self._make_booking_for_contract(c, unit_quantity=3.0, state='delivered')
        c.invalidate_recordset()
        self.assertAlmostEqual(c.utilized_quantity, 3.0)

    def test_cancelled_booking_not_counted(self):
        c = self._make_contract()
        self._make_booking_for_contract(c, unit_quantity=10.0, state='cancelled')
        c.invalidate_recordset()
        self.assertAlmostEqual(c.utilized_quantity, 0.0)

    def test_draft_booking_not_counted(self):
        c = self._make_contract()
        self._make_booking_for_contract(c, unit_quantity=10.0, state='draft')
        c.invalidate_recordset()
        self.assertAlmostEqual(c.utilized_quantity, 0.0)

    def test_utilization_pct_full(self):
        c = self._make_contract()
        self._make_booking_for_contract(c, unit_quantity=20.0, state='confirmed')
        c.invalidate_recordset()
        self.assertAlmostEqual(c.utilization_pct, 100.0)

    def test_upload_document_default_returns_none(self):
        """Base adapter upload_document returns None (not supported by default)."""
        from odoo.addons.mml_freight.adapters.base_adapter import FreightAdapterBase

        class _StubAdapter(FreightAdapterBase):
            def request_quote(self, tender): return []
            def create_booking(self, tender, quote): return {}
            def get_tracking(self, booking): return []

        adapter = _StubAdapter(None, None)
        result = adapter.upload_document(None, 'test.pdf', b'bytes', 'INV')
        self.assertIsNone(result)
