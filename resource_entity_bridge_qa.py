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
# @File    : resource_entity_bridge_qa.py
# @Software: PyCharm

"""Canned SPARQL queries that join the resource catalogue to the named-entity
extraction of the same paper, across two separate named graphs.

structsense writes the two kinds of record into different named graphs (often
different spaces, e.g. a resource space and a named-entity space), so no single
graph holds both halves. Every query here reads TWO graphs — `resource_graph`
and `entity_graph` — and joins them on evidence the two exports genuinely share:

  * the publication node itself. Both exports mint the same IRI for a paper
    (UUID5 over its DOI) and both carry ner:doi / ner:title on it, so
    ?paper is an exact join key. The resource side reaches it with
    prov:hadPrimarySource / dcterms:source / bkr:scopeAssertedIn from records
    and scopes; the entity side with prov:hadPrimarySource from entities.
  * the document version (ner:DocumentVersion, ner:checksum, ner:sourcePath) —
    which file each pipeline actually read.
  * ontology terms. Resource scopes point at OBO/EFO IRIs with
    bkrls:appliesToTaxon / appliesToAnatomicalStructure / appliesToCellType /
    appliesToAssay / appliesToCondition and bkr:appliesToModality; named
    entities point at the same IRIs with skos:exactMatch (and the other
    skos:*Match predicates) or via ner:resolvedToConcept -> ner:conceptIRI.
  * quoted evidence text (ner:evidenceText on the resource side, mention
    surface forms and sentences on the entity side).
  * the shared mapping vocabulary: both sides emit ner:ConceptMappingDecision
    with ner:mappingStatus / ner:mappingRelationType / ner:alignmentMethodRaw.

Each QAQuery below is served by brainkb_qa_list / brainkb_qa_run in server.py.
To add one, copy a whole register(...) block, give it a new id — see guide.md.
"""

from qa_registry import QAParam, QAQuery, register, register_category

# The description is shown to the MCP agent in the brainkb_qa_list menu; it is
# the only thing the agent reads before deciding to open this category. Name the
# kinds of questions answered, and keep it current as queries are added.

CATEGORY = register_category(
    "resource_entity_bridge",
    "Join a paper's resource catalogue to the named entities extracted from "
    "the same paper, when the two live in SEPARATE named graphs (for example a "
    "resource space and a named-entity space). Use this category whenever a "
    "question spans both kinds of record: whether a tool's declared, validated "
    "or observed scope actually covers the species, brain regions, cell types, "
    "assays or conditions the paper's entities are grounded in; which entities "
    "fall outside the scope of every resource the paper used; which resources "
    "are declared for a given ontology concept; which entities are named in the "
    "quoted evidence that supports a resource claim; and where in the document "
    "a resource is mentioned. "
    "Also covers cross-graph provenance and agreement: whether both pipelines "
    "read the same document version and checksum, which software, model, "
    "configuration and run produced each half, whether the resource-side and "
    "entity-side concept mappers agree on how a term was grounded, which papers "
    "have only one of the two halves, and a per-paper completeness summary. "
    "EVERY query takes two graph parameters — `resource_graph` (default "
    "https://www.brainkb.org/resources/) and `entity_graph` (default "
    "https://www.brainkb.org/named-entity/). Start with rb_paired_graphs to "
    "discover which graph pair actually holds both halves, or get the IRIs from "
    "brainkb_list_spaces / rs_resource_graphs / ne_named_entity_graphs; then "
    "pass the same pair to every other query here. Passing the same IRI twice "
    "works when one graph happens to hold both kinds of record. "
    "Resolve wording into parameter values with rs_find_resource (resource "
    "names), ne_find_entity (entity labels) and rb_shared_papers (DOIs). "
    "For single-graph questions use the `resources` or `named_entities` "
    "category instead. "
    "A join is only as good as its key: paper-level joins are exact; ontology "
    "joins depend on both sides having mapped a term, so an empty result can "
    "mean 'not mapped' rather than 'no overlap'; text joins are "
    "substring matches on quoted evidence and can miss paraphrase. Shared "
    "grounding shows the two pipelines point at the same concept — it does not "
    "by itself establish that the resource was applied to that entity.",
)

################################################################################
# Every query below is self-contained: its own PREFIXes, its own params, no
# shared constants. Copy a whole register(...) block to start a new one.
#
# `question`, `notes` and `example` are what the MCP agent sees in
# brainkb_qa_list — it chooses a query and fills its params from those alone,
# so say there (not in a code comment) anything the agent needs to know.

#******************************************************************************
# Discovery helpers — find the graph pair and the papers covered by both halves.

################################################################################
# Optional user parameter query — result limit only
# Helper — Which graph pairs hold both halves?
################################################################################

register(QAQuery(
    id="rb_paired_graphs",
    category=CATEGORY,
    question="Which pairs of named graphs hold the resource records and the named entities of the same papers?",
    notes=(
        "RUN THIS FIRST in this category when you do not already know the two "
        "graph IRIs. It finds every (?resourceGraph, ?entityGraph) pair that "
        "describes at least one paper in common — for example a resource space "
        "and a named-entity space ingested separately. Relevant requests "
        "include: 'Where are the resource and entity graphs for this paper?'; "
        "'Which spaces can be joined?'. "
        "No input is required; limit defaults to 100 rows. "
        "Returns ?resourceGraph, ?entityGraph, ?sharedPapers (papers in both), "
        "?resources and ?namedEntities, most shared papers first. Pass the IRIs "
        "exactly as returned, trailing slash included, as the `resource_graph` "
        "and `entity_graph` of the other queries here. "
        "Unlike the other queries this one scans every graph the endpoint can "
        "see, so it is the slowest; run it once and reuse the IRIs. Pairs are "
        "directional: a graph holding both kinds of record appears paired with "
        "itself only if you also look for that, since self-pairs are excluded. "
        "An empty result means no two graphs share a paper — the halves may be "
        "in one graph (use the `resources` category) or the papers differ."
    ),
    example={"limit": 100},
    sparql="""
PREFIX dcat: <http://www.w3.org/ns/dcat#>
PREFIX bkr:  <https://brainkb.org/resource/>
PREFIX ner:  <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT ?resourceGraph ?entityGraph
       (COUNT(DISTINCT ?paper) AS ?sharedPapers)
       (COUNT(DISTINCT ?resource) AS ?resources)
       (COUNT(DISTINCT ?entity) AS ?namedEntities)
WHERE {
  GRAPH ?resourceGraph {
    ?record a dcat:CatalogRecord ;
            prov:hadPrimarySource ?paper ;
            bkr:describesResource ?resource .
  }
  GRAPH ?entityGraph {
    ?entity a ner:NamedEntity ;
            prov:hadPrimarySource ?paper .
  }
  FILTER(?resourceGraph != ?entityGraph)
}
GROUP BY ?resourceGraph ?entityGraph
ORDER BY DESC(?sharedPapers) ?resourceGraph
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "limit",
            "int",
            "Maximum number of graph-pair rows to return.",
            default=100,
            minimum=1,
            maximum=1000,
        ),
    ),
))

################################################################################
# End helper — paired graphs
################################################################################

################################################################################
# Optional user parameter query
# Helper — Papers covered by both graphs
################################################################################

register(QAQuery(
    id="rb_shared_papers",
    category=CATEGORY,
    question="Which papers have both a resource catalogue and a named-entity extraction, and how much of each?",
    notes=(
        "Run this to pick the paper a cross-graph question is about, and to see "
        "how much each half holds before drilling in. Relevant requests include: "
        "'Which papers can I ask cross-graph questions about?'; 'How much was "
        "extracted from each paper on each side?'. "
        "OPTIONAL INPUT: `search`, a case-insensitive substring of the title or "
        "DOI; omit or pass an empty string for all shared papers. "
        "Returns ?paper (the IRI both graphs use — pass it as `paper` to other "
        "queries), ?doi, ?title, ?resources (catalogued in resource_graph), "
        "?namedEntities and ?mentions (in entity_graph). Papers present in only "
        "one graph are excluded by design — use rb_extraction_coverage_gaps for "
        "those. Limit defaults to 200 rows."
    ),
    example={"search": "", "limit": 200},
    sparql="""
PREFIX dcat: <http://www.w3.org/ns/dcat#>
PREFIX bkr:  <https://brainkb.org/resource/>
PREFIX ner:  <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT ?paper ?doi ?title
       (COUNT(DISTINCT ?resource) AS ?resources)
       (COUNT(DISTINCT ?entity) AS ?namedEntities)
       (COUNT(DISTINCT ?mention) AS ?mentions)
WHERE {
  VALUES ?requestedText { {{search}} }

  GRAPH {{resource_graph}} {
    ?record a dcat:CatalogRecord ;
            prov:hadPrimarySource ?paper ;
            bkr:describesResource ?resource .
    OPTIONAL { ?paper ner:doi ?doi }
    OPTIONAL { ?paper ner:title ?title }
  }
  GRAPH {{entity_graph}} {
    ?entity a ner:NamedEntity ;
            prov:hadPrimarySource ?paper .
    OPTIONAL { ?entity ner:hasMention ?mention }
  }
  FILTER(?requestedText = ""
         || CONTAINS(LCASE(CONCAT(STR(COALESCE(?title, "")), " ", STR(COALESCE(?doi, "")))),
                     LCASE(?requestedText)))
}
GROUP BY ?paper ?doi ?title
ORDER BY DESC(?resources) ?title
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "search",
            "string",
            "Optional substring of the paper title or DOI; omit or use an empty string for all shared papers.",
            default="",
        ),
        QAParam(
            "resource_graph",
            "iri",
            "Named graph holding the resource catalogue; omit for the default. "
            "Get it from rb_paired_graphs, rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
        QAParam(
            "entity_graph",
            "iri",
            "Named graph holding the named-entity extraction; omit for the default. "
            "Get it from rb_paired_graphs or ne_named_entity_graphs.",
            default="https://www.brainkb.org/named-entity/",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of paper rows to return.",
            default=200,
            minimum=1,
            maximum=1000,
        ),
    ),
))

