"""Closed vocabularies used by the canonical input contract.

Every enum here is *contract data*: a validator rejects a value outside the
declared set, and the set is versioned with the contract rather than inferred
from whatever a source system happens to emit. Two of them are deliberately
narrow, and the narrowness is the point:

``READER_ID_GRAINS``
    Exactly one permitted value. A reader id in this contract means one
    resolved person and nothing else. A device, browser, install or session
    identifier is not a reader, and a column that mixes grains is rejected
    rather than documented as a caveat. Supporting a second grain is a contract
    *version* change, not a configuration toggle, because every window feature
    counts distinct readers.

``READER_EVENT_KINDS``
    Delivery versus interaction, and nothing narrower. Whether a given
    delivery counts as an *article view* is an editorial decision that this
    contract deliberately does not make; see
    ``docs/canonical-input-contract.md`` and the manifest's ``article_view``
    block. The contract supplies the mechanism; the publisher supplies the
    selection.
"""

from __future__ import annotations

# --- identity ---------------------------------------------------------------

GRAIN_RESOLVED_PERSON = "resolved_person"

READER_ID_GRAINS: tuple[str, ...] = (GRAIN_RESOLVED_PERSON,)

# --- reader events ----------------------------------------------------------

CHANNEL_WEB = "web"
CHANNEL_APP = "app"

READER_EVENT_CHANNELS: tuple[str, ...] = (CHANNEL_WEB, CHANNEL_APP)

#: A page or screen being shown to the reader. Countable as a "view".
EVENT_KIND_CONTENT_DELIVERY = "content_delivery"
#: Anything the reader did *with* content that is not the content arriving.
#: Counted in the raw event total, never counted as a view.
EVENT_KIND_CONTENT_INTERACTION = "content_interaction"

READER_EVENT_KINDS: tuple[str, ...] = (
    EVENT_KIND_CONTENT_DELIVERY,
    EVENT_KIND_CONTENT_INTERACTION,
)

# --- content ----------------------------------------------------------------

CONTENT_TYPES: tuple[str, ...] = (
    "article",
    "liveblog",
    "gallery",
    "video",
    "podcast",
    "newsletter",
    "other",
)

#: The content's section metadata resolved to at least one usable section.
SECTION_RESOLUTION_RESOLVED = "resolved"
#: The content exists but its section metadata did not resolve to anything
#: usable. This is a data-quality outcome and is *not* the same as "the reader
#: did not read"; collapsing the two destroys the distinction downstream.
SECTION_RESOLUTION_UNRESOLVED = "unresolved"

SECTION_RESOLUTIONS: tuple[str, ...] = (
    SECTION_RESOLUTION_RESOLVED,
    SECTION_RESOLUTION_UNRESOLVED,
)

# --- subscription -----------------------------------------------------------

#: Seven states, one per commercial situation a reader can be in. Ordered from
#: "known to us but never paid" through the paying states to the ended ones.
SUBSCRIPTION_STATES: tuple[str, ...] = (
    "registered_unpaid",
    "trial",
    "active",
    "grace",
    "payment_failed",
    "cancelled",
    "expired",
)

#: Who pays. Optional: a deployment whose billing system cannot distinguish
#: these supplies null rather than guessing. Never a model feature.
PAYER_TYPES: tuple[str, ...] = ("individual", "institutional", "guest")

# --- community ---------------------------------------------------------------

#: Every value names an action the reader **performed**. There is no
#: ``like_received`` and there never will be: a received reaction measures
#: someone else's behaviour, and recording it here would invert the feature.
COMMUNITY_ACTION_KINDS: tuple[str, ...] = (
    "post_created",
    "reply_created",
    "like_given",
    "dislike_given",
    "flag_given",
)

# --- manifest ---------------------------------------------------------------

WEEKDAYS: tuple[str, ...] = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

#: Which end of the week the anchoring weekday sits on. Both conventions exist
#: in the wild and they differ by up to six days, so the contract makes the
#: publisher say which one it means instead of hardcoding either.
WEEK_ANCHOR_POSITIONS: tuple[str, ...] = ("week_starts_on", "week_ends_on")

#: The input is present and its coverage floor is declared.
AVAILABILITY_AVAILABLE = "available"
#: The publisher does not deliver this input at all.
AVAILABILITY_NOT_DEPLOYED = "not_deployed"
#: The publisher will deliver this input, but the underlying product did not
#: exist yet for part or all of the analysis period. Distinct from
#: ``not_deployed`` because a window that straddles the launch date must drop
#: the feature block rather than read zeros out of the pre-launch period.
AVAILABILITY_NOT_YET_LAUNCHED = "not_yet_launched"

AVAILABILITY_STATUSES: tuple[str, ...] = (
    AVAILABILITY_AVAILABLE,
    AVAILABILITY_NOT_DEPLOYED,
    AVAILABILITY_NOT_YET_LAUNCHED,
)
