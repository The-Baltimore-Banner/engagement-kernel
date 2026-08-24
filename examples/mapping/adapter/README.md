# The Riverbend Ledger adapter (worked example)

Every name, table and value here is invented. There is no Riverbend Ledger.

This is not a library to install. It is the shape of what an adopter's own coding
agent produces: a handful of queries in whatever their stack already runs, plus
the export step that writes the contract's Parquet files. The point of committing
it is that the mapping manifest beside it cites real files with real digests, so
the traceability checks in `engagement-kernel-lint-mapping --adapter-bundle` have
something to check. A mapping that cites code nobody can open is a claim.

The queries are deliberately incomplete as SQL -- they name the columns and the
rules, not the whole DDL -- because the reviewable content of a mapping is the
rule, not the syntax.
