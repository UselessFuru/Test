"""Render build facts from complete source units, never model-written claims."""
import json
import re

from game_guides import (_flat, _markdown, build_instructions, checked_build, game_for,
                         needs_model_selection, requested_sections, section_names,
                         _headings_at, section_key)
from preferred_guides import normalized
from guide_records import records_from_text


def instructions(query):
    return ('\nFor this CHARACTER BUILD request, your FINAL answer must use the JSON selection format below. '
            'Continue making native search_web/read_page calls normally until you have the details. '
            'No free-form introduction, invented surname, equipment effect, ranking or stat is permitted. '
            'Prefer the Genshin Helper Team page when search returns preferred_source. Keep its ranking, '
            'build role, piece counts, ties and conditions. You may set top-level "build_role" to an exact '
            'role id returned by read_page. Choose one role, consistent with the question; never mix roles. '
            'For teams or missing details, read another matching character guide. '
            'The app renders complete source paragraphs/rows with citations. '
            + build_instructions(game_for(query), query)
            + '\nEasier selection: read_page also returns complete records with record_id. '
              'You may return {"rows":[{"record_id":"1:12"},{"record_id":"1:19"}]}. '
              'The app copies the exact page text for those records; do not rewrite it. '
              'For an unclassified record also supply "section" and an exact "label" from its '
              'quote or item heading, e.g. {"record_id":"2:8","section":"equipment","label":"exact item name"}. '
              'A whole build needs named equipment and recommended stats, not only an overview or navigation heading. '
              'Read more pages or sections until those details are available. Keep pre-release caveats visible.')


def decode_selection(answer):
    answer = re.sub(r'^```(?:json)?\s*|\s*```$', '', answer.strip())
    try:
        value = json.loads(answer)
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def source_units(excerpt):
    return {_flat(re.sub(r'^Guide recommendation:\s*', '', line)) for line in excerpt.splitlines()
            if line.strip() and not line.lstrip().startswith(('#', '[…]', '[...]', 'Section context:'))}


def role_source(source, selection, query):
    """One named source role per answer; never combine another role's stats."""
    roles = source.get('build_roles', [])
    if not roles:
        return source
    words = set(normalized(query).split())
    # Specific playstyles in the user's question take precedence over a model
    # selection. General words such as 'build' don't distinguish roles.
    def score(role):
        names = set(normalized(role['name'] + ' ' + role['id']).split()) - {'build', 'and', 'best', 'role'}
        return len(names & words)
    best_score = max(score(r) for r in roles)
    matches = [r for r in roles if best_score and score(r) == best_score]
    chosen = matches[0] if len(matches) == 1 else next((r for r in roles if r['id'] == selection.get('build_role')), None)
    chosen = chosen or next((r for r in roles if r.get('best_role')), roles[0])
    return dict(source, excerpt=chosen['text'], selected_role=chosen['name'])


def retain_cited_passages(rendered, originals, expanded):
    """Citation previews must contain the facts shown, even after focused reads."""
    for source in expanded:
        chunks = []
        for record in source.get('guide_records', []):
            quote = record['quote']
            if _markdown(quote) in rendered:
                heading = ' › '.join(record['headings'])
                chunks.append((heading + '\n' if heading else '') + quote)
        if chunks:
            original = next((s for s in originals if s['id'] == source['id']), None)
            if original is not None:
                text = '\n\n'.join(chunks) + '\n\n' + original.get('excerpt', '')
                original['excerpt'] = text if len(text) <= 26000 else text[:26000].rsplit('\n', 1)[0]


