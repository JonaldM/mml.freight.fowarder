"""Source-inspection tests for the freight hardening defects.

Pure-Python (no Odoo). Covers:
- carrier_shipment_id / carrier_booking_id are indexed (table-scan fix).
- cron_fetch_missing_documents applies a batch limit.
- freight.tender carries a multi-company record rule in the security XML, and
  that file is wired into the manifest.
"""

import pathlib

_ADDON_DIR = pathlib.Path(__file__).parent.parent
_MODELS_DIR = _ADDON_DIR / 'models'
_SECURITY_DIR = _ADDON_DIR / 'security'


class TestBookingIndexes:
    """carrier_shipment_id / carrier_booking_id are searched on every inbound
    tracking webhook — they must be indexed to avoid full table scans."""

    def _source(self):
        return (_MODELS_DIR / 'freight_booking.py').read_text(encoding='utf-8')

    def test_carrier_booking_id_indexed(self):
        src = self._source()
        line = next(
            (ln for ln in src.splitlines() if ln.strip().startswith('carrier_booking_id = fields.Char')),
            '',
        )
        assert 'index=True' in line, "carrier_booking_id must be index=True"

    def test_carrier_shipment_id_indexed(self):
        src = self._source()
        line = next(
            (ln for ln in src.splitlines() if ln.strip().startswith('carrier_shipment_id = fields.Char')),
            '',
        )
        assert 'index=True' in line, "carrier_shipment_id must be index=True"


class TestDocFetchBatchLimit:
    """cron_fetch_missing_documents must bound its search so a slow carrier API
    can't make a single run hang on an unbounded batch."""

    def _source(self):
        return (_MODELS_DIR / 'freight_booking_cron.py').read_text(encoding='utf-8')

    def test_batch_limit_constant_defined(self):
        assert '_DOC_FETCH_BATCH_LIMIT' in self._source()

    def test_search_passes_limit(self):
        src = self._source()
        # The unbounded search must be gone; the bounded form must be present.
        assert "self.search([('state', 'in', doc_states)])" not in src, (
            "cron_fetch_missing_documents must not use an unbounded search"
        )
        assert 'limit=_DOC_FETCH_BATCH_LIMIT' in src, (
            "cron_fetch_missing_documents must pass limit=_DOC_FETCH_BATCH_LIMIT"
        )


class TestMultiCompanyRecordRules:
    """freight.tender must be scoped to allowed companies via an ir.rule."""

    def _rules_source(self):
        return (_SECURITY_DIR / 'freight_record_rules.xml').read_text(encoding='utf-8')

    def test_record_rules_file_exists(self):
        assert (_SECURITY_DIR / 'freight_record_rules.xml').exists()

    def test_tender_company_rule_present(self):
        src = self._rules_source()
        assert 'model_freight_tender' in src, "rule must target freight.tender"
        assert "[('company_id', 'in', company_ids)]" in src, (
            "rule must scope by company_ids (Odoo 19 multi-company form)"
        )

    def test_rules_wired_into_manifest(self):
        manifest = (_ADDON_DIR / '__manifest__.py').read_text(encoding='utf-8')
        assert 'security/freight_record_rules.xml' in manifest, (
            "record-rules file must be listed in the manifest data"
        )
