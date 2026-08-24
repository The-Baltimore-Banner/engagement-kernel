-- subscription_span: subscription state as non-overlapping intervals.
--
-- Riverbend's billing system stores a current-status snapshot per account plus a
-- separate change log. Neither is intervals, so the spans are rebuilt from the
-- log -- which is the whole derivation, and the reason this file is longer than
-- the others.
--
-- The state vocabulary is the part a human had to decide. Riverbend has eleven
-- billing statuses; the contract has seven states. The mapping below is the
-- adapter author's work. Which of the seven are ENTITLED -- and therefore who
-- gets scored at all -- is not in this file: that is scored_population in the
-- manifest, and it was answered by the subscriptions director, not here.
--
-- payer_type is left null for accounts on an institutional or gifted plan.
-- Riverbend cannot tell those apart from individual payers in the billing data,
-- and the contract's null means "unknown", not "individual". Guessing
-- 'individual' would move a known unknown into a false known.
SELECT
    person_key                                   AS reader_id,
    CASE billing_status
        WHEN 'trialing'        THEN 'trial'
        WHEN 'active'          THEN 'active'
        WHEN 'past_due'        THEN 'grace'
        WHEN 'unpaid'          THEN 'grace'
        WHEN 'canceled'        THEN 'cancelled'
        WHEN 'ended'           THEN 'expired'
        WHEN 'paused'          THEN 'paused'
        WHEN 'refunded'        THEN 'cancelled'
        WHEN 'chargeback'      THEN 'cancelled'
        WHEN 'comped'          THEN 'active'
        WHEN 'never_purchased' THEN 'registered_unpaid'
    END                                          AS state,
    CASE plan_kind
        WHEN 'consumer'      THEN 'individual'
        WHEN 'institutional' THEN NULL   -- unknown, not individual
        WHEN 'gift'          THEN NULL
    END                                          AS payer_type,
    valid_from                                   AS start_ts,
    valid_to                                     AS end_ts     -- null = still open
FROM billing.status_interval
