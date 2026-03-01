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
            'po_ids': [(4, cls.po.id)],
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

    def test_action_book_lock_acquired_and_state_rechecked(self):
        """action_book executes SELECT FOR UPDATE NOWAIT before the adapter API call."""
        call_order = []
        original_execute = self.env.cr.execute

        def mock_execute(query, *args, **kwargs):
            if 'FOR UPDATE NOWAIT' in str(query):
                call_order.append('lock')
            return original_execute(query, *args, **kwargs)

        mock_adapter = MagicMock()

        def record_booking(*args, **kwargs):
            call_order.append('api')
            return self._mock_booking_result()

        mock_adapter.create_booking.side_effect = record_booking

        with patch.object(self.env.cr, 'execute', side_effect=mock_execute), \
             patch.object(
                 type(self.env['freight.adapter.registry']),
                 'get_adapter', return_value=mock_adapter,
             ):
            self.tender.action_book()

        self.assertIn('lock', call_order, 'SELECT FOR UPDATE NOWAIT must be called')
        self.assertIn('api', call_order, 'create_booking must be called')
        self.assertLess(
            call_order.index('lock'),
            call_order.index('api'),
            'Lock must be acquired before the API call',
        )

    def test_action_book_not_selected_state_raises(self):
        """action_book raises UserError when state is not 'selected' (state guard, not quote guard)."""
        tender2 = self.env['freight.tender'].create({
            'po_ids': [(4, self.po.id)],
            'company_id': self.env.company.id,
            'currency_id': self.env.company.currency_id.id,
            'state': 'draft',
        })
        # Set a quote so the 'no quote selected' guard is bypassed and the state guard fires
        quote2 = self.env['freight.tender.quote'].create({
            'tender_id': tender2.id,
            'carrier_id': self.carrier.id,
            'state': 'received',
            'currency_id': self.env.company.currency_id.id,
            'total_rate': 100.0,
        })
        tender2.selected_quote_id = quote2.id
        with self.assertRaises(UserError):
            tender2.action_book()
