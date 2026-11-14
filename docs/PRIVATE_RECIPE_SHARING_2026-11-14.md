# Private recipe sharing — 14 November 2026

Recipe owners can now grant named users view-and-mix access or a separate editing
permission without changing the original recipe's visibility. Sharing covers the
recipe and its versions. Only the owner manages the access list. Existing Old
Goat/admin authorities remain in place; sharing does not assign a system role.

## User flow

Open an owned recipe and choose Share recipe. Search at least two username
characters, select a displayed user and choose View and mix (default) or View,
mix and edit. The owner can update permissions or revoke access on that page.
No message/email is sent. Search returns only IDs and usernames, with a limit
of ten results; disabled/banned/suspended accounts are excluded.

Recipients find the recipe in Shared with me and their permitted library/stash
results, including What Can I Make. Search, filters, pagination and stash sorting
retain the selected scope. Private shared recipes remain excluded from public
discovery, flavour recipe lists and unrelated users' libraries.

Editors can save new versions but cannot change ownership, visibility or the
access list. Choosing Help needed cannot bypass the owner-only publication rule.
Private notebooks remain per-user. Private recipe grants expose only the
recipient's own personal batch notes/history; the owner's private batch records
are not implicitly shared. Recipe comments and recipe version notes remain part
of the recipe's existing view access.

## Enforcement and storage

The new recipe_access table records recipient, edit permission, owner grantor,
timestamps and revocation. The unique recipe/user key prevents duplicate grants.
An audit table records permission changes. Grant updates recheck ownership in an
immediate transaction. Access-management POSTs require a session CSRF token.

The central recipe view gate and library predicates recognize live grants while
preserving hidden/removed/inactive checks for recipients. Image, quick mix and
guided mix entry points reuse the gate. Guided step, event and note mutations
now check recipe access before changing a session, so revocation stops future
actions on sessions already started. Recipe images retain private/no-store
headers. A public recipe remains publicly viewable if its individual grant is
revoked; the sharing screen explains that distinction. Previously seen content
and the recipient's own historical mix records cannot be erased by revocation.

## Validation

Seven new integration tests pass across owner, recipient and unrelated accounts:
search/CSRF, grants/revocation/audit, view and both mix modes, exact access lists,
private notebook/image privacy, shared library/stash/WCM, public discovery,
separate edit permission, publication and Help-needed bypass attempts, version
ownership, downgrade, pre-mutation revocation, private batch notes and moderation.

Of 152 existing recipe-builder/library/API tests, 148 pass. The four failures
were independently reproduced on unmodified main 84af030: dashboard foundation
copy, dashboard discovery-state redirect, flavour catalogue copy and flavour
default-percentage copy. They predate this feature. Total passing tests: 155.

## Deployment

Run tvw-update. Startup creates the access and audit tables idempotently; no
existing recipe visibility, user roles or permissions are rewritten. No access
is granted until an owner chooses a recipient. Live visual and multi-account
checks remain after deployment.
