# Adding canned QA queries

The MCP server can answer common questions with prepared SPARQL queries instead
of making the model write SPARQL itself. The queries live in plain Python modules
and are served by two generic tools:

| Tool | What it does |
|------|--------------|
| `brainkb_qa_list(category?, search?)` | With no arguments, returns the category menu. With `category` or `search`, returns matching queries with their parameters |
| `brainkb_qa_run(query_id, params?)` | Checks and escapes the parameters, fills them into the template, and runs it |

When you add a query, you don't add a tool. The model finds it through
`brainkb_qa_list`.

## How it fits together

```
qa_registry.py        QAParam, QAQuery, register_category(), register(): the shared machinery
named_entities_qa.py  register_category("named_entities", ...), then register() per query
resources_qa.py       register_category("resources", ...): resource catalogues (tools, datasets, models)
<your>_qa.py          any other category, same pattern
server.py             QA_MODULES = (...) imports each module; defines the 2 tools
```

Queries run through the same endpoint as `brainkb_sparql`
(`/api/query/sparql/`), so they need the same role and are subject to the same
read rate limit.

## How the agent finds a query

As the number of categories grows, the agent never reads every query at once.
Discovery takes two steps:

1. **`brainkb_qa_list()`** returns only the menu: one line per category.

   ```json
   {"categories": [
     {"name": "cell_types", "description": "Cell types and where they are found: ...", "query_count": 30},
     {"name": "named_entities", "description": "Named entities in the knowledge graph: look up an entity by name ...", "query_count": 2}
   ],
    "next": "Call brainkb_qa_list(category=...) for the queries in one category."}
   ```

   The agent picks the category whose **description** matches the user's question.

2. **`brainkb_qa_list(category="cell_types")`** returns that category's queries,
   each with its `question`, `notes`, `params` and `example`. The agent picks one
   and calls `brainkb_qa_run`.

When no description fits, `brainkb_qa_list(search="marker gene")` searches every
query's id, `question` and `notes` for all the given words. `search` also works
together with `category` to narrow within one category.

The tool's own docstring spells out these steps, and the agent reads it from the
tool list. So the category description is the only thing that decides whether a
category gets opened at all. If a category is missing from the menu, or its
description is vague, its queries effectively can't be found.

### Writing the category description

Each module calls `register_category()` once, before its queries. A query whose
category isn't registered raises an error at import time, and so does an empty
description.

```python
CATEGORY = register_category(
    "named_entities",
    "Named entities in the knowledge graph: look up an entity by name to get its "
    "IRI and types, and list the entities of a given type. Start here when the "
    "user names something (a region, cell type, gene, ...) and you need its IRI "
    "for other queries.",
)
```

- Say **what questions it answers** in the user's terms, not how the SPARQL works.
- Say what sets it apart from neighbouring categories ("start here when…").
- Update it when you add queries that widen the category's scope. A description
  that lists three kinds of question while the category answers ten hides the
  other seven.

## Add a query to an existing category

Open the category module (for example `named_entities_qa.py`) and add a new
`register(...)` block. **Each query is self-contained.** It declares its own
`PREFIX`es and its own parameters, including `limit`, and shares nothing with the
other queries in the file. This lets you read, copy, move or delete one query
without checking what else depends on it.

```python
register(QAQuery(
    id="ne_mentions_of_entity",          # unique across ALL categories, snake_case
    category=CATEGORY,
    question="Where is a given entity mentioned, and in which documents?",
    notes=(
        "Use when the user asks where or in what source an entity appears. "
        "entity must be an IRI; if the user gives a name, look it up first with "
        "brainkb_search. doc_filter narrows by a substring of the document IRI "
        "(leave empty for all). Returns ?mention and ?document IRIs."
    ),
    example={"entity": "http://purl.obolibrary.org/obo/CL_0000540", "limit": 20},
    sparql="""
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT ?mention ?document WHERE {
  ?mention ?refersTo {{entity}} ;
           prov:wasDerivedFrom ?document .
  FILTER(CONTAINS(LCASE(STR(?document)), LCASE({{doc_filter}})))
}
LIMIT {{limit}}
""",
    params=(
        QAParam("entity", "iri", "IRI of the entity."),
        QAParam("doc_filter", "string", "Substring to match in the document IRI.", default=""),
        QAParam("limit", "int", "Maximum number of rows to return.",
                default=100, minimum=1, maximum=1000),
    ),
))
```