################################################################################
# End helper — shared papers
################################################################################

################################################################################
# Optional user parameter query
# Which papers are missing one of the two halves?
################################################################################

register(QAQuery(
    id="rb_extraction_coverage_gaps",
    category=CATEGORY,
    question="Which papers have resource records but no named entities, or named entities but no resource records?",
    notes=(
        "Use to audit pipeline coverage across the two graphs before trusting a "
        "cross-graph count. Relevant requests include: 'Which papers still need "
        "NER?'; 'Which papers were NER-ed but never mined for resources?'; "
        "'Is the resource space in step with the entity space?'. "
        "No input beyond the two graphs; limit defaults to 200 rows. "
        "Returns ?paper, ?doi, ?title and ?missingSide — \"named_entities\" when "
        "the paper is catalogued in resource_graph but absent from "
        "entity_graph, \"resources\" the other way round. Papers covered by both "
        "do not appear (use rb_shared_papers). "
        "A paper counts as present in a graph when that graph types it as "
        "ner:Publication, so a graph that records entities without emitting the "
        "publication node will look like a gap; check with rb_shared_papers."
    ),
    example={"limit": 200},
    sparql="""
PREFIX bkr:  <https://brainkb.org/resource/>
PREFIX ner:  <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT DISTINCT ?paper ?doi ?title ?missingSide
WHERE {
  {
    GRAPH {{resource_graph}} {
      ?paper a ner:Publication .
      OPTIONAL { ?paper ner:doi ?doi }
      OPTIONAL { ?paper ner:title ?title }
    }
    FILTER NOT EXISTS {
      GRAPH {{entity_graph}} { ?e a ner:NamedEntity ; prov:hadPrimarySource ?paper }
    }
    BIND("named_entities" AS ?missingSide)
  }
  UNION
  {
    GRAPH {{entity_graph}} {
      ?paper a ner:Publication .
      OPTIONAL { ?paper ner:doi ?doi }
      OPTIONAL { ?paper ner:title ?title }
    }
    FILTER NOT EXISTS {
      GRAPH {{resource_graph}} {
        ?rec prov:hadPrimarySource ?paper ;
             bkr:describesResource ?r .
      }
    }
    BIND("resources" AS ?missingSide)
  }
}
ORDER BY ?missingSide ?title
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "resource_graph",
            "iri",
            "Named graph holding the resource catalogue; omit for the default.",
            default="https://www.brainkb.org/resources/",
        ),
        QAParam(
            "entity_graph",
            "iri",
            "Named graph holding the named-entity extraction; omit for the default.",
            default="https://www.brainkb.org/named-entity/",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=200,
            minimum=1,
            maximum=1000,
        ),
    ),
))

#******************************************************************************
# Cross-graph provenance — was this the same document, the same software, the
# same grounding decision?

################################################################################
# Optional user parameter query
# Did both pipelines read the same file?
################################################################################

register(QAQuery(
    id="rb_document_version_agreement",
    category=CATEGORY,
    question="Did the resource extraction and the entity extraction read the same document version and checksum?",
    notes=(
        "Use before comparing the two halves of a paper: offsets, quotes and "
        "counts are only comparable if both pipelines read the same file. "
        "Relevant requests include: 'Were the resources and entities extracted "
        "from the same PDF?'; 'Do the checksums match?'; 'Which file version "
        "did each run use?'. "
        "OPTIONAL INPUT: `paper`, the publication IRI from rb_shared_papers; "
        "omit to check every shared paper. "
        "Returns ?doi, ?title, ?resourceChecksums and ?entityChecksums "
        "(all ner:checksum values recorded on each side, ' | '-separated), the "
        "matching ?resourcePaths and ?entityPaths (ner:sourcePath, often the "
        "file name), and ?sharedChecksums — how many checksums appear on both "
        "sides. "
        "?sharedChecksums = 0 with non-empty checksum columns is a real divergence: "
        "the two runs read different bytes, so entity offsets do not address "
        "the text the resource quotes came from, and apparent disagreement "
        "between the halves may be a file difference rather than a modelling "
        "one. Empty checksum columns mean the export simply did not record "
        "them, which is not evidence of divergence. Limit defaults to 100 rows."
    ),
    example={"limit": 100},
    sparql="""
PREFIX ner: <https://brainkb.org/ner/>

SELECT ?paper ?doi ?title ?resourceChecksums ?entityChecksums
       ?resourcePaths ?entityPaths
       (COALESCE(?sharedCount, 0) AS ?sharedChecksums)
WHERE {
  VALUES ?requestedPaper { {{paper}} }

  # One sub-select per side, so the versions of one graph never multiply the
  # versions of the other, and each aggregate sees only its own graph's rows.
  {
    SELECT ?paper
           (GROUP_CONCAT(DISTINCT COALESCE(?rsum, ""); SEPARATOR=" | ") AS ?resourceChecksums)
           (GROUP_CONCAT(DISTINCT COALESCE(?rpath, ""); SEPARATOR=" | ") AS ?resourcePaths)
    WHERE {
      GRAPH {{resource_graph}} {
        ?paper a ner:Publication ;
               ner:hasDocumentVersion ?rversion .
        OPTIONAL { ?rversion ner:checksum ?rsum }
        OPTIONAL { ?rversion ner:sourcePath ?rpath }
      }
    }
    GROUP BY ?paper
  }
  {
    SELECT ?paper
           (GROUP_CONCAT(DISTINCT COALESCE(?esum, ""); SEPARATOR=" | ") AS ?entityChecksums)
           (GROUP_CONCAT(DISTINCT COALESCE(?epath, ""); SEPARATOR=" | ") AS ?entityPaths)
    WHERE {
      GRAPH {{entity_graph}} {
        ?paper ner:hasDocumentVersion ?eversion .
        OPTIONAL { ?eversion ner:checksum ?esum }
        OPTIONAL { ?eversion ner:sourcePath ?epath }
      }
    }
    GROUP BY ?paper
  }
  # Agreement as a join on the checksum literal itself: ?sharedSum binds only
  # for a checksum recorded on BOTH sides for this paper.
  OPTIONAL {
    SELECT ?paper (COUNT(DISTINCT ?sharedSum) AS ?sharedCount)
    WHERE {
      GRAPH {{resource_graph}} { ?paper ner:hasDocumentVersion ?sv1 . ?sv1 ner:checksum ?sharedSum }
      GRAPH {{entity_graph}}   { ?paper ner:hasDocumentVersion ?sv2 . ?sv2 ner:checksum ?sharedSum }
    }
    GROUP BY ?paper
  }
  GRAPH {{resource_graph}} {
    OPTIONAL { ?paper ner:doi ?doi }
    OPTIONAL { ?paper ner:title ?title }
  }
  FILTER(?requestedPaper = "" || STR(?paper) = ?requestedPaper)
}
ORDER BY ?title
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "paper",
            "string",
            "Optional publication IRI (as a string, from rb_shared_papers); "
            "omit or use an empty string to check every paper in both graphs.",
            default="",
        ),
        QAParam(
            "resource_graph",
            "iri",
            "Named graph holding the resource catalogue; omit for the default.",
            default="https://www.brainkb.org/resources/",
        ),
        QAParam(
            "entity_graph",
            "iri",
            "Named graph holding the named-entity extraction; omit for the default.",
            default="https://www.brainkb.org/named-entity/",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of paper rows to return.",
            default=100,
            minimum=1,
            maximum=1000,
        ),
    ),
))

