import re

def classify_product(name):
    name_clean = name.lower()
    
    # 1. Default Data Structure
    data = {
        'category': 'Flavor',
        'subtype': None,
        'unit': 'ml',
        'initial_volume': 10.0,
        'nic_strength': 0.0
    }

    # 2. Volume Parsing (Extracts 30 from "30ml" or 5000 from "5000ml")
    volume_match = re.search(r'(\d+)\s*ml', name_clean)
    if volume_match:
        data['initial_volume'] = float(volume_match.group(1))

    # 3. Rules for HARDWARE
    hardware_keys = ['bottle', 'cap', 'can', 'jerry', 'container', 'pet', 'glass', 'dripper', 'hoodie', 'shirt']
    if any(key in name_clean for key in hardware_keys):
        data['category'] = 'Hardware'
        data['unit'] = 'unit'
        data['initial_volume'] = 1.0
        return data

    # 4. Rules for NICOTINE
    nic_keys = ['nic', 'nicotine', 'salt', 'freebase']
    if any(key in name_clean for key in nic_keys):
        data['category'] = 'Nicotine'
        data['subtype'] = 'Salt' if 'salt' in name_clean else 'Freebase'
        # Optional: Extract strength if in name (e.g., "12mg")
        strength_match = re.search(r'(\d+)mg', name_clean)
        if strength_match:
            data['nic_strength'] = float(strength_match.group(1))
        return data

    # 5. Rules for BASE
    base_keys = ['pg', 'vg', 'propylene glycol', 'vegetable glycerine']
    if any(key in name_clean for key in base_keys):
        data['category'] = 'Base'
        data['subtype'] = 'PG' if 'pg' in name_clean else 'VG'
        return data

    # 6. Rules for FLAVOR ADDITIVES
    additive_keys = ['sweetener', 'enhancer', 'additive', 'erythritol', 'dispo sweet']
    if any(key in name_clean for key in additive_keys):
        data['category'] = 'Flavor'
        data['subtype'] = 'Additive'
        
    return data