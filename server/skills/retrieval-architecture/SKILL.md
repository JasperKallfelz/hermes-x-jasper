---
name: retrieval-architecture
description: "Use when designing or reviewing RAG/GraphRAG systems."
author: hermes
license: MIT
version: 1.0.0
platforms: [macos, linux]
metadata:
  hermes:
    tags: [rag, graph-rag, retrieval, embeddings, vector, bm25, reranking, provenance, evaluation]
    related_skills: [grounded-citations, source-backed-project-research, documentation-diff-review]
---

# Retrieval architecture

## When to Use

Use this skill when you need to design, debug, evaluate, or explain a retrieval-augmented system, especially when deciding between dense search, sparse search, hybrid retrieval, reranking, and graph layers.

Use it for any work involving:

- RAG pipelines
- Vector databases / embeddings
- Hybrid sparse+dense retrieval
- GraphRAG or entity graphs
- Retrieval evaluation and diagnostics
- Local knowledge bases / second brains

## Core principle

Retrieval is a **bevidence pipeline**, not a magic memory layer.
The goal is to answer questions from the right source fragments with traceable provenance.

## Recommended default order

1. **Fix ingestion first.**
   - Preserve structure, titles, section paths, timestamps, source IDs.
   - Keep raw provenance next to derived chunks.
2. **Use structural chunking.**
   - Prefer document/section-aware boundaries over blind fixed-size slicing.
3. **Add hybrid retrieval.**
   - Dense embeddings for semantics.
   - Sparse/BM25 for exact names, paths, IDs, and codes.
4. **Rerank candidates.**
   - Retrieve broadly, then rank by question+passage relevance.
5. **Pack context deliberately.**
   - Deduplicate near-duplicates.
   - Prefer diverse but supporting evidence.
   - Include parent context when child chunks are too small.
6. **Measure before adding complexity.**
   - Evaluate retrieval and answer faithfulness separately.
7. **Only then consider graph layers.**
   - Use graphs for relations, multi-hop paths, or temporal linkage.

## What counts as GraphRAG here

GraphRAG is only justified when the query needs explicit relations across sources, such as:

- dependency chains
- decision lineage
- entity-centric exploration
- multi-hop reasoning
- temporal evolution
- impact analysis

A directory tree, source index, or `source → folder → file` map is **not** GraphRAG; it is provenance/navigation metadata.

## When GraphRAG is probably a bad trade

Avoid graph complexity first when:

- the answer is in one passage
- exact terms dominate the query
- the corpus is small or highly redundant
- entity extraction is noisy
- updates are frequent and hard to reindex
- you lack a gold evaluation set

## Evaluation checklist

Track retrieval separately from generation:

- Recall@k
- Precision@k
- MRR / nDCG
- duplicate rate in top-k
- reranker lift
- faithfulness / citation correctness
- abstain behavior
- latency by stage

If answer quality is poor, diagnose in this order:

1. parsing / chunking
2. missing metadata
3. retrieval candidate quality
4. reranking
5. context packing
6. generation
7. graph construction

## Common pitfalls

- Treating embeddings as truth instead of similarity signals
- Over-tuning ANN before fixing chunking and metadata
- Using larger and larger chunks to cover retrieval failures
- Letting a graph replace provenance
- Accepting extracted edges as facts without text evidence
- Mixing embedding models or chunking schemes in one index without versioning
- Optimizing demos instead of a stable query set

## Local Hermes/OpenViking note

For the current Hermes/OpenViking stack, prefer:

- dense + sparse hybrid search first
- better chunking and metadata
- reranking and parent-child retrieval
- claim-level provenance
- a small semantic graph only for clearly relational query classes

See `references/local-hermes-openviking-notes.md` for the current stack-specific notes and pitfalls.
