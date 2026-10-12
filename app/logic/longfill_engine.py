import math

class LongFillEngine:
    @staticmethod
    def calculate_batch(recipe, style, vessel_size):
        """
        recipe: dict with flavors [{name, percentage, density}]
        style: 'MTL' or 'DTL'
        vessel_size: float (ml)
        """
        # 1. Determine Retail Unit Size based on Style
        retail_unit_vol = 30.0 if style == 'MTL' else 120.0
        
        # 2. Determine PG Offset (DTL adds 10ml PG nicotine later)
        pg_offset_per_unit = 10.0 if style == 'DTL' else 0.0
        
        # 3. Calculate Base Volume per Retail Unit (Flavor Total + PG)
        flavor_total_pct = sum(f['percentage'] for f in recipe['flavors'])
        
        # Total Base Vol = (Flavor Pct of Retail Unit) + (PG portion - nicotine offset)
        # For Long Fills, the "Base" is usually 25-33% of the retail bottle
        # Let's assume the recipe defines flavor % relative to the FINAL bottle.
        
        flavor_vol_per_unit = (flavor_total_pct / 100) * retail_unit_vol
        
        # Target PG is usually the rest of the PG-side of the longfill
        # Typically: Longfill Base = 25% of bottle. 
        # If flavors = 10%, PG = 15%. If DTL, PG = 15% - 10ml.
        base_target_vol = (0.25 * retail_unit_vol) # Standard 25% Longfill
        pg_vol_per_unit = base_target_vol - flavor_vol_per_unit - pg_offset_per_unit

        total_base_per_unit = flavor_vol_per_unit + pg_vol_per_unit
        
        # 4. Calculate Whole Units that fit in Vessel
        num_units = math.floor(vessel_size / total_base_per_unit)
        
        # 5. Build Ingredient List in Grams
        batch_ingredients = []
        for f in recipe['flavors']:
            grams = (f['percentage'] / 100) * retail_unit_vol * num_units * f['density']
            batch_ingredients.append({
                'name': f['name'],
                'target_grams': round(grams, 2),
                'actual_grams': 0.0,
                'type': 'flavor'
            })
            
        # Add PG Step
        pg_grams = pg_vol_per_unit * num_units * 1.036 # PG Density
        batch_ingredients.append({
            'name': 'Propylene Glycol (PG)',
            'target_grams': round(pg_grams, 2),
            'actual_grams': 0.0,
            'type': 'pg'
        })
        
        return {
            'num_units': num_units,
            'total_volume': num_units * total_base_per_unit,
            'headroom': vessel_size - (num_units * total_base_per_unit),
            'ingredients': batch_ingredients
        }