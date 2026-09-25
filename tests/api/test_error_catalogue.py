"""No route can refuse a person in English without somebody noticing.

The frontend turns API refusals into Chinese by matching fixed phrases
(``translateApiError`` in ``shared.js``), and ``error_messages.test.js``
checks that every phrase *in its own list* translates. Nothing checked
that the list still covered the API, so a raise site added later simply
reached the screen in English — six of them had, by 2026-09-25, including
one whose pattern had a typo and could never match.

This reads the raise sites out of the AST rather than by grepping: the
ones built across several lines or out of an f-string are exactly the
ones a regex over the source misses, and exactly the ones that were
missing from the list.
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "src" / "volleyflow" / "api"
CATALOGUE = ROOT / "tests" / "frontend" / "error_messages.test.js"

# Messages no person can ever read, so no person needs them in Chinese.
EXEMPT = {
    # LINE's webhook signature check. This answers LINE's servers, never a
    # browser; a human being never sees it.
    "Invalid LINE signature",
}


def _render(node: ast.AST) -> str | None:
    """The literal a person would see, with ``{}`` where a value goes.

    None when it can't be known from the source — ``str(exc)`` and the
    like, whose wording belongs to whatever raised it.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            elif isinstance(value, ast.FormattedValue):
                parts.append("{}")
            else:
                return None
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _render(node.left), _render(node.right)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.IfExp):
        # One raise, two wordings — both have to be covered.
        body, orelse = _render(node.body), _render(node.orelse)
        if body is None or orelse is None:
            return None
        return f"{body}\n{orelse}"
    return None


def _raise_sites() -> set[str]:
    """Every distinct message template the API can raise."""
    found: set[str] = set()
    for path in sorted(API.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", "") or getattr(node.func, "attr", "")
            if name != "HTTPException":
                continue
            detail = node.args[1] if len(node.args) >= 2 else None
            for keyword in node.keywords:
                if keyword.arg == "detail":
                    detail = keyword.value
            if detail is None:
                continue
            rendered = _render(detail)
            if rendered is None:
                continue
            found.update(rendered.split("\n"))
    return found


def _catalogued() -> list[str]:
    """The example messages error_messages.test.js checks."""
    source = CATALOGUE.read_text(encoding="utf-8")
    start = source.index("const KNOWN_MESSAGES = [")
    end = source.index("\n];", start)
    return re.findall(r'"((?:[^"\\]|\\.)*)"', source[start:end])


def test_every_api_refusal_has_an_example_in_the_frontend_catalogue() -> None:
    templates = _raise_sites() - EXEMPT
    catalogued = _catalogued()

    missing = []
    for template in sorted(templates):
        # "No season with id {}" has to match the catalogue's concrete
        # "No season with id 42", so the template becomes a pattern.
        pattern = (
            "^" + "(?s:.*?)".join(re.escape(p) for p in template.split("{}")) + "$"
        )
        if not any(re.match(pattern, example) for example in catalogued):
            missing.append(template)

    assert missing == [], (
        "these API messages have no example in error_messages.test.js, so "
        "nothing checks they reach the reader in Chinese"
    )
