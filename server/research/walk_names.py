"""Street-name keys, display and fuzzy matching for the walk list, plus the shared vote-history window.

Pure Python (no Django) so the builder in tools/ and the views share one normaliser: voter-file parts, TIGER FULLNAME
strings and typed CSV text for the same street reduce to the same key. Accents and punctuation are dropped, every token
gets its USPS suffix / directional abbreviation, ordinals become numeric (FIRST -> 1ST), SAINT -> ST, MOUNT -> MT.
Directionals stay in the key: N and S Academy Blvd are different streets (house numbers restart).
"""
import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache

# ---- vote-history window (one config for the builder, its QA gate and the views) ----------------------------------
PRIMARIES = {'2014p': '2014-06-24', '2016p': '2016-06-28', '2018p': '2018-06-26',
             '2020p': '2020-06-30', '2022p': '2022-06-28', '2024p': '2024-06-25'}
GENERALS = {'2014g': '2014-11-04', '2016g': '2016-11-08', '2018g': '2018-11-06',
            '2020g': '2020-11-03', '2022g': '2022-11-08', '2024g': '2024-11-05'}
VH_FIELD = 'svi_vh_{}'                       # VH_FIELD.format('2020g'); blank = did not vote, else a 2-char code
ABS_PREFIXES = ('M', 'A')                    # vote codes counted as mail ballots
ABS_RULE = 'Abs = mail ballots'
ABS_GATE = {'elections': ('2020g', '2022g', '2024g'), 'min_share': 0.85}  # M*/A* share of recorded votes, each one
WINDOW = {'primaries': list(PRIMARIES), 'generals': list(GENERALS), 'abs_rule': ABS_RULE}

