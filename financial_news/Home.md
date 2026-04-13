---
tags:
  - financial-news
  - vault-guide
---

# Financial News Vault Guide

This vault is organized around three different kinds of notes:

1. **Generated daily imports** → month folders like `2026-04/`
2. **Manual source profiles** → `Sources/`
3. **Manual/heuristic topic MOCs and weekly theme notes** → `Topics/` and `Themes/`

## Important operating rule

Generated daily imports should live in the real vault output path outside this git checkout.
This repo tracks the structure and operator docs only:

- source profiles
- topic MOCs
- weekly-note seeds
- templates
- ingestion code and tests

## How the graph is meant to work now

- Daily import notes are **not** supposed to link back to a generic `Home` page.
- Each imported summary block links directly to:
  - its matching source profile when known
  - one or more topic MOCs when heuristics match
- Durable narratives should be promoted into weekly theme notes or edited manually inside topic MOCs.

## Manually curated areas

- [[Sources/Home|Source profiles]]
- [[Themes/Home|Weekly theme notes]]
- [[Topics/AI|AI]]
- [[Topics/Tech|Tech]]
- [[Topics/Energy|Energy]]
- [[Topics/Utilities|Utilities]]
- [[Topics/Trump|Trump]]
- [[Topics/War and Geopolitics|War and Geopolitics]]
- [[Topics/Rates and Fed|Rates and Fed]]
- [[Topics/Financials|Financials]]
- [[Topics/Retail and Consumer|Retail and Consumer]]
- [[Topics/Healthcare|Healthcare]]
- [[Topics/Telecom and Media|Telecom and Media]]

## Heuristic vs manual

- **Heuristic:** summary-block topic links inferred from keywords in headlines/insights.
- **Manual:** topic MOC structure, source profiles, and weekly notes.
- **Manual judgment still wins** if the heuristic linking is noisy or missing context.
