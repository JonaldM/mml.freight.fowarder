"""Pure-Python tests for the K+N activation gate.

These tests don't need a live Odoo instance — they verify the env-var gate
helpers, the seed data file, and the model class structure. Live ORM
behaviour (write/create through TransactionCase) is exercised separately
in the odoo_integration tier.
"""
import os
import pathlib
import xml.etree.ElementTree as ET

import pytest

from odoo.exceptions import UserError
from odoo.addons.mml_freight_knplus.models import freight_carrier_knplus as gate_module
from odoo.addons.mml_freight_knplus.models.freight_carrier_knplus import (
    KNPLUS_DISABLED_MESSAGE,
    KNPLUS_ENABLE_ENV_VAR,
    FreightCarrierKnplus,
    _knplus_enabled,
)


# ----------------------------------------------------------------------
# 1. _knplus_enabled() reads MML_KNPLUS_ENABLE correctly.
# ----------------------------------------------------------------------
class TestKnplusEnabledHelper:

    def test_env_var_unset_returns_false(self, monkeypatch):
        monkeypatch.delenv(KNPLUS_ENABLE_ENV_VAR, raising=False)
        assert _knplus_enabled() is False

    def test_env_var_zero_returns_false(self, monkeypatch):
        monkeypatch.setenv(KNPLUS_ENABLE_ENV_VAR, '0')
        assert _knplus_enabled() is False

    def test_env_var_empty_returns_false(self, monkeypatch):
        monkeypatch.setenv(KNPLUS_ENABLE_ENV_VAR, '')
        assert _knplus_enabled() is False

    def test_env_var_one_returns_true(self, monkeypatch):
        monkeypatch.setenv(KNPLUS_ENABLE_ENV_VAR, '1')
        assert _knplus_enabled() is True

    def test_env_var_one_with_whitespace_is_tolerated(self, monkeypatch):
        # A common operator paste mistake — be forgiving on padding only.
        monkeypatch.setenv(KNPLUS_ENABLE_ENV_VAR, '  1  ')
        assert _knplus_enabled() is True

    def test_env_var_true_string_does_not_count(self, monkeypatch):
        # "true" / "yes" are NOT honoured — we want a deliberate "1".
        monkeypatch.setenv(KNPLUS_ENABLE_ENV_VAR, 'true')
        assert _knplus_enabled() is False


# ----------------------------------------------------------------------
# 2. _knplus_assert_can_activate() — direct unit test on the helper that
#    write()/create() call. This is the actual gate logic.
# ----------------------------------------------------------------------
class TestKnplusAssertCanActivate:

    def setup_method(self):
        # Bound method, but @api.model decorator in the stub is a no-op,
        # so we can call the underlying function directly.
        self.assert_fn = FreightCarrierKnplus._knplus_assert_can_activate

    def test_non_knplus_carrier_is_never_blocked(self, monkeypatch):
        monkeypatch.delenv(KNPLUS_ENABLE_ENV_VAR, raising=False)
        # Should NOT raise — gate only applies to delivery_type='knplus'.
        self.assert_fn(None, {'delivery_type': 'dsv_generic', 'active': True})
        self.assert_fn(None, {'delivery_type': 'fixed', 'active': True})

    def test_knplus_inactive_carrier_is_allowed(self, monkeypatch):
        monkeypatch.delenv(KNPLUS_ENABLE_ENV_VAR, raising=False)
        # Inactive K+N rows are fine — they are exactly the safe default.
        self.assert_fn(None, {'delivery_type': 'knplus', 'active': False})

    def test_knplus_active_without_env_raises(self, monkeypatch):
        monkeypatch.delenv(KNPLUS_ENABLE_ENV_VAR, raising=False)
        with pytest.raises(UserError) as exc:
            self.assert_fn(None, {'delivery_type': 'knplus', 'active': True})
        # The error must clearly tell the operator what to do.
        assert 'K+N integration is not yet active' in str(exc.value)
        assert KNPLUS_ENABLE_ENV_VAR in str(exc.value)

    def test_knplus_active_default_active_raises(self, monkeypatch):
        # `active` is omitted from vals — Odoo defaults it to True, so the gate
        # must still fire.
        monkeypatch.delenv(KNPLUS_ENABLE_ENV_VAR, raising=False)
        with pytest.raises(UserError):
            self.assert_fn(None, {'delivery_type': 'knplus'})

    def test_knplus_active_with_env_override_succeeds(self, monkeypatch):
        monkeypatch.setenv(KNPLUS_ENABLE_ENV_VAR, '1')
        # No raise — env override permits activation.
        self.assert_fn(None, {'delivery_type': 'knplus', 'active': True})

    def test_disabled_message_is_stable(self):
        # KNPLUS_DISABLED_MESSAGE is the canonical user-facing copy. Tests
        # elsewhere may assert against substrings — keep the key phrases stable.
        assert 'K+N integration is not yet active' in KNPLUS_DISABLED_MESSAGE
        assert KNPLUS_ENABLE_ENV_VAR in KNPLUS_DISABLED_MESSAGE
        assert 'README.md' in KNPLUS_DISABLED_MESSAGE


