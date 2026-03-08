"""Verify DSV wizard ACL restricts create/write/unlink to stock managers."""
import csv
import pathlib


def test_dsv_wizard_acl_restricted_to_managers():
    acl_path = pathlib.Path('addons/mml_freight_dsv/security/ir.model.access.csv')
    with open(acl_path) as f:
        rows = list(csv.DictReader(f))

    # Find rows for wizard models
    wizard_rows = [
        r for r in rows
        if 'wizard' in r.get('id', '').lower() or 'wizard' in r.get('name', '').lower()
    ]

    for row in wizard_rows:
        group = row.get('group_id:id', '')
        has_write = row.get('perm_write', '0') == '1'
        has_create = row.get('perm_create', '0') == '1'
        if has_write or has_create:
            assert 'group_user' not in group, (
                f"Wizard ACL row '{row.get('id')}' grants write/create to group_user; "
                f"restrict to stock.group_stock_manager"
            )
