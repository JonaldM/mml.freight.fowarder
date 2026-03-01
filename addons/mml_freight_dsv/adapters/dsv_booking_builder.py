"""DSV Generic — booking request payload builder.

Ref: https://developer.dsv.com/guide-mydsv (Booking API sample payload)

Key structural points:
  - parties.sender / receiver use addressLine1 (unlike quote API which uses address1)
  - MDM goes in parties.freightPayer.address.mdm and parties.bookingParty.address.mdm
  - product.name is "Sea" / "Air" / "Road" / "Rail" (not the internal product type)
  - Sea shipments add cargoType: "LCL" or "FCL", FCL adds product.containerType
  - Package weight is totalWeight, volume is totalVolume
"""

# Maps internal product type → (DSV product name, cargoType, containerType)
_PRODUCT_DSV_MAP = {
    'SEA_LCL':    ('Sea', 'LCL', None),
    'SEA_FCL_20': ('Sea', 'FCL', '20GP'),
    'SEA_FCL_40': ('Sea', 'FCL', '40GP'),
    'AIR_EXPRESS': ('Air', None, None),
    'ROAD':        ('Road', None, None),
    'RAIL':        ('Rail', None, None),
}


def _address_dict(partner):
    return {
        'companyName':  partner.name or '',
        'addressLine1': partner.street or '',
        'city':         partner.city or '',
        'countryCode':  partner.country_id.code if partner.country_id else '',
        'zipCode':      partner.zip or '',
    }


def build_booking_payload(tender, selected_quote, carrier):
    """Build DSV POST /my/booking/v2/bookings body dict."""
    transport_mode = (selected_quote.transport_mode or '').upper()
    product_name, cargo_type, container_type = _PRODUCT_DSV_MAP.get(
        transport_mode, ('Sea', 'LCL', None)
    )

    descs = [l.description for l in tender.package_line_ids if l.description]
    goods_desc = ', '.join(descs) if descs else 'General Cargo'

    product = {'name': product_name}
    if container_type:
        product['containerType'] = container_type

    payload = {
        'autobook': False,
        'product': product,
        'incoterms': {
            'code': tender.incoterm_id.code if tender.incoterm_id else '',
        },
        'parties': {
            'sender': {
                'address': _address_dict(tender.origin_partner_id),
            },
            'receiver': {
                'address': _address_dict(tender.dest_partner_id),
            },
            'freightPayer': {
                'address': {'mdm': carrier.x_dsv_mdm or ''},
            },
            'bookingParty': {
                'address': {'mdm': carrier.x_dsv_mdm or ''},
            },
        },
        'packages': [
            {
                'quantity':       line.quantity,
                'description':    line.description or '',
                'totalWeight':    line.weight_kg,
                'totalVolume':    line.volume_m3,
                'length':         line.length_cm,
                'width':          line.width_cm,
                'height':         line.height_cm,
                'dangerousGoods': line.is_dangerous,
                'harmonizedCode': line.hs_code or '',
                'stackable':      'NO' if line.is_dangerous else 'STACKABLE',
            }
            for line in tender.package_line_ids
        ],
        'references': [
            {'value': po.name, 'type': 'ORDER_NUMBER'}
            for po in (tender.po_ids or [])
            if po.name
        ],
        'goodsDescription': goods_desc,
        'units': {'dimension': 'CM', 'weight': 'KG', 'volume': 'M3'},
    }

    if cargo_type:
        payload['cargoType'] = cargo_type

    if selected_quote.carrier_quote_ref:
        payload['quoteId'] = selected_quote.carrier_quote_ref

    return payload
