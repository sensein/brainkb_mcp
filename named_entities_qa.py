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
        "The query is scoped to the named graph "
        "https://www.brainkb.org/named-entity/. Selected papers require an "
        "additional source filter. An empty result means no entities match "
        "the required type, label, and source-provenance patterns."
    ),
    example={"entity_type": "https://brainkb.org/ner/Drug"},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT DISTINCT ?e ?pub ?label ?doi
WHERE {
  GRAPH <https://www.brainkb.org/named-entity/> {
    ?e a ner:NamedEntity ;
       a ?entity_type ;
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
        "Optionally supply requestedKey as an exact normalized entity key. "
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
        "The query is scoped to https://www.brainkb.org/named-entity/. "
        "An empty result means no mention records match the supplied key "
        "and required source-document links."
    ),
    example={"requestedKey": "hippocampus"},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>

SELECT DISTINCT ?pub ?key ?surface ?doi
WHERE {
  GRAPH <https://www.brainkb.org/named-entity/> {
    ?e ner:normalizedEntityKey ?key ;
       ner:hasMention ?m .

    ?m ner:surfaceForm ?surface ;
       ner:partOfDocumentVersion/ner:versionOfDocument ?pub .

    OPTIONAL { ?pub ner:doi ?doi }
  }

  FILTER(!BOUND(?requestedKey) || ?key = ?requestedKey)
}
ORDER BY ?key ?surface ?pub
""",
    params=(
        QAParam(
            "requestedKey",
            "string",
            "Optional exact normalized entity key; omit to return all keys.",
            default=None,
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
        "The query covers all sources in the named graph "
        "https://www.brainkb.org/named-entity/. Requests about a specific "
        "entity, mention, or document require additional filters. "
        "An empty result means no mentions match all required grounding "
        "patterns, not necessarily that no entities were extracted."
    ),
    example={},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>

SELECT DISTINCT ?m ?pub ?surface ?start ?end ?doi
WHERE {
  GRAPH <https://www.brainkb.org/named-entity/> {
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
    params=(),
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
        "The query is scoped to https://www.brainkb.org/named-entity/. "
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

  GRAPH <https://www.brainkb.org/named-entity/> {
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
        "The query is scoped to the named graph "
        "https://www.brainkb.org/named-entity/. A specific entity or selected "
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
  GRAPH <https://www.brainkb.org/named-entity/> {
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
            params=(),
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
        "The query is scoped to https://www.brainkb.org/named-entity/. "
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

  GRAPH <https://www.brainkb.org/named-entity/> {
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
        "The query is scoped to https://www.brainkb.org/named-entity/. "
        "An empty result means no qualifying mapping target is associated "
        "with entities from at least two sources."
    ),
    example={},
    sparql="""
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT ?obo (COUNT(DISTINCT ?pub) AS ?papers)
WHERE {
  GRAPH <https://www.brainkb.org/named-entity/> {
    ?e (skos:exactMatch|skos:closeMatch|skos:broadMatch) ?obo ;
       prov:hadPrimarySource ?pub .

    FILTER(!STRSTARTS(STR(?obo), "https://brainkb.org/concept/"))
  }
}
GROUP BY ?obo
HAVING(COUNT(DISTINCT ?pub) > 1)
ORDER BY DESC(?papers) ?obo
""",
    params=(),
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
        "https://www.brainkb.org/named-entity/. Requests about a specific "
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

  GRAPH <https://www.brainkb.org/named-entity/> {
    ?e ner:normalizedEntityKey ?key ;
       ?p ?obo .

    FILTER(!STRSTARTS(STR(?obo), "https://brainkb.org/concept/"))
  }
}
ORDER BY ?key ?e ?tier ?obo
""",
    params=(),
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
        "The query is scoped to https://www.brainkb.org/named-entity/. "
        "An empty result means no entities match these coverage-gap patterns."
    ),
    example={"limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT DISTINCT ?e ?cls ?key ?gap
WHERE {
  GRAPH <https://www.brainkb.org/named-entity/> {
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
      GRAPH <https://www.brainkb.org/named-entity/> {
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
        "The query is scoped to https://www.brainkb.org/named-entity/. "
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
      GRAPH <https://www.brainkb.org/named-entity/> {
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
          GRAPH <https://www.brainkb.org/named-entity/> {
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
        "The query is scoped to https://www.brainkb.org/named-entity/. "
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

  GRAPH <https://www.brainkb.org/named-entity/> {
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
        "https://www.brainkb.org/named-entity/. "
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
  GRAPH <https://www.brainkb.org/named-entity/> {
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
        "https://www.brainkb.org/named-entity/. Requests about a specific "
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
  GRAPH <https://www.brainkb.org/named-entity/> {
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
        "https://www.brainkb.org/named-entity/. "
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
  GRAPH <https://www.brainkb.org/named-entity/> {
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
    ),
))

################################################################################
# End CQ 14
################################################################################

################################################################################
# Optional user parameter query — result limit only
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
        "No input is required; limit defaults to 1000 result rows. "
        "The effect-key filter is unset using UNDEF, so all qualifying "
        "effects are included. This registration has no effect-key parameter. "
        "A request about a specific effect requires an additional filter. "
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
        "The query is scoped to https://www.brainkb.org/named-entity/. "
        "An empty result means no records match the required claim, "
        "entity-key, hypotheticality, and source-date patterns."
    ),
    example={"limit": 1000},
    sparql="""
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX dcterms: <http://purl.org/dc/terms/>

SELECT ?pub ?effectKey ?year ?doi ?causeKey ?hypothetical
WHERE {
  VALUES ?requestedKey { UNDEF }
  FILTER(!BOUND(?requestedKey) || ?effectKey = ?requestedKey)

  GRAPH <https://www.brainkb.org/named-entity/> {
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
}
ORDER BY ?year
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "limit",
            "int",
            "Maximum number of claim-source-year rows to return.",
            default=1000,
            minimum=1,
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
        "https://www.brainkb.org/named-entity/. "
        "A specific claim requires an additional claim-IRI filter. "
        "An empty result means no version records match the required "
        "type, parent-claim, and revision-number patterns."
    ),
    example={"limit": 1000},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>

SELECT ?rel ?rev ?from ?until
WHERE {
  GRAPH <https://www.brainkb.org/named-entity/> {
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
    ),
))

################################################################################
# End CQ16
################################################################################