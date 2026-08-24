-- content: one row per piece of content.
--
-- section_resolution is the field an adopter is most likely to get wrong by
-- being helpful. Riverbend's CMS lets an editor leave the section tag empty, and
-- roughly 4% of articles have none. The tempting move is to fill those with the
-- site default ('news'), which would attribute a twentieth of all reading to a
-- section nobody chose. Instead they are emitted as 'unresolved', which the
-- contract has a value for precisely so that unresolved metadata does not read
-- as reading.
SELECT
    article_key                                  AS content_id,
    CASE cms_template
        WHEN 'story'     THEN 'article'
        WHEN 'live'      THEN 'liveblog'
        WHEN 'gallery'   THEN 'gallery'
        WHEN 'newsletter' THEN 'newsletter'
        ELSE 'other'
    END                                          AS content_type,
    CASE WHEN section_slug IS NULL OR section_slug = ''
         THEN 'unresolved' ELSE 'resolved' END   AS section_resolution,
    CASE WHEN section_slug IS NULL OR section_slug = ''
         THEN NULL ELSE ARRAY[section_slug] END   AS sections,
    first_published_at                           AS published_ts
FROM cms.article