################################################################################
# Optional user parameter query
# Which software and model produced each half?
################################################################################

register(QAQuery(
    id="rb_extraction_agents",
    category=CATEGORY,
    question="Which pipeline, model, configuration and run produced the resource records and the named entities of a paper?",
    notes=(
        "Use to attribute each half of a paper's record, to compare pipeline "
        "versions across the two graphs, or to find which run to re-do. "
        "Relevant requests include: 'Who or what extracted this?'; 'Same "
        "structsense version on both sides?'; 'Which model produced the "
        "entities?'; 'Which run identifier should I cite?'. "
        "OPTIONAL INPUT: `paper`, the publication IRI from rb_shared_papers; "
        "omit for every shared paper. "
        "Returns ?doi, ?resourceAgent and ?resourceAgentVersion (the agent the "
        "catalogue records are attributed to), ?entityActivity, ?entityRun "
        "(ner:runIdentifier), ?entityAgent and ?entityAgentVersion, plus "
        "?entitySoftware, ?entityConfigHash and ?entitySchemaVersion when the "
        "export records them. One row per agent pair, so a run with both a "
        "pipeline agent and a language-model agent yields several rows — read "
        "them together rather than as alternatives. "
        "The resource export usually names only the pipeline agent, so an empty "
        "model column there means 'not recorded', not 'no model'. Limit "
        "defaults to 200 rows."
    ),
    example={"limit": 200},
    sparql="""
PREFIX dcat: <http://www.w3.org/ns/dcat#>
PREFIX ner:  <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT DISTINCT ?paper ?doi
       ?resourceAgent ?resourceAgentVersion
       ?entityActivity ?entityRun ?entityAgent ?entityAgentVersion
       ?entitySoftware ?entityConfigHash ?entitySchemaVersion
WHERE {
  VALUES ?requestedPaper { {{paper}} }

  GRAPH {{resource_graph}} {
    ?record a dcat:CatalogRecord ;
            prov:hadPrimarySource ?paper .
    OPTIONAL { ?paper ner:doi ?doi }
    OPTIONAL {
      ?record prov:wasAttributedTo ?ragent .
      OPTIONAL { ?ragent rdfs:label ?resourceAgent }
      OPTIONAL { ?ragent ner:agentVersion ?resourceAgentVersion }
    }
  }
  GRAPH {{entity_graph}} {
    ?entity a ner:NamedEntity ;
            prov:hadPrimarySource ?paper ;
            prov:wasGeneratedBy ?entityActivity .
    OPTIONAL { ?entityActivity ner:runIdentifier ?entityRun }
    OPTIONAL {
      ?entityActivity prov:wasAssociatedWith ?eagent .
      OPTIONAL { ?eagent rdfs:label ?entityAgent }
      OPTIONAL { ?eagent ner:agentVersion ?entityAgentVersion }
    }
    OPTIONAL { ?entityActivity ner:usedSoftware ?sw . ?sw rdfs:label ?entitySoftware }
    OPTIONAL {
      ?entityActivity ner:usedConfiguration ?cfg .
      OPTIONAL { ?cfg ner:configurationHash ?entityConfigHash }
      OPTIONAL { ?cfg ner:schemaVersion ?entitySchemaVersion }
    }
  }
  FILTER(?requestedPaper = "" || STR(?paper) = ?requestedPaper)
}
ORDER BY ?doi ?entityAgent
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "paper",
            "string",
            "Optional publication IRI (as a string, from rb_shared_papers); "
            "omit or use an empty string for every shared paper.",
            default="",
        ),
        QAParam(
            "resource_graph",
            "iri",
            "Named graph holding the resource catalogue; omit for the default.",
            default="https://www.brainkb.org/resources/",
        ),
        QAParam(
            "entity_graph",
            "iri",
            "Named graph holding the named-entity extraction; omit for the default.",
            default="https://www.brainkb.org/named-entity/",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=200,
            minimum=1,
            maximum=1000,
        ),
    ),
))

################################################################################
# Optional user parameter query
# Do the two concept mappers agree?
################################################################################

register(QAQuery(
    id="rb_mapping_decision_agreement",
    category=CATEGORY,
    question="For ontology terms grounded on both sides, do the resource scope mapper and the entity concept mapper agree?",
    notes=(
        "Use to reconcile grounding between the two pipelines, or to find terms "
        "one side accepted and the other only called ambiguous. Relevant "
        "requests include: 'Do the mappers agree on this term?'; 'Which "
        "groundings are contested across graphs?'; 'Which mapping method was "
        "used on each side?'. "
        "OPTIONAL INPUT: `concept`, an ontology term IRI (as a string) to "
        "restrict to; omit for every term grounded on both sides. "
        "Returns ?concept, ?resourceLabel and ?entityLabel (how each side names "
        "it), ?resourceField (bkr:mappedField, e.g. anatomical_structures), "
        "?resourceStatus / ?entityStatus (local name of ner:mappingStatus: "
        "accepted, ambiguous, rejected), ?resourceRelation / ?entityRelation "
        "(exact, narrower, broader, close), ?resourceMethod / ?entityMethod "
        "(ner:alignmentMethodRaw) and ?agreement — \"same_status\" when the two "
        "statuses are equal, \"different_status\" otherwise. "
        "Rows appear only where BOTH sides emitted a mapping decision for the "
        "term, so this measures agreement among grounded terms, not coverage; "
        "for coverage use rb_scope_entity_overlap and "
        "rb_unmapped_curation_backlog. Limit defaults to 200 rows."
    ),
    example={"limit": 200},
    sparql="""
PREFIX bkr:  <https://brainkb.org/resource/>
PREFIX ner:  <https://brainkb.org/ner/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT DISTINCT ?concept ?resourceLabel ?entityLabel ?resourceField
       ?resourceStatus ?entityStatus ?resourceRelation ?entityRelation
       ?resourceMethod ?entityMethod ?agreement
WHERE {
  VALUES ?requestedConcept { {{concept}} }

  GRAPH {{resource_graph}} {
    ?rdecision a ner:ConceptMappingDecision ;
               ner:conceptIRI ?concept ;
               ner:mappingStatus ?rstatus .
    OPTIONAL { ?rdecision rdfs:label ?resourceLabel }
    OPTIONAL { ?rdecision bkr:mappedField ?resourceField }
    OPTIONAL { ?rdecision ner:mappingRelationType ?rrel }
    OPTIONAL { ?rdecision ner:alignmentMethodRaw ?resourceMethod }
  }
  GRAPH {{entity_graph}} {
    ?edecision a ner:ConceptMappingDecision ;
               ner:mappingStatus ?estatus ;
               ner:selectedCandidate ?candidate .
    ?candidate ner:candidateConcept ?oconcept .
    ?oconcept ner:conceptIRI ?concept .
    OPTIONAL { ?edecision rdfs:label ?entityLabel }
    OPTIONAL { ?edecision ner:mappingRelationType ?erel }
    OPTIONAL { ?edecision ner:alignmentMethodRaw ?entityMethod }
  }
  FILTER(?requestedConcept = "" || STR(?concept) = ?requestedConcept)

  BIND(REPLACE(STR(?rstatus), "^.*[/#]", "") AS ?resourceStatus)
  BIND(REPLACE(STR(?estatus), "^.*[/#]", "") AS ?entityStatus)
  BIND(REPLACE(STR(?rrel), "^.*[/#]", "") AS ?resourceRelation)
  BIND(REPLACE(STR(?erel), "^.*[/#]", "") AS ?entityRelation)
  BIND(IF(?resourceStatus = ?entityStatus, "same_status", "different_status") AS ?agreement)
}
ORDER BY ?agreement ?concept
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "concept",
            "string",
            "Optional ontology term IRI (as a string, e.g. "
            "http://purl.obolibrary.org/obo/UBERON_0010011); omit or use an "
            "empty string for every term grounded on both sides.",
            default="",
        ),
        QAParam(
            "resource_graph",
            "iri",
            "Named graph holding the resource catalogue; omit for the default.",
            default="https://www.brainkb.org/resources/",
        ),
        QAParam(
            "entity_graph",
            "iri",
            "Named graph holding the named-entity extraction; omit for the default.",
            default="https://www.brainkb.org/named-entity/",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=200,
            minimum=1,
            maximum=1000,
        ),
    ),
))

