"""Restricted expression grammar (SEM-FR-006).

Expressions are parsed to an AST at save time. Anything outside the grammar —
subqueries, UDFs, window functions, comments, semicolons, double quotes,
unterminated strings — raises ExpressionNotAllowed (422). The AST is the ONLY
thing the compiler ever renders; raw expression text never reaches SQL.

Grammar (BRD 06 §3):
    expr      := term (('+'|'-'|'*'|'/'|'%') term)*
    term      := column | literal | func | case | '(' expr ')'
    func      := ('coalesce'|'nullif'|'cast'|'date_trunc'|'extract'|'lower'|
                  'upper'|'trim'|'concat'|'abs'|'round') '(' args ')'
    case      := 'CASE' ('WHEN' cond 'THEN' expr)+ ('ELSE' expr)? 'END'
    cond      := expr ('='|'!='|'>'|'>='|'<'|'<='|'IS NULL'|'IS NOT NULL') expr?
                 | cond ('AND'|'OR') cond | 'NOT' cond
    column    := ^[a-z][a-z0-9_]{0,62}$
    literal   := number | quoted string (escaped) | TRUE | FALSE | NULL
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.errors import ExpressionNotAllowed

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

FUNC_WHITELIST = frozenset(
    ["coalesce", "nullif", "cast", "date_trunc", "extract",
     "lower", "upper", "trim", "concat", "abs", "round"]
)
KEYWORDS = frozenset(
    ["case", "when", "then", "else", "end", "and", "or", "not",
     "is", "null", "true", "false", "as", "from"]
)
CAST_TYPES = frozenset(
    ["integer", "bigint", "double", "decimal", "varchar", "date", "timestamp", "boolean"]
)
EXTRACT_PARTS = frozenset(
    ["year", "quarter", "month", "week", "day", "hour", "minute", "second", "dow", "doy"]
)
TIME_GRAINS = ("hour", "day", "week", "month", "quarter", "year")


@dataclass(slots=True)
class Token:
    kind: str  # ident | number | string | op | kw
    value: str


_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<number>\d+(?:\.\d+)?)
  | (?P<string>'(?:[^']|'')*')
  | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>>=|<=|!=|[><=+\-*/%(),])
    """,
    re.VERBOSE,
)


def tokenize(text: str) -> list[Token]:
    if not isinstance(text, str) or not text.strip():
        raise ExpressionNotAllowed("empty expression")
    if "--" in text or "/*" in text or ";" in text or '"' in text:
        raise ExpressionNotAllowed("comments, semicolons and double quotes are not allowed")
    tokens: list[Token] = []
    pos = 0
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if m is None:
            raise ExpressionNotAllowed(f"illegal character at position {pos}: {text[pos]!r}")
        pos = m.end()
        if m.lastgroup == "ws":
            continue
        value = m.group()
        if m.lastgroup == "ident":
            lowered = value.lower()
            if lowered in KEYWORDS:
                tokens.append(Token("kw", lowered))
            elif lowered in FUNC_WHITELIST or lowered in CAST_TYPES \
                    or lowered in EXTRACT_PARTS:
                # function/type/part names are case-insensitive
                tokens.append(Token("ident", lowered))
            else:
                # column identifiers keep their original casing so the
                # ^[a-z][a-z0-9_]{0,62}$ gate applies to what was written
                tokens.append(Token("ident", value))
        elif m.lastgroup == "string":
            tokens.append(Token("string", value[1:-1].replace("''", "'")))
        else:
            tokens.append(Token(m.lastgroup or "op", value))
    return tokens


