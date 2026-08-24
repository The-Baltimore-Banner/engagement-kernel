"""The training panel: which reader-weeks every transform is fit on.

One reader-week per reader per calendar month, chosen deterministically from a
seed. Two properties come out of that rule and both are the point.

**No reader can dominate a fit.** Without the rule, a reader present in all 52
weeks contributes 52 rows and a reader who subscribed in December contributes 4,
so every fitted threshold, every standardisation and every centroid is weighted by
tenure. The clusters then describe how long people have been subscribers.

**The sample is decorrelated.** Consecutive weekly snapshots share 21 of their 28
window days, so two adjacent rows for the same reader are very nearly the same
row. Taking one per month puts the rows far enough apart that they carry
independent information.

The choice is hashed from ``(seed, reader, month)`` rather than drawn from a
random stream, which makes it independent of the order rows arrive in. A shuffled
input therefore produces the same panel, and a panel that changed when the input
happened to be sorted differently would make every downstream comparison between
two runs meaningless.

:func:`balance_by_month` is the second half of the same argument. A subscriber base
that grew over the baseline puts more rows in later months, so even a per-reader
capped panel tilts every fit toward the most recent months. Downsampling each
month to a common size fixes it at the sampling layer, where it is visible, rather
than with row weights threaded through every fit.
"""

from __future__ import annotations

import hashlib

import pandas as pd

READER_KEY = "reader_id"
WEEK_KEY = "as_of_week_end"

#: The sampling rule, as a string, so a frozen model can say which one produced it.
PANEL_RULE = "one_week_per_reader_per_month"


class PanelError(ValueError):
    """The panel does not satisfy an invariant the fits depend on."""


def _digest(seed: int, *parts: object) -> int:
    payload = ":".join([str(seed), *(str(part) for part in parts)])
    return int(hashlib.sha256(payload.encode()).hexdigest(), 16)


def sample_panel(eligible: pd.DataFrame, *, seed: int) -> pd.DataFrame:
    """Take one row per reader per calendar month.

    ``eligible`` must already be filtered to the fitting population: this function
    enforces the sampling rule and nothing else. Handing it every reader-week would
    produce a well-formed panel of the wrong population, which no assertion here
    could detect.
    """
    if eligible.empty:
        return eligible.copy()
    frame = eligible.copy()
    frame["_month"] = pd.to_datetime(frame[WEEK_KEY]).dt.strftime("%Y-%m")
    frame = frame.sort_values([READER_KEY, "_month", WEEK_KEY], kind="mergesort")

    keep = []
    for (reader_id, month), group in frame.groupby([READER_KEY, "_month"], sort=True):
        pick = _digest(seed, reader_id, month) % len(group)
        keep.append(group.index[pick])
    panel = frame.loc[keep].drop(columns="_month")
    return panel.sort_values([READER_KEY, WEEK_KEY], kind="mergesort").reset_index(drop=True)


def balance_by_month(
    panel: pd.DataFrame,
    *,
    seed: int,
    target: int | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Downsample every calendar month to a common row count.

    ``target`` defaults to the smallest month, i.e. full equalisation. Only ever
    drops rows, so the one-row-per-reader-per-month invariant survives. Returns the
    balanced panel and a per-month before/after record for the freeze metadata --
    the record matters because equalising to the smallest month can be expensive,
    and a run should say how much of its panel it spent on balance.
    """
    empty = pd.DataFrame(columns=["month", "n_before", "n_after"])
    if panel.empty:
        return panel.copy(), empty
    frame = panel.copy()
    frame["_month"] = pd.to_datetime(frame[WEEK_KEY]).dt.strftime("%Y-%m").to_numpy()
    before = frame["_month"].value_counts()
    if target is None:
        target = int(before.min())
    frame["_rank"] = [
        _digest(seed, reader_id, week)
        for reader_id, week in zip(
            frame[READER_KEY].astype(str), frame[WEEK_KEY].astype(str), strict=True
        )
    ]
    kept = (
        frame.sort_values(["_month", "_rank"], kind="mergesort")
        .groupby("_month", sort=True, group_keys=False)
        .head(target)
    )
    after = kept["_month"].value_counts()
    record = (
        pd.DataFrame({"month": before.index, "n_before": before.to_numpy()})
        .sort_values("month")
        .reset_index(drop=True)
    )
    record["n_after"] = record["month"].map(after).fillna(0).astype(int)
    balanced = (
        kept.drop(columns=["_month", "_rank"])
        .sort_values([READER_KEY, WEEK_KEY], kind="mergesort")
        .reset_index(drop=True)
    )
    return balanced, record


def validate_panel(
    panel: pd.DataFrame,
    *,
    baseline_end: object | None = None,
    min_rows: int = 1,
) -> None:
    """Assert the panel invariants, rather than declaring them.

    Two things, and the second one is the one that has actually been violated in
    practice. A baseline period declared in configuration and never *consumed as a
    bound* is not a bound: a refit ran with a panel extending months past its own
    declared baseline, so a large share of the fitting rows sat inside the period
    that was supposed to be held out, and nothing failed. A declaration with no
    assertion behind it is documentation.
    """
    if len(panel) < min_rows:
        raise PanelError(
            f"the training panel has {len(panel)} rows, below the {min_rows} required. "
            "Every threshold, standardisation and centroid comes from these rows"
        )
    months = pd.to_datetime(panel[WEEK_KEY]).dt.strftime("%Y-%m")
    counts = panel.groupby([panel[READER_KEY], months]).size()
    if (counts > 1).any():
        offenders = counts[counts > 1].index.tolist()[:5]
        raise PanelError(
            f"the panel has more than one row for a reader-month: {offenders}. Those "
            "readers are weighted more heavily than the rest of the population in every fit"
        )
    if baseline_end is not None:
        weeks = pd.to_datetime(panel[WEEK_KEY])
        bound = pd.Timestamp(baseline_end)
        over = weeks > bound
        if bool(over.any()):
            raise PanelError(
                f"{int(over.sum())} panel rows fall after the declared baseline end "
                f"{bound.date()}; the latest is {weeks[over].max().date()}. The fitting "
                "population must not extend past the baseline it declares"
            )


def content_active_subset(panel: pd.DataFrame) -> pd.DataFrame:
    """The panel rows the topic block and the surfaces are fit on.

    One panel, two fitting populations. The topic block and the clustering surfaces
    are only defined for readers with a describable topic mix, so they fit on this
    subset -- while winsorisation and the channel calibrations fit on the whole
    panel, because a count is defined for everybody.
    """
    if "content_active_flag" not in panel.columns:
        raise PanelError("panel has no content_active_flag; the conditional fits need it")
    return panel.loc[panel["content_active_flag"].astype(bool)].copy()