#******************************************************************************
# Scope vs. what the paper is actually about — the scientific core of the bridge.

################################################################################
# Optional user parameter query
# Scope terms that are also entity groundings
################################################################################

register(QAQuery(
    id="rb_scope_entity_overlap",
    category=CATEGORY,
    question="Which of a resource's scope terms are also the ontology grounding of a named entity extracted from the same paper?",
    notes=(
        "The core bridge query: it shows, term by term, where a resource's "
        "declared, validated, observed or excluded scope meets what the paper's "
        "text is actually about. Relevant requests include: 'Is this tool "
        "declared for the regions this paper studies?'; 'Which of the paper's "
        "cell types does this dataset cover?'; 'Show the overlap between scope "
        "and extracted entities'. "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name (omit for all resources); `facet`, a case-insensitive substring "
        "of the scope facet to keep — Taxon, AnatomicalStructure, CellType, "
        "Assay, Condition or Modality (omit for all facets). "
        "Returns ?resourceName, ?scopeType (DeclaredScope, ValidatedScope, "
        "ObservedScope, OutOfScope), ?facet, ?term (the shared ontology IRI), "
        "?entityLabel, ?entityType (CellType, BrainRegion, Gene, Species, …), "
        "?mentions (how often the paper mentions that entity) and "
        "?scopeStatement (the sentence the scope was extracted from). "
        "Read ?scopeType carefully: ValidatedScope means the paper reports "
        "performance for that term, ObservedScope only that the resource was "
        "used on it, DeclaredScope that the source claims it, and OutOfScope "
        "that the source rules it out — an OutOfScope row overlapping an "
        "extracted entity is a conflict worth reporting (see "
        "rb_out_of_scope_conflicts). Terms neither side mapped never appear. "
        "Limit defaults to 500 rows."
    ),
    example={"name": "", "facet": "", "limit": 500},
    sparql="""
PREFIX bkr:   <https://brainkb.org/resource/>
PREFIX bkrls: <https://brainkb.org/resource/lifesci/>
PREFIX ner:   <https://brainkb.org/ner/>
PREFIX prov:  <http://www.w3.org/ns/prov#>
PREFIX rdfs:  <http://www.w3.org/2000/01/rdf-schema#>
PREFIX skos:  <http://www.w3.org/2004/02/skos/core#>

SELECT DISTINCT ?resourceName ?scopeType ?facet ?term ?entityLabel ?entityType
       (COUNT(DISTINCT ?mention) AS ?mentions) (SAMPLE(?statement) AS ?scopeStatement)
WHERE {
  VALUES ?requestedName  { {{name}} }
  VALUES ?requestedFacet { {{facet}} }

  GRAPH {{resource_graph}} {
    ?resource a bkr:Resource ;
              bkr:resourceName ?resourceName .

    VALUES ?scopeLink { bkr:hasDeclaredScope bkr:hasValidatedScope bkr:hasObservedScope bkr:hasExcludedScope }
    ?resource ?scopeLink ?scope .
    ?scope a ?scopeClass ;
           bkr:scopeAssertedIn ?paper .

    VALUES ?facetProperty { bkrls:appliesToTaxon bkrls:appliesToAnatomicalStructure
                            bkrls:appliesToCellType bkrls:appliesToAssay
                            bkrls:appliesToCondition bkr:appliesToModality }
    ?scope ?facetProperty ?term .
    OPTIONAL { ?scope bkr:scopeStatement ?statement }

    BIND(REPLACE(STR(?scopeClass), "^.*[/#]", "") AS ?scopeType)
    BIND(REPLACE(REPLACE(STR(?facetProperty), "^.*[/#]", ""), "^appliesTo", "") AS ?facet)
  }
  GRAPH {{entity_graph}} {
    ?entity a ner:NamedEntity ;
            prov:hadPrimarySource ?paper ;
            rdfs:label ?entityLabel .
    { ?entity skos:exactMatch ?term }
    UNION
    { ?entity ner:resolvedToConcept ?concept . ?concept ner:conceptIRI ?term }
    OPTIONAL { ?entity ner:hasMention ?mention }
    OPTIONAL {
      ?entity a ?entityClass .
      FILTER(?entityClass != ner:NamedEntity)
      BIND(REPLACE(STR(?entityClass), "^.*[/#]", "") AS ?entityType)
    }
  }
  FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?resourceName)), LCASE(?requestedName)))
  FILTER(?requestedFacet = "" || CONTAINS(LCASE(?facet), LCASE(?requestedFacet)))
}
GROUP BY ?resourceName ?scopeType ?facet ?term ?entityLabel ?entityType
ORDER BY ?resourceName ?scopeType ?facet ?entityLabel
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "name",
            "string",
            "Optional substring of a resource name; omit or use an empty string for all resources.",
            default="",
        ),
        QAParam(
            "facet",
            "string",
            "Optional substring of the scope facet to keep: Taxon, "
            "AnatomicalStructure, CellType, Assay, Condition or Modality; "
            "omit or use an empty string for all facets.",
            default="",
        ),
        QAParam(
            "resource_graph",
            "iri",
            "Named graph holding the resource catalogue; omit for the default.",
            default="https://www.brainkb.org/resources/",
        ),
        QAParam(
            "entity_graph",
            "iri",
            "Named graph holding the named-entity extraction; omit for the default.",
            default="https://www.brainkb.org/named-entity/",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=500,
            minimum=1,
            maximum=1000,
        ),
    ),
))

################################################################################
# Optional user parameter query
# Entities no resource in the paper claims to cover
################################################################################

register(QAQuery(
    id="rb_entities_outside_scope",
    category=CATEGORY,
    question="Which named entities of a paper are grounded in ontology terms that no scope of that paper's resources covers?",
    notes=(
        "The complement of rb_scope_entity_overlap: what the paper talks about "
        "that none of its catalogued resources is declared, validated or "
        "observed for. Relevant requests include: 'What does this paper discuss "
        "that its tools do not cover?'; 'Which regions or cell types are out of "
        "scope for every resource here?'; 'Where would I need another tool?'. "
        "OPTIONAL INPUT: `entity_type`, a case-insensitive substring of the "
        "entity class to keep — CellType, BrainRegion, Gene, Species, "
        "CellSubtype (omit for all). "
        "Returns ?doi, ?entityLabel, ?entityType, ?term (its ontology IRI) and "
        "?mentions, most mentioned first. "
        "Only entities that HAVE an ontology grounding can be tested, so an "
        "unmapped entity is absent here rather than reported as uncovered — "
        "rb_unmapped_curation_backlog lists those. A row means no scope record "
        "names that exact term; a resource declared for a broader parent region "
        "may still apply, since this query does not traverse the ontology "
        "hierarchy. Limit defaults to 500 rows."
    ),
    example={"entity_type": "", "limit": 500},
    sparql="""
PREFIX bkr:   <https://brainkb.org/resource/>
PREFIX bkrls: <https://brainkb.org/resource/lifesci/>
PREFIX ner:   <https://brainkb.org/ner/>
PREFIX prov:  <http://www.w3.org/ns/prov#>
PREFIX rdfs:  <http://www.w3.org/2000/01/rdf-schema#>
PREFIX skos:  <http://www.w3.org/2004/02/skos/core#>

SELECT ?doi ?entityLabel ?entityType ?term (COUNT(DISTINCT ?mention) AS ?mentions)
WHERE {
  VALUES ?requestedType { {{entity_type}} }

  GRAPH {{resource_graph}} {
    ?paper a ner:Publication .
    OPTIONAL { ?paper ner:doi ?doi }
  }
  GRAPH {{entity_graph}} {
    ?entity a ner:NamedEntity ;
            prov:hadPrimarySource ?paper ;
            rdfs:label ?entityLabel .
    { ?entity skos:exactMatch ?term }
    UNION
    { ?entity ner:resolvedToConcept ?concept . ?concept ner:conceptIRI ?term }
    OPTIONAL { ?entity ner:hasMention ?mention }
    OPTIONAL {
      ?entity a ?entityClass .
      FILTER(?entityClass != ner:NamedEntity)
      BIND(REPLACE(STR(?entityClass), "^.*[/#]", "") AS ?entityType)
    }
  }
  FILTER(?requestedType = "" || CONTAINS(LCASE(STR(COALESCE(?entityType, ""))), LCASE(?requestedType)))

  FILTER NOT EXISTS {
    GRAPH {{resource_graph}} {
      ?scope bkr:scopeAssertedIn ?paper .
      VALUES ?facetProperty { bkrls:appliesToTaxon bkrls:appliesToAnatomicalStructure
                              bkrls:appliesToCellType bkrls:appliesToAssay
                              bkrls:appliesToCondition bkr:appliesToModality }
      ?scope ?facetProperty ?term .
    }
  }
}
GROUP BY ?doi ?entityLabel ?entityType ?term
ORDER BY DESC(?mentions) ?entityLabel
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "entity_type",
            "string",
            "Optional substring of the entity class to keep (CellType, "
            "BrainRegion, Gene, Species, CellSubtype); omit or use an empty "
            "string for all entity types.",
            default="",
        ),
        QAParam(
            "resource_graph",
            "iri",
            "Named graph holding the resource catalogue; omit for the default.",
            default="https://www.brainkb.org/resources/",
        ),
        QAParam(
            "entity_graph",
            "iri",
            "Named graph holding the named-entity extraction; omit for the default.",
            default="https://www.brainkb.org/named-entity/",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=500,
            minimum=1,
            maximum=1000,
        ),
    ),
))

