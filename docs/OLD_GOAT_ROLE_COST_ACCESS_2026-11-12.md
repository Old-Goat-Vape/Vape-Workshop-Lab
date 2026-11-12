# Cost access for the Old Goat role

The original account named Old Goat retains private costing access. Any signed-in user with the exact Old Goat role now has that same costing access, regardless of username. Master and all lower roles retain their existing permissions.

A shared `can_access_private_costs(username, role)` predicate controls recipe costing pages, calculate/search/override APIs via `UserContext.show_cost`, the Workshop recipe detail's Cost this recipe link, and the existing private inventory-cost branch. No other account-specific Old Goat authority is broadened: formula/reference rules, negative-stock overrides and identity flags stay as before.

The Lab catalogue already exposes some supplier prices, and its inventory edit template already uses a Master/Old Goat role check for its price field. Those existing views are preserved; this change does not grant Master access to the restricted recipe-cost calculator or its write APIs.

Verification: role/account matrix, page rendering, calculation API, catalogue search, pricing override persistence, disallowed roles' direct API requests, inventory cost-write guard and unrelated account authority, plus existing costing regression tests. Live role-based QA follows deployment.
