-- email_click: one row per click on an email.
--
-- Riverbend's ESP exposes clicks with a subscriber hash rather than their own
-- person key, so this joins through the ESP mapping table. Rows whose hash does
-- not resolve are DROPPED, not assigned to a placeholder reader: the contract
-- requires every reader_id to be in the registry, and an unresolved click is a
-- click by somebody the rest of the delivery has never heard of.
--
-- The drop rate is ~2% and is worth watching. If it climbed, email engagement
-- would fall for a reason that has nothing to do with readers.
SELECT
    esp.click_id                                 AS event_id,
    map.person_key                               AS reader_id,
    esp.clicked_at                               AS event_ts,   -- UTC from the ESP
    esp.list_uuid                                AS list_id,
    esp.campaign_uuid                            AS campaign_id
FROM esp.click esp
JOIN esp.subscriber_map map
  ON map.subscriber_hash = esp.subscriber_hash