################################################################################
# Required user parameter query
# Which resources are declared for this concept?
################################################################################

register(QAQuery(
    id="rb_resources_for_concept",
    category=CATEGORY,
    question="Which resources have scope on a given ontology concept, and which papers' entities ground to that same concept?",
    notes=(
        "The reuse query: start from a concept the user cares about (a brain "
        "region, cell type or species) and find the resources whose scope "
        "covers it, with the evidence and the papers whose text is about it. "
        "Relevant requests include: 'Which tools are validated on dorsal "
        "striatum?'; 'What datasets cover D1 MSNs?'; 'Is there a resource for "
        "this UBERON term?'. "
        "REQUIRED INPUT: `concept`, the ontology term IRI — get it from "
        "ne_entity_external_mappings, rb_scope_entity_overlap or an OBO/EFO "
        "lookup (e.g. http://purl.obolibrary.org/obo/UBERON_0010011). "
        "Returns ?resourceName, ?resourceType (local name of the resource "
        "class), ?scopeType, ?evidenceLevel (benchmarked, "
        "documented_in_publication, extracted_from_text, developer_asserted), "
        "?scopeStatement, ?doi of the asserting paper, and ?entityLabels — the "
        "labels that same paper's entities use for the concept. "
        "Rank by ?scopeType and ?evidenceLevel, not by row order: a "
        "ValidatedScope row with evidenceLevel=benchmarked is a far stronger "
        "claim than an ObservedScope one. An OutOfScope row means the source "
        "explicitly excludes the concept. ?entityLabels is empty when the "
        "paper's entity graph did not map that concept. Limit defaults to 200 "
        "rows."
    ),
    example={"concept": "http://purl.obolibrary.org/obo/UBERON_0010011", "limit": 200},
    sparql="""
PREFIX bkr:   <https://brainkb.org/resource/>
PREFIX bkrls: <https://brainkb.org/resource/lifesci/>
PREFIX ner:   <https://brainkb.org/ner/>
PREFIX prov:  <http://www.w3.org/ns/prov#>
PREFIX rdfs:  <http://www.w3.org/2000/01/rdf-schema#>
PREFIX skos:  <http://www.w3.org/2004/02/skos/core#>

SELECT ?resourceName ?resourceType ?scopeType ?evidenceLevel ?scopeStatement ?doi
       (GROUP_CONCAT(DISTINCT ?entityLabel; SEPARATOR="; ") AS ?entityLabels)
WHERE {
  GRAPH {{resource_graph}} {
    ?resource a bkr:Resource ;
              bkr:resourceName ?resourceName .
    VALUES ?scopeLink { bkr:hasDeclaredScope bkr:hasValidatedScope bkr:hasObservedScope bkr:hasExcludedScope }
    ?resource ?scopeLink ?scope .
    ?scope a ?scopeClass ;
           bkr:scopeAssertedIn ?paper .

    VALUES ?facetProperty { bkrls:appliesToTaxon bkrls:appliesToAnatomicalStructure
                            bkrls:appliesToCellType bkrls:appliesToAssay
                            bkrls:appliesToCondition bkr:appliesToModality }
    ?scope ?facetProperty {{concept}} .

    OPTIONAL { ?scope bkr:scopeStatement ?scopeStatement }
    OPTIONAL { ?scope bkr:scopeEvidenceLevel ?level }
    OPTIONAL { ?paper ner:doi ?doi }
    OPTIONAL {
      ?resource a ?resourceClass .
      FILTER(?resourceClass != bkr:Resource)
      BIND(REPLACE(STR(?resourceClass), "^.*[/#]", "") AS ?resourceType)
    }
    BIND(REPLACE(STR(?scopeClass), "^.*[/#]", "") AS ?scopeType)
    BIND(REPLACE(STR(?level), "^.*[/#]", "") AS ?evidenceLevel)
  }
  OPTIONAL {
    GRAPH {{entity_graph}} {
      ?entity a ner:NamedEntity ;
              prov:hadPrimarySource ?paper ;
              rdfs:label ?entityLabel .
      { ?entity skos:exactMatch {{concept}} }
      UNION
      { ?entity ner:resolvedToConcept ?oc . ?oc ner:conceptIRI {{concept}} }
    }
  }
}
GROUP BY ?resourceName ?resourceType ?scopeType ?evidenceLevel ?scopeStatement ?doi
ORDER BY ?scopeType ?resourceName
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "concept",
            "iri",
            "IRI of the ontology concept (e.g. "
            "http://purl.obolibrary.org/obo/UBERON_0010011 for basal ganglia).",
        ),
        QAParam(
            "resource_graph",
            "iri",
            "Named graph holding the resource catalogue; omit for the default.",
            default="https://www.brainkb.org/resources/",
        ),
        QAParam(
            "entity_graph",
            "iri",
            "Named graph holding the named-entity extraction; omit for the default.",
            default="https://www.brainkb.org/named-entity/",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=200,
            minimum=1,
            maximum=1000,
        ),
    ),
))

################################################################################
# Optional user parameter query
# Species in the text vs taxa in the scope
################################################################################

register(QAQuery(
    id="rb_taxon_scope_vs_species",
    category=CATEGORY,
    question="Does a resource's declared taxon cover the species the paper's entities actually name?",
    notes=(
        "Use for the species sanity check that cross-species papers most often "
        "fail. Relevant requests include: 'Is this tool human-only while the "
        "paper also uses mouse?'; 'Which species does the paper name that the "
        "resource does not declare?'. "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name; omit for all resources with a declared taxon. "
        "Returns ?resourceName, ?scopeTaxa (every NCBITaxon IRI any scope of "
        "that resource declares, ' | '-separated), ?speciesLabel and "
        "?speciesTerm for one species entity of the same paper, ?mentions, and "
        "?speciesInScope — 1 when that taxon is among ?scopeTaxa, 0 when it is "
        "not. "
        "?speciesInScope = 0 is the finding: the paper names a species the "
        "resource's record does not claim, and those rows sort first. It is a "
        "flag to check, not proof of misuse — the species may be named only in a "
        "citation, and the resource record may simply be incomplete. Resources "
        "with no taxon scope at all are absent rather than 0. Limit defaults to "
        "300 rows."
    ),
    example={"name": "", "limit": 300},
    sparql="""
PREFIX bkr:   <https://brainkb.org/resource/>
PREFIX bkrls: <https://brainkb.org/resource/lifesci/>
PREFIX ner:   <https://brainkb.org/ner/>
PREFIX prov:  <http://www.w3.org/ns/prov#>
PREFIX rdfs:  <http://www.w3.org/2000/01/rdf-schema#>
PREFIX skos:  <http://www.w3.org/2004/02/skos/core#>

SELECT DISTINCT ?resourceName ?speciesLabel ?speciesTerm ?scopeTaxa ?mentions
       (IF(CONTAINS(CONCAT(" | ", ?scopeTaxa, " | "),
                    CONCAT(" | ", STR(?speciesTerm), " | ")), 1, 0) AS ?speciesInScope)
WHERE {
  VALUES ?requestedName { {{name}} }

  # The resource's taxa and the paper's species are aggregated separately, then
  # compared with a string test on the ' | '-delimited list. Testing membership
  # as an OPTIONAL join against an already-aggregated row, or with EXISTS, is
  # what makes this kind of query engine-dependent.
  {
    SELECT ?resource ?resourceName ?paper
           (GROUP_CONCAT(DISTINCT STR(?scopeTaxon); SEPARATOR=" | ") AS ?scopeTaxa)
    WHERE {
      GRAPH {{resource_graph}} {
        ?resource a bkr:Resource ;
                  bkr:resourceName ?resourceName .
        VALUES ?scopeLink { bkr:hasDeclaredScope bkr:hasValidatedScope bkr:hasObservedScope }
        ?resource ?scopeLink ?scope .
        ?scope bkr:scopeAssertedIn ?paper ;
               bkrls:appliesToTaxon ?scopeTaxon .
      }
    }
    GROUP BY ?resource ?resourceName ?paper
  }
  {
    SELECT ?paper ?speciesLabel ?speciesTerm (COUNT(DISTINCT ?mention) AS ?mentions)
    WHERE {
      GRAPH {{entity_graph}} {
        ?entity a ner:Species ;
                prov:hadPrimarySource ?paper ;
                rdfs:label ?speciesLabel .
        { ?entity skos:exactMatch ?speciesTerm }
        UNION
        { ?entity ner:resolvedToConcept ?concept . ?concept ner:conceptIRI ?speciesTerm }
        OPTIONAL { ?entity ner:hasMention ?mention }
      }
    }
    GROUP BY ?paper ?speciesLabel ?speciesTerm
  }
  FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?resourceName)), LCASE(?requestedName)))
}
ORDER BY ?speciesInScope DESC(?mentions) ?resourceName
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "name",
            "string",
            "Optional substring of a resource name; omit or use an empty string for all resources.",
            default="",
        ),
        QAParam(
            "resource_graph",
            "iri",
            "Named graph holding the resource catalogue; omit for the default.",
            default="https://www.brainkb.org/resources/",
        ),
        QAParam(
            "entity_graph",
            "iri",
            "Named graph holding the named-entity extraction; omit for the default.",
            default="https://www.brainkb.org/named-entity/",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=300,
            minimum=1,
            maximum=1000,
        ),
    ),
))

