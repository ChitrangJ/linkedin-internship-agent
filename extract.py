"""Pull key facts out of LinkedIn job text: how long ago it was posted, the
start date, and mandatory requirements (languages, degree, experience, work
permit, enrolment). Text-based and multilingual (EN / FR / DE / NL), so it is
best-effort: it highlights what the description says, it doesn't guarantee it.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------
# Posting age ("5 minutes ago" -> 5)
# --------------------------------------------------------------------------
_AGO_UNITS = {
    "second": 1 / 60, "minute": 1, "min": 1, "hour": 60, "hr": 60,
    "day": 1440, "week": 10080, "month": 43200,
}


def minutes_ago(text: str) -> float:
    """'5 minutes ago' -> 5, '2 hours ago' -> 120, 'Just now' -> 0. Unknown -> large."""
    t = (text or "").lower()
    if "just now" in t or "moments ago" in t:
        return 0
    m = re.search(r"(\d+)\s*(second|minute|min|hour|hr|day|week|month)", t)
    if not m:
        return 10 ** 6
    return int(m.group(1)) * _AGO_UNITS[m.group(2)]


# --------------------------------------------------------------------------
# Start date
# --------------------------------------------------------------------------
_MONTHS = {
    "january": ["january", "jan", "janvier", "janv", "januar", "jänner", "januari"],
    "february": ["february", "feb", "février", "fevrier", "févr", "februar", "februari"],
    "march": ["march", "mar", "mars", "märz", "maerz", "maart"],
    "april": ["april", "apr", "avril", "avr"],
    "may": ["mai", "mei"],  # English "may" handled separately (it's also a verb)
    "june": ["june", "jun", "juin", "juni"],
    "july": ["july", "jul", "juillet", "juil", "juli"],
    "august": ["august", "aug", "août", "aout", "augustus"],
    "september": ["september", "sep", "sept", "septembre"],
    "october": ["october", "oct", "octobre", "oktober", "okt"],
    "november": ["november", "nov", "novembre"],
    "december": ["december", "dec", "décembre", "decembre", "dezember", "dez"],
}
_MONTH_LOOKUP = {alias: en for en, aliases in _MONTHS.items() for alias in aliases}
_MONTH_RE = "|".join(sorted((re.escape(a) for a in _MONTH_LOOKUP), key=len, reverse=True))

# "1 January 2027", "1er janvier", "January 2027", "Jan. 2027", "01.01.2027", "1/1/27"
_DATE_RE = (
    r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?may\b(?:\s*,?\s*20\d\d)?|\bmay\s*,?\s*20\d\d\b|"
    rf"(?:(?:\d{{1,2}})(?:st|nd|rd|th|er|e|\.)?\s*(?:of\s+)?)?\b(?:{_MONTH_RE})\b\.?(?:\s*,?\s*(?:20\d\d))?"
    r"|\b\d{1,2}[./-]\d{1,2}[./-](?:20)?\d\d\b"
    r"|\b(?:1er|2e|2ème|premier|second|first|second|1st|2nd)\s+(?:semestre|semester|half)(?:\s+(?:of\s+)?20\d\d)?"
    r"|\b(?:spring|summer|autumn|fall|winter|printemps|été|automne|hiver|frühjahr|sommer|herbst)\s+20\d\d"
)
_START_KW = (
    r"start(?:ing)?\s*date|date\s+de\s+(?:début|démarrage|prise\s+de\s+poste)|starting\s+(?:in|on|from|date)?|"
    r"start(?:s|ing)?\s+(?:in|on|from|as\s+of|by|around|early|mid|end)|\bstart\s*:|to\s+start|available\s+(?:from|starting|as\s+of)|"
    r"availability|début|démarrage|dès|à\s+partir\s+d[eu']|à\s+pourvoir|prise\s+de\s+poste|"
    r"date\s+d'entrée|beginn|eintritt(?:sdatum)?|startdatum|starttermin|einstieg|zum|ab\s+(?:dem|sofort)?|"
    r"vanaf|begindatum|per\b|from\b|commencing|beginning|as\s+of|for\s+a\s+start|\bstart\b"
)
_NOT_MONTH = r"(?!\s+(?:food|wrigley|petcare|pet\s*care|inc|incorporated|chocolat|group|groupe)\b)"
_START_RE = re.compile(rf"(?:{_START_KW})[^.;!?\n]{{0,40}}?({_DATE_RE}){_NOT_MONTH}", re.I)
_TITLE_DATE_RE = re.compile(
    rf"(?:start(?:ing)?|from|dès|ab|à\s+partir\s+de|début)?\s*({_DATE_RE}){_NOT_MONTH}", re.I
)
_ASAP_RE = re.compile(
    r"\basap\b|as\s+soon\s+as\s+possible|immediate(?:ly)?\s+start|start\s+immediately|"
    r"dès\s+que\s+possible|dès\s+maintenant|au\s+plus\s+vite|ab\s+sofort|zum\s+nächstmöglichen|"
    r"nächstmöglich|per\s+direct|zo\s+snel\s+mogelijk",
    re.I,
)


_EN_MONTHS = list(_MONTHS)


def _normalize_date(s: str) -> str:
    s = " ".join(s.split())
    num = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-]((?:20)?\d\d)", s)
    if num:  # European day-first: 01.01.2027 -> 1 January 2027
        d, mo, y = int(num.group(1)), int(num.group(2)), num.group(3)
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{d} {_EN_MONTHS[mo - 1].capitalize()} {y if len(y) == 4 else '20' + y}"
        return s
    def month(m):
        key = m.group(0).lower().rstrip(".")
        return _MONTH_LOOKUP.get(key, key).capitalize()
    sem = re.search(r"(1er|premier|first|1st|2e|2ème|second|2nd)\s+(?:semestre|semester|half)(?:\s+(?:of\s+)?(20\d\d))?", s, re.I)
    if sem:
        first = sem.group(1).lower() in ("1er", "premier", "first", "1st")
        half = "H1 (Jan-Jun)" if first else "H2 (Jul-Dec)"
        return f"{half} {sem.group(2) or ''}".strip()
    s = re.sub(rf"\b(?:{_MONTH_RE})\b\.?", month, s, flags=re.I)
    s = re.sub(r"\bmay\b", "May", s, flags=re.I)
    s = re.sub(r"\b(\d{1,2})(?:st|nd|rd|th|er|e|\.)\s*(?:of\s+)?(?=[A-Za-z])", r"\1 ", s)
    return s.strip(" ,.")


def start_date(title: str, description: str) -> str:
    """Best guess of the start date, e.g. '1 January 2027', 'January 2027', 'ASAP'. '' if none."""
    found = ""
    m = _START_RE.search(title) or (
        _TITLE_DATE_RE.search(title) if re.search(rf"\b(?:{_MONTH_RE}|may)\b\.?\s*20\d\d|semestre|semester|\b(?:spring|summer|autumn|fall|winter|printemps|été|automne|hiver)\s+20\d\d", title, re.I) else None
    )
    if not m:
        m = _START_RE.search(description)
    if m:
        found = _normalize_date(m.group(1))
    if _ASAP_RE.search(title + " " + description):
        found = f"{found} / ASAP" if found else "ASAP"
    return found


# --------------------------------------------------------------------------
# Requirements
# --------------------------------------------------------------------------
_LANGS = {
    "English": r"english|anglais|englisch|engels|inglés",
    "French": r"french|français|francais|französisch|franzosisch|frans",
    "German": r"german|allemand|deutsch|duits",
    "Dutch": r"dutch|néerlandais|neerlandais|niederländisch|nederlands",
    "Luxembourgish": r"luxembourgish|luxembourgeois|luxemburgisch|lëtzebuergesch",
    "Italian": r"italian|italien|italienisch|italiaans",
    "Spanish": r"spanish|espagnol|spanisch|spaans",
}
_LEVEL_WORDS = [
    (r"\b(c2)\b", "C2"), (r"\b(c1)\b", "C1"), (r"\b(b2)\b", "B2"), (r"\b(b1)\b", "B1"),
    (r"native|mother\s+tongue|langue\s+maternelle|muttersprach|moedertaal", "native"),
    (r"bilingu|zweisprachig|tweetalig", "bilingual"),
    (r"fluen|fluent|courant|fließend|fliessend|vloeiend|verhandlungssicher|perfect|parfait|excellent|"
     r"maîtrise|business[- ]level|professional\s+working|proficien|sehr\s+gut|exzellent|uitstekend", "fluent"),
    (r"good|bon(?:ne)?\s|gut|goed|working\s+knowledge|conversational", "good"),
    (r"basic|notions|grundkenntnis|basiskennis", "basic"),
]
_LEVEL_RANK = {"C2": 7, "native": 6, "bilingual": 6, "C1": 5, "fluent": 4, "B2": 3, "good": 2, "B1": 1, "basic": 0}
_OPTIONAL_RE = re.compile(
    r"\bplus\b|nice\s+to\s+have|is\s+an?\s+(?:advantage|asset|bonus)|would\s+be|preferred|"
    r"un\s+plus|apprécié|souhaité|serait\s+un|von\s+vorteil|wünschenswert|wäre|pré|bonus|pluspunt|is\s+een\s+pré",
    re.I,
)


def _clauses(text: str) -> list[str]:
    return [c.strip() for c in re.split(r"(?<=[.;!?•·])\s+|\s[-–•·]\s|\n", text) if c.strip()]


def language_requirements(description: str) -> list[str]:
    best: dict[str, tuple[str, bool]] = {}
    for clause in _clauses(description):
        low = clause.lower()
        if len(low) > 400:
            continue
        for lang, lrx in _LANGS.items():
            for lm in re.finditer(rf"\b(?:{lrx})\w*", low):
                window = low[max(0, lm.start() - 60): lm.end() + 60]
                level = None
                for rx, name in _LEVEL_WORDS:
                    if re.search(rx, window):
                        level = name
                        break
                optional = bool(_OPTIONAL_RE.search(window))
                if not level:
                    if not optional:
                        continue
                    level = "basic"
                prev = best.get(lang)
                if (not prev or (prev[1] and not optional)
                        or (prev[1] == optional and _LEVEL_RANK[level] > _LEVEL_RANK[prev[0]])):
                    best[lang] = (level, optional)
    out = []
    for lang, (level, optional) in best.items():
        if optional:
            out.append(f"{lang} (nice to have)" if level == "basic" else f"{lang} {level} (nice to have)")
        else:
            out.append(f"{lang} {level}")
    return out


_DEGREE_PATTERNS = [
    (r"bac\s*\+\s*5|bac5\b", "Bac+5"),
    (r"bac\s*\+\s*4|bac4\b", "Bac+4"),
    (r"bac\s*\+\s*3|bac3\b", "Bac+3"),
    (r"\b(?:master(?:'s)?|msc|m\.sc|m2|m1|mba|grande\s+école|école\s+de\s+commerce|business\s+school|"
     r"école\s+d'ingénieur|masterstudium|masterstudiengang|masteropleiding)\b", "Master's / business school"),
    (r"\b(?:bachelor(?:'s)?|bsc|b\.sc|licence|bachelorstudium|hbo|wo-?\s?opleiding)\b", "Bachelor's"),
]
_ENROL_RE = re.compile(
    r"enrol+ed|currently\s+(?:studying|a\s+student|pursuing)|immatrikul|eingeschrieben|"
    r"inscrit(?:e)?\s+(?:dans|en)|ingeschreven|student\s+status|statut\s+étudiant",
    re.I,
)
_CONVENTION_RE = re.compile(r"convention\s+de\s+stage|internship\s+agreement|stage\s*overeenkomst|stageovereenkomst", re.I)
_PFLICHT_RE = re.compile(r"pflichtpraktikum|curricular\s+internship|mandatory\s+internship|stage\s+obligatoire|"
                         r"verplichte\s+stage|als\s+teil\s+(?:deines|ihres)\s+studiums", re.I)
_PERMIT_RE = re.compile(
    r"work\s+permit|right\s+to\s+work|work\s+authori[sz]ation|eligible\s+to\s+work|visa\s+sponsorship|"
    r"(?:no|not|cannot|unable\s+to)\s+(?:provide\s+)?(?:visa|sponsor)|eu\s+(?:citizen|passport|nationality)|"
    r"arbeitserlaubnis|aufenthaltstitel|arbeitsgenehmigung|permis\s+de\s+travail|autorisation\s+de\s+travail|"
    r"werkvergunning|tewerkstellingsvergunning",
    re.I,
)
_MAX_WORDS = re.compile(r"(?:max(?:imum|\.)?|up\s+to|less\s+than|under|no\s+more\s+than|moins\s+de|jusqu'à|"
                        r"höchstens|maximal|bis\s+zu|weniger\s+als|maximaal|minder\s+dan)\s*(?:of\s+)?$", re.I)


def _snippet(text: str, m: re.Match, width: int = 70) -> str:
    start = max(0, m.start() - width // 2)
    end = min(len(text), m.end() + width // 2)
    s = text[start:end]
    s = re.split(r"(?<=[.;!?])\s", s)
    # keep the piece containing the match
    pos, acc = m.start() - start, 0
    for piece in s:
        if acc <= pos <= acc + len(piece) + 1:
            return ("…" if start > 0 else "") + piece.strip(" ;,") + "…"
        acc += len(piece) + 1
    return text[start:end].strip()


def experience_note(description: str, years_re: re.Pattern, exp_words: re.Pattern) -> str:
    """Short note like '2-3 yrs', 'min 2 yrs', 'max 4 yrs', or ''."""
    text = description.lower()
    text = re.sub(r"bac\s*\+\s*\d", " ", text)
    notes = []
    for m in years_re.finditer(text):
        lo, hi = int(m.group(1)), m.group(2)
        if lo > 25:
            continue
        before = re.split(r"[.;!?\n•]", text[max(0, m.start() - 60): m.start()])[-1]
        after = re.split(r"[.;!?\n•]", text[m.end(): m.end() + 70])[0]
        if not (exp_words.search(after) or exp_words.search(before[-40:])):
            continue
        if _MAX_WORDS.search(before):
            notes.append(f"max {lo} yrs exp")
        elif hi:
            notes.append(f"{lo}-{hi} yrs exp")
        elif "+" in m.group(0) or re.search(r"(at least|minimum|min\.|mindestens|au moins|minimaal|minstens)\s*$", before):
            notes.append(f"min {lo} yrs exp")
        else:
            notes.append(f"{lo} yrs exp")
    return notes[0] if notes else ""


def requirements(description: str, years_re: re.Pattern, exp_words: re.Pattern) -> list[str]:
    reqs: list[str] = []
    reqs += language_requirements(description)
    low = description.lower()
    degrees = []
    for rx, name in _DEGREE_PATTERNS:
        if re.search(rx, low) and name not in degrees:
            degrees.append(name)
            if name.startswith("Bac+"):
                break  # the Bac+N level is the most specific; skip generic words
    if degrees:
        reqs.append("Degree: " + " / ".join(degrees[:2]))
    exp = experience_note(description, years_re, exp_words)
    if exp:
        reqs.append(exp)
    if _PFLICHT_RE.search(description):
        reqs.append("Mandatory (curricular) internship only")
    elif _CONVENTION_RE.search(description):
        reqs.append("Convention de stage required")
    elif _ENROL_RE.search(description):
        reqs.append("Must be an enrolled student")
    pm = _PERMIT_RE.search(description)
    if pm:
        reqs.append("Work permit: " + _snippet(description, pm))
    return reqs
