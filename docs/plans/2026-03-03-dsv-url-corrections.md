# DSV API URL Corrections — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to implement this plan task-by-task.

**Goal:** Fix three classes of DSV API bugs discovered from Postman collection review so the integration can be tested against the real DSV demo sandbox.

**Architecture:** All changes are in two adapter files (`dsv_auth.py`, `dsv_generic_adapter.py`) and their test files. Module-level URL constants are replaced with carrier-aware helper functions. The Upload endpoint path is restructured to put document type in the URL (not the form body). No model, wizard, or view changes needed.

**Tech Stack:** Python, `requests`, Odoo 19 `delivery.carrier.x_dsv_environment` field (`'demo'` | `'production'`).

---

## Background — What the Postman Collections Revealed

| API | Demo base URL | Production base URL |
|-----|--------------|-------------------|
| Generic (booking, tracking, labels, docs, invoice, upload) | `https://api.dsv.com/my-demo` | `https://api.dsv.com/my` |
| Quote | `https://api.dsv.com/qs-demo` | `https://api.dsv.com/qs` |
| OAuth token | `https://api.dsv.com/my-demo/oauth/v1/token` | `https://api.dsv.com/my/oauth/v1/token` |

**Upload endpoint (confirmed from Postman):**
```
POST /upload/v1/shipments/bookingId/{doc_type}/{booking_id}
Body: multipart/form-data — file only (NO document_type form field)
```

Our implementation had:
```
POST /upload/v1/shipments/{booking_id}/documents   ← wrong
Body: file + document_type=INV                     ← wrong
```

---

## Before You Start

Read these files:
- `addons/mml_freight_dsv/adapters/dsv_auth.py` — `_OAUTH_URL` constant, `DEMO_TOKEN` short-circuit in `get_token()`
- `addons/mml_freight_dsv/adapters/dsv_generic_adapter.py` — `_DSV_GENERIC_BASE`, `_DSV_QUOTE_BASE` constants, `upload_document()` method
- `addons/mml_freight_dsv/tests/test_dsv_auth.py` — existing auth tests (note `test_demo_no_http`)
- `addons/mml_freight_dsv/tests/test_dsv_generic_adapter.py` — existing adapter tests
- `addons/mml_freight_dsv/tests/test_dsv_doc_upload.py` — existing upload tests

---

## Task 1: Environment-aware base URLs for Generic and Quote APIs

**Files:**
- Modify: `addons/mml_freight_dsv/adapters/dsv_generic_adapter.py`
- Test: `addons/mml_freight_dsv/tests/test_dsv_generic_adapter.py`

Replace the two hardcoded module-level constants with helper functions.

### Step 1: Write the failing tests

Append this class to `addons/mml_freight_dsv/tests/test_dsv_generic_adapter.py` (after the existing `TestDsvGenericAdapter` class):

```python
import unittest


class TestDsvBaseUrls(unittest.TestCase):

    def _carrier(self, env):
        m = MagicMock()
        m.x_dsv_environment = env
        return m

    def test_generic_base_demo(self):
        from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import _generic_base
        self.assertEqual(_generic_base(self._carrier('demo')), 'https://api.dsv.com/my-demo')

    def test_generic_base_production(self):
        from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import _generic_base
        self.assertEqual(_generic_base(self._carrier('production')), 'https://api.dsv.com/my')

    def test_quote_base_demo(self):
        from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import _quote_base
        self.assertEqual(_quote_base(self._carrier('demo')), 'https://api.dsv.com/qs-demo')

    def test_quote_base_production(self):
        from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import _quote_base
        self.assertEqual(_quote_base(self._carrier('production')), 'https://api.dsv.com/qs')
```

### Step 2: Run to verify they fail

```bash
cd E:\ClaudeCode\projects\mml.odoo.apps\fowarder.intergration
python -m pytest addons/mml_freight_dsv/tests/test_dsv_generic_adapter.py::TestDsvBaseUrls -v
```
Expected: FAIL — `cannot import name '_generic_base'`

### Step 3: Implement

In `addons/mml_freight_dsv/adapters/dsv_generic_adapter.py`, replace the two module-level constants:

**Remove:**
```python
_DSV_GENERIC_BASE = 'https://api.dsv.com/my'
_DSV_QUOTE_BASE   = 'https://api.dsv.com/qs'
```