That's all. Restart the server and the query appears in `brainkb_qa_list`.

### What the MCP agent sees

The agent never reads your Python. It picks a query and fills in its parameters
using only what `brainkb_qa_list` returns:

```json
{
  "id": "ne_mentions_of_entity",
  "category": "named_entities",
  "question": "Where is a given entity mentioned, and in which documents?",
  "notes": "Use when the user asks where or in what source an entity appears. ...",
  "example": {"entity": "http://purl.obolibrary.org/obo/CL_0000540", "limit": 20},
  "params": [
    {"name": "entity", "type": "iri", "description": "IRI of the entity.", "required": true, "default": null},
    ...
  ]
}
```

So put anything the agent needs into these fields, not into `#` comments:

| Field | What to write |
|-------|---------------|
| `question` | The question as a user would ask it, specific enough that two queries can't be confused. "Which cell types are located in a given brain region?" is better than "region query". |
| `notes` | When to choose this query over similar ones, where parameter values come from (for example, "get the IRI from `brainkb_search` first"), what each result column means, and what an empty result means. |
| `example` | A params dict that actually returns rows. The agent copies its shape, so real IRIs work better than placeholders. Unknown keys raise an error at import time. |
| `QAParam` `description` | One line per parameter: what it is, with an example value where helpful. |

Use `#` comments only for notes aimed at the next developer.

### Queries that need input from the user

A parameter without a `default` is required. `brainkb_qa_run` refuses to run
without it and returns `400 missing required parameter: <name>`. When the value
has to come from the user, say so in `notes`, so the agent asks for it instead of
inventing one. `ne_find_entity` in `named_entities_qa.py` is the reference
example (SPARQL shortened here):

```python
register(QAQuery(
    id="ne_find_entity",
    category=CATEGORY,
    question="Which entities match a given name, and what are their normalized keys, IRIs and types?",
    notes=(
        "REQUIRES INPUT: `name`, the text the user typed (e.g. 'hippocampus', "
        "'PV', 'parvalbumin'). Ask the user if no name was given; do not guess. "
        "Run this before any query that takes an entity key parameter ... "
        # ... what the columns mean, what an empty result means
    ),
    example={"name": "hippocampus", "limit": 100},
    sparql="""
...
SELECT ?e ?key ... WHERE {
  VALUES ?requestedName { {{name}} }
  ...
  FILTER(CONTAINS(LCASE(STR(?matched)), LCASE(?requestedName)))
}
GROUP BY ?e ?key
LIMIT {{limit}}
""",
    params=(
        QAParam("name", "string", "Required text to look for, e.g. 'hippocampus' or 'PV'."),  # required
        QAParam("limit", "int", "Maximum number of matching entities to return.",
                default=100, minimum=1),                                                     # optional
    ),
))
```

Whatever the user types is escaped before it's inserted, including quotes. For
example, `CA1 "pyramidal"` becomes `"CA1 \"pyramidal\""` in the query, so user
input can't break out of the literal.

### Optional filters

Most queries take optional filters (an entity key, a DOI, an ontology acronym).
Declare them as `string` parameters with `default=""`, bind them with `VALUES`,
and test them in a `FILTER` **outside** the `GRAPH` block, so that an empty
string means "no filter":

```sparql
WHERE {
  VALUES (?requestedCell ?requestedRegion) { ({{cell_key}} {{region_key}}) }

  GRAPH <https://www.brainkb.org/named-entity/> {
    ...
  }

  FILTER(?requestedCell = "" || STR(?cell) = ?requestedCell)
  FILTER(?requestedRegion = "" || STR(?region) = ?requestedRegion)
}
```

Keep the `FILTER` outside the `GRAPH` block. A filter inside it can't see the
outer `VALUES` binding, so it silently removes every row. Don't use
`VALUES ?x { UNDEF }` for a filter the agent should be able to set, because the
agent can only fill in `{{placeholders}}`. For an optional IRI filter, use a
`string` parameter and compare with `STR(?term) = ?requestedTerm`, because an
`iri` parameter can't be left empty.

Every named-entity query also takes an optional `graph` parameter (`iri`,
default `https://www.brainkb.org/named-entity/`) and reads `GRAPH {{graph}}`, so the
same query works on NER data ingested into another space. Give a new query the
same parameter. `ne_named_entity_graphs` lists the graphs that hold named entities.

