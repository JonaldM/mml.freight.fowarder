"""DSV Generic — quote request payload builder.

Ref: https://developer.dsv.com/guide-mydsv (Quote API section + sample payload)
"""

# Maps our internal product type strings to DSV cargoType values.
# Air/Road/Rail don't use cargoType — they're indicated via product selection alone.
_PRODUCT_TO_CARGO_TYPE = {
    'SEA_LCL':    'LCL',
    'SEA_FCL_20': 'FCL',
    'SEA_FCL_40': 'FCL',
}


def get_product_types(carrier, total_cbm, mode_preference):
    """Return list of DSV productType strings for the given tender.

    Grey zones return two types to trigger parallel requests.
    Specific mode_preference bypasses CBM thresholds.
    """
    if mode_preference == 'air':
        return ['AIR_EXPRESS']
    # Sea or any: use CBM thresholds
    lcl_max   = getattr(carrier, 'x_dsv_lcl_fcl_threshold',      15.0) or 15.0
    fcl20_max = getattr(carrier, 'x_dsv_fcl20_fcl40_threshold',   25.0) or 25.0
    fcl40_top = getattr(carrier, 'x_dsv_fcl40_upper',             40.0) or 40.0

    if total_cbm < lcl_max:
        return ['SEA_LCL']
    elif total_cbm < fcl20_max:
        return ['SEA_LCL', 'SEA_FCL_20']
    elif total_cbm < fcl40_top:
        return ['SEA_FCL_20', 'SEA_FCL_40']
    else:
        return ['SEA_FCL_40']


def build_quote_payload(tender, product_type, mdm_number):
    """Build DSV POST /qs/quote/v1/quotes body dict from a freight.tender record.

    Field names match the DSV Quote API sample payload:
    - from/to addresses use 'address1' (not 'addressLine1')
    - MDM goes under bookingParty.mdm (not root-level mdmNumber)
    - Sea shipments use cargoType (LCL/FCL); no cargoType for Air/Road/Rail
    - Package weight is totalWeight; volume is totalVolume
    - Package description is goodsDescription
    """
    origin = tender.origin_partner_id
    dest   = tender.dest_partner_id

    payload = {
        'from': {
            'country':  tender.origin_country_id.code if tender.origin_country_id else '',
            'city':     origin.city   or '',
            'zipCode':  origin.zip    or '',
            'address1': origin.street or '',
        },
        'to': {
            'country': tender.dest_country_id.code if tender.dest_country_id else '',
            'city':    dest.city  or '',
            'zipCode': dest.zip   or '',
        },
        'bookingParty': {
            'mdm': mdm_number or '',
        },
        'pickupDate': str(tender.requested_pickup_date) if tender.requested_pickup_date else '',
        'packages': [
            {
                'quantity':         line.quantity,
                'goodsDescription': line.description or '',
                'totalWeight':      line.weight_kg,
                'totalVolume':      line.volume_m3,
                'length':           line.length_cm,
                'width':            line.width_cm,
                'height':           line.height_cm,
            }
            for line in tender.package_line_ids
        ],
        'unitsOfMeasurement': {'weight': 'KG', 'dimension': 'CM', 'volume': 'M3'},
        'source': 'Public',
    }

    # Sea shipments require cargoType; Air/Road/Rail omit it
    cargo_type = _PRODUCT_TO_CARGO_TYPE.get(product_type)
    if cargo_type:
        payload['cargoType'] = cargo_type

    return payload