**Add:**
```python
def _generic_base(carrier):
    """DSV Generic API base URL — /my-demo for demo, /my for production."""
    if getattr(carrier, 'x_dsv_environment', 'production') == 'demo':
        return 'https://api.dsv.com/my-demo'
    return 'https://api.dsv.com/my'


def _quote_base(carrier):
    """DSV Quote API base URL — /qs-demo for demo, /qs for production."""
    if getattr(carrier, 'x_dsv_environment', 'production') == 'demo':
        return 'https://api.dsv.com/qs-demo'
    return 'https://api.dsv.com/qs'
```

Then replace all usages throughout the file. There are **8 usages of `_DSV_GENERIC_BASE`** and **1 usage of `_DSV_QUOTE_BASE`**:

| Line (approx) | Old | New |
|---|---|---|
| `create_booking` | `f'{_DSV_GENERIC_BASE}/booking/v2/bookings'` | `f'{_generic_base(self.carrier)}/booking/v2/bookings'` |
| `cancel_booking` | `f'{_DSV_GENERIC_BASE}/booking/v2/bookings/{bk_id}'` | `f'{_generic_base(self.carrier)}/booking/v2/bookings/{bk_id}'` |
| `confirm_booking` | `f'{_DSV_GENERIC_BASE}/booking/v2/bookings/{bk_id}/confirm'` | `f'{_generic_base(self.carrier)}/booking/v2/bookings/{bk_id}/confirm'` |
| `get_tracking` | `f'{_DSV_GENERIC_BASE}/tracking/v2/shipments/tmsId/{shipment_id}'` | `f'{_generic_base(self.carrier)}/tracking/v2/shipments/tmsId/{shipment_id}'` |
| `get_label` | `f'{_DSV_GENERIC_BASE}/printing/v1/labels/{bk_id}'` | `f'{_generic_base(self.carrier)}/printing/v1/labels/{bk_id}'` |
| `get_documents` | `f'{_DSV_GENERIC_BASE}/download/v1/shipments/bookingId/{bk_id}/documents'` | `f'{_generic_base(self.carrier)}/download/v1/shipments/bookingId/{bk_id}/documents'` |
| `get_invoice` | `f'{_DSV_GENERIC_BASE}/invoice/v1/invoices/shipments/{shipment_id}'` | `f'{_generic_base(self.carrier)}/invoice/v1/invoices/shipments/{shipment_id}'` |
| `upload_document` | `f'{_DSV_GENERIC_BASE}/upload/v1/shipments/{bk_id}/documents'` | *(will be fixed completely in Task 3 — for now change to `_generic_base(self.carrier)`)* |
| `request_quote` | `f'{_DSV_QUOTE_BASE}/quote/v1/quotes'` | `f'{_quote_base(self.carrier)}/quote/v1/quotes'` |

### Step 4: Run tests

```bash
python -m pytest addons/mml_freight_dsv/tests/test_dsv_generic_adapter.py::TestDsvBaseUrls -v
```
Expected: 4 PASS.

Also run the full adapter test suite to check nothing else broke:
```bash
python -m pytest addons/mml_freight_dsv/tests/test_dsv_generic_adapter.py -v
```

### Step 5: Commit

```bash
git add addons/mml_freight_dsv/adapters/dsv_generic_adapter.py \
        addons/mml_freight_dsv/tests/test_dsv_generic_adapter.py
git commit -m "fix(mml_freight_dsv): environment-aware base URLs for Generic and Quote APIs"
```

---

## Task 2: Environment-aware OAuth URL + remove DEMO_TOKEN short-circuit

**Files:**
- Modify: `addons/mml_freight_dsv/adapters/dsv_auth.py`
- Test: `addons/mml_freight_dsv/tests/test_dsv_auth.py`

Currently `_OAUTH_URL` is hardcoded to the production endpoint, and `get_token()` short-circuits for demo by returning a fake `'DEMO_TOKEN'` without HTTP. This prevents real OAuth against DSV's demo sandbox. The `DsvMockAdapter` already provides credential-free development mode — auth should not fake tokens.

### Step 1: Write the failing tests

In `addons/mml_freight_dsv/tests/test_dsv_auth.py`:

**Delete** the `test_demo_no_http` test method entirely.

**Add** these two new test methods inside `TestDsvAuth`:

