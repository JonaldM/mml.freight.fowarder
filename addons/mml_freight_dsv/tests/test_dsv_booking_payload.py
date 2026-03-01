from odoo.tests.common import TransactionCase
from odoo.addons.mml_freight_dsv.adapters.dsv_booking_builder import build_booking_payload


class TestDsvBookingPayload(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV BK Test',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'dsv_generic',
            'x_dsv_mdm': 'MDM002',
        })
        origin = cls.env['res.partner'].create({
            'name': 'SH Sup', 'country_id': cls.env.ref('base.cn').id,
            'city': 'Shanghai', 'zip': '200001', 'street': '1 Main',
        })
        dest = cls.env['res.partner'].create({
            'name': 'AKL WH', 'country_id': cls.env.ref('base.nz').id,
            'city': 'Auckland', 'zip': '0600', 'street': '2 Freight',
        })
        po = cls.env['purchase.order'].create({'partner_id': origin.id})
        cls.tender = cls.env['freight.tender'].create({
            'purchase_order_id': po.id,
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
            'origin_partner_id': origin.id,
            'dest_partner_id': dest.id,
            'requested_pickup_date': '2026-05-10',
            'incoterm_id': cls.env.ref('account.incoterm_FOB').id,
        })
        cls.env['freight.tender.package'].create({
            'tender_id': cls.tender.id,
            'description': 'Widget', 'quantity': 5,
            'weight_kg': 10.0, 'length_cm': 40.0,
            'width_cm': 30.0, 'height_cm': 20.0,
        })
        nzd = cls.env['res.currency'].search([('name', '=', 'NZD')], limit=1) \
              or cls.env.company.currency_id
        cls.quote = cls.env['freight.tender.quote'].create({
            'tender_id': cls.tender.id,
            'carrier_id': cls.carrier.id,
            'state': 'received',
            'currency_id': nzd.id,
            'carrier_quote_ref': 'QREF001',
            'transport_mode': 'sea_lcl',
        })

    def test_autobook_is_false(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertFalse(p['autobook'])

    def test_quote_id_set(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertEqual(p['quoteId'], 'QREF001')

    def test_customer_reference_is_po_name(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertEqual(p['customerReference'], self.tender.purchase_order_id.name)

    def test_mdm_number_from_carrier(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertEqual(p['mdmNumber'], 'MDM002')

    def test_packages_mapped(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertEqual(len(p['packages']), 1)
        self.assertEqual(p['packages'][0]['quantity'], 5)

    def test_package_volume_mapped(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertAlmostEqual(p['packages'][0]['volume'], 0.12, places=6)

    def test_goods_description_from_package_descriptions(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertIn('Widget', p['goodsDescription'])

    def test_shipper_country_code(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertEqual(p['shipper']['country'], 'CN')

    def test_consignee_country_code(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertEqual(p['consignee']['country'], 'NZ')
