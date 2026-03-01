from odoo.tests.common import TransactionCase
from odoo.addons.mml_freight_dsv.adapters.dsv_quote_builder import (
    get_product_types, build_quote_payload,
)


class TestDsvQuotePayload(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.service_product = cls.env['product.product'].create({
            'name': 'DSV Test Service',
            'type': 'service',
        })
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV QP Test',
            'product_id': cls.service_product.id,
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
            'po_ids': [(4, po.id)],
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

    def test_boundary_exactly_at_lcl_threshold(self):
        # 15.0 CBM must enter grey zone 1, not stay LCL-only
        modes = get_product_types(self.carrier, 15.0, 'any')
        self.assertEqual(modes, ['SEA_LCL', 'SEA_FCL_20'])

    def test_boundary_exactly_at_fcl20_threshold(self):
        # 25.0 CBM must enter grey zone 2, not stay in grey zone 1
        modes = get_product_types(self.carrier, 25.0, 'any')
        self.assertEqual(modes, ['SEA_FCL_20', 'SEA_FCL_40'])

    def test_boundary_exactly_at_fcl40_upper(self):
        # 40.0 CBM must be FCL40-only, not grey zone 2
        modes = get_product_types(self.carrier, 40.0, 'any')
        self.assertEqual(modes, ['SEA_FCL_40'])

    # --- Payload structure (DSV Quote API schema) ---

    def test_payload_required_top_level_keys(self):
        p = build_quote_payload(self.tender, 'SEA_LCL', 'MDM123')
        for key in ('from', 'to', 'packages', 'bookingParty', 'unitsOfMeasurement'):
            self.assertIn(key, p, f"Missing required key: {key}")

    def test_payload_sea_lcl_has_cargo_type(self):
        p = build_quote_payload(self.tender, 'SEA_LCL', 'MDM123')
        self.assertEqual(p['cargoType'], 'LCL')

    def test_payload_sea_fcl20_has_cargo_type_fcl(self):
        p = build_quote_payload(self.tender, 'SEA_FCL_20', 'MDM123')
        self.assertEqual(p['cargoType'], 'FCL')

    def test_payload_air_has_no_cargo_type(self):
        p = build_quote_payload(self.tender, 'AIR_EXPRESS', 'MDM123')
        self.assertNotIn('cargoType', p)

    def test_payload_mdm_in_booking_party(self):
        p = build_quote_payload(self.tender, 'SEA_LCL', 'MDM123')
        self.assertEqual(p['bookingParty']['mdm'], 'MDM123')

    def test_payload_origin_country_code(self):
        p = build_quote_payload(self.tender, 'SEA_LCL', 'MDM123')
        self.assertEqual(p['from']['country'], 'CN')

    def test_payload_dest_country_code(self):
        p = build_quote_payload(self.tender, 'SEA_LCL', 'MDM123')
        self.assertEqual(p['to']['country'], 'NZ')

    def test_payload_from_uses_address1_not_addressline1(self):
        p = build_quote_payload(self.tender, 'SEA_LCL', 'MDM123')
        self.assertIn('address1', p['from'])
        self.assertNotIn('addressLine1', p['from'])

    def test_payload_package_total_weight(self):
        p = build_quote_payload(self.tender, 'SEA_LCL', 'MDM123')
        self.assertAlmostEqual(p['packages'][0]['totalWeight'], 25.0)
        self.assertNotIn('grossWeight', p['packages'][0])

    def test_payload_package_total_volume(self):
        p = build_quote_payload(self.tender, 'SEA_LCL', 'MDM123')
        self.assertIn('totalVolume', p['packages'][0])
        self.assertNotIn('volume', p['packages'][0])

    def test_payload_package_goods_description(self):
        p = build_quote_payload(self.tender, 'SEA_LCL', 'MDM123')
        self.assertEqual(p['packages'][0]['goodsDescription'], 'Widget')
        self.assertNotIn('description', p['packages'][0])

    def test_payload_package_quantity(self):
        p = build_quote_payload(self.tender, 'SEA_LCL', 'MDM123')
        self.assertEqual(p['packages'][0]['quantity'], 10)

    def test_payload_units_of_measurement(self):
        uom = build_quote_payload(self.tender, 'SEA_LCL', 'MDM123')['unitsOfMeasurement']
        self.assertEqual(uom['weight'], 'KG')
        self.assertEqual(uom['dimension'], 'CM')
        self.assertEqual(uom['volume'], 'M3')

    def test_payload_source_is_public(self):
        p = build_quote_payload(self.tender, 'SEA_LCL', 'MDM123')
        self.assertEqual(p['source'], 'Public')
