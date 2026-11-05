"""Conservative subject matching and live-result spelling recovery."""
import re
from urllib.parse import unquote, urlsplit

from game_guides import (BUILD_WORDS, GAME_PATTERN, _explicit_game, game_for,
                         games_in, source_games, subject_terms)


def identity_query(query):
    anchor = query.split(' — ', 1)[0]
    prefix = re.split(r'\b(?:with|without|using|only)\b', anchor, maxsplit=1, flags=re.I)[0]
    identity = prefix if subject_terms(prefix) else anchor
    identity = re.sub(r'\b(?:hyperbloom|off[ -]field|on[ -]field|shield|support|dps)\b', '', identity, flags=re.I)
    game = _explicit_game(anchor)
    if game and not _explicit_game(identity):
        identity += ' in ' + game
    return identity


def character_terms(query):
    identity = identity_query(query)
    game = _explicit_game(identity)
    if game:
        identity = re.sub(re.escape(game), ' ', identity, flags=re.I)
    return subject_terms(GAME_PATTERN.sub(' ', identity))


def game_matches(query, title, url, intro=''):
    wanted = game_for(query).key
    if wanted != 'other':
        return source_games(title, url, intro) == {wanted}
    game = _explicit_game(query)
    words = set(re.findall(r'\w+', (title + ' ' + unquote(url).replace('-', ' ') + ' ' + intro).casefold()))
    return bool(game and set(re.findall(r'\w+', game.casefold())) <= words)


def one_typo(left, right):
    """One inserted/deleted/substituted letter or transposition, long names only."""
    a, b = left.casefold(), right.casefold()
    if min(len(a), len(b)) < 5 or a[0] != b[0] or abs(len(a) - len(b)) > 1 or a == b:
        return False
    if len(a) == len(b):
        positions = [i for i in range(len(a)) if a[i] != b[i]]
        return len(positions) == 1 or (len(positions) == 2 and positions[1] == positions[0] + 1
                                     and a[positions[0]] == b[positions[1]] and a[positions[1]] == b[positions[0]])
    short, long = sorted((a, b), key=len)
    return any(long[:i] + long[i + 1:] == short for i in range(len(long)))


def spelling_hint(query, results):
    terms = character_terms(query)
    if len(terms) != 1:
        return None  # Never silently rename a multiword identity.
    name, candidates = terms[0], {}
    for row in results:
        title, url = row.get('title', ''), row.get('url', '')
        if not BUILD_WORDS.search(title) or not game_matches(query, title, url):
            continue
        tokens = re.findall(r'[^\W\d_]+', title)
        if name in [t.casefold() for t in tokens]:
            return None  # An exact matching guide exists: do not substitute.
        for token in tokens:
            if one_typo(name, token):
                key = token.casefold()
                candidate = candidates.setdefault(key, {'name': token, 'hosts': set(), 'urls': []})
                host = (urlsplit(url).hostname or '').removeprefix('www.')
                candidate['hosts'].add(host)
                if url not in candidate['urls']:
                    candidate['urls'].append(url)
    confirmed = [c for c in candidates.values() if len(c['hosts']) >= 2]
    if len(confirmed) != 1 or len(candidates) != 1:
        return None
    candidate = confirmed[0]
    return {'original': name, 'name': candidate['name'], 'urls': candidate['urls'],
            'note': 'A close spelling appears in guide titles on multiple sites for this game. Verify it again in the page title before using it.'}
