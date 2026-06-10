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


def _read_source() -> str:
    src_path = pathlib.Path(__file__).parent.parent / 'controllers' / 'mf_webhook.py'
    return src_path.read_text()


def test_auth_check_before_message_type_extraction():
    source = _read_source()

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


def test_carrier_lookup_and_auth_before_body_parse():
    """Authenticate first, parse second: _find_carrier() and the X-MF-Secret
    compare_digest must both run before get_json_data() parses the body."""
    source = _read_source()

    carrier_line = _find_line(source, '_find_carrier(request.env)')
    auth_line = _find_line(source, 'compare_digest')
    body_parse_line = _find_line(source, 'request.get_json_data()')

    assert carrier_line != -1, "_find_carrier(request.env) not found in mf_webhook.py"
    assert auth_line != -1, "compare_digest not found in mf_webhook.py"
    assert body_parse_line != -1, "get_json_data() not found in mf_webhook.py"
    assert carrier_line < body_parse_line, (
        f"Carrier lookup must precede body parsing: "
        f"carrier at line {carrier_line}, get_json_data at line {body_parse_line}"
    )
    assert auth_line < body_parse_line, (
        f"Secret validation must precede body parsing: "
        f"compare_digest at line {auth_line}, get_json_data at line {body_parse_line}"
    )


def test_no_carrier_branch_fails_closed_with_403():
    """The no-carrier-configured branch must fail closed (HTTP 403), not 200."""
    source = _read_source()
    lines = source.splitlines()

    # Locate the 'if not carrier:' guard and inspect its branch body.
    guard_idx = next(
        (i for i, ln in enumerate(lines) if ln.strip() == 'if not carrier:'),
        None,
    )
    assert guard_idx is not None, "'if not carrier:' guard not found in mf_webhook.py"

    # The branch body is the indented block immediately following the guard,
    # up to (but not including) the next line at the guard's own indent or less.
    guard_indent = len(lines[guard_idx]) - len(lines[guard_idx].lstrip())
    branch = []
    for ln in lines[guard_idx + 1:]:
        if ln.strip() == '':
            continue
        indent = len(ln) - len(ln.lstrip())
        if indent <= guard_indent:
            break
        branch.append(ln)
    branch_text = '\n'.join(branch)

    assert 'status=403' in branch_text, (
        "No-carrier branch must return HTTP 403 (fail closed)"
    )
    assert "{'status': 'ok'}" not in branch_text, (
        "No-carrier branch must NOT return HTTP 200 {'status': 'ok'}"
    )