# ----------------------------------------------------------------------
# 3. Seed data file pre-seeds K+N carriers as active=False.
# ----------------------------------------------------------------------
class TestKnplusSeedData:

    DATA_FILE = (
        pathlib.Path(__file__).parent.parent
        / 'data' / 'delivery_carrier_data.xml'
    )
    DEMO_FILE = (
        pathlib.Path(__file__).parent.parent.parent
        / 'mml_freight_demo' / 'data' / 'demo_carriers.xml'
    )

    def _carrier_records(self, xml_path):
        tree = ET.parse(xml_path)
        return [
            r for r in tree.getroot().iter('record')
            if r.get('model') == 'delivery.carrier'
        ]

    def _get_field(self, record, name):
        for f in record.findall('field'):
            if f.get('name') == name:
                return f
        return None

    def test_data_file_exists(self):
        assert self.DATA_FILE.is_file(), (
            f'Expected seed data file at {self.DATA_FILE} — required so K+N '
            'carrier rows install with active=False by default.'
        )

    def test_data_file_is_noupdate(self):
        # noupdate="1" prevents the row resetting to active on -u.
        tree = ET.parse(self.DATA_FILE)
        assert tree.getroot().get('noupdate') == '1', (
            'delivery_carrier_data.xml must declare noupdate="1" so operator '
            'changes to the carrier row are not reverted on module update.'
        )

    def test_data_file_is_referenced_in_manifest(self):
        manifest_path = self.DATA_FILE.parent.parent / '__manifest__.py'
        manifest_src = manifest_path.read_text()
        assert 'data/delivery_carrier_data.xml' in manifest_src, (
            'Manifest must include data/delivery_carrier_data.xml so the '
            'inactive seed records load on install.'
        )

    def test_all_seeded_knplus_carriers_default_inactive(self):
        records = self._carrier_records(self.DATA_FILE)
        knplus_records = [
            r for r in records
            if (self._get_field(r, 'delivery_type') is not None
                and self._get_field(r, 'delivery_type').text == 'knplus')
        ]
        assert knplus_records, (
            'Expected at least one K+N carrier record in '
            'data/delivery_carrier_data.xml.'
        )
        for rec in knplus_records:
            active_field = self._get_field(rec, 'active')
            assert active_field is not None, (
                f'K+N carrier {rec.get("id")} must explicitly set active=False.'
            )
            # Odoo accepts eval="False" or "0"; both must produce inactive.
            eval_attr = active_field.get('eval')
            text_val = (active_field.text or '').strip()
            inactive = eval_attr in ('False', '0') or text_val in ('False', '0')
            assert inactive, (
                f'K+N carrier {rec.get("id")} must default to active=False, '
                f'got eval={eval_attr!r} text={text_val!r}.'
            )

    def test_demo_knplus_carrier_is_inactive(self):
        # Defence in depth: the demo carrier set must also default K+N inactive
        # so demo databases inherit the same safe default.
        records = self._carrier_records(self.DEMO_FILE)
        knplus_demo = [
            r for r in records
            if (self._get_field(r, 'delivery_type') is not None
                and self._get_field(r, 'delivery_type').text == 'knplus')
        ]
        assert knplus_demo, 'Demo data must contain at least one K+N carrier.'
        for rec in knplus_demo:
            active_field = self._get_field(rec, 'active')
            assert active_field is not None, (
                f'Demo K+N carrier {rec.get("id")} must explicitly set active=False.'
            )
            eval_attr = active_field.get('eval')
            text_val = (active_field.text or '').strip()
            inactive = eval_attr in ('False', '0') or text_val in ('False', '0')
            assert inactive, (
                f'Demo K+N carrier {rec.get("id")} must default to active=False.'
            )


# ----------------------------------------------------------------------
# 4. Model exposes the gate hooks (create / write overrides + helper).
# ----------------------------------------------------------------------
class TestKnplusModelStructure:

    def test_model_inherits_delivery_carrier(self):
        assert FreightCarrierKnplus._inherit == 'delivery.carrier'

    def test_create_is_overridden(self):
        assert 'create' in FreightCarrierKnplus.__dict__, (
            'FreightCarrierKnplus.create must be overridden to gate K+N '
            'carriers at row creation time.'
        )

    def test_write_is_overridden(self):
        assert 'write' in FreightCarrierKnplus.__dict__, (
            'FreightCarrierKnplus.write must be overridden so flipping '
            'active=True is gated.'
        )

    def test_assert_helper_is_present(self):
        assert hasattr(FreightCarrierKnplus, '_knplus_assert_can_activate')
        assert hasattr(FreightCarrierKnplus, '_knplus_write_would_activate')

    def test_env_var_constant_is_documented(self):
        # The exact env-var name is the load-bearing contract — ensure it
        # cannot be silently changed without breaking this test.
        assert KNPLUS_ENABLE_ENV_VAR == 'MML_KNPLUS_ENABLE'

    def test_module_re_exports_helper(self):
        # External modules / docs reference the helper by name.
        assert callable(getattr(gate_module, '_knplus_enabled'))
