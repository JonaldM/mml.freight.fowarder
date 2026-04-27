# MML Freight — K+N Adapter

Kuehne+Nagel (K+N) carrier adapter for the `mml_freight` orchestration framework.

## Status: Scaffold

This module is a **scaffold pending K+N API onboarding**. The live adapter
(`adapters/kn_adapter.py`) raises `NotImplementedError` / `UserError` on every
real call to `create_booking`, `get_tracking`, `get_documents`, and
`handle_webhook`. The mock adapter (`adapters/kn_mock_adapter.py`) returns
canned responses in sandbox mode, so demo environments stay green.

To prevent operators from accidentally tendering real freight against the
scaffold, the module ships with two layers of defence:

1. Pre-seeded `delivery.carrier` rows for K+N install with `active=False`.
   See `data/delivery_carrier_data.xml` and
   `mml_freight_demo/data/demo_carriers.xml`.
2. The inherited `delivery.carrier` model rejects any `create` or `write`
   that would leave a `delivery_type='knplus'` carrier active, raising a
   `UserError` with a clear "K+N integration is not yet active" message —
   unless the activation env override is set (see below).

## Activation Procedure

When K+N onboarding is complete and you are ready to bring the adapter live:

1. Implement the live calls in `adapters/kn_adapter.py` (replace the
   `NotImplementedError` / `UserError` raises with real K+N HTTP integration
   per `fowarder.docs/KN-API-Integration-Guide.md`).
2. Set `MML_KNPLUS_ENABLE=1` in the Odoo container environment (e.g. add
   to the deployment env file, Compose `environment:`, or Kubernetes Secret).
3. Restart the Odoo service so the new env var is visible to the worker
   processes.
4. In Odoo, open **Inventory > Configuration > Delivery Methods**, locate
   the K+N carrier rows, and toggle `active=True`. Verify the K+N
   environment is set to `production` (rather than `sandbox`) only after
   end-to-end tests against the live K+N sandbox have passed.
5. Run a smoke tender against a low-value PO to confirm quote / booking /
   tracking flows end-to-end before turning on `auto_tender`.

To disable K+N again (e.g. for incident response), unset the env var, or
flip `MML_KNPLUS_ENABLE` to `0`, then deactivate the carrier rows. The
gate only blocks the *transition* from inactive to active — already-active
rows continue to work until they are explicitly toggled or rewritten.

## Tests

```bash
# Pure-Python tests (no Odoo instance required)
pytest -m "not odoo_integration" -q

# Full suite (requires Odoo + database)
pytest -q
```
