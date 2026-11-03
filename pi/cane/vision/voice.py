"""Approved vision-awareness phrases; system/fault and legacy object audio are separate."""

VOICE = {
    'PUDDLE': ('puddle_ahead', "Looks like there's a puddle ahead. Take care!"),
    'DAMAGED_PAVEMENT': ('damaged_pavement', 'The pavement ahead looks damaged. Watch your step!'),
    'STAIRS': ('stairs_ahead', 'Careful, there might be stairs ahead.'),
    'CROWD': ('crowd_ahead', 'Looks a bit crowded ahead. Take care!'),
}

CATEGORIES = {'possible_puddle': 'PUDDLE', 'pothole': 'DAMAGED_PAVEMENT',
              'damaged_pavement': 'DAMAGED_PAVEMENT', 'stairs': 'STAIRS', 'downstairs': 'STAIRS'}