################################################################################
# Optional user parameter query
# Out-of-scope declarations that the paper's entities contradict
################################################################################

register(QAQuery(
    id="rb_out_of_scope_conflicts",
    category=CATEGORY,
    question="Does a paper extract entities for exactly the concepts its resources declare out of scope?",
    notes=(
        "Use as an audit: a resource record that rules a concept out, in a "
        "paper whose own text is grounded in that concept. Relevant requests "
        "include: 'Is any tool here used outside its stated limits?'; 'Flag "
        "out-of-scope conflicts'; 'Where does the paper contradict a "
        "resource's exclusions?'. "
        "No input beyond the two graphs and an optional resource-name filter "
        "(`name`). "
        "Returns ?resourceName, ?term, ?entityLabel, ?entityType, ?mentions, "
        "?scopeStatement (the exclusion as stated) and ?evidenceLevel. "
        "A row is a prompt to read the paper, not a verdict: the entity may be "
        "discussed without the resource being applied to it, and an exclusion "
        "extracted from text can be narrower than the record makes it look. "
        "Quote ?scopeStatement when reporting one. An empty result is the "
        "normal, healthy case. Limit defaults to 200 rows."
    ),
    example={"name": "", "limit": 200},
    sparql="""
PREFIX bkr:   <https://brainkb.org/resource/>
PREFIX bkrls: <https://brainkb.org/resource/lifesci/>
PREFIX ner:   <https://brainkb.org/ner/>
PREFIX prov:  <http://www.w3.org/ns/prov#>
PREFIX rdfs:  <http://www.w3.org/2000/01/rdf-schema#>
PREFIX skos:  <http://www.w3.org/2004/02/skos/core#>

SELECT ?resourceName ?term ?entityLabel ?entityType
       (COUNT(DISTINCT ?mention) AS ?mentions)
       (SAMPLE(?statement) AS ?scopeStatement)
       (SAMPLE(?level) AS ?evidenceLevel)
WHERE {
  VALUES ?requestedName { {{name}} }

  GRAPH {{resource_graph}} {
    ?resource a bkr:Resource ;
              bkr:resourceName ?resourceName ;
              bkr:hasExcludedScope ?scope .
    ?scope bkr:scopeAssertedIn ?paper .
    VALUES ?facetProperty { bkrls:appliesToTaxon bkrls:appliesToAnatomicalStructure
                            bkrls:appliesToCellType bkrls:appliesToAssay
                            bkrls:appliesToCondition bkr:appliesToModality }
    ?scope ?facetProperty ?term .
    OPTIONAL { ?scope bkr:scopeStatement ?statement }
    OPTIONAL {
      ?scope bkr:scopeEvidenceLevel ?rawLevel .
      BIND(REPLACE(STR(?rawLevel), "^.*[/#]", "") AS ?level)
    }
  }
  GRAPH {{entity_graph}} {
    ?entity a ner:NamedEntity ;
            prov:hadPrimarySource ?paper ;
            rdfs:label ?entityLabel .
    { ?entity skos:exactMatch ?term }
    UNION
    { ?entity ner:resolvedToConcept ?concept . ?concept ner:conceptIRI ?term }
    OPTIONAL { ?entity ner:hasMention ?mention }
    OPTIONAL {
      ?entity a ?entityClass .
      FILTER(?entityClass != ner:NamedEntity)
      BIND(REPLACE(STR(?entityClass), "^.*[/#]", "") AS ?entityType)
    }
  }
  FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?resourceName)), LCASE(?requestedName)))
}
GROUP BY ?resourceName ?term ?entityLabel ?entityType
ORDER BY DESC(?mentions) ?resourceName
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "name",
            "string",
            "Optional substring of a resource name; omit or use an empty string for all resources.",
            default="",
        ),
        QAParam(
            "resource_graph",
            "iri",
            "Named graph holding the resource catalogue; omit for the default.",
            default="https://www.brainkb.org/resources/",
        ),
        QAParam(
            "entity_graph",
            "iri",
            "Named graph holding the named-entity extraction; omit for the default.",
            default="https://www.brainkb.org/named-entity/",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=200,
            minimum=1,
            maximum=1000,
        ),
    ),
))

#******************************************************************************
# Evidence-level bridge — the same passage, seen by both pipelines.

################################################################################
# Optional user parameter query
# Entities named inside a resource claim's quoted evidence
################################################################################

register(QAQuery(
    id="rb_evidence_passage_entities",
    category=CATEGORY,
    question="Which named entities are named inside the quoted evidence that supports a resource's scope claim?",
    notes=(
        "Use to read a resource claim together with the entities in the very "
        "text it was extracted from — the finest-grained bridge between the two "
        "graphs. Relevant requests include: 'What is this tool used on, in the "
        "sentence that mentions it?'; 'Which cell types appear in the evidence "
        "for this claim?'; 'Show the passage behind this scope statement'. "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name (omit for all); `entity_type`, a substring of the entity class to "
        "keep — CellType, BrainRegion, Gene, Species (omit for all). "
        "Returns ?resourceName, ?scopeType, ?evidenceText (the quote), "
        "?entityLabel, ?entityType, ?surfaceForm (the string matched in the "
        "quote) and ?startOffset / ?endOffset of one mention in the entity "
        "graph's document version. "
        "The match is a case-insensitive substring of the quote, restricted to "
        "surface forms of 4 characters or more, so it finds co-occurrence in "
        "the passage — not a claim that the resource was applied to that "
        "entity. Common words ('human', 'cell') will match many quotes; prefer "
        "rows whose ?surfaceForm is specific, and set `entity_type` to cut "
        "noise. Offsets only line up with the quote if "
        "rb_document_version_agreement shows a shared checksum. Limit defaults "
        "to 300 rows."
    ),
    example={"name": "", "entity_type": "CellType", "limit": 300},
    sparql="""
PREFIX bkr:  <https://brainkb.org/resource/>
PREFIX ner:  <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT DISTINCT ?resourceName ?scopeType ?evidenceText ?entityLabel ?entityType
       ?surfaceForm ?startOffset ?endOffset
WHERE {
  VALUES ?requestedName { {{name}} }
  VALUES ?requestedType { {{entity_type}} }

  GRAPH {{resource_graph}} {
    ?resource a bkr:Resource ;
              bkr:resourceName ?resourceName .
    VALUES ?scopeLink { bkr:hasDeclaredScope bkr:hasValidatedScope bkr:hasObservedScope bkr:hasExcludedScope }
    ?resource ?scopeLink ?scope .
    ?scope a ?scopeClass ;
           bkr:scopeAssertedIn ?paper ;
           bkr:evidencedByMention ?resourceMention .
    ?resourceMention ner:evidenceText ?evidenceText .
    BIND(REPLACE(STR(?scopeClass), "^.*[/#]", "") AS ?scopeType)
  }
  GRAPH {{entity_graph}} {
    ?entity a ner:NamedEntity ;
            prov:hadPrimarySource ?paper ;
            rdfs:label ?entityLabel ;
            ner:hasMention ?mention .
    ?mention ner:surfaceForm ?surfaceForm .
    OPTIONAL { ?mention ner:documentStartOffset ?startOffset }
    OPTIONAL { ?mention ner:documentEndOffset ?endOffset }
    OPTIONAL {
      ?entity a ?entityClass .
      FILTER(?entityClass != ner:NamedEntity)
      BIND(REPLACE(STR(?entityClass), "^.*[/#]", "") AS ?entityType)
    }
  }
  FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?resourceName)), LCASE(?requestedName)))
  FILTER(?requestedType = "" || CONTAINS(LCASE(STR(COALESCE(?entityType, ""))), LCASE(?requestedType)))
  FILTER(STRLEN(STR(?surfaceForm)) >= 4
         && CONTAINS(LCASE(STR(?evidenceText)), LCASE(STR(?surfaceForm))))
}
ORDER BY ?resourceName ?entityLabel
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "name",
            "string",
            "Optional substring of a resource name; omit or use an empty string for all resources.",
            default="",
        ),
        QAParam(
            "entity_type",
            "string",
            "Optional substring of the entity class to keep (CellType, "
            "BrainRegion, Gene, Species); omit or use an empty string for all.",
            default="",
        ),
        QAParam(
            "resource_graph",
            "iri",
            "Named graph holding the resource catalogue; omit for the default.",
            default="https://www.brainkb.org/resources/",
        ),
        QAParam(
            "entity_graph",
            "iri",
            "Named graph holding the named-entity extraction; omit for the default.",
            default="https://www.brainkb.org/named-entity/",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=300,
            minimum=1,
            maximum=1000,
        ),
    ),
))

