-- reader: the registry. One row per resolved person.
--
-- Riverbend's identity service already resolves devices to accounts, so the
-- grain the contract wants exists upstream. What does NOT exist upstream is a
-- guarantee that unresolved traffic is excluded: `identity.person` carries a row
-- for every anonymous browser too, distinguished only by `resolution_source`.
-- Shipping those would mint one contract reader per browser, which the contract
-- refuses by design and the validator catches via id_grain -- but only if the
-- grain column disagrees. It would not: they would all say `person`. So the
-- filter is the load-bearing line here, not the rename.
SELECT
    person_key           AS reader_id,
    'resolved_person'    AS id_grain
FROM identity.person
WHERE resolution_source IN ('login', 'newsletter_link', 'purchase')
  AND is_internal_account = FALSE
