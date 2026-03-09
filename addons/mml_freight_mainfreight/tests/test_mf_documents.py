"""Pure-Python tests for Mainfreight document adapter methods.

No live Odoo instance required — uses Odoo stubs from conftest.py.
"""
import pytest


class FakeCarrier:
    name = 'Mainfreight Test'
    x_mf_environment = 'uat'
    x_mf_api_key = 'test-key'


class FakeBooking:
    name = 'MF-BOOKING-001'
    carrier_booking_id = 'HB123456'
    container_number = ''
    bill_of_lading = ''


class TestMFMockAdapterDocuments:

    def _make_adapter(self):
        from odoo.addons.mml_freight_mainfreight.adapters.mf_mock_adapter import MFMockAdapter
        adapter = MFMockAdapter.__new__(MFMockAdapter)
        adapter.carrier = FakeCarrier()
        adapter.env = None
        return adapter

    def test_get_documents_uat_returns_two_docs(self):
        adapter = self._make_adapter()
        docs = adapter.get_documents(FakeBooking())
        assert len(docs) == 2

    def test_get_documents_uat_contains_pod(self):
        adapter = self._make_adapter()
        docs = adapter.get_documents(FakeBooking())
        types = [d['doc_type'] for d in docs]
        assert 'pod' in types

    def test_get_documents_uat_contains_customs(self):
        adapter = self._make_adapter()
        docs = adapter.get_documents(FakeBooking())
        types = [d['doc_type'] for d in docs]
        assert 'customs' in types

    def test_get_documents_uat_bytes_is_valid_pdf_header(self):
        adapter = self._make_adapter()
        docs = adapter.get_documents(FakeBooking())
        for doc in docs:
            assert doc['bytes'].startswith(b'%PDF-'), f"{doc['doc_type']} bytes not a PDF"

    def test_get_documents_uat_all_have_filename(self):
        adapter = self._make_adapter()
        docs = adapter.get_documents(FakeBooking())
        for doc in docs:
            assert doc.get('filename'), f"Missing filename on {doc['doc_type']}"

    def test_get_documents_uat_all_have_carrier_doc_ref(self):
        adapter = self._make_adapter()
        docs = adapter.get_documents(FakeBooking())
        for doc in docs:
            assert doc.get('carrier_doc_ref'), f"Missing carrier_doc_ref on {doc['doc_type']}"

    def test_get_invoice_uat_returns_dict(self):
        adapter = self._make_adapter()
        result = adapter.get_invoice(FakeBooking())
        assert isinstance(result, dict)

    def test_get_invoice_uat_has_required_keys(self):
        adapter = self._make_adapter()
        result = adapter.get_invoice(FakeBooking())
        assert {'carrier_invoice_ref', 'amount', 'currency', 'invoice_date'} <= result.keys()

    def test_get_invoice_uat_amount_is_positive(self):
        adapter = self._make_adapter()
        result = adapter.get_invoice(FakeBooking())
        assert result['amount'] > 0

    def test_get_invoice_uat_currency_is_nzd(self):
        adapter = self._make_adapter()
        result = adapter.get_invoice(FakeBooking())
        assert result['currency'] == 'NZD'


class TestMFAdapterFetchCarrierDocumentsStub:

    def _make_adapter(self):
        from odoo.addons.mml_freight_mainfreight.adapters.mf_adapter import MFAdapter
        adapter = MFAdapter.__new__(MFAdapter)
        adapter.carrier = FakeCarrier()
        adapter.env = None
        return adapter

    def test_fetch_carrier_documents_raises_not_implemented(self):
        adapter = self._make_adapter()
        with pytest.raises(NotImplementedError):
            adapter._fetch_carrier_documents(FakeBooking())

    def test_get_invoice_returns_none(self):
        adapter = self._make_adapter()
        result = adapter.get_invoice(FakeBooking())
        assert result is None


class TestExtractPodUrls:

    def _make_adapter(self):
        from odoo.addons.mml_freight_mainfreight.adapters.mf_adapter import MFAdapter
        adapter = MFAdapter.__new__(MFAdapter)
        adapter.carrier = FakeCarrier()
        adapter.env = None
        return adapter

    def test_extracts_from_pod_urls_list(self):
        adapter = self._make_adapter()
        data = {'podUrls': ['https://example.com/pod1.pdf', 'https://example.com/pod2.pdf']}
        assert adapter._extract_pod_urls(data) == ['https://example.com/pod1.pdf', 'https://example.com/pod2.pdf']

    def test_extracts_from_pod_url_string(self):
        adapter = self._make_adapter()
        data = {'podUrl': 'https://example.com/pod.pdf'}
        assert adapter._extract_pod_urls(data) == ['https://example.com/pod.pdf']

    def test_returns_empty_for_empty_dict(self):
        adapter = self._make_adapter()
        assert adapter._extract_pod_urls({}) == []

    def test_returns_empty_for_non_dict(self):
        adapter = self._make_adapter()
        assert adapter._extract_pod_urls([]) == []

    def test_skips_non_http_values(self):
        adapter = self._make_adapter()
        data = {'podUrls': ['ftp://bad.com/pod.pdf', 'https://good.com/pod.pdf']}
        result = adapter._extract_pod_urls(data)
        assert result == ['https://good.com/pod.pdf']
