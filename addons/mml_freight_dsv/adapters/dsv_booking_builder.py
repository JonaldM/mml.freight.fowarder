"""DSV Generic — booking request payload builder."""


def _partner_dict(partner):
    return {
        'name':         partner.name or '',
        'addressLine1': partner.street or '',
        'city':         partner.city   or '',
        'zipCode':      partner.zip    or '',
        'country':      partner.country_id.code if partner.country_id else '',
    }


def build_booking_payload(tender, selected_quote, carrier):
    """Build DSV POST /booking/v2/bookings body dict."""
    descs = [l.description for l in tender.package_line_ids if l.description]
    goods_desc = ', '.join(descs) if descs else 'General Cargo'
    packages = [
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
    ]
    return {
        'autobook':          False,
        'productType':       (selected_quote.transport_mode or '').upper(),
        'mdmNumber':         carrier.x_dsv_mdm or '',
        'quoteId':           selected_quote.carrier_quote_ref or '',
        'pickupDate':        str(tender.requested_pickup_date) if tender.requested_pickup_date else '',
        'incoterms':         tender.incoterm_id.code if tender.incoterm_id else '',
        'shipper':           _partner_dict(tender.origin_partner_id),
        'consignee':         _partner_dict(tender.dest_partner_id),
        'packages':          packages,
        'goodsDescription':  goods_desc,
        'customerReference': tender.purchase_order_id.name if tender.purchase_order_id else '',
    }
