"""DSV Generic — quote request payload builder."""


def get_product_types(carrier, total_cbm, mode_preference):
    """Return list of DSV productType strings for the given tender.

    Grey zones return two types to trigger parallel requests.
    Specific mode_preference bypasses CBM thresholds.
    """
    if mode_preference == 'air':
        return ['AIR_EXPRESS']
    if mode_preference == 'road':
        return ['ROAD']

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
    """Build DSV POST /qs/quote/v1/quotes body dict from a freight.tender record."""
    origin = tender.origin_partner_id
    dest   = tender.dest_partner_id
    return {
        'from': {
            'country':      origin.country_id.code if origin.country_id else '',
            'city':         origin.city  or '',
            'zipCode':      origin.zip   or '',
            'addressLine1': origin.street or '',
        },
        'to': {
            'country':      dest.country_id.code if dest.country_id else '',
            'city':         dest.city  or '',
            'zipCode':      dest.zip   or '',
            'addressLine1': dest.street or '',
        },
        'pickupDate':  str(tender.requested_pickup_date) if tender.requested_pickup_date else '',
        'incoterms':   tender.incoterm_id.code if tender.incoterm_id else '',
        'productType': product_type,
        'mdmNumber':   mdm_number or '',
        'packages': [
            {
                'quantity':       line.quantity,
                'description':    line.description or '',
                'grossWeight':    line.weight_kg,
                'length':         line.length_cm,
                'width':          line.width_cm,
                'height':         line.height_cm,
                'volume':         line.volume_m3,
                'dangerousGoods': line.is_dangerous,
                'harmonizedCode': line.hs_code or '',
            }
            for line in tender.package_line_ids
        ],
        'unitsOfMeasurement': {'weight': 'KG', 'dimension': 'CM', 'volume': 'M3'},
    }
