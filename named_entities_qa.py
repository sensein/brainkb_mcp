# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------------
# DISCLAIMER: This software is provided "as is" without any warranty,
# express or implied, including but not limited to the warranties of
# merchantability, fitness for a particular purpose, and non-infringement.
#
# In no event shall the authors or copyright holders be liable for any
# claim, damages, or other liability, whether in an action of contract,
# tort, or otherwise, arising from, out of, or in connection with the
# software or the use or other dealings in the software.
# -----------------------------------------------------------------------------
 
# @Author  : Tek Raj Chhetri
# @Email   : tekraj@mit.edu
# @Web     : https://tekrajchhetri.com/
# @File    : named_entities_qa.py
# @Software: PyCharm

"""Canned SPARQL queries about named entities.

Each QAQuery below is served by brainkb_qa_list / brainkb_qa_run in server.py.
To add one, copy a whole register(...) block, give it a new id — see guide.md.
"""

from qa_registry import QAParam, QAQuery, register, register_category

# The description is shown to the MCP agent in the brainkb_qa_list menu; it is
# the only thing the agent reads before deciding to open this category. Name the
# kinds of questions answered, and keep it current as queries are added.

CATEGORY = register_category(
    "named_entities",
    "Explore named entities, their mentions, relationships, ontology mappings, "
    "and supporting evidence across papers and other source documents. "
    "Find entities by type or source; inspect naming variations and exact "
    "mention locations; identify entities shared across sources; compare "
    "documents by shared entities; and count entities and mentions. "
    "Explore connected entities, anatomical containment, cell locations, "
    "marker expression, phenotypes, organisms, method participants, "
    "hierarchies, and data or software dependencies. "
    "Inspect ontology mappings, including BKE taxonomy links, mapping tiers, "
    "coverage gaps, candidate decisions, and mapping provenance. "
    "Retrieve recorded causal entity claims, chains, mediators, moderators, "
    "confounders, negation, hypotheticality, evidence bases, effect sizes, "
    "and potentially conflicting entity claims across sources. "
    "Examine publication-year trends, claim-version intervals, extraction "
    "runs, agents, configurations, confidence scores, reviewer decisions, "
    "changes, and validation summaries. "
    "Inspect judge runs, prompts, verdicts and change records, transgenic "
    "lines and carried elements, measure lineage, and phenotype grounding. "
    "Every query reads one named graph, given by its optional `graph` "
    "parameter (default https://www.brainkb.org/named-entity/). When the user "
    "means a particular space or workspace, or data ingested elsewhere, find its "
    "graph IRI with ne_named_entity_graphs, brainkb_read_space or brainkb_list_spaces, "
    "and pass that same `graph` to the lookup helpers and the query. "
    "For a space with several graphs, run the query once per graph. "
    "Resolve user wording into parameter values first: ne_available_entity_types "
    "for type IRIs, ne_find_entity for normalized entity keys and IRIs, and "
    "ne_list_sources for source IRIs and DOIs. "
    "Use for questions such as 'Which papers mention this entity?', "
    "'What names are used for it?', 'What is it connected to?', "
    "'Which entities map to BKE?', 'Which cells express this marker?', "
    "'What evidence supports this claim?', or 'How was this extraction reviewed?'. "
    "Choose the individual QA whose predicates and supported filters match "
    "the request. Distinguish entity identity from mentions and ontology "
    "mappings, and entity provenance from evidence for a specific relationship. "
    "Shared identity does not establish agreement between entity claims. "
    "Detailed audit queries require records that compact exports may omit; "
    "empty answers can reflect missing records or unmatched filters."
)

################################################################################
# Every query below is self-contained: its own PREFIXes, its own params, no
# shared constants. Copy a whole register(...) block to start a new one.
#
# `question`, `notes` and `example` are what the MCP agent sees in
# brainkb_qa_list — it chooses a query and fills its params from those alone,
# so say there (not in a code comment) anything the agent needs to know.

#******************************************************************************
# Parameter lookup helpers — resolve user wording into the type IRIs, entity
# keys and DOIs that the competency-question queries take as parameters.

################################################################################
# Optional user parameter query — result limit only
# Helper — Which named graphs hold named-entity data?
################################################################################

register(QAQuery(
    id="ne_named_entity_graphs",
    category=CATEGORY,
    question="Which named graphs contain named-entity data, and how many entities does each hold?",
    notes=(
        "Run this to choose the `graph` parameter of the other named-entity "
        "queries when the data may not be in the default graph "
        "https://www.brainkb.org/named-entity/, for example after an ingest into "
        "a user's own space. Relevant requests include: 'Which graphs have "
        "extracted entities?'; 'Query the entities in my lab space'; "
        "'Where was the NER output ingested?'. "
        "No input is required; limit defaults to 100 rows. "
        "Returns ?graph (named-graph IRI) and ?entities (distinct ner:NamedEntity "
        "IRIs in it), largest first. "
        "To map a graph to a space, compare ?graph with the graphs listed by "
        "brainkb_list_spaces or brainkb_read_space(slug); when the user names a "
        "space, use its graph IRI from there and confirm it appears here. "
        "Pass ?graph exactly as returned, trailing slash included. "
        "Unlike the other queries, this one reads every graph the SPARQL endpoint "
        "can see. An empty result means no graph contains typed named entities."
    ),
    example={"limit": 100},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>

SELECT ?graph (COUNT(DISTINCT ?e) AS ?entities)
WHERE {
  GRAPH ?graph {
    ?e a ner:NamedEntity .
  }
}
GROUP BY ?graph
ORDER BY DESC(?entities) ?graph
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "limit",
            "int",
            "Maximum number of graph rows to return.",
            default=100,
            minimum=1,
        ),
    ),
))

################################################################################
# End helper — named-entity graphs
################################################################################

################################################################################
# Optional user parameter query — result limit only
# Helper — Which entity types are available?
################################################################################

