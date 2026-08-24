-- Cut sessions on 30 minutes of inactivity, per reader.
--
-- `engagement_time_seconds` comes out of this too: Riverbend's tracker fires a
-- 15-second heartbeat while a tab is focused, so active time is the heartbeat
-- count times 15 -- NOT the wall-clock gap between events, which counts the time
-- a reader left the tab open over lunch. The contract's field is measured
-- attention, and null means unmeasured; a reader with no heartbeats gets null
-- rather than 0, because 0 would mean "arrived and read nothing".
WITH gapped AS (
    SELECT
        *,
        CASE
            WHEN occurred_at - LAG(occurred_at) OVER w > INTERVAL '30 minutes'
              OR LAG(occurred_at) OVER w IS NULL
            THEN 1 ELSE 0
        END AS starts_session
    FROM analytics.page_event
    WINDOW w AS (PARTITION BY person_key ORDER BY occurred_at)
)
SELECT
    *,
    person_key || ':' || SUM(starts_session) OVER (
        PARTITION BY person_key ORDER BY occurred_at
    ) AS session_key,
    NULLIF(heartbeat_count, 0) * 15 AS active_seconds
FROM gapped
