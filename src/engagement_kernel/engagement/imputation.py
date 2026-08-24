"""Neutral imputation for the conditional features.

The features in this package split into two classes and the split decides what an
absent value means.

A **level** feature answers "how much?". Zero is a real answer: a reader who read
nothing read nothing, and zero views is the honest value.

A **conditional** feature answers "given that they were active, what was it
like?" -- time per view, articles per session, momentum, week-to-week evenness.
For a reader with no activity the question has no answer, and every available
number is a lie about them:

* zero puts them at the *bottom* of the distribution, so an inactive reader reads
  as a maximally shallow one and clusters with the least engaged readers on a
  dimension they have no value on at all;
* the feature minimum has the same problem with extra steps;
* leaving it null fails the finiteness assertion at the matrix, which is the
  correct place to fail but the wrong thing to do about it.

The honest answer is "no information", and after standardisation "no information"
is the baseline mean -- exactly 0 in z-space. That is what this module writes.

The flags it returns are diagnostics and never features. A flag saying which rows
were imputed tells a distance function precisely the thing imputation exists to
keep out of it, so the flags come back in a separate frame rather than as columns
on the imputed one, and the model guard refuses ``*_imputed_flag`` by pattern
besides.
"""

from __future__ import annotations

import pandas as pd

IMPUTED_FLAG_SUFFIX = "_imputed_flag"


class ImputationError(ValueError):
    """Imputation was asked to run against a frame it does not line up with."""


def neutral_impute(
    standardized: pd.DataFrame,
    inactive_mask: pd.Series,
    columns: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Set the named conditional columns to exactly 0 where the row is inactive.

    Returns the imputed frame and a flag frame carrying one
    ``{column}_imputed_flag`` per input column. Only the named columns move: a
    level feature passed here by mistake would have its true zeros overwritten
    with the same zero and its non-zeros destroyed, so the caller names them.
    """
    missing = [column for column in columns if column not in standardized.columns]
    if missing:
        raise ImputationError(f"columns not present for imputation: {missing}")
    mask = inactive_mask.astype(bool)
    if not mask.index.equals(standardized.index):
        raise ImputationError(
            "inactive_mask index does not match the feature frame index. A silent "
            "realignment here would impute the wrong rows and every value would "
            "still be finite"
        )

    out = standardized.copy()
    flags = pd.DataFrame(index=standardized.index)
    for column in columns:
        out.loc[mask, column] = 0.0
        flags[f"{column}{IMPUTED_FLAG_SUFFIX}"] = mask.astype(int)
    return out, flags


def imputation_share(flags: pd.DataFrame) -> pd.Series:
    """Share of rows imputed, per feature.

    Reconciled against the conditional population by the feature-quality gate: a
    topic block imputed on 40% of rows when 70% of rows are content-inactive
    means the mask and the block have come apart, and nothing else would say so.
    """
    flag_columns = [c for c in flags.columns if c.endswith(IMPUTED_FLAG_SUFFIX)]
    return flags[flag_columns].mean().rename(lambda c: c.removesuffix(IMPUTED_FLAG_SUFFIX))
