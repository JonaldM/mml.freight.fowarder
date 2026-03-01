from odoo.tests.common import TransactionCase

class TestPoFormFields(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'PO Fields S'})

    def _inc(self, code):
        i = self.env['account.incoterms'].search([('code', '=', code)], limit=1)
        if not i:
            i = self.env['account.incoterms'].create({'name': code, 'code': code})
        return i

    def test_responsibility_recomputes(self):
        po = self.env['purchase.order'].create({'partner_id': self.partner.id, 'incoterm_id': self._inc('FOB').id})
        self.assertEqual(po.freight_responsibility, 'buyer')
        po.incoterm_id = self._inc('DDP')
        self.assertEqual(po.freight_responsibility, 'seller')

    def test_cargo_date_writable(self):
        po = self.env['purchase.order'].create({'partner_id': self.partner.id})
        po.cargo_ready_date = '2026-04-01'
        self.assertEqual(str(po.cargo_ready_date), '2026-04-01')

    def test_tender_count_zero(self):
        po = self.env['purchase.order'].create({'partner_id': self.partner.id})
        self.assertEqual(po.tender_count, 0)

    def test_tender_count_increments(self):
        po = self.env['purchase.order'].create({'partner_id': self.partner.id, 'incoterm_id': self._inc('FOB').id})
        self.env['freight.tender'].create({'po_ids': [(4, po.id)], 'company_id': self.env.company.id, 'currency_id': self.env.company.currency_id.id})
        self.assertEqual(po.tender_count, 1)

    def test_tender_count_up_to_date_after_m2m_create(self):
        """tender_count must reflect all linked tenders even when ORM cache was populated before creation.

        Bug: @api.depends('freight_tender_id') only invalidates cache when po.freight_tender_id
        changes. When a tender is linked via tender.po_ids M2M without touching freight_tender_id,
        the cached count stays stale within the same ORM session.
        """
        po = self.env['purchase.order'].create({'partner_id': self.partner.id})
        # Warm the cache — the wrong @api.depends will cache this as 0 and never recompute
        self.assertEqual(po.tender_count, 0)
        # Create a tender linking this PO via po_ids M2M — do NOT set po.freight_tender_id
        self.env['freight.tender'].create({
            'po_ids': [(4, po.id)],
            'company_id': self.env.company.id,
            'currency_id': self.env.company.currency_id.id,
        })
        # With wrong @api.depends, po.freight_tender_id hasn't changed so cache is stale (0).
        # After fix (no @api.depends = always-recompute), must return 1.
        self.assertEqual(
            po.tender_count, 1,
            'tender_count must be fresh after a tender is linked via M2M (no freight_tender_id change)',
        )

    def test_freight_cost_currency_field_references_booking_currency(self):
        """freight_cost must declare the booking's currency, not the PO's currency.

        Bug: freight_cost uses currency_field='currency_id' (PO's currency). If the booking
        is quoted/booked in a different currency (e.g. USD booking on an NZD PO), the Odoo
        UI displays the amount with the wrong currency symbol and no conversion.
        """
        field = self.env['purchase.order']._fields.get('freight_cost')
        self.assertIsNotNone(field, 'freight_cost field must exist on purchase.order')
        self.assertNotEqual(
            field.currency_field, 'currency_id',
            'freight_cost must not use the PO\'s currency_id — booking may be in a different currency',
        )
        self.assertIn(
            'booking_id',
            field.currency_field,
            'freight_cost currency_field must traverse to the booking currency',
        )

    def test_action_creates_tender(self):
        po = self.env['purchase.order'].create({'partner_id': self.partner.id, 'incoterm_id': self._inc('EXW').id})
        po.action_request_freight_tender()
        self.assertTrue(po.freight_tender_id)
        self.assertIn(po, po.freight_tender_id.po_ids)
