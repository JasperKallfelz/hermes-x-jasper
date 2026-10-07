# Local Hermes/OpenViking notes

These notes capture the current Hermes/OpenViking retrieval shape.

## Current stack shape

- OpenViking is the local retrieval service.
- Embeddings currently come from `qwen3-embedding:8b` via Ollama.
- The vector store is real and populated.
- The current “memory graph” is structural provenance (`source → folder → file`), not a semantic knowledge graph.

## Practical takeaways

- Treat the current graph as provenance/navigation, not GraphRAG.
- Dense embeddings alone are not enough for technical/private knowledge.
- Prefer hybrid retrieval before adding graph complexity.
- GraphRAG is only worth it for clearly relational, multi-hop, temporal, or dependency-style questions.
- For local Hermes use, the next real wins are better chunking, metadata, reranking, parent-child retrieval, and claim-level provenance.

## Common trap

Do not call a document tree or file index “GraphRAG” unless the system actually stores and traverses semantic entities and relations with evidence-backed edges.