A typical agent flow chains queries: resolve the user's wording with a lookup
helper, then pass the exact value it returned:

```
brainkb_qa_run("ne_find_entity", {"name": "PV"})
  → copy ?key from the intended row, e.g. "pv_interneuron"
brainkb_qa_run("ne_cell_type_region_assertions", {"cell_key": "pv_interneuron"})

brainkb_qa_run("ne_available_entity_types", {})
  → copy ?type, e.g. https://brainkb.org/ner/Drug
brainkb_qa_run("ne_entities_of_type", {"entity_type": "https://brainkb.org/ner/Drug"})
```

The lookup helpers are `ne_available_entity_types` (type IRIs), `ne_find_entity`
(entity keys and IRIs), `ne_list_sources` (DOIs) and `ne_ontology_coverage`
(ontology acronyms).

### Placeholders and parameter types

Placeholders are written `{{name}}`. Don't use `$name`, because SPARQL itself
uses `$var` for variables. Each placeholder must match exactly one `QAParam`.
A placeholder with no parameter, or a parameter that is never used, raises an
error at import time, so the mistake shows up when the server starts rather
than when the query first runs.

| `type` | Caller passes | Inserted as | Checks |
|--------|---------------|-------------|--------|
| `iri` | `http://purl.obolibrary.org/obo/UBERON_0000955` | `<http://…/UBERON_0000955>` | Must be an absolute IRI with no `<>"{}\|^`\` or whitespace |
| `string` | `hippocampus` | `"hippocampus"` | Quotes, backslashes and newlines are escaped |
| `int` | `50` | `50` | Must be an integer; clamped to `minimum`/`maximum` |

A parameter with `default=None` (the default) is required. Any other default
makes it optional.

Because every value is escaped for its type, a caller can't inject SPARQL. Never
build query text yourself with f-strings or `%`/`.format()`. If you need
something the three types don't cover, add a new type to `QAParam.render` in
`qa_registry.py` (and to `PARAM_TYPES`), so its escaping lives in one place.

### Keep results bounded

Every list-style query should end in `LIMIT {{limit}}` with its own `limit`
parameter (default 100, max 1000 is a good starting point). Unbounded queries against a large graph can
time out and return more data than the model can use.

## Add a new category

1. Create a module, for example `cell_types_qa.py`:

   ```python
   """Canned SPARQL queries about cell types."""

   from qa_registry import QAParam, QAQuery, register, register_category

   CATEGORY = register_category(
       "cell_types",
       "Cell types and where they are found: which cell types are in a brain "
       "region, their marker genes, and their morphological or electrophysiological "
       "classes. Use for questions about what cells exist or where; resolve names "
       "to IRIs with the named_entities category first.",
   )


   register(QAQuery(
       id="ct_cell_types_in_region",
       category=CATEGORY,
       question="Which cell types are located in a given brain region?",
       notes=(
           "Use when the user names a brain region and asks what cell types are "
           "in it. region must be an UBERON IRI; resolve a region name with "
           "brainkb_search first. Returns ?cellType IRIs and their ?label."
       ),
       example={"region": "http://purl.obolibrary.org/obo/UBERON_0001954", "limit": 25},
       sparql="""
   PREFIX obo:  <http://purl.obolibrary.org/obo/>
   PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

   SELECT DISTINCT ?cellType ?label WHERE {
     ?cellType obo:RO_0001025 {{region}} .
     OPTIONAL { ?cellType rdfs:label ?label }
   }
   LIMIT {{limit}}
   """,
       params=(
           QAParam("region", "iri", "IRI of the brain region (e.g. an UBERON term)."),
           QAParam("limit", "int", "Maximum number of rows to return.",
                   default=100, minimum=1, maximum=1000),
       ),
   ))
   ```

   Prefix query ids with a short category tag (`ne_`, `ct_`, …). Ids must be
   unique across all modules, and a duplicate raises an error at import time.

2. Add the module to `QA_MODULES` in `server.py`:

   ```python
   QA_MODULES = ("named_entities_qa", "cell_types_qa")
   ```

3. Add the file to the `COPY` line in the `Dockerfile`, or it won't be in the image:

   ```dockerfile
   COPY server.py server.json qa_registry.py named_entities_qa.py cell_types_qa.py ./
   ```

## Cross-graph queries (joining two kinds of export)

structsense writes resource records and named-entity records into different
named graphs, often in different spaces, so a question that spans both has to
read two graphs in one query. `resource_entity_bridge_qa.py` is the worked
example; copy its shape rather than inventing another.

1. **Two graph parameters, not one.** Name them for the data, not the position —
   `resource_graph` and `entity_graph` — each with the usual default and a
   `notes` line telling the agent where to get the real IRI. Say in the category
   description that every query takes both, and provide a discovery query
   (`rb_paired_graphs`) that finds which pair actually shares papers. Passing
   the same IRI twice must still work, for the case where one graph holds both.

2. **Join on something both exports mint identically.** The publication node is
   the reliable key: both sides derive the same IRI from the DOI and carry
   `ner:doi`. The document version (`ner:checksum`), ontology term IRIs and
   quoted evidence text are the other real bridges. Matching on a label or a
   resource name is a heuristic — say so in `notes`, because the agent cannot
   see the query.

3. **Count each side in its own sub-select.** Two independent patterns in one
   `WHERE` multiply each other's rows: 67 resources against 144 entities is
   9 648 intermediate solutions for one paper, and that is the small case.
   `COUNT(DISTINCT ?x)` still reports the right number, but the query may not
   finish. When a query reports counts from both graphs, give each its own
   `{ SELECT ?paper (COUNT(DISTINCT …) AS …) … GROUP BY ?paper }` block and join
   those, with `COALESCE(?count, 0)` in the projection for the optional ones.

Two portability traps, both found by running these queries rather than reading
them:

- **A `FILTER` only sees variables bound in its own group.** A `VALUES` in the
  outer group and the `FILTER` that uses it inside a `GRAPH { … }` block is not
  the same query: inside the block the variable is unbound, the comparison is an
  error, and a spec-strict engine drops every row. Keep the `FILTER` in the same
  group as its `VALUES`, after the `GRAPH` blocks. Lenient stores answer the
  other form anyway, which is how it survives until someone runs it elsewhere.
- **Avoid `EXISTS` that contains a `GRAPH` clause.** Express the same test as an
  `OPTIONAL` join on the shared variable and count what bound
  (`COUNT(DISTINCT ?coveringScope)`, 0 meaning "no"). It reads as well, it is
  testable offline, and rdflib — the only engine you can check against without
  the stack — cannot evaluate the `EXISTS` form at all.

## Test a query before committing

Rendering needs no server or login. It shows the exact SPARQL that will be sent:

```bash
python - <<'EOF'
import qa_registry, named_entities_qa
q = qa_registry.get("ne_entities_of_type")
print(q.render(q.example))   # renders the query exactly as the agent's example would
EOF
```

Then check it end to end from an MCP client:

```
brainkb_qa_list(category="named_entities")
brainkb_qa_run(query_id="ne_entities_of_type",
               params={"entity_type": "https://brainkb.org/ner/Drug"})
