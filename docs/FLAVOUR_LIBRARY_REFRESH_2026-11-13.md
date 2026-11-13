# Flavour library refresh

The Workshop `/flavours` page now uses readable image cards, prominent brand/name headings, stash quantities and explicit Explore / Add or Update stash links. The old one-click action wrote 10 ml immediately; browsing cards no longer changes quantities.

Search, manufacturer, suggested family, stash-only, sorting and pagination combine on the server. Family and stash filters apply before counting/pagination. Personal users see their own stash quantities; the original Old Goat account and Old Goat roles use the existing Lab holdings bridge. Families/artwork are name-derived suggestions, clearly labelled, and do not rewrite flavour identity or verified tasting data.

Flavour detail pages have a visual summary, current-recipe sample count/average, stash quantity, section links, public usage distributions and observed pairings. Imported hints remain separate from current-recipe statistics. Private recipes contribute only aggregate data; existing public recipe-list visibility stays unchanged.

Each user gets a plain-text private notebook per canonical flavour. Notes are session-user scoped, escaped when displayed, limited to 10,000 characters on write, and never posted as community content. The startup migration creates the notebook table. Duplicate flavour consolidation preserves each user's source notes in the surviving notebook, appending when both exist. Structured tasting tests/strength/steep observations remain a future phase.

The new detail-page costing block queries and renders the linked catalogue price only for the original Old Goat account or exact Old Goat role. Master does not gain restricted recipe costing; existing Lab catalogue price views are preserved.

Validation: 43 focused tests passed across the refresh, flavour search/discovery, costing-role and catalogue identity suites. Includes server-side stash/family filtering, personal/Lab stash separation, safe navigation instead of silent quantity writes, private notebook isolation/escaping/length limits and financial visibility. The local browser could not reach the executor's preview server, so full visual and phone QA must follow deployment. Temporary synthetic preview server/data are not shipped.
