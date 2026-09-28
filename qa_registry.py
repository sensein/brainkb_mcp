# -*- coding: utf-8 -*-
"""Registry for canned ("QA") SPARQL queries exposed through the MCP server.

A QA module (e.g. named_entities_qa.py) calls register_category() once, with a
description the agent reads to decide whether the category is relevant, then
declares queries as QAQuery objects and calls register() on each. server.py imports the modules listed in QA_MODULES and
serves every registered query through two generic tools — brainkb_qa_list and
brainkb_qa_run — so adding a query never requires a new @mcp.tool().

Templates use {{name}} placeholders, NOT string.Template's $name: SPARQL accepts
$var as a variable, so $-substitution would collide with real queries. Every
value is validated and escaped for its declared type before substitution; a
caller can never splice raw text into the query. See guide.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# Characters the SPARQL IRIREF production forbids, plus whitespace/control chars.
_IRI_BAD = re.compile(r'[\x00-\x20<>"{}|^`\\]')

PARAM_TYPES = ("iri", "string", "int")


@dataclass(frozen=True)
class QAParam:
    """One template parameter.

    type:
      iri    -> absolute IRI, rendered as <...>
      string -> literal, rendered as "..." with escaping
      int    -> integer, rendered bare; clamped to [minimum, maximum]
    A parameter with default=None is required.
    """
    name: str
    type: str
    description: str
    default: Any = None
    minimum: Optional[int] = None
    maximum: Optional[int] = None

    def __post_init__(self) -> None:
        if self.type not in PARAM_TYPES:
            raise ValueError(f"param {self.name!r}: type must be one of {PARAM_TYPES}")

    @property
    def required(self) -> bool:
        return self.default is None

    def render(self, value: Any) -> str:
        if self.type == "iri":
            v = str(value).strip()
            if not v or _IRI_BAD.search(v) or ":" not in v:
                raise ValueError(f"{self.name}: not a valid absolute IRI")
            return f"<{v}>"
        if self.type == "string":
            v = str(value)
            v = (v.replace("\\", "\\\\").replace('"', '\\"')
                  .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))
            return f'"{v}"'
        # int
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{self.name}: must be an integer")
        if self.minimum is not None:
            n = max(self.minimum, n)
        if self.maximum is not None:
            n = min(self.maximum, n)
        return str(n)


@dataclass(frozen=True)
class QAQuery:
    """A named, parameterised SPARQL query.

    id        unique across ALL categories, lowercase snake_case
    category  groups queries for listing (one category per QA module)
    question  the natural-language question this answers — the model picks a
              query by reading this, so write it the way a user would ask
    sparql    the template, with {{param}} placeholders
    notes     guidance for the MCP agent, returned by brainkb_qa_list: when to
              pick this query over similar ones, what the result columns mean,
              where to get parameter values (e.g. an IRI from brainkb_search)
    example   a working params dict the agent can copy
    """
    id: str
    category: str
    question: str
    sparql: str
    params: Tuple[QAParam, ...] = field(default_factory=tuple)
    notes: str = ""
    example: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _ID_RE.match(self.id):
            raise ValueError(f"query id {self.id!r} must be lowercase snake_case")
        declared = {p.name for p in self.params}
        used = set(_PLACEHOLDER.findall(self.sparql))
        if used - declared:
            raise ValueError(f"{self.id}: placeholders without a param: {sorted(used - declared)}")
        if declared - used:
            raise ValueError(f"{self.id}: params never used in the template: {sorted(declared - used)}")
        if set(self.example) - declared:
            raise ValueError(f"{self.id}: example uses unknown params: {sorted(set(self.example) - declared)}")

    def render(self, args: Optional[Dict[str, Any]] = None) -> str:
        args = dict(args or {})
        known = {p.name: p for p in self.params}
        unknown = set(args) - set(known)
        if unknown:
            raise ValueError(f"unknown parameter(s): {sorted(unknown)}")
        values: Dict[str, str] = {}
        for p in self.params:
            if p.name in args and args[p.name] is not None:
                values[p.name] = p.render(args[p.name])
            elif p.required:
                raise ValueError(f"missing required parameter: {p.name}")
            else:
                values[p.name] = p.render(p.default)
        return _PLACEHOLDER.sub(lambda m: values[m.group(1)], self.sparql)

    def describe(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "question": self.question,
            "notes": self.notes,
            "example": self.example,
            "params": [
                {"name": p.name, "type": p.type, "description": p.description,
                 "required": p.required, "default": p.default}
                for p in self.params
            ],
        }


_CATEGORIES: Dict[str, str] = {}   # name -> agent-facing description
_REGISTRY: Dict[str, QAQuery] = {}


def register_category(name: str, description: str) -> str:
    """Declare a category. `description` is what the agent reads in the
    brainkb_qa_list menu to decide whether to open this category, so say what
    kinds of questions it answers — not how it is implemented."""
    if not _ID_RE.match(name):
        raise ValueError(f"category {name!r} must be lowercase snake_case")
    if not description.strip():
        raise ValueError(f"category {name!r} needs a description")
    if name in _CATEGORIES:
        raise ValueError(f"duplicate QA category: {name!r}")
    _CATEGORIES[name] = description.strip()
    return name


def register(query: QAQuery) -> QAQuery:
    if query.category not in _CATEGORIES:
        raise ValueError(f"{query.id}: category {query.category!r} is not registered; "
                         "call register_category() first")
    if query.id in _REGISTRY:
        raise ValueError(f"duplicate QA query id: {query.id!r}")
    _REGISTRY[query.id] = query
    return query


def get(query_id: str) -> Optional[QAQuery]:
    return _REGISTRY.get(query_id)


def has_category(name: str) -> bool:
    return name in _CATEGORIES


def categories() -> List[Dict[str, Any]]:
    """The menu: one entry per category, without its queries."""
    counts: Dict[str, int] = {}
    for q in _REGISTRY.values():
        counts[q.category] = counts.get(q.category, 0) + 1
    return [{"name": n, "description": d, "query_count": counts.get(n, 0)}
            for n, d in sorted(_CATEGORIES.items())]


def list_queries(category: str = "", search: str = "") -> List[Dict[str, Any]]:
    """Queries in `category` (all if empty) whose id/question/notes contain
    every word of `search` (case-insensitive; no filter if empty)."""
    words = search.lower().split()
    out = []
    for q in _REGISTRY.values():
        if category and q.category != category:
            continue
        text = f"{q.id} {q.question} {q.notes}".lower()
        if all(w in text for w in words):
            out.append(q.describe())
    return out