# ---- normaliser tables ------------------------------------------------------------------------------------------
# USPS Publication 28 C1: standard abbreviation first, then the spellings mapped to it. Plural forms USPS folds into
# a singular (PIKES, PARKS, TRAILS ...) are left alone so names like PIKES PEAK keep their word.
_USPS = """ALY ALLEE ALLEY ALLY|ANX ANEX ANNEX ANNX|ARC ARCADE|AVE AV AVEN AVENU AVENUE AVN AVNUE|BYU BAYOO BAYOU|
BCH BEACH|BND BEND|BLF BLUF BLUFF|BLFS BLUFFS|BTM BOT BOTTM BOTTOM|BLVD BOUL BOULEVARD BOULV|BR BRNCH BRANCH|
BRG BRDGE BRIDGE|BRK BROOK|BRKS BROOKS|BG BURG|BGS BURGS|BYP BYPA BYPAS BYPASS BYPS|CP CAMP CMP|CYN CANYN CANYON CNYN|
CPE CAPE|CSWY CAUSEWAY CAUSWA|CTR CEN CENT CENTER CENTR CENTRE CNTER CNTR|CTRS CENTERS|
CIR CIRC CIRCL CIRCLE CRCL CRCLE|CIRS CIRCLES|CLF CLIFF|CLFS CLIFFS|CLB CLUB|CMN COMMON|CMNS COMMONS|COR CORNER|
CORS CORNERS|CRSE COURSE|CT COURT CRT|CTS COURTS|CV COVE|CVS COVES|CRK CREEK|CRES CRESCENT CRSENT CRSNT|CRST CREST|
XING CROSSING CRSSNG|XRD CROSSROAD|XRDS CROSSROADS|CURV CURVE|DL DALE|DM DAM|DV DIV DIVIDE DVD|DR DRIV DRIVE DRV|
DRS DRIVES|EST ESTATE|ESTS ESTATES|EXPY EXP EXPR EXPRESS EXPRESSWAY EXPW|EXT EXTENSION EXTN EXTNSN|EXTS EXTENSIONS|
FALL|FLS FALLS|FRY FERRY FRRY|FLD FIELD|FLDS FIELDS|FLT FLAT|FLTS FLATS|FRD FORD|FRDS FORDS|FRST FOREST|
FRG FORG FORGE|FRGS FORGES|FRK FORK|FRKS FORKS|FT FORT FRT|FWY FREEWAY FREEWY FRWAY FRWY|GDN GARDEN GARDN GRDEN GRDN|
GDNS GARDENS GRDNS|GTWY GATEWAY GATEWY GATWAY GTWAY|GLN GLEN|GLNS GLENS|GRN GREEN|GRNS GREENS|GRV GROV GROVE|
GRVS GROVES|HBR HARB HARBOR HARBR HRBOR|HBRS HARBORS|HVN HAVEN|HTS HT HEIGHTS|HWY HIGHWAY HIGHWY HIWAY HIWY HWAY|
HL HILL|HLS HILLS|HOLW HLLW HOLLOW|INLT INLET|IS ISLAND ISLND|ISS ISLANDS ISLNDS|ISLE|
JCT JCTION JCTN JUNCTION JUNCTN JUNCTON|JCTS JCTNS JUNCTIONS|KY KEY|KYS KEYS|KNL KNOL KNOLL|KNLS KNOLLS|LK LAKE|
LKS LAKES|LAND|LNDG LANDING LNDNG|LN LANE|LGT LIGHT|LGTS LIGHTS|LF LOAF|LCK LOCK|LCKS LOCKS|LDG LDGE LODG LODGE|LOOP|
MALL|MNR MANOR|MNRS MANORS|MDW MEADOW|MDWS MEADOWS MEDOWS|MEWS|ML MILL|MLS MILLS|MSN MISSN MSSN MISSION|
MTWY MOTORWAY|MT MNT MOUNT|MTN MNTAIN MNTN MOUNTAIN MOUNTIN MTIN|MTNS MNTNS MOUNTAINS|NCK NECK|
ORCH ORCHARD ORCHRD|OVAL OVL|OPAS OVERPASS|PARK PRK|PKWY PARKWAY PARKWY PKWAY PKY|PASS|PSGE PASSAGE|PATH|PIKE|
PNE PINE|PNES PINES|PL PLACE|PLN PLAIN|PLNS PLAINS|PLZ PLAZA PLZA|PT POINT|PTS POINTS|PRT PORT|PRTS PORTS|
PR PRAIRIE PRR|RADL RAD RADIAL RADIEL|RAMP|RNCH RANCH RNCHS|RPD RAPID|RPDS RAPIDS|RST REST|RDG RDGE RIDGE|
RDGS RIDGES|RIV RIVER RVR RIVR|RD ROAD|RDS ROADS|RTE ROUTE|ROW|RUE|RUN|SHL SHOAL|SHLS SHOALS|SHR SHOAR SHORE|
SHRS SHOARS SHORES|SKWY SKYWAY|SPG SPNG SPRING SPRNG|SPGS SPNGS SPRINGS SPRNGS|SPUR|SQ SQR SQRE SQU SQUARE|
SQS SQRS SQUARES|STA STATION STATN STN|STRA STRAV STRAVEN STRAVENUE STRAVN STRVN STRVNUE|STRM STREAM STREME|
ST STREET STRT STR|STS STREETS|SMT SUMIT SUMITT SUMMIT|TER TERR TERRACE|TRWY THROUGHWAY|TRCE TRACE|TRAK TRACK TRK|
TRFY TRAFFICWAY|TRL TRAIL|TRLR TRAILER|TUNL TUNEL TUNNEL TUNNL|TPKE TRNPK TURNPIKE TURNPK|UPAS UNDERPASS|UN UNION|
UNS UNIONS|VLY VALLEY VALLY VLLY|VLYS VALLEYS|VIA VDCT VIADCT VIADUCT|VW VIEW|VWS VIEWS|
VLG VILL VILLAG VILLAGE VILLG VILLIAGE|VLGS VILLAGES|VL VILLE|VIS VIST VISTA VST VSTA|WALK|WALL|WAY WY|WAYS|WL WELL|
WLS WELLS"""
SUFFIXES = {word: group.split()[0] for group in _USPS.replace('\n', '').split('|') for word in group.split()}
DIRECTIONS = {'N': 'N', 'S': 'S', 'E': 'E', 'W': 'W', 'NE': 'NE', 'NW': 'NW', 'SE': 'SE', 'SW': 'SW',
              'NORTH': 'N', 'SOUTH': 'S', 'EAST': 'E', 'WEST': 'W',
              'NORTHEAST': 'NE', 'NORTHWEST': 'NW', 'SOUTHEAST': 'SE', 'SOUTHWEST': 'SW'}
_DIR_LONG = {'N': 'NORTH', 'S': 'SOUTH', 'E': 'EAST', 'W': 'WEST',
             'NE': 'NORTHEAST', 'NW': 'NORTHWEST', 'SE': 'SOUTHEAST', 'SW': 'SOUTHWEST'}
_WORDS = {**SUFFIXES, **DIRECTIONS, 'SAINT': 'ST', 'SAINTE': 'STE', 'INTERSTATE': 'I', 'COUNTY': 'CO'}

_UNITS = ['FIRST', 'SECOND', 'THIRD', 'FOURTH', 'FIFTH', 'SIXTH', 'SEVENTH', 'EIGHTH', 'NINTH']
_TEENS = ['TENTH', 'ELEVENTH', 'TWELFTH', 'THIRTEENTH', 'FOURTEENTH', 'FIFTEENTH', 'SIXTEENTH', 'SEVENTEENTH',
          'EIGHTEENTH', 'NINETEENTH']
