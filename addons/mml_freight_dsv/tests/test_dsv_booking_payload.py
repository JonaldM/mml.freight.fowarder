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
            'po_ids': [(4, po.id)],
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

    def test_product_name_for_sea_lcl(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertEqual(p['product']['name'], 'Sea')

    def test_cargo_type_lcl_for_sea_lcl(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertEqual(p['cargoType'], 'LCL')

    def test_cargo_type_absent_for_air(self):
        self.quote.transport_mode = 'air'
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertNotIn('cargoType', p)
        self.quote.transport_mode = 'sea_lcl'  # restore

    def test_quote_id_set(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertEqual(p['quoteId'], 'QREF001')

    def test_mdm_in_freight_payer(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertEqual(p['parties']['freightPayer']['address']['mdm'], 'MDM002')

    def test_mdm_in_booking_party(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertEqual(p['parties']['bookingParty']['address']['mdm'], 'MDM002')

    def test_sender_country_code(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertEqual(p['parties']['sender']['address']['countryCode'], 'CN')

    def test_receiver_country_code(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertEqual(p['parties']['receiver']['address']['countryCode'], 'NZ')

    def test_parties_uses_address_line1_not_address1(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        sender_addr = p['parties']['sender']['address']
        self.assertIn('addressLine1', sender_addr)
        self.assertNotIn('address1', sender_addr)

    def test_packages_mapped(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertEqual(len(p['packages']), 1)
        self.assertEqual(p['packages'][0]['quantity'], 5)

    def test_package_total_weight(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertAlmostEqual(p['packages'][0]['totalWeight'], 10.0)
        self.assertNotIn('grossWeight', p['packages'][0])

    def test_package_total_volume(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertAlmostEqual(p['packages'][0]['totalVolume'], 0.12, places=6)
        self.assertNotIn('volume', p['packages'][0])

    def test_references_contain_po_name(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        po_name = self.tender.po_ids[0].name
        ref_values = [r['value'] for r in p['references']]
        self.assertIn(po_name, ref_values)

    def test_goods_description_from_package_descriptions(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertIn('Widget', p['goodsDescription'])

    def test_no_flat_shipper_consignee_keys(self):
        """Old flat shipper/consignee/mdmNumber keys must not appear."""
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertNotIn('shipper', p)
        self.assertNotIn('consignee', p)
        self.assertNotIn('mdmNumber', p)
        self.assertNotIn('productType', p)
