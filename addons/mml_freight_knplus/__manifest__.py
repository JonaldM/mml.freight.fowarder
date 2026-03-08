{
    'name': 'MML Freight — K+N Adapter',
    'version': '19.0.1.0.0',
    'category': 'Inventory/Inventory',
    'summary': 'Kuehne+Nagel carrier adapter for MML freight orchestration',
    'author': 'MML Consumer Products Ltd',
    'website': 'https://www.mmlconsumerproducts.co.nz',
    'license': 'LGPL-3',
    'depends': ['mml_freight'],
    'data': [
        'security/ir.model.access.csv',
        'views/freight_carrier_knplus_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
