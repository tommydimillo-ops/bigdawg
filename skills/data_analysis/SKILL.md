---
name: data_analysis
description: Analyze a spreadsheet, CSV, or other data file and explain trends, outliers, or summary statistics in plain language.
version: 1.0
risk_level: low
capabilities:
  - data analysis
  - spreadsheet
  - statistics
  - trends
required_tools:
  - read_document
  - run_python
  - search_files
---

When the user asks to analyze, summarize, or find trends in a data file:

1. Locate the file with `search_files` if the user didn't give an exact
   path, then `read_document` to see its actual structure before assuming
   a format.
2. For real computation (sums, averages, trend lines, outlier detection),
   use `run_python` rather than estimating by eye -- it runs in an
   isolated sandbox with no network access and no write access outside
   that sandbox, so this is safe to use freely for calculation.
3. Explain findings in plain language first (what the data actually
   shows), with the specific numbers as support -- not a wall of raw
   output.
4. Call out anything that looks like a data quality issue (missing
   values, an obvious outlier, inconsistent units) rather than silently
   working around it.
5. Don't assume trends imply causation; describe what the data shows, not
   why it happened, unless the user specifically asks for an
   interpretation.
