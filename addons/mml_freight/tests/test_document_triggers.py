"""Pure-Python tests for FreightBooking state trigger and cron safety net logic.

No Odoo instance required. All Odoo model calls are intercepted through
FakeEnv / FakeRegistry, which capture adapter lookups and ORM writes.

Scenarios covered:
Group 1 — _auto_fetch_documents() helper:
1.  arrived_port trigger: doc_types=['customs', 'packing_list', 'label'] filters pod out
2.  delivered trigger: doc_types=None passes all document types through
3.  doc_types filter: pod returned by adapter is filtered out when not in doc_types list
4.  No adapter returns False silently (no raise)
5.  Adapter returns empty list returns False silently (no raise)
6.  Adapter raises RuntimeError — _auto_fetch_documents propagates (callers catch it)

Group 2 — _auto_fetch_invoice() helper:
7.  Adapter raises RuntimeError — returns False, posts chatter, does NOT re-raise
8.  No adapter returns False silently (no raise)
9.  Adapter returns None/empty returns False silently (no raise)

Group 3 — cron_fetch_missing_documents() targeting logic:
10. Booking with no document_ids → needs_docs = True
11. Delivered booking with all docs + invoice → skipped (no action)
12. Delivered booking with docs but no POD doc → needs_pod = True
13. Delivered booking with actual_rate == 0 → needs_invoice = True
14. Booking with no carrier credentials → skipped entirely
15. docs_fetch_pending flag targets a booking even when it already has docs.

Group 4 — write() state transition flagging:
write() must only set state and flag docs_fetch_pending on the
arrived_port/delivered transition; it must NOT fetch documents/invoice in-band
(that I/O moved to the cron to avoid holding row locks on slow carrier HTTP).
"""

import sys
import types
import importlib.util
import pathlib
import pytest

# ---------------------------------------------------------------------------
# Patch missing odoo.fields stubs before importing freight_booking directly.
# ---------------------------------------------------------------------------
_odoo_fields = sys.modules.get('odoo.fields')
if _odoo_fields is not None:
    for _fname in ('Monetary', 'Date', 'Datetime', 'Image', 'Html'):
        if not hasattr(_odoo_fields, _fname):
            setattr(_odoo_fields, _fname, type(_fname, (), {
                '__init__': lambda self, *a, **kw: None,
                '__set_name__': lambda self, owner, name: None,
            }))

# ---------------------------------------------------------------------------
# Load freight_booking.py directly, bypassing models/__init__.py.
# ---------------------------------------------------------------------------
_MODELS_DIR = pathlib.Path(__file__).parent.parent / 'models'


