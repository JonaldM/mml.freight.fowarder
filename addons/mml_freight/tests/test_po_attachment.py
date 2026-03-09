"""Pure-Python tests for FreightBooking._attach_documents_to_pos().

No Odoo instance required. All Odoo model calls are intercepted through
FakeEnv, which captures ir.attachment search/create operations and PO
message_post calls.

Scenarios covered:
1. Creates the correct number of attachments (1 per doc per PO)
2. Attaches to the correct res_id (po.id)
3. No attachments created when there are no POs
4. No attachments created when there are no docs
5. One chatter message posted per PO when at least one doc was attached
6. Chatter message contains the booking name
7. Chatter message contains the doc_type
8. Idempotency: skips attachment when the same filename already exists on PO
9. No chatter posted when all attachments were duplicates (skipped)
"""

import sys
import types
import importlib.util
import pathlib
import pytest

# ---------------------------------------------------------------------------
# Patch missing odoo.fields stubs before importing freight_booking directly.
# The conftest installs a minimal stub but omits Monetary (and a few others
# used by sibling models loaded via models/__init__.py). We add them here so
# the direct file import below succeeds without loading the full package init.
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
# Load freight_booking.py directly, bypassing models/__init__.py so we avoid
# importing the full sibling model chain (which requires Odoo ORM stubs for
# Monetary, Many2one relationships, etc.).
# ---------------------------------------------------------------------------
_MODELS_DIR = pathlib.Path(__file__).parent.parent / 'models'