```python
def test_demo_uses_demo_oauth_url(self):
    """Demo environment authenticates against /my-demo/ OAuth endpoint."""
    self.carrier.write({
        'x_dsv_environment': 'demo',
        'x_dsv_client_id': 'demo-id',
        'x_dsv_client_secret': 'demo-secret',
        'x_dsv_access_token': False,
        'x_dsv_token_expiry': False,
    })
    mock_resp = MagicMock(ok=True, status_code=200)
    mock_resp.json.return_value = {'access_token': 'DEMO_REAL_TOKEN', 'expires_in': 600}
    with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.requests.post',
               return_value=mock_resp) as mock_post:
        token = get_token(self.carrier)
    called_url = mock_post.call_args[0][0]
    self.assertIn('my-demo', called_url)
    self.assertNotIn('/my/', called_url.replace('my-demo', ''))
    self.assertEqual(token, 'DEMO_REAL_TOKEN')

def test_production_uses_my_oauth_url(self):
    """Production environment authenticates against /my/ OAuth endpoint."""
    self.carrier.write({
        'x_dsv_environment': 'production',
        'x_dsv_client_id': 'prod-id',
        'x_dsv_client_secret': 'prod-secret',
        'x_dsv_access_token': False,
        'x_dsv_token_expiry': False,
    })
    mock_resp = MagicMock(ok=True, status_code=200)
    mock_resp.json.return_value = {'access_token': 'PROD_TOKEN', 'expires_in': 600}
    with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.requests.post',
               return_value=mock_resp) as mock_post:
        token = get_token(self.carrier)
    called_url = mock_post.call_args[0][0]
    self.assertIn('/my/', called_url)
    self.assertNotIn('my-demo', called_url)
    self.assertEqual(token, 'PROD_TOKEN')
```

### Step 2: Run to verify the new tests fail

```bash
python -m pytest addons/mml_freight_dsv/tests/test_dsv_auth.py::TestDsvAuth::test_demo_uses_demo_oauth_url \
  addons/mml_freight_dsv/tests/test_dsv_auth.py::TestDsvAuth::test_production_uses_my_oauth_url -v
```
Expected: FAIL — demo short-circuits with DEMO_TOKEN, never calls `requests.post`.

### Step 3: Implement

In `addons/mml_freight_dsv/adapters/dsv_auth.py`:

**Replace** the `_OAUTH_URL` constant and the `get_token()` function:

```python
# DSV OAuth token endpoints — demo uses my-demo prefix, production uses my.
# Ref: https://developer.dsv.com/oauth-guide
_OAUTH_URLS = {
    'demo':       'https://api.dsv.com/my-demo/oauth/v1/token',
    'production': 'https://api.dsv.com/my/oauth/v1/token',
}


def _oauth_url(carrier):
    return _OAUTH_URLS.get(carrier.x_dsv_environment, _OAUTH_URLS['production'])


def get_token(carrier):
    """Return valid DSV access token, refreshing if near expiry."""
    now = fields.Datetime.now()
    if (carrier.x_dsv_access_token and carrier.x_dsv_token_expiry
            and carrier.x_dsv_token_expiry > now + timedelta(seconds=REFRESH_WINDOW_SECONDS)):
        return carrier.x_dsv_access_token
    return refresh_token(carrier)
```

In `refresh_token()`, replace the hardcoded `_OAUTH_URL` with `_oauth_url(carrier)`:

```python
resp = requests.post(
    _oauth_url(carrier),   # ← was: _OAUTH_URL
    headers={
        'DSV-Subscription-Key': carrier.dsv_any_subkey(),
        'Content-Type': 'application/x-www-form-urlencoded',
    },
    data={
        'grant_type':    'client_credentials',
        'client_id':     carrier.x_dsv_client_id,
        'client_secret': carrier.x_dsv_client_secret,
    },
    timeout=10,
)
```

### Step 4: Run tests

```bash
python -m pytest addons/mml_freight_dsv/tests/test_dsv_auth.py -v
```
Expected: all 6 tests PASS (`test_demo_uses_demo_oauth_url`, `test_production_uses_my_oauth_url`, `test_cached_token_not_expired`, `test_near_expiry_refreshes`, `test_401_raises`, `test_missing_creds_raises`). `test_demo_no_http` should no longer exist.

### Step 5: Commit

```bash
git add addons/mml_freight_dsv/adapters/dsv_auth.py \
        addons/mml_freight_dsv/tests/test_dsv_auth.py
git commit -m "fix(mml_freight_dsv): environment-aware OAuth URL, remove DEMO_TOKEN short-circuit"
```

---

## Task 3: Fix Upload API endpoint — document type in URL path, not form body

**Files:**
- Modify: `addons/mml_freight_dsv/adapters/dsv_generic_adapter.py`
- Test: `addons/mml_freight_dsv/tests/test_dsv_doc_upload.py`

The Postman collection confirms the correct URL is:
```
POST /upload/v1/shipments/bookingId/{doc_type}/{booking_id}
Body: multipart/form-data — file field only
```

### Step 1: Write the failing tests

