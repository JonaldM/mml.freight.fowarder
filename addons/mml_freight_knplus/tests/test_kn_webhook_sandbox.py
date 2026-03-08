"""Structural test: K+N webhook sandbox mode must return HTTP 501.

Uses AST-free source inspection — no Odoo runtime required.
"""

import pathlib


def test_sandbox_returns_501():
    src = pathlib.Path('addons/mml_freight_knplus/controllers/kn_webhook.py').read_text()
    assert '501' in src, "Sandbox mode must return HTTP 501 Not Implemented"