_TENS = ['TWENTY', 'THIRTY', 'FORTY', 'FIFTY', 'SIXTY', 'SEVENTY', 'EIGHTY', 'NINETY']
_TENTHS = ['TWENTIETH', 'THIRTIETH', 'FORTIETH', 'FIFTIETH', 'SIXTIETH', 'SEVENTIETH', 'EIGHTIETH', 'NINETIETH']


def _ordinal(n):
    return '%d%s' % (n, 'TH' if 10 <= n % 100 <= 20 else {1: 'ST', 2: 'ND', 3: 'RD'}.get(n % 10, 'TH'))


_ORDINALS = {w: _ordinal(i + 1) for i, w in enumerate(_UNITS)}
_ORDINALS.update({w: _ordinal(i + 10) for i, w in enumerate(_TEENS)})
_ORDINALS.update({w: _ordinal(20 + 10 * i) for i, w in enumerate(_TENTHS)})
_ORDINALS.update({t + u: _ordinal(20 + 10 * i + j + 1) for i, t in enumerate(_TENS) for j, u in enumerate(_UNITS)})
_TENS_VALUE = {w: 20 + 10 * i for i, w in enumerate(_TENS)}
_DROP = re.compile(r"['‘’`´.]")       # O'Brien -> OBRIEN, U.S. -> US, St. -> ST
_NON_ALNUM = re.compile(r'[^A-Z0-9]+')
_ROUTE_SPLIT = re.compile(r'^(I|US|SH|SR|CR|CO|HWY)(\d+)$')    # I25 -> I 25
_ORD_NUM = re.compile(r'(\d+)(ST|ND|RD|TH)')


def _part(value):
    """'' for None / NaN (pandas blanks), else the stripped text."""
    if value is None or (isinstance(value, float) and value != value):
        return ''
    return str(value).strip()


@lru_cache(maxsize=1 << 17)
def name_key(text):
    """Match key for free text: TIGER FULLNAME, typed CSV street or joined voter-file parts."""
    text = _part(text)
    if not text:
        return ''
    text = ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c)).upper()
    raw = []
    for token in _NON_ALNUM.sub(' ', _DROP.sub('', text)).split():
        split = _ROUTE_SPLIT.match(token)
        raw.extend(split.groups() if split else (token,))
    out, i = [], 0
    while i < len(raw):
        token, after = raw[i], raw[i + 1] if i + 1 < len(raw) else ''
        if token in _TENS_VALUE and after in _UNITS:                       # TWENTY FIRST -> 21ST
            out.append(_ordinal(_TENS_VALUE[token] + _UNITS.index(after) + 1))
            i += 2
            continue
        if token == 'U' and after == 'S' and i + 2 < len(raw) and (
                raw[i + 2] in ('HWY', 'HIGHWAY', 'RTE', 'ROUTE') or raw[i + 2].isdigit()):
            out.append('US')                                                 # U S HIGHWAY 24
            i += 2
            continue
        out.append(_ORDINALS.get(token) or _WORDS.get(token, token))
        i += 1
    return ' '.join(out)


def street_key(predir, name, suffix, postdir):
    """Key of voter-file address parts; equal to name_key of the same street written out as text."""
    return name_key(' '.join(p for p in map(_part, (predir, name, suffix, postdir)) if p))


def _cap(match):
    run, start = match.group(0), match.start()
    if start and match.string[start - 1] == "'" and len(run) < 2:          # PIKE'S -> Pike's
        return run.lower()
    if run.startswith('MC') and len(run) > 2:                              # MCARTHUR -> McArthur
        return 'Mc' + run[2] + run[3:].lower()
    return run[0] + run[1:].lower()


_KEEP_UPPER = {'US', 'I', 'NE', 'NW', 'SE', 'SW', 'SH', 'CR', 'II', 'III', 'IV'}


def _title(word):
    upper = word.upper()
    if upper in _KEEP_UPPER:
        return upper
    ordinal = _ORD_NUM.fullmatch(upper)
    if ordinal:
        return ordinal.group(1) + ordinal.group(2).lower()
    return re.sub(r'[A-Z]+', _cap, upper)


def display_city(text):
    """Title case for a stored (upper-case) city: 'COLORADO SPRINGS' -> 'Colorado Springs'."""
    return ' '.join(_title(w) for w in _part(text).split())


