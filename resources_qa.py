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
# @File    : resources_qa.py
# @Software: PyCharm

"""Canned SPARQL queries about research resources (tools, datasets, models, ...).

Covers the resource knowledge graphs structsense extracts from papers, in the
BrainKB Resource Ontology (bkr). Each QAQuery below is served by
brainkb_qa_list / brainkb_qa_run in server.py. To add one, copy a whole
register(...) block, give it a new id — see guide.md.
"""

from qa_registry import QAParam, QAQuery, register, register_category

# The description is shown to the MCP agent in the brainkb_qa_list menu; it is
# the only thing the agent reads before deciding to open this category. Name the
# kinds of questions answered, and keep it current as queries are added.

CATEGORY = register_category(
    "resources",
    "Explore research resources (software tools, libraries, pipelines, models, "
    "datasets, atlases, benchmarks) catalogued from papers: which resources "
    "exist, of what type, and which papers describe or only mention each. "
    "Inspect applicability scope — what a resource is declared for, validated "
    "on, observed in use on, and explicitly out of scope for — by taxon, "
    "anatomy, cell type, assay, modality and task, with quoted evidence. "
    "Retrieve assumptions and their criticality, failure modes (including "
    "silent failures), inputs and outputs, benchmark results, identifiers, "
    "versions, licences and access conditions. "
    "Audit record completeness, fields the source did not state, field-level "
    "evidence quotes, extraction runs and models, scope-to-ontology mapping "
    "decisions and unmapped labels, and curation gaps (resources with no scope, "
    "resources named but never described). "
    "Compare what different papers claim about the same resource, and join a "
    "paper's resources to the named entities extracted from it. "
    "Track maintenance from provenance-tracked activity assessments (when, by "
    "which checker version, from which queries) against code hosts and package "
    "registries, ontology registries (OBO Foundry, OLS), RRIDs and data archives "
    "(GEO): current activity status (active, stale, inactive, archived, "
    "deprecated, unavailable, unknown; datasets: available, embargoed), last "
    "commit/release dates, status history and "
    "changes, drift between the version a paper states and what the registry "
    "shows today, and resources never checked. "
    "Every query reads one named graph, given by its optional `graph` "
    "parameter (default https://www.brainkb.org/resources/). When the user "
    "means a particular space or workspace, find its graph IRI with "
    "rs_resource_graphs, brainkb_read_space or brainkb_list_spaces, and pass "
    "that same `graph` to every query. "
    "Resolve user wording first: rs_find_resource for resource names and IRIs, "
    "rs_list_papers for paper titles and DOIs. "
    "Use for questions such as 'Which tools does this paper use?', 'What is "
    "this tool validated on?', 'What is it not suitable for?', 'Does it fail "
    "silently?', 'Which version and licence?', or 'Do papers disagree about it?'. "
    "Every claim is attributed to the paper that states it; an absent field "
    "means the source was silent, not that the value is open or none."
)

################################################################################
# Every query below is self-contained: its own PREFIXes, its own params, no
# shared constants. Copy a whole register(...) block to start a new one.
#
# `question`, `notes` and `example` are what the MCP agent sees in
# brainkb_qa_list — it chooses a query and fills its params from those alone,
# so say there (not in a code comment) anything the agent needs to know.

#******************************************************************************
# Parameter lookup helpers — resolve user wording into the graph, resource
# names and paper titles/DOIs the competency-question queries filter on.

################################################################################
# Optional user parameter query — result limit only
# Helper — Which named graphs hold resource data?
################################################################################

