"""Complete, addressable evidence units from generic guide page structure.

No sites, characters or item database: headings and table columns supply the
relationships. Records live only as long as the current question.
"""
import re

from game_guides import _flat, section_key


NAVIGATION = re.compile(r'\b(?:navigation|table of contents|list of contents|related guides?|jump to|read more|click here|sequence nodes|resonance chain|upgrade materials)\b', re.I)
STAT_VALUE = re.compile(r'\b(?:crit|atk|attack|def|defen[cs]e|hp|health|damage|dmg|energy|recharge|regen\w*|mastery|speed|strength|healing|shield|resistance|precision|efficiency)\b|\d|%', re.I)
HEADER_VALUES = re.compile(r'^(?:recommendations?|description|main stats?|stats?|benefits?|effects?|slots?|cost|build|rank|rarity|how obtained|character|role|item|name|weapons?|equipment)$', re.I)


def valid_stat(quote, headings=()):
    """A navigation heading is never a slot/stat recommendation."""
    quote = _flat(quote)
    if quote.startswith(('#', 'Table columns:')) or NAVIGATION.search(quote):
        return False
    if any(_flat(h) == quote for h in headings):
        return False
    cells = [c.strip() for c in re.split(r'\s*\|\s*|:\s*', quote, maxsplit=1)]
    if len(cells) == 2:
        label, value = cells
        return bool(label and value and not HEADER_VALUES.fullmatch(value)
                    and not re.search(r'\b(?:best|guide|overview|nodes|contents)\b', value, re.I)
                    and (STAT_VALUE.search(value) or '|' in quote))
    # Complete stat priorities / qualifications can be prose or comparisons.
    return bool(STAT_VALUE.search(quote) and (re.search(r'[>%=]|\b(?:only|if|when|unless|until|target|priority|needs?|recommended)\b', quote, re.I)))


