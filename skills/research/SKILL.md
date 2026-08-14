---
name: research
description: Research a topic across multiple sources and synthesize the findings into a clear, sourced summary.
version: 1.0
risk_level: low
capabilities:
  - research
  - web search
  - synthesis
  - fact-checking
required_tools:
  - research_agent
  - create_note
---

When the user asks to research, look into, or compare something:

1. Use `research_agent` to gather information from multiple independent
   sources rather than stopping at the first result -- cross-check any
   specific claim (a price, a date, a statistic) against at least one
   other source before presenting it as fact.
2. Prefer recent, reputable sources; note when information might be
   outdated.
3. Synthesize findings into a short, direct summary organized around what
   the user actually asked (a comparison, a recommendation, an overview)
   rather than a raw list of what each source said.
4. If the user asks to save or keep the findings, use `create_note` --
   don't create a note unless asked.
5. If sources disagree on something material, say so explicitly rather
   than silently picking one.
