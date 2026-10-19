# Asking cross-graph questions (`resource_entity_bridge`)

Questions you can type at an MCP client with BrainKB connected, when a paper's
resource records and its named entities live in **separate named graphs**. The
agent reads the category description, picks the query and fills in the
parameters, so you never name a query id. The "Routes to" column shows which
canned query should answer each question, so you can tell when the agent picked
the wrong one.

`brainkb_qa_run` uses the same SPARQL endpoint as `brainkb_sparql`, so it needs
the same Admin/SuperAdmin role.

## Start by naming your two graphs

Name the pair once and the agent reuses it for the rest of the session:

> "My resource records are in the test-resource space and my named entities in
> the named-entity-test space. Find the graph IRIs and use that pair for
> everything that follows."

The agent runs `rb_paired_graphs` (or `brainkb_list_spaces`) and remembers the
result as `resource_graph` / `entity_graph`.

## Orientation: which papers have both halves

| Ask this | Routes to |
|---|---|
| "Which papers have both resource records and named entities, and how much of each?" | `rb_shared_papers` |
| "Give me a per-paper overview of how complete and consistent the two graphs are." | `rb_paper_bridge_summary` |
| "Which papers are worth asking cross-graph questions about?" | `rb_paper_bridge_summary` |
| "Which papers still need NER, and which were NER-ed but never mined for resources?" | `rb_extraction_coverage_gaps` |

## Scope compared with what the paper is about

| Ask this | Routes to |
|---|---|
| "Is EpiBRAIN declared for the brain regions this paper actually studies?" | `rb_scope_entity_overlap` (`name`, `facet`) |
| "Show the overlap between each tool's scope and the cell types extracted from the paper." | `rb_scope_entity_overlap` (`facet=CellType`) |
| "What does this paper discuss that none of its tools claims to cover?" | `rb_entities_outside_scope` |
| "Which brain regions in the paper are outside the scope of every resource it used?" | `rb_entities_outside_scope` (`entity_type=BrainRegion`) |
| "Which resources are validated on basal ganglia?" (or any region, cell type or species) | `rb_resources_for_concept` (needs the term IRI, e.g. `UBERON:0010011`; the agent can resolve it) |
| "What datasets or tools cover dorsal striatum, and how strong is the evidence?" | `rb_resources_for_concept` |
| "Is any tool here human-only while the paper also uses mouse?" | `rb_taxon_scope_vs_species` |
| "Flag any resource used outside the limits its own record states." | `rb_out_of_scope_conflicts` |

## Evidence: the same passage seen by both pipelines

| Ask this | Routes to |
|---|---|
| "Which cell types are named in the evidence quoted for this tool's scope claim?" | `rb_evidence_passage_entities` (`name`, `entity_type`) |
| "Show me the passage behind this scope statement and what else is mentioned in it." | `rb_evidence_passage_entities` |
| "Where in the document is Kilosort mentioned, and in which section?" | `rb_resource_mention_sites` (`name`) |

## Provenance across the two graphs

| Ask this | Routes to |
|---|---|
| "Were the resources and the entities extracted from the same PDF? Do the checksums match?" | `rb_document_version_agreement` |
| "Who or what produced each half of this paper's record: which pipeline version, which model, which run?" | `rb_extraction_agents` |
| "Same structsense version on both sides?" | `rb_extraction_agents` |
| "Do the resource scope mapper and the entity concept mapper agree on how terms were grounded?" | `rb_mapping_decision_agreement` |
| "Which ontology groundings are contested between the two graphs?" | `rb_mapping_decision_agreement` |

## Curation

| Ask this | Routes to |
|---|---|
| "What still needs ontology mapping for this paper, from either pipeline?" | `rb_unmapped_curation_backlog` |
| "Build me one curation worklist across both graphs." | `rb_unmapped_curation_backlog` |

## Two-step questions

These chain naturally and get the most out of the category:

> "Before you compare anything between the two halves of this paper, check they
> read the same document version, then show me the scope/entity overlap."

The first step is `rb_document_version_agreement`. If it returns
`sharedChecksums = 0`, the two pipelines read different files, so offsets and
quotes can't be compared. The query notes tell the agent to add that caveat to
the second answer instead of reporting the overlap as is.

> "Which species does the paper name that its resources don't declare, and how
> often is each mentioned?"

This is `rb_taxon_scope_vs_species`. For example, a paper with many *Mus
musculus* mentions whose EpiBRAIN record declares only *Homo sapiens*.

## When not to use this category

If a question is about only one graph ("which tools does this paper use?"), the
agent picks the `resources` (or `named_entities`) category instead. That is
correct and cheaper. The bridge queries are for questions that genuinely need
both sides.