def _load_module_from_file(module_name, file_path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod

_fb_module = _load_module_from_file(
    'mml_freight.models.freight_booking_isolated',
    _MODELS_DIR / 'freight_booking.py',
)
FreightBooking = _fb_module.FreightBooking


# ---------------------------------------------------------------------------
# Fake objects
# ---------------------------------------------------------------------------

class FakeAttachment:
    """Minimal stand-in for ir.attachment (source attachment on freight.document)."""

    def __init__(self, name='doc.pdf', datas=b'%PDF', mimetype='application/pdf'):
        self.name = name
        self.datas = datas
        self.mimetype = mimetype

    def __bool__(self):
        return True


class FakeFreightDoc:
    """Minimal stand-in for freight.document record."""

    def __init__(self, attachment, doc_type='pod'):
        self.attachment_id = attachment
        self.doc_type = doc_type


class FakePO:
    """Minimal stand-in for purchase.order record."""

    def __init__(self, po_id):
        self.id = po_id
        self.messages = []

    def message_post(self, body='', message_type='comment', subtype_xmlid=None):
        self.messages.append({
            'body': body,
            'message_type': message_type,
            'subtype_xmlid': subtype_xmlid,
        })

    def __bool__(self):
        return True


class FakeAttachmentModel:
    """Stand-in for self.env['ir.attachment'].

    Tracks created attachments and supports configurable search results.
    """

    def __init__(self, existing_names_by_po=None):
        # existing_names_by_po: dict[int, set[str]] — filenames that already exist per PO id
        self._existing = existing_names_by_po or {}
        self.created = []  # list of dicts passed to .create()

    def search(self, domain, limit=None):
        # Extract res_id and name from the domain list
        # domain format: [('res_model', '=', 'purchase.order'), ('res_id', '=', po_id), ('name', '=', fname)]
        res_id = None
        name = None
        for clause in domain:
            if clause[0] == 'res_id':
                res_id = clause[2]
            if clause[0] == 'name':
                name = clause[2]
        existing = self._existing.get(res_id, set())
        if name in existing:
            return [True]  # truthy — attachment found
        return []  # falsy — not found

    def create(self, vals):
        self.created.append(vals)
        # Record so subsequent searches see this as existing
        po_id = vals.get('res_id')
        fname = vals.get('name')
        if po_id is not None and fname:
            self._existing.setdefault(po_id, set()).add(fname)
        return True


class FakeEnv:
    """Minimal stand-in for self.env.

    Usage: pass existing_names_by_po to pre-seed attachments that already exist.
    """

    def __init__(self, existing_names_by_po=None):
        self._attachment_model = FakeAttachmentModel(existing_names_by_po)
        self.context = {}

    def __getitem__(self, model_name):
        if model_name == 'ir.attachment':
            return self._attachment_model
        raise KeyError(f'FakeEnv does not stub model: {model_name}')

    @property
    def attachment_model(self):
        return self._attachment_model


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_booking(name='FB/2026/001', po_list=None):
    """Return a FreightBooking-like object with faked env and po_ids."""
    booking = FreightBooking.__new__(FreightBooking)
    booking.name = name
    booking.po_ids = po_list or []
    return booking


def _attach(booking, freight_docs, existing_names_by_po=None):
    """Wire a FakeEnv onto *booking* and call _attach_documents_to_pos()."""
    env = FakeEnv(existing_names_by_po)
    booking.env = env
    booking._attach_documents_to_pos(freight_docs)
    return env


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAttachDocumentsToPOs:

    # 1. Creates correct number of attachments (1 per doc per PO)
    def test_attachment_count_is_docs_times_pos(self):
        po_a = FakePO(10)
        po_b = FakePO(20)
        doc1 = FakeFreightDoc(FakeAttachment('invoice.pdf'), 'invoice')
        doc2 = FakeFreightDoc(FakeAttachment('pod.pdf'), 'pod')

        booking = _make_booking(po_list=[po_a, po_b])
        env = _attach(booking, [doc1, doc2])

        # 2 docs x 2 POs = 4 attachments
        assert len(env.attachment_model.created) == 4

    # 2. Attaches to the correct res_id (po.id)
    def test_attachments_use_correct_res_id(self):
        po_a = FakePO(10)
        po_b = FakePO(20)
        doc = FakeFreightDoc(FakeAttachment('customs.pdf'), 'customs')

        booking = _make_booking(po_list=[po_a, po_b])
        env = _attach(booking, [doc])

        res_ids = [c['res_id'] for c in env.attachment_model.created]
        assert 10 in res_ids
        assert 20 in res_ids

    # 3. No attachments when no POs
    def test_no_attachments_when_no_pos(self):
        doc = FakeFreightDoc(FakeAttachment('pod.pdf'), 'pod')
        booking = _make_booking(po_list=[])
        env = _attach(booking, [doc])
        assert env.attachment_model.created == []

    # 4. No attachments when no docs
    def test_no_attachments_when_no_docs(self):
        po = FakePO(10)
        booking = _make_booking(po_list=[po])
        env = _attach(booking, [])
        assert env.attachment_model.created == []

    # 5. One chatter message per PO when at least one doc was attached
    def test_one_chatter_message_per_po(self):
        po_a = FakePO(10)
        po_b = FakePO(20)
        doc = FakeFreightDoc(FakeAttachment('pod.pdf'), 'pod')

        booking = _make_booking(po_list=[po_a, po_b])
        _attach(booking, [doc])

        assert len(po_a.messages) == 1
        assert len(po_b.messages) == 1

    # 6. Chatter message contains booking name
    def test_chatter_message_contains_booking_name(self):
        po = FakePO(10)
        doc = FakeFreightDoc(FakeAttachment('pod.pdf'), 'pod')
        booking = _make_booking(name='FB/2026/042', po_list=[po])
        _attach(booking, [doc])

        assert 'FB/2026/042' in po.messages[0]['body']

    # 7. Chatter message contains doc_type
    def test_chatter_message_contains_doc_type(self):
        po = FakePO(10)
        doc = FakeFreightDoc(FakeAttachment('customs.pdf'), 'customs')
        booking = _make_booking(po_list=[po])
        _attach(booking, [doc])

        assert 'customs' in po.messages[0]['body']

    # 8. Idempotency: skips attachment when same filename already exists on PO
    def test_skips_existing_attachment(self):
        po = FakePO(10)
        doc = FakeFreightDoc(FakeAttachment('pod.pdf'), 'pod')
        # Pre-seed: 'pod.pdf' already exists on PO 10
        existing = {10: {'pod.pdf'}}
        booking = _make_booking(po_list=[po])
        env = _attach(booking, [doc], existing_names_by_po=existing)

        # No new attachment should be created
        assert env.attachment_model.created == []

    # 9. No chatter posted when all attachments were duplicates (skipped)
    def test_no_chatter_when_all_duplicates(self):
        po = FakePO(10)
        doc = FakeFreightDoc(FakeAttachment('pod.pdf'), 'pod')
        existing = {10: {'pod.pdf'}}
        booking = _make_booking(po_list=[po])
        _attach(booking, [doc], existing_names_by_po=existing)

        assert po.messages == []

    # Bonus: correct fields in created attachment record
    def test_attachment_fields_are_correct(self):
        po = FakePO(10)
        att = FakeAttachment('invoice.pdf', b'%PDF-data', 'application/pdf')
        doc = FakeFreightDoc(att, 'invoice')
        booking = _make_booking(po_list=[po])
        env = _attach(booking, [doc])

        assert len(env.attachment_model.created) == 1
        created = env.attachment_model.created[0]
        assert created['name'] == 'invoice.pdf'
        assert created['type'] == 'binary'
        assert created['datas'] == b'%PDF-data'
        assert created['res_model'] == 'purchase.order'
        assert created['res_id'] == 10
        assert created['mimetype'] == 'application/pdf'

    # Bonus: partial idempotency — one doc new, one duplicate
    def test_partial_idempotency_attaches_new_skips_duplicate(self):
        po = FakePO(10)
        doc_existing = FakeFreightDoc(FakeAttachment('pod.pdf'), 'pod')
        doc_new = FakeFreightDoc(FakeAttachment('invoice.pdf'), 'invoice')
        existing = {10: {'pod.pdf'}}
        booking = _make_booking(po_list=[po])
        env = _attach(booking, [doc_existing, doc_new], existing_names_by_po=existing)

        # Only the new one should be created
        assert len(env.attachment_model.created) == 1
        assert env.attachment_model.created[0]['name'] == 'invoice.pdf'
        # Chatter should reference only the new doc_type
        assert len(po.messages) == 1
        assert 'invoice' in po.messages[0]['body']
        assert 'pod' not in po.messages[0]['body']
