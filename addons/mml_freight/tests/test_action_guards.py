from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestActionGuards(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Guard Test Carrier',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'fixed',
        })
        supplier = cls.env['res.partner'].create({'name': 'Guard Supplier'})
        cls.po = cls.env['purchase.order'].create({'partner_id': supplier.id})
        cls.tender = cls.env['freight.tender'].create({
            'purchase_order_id': cls.po.id,
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id': cls.carrier.id,
            'tender_id': cls.tender.id,
            'currency_id': cls.env.company.currency_id.id,
            'carrier_booking_id': 'BK-GUARD-001',
            'state': 'confirmed',
        })

    # ── action_confirm_with_dsv ──────────────────────────────────────────────

    def test_confirm_with_dsv_already_confirmed_raises(self):
        """action_confirm_with_dsv raises UserError when booking is already confirmed."""
        with self.assertRaises(UserError, msg='Must raise when already confirmed'):
            self.booking.action_confirm_with_dsv()

    def test_confirm_with_dsv_draft_proceeds(self):
        """action_confirm_with_dsv proceeds from draft state (no UserError raised from guard)."""
        booking = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'tender_id': self.tender.id,
            'currency_id': self.env.company.currency_id.id,
            'carrier_booking_id': 'BK-DRAFT-001',
            'state': 'draft',
        })
        mock_result = {
            'carrier_shipment_id': 'SH-001', 'vessel_name': '', 'voyage_number': '',
            'container_number': '', 'bill_of_lading': '', 'feeder_vessel_name': '',
            'feeder_voyage_number': '', 'eta': '',
        }
        mock_adapter = MagicMock(confirm_booking=MagicMock(return_value=mock_result))
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter', return_value=mock_adapter,
        ):
            booking.action_confirm_with_dsv()
        self.assertEqual(booking.state, 'confirmed')

    # ── action_request_quotes ────────────────────────────────────────────────

    def test_request_quotes_lock_acquired(self):
        """action_request_quotes executes SELECT FOR UPDATE NOWAIT."""
        tender = self.env['freight.tender'].create({
            'purchase_order_id': self.po.id,
            'company_id': self.env.company.id,
            'currency_id': self.env.company.currency_id.id,
            'state': 'draft',
        })
        execute_calls = []
        original_execute = self.env.cr.execute

        def mock_execute(query, *args, **kwargs):
            if 'FOR UPDATE NOWAIT' in str(query):
                execute_calls.append(query)
            return original_execute(query, *args, **kwargs)

        mock_adapter = MagicMock(request_quote=MagicMock(return_value=[]))
        with patch.object(self.env.cr, 'execute', side_effect=mock_execute), \
             patch.object(
                 type(self.env['freight.adapter.registry']),
                 'get_eligible_carriers', return_value=self.carrier,
             ), \
             patch.object(
                 type(self.env['freight.adapter.registry']),
                 'get_adapter', return_value=mock_adapter,
             ):
            tender.action_request_quotes()

        self.assertTrue(execute_calls, 'SELECT FOR UPDATE NOWAIT must be called')

    def test_request_quotes_wrong_state_raises(self):
        """action_request_quotes raises UserError when state is not draft/partial."""
        tender = self.env['freight.tender'].create({
            'purchase_order_id': self.po.id,
            'company_id': self.env.company.id,
            'currency_id': self.env.company.currency_id.id,
            'state': 'booked',
        })
        with self.assertRaises(UserError):
            tender.action_request_quotes()