################################################################################
# Optional user parameter query
# Where in the document is this resource mentioned?
################################################################################

register(QAQuery(
    id="rb_resource_mention_sites",
    category=CATEGORY,
    question="Where in the document is a catalogued resource mentioned, by section and character offset?",
    notes=(
        "Use to go from a catalogue entry to the places in the text that talk "
        "about it, when the entity graph also extracted the resource as a named "
        "entity. Relevant requests include: 'Where is Kilosort mentioned?'; "
        "'Which section introduces this dataset?'; 'Show the sentences naming "
        "this tool'. "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name; omit for every resource that matches an entity. "
        "Returns ?resourceName, ?entity (the named-entity IRI — pass it to the "
        "named_entities queries), ?surfaceForm, ?section (ner:sectionLabel), "
        "?startOffset, ?endOffset and ?sentenceText. "
        "Matching is on the resource name against the entity's label, "
        "normalized label or a mention's surface form, case-insensitively and "
        "within the same paper, so a tool the paper spells differently from its "
        "catalogue name is missed, and a short name can match a longer phrase. "
        "Offsets address the entity graph's document version — check "
        "rb_document_version_agreement before quoting them against the "
        "resource-side text. Limit defaults to 300 rows."
    ),
    example={"name": "", "limit": 300},
    sparql="""
PREFIX bkr:  <https://brainkb.org/resource/>
PREFIX ner:  <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT DISTINCT ?resourceName ?entity ?surfaceForm ?section ?startOffset ?endOffset ?sentenceText
WHERE {
  VALUES ?requestedName { {{name}} }

  GRAPH {{resource_graph}} {
    ?resource a bkr:Resource ;
              bkr:resourceName ?resourceName ;
              bkr:hasRecord ?record .
    ?record prov:hadPrimarySource ?paper .
  }
  GRAPH {{entity_graph}} {
    ?entity a ner:NamedEntity ;
            prov:hadPrimarySource ?paper ;
            rdfs:label ?entityLabel ;
            ner:hasMention ?mention .
    ?mention ner:surfaceForm ?surfaceForm .
    OPTIONAL { ?entity ner:normalizedEntityLabel ?normalizedLabel }
    OPTIONAL { ?mention ner:documentStartOffset ?startOffset }
    OPTIONAL { ?mention ner:documentEndOffset ?endOffset }
    OPTIONAL { ?mention ner:inSection ?sec . ?sec ner:sectionLabel ?section }
    OPTIONAL { ?mention ner:inSentence ?sent . ?sent ner:sentenceText ?sentenceText }
  }
  FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?resourceName)), LCASE(?requestedName)))
  FILTER(LCASE(STR(?entityLabel)) = LCASE(STR(?resourceName))
         || LCASE(STR(COALESCE(?normalizedLabel, ""))) = LCASE(STR(?resourceName))
         || LCASE(STR(?surfaceForm)) = LCASE(STR(?resourceName)))
}
ORDER BY ?resourceName ?startOffset
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "name",
            "string",
            "Optional substring of a resource name; omit or use an empty string for all resources.",
            default="",
        ),
        QAParam(
            "resource_graph",
            "iri",
            "Named graph holding the resource catalogue; omit for the default.",
            default="https://www.brainkb.org/resources/",
        ),
        QAParam(
            "entity_graph",
            "iri",
            "Named graph holding the named-entity extraction; omit for the default.",
            default="https://www.brainkb.org/named-entity/",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=300,
            minimum=1,
            maximum=1000,
        ),
    ),
))

#******************************************************************************
# Curation and completeness across the two graphs.

################################################################################
# Optional user parameter query
# One shared curation backlog
################################################################################

register(QAQuery(
    id="rb_unmapped_curation_backlog",
    category=CATEGORY,
    question="Which labels failed to ground to an ontology on either side for the same paper?",
    notes=(
        "Use to build one curation worklist from both pipelines instead of two. "
        "Relevant requests include: 'What still needs mapping for this paper?'; "
        "'Which labels did the mappers give up on?'; 'Where are the grounding "
        "gaps across both graphs?'. "
        "No input beyond the two graphs; limit defaults to 300 rows. "
        "Returns ?doi, ?side (\"resource_scope\" or \"named_entity\"), ?label "
        "(the ungrounded label, from a bkr:scopeStatement line beginning "
        "'unmapped ' or from an entity with no skos match and no resolved "
        "concept), ?detail (the scope's mapping status, or the entity class) "
        "and ?mentions for the entity rows. "
        "The two sides are listed side by side so the same label appearing "
        "twice is one curation job, not two; sort by ?label to spot those. "
        "Resource-side rows depend on the export writing the 'unmapped <field>: "
        "<label>' convention, so a graph without it shows only entity rows."
    ),
    example={"limit": 300},
    sparql="""
PREFIX bkr:  <https://brainkb.org/resource/>
PREFIX ner:  <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>

SELECT DISTINCT ?doi ?side ?label ?detail (COUNT(DISTINCT ?mention) AS ?mentions)
WHERE {
  {
    GRAPH {{resource_graph}} {
      ?scope bkr:scopeStatement ?statement ;
             bkr:scopeAssertedIn ?paper .
      FILTER(STRSTARTS(LCASE(STR(?statement)), "unmapped "))
      OPTIONAL { ?paper ner:doi ?doi }
      BIND(REPLACE(STR(?statement), "^[Uu]nmapped ", "") AS ?label)
      BIND("scope_statement" AS ?detail)
    }
    GRAPH {{entity_graph}} { ?paper a ner:Publication }
    BIND("resource_scope" AS ?side)
  }
  UNION
  {
    GRAPH {{resource_graph}} {
      ?paper a ner:Publication .
      OPTIONAL { ?paper ner:doi ?doi }
    }
    GRAPH {{entity_graph}} {
      ?entity a ner:NamedEntity ;
              prov:hadPrimarySource ?paper ;
              rdfs:label ?label .
      OPTIONAL { ?entity ner:hasMention ?mention }
      OPTIONAL {
        ?entity a ?entityClass .
        FILTER(?entityClass != ner:NamedEntity)
        BIND(REPLACE(STR(?entityClass), "^.*[/#]", "") AS ?detail)
      }
      FILTER NOT EXISTS { ?entity skos:exactMatch ?anyTerm }
      FILTER NOT EXISTS { ?entity ner:resolvedToConcept ?anyConcept }
    }
    BIND("named_entity" AS ?side)
  }
}
GROUP BY ?doi ?side ?label ?detail
ORDER BY ?label ?side
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "resource_graph",
            "iri",
            "Named graph holding the resource catalogue; omit for the default.",
            default="https://www.brainkb.org/resources/",
        ),
        QAParam(
            "entity_graph",
            "iri",
            "Named graph holding the named-entity extraction; omit for the default.",
            default="https://www.brainkb.org/named-entity/",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=300,
            minimum=1,
            maximum=1000,
        ),
    ),
))

