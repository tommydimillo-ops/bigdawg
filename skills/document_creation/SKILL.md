---
name: document_creation
description: Draft a document, report, or presentation outline from a request or an existing source document.
version: 1.0
risk_level: low
capabilities:
  - document creation
  - writing
  - presentation
  - report
required_tools:
  - create_note
  - read_document
---

When the user asks to write, draft, or create a document, report, or
presentation:

1. If they reference an existing file, `read_document` it first rather
   than working from assumptions about its content.
2. Ask (in your reply, not by blocking) what format or length they want
   only if it's genuinely ambiguous -- otherwise default to a clear,
   well-structured draft (headings, short paragraphs or bullets) sized to
   what was actually requested.
3. For a presentation request, produce a slide-by-slide outline (title +
   3-5 bullets per slide) rather than a single wall of prose -- this
   project has no slide-generation tool, so the outline itself is the
   deliverable unless the user says otherwise.
4. Use `create_note` to save the draft when asked to save/keep it --
   don't create a note unless asked.
5. Flag clearly if the request needs information you don't have (e.g. a
   presentation "from this document" but no document was actually
   provided) instead of inventing content to fill the gap.
