from odoo.tests.common import TransactionCase
from odoo.addons.mml_freight_dsv.adapters.dsv_quote_builder import (
    get_product_types, build_quote_payload,
)


class TestDsvQuotePayload(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV QP Test',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'dsv_generic',
            'x_dsv_mdm': 'MDM123',
            'x_dsv_lcl_fcl_threshold': 15.0,
            'x_dsv_fcl20_fcl40_threshold': 25.0,
            'x_dsv_fcl40_upper': 40.0,
        })
        origin = cls.env['res.partner'].create({
            'name': 'SH Supplier',
            'country_id': cls.env.ref('base.cn').id,
            'city': 'Shanghai', 'zip': '200000', 'street': '1 Nanjing Rd',
        })
        dest = cls.env['res.partner'].create({
            'name': 'AKL WH',
            'country_id': cls.env.ref('base.nz').id,
            'city': 'Auckland', 'zip': '0600', 'street': '1 Freight Dr',
        })
        po = cls.env['purchase.order'].create({'partner_id': origin.id})
        cls.tender = cls.env['freight.tender'].create({
            'purchase_order_id': po.id,
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
            'origin_partner_id': origin.id,
            'origin_country_id': cls.env.ref('base.cn').id,
            'dest_partner_id': dest.id,
            'dest_country_id': cls.env.ref('base.nz').id,
            'requested_pickup_date': '2026-04-01',
            'incoterm_id': cls.env.ref('account.incoterm_FOB').id,
        })
        cls.env['freight.tender.package'].create({
            'tender_id': cls.tender.id,
            'description': 'Widget', 'quantity': 10,
            'weight_kg': 25.0, 'length_cm': 60.0,
            'width_cm': 40.0, 'height_cm': 30.0,
        })

    # --- Mode selection ---

    def test_small_cbm_is_lcl_only(self):
        self.assertEqual(get_product_types(self.carrier, 5.0, 'any'), ['SEA_LCL'])

    def test_grey_zone_lcl_to_fcl20(self):
        modes = get_product_types(self.carrier, 18.0, 'any')
        self.assertIn('SEA_LCL', modes)
        self.assertIn('SEA_FCL_20', modes)
        self.assertEqual(len(modes), 2)

    def test_grey_zone_fcl20_to_fcl40(self):
        modes = get_product_types(self.carrier, 30.0, 'any')
        self.assertIn('SEA_FCL_20', modes)
        self.assertIn('SEA_FCL_40', modes)
        self.assertEqual(len(modes), 2)

    def test_large_cbm_is_fcl40_only(self):
        self.assertEqual(get_product_types(self.carrier, 45.0, 'any'), ['SEA_FCL_40'])

    def test_air_preference_bypasses_cbm(self):
        self.assertEqual(get_product_types(self.carrier, 5.0, 'air'), ['AIR_EXPRESS'])

    def test_sea_preference_uses_cbm_thresholds(self):
        modes = get_product_types(self.carrier, 5.0, 'sea')
        self.assertEqual(modes, ['SEA_LCL'])

    # --- Payload structure ---

    def test_payload_required_top_level_keys(self):
        p = build_quote_payload(self.tender, 'SEA_LCL', 'MDM123')
        for key in ('from', 'to', 'packages', 'productType', 'mdmNumber', 'unitsOfMeasurement'):
            self.assertIn(key, p)

    def test_payload_product_type_set(self):
        p = build_quote_payload(self.tender, 'SEA_FCL_20', 'MDM123')
        self.assertEqual(p['productType'], 'SEA_FCL_20')

    def test_payload_origin_country_code(self):
        p = build_quote_payload(self.tender, 'SEA_LCL', 'MDM123')
        self.assertEqual(p['from']['country'], 'CN')

    def test_payload_dest_country_code(self):
        p = build_quote_payload(self.tender, 'SEA_LCL', 'MDM123')
        self.assertEqual(p['to']['country'], 'NZ')

    def test_payload_package_weight(self):
        p = build_quote_payload(self.tender, 'SEA_LCL', 'MDM123')
        self.assertAlmostEqual(p['packages'][0]['grossWeight'], 25.0)

    def test_payload_package_quantity(self):
        p = build_quote_payload(self.tender, 'SEA_LCL', 'MDM123')
        self.assertEqual(p['packages'][0]['quantity'], 10)

    def test_payload_units_of_measurement(self):
        uom = build_quote_payload(self.tender, 'SEA_LCL', 'MDM123')['unitsOfMeasurement']
        self.assertEqual(uom['weight'], 'KG')
        self.assertEqual(uom['dimension'], 'CM')
        self.assertEqual(uom['volume'], 'M3')

    def test_payload_incoterm_code(self):
        p = build_quote_payload(self.tender, 'SEA_LCL', 'MDM123')
        self.assertEqual(p['incoterms'], 'FOB')
