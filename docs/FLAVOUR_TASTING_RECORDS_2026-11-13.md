# Catalogue navigation and tasting records

Flavour cards carry a per-tab `return_to` catalogue URL. The detail-page Back link and main Flavours navigation preserve search, manufacturer, suggested family, stash-only filter, sort direction/key, page and page size. This context also follows pairings, recipe-list pagination, merged-ID redirects and private notebook/test saves. Clear filters still resets the list explicitly. The return URL is restricted to the local `/flavours` path and known query keys; external hosts, other paths and malformed URLs are rejected. No cross-tab session-global filter state is used.

Tests are private records tied to the signed-in user and canonical flavour. A test stores its label, single-flavour/recipe context, percentage, optional PG/VG, device and watts. It contains one or more tastings with actual steep age, perceived strength and impressions. Day 0 is valid and differs from an omitted age. Users can add follow-up tastings and correct individual observations. Validation rejects non-finite/out-of-range values and preserves submitted details on errors. Each ownership check includes user, flavour and test ID; an observation correction is additionally constrained to its owning test. Catalogue duplicate consolidation repoints tests without combining different experiments.

Sharing is a separate action: the page previews a community-note snapshot and the user explicitly posts it. Private labels and device identifiers are omitted from that copy. The snapshot includes context, percentage, optional PG/VG and tasting impressions. The existing note moderation/reporting flow applies. A digest detects stale previews and makes repeated submissions idempotent. Later tastings/corrections remain private; the user may preview and explicitly post an updated snapshot. No automatic recommendations, percentage averages or steeping rules are changed by these records.

Startup creates `flavour_tasting_tests` and `flavour_tasting_observations` with ownership/test indexes. Existing plain notebooks, public notes/reviews, financial permissions and mixing behaviour remain as saved. Data migration does not auto-publish observations.

Run from `app`:

```sh
TVW_ENABLE_LAB_MODULES=true python -m unittest tests.test_flavour_tastings tests.test_flavour_refresh tests.test_flavour_discovery tests.test_flavour_search tests.test_cost_access_roles tests.test_catalogue_identity
```

Tests cover filter return links, unsafe return targets, card context, day-zero/follow-up persistence, private isolation, cross-owner writes/sharing, invalid values, escaped rendering, explicit sharing snapshots, stale previews, duplicate sharing and cross-test corrections. Browser visual QA follows server deployment.
