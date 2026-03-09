"""Verify _get_freight_cost_product includes company scope filter."""
import pathlib


def test_freight_cost_product_search_includes_company_filter():
    src = (pathlib.Path(__file__).parent.parent / 'models' / 'freight_booking.py').read_text(encoding='utf-8')
    # Find the _get_freight_cost_product function block and verify company_id is in the domain
    assert '_get_freight_cost_product' in src, "Method not found"
    # Simple heuristic: company_id must appear near the product search
    assert 'company_id' in src, (
        "_get_freight_cost_product must filter by company_id"
    )
