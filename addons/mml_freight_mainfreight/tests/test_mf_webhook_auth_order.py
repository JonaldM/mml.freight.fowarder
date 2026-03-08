"""Structural test: MF webhook must validate auth before extracting message metadata.

Parses the source file to find the line numbers of the auth check and the
message_type extraction, then asserts auth comes first.
"""

import ast
import pathlib


def _find_line(source: str, needle: str) -> int:
    """Return the 1-based line number of the first line containing needle."""
    for lineno, line in enumerate(source.splitlines(), start=1):
        if needle in line:
            return lineno
    return -1


def test_auth_check_before_message_type_extraction():
    src_path = pathlib.Path(
        'addons/mml_freight_mainfreight/controllers/mf_webhook.py'
    )
    source = src_path.read_text()

    # Auth check: compare_digest is the definitive secret comparison
    auth_line = _find_line(source, 'compare_digest')
    # message_type extraction
    message_type_line = _find_line(source, "body.get('messageType')")

    assert auth_line != -1, "Auth check (compare_digest) not found in mf_webhook.py"
    assert message_type_line != -1, "message_type extraction not found in mf_webhook.py"
    assert auth_line < message_type_line, (
        f"Auth check must appear before message_type extraction: "
        f"auth at line {auth_line}, message_type at line {message_type_line}"
    )
