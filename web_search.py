"""Public web search and bounded guide excerpts; no remote language model."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import base64
import html
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, getproxies, proxy_bypass

from game_guides import (BUILD_WORDS, FILLER, GAME_PATTERN, GENERIC, context_query,
                         _explicit_game, game_for, games_in, source_games, subject_terms)


class WebSearchError(Exception):
    pass


# These are publisher directories, not a per-character database. Character
# and related build/team URLs are followed only when the publisher links them.
GUIDE_DIRECTORIES = {
    "genshin": ("https://www.icy-veins.com/genshin-impact/tier-list", "https://keqingmains.com/"),
    "wuwa": ("https://www.prydwen.gg/wuthering-waves/characters",),
    "zzz": ("https://www.icy-veins.com/zenless-zone-zero/tier-list",),
    "nte": ("https://www.icy-veins.com/neverness-to-everness/tier-list",),
}
_DIRECTORY_CACHE = {}  # Published links only; refreshed every 15 minutes.


GUIDE_WORDS = re.compile(
    r"\b(builds?|guides?|walkthroughs?|quests?|puzzles?|weapons?|artifacts?|relics?|"
    r"loadouts?|rotations?|tier[\s-]?lists?|boss(?:es)?|team(?:s| comps?)?|"
    r"patch(?:es| notes)?|banners?|reruns?|meta|genshin|honkai|wuthering|"
    r"zenless|wuwa|zzz|neverness|nte|echo(?:es|s)?|sonata|w[ -]?engines?|drive (?:discs?|disks?)|arcs?|modules?|consoles?|elden ring)\b|攻略|ビルド|武器|聖遺物", re.I)
LOOKUP_WORDS = re.compile(
    r"\b(search|look\s*(?:up|online)|google|browse|internet|online|latest|"
    r"current|today|recent|news|weather)\b|調べ|検索|最新", re.I)
FOLLOWUP = re.compile(
    r"\b(her|hers|she|him|his|he|their|they|it|that|those|these)\b|"
    r"^(?:what about|how about|and |also |which (?:weapon|artifact|relic|team))", re.I)


def should_search(text: str, mode: str, history: list = ()) -> bool:
    if mode == "off":
        return False
    if mode == "always":
        return True
    if GUIDE_WORDS.search(text) or LOOKUP_WORDS.search(text):
        return True
    # A bare new character name after a build is also a new web lookup.
    resolved = search_query(text, history)
    if resolved != text and BUILD_WORDS.search(resolved):
        return True
    return bool(FOLLOWUP.search(text) and any(
        GUIDE_WORDS.search(str(item.get("content", "")))
        for item in history[-4:] if item.get("role") == "user"))


def _clean_query(text: str) -> str:
    query = re.sub(r"\s+", " ", text).strip()
    # Strip request wording only at the start; keep names, quoted titles,
    # negations, versions, and equipment constraints in the actual question.
    prefixes = (
        r"^(?:hey|hi|hello)[,! ]+",
        r"^(?:oh|mb|my bad|sorry|sry|oops|whoops|actually|i meant|i mean)\b[ ,:]*",
        r"^(?:now|next|then|okay|ok)\b[ ,:]*",
        r"^(?:please|pls|plz)\b[ ,]*",
        r"^(?:can|could|would|will)\s+(?:you|u)\b[ ,]*",
        r"^(?:give|show|tell)\s+(?:me|us)\s+",
        r"^(?:find|search for|look up)\s+(?:me\s+)?",
        r"^(?:i want|i need|i would like|i'd like)\s+",
    )
    for _ in range(6):
        previous = query
        for prefix in prefixes:
            query = re.sub(prefix, "", query, count=1, flags=re.I)
        if query == previous:
            break
    query = re.sub(r"\s+(?:please|pls|plz)[.!?]*$", "", query, flags=re.I)
    query = query.strip(" ?!.;,:")
    match = re.fullmatch(r"(?:a |an |the )?((?:best )?(?:build )?guide|(?:best )?build|walkthrough) for (.+)", query, re.I)
    if match:
        query = match[2] + " " + match[1]
    # This full name unambiguously identifies the game; 'Raiden' alone does not.
    if re.search(r"\braiden shogun\b", query, re.I) and not games_in(query) and not re.search(r"\b(?:in|from)\s+", query, re.I):
        query += " Genshin Impact"
    return query


def search_query(text: str, history: list = ()) -> str:
    # Resolve explicit current-question context before inheriting any game.
    # This also preserves the existing unambiguous Raiden Shogun game hint.
    return _clean_query(context_query(_clean_query(text), history))[:350]


_QUERY_FILLER = FILLER
_GAME_CONTEXT = GAME_PATTERN


def _topic_terms(query: str) -> list[str]:
    return subject_terms(query) or list(games_in(query))


def relevant_result(row: dict, query: str) -> bool:
    if not isinstance(row, dict):
        return False
    text = html.unescape(" ".join(str(row.get(k) or "") for k in
                                 ("body", "description", "snippet", "excerpt")))
    title = str(row.get("title") or "")
    url = str(row.get("href") or row.get("url") or "")
    wanted = game_for(query)
    actual = source_games(title, url, text)
    # An exact title/path game takes precedence over a different game's name
    # appearing in related links or the publisher's footer.
    if wanted is not GENERIC and actual != {wanted.key}:
        return False
    if wanted is GENERIC and not games_in(query):
        # Unlisted games are not a whitelist exception: verify the title the
        # user supplied against the page just as we verify a known game.
        game_name = _explicit_game(query)
        game_words = re.findall(r"[^\W_]+", game_name.casefold())
        identity = title + " " + unquote(url).replace("-", " ")
        identity_words = set(re.findall(r"[^\W_]+", identity.casefold()))
        if games_in(identity) and game_words and not all(word in identity_words for word in game_words):
            return False
        page_words = set(re.findall(r"[^\W_]+", (identity + " " + text[:1800]).casefold()))
        if game_words and not all(word in page_words for word in game_words):
            return False
    terms = subject_terms(query)
    if not terms:
        return wanted is not GENERIC and wanted.key in actual
    # The character must occur in the title/intro or URL, not merely somewhere
    # in a long site's character-navigation list.
    focused = title + " " + unquote(url) + " " + text[:1800]
    words = set(re.findall(r"[^\W_]+", focused.casefold()))
    return terms[0] in words and sum(term in words for term in terms) >= min(2, len(terms))


def _focused_query(query: str) -> str:
    terms = _topic_terms(query)[:4]
    if not terms:
        return query
    match = re.search(r"\b" + r"\W+".join(re.escape(t) for t in terms) + r"\b", query, re.I)
    if match and '"' not in query:
        return query[:match.start()] + '"' + match[0] + '"' + query[match.end():]
    return query + " " + " ".join('"' + t + '"' for t in terms[:2])


def search_url(query: str) -> str:
    return "https://duckduckgo.com/?" + urlencode({"q": query})


def public_url(value: str) -> str:
    """Validate link syntax without performing a network request."""
    if not isinstance(value, str) or len(value) > 2500 or re.search(r"[\s\\\x00-\x1f]", value):
        return ""
    try:
        p = urlsplit(value)
        host = (p.hostname or "").rstrip(".").lower()
        if (p.scheme not in ("http", "https") or p.username is not None
                or p.password is not None or p.port not in (None, 80, 443)
                or "." not in host or "%" in host
                or host.endswith((".localhost", ".local", ".internal", ".lan", ".home"))):
            return ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            if not re.fullmatch(r"[a-z0-9.-]+", host.encode("idna").decode()):
                return ""
        if re.search(r"\.(?:exe|msi|bat|cmd|ps1|zip|rar|7z|dmg|apk)$", p.path, re.I):
            return ""
        return urlunsplit((p.scheme, p.netloc, p.path or "/", p.query, p.fragment))
    except (ValueError, UnicodeError):
        return ""


def _validate_destination(url: str):
    if not public_url(url):
        raise WebSearchError("This result is not a public web page.")
    p = urlsplit(url)
    try:
        addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80),
                                       type=socket.SOCK_STREAM)
    except socket.gaierror:
        # Some configured HTTP proxies resolve public names remotely while
        # the local resolver has no internet DNS. Literal private/local URLs
        # were already rejected above; do not disable the user's proxy.
        if getproxies().get(p.scheme) and not proxy_bypass(p.hostname):
            return
        raise
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise WebSearchError("Local or private web destinations are not read.")


class _PublicRedirects(HTTPRedirectHandler):
    max_redirections = 3

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_destination(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_page(url: str, check=lambda: None, *, extract: bool = True) -> tuple[str, str]:
    """Read text only; no browser scripts, cookies, credentials, or file access."""
    check()
    _validate_destination(url)
    p = urlsplit(url)
    url = urlunsplit((p.scheme, p.netloc, quote(p.path, safe="/%:@-._~!$&'()*+,;="),
                     quote(p.query, safe="=&%:@/?-._~!$'()*+,;"), ""))
    headers = {"User-Agent": "RaidenCompanion/0.11 (personal guide reader)",
               "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8",
               "Accept-Encoding": "identity"}
    deadline = time.monotonic() + 20
    with build_opener(_PublicRedirects()).open(Request(url, headers=headers), timeout=12) as response:
        kind = response.headers.get_content_type()
        if kind not in ("text/html", "application/xhtml+xml", "text/plain"):
            raise WebSearchError("The result is not a readable HTML or text page.")
        chunks, size = [], 0
        while True:
            check()
            if time.monotonic() > deadline:
                raise WebSearchError("The page took too long to read.")
            chunk = response.read(65536)
            if not chunk:
                break
            size += len(chunk)
            if size > 2 * 1024 * 1024:
                raise WebSearchError("The page is too large to read.")
            chunks.append(chunk)
        raw = b"".join(chunks)
        charset = response.headers.get_content_charset() or "utf-8"
        try:
            markup = raw.decode(charset, errors="replace")
        except LookupError:
            markup = raw.decode("utf-8", errors="replace")
        return (extract_text(markup) if extract and kind != "text/plain" else markup), response.geturl()


def extract_text(markup: str) -> str:
    from lxml import html as lhtml
    from lxml.etree import LxmlError
    try:
        root = lhtml.fromstring(markup, parser=lhtml.HTMLParser(no_network=True, remove_comments=True))
    except (LxmlError, ValueError):
        raise WebSearchError("The page text could not be extracted.") from None
    # React streams public page HTML in hidden S:n segments before attaching it
    # to the page. Read that supplied markup without executing its scripts.
    for node in root.xpath("//*[@hidden and starts-with(@id, 'S:')]"):
        if re.fullmatch(r"S:\d+", node.get("id", "")):
            node.attrib.pop("hidden", None)
    # Accordion labels are content (often equipment names), not navigation.
    # Preserve their visible label and already-delivered panel text together.
    for node in root.xpath("//button[@aria-controls and @aria-expanded]"):
        panels = root.xpath("//*[@id=$panel_id]", panel_id=node.get("aria-controls"))
        if panels and panels[0].get("role") == "region":
            label = re.sub(r"\s+", " ", node.text_content()).strip()
            node.clear()
            node.tag = "h3"
            node.text = label
            panels[0].attrib.pop("hidden", None)
            panels[0].attrib.pop("aria-hidden", None)
    for node in root.xpath("//script|//style|//noscript|//iframe|//svg|//nav|//footer|"
                           "//aside|//form|//button|//*[@hidden]|//*[@aria-hidden='true']"):
        if node.getparent() is not None:
            node.drop_tree()
    main = root.xpath("//main|//*[@role='main']")
    articles = root.xpath("//article")
    character_pages = root.xpath("//*[contains(concat(' ',normalize-space(@class),' '),' character-page ')]")
    content = max(main + articles + character_pages or [root], key=lambda n: len(n.text_content()))
    # Some publishers style divs as section headings. Preserve that hierarchy
    # so a long character kit cannot crowd equipment out of the excerpt.
    for node in content.xpath(".//*[contains(concat(' ',normalize-space(@class),' '),' mobile-header ') or "
                              "contains(concat(' ',normalize-space(@class),' '),' content-header ')]"):
        tag = "h2" if "mobile-header" in node.get("class", "").split() else "h3"
        label = re.sub(r"\s+", " ", node.text_content()).strip()
        node.clear()
        node.tag, node.text = tag, label
    for block in content.xpath(".//*[contains(concat(' ',normalize-space(@class),' '),' build-tips ')]/*["
                               "contains(concat(' ',normalize-space(@class),' '),' single-item ')]"):
        labels = block.xpath(".//h3")
        advice = block.getnext()
        if (len(labels) != 1 or advice is None or "information" not in advice.get("class", "").split()):
            continue
        title = re.sub(r"\s+", " ", labels[0].text_content()).strip()
        paragraphs = advice.xpath("./p")
        recommendation = " ".join(p.text_content() for p in paragraphs
                                  if not re.match(r"Below you can find", p.text_content())) if paragraphs else advice.text_content()
        recommendation = re.sub(r"\s+", " ", recommendation).strip()
        if not (2 <= len(title) <= 120 and 12 <= len(recommendation) <= 1800):
            continue
        block.clear()
        label = lhtml.Element("h4")
        label.text = title
        passage = lhtml.Element("p")
        passage.text = "Guide recommendation: " + recommendation
        block.extend((label, passage))
        for paragraph in paragraphs:
            advice.remove(paragraph)
        if not paragraphs:
            advice.text = ""
        # Named main-Echo recommendations belong to their own equipment row.
        for item in advice.xpath(".//ul[contains(@class,'ww-echo-list')]/li"):
            names = item.xpath(".//*[contains(concat(' ',normalize-space(@class),' '),' ww-echo-name ')]")
            if not names:
                continue
            name = re.sub(r"\s+", " ", names[0].text_content()).strip()
            quote = re.sub(r"\s+", " ", item.text_content()).strip()
            if quote.startswith(name):
                quote = quote[len(name):].lstrip(" -–—")
            if not (2 <= len(name) <= 100 and 12 <= len(quote) <= 900):
                continue
            item.clear()
            heading = lhtml.Element("h4")
            heading.text = "Main Echo: " + name
            detail = lhtml.Element("p")
            detail.text = "Guide recommendation: " + quote
            item.extend((heading, detail))
    for box in content.xpath(".//*[contains(concat(' ',normalize-space(@class),' '),' main-stats ')]/*"):
        slots = box.xpath("./*[contains(concat(' ',normalize-space(@class),' '),' stats-inside ')]")
        stats = box.xpath("./*[contains(concat(' ',normalize-space(@class),' '),' list-stats ')]")
        if len(slots) == len(stats) == 1:
            label = re.sub(r"\s+", " ", slots[0].text_content()).strip()
            value = re.sub(r"\s+", " ", stats[0].text_content()).strip()
            if label and value:
                box.clear()
                box.tag, box.text = "p", label + ": " + value
    for team in content.xpath(".//*[contains(concat(' ',normalize-space(@class),' '),' team-showcase ')]"):
        for heading in team.xpath("./*[contains(concat(' ',normalize-space(@class),' '),' team-header ')]"):
            label = re.sub(r"\s+", " ", heading.text_content()).strip()
            heading.clear()
            heading.tag, heading.text = "h4", label
        for row in team.xpath("./*[contains(concat(' ',normalize-space(@class),' '),' team-row ')]"):
            names = row.xpath("./*[contains(concat(' ',normalize-space(@class),' '),' column ')]/*["
                              "contains(concat(' ',normalize-space(@class),' '),' big ')]//img/@alt")
            if 2 <= len(names) <= 6 and all(1 <= len(n) <= 70 for n in names):
                # Small portraits are substitutes for a slot, not extra members.
                row.clear()
                row.tag, row.text = "p", "Team lineup: " + " / ".join(names)
    for block in content.xpath(".//*[contains(concat(' ',normalize-space(@class),' '),' skill-priority ')]"):
        skills = block.xpath("./*[contains(concat(' ',normalize-space(@class),' '),' skill ')]")
        names = [re.sub(r"\s+", " ", s.text_content()).strip() for s in skills]
        if 2 <= len(names) <= 8 and all(1 <= len(n) <= 70 for n in names):
            previous = block.getprevious()
            mode = previous.text_content().strip() if previous is not None and previous.tag in ("h4", "h5", "h6") else ""
            block.clear()
            block.tag = "p"
            block.text = "Skill Priority: " + (mode + " — " if mode else "") + " > ".join(names)
    for node in content.xpath(".//img[@alt]"):
        label = node.get("alt", "").strip()[:120]
        if label:
            node.tail = " " + label + " " + (node.tail or "")
    # General guide tables do not need a publisher adapter. Keep each row's
    # cells together so the model can select a complete, source-backed row.
    for row in content.xpath(".//tr"):
        cells = row.xpath("./th|./td")
        if cells and not row.xpath(".//table"):
            values = [re.sub(r"\s+", " ", cell.text_content()).strip() for cell in cells]
            row.clear()
            row.tag, row.text = "p", " | ".join(values)
    # Keep the publisher's item name attached to its recommendation. Long
    # expandable tooltip descriptions must not push the advice out of the
    # excerpt. This uses page structure, never a hardcoded character/item list.
    for block in content.xpath(".//details[summary]"):
        summary = block.find("summary")
        titles = summary.xpath(".//*[contains(concat(' ', normalize-space(@class), ' '), ' details-block__title ')]")
        title = re.sub(r"\s+", " ", (titles[0] if titles else summary).text_content()).strip()
        advice = block.getnext()
        if (not 2 <= len(title) <= 120 or advice is None or
                not (advice.tag == "p" or "writer-description" in advice.get("class", ""))):
            continue
        recommendation = re.sub(r"\s+", " ", advice.text_content()).strip()
        if not 12 <= len(recommendation) <= 1800:
            continue
        summary.clear()
        summary.tag = "h3"
        summary.text = title
        advice.clear()
        advice.tag = "p"
        advice.text = "Guide recommendation: " + recommendation
        block.insert(1, advice)
    # Keep each displayed team together. Flattening all portraits loses the
    # boundary between teams and can create a fictitious lineup.
    for block in content.xpath(".//*[contains(concat(' ',normalize-space(@class),' '),' team-block ')]"):
        choices = block.xpath(".//*[contains(concat(' ',normalize-space(@class),' '),' team-portrait-option ')]")
        names = [re.sub(r"\s+", " ", x.text_content()).strip() for x in choices]
        if 2 <= len(names) <= 6 and all(1 <= len(n) <= 70 for n in names):
            block.clear()
            block.tag = "p"
            block.text = "Team lineup: " + " / ".join(names)
    for node in content.iter():
        if node.tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            node.text = "\n" + ("#" * int(node.tag[1])) + " " + (node.text or "")
            node.tail = "\n" + (node.tail or "")
        elif node.tag in ("p", "div", "section", "li", "tr", "br"):
            node.text = "\n" + (node.text or "")
            node.tail = "\n" + (node.tail or "")
        elif node.tag in ("td", "th"):
            node.tail = " | " + (node.tail or "")
    lines = [re.sub(r"\s+", " ", line).strip() for line in content.text_content().splitlines()]
    return "\n".join(line for line in lines if line)[:100000]


def relevant_excerpt(text: str, query: str, limit: int = 2800) -> str:
    if len(text) <= limit:
        return text
    stop = {"the", "and", "for", "with", "give", "can", "you", "please", "guide", "build", "best"}
    terms = set(re.findall(r"\w{3,}", query.lower())) - stop
    headings = list(re.finditer(r"(?m)^#{1,6} +([^\n]+)", text))
    if headings and BUILD_WORDS.search(query):
        def section_end(heading):
            level = len(heading[0]) - len(heading[0].lstrip("#"))
            return next((h.start() for h in headings if h.start() > heading.start()
                         and len(h[0]) - len(h[0].lstrip("#")) <= level), len(text))

        def section_excerpt(heading, budget):
            # Preserve parent headings such as 'Hyperbloom' above a generic
            # 'Weapons' subsection. Dropping these can mix incompatible builds.
            parents = []
            level = len(heading[0]) - len(heading[0].lstrip("#"))
            for earlier in headings:
                if earlier.start() >= heading.start():
                    break
                depth = len(earlier[0]) - len(earlier[0].lstrip("#"))
                while parents and parents[-1][0] >= depth:
                    parents.pop()
                parents.append((depth, earlier[0]))
            breadcrumb = " > ".join(label.lstrip("# ") for depth, label in parents
                                    if depth < level and label != headings[0][0])
            prefix = ("Section context: " + breadcrumb[:180] + "\n") if breadcrumb else ""
            end = section_end(heading)
            section = text[heading.start():end]
            # Named equipment records take precedence over the section's
            # generic preamble and lengthy tooltip stat blocks.
            records = list(re.finditer(r"(?m)^(#{2,6} [^\n]+)\nGuide recommendation: ([^\n]+)", section))
            if records:
                chosen = records[:2]
                budget_option = next((r for r in records[2:] if re.search(r"free[ -]to[ -]play|\bf2p\b|budget", r[2], re.I)), None)
                if budget_option:
                    chosen.append(budget_option)
                chunks = [heading[0]]
                for record in chosen:
                    chunk = record[0]
                    if len("\n".join(chunks)) + len(chunk) <= budget - len(prefix):
                        chunks.append(chunk)
                if len(chunks) > 1:
                    return prefix + "\n".join(chunks)
            part = section[:max(100, budget - len(prefix))]
            # End on the last complete line where possible, so stat tables and
            # weapon names aren't presented as if a truncated row was complete.
            if len(part) > 200 and not part.endswith("\n") and "\n" in part:
                part = part.rsplit("\n", 1)[0]
            return prefix + part

        title = next((h for h in headings if h[0].startswith("# ")), headings[0])
        intro_end = next((h.start() for h in headings if h.start() > title.start()), len(text))
        passages = [(title.start(), text[title.start():min(intro_end, title.start() + 350)])]
        # Actual section headings avoid selecting navigation, a table of
        # contents, or repeated uses of the character's name.
        for topic, excluded, budget in (
            (r"\b(weapons?|light cones?|w[ -]?engines?|arcs?)\b", r"$^", 1300 if limit >= 5000 else 700),
            (r"\b(artifacts?|relics?|equipment|echo(?:es|s)?|sonata|drive (?:discs?|disks?)|modules?|consoles?)\b", r"stats?|requirements", 1300 if limit >= 5000 else 400),
            (r"(?:main |sub)?stats?|stat priority|energy recharge requirements", r"base stats|^stats?$", 1000 if limit >= 5000 else 400),
            (r"\bteams?\b|teambuilding", r"$^", 600 if limit >= 5000 else 450),
            (r"talent priority|skill priority|core skills?|forte|rotation|level and talent", r"$^", 550 if limit >= 5000 else 350),
        ):
            candidates = [h for h in headings if re.search(topic, h[1], re.I)
                          and not re.search(excluded, h[1], re.I)]
            # Tab labels can use heading tags but contain no article text.
            # Prefer actual sections over those empty navigation headings.
            candidates = [h for h in candidates if re.search(r"\w", re.sub(
                r"(?m)^#{1,6}[^\n]*", "", text[h.end():section_end(h)]))]
            if candidates:
                examples = [h for h in candidates if re.search(r"example teams|team examples", h[1], re.I)]
                if examples:
                    candidates = examples
                priorities = [h for h in candidates if re.search(r"(?:talent|skill) priority", h[1], re.I)]
                if priorities:
                    candidates = priorities
                records = [h for h in candidates if "\nGuide recommendation:" in text[h.end():section_end(h)]]
                if records:
                    candidates = records
                heading = min(candidates, key=lambda h: (len(h[1]), h.start()))
                if all(heading.start() != start for start, _ in passages):
                    passages.append((heading.start(), section_excerpt(heading, budget)))
        if game_for(query) is GENERIC:
            # New games need not use any of our equipment vocabulary. Retain
            # useful ordinary sections/tables too, for source-checked model
            # selection, rather than dropping them because their names differ.
            covered = [(h.start(), section_end(h)) for h in headings
                       if any(h.start() == start for start, _ in passages[1:])]
            extra = [h for h in headings if h[0].startswith(("## ", "### "))
                     and not any(start <= h.start() < end for start, end in covered)
                     and not re.search(r"\b(navigation|contents|lore|story|comments|gallery|profile)\b", h[1], re.I)
                     and re.search(r"\w", re.sub(r"(?m)^#{1,6}[^\n]*", "", text[h.end():section_end(h)]))]
            extra.sort(key=lambda h: (not bool(re.search(r"best|recommend|equipment|gear|upgrade", h[1], re.I)),
                                      " | " not in text[h.end():section_end(h)], h.start()))
            for heading in extra[:4]:
                remaining = limit - sum(len(part) + 7 for _, part in passages)
                if remaining < 300:
                    break
                passages.append((heading.start(), section_excerpt(heading, min(1000, remaining))))
        if len(passages) > 1:
            return "\n[...]\n".join(part for _, part in sorted(passages))[:limit]
    # Overlapping passages preserve adjacent names and table explanations.
    chunks = [text[i:i + 550] for i in range(0, len(text), 470)]
    scores = [sum(min(chunk.lower().count(term), 2) for term in terms) for chunk in chunks]
    selected = {0}
    if BUILD_WORDS.search(query):
        for topic in (r"weapons?|light cones?|w[ -]?engines?|arcs?", r"artifacts?|relics?|equipment|echo(?:es|s)?|sonata|drive (?:discs?|disks?)|modules?|consoles?",
                      r"stats?|energy recharge|crit|mastery", r"teams?|rotation|talents?|skills?"):
            candidates = [i for i, chunk in enumerate(chunks) if re.search(topic, chunk, re.I)]
            if candidates and len(selected) < 4:
                selected.add(max(candidates, key=lambda i: scores[i] + len(re.findall(topic, chunks[i], re.I))))
    for i in sorted(range(len(chunks)), key=lambda n: scores[n], reverse=True):
        if len(selected) >= 4:
            break
        selected.add(i)
    return "\n[...]\n".join(chunks[i] for i in sorted(selected))[:limit]


def parse_search_results(markup: str, provider: str) -> list:
    from lxml import html as lhtml
    root = lhtml.fromstring(markup, parser=lhtml.HTMLParser(no_network=True))
    if provider == "brave":
        items = root.xpath("//div[@data-type='web']")
    elif provider == "bing":
        items = root.xpath("//li[contains(concat(' ',normalize-space(@class),' '),' b_algo ')]")
    else:
        items = root.xpath("//div[contains(concat(' ',normalize-space(@class),' '),' result ')]")
    results = []
    for item in items:
        if provider == "brave":
            anchors = item.xpath(".//a[.//div[contains(@class,'title')]]")
            title_parts = item.xpath(".//div[contains(@class,'title')]//text()")
            snippets = item.xpath(".//div[contains(@class,'snippet')]//div[contains(@class,'content')]//text()")
        elif provider == "bing":
            anchors = item.xpath(".//h2/a")
            title_parts = anchors[0].itertext() if anchors else []
            snippets = item.xpath(".//p//text()")
        else:
            anchors = item.xpath(".//a[contains(@class,'result__a')]")
            title_parts = anchors[0].itertext() if anchors else []
            snippets = item.xpath(".//*[contains(@class,'result__snippet')]//text()")
        if not anchors:
            continue
        url = anchors[0].get("href", "")
        if url.startswith("//"):
            url = "https:" + url
        p = urlsplit(url)
        if provider == "bing" and p.hostname in ("bing.com", "www.bing.com") and p.path == "/ck/a":
            encoded = parse_qs(p.query).get("u", [""])[0]
            try:
                if encoded.startswith("a1"):
                    encoded = encoded[2:]
                    url = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
            except (ValueError, UnicodeError):
                continue
        elif provider == "duckduckgo" and p.hostname in ("duckduckgo.com", "www.duckduckgo.com"):
            url = parse_qs(p.query).get("uddg", [url])[0]
        url = public_url(url)
        title = re.sub(r"\s+", " ", " ".join(title_parts)).strip()
        snippet = re.sub(r"\s+", " ", " ".join(snippets)).strip()
        if url and title:
            results.append({"href": url, "title": title, "body": snippet})
        if len(results) == 8:
            break
    return results


def _page_key(url: str) -> tuple:
    parsed = urlsplit(url)
    path = re.sub(r"^/[a-z]{2}(?:-[a-z]{2})?/", "/", parsed.path).rstrip("/")
    return (parsed.hostname, path, parsed.query)


def _engine_search(query: str, backend: str, status=lambda _: None, check=lambda: None,
                   *, exclude_urls=()) -> list:
    try:
        from lxml.etree import LxmlError
    except ImportError:
        raise WebSearchError("Web search needs its dependency. Close Raiden and run the updated Start-Raiden.bat.") from None
    providers = ["brave", "bing", "duckduckgo"] if backend == "auto" else [backend]
    endpoints = {"brave": "https://search.brave.com/search",
                 "bing": "https://www.bing.com/search",
                 "duckduckgo": "https://html.duckduckgo.com/html/"}
    saw_unrelated = False
    excluded = {_page_key(url) for url in exclude_urls}
    # Conversational lead-ins can make a provider search for "what" or "about".
    phrase = re.sub(r"^(?:what about|how about|and|also)\b[ ,:]*", "", query, flags=re.I).strip()
    for attempt, phrase in enumerate(dict.fromkeys((phrase, _focused_query(phrase)))):
        for provider in providers:
            check()
            if provider not in endpoints:
                continue
            status(f"{'Refining search' if attempt else 'Searching the web'} · {provider.title()}…")
            try:
                markup, _ = fetch_page(endpoints[provider] + "?" + urlencode({"q": phrase}),
                                       check, extract=False)
                rows = parse_search_results(markup, provider)
                relevant = [r for r in rows if relevant_result(r, query)
                            and _page_key(r.get("href") or r.get("url") or "") not in excluded]
                if relevant:
                    return relevant
                saw_unrelated |= bool(rows)
                if rows:
                    status("Results did not match the subject. Trying a more focused search…")
            except (WebSearchError, OSError, ValueError, LxmlError, HTTPError, URLError):
                continue
    # Do not return access challenges as source evidence or try to bypass them.
    reason = "Search results did not match the requested subject. " if saw_unrelated else "Search providers returned no usable results or blocked the lookup. "
    raise WebSearchError(reason + "No search result was treated as verified guide evidence.")


def guide_entry(query: str) -> dict | None:
    """Known page addresses, never invented URLs or a search-engine snippet."""
    try:
        entries = json.loads((Path(__file__).parent / "assets" / "guides.json").read_text(encoding="utf-8"))
        for entry in entries:
            if (game_for(query).key == entry.get("game", "genshin")
                    and any(subject_terms(query)[:len(name.split())] == name.casefold().split()
                            for name in entry["aliases"])
                    and (BUILD_WORDS.search(query) or re.search(r"\bguide\b", query, re.I))
                    and not re.search(r"\b(boss|quest|puzzle)\b", query, re.I)):
                return entry
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None


def clear_legacy_guide_cache(profile_dir: Path | None = None) -> list[str]:
    """Remove only this app's old guide cache; never read it or touch settings.

    Guide retrieval no longer writes any page contents to disk. Return failures
    so the UI can tell the user if a locked legacy file could not be removed.
    """
    if profile_dir is None:
        base = os.environ.get("APPDATA") or str(Path.home() / ".config")
        profile_dir = Path(base) / "RaidenCompanion"
    paths = [profile_dir / "guide-cache.json"]
    failures = []
    try:
        paths.extend(profile_dir.glob("guide-cache-*.tmp"))
    except OSError:
        failures.append(str(profile_dir))
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            failures.append(str(path))
    return failures


def _read_source(source: dict, query: str, check, *, discover_links: bool = False) -> dict:
    check()
    if urlsplit(source["url"]).hostname in ("www.youtube.com", "youtube.com", "youtu.be"):
        return source
    try:
        if discover_links:
            markup, final_url = fetch_page(source["url"], check, extract=False)
            text = extract_text(markup)
        else:
            text, final_url = fetch_page(source["url"], check)
        # A title/URL alone cannot make a login wall into guide evidence.
        terms = subject_terms(query)
        body_words = set(re.findall(r"[^\W_]+", text[:5000].casefold()))
        body_matches = not terms or sum(t in body_words for t in terms) >= min(2, len(terms))
        if (len(text.strip()) >= 200 and body_matches and
                relevant_result({"title": source["title"], "url": final_url, "excerpt": text}, query)):
            source.update(excerpt=relevant_excerpt(text, query, 6000 if BUILD_WORDS.search(query) else 2800),
                          coverage="Page excerpts", final_url=final_url)
            if discover_links:
                source["related_links"] = _matching_guide_links(_guide_links(markup, final_url, game_for(query)), query)
    except (WebSearchError, OSError, ValueError, HTTPError, URLError):
        pass
    return source


def _guide_links(markup: str, base_url: str, game) -> list:
    """Read public links on this publisher and within the requested game."""
    from lxml import html as lhtml
    from lxml.etree import LxmlError
    try:
        root = lhtml.fromstring(markup, parser=lhtml.HTMLParser(no_network=True, remove_comments=True))
    except (LxmlError, ValueError):
        return []
    host = urlsplit(base_url).hostname
    links, seen = [], set()
    for anchor in root.xpath("//a[@href]"):
        url = public_url(urljoin(base_url, anchor.get("href", "")))
        if not url:
            continue
        parsed = urlsplit(url)
        url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
        if parsed.hostname != host or url in seen or parsed.query:
            continue
        if re.search(r"/(?:news|forums|i|images|wp-content|cdn-cgi)/|\.(?:png|jpe?g|webp|pdf)$", parsed.path, re.I):
            continue
        label = re.sub(r"\s+", " ", anchor.text_content()).strip()
        if not label:
            label = " ".join(anchor.xpath(".//img/@alt"))
        if source_games(label, url, "") != {game.key}:
            continue
        if not (re.search(r"guide|build|profile|/characters/", parsed.path, re.I)
                or host in ("keqingmains.com", "hsr.keqingmains.com")):
            continue
        seen.add(url)
        links.append({"title": (label or unquote(parsed.path).replace("-", " "))[:180], "url": url})
        if len(links) == 700:
            break
    return links


def _guide_link_rank(link: dict) -> int:
    path = urlsplit(link["url"]).path
    if re.search(r"guide-best-builds|quickguide|/builds/", path):
        return 0
    if "team" in path:
        return 1
    return 3 if "profile" in path else 2


def _matching_guide_links(links: list, query: str) -> list:
    terms = subject_terms(query)
    if not terms:
        return []
    matched, seen = [], set()
    for link in links:
        if link["url"] in seen or not relevant_result(link, query):
            continue
        # A character's name in an unrelated article's description is not a
        # character guide. Directory links must name the subject themselves.
        name = unquote(urlsplit(link["url"]).path).replace("-", " ") + " " + link["title"]
        if re.search(r"\b" + re.escape(terms[0]) + r"\b", name, re.I):
            seen.add(link["url"])
            matched.append(dict(link))
    return sorted(matched, key=_guide_link_rank)


def _directory_links(url: str, game, check) -> list:
    cached = _DIRECTORY_CACHE.get(url)
    if cached and time.monotonic() - cached[0] < 900:
        check()
        return cached[1]
    try:
        markup, final_url = fetch_page(url, check, extract=False)
        if urlsplit(final_url).hostname != urlsplit(url).hostname:
            return []
        links = _guide_links(markup, final_url, game)
        if links:
            _DIRECTORY_CACHE[url] = (time.monotonic(), links)
        return links
    except (WebSearchError, OSError, ValueError, HTTPError, URLError):
        return []


def _directory_sources(query: str, fetched_at: str, status, check) -> list:
    game = game_for(query)
    directories = GUIDE_DIRECTORIES.get(game.key, ())
    if not directories or not BUILD_WORDS.search(query) or not subject_terms(query):
        return []
    status("Finding the character in guide directories…")
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="RaidenDirectory") as pool:
        batches = list(pool.map(lambda url: _directory_links(url, game, check), directories))
    candidates = _matching_guide_links([link for batch in batches for link in batch], query)
    if not candidates:
        return []
    status("Reading the character's build and team guides…")
    def read(link):
        source = dict(link, id=0, snippet="", excerpt="", coverage="Unread link", fetched_at=fetched_at)
        return _read_source(source, query, check, discover_links=True)
    # Follow at most two directory matches, then their published build/team
    # tabs. This handles directories that initially link to a profile page.
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="RaidenDiscovered") as pool:
        sources = list(pool.map(read, candidates[:2]))
        seen = {s["url"] for s in sources}
        related = []
        for source in sources:
            for link in source.pop("related_links", []):
                if link["url"] not in seen:
                    seen.add(link["url"])
                    related.append(link)
        if related:
            sources.extend(pool.map(read, sorted(related, key=_guide_link_rank)[:3 - len(sources)]))
    for source in sources:
        source.pop("related_links", None)
    return [dict(s, id=i + 1) for i, s in enumerate(sorted(
        (s for s in sources if s.get("excerpt")), key=_guide_link_rank))]


def _direct_sources(query: str, fetched_at: str, status, check) -> list:
    entry = guide_entry(query)
    if not entry:
        return []
    status("Reading character guides directly…")
    sources = [{"id": i + 1, "title": page["title"], "url": page["url"],
                "snippet": "", "excerpt": "", "coverage": "Unread link", "fetched_at": fetched_at}
               for i, page in enumerate(entry["pages"][:3]) if public_url(page["url"])]
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="RaidenDirect") as pool:
        read = list(pool.map(lambda s: _read_source(s, query, check), sources))
    # Catalog addresses aren't search evidence. Keep only pages actually read.
    return [dict(s, id=i + 1) for i, s in enumerate(s for s in read if s["excerpt"])]


def lookup(query: str, backend: str = "auto", status=lambda _: None, check=lambda: None) -> dict:
    check()
    query = search_query(query)
    user_check = check
    deadline = time.monotonic() + 40
    def check():
        user_check()
        if time.monotonic() > deadline:
            raise WebSearchError("Live lookup took too long. No fresh guide could be verified.")
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    direct = _direct_sources(query, fetched_at, status, check)
    if not direct:
        direct = _directory_sources(query, fetched_at, status, check)
    if direct:
        result = {"sources": direct, "notice": f"Read {len(direct)} guide page(s) directly · {fetched_at}. Guide publication dates may differ.",
                  "search_query": query, "search_url": search_url(query), "fetched_at": fetched_at,
                  "evidence_mode": "live", "evidence_label": "Live guide pages"}
        check()
        return result
    status("Searching the web…")
    rows = _engine_search(query, backend, status, check)
    check()
    sources, seen = [], set()
    for row in rows:
        if not relevant_result(row, query):
            continue
        url = public_url(row.get("href") or row.get("url"))
        if not url:
            continue
        canonical = _page_key(url)
        if canonical in seen:
            continue
        seen.add(canonical)
        title = html.unescape(str(row.get("title") or url))[:180]
        snippet = html.unescape(str(row.get("body") or row.get("description") or ""))[:450]
        sources.append({"id": len(sources) + 1, "title": title, "url": url,
                        "snippet": snippet, "excerpt": "", "coverage": "Search snippet",
                        "fetched_at": fetched_at})
        if len(sources) == 5:
            break
    if not sources:
        raise WebSearchError("The search returned no usable public links. Add the game and character or quest name, then try again.")

    status("Reading guide pages…")
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="RaidenWeb") as pool:
        list(pool.map(lambda s: _read_source(s, query, check), sources[:3]))
        # If top results block page access, try the remaining links instead of
        # stopping with snippets while a readable guide is available below.
        missing = 3 - sum(bool(s["excerpt"]) for s in sources)
        if missing:
            list(pool.map(lambda s: _read_source(s, query, check), sources[3:3 + missing]))
    check()
    read_count = sum(bool(s["excerpt"]) for s in sources)
    if not read_count:
        # A matching search title is not a readable guide. Continue with other
        # public results inside the same deadline; never bypass blocked pages.
        status("Looking for another readable guide…")
        try:
            alternatives = _engine_search(query, backend, status, check,
                                           exclude_urls=[s["url"] for s in sources])
            fresh = []
            for row in alternatives:
                url = public_url(row.get("href") or row.get("url"))
                if not url or _page_key(url) in seen or not relevant_result(row, query):
                    continue
                seen.add(_page_key(url))
                fresh.append({"id": len(sources) + len(fresh) + 1,
                              "title": html.unescape(str(row.get("title") or url))[:180],
                              "url": url, "snippet": html.unescape(str(row.get("body") or row.get("description") or ""))[:450],
                              "excerpt": "", "coverage": "Search snippet", "fetched_at": fetched_at})
                if len(fresh) == 3:
                    break
            with ThreadPoolExecutor(max_workers=3, thread_name_prefix="RaidenAlternate") as pool:
                list(pool.map(lambda s: _read_source(s, query, check), fresh))
            sources.extend(fresh)
        except WebSearchError:
            user_check()
        read_count = sum(bool(s["excerpt"]) for s in sources)
    user_check()
    notice = (f"Web lookup · {read_count} page(s) read · {len(sources)} source links · {fetched_at}. "
              "Search snippets are labelled; videos were not watched.")
    if not read_count:
        notice += " Page text was unavailable; this answer can use search snippets only."
    result = {"sources": sources, "notice": notice, "search_query": query,
              "search_url": search_url(query), "fetched_at": fetched_at,
              "evidence_mode": "live", "evidence_label": "Live web lookup"}
    return result


def evidence_text(sources: list) -> str:
    records = []
    for source in sources:
        records.append(
            f"[{source['id']}] {source['title']}\nURL: {source['url']}\n"
            f"Evidence type: {source['coverage']}; retrieved {source['fetched_at']}\n"
            f"Search snippet: {source['snippet']}\n"
            f"Page excerpts: {source['excerpt'] or '(page not read)'}")
    return "\n\n".join(records)[:12500]