def _load_module_from_file(module_name, file_path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


_fb_module = _load_module_from_file(
    'mml_freight.models.freight_booking_triggers_isolated',
    _MODELS_DIR / 'freight_booking.py',
)
FreightBooking = _fb_module.FreightBooking

# Register the base module under its canonical odoo.addons path so that
# extension sub-modules can import BOOKING_STATES / _DSV_BOOKING_STATE_MAP from it.
sys.modules.setdefault('odoo.addons.mml_freight.models.freight_booking', _fb_module)

# Load extension modules and copy their methods onto FreightBooking so that
# direct-file-load tests can call them without Odoo's ORM registry.
_fb_docs_module = _load_module_from_file(
    'mml_freight.models.freight_booking_documents_isolated',
    _MODELS_DIR / 'freight_booking_documents.py',
)
for _name, _method in vars(_fb_docs_module.FreightBookingDocuments).items():
    if callable(_method) and not _name.startswith('__'):
        setattr(FreightBooking, _name, _method)

_fb_cron_module = _load_module_from_file(
    'mml_freight.models.freight_booking_cron_isolated',
    _MODELS_DIR / 'freight_booking_cron.py',
)
for _name, _method in vars(_fb_cron_module.FreightBookingCron).items():
    if callable(_method) and not _name.startswith('__'):
        setattr(FreightBooking, _name, _method)


# ---------------------------------------------------------------------------
# Fake objects
# ---------------------------------------------------------------------------

class FakeAdapter:
    """Configurable stand-in for a carrier adapter."""

    def __init__(self, docs=None, invoice=None, raise_on_docs=False, raise_on_invoice=False):
        self._docs = docs or []
        self._invoice = invoice
        self._raise_on_docs = raise_on_docs
        self._raise_on_invoice = raise_on_invoice
        self.docs_call_count = 0
        self.invoice_call_count = 0

    def get_documents(self, booking):
        self.docs_call_count += 1
        if self._raise_on_docs:
            raise RuntimeError('simulated adapter failure')
        return self._docs

    def get_invoice(self, booking):
        self.invoice_call_count += 1
        if self._raise_on_invoice:
            raise RuntimeError('simulated invoice failure')
        return self._invoice


class FakeAdapterRegistry:
    """Stand-in for self.env['freight.adapter.registry']."""

    def __init__(self, adapter=None):
        self._adapter = adapter

    def get_adapter(self, carrier):
        return self._adapter


class FakeFreightDocument:
    """Stand-in for freight.document record."""

    def __init__(self, doc_type='pod', carrier_doc_ref=''):
        self.doc_type = doc_type
        self.carrier_doc_ref = carrier_doc_ref
        self.attachment_id = None

    def __bool__(self):
        return True


class FakeDocumentSet:
    """Stand-in for a One2many recordset of freight.document."""

    def __init__(self, docs=None):
        self._docs = list(docs or [])

    def filtered(self, fn):
        return FakeDocumentSet([d for d in self._docs if fn(d)])

    def __bool__(self):
        return bool(self._docs)

    def __or__(self, other):
        combined = self._docs + (other._docs if isinstance(other, FakeDocumentSet) else [])
        return FakeDocumentSet(combined)

    def __iter__(self):
        return iter(self._docs)

    def __len__(self):
        return len(self._docs)

    def __getitem__(self, item):
        return self._docs[item]


class FakeAttachmentModel:
    """Stand-in for self.env['ir.attachment']."""

    def __init__(self):
        self.created = []

    def create(self, vals):
        self.created.append(vals)
        att = types.SimpleNamespace(
            id=len(self.created),
            name=vals.get('name', ''),
            datas=vals.get('datas', b''),
            mimetype=vals.get('mimetype', 'application/pdf'),
        )
        return att

    def search(self, domain, limit=None):
        return []


class FakeFreightDocumentModel:
    """Stand-in for self.env['freight.document']."""

    def __init__(self):
        self.created = []

    def create(self, vals):
        self.created.append(vals)
        doc = FakeFreightDocument(
            doc_type=vals.get('doc_type', ''),
            carrier_doc_ref=vals.get('carrier_doc_ref', ''),
        )
        doc.id = len(self.created)
        return doc

    def __or__(self, other):
        return FakeDocumentSet()


class FakeEnv:
    """Stand-in for self.env."""

    def __init__(self, adapter=None):
        self._registry = FakeAdapterRegistry(adapter)
        self._attachment_model = FakeAttachmentModel()
        self._freight_document_model = FakeFreightDocumentModel()
        self.context = {}
        self._chatter_posts = []

    def __getitem__(self, model_name):
        if model_name == 'freight.adapter.registry':
            return self._registry
        if model_name == 'ir.attachment':
            return self._attachment_model
        if model_name == 'freight.document':
            return self._freight_document_model
        raise KeyError(f'FakeEnv does not stub model: {model_name}')

    def get(self, key, default=None):
        return self.context.get(key, default)

    @property
    def attachment_model(self):
        return self._attachment_model

    @property
    def freight_document_model(self):
        return self._freight_document_model


class FakeCarrier:
    """Stand-in for delivery.carrier."""

    def __init__(self, name='Test Carrier', api_key=None, dsv_client_id=None):
        self.name = name
        if api_key is not None:
            self.x_mf_api_key = api_key
        if dsv_client_id is not None:
            self.x_dsv_client_id = dsv_client_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_booking(
    name='FB/2026/001',
    state='arrived_port',
    actual_rate=0,
    document_ids=None,
    adapter=None,
    carrier=None,
):
    """Return a FreightBooking instance wired with a FakeEnv."""
    booking = FreightBooking.__new__(FreightBooking)
    booking.name = name
    booking.state = state
    booking.actual_rate = actual_rate
    booking.po_ids = []
    booking.pod_attachment_id = None
    booking.carrier_id = carrier or FakeCarrier(api_key='key123')
    booking.id = 1
    booking.document_ids = FakeDocumentSet(document_ids or [])
    booking.docs_fetch_pending = False

    env = FakeEnv(adapter=adapter)
    booking.env = env
    # with_context on a plain instance just returns self for these tests
    booking.with_context = lambda **kw: booking
    booking._chatter = []
    booking.message_post = lambda body='', message_type='comment', subtype_xmlid=None: (
        booking._chatter.append(body)
    )

    return booking


def _make_doc(doc_type):
    return {
        'doc_type': doc_type,
        'filename': f'{doc_type}.pdf',
        'bytes': b'%PDF',
        'carrier_doc_ref': '',
    }


# ---------------------------------------------------------------------------
# Group 1 — _auto_fetch_documents()
# ---------------------------------------------------------------------------

class TestAutoFetchDocuments:

    # 1. arrived_port filter: only customs/packing_list/label processed; pod excluded
    def test_arrived_port_filter_excludes_pod(self):
        adapter = FakeAdapter(docs=[
            _make_doc('customs'),
            _make_doc('packing_list'),
            _make_doc('label'),
            _make_doc('pod'),
        ])
        booking = _make_booking(adapter=adapter)

        booking._auto_fetch_documents(doc_types=['customs', 'packing_list', 'label'])

        created_types = [c['name'].replace('.pdf', '') for c in booking.env.attachment_model.created]
        assert 'pod' not in created_types
        assert 'customs' in created_types
        assert 'packing_list' in created_types
        assert 'label' in created_types

    # 2. delivered: doc_types=None passes all types through
    def test_delivered_none_doc_types_passes_all(self):
        adapter = FakeAdapter(docs=[
            _make_doc('pod'),
            _make_doc('customs'),
            _make_doc('invoice'),
        ])
        booking = _make_booking(state='delivered', adapter=adapter)

        booking._auto_fetch_documents(doc_types=None)

        created_types = [c['name'].replace('.pdf', '') for c in booking.env.attachment_model.created]
        assert 'pod' in created_types
        assert 'customs' in created_types
        assert 'invoice' in created_types

    # 3. filter in action: pod from adapter filtered when doc_types excludes it
    def test_doc_types_filter_excludes_non_matching_types(self):
        adapter = FakeAdapter(docs=[
            _make_doc('pod'),
            _make_doc('customs'),
        ])
        booking = _make_booking(adapter=adapter)

        result = booking._auto_fetch_documents(doc_types=['customs'])

        created_types = [c['name'].replace('.pdf', '') for c in booking.env.attachment_model.created]
        assert 'pod' not in created_types
        assert 'customs' in created_types
        assert result is True

    # 4. No adapter available → returns False, no raise
    def test_no_adapter_returns_false(self):
        booking = _make_booking(adapter=None)

        result = booking._auto_fetch_documents(doc_types=['customs'])

        assert result is False

    # 5. Adapter returns empty list → returns False, no raise
    def test_empty_docs_returns_false(self):
        adapter = FakeAdapter(docs=[])
        booking = _make_booking(adapter=adapter)

        result = booking._auto_fetch_documents(doc_types=None)

        assert result is False

    # 6. All docs filtered out (none match doc_types) → returns False
    def test_all_filtered_returns_false(self):
        adapter = FakeAdapter(docs=[_make_doc('pod')])
        booking = _make_booking(adapter=adapter)

        result = booking._auto_fetch_documents(doc_types=['customs'])

        assert result is False
        assert booking.env.attachment_model.created == []

    # 6b. Adapter raises → exception propagates (write() wraps in try/except)
    def test_adapter_raise_propagates(self):
        adapter = FakeAdapter(raise_on_docs=True)
        booking = _make_booking(adapter=adapter)

        with pytest.raises(RuntimeError, match='simulated adapter failure'):
            booking._auto_fetch_documents(doc_types=None)


# ---------------------------------------------------------------------------
# Group 2 — _auto_fetch_invoice()
# ---------------------------------------------------------------------------

class TestAutoFetchInvoice:

    # 7. Adapter raises → returns False, posts chatter, does NOT re-raise
    def test_adapter_raise_returns_false_and_posts_chatter(self):
        adapter = FakeAdapter(raise_on_invoice=True)
        booking = _make_booking(state='delivered', adapter=adapter)

        result = booking._auto_fetch_invoice()

        assert result is False
        # Chatter note posted
        assert any('retry' in msg.lower() or 'failed' in msg.lower() for msg in booking._chatter)

    # 8. No adapter returns False silently
    def test_no_adapter_returns_false(self):
        booking = _make_booking(state='delivered', adapter=None)

        result = booking._auto_fetch_invoice()

        assert result is False
        assert booking._chatter == []

    # 9. Adapter returns None → returns False
    def test_adapter_returns_none_returns_false(self):
        adapter = FakeAdapter(invoice=None)
        booking = _make_booking(state='delivered', adapter=adapter)

        result = booking._auto_fetch_invoice()

        assert result is False


# ---------------------------------------------------------------------------
# Group 3 — cron_fetch_missing_documents() targeting logic
# ---------------------------------------------------------------------------
#
# Rather than calling cron_fetch_missing_documents() (which uses self.search and
# self.env infrastructure), we test the targeting conditions directly using the
# same predicate logic that the cron uses. This is the pure-Python approach.
# ---------------------------------------------------------------------------

# MAINTENANCE WARNING: _cron_needs() duplicates the targeting logic from
# cron_fetch_missing_documents() in freight_booking_cron.py.
# Any change to that method's targeting conditions (doc_states list, the
# docs_fetch_pending flag, needs_* checks, credential detection) MUST be
# mirrored here, or Group 3 tests will pass while covering stale behaviour.
def _cron_needs(booking):
    """Replicate the cron targeting logic from cron_fetch_missing_documents()."""
    doc_states = ['in_transit', 'arrived_port', 'customs', 'delivered']
    if booking.state not in doc_states:
        return None  # not targeted

    carrier = booking.carrier_id
    has_credentials = bool(
        getattr(carrier, 'x_mf_api_key', None) or
        getattr(carrier, 'x_dsv_client_id', None)
    )
    if not has_credentials:
        return {'skipped': True}

    pending = getattr(booking, 'docs_fetch_pending', False)
    needs_docs = not booking.document_ids
    needs_pod = (
        booking.state == 'delivered' and
        not booking.document_ids.filtered(lambda d: d.doc_type == 'pod')
    )
    needs_invoice = booking.state == 'delivered' and booking.actual_rate == 0

    if not (pending or needs_docs or needs_pod or needs_invoice):
        return {
            'pending': False,
            'needs_docs': False,
            'needs_pod': False,
            'needs_invoice': False,
        }

    return {
        'pending': pending,
        'needs_docs': needs_docs,
        'needs_pod': needs_pod,
        'needs_invoice': needs_invoice,
    }


class TestCronTargetingLogic:

    # 10. Booking with no document_ids → needs_docs = True
    def test_no_documents_sets_needs_docs(self):
        booking = _make_booking(state='in_transit', document_ids=[])
        result = _cron_needs(booking)
        assert result is not None
        assert result.get('needs_docs') is True

    # 11. Delivered booking with all docs + POD + invoice → completely skipped
    def test_complete_delivered_booking_is_skipped(self):
        pod_doc = FakeFreightDocument(doc_type='pod')
        customs_doc = FakeFreightDocument(doc_type='customs')
        booking = _make_booking(
            state='delivered',
            actual_rate=500.0,
            document_ids=[pod_doc, customs_doc],
        )
        result = _cron_needs(booking)
        assert result == {
            'pending': False, 'needs_docs': False, 'needs_pod': False, 'needs_invoice': False,
        }

    # 12. Delivered with docs but no POD → needs_pod = True
    def test_delivered_no_pod_sets_needs_pod(self):
        customs_doc = FakeFreightDocument(doc_type='customs')
        booking = _make_booking(
            state='delivered',
            actual_rate=500.0,
            document_ids=[customs_doc],
        )
        result = _cron_needs(booking)
        assert result is not None
        assert result.get('needs_pod') is True

    # 13. Delivered with actual_rate == 0 → needs_invoice = True
    def test_delivered_zero_rate_sets_needs_invoice(self):
        pod_doc = FakeFreightDocument(doc_type='pod')
        booking = _make_booking(
            state='delivered',
            actual_rate=0,
            document_ids=[pod_doc],
        )
        result = _cron_needs(booking)
        assert result is not None
        assert result.get('needs_invoice') is True

    # 14. Carrier with no credentials → skipped
    def test_no_credentials_carrier_skipped(self):
        # Carrier with neither x_mf_api_key nor x_dsv_client_id
        carrier = FakeCarrier(api_key=None, dsv_client_id=None)
        booking = _make_booking(
            state='in_transit',
            document_ids=[],
            carrier=carrier,
        )
        result = _cron_needs(booking)
        assert result == {'skipped': True}

    # Extra: booking in non-targeted state is not targeted by cron
    def test_draft_state_not_targeted(self):
        booking = _make_booking(state='draft', document_ids=[])
        result = _cron_needs(booking)
        assert result is None

    # Extra: carrier with only DSV credentials still passes credential check
    def test_dsv_credentials_pass_credential_check(self):
        carrier = FakeCarrier(dsv_client_id='dsv-client-abc')
        booking = _make_booking(
            state='in_transit',
            document_ids=[],
            carrier=carrier,
        )
        result = _cron_needs(booking)
        assert result is not None
        assert result.get('skipped') is not True

    # Pending flag targets a booking even when it already has documents.
    # This closes the gap left by moving the fetch out of write(): an
    # arrived_port booking with some docs but missing the customs/label subset
    # would otherwise be skipped by the needs_* checks.
    def test_pending_flag_targets_booking_with_existing_docs(self):
        customs_doc = FakeFreightDocument(doc_type='customs')
        booking = _make_booking(
            state='arrived_port',
            document_ids=[customs_doc],   # has docs → needs_docs False
        )
        booking.docs_fetch_pending = True
        result = _cron_needs(booking)
        assert result is not None
        assert result.get('pending') is True

    def test_no_pending_no_needs_is_not_actioned(self):
        customs_doc = FakeFreightDocument(doc_type='customs')
        booking = _make_booking(
            state='arrived_port',
            document_ids=[customs_doc],
        )
        booking.docs_fetch_pending = False
        result = _cron_needs(booking)
        # Targeted (returns a dict) but every action flag is False.
        assert result == {
            'pending': False, 'needs_docs': False, 'needs_pod': False, 'needs_invoice': False,
        }


# ---------------------------------------------------------------------------
# Group 4 — write() state transition dispatch
# ---------------------------------------------------------------------------

class _IterableFreightBooking(FreightBooking):
    """FreightBooking subclass that makes a single instance iterable/filterable.

    write() snapshots prev_states via ``for rec in self`` and selects records to
    flag via ``self.filtered(...)``.  Python resolves special methods (__iter__)
    on the *type*, not the instance, so they must be defined at class level.
    ``filtered`` returns a 1- or 0-length list of self depending on the predicate.
    """

    # When True the instance behaves as an empty recordset (falsy, iterates to nothing).
    _is_empty = False

    def __iter__(self):
        if self._is_empty:
            return iter(())
        yield self

    def __len__(self):
        return 0 if self._is_empty else 1

    def __bool__(self):
        return not self._is_empty

    def filtered(self, fn):
        # Mirror Odoo: return a recordset (a FreightBooking instance) so that
        # super(FreightBooking, result).write(...) is valid. An unmatched predicate
        # yields an empty (falsy) recordset rather than a plain list.
        if fn(self):
            return self
        empty = _IterableFreightBooking.__new__(_IterableFreightBooking)
        empty._is_empty = True
        return empty


class TestWriteStateTriggerDispatch:
    """write() must only set state + flag docs_fetch_pending — never fetch in-band.

    Fix: synchronous carrier document/invoice fetches were moved OUT of write()
    (they held row locks on slow HTTP). write() now flags docs_fetch_pending on the
    arrived_port/delivered transition; cron_fetch_missing_documents does the I/O.
    """

    def _make_booking_for_write(self, prev_state, pending=False):
        """Build an iterable FreightBooking that records super().write() calls."""
        booking = _IterableFreightBooking.__new__(_IterableFreightBooking)
        booking.name = 'FB/2026/001'
        booking.state = prev_state
        booking.actual_rate = 0
        booking.po_ids = []
        booking.pod_attachment_id = None
        booking.carrier_id = FakeCarrier(api_key='key123')
        booking.id = 1
        booking.document_ids = FakeDocumentSet([])
        booking.docs_fetch_pending = pending

        env = FakeEnv(adapter=None)
        booking.env = env
        booking.with_context = lambda **kw: booking
        booking._chatter = []
        booking.message_post = lambda body='', message_type='comment', subtype_xmlid=None: (
            booking._chatter.append(body)
        )

        # Guard rails: if write() ever calls the fetch helpers again, fail loudly.
        booking._auto_fetch_calls = []
        booking._invoice_calls = []

        def fake_auto_fetch(doc_types=None):
            booking._auto_fetch_calls.append(doc_types)
            return True

        def fake_auto_invoice():
            booking._invoice_calls.append(True)
            return True

        booking._auto_fetch_documents = fake_auto_fetch
        booking._auto_fetch_invoice = fake_auto_invoice
        return booking

    def test_delivered_transition_flags_pending_and_skips_fetch(self):
        """write() to delivered flags docs_fetch_pending and does NOT fetch in-band."""
        from unittest.mock import patch
        booking = self._make_booking_for_write('arrived_port')

        with patch('odoo.models.Model.write', return_value=True) as mock_write:
            booking.write({'state': 'delivered'})

        # No synchronous carrier I/O from write().
        assert booking._auto_fetch_calls == []
        assert booking._invoice_calls == []
        # super().write was called to set the pending flag.
        flag_calls = [c for c in mock_write.call_args_list
                      if c.args and isinstance(c.args[-1], dict)
                      and 'docs_fetch_pending' in c.args[-1]]
        assert flag_calls, 'write() must set docs_fetch_pending via super().write'
        assert flag_calls[-1].args[-1]['docs_fetch_pending'] is True

    def test_arrived_port_transition_flags_pending_and_skips_fetch(self):
        """write() to arrived_port flags docs_fetch_pending and does NOT fetch."""
        from unittest.mock import patch
        booking = self._make_booking_for_write('in_transit')

        with patch('odoo.models.Model.write', return_value=True) as mock_write:
            booking.write({'state': 'arrived_port'})

        assert booking._auto_fetch_calls == []
        assert booking._invoice_calls == []
        flag_calls = [c for c in mock_write.call_args_list
                      if c.args and isinstance(c.args[-1], dict)
                      and 'docs_fetch_pending' in c.args[-1]]
        assert flag_calls and flag_calls[-1].args[-1]['docs_fetch_pending'] is True

    def test_no_flag_when_state_unchanged(self):
        """write() must not re-flag when prev state equals new state."""
        from unittest.mock import patch
        booking = self._make_booking_for_write('delivered')  # already delivered

        with patch('odoo.models.Model.write', return_value=True) as mock_write:
            booking.write({'state': 'delivered'})

        assert booking._auto_fetch_calls == []
        assert booking._invoice_calls == []
        flag_calls = [c for c in mock_write.call_args_list
                      if c.args and isinstance(c.args[-1], dict)
                      and 'docs_fetch_pending' in c.args[-1]]
        assert flag_calls == []

    def test_no_flag_when_already_pending(self):
        """write() must not re-flag a booking that is already pending."""
        from unittest.mock import patch
        booking = self._make_booking_for_write('in_transit', pending=True)

        with patch('odoo.models.Model.write', return_value=True) as mock_write:
            booking.write({'state': 'arrived_port'})

        flag_calls = [c for c in mock_write.call_args_list
                      if c.args and isinstance(c.args[-1], dict)
                      and 'docs_fetch_pending' in c.args[-1]]
        assert flag_calls == []

    def test_non_state_write_does_not_flag(self):
        """write() with no state key must not flag or fetch."""
        from unittest.mock import patch
        booking = self._make_booking_for_write('in_transit')

        with patch('odoo.models.Model.write', return_value=True) as mock_write:
            booking.write({'actual_rate': 500.0})

        assert booking._auto_fetch_calls == []
        assert booking._invoice_calls == []
        flag_calls = [c for c in mock_write.call_args_list
                      if c.args and isinstance(c.args[-1], dict)
                      and 'docs_fetch_pending' in c.args[-1]]
        assert flag_calls == []