Append these two methods inside `TestDsvDocUpload` in `addons/mml_freight_dsv/tests/test_dsv_doc_upload.py`:

```python
def test_upload_url_has_type_in_path_before_booking_id(self):
    """Upload URL must be .../bookingId/{doc_type}/{booking_id}."""
    mock_resp = _resp(status=200, json_data={'documentId': 'REF-URL'})
    with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token',
               return_value='tok'), \
         patch('requests.post', return_value=mock_resp) as mock_post:
        self._adapter().upload_document(
            self.booking, 'pi.pdf', b'bytes', 'INV'
        )
    called_url = mock_post.call_args[0][0]
    self.assertIn('bookingId/INV/BK-UPLOAD-001', called_url)

def test_upload_body_has_no_document_type_field(self):
    """Upload POST body must NOT include document_type — type belongs in the URL path."""
    mock_resp = _resp(status=200, json_data={'documentId': 'REF-BODY'})
    with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token',
               return_value='tok'), \
         patch('requests.post', return_value=mock_resp) as mock_post:
        self._adapter().upload_document(
            self.booking, 'pi.pdf', b'bytes', 'PKL'
        )
    call_kwargs = mock_post.call_args[1]
    self.assertNotIn('document_type', call_kwargs.get('data', {}))
```

### Step 2: Run to verify they fail

```bash
python -m pytest addons/mml_freight_dsv/tests/test_dsv_doc_upload.py::TestDsvDocUpload::test_upload_url_has_type_in_path_before_booking_id \
  addons/mml_freight_dsv/tests/test_dsv_doc_upload.py::TestDsvDocUpload::test_upload_body_has_no_document_type_field -v
```
Expected: FAIL — URL currently ends in `.../documents`, body has `document_type`.

### Step 3: Implement

In `addons/mml_freight_dsv/adapters/dsv_generic_adapter.py`, in `upload_document()`:

**Update the docstring comment** (line ~381):
```python
# Old:
#   DSV Upload API: POST /my/upload/v1/shipments/{booking_id}/documents
#   Body: multipart/form-data — file + document_type
# New:
#   DSV Upload API: POST /my/upload/v1/shipments/bookingId/{doc_type}/{booking_id}
#   Body: multipart/form-data — file only (doc type is a URL path parameter)
```

**Update the URL** (line ~398):
```python
# Old:
url = f'{_generic_base(self.carrier)}/upload/v1/shipments/{bk_id}/documents'
# New:
url = f'{_generic_base(self.carrier)}/upload/v1/shipments/bookingId/{dsv_type}/{bk_id}'
```

**Update BOTH POST calls** — remove `data={'document_type': dsv_type}` from both (initial request and 401-retry):

```python
resp = requests.post(
    url,
    headers=headers,
    files={'file': (filename, file_bytes, 'application/octet-stream')},
    timeout=60,
)
```

(The `data=` parameter is removed entirely from both POST calls.)

### Step 4: Run all upload tests

```bash
python -m pytest addons/mml_freight_dsv/tests/test_dsv_doc_upload.py -v
```
Expected: all 7 `TestDsvDocUpload` tests PASS, plus 8 `TestDetectDsvType` tests PASS.

### Step 5: Commit

```bash
git add addons/mml_freight_dsv/adapters/dsv_generic_adapter.py \
        addons/mml_freight_dsv/tests/test_dsv_doc_upload.py
git commit -m "fix(mml_freight_dsv): correct Upload API URL — doc type in path, not form body"
```

---

## Verification Checklist

Before calling this sprint done:

- [ ] `_generic_base(carrier)` returns `https://api.dsv.com/my-demo` for demo, `https://api.dsv.com/my` for production
- [ ] `_quote_base(carrier)` returns `https://api.dsv.com/qs-demo` for demo, `https://api.dsv.com/qs` for production
- [ ] All 8 usages of `_DSV_GENERIC_BASE` replaced with `_generic_base(self.carrier)`
- [ ] 1 usage of `_DSV_QUOTE_BASE` replaced with `_quote_base(self.carrier)`
- [ ] OAuth URL is `/my-demo/oauth/v1/token` for demo, `/my/oauth/v1/token` for production
- [ ] No `DEMO_TOKEN` short-circuit in `get_token()`
- [ ] `test_demo_no_http` deleted from `test_dsv_auth.py`
- [ ] Upload URL: `.../upload/v1/shipments/bookingId/{type}/{booking_id}`
- [ ] Upload body: only `file` field, no `document_type`
- [ ] All tests pass (standalone: 8 detect tests + 4 base URL tests; Odoo integration tests via `odoo-bin --test-enable`)
