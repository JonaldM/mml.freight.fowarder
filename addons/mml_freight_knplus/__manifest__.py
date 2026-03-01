{
    'name': 'MML Freight — K+N Adapter',
    'version': '19.0.1.1.0',
    'category': 'Inventory/Inventory',
    'summary': 'Kuehne+Nagel carrier adapter for MML freight orchestration',
    'author': 'MML',
    'license': 'OPL-1',
    'depends': ['mml_freight'],
    'data': [
        'security/ir.model.access.csv',
        'views/freight_carrier_knplus_views.xml',
    ],
    'installable': True,
    'auto_install': False,
}
