"""Live Genshin Helper Team fields; no roster, guide copy or site code bundled."""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import urljoin, urlsplit
from lxml import html as lhtml
from lxml.etree import LxmlError

HOME = "https://genshin-impact-helper-team.github.io/genshin-builds/en/"


def cls(name):
    return "contains(concat(' ', normalize-space(@class), ' '), ' " + name + " ')"


def text(node):
    return re.sub(r"\s+", " ", ' '.join(node.itertext())).strip()


def normalized(value):
    return " ".join(re.findall(r"[^\W_]+", unicodedata.normalize('NFKC', value).casefold()))


def helper_url(url):
    p = urlsplit(url)
    return p.scheme == 'https' and p.hostname == 'genshin-impact-helper-team.github.io' and p.path.startswith('/genshin-builds/en/')


def find_character(markup, question):
    """Match against the site's live names, never model-invented query additions."""
    try:
        root = lhtml.fromstring(markup, parser=lhtml.HTMLParser(no_network=True))
    except (ValueError, LxmlError):
        return None
    question = ' ' + normalized(question) + ' '
    found, prefixes = [], []
    for card in root.xpath('//a[@data-character-card and @data-name and @href]'):
        name = card.get('data-name', '').strip()
        words = normalized(name).split()
        if name and words:
            url = urljoin(HOME, card.get('href'))
            if helper_url(url):
                entry = {'name': name, 'url': url}
                if ' ' + normalized(name) + ' ' in question:
                    found.append(entry)
                elif len(words) > 1 and len(words[0]) >= 3 and ' ' + words[0] + ' ' in question:
                    prefixes.append(entry)
    # Ambiguous/multiple characters are researched normally; don't pick one.
    unique = {row['name']: row for row in found or prefixes}
    return next(iter(unique.values())) if len(unique) == 1 else None


def parse_build(markup, url):
    if not helper_url(url):
        return None
    try:
        root = lhtml.fromstring(markup, parser=lhtml.HTMLParser(no_network=True, remove_comments=True))
    except (ValueError, LxmlError):
        return None
    titles = root.xpath('//*[@id="character-title"]')
    panels = root.xpath('//*[@data-build-panel]')
    if len(titles) != 1 or not panels:
        return None
    character = text(titles[0])
    if not character or len(character) > 80:
        return None
    notes = {n.get('data-note-id'): text(n) for n in root.xpath('//*[@data-note-id and ' + cls('note-target') + ']')}
    lines, roles = ['# ' + character + ' — Genshin Impact'], []

    def attached_note(row):
        ids = row.xpath('.//button[@data-note-id]/@data-note-id')
        return ' '.join(dict.fromkeys(notes[i] for i in ids if i in notes))

    for panel in panels:
        role_id = panel.get('data-id', '')
        buttons = root.xpath('//button[@data-build-tab and @data-id=$id]', id=role_id)
        if not buttons:
            continue
        button = buttons[0]
        labels = button.xpath('.//*[' + cls('build-switcher-name') + ']')
        role = text(labels[0]) if labels else role_id
        version = button.get('data-last-updated', '')
        roles.append({'id': role_id, 'name': role, 'version': version,
                      'best_role': bool(button.xpath('.//*[' + cls('build-badge') + ']'))})
        lines.append('## Build: ' + role)
        if version:
            lines.append('Source update label: ' + version)
        for card_class, heading in [('weapon-card', 'Weapons'), ('artifact-sets-card', 'Artifacts')]:
            cards = panel.xpath('.//*[' + cls(card_class) + ']')
            if not cards:
                continue
            lines.append('### ' + heading)
            for row in cards[0].xpath('.//*[' + cls('rank-row') + ']'):
                content = row.xpath('./*[' + cls('rank-content') + ']')
                if not content:
                    continue
                # Piece counts, refinements and mixed 2-piece options remain
                # attached to the exact source label instead of being guessed.
                label = text(content[0])
                if not label or len(label) > 180:
                    continue
                numbers = row.xpath('./*[' + cls('rank-number') + ']')
                approx = bool(row.xpath('./*[' + cls('rank-approx') + ']'))
                if not numbers:
                    groups = row.xpath('ancestor::*[' + cls('rank-group') + ']')
                    if groups:
                        numbers = groups[-1].xpath('.//*[' + cls('rank-number') + ']')
                note = attached_note(row)
                conditional = 'conditional-row' in row.get('class', '').split()
                if conditional and not note:
                    continue  # Never publish an option stripped of its condition.
                if conditional:
                    detail = 'Conditional option for ' + role + '.'
                elif numbers:
                    detail = ('Approximately tied at source rank ' if approx else 'Source rank ') + text(numbers[0]) + ' for ' + role + '.'
                else:
                    detail = 'Listed option for ' + role + '.'
                # Make each record unique so identical rank numbers in weapon
                # and artifact lists can't acquire the other list's heading.
                detail = label + ' — ' + detail
                groups = row.xpath('ancestor::*[' + cls('rank-group') + ']')
                if groups:
                    peers = [text(n) for n in groups[-1].xpath('.//*[' + cls('rank-content') + ']')]
                    peers = [p for p in peers if p != label]
                    if peers:
                        detail += ' Approximately tied with ' + ' / '.join(peers) + ' at this rank.'
                if 'is-free-weapon' in row.get('class', '').split():
                    detail += ' Marked as a free option by this guide.'
                if note:
                    detail += ' ' + note
                lines.extend(['#### ' + label, 'Guide recommendation: ' + detail])
        stats = panel.xpath('.//*[' + cls('artifact-stats-card') + ']')
        if stats:
            lines.append('### Main stats and substats')
            for row in stats[0].xpath('.//*[' + cls('stat-row') + ']'):
                labels = row.xpath('./strong')
                values = row.xpath('./span')
                if labels and values:
                    value = text(labels[0]) + ': ' + text(values[0])
                    note = attached_note(row)
                    lines.append(value + ('. ' + note if note else ''))
            priorities, qualifications = [], []
            for group in stats[0].xpath('.//*[' + cls('rank-group') + ']'):
                values = group.xpath('.//*[' + cls('rank-content') + ']')
                if values:
                    priorities.append(' ≈ '.join(text(n) for n in values))
                for row in group.xpath('.//*[' + cls('rank-row') + ']'):
                    note = attached_note(row)
                    if note:
                        qualifications.append(note)
            if priorities:
                lines.append('Substats: ' + ' > '.join(priorities) + ('. ' + ' '.join(dict.fromkeys(qualifications)) if qualifications else ''))
        talents = panel.xpath('.//*[' + cls('talent-card') + ']')
        if talents:
            priorities = []
            for group in talents[0].xpath('.//*[' + cls('rank-group') + ']'):
                values = group.xpath('.//*[' + cls('rank-content') + ']')
                if values:
                    priorities.append(' ≈ '.join(text(n) for n in values))
            if priorities:
                lines.extend(['### Talents', 'Talent Priority: ' + ' > '.join(priorities)])
    if not roles or not any(line.startswith('Guide recommendation:') for line in lines):
        return None
    # Keep complete roles separate so on-field gear cannot be silently mixed
    # with another role's stats. These are live source fields, not a database.
    blocks = re.split(r'(?m)^## Build: ', '\n'.join(lines))[1:]
    for role, block in zip(roles, blocks):
        role['text'] = '# ' + character + ' — Genshin Impact\n## Build: ' + block
    return {'title': character + ' build — Genshin Impact Helper Team', 'url': url,
            'text': '\n'.join(lines), 'links': [], 'canonical_character': character,
            'build_roles': roles, 'preferred_build': True}
