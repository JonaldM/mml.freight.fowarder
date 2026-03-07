{
    'name': 'MML Freight — Mainfreight A&O Adapter',
    'version': '19.0.1.0.0',
    'category': 'Inventory/Inventory',
    'summary': 'Mainfreight Air & Ocean carrier adapter for MML freight orchestration',
    'description': """
        Integrates Mainfreight Air & Ocean as a carrier in the MML freight tender pipeline.

        Capabilities:
        - Shipment tracking via Mainfreight Tracking API (A&O housebills, containers)
        - Webhook receiver for Mainfreight Subscription API tracking push events
        - Manual quote/booking workflow (no booking API for A&O — use Mainchain portal)

        The existing mml_freight cron_sync_tracking already covers Mainfreight bookings
        once this module is installed. The dedicated MF cron (ir_cron.xml) is inactive
        by default — enable it only if you need a different polling interval.

        NOTE: Mainfreight Warehousing API (inward receipt, outbound dispatch, stock on hand)
        is handled by stock_3pl_mainfreight in the mainfreight.3pl.intergration project.
    """,
    'author': 'MML',
    'license': 'OPL-1',
    'depends': ['mml_freight'],
    'data': [
        'security/ir.model.access.csv',
        'views/freight_carrier_mainfreight_views.xml',
        'data/ir_cron.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
