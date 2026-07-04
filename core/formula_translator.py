"""
Material-cost-estimation formula translator: desktop (Excel-style, positional)
-> web (JavaScript, named).

Desktop syntax:
    =ROUND(($2*$3*$6*$7)/1000000000,4)
    - $N  (and the $N$ variant) = the field at Trans_ID N within the group
    - ROUND(expr, n) = round to n decimals
Web syntax:
    =parseFloat((((Number(e.CutSizeW)*...)/1000000000)).toFixed(4))
    - Number(e.<WebFieldName>) = field reference by name
    - parseFloat((expr).toFixed(n)) = round to n decimals

Translation is MECHANICAL but needs a per-group POSITION->WEB-FIELD-NAME map
(supplied by the business, since the web field vocabulary isn't derivable from
the desktop data). Given that map, translation is exact, not guessed.

Usage:
    t = translate_formula("=ROUND(($2*$3)/1000,4)", {2: "CutSizeW", 3: "CutSizeL"})
    -> "=parseFloat((((Number(e.CutSizeW)*Number(e.CutSizeL))/1000)).toFixed(4))"
A formula referencing a position not in the map raises KeyError (so the caller
can flag it for review rather than emit a wrong formula).
"""

from __future__ import annotations

import re


def _replace_positions(expr: str, pos_to_name: dict) -> str:
    """Replace $N and $N$ with Number(e.<WebFieldName>)."""
    # match $N optionally followed by a trailing $ (desktop has a $2$ variant)
    def repl(m):
        n = int(m.group(1))
        if n not in pos_to_name:
            raise KeyError(n)
        return f"Number(e.{pos_to_name[n]})"
    return re.sub(r"\$(\d+)\$?", repl, expr)


def _convert_round(expr: str) -> str:
    """Convert ROUND(inner, n) -> parseFloat((inner).toFixed(n)), innermost first.
    Handles nested ROUND and parenthesised inner expressions."""
    pat = re.compile(r"ROUND\s*\(", re.IGNORECASE)
    while True:
        m = pat.search(expr)
        if not m:
            break
        start = m.start()
        open_paren = m.end() - 1          # index of the '(' after ROUND
        # find the matching close paren for this ROUND(
        depth = 0
        i = open_paren
        while i < len(expr):
            if expr[i] == "(":
                depth += 1
            elif expr[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        inner = expr[open_paren + 1:i]    # arguments between the outer parens
        # split inner into (value_expr, decimals) on the LAST top-level comma
        depth = 0
        comma = -1
        for j, ch in enumerate(inner):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                comma = j
        if comma == -1:
            value, dec = inner, "2"
        else:
            value, dec = inner[:comma], inner[comma + 1:].strip()
        replacement = f"parseFloat(({value}).toFixed({dec}))"
        expr = expr[:start] + replacement + expr[i + 1:]
    return expr


def translate_formula(desktop_formula: str, pos_to_name: dict) -> str:
    """Translate one desktop formula to web JS. Raises KeyError(position) if a
    referenced $N has no web field name in pos_to_name."""
    f = (desktop_formula or "").strip()
    if not f:
        return ""
    if f.startswith("="):
        f = f[1:]
    f = _replace_positions(f, pos_to_name)
    f = _convert_round(f)
    # web formulas are stored with a leading '='
    return "=" + f
