# Automatic recipe profile artwork

12 November 2026. Display priority: uploaded mixer photo → first/main saved profile → recognised existing recipe tag → general TVW workshop image. Recipe titles and ingredients are not guessed. Existing recipe records are not rewritten.

The editor allows a main profile from the selected profiles. Saving puts that profile first in the version's existing profiles_json; no schema change is needed. Older versions keep their own profile order. Main profile survives browser drafts, validation errors, save and edit. Removing/deselecting a profile updates the preview; removing an existing photo previews the appropriate automatic artwork, while cancelling removal restores the photo.

Shared picture macro serves the recipe library, discovery cards/featured recipes and saved recipe detail. Automatic images carry visible profile-artwork captions and descriptive alt text, distinct from a mixer's own photo. The browser preview labels its source and responds to profiles, main-profile selection and recognised tags. Uploaded photos retain the authenticated recipe-visibility endpoint. Art choices use fixed allowlisted static paths, not user-supplied URLs.

## Collection

Ten 960×540 WebP assets, individually under 100 KB and approximately 500 KB combined, under app/static/img/recipe-profiles/. Cards lazy-load with explicit dimensions. Fruit/Berry/Citrus/Tropical share fruit imagery; Custard/Cream share cream imagery. Dessert, Bakery, Beverage, Candy, Tobacco, Menthol, Nut and Spice each have artwork. Unknown profiles/tags use the existing mixing-bench.webp.

Created using built-in image generation. Common prompt: premium realistic editorial still life on a dark charcoal workshop tabletop, warm amber key light, subtle teal rim light, central crop-safe subjects, no text/logos/people/smoke/vaping devices. Subject prompts: fruit (strawberries, blueberries, orange, mango); dessert (caramel cheesecake); bakery (shortbread and pastry); cream (vanilla custard/whipped cream); beverage (cola/citrus and coffee); candy (hard candy/fruit sweets); tobacco (cured leaves only); menthol (mint/ice); nut (hazelnuts/almonds); spice (cinnamon/star anise/vanilla). Illustrative profile artwork, not photographs of individual recipes.

## Validation

29 focused Python tests passed, including artwork selection, main-profile validation/persistence, shared macro rendering, all asset dimensions/sizes, existing story privacy/upload tests, discovery, dashboard and usage tests. Preview smoke checks in jsdom passed for default/profile/main switching, deselection, uploaded-photo priority, removing/restoring an existing photo and legacy tag fallback. Existing usage JS checks, JS/Python syntax and whitespace checks passed. Final live desktop/mobile visual verification follows deployment.
