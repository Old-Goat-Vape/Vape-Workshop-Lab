class BatchReconciler:
    @staticmethod
    def reconcile(recipe, style, new_vessel_size, unit_count, previously_added_list):
        """
        Calculates a new batch while reconciling already-poured ingredients.
        previously_added_list: [{'name': 'Flavor A', 'actual_poured': 2.50}, ...]
        """
        # 1. Standard Volume Constants
        retail_unit_vol = 30.0 if style == 'MTL' else 120.0
        pg_offset = 10.0 if style == 'DTL' else 0.0
        
        # 2. Calculate New Target Weights for the WHOLE batch
        # (This uses your 'Recipe Integrity' rule)
        reconciled_steps = []
        
        # Loop through recipe ingredients
        for i, flavor in enumerate(recipe['flavors']):
            # Total weight required for the perfect recipe at this unit count
            target_total = (flavor['percentage'] / 100) * retail_unit_vol * unit_count * flavor['density']
            
            # Check if we already poured this in the previous (overpoured) session
            already_poured = 0.0
            if i < len(previously_added_list):
                already_poured = previously_added_list[i]['actual_poured']
            
            delta = max(0, target_total - already_poured)
            
            reconciled_steps.append({
                'name': flavor['name'],
                'target_total': round(target_total, 2),
                'already_poured': round(already_poured, 2),
                'delta_to_add': round(delta, 2),
                'status': 'skip' if delta <= 0.01 else 'pending'
            })

        # 3. Handle PG (Always the final step)
        flavor_total_pct = sum(f['percentage'] for f in recipe['flavors'])
        flavor_vol_per_unit = (flavor_total_pct / 100) * retail_unit_vol
        pg_vol_per_unit = (0.25 * retail_unit_vol) - flavor_vol_per_unit - pg_offset
        
        pg_target_total = pg_vol_per_unit * unit_count * 1.036
        
        # Check if PG was already started (rare, but possible)
        pg_poured = 0.0
        if len(previously_added_list) > len(recipe['flavors']):
            pg_poured = previously_added_list[-1]['actual_poured']

        pg_delta = max(0, pg_target_total - pg_poured)

        reconciled_steps.append({
            'name': 'Propylene Glycol (PG)',
            'target_total': round(pg_target_total, 2),
            'already_poured': round(pg_poured, 2),
            'delta_to_add': round(pg_delta, 2),
            'status': 'skip' if pg_delta <= 0.01 else 'pending'
        })

        return reconciled_steps