"""Write the contract's Parquet files from the queries beside this file.

Deliberately boring. The contract does not care what produces the files, so the
export step is the least interesting part of an adapter and should stay that way:
run each query, write each result, write the manifest, then run the validator and
refuse to publish a delivery it rejects.

The last step is the one adopters skip. Validating in the job that produces the
delivery -- rather than by hand, once, on the day it was built -- is what stops a
schema drift upstream from arriving as a quietly wrong model six weeks later.
"""

QUERIES = {
    "reader": "readers.sql",
    "reader_event": "events.sql",
    "content": "content.sql",
    "subscription_span": "subscriptions.sql",
    "email_click": "email_clicks.sql",
}

# email_open and community_action are not exported. That is declared in the
# manifest as not_yet_launched and not_deployed respectively -- not omitted, and
# not written as empty files. An empty file is read as "nobody did this"; a
# declared absence selects a different feature set and says so in the run report.
