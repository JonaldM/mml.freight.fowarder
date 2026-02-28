{
    'name': 'MML Freight — DSV Adapter',
    'version': '19.0.1.0.0',
    'category': 'Inventory/Inventory',
    'summary': 'DSV Generic and XPress carrier adapters for MML freight orchestration',
    'author': 'MML',
    'license': 'OPL-1',
    'depends': ['mml_freight'],
    'data': [
        'security/ir.model.access.csv',
        'views/freight_carrier_dsv_views.xml',
    ],
    'installable': True,
    'auto_install': False,
}