################################################################################
# Optional user parameter query
# One row per paper: is the cross-graph record complete and consistent?
################################################################################

register(QAQuery(
    id="rb_paper_bridge_summary",
    category=CATEGORY,
    question="For each paper, how complete and consistent is the pairing of its resource records and its named entities?",
    notes=(
        "Use as the cross-graph dashboard row before any deeper question, and "
        "to triage a large ingest. Relevant requests include: 'Which papers are "
        "fully processed on both sides?'; 'Give me a per-paper overview of the "
        "two graphs'; 'Which papers are worth a cross-graph question?'. "
        "No input beyond the two graphs; limit defaults to 200 rows. "
        "Returns ?doi, ?title, ?resources, ?scopeTerms (distinct ontology terms "
        "any scope of that paper declares), ?namedEntities, ?groundedEntities "
        "(entities with an ontology grounding), ?sharedTerms (terms that are "
        "both a scope target and an entity grounding) and ?sharedChecksums "
        "(document checksums recorded on both sides). "
        "Read ?sharedTerms against ?scopeTerms and ?groundedEntities: a high "
        "?groundedEntities with ?sharedTerms = 0 means the two halves are about "
        "different things, or one side mapped to a different ontology. "
        "?sharedChecksums = 0 means offsets and quotes are not comparable — see "
        "rb_document_version_agreement. Only papers present in both graphs "
        "appear."
    ),
    example={"limit": 200},
    sparql="""
PREFIX dcat:  <http://www.w3.org/ns/dcat#>
PREFIX bkr:   <https://brainkb.org/resource/>
PREFIX bkrls: <https://brainkb.org/resource/lifesci/>
PREFIX ner:   <https://brainkb.org/ner/>
PREFIX prov:  <http://www.w3.org/ns/prov#>
PREFIX skos:  <http://www.w3.org/2004/02/skos/core#>

SELECT ?doi ?title ?resources ?namedEntities
       (COALESCE(?scopeTermCount, 0) AS ?scopeTerms)
       (COALESCE(?groundedCount, 0) AS ?groundedEntities)
       (COALESCE(?sharedCount, 0) AS ?sharedTerms)
       (COALESCE(?sharedChecksumCount, 0) AS ?sharedChecksums)
WHERE {
  # Each half is counted in its own sub-select, so the two sides never multiply
  # each other's rows — the whole-pattern form of this query does not finish on
  # a real ingest.
  {
    SELECT ?paper (COUNT(DISTINCT ?resource) AS ?resources)
    WHERE {
      GRAPH {{resource_graph}} {
        ?record a dcat:CatalogRecord ;
                prov:hadPrimarySource ?paper ;
                bkr:describesResource ?resource .
      }
    }
    GROUP BY ?paper
  }
  {
    SELECT ?paper (COUNT(DISTINCT ?entity) AS ?namedEntities)
    WHERE {
      GRAPH {{entity_graph}} {
        ?entity a ner:NamedEntity ;
                prov:hadPrimarySource ?paper .
      }
    }
    GROUP BY ?paper
  }
  OPTIONAL {
    SELECT ?paper (COUNT(DISTINCT ?scopeTerm) AS ?scopeTermCount)
    WHERE {
      GRAPH {{resource_graph}} {
        ?scope bkr:scopeAssertedIn ?paper .
        VALUES ?facetProperty { bkrls:appliesToTaxon bkrls:appliesToAnatomicalStructure
                                bkrls:appliesToCellType bkrls:appliesToAssay
                                bkrls:appliesToCondition bkr:appliesToModality }
        ?scope ?facetProperty ?scopeTerm .
      }
    }
    GROUP BY ?paper
  }
  OPTIONAL {
    SELECT ?paper (COUNT(DISTINCT ?groundedEntity) AS ?groundedCount)
    WHERE {
      GRAPH {{entity_graph}} {
        ?groundedEntity a ner:NamedEntity ;
                        prov:hadPrimarySource ?paper ;
                        skos:exactMatch ?groundingTerm .
      }
    }
    GROUP BY ?paper
  }
  OPTIONAL {
    SELECT ?paper (COUNT(DISTINCT ?sharedTerm) AS ?sharedCount)
    WHERE {
      GRAPH {{entity_graph}} {
        ?sharedEntity a ner:NamedEntity ;
                      prov:hadPrimarySource ?paper ;
                      skos:exactMatch ?sharedTerm .
      }
      GRAPH {{resource_graph}} {
        ?sharedScope bkr:scopeAssertedIn ?paper ;
                     ?sharedFacet ?sharedTerm .
        FILTER(?sharedFacet IN (bkrls:appliesToTaxon, bkrls:appliesToAnatomicalStructure,
                                bkrls:appliesToCellType, bkrls:appliesToAssay,
                                bkrls:appliesToCondition, bkr:appliesToModality))
      }
    }
    GROUP BY ?paper
  }
  OPTIONAL {
    SELECT ?paper (COUNT(DISTINCT ?sharedSum) AS ?sharedChecksumCount)
    WHERE {
      GRAPH {{resource_graph}} { ?paper ner:hasDocumentVersion ?sv1 . ?sv1 ner:checksum ?sharedSum }
      GRAPH {{entity_graph}}   { ?paper ner:hasDocumentVersion ?sv2 . ?sv2 ner:checksum ?sharedSum }
    }
    GROUP BY ?paper
  }
  GRAPH {{resource_graph}} {
    OPTIONAL { ?paper ner:doi ?doi }
    OPTIONAL { ?paper ner:title ?title }
  }
}
ORDER BY DESC(?sharedTerms) ?title
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "resource_graph",
            "iri",
            "Named graph holding the resource catalogue; omit for the default.",
            default="https://www.brainkb.org/resources/",
        ),
        QAParam(
            "entity_graph",
            "iri",
            "Named graph holding the named-entity extraction; omit for the default.",
            default="https://www.brainkb.org/named-entity/",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of paper rows to return.",
            default=200,
            minimum=1,
            maximum=1000,
        ),
    ),
))