register(QAQuery(
    id="ne_available_entity_types",
    category=CATEGORY,
    question="Which entity types (classes) are available, and how many entities does each have?",
    notes=(
        "Run this first to turn a user's type name into a class IRI, for example "
        "before ne_entities_of_type (parameter entity_type) or to see what kinds "
        "of entities the graph holds. Relevant requests include: "
        "'What entity types are there?'; 'Which kinds of entities were extracted?'; "
        "'Is there a Drug type?'. "
        "No input is required; limit defaults to 1000 rows. "
        "Returns ?type (class IRI in the ner namespace), ?typeName (its local "
        "name, e.g. Drug or BrainRegion) and ?entities (distinct entity IRIs "
        "typed with that class). Match the user's wording against ?typeName "
        "case-insensitively and pass the corresponding ?type IRI; do not "
        "construct a type IRI that this query did not return. "
        "Excludes the generic ner:NamedEntity class. Counts use asserted types "
        "only, without subclass expansion, so a parent class may show fewer "
        "entities than its subclasses combined. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no typed named entities are loaded."
    ),
    example={"limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>

SELECT ?type ?typeName (COUNT(DISTINCT ?e) AS ?entities)
WHERE {
  GRAPH {{graph}} {
    ?e a ner:NamedEntity ;
       a ?type .

    FILTER(
      STRSTARTS(STR(?type), STR(ner:)) &&
      ?type != ner:NamedEntity
    )
    BIND(STRAFTER(STR(?type), STR(ner:)) AS ?typeName)
  }
}
GROUP BY ?type ?typeName
ORDER BY DESC(?entities) ?typeName
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "limit",
            "int",
            "Maximum number of entity-type rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End helper — available entity types
################################################################################

################################################################################
# User parameter query — name required
# Helper — Find entities, their normalized keys and IRIs, by name
################################################################################

register(QAQuery(
    id="ne_find_entity",
    category=CATEGORY,
    question="Which entities match a given name, and what are their normalized keys, IRIs and types?",
    notes=(
        "REQUIRES INPUT: `name`, the text the user typed (e.g. 'hippocampus', "
        "'PV', 'parvalbumin'). Ask the user if no name was given; do not guess. "
        "Run this before any query that takes an entity key parameter "
        "(entity_key, cause_key, effect_key, cell_key, region_key, marker_key, "
        "phenotype_key, ...): those parameters need an exact normalized key "
        "copied from ?key here, never one invented from the user's wording. "
        "Matching is a case-insensitive substring match against the normalized "
        "key, the normalized label, rdfs:label, and verbatim mention surface "
        "forms, so abbreviations and spelling variants used in the text are found. "
        "Returns ?e (entity IRI), ?key (normalized entity key), ?label "
        "(normalized label), ?types (ner class local names, comma-separated) and "
        "?matchedText (the values that matched, ' | '-separated). "
        "Several entities can match; if more than one plausibly fits, show the "
        "candidates and ask the user which one is meant rather than picking. "
        "Separate entity IRIs can share a key; equal labels or keys do not "
        "establish identity. Use ?e with ne_node_neighborhood when the IRI matters. "
        "Short names can match many entities; limit defaults to 100 rows. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no entity key, label, or surface form contains the text."
    ),
    example={"name": "hippocampus", "limit": 100},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?e ?key
       (SAMPLE(?lbl) AS ?label)
       (GROUP_CONCAT(DISTINCT ?typeName; separator=", ") AS ?types)
       (GROUP_CONCAT(DISTINCT ?matched; separator=" | ") AS ?matchedText)
WHERE {
  VALUES ?requestedName { {{name}} }

  GRAPH {{graph}} {
    ?e a ner:NamedEntity ;
       ner:normalizedEntityKey ?key .

    {
      { ?e ner:normalizedEntityKey ?matched }
      UNION
      { ?e ner:normalizedEntityLabel ?matched }
      UNION
      { ?e rdfs:label ?matched }
      UNION
      { ?e ner:hasMention/ner:surfaceForm ?matched }
    }

    OPTIONAL { ?e ner:normalizedEntityLabel ?lbl }
    OPTIONAL {
      ?e a ?cls .
      FILTER(STRSTARTS(STR(?cls), STR(ner:)) && ?cls != ner:NamedEntity)
      BIND(STRAFTER(STR(?cls), STR(ner:)) AS ?typeName)
    }
  }

  FILTER(CONTAINS(LCASE(STR(?matched)), LCASE(?requestedName)))
}
GROUP BY ?e ?key
ORDER BY ?key ?e
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "name",
            "string",
            "Required text to look for, e.g. 'hippocampus' or 'PV'.",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of matching entities to return.",
            default=100,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End helper — find entity
################################################################################

################################################################################
# Optional user parameter query
# Helper — List source documents with their DOIs and dates
################################################################################

register(QAQuery(
    id="ne_list_sources",
    category=CATEGORY,
    question="Which source documents are loaded, with their DOIs, titles and dates?",
    notes=(
        "Use to find the DOI of a paper before running a query with a `doi` "
        "parameter (ne_source_typed_inventory, ne_sources_by_shared_entities, "
        "ne_source_extraction_density, ne_organisms_per_source), or to answer "
        "'Which papers are in the knowledge base?'. "
        "OPTIONAL INPUT: `search`, a case-insensitive substring matched against "
        "the DOI, the source IRI and the title or label (e.g. part of a DOI or a "
        "title word). Omit it or use an empty string to list all sources. "
        "Returns ?pub (source IRI), ?doi (optional), ?title (optional title or "
        "label) and ?date (optional publication date). Pass ?doi exactly as "
        "returned to DOI parameters; DOI-parameter queries match exactly. "
        "Sources without DOIs remain visible and cannot be selected with a "
        "DOI parameter. Sources are the targets of entity provenance and of "
        "mention document versions; they may include documents other than papers. "
        "A source with several dates or titles produces several rows. "
        "Limit defaults to 1000 rows. The query is scoped to "
        "the named graph given by `graph`. An empty result means no "
        "source matches the search text."
    ),
    example={"search": "", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX dcterms: <http://purl.org/dc/terms/>

SELECT DISTINCT ?pub ?doi ?title ?date
WHERE {
  VALUES ?requestedText { {{search}} }

  GRAPH {{graph}} {
    {
      { ?e a ner:NamedEntity ; prov:hadPrimarySource ?pub }
      UNION
      { ?dv ner:versionOfDocument ?pub }
    }

    OPTIONAL { ?pub ner:doi ?doi }
    OPTIONAL { ?pub (dcterms:title|rdfs:label) ?title }
    OPTIONAL { ?pub (ner:publicationDate|dcterms:issued) ?date }
  }

  FILTER(
    ?requestedText = "" ||
    CONTAINS(LCASE(STR(?pub)), LCASE(?requestedText)) ||
    (BOUND(?doi) && CONTAINS(LCASE(STR(?doi)), LCASE(?requestedText))) ||
    (BOUND(?title) && CONTAINS(LCASE(STR(?title)), LCASE(?requestedText)))
  )
}
ORDER BY ?doi ?pub
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "search",
            "string",
            "Optional substring of a DOI, source IRI or title; omit or use an empty string for all sources.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of source rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End helper — list sources
################################################################################

#******************************************************************************
# Competency Questions

################################################################################


################################################################################
# User parameter query — type IRI resolved through ne_available_entity_types
# CQ1 — Which entities of a given type exist, and in which papers?
################################################################################

register(QAQuery(
    id="ne_entities_of_type",
    category=CATEGORY,
    question="Which entities of a requested type exist, and which sources mention them?",
    notes=(
        "Use for requests such as 'List the drugs', 'Show brain regions', "
        "'Which genes occur in the documents?', or "
        "'Which papers mention cell types?'. "
        "First run ne_available_entity_types to resolve the user's type name "
        "to an available class IRI, then supply it as entity_type. "
        "Type discovery must use the same named graph. "
        "For example, 'drug' resolves to https://brainkb.org/ner/Drug "
        "when that type is returned. "
        "The runner must bind entity_type as an IRI before execution; "
        "do not execute this query with the parameter unbound. "
        "Returns ?e (entity IRI), ?pub (source document IRI), "
        "?label (normalized entity label), and ?doi (optional source DOI). "
        "Returns all matching rows without a query-level row limit. "
        "An entity appearing in several sources has multiple source rows; "
        "these are not duplicate entities. Sources without DOIs remain visible. "
        "Matches the selected rdf:type without explicit subclass expansion. "
        "Source provenance indicates occurrence, not support for a specific claim. "
        "The query is scoped to "
        "the named graph given by `graph`. Selected papers require an "
        "additional source filter. An empty result means no entities match "
        "the required type, label, and source-provenance patterns."
    ),
    example={"entity_type": "https://brainkb.org/ner/Drug"},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT DISTINCT ?e ?pub ?label ?doi
WHERE {
  GRAPH {{graph}} {
    ?e a ner:NamedEntity ;
       a {{entity_type}} ;
       ner:normalizedEntityLabel ?label ;
       prov:hadPrimarySource ?pub .

    OPTIONAL { ?pub ner:doi ?doi }
  }
}
ORDER BY ?label ?e ?pub
""",
    params=(
        QAParam(
            "entity_type",
            "iri",
            "Required class IRI selected from ne_available_entity_types.",
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ1
################################################################################

################################################################################
# Optional user parameter query
# CQ2 — Under which verbatim surface forms is an entity mentioned?
################################################################################

register(QAQuery(
    id="ne_entity_surface_forms",
    category=CATEGORY,
    question="How is an entity named or written across papers and other sources?",
    notes=(
        "Use to inspect verbatim terminology variations for extracted entities. "
        "Relevant requests include: "
        "'What names are used for this entity?'; "
        "'How is this entity mentioned across papers?'; "
        "'Show spelling or naming variations'; "
        "'Which sources use each name?'; "
        "'Show the original wording for this entity'. "
        "OPTIONAL INPUT: `entity_key`, an exact normalized entity key from "
        "ne_find_entity; omit or use an empty string for all keys. "
        "If the user supplies a name or IRI, first look up the entity and "
        "retrieve its normalized key; do not invent a key from the name. "
        "If omitted, returns surface forms for all entities with matching "
        "mention records. Returns all qualifying rows without a query-level limit. "
        "Returns ?pub (source document IRI), ?key (normalized entity key), "
        "?surface (verbatim mention text), and ?doi (optional source DOI). "
        "Repeated occurrences of the same surface form for the same key and "
        "source are deduplicated; this query does not count occurrences or "
        "return mention offsets. Sources without DOIs remain visible. "
        "Source attribution follows each mention's document version. "
        "Surface forms reflect extracted wording, not independently verified "
        "synonyms. Matching a normalized key does not disambiguate separate "
        "entity IRIs that share that key; use an entity-IRI filter when needed. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no mention records match the supplied key "
        "and required source-document links."
    ),
    example={"entity_key": "hippocampus"},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>

SELECT DISTINCT ?pub ?key ?surface ?doi
WHERE {
  VALUES ?requestedKey { {{entity_key}} }

  GRAPH {{graph}} {
    ?e ner:normalizedEntityKey ?key ;
       ner:hasMention ?m .

    ?m ner:surfaceForm ?surface ;
       ner:partOfDocumentVersion/ner:versionOfDocument ?pub .

    OPTIONAL { ?pub ner:doi ?doi }
  }

  FILTER(?requestedKey = "" || STR(?key) = ?requestedKey)
}
ORDER BY ?key ?surface ?pub
""",
    params=(
        QAParam(
            "entity_key",
            "string",
            "Optional exact normalized entity key; omit or use an empty string for all keys.",
            default="",
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ2
################################################################################

################################################################################
# Non user parameter query
# CQ3 — Exactly where in the source document does each mention sit?
################################################################################

register(QAQuery(
    id="ne_mention_source_offsets",
    category=CATEGORY,
    question="Where does each entity mention occur in its source document?",
    notes=(
        "Use to inspect the character-level grounding of extracted mentions. "
        "Relevant requests include: "
        "'Where exactly are the entities mentioned?'; "
        "'Show the character offsets of each mention'; "
        "'Show the original text and its position in the document'; "
        "'Which source contains this mention?'; "
        "'Retrieve mention spans for grounding checks'. "
        "Requires no user parameters and returns all qualifying rows "
        "without a query-level row limit. "
        "Returns ?m (mention IRI), ?pub (source document IRI), "
        "?surface (verbatim mention text), ?start (document start offset), "
        "?end (document end offset), and ?doi (optional source DOI). "
        "Offsets refer to the text representation of the linked document "
        "version, not PDF page coordinates or positions in a differently "
        "formatted copy. Reproducing a span requires that exact text version "
        "and the exporter's offset convention. "
        "Only mentions with both offsets, a surface form, and a source-document "
        "link are returned. Sources without DOIs remain visible. "
        "Separate occurrences remain separate mention IRIs even when their "
        "surface forms are identical. "
        "The query retrieves stored grounding information; it does not "
        "independently verify that the offsets match the source text. "
        "The query covers all sources in "
        "the named graph given by `graph`. Requests about a specific "
        "entity, mention, or document require additional filters. "
        "An empty result means no mentions match all required grounding "
        "patterns, not necessarily that no entities were extracted."
    ),
    example={},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>

SELECT DISTINCT ?m ?pub ?surface ?start ?end ?doi
WHERE {
  GRAPH {{graph}} {
    ?m a ner:EntityMention ;
       ner:surfaceForm ?surface ;
       ner:documentStartOffset ?start ;
       ner:documentEndOffset ?end ;
       ner:partOfDocumentVersion/ner:versionOfDocument ?pub .

    OPTIONAL { ?pub ner:doi ?doi }
  }
}
ORDER BY ?pub ?start ?end ?m
""",
    params=(
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ3
################################################################################

################################################################################
# Optional user parameter query
# CQ4 — Full typed inventory contributed by one paper or all sources
################################################################################

register(QAQuery(
    id="ne_source_typed_inventory",
    category=CATEGORY,
    question="Which entities and entity types occur in a paper or across all sources?",
    notes=(
        "OPTIONAL INPUT: `doi`, the paper's DOI as stored in the graph. "
        "Omit it or supply an empty string to return inventories for all "
        "sources, including sources without DOIs. Do not ask for a DOI "
        "when the user requests an inventory across all sources. "
        "If the user requests a specific paper, use its supplied or "
        "looked-up DOI; do not silently return all sources when that "
        "paper has not been identified. "
        "Relevant requests include: "
        "'List all entities in this paper'; "
        "'What entities were extracted from this study?'; "
        "'Show the typed entity inventory across documents'. "
        "A supplied DOI is matched exactly; a DOI URL may differ from "
        "the bare DOI stored in the graph. "
        "Returns ?pub (source document IRI), ?doi (optional DOI), "
        "?e (canonical entity IRI), ?key (normalized entity key), and "
        "?cls (entity class IRI in the ner namespace). "
        "Excludes the generic ner:NamedEntity class. "
        "Entities with multiple types produce multiple rows; these are "
        "type assignments, not duplicate entities. "
        "Types belong to shared entity nodes and may aggregate assignments "
        "across sources; they are not necessarily source-specific typings. "
        "Returns all qualifying rows without a query-level row limit. "
        "The query is scoped to the named graph given by `graph`. "
        "Selecting a particular source without a DOI requires an additional "
        "source-IRI filter. An empty result means no records match the "
        "filter and required entity-key, type, and source-provenance patterns."
    ),
    example={},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT DISTINCT ?pub ?doi ?e ?key ?cls
WHERE {
  VALUES ?requestedDoi { {{doi}} }

  GRAPH {{graph}} {
    ?e prov:hadPrimarySource ?pub ;
       ner:normalizedEntityKey ?key ;
       a ?cls .

    OPTIONAL { ?pub ner:doi ?doi }

    FILTER(
      STRSTARTS(STR(?cls), STR(ner:)) &&
      ?cls != ner:NamedEntity
    )
  }

  FILTER(
    ?requestedDoi = "" ||
    (BOUND(?doi) && STR(?doi) = STR(?requestedDoi))
  )
}
ORDER BY ?cls ?key ?e ?pub
""",
    params=(
        QAParam(
            "doi",
            "string",
            "Optional exact paper DOI; omit or use an empty string for all sources.",
            default="",
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ4
################################################################################

################################################################################
# Non user parameter query
# CQ5 — Which entities are shared across papers?

register(QAQuery(
    id="ne_entities_shared_across_sources",
    category=CATEGORY,
    question="Which entities are shared across papers or sources?",
    notes=(
        "Use to find canonical entities occurring in multiple sources and "
        "inspect how those sources name them. Relevant requests include: "
        "'Which entities appear in multiple papers?'; "
        "'What entities do the papers have in common?'; "
        "'Which entities recur across studies?'; "
        "'Which entities are mentioned most widely?'; "
        "'Are different names used for the same entity across papers?'; "
        "'Show terminology variations across documents'; "
        "'Is the same entity IRI reused across sources?'. "
        "Returns all qualifying results without a query-level row limit. "
        "Groups by canonical entity IRI and normalized key. "
        "Returns ?entity (canonical IRI), ?key (normalized key), "
        "?papers (distinct source-document count), "
        "?mentions (distinct mention-node count), "
        "?wording (distinct surface forms separated by ' | '), and "
        "?sources (DOIs where available, otherwise source IRIs). "
        "Each mention is attributed through its document version, preventing "
        "mentions from being incorrectly counted against every source of a "
        "shared entity. The papers column includes non-paper documents. "
        "Concatenated values are unordered. "
        "Shared identity reflects existing graph assignments; it does not "
        "prove correct entity resolution, consistent meaning, or agreement "
        "between scientific entity claims. Separate IRIs representing the same "
        "real-world entity are not merged. "
        "The query is scoped to "
        "the named graph given by `graph`. A specific entity or selected "
        "set of papers requires additional filters. "
        "An empty result means no entity has qualifying mention records in "
        "at least two sources."
    ),
    example={},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>

SELECT ?entity ?key
       (COUNT(DISTINCT ?source) AS ?papers)
       (COUNT(DISTINCT ?mention) AS ?mentions)
       (GROUP_CONCAT(DISTINCT ?surface; separator=" | ") AS ?wording)
       (GROUP_CONCAT(DISTINCT ?sourceId; separator=" | ") AS ?sources)
WHERE {
  GRAPH {{graph}} {
    ?entity a ner:NamedEntity ;
            ner:normalizedEntityKey ?key ;
            ner:hasMention ?mention .

    ?mention ner:surfaceForm ?surface ;
             ner:partOfDocumentVersion/ner:versionOfDocument ?source .

    OPTIONAL { ?source ner:doi ?doi }
    BIND(COALESCE(STR(?doi), STR(?source)) AS ?sourceId)
  }
}
GROUP BY ?entity ?key
HAVING(COUNT(DISTINCT ?source) > 1)
ORDER BY DESC(?papers) ?key ?entity
""",
    params=(
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################

################################################################################
# Optional user parameter query
# CQ6 — Which papers are most related by shared entities?
################################################################################

register(QAQuery(
    id="ne_sources_by_shared_entities",
    category=CATEGORY,
    question="Which papers or sources share the most entities with a given paper?",
    notes=(
        "OPTIONAL INPUT: `doi`, the reference paper's DOI as stored in the graph. "
        "Omit it or supply an empty string to compare all source pairs, "
        "including sources without DOIs. "
        "Relevant requests include: "
        "'Which papers share entities with this paper?'; "
        "'Find related studies based on common entities'; "
        "'Which documents have the most entities in common?'; "
        "'Rank papers by shared entities'. "
        "Do not ask for a DOI when the user requests comparisons across "
        "all sources. If the user requests comparison with a specific paper, "
        "use its supplied or looked-up DOI; do not silently compare all "
        "sources when the reference paper has not been identified. "
        "A supplied DOI is matched exactly. "
        "Returns ?p1 (reference source IRI), ?p2 (other source IRI), "
        "?doi (optional reference DOI), ?otherDoi (optional other DOI), "
        "and ?shared (number of distinct entity IRIs shared by the pair). "
        "Excludes self-comparisons and returns only pairs sharing at least "
        "one entity, ordered by shared-entity count descending. "
        "When no DOI is supplied, both directions of each pair are returned: "
        "A to B and B to A. "
        "Shared entities are identified by the same entity IRI, not matching "
        "names or external ontology terms. "
        "The score is a raw overlap count, not a normalized similarity score; "
        "sources with larger extracted inventories may rank higher. "
        "Entity overlap does not establish scientific agreement or citation "
        "relationships. Sources may include documents other than papers. "
        "Selecting a reference source without a DOI requires an additional "
        "source-IRI filter. "
        "Returns all qualifying rows without a query-level row limit. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no source pairs match the filter and share "
        "an entity through the recorded source-provenance links."
    ),
    example={},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT ?p1 ?p2 ?doi ?otherDoi
       (COUNT(DISTINCT ?e) AS ?shared)
WHERE {
  VALUES ?requestedDoi { {{doi}} }

  GRAPH {{graph}} {
    ?e a ner:NamedEntity ;
       prov:hadPrimarySource ?p1, ?p2 .

    OPTIONAL { ?p1 ner:doi ?doi }
    OPTIONAL { ?p2 ner:doi ?otherDoi }

    FILTER(?p1 != ?p2)
  }

  FILTER(
    ?requestedDoi = "" ||
    (BOUND(?doi) && STR(?doi) = STR(?requestedDoi))
  )
}
GROUP BY ?p1 ?p2 ?doi ?otherDoi
ORDER BY DESC(?shared) ?p1 ?p2
""",
    params=(
        QAParam(
            "doi",
            "string",
            "Optional exact reference-paper DOI; omit or use an empty string to compare all source pairs.",
            default="",
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ6
################################################################################

################################################################################
# Non user parameter query
# CQ7 — Which external mappings are associated with multiple sources?
################################################################################

register(QAQuery(
    id="ne_external_mappings_across_sources",
    category=CATEGORY,
    question="Which external ontology terms are mapped to entities occurring across multiple sources?",
    notes=(
        "Use for requests such as "
        "'Which ontology terms are represented across multiple papers?'; "
        "'Which external mappings are most widely represented?'; "
        "'What mapped concepts do the sources have in common?'. "
        "Requires no user parameters and returns all qualifying rows "
        "without a query-level row limit. "
        "Returns ?obo (external mapping target IRI) and "
        "?papers (number of distinct sources mentioning entities mapped "
        "to that target). Despite its name, ?obo is not restricted to "
        "the OBO namespace. "
        "Includes exactMatch, closeMatch, and broadMatch mappings. "
        "Excludes provisional concept IRIs beginning with "
        "https://brainkb.org/concept/. "
        "Only targets associated with more than one source are returned. "
        "Each source is counted once per target, even when several entities "
        "in that source map to the same target. Different entities may "
        "contribute to the same target's source count; this does not require "
        "one canonical entity to occur in every counted source. "
        "Mapping tiers are combined in this query and do not all establish "
        "identity. Use ne_entity_external_mappings to inspect individual tiers. "
        "Mappings belong to entity nodes; source provenance indicates where "
        "those entities occur. It does not establish that each source "
        "independently asserted or endorsed the mapping. "
        "Sources may include documents other than papers. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no qualifying mapping target is associated "
        "with entities from at least two sources."
    ),
    example={},
    sparql="""
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT ?obo (COUNT(DISTINCT ?pub) AS ?papers)
WHERE {
  GRAPH {{graph}} {
    ?e (skos:exactMatch|skos:closeMatch|skos:broadMatch) ?obo ;
       prov:hadPrimarySource ?pub .

    FILTER(!STRSTARTS(STR(?obo), "https://brainkb.org/concept/"))
  }
}
GROUP BY ?obo
HAVING(COUNT(DISTINCT ?pub) > 1)
ORDER BY DESC(?papers) ?obo
""",
    params=(
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ7
################################################################################


################################################################################
# Non user parameter query
# CQ8 — What is an entity mapped to, at which mapping relationship?
################################################################################

register(QAQuery(
    id="ne_entity_external_mappings",
    category=CATEGORY,
    question="Which external ontology terms are entities mapped to, and at which mapping tier?",
    notes=(
        "Use for requests such as "
        "'Show entity-to-ontology mappings'; "
        "'Which ontology terms are linked to the extracted entities?'; "
        "'Show exact, close, broad, narrow, and related matches'; "
        "'What mapping relationship connects each entity to its ontology term?'. "
        "Requires no user parameters and returns all qualifying rows "
        "without a query-level row limit. "
        "Returns ?e (canonical entity IRI), ?key (normalized entity key), "
        "?tier (exact, close, broad, narrow, or related), and "
        "?obo (external mapping target IRI). Despite its name, ?obo is "
        "not restricted to the OBO namespace. "
        "Excludes provisional concept IRIs beginning with "
        "https://brainkb.org/concept/. "
        "The tier identifies the recorded SKOS mapping relationship, "
        "not a numeric confidence score. Broad and narrow describe mapping "
        "direction; related indicates an associative mapping. Do not treat "
        "all tiers as identity or interchangeability. "
        "An entity can have multiple targets or mapping tiers, producing "
        "multiple rows without implying duplicate entities. "
        "Mappings are read from shared entity nodes and are not attributed "
        "to individual sources or mapping decisions by this query. "
        "The query covers all qualifying entities in "
        "the named graph given by `graph`. Requests about a specific "
        "entity, tier, or ontology require additional filters. "
        "An empty result means no records match the required normalized-key "
        "and external-mapping patterns."
    ),
    example={},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>

SELECT DISTINCT ?e ?key ?tier ?obo
WHERE {
  VALUES (?p ?tier) {
    (skos:exactMatch   "exact")
    (skos:closeMatch   "close")
    (skos:narrowMatch  "narrow")
    (skos:broadMatch   "broad")
    (skos:relatedMatch "related")
  }

  GRAPH {{graph}} {
    ?e ner:normalizedEntityKey ?key ;
       ?p ?obo .

    FILTER(!STRSTARTS(STR(?obo), "https://brainkb.org/concept/"))
  }
}
ORDER BY ?key ?e ?tier ?obo
""",
    params=(
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ8
################################################################################

################################################################################
# Optional user parameter query
# CQ9 — Which entities lack an external-ontology mapping?
################################################################################

register(QAQuery(
    id="ne_entities_without_external_mapping",
    category=CATEGORY,
    question="Which entities lack an external-ontology mapping, including those with only provisional BRAINKB concepts?",
    notes=(
        "Use for requests such as "
        "'Which entities are unmapped?'; "
        "'Show external ontology coverage gaps'; "
        "'Which entities have only provisional BrainKB mappings?'; "
        "'Which entities need ontology mapping or curation?'. "
        "No input is required; limit defaults to 1000 result rows. "
        "Returns ?e (canonical entity IRI), ?cls (entity class), "
        "?key (normalized entity key), and ?gap (optional entity comment). "
        "Includes entities without a resolved concept and entities whose "
        "resolved concepts have only BRAINKB ontology metadata. "
        "Excludes an entity if any resolved concept is linked through its "
        "ontology version to an ontology acronym other than BRAINKB, "
        "compared case-insensitively. "
        "Entities with both provisional and external mappings are excluded. "
        "Missing ontology metadata can also cause an entity to appear; "
        "a result is a coverage-review candidate, not definitive proof "
        "that no external mapping exists. Direct SKOS mapping edges without "
        "the resolved-concept metadata path are not checked by this query. "
        "The gap field contains an existing entity comment, which may not "
        "specifically explain a mapping failure. "
        "Excludes the generic ner:NamedEntity class. Multiple classes or "
        "comments can produce multiple rows for one entity; the limit "
        "applies to rows, not distinct entities. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no entities match these coverage-gap patterns."
    ),
    example={"limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT DISTINCT ?e ?cls ?key ?gap
WHERE {
  GRAPH {{graph}} {
    ?e a ner:NamedEntity ;
       ner:normalizedEntityKey ?key ;
       a ?cls .

    OPTIONAL { ?e rdfs:comment ?gap }

    FILTER(
      STRSTARTS(STR(?cls), STR(ner:)) &&
      ?cls != ner:NamedEntity
    )
  }

  MINUS {
    SELECT DISTINCT ?e
    WHERE {
      GRAPH {{graph}} {
        ?e ner:resolvedToConcept ?c .

        ?c ner:conceptInOntologyVersion/
           ner:versionOfOntology/
           ner:ontologyAcronym ?acronym .

        FILTER(UCASE(STR(?acronym)) != "BRAINKB")
      }
    }
  }
}
ORDER BY ?cls ?key ?e ?gap
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "limit",
            "int",
            "Maximum number of result rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# Optional user parameter query
# CQ10 — Tier distribution per external ontology
################################################################################

register(QAQuery(
    id="ne_mapping_tiers_by_ontology",
    category=CATEGORY,
    question="How are entity mappings distributed across mapping tiers for each external ontology?",
    notes=(
        "Use for requests such as "
        "'Show mapping tiers by ontology'; "
        "'How many exact versus close matches does each ontology have?'; "
        "'Which ontologies mostly provide broader or related matches?'; "
        "'Summarize external ontology alignment for this corpus'. "
        "No input is required; limit defaults to 1000 result rows. "
        "Returns ?acr (ontology acronym), ?tier (exact, close, broad, "
        "narrow, or related), and ?n (number of distinct entity-target "
        "mapping pairs for that ontology and tier). "
        "Counts distinct combinations of entity IRI, target IRI, ontology "
        "acronym, and tier before aggregation, preventing repeated "
        "ontology-version metadata from inflating totals. "
        "An entity mapped to several targets contributes multiple pairs. "
        "A pair recorded under multiple tiers contributes to each tier. "
        "Counts are not mention counts or source-document counts. "
        "Excludes BRAINKB ontology metadata case-insensitively. "
        "Requires the target's concept IRI and ontology-acronym metadata; "
        "mappings without that metadata are not counted. "
        "The distribution can identify alignment patterns worth reviewing, "
        "but does not independently establish ontology granularity or "
        "mapping quality. Mapping tiers are relationships, not confidence scores. "
        "The query is scoped to the named graph given by `graph`. "
        "The limit applies to aggregated ontology-tier rows after counting. "
        "Absent ontology-tier combinations are omitted rather than shown "
        "with zero counts. An empty result means no mappings match the "
        "required entity, target-metadata, and external-ontology patterns."
    ),
    example={"limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>

SELECT ?acr ?tier (COUNT(*) AS ?n)
WHERE {
  {
    SELECT DISTINCT ?e ?term ?acr ?tier
    WHERE {
      GRAPH {{graph}} {
        {
          { ?e skos:exactMatch ?term . BIND("exact" AS ?tier) }
          UNION
          { ?e skos:closeMatch ?term . BIND("close" AS ?tier) }
          UNION
          { ?e skos:broadMatch ?term . BIND("broad" AS ?tier) }
          UNION
          { ?e skos:narrowMatch ?term . BIND("narrow" AS ?tier) }
          UNION
          { ?e skos:relatedMatch ?term . BIND("related" AS ?tier) }
        }

        ?e a ner:NamedEntity .
      }

      {
        SELECT DISTINCT ?term ?acr
        WHERE {
          GRAPH {{graph}} {
            ?concept ner:conceptIRI ?iri ;
                     ner:conceptInOntologyVersion/
                     ner:versionOfOntology/
                     ner:ontologyAcronym ?acr .

            FILTER(UCASE(STR(?acr)) != "BRAINKB")
            BIND(IRI(STR(?iri)) AS ?term)
          }
        }
      }
    }
  }
}
GROUP BY ?acr ?tier
ORDER BY ?acr DESC(?n) ?tier
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "limit",
            "int",
            "Maximum number of aggregated ontology-tier rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# Optional user parameter query
# CQ11 — Causal claims involving an entity as cause, effect, or mediator
################################################################################

register(QAQuery(
    id="ne_entity_causal_claims",
    category=CATEGORY,
    question="Which causal claims involve an entity, in which roles, and with what negation or hypotheticality flags?",
    notes=(
        "OPTIONAL INPUT: `entity_key`, an exact normalized entity key. "
        "Omit it or supply an empty string to return all qualifying "
        "entity-role participations. Limit defaults to 1000 result rows. "
        "Relevant requests include: "
        "'Which causal claims involve this entity?'; "
        "'Is this entity a cause, effect, or mediator?'; "
        "'What effects are attributed to this entity?'; "
        "'What causes this entity or process?'; "
        "'Which claims involving this entity are negated or hypothetical?'. "
        "If the user supplies an entity name or IRI, retrieve its normalized "
        "key first; do not guess the key. If a specific entity has not been "
        "resolved, do not silently substitute an all-entity query. "
        "Returns ?key (participating entity key), ?role (cause, effect, "
        "or mediator), ?causeKey, ?effectKey, ?negated, and ?hypothetical. "
        "Reads flags from each claim's current causal-relation version. "
        "Only claims with both flags and normalized cause/effect keys "
        "are returned. Claims lacking these fields are omitted. "
        "A true negation flag records a negated claim; a true hypotheticality "
        "flag records a hypothetical claim. A false hypotheticality flag "
        "does not independently establish experimental intervention or truth. "
        "An entity occupying multiple roles produces multiple rows. "
        "With no entity filter, a claim may appear for each participating "
        "entity and role. Separate claims may produce identical displayed "
        "rows because claim IRIs and source provenance are not selected. "
        "Matching a normalized key does not disambiguate separate entity "
        "IRIs sharing that key. "
        "The query is scoped to the named graph given by `graph`. "
        "Requests restricted to one role or flag require additional filters. "
        "An empty result means no claims match the filter and required "
        "patterns; it does not establish that no causal relationship exists."
    ),
    example={"entity_key": "dendritic_calcium_signaling", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>

SELECT ?key ?role ?causeKey ?effectKey ?negated ?hypothetical
WHERE {
  VALUES ?requestedKey { {{entity_key}} }

  GRAPH {{graph}} {
    ?x ner:normalizedEntityKey ?key .

    ?rel ner:hasCause ?c ;
         ner:hasEffect ?ef ;
         ner:hasCurrentCausalRelationVersion ?v .

    ?v ner:causalNegated ?negated ;
       ner:causalHypothetical ?hypothetical .

    ?c ner:normalizedEntityKey ?causeKey .
    ?ef ner:normalizedEntityKey ?effectKey .

    {
      { ?rel ner:hasCause ?x . BIND("cause" AS ?role) }
      UNION
      { ?rel ner:hasEffect ?x . BIND("effect" AS ?role) }
      UNION
      { ?rel ner:hasMediator ?x . BIND("mediator" AS ?role) }
    }
  }

  FILTER(?requestedKey = "" || ?key = ?requestedKey)
}
ORDER BY ?key ?role ?causeKey ?effectKey ?negated ?hypothetical ?rel
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "entity_key",
            "string",
            "Optional exact normalized entity key; omit or use an empty string for all entities.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of entity-role claim rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ11
################################################################################

################################################################################
# Optional user parameter query
# CQ12 — Reconstruct recorded multi-step causal chains
################################################################################

register(QAQuery(
    id="ne_ordered_causal_chains",
    category=CATEGORY,
    question="Which multi-step causal chains are recorded, and how are their claims ordered?",
    notes=(
        "Use for requests such as "
        "'Show multi-step mechanisms'; "
        "'Reconstruct the recorded causal chains'; "
        "'What are the steps in each mechanism?'; "
        "'Show the order of claims within a causal chain'. "
        "No input is required; limit defaults to 1000 result rows. "
        "Returns ?chain (chain IRI), ?chainLabel (optional chain label), "
        "?rel (causal-relation IRI), ?position (distinct predecessor count), "
        "?causeKey, and ?effectKey. "
        "Uses explicit CausalChain membership and nextCausalRelation links; "
        "it does not infer chains from co-occurrence or shared entities. "
        "For a linear, acyclic chain, predecessor counts give zero-based "
        "positions: 0, 1, 2, and so on. Branching chains can have tied "
        "positions; cycles make this count unsuitable as a step index. "
        "Only predecessors belonging to the same chain are counted, but "
        "the transitive path can traverse relations outside that chain. "
        "The query does not verify that one step's effect is the next "
        "step's cause. Ordered claim records may describe related parts "
        "of a mechanism without forming a continuous entity-to-entity path. "
        "Claims missing normalized cause or effect keys are omitted. "
        "Negation, hypotheticality, and source evidence are not selected; "
        "do not interpret every returned step as an established causal fact. "
        "The limit applies to step rows, not complete chains, and can "
        "truncate a chain. The query covers all recorded chains in "
        "the named graph given by `graph`. "
        "A specific chain requires an additional chain-IRI filter. "
        "An empty result means no records match the required chain "
        "membership and cause/effect patterns."
    ),
    example={"limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?chain ?chainLabel ?rel
       (COUNT(DISTINCT ?prior) AS ?position)
       ?causeKey ?effectKey
WHERE {
  GRAPH {{graph}} {
    ?chain a ner:CausalChain ;
           ner:hasChainRelation ?rel .

    ?rel ner:hasCause/ner:normalizedEntityKey ?causeKey ;
         ner:hasEffect/ner:normalizedEntityKey ?effectKey .

    OPTIONAL { ?chain rdfs:label ?chainLabel }

    OPTIONAL {
      ?chain ner:hasChainRelation ?prior .
      ?prior ner:nextCausalRelation+ ?rel .
      FILTER(?prior != ?rel)
    }
  }
}
GROUP BY ?chain ?chainLabel ?rel ?causeKey ?effectKey
ORDER BY ?chain ?position ?rel ?causeKey ?effectKey
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "limit",
            "int",
            "Maximum number of chain-step rows to return; may truncate a chain.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ12
################################################################################

################################################################################
# Optional user parameter query
# CQ13 — Quantitative evidence per cause–effect pair
################################################################################

register(QAQuery(
    id="ne_causal_effect_estimates",
    category=CATEGORY,
    question="What quantitative effect estimates are recorded for cause–effect pairs?",
    notes=(
        "Use for requests such as "
        "'Show quantitative evidence for causal claims'; "
        "'List effect sizes with p-values and sample sizes'; "
        "'What measurements support each cause–effect pair?'; "
        "'Retrieve effect estimates for meta-analysis preparation'. "
        "No input is required; limit defaults to 1000 result rows. "
        "Returns ?causeKey (normalized cause key), "
        "?effectKey (normalized effect key), ?measure (effect measure), "
        "?value (recorded effect value), ?p (optional p-value), and "
        "?n (optional sample size). "
        "Retrieves estimates attached to each claim's current "
        "causal-relation version; historical versions are not included. "
        "Requires normalized cause/effect keys, an effect measure, and "
        "an effect value. Estimates lacking p-values or sample sizes "
        "remain visible with those fields unbound. "
        "Missing values must not be interpreted as zero. "
        "Each claim can have multiple estimates. Separate claims or "
        "estimates may produce identical displayed rows because their "
        "IRIs and source identifiers are not selected. Do not assume "
        "such rows are duplicates or independent observations. "
        "These results are a starting point for evidence review, not "
        "automatically poolable meta-analysis data. Before combining "
        "estimates, verify source provenance, study independence, measure "
        "definitions, units, direction, uncertainty, and study context. "
        "Negation and hypotheticality flags are not selected; a returned "
        "estimate alone does not establish an affirmative causal finding. "
        "The query covers all qualifying estimates in "
        "the named graph given by `graph`. Requests about a specific "
        "entity, source, measure, or significance threshold require "
        "additional filters. "
        "The limit applies to result rows, not distinct claims or studies. "
        "An empty result means no records match the required estimate "
        "patterns, not that the source documents contain no quantitative evidence."
    ),
    example={"limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>

SELECT ?causeKey ?effectKey ?measure ?value ?p ?n
WHERE {
  GRAPH {{graph}} {
    ?rel ner:hasCause/ner:normalizedEntityKey ?causeKey ;
         ner:hasEffect/ner:normalizedEntityKey ?effectKey ;
         ner:hasCurrentCausalRelationVersion ?v .

    ?v ner:hasEffectEstimate ?est .

    ?est ner:effectMeasure ?measure ;
         ner:effectValue ?value .

    OPTIONAL { ?est ner:pValue ?p }
    OPTIONAL { ?est ner:sampleSize ?n }
  }
}
ORDER BY ?causeKey ?effectKey ?measure ?rel ?est ?value ?p ?n
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "limit",
            "int",
            "Maximum number of quantitative-evidence rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ13
################################################################################

################################################################################
# Optional user parameter query
# CQ 14 - When was each entity first mentioned in dated sources, and in how many?
################################################################################

register(QAQuery(
    id="ne_entity_first_source_year",
    category=CATEGORY,
    question="What is the earliest source year recorded for each entity, and how many dated sources mention it?",
    notes=(
        "Use for requests such as "
        "'When does each entity first appear in the corpus?'; "
        "'What is the earliest paper mentioning each entity?'; "
        "'How many dated sources mention each entity?'; "
        "'List entities by their earliest recorded source year'. "
        "No input is required; limit defaults to 1000 result rows. "
        "Returns ?e (canonical entity IRI), ?key (normalized entity key), "
        "?first (earliest valid source year), and "
        "?papers (distinct sources with a usable year). "
        "Reads ner:publicationDate or dcterms:issued and converts the "
        "first four characters to an integer year. "
        "Sources without a usable year are excluded from both the "
        "earliest-year calculation and the source count. "
        "Multiple date values for one source do not increase its source "
        "count, but the earliest usable year contributes to the minimum. "
        "The earliest year is relative to the loaded corpus; it is not "
        "necessarily the entity's discovery date or first mention anywhere. "
        "These are source dates, not extraction dates or dates of biological events. "
        "The query returns the earliest year, not the identity of the "
        "earliest paper; retrieving that paper requires an additional query. "
        "Sources may include documents other than papers. "
        "The query covers all qualifying entities in "
        "the named graph given by `graph`. "
        "A specific entity or date range requires additional filters. "
        "An empty result means no entities have source-provenance links "
        "to documents with usable years."
    ),
    example={"limit": 1000},
    sparql="""
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX dcterms: <http://purl.org/dc/terms/>

SELECT ?e ?key
       (MIN(?year) AS ?first)
       (COUNT(DISTINCT ?pub) AS ?papers)
WHERE {
  GRAPH {{graph}} {
    ?e ner:normalizedEntityKey ?key ;
       prov:hadPrimarySource ?pub .

    ?pub (ner:publicationDate|dcterms:issued) ?date .

    BIND(xsd:integer(SUBSTR(STR(?date), 1, 4)) AS ?year)
    FILTER(BOUND(?year))
  }
}
GROUP BY ?e ?key
ORDER BY ?first ?key ?e
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "limit",
            "int",
            "Maximum number of entity-level result rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ 14
################################################################################

################################################################################
# Optional user parameter query
# CQ15 — Hypotheticality of claims about effects, by claim-source year
################################################################################

register(QAQuery(
    id="ne_effect_claims_by_source_year",
    category=CATEGORY,
    question="What hypotheticality is recorded for claims about effects, by claim-source year?",
    notes=(
        "Use for requests such as "
        "'Show hypothetical causal claims by source year'; "
        "'How are claims about effects characterized across publication years?'; "
        "'List causes, effects, and hypotheticality flags by year'. "
        "OPTIONAL INPUT: `effect_key`, an exact normalized key of the effect "
        "from ne_find_entity; omit or use an empty string for all effects. "
        "If the user names a specific effect, resolve its key first. "
        "Limit defaults to 1000 result rows. "
        "Returns ?pub (claim-source IRI), ?effectKey, ?year (source year), "
        "?doi (optional source DOI), ?causeKey, and ?hypothetical. "
        "Source attribution comes from the causal claim, not from the "
        "cause or effect entity. Hypotheticality comes from the claim's "
        "current causal-relation version. "
        "True records a hypothetical claim; false does not independently "
        "establish experimental intervention, truth, or scientific consensus. "
        "Years are derived from ner:publicationDate or dcterms:issued. "
        "Sources without either date property are omitted. If a date "
        "cannot be converted to an integer year, its row can remain with "
        "?year unbound. Sources without DOIs remain visible. "
        "Source years are not claim-revision timestamps; this query does "
        "not reconstruct changes to a claim over time. "
        "Multiple dates or separate claims can produce multiple rows. "
        "Identical displayed rows do not necessarily represent duplicate claims. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no records match the required claim, "
        "entity-key, hypotheticality, and source-date patterns."
    ),
    example={"effect_key": "", "limit": 1000},
    sparql="""
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX dcterms: <http://purl.org/dc/terms/>

SELECT ?pub ?effectKey ?year ?doi ?causeKey ?hypothetical
WHERE {
  VALUES ?requestedKey { {{effect_key}} }

  GRAPH {{graph}} {
    ?rel ner:hasEffect/ner:normalizedEntityKey ?effectKey ;
         ner:hasCause ?c ;
         ner:hasCurrentCausalRelationVersion/
         ner:causalHypothetical ?hypothetical .

    ?c ner:normalizedEntityKey ?causeKey .
    ?rel prov:hadPrimarySource ?pub .

    OPTIONAL { ?pub ner:doi ?doi }

    ?pub (ner:publicationDate|dcterms:issued) ?date .
    BIND(xsd:integer(SUBSTR(STR(?date), 1, 4)) AS ?year)
  }

  FILTER(?requestedKey = "" || STR(?effectKey) = ?requestedKey)
}
ORDER BY ?year
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "effect_key",
            "string",
            "Optional exact effect key; omit or use an empty string for all effects.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of claim-source-year rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ15
################################################################################

################################################################################
# Optional user parameter query — result limit only
# CQ16 — Validity intervals for causal-claim versions
################################################################################

register(QAQuery(
    id="ne_causal_claim_validity_intervals",
    category=CATEGORY,
    question="For how long was each version of a claim considered current, according to its recorded validity interval?",
    notes=(
        "Use for requests such as "
        "'Show claim-version validity intervals'; "
        "'When was each claim version considered current?'; "
        "'List claim revisions with their start and end dates'; "
        "'Show the recorded validity history of causal claims'. "
        "No input is required; limit defaults to 1000 result rows. "
        "Returns ?rel (causal-relation IRI), ?rev (revision number), "
        "?from (optional valid-from value), and "
        "?until (optional valid-until value). "
        "Includes recorded versions with a parent claim and revision number, "
        "including historical versions. Versions without either interval "
        "endpoint remain visible with the corresponding field unbound. "
        "A missing valid-until value does not by itself establish that "
        "a version is current; the endpoint may simply be absent. "
        "Use hasCurrentCausalRelationVersion to identify an explicitly "
        "designated current version. "
        "These are recorded version-validity intervals, not publication "
        "dates or proof that a scientific claim was true during that period. "
        "The query returns interval endpoints; it does not calculate duration. "
        "Results are ordered by claim IRI and revision number. "
        "The limit applies to result rows and may truncate a claim's history. "
        "The query covers all qualifying versions in "
        "the named graph given by `graph`. "
        "A specific claim requires an additional claim-IRI filter. "
        "An empty result means no version records match the required "
        "type, parent-claim, and revision-number patterns."
    ),
    example={"limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>

SELECT ?rel ?rev ?from ?until
WHERE {
  GRAPH {{graph}} {
    ?v a ner:CausalRelationVersion ;
       ner:versionOfCausalRelation ?rel ;
       ner:relationRevisionNumber ?rev .

    OPTIONAL { ?v ner:validFrom ?from }
    OPTIONAL { ?v ner:validUntil ?until }
  }
}
ORDER BY ?rel ?rev
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "limit",
            "int",
            "Maximum number of claim-version interval rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ16
################################################################################
################################################################################
# Optional user parameter query
# CQ17 (doc CQ18) — Dated sources per year for entities mapped to a term
################################################################################

register(QAQuery(
    id="ne_term_sources_by_year",
    category=CATEGORY,
    question="How many dated sources per year mention entities mapped exactly or closely to an ontology term?",
    notes=(
        "Use for requests such as 'How has mention of this ontology term "
        "changed over time?'; 'How many papers per year mention entities "
        "mapped to UBERON_0001954?'; 'Show the yearly trend for each mapped term'. "
        "OPTIONAL INPUT: `term_iri`, the full external term IRI (e.g. "
        "http://purl.obolibrary.org/obo/UBERON_0001954), matched exactly as a "
        "string. Omit it or use an empty string for every mapped term. "
        "Get term IRIs from ne_entity_external_mappings (?obo) or "
        "ne_entities_by_ontology_tier; do not build one from a CURIE unless the "
        "IRI form is certain. Limit defaults to 1000 rows. "
        "Returns ?term (mapped term IRI), ?year (integer source year) and "
        "?papers (distinct sources in that year mentioning an entity with an "
        "exactMatch or closeMatch to the term). "
        "Broad, narrow and related mappings are excluded. Years come from the "
        "first four characters of ner:publicationDate or dcterms:issued; "
        "undated sources and unparseable dates are excluded. A source with dates "
        "in different years is counted in each. Years with no sources are "
        "omitted rather than shown as zero. Entity provenance shows where the "
        "entity occurs, not that the source asserted the mapping. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no dated source has an entity mapped to the term."
    ),
    example={"term_iri": "", "limit": 1000},
    sparql="""
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
PREFIX ner: <https://brainkb.org/ner/>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX dcterms: <http://purl.org/dc/terms/>

SELECT ?term ?year (COUNT(DISTINCT ?pub) AS ?papers)
WHERE {
  VALUES ?requestedTerm { {{term_iri}} }

  GRAPH {{graph}} {
    ?e (skos:exactMatch|skos:closeMatch) ?term ;
       prov:hadPrimarySource ?pub .

    ?pub (ner:publicationDate|dcterms:issued) ?date .

    BIND(xsd:integer(SUBSTR(STR(?date), 1, 4)) AS ?year)
    FILTER(BOUND(?year))
  }

  FILTER(?requestedTerm = "" || STR(?term) = ?requestedTerm)
}
GROUP BY ?term ?year
ORDER BY ?term ?year
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "term_iri",
            "string",
            "Optional full external term IRI; omit or use an empty string for all mapped terms.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of term-year rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ17
################################################################################

################################################################################
# Optional user parameter query — result limit only
# CQ18 (doc CQ21) — Extraction runs, agents, model versions and configurations
################################################################################

register(QAQuery(
    id="ne_extraction_runs",
    category=CATEGORY,
    question="Which extraction runs produced the data, when, by which agent and model version, with which configuration?",
    notes=(
        "Use for requests such as 'Who or what extracted these entities?'; "
        "'Which model version was used?'; 'When did the extraction runs happen?'; "
        "'Which configuration hash did each run use?'. "
        "No input is required; limit defaults to 1000 rows. "
        "Returns ?run (activity IRI), ?runId (run identifier), ?agent "
        "(optional associated agent IRI), ?agentVersion (optional), ?started and "
        "?ended (optional timestamps), ?cfg (optional configuration artifact) and "
        "?hash (optional configuration hash). "
        "Only ner:NERExtractionActivity records with a run identifier are "
        "returned. A run with several agents or configurations produces "
        "several rows. Missing timestamps or configuration fields mean the "
        "export did not record them, not that the run had none. "
        "To see which run produced a particular mention, use "
        "ne_entity_provenance_walk. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no extraction-activity records are loaded; "
        "compact exports may omit them."
    ),
    example={"limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT DISTINCT ?run ?runId ?agent ?agentVersion ?started ?ended ?cfg ?hash
WHERE {
  GRAPH {{graph}} {
    ?run a ner:NERExtractionActivity ;
         ner:runIdentifier ?runId .

    OPTIONAL { ?run prov:startedAtTime ?started }
    OPTIONAL { ?run prov:endedAtTime ?ended }
    OPTIONAL {
      ?run prov:wasAssociatedWith ?agent .
      OPTIONAL { ?agent ner:agentVersion ?agentVersion }
    }
    OPTIONAL {
      ?run prov:used ?cfg .
      ?cfg a ner:ConfigurationArtifact ;
           ner:configurationHash ?hash .
    }
  }
}
ORDER BY ?started ?runId ?run
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "limit",
            "int",
            "Maximum number of run rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ18
################################################################################

################################################################################
# Optional user parameter query
# CQ19 (doc CQ22) — Provenance walk: mention → document → run → agent
################################################################################

register(QAQuery(
    id="ne_entity_provenance_walk",
    category=CATEGORY,
    question="What is the full provenance of an entity's mentions: verbatim text, source document, extraction run and agent?",
    notes=(
        "Use for requests such as 'Where did this entity come from?'; "
        "'Which run and model extracted this mention?'; 'Trace this entity back "
        "to its source text'. "
        "OPTIONAL INPUT: `entity_key`, an exact normalized entity key from "
        "ne_find_entity. Omit it or use an empty string for all entities (large). "
        "If the user names a specific entity, resolve its key first; do not "
        "silently run the unfiltered query instead. Limit defaults to 1000 rows. "
        "Returns ?e (entity IRI), ?key, ?m (mention IRI), ?surface (verbatim "
        "text), ?dv (document version IRI), ?pub (source IRI), ?doi (optional), "
        "?runId and ?agentLabel (optional). "
        "The run is linked only through the mention's current annotation "
        "version and its snapshot. When that link is missing (common in compact "
        "exports), ?runId and ?agentLabel stay blank: a run that merely processed "
        "the same document is not evidence that it extracted this mention. "
        "Separate IRIs sharing a key are all returned; compare ?e values. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no mentions with document links match the key."
    ),
    example={"entity_key": "hippocampus", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT DISTINCT ?e ?key ?m ?surface ?dv ?pub ?doi ?runId ?agentLabel
WHERE {
  VALUES ?requestedKey { {{entity_key}} }

  GRAPH {{graph}} {
    ?e ner:normalizedEntityKey ?key ;
       ner:hasMention ?m .

    ?m ner:surfaceForm ?surface ;
       ner:partOfDocumentVersion ?dv .

    ?dv ner:versionOfDocument ?pub .

    OPTIONAL { ?pub ner:doi ?doi }
    OPTIONAL {
      ?m ner:hasCurrentAnnotationVersion/ner:inSnapshot ?snapshot .
      ?snapshot prov:wasGeneratedBy ?run .
      ?run ner:runIdentifier ?runId .
      OPTIONAL { ?run prov:wasAssociatedWith/rdfs:label ?agentLabel }
    }
  }

  FILTER(?requestedKey = "" || STR(?key) = ?requestedKey)
}
ORDER BY ?key ?e ?pub ?m
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "entity_key",
            "string",
            "Optional exact normalized entity key; omit or use an empty string for all entities.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of provenance rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ19
################################################################################

################################################################################
# Optional user parameter query
# CQ20 (doc CQ23) — Classification confidence per mention (full audit profile)
################################################################################

register(QAQuery(
    id="ne_mention_classification_confidence",
    category=CATEGORY,
    question="What classification confidence is recorded for each entity mention?",
    notes=(
        "Use for requests such as 'How confident was the classifier about each "
        "mention?'; 'Which mentions have low classification confidence?'; "
        "'Show confidence for this entity's mentions'. "
        "OPTIONAL INPUT: `entity_key`, an exact normalized key from "
        "ne_find_entity; omit or use an empty string for all mentions. "
        "Limit defaults to 1000 rows. "
        "Returns ?m (mention IRI), ?key (optional owning entity key), "
        "?surface (verbatim text) and ?confidence, highest confidence first. "
        "For low-confidence mentions, reorder or filter the returned rows. "
        "Confidence comes from the classification on the mention's current "
        "annotation version; it is a model score, not a validated probability "
        "that the mention is correct. "
        "Requires full-profile audit records; compact exports omit "
        "classifications, so an empty result usually means those records "
        "are absent, not that confidence was zero. "
        "The query is scoped to the named graph given by `graph`."
    ),
    example={"entity_key": "", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>

SELECT DISTINCT ?m ?key ?surface ?confidence
WHERE {
  VALUES ?requestedKey { {{entity_key}} }

  GRAPH {{graph}} {
    ?m a ner:EntityMention ;
       ner:surfaceForm ?surface ;
       ner:hasCurrentAnnotationVersion/
       ner:hasClassification/
       ner:classificationConfidence ?confidence .

    OPTIONAL { ?e ner:hasMention ?m ; ner:normalizedEntityKey ?key }
  }

  FILTER(?requestedKey = "" || (BOUND(?key) && STR(?key) = ?requestedKey))
}
ORDER BY DESC(?confidence) ?surface ?m
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "entity_key",
            "string",
            "Optional exact normalized entity key; omit or use an empty string for all mentions.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of mention rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ20
################################################################################

################################################################################
# Optional user parameter query
# CQ21 (doc CQ24b) — Judge review decisions on mentions, per dimension
################################################################################

register(QAQuery(
    id="ne_mention_review_decisions",
    category=CATEGORY,
    question="What did each LLM or human judge decide about a mention, on which dimension and with what confidence, and what did the combiner conclude?",
    notes=(
        "Use for requests such as 'How was this mention reviewed?'; 'What did "
        "the judges say about this entity?'; 'Show the ensemble decision'; "
        "'Which mentions did the judges reject?'. "
        "OPTIONAL INPUTS: `entity_key` (exact normalized key from ne_find_entity) "
        "and `dimension` (exact review dimension, e.g. 'ensemble-combined' for "
        "the combiner's decision). Omit either or use an empty string for all. "
        "Do not guess other dimension names; run once without `dimension` to "
        "see which values exist. Limit defaults to 1000 rows. "
        "Returns ?m (mention IRI), ?key (optional owning entity key), ?surface, "
        "?dimension, ?status (review status), ?conf (optional confidence) and "
        "?agentVersion (optional model/agent version of the judge). "
        "Decisions are read from the mention's current annotation version. "
        "The combiner's decision uses dimension 'ensemble-combined' and is "
        "derived from the per-judge decisions (prov:wasDerivedFrom); per-judge "
        "rows and the combined row both appear. Entity-level verdicts are in "
        "ne_entity_review_verdicts. "
        "Requires full-profile audit records; compact exports omit reviews, so "
        "an empty result usually means those records are absent. "
        "The query is scoped to the named graph given by `graph`."
    ),
    example={"entity_key": "", "dimension": "", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT DISTINCT ?m ?key ?surface ?dimension ?status ?conf ?agentVersion
WHERE {
  VALUES (?requestedKey ?requestedDimension) { ({{entity_key}} {{dimension}}) }

  GRAPH {{graph}} {
    ?m ner:surfaceForm ?surface ;
       ner:hasCurrentAnnotationVersion/ner:hasReviewDecision ?rd .

    ?rd ner:reviewDimension ?dimension ;
        ner:reviewStatus ?status .

    OPTIONAL { ?rd ner:reviewConfidence ?conf }
    OPTIONAL {
      ?rd prov:wasGeneratedBy/prov:wasAssociatedWith/ner:agentVersion ?agentVersion
    }
    OPTIONAL { ?e ner:hasMention ?m ; ner:normalizedEntityKey ?key }
  }

  FILTER(?requestedKey = "" || (BOUND(?key) && STR(?key) = ?requestedKey))
  FILTER(?requestedDimension = "" || STR(?dimension) = ?requestedDimension)
}
ORDER BY ?surface ?m ?dimension
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "entity_key",
            "string",
            "Optional exact normalized entity key; omit or use an empty string for all mentions.",
            default="",
        ),
        QAParam(
            "dimension",
            "string",
            "Optional exact review dimension, e.g. 'ensemble-combined'; omit or use an empty string for all.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of review-decision rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ21
################################################################################

################################################################################
# Optional user parameter query
# CQ22 (doc CQ24) — Per-source extraction density
################################################################################

register(QAQuery(
    id="ne_source_extraction_density",
    category=CATEGORY,
    question="How many entities and mentions were extracted from each paper or source?",
    notes=(
        "Use for requests such as 'How many entities were extracted per paper?'; "
        "'Which papers have the most mentions?'; 'How dense is the extraction "
        "for this paper?'. "
        "OPTIONAL INPUT: `doi`, an exact DOI as stored in the graph (from "
        "ne_list_sources). Omit it or use an empty string for all sources, "
        "including sources without DOIs. If the user names a specific paper, "
        "resolve its DOI first; do not silently return every source. "
        "Limit defaults to 1000 rows. "
        "Returns ?pub (source IRI), ?doi (optional), ?entities (distinct entity "
        "IRIs with at least one mention in the source) and ?mentions (distinct "
        "mention IRIs in the source). "
        "Counts attribute each mention through its document version, so a "
        "shared entity is counted only in sources where it has a mention. "
        "Counts reflect extraction output, not document length or importance; "
        "compare densities with care. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no mentions link to a matching source."
    ),
    example={"doi": "", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>

SELECT ?pub ?doi
       (COUNT(DISTINCT ?e) AS ?entities)
       (COUNT(DISTINCT ?m) AS ?mentions)
WHERE {
  VALUES ?requestedDoi { {{doi}} }

  GRAPH {{graph}} {
    ?e a ner:NamedEntity ;
       ner:hasMention ?m .

    ?m ner:partOfDocumentVersion/ner:versionOfDocument ?pub .

    OPTIONAL { ?pub ner:doi ?doi }
  }

  FILTER(
    ?requestedDoi = "" ||
    (BOUND(?doi) && STR(?doi) = STR(?requestedDoi))
  )
}
GROUP BY ?pub ?doi
ORDER BY DESC(?mentions) ?pub
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "doi",
            "string",
            "Optional exact paper DOI; omit or use an empty string for all sources.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of source rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ22
################################################################################

################################################################################
# User parameter query — entity key required
# CQ23 (doc CQ26r) — Neighborhood of an entity, by normalized key
################################################################################

register(QAQuery(
    id="ne_entity_neighborhood",
    category=CATEGORY,
    question="Which entities or nodes are linked to a given entity, in either direction, and via which predicates?",
    notes=(
        "REQUIRES INPUT: `entity_key`, an exact normalized entity key. If the "
        "user gives a name, run ne_find_entity first and copy ?key; ask the "
        "user when several candidates fit. Do not guess a key. "
        "Relevant requests include: 'What is this entity connected to?'; "
        "'Show everything linked to the hippocampus'; 'Which relations does "
        "this gene have?'. To start from an IRI instead (an entity IRI, or an "
        "external ontology term such as an OBO or BKE IRI), use "
        "ne_node_neighborhood. Limit defaults to 500 rows. "
        "Returns ?x (anchor entity IRI), ?key, ?direction ('out' when the anchor "
        "is the subject, 'in' when it is the object), ?predicate, ?other (linked "
        "IRI), ?otherKey (optional normalized key, when ?other is an entity) and "
        "?otherLabel (optional rdfs:label). "
        "Only IRI-valued neighbors are returned; rdf:type and literal "
        "properties are excluded. Neighbors include mentions, mapping targets, "
        "provenance nodes and claim nodes as well as other entities; use "
        "?predicate to tell them apart. Separate IRIs sharing the key are all "
        "anchored; compare ?x values. Materialized direct edges aggregate across "
        "sources and cannot show which source stated a relationship or whether "
        "it was negated; use the RelationAssertion queries (e.g. "
        "ne_cell_type_region_assertions) for source-level evidence. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no entity has that exact key."
    ),
    example={"entity_key": "hippocampus", "limit": 500},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT DISTINCT ?x ?key ?direction ?predicate ?other ?otherKey ?otherLabel
WHERE {
  GRAPH {{graph}} {
    ?x ner:normalizedEntityKey {{entity_key}} .
    BIND({{entity_key}} AS ?key)

    {
      { ?x ?predicate ?other . BIND("out" AS ?direction) }
      UNION
      { ?other ?predicate ?x . BIND("in" AS ?direction) }
    }

    FILTER(isIRI(?other))
    FILTER(?predicate != rdf:type)

    OPTIONAL { ?other ner:normalizedEntityKey ?otherKey }
    OPTIONAL { ?other rdfs:label ?otherLabel }
  }
}
ORDER BY ?x ?direction ?predicate ?other
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "entity_key",
            "string",
            "Required exact normalized entity key from ne_find_entity, e.g. 'hippocampus'.",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of neighbor rows to return.",
            default=500,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ23
################################################################################

################################################################################
# User parameter query — node IRI required
# CQ24 (doc CQ26r, IRI anchor) — Neighborhood of any node, by IRI
################################################################################

register(QAQuery(
    id="ne_node_neighborhood",
    category=CATEGORY,
    question="Which nodes are linked to a given IRI (an entity, or an external ontology term), in either direction, and via which predicates?",
    notes=(
        "REQUIRES INPUT: `node_iri`, a full absolute IRI. Use an entity IRI "
        "(?e from ne_find_entity) to inspect one specific entity, or an external "
        "ontology term IRI (e.g. http://purl.obolibrary.org/obo/UBERON_0001954, "
        "a BKE taxonomy IRI, or ?obo from ne_entity_external_mappings) to list "
        "every entity, from any source, that links to that concept. "
        "Relevant requests include: 'Which entities are mapped to this ontology "
        "term?'; 'What links to this IRI?'; 'Show this node's connections'. "
        "Do not pass a CURIE such as UBERON:0001954; expand it to the full IRI. "
        "For lookup by normalized key, use ne_entity_neighborhood. "
        "Limit defaults to 500 rows. "
        "Returns ?direction ('out' when the given IRI is the subject, 'in' when "
        "it is the object), ?predicate, ?other (linked IRI), ?otherKey "
        "(optional normalized key) and ?otherLabel (optional rdfs:label). "
        "Only IRI-valued neighbors are returned; rdf:type is excluded. "
        "Mapping predicates (skos:exactMatch, closeMatch, broadMatch, ...) are "
        "tiers, not all identity claims. Direct edges aggregate across sources "
        "and carry no negation or source attribution. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means nothing in that graph links to or from the IRI."
    ),
    example={"node_iri": "http://purl.obolibrary.org/obo/UBERON_0001954", "limit": 500},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT DISTINCT ?direction ?predicate ?other ?otherKey ?otherLabel
WHERE {
  GRAPH {{graph}} {
    {
      { {{node_iri}} ?predicate ?other . BIND("out" AS ?direction) }
      UNION
      { ?other ?predicate {{node_iri}} . BIND("in" AS ?direction) }
    }

    FILTER(isIRI(?other))
    FILTER(?predicate != rdf:type)

    OPTIONAL { ?other ner:normalizedEntityKey ?otherKey }
    OPTIONAL { ?other rdfs:label ?otherLabel }
  }
}
ORDER BY ?direction ?predicate ?other
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "node_iri",
            "iri",
            "Required full IRI of an entity or ontology term, e.g. http://purl.obolibrary.org/obo/UBERON_0001954.",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of neighbor rows to return.",
            default=500,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ24
################################################################################

################################################################################
# Optional user parameter query
# CQ25 (doc CQ25) — Cell types located in brain regions, per source assertion
################################################################################

register(QAQuery(
    id="ne_cell_type_region_assertions",
    category=CATEGORY,
    question="Which cell types are located in which brain regions, according to which papers, with what evidence?",
    notes=(
        "Use for requests such as 'Which cell types are in the hippocampus?'; "
        "'Where is this neuron type located?'; 'Which papers say PV "
        "interneurons are in CA1?'. "
        "OPTIONAL INPUTS: `cell_key` and `region_key`, exact normalized keys "
        "from ne_find_entity. Supply region_key for 'what cells are in X', "
        "cell_key for 'where is cell Y', both to check one pair; omit or use "
        "empty strings for all. Limit defaults to 1000 rows. "
        "Returns ?cell (cell-type key), ?region (region key), ?pub (source IRI "
        "of the assertion), ?doi (optional), ?negated, ?modality, ?context and "
        "?evidence (optional verbatim evidence text). "
        "Reads source-level RelationAssertion records with predicate "
        "RO:0001025 (located in), so every row carries the stating source and "
        "its qualifiers. Rows with ?negated true state that the cell is NOT in "
        "the region; report them as such, never as support. Modality and context "
        "(e.g. species, condition) restrict the claim and should be reported. "
        "Cell types cover ner:CellType and all its subclasses in ontology 2.5.0. "
        "Direct located-in edges without assertion records are not returned; "
        "missing assertion evidence cannot be reconstructed from a direct edge. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no matching assertions, not that the cell is "
        "absent from the region."
    ),
    example={"cell_key": "", "region_key": "hippocampus", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX obo: <http://purl.obolibrary.org/obo/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT DISTINCT ?cell ?region ?pub ?doi ?negated ?modality ?context ?evidence
WHERE {
  VALUES (?requestedCell ?requestedRegion) { ({{cell_key}} {{region_key}}) }

  GRAPH {{graph}} {
    # every subclass of ner:CellType in named_entity_ontology.owl 2.5.0 (generated)
    VALUES ?cellClass {
      ner:Astrocyte ner:CellSubtype ner:CellType ner:EpendymalCell ner:ExcitatoryNeuron
      ner:GlialCell ner:InhibitoryNeuron ner:Interneuron ner:Microglia ner:MotorNeuron
      ner:NeuralCellType ner:NeuralStemCell ner:Neuron ner:Oligodendrocyte
      ner:ProjectionNeuron ner:SensoryNeuron
    }

    ?c a ?cellClass ;
       ner:normalizedEntityKey ?cell .

    ?assertion a ner:RelationAssertion ;
               ner:assertionSubject ?c ;
               ner:assertionPredicate obo:RO_0001025 ;
               ner:assertionObject ?r ;
               prov:hadPrimarySource ?pub .

    ?r ner:normalizedEntityKey ?region .

    OPTIONAL { ?pub ner:doi ?doi }
    OPTIONAL { ?assertion ner:assertionNegated ?negated }
    OPTIONAL { ?assertion ner:assertionModality ?modality }
    OPTIONAL { ?assertion ner:assertionContext ?context }
    OPTIONAL { ?assertion ner:evidenceText ?evidence }
  }

  FILTER(?requestedCell = "" || STR(?cell) = ?requestedCell)
  FILTER(?requestedRegion = "" || STR(?region) = ?requestedRegion)
}
ORDER BY ?region ?cell ?pub
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "cell_key",
            "string",
            "Optional exact cell-type key; omit or use an empty string for all cell types.",
            default="",
        ),
        QAParam(
            "region_key",
            "string",
            "Optional exact brain-region key; omit or use an empty string for all regions.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of assertion rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ25
################################################################################

################################################################################
# Optional user parameter query
# CQ26 (doc CQ26) — Anatomical containment (BFO:0000050 part of)
################################################################################

register(QAQuery(
    id="ne_anatomical_part_of",
    category=CATEGORY,
    question="Which structures are stated to be part of which others (BFO part of), with their ontology terms where mapped?",
    notes=(
        "Use for requests such as 'What is CA1 part of?'; 'Which substructures "
        "does the hippocampus contain?'; 'Show anatomical containment'. "
        "OPTIONAL INPUTS: `part_key` (the contained structure) and `whole_key` "
        "(the containing structure), exact normalized keys from ne_find_entity. "
        "Use whole_key for 'what are the parts of X', part_key for 'what is Y "
        "part of'; omit or use empty strings for all. Limit defaults to 1000 rows. "
        "Returns ?part and ?whole (normalized keys) and ?partTerm / ?wholeTerm "
        "(optional resolved concept identifiers, e.g. UBERON CURIEs or "
        "provisional BRAINKB concepts). "
        "Only direct, materialized BFO:0000050 edges are returned; transitive "
        "containment is not computed. An entity with several resolved concepts "
        "produces several rows. Direct edges aggregate across sources and "
        "carry no source attribution or negation. Applies to any entity with "
        "part-of edges, not only brain regions. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no matching part-of edges were materialized."
    ),
    example={"part_key": "", "whole_key": "hippocampus", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX obo: <http://purl.obolibrary.org/obo/>

SELECT DISTINCT ?part ?whole ?partTerm ?wholeTerm
WHERE {
  VALUES (?requestedPart ?requestedWhole) { ({{part_key}} {{whole_key}}) }

  GRAPH {{graph}} {
    ?p obo:BFO_0000050 ?w ;
       ner:normalizedEntityKey ?part .

    ?w ner:normalizedEntityKey ?whole .

    OPTIONAL { ?p ner:resolvedToConcept/ner:conceptIdentifier ?partTerm }
    OPTIONAL { ?w ner:resolvedToConcept/ner:conceptIdentifier ?wholeTerm }
  }

  FILTER(?requestedPart = "" || STR(?part) = ?requestedPart)
  FILTER(?requestedWhole = "" || STR(?whole) = ?requestedWhole)
}
ORDER BY ?whole ?part
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "part_key",
            "string",
            "Optional exact key of the contained structure; omit or use an empty string for all.",
            default="",
        ),
        QAParam(
            "whole_key",
            "string",
            "Optional exact key of the containing structure; omit or use an empty string for all.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of part-whole rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ26
################################################################################

################################################################################
# Optional user parameter query
# CQ27 (doc CQ27) — Organisms, strains and life stages studied, per source
################################################################################

register(QAQuery(
    id="ne_organisms_per_source",
    category=CATEGORY,
    question="Which organisms, species, strains, genotypes and life stages were studied, per paper?",
    notes=(
        "Use for requests such as 'Which species were studied?'; 'What mouse "
        "strains does this paper use?'; 'Which developmental stages appear?'. "
        "OPTIONAL INPUT: `doi`, an exact DOI from ne_list_sources; omit or use "
        "an empty string for all sources. If the user names a paper, resolve "
        "its DOI first rather than returning everything. "
        "Limit defaults to 1000 rows. "
        "Returns ?pub (source IRI), ?doi (optional), ?kind (class IRI: Species, "
        "Strain, Genotype, Organism, LifeStage or DevelopmentalStage) and ?key "
        "(normalized entity key). "
        "Covers ner:OrganismEntity and its subclasses in ontology 2.5.0. "
        "Source attribution is entity provenance: the entity occurs in the "
        "source, which does not by itself prove it was the studied organism "
        "rather than, say, one cited from other work. An entity with several "
        "organism classes produces several rows. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no organism entities match."
    ),
    example={"doi": "", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT DISTINCT ?pub ?doi ?kind ?key
WHERE {
  VALUES ?requestedDoi { {{doi}} }

  GRAPH {{graph}} {
    # every subclass of ner:OrganismEntity in named_entity_ontology.owl 2.5.0 (generated)
    VALUES ?kind {
      ner:DevelopmentalStage ner:Genotype ner:LifeStage ner:Organism ner:Species
      ner:Strain
    }

    ?e a ?kind ;
       ner:normalizedEntityKey ?key ;
       prov:hadPrimarySource ?pub .

    OPTIONAL { ?pub ner:doi ?doi }
  }

  FILTER(
    ?requestedDoi = "" ||
    (BOUND(?doi) && STR(?doi) = STR(?requestedDoi))
  )
}
ORDER BY ?doi ?pub ?kind ?key
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "doi",
            "string",
            "Optional exact paper DOI; omit or use an empty string for all sources.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ27
################################################################################

################################################################################
# Optional user parameter query
# CQ28 (doc CQ28) — Elements carried by transgenic lines / genotypes
################################################################################

register(QAQuery(
    id="ne_line_carried_elements",
    category=CATEGORY,
    question="Which transgenic lines, strains or genotypes carry which indicator, effector or regulatory element?",
    notes=(
        "Use for requests such as 'What does the Pvalb-Cre line carry?'; "
        "'Which lines express GCaMP?'; 'Which reporters or effectors are in "
        "each genotype?'. "
        "OPTIONAL INPUTS: `line_key` (the line, strain, genotype or cell line) "
        "and `carries_key` (the carried element), exact normalized keys from "
        "ne_find_entity; omit or use empty strings for all. "
        "Limit defaults to 1000 rows. "
        "Returns ?line (line key), ?carries (carried element key) and "
        "?carriesClass (ner class of the carried element). "
        "Reads RO:0000057 (has participant) edges from entities typed "
        "ner:Genotype, ner:Strain or ner:CellLine. The predicate is generic: "
        "confirm from the class and source text whether the element is an "
        "indicator, effector or regulatory element. An element with several "
        "classes produces several rows. Direct edges aggregate across sources "
        "and carry no source attribution or negation. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no matching edges were materialized."
    ),
    example={"line_key": "", "carries_key": "", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX obo: <http://purl.obolibrary.org/obo/>

SELECT DISTINCT ?line ?carries ?carriesClass
WHERE {
  VALUES (?requestedLine ?requestedCarries) { ({{line_key}} {{carries_key}}) }

  GRAPH {{graph}} {
    VALUES ?lineClass { ner:Genotype ner:Strain ner:CellLine }

    ?l a ?lineClass ;
       ner:normalizedEntityKey ?line ;
       obo:RO_0000057 ?x .

    ?x ner:normalizedEntityKey ?carries ;
       a ?carriesClass .

    FILTER(
      STRSTARTS(STR(?carriesClass), STR(ner:)) &&
      ?carriesClass != ner:NamedEntity
    )
  }

  FILTER(?requestedLine = "" || STR(?line) = ?requestedLine)
  FILTER(?requestedCarries = "" || STR(?carries) = ?requestedCarries)
}
ORDER BY ?line ?carries ?carriesClass
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "line_key",
            "string",
            "Optional exact key of the line, strain or genotype; omit or use an empty string for all.",
            default="",
        ),
        QAParam(
            "carries_key",
            "string",
            "Optional exact key of the carried element; omit or use an empty string for all.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ28
################################################################################

################################################################################
# Optional user parameter query
# CQ29 (doc CQ29) — Participants linked to methods (RO:0000057)
################################################################################

register(QAQuery(
    id="ne_method_participants",
    category=CATEGORY,
    question="Which participants (e.g. reagents, subjects, targets) are explicitly linked to methods, assays or interventions?",
    notes=(
        "Use for requests such as 'What was used in the patch-clamp experiments?'; "
        "'Which drugs were part of this intervention?'; 'What participates in "
        "each method?'. "
        "OPTIONAL INPUTS: `method_key` and `participant_key`, exact normalized "
        "keys from ne_find_entity; omit or use empty strings for all. "
        "Limit defaults to 1000 rows. "
        "Returns ?method (method key) and ?participant (participant key). "
        "Methods cover ner:Method, ner:ImagingModality, "
        "ner:ElectrophysiologyModality, ner:Intervention and their subclasses in "
        "ontology 2.5.0 (assays, computational and statistical methods, "
        "stimulation, genetic, pharmacological and surgical interventions, ...). "
        "Reads RO:0000057 (has participant) direct edges only; the role of the "
        "participant (reagent, subject, target) is not recorded by this edge. "
        "For chemicals in assays with their broader classes, use "
        "ne_assay_chemical_participants. Direct edges aggregate across sources. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no matching edges were materialized."
    ),
    example={"method_key": "", "participant_key": "", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX obo: <http://purl.obolibrary.org/obo/>

SELECT DISTINCT ?method ?participant
WHERE {
  VALUES (?requestedMethod ?requestedParticipant) { ({{method_key}} {{participant_key}}) }

  GRAPH {{graph}} {
    # every subclass of ner:Method, ner:ImagingModality, ner:ElectrophysiologyModality, ner:Intervention in named_entity_ontology.owl 2.5.0 (generated)
    VALUES ?methodClass {
      ner:Assay ner:ComputationalMethod ner:ElectrophysiologyModality
      ner:ExperimentalMethod ner:GeneticIntervention ner:ImagingModality ner:Intervention
      ner:Method ner:NeuroimagingModality ner:NeuromodulationIntervention
      ner:PharmacologicalIntervention ner:StatisticalMethod ner:Stimulation
      ner:SurgicalIntervention
    }

    ?m a ?methodClass ;
       ner:normalizedEntityKey ?method ;
       obo:RO_0000057 ?x .

    ?x ner:normalizedEntityKey ?participant .
  }

  FILTER(?requestedMethod = "" || STR(?method) = ?requestedMethod)
  FILTER(?requestedParticipant = "" || STR(?participant) = ?requestedParticipant)
}
ORDER BY ?method ?participant
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "method_key",
            "string",
            "Optional exact method key; omit or use an empty string for all methods.",
            default="",
        ),
        QAParam(
            "participant_key",
            "string",
            "Optional exact participant key; omit or use an empty string for all participants.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ29
################################################################################

################################################################################
# Optional user parameter query
# CQ30 (doc CQ30) — Chemical participants of assays, with broader classes
################################################################################

register(QAQuery(
    id="ne_assay_chemical_participants",
    category=CATEGORY,
    question="Which chemicals (drugs, neurotransmitters, ions, ...) are linked to assays, and what broader classes are recorded for them?",
    notes=(
        "Use for requests such as 'Which compounds were applied in the assays?'; "
        "'What stimuli were used in each assay?'; 'Which drug classes appear in "
        "assays?'. "
        "OPTIONAL INPUTS: `assay_key` and `chemical_key`, exact normalized keys "
        "from ne_find_entity; omit or use empty strings for all. "
        "Limit defaults to 1000 rows. "
        "Returns ?assay (assay key), ?stimulus (chemical key) and ?class "
        "(optional key of a skos:broader parent recorded for the chemical, "
        "e.g. a drug class). "
        "Assays are entities typed ner:Assay; chemicals cover "
        "ner:ChemicalEntity and its subclasses in ontology 2.5.0. Reads "
        "RO:0000057 (has participant) direct edges; the edge does not say "
        "whether the chemical was a stimulus, reagent or control. Only direct "
        "broader parents are shown. Chemicals with several parents produce "
        "several rows. Direct edges aggregate across sources. "
        "For participants of all method types, use ne_method_participants. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no matching edges were materialized."
    ),
    example={"assay_key": "", "chemical_key": "", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX obo: <http://purl.obolibrary.org/obo/>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>

SELECT DISTINCT ?assay ?stimulus ?class
WHERE {
  VALUES (?requestedAssay ?requestedChemical) { ({{assay_key}} {{chemical_key}}) }

  GRAPH {{graph}} {
    # every subclass of ner:Assay in named_entity_ontology.owl 2.5.0 (generated)
    VALUES ?assayClass {
      ner:Assay
    }
    # every subclass of ner:ChemicalEntity in named_entity_ontology.owl 2.5.0 (generated)
    VALUES ?stimulusClass {
      ner:AminoAcid ner:Carbohydrate ner:ChemicalEntity ner:Drug ner:Hormone ner:Ion
      ner:Lipid ner:Metabolite ner:Neuromodulator ner:Neurotransmitter
      ner:NeurotransmitterEntity ner:TherapeuticAgent ner:Toxin
    }

    ?a a ?assayClass ;
       ner:normalizedEntityKey ?assay ;
       obo:RO_0000057 ?s .

    ?s a ?stimulusClass ;
       ner:normalizedEntityKey ?stimulus .

    OPTIONAL { ?s skos:broader/ner:normalizedEntityKey ?class }
  }

  FILTER(?requestedAssay = "" || STR(?assay) = ?requestedAssay)
  FILTER(?requestedChemical = "" || STR(?stimulus) = ?requestedChemical)
}
ORDER BY ?class ?stimulus ?assay
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "assay_key",
            "string",
            "Optional exact assay key; omit or use an empty string for all assays.",
            default="",
        ),
        QAParam(
            "chemical_key",
            "string",
            "Optional exact chemical key; omit or use an empty string for all chemicals.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ30
################################################################################

################################################################################
# Optional user parameter query
# CQ31 (doc CQ31) — Evidence basis of causal claims (intervention vs other)
################################################################################

register(QAQuery(
    id="ne_causal_evidence_basis",
    category=CATEGORY,
    question="Which effects are supported by an intervention in the paper itself, and which only by correlation, simulation or argument?",
    notes=(
        "Use for requests such as 'Which causal claims are backed by "
        "experiments?'; 'Which effects are only correlational?'; 'What still "
        "needs an experiment?'; 'What is the evidence basis for this claim?'. "
        "OPTIONAL INPUTS: `cause_key` and `effect_key`, exact normalized keys "
        "from ne_find_entity; omit or use empty strings for all claims. "
        "Limit defaults to 1000 rows. "
        "Returns ?rel (causal-relation IRI), ?causeKey, ?effectKey, "
        "?hypothetical, ?negated (optional), ?basis (optional evidence-basis "
        "name, e.g. intervention, correlation, simulation, argument — the local "
        "part of the causal-basis IRI) and ?evidence (optional comment). "
        "Values are read from each claim's current version. Only claims with a "
        "hypotheticality flag are returned; a missing ?basis means none was "
        "recorded, not that no evidence exists. To list only non-interventional "
        "claims, filter returned rows where ?basis is not 'intervention'. "
        "A claim with several bases produces several rows. Negated claims state "
        "the absence of an effect; report them as such. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no matching causal claims are recorded."
    ),
    example={"cause_key": "", "effect_key": "", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT DISTINCT ?rel ?causeKey ?effectKey ?hypothetical ?negated ?basis ?evidence
WHERE {
  VALUES (?requestedCause ?requestedEffect) { ({{cause_key}} {{effect_key}}) }

  GRAPH {{graph}} {
    ?rel ner:hasCause/ner:normalizedEntityKey ?causeKey ;
         ner:hasEffect/ner:normalizedEntityKey ?effectKey ;
         ner:hasCurrentCausalRelationVersion ?v .

    ?v ner:causalHypothetical ?hypothetical .

    OPTIONAL { ?v ner:causalNegated ?negated }
    OPTIONAL { ?v rdfs:comment ?evidence }
    OPTIONAL {
      ?v ner:causalEvidenceBasis ?b .
      BIND(STRAFTER(STR(?b), "causal-basis/") AS ?basis)
    }
  }

  FILTER(?requestedCause = "" || STR(?causeKey) = ?requestedCause)
  FILTER(?requestedEffect = "" || STR(?effectKey) = ?requestedEffect)
}
ORDER BY ?hypothetical ?basis ?causeKey ?effectKey ?rel
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "cause_key",
            "string",
            "Optional exact cause key; omit or use an empty string for all causes.",
            default="",
        ),
        QAParam(
            "effect_key",
            "string",
            "Optional exact effect key; omit or use an empty string for all effects.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of claim rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ31
################################################################################

################################################################################
# Optional user parameter query
# CQ32 (doc CQ32) — Effect estimates with p < 0.05
################################################################################

register(QAQuery(
    id="ne_significant_effect_estimates",
    category=CATEGORY,
    question="Which recorded effect estimates have p < 0.05, grouped by measure and ordered by absolute value?",
    notes=(
        "Use for requests such as 'Which effects are statistically significant?'; "
        "'Show the largest significant effects'; 'Which significant effects "
        "involve this entity?'. "
        "OPTIONAL INPUTS: `cause_key` and `effect_key`, exact normalized keys "
        "from ne_find_entity; omit or use empty strings for all. "
        "Limit defaults to 1000 rows. "
        "Returns ?causeKey, ?effectKey, ?measure, ?value, ?p and ?n (optional "
        "sample size), ordered by measure then descending absolute value. "
        "The threshold is fixed at p < 0.05; for a stricter threshold (e.g. "
        "0.01), filter the returned ?p values. For all estimates regardless of "
        "p, use ne_causal_effect_estimates. Estimates without a p-value are "
        "excluded. Reads the claim's current version only. "
        "Values across different measures are not comparable, and units, "
        "study designs and multiple-comparison corrections are not recorded "
        "here and still need review. Negation and hypotheticality are not "
        "selected; check them with ne_entity_causal_claims before reporting a "
        "significant estimate as an affirmative finding. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no matching estimate has a recorded p < 0.05."
    ),
    example={"cause_key": "", "effect_key": "", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>

SELECT ?causeKey ?effectKey ?measure ?value ?p ?n
WHERE {
  VALUES (?requestedCause ?requestedEffect) { ({{cause_key}} {{effect_key}}) }

  GRAPH {{graph}} {
    ?rel ner:hasCause/ner:normalizedEntityKey ?causeKey ;
         ner:hasEffect/ner:normalizedEntityKey ?effectKey ;
         ner:hasCurrentCausalRelationVersion/ner:hasEffectEstimate ?est .

    ?est ner:effectMeasure ?measure ;
         ner:effectValue ?value ;
         ner:pValue ?p .

    OPTIONAL { ?est ner:sampleSize ?n }

    FILTER(?p < 0.05)
  }

  FILTER(?requestedCause = "" || STR(?causeKey) = ?requestedCause)
  FILTER(?requestedEffect = "" || STR(?effectKey) = ?requestedEffect)
}
ORDER BY ?measure DESC(ABS(?value)) ?causeKey ?effectKey
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "cause_key",
            "string",
            "Optional exact cause key; omit or use an empty string for all causes.",
            default="",
        ),
        QAParam(
            "effect_key",
            "string",
            "Optional exact effect key; omit or use an empty string for all effects.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of estimate rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ32
################################################################################

################################################################################
# Optional user parameter query
# CQ33 (doc CQ33) — Mediators, moderators and confounders of an effect
################################################################################

register(QAQuery(
    id="ne_causal_third_variables",
    category=CATEGORY,
    question="What mediates, moderates or confounds a stated cause–effect relationship?",
    notes=(
        "Use for requests such as 'What mediates the effect of X on Y?'; "
        "'Which confounders are reported?'; 'What moderates this effect?'. "
        "OPTIONAL INPUTS: `cause_key`, `effect_key` (exact normalized keys from "
        "ne_find_entity) and `role` (exactly 'mediator', 'moderator' or "
        "'confounder'). Omit any or use empty strings for all. "
        "Limit defaults to 1000 rows. "
        "Returns ?causeKey, ?effectKey, ?role and ?third (key of the mediating, "
        "moderating or confounding entity). "
        "Only third variables explicitly recorded on a causal relation are "
        "returned; nothing is inferred from chains or co-occurrence. Negation, "
        "hypotheticality and source are not selected; use "
        "ne_entity_causal_claims for those flags. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no matching third variables were recorded."
    ),
    example={"cause_key": "", "effect_key": "", "role": "", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>

SELECT DISTINCT ?causeKey ?effectKey ?role ?third
WHERE {
  VALUES (?requestedCause ?requestedEffect ?requestedRole) {
    ({{cause_key}} {{effect_key}} {{role}})
  }

  GRAPH {{graph}} {
    ?rel ner:hasCause/ner:normalizedEntityKey ?causeKey ;
         ner:hasEffect/ner:normalizedEntityKey ?effectKey .

    {
      { ?rel ner:hasMediator ?t . BIND("mediator" AS ?role) }
      UNION
      { ?rel ner:hasModerator ?t . BIND("moderator" AS ?role) }
      UNION
      { ?rel ner:hasConfounder ?t . BIND("confounder" AS ?role) }
    }

    ?t ner:normalizedEntityKey ?third .
  }

  FILTER(?requestedCause = "" || STR(?causeKey) = ?requestedCause)
  FILTER(?requestedEffect = "" || STR(?effectKey) = ?requestedEffect)
  FILTER(?requestedRole = "" || ?role = LCASE(?requestedRole))
}
ORDER BY ?causeKey ?effectKey ?role ?third
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "cause_key",
            "string",
            "Optional exact cause key; omit or use an empty string for all causes.",
            default="",
        ),
        QAParam(
            "effect_key",
            "string",
            "Optional exact effect key; omit or use an empty string for all effects.",
            default="",
        ),
        QAParam(
            "role",
            "string",
            "Optional role: 'mediator', 'moderator' or 'confounder'; omit or use an empty string for all.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ33
################################################################################

################################################################################
# Optional user parameter query
# CQ34 (doc CQ34) — Cross-references (rdfs:seeAlso) and their mappings
################################################################################

register(QAQuery(
    id="ne_entity_cross_references",
    category=CATEGORY,
    question="Which entities have cross-references (rdfs:seeAlso) to other entities, and what ontology terms are they mapped to?",
    notes=(
        "Use for requests such as 'Which entities are cross-referenced?'; "
        "'What does this entity see-also?'; 'Show cross-species or cross-"
        "vocabulary counterparts recorded in the text'. "
        "OPTIONAL INPUT: `entity_key`, an exact normalized key from "
        "ne_find_entity, matched on either side of the cross-reference; omit or "
        "use an empty string for all. Limit defaults to 1000 rows. "
        "Returns ?a and ?b (keys of the referring and referenced entities) and "
        "?aTerm / ?bTerm (optional resolved concept identifiers). "
        "rdfs:seeAlso is a loose pointer: it is not evidence of homology, "
        "equivalence or any specific relationship by itself. Only references "
        "to named entities are returned. Entities with several resolved "
        "concepts produce several rows. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no matching cross-references were materialized."
    ),
    example={"entity_key": "", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT DISTINCT ?a ?aTerm ?b ?bTerm
WHERE {
  VALUES ?requestedKey { {{entity_key}} }

  GRAPH {{graph}} {
    ?x rdfs:seeAlso ?y ;
       ner:normalizedEntityKey ?a .

    ?y a ner:NamedEntity ;
       ner:normalizedEntityKey ?b .

    OPTIONAL { ?x ner:resolvedToConcept/ner:conceptIdentifier ?aTerm }
    OPTIONAL { ?y ner:resolvedToConcept/ner:conceptIdentifier ?bTerm }
  }

  FILTER(
    ?requestedKey = "" ||
    STR(?a) = ?requestedKey ||
    STR(?b) = ?requestedKey
  )
}
ORDER BY ?a ?b
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "entity_key",
            "string",
            "Optional exact entity key matched on either side; omit or use an empty string for all.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ34
################################################################################

################################################################################
# Optional user parameter query
# CQ35 (doc CQ35) — Derivation lineage of measures (prov:wasDerivedFrom+)
################################################################################

register(QAQuery(
    id="ne_measure_derivation_lineage",
    category=CATEGORY,
    question="How are the paper's measures derived: from which quantities or data is each measure computed?",
    notes=(
        "Use for requests such as 'How is this measure computed?'; 'What data "
        "is the firing rate derived from?'; 'Show the derivation lineage of "
        "each measurement'. "
        "OPTIONAL INPUT: `measure_key`, an exact normalized key of a measurement "
        "entity from ne_find_entity; omit or use an empty string for all. "
        "Limit defaults to 1000 rows. "
        "Returns ?measure (measurement key) and ?derivedFrom (key of an entity "
        "it is derived from, directly or transitively). "
        "Follows prov:wasDerivedFrom one or more steps, so all ancestors are "
        "listed, but the step count and intermediate order are not returned. "
        "Measures cover ner:Measurement and its subclasses in ontology 2.5.0 "
        "(electrophysiological, imaging, molecular). Only ancestors with a "
        "normalized key are shown. Direct edges aggregate across sources. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no lineage was recorded for matching measures."
    ),
    example={"measure_key": "", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT DISTINCT ?measure ?derivedFrom
WHERE {
  VALUES ?requestedMeasure { {{measure_key}} }

  GRAPH {{graph}} {
    # every subclass of ner:Measurement in named_entity_ontology.owl 2.5.0 (generated)
    VALUES ?measureClass {
      ner:ElectrophysiologicalMeasurement ner:ImagingMeasurement ner:Measurement
      ner:MolecularMeasurement
    }

    ?m a ?measureClass ;
       ner:normalizedEntityKey ?measure ;
       prov:wasDerivedFrom+ ?src .

    ?src ner:normalizedEntityKey ?derivedFrom .
  }

  FILTER(?requestedMeasure = "" || STR(?measure) = ?requestedMeasure)
}
ORDER BY ?measure ?derivedFrom
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "measure_key",
            "string",
            "Optional exact measurement key; omit or use an empty string for all measures.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of lineage rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ35
################################################################################

################################################################################
# Optional user parameter query
# CQ36 (doc CQ36) — Software and algorithm dependencies (prov:used)
################################################################################

register(QAQuery(
    id="ne_prov_used_dependencies",
    category=CATEGORY,
    question="Which software, algorithms, datasets or tools does each analysis or method depend on (prov:used)?",
    notes=(
        "Use for requests such as 'Which software was used?'; 'What does this "
        "analysis depend on?'; 'Which methods use Suite2p?'. "
        "OPTIONAL INPUT: `entity_key`, an exact normalized key from "
        "ne_find_entity, matched on either side (the user or the thing used); "
        "omit or use an empty string for all. Limit defaults to 1000 rows. "
        "Returns ?user (key of the entity that uses something), ?uses (key of "
        "the used entity) and ?usesClass (ner class of the used entity, e.g. "
        "Software or Algorithm). "
        "Reads direct prov:used edges between entities. Extraction runs using "
        "configuration artifacts are excluded because those lack entity keys. "
        "A used entity with several classes produces several rows. Direct "
        "edges aggregate across sources and carry no source attribution. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no matching prov:used edges were materialized."
    ),
    example={"entity_key": "", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT DISTINCT ?user ?uses ?usesClass
WHERE {
  VALUES ?requestedKey { {{entity_key}} }

  GRAPH {{graph}} {
    ?u prov:used ?x ;
       ner:normalizedEntityKey ?user .

    ?x ner:normalizedEntityKey ?uses ;
       a ?usesClass .

    FILTER(
      STRSTARTS(STR(?usesClass), STR(ner:)) &&
      ?usesClass != ner:NamedEntity
    )
  }

  FILTER(
    ?requestedKey = "" ||
    STR(?user) = ?requestedKey ||
    STR(?uses) = ?requestedKey
  )
}
ORDER BY ?uses ?user ?usesClass
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "entity_key",
            "string",
            "Optional exact entity key matched on either side; omit or use an empty string for all.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ36
################################################################################

################################################################################
# Optional user parameter query
# CQ37 (doc CQ37) — Direct broader relationships (skos:broader)
################################################################################

register(QAQuery(
    id="ne_broader_relationships",
    category=CATEGORY,
    question="Which direct broader (parent–child) relationships between entities are recorded?",
    notes=(
        "Use for requests such as 'What is the parent category of this entity?'; "
        "'Which entities are narrower than X?'; 'Show the direct hierarchy "
        "stated in the text'. "
        "OPTIONAL INPUTS: `child_key` and `parent_key`, exact normalized keys "
        "from ne_find_entity. Use parent_key for 'what are the children of X', "
        "child_key for 'what is Y a kind of'; omit or use empty strings for all. "
        "Limit defaults to 1000 rows. "
        "Returns ?child and ?parent (keys) and ?parentTerm (optional resolved "
        "concept identifier of the parent). "
        "Only one-step skos:broader edges are returned; for full paths to "
        "ancestors, use ne_broader_paths_to_ancestors. Broader edges come from "
        "the extracted text, not from the external ontology hierarchy, and "
        "aggregate across sources. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no matching broader edges were materialized."
    ),
    example={"child_key": "", "parent_key": "", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>

SELECT DISTINCT ?child ?parent ?parentTerm
WHERE {
  VALUES (?requestedChild ?requestedParent) { ({{child_key}} {{parent_key}}) }

  GRAPH {{graph}} {
    ?c skos:broader ?p ;
       ner:normalizedEntityKey ?child .

    ?p ner:normalizedEntityKey ?parent .

    OPTIONAL { ?p ner:resolvedToConcept/ner:conceptIdentifier ?parentTerm }
  }

  FILTER(?requestedChild = "" || STR(?child) = ?requestedChild)
  FILTER(?requestedParent = "" || STR(?parent) = ?requestedParent)
}
ORDER BY ?parent ?child
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "child_key",
            "string",
            "Optional exact child key; omit or use an empty string for all.",
            default="",
        ),
        QAParam(
            "parent_key",
            "string",
            "Optional exact parent key; omit or use an empty string for all.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ37
################################################################################

################################################################################
# Optional user parameter query — result limit only
# CQ38 (doc CQ38) — Unmapped entities ranked by mention count
################################################################################

register(QAQuery(
    id="ne_unmapped_entities_by_mentions",
    category=CATEGORY,
    question="Which entities without an external-ontology mapping are mentioned most often?",
    notes=(
        "Use for requests such as 'Which unmapped entities matter most?'; "
        "'Prioritize ontology curation by frequency'; 'Which frequently "
        "mentioned entities have only provisional BrainKB concepts?'. "
        "No input is required; limit defaults to 1000 rows. "
        "Returns ?e (entity IRI), ?cls (ner class), ?key and ?mentions "
        "(distinct mention count), most-mentioned first. "
        "Uses the same gap rule as ne_entities_without_external_mapping: an "
        "entity is excluded if any resolved concept belongs to an ontology "
        "other than BRAINKB; entities with only provisional BRAINKB concepts "
        "count as unmapped. Direct SKOS edges without concept metadata are not "
        "checked, so results are curation candidates, not proof of a gap. "
        "Entities with several classes appear once per class with the same "
        "count; the limit applies to rows. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means every mentioned entity has an external mapping."
    ),
    example={"limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>

SELECT ?e ?cls ?key (COUNT(DISTINCT ?m) AS ?mentions)
WHERE {
  GRAPH {{graph}} {
    ?e a ner:NamedEntity , ?cls ;
       ner:normalizedEntityKey ?key ;
       ner:hasMention ?m .

    FILTER(
      STRSTARTS(STR(?cls), STR(ner:)) &&
      ?cls != ner:NamedEntity
    )
  }

  MINUS {
    SELECT DISTINCT ?e
    WHERE {
      GRAPH {{graph}} {
        ?e ner:resolvedToConcept ?c .

        ?c ner:conceptInOntologyVersion/
           ner:versionOfOntology/
           ner:ontologyAcronym ?acronym .

        FILTER(UCASE(STR(?acronym)) != "BRAINKB")
      }
    }
  }
}
GROUP BY ?e ?cls ?key
ORDER BY DESC(?mentions) ?key ?e
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "limit",
            "int",
            "Maximum number of entity-class rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ38
################################################################################

################################################################################
# Optional user parameter query
# CQ39 (doc CQ39) — Cause/effect pairs recurring across sources
################################################################################

register(QAQuery(
    id="ne_causal_pairs_across_sources",
    category=CATEGORY,
    question="Which cause–effect pairs are stated by more than one source with the same polarity and negation, and is any of those claims non-hypothetical?",
    notes=(
        "Use for requests such as 'Which causal findings are replicated across "
        "papers?'; 'Which effects do several studies agree on?'; 'Is the "
        "effect of X on Y reported by more than one paper?'. "
        "OPTIONAL INPUTS: `cause_key` and `effect_key`, exact normalized keys "
        "from ne_find_entity; omit or use empty strings for all pairs. "
        "Limit defaults to 1000 rows. "
        "Returns ?causeKey, ?effectKey, ?polarity (optional, e.g. increases or "
        "decreases), ?negated, ?papers (distinct sources stating that "
        "combination) and ?anyNonHypothetical (1 if at least one of those "
        "claims is non-hypothetical, else 0). "
        "Groups claims by cause, effect, polarity and negation, using claim "
        "provenance and each claim's current version. Only groups stated by "
        "more than one source are returned. Recurrence is not independent "
        "replication: sources may share data, authors or cite each other. "
        "Claims without a polarity are grouped together under an unbound "
        "polarity. The same pair can appear in separate rows with opposite "
        "polarity or negation; that is a candidate for review, not proof of "
        "disagreement. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no matching combination recurs across sources."
    ),
    example={"cause_key": "", "effect_key": "", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT ?causeKey ?effectKey ?polarity ?negated
       (COUNT(DISTINCT ?pub) AS ?papers)
       (MAX(IF(?hyp = false, 1, 0)) AS ?anyNonHypothetical)
WHERE {
  VALUES (?requestedCause ?requestedEffect) { ({{cause_key}} {{effect_key}}) }

  GRAPH {{graph}} {
    ?rel ner:hasCause/ner:normalizedEntityKey ?causeKey ;
         ner:hasEffect/ner:normalizedEntityKey ?effectKey ;
         prov:hadPrimarySource ?pub ;
         ner:hasCurrentCausalRelationVersion ?v .

    ?v ner:causalHypothetical ?hyp ;
       ner:causalNegated ?negated .

    OPTIONAL { ?v ner:causalPolarity ?polarity }
  }

  FILTER(?requestedCause = "" || STR(?causeKey) = ?requestedCause)
  FILTER(?requestedEffect = "" || STR(?effectKey) = ?requestedEffect)
}
GROUP BY ?causeKey ?effectKey ?polarity ?negated
HAVING(COUNT(DISTINCT ?pub) > 1)
ORDER BY DESC(?papers) ?causeKey ?effectKey
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "cause_key",
            "string",
            "Optional exact cause key; omit or use an empty string for all causes.",
            default="",
        ),
        QAParam(
            "effect_key",
            "string",
            "Optional exact effect key; omit or use an empty string for all effects.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of grouped rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ39
################################################################################

################################################################################
# Optional user parameter query
# CQ40 (doc CQ40) — Participates-in relationships (RO:0000056)
################################################################################

register(QAQuery(
    id="ne_participates_in_relationships",
    category=CATEGORY,
    question="Which entities are explicitly stated to participate in which processes (RO participates in)?",
    notes=(
        "Use for requests such as 'Which processes does this gene take part in?'; "
        "'What participates in synaptic transmission?'; 'Show participates-in "
        "relationships'. "
        "OPTIONAL INPUTS: `entity_key` (the participant) and `process_key` (the "
        "process), exact normalized keys from ne_find_entity; omit or use empty "
        "strings for all. Limit defaults to 1000 rows. "
        "Returns ?entity (participant key) and ?process (process key). "
        "Reads direct RO:0000056 edges only. The inverse direction, has "
        "participant (RO:0000057), is covered by ne_method_participants and "
        "ne_line_carried_elements. Direct edges aggregate across sources and "
        "carry no source attribution or negation. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no matching edges were materialized."
    ),
    example={"entity_key": "", "process_key": "", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX obo: <http://purl.obolibrary.org/obo/>

SELECT DISTINCT ?entity ?process
WHERE {
  VALUES (?requestedEntity ?requestedProcess) { ({{entity_key}} {{process_key}}) }

  GRAPH {{graph}} {
    ?e obo:RO_0000056 ?p ;
       ner:normalizedEntityKey ?entity .

    ?p ner:normalizedEntityKey ?process .
  }

  FILTER(?requestedEntity = "" || STR(?entity) = ?requestedEntity)
  FILTER(?requestedProcess = "" || STR(?process) = ?requestedProcess)
}
ORDER BY ?entity ?process
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "entity_key",
            "string",
            "Optional exact participant key; omit or use an empty string for all.",
            default="",
        ),
        QAParam(
            "process_key",
            "string",
            "Optional exact process key; omit or use an empty string for all.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ40
################################################################################

################################################################################
# Optional user parameter query — result limit only
# CQ41 (doc CQ41) — Judge runs: model version, prompt hash and mode
################################################################################

register(QAQuery(
    id="ne_judge_runs",
    category=CATEGORY,
    question="Which judges ran, with which model version, which exact prompt (hash) and in which mode?",
    notes=(
        "Use for requests such as 'Which LLM judges reviewed the extraction?'; "
        "'Which prompt and model did each judge use?'; 'Is the review "
        "reproducible?'. "
        "No input is required; limit defaults to 1000 rows. "
        "Returns ?judge (activity label, beginning 'judge:'), ?model (optional "
        "agent version), ?prompt (optional prompt label), ?hash (optional prompt "
        "hash) and ?mode (optional comment describing the mode). "
        "Only ner:AutomaticValidationActivity records labelled 'judge:...' "
        "are returned. The prompt hash identifies the exact prompt text; "
        "identical hashes mean identical prompts. A judge run with several "
        "agents or prompts produces several rows. "
        "Requires full-profile audit records; compact exports omit them, so an "
        "empty result usually means those records are absent. "
        "The query is scoped to the named graph given by `graph`."
    ),
    example={"limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT DISTINCT ?judge ?model ?prompt ?hash ?mode
WHERE {
  GRAPH {{graph}} {
    ?act a ner:AutomaticValidationActivity ;
         rdfs:label ?judge ;
         prov:wasAssociatedWith ?ag .

    OPTIONAL { ?ag ner:agentVersion ?model }
    OPTIONAL {
      ?act ner:usedPrompt ?p .
      ?p rdfs:label ?prompt ;
         ner:promptHash ?hash .
    }
    OPTIONAL { ?act rdfs:comment ?mode }

    FILTER(STRSTARTS(STR(?judge), "judge:"))
  }
}
ORDER BY ?judge ?model
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "limit",
            "int",
            "Maximum number of judge rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ41
################################################################################

################################################################################
# Optional user parameter query
# CQ42 (doc CQ42) — Judge verdicts on entities
################################################################################

register(QAQuery(
    id="ne_entity_review_verdicts",
    category=CATEGORY,
    question="What verdict did each judge give on an entity, on which dimension, with what confidence and reason?",
    notes=(
        "Use for requests such as 'Did the judges accept this entity?'; 'Why "
        "was this entity rejected?'; 'Show entity-level review verdicts'. "
        "OPTIONAL INPUTS: `entity_key` (exact normalized key from ne_find_entity) "
        "and `dimension` (exact review dimension); omit either or use empty "
        "strings for all. Do not guess dimension names; run once without "
        "`dimension` to see which exist. Limit defaults to 1000 rows. "
        "Returns ?key, ?dimension, ?status (verdict), ?conf (optional "
        "confidence) and ?reason (optional judge comment). "
        "Reads review decisions attached directly to entities; mention-level "
        "decisions are in ne_mention_review_decisions. Confidence is the "
        "judge's own score, not a validated probability. "
        "Requires full-profile audit records; compact exports omit reviews, so "
        "an empty result usually means those records are absent. "
        "The query is scoped to the named graph given by `graph`."
    ),
    example={"entity_key": "", "dimension": "", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>

SELECT DISTINCT ?key ?dimension ?status ?conf ?reason
WHERE {
  VALUES (?requestedKey ?requestedDimension) { ({{entity_key}} {{dimension}}) }

  GRAPH {{graph}} {
    ?e ner:normalizedEntityKey ?key ;
       ner:hasReviewDecision ?r .

    ?r ner:reviewDimension ?dimension ;
       ner:reviewStatus ?status .

    OPTIONAL { ?r ner:reviewConfidence ?conf }
    OPTIONAL { ?r ner:reviewComment ?reason }
  }

  FILTER(?requestedKey = "" || STR(?key) = ?requestedKey)
  FILTER(?requestedDimension = "" || STR(?dimension) = ?requestedDimension)
}
ORDER BY ?key ?dimension
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "entity_key",
            "string",
            "Optional exact entity key; omit or use an empty string for all entities.",
            default="",
        ),
        QAParam(
            "dimension",
            "string",
            "Optional exact review dimension; omit or use an empty string for all.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of verdict rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ42
################################################################################

################################################################################
# Optional user parameter query
# CQ43 (doc CQ43) — Changes made by judges, and on whose authority
################################################################################

register(QAQuery(
    id="ne_judge_change_records",
    category=CATEGORY,
    question="What did the judges change (relabels, tier changes, key renames, demoted mappings, dropped items and claims), and on whose authority?",
    notes=(
        "Use for requests such as 'What did the review change?'; 'Which "
        "entities were relabelled?'; 'Which claims were removed and why?'; "
        "'Which judge licensed this change?'. "
        "OPTIONAL INPUTS: `entity_key` (exact normalized key of the changed "
        "entity) and `change_kind` (exact local class name, e.g. "
        "MappingRemovedChange, ClassificationChangedChange, "
        "CanonicalFormChangedChange, MentionRemovedChange, "
        "CausalRelationRemovedChange, SpecificityChangedChange). Omit either or "
        "use empty strings for all. Limit defaults to 1000 rows. "
        "Returns ?kind (change-record class IRI), ?key (optional changed-entity "
        "key), ?field (changed field), ?old / ?new (optional literal values), "
        "?reason (optional) and ?licensedBy (optional label of the review "
        "activity whose decision licensed the change). "
        "Covers ner:ChangeRecord and every subclass in ontology 2.5.0. A record "
        "typed with both a subclass and ner:ChangeRecord appears once per type. "
        "Changes to claims or mentions may have no entity key. With an "
        "entity_key filter, records without a changed entity are excluded. "
        "Requires full-profile audit records; compact exports omit changes, so "
        "an empty result usually means those records are absent. "
        "The query is scoped to the named graph given by `graph`."
    ),
    example={"entity_key": "", "change_kind": "", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT DISTINCT ?kind ?key ?field ?old ?new ?reason ?licensedBy
WHERE {
  VALUES (?requestedKey ?requestedKind) { ({{entity_key}} {{change_kind}}) }

  GRAPH {{graph}} {
    # every subclass of ner:ChangeRecord in named_entity_ontology.owl 2.5.0 (generated)
    VALUES ?kind {
      ner:CanonicalFormChangedChange ner:CausalConfidenceChangedChange
      ner:CausalContextChangedChange ner:CausalDirectionChangedChange
      ner:CausalModalityChangedChange ner:CausalPolarityChangedChange
      ner:CausalPredicateMappingChangedChange ner:CausalRelationAddedChange
      ner:CausalRelationChange ner:CausalRelationRemovedChange ner:CausalTypeChangedChange
      ner:ChangeRecord ner:ClassificationChangedChange ner:CoreferenceChangedChange
      ner:MappingAddedChange ner:MappingChangedChange ner:MappingDeprecatedChange
      ner:MappingRemovedChange ner:MentionAddedChange ner:MentionRemovedChange
      ner:OntologyConceptReplacedChange ner:ReviewStatusChangedChange
      ner:SpanChangedChange ner:SpecificityChangedChange
    }

    ?cr a ?kind ;
        ner:changedField ?field ;
        ner:triggeredByActivity ?act .

    OPTIONAL { ?cr ner:changedEntity/ner:normalizedEntityKey ?key }
    OPTIONAL { ?cr ner:oldLiteralValue ?old }
    OPTIONAL { ?cr ner:newLiteralValue ?new }
    OPTIONAL { ?cr ner:changeReason ?reason }
    OPTIONAL { ?cr ner:hasReviewDecision/prov:wasGeneratedBy/rdfs:label ?licensedBy }
  }

  FILTER(?requestedKey = "" || (BOUND(?key) && STR(?key) = ?requestedKey))
  FILTER(
    ?requestedKind = "" ||
    STRAFTER(STR(?kind), "https://brainkb.org/ner/") = ?requestedKind
  )
}
ORDER BY ?kind ?key ?field
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "entity_key",
            "string",
            "Optional exact key of the changed entity; omit or use an empty string for all.",
            default="",
        ),
        QAParam(
            "change_kind",
            "string",
            "Optional change-record class local name, e.g. 'MappingRemovedChange'; omit or use an empty string for all.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of change rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ43
################################################################################

################################################################################
# Optional user parameter query
# CQ44 (doc CQ44) — Mappings demoted by the mapping judge
################################################################################

register(QAQuery(
    id="ne_demoted_mappings",
    category=CATEGORY,
    question="Which tool-proposed ontology mappings did the mapping judge reject as meaning something else (IRI removed, entity kept)?",
    notes=(
        "Use for requests such as 'Which mappings were rejected?'; 'Why was "
        "this entity's ontology term removed?'; 'Show demoted mappings'. "
        "OPTIONAL INPUT: `entity_key`, an exact normalized key from "
        "ne_find_entity; omit or use an empty string for all. "
        "Limit defaults to 1000 rows. "
        "Returns ?key (entity key), ?removedIri (the rejected term IRI, as a "
        "literal) and ?reason (the judge's reason). "
        "Reads ner:MappingRemovedChange records linked to the entity either as "
        "changedEntity or through the entity's hasChangeRecord. The entity "
        "itself was kept; only the mapping was removed. A rejected IRI no "
        "longer appears in ne_entity_external_mappings. "
        "For all change types, use ne_judge_change_records. "
        "Requires full-profile audit records; compact exports omit changes, so "
        "an empty result usually means those records are absent. "
        "The query is scoped to the named graph given by `graph`."
    ),
    example={"entity_key": "", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>

SELECT DISTINCT ?key ?removedIri ?reason
WHERE {
  VALUES ?requestedKey { {{entity_key}} }

  GRAPH {{graph}} {
    ?cr a ner:MappingRemovedChange ;
        (ner:changedEntity|^ner:hasChangeRecord)/ner:normalizedEntityKey ?key ;
        ner:oldLiteralValue ?removedIri ;
        ner:changeReason ?reason .
  }

  FILTER(?requestedKey = "" || STR(?key) = ?requestedKey)
}
ORDER BY ?key ?removedIri
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "entity_key",
            "string",
            "Optional exact entity key; omit or use an empty string for all entities.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ44
################################################################################

################################################################################
# Optional user parameter query — result limit only
# CQ45 (doc CQ45) — Source and method behind accepted mappings
################################################################################

register(QAQuery(
    id="ne_accepted_mapping_sources",
    category=CATEGORY,
    question="Which mapping source (trusted ontology, local hybrid, BioPortal) produced the accepted mappings, and by which method?",
    notes=(
        "Use for requests such as 'Where did the accepted mappings come from?'; "
        "'How many mappings came from BioPortal?'; 'Which mapping methods were "
        "used?'. "
        "No input is required; limit defaults to 1000 rows. "
        "Returns ?source (label of the generating mapping activity), ?method "
        "(optional method name, the local part of the mapping-method IRI) and "
        "?decisions (distinct accepted mapping decisions). "
        "Counts only decisions with mapping status accepted. A decision "
        "generated by an activity with several methods is counted under each. "
        "Counts are decisions, not distinct entities or ontology terms. "
        "Requires full-profile mapping-decision records; compact exports omit "
        "them, so an empty result usually means those records are absent. "
        "The query is scoped to the named graph given by `graph`."
    ),
    example={"limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?source ?method (COUNT(DISTINCT ?dec) AS ?decisions)
WHERE {
  GRAPH {{graph}} {
    ?dec a ner:ConceptMappingDecision ;
         ner:mappingStatus <https://brainkb.org/ner/mapping-status/accepted> ;
         prov:wasGeneratedBy ?act .

    ?act rdfs:label ?source .

    OPTIONAL {
      ?act ner:usedMappingMethod ?m .
      BIND(STRAFTER(STR(?m), "mapping-method/") AS ?method)
    }
  }
}
GROUP BY ?source ?method
ORDER BY DESC(?decisions) ?source ?method
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "limit",
            "int",
            "Maximum number of source-method rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ45
################################################################################

################################################################################
# Optional user parameter query — result limit only
# CQ46 (doc CQ46) — Quality summary of each extraction snapshot
################################################################################

register(QAQuery(
    id="ne_snapshot_quality_summaries",
    category=CATEGORY,
    question="What is the quality summary of each extraction snapshot (what the ensemble dropped, demoted and fixed)?",
    notes=(
        "Use for requests such as 'Summarize the review of each extraction'; "
        "'What did the ensemble drop or fix?'; 'Show the validation reports'. "
        "No input is required; limit defaults to 1000 rows. "
        "Returns ?snapshot (snapshot IRI), ?label (optional report label), "
        "?summary (optional report comment text) and ?generated (optional "
        "snapshot timestamp). "
        "Summaries are free text written by the pipeline; for itemized changes "
        "use ne_judge_change_records. A snapshot with several reports produces "
        "several rows. "
        "Requires full-profile validation reports; compact exports omit them, "
        "so an empty result usually means those records are absent. "
        "The query is scoped to the named graph given by `graph`."
    ),
    example={"limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT DISTINCT ?snapshot ?label ?summary ?generated
WHERE {
  GRAPH {{graph}} {
    ?snapshot a ner:ExtractionSnapshot ;
              ner:hasValidationReport ?report .

    OPTIONAL { ?report rdfs:comment ?summary }
    OPTIONAL { ?report rdfs:label ?label }
    OPTIONAL { ?snapshot prov:generatedAtTime ?generated }
  }
}
ORDER BY ?generated ?snapshot
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "limit",
            "int",
            "Maximum number of snapshot rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ46
################################################################################

################################################################################
# Optional user parameter query
# CQ47 (doc CQ47) — Entities mapped to a selected ontology, by tier
################################################################################

register(QAQuery(
    id="ne_entities_by_ontology_tier",
    category=CATEGORY,
    question="Which entities map to terms in a selected ontology (e.g. UBERON, CL, BKE), at which mapping tier?",
    notes=(
        "Use for requests such as 'Which entities map to UBERON?'; 'Which "
        "entities map to BKE?'; 'Show the exact CL matches'; 'Which terms of "
        "this ontology are used?'. "
        "OPTIONAL INPUTS: `ontology_acronym` (e.g. UBERON, CL, CHEBI, BKE, "
        "BRAINKB; matched case-insensitively) and `tier` (exactly 'exact', "
        "'close', 'broad', 'narrow' or 'related'). Omit either or use empty "
        "strings for all. Use ne_ontology_coverage to see which acronyms exist; "
        "do not guess an acronym spelling. Limit defaults to 1000 rows. "
        "Returns ?e (entity IRI), ?key, ?acronym, ?curie (concept identifier), "
        "?conceptLabel (optional preferred label) and ?tier. "
        "Requires both the SKOS mapping edge and resolved-concept metadata "
        "linking the same term to its ontology; mappings lacking that metadata "
        "are not returned. Tiers are mapping relationships, not confidence "
        "scores, and not all are identity claims. A concept recorded under "
        "several ontology versions is returned once. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no entity maps to that ontology at that tier."
    ),
    example={"ontology_acronym": "UBERON", "tier": "", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT DISTINCT ?e ?key ?acronym ?curie ?conceptLabel ?tier
WHERE {
  VALUES (?requestedOntology ?requestedTier) { ({{ontology_acronym}} {{tier}}) }

  GRAPH {{graph}} {
    {
      SELECT DISTINCT ?e ?term ?tier
      WHERE {
        { ?e skos:exactMatch ?term . BIND("exact" AS ?tier) }
        UNION
        { ?e skos:closeMatch ?term . BIND("close" AS ?tier) }
        UNION
        { ?e skos:broadMatch ?term . BIND("broad" AS ?tier) }
        UNION
        { ?e skos:narrowMatch ?term . BIND("narrow" AS ?tier) }
        UNION
        { ?e skos:relatedMatch ?term . BIND("related" AS ?tier) }
      }
    }

    # StructSense stores conceptIRI as xsd:anyURI, while SKOS objects are IRIs.
    # Convert the bound SKOS object for an indexed lookup instead of a string cross-join.
    BIND(STRDT(STR(?term), xsd:anyURI) AS ?iri)

    ?c ner:conceptIRI ?iri ;
       ner:conceptIdentifier ?curie ;
       ner:conceptInOntologyVersion/
       ner:versionOfOntology/
       ner:ontologyAcronym ?acronym .

    ?e ner:normalizedEntityKey ?key ;
       ner:resolvedToConcept ?c .

    OPTIONAL { ?c ner:preferredLabel ?conceptLabel }
  }

  FILTER(?requestedOntology = "" || UCASE(STR(?acronym)) = UCASE(?requestedOntology))
  FILTER(?requestedTier = "" || ?tier = LCASE(?requestedTier))
}
ORDER BY ?acronym ?curie ?key
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "ontology_acronym",
            "string",
            "Optional ontology acronym, e.g. 'UBERON' or 'BKE' (case-insensitive); omit or use an empty string for all.",
            default="",
        ),
        QAParam(
            "tier",
            "string",
            "Optional tier: 'exact', 'close', 'broad', 'narrow' or 'related'; omit or use an empty string for all.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of mapping rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ47
################################################################################

################################################################################
# Optional user parameter query
# CQ48 (doc CQ48) — Per ontology term: mapped entities and source count
################################################################################

register(QAQuery(
    id="ne_ontology_term_entity_sources",
    category=CATEGORY,
    question="For each term of a selected ontology, which entities resolve to it and how many sources mention those entities?",
    notes=(
        "Use for requests such as 'Which UBERON terms are most widely "
        "represented?'; 'Which entities resolve to each CL term?'; 'Rank "
        "ontology terms by number of papers'. "
        "OPTIONAL INPUT: `ontology_acronym` (e.g. UBERON, CL, BKE, BRAINKB; "
        "matched case-insensitively); omit or use an empty string for all "
        "ontologies. Use ne_ontology_coverage to see which acronyms exist. "
        "Limit defaults to 1000 rows. "
        "Returns ?acronym, ?conceptType (concept identifier, e.g. a CURIE), "
        "?entities (comma-separated keys of entities resolved to it) and "
        "?papers (distinct sources in which those entities occur), most "
        "sources first. "
        "Uses resolvedToConcept, not SKOS tiers, so it includes provisional "
        "BRAINKB concepts unless filtered. Source counts come from entity "
        "provenance: they show where the entities occur, not that each source "
        "endorsed the mapping. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no entity resolves to a concept in that ontology."
    ),
    example={"ontology_acronym": "UBERON", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT ?acronym ?conceptType
       (GROUP_CONCAT(DISTINCT ?key; separator=", ") AS ?entities)
       (COUNT(DISTINCT ?pub) AS ?papers)
WHERE {
  VALUES ?requestedOntology { {{ontology_acronym}} }

  GRAPH {{graph}} {
    ?e ner:normalizedEntityKey ?key ;
       ner:resolvedToConcept ?c ;
       prov:hadPrimarySource ?pub .

    ?c ner:conceptIdentifier ?conceptType ;
       ner:conceptInOntologyVersion/
       ner:versionOfOntology/
       ner:ontologyAcronym ?acronym .
  }

  FILTER(?requestedOntology = "" || UCASE(STR(?acronym)) = UCASE(?requestedOntology))
}
GROUP BY ?acronym ?conceptType
ORDER BY DESC(?papers) ?acronym ?conceptType
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "ontology_acronym",
            "string",
            "Optional ontology acronym, e.g. 'UBERON' (case-insensitive); omit or use an empty string for all.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of term rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ48
################################################################################

################################################################################
# Optional user parameter query — result limit only
# CQ49 (doc CQ49) — Per-ontology coverage table
################################################################################

register(QAQuery(
    id="ne_ontology_coverage",
    category=CATEGORY,
    question="Which ontologies does the graph link into, and how many entities and concepts does each anchor?",
    notes=(
        "Use for requests such as 'Which ontologies are used?'; 'Show ontology "
        "coverage'; 'How many entities map to each ontology?'. Also run it to "
        "discover valid acronyms before passing `ontology_acronym` to "
        "ne_entities_by_ontology_tier or ne_ontology_term_entity_sources. "
        "No input is required; limit defaults to 1000 rows. "
        "Returns ?acronym, ?entities (distinct entities resolved to a concept in "
        "that ontology) and ?concepts (distinct concepts used), most entities "
        "first. "
        "BRAINKB (provisional local concepts) is included explicitly; treat it "
        "as unresolved external coverage. An entity resolved to concepts in "
        "several ontologies is counted in each, so entity counts do not sum to "
        "the number of entities. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no resolved concepts carry ontology metadata."
    ),
    example={"limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>

SELECT ?acronym
       (COUNT(DISTINCT ?e) AS ?entities)
       (COUNT(DISTINCT ?c) AS ?concepts)
WHERE {
  GRAPH {{graph}} {
    ?e ner:resolvedToConcept ?c .

    ?c ner:conceptInOntologyVersion/
       ner:versionOfOntology/
       ner:ontologyAcronym ?acronym .
  }
}
GROUP BY ?acronym
ORDER BY DESC(?entities) ?acronym
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "limit",
            "int",
            "Maximum number of ontology rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ49
################################################################################

################################################################################
# Optional user parameter query
# CQ50 (doc CQ50) — Phenotypes by level, per source
################################################################################

register(QAQuery(
    id="ne_phenotypes_by_level",
    category=CATEGORY,
    question="Which phenotypes does the corpus contain, at which level (behavioral, cognitive, electrophysiological, morphological, molecular, cellular, clinical/symptom), from which papers?",
    notes=(
        "Use for requests such as 'Which phenotypes are reported?'; 'List the "
        "behavioral phenotypes'; 'Which symptoms appear in the papers?'. "
        "OPTIONAL INPUT: `level`, the exact local class name: "
        "BehavioralPhenotype, CellularPhenotype, ClinicalPhenotype, "
        "CognitivePhenotype, ElectrophysiologicalPhenotype, MolecularPhenotype, "
        "MorphologicalPhenotype, MorphologyClass, NeurologicalSign, Phenotype or "
        "Symptom. Map the user's wording to one of these (e.g. 'behavioural' → "
        "BehavioralPhenotype, 'clinical signs' → NeurologicalSign or "
        "ClinicalPhenotype). Omit or use an empty string for all levels. "
        "Limit defaults to 1000 rows. "
        "Returns ?pub (optional source IRI), ?level (class IRI), ?key and ?doi "
        "(optional). "
        "Covers ner:Phenotype and its subclasses in ontology 2.5.0; matching "
        "'Phenotype' returns only entities typed with the generic class, not its "
        "subclasses. Source is entity provenance (where the phenotype occurs), "
        "not evidence for a phenotype association; use ne_phenotype_assertions "
        "for bearer–phenotype claims. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no phenotype entities match."
    ),
    example={"level": "", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT DISTINCT ?pub ?level ?key ?doi
WHERE {
  VALUES ?requestedLevel { {{level}} }

  GRAPH {{graph}} {
    # every subclass of ner:Phenotype in named_entity_ontology.owl 2.5.0 (generated)
    VALUES ?level {
      ner:BehavioralPhenotype ner:CellularPhenotype ner:ClinicalPhenotype
      ner:CognitivePhenotype ner:ElectrophysiologicalPhenotype ner:MolecularPhenotype
      ner:MorphologicalPhenotype ner:MorphologyClass ner:NeurologicalSign ner:Phenotype
      ner:Symptom
    }

    ?p a ?level ;
       ner:normalizedEntityKey ?key .

    OPTIONAL {
      ?p prov:hadPrimarySource ?pub .
      OPTIONAL { ?pub ner:doi ?doi }
    }
  }

  FILTER(
    ?requestedLevel = "" ||
    STRAFTER(STR(?level), "https://brainkb.org/ner/") = ?requestedLevel
  )
}
ORDER BY ?level ?key ?pub
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "level",
            "string",
            "Optional phenotype class local name, e.g. 'BehavioralPhenotype'; omit or use an empty string for all.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ50
################################################################################

################################################################################
# Optional user parameter query
# CQ51 (doc CQ51) — Bearer–phenotype assertions (RO:0002200 has phenotype)
################################################################################

register(QAQuery(
    id="ne_phenotype_assertions",
    category=CATEGORY,
    question="Which entities (genes, cell types, drugs, regions) bear which phenotypes, and in which papers was the association stated?",
    notes=(
        "Use for requests such as 'Which phenotypes are associated with this "
        "gene?'; 'Which genotypes show anxiety-like behavior?'; 'Where is this "
        "phenotype association stated?'. "
        "OPTIONAL INPUTS: `bearer_key` and `phenotype_key`, exact normalized "
        "keys from ne_find_entity; omit or use empty strings for all. "
        "Limit defaults to 1000 rows. "
        "Returns ?bearer, ?phenotype (keys), ?pub (assertion source IRI), ?doi "
        "(optional), ?evidence (optional verbatim text), ?negated, ?modality "
        "and ?context (optional qualifiers). "
        "Reads source-level RelationAssertion records with predicate "
        "RO:0002200 (has phenotype), so each row carries the stating source. "
        "Rows with ?negated true state the entity does NOT have the phenotype; "
        "report them as such. Context and modality (species, condition, "
        "hedging) restrict the claim. Direct edges without assertion records "
        "are not returned. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no matching assertions, not that no association exists."
    ),
    example={"bearer_key": "", "phenotype_key": "", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX obo: <http://purl.obolibrary.org/obo/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT DISTINCT ?bearer ?phenotype ?pub ?doi ?evidence ?negated ?modality ?context
WHERE {
  VALUES (?requestedBearer ?requestedPhenotype) { ({{bearer_key}} {{phenotype_key}}) }

  GRAPH {{graph}} {
    ?assertion a ner:RelationAssertion ;
               ner:assertionSubject ?c ;
               ner:assertionPredicate obo:RO_0002200 ;
               ner:assertionObject ?m ;
               prov:hadPrimarySource ?pub .

    ?c ner:normalizedEntityKey ?bearer .
    ?m ner:normalizedEntityKey ?phenotype .

    OPTIONAL { ?pub ner:doi ?doi }
    OPTIONAL { ?assertion ner:evidenceText ?evidence }
    OPTIONAL { ?assertion ner:assertionNegated ?negated }
    OPTIONAL { ?assertion ner:assertionModality ?modality }
    OPTIONAL { ?assertion ner:assertionContext ?context }
  }

  FILTER(?requestedBearer = "" || STR(?bearer) = ?requestedBearer)
  FILTER(?requestedPhenotype = "" || STR(?phenotype) = ?requestedPhenotype)
}
ORDER BY ?bearer ?phenotype ?pub
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "bearer_key",
            "string",
            "Optional exact key of the bearing entity; omit or use an empty string for all.",
            default="",
        ),
        QAParam(
            "phenotype_key",
            "string",
            "Optional exact phenotype key; omit or use an empty string for all.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of assertion rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ51
################################################################################

################################################################################
# Optional user parameter query — result limit only
# CQ52 (doc CQ52) — Phenotype grounding in HP / MP / PATO, by tier
################################################################################

register(QAQuery(
    id="ne_phenotype_vocabulary_coverage",
    category=CATEGORY,
    question="How well are phenotypes grounded in HP, MP and PATO, and at which mapping tier?",
    notes=(
        "Use for requests such as 'How many phenotypes map to HP?'; 'Show "
        "phenotype ontology coverage'; 'Are phenotypes mapped exactly or only "
        "broadly?'. "
        "No input is required; limit defaults to 1000 rows. "
        "Returns ?vocab (HP, MP or PATO), ?tier (exact, close, narrow, broad or "
        "related) and ?n (distinct phenotype entities with such a mapping). "
        "Covers ner:Phenotype and its subclasses in ontology 2.5.0. The "
        "vocabulary is read from the OBO IRI prefix (e.g. HP_0000729 → HP). "
        "An entity mapped at several tiers or vocabularies is counted in each. "
        "Phenotypes with no HP/MP/PATO mapping are not counted here; use "
        "ne_entities_without_external_mapping for gaps. Absent vocabulary-tier "
        "combinations are omitted rather than shown as zero. "
        "The query is scoped to the named graph given by `graph`."
    ),
    example={"limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>

SELECT ?vocab ?tier (COUNT(DISTINCT ?e) AS ?n)
WHERE {
  GRAPH {{graph}} {
    # every subclass of ner:Phenotype in named_entity_ontology.owl 2.5.0 (generated)
    VALUES ?cls {
      ner:BehavioralPhenotype ner:CellularPhenotype ner:ClinicalPhenotype
      ner:CognitivePhenotype ner:ElectrophysiologicalPhenotype ner:MolecularPhenotype
      ner:MorphologicalPhenotype ner:MorphologyClass ner:NeurologicalSign ner:Phenotype
      ner:Symptom
    }
    VALUES (?p ?tier) {
      (skos:exactMatch   "exact")
      (skos:closeMatch   "close")
      (skos:narrowMatch  "narrow")
      (skos:broadMatch   "broad")
      (skos:relatedMatch "related")
    }

    ?e a ?cls ;
       ?p ?obo .

    FILTER(STRSTARTS(STR(?obo), "http://purl.obolibrary.org/obo/"))
    BIND(STRBEFORE(STRAFTER(STR(?obo), "obo/"), "_") AS ?vocab)
    FILTER(?vocab IN ("HP", "MP", "PATO"))
  }
}
GROUP BY ?vocab ?tier
ORDER BY ?vocab DESC(?n) ?tier
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "limit",
            "int",
            "Maximum number of vocabulary-tier rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ52
################################################################################

################################################################################
# Optional user parameter query
# CQ53 (doc CQ53) — Causal claims whose effect is a phenotype
################################################################################

register(QAQuery(
    id="ne_phenotype_causal_effects",
    category=CATEGORY,
    question="Which causal claims have a phenotype as their effect (e.g. genotype or intervention → phenotype), with negation, hypotheticality and any effect size?",
    notes=(
        "Use for requests such as 'What causes this phenotype?'; 'Which "
        "interventions change behavior?'; 'What phenotypic effects does this "
        "gene knockout have?'. "
        "OPTIONAL INPUTS: `cause_key` and `phenotype_key`, exact normalized "
        "keys from ne_find_entity; omit or use empty strings for all. "
        "Limit defaults to 1000 rows. "
        "Returns ?causeKey, ?phenotype (effect key), ?negated, ?hypothetical, "
        "and ?measure / ?value (optional effect estimate). "
        "Effects are restricted to ner:Phenotype and its subclasses in "
        "ontology 2.5.0; flags and estimates come from the claim's current "
        "version. Negated claims state that the cause does NOT produce the "
        "phenotype; hypothetical claims are not findings. A claim with several "
        "estimates produces several rows; claims without estimates appear once "
        "with ?measure unbound. Source is not selected; use "
        "ne_effect_claims_by_source_year or ne_causal_pairs_across_sources for "
        "source attribution. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no matching phenotype-effect claims are recorded."
    ),
    example={"cause_key": "", "phenotype_key": "", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>

SELECT DISTINCT ?causeKey ?phenotype ?negated ?hypothetical ?measure ?value
WHERE {
  VALUES (?requestedCause ?requestedPhenotype) { ({{cause_key}} {{phenotype_key}}) }

  GRAPH {{graph}} {
    # every subclass of ner:Phenotype in named_entity_ontology.owl 2.5.0 (generated)
    VALUES ?cls {
      ner:BehavioralPhenotype ner:CellularPhenotype ner:ClinicalPhenotype
      ner:CognitivePhenotype ner:ElectrophysiologicalPhenotype ner:MolecularPhenotype
      ner:MorphologicalPhenotype ner:MorphologyClass ner:NeurologicalSign ner:Phenotype
      ner:Symptom
    }

    ?rel ner:hasEffect ?p ;
         ner:hasCause/ner:normalizedEntityKey ?causeKey ;
         ner:hasCurrentCausalRelationVersion ?v .

    ?p a ?cls ;
       ner:normalizedEntityKey ?phenotype .

    ?v ner:causalNegated ?negated ;
       ner:causalHypothetical ?hypothetical .

    OPTIONAL {
      ?v ner:hasEffectEstimate ?est .
      ?est ner:effectMeasure ?measure ;
           ner:effectValue ?value .
    }
  }

  FILTER(?requestedCause = "" || STR(?causeKey) = ?requestedCause)
  FILTER(?requestedPhenotype = "" || STR(?phenotype) = ?requestedPhenotype)
}
ORDER BY ?phenotype ?causeKey
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "cause_key",
            "string",
            "Optional exact cause key; omit or use an empty string for all causes.",
            default="",
        ),
        QAParam(
            "phenotype_key",
            "string",
            "Optional exact phenotype key; omit or use an empty string for all phenotypes.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of claim rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ53
################################################################################

################################################################################
# Optional user parameter query
# CQ54 (doc CQ54) — Broader paths from leaves to ancestors
################################################################################

register(QAQuery(
    id="ne_broader_paths_to_ancestors",
    category=CATEGORY,
    question="Which ancestors does each leaf entity reach through the extracted broader hierarchy?",
    notes=(
        "Use for requests such as 'What are all the ancestors of this cell "
        "type?'; 'Show the full hierarchy under this class'; 'Which leaf "
        "entities fall under X?'. "
        "OPTIONAL INPUTS: `leaf_key` and `ancestor_key`, exact normalized keys "
        "from ne_find_entity; omit or use empty strings for all. Use "
        "ancestor_key for 'which leaves fall under X'. Limit defaults to 1000 rows. "
        "Returns ?leaf (key of an entity with no narrower children), "
        "?ancestor (key reached by one or more skos:broader steps) and "
        "?ancestorTerm (optional resolved concept identifier of the ancestor). "
        "Paths are corpus-level: individual steps can come from different "
        "sources, so a full path may not be stated by any single paper. Not "
        "restricted to cells. Only leaves are returned as starting points; for "
        "one-step edges from any entity, use ne_broader_relationships. Path "
        "length and step order are not returned. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no matching broader paths were materialized."
    ),
    example={"leaf_key": "", "ancestor_key": "", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>

SELECT DISTINCT ?leaf ?ancestor ?ancestorTerm
WHERE {
  VALUES (?requestedLeaf ?requestedAncestor) { ({{leaf_key}} {{ancestor_key}}) }

  GRAPH {{graph}} {
    ?l skos:broader+ ?a ;
       ner:normalizedEntityKey ?leaf .

    FILTER NOT EXISTS { ?child skos:broader ?l }

    ?a ner:normalizedEntityKey ?ancestor .

    OPTIONAL { ?a ner:resolvedToConcept/ner:conceptIdentifier ?ancestorTerm }
  }

  FILTER(?requestedLeaf = "" || STR(?leaf) = ?requestedLeaf)
  FILTER(?requestedAncestor = "" || STR(?ancestor) = ?requestedAncestor)
}
ORDER BY ?leaf ?ancestor
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "leaf_key",
            "string",
            "Optional exact leaf-entity key; omit or use an empty string for all leaves.",
            default="",
        ),
        QAParam(
            "ancestor_key",
            "string",
            "Optional exact ancestor key; omit or use an empty string for all ancestors.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of leaf-ancestor rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ54
################################################################################

################################################################################
# Optional user parameter query
# CQ55 (doc CQ55) — Source-grounded cell–marker expression assertions
################################################################################

register(QAQuery(
    id="ne_cell_marker_expression_assertions",
    category=CATEGORY,
    question="Which cell types express which markers, according to which papers, with what qualifiers and evidence?",
    notes=(
        "Use for requests such as 'Which cells express this marker?'; 'What "
        "markers does this cell type express?'; 'Which papers report Pvalb in "
        "these interneurons?'. "
        "OPTIONAL INPUTS: `cell_key` and `marker_key`, exact normalized keys "
        "from ne_find_entity. Supply marker_key for 'which cells express X', "
        "cell_key for 'what does cell Y express'; omit or use empty strings for "
        "all. Limit defaults to 1000 rows. "
        "Returns ?cell, ?marker (keys), ?pub (assertion source IRI), ?doi "
        "(optional), ?evidence (optional verbatim text), ?negated, ?modality "
        "and ?context (optional qualifiers). "
        "Reads source-level RelationAssertion records with predicate "
        "RO:0002292 (expresses). Each row is one assertion: do not join rows "
        "from different assertions on a shared cell to claim, e.g., that a "
        "marker is expressed in a region or species; check ?context and the "
        "evidence instead. Rows with ?negated true state the marker is NOT "
        "expressed; report them as such. "
        "The query is scoped to the named graph given by `graph`. "
        "An empty result means no matching assertions, not that the cell "
        "lacks the marker."
    ),
    example={"cell_key": "", "marker_key": "", "limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX obo: <http://purl.obolibrary.org/obo/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT DISTINCT ?cell ?marker ?pub ?doi ?evidence ?negated ?modality ?context
WHERE {
  VALUES (?requestedCell ?requestedMarker) { ({{cell_key}} {{marker_key}}) }

  GRAPH {{graph}} {
    ?assertion a ner:RelationAssertion ;
               ner:assertionSubject ?c ;
               ner:assertionPredicate obo:RO_0002292 ;
               ner:assertionObject ?m ;
               prov:hadPrimarySource ?pub .

    ?c ner:normalizedEntityKey ?cell .
    ?m ner:normalizedEntityKey ?marker .

    OPTIONAL { ?pub ner:doi ?doi }
    OPTIONAL { ?assertion ner:evidenceText ?evidence }
    OPTIONAL { ?assertion ner:assertionNegated ?negated }
    OPTIONAL { ?assertion ner:assertionModality ?modality }
    OPTIONAL { ?assertion ner:assertionContext ?context }
  }

  FILTER(?requestedCell = "" || STR(?cell) = ?requestedCell)
  FILTER(?requestedMarker = "" || STR(?marker) = ?requestedMarker)
}
ORDER BY ?cell ?marker ?pub
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "cell_key",
            "string",
            "Optional exact cell-type key; omit or use an empty string for all cells.",
            default="",
        ),
        QAParam(
            "marker_key",
            "string",
            "Optional exact marker key; omit or use an empty string for all markers.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of assertion rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "For another space, pass a graph IRI from brainkb_read_space or brainkb_list_spaces.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ55
################################################################################