register(QAQuery(
    id="rs_resource_graphs",
    category=CATEGORY,
    question="Which named graphs contain resource-catalogue data, and how many resources does each hold?",
    notes=(
        "Run this to choose the `graph` parameter of the other resource queries "
        "when the data may not be in the default graph "
        "https://www.brainkb.org/resources/, for example after an ingest into a "
        "user's own space. Relevant requests include: 'Which graphs have "
        "resource records?'; 'Where was the resource KG ingested?'. "
        "No input is required; limit defaults to 100 rows. "
        "Returns ?graph (named-graph IRI) and ?resources (distinct bkr:Resource "
        "IRIs in it), largest first. Pass ?graph exactly as returned, trailing "
        "slash included. Unlike the other queries, this one reads every graph "
        "the SPARQL endpoint can see. An empty result means no graph contains "
        "typed resources."
    ),
    example={"limit": 100},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>

SELECT ?graph (COUNT(DISTINCT ?r) AS ?resources)
WHERE {
  GRAPH ?graph {
    ?r a bkr:Resource .
  }
}
GROUP BY ?graph
ORDER BY DESC(?resources) ?graph
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
# End helper — resource graphs
################################################################################

################################################################################
# Optional user parameter query
# Helper — Find a resource by name
################################################################################

register(QAQuery(
    id="rs_find_resource",
    category=CATEGORY,
    question="Which resources match a name, and what are their IRIs and types?",
    notes=(
        "Run this to resolve the resource a user names (e.g. 'Kilosort', "
        "'Allen CCF') to its exact ?name before passing it as the `name` "
        "parameter of other resource queries, or to see whether a resource is "
        "catalogued at all. "
        "OPTIONAL INPUT: `search`, a case-insensitive substring of the resource "
        "name. Omit it or use an empty string to list every resource. "
        "Returns ?resource (IRI), ?name, ?type (local name of each rdf:type, e.g. "
        "SoftwareApplication, Dataset), ?records (papers that describe it) and "
        "?stub (true when it is only mentioned, never described). A resource "
        "with several types produces several rows. Instance IRIs are UUIDs; "
        "address resources by name. Limit defaults to 1000 rows. The query is "
        "scoped to the named graph given by `graph`."
    ),
    example={"search": "kilosort", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX dcterms: <http://purl.org/dc/terms/>

SELECT ?resource ?name ?type (COUNT(DISTINCT ?rec) AS ?records) ?stub
WHERE {
  VALUES ?requestedText { {{search}} }

  GRAPH {{graph}} {
    ?resource a bkr:Resource ;
              bkr:resourceName ?name .
    FILTER(?requestedText = "" || CONTAINS(LCASE(STR(?name)), LCASE(?requestedText)))

    OPTIONAL {
      ?resource a ?t .
      FILTER(?t != bkr:Resource)
      BIND(REPLACE(STR(?t), "^.*[/#]", "") AS ?type)
    }
    OPTIONAL { ?resource bkr:hasRecord ?rec }
    BIND(EXISTS {
      ?resource dcterms:identifier ?k .
      FILTER(STRSTARTS(STR(?k), "mentioned/"))
    } AS ?stub)
  }
}
GROUP BY ?resource ?name ?type ?stub
ORDER BY ?name ?type
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "search",
            "string",
            "Optional substring of a resource name; omit or use an empty string for all resources.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of resource rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End helper — find resource
################################################################################

################################################################################
# Optional user parameter query
# Helper — List the papers resource records were extracted from
################################################################################

register(QAQuery(
    id="rs_list_papers",
    category=CATEGORY,
    question="Which papers were resources extracted from, with their DOIs, titles and resource counts?",
    notes=(
        "Use to find a paper's title or DOI before filtering other resource "
        "queries by paper, or to answer 'Which papers are in the resource "
        "catalogue?'. "
        "OPTIONAL INPUT: `search`, a case-insensitive substring matched against "
        "the DOI, the paper IRI and the title. Omit it or use an empty string to "
        "list all papers. "
        "Returns ?paper (IRI; the same ner:Publication node the paper's NER "
        "graph uses), ?doi and ?title (optional), and ?described (resources the "
        "paper has a record for). Limit defaults to 1000 rows. The query is "
        "scoped to the named graph given by `graph`."
    ),
    example={"search": "", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT ?paper ?doi ?title (COUNT(DISTINCT ?resource) AS ?described)
WHERE {
  VALUES ?requestedText { {{search}} }

  GRAPH {{graph}} {
    ?record bkr:describesResource ?resource ;
            prov:hadPrimarySource ?paper .
    OPTIONAL { ?paper ner:doi ?doi }
    OPTIONAL { ?paper ner:title ?title }
  }

  FILTER(
    ?requestedText = "" ||
    CONTAINS(LCASE(STR(?paper)), LCASE(?requestedText)) ||
    (BOUND(?doi) && CONTAINS(LCASE(STR(?doi)), LCASE(?requestedText))) ||
    (BOUND(?title) && CONTAINS(LCASE(STR(?title)), LCASE(?requestedText)))
  )
}
GROUP BY ?paper ?doi ?title
ORDER BY ?title ?paper
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "search",
            "string",
            "Optional substring of a DOI, paper IRI or title; omit or use an empty string for all papers.",
            default="",
        ),
        QAParam(
            "limit",
            "int",
            "Maximum number of paper rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End helper — list papers
################################################################################

#******************************************************************************
# Competency Questions — A. What is catalogued, and who attests it

################################################################################
# Optional user parameter query
# CQ01 — Which resources are catalogued, of what type, by how many papers?
################################################################################

register(QAQuery(
    id="rs_catalogue_overview",
    category=CATEGORY,
    question="Which resources does the catalogue hold, of what type, and how many papers describe or reference each?",
    notes=(
        "Use for 'What resources are catalogued?', 'Which tools are most "
        "used across papers?', 'List the datasets'. "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name; omit for all. "
        "Returns ?resource, ?name, ?type (extracted type, e.g. software, dataset), "
        "?records (papers that describe it) and ?papers (papers that reference "
        "it, described or only mentioned), most-referenced first. Mention-only "
        "stubs are excluded; see rs_curation_gaps for them. Limit defaults to "
        "1000 rows. Scoped to the named graph given by `graph`."
    ),
    example={"name": "", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX dcterms: <http://purl.org/dc/terms/>

SELECT ?resource ?name ?type (COUNT(DISTINCT ?rec) AS ?records) (COUNT(DISTINCT ?paper) AS ?papers)
WHERE {
  VALUES ?requestedName { {{name}} }

  GRAPH {{graph}} {
    ?resource a bkr:Resource ; bkr:resourceName ?name .
    FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?name)), LCASE(?requestedName)))
    OPTIONAL { ?resource bkr:extractedType ?t . BIND(STRAFTER(STR(?t), "/extracted-type/") AS ?type) }
    OPTIONAL { ?resource bkr:hasRecord ?rec }
    OPTIONAL { ?resource dcterms:isReferencedBy ?paper }
    FILTER NOT EXISTS { ?resource dcterms:identifier ?k . FILTER(STRSTARTS(STR(?k), "mentioned/")) }
  }
}
GROUP BY ?resource ?name ?type
ORDER BY DESC(?papers) ?name
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
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ01
################################################################################

################################################################################
# Optional user parameter query
# CQ02 — Which resources are described by more than one paper?
################################################################################

register(QAQuery(
    id="rs_multi_paper_resources",
    category=CATEGORY,
    question="Which resources are described by more than one paper, and by which papers?",
    notes=(
        "Use for 'Which tools appear in several papers?', 'Which papers both "
        "describe this dataset?'. A resource is one node across papers with one "
        "record per describing paper. "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name; omit for all. "
        "Returns ?resource, ?name, ?paper, ?title, ?doi and ?record, one row per "
        "(resource, paper). To compare what those papers claim, follow up with "
        "rs_compare_papers. Limit defaults to 1000 rows. Scoped to the named "
        "graph given by `graph`."
    ),
    example={"name": "", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT ?resource ?name ?paper ?title ?doi ?record
WHERE {
  VALUES ?requestedName { {{name}} }

  GRAPH {{graph}} {
    ?resource a bkr:Resource ; bkr:resourceName ?name ; bkr:hasRecord ?record .
    FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?name)), LCASE(?requestedName)))
    ?record prov:hadPrimarySource ?paper .
    OPTIONAL { ?paper ner:title ?title }
    OPTIONAL { ?paper ner:doi ?doi }
    { SELECT ?resource WHERE { ?resource bkr:hasRecord ?rc } GROUP BY ?resource HAVING (COUNT(DISTINCT ?rc) > 1) }
  }
}
ORDER BY ?name ?title
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
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ02
################################################################################

################################################################################
# Optional user parameter query
# CQ03 — Which papers attest a resource, when, and by which model?
################################################################################

register(QAQuery(
    id="rs_resource_attestation",
    category=CATEGORY,
    question="Which papers attest a given resource, through which record, extracted when and by which model?",
    notes=(
        "Use for 'Where does this resource come from?', 'Which paper says "
        "Kilosort exists, and which model extracted it?'. "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name (resolve with rs_find_resource); omit for every resource. "
        "Returns ?name, ?paper, ?title, ?doi, ?record, ?extraction (the "
        "bkr:AutomatedExtraction run), ?ended (run end time) and ?model (the "
        "language model's name). Limit defaults to 1000 rows. Scoped to the "
        "named graph given by `graph`."
    ),
    example={"name": "kilosort", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT ?name ?paper ?title ?doi ?record ?extraction ?ended ?model
WHERE {
  VALUES ?requestedName { {{name}} }

  GRAPH {{graph}} {
    ?resource a bkr:Resource ; bkr:resourceName ?name ; bkr:hasRecord ?record .
    FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?name)), LCASE(?requestedName)))
    ?record prov:hadPrimarySource ?paper .
    OPTIONAL { ?paper ner:title ?title }
    OPTIONAL { ?paper ner:doi ?doi }
    OPTIONAL { ?record prov:wasGeneratedBy ?extraction .
               OPTIONAL { ?extraction prov:endedAtTime ?ended }
               OPTIONAL { ?extraction prov:wasAssociatedWith ?agent . ?agent a ner:LanguageModelAgent ; ner:modelName ?model } }
  }
}
ORDER BY ?name ?title
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
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ03
################################################################################

################################################################################
# Optional user parameter query
# CQ04 — For each paper, which resources does it describe or only mention?
################################################################################

register(QAQuery(
    id="rs_paper_resources",
    category=CATEGORY,
    question="For each paper, which resources does it describe, and which does it only mention?",
    notes=(
        "Use for 'Which tools does this paper use?', 'What resources does "
        "paper X describe?', 'Which resources are only named in this paper?'. "
        "OPTIONAL INPUT: `paper`, a case-insensitive substring of the paper's "
        "title, DOI or IRI (resolve with rs_list_papers); omit for every paper. "
        "Returns ?paper, ?title, ?how ('described' or 'mentioned only'), "
        "?resource, ?name and ?describedElsewhere (for mention-only rows: true "
        "when another paper in the graph describes the resource). Limit "
        "defaults to 1000 rows. Scoped to the named graph given by `graph`."
    ),
    example={"paper": "", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX dcterms: <http://purl.org/dc/terms/>

SELECT DISTINCT ?paper ?title ?how ?resource ?name ?describedElsewhere
WHERE {
  VALUES ?requestedPaper { {{paper}} }

  GRAPH {{graph}} {
    {
      ?rec bkr:describesResource ?resource ; prov:hadPrimarySource ?paper .
      BIND("described" AS ?how)
    } UNION {
      ?rec prov:hadPrimarySource ?paper ; dcterms:references ?resource .
      FILTER NOT EXISTS { ?own bkr:describesResource ?resource ; prov:hadPrimarySource ?paper }
      BIND("mentioned only" AS ?how)
      BIND(EXISTS { ?resource bkr:hasRecord ?other } AS ?describedElsewhere)
    }
    ?resource bkr:resourceName ?name .
    OPTIONAL { ?paper ner:title ?title }
    OPTIONAL { ?paper ner:doi ?doi }
  }

  FILTER(
    ?requestedPaper = "" ||
    CONTAINS(LCASE(STR(?paper)), LCASE(?requestedPaper)) ||
    (BOUND(?doi) && CONTAINS(LCASE(STR(?doi)), LCASE(?requestedPaper))) ||
    (BOUND(?title) && CONTAINS(LCASE(STR(?title)), LCASE(?requestedPaper)))
  )
}
ORDER BY ?title ?how ?name
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "paper",
            "string",
            "Optional substring of a paper title, DOI or IRI; omit or use an empty string for all papers.",
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
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ04
################################################################################

#******************************************************************************
# Competency Questions — B. Applicability scope

################################################################################
# Optional user parameter query
# CQ05 — Declared, validated, observed and excluded scope per resource
################################################################################

register(QAQuery(
    id="rs_scope_overview",
    category=CATEGORY,
    question="For each resource, what is it declared for, validated on, observed in use on, and out of scope for — on which taxa, anatomy and cell types?",
    notes=(
        "Use for 'What is this tool for?', 'What species does it apply to?', "
        "'Summarise the applicability of X'. For only validated, only excluded "
        "or only observed scopes with their quotes, prefer rs_validated_scopes, "
        "rs_out_of_scope or rs_observed_use. "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name; omit for all. "
        "Returns ?name, ?claim ('1 declared', '2 validated', '3 observed in "
        "use', '4 out of scope'), ?statement, ?evidenceLevel, ?taxon, ?anatomy, "
        "?cellType (ontology IRIs), ?title (paper asserting it) and ?scope. "
        "Facet IRIs appear only for accepted tool-backed mappings. A scope with "
        "several facets produces several rows. Limit defaults to 1000 rows. "
        "Scoped to the named graph given by `graph`."
    ),
    example={"name": "", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX bkrls: <https://brainkb.org/resource/lifesci/>
PREFIX ner: <https://brainkb.org/ner/>

SELECT ?name ?claim ?statement ?evidenceLevel ?taxon ?anatomy ?cellType ?title ?scope
WHERE {
  VALUES ?requestedName { {{name}} }
  VALUES (?p ?claim) { (bkr:hasDeclaredScope "1 declared") (bkr:hasValidatedScope "2 validated")
                       (bkr:hasObservedScope "3 observed in use") (bkr:hasExcludedScope "4 out of scope") }

  GRAPH {{graph}} {
    ?resource bkr:resourceName ?name ; ?p ?scope .
    FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?name)), LCASE(?requestedName)))
    OPTIONAL { ?scope bkr:scopeStatement ?statement }
    OPTIONAL { ?scope bkr:scopeEvidenceLevel ?l . BIND(STRAFTER(STR(?l), "/evidence-level/") AS ?evidenceLevel) }
    OPTIONAL { ?scope bkrls:appliesToTaxon ?taxon }
    OPTIONAL { ?scope bkrls:appliesToAnatomicalStructure ?anatomy }
    OPTIONAL { ?scope bkrls:appliesToCellType ?cellType }
    OPTIONAL { ?scope bkr:scopeAssertedIn ?paper . OPTIONAL { ?paper ner:title ?title } }
  }
}
ORDER BY ?name ?claim
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
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ05
################################################################################

################################################################################
# Optional user parameter query
# CQ06 — Validated scopes and the evidence behind each
################################################################################

register(QAQuery(
    id="rs_validated_scopes",
    category=CATEGORY,
    question="On what has each resource been validated, and what quoted evidence backs each validation?",
    notes=(
        "Use for 'Has this tool been validated on mouse data?', 'What evidence "
        "shows X works on Y?'. A validated scope is kept only with evidence or a "
        "benchmark result; one without is demoted to declared. "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name; omit for all. "
        "Returns ?name, ?statement, ?quote (verbatim source evidence), ?title "
        "(asserting paper), ?validation (validating activity or result) and "
        "?scope. Several quotes produce several rows. Limit defaults to 1000 "
        "rows. Scoped to the named graph given by `graph`."
    ),
    example={"name": "", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX ner: <https://brainkb.org/ner/>

SELECT ?name ?statement ?quote ?title ?validation ?scope
WHERE {
  VALUES ?requestedName { {{name}} }

  GRAPH {{graph}} {
    ?resource bkr:resourceName ?name ; bkr:hasValidatedScope ?scope .
    FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?name)), LCASE(?requestedName)))
    OPTIONAL { ?scope bkr:scopeStatement ?statement }
    OPTIONAL { ?scope bkr:scopeValidatedBy ?validation }
    OPTIONAL { ?scope bkr:evidencedByMention ?m . ?m ner:evidenceText ?quote }
    OPTIONAL { ?scope bkr:scopeAssertedIn ?paper . OPTIONAL { ?paper ner:title ?title } }
  }
}
ORDER BY ?name
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
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ06
################################################################################

################################################################################
# Optional user parameter query
# CQ07 — What each resource is explicitly not for
################################################################################

register(QAQuery(
    id="rs_out_of_scope",
    category=CATEGORY,
    question="What is each resource explicitly NOT suitable for (out of scope), and on what evidence?",
    notes=(
        "Use for 'What shouldn't I use this tool for?', 'Is X unsuitable for "
        "human data?', 'What are the stated exclusions?'. "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name; omit for all. "
        "Returns ?name, ?statement, ?taxon (IRI, when mapped), ?quote "
        "(verbatim evidence), ?title (asserting paper) and ?scope. Absence of a "
        "row does not mean a use is supported; the source may simply be silent. "
        "Limit defaults to 1000 rows. Scoped to the named graph given by `graph`."
    ),
    example={"name": "", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX bkrls: <https://brainkb.org/resource/lifesci/>
PREFIX ner: <https://brainkb.org/ner/>

SELECT ?name ?statement ?taxon ?quote ?title ?scope
WHERE {
  VALUES ?requestedName { {{name}} }

  GRAPH {{graph}} {
    ?resource bkr:resourceName ?name ; bkr:hasExcludedScope ?scope .
    FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?name)), LCASE(?requestedName)))
    OPTIONAL { ?scope bkr:scopeStatement ?statement }
    OPTIONAL { ?scope bkrls:appliesToTaxon ?taxon }
    OPTIONAL { ?scope bkr:evidencedByMention ?m . ?m ner:evidenceText ?quote }
    OPTIONAL { ?scope bkr:scopeAssertedIn ?paper . OPTIONAL { ?paper ner:title ?title } }
  }
}
ORDER BY ?name
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
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ07
################################################################################

################################################################################
# Optional user parameter query
# CQ08 — How each resource was used across the corpus
################################################################################

register(QAQuery(
    id="rs_observed_use",
    category=CATEGORY,
    question="How was each resource used across the papers: on what, and in which paper?",
    notes=(
        "Use for 'How have papers used this tool?', 'Which studies applied X "
        "to rat data?'. Observed scopes record what a paper reports having done "
        "with a resource, typically a third-party resource the study used. "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name; omit for all. "
        "Returns ?name, ?use (statement), ?taxon (IRI, when mapped), ?quote, "
        "?title, ?paper and ?scope. Limit defaults to 1000 rows. Scoped to the "
        "named graph given by `graph`."
    ),
    example={"name": "", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX bkrls: <https://brainkb.org/resource/lifesci/>
PREFIX ner: <https://brainkb.org/ner/>

SELECT ?name ?use ?taxon ?quote ?title ?paper ?scope
WHERE {
  VALUES ?requestedName { {{name}} }

  GRAPH {{graph}} {
    ?resource bkr:resourceName ?name ; bkr:hasObservedScope ?scope .
    FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?name)), LCASE(?requestedName)))
    OPTIONAL { ?scope bkr:scopeStatement ?use }
    OPTIONAL { ?scope bkrls:appliesToTaxon ?taxon }
    OPTIONAL { ?scope bkr:evidencedByMention ?m . ?m ner:evidenceText ?quote }
    OPTIONAL { ?scope bkr:scopeAssertedIn ?paper . OPTIONAL { ?paper ner:title ?title } }
  }
}
ORDER BY ?name ?title
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
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ08
################################################################################

################################################################################
# Optional user parameter query
# CQ09 — Which resources apply to a given taxon, under which kind of claim?
################################################################################

register(QAQuery(
    id="rs_resources_by_taxon",
    category=CATEGORY,
    question="Which resources apply to a given taxon (species), under which kind of claim?",
    notes=(
        "Use for 'Which tools work on mouse?', 'What resources were validated "
        "on human data?', 'Which species are covered?'. "
        "OPTIONAL INPUT: `taxon`, a case-insensitive substring of the taxon IRI, "
        "e.g. 'NCBITaxon_10090' (mouse), 'NCBITaxon_9606' (human), or a full "
        "IRI; omit for every taxon. Taxa are NCBITaxon IRIs, not names: convert "
        "a species name to its NCBITaxon id before passing it. "
        "Returns ?taxon, ?name, ?claim ('declared', 'validated', 'observed in "
        "use', 'out of scope') and ?scopes (count). Note 'out of scope' rows "
        "mean the resource does NOT apply to that taxon. Uses asserted triples "
        "only, no subclass expansion. Limit defaults to 1000 rows. Scoped to "
        "the named graph given by `graph`."
    ),
    example={"taxon": "NCBITaxon_10090", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX bkrls: <https://brainkb.org/resource/lifesci/>

SELECT ?taxon ?name ?claim (COUNT(DISTINCT ?scope) AS ?scopes)
WHERE {
  VALUES ?requestedTaxon { {{taxon}} }
  VALUES (?p ?claim) { (bkr:hasDeclaredScope "declared") (bkr:hasValidatedScope "validated")
                       (bkr:hasObservedScope "observed in use") (bkr:hasExcludedScope "out of scope") }

  GRAPH {{graph}} {
    ?resource bkr:resourceName ?name ; ?p ?scope .
    ?scope bkrls:appliesToTaxon ?taxon .
    FILTER(?requestedTaxon = "" || CONTAINS(LCASE(STR(?taxon)), LCASE(?requestedTaxon)))
  }
}
GROUP BY ?taxon ?name ?claim
ORDER BY ?taxon ?name ?claim
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "taxon",
            "string",
            "Optional substring of an NCBITaxon IRI (e.g. NCBITaxon_10090); omit or use an empty string for all taxa.",
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
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ09
################################################################################

#******************************************************************************
# Competency Questions — C. Assumptions, failure modes, inputs and outputs

################################################################################
# Optional user parameter query
# CQ10 — Assumptions, criticality and violation consequences
################################################################################

register(QAQuery(
    id="rs_assumptions",
    category=CATEGORY,
    question="Which assumptions condition each resource, how critical are they, what breaks if they fail, and what quote says so?",
    notes=(
        "Use for 'What does this tool assume?', 'What preconditions must my "
        "data meet?', 'What happens if the assumption is violated?'. "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name; omit for all. "
        "Returns ?name, ?kind (assumption class local name), ?criticality, "
        "?statement, ?consequence (what breaks on violation), ?quote, ?title "
        "(paper stating it) and ?assumption. Limit defaults to 1000 rows. "
        "Scoped to the named graph given by `graph`."
    ),
    example={"name": "", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX ner: <https://brainkb.org/ner/>

SELECT ?name ?kind ?criticality ?statement ?consequence ?quote ?title ?assumption
WHERE {
  VALUES ?requestedName { {{name}} }

  GRAPH {{graph}} {
    ?resource bkr:resourceName ?name ; bkr:hasAssumption ?assumption .
    FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?name)), LCASE(?requestedName)))
    ?assumption bkr:assumptionStatement ?statement ; a ?k .
    FILTER(STRSTARTS(STR(?k), "https://brainkb.org/resource/"))
    BIND(STRAFTER(STR(?k), "https://brainkb.org/resource/") AS ?kind)
    OPTIONAL { ?assumption bkr:assumptionCriticality ?c . BIND(STRAFTER(STR(?c), "/criticality/") AS ?criticality) }
    OPTIONAL { ?assumption bkr:violationConsequence ?consequence }
    OPTIONAL { ?assumption bkr:evidencedByMention ?m . ?m ner:evidenceText ?quote }
    OPTIONAL { ?assumption bkr:assumptionStatedIn ?paper . OPTIONAL { ?paper ner:title ?title } }
  }
}
ORDER BY ?name ?criticality
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
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ10
################################################################################

################################################################################
# Optional user parameter query
# CQ11 — Failure modes, silent failures first
################################################################################

register(QAQuery(
    id="rs_failure_modes",
    category=CATEGORY,
    question="How can each resource fail — which failure modes are silent (output stays plausible), under what condition, with what symptom and severity?",
    notes=(
        "Use for 'How does this tool fail?', 'Does X fail silently?', 'What "
        "are the known limitations?'. "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name; omit for all. "
        "Returns ?name, ?silent (true when the output still looks plausible), "
        "?condition, ?symptom, ?severity, ?quote, ?title (paper stating it) and "
        "?failure, silent failures first. Limit defaults to 1000 rows. Scoped "
        "to the named graph given by `graph`."
    ),
    example={"name": "", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT ?name ?silent ?condition ?symptom ?severity ?quote ?title ?failure
WHERE {
  VALUES ?requestedName { {{name}} }

  GRAPH {{graph}} {
    ?resource bkr:resourceName ?name ; bkr:hasLimitation ?failure .
    FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?name)), LCASE(?requestedName)))
    ?failure a bkr:FailureMode .
    OPTIONAL { ?failure bkr:isSilentFailure ?silent }
    OPTIONAL { ?failure bkr:failureCondition ?condition }
    OPTIONAL { ?failure bkr:failureSymptom ?symptom }
    OPTIONAL { ?failure bkr:failureSeverity ?s . BIND(STRAFTER(STR(?s), "/criticality/") AS ?severity) }
    OPTIONAL { ?failure bkr:evidencedByMention ?m . ?m ner:evidenceText ?quote }
    OPTIONAL { ?failure prov:hadPrimarySource ?paper . OPTIONAL { ?paper ner:title ?title } }
  }
}
ORDER BY DESC(?silent) ?name
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
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ11
################################################################################

################################################################################
# Optional user parameter query
# CQ12 — Inputs and outputs
################################################################################

register(QAQuery(
    id="rs_inputs_outputs",
    category=CATEGORY,
    question="What does each resource take as input and produce as output, in what format?",
    notes=(
        "Use for 'What file formats does this tool accept?', 'What does X "
        "output?', 'Can I feed Y's output into Z?'. "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name; omit for all. "
        "Returns ?name, ?direction ('input' or 'output'), ?io (name), ?format, "
        "?description, ?title (paper stating it) and ?spec. Limit defaults to "
        "1000 rows. Scoped to the named graph given by `graph`."
    ),
    example={"name": "", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT ?name ?direction ?io ?format ?description ?title ?spec
WHERE {
  VALUES ?requestedName { {{name}} }
  VALUES (?p ?direction) { (bkr:expectsInput "input") (bkr:producesOutput "output") }

  GRAPH {{graph}} {
    ?resource bkr:resourceName ?name ; ?p ?spec .
    FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?name)), LCASE(?requestedName)))
    OPTIONAL { ?spec bkr:ioName ?io }
    OPTIONAL { ?spec bkr:ioFormat ?format }
    OPTIONAL { ?spec bkr:ioDescription ?description }
    OPTIONAL { ?spec prov:hadPrimarySource ?paper . OPTIONAL { ?paper ner:title ?title } }
  }
}
ORDER BY ?name ?direction
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
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ12
################################################################################

################################################################################
# Optional user parameter query
# CQ13 — Benchmark results
################################################################################

register(QAQuery(
    id="rs_benchmark_results",
    category=CATEGORY,
    question="Which benchmark results are reported for each resource: metric, score, on which benchmark, with what quote?",
    notes=(
        "Use for 'How well does this tool perform?', 'What accuracy is "
        "reported for X?', 'Which benchmarks was it evaluated on?'. "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name; omit for all. "
        "Returns ?name, ?metric, ?score, ?benchmark (benchmark resource name), "
        "?quote, ?title (reporting paper) and ?result. Scores from different "
        "papers or benchmarks are not directly comparable. Limit defaults to "
        "1000 rows. Scoped to the named graph given by `graph`."
    ),
    example={"name": "", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT ?name ?metric ?score ?benchmark ?quote ?title ?result
WHERE {
  VALUES ?requestedName { {{name}} }

  GRAPH {{graph}} {
    ?resource bkr:resourceName ?name ; bkr:hasAssessment ?result .
    FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?name)), LCASE(?requestedName)))
    ?result a bkr:BenchmarkResult .
    OPTIONAL { ?result bkr:assessmentMetric ?metric }
    OPTIONAL { ?result bkr:assessmentScore ?score }
    OPTIONAL { ?result bkr:onBenchmark ?b . ?b bkr:resourceName ?benchmark }
    OPTIONAL { ?result bkr:evidencedByMention ?m . ?m ner:evidenceText ?quote }
    OPTIONAL { ?result prov:hadPrimarySource ?paper . OPTIONAL { ?paper ner:title ?title } }
  }
}
ORDER BY ?name ?metric
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
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ13
################################################################################

#******************************************************************************
# Competency Questions — D. Identifiers, versions, licence and access

################################################################################
# Optional user parameter query
# CQ14 — Identifiers, scheme, archive and quote
################################################################################

register(QAQuery(
    id="rs_identifiers",
    category=CATEGORY,
    question="Which identifiers (RRID, DOI, URL, accession) does each resource carry, under which scheme, deposited where, and stated by which quote?",
    notes=(
        "Use for 'What is the RRID of this tool?', 'Where is this dataset "
        "deposited?', 'What is X's DOI or accession?'. Only identifiers stated "
        "in the source survive grounding; none listed means the paper gave none. "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name; omit for all. "
        "Returns ?name, ?value, ?scheme, ?archive (repository name), ?quote, "
        "?title (paper whose quote contains the value) and ?identifier. Limit "
        "defaults to 1000 rows. Scoped to the named graph given by `graph`."
    ),
    example={"name": "", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX ner: <https://brainkb.org/ner/>
PREFIX adms: <http://www.w3.org/ns/adms#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT ?name ?value ?scheme ?archive ?quote ?title ?identifier
WHERE {
  VALUES ?requestedName { {{name}} }

  GRAPH {{graph}} {
    ?resource bkr:resourceName ?name ; adms:identifier ?identifier .
    FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?name)), LCASE(?requestedName)))
    ?identifier skos:notation ?value .
    OPTIONAL { ?identifier bkr:identifierScheme ?s . BIND(STRAFTER(STR(?s), "/identifier-type/") AS ?scheme) }
    OPTIONAL { ?resource bkr:depositedIn ?a . ?a bkr:resourceName ?archive }
    OPTIONAL { ?assertion bkr:assertionAbout ?resource ; bkr:assertedProperty ?f ; bkr:evidencedByMention ?m .
               FILTER(STRSTARTS(STR(?f), "stable_identifiers"))
               ?m ner:evidenceText ?quote .
               FILTER(CONTAINS(STR(?quote), STR(?value)))
               OPTIONAL { ?m prov:hadPrimarySource ?paper . OPTIONAL { ?paper ner:title ?title } } }
  }
}
ORDER BY ?name ?value
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
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ14
################################################################################

################################################################################
# Optional user parameter query
# CQ15 — Versions per paper
################################################################################

register(QAQuery(
    id="rs_versions",
    category=CATEGORY,
    question="Which versions of each resource are stated, by which paper, and with what quote?",
    notes=(
        "Use for 'Which version of this tool was used?', 'Do papers use "
        "different versions of X?'. "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name; omit for all. "
        "Returns ?name, ?version, ?latestInItsPaper (true when it is the latest "
        "version the stating paper gives, not the latest release overall), "
        "?title, ?quote and ?versionNode. Limit defaults to 1000 rows. Scoped "
        "to the named graph given by `graph`."
    ),
    example={"name": "", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT ?name ?version ?latestInItsPaper ?title ?quote ?versionNode
WHERE {
  VALUES ?requestedName { {{name}} }

  GRAPH {{graph}} {
    ?resource bkr:resourceName ?name ; bkr:hasVersion ?versionNode .
    FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?name)), LCASE(?requestedName)))
    ?versionNode bkr:versionIdentifier ?version .
    BIND(EXISTS { ?resource bkr:latestVersion ?versionNode } AS ?latestInItsPaper)
    OPTIONAL { ?versionNode prov:hadPrimarySource ?paper . OPTIONAL { ?paper ner:title ?title } }
    OPTIONAL {
      ?assertion bkr:assertionAbout ?resource ; bkr:assertedProperty ?f ; bkr:evidencedByMention ?m .
      FILTER(STRSTARTS(STR(?f), "versions"))
      ?m ner:evidenceText ?quote ; prov:hadPrimarySource ?paper .
      FILTER(CONTAINS(STR(?quote), STR(?version)))
    }
  }
}
ORDER BY ?name ?version
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
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ15
################################################################################

################################################################################
# Optional user parameter query
# CQ16 — Licence, rights and access, and which papers are silent on them
################################################################################

register(QAQuery(
    id="rs_licence_access",
    category=CATEGORY,
    question="What licence, rights statement or access condition is stated for each resource — and which papers state none?",
    notes=(
        "Use for 'What licence is this tool under?', 'Is this dataset openly "
        "accessible?', 'Which resources have no stated licence?'. A missing "
        "licence is the source's silence, never 'open': do not report it as "
        "open or free. "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name; omit for all. "
        "Returns ?name, ?licence (IRI or literal), ?rights, ?accessLevel and "
        "?silentPaper (title or IRI of a paper whose record lists the licence "
        "as not found). Limit defaults to 1000 rows. Scoped to the named graph "
        "given by `graph`."
    ),
    example={"name": "", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX dcterms: <http://purl.org/dc/terms/>

SELECT DISTINCT ?name ?licence ?rights ?accessLevel ?silentPaper
WHERE {
  VALUES ?requestedName { {{name}} }

  GRAPH {{graph}} {
    ?resource a bkr:Resource ; bkr:resourceName ?name ; bkr:hasRecord ?anyRecord .
    FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?name)), LCASE(?requestedName)))
    OPTIONAL { ?resource dcterms:license ?licence }
    OPTIONAL { ?resource bkr:rightsStatement ?rights }
    OPTIONAL { ?resource bkr:hasAccessCondition ?ac . ?ac bkr:accessLevel ?al . BIND(STRAFTER(STR(?al), "/access-level/") AS ?accessLevel) }
    OPTIONAL { ?resource bkr:hasRecord ?rec . ?rec bkr:notFoundField "license" ; prov:hadPrimarySource ?p .
               OPTIONAL { ?p ner:title ?pt } BIND(COALESCE(?pt, STR(?p)) AS ?silentPaper) }
  }
}
ORDER BY ?name
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
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ16
################################################################################