def render_build(answer, sources, query, *, coverage=None):
    selection = decode_selection(answer)
    original_sources = sources
    # Complete original lines are used for validation, not chopped search/read
    # windows that might accidentally remove a qualification or negation.
    sources = [dict(s, excerpt=s.get('full_text') or s.get('excerpt', '')) for s in sources]
    preferred = [role_source(s, selection, query) for s in sources if s.get('preferred_build')]
    others = [s for s in sources if not s.get('preferred_build')]
    by_id = {str(s['id']): s for s in preferred + others}
    rows = []
    for source in others:
        source.setdefault('guide_records', records_from_text(source['excerpt']))
    records = {f"{s['id']}:{r['record_id']}": (s, r) for s in others for r in s['guide_records']}
    for row in selection.get('rows', [])[:24] if isinstance(selection.get('rows'), list) else []:
        if not isinstance(row, dict):
            continue
        if row.get('record_id'):
            entry = records.get(str(row['record_id']))
            if not entry:
                continue
            source, record = entry
            row = {'source_id': source['id'], 'quote': record['quote'],
                   'section': row.get('section') or record['section'],
                   'label': row.get('label') or record['label']}
        source = by_id.get(str(row.get('source_id')))
        quote, label = row.get('quote'), row.get('label', '')
        if not source or not isinstance(quote, str) or not isinstance(label, str):
            continue
        if _flat(quote) not in source_units(source['excerpt']):
            continue
        # Don't shorten an enclosing item name (e.g. source says 'X Replica'
        # but the model labels it 'X'). All displayed names stay exact.
        if row.get('section') in ('weapons', 'equipment'):
            heads = _headings_at(source['excerpt'], quote)
            table_label = '|' in quote and _flat(label) in [_flat(c) for c in quote.strip('|').split('|')]
            if not table_label and heads and heads[-1][0] >= 3 and not section_key(heads[-1][1]):
                if _flat(label) != _flat(heads[-1][1]):
                    continue
        rows.append(row)
    if not preferred:
        rendered = checked_build({'rows': rows}, others, query, coverage=coverage)
        retain_cited_passages(rendered, original_sources, others)
        return rendered

    preferred_ids = {str(s['id']) for s in preferred}
    main = checked_build({'rows': [r for r in rows if str(r.get('source_id')) in preferred_ids]}, preferred, query, coverage=coverage)
    if main and preferred[0].get('selected_role'):
        role = _markdown(preferred[0]['selected_role'])
        main = main.replace('\n\n*Build: ' + role + '*', '')
        main = '*' + role + '*\n\n' + main
    pieces = [main] if main else []
    # Other matching publishers fill genuinely missing sections, without
    # overwriting the preferred source's named equipment, rankings or stats.
    mapping = section_names(game_for(query))
    for key in requested_sections(query):
        if key == 'overview' or '### ' + mapping[key] + '\n' in main:
            continue
        # A constrained selection must not silently fall back to another
        # publisher's unconditional top-ranked loadout.
        if needs_model_selection(query) and key != 'teams':
            continue
        subquery = query.split(' — ', 1)[0] + ' — ' + mapping[key]
        piece = checked_build({'rows': [r for r in rows if r.get('section') == key and str(r.get('source_id')) not in preferred_ids]}, others, subquery, coverage=coverage)
        if piece:
            pieces.append(piece)
    rendered = '\n\n'.join(pieces)
    retain_cited_passages(rendered, original_sources, others)
    if rendered and main:
        source = preferred[0]
        rendered = '## ' + _markdown(source['canonical_character']) + '\n\n' + rendered
        # Keep the selected role's actual fields in the in-chat citation, even
        # if the model's focused read window originally showed another role.
        original = next((s for s in original_sources if s['id'] == source['id']), None)
        if original is not None:
            excerpt = source['excerpt']
            original['excerpt'] = excerpt if len(excerpt) <= 26000 else excerpt[:26000].rsplit('\n', 1)[0]
    return rendered


def missing_sections(query, coverage):
    wanted = set(requested_sections(query))
    required = {'weapons', 'equipment', 'stats'} if 'overview' in wanted else wanted
    return [key for key in requested_sections(query) if key in required - set(coverage)]


def incomplete_answer(answer, query, missing):
    headings = section_names(game_for(query))
    names = ', '.join(headings[key].lower() for key in missing)
    return answer + '\n\n*Still unverified from the readable pages: ' + names + '. These details have been left out.*'