def display_street(predir, name, suffix, postdir):
    """Title-case label with USPS abbreviations for the directionals and suffix: 'N Cedar Heights Dr'."""
    pre, base, suf, post = map(_part, (predir, name, suffix, postdir))
    words = [_title(w) for w in base.split()]
    if pre:
        bare = re.sub(r'[^A-Z]', '', pre.upper())
        words.insert(0, DIRECTIONS.get(bare, bare or pre))
    if suf:
        bare = re.sub(r'[^A-Z]', '', suf.upper())
        words.append(SUFFIXES[bare].title() if bare in SUFFIXES else _title(suf))
    if post:
        bare = re.sub(r'[^A-Z]', '', post.upper())
        words.append(DIRECTIONS.get(bare, bare or post))
    return ' '.join(words)


# ---- similarity ------------------------------------------------------------------------------------------------
_W_DIRECTION, _W_TYPE, _W_ROUTE = 0.15, 0.25, 0.5
_ROUTE = {'US', 'I', 'STATE', 'SH', 'SR', 'CO', 'CR', 'HWY', 'RTE', 'RD'}


@lru_cache(maxsize=1 << 16)
def _profile(key):
    """Tokens with weights: leading/trailing directionals and the final street type count little, route designators
    of numbered roads count half, every other word counts fully; plus the spacing-free spelled-out form."""
    tokens = key.split()
    lo, hi = 0, len(tokens)
    while hi - lo > 1 and tokens[lo] in DIRECTIONS:
        lo += 1
    while hi - lo > 1 and tokens[hi - 1] in DIRECTIONS:
        hi -= 1
    numbered = any(t.isdigit() for t in tokens)
    weights = []
    for i, token in enumerate(tokens):
        if i < lo or i >= hi:
            weights.append(_W_DIRECTION)
        elif i == hi - 1 and hi - lo > 1 and token in SUFFIXES:
            weights.append(_W_TYPE)
        elif numbered and token in _ROUTE:
            weights.append(_W_ROUTE)
        else:
            weights.append(1.0)
    return tokens, weights, ''.join(_DIR_LONG.get(t, t) for t in tokens)


def _token_similarity(a, b):
    if a == b:
        return 1.0
    if min(len(a), len(b)) < 3 or any(c.isdigit() for c in a + b):
        return 0.0
    matcher = SequenceMatcher(None, a, b, autojunk=False)
    if matcher.real_quick_ratio() < 0.75 or matcher.quick_ratio() < 0.75:
        return 0.0
    ratio = matcher.ratio()
    return ratio if ratio >= 0.75 else 0.0


@lru_cache(maxsize=1 << 18)
def _similarity(a, b):
    ta, wa, ja = _profile(a)
    tb, wb, jb = _profile(b)
    pairs = sorted((-s, i, j) for i, x in enumerate(ta) for j, y in enumerate(tb) if (s := _token_similarity(x, y)))
    used_a, used_b, matched = set(), set(), 0.0
    for s, i, j in pairs:
        if i not in used_a and j not in used_b:
            used_a.add(i)
            used_b.add(j)
            matched -= s * (wa[i] + wb[j])
    score = matched / (sum(wa) + sum(wb))
    if ja == jb:                                       # NORTHGATE BLVD vs NORTH GATE BLVD
        score = max(score, 0.95)
    return round(score, 4)


def similarity(a, b):
    """0..1 order-insensitive token-set similarity of two street names (text or keys). Whole words must agree
    (typos tolerated at >= 0.75 character ratio, numbers exact); a missing directional or street type costs little,
    a missing or different base word costs a lot. 'Cedar Heights Drive' vs 'N Cedar Heights Dr' = 0.97,
    'Cheyenne Blvd' vs 'Cheyenne Mountain Blvd' = 0.72, 'Main St' vs 'Maple St' = 0.2."""
    a, b = name_key(a), name_key(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return _similarity(*sorted((a, b)))


# ---- sides and house numbers -------------------------------------------------------------------------------------
def parse_side(text):
    """'even' | 'odd' | 'both' from free text ('Even', 'E', 'odd #s', 'evens only'); anything else or both -> 'both'."""
    words = re.findall(r'[a-z]+', _part(text).lower())
    even = any(w in ('e', 'ev', 'evn') or w.startswith('even') for w in words)
    odd = any(w in ('o', 'od') or w.startswith('odd') for w in words)
    return 'both' if even == odd else 'even' if even else 'odd'


def house_int(text):
    """Leading house number as int ('1234 1/2' -> 1234, '12A' -> 12), None when there is none."""
    if isinstance(text, bool):
        return None
    if isinstance(text, int):
        return text if 0 <= text < 10 ** 9 else None
    match = re.search(r'\d+', _part(text))
    if not match:
        return None
    digits = match.group().lstrip('0') or '0'
    return int(digits) if len(digits) <= 9 else None
