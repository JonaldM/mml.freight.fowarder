"""Pure-Python tests for IAS 2 incoterm-aware landed-cost split method.

No Odoo instance required. Loads freight_booking_landed_cost.py directly and
exercises _landed_cost_split_method() with a fake booking, plus source-inspection
checks that the action wires the helper into the cost line.

IAS 2 rule under test: insurance/duty-bearing incoterms (CIF/CIP) apportion the
capitalised cost BY VALUE; freight-only incoterms apportion BY WEIGHT.
"""

import sys
import types
import importlib.util
import pathlib


_MODELS_DIR = pathlib.Path(__file__).parent.parent / 'models'


def _load_module_from_file(module_name, file_path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


_lc_module = _load_module_from_file(
    'mml_freight.models.freight_booking_landed_cost_isolated',
    _MODELS_DIR / 'freight_booking_landed_cost.py',
)
FreightBookingLandedCost = _lc_module.FreightBookingLandedCost


def _make_booking(incoterm_code):
    """Return a bare FreightBookingLandedCost wired with a fake tender/incoterm."""
    booking = FreightBookingLandedCost.__new__(FreightBookingLandedCost)
    incoterm = types.SimpleNamespace(code=incoterm_code) if incoterm_code is not None else False
    booking.tender_id = types.SimpleNamespace(incoterm_id=incoterm)
    booking.ensure_one = lambda: None
    return booking


class TestLandedCostSplitMethod:

    def test_cif_splits_by_value(self):
        assert _make_booking('CIF')._landed_cost_split_method() == 'by_value'

    def test_cip_splits_by_value(self):
        assert _make_booking('CIP')._landed_cost_split_method() == 'by_value'

    def test_cif_lowercase_splits_by_value(self):
        # Incoterm code comparison must be case-insensitive.
        assert _make_booking('cif')._landed_cost_split_method() == 'by_value'

    def test_fob_splits_by_weight(self):
        assert _make_booking('FOB')._landed_cost_split_method() == 'by_weight'

    def test_exw_splits_by_weight(self):
        assert _make_booking('EXW')._landed_cost_split_method() == 'by_weight'

    def test_cfr_splits_by_weight(self):
        # CFR is cost+freight but NO insurance — stays by_weight.
        assert _make_booking('CFR')._landed_cost_split_method() == 'by_weight'

    def test_no_incoterm_defaults_by_weight(self):
        assert _make_booking(None)._landed_cost_split_method() == 'by_weight'

    def test_empty_code_defaults_by_weight(self):
        assert _make_booking('')._landed_cost_split_method() == 'by_weight'


class TestLandedCostActionWiring:
    """Source-inspection: the create action must use the helper, not hardcode by_weight."""

    def _source(self):
        return (_MODELS_DIR / 'freight_booking_landed_cost.py').read_text(encoding='utf-8')

    def test_split_method_helper_defined(self):
        assert '_landed_cost_split_method' in self._source()

    def test_insurance_bearing_incoterms_documented(self):
        src = self._source()
        assert 'CIF' in src and 'CIP' in src, "CIF/CIP incoterms must be branched on"

    def test_cost_line_uses_helper_not_hardcoded(self):
        src = self._source()
        # The cost line must reference the computed split_method variable.
        assert "'split_method': split_method" in src, (
            "cost line must use the incoterm-aware split_method, not a hardcoded value"
        )