#******************************************************************************
# Competency Questions — E. Grounding, completeness and provenance

################################################################################
# Optional user parameter query
# CQ17 — Record completeness and fields the source did not state
################################################################################

register(QAQuery(
    id="rs_record_completeness",
    category=CATEGORY,
    question="How complete is each resource record, and which fields did the source paper not state?",
    notes=(
        "Use for 'How complete is the record for X?', 'What information is "
        "missing about this tool?', 'Which fields did the paper not mention?'. "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name; omit for all. "
        "Returns ?name, ?title (paper), ?completeness (fraction of fields "
        "filled), ?notFound (one field name per row the source did not state) "
        "and ?record. Limit defaults to 1000 rows. Scoped to the named graph "
        "given by `graph`."
    ),
    example={"name": "", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT ?name ?title ?completeness ?notFound ?record
WHERE {
  VALUES ?requestedName { {{name}} }

  GRAPH {{graph}} {
    ?record bkr:describesResource ?resource ; prov:hadPrimarySource ?paper .
    ?resource bkr:resourceName ?name .
    FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?name)), LCASE(?requestedName)))
    OPTIONAL { ?record bkr:fieldCompleteness ?completeness }
    OPTIONAL { ?record bkr:notFoundField ?notFound }
    OPTIONAL { ?paper ner:title ?title }
  }
}
ORDER BY ?name ?title ?notFound
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
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ17
################################################################################

