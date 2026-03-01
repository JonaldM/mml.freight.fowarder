from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestActionBookGuard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Book Guard Carrier',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'fixed',
        })
        supplier = cls.env['res.partner'].create({'name': 'Book Guard Supplier'})
        cls.po = cls.env['purchase.order'].create({'partner_id': supplier.id})
        cls.tender = cls.env['freight.tender'].create({
            'purchase_order_id': cls.po.id,
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
            'state': 'selected',
        })
        cls.quote = cls.env['freight.tender.quote'].create({
            'tender_id': cls.tender.id,
            'carrier_id': cls.carrier.id,
            'state': 'received',
            'currency_id': cls.env.company.currency_id.id,
            'total_rate': 1800.0,
        })
        cls.tender.selected_quote_id = cls.quote.id

    def _mock_booking_result(self):
        return {
            'carrier_booking_id': 'DSV-BK-GUARD-001',
            'carrier_shipment_id': '',
            'carrier_tracking_url': '',
            'requires_manual_confirmation': True,
        }

    def test_action_book_already_booked_raises(self):
        """action_book raises UserError if tender is already in 'booked' state."""
        self.tender.write({'state': 'booked'})
        with self.assertRaises(UserError, msg='Must raise when already booked'):
            self.tender.action_book()
        # Reset
        self.tender.write({'state': 'selected'})

    def test_action_book_lock_acquired_and_state_rechecked(self):
        """action_book executes SELECT FOR UPDATE NOWAIT before API call."""
        execute_calls = []
        original_execute = self.env.cr.execute

        def mock_execute(query, *args, **kwargs):
            if 'FOR UPDATE NOWAIT' in str(query):
                execute_calls.append(query)
            return original_execute(query, *args, **kwargs)

        mock_adapter = MagicMock(
            create_booking=MagicMock(return_value=self._mock_booking_result()),
        )
        with patch.object(self.env.cr, 'execute', side_effect=mock_execute), \
             patch.object(
                 type(self.env['freight.adapter.registry']),
                 'get_adapter', return_value=mock_adapter,
             ):
            self.tender.action_book()

        self.assertTrue(execute_calls, 'SELECT FOR UPDATE NOWAIT must be called before API call')

    def test_action_book_not_selected_state_raises(self):
        """action_book raises UserError when state is not 'selected' (existing guard still works)."""
        tender2 = self.env['freight.tender'].create({
            'purchase_order_id': self.po.id,
            'company_id': self.env.company.id,
            'currency_id': self.env.company.currency_id.id,
            'state': 'draft',
        })
        with self.assertRaises(UserError):
            tender2.action_book()