def records_from_text(text):
    """Keep complete rows/paragraphs plus their actual heading ancestry."""
    records, heads, table_header = [], [], []
    lines = text.splitlines()
    heading_labels = {re.sub(r'^#{1,6}\s+', '', line).strip() for line in lines if line.startswith('#')}
    seen = set()
    for index, raw in enumerate(lines):
        line = raw.strip()
        match = re.match(r'^(#{1,6})\s+(.+)', line)
        if match:
            depth, title = len(match[1]), match[2]
            while heads and heads[-1][0] >= depth:
                heads.pop()
            heads.append((depth, title))
            table_header = []
            continue
        if line.startswith('Table columns: '):
            table_header = [v.strip() for v in line[len('Table columns: '):].split('|')]
            continue
        if any(NAVIGATION.search(h) for _, h in heads):
            continue
        if (not line or line.startswith(('Section context:', '[…]', '[...]'))
                or NAVIGATION.search(line) or line in heading_labels):
            continue
        quote = re.sub(r'^Guide recommendation:\s*', '', line)
        if not 6 <= len(quote) <= 900:
            continue
        category = next((section_key(h) for d, h in reversed(heads) if d > 1 and section_key(h)), '')
        if category == 'stats' and any(re.fullmatch(r'(?:kit|profile|base stats|character stats)', h, re.I) for _, h in heads):
            continue  # Level/ascension base values are not build stat targets.
        title = heads[-1][1] if heads else ''
        named_heading = bool(heads and heads[-1][0] >= 3 and not section_key(title))
        label, automatic = '', False
        cells = [v.strip() for v in line.strip('|').split('|')] if '|' in line else []
        if len(cells) >= 2:
            # A table's header describes columns, not a recommended item.
            if (all(HEADER_VALUES.fullmatch(v) or not v for v in cells)
                    or re.fullmatch(r'(?:slot|rank|item|name)', cells[0], re.I)):
                table_header = cells
                continue
            first, value = cells[0], cells[1]
            field = section_key(first)
            if len(cells) == 2 and re.match(r'^(?:best|recommended|main)\s+', first, re.I) and field in ('weapons', 'equipment'):
                category, label, automatic = field, value, True
            elif len(cells) == 2 and (category == 'stats' or field == 'stats' or re.search(r'\b(?:\d[ -]?cost|energy regen|substats?|stat priority)\b', first, re.I)) and valid_stat(line, heading_labels):
                category, label, automatic = 'stats', first, True
            elif len(cells) == 2 and field == 'skills' and re.search(r'priority|rotation', first, re.I):
                category, automatic = 'skills', True
            elif category in ('weapons', 'equipment') and table_header:
                item_column = next((i for i, h in enumerate(table_header) if re.search(r'item|name|weapon|equipment|set|engine|cone', h, re.I)), 0)
                label = cells[item_column] if item_column < len(cells) else ''
                automatic = bool(label and len(cells) > 1 and not HEADER_VALUES.fullmatch(label))
            elif table_header and re.fullmatch(r'slot', table_header[0], re.I):
                label = first if category == 'stats' else value
                automatic = category == 'stats' and valid_stat(line, heading_labels)
            elif table_header and not category:
                # Unknown games' table column names remain useful evidence;
                # Qwen supplies the category, never an invented item name.
                label = first
            elif category == 'teams' and 2 <= len(cells) <= 6:
                if all(1 <= len(v) <= 70 and not re.fullmatch(r'(?:(?:main|sub)[ -]?)?(?:DPS|support|sustain|healer)(?:\s*/\s*(?:Unison |sub-)?(?:DPS|support|sustain|healer))*', v, re.I) for v in cells):
                    label, automatic = title, True
        elif category == 'stats' and valid_stat(quote, heading_labels):
            label = quote.split(':', 1)[0] if ':' in quote else ''
            automatic = bool(label or re.search(r'[>=]|\b(?:only matters|only relevant|if using|when using|unless)\b', quote, re.I))
        elif category == 'teams' and (line.startswith('Team lineup:') or
                re.search(r'\S\s+(?:/|\+)\s+\S.*\s+(?:/|\+)\s+\S', line)):
            label, automatic = title, True
        elif category == 'skills' and re.match(r'(?:Talent|Skill) Priority:', line, re.I):
            automatic = True
        elif named_heading or (heads and heads[-1][0] >= 3 and line.startswith('Guide recommendation:')):
            label = title
            # Already structured recommendation cards are unambiguous. Plain
            # prose stays available for Qwen to select, including unknown gear.
            automatic = line.startswith('Guide recommendation:') and category in ('weapons', 'equipment')
            if not automatic and category in ('weapons', 'equipment') and len(quote.split()) >= 8:
                # Generic ranked-item sections: useful for article paragraphs
                # that have no publisher-specific recommendation-card markup.
                ranked = any(re.match(r'^(?:best|recommended)\s+', h, re.I) and section_key(h) == category for _, h in heads[:-1])
                automatic = bool(ranked and re.search(r'\b(?:best|signature|recommended|alternative|option)\b', quote, re.I)
                                 and not re.search(r'\b(?:not recommended|avoid|do not use)\b', quote, re.I))
        if category == 'stats' and not valid_stat(quote, heading_labels):
            category, automatic = '', False
        if category == 'stats' and any(re.search(r'\b(?:endgame|total stats)\b', h, re.I) for _, h in heads):
            automatic = False  # Total stat targets need their assumptions, unlike slot main stats.
        if len(label) > 120:
            label, automatic = '', False
        key = (category, label, quote)
        if key in seen:
            continue
        seen.add(key)
        records.append({'record_id': str(len(records) + 1), 'section': category,
                        'label': label, 'quote': quote, 'automatic': automatic,
                        'headings': [h for d, h in heads if d > 1], 'line': index})
    return records


def public_records(source, focus='', limit=24):
    """Small-model-friendly IDs; retain diversity instead of only a long kit."""
    records = source.get('guide_records', [])
    words = set(re.findall(r'\w{3,}', focus.casefold()))
    ranked = sorted(records, key=lambda r: (not r['automatic'],
        -len(words & set(re.findall(r'\w{3,}', ' '.join(r['headings']).casefold()))),
        not bool(re.search(r'\b(?:best|recommended|alternative|priority|ideal)\b', r['quote'], re.I)), r['line']))
    selected, counts = [], {}
    for record in ranked:
        key = record['section'] or 'unclassified'
        if counts.get(key, 0) >= (6 if key in ('stats', 'unclassified') else 4):
            continue
        counts[key] = counts.get(key, 0) + 1
        selected.append(record)
        if len(selected) >= limit:
            break
    return [dict(record_id=f"{source['id']}:{r['record_id']}", section=r['section'],
                 label=r['label'], quote=r['quote'], headings=r['headings'][-3:]) for r in selected]