################################################################################
# Optional user parameter query
# CQ18 — Field-level statements and their anchoring quotes
################################################################################

register(QAQuery(
    id="rs_field_evidence",
    category=CATEGORY,
    question="Which field-level statements does each resource record make, and which verbatim quote anchors each?",
    notes=(
        "Use for 'Where in the paper does it say that?', 'Show the evidence "
        "for each field of X', 'Is this claim grounded in the text?'. "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name; omit for all. "
        "Returns ?name, ?field (record field path, e.g. versions, "
        "stable_identifiers), ?quote (verbatim text), ?method (assertion "
        "method), ?title (paper the quote comes from) and ?assertion. Limit "
        "defaults to 1000 rows. Scoped to the named graph given by `graph`."
    ),
    example={"name": "", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT ?name ?field ?quote ?method ?title ?assertion
WHERE {
  VALUES ?requestedName { {{name}} }

  GRAPH {{graph}} {
    ?assertion a bkr:ResourceAssertion ; bkr:assertionAbout ?resource ; bkr:assertedProperty ?field .
    ?resource bkr:resourceName ?name .
    FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?name)), LCASE(?requestedName)))
    OPTIONAL { ?assertion bkr:evidencedByMention ?m . ?m ner:evidenceText ?quote .
               OPTIONAL { ?m prov:hadPrimarySource ?paper . OPTIONAL { ?paper ner:title ?title } } }
    OPTIONAL { ?assertion bkr:assertionMethod ?am . BIND(STRAFTER(STR(?am), "/assertion-method/") AS ?method) }
  }
}
ORDER BY ?name ?field
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
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ18
################################################################################

################################################################################
# Optional user parameter query — result limit only
# CQ19 — Extraction runs: paper, pipeline, model, time, record count
################################################################################