class _Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.i = 0

    # -- token helpers ---------------------------------------------------
    def peek(self) -> Token | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def next(self) -> Token:
        tok = self.peek()
        if tok is None:
            raise ExpressionNotAllowed("unexpected end of expression")
        self.i += 1
        return tok

    def accept(self, kind: str, value: str | None = None) -> Token | None:
        tok = self.peek()
        if tok and tok.kind == kind and (value is None or tok.value == value):
            self.i += 1
            return tok
        return None

    def expect(self, kind: str, value: str | None = None) -> Token:
        tok = self.accept(kind, value)
        if tok is None:
            found = self.peek()
            raise ExpressionNotAllowed(
                f"expected {value or kind}, found {(found.value if found else 'end')!r}"
            )
        return tok

    # -- expr ------------------------------------------------------------
    def parse_expr(self) -> dict:
        node = self.parse_mul()
        while True:
            tok = self.peek()
            if tok and tok.kind == "op" and tok.value in ("+", "-"):
                self.next()
                node = {"t": "bin", "op": tok.value, "l": node, "r": self.parse_mul()}
            else:
                return node

    def parse_mul(self) -> dict:
        node = self.parse_term()
        while True:
            tok = self.peek()
            if tok and tok.kind == "op" and tok.value in ("*", "/", "%"):
                self.next()
                node = {"t": "bin", "op": tok.value, "l": node, "r": self.parse_term()}
            else:
                return node

    def parse_term(self) -> dict:
        tok = self.peek()
        if tok is None:
            raise ExpressionNotAllowed("unexpected end of expression")
        if tok.kind == "op" and tok.value == "-":  # unary minus on a numeric literal
            self.next()
            num = self.expect("number")
            return {"t": "lit", "kind": "num", "v": "-" + num.value}
        if tok.kind == "op" and tok.value == "(":
            self.next()
            inner = self.parse_expr()
            self.expect("op", ")")
            return inner
        if tok.kind == "number":
            self.next()
            return {"t": "lit", "kind": "num", "v": tok.value}
        if tok.kind == "string":
            self.next()
            return {"t": "lit", "kind": "str", "v": tok.value}
        if tok.kind == "kw":
            if tok.value in ("true", "false"):
                self.next()
                return {"t": "lit", "kind": "bool", "v": tok.value}
            if tok.value == "null":
                self.next()
                return {"t": "lit", "kind": "null", "v": None}
            if tok.value == "case":
                return self.parse_case()
            raise ExpressionNotAllowed(f"keyword {tok.value!r} not allowed here")
        if tok.kind == "ident":
            self.next()
            nxt = self.peek()
            if nxt and nxt.kind == "op" and nxt.value == "(":
                return self.parse_func(tok.value)
            if not NAME_RE.match(tok.value):
                raise ExpressionNotAllowed(f"illegal column identifier {tok.value!r}")
            return {"t": "col", "name": tok.value}
        raise ExpressionNotAllowed(f"unexpected token {tok.value!r}")

    def parse_func(self, name: str) -> dict:
        if name not in FUNC_WHITELIST:
            raise ExpressionNotAllowed(
                f"function {name!r} not allowed; allowed: {', '.join(sorted(FUNC_WHITELIST))}"
            )
        self.expect("op", "(")
        if name == "cast":
            inner = self.parse_expr()
            self.expect("kw", "as")
            type_tok = self.expect("ident")
            if type_tok.value not in CAST_TYPES:
                raise ExpressionNotAllowed(f"cast type {type_tok.value!r} not allowed")
            self.expect("op", ")")
            return {"t": "cast", "expr": inner, "type": type_tok.value}
        if name == "extract":
            part_tok = self.expect("ident")
            if part_tok.value not in EXTRACT_PARTS:
                raise ExpressionNotAllowed(f"extract part {part_tok.value!r} not allowed")
            self.expect("kw", "from")
            inner = self.parse_expr()
            self.expect("op", ")")
            return {"t": "extract", "part": part_tok.value, "expr": inner}
        if name == "date_trunc":
            grain_tok = self.expect("string")
            if grain_tok.value not in TIME_GRAINS:
                raise ExpressionNotAllowed(f"date_trunc grain {grain_tok.value!r} not allowed")
            self.expect("op", ",")
            inner = self.parse_expr()
            self.expect("op", ")")
            return {"t": "date_trunc", "grain": grain_tok.value, "expr": inner}
        args = []
        if not self.accept("op", ")"):
            args.append(self.parse_expr())
            while self.accept("op", ","):
                args.append(self.parse_expr())
            self.expect("op", ")")
        return {"t": "func", "name": name, "args": args}

    def parse_case(self) -> dict:
        self.expect("kw", "case")
        whens = []
        while self.accept("kw", "when"):
            cond = self.parse_cond()
            self.expect("kw", "then")
            whens.append({"when": cond, "then": self.parse_expr()})
        if not whens:
            raise ExpressionNotAllowed("CASE requires at least one WHEN")
        else_expr = self.parse_expr() if self.accept("kw", "else") else None
        self.expect("kw", "end")
        return {"t": "case", "whens": whens, "else": else_expr}

    # -- cond ------------------------------------------------------------
    def parse_cond(self) -> dict:
        node = self.parse_and()
        while self.accept("kw", "or"):
            node = {"t": "logic", "op": "OR", "l": node, "r": self.parse_and()}
        return node

    def parse_and(self) -> dict:
        node = self.parse_not()
        while self.accept("kw", "and"):
            node = {"t": "logic", "op": "AND", "l": node, "r": self.parse_not()}
        return node

    def parse_not(self) -> dict:
        if self.accept("kw", "not"):
            return {"t": "not", "c": self.parse_not()}
        return self.parse_comparison()

    def parse_comparison(self) -> dict:
        # '(' may open a parenthesized cond OR a parenthesized expr; backtrack.
        tok = self.peek()
        if tok and tok.kind == "op" and tok.value == "(":
            saved = self.i
            try:
                self.next()
                inner = self.parse_cond()
                self.expect("op", ")")
                return inner
            except ExpressionNotAllowed:
                self.i = saved
        left = self.parse_expr()
        if self.accept("kw", "is"):
            negated = self.accept("kw", "not") is not None
            self.expect("kw", "null")
            return {"t": "cond", "op": "IS NOT NULL" if negated else "IS NULL",
                    "l": left, "r": None}
        tok = self.peek()
        if tok and tok.kind == "op" and tok.value in ("=", "!=", ">", ">=", "<", "<="):
            self.next()
            return {"t": "cond", "op": tok.value, "l": left, "r": self.parse_expr()}
        raise ExpressionNotAllowed("expected a comparison operator")


def parse_expression(text: str) -> dict:
    """Parse an expression (measure/dimension expr) to its AST or raise 422."""
    parser = _Parser(tokenize(text))
    node = parser.parse_expr()
    if parser.peek() is not None:
        raise ExpressionNotAllowed(f"trailing input from {parser.peek().value!r}")
    return node


def parse_condition(text: str) -> dict:
    """Parse a boolean condition (measure-level filter) to its AST or raise 422."""
    parser = _Parser(tokenize(text))
    node = parser.parse_cond()
    if parser.peek() is not None:
        raise ExpressionNotAllowed(f"trailing input from {parser.peek().value!r}")
    return node


def collect_columns(node: dict | None) -> set[str]:
    """All column identifiers referenced by an AST (for allowlist validation)."""
    if node is None:
        return set()
    out: set[str] = set()
    stack = [node]
    while stack:
        n = stack.pop()
        if not isinstance(n, dict):
            continue
        if n.get("t") == "col":
            out.add(n["name"])
            continue
        for key in ("l", "r", "expr", "c", "else"):
            child = n.get(key)
            if isinstance(child, dict):
                stack.append(child)
        for arg in n.get("args", []) or []:
            stack.append(arg)
        for w in n.get("whens", []) or []:
            stack.append(w["when"])
            stack.append(w["then"])
    return out