```

Bad input comes back as a `400` error dict (for example
`missing required parameter: entity_type`). An unknown id comes back as a `404`.

Rendering only proves the template is well formed. To check that a query also
matches real data — the predicate exists, the join binds, the pattern does not
explode — run it against a Turtle export offline with `test_qa_queries.py`
(needs `rdflib`, nothing else):

```bash
python test_qa_queries.py \
    --graph https://brainkb.org/graph/test-resource/=resources.ttl \
    --graph https://brainkb.org/graph/named-entity-test/=named_entities.ttl \
    --category resource_entity_bridge \
    --param resource_graph=https://brainkb.org/graph/test-resource/ \
    --param entity_graph=https://brainkb.org/graph/named-entity-test/
```

It loads each file into the named graph you give it, then renders and executes
every query of the category with its own `example` parameters, printing row
counts and timings:

```
  ok     rb_scope_entity_overlap                23 rows    7.34s
  EMPTY  rb_out_of_scope_conflicts               0 rows    0.27s
  SLOW   rb_paper_bridge_summary            no result in 45s
```

`EMPTY` is only acceptable when you can name the record the sample lacks —
otherwise it is a wrong predicate or a broken join. `SLOW` means a cross product:
rdflib is slower than the production store, but a query that will not finish over
a 20 000-triple sample will not survive a real ingest either. Exit status is
non-zero if any query fails to render or raises.
