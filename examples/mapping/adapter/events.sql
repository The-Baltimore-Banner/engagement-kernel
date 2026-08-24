-- reader_event: one row per delivered piece of content, per reader.
--
-- Two derivations, both semantic rather than mechanical.
--
-- session_id: Riverbend's warehouse has no sessions. `analytics.page_event` is a
-- flat event stream. Sessions are cut on 30 minutes of inactivity per reader,
-- which is the convention their existing dashboards already use -- picking a
-- different threshold here would make the kernel's session counts disagree with
-- every number the newsroom already trusts, for no stated reason.
--
-- event_ts: stored UTC in the warehouse and emitted UTC. It is NOT converted to
-- America/Chicago here, even though that is the declared day boundary: the
-- engine applies the boundary itself, and applying it twice is invisible.
SELECT
    event_key                                    AS event_id,
    person_key                                   AS reader_id,
    occurred_at                                  AS event_ts,   -- already UTC
    CASE surface
        WHEN 'web'  THEN 'web'
        WHEN 'ios'  THEN 'app'
        WHEN 'and'  THEN 'app'
        WHEN 'amp'  THEN 'web'
    END                                          AS channel,
    'content_delivery'                           AS event_kind,
    article_key                                  AS content_id,
    session_key                                  AS session_id, -- from sessionize.sql
    active_seconds                               AS engagement_time_seconds
FROM analytics.page_event_sessionized
WHERE article_key IS NOT NULL