register(QAQuery(
    id="rs_extraction_runs",
    category=CATEGORY,
    question="Which extraction runs produced the resource catalogue: for which paper, by which pipeline version and model, when, and how many records?",
    notes=(
        "Use for 'How was the resource catalogue produced?', 'Which model "
        "extracted these resources?', 'When was paper X processed?'. "
        "No input is required. "
        "Returns ?paper, ?title, ?pipeline (pipeline agent label, e.g. "
        "structsense), ?pipelineVersion, ?model, ?ended (run end time) and "
        "?records (resource records the run produced). Limit defaults to 1000 "
        "rows. Scoped to the named graph given by `graph`."
    ),
    example={"limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?paper ?title ?pipeline ?pipelineVersion ?model ?ended (COUNT(DISTINCT ?record) AS ?records)
WHERE {
  GRAPH {{graph}} {
    ?run a bkr:AutomatedExtraction ; bkr:usedResource ?paper .
    ?record prov:wasGeneratedBy ?run ; bkr:describesResource ?r .
    OPTIONAL { ?paper ner:title ?title }
    OPTIONAL { ?run prov:endedAtTime ?ended }
    OPTIONAL { ?run prov:wasAssociatedWith ?pa . ?pa a ner:PipelineAgent ; rdfs:label ?pipeline .
               OPTIONAL { ?pa ner:agentVersion ?pipelineVersion } }
    OPTIONAL { ?run prov:wasAssociatedWith ?ma . ?ma a ner:LanguageModelAgent ; ner:modelName ?model }
  }
}
GROUP BY ?paper ?title ?pipeline ?pipelineVersion ?model ?ended
ORDER BY ?title
LIMIT {{limit}}
""",
    params=(
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
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ19
################################################################################

#******************************************************************************
# Competency Questions — F. Concept mapping

################################################################################
# Optional user parameter query
# CQ20 — Mapping audit
################################################################################

register(QAQuery(
    id="rs_mapping_audit",
    category=CATEGORY,
    question="Which scope labels were mapped to ontology concepts, to what, by which method, with what status and relation?",
    notes=(
        "Use for 'How was \"mouse\" mapped?', 'Which mappings are ambiguous or "
        "only proposed?', 'Audit the ontology mappings'. Only accepted mappings "
        "are asserted on a scope; proposed (weak tool tier) and ambiguous (two "
        "classes tied) decisions are recorded for review, never asserted. A "
        "language-model mapping never carries an IRI. "
        "OPTIONAL INPUTS: `label`, a case-insensitive substring of the mapped "
        "label; `status`, an exact mapping status such as accepted, proposed or "
        "ambiguous. Omit either for all. "
        "Returns ?field (scope dimension), ?label, ?status, ?relation, ?method, "
        "?concept (IRI), ?curie, ?ontology and ?decision. Limit defaults to "
        "1000 rows. Scoped to the named graph given by `graph`."
    ),
    example={"label": "", "status": "", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX ner: <https://brainkb.org/ner/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?field ?label ?status ?relation ?method ?concept ?curie ?ontology ?decision
WHERE {
  VALUES ?requestedLabel { {{label}} }
  VALUES ?requestedStatus { {{status}} }

  GRAPH {{graph}} {
    ?decision a ner:ConceptMappingDecision ; bkr:mappedField ?field ; rdfs:label ?label .
    FILTER(?requestedLabel = "" || CONTAINS(LCASE(STR(?label)), LCASE(?requestedLabel)))
    OPTIONAL { ?decision ner:mappingStatus ?s . BIND(STRAFTER(STR(?s), "/mapping-status/") AS ?status) }
    OPTIONAL { ?decision ner:mappingRelationType ?r . BIND(STRAFTER(STR(?r), "/mapping-relation/") AS ?relation) }
    OPTIONAL { ?decision ner:alignmentMethodRaw ?method }
    OPTIONAL { ?decision ner:conceptIRI ?concept }
    OPTIONAL { ?decision ner:conceptIdentifier ?curie }
    OPTIONAL { ?decision ner:ontologyAcronym ?ontology }
  }

  FILTER(?requestedStatus = "" || (BOUND(?status) && LCASE(?status) = LCASE(?requestedStatus)))
}
ORDER BY ?status ?field ?label
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "label",
            "string",
            "Optional substring of a mapped scope label; omit or use an empty string for all labels.",
            default="",
        ),
        QAParam(
            "status",
            "string",
            "Optional exact mapping status (accepted, proposed, ambiguous); omit or use an empty string for all.",
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
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ20
################################################################################

################################################################################
# Optional user parameter query
# CQ21 — Scope labels that stayed unmapped
################################################################################

register(QAQuery(
    id="rs_unmapped_scope_labels",
    category=CATEGORY,
    question="Which scope labels stayed unmapped to any ontology concept?",
    notes=(
        "Use for 'Which species or regions could not be mapped?', 'What "
        "ontology gaps does the resource catalogue have?'. A label is unmapped "
        "when no tool found a concept or its dimension is not routed to a "
        "mapper; such a scope carries a statement 'unmapped <field>: <label>'. "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name; omit for all. "
        "Returns ?name, ?field (dimension, e.g. taxon, anatomy), ?label and "
        "?scope. Limit defaults to 1000 rows. Scoped to the named graph given "
        "by `graph`."
    ),
    example={"name": "", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>

SELECT ?name ?field ?label ?scope
WHERE {
  VALUES ?requestedName { {{name}} }
  VALUES ?p { bkr:hasDeclaredScope bkr:hasValidatedScope bkr:hasObservedScope bkr:hasExcludedScope }

  GRAPH {{graph}} {
    ?resource bkr:resourceName ?name ; ?p ?scope .
    FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?name)), LCASE(?requestedName)))
    ?scope bkr:scopeStatement ?st .
    FILTER(STRSTARTS(STR(?st), "unmapped "))
    BIND(STRBEFORE(STRAFTER(STR(?st), "unmapped "), ": ") AS ?field)
    BIND(STRAFTER(STR(?st), ": ") AS ?label)
  }
}
ORDER BY ?field ?label
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
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ21
################################################################################

################################################################################
# Optional user parameter query
# CQ22 — Ontology coverage by dimension
################################################################################

register(QAQuery(
    id="rs_concept_coverage",
    category=CATEGORY,
    question="Which ontology concepts (taxa, anatomy, cell types, assays, modalities, tasks, ...) does the resource catalogue apply to, and across how many resources?",
    notes=(
        "Use for 'Which species are best covered by tools?', 'What brain "
        "regions do the catalogued resources target?', 'Which modalities are "
        "supported?'. Counts declared, validated and observed scopes; "
        "out-of-scope claims are excluded. "
        "OPTIONAL INPUT: `dimension`, one of taxon, anatomy, cell type, assay, "
        "condition, developmental stage, modality, task, topic (exact, "
        "case-insensitive); omit for all. "
        "Returns ?dimension, ?concept (IRI) and ?resources (count), most-covered "
        "first within each dimension. Limit defaults to 1000 rows. Scoped to "
        "the named graph given by `graph`."
    ),
    example={"dimension": "taxon", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX bkrls: <https://brainkb.org/resource/lifesci/>

SELECT ?dimension ?concept (COUNT(DISTINCT ?resource) AS ?resources)
WHERE {
  VALUES ?requestedDimension { {{dimension}} }
  VALUES ?p { bkr:hasDeclaredScope bkr:hasValidatedScope bkr:hasObservedScope }
  VALUES (?facet ?dimension) { (bkrls:appliesToTaxon "taxon") (bkrls:appliesToAnatomicalStructure "anatomy")
                               (bkrls:appliesToCellType "cell type") (bkrls:appliesToAssay "assay")
                               (bkrls:appliesToCondition "condition") (bkrls:appliesToDevelopmentalStage "developmental stage")
                               (bkr:appliesToModality "modality") (bkr:appliesToTask "task") (bkr:appliesToTopic "topic") }
  FILTER(?requestedDimension = "" || ?dimension = LCASE(?requestedDimension))

  GRAPH {{graph}} {
    ?resource ?p ?scope . ?scope ?facet ?concept .
  }
}
GROUP BY ?dimension ?concept
ORDER BY ?dimension DESC(?resources)
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "dimension",
            "string",
            "Optional scope dimension (taxon, anatomy, cell type, assay, condition, "
            "developmental stage, modality, task, topic); omit or use an empty string for all.",
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
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ22
################################################################################

#******************************************************************************
# Competency Questions — G. Joining the resource KG and the NER KG of a paper

################################################################################
# Optional user parameter query — two graphs
# CQ23 — Per paper: resources described and named entities found
################################################################################

register(QAQuery(
    id="rs_paper_resources_and_entities",
    category=CATEGORY,
    question="For each paper, how many resources does it describe, and how many named entities did its NER extraction find?",
    notes=(
        "Use for 'How much was extracted from each paper?', 'Compare resource "
        "and entity extraction per paper'. The paper is the same node in both "
        "graphs, so the join is exact. Only papers present in both graphs "
        "appear. "
        "Takes two graphs: `graph` (resource data) and `ner_graph` (named-entity "
        "data, default https://www.brainkb.org/named-entity/; find others with "
        "ne_named_entity_graphs). They may be the same graph. "
        "Returns ?paper, ?title, ?resources and ?namedEntities. Limit defaults "
        "to 1000 rows."
    ),
    example={"limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT ?paper ?title (COUNT(DISTINCT ?resource) AS ?resources) (COUNT(DISTINCT ?entity) AS ?namedEntities)
WHERE {
  GRAPH {{graph}} {
    ?record prov:hadPrimarySource ?paper ; bkr:describesResource ?resource .
    OPTIONAL { ?paper ner:title ?title }
  }
  GRAPH {{ner_graph}} {
    ?entity a ner:NamedEntity ; prov:hadPrimarySource ?paper .
  }
}
GROUP BY ?paper ?title
ORDER BY ?title
LIMIT {{limit}}
""",
    params=(
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
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
        QAParam(
            "ner_graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "Find others with ne_named_entity_graphs.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ23
################################################################################

################################################################################
# Optional user parameter query — two graphs
# CQ24 — Resources also extracted as named entities from the same paper
################################################################################

register(QAQuery(
    id="rs_resources_as_entities",
    category=CATEGORY,
    question="Which catalogued resources were also extracted as named entities from the same paper, and how often are they mentioned?",
    notes=(
        "Use for 'Where in the paper is this tool mentioned?', 'Link the "
        "resource record to its named-entity mentions'. The resource record "
        "says what the resource is; the NER mentions say where the paper talks "
        "about it. Matched on the case-insensitive name within one paper, so "
        "differently spelled names are missed. "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name; omit for all. "
        "Takes two graphs: `graph` (resource data) and `ner_graph` (named-entity "
        "data, default https://www.brainkb.org/named-entity/). "
        "Returns ?title, ?name, ?resource, ?entity (named-entity IRI, usable in "
        "named_entities queries) and ?mentions. Limit defaults to 1000 rows."
    ),
    example={"name": "", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?title ?name ?resource ?entity (COUNT(DISTINCT ?mention) AS ?mentions)
WHERE {
  VALUES ?requestedName { {{name}} }

  GRAPH {{graph}} {
    ?record prov:hadPrimarySource ?paper ; bkr:describesResource ?resource .
    ?resource bkr:resourceName ?name .
    FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?name)), LCASE(?requestedName)))
    OPTIONAL { ?paper ner:title ?title }
  }
  GRAPH {{ner_graph}} {
    ?entity a ner:NamedEntity ; prov:hadPrimarySource ?paper ; rdfs:label ?elabel .
    FILTER(LCASE(STR(?elabel)) = LCASE(STR(?name)))
    OPTIONAL { ?entity ner:hasMention ?mention }
  }
}
GROUP BY ?title ?name ?resource ?entity
ORDER BY ?title ?name
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
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
        QAParam(
            "ner_graph",
            "iri",
            "Named graph holding the named-entity data; omit for the default. "
            "Find others with ne_named_entity_graphs.",
            default="https://www.brainkb.org/named-entity/",
        ),
    ),
))

################################################################################
# End CQ24
################################################################################

#******************************************************************************
# Competency Questions — H. Curation gaps

################################################################################
# Optional user parameter query — result limit only
# CQ25 — What needs curation
################################################################################

register(QAQuery(
    id="rs_curation_gaps",
    category=CATEGORY,
    question="What needs curation: described tools or models with no applicability scope, and resources that are named but never described?",
    notes=(
        "Use for 'What is missing from the resource catalogue?', 'Which "
        "resources need curation?', 'Which tools are mentioned but not "
        "described anywhere?'. "
        "No input is required. "
        "Returns ?gap ('no scope, not declared absent' — an extraction gap for "
        "a software tool, library, pipeline, workflow or model; or 'named, "
        "never described' — a mention-only stub no paper in the graph "
        "describes), ?name, ?mentionedIn (title of a paper naming the stub) "
        "and ?resource. Limit defaults to 1000 rows. Scoped to the named graph "
        "given by `graph`."
    ),
    example={"limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX dcterms: <http://purl.org/dc/terms/>
PREFIX schema: <https://schema.org/>

SELECT DISTINCT ?gap ?name ?mentionedIn ?resource
WHERE {
  GRAPH {{graph}} {
    {
      ?resource bkr:hasRecord ?rec ; bkr:resourceName ?name .
      VALUES ?cls { schema:SoftwareApplication bkr:SoftwareLibrary bkr:Pipeline bkr:Workflow bkr:ComputationalModel }
      ?resource a ?cls .
      FILTER NOT EXISTS { VALUES ?p { bkr:hasDeclaredScope bkr:hasValidatedScope bkr:hasObservedScope bkr:hasExcludedScope }
                          ?resource ?p ?anyScope }
      FILTER NOT EXISTS { ?resource bkr:hasRecord ?r2 . ?r2 bkr:notFoundField "applicability" }
      BIND("no scope, not declared absent" AS ?gap)
    } UNION {
      ?resource dcterms:identifier ?k ; bkr:resourceName ?name .
      FILTER(STRSTARTS(STR(?k), "mentioned/"))
      FILTER NOT EXISTS { ?resource bkr:hasRecord ?r3 }
      OPTIONAL { ?mrec dcterms:references ?resource ; prov:hadPrimarySource ?p . OPTIONAL { ?p ner:title ?mentionedIn } }
      BIND("named, never described" AS ?gap)
    }
  }
}
ORDER BY ?gap ?name
LIMIT {{limit}}
""",
    params=(
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
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ25
################################################################################

#******************************************************************************
# Competency Questions — I. Comparing papers on a shared resource

################################################################################
# Optional user parameter query
# CQ26 — What each paper claims about a shared resource
################################################################################

register(QAQuery(
    id="rs_compare_papers",
    category=CATEGORY,
    question="Where several papers describe the same resource, what does each claim: which scopes, versions, assumptions and failure modes?",
    notes=(
        "Use for 'Do papers agree about this tool?', 'Compare what papers say "
        "about X', 'Does one paper validate a use another rules out?'. Only "
        "resources described by two or more papers appear. Every claim is "
        "attributed to its paper, so readings can be laid side by side; a "
        "difference is a candidate disagreement for the user to judge, not a "
        "confirmed conflict. "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name; omit for all shared resources. "
        "Returns ?name, ?title, ?kind ('scope: declared|validated|observed|out "
        "of scope', 'version', 'assumption', 'failure mode'), ?claim (statement "
        "or version), ?detail (taxon IRI, criticality IRI or silent-failure "
        "flag) and ?paper. Limit defaults to 1000 rows. Scoped to the named "
        "graph given by `graph`."
    ),
    example={"name": "", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX bkrls: <https://brainkb.org/resource/lifesci/>
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT ?name ?title ?kind ?claim ?detail ?paper
WHERE {
  VALUES ?requestedName { {{name}} }

  GRAPH {{graph}} {
    { SELECT ?resource WHERE { ?resource bkr:hasRecord ?rc } GROUP BY ?resource HAVING (COUNT(DISTINCT ?rc) > 1) }
    ?resource bkr:resourceName ?name .
    FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?name)), LCASE(?requestedName)))
    {
      VALUES (?p ?kind) { (bkr:hasDeclaredScope "scope: declared") (bkr:hasValidatedScope "scope: validated")
                          (bkr:hasObservedScope "scope: observed") (bkr:hasExcludedScope "scope: out of scope") }
      ?resource ?p ?n . ?n bkr:scopeAssertedIn ?paper .
      OPTIONAL { ?n bkr:scopeStatement ?claim }
      OPTIONAL { ?n bkrls:appliesToTaxon ?detail }
    } UNION {
      ?resource bkr:hasVersion ?n . ?n bkr:versionIdentifier ?claim ; prov:hadPrimarySource ?paper .
      BIND("version" AS ?kind)
    } UNION {
      ?resource bkr:hasAssumption ?n . ?n bkr:assumptionStatement ?claim ; bkr:assumptionStatedIn ?paper .
      OPTIONAL { ?n bkr:assumptionCriticality ?detail }
      BIND("assumption" AS ?kind)
    } UNION {
      ?resource bkr:hasLimitation ?n . ?n a bkr:FailureMode ; bkr:limitationStatement ?claim ; prov:hadPrimarySource ?paper .
      OPTIONAL { ?n bkr:isSilentFailure ?detail }
      BIND("failure mode" AS ?kind)
    }
    OPTIONAL { ?paper ner:title ?title }
  }
}
ORDER BY ?name ?kind ?title
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
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ26
################################################################################

#******************************************************************************
# Competency Questions — J. Maintenance and activity
#
# An activity check (bkr:hasActivityCheck) records what code hosts and package
# registries reported about a resource at one point in time. Most queries read
# only the latest check per resource; rs_activity_history and
# rs_activity_status_changes read them all.

################################################################################
# Optional user parameter query
# CQ27 — Current activity status of each resource
################################################################################

register(QAQuery(
    id="rs_activity_status",
    category=CATEGORY,
    question="What is the current activity status of each resource, and which resources have a given status?",
    notes=(
        "Use for 'Is this tool still maintained?', 'When was X last active?', "
        "'What is the activity status of the resources in this graph?'. Reads "
        "only the latest activity check per resource. "
        "Also use for 'Give me the archived / inactive / stale resources', 'Which "
        "datasets are embargoed?', 'List active tools'. "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name (resolve with rs_find_resource); omit for every resource. `status`, "
        "one status or a comma-separated list (e.g. 'archived' or "
        "'inactive,stale'); case-insensitive; omit for every status. "
        "Returns ?resource, ?name, ?status (code and ontologies: 'active', 'stale', "
        "'inactive', 'archived', 'deprecated', 'unavailable', 'unknown' when no "
        "source gave a date; datasets: 'available' or 'embargoed' — a dataset is "
        "not stale for being unchanged), ?lastActivity (latest commit, release, "
        "package upload or dataset update; may be unbound), ?checkedOn (when the "
        "assessment was made: prov:generatedAtTime, a full timestamp; a date only "
        "for checks recorded before provenance tracking) and ?assessment (pass to "
        "rs_activity_provenance for who/when/from where) and ?reason (why it got "
        "that status: the rule and the evidence). Resources never checked do not appear — see "
        "rs_unchecked_resources. Limit defaults to 1000 rows. Scoped to the "
        "named graph given by `graph`."
    ),
    example={"name": "", "status": "", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT ?resource ?name ?status ?lastActivity ?checkedOn ?assessment ?reason
WHERE {
  VALUES ?requestedName { {{name}} }
  VALUES ?requestedStatus { {{status}} }

  GRAPH {{graph}} {
    ?resource bkr:resourceName ?name ;
              bkr:hasActivityAssessment ?assessment .
    ?assessment prov:generatedAtTime ?checkedOn ;
           bkr:activityStatus ?statusIRI .
    OPTIONAL { ?assessment bkr:lastActivityDate ?lastActivity }
    OPTIONAL { ?assessment bkr:statusReason ?reason }
    FILTER NOT EXISTS {
      ?resource bkr:hasActivityAssessment ?newer .
      ?newer prov:generatedAtTime ?newerDate .
      FILTER(STR(?newerDate) > STR(?checkedOn))
    }
  }
  BIND(STRAFTER(STR(?statusIRI), "/activity-status/") AS ?status)
  FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?name)), LCASE(?requestedName)))
  FILTER(?requestedStatus = "" ||
         CONTAINS(CONCAT(",", REPLACE(LCASE(?requestedStatus), " ", ""), ","), CONCAT(",", ?status, ",")))
}
ORDER BY ?status ?name
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
            "status",
            "string",
            "Optional status or comma-separated statuses: active, stale, inactive, archived, deprecated, "
            "unavailable, unknown, available, embargoed. Omit for all.",
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
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ27
################################################################################

################################################################################
# Optional user parameter query
# CQ28 — Resources no longer actively maintained
################################################################################

register(QAQuery(
    id="rs_unmaintained_resources",
    category=CATEGORY,
    question="Which resources are no longer actively maintained (stale, inactive, archived, deprecated or unavailable)?",
    notes=(
        "Use for 'Which tools are abandoned?', 'Which resources are archived or "
        "deprecated?', 'Is anything in this catalogue unmaintained?'. Reads only "
        "the latest activity check per resource and keeps statuses 'stale', "
        "'inactive', 'archived', 'deprecated' and 'unavailable' ('inactive' "
        "includes ontologies OBO Foundry lists as inactive or orphaned). Datasets "
        "('available', 'embargoed') and 'unknown' are not included. "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name; omit for every resource. "
        "Returns ?resource, ?name, ?status, ?lastActivity (may be unbound) and "
        "?checkedOn, longest-inactive first. Limit defaults to 1000 rows. "
        "Scoped to the named graph given by `graph`."
    ),
    example={"name": "", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT ?resource ?name ?status ?lastActivity ?checkedOn
WHERE {
  VALUES ?requestedName { {{name}} }

  GRAPH {{graph}} {
    ?resource bkr:resourceName ?name ;
              bkr:hasActivityAssessment ?assessment .
    ?assessment prov:generatedAtTime ?checkedOn ;
           bkr:activityStatus ?statusIRI .
    OPTIONAL { ?assessment bkr:lastActivityDate ?lastActivity }
    FILTER NOT EXISTS {
      ?resource bkr:hasActivityAssessment ?newer .
      ?newer prov:generatedAtTime ?newerDate .
      FILTER(STR(?newerDate) > STR(?checkedOn))
    }
  }
  BIND(STRAFTER(STR(?statusIRI), "/activity-status/") AS ?status)
  FILTER(?status IN ("stale", "inactive", "archived", "deprecated", "unavailable"))
  FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?name)), LCASE(?requestedName)))
}
ORDER BY ?lastActivity ?name
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
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ28
################################################################################

################################################################################
# Optional user parameter query
# CQ29 — Resources with no activity since a given date
################################################################################

register(QAQuery(
    id="rs_inactive_since",
    category=CATEGORY,
    question="Which resources have had no commit, release or package upload since a given date?",
    notes=(
        "Use for 'Which tools have not been updated since 2023?', 'What has "
        "been inactive for over two years?'. Reads only the latest activity "
        "check per resource; resources whose check found no activity date are "
        "left out. "
        "OPTIONAL INPUT: `since`, a date as YYYY-MM-DD (default 2023-01-01) — "
        "resources whose last activity falls before that day are returned; "
        "`name`, a case-insensitive substring of the resource name. "
        "Returns ?resource, ?name, ?lastActivity and ?checkedOn, oldest first. "
        "Limit defaults to 1000 rows. Scoped to the named graph given by `graph`."
    ),
    example={"since": "2023-01-01", "name": "", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT ?resource ?name ?lastActivity ?checkedOn
WHERE {
  VALUES ?requestedName { {{name}} }

  GRAPH {{graph}} {
    ?resource bkr:resourceName ?name ;
              bkr:hasActivityAssessment ?assessment .
    ?assessment prov:generatedAtTime ?checkedOn ;
           bkr:lastActivityDate ?lastActivity .
    FILTER NOT EXISTS {
      ?resource bkr:hasActivityAssessment ?newer .
      ?newer prov:generatedAtTime ?newerDate .
      FILTER(STR(?newerDate) > STR(?checkedOn))
    }
  }
  # Compare the YYYY-MM-DD prefix, so xsd:date and xsd:dateTime values both work.
  FILTER(SUBSTR(STR(?lastActivity), 1, 10) < {{since}})
  FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?name)), LCASE(?requestedName)))
}
ORDER BY ?lastActivity ?name
LIMIT {{limit}}
""",
    params=(
        QAParam(
            "since",
            "string",
            "Cut-off date as YYYY-MM-DD; resources last active before this day are returned.",
            default="2023-01-01",
        ),
        QAParam(
            "name",
            "string",
            "Optional substring of a resource name; omit or use an empty string for all resources.",
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
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ29
################################################################################

################################################################################
# Optional user parameter query — result limit only
# CQ30 — Number of resources per activity status
################################################################################

register(QAQuery(
    id="rs_activity_status_counts",
    category=CATEGORY,
    question="How many resources are in each activity status?",
    notes=(
        "Use for 'How much of the catalogue is still maintained?', 'Summarise "
        "activity status'. Reads only the latest activity check per resource. "
        "Returns ?status and ?resources (distinct resources), largest first. "
        "Unchecked resources are not counted — see rs_unchecked_resources. "
        "Limit defaults to 1000 rows. Scoped to the named graph given by `graph`."
    ),
    example={"limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT ?status (COUNT(DISTINCT ?resource) AS ?resources)
WHERE {
  GRAPH {{graph}} {
    ?resource bkr:hasActivityAssessment ?assessment .
    ?assessment prov:generatedAtTime ?checkedOn ;
           bkr:activityStatus ?statusIRI .
    FILTER NOT EXISTS {
      ?resource bkr:hasActivityAssessment ?newer .
      ?newer prov:generatedAtTime ?newerDate .
      FILTER(STR(?newerDate) > STR(?checkedOn))
    }
  }
  BIND(STRAFTER(STR(?statusIRI), "/activity-status/") AS ?status)
}
GROUP BY ?status
ORDER BY DESC(?resources) ?status
LIMIT {{limit}}
""",
    params=(
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
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ30
################################################################################

################################################################################
# Optional user parameter query
# CQ31 — What each code host or package registry reported
################################################################################

register(QAQuery(
    id="rs_activity_signals",
    category=CATEGORY,
    question="What did each code host or package registry report for each resource (last commit, last release, latest version, archived, deprecated)?",
    notes=(
        "Use for 'Why is X marked stale?', 'What does GitHub / PyPI say about "
        "this tool?', 'What is the latest released version?'. Reads the signals "
        "of the latest activity check per resource; one row per source checked. "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name; omit for every resource. "
        "Returns ?resource, ?name, ?source (code host or registry, e.g. "
        "'github', 'pypi', 'bioconductor', 'obofoundry', 'ols', 'rrid', 'geo'), "
        "?checkedURL, ?lastCommit, ?lastRelease, ?latestVersion, ?archived, "
        "?deprecated, ?available, ?embargoed (datasets), ?answeredAt (when the "
        "source answered), ?decided (true when this signal decided the status), "
        "?declaredStatus (a "
        "registry's curated status, e.g. OBO Foundry 'inactive') and ?note "
        "(says 'matched by title' or similar when the source was found from the "
        "resource's name); any but the first four may be unbound when the source "
        "did not report it. "
        "Limit defaults to 1000 rows. Scoped to the named graph given by `graph`."
    ),
    example={"name": "", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?resource ?name ?source ?checkedURL ?lastCommit ?lastRelease ?latestVersion
       ?archived ?deprecated ?available ?embargoed ?answeredAt ?decided ?declaredStatus ?note
WHERE {
  VALUES ?requestedName { {{name}} }

  GRAPH {{graph}} {
    ?resource bkr:resourceName ?name ;
              bkr:hasActivityAssessment ?assessment .
    ?assessment prov:generatedAtTime ?checkedOn ;
           prov:wasDerivedFrom ?signal .
    ?signal bkr:activitySource ?sourceIRI ;
            bkr:checkedURL ?checkedURL .
    OPTIONAL { ?signal bkr:lastCommitDate ?lastCommit }
    OPTIONAL { ?signal bkr:lastReleaseDate ?lastRelease }
    OPTIONAL { ?signal bkr:latestVersion ?latestVersion }
    OPTIONAL { ?signal bkr:isArchived ?archived }
    OPTIONAL { ?signal bkr:isDeprecated ?deprecated }
    OPTIONAL { ?signal bkr:isAvailable ?available }
    OPTIONAL { ?signal bkr:isEmbargoed ?embargoed }
    OPTIONAL { ?signal prov:generatedAtTime ?answeredAt }
    BIND(EXISTS { ?assessment bkr:statusDecidedBy ?signal } AS ?decided)
    OPTIONAL { ?signal bkr:declaredActivityStatus ?declaredStatus }
    OPTIONAL { ?signal rdfs:comment ?note }
    FILTER NOT EXISTS {
      ?resource bkr:hasActivityAssessment ?newer .
      ?newer prov:generatedAtTime ?newerDate .
      FILTER(STR(?newerDate) > STR(?checkedOn))
    }
  }
  BIND(STRAFTER(STR(?sourceIRI), "/activity-source/") AS ?source)
  FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?name)), LCASE(?requestedName)))
}
ORDER BY ?name ?source
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
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ31
################################################################################

################################################################################
# Optional user parameter query
# CQ32 — Full history of activity checks
################################################################################

register(QAQuery(
    id="rs_activity_history",
    category=CATEGORY,
    question="What is the full history of activity checks for each resource?",
    notes=(
        "Use for 'How has X's activity changed over time?', 'When was this "
        "resource checked?'. Returns every activity check, not only the latest. "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name; omit for every resource. "
        "Returns ?resource, ?name, ?checkedOn, ?status and ?lastActivity (may "
        "be unbound), oldest check first per resource. For status changes only, "
        "use rs_activity_status_changes. Limit defaults to 1000 rows. Scoped to "
        "the named graph given by `graph`."
    ),
    example={"name": "", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT ?resource ?name ?checkedOn ?status ?lastActivity
WHERE {
  VALUES ?requestedName { {{name}} }

  GRAPH {{graph}} {
    ?resource bkr:resourceName ?name ;
              bkr:hasActivityAssessment ?assessment .
    ?assessment prov:generatedAtTime ?checkedOn ;
           bkr:activityStatus ?statusIRI .
    OPTIONAL { ?assessment bkr:lastActivityDate ?lastActivity }
  }
  BIND(STRAFTER(STR(?statusIRI), "/activity-status/") AS ?status)
  FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?name)), LCASE(?requestedName)))
}
ORDER BY ?name ?checkedOn
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
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ32
################################################################################

################################################################################
# Optional user parameter query
# CQ33 — Status changes between consecutive checks
################################################################################

register(QAQuery(
    id="rs_activity_status_changes",
    category=CATEGORY,
    question="Which resources changed activity status between two consecutive checks (e.g. active to stale)?",
    notes=(
        "Use for 'Which tools went stale recently?', 'Has anything been "
        "archived since the last check?', 'Did X become active again?'. Pairs "
        "each check with the one immediately before it and keeps only pairs "
        "whose status differs. "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name; omit for every resource. "
        "Returns ?resource, ?name, ?previousStatus, ?previousCheck, "
        "?currentStatus and ?currentCheck. An empty result means no resource "
        "has changed status, or each has been checked only once. Limit "
        "defaults to 1000 rows. Scoped to the named graph given by `graph`."
    ),
    example={"name": "", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT ?resource ?name ?previousStatus ?previousCheck ?currentStatus ?currentCheck
WHERE {
  VALUES ?requestedName { {{name}} }

  GRAPH {{graph}} {
    ?resource bkr:resourceName ?name ;
              bkr:hasActivityAssessment ?prev, ?curr .
    ?prev prov:generatedAtTime ?previousCheck ; bkr:activityStatus ?prevIRI .
    ?curr prov:generatedAtTime ?currentCheck ; bkr:activityStatus ?currIRI .
    FILTER(STR(?previousCheck) < STR(?currentCheck) && ?prevIRI != ?currIRI)
    FILTER NOT EXISTS {
      ?resource bkr:hasActivityAssessment ?between .
      ?between prov:generatedAtTime ?d .
      FILTER(STR(?d) > STR(?previousCheck) && STR(?d) < STR(?currentCheck))
    }
  }
  BIND(STRAFTER(STR(?prevIRI), "/activity-status/") AS ?previousStatus)
  BIND(STRAFTER(STR(?currIRI), "/activity-status/") AS ?currentStatus)
  FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?name)), LCASE(?requestedName)))
}
ORDER BY ?name ?currentCheck
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
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ33
################################################################################

################################################################################
# Optional user parameter query
# CQ34 — Paper-stated version and lifecycle vs. observed activity
################################################################################

register(QAQuery(
    id="rs_version_drift",
    category=CATEGORY,
    question="How does the version and lifecycle status a paper states compare with what the repository or registry shows today?",
    notes=(
        "Use for 'Is the version in the paper still current?', 'The paper says "
        "this tool is maintained — is it?', 'Which resources have drifted from "
        "what was published?'. Catches drift such as a paper stating "
        "'maintained, v1.0' while the repository has been inactive since 2019. "
        "Compares each paper-stated version with the latest activity check. "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name; omit for every resource. "
        "Returns ?resource, ?name, ?paperStatedVersion, ?paperLifecycle (may be "
        "unbound), ?title and ?paper (the paper stating the version), "
        "?observedStatus and ?latestVersion (reported by a code host or "
        "registry; may be unbound, one row per source reporting one). A "
        "difference is a candidate drift for the user to judge — the paper may "
        "simply predate later releases. Limit defaults to 1000 rows. Scoped to "
        "the named graph given by `graph`."
    ),
    example={"name": "", "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX ner: <https://brainkb.org/ner/>
PREFIX prov: <http://www.w3.org/ns/prov#>

SELECT DISTINCT ?resource ?name ?paperStatedVersion ?paperLifecycle ?title ?paper
                ?observedStatus ?latestVersion
WHERE {
  VALUES ?requestedName { {{name}} }

  GRAPH {{graph}} {
    ?resource bkr:resourceName ?name ;
              bkr:hasVersion ?version ;
              bkr:hasActivityAssessment ?assessment .
    ?version bkr:versionIdentifier ?paperStatedVersion .
    OPTIONAL { ?version bkr:lifecycleStatus ?lc }
    OPTIONAL { ?version prov:hadPrimarySource ?paper . OPTIONAL { ?paper ner:title ?title } }
    ?assessment prov:generatedAtTime ?checkedOn ;
           bkr:activityStatus ?statusIRI .
    OPTIONAL { ?assessment prov:wasDerivedFrom ?s . ?s bkr:latestVersion ?latestVersion }
    FILTER NOT EXISTS {
      ?resource bkr:hasActivityAssessment ?newer .
      ?newer prov:generatedAtTime ?newerDate .
      FILTER(STR(?newerDate) > STR(?checkedOn))
    }
  }
  BIND(STRAFTER(STR(?lc), "/lifecycle-status/") AS ?paperLifecycle)
  BIND(STRAFTER(STR(?statusIRI), "/activity-status/") AS ?observedStatus)
  FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?name)), LCASE(?requestedName)))
}
ORDER BY ?name ?title
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
            "limit",
            "int",
            "Maximum number of rows to return.",
            default=1000,
            minimum=1,
        ),
        QAParam(
            "graph",
            "iri",
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ34
################################################################################

################################################################################
# Optional user parameter query — result limit only
# CQ35 — Resources never checked for activity
################################################################################

register(QAQuery(
    id="rs_unchecked_resources",
    category=CATEGORY,
    question="Which resources have never had an activity check (no repository or package found, or the check was never run)?",
    notes=(
        "Use for 'Which resources have we not checked for maintenance?', "
        "'Which tools have no repository or package link?'. "
        "Returns ?resource, ?name, ?url (access URL; unbound when none is "
        "recorded) and ?stub (true for a mention stub — a resource only named "
        "in a paper, never described, so there is no link to check). Limit "
        "defaults to 1000 rows. Scoped to the named graph given by `graph`."
    ),
    example={"limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX dcterms: <http://purl.org/dc/terms/>

SELECT DISTINCT ?resource ?name ?url ?stub
WHERE {
  GRAPH {{graph}} {
    # A described resource has a record; a mention stub has none and is typed only
    # by its subclass (e.g. bkr:SoftwareLibrary), so `a bkr:Resource` misses it
    # without entailment. The paper also carries bkr:resourceName: neither branch
    # matches it.
    { ?resource bkr:hasRecord ?record }
    UNION
    { ?resource dcterms:identifier ?id FILTER(STRSTARTS(STR(?id), "mentioned/")) }
    ?resource bkr:resourceName ?name .
    OPTIONAL { ?resource bkr:accessURL ?url }
    FILTER NOT EXISTS { ?resource bkr:hasActivityAssessment ?assessment }
    BIND(NOT EXISTS { ?resource bkr:hasRecord ?rec } AS ?stub)
  }
}
ORDER BY ?stub ?name
LIMIT {{limit}}
""",
    params=(
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
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ35
################################################################################

################################################################################
# Optional user parameter query
# CQ36 — Provenance of activity assessments
################################################################################

register(QAQuery(
    id="rs_activity_provenance",
    category=CATEGORY,
    question="When was each activity assessment made, by which software version, and from which sources and queries?",
    notes=(
        "Use for 'When was X last checked?', 'Which version of the checker produced "
        "this status?', 'Where did this date come from?', 'Show the provenance of "
        "this assessment'. Follows the PROV / NIDM-style chain: an "
        "ActivityAssessment (cf. nidm:AcquisitionObject) prov:wasGeneratedBy an "
        "ActivityCheck (cf. nidm:Acquisition) that is dcterms:isPartOf an "
        "ActivityCheckRun (cf. nidm:Session), associated with a prov:SoftwareAgent "
        "carrying its NIDM software version; the assessment prov:wasDerivedFrom "
        "one signal per source, each with the time the answer arrived and the API "
        "URLs queried (prov:hadPrimarySource). "
        "OPTIONAL INPUT: `name`, a case-insensitive substring of the resource "
        "name; `latest`, 1 (default) for each resource's latest assessment only, 0 "
        "for every assessment. "
        "Also answers 'How was the status reached?' (?reason, and ?decided on the "
        "signals that decided it) and 'Which run, with which thresholds?'. "
        "Returns ?resource, ?name, ?assessment, ?assessedAt, ?status, ?reason, "
        "?check, ?checkStarted, ?checkEnded, ?run, ?runStarted, ?runEnded, "
        "?staleAfterDays, ?inactiveAfterDays, ?agent, ?softwareVersion, ?signal, "
        "?source, ?decided, ?answeredAt and ?queried (one row per URL queried). Checks "
        "recorded before provenance tracking have a date-only ?assessedAt and no "
        "run, start time or software version. Limit defaults to 1000 rows. Scoped "
        "to the named graph given by `graph`."
    ),
    example={"name": "", "latest": 1, "limit": 1000},
    sparql="""
PREFIX bkr: <https://brainkb.org/resource/>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX dcterms: <http://purl.org/dc/terms/>
PREFIX nidm: <http://purl.org/nidash/nidm#>

SELECT ?resource ?name ?assessment ?assessedAt ?status ?reason ?check ?checkStarted ?checkEnded
       ?run ?runStarted ?runEnded ?staleAfterDays ?inactiveAfterDays ?agent ?softwareVersion
       ?signal ?source ?decided ?answeredAt ?queried
WHERE {
  VALUES ?requestedName { {{name}} }
  VALUES ?latest { {{latest}} }

  GRAPH {{graph}} {
    ?resource bkr:resourceName ?name ;
              bkr:hasActivityAssessment ?assessment .
    ?assessment prov:generatedAtTime ?assessedAt ;
                bkr:activityStatus ?statusIRI ;
                prov:wasGeneratedBy ?check .
    OPTIONAL { ?assessment bkr:statusReason ?reason }
    OPTIONAL { ?assessment bkr:staleAfterDays ?staleAfterDays }
    OPTIONAL { ?assessment bkr:inactiveAfterDays ?inactiveAfterDays }
    OPTIONAL { ?check prov:startedAtTime ?checkStarted }
    OPTIONAL { ?check prov:endedAtTime ?checkEnded }
    OPTIONAL { ?check dcterms:isPartOf ?run .
               OPTIONAL { ?run prov:startedAtTime ?runStarted }
               OPTIONAL { ?run prov:endedAtTime ?runEnded } }
    OPTIONAL { ?check prov:wasAssociatedWith ?agent .
               OPTIONAL { ?agent nidm:NIDM_0000122 ?softwareVersion } }
    OPTIONAL {
      ?assessment prov:wasDerivedFrom ?signal .
      OPTIONAL { ?signal bkr:activitySource ?sourceIRI }
      OPTIONAL { ?signal prov:generatedAtTime ?answeredAt }
      OPTIONAL { ?signal prov:hadPrimarySource ?queried }
      BIND(EXISTS { ?assessment bkr:statusDecidedBy ?signal } AS ?decided)
    }
    FILTER(?latest = 0 || NOT EXISTS {
      ?resource bkr:hasActivityAssessment ?newer .
      ?newer prov:generatedAtTime ?newerDate .
      FILTER(STR(?newerDate) > STR(?assessedAt))
    })
  }
  BIND(STRAFTER(STR(?statusIRI), "/activity-status/") AS ?status)
  BIND(STRAFTER(STR(?sourceIRI), "/activity-source/") AS ?source)
  FILTER(?requestedName = "" || CONTAINS(LCASE(STR(?name)), LCASE(?requestedName)))
}
ORDER BY ?name DESC(?assessedAt) ?source ?queried
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
            "latest",
            "int",
            "1 for each resource's latest assessment only, 0 for every assessment.",
            default=1,
            minimum=0,
            maximum=1,
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
            "Named graph holding the resource data; omit for the default. "
            "For another space, pass a graph IRI from rs_resource_graphs or brainkb_list_spaces.",
            default="https://www.brainkb.org/resources/",
        ),
    ),
))

################################################################################
# End CQ36
################################################################################
