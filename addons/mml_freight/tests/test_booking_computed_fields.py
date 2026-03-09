"""Verify computed field methods have @api.depends decorators."""
import ast
import pathlib


def _get_method_decorators(source: str, method_name: str) -> list:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name:
            return [ast.dump(d) for d in node.decorator_list]
    return []


def test_compute_current_status_has_depends():
    src = (pathlib.Path(__file__).parent.parent / 'models' / 'freight_booking.py').read_text(encoding='utf-8')
    decorators = _get_method_decorators(src, '_compute_current_status')
    assert decorators, '_compute_current_status method not found'
    assert any('depends' in d for d in decorators), (
        '_compute_current_status must have @api.depends'
    )


def test_compute_transit_kpis_has_depends():
    src = (pathlib.Path(__file__).parent.parent / 'models' / 'freight_booking.py').read_text(encoding='utf-8')
    decorators = _get_method_decorators(src, '_compute_transit_kpis')
    assert decorators, '_compute_transit_kpis method not found'
    assert any('depends' in d for d in decorators), (
        '_compute_transit_kpis must have @api.depends'
    )
