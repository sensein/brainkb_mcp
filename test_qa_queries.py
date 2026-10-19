#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline smoke test for canned QA queries — no server, no login, no network.

Loads one or more Turtle exports into named graphs of an in-memory rdflib
Dataset, then renders and executes every registered query of a category with
its own `example` parameters, reporting row counts and timings. Catches the
mistakes a render-only check cannot: a predicate that does not exist in the
data, a join that silently returns nothing, a pattern that explodes into a
cross product.

    pip install rdflib

    # resource + named-entity export in two separate graphs (the usual layout)
    python test_qa_queries.py \
        --graph https://brainkb.org/graph/test-resource/=basal_ganglia_resources.ttl \
        --graph https://brainkb.org/graph/named-entity-test/=biorxiv_2026.02.03.703645.ttl \
        --category resource_entity_bridge \
        --param resource_graph=https://brainkb.org/graph/test-resource/ \
        --param entity_graph=https://brainkb.org/graph/named-entity-test/

Each query runs under a wall-clock timeout (--timeout, default 60s). A timeout
is a finding, not a test harness problem: rdflib is slower than the production
store, but a query that does not finish over a 20k-triple sample is usually
building a cross product that a real ingest will not survive either.

Exit status is non-zero if any query fails to render or raises.
"""

from __future__ import annotations

import argparse
import importlib
import os
import signal
import sys
import time

# Importable from anywhere: the QA modules sit next to this script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class _Timeout(Exception):
    pass


def _alarm(signum, frame):  # noqa: ARG001
    raise _Timeout()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--graph", action="append", default=[], metavar="IRI=FILE.ttl",
                    help="load FILE.ttl into named graph IRI (repeatable)")
    ap.add_argument("--module", action="append", default=[],
                    help="QA module to import (default: every module in server.QA_MODULES)")
    ap.add_argument("--category", default="",
                    help="only test queries of this category (default: all)")
    ap.add_argument("--param", action="append", default=[], metavar="NAME=VALUE",
                    help="override an example parameter for every query that declares it "
                         "(repeatable) — typically the graph IRIs")
    ap.add_argument("--timeout", type=int, default=60,
                    help="per-query wall-clock limit in seconds (default 60)")
    ap.add_argument("--show", type=int, default=0,
                    help="print the first N result rows of each query")
    args = ap.parse_args()

    try:
        from rdflib import Dataset, URIRef
    except ImportError:
        print("rdflib is required: pip install rdflib", file=sys.stderr)
        return 2

    import qa_registry

    modules = args.module
    if not modules:
        # Default to whatever the server serves, without importing server.py
        # itself (it needs the MCP dependencies).
        modules = ["named_entities_qa", "resources_qa", "resource_entity_bridge_qa"]
    for name in modules:
        importlib.import_module(name)

    dataset = Dataset()
    for spec in args.graph:
        iri, _, path = spec.partition("=")
        if not path:
            print(f"--graph needs IRI=FILE.ttl, got {spec!r}", file=sys.stderr)
            return 2
        graph = dataset.graph(URIRef(iri))
        graph.parse(path, format="turtle")
        print(f"loaded {len(graph):>6} triples into <{iri}>")

    overrides = {}
    for spec in args.param:
        name, _, value = spec.partition("=")
        overrides[name] = value

    queries = qa_registry.list_queries(category=args.category)
    print(f"\ntesting {len(queries)} quer{'y' if len(queries) == 1 else 'ies'}"
          f"{' in ' + args.category if args.category else ''}\n")

    failures, empties = [], []
    signal.signal(signal.SIGALRM, _alarm)

    for described in sorted(queries, key=lambda q: q["id"]):
        query = qa_registry.get(described["id"])
        params = dict(described["example"])
        declared = {p["name"] for p in described["params"]}
        required = {p["name"] for p in described["params"] if p["required"]}
        for name, value in overrides.items():
            if name in declared:
                params[name] = value
        missing = required - set(params)
        if missing:
            print(f"  SKIP   {query.id:34s} no example value for {sorted(missing)}")
            continue

        try:
            sparql = query.render(params)
        except ValueError as exc:
            print(f"  RENDER {query.id:34s} {exc}")
            failures.append(query.id)
            continue

        started = time.monotonic()
        signal.alarm(args.timeout)
        try:
            rows = list(dataset.query(sparql))
        except _Timeout:
            print(f"  SLOW   {query.id:34s} no result in {args.timeout}s")
            failures.append(query.id)
            continue
        except Exception as exc:  # noqa: BLE001 — report whatever the engine raised
            print(f"  ERROR  {query.id:34s} {type(exc).__name__}: {exc}")
            failures.append(query.id)
            continue
        finally:
            signal.alarm(0)

        elapsed = time.monotonic() - started
        flag = "ok    " if rows else "EMPTY "
        if not rows:
            empties.append(query.id)
        print(f"  {flag} {query.id:34s} {len(rows):>6} rows  {elapsed:6.2f}s")
        for row in rows[:args.show]:
            print("           ", [None if v is None else str(v)[:60] for v in row])

    print()
    if empties:
        print(f"{len(empties)} query/queries returned no rows (fine if the sample "
              f"lacks those records): {', '.join(empties)}")
    if failures:
        print(f"FAILED: {', '.join(failures)}")
        return 1
    print("all queries rendered and executed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
