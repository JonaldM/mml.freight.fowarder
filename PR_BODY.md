## Summary

- Audit finding M17 (2026-04-27 production-readiness review): `mml_freight_knplus` is a scaffold — every adapter call (`create_booking`, `get_tracking`, `get_documents`, `handle_webhook`) raises `UserError` / `NotImplementedError`. Without a UI gate, an operator could pick K+N from the carrier dropdown and tender real freight against the stub.
- Adds a defence-in-depth gate so that K+N carriers cannot be activated by accident:
  - Pre-seeded `delivery.carrier` rows for K+N install with `active=False` (`mml_freight_knplus/data/delivery_carrier_data.xml`, plus `mml_freight_demo/data/demo_carriers.xml` for demo dbs).
  - The inherited `delivery.carrier` model rejects any `create` / `write` that would leave a `delivery_type='knplus'` carrier active, with a clear `UserError`: *"K+N integration is not yet active..."*
  - The gate is bypassable only when the operator explicitly sets `MML_KNPLUS_ENABLE=1` in the Odoo environment — the documented signal that K+N onboarding is complete and `kn_adapter.py` has been brought live.
- Adapter behaviour itself is unchanged — every `UserError` / `NotImplementedError` raise in `kn_adapter.py` stays exactly as it was. This PR is purely UI-side defence.
- Adds `addons/mml_freight_knplus/README.md` documenting the activation procedure (set env var, restart, toggle carrier rows, run smoke tender).

## Files modified

- `addons/mml_freight_knplus/__manifest__.py` — register new data file
- `addons/mml_freight_knplus/models/freight_carrier_knplus.py` — `create` / `write` gate + helper
- `addons/mml_freight_knplus/data/delivery_carrier_data.xml` — new, `noupdate="1"`, K+N row with `active=False`
- `addons/mml_freight_demo/data/demo_carriers.xml` — K+N demo row defaults to `active=False`
- `addons/mml_freight_knplus/tests/test_pure_kn_gate.py` — 23 new pure-Python tests
- `addons/mml_freight_knplus/tests/__init__.py` — wire new test module
- `addons/mml_freight_knplus/README.md` — new, activation procedure

## Test plan

- [x] `pytest -m "not odoo_integration" -q` from worktree root: 96 passed (was 73, +23 new)
- [x] No changes to existing 73 tests — purely additive
- [x] New tests cover:
  - `_knplus_enabled()` env-var parsing (unset, `0`, `''`, `1`, padded `1`, `'true'`)
  - `_knplus_assert_can_activate()` for K+N active without override (raises), inactive (allowed), with override (allowed), non-K+N (never blocked), default-active vals (raises)
  - Stable `KNPLUS_DISABLED_MESSAGE` copy and `KNPLUS_ENABLE_ENV_VAR` constant
  - `data/delivery_carrier_data.xml` exists, is `noupdate="1"`, referenced in manifest, all K+N records `active=False`
  - `mml_freight_demo/data/demo_carriers.xml` K+N record `active=False`
  - Model class still inherits `delivery.carrier`; `create`, `write`, helpers all present

## Risk / rollback

- Operator-visible behaviour: any user trying to activate a K+N carrier sees the gate UserError until `MML_KNPLUS_ENABLE=1` is set. This is the intended outcome of the audit finding.
- Existing already-active K+N rows in production are unlikely (audit noted the module is a scaffold), but if any exist they continue to work — the gate fires only on the *transition* from inactive to active or on the create path. To deactivate them on rollout, the data file is `noupdate="1"` so it does not retroactively flip active rows.
- Rollback: revert the commit. The data file is `noupdate="1"` so it only seeds new rows; existing rows are untouched on uninstall/upgrade.

## Closes

Audit finding M17 from the 2026-04-27 production-readiness review.
