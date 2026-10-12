"""The districts Terrier serves (multi-district build, Phase 2): one entry per congressional district.

Pure Python (no Django, numpy or pandas): server/settings.py, the server and every build tool read the same values.
A server process serves ONE district: CU_DISTRICT (default co-05) picks it in server/settings.py. Build tools take
--district (default co-05). The co-05 entry reproduces the single-district app exactly: its data directory is .local
itself, its street salt is the sibling cu-geography repo's, its snapshot keys start co-cd005-, and its election
window, vote rule and city words are the ones walk_names.py and build_walk.py have always used.
"""
import copy
import json
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from server.research import walk_names as N
from tools import hostpaths as H

REPO = Path(__file__).resolve().parents[1]
# The inputs sit at absolute paths, like build_scores_v3.ROOT: a git worktree of this repo sits elsewhere; the data does not move.
# tools/hostpaths.py makes each an environment variable whose default is the Windows path used so far (the build container sets them).
LOCAL = H.env_path('TERRIER_LOCAL_DIR', REPO / '.local')
WORK = H.WORK
GEOGRAPHY = H.GEOGRAPHY
SIBLING_LOCAL = H.SIBLING / '.local'
DOWNLOADS = H.SOURCES
DEFAULT = 'co-05'

_CITY_WORDS = {'SPG': 'SPRING', 'SPGS': 'SPRINGS', 'SPRNGS': 'SPRINGS', 'FT': 'FORT', 'MTN': 'MOUNTAIN',
               'MTNS': 'MOUNTAINS', 'GRN': 'GREEN', 'FLS': 'FALLS', 'HTS': 'HEIGHTS', 'LK': 'LAKE', 'VLY': 'VALLEY'}
_SPRINGS = ('SPG', 'SPGS', 'SPRING', 'SPRINGS', 'SPRNGS')


def _co_lead(tokens):
    """'CO SPGS', 'CO SPRINGS' -> COLORADO ... (the rule of build_walk.canonical_city, unchanged)."""
    if len(tokens) > 1 and tokens[0] == 'CO' and tokens[1] in _SPRINGS:
        tokens = ['COLORADO', *tokens[1:]]
    return tokens


def _ct_lead(tokens):
    """'TOWN OF BERLIN' -> BERLIN, 'CT MIDDLETOWN' -> MIDDLETOWN (spellings found in the CT file)."""
    if len(tokens) > 2 and tokens[:2] == ['TOWN', 'OF']:
        tokens = tokens[2:]
    if len(tokens) > 1 and tokens[0] == 'CT':
        tokens = tokens[1:]
    return tokens


_OR_COUNTIES = ('41005', '41017', '41031', '41043', '41047', '41051')   # Benton 41003 sliver: virtual streets, no file
_CT_REGIONS = ('09110', '09130', '09140', '09160')
_WITH_CANDIDATE = {'cua': 30, 'sup': 25, 'part': 25, 'soi': 20}          # Paul 2026-10-07 (O-11); ids as in area_layers
_INTERIM = {'cua': 40, 'part': 35, 'soi': 25}     # Colorado's weights, shown for OR/CT until candidate support exists (Phase 4B)
# Candidate support, general mode (Phase 4B, tools/support.py): direction signals only, each turned toward the candidate's
# party, equal weights until Paul says otherwise. party_values overrides the registration signal per code (default: the
# candidate's party 1.0, the other major party 0.0, every other code 0.5).
_SUPPORT = {'signals': {'partisan': 1, 'presidential': 1, 'registration': 1, 'primary_ballot': 1},
            'party_values': {}, 'ballot_years': (2014, 2025)}
# Street plan (Phase 4E; Paul 2026-10-07): presets (field_labels.ROI_PRESETS), weights of the percentile-rank blend, floors;
# the default is "Needs a push" (Paul 2026-10-08), the other three stay as alternatives. p: what the chance of voting is read
# from (the mean of the five turnout-only scores). potential: the defaults of the two weights of "Needs a push" (Paul 2026-10-09
# left them to the agent: wp the balance of undecided against supporter, wi the weight of sphere of influence, top counts 1.5
# times). turf: the one-click walk list's door count (10..500) and the longest straight jump between pieces, in metres.
_ROI = {'presets': ('push', 'mixed', 'turnout', 'identify'), 'preset': 'push', 'p': 'turnout',
        'weights': {'value': 1.0, 'pop_density': 0.0, 'voter_density': 0.0}, 'min_doors': 5, 'min_length_m': 50,
        'potential': {'wp': 0.5, 'wi': 0.5}, 'turf': {'doors': 60, 'jump_m': 400.0}}
# NAMELSAD of every county the district polygon meets (area > 1e-10 deg2, the extend_geography rule), read from the
# TIGER2025 county file on 2026-10-07; CT's TIGER2025 "counties" are the planning regions
_OR_NAMES = {'41003': 'Benton County', '41005': 'Clackamas County', '41017': 'Deschutes County',
             '41031': 'Jefferson County', '41043': 'Linn County', '41047': 'Marion County', '41051': 'Multnomah County'}
_CT_NAMES = {'09110': 'Capitol Planning Region', '09130': 'Lower Connecticut River Valley Planning Region',
             '09140': 'Naugatuck Valley Planning Region', '09160': 'Northwest Hills Planning Region',
             '09170': 'South Central Connecticut Planning Region'}   # 09170: touches only; 25 voter blocks are recoded there

DISTRICTS = {
    'co-05': {
        'id': 'co-05', 'state': 'CO', 'state_name': 'Colorado', 'state_fips': '08', 'cd': '005', 'cd119fp': '05',
        'district_id': '0805', 'label': 'Colorado · District 05', 'short_label': 'District 5',
        'source_csv': DOWNLOADS / 'statara_co_cd005_20260904.csv',
        'sha256': '4c48800b8c21870e6e2b6242810bbbe917d8169e529b440efb662f71c464239f',
        'rows': 491308, 'households': 343000,
        'data_dir': '', 'street_salt': SIBLING_LOCAL / '.street-salt', 'snapshot_prefix': 'co-cd005-',
        'geography': GEOGRAPHY, 'road_files': ('roads.zip',), 'counties': ('08041',),
        'block_regions': False, 'road_clip_m': None,
        'bounds': (-109.1, 36.9, -102.0, 41.1), 'utm_epsg': 26913, 'center': (-104.82, 38.86), 'zoom': 10,
        'roads_label': 'El Paso County',
        'precinct_key': 'code', 'precinct_pattern': r'\d{10}',
        'city_field': 'svi_registration_city', 'city_words': {**_CITY_WORDS, 'COLO': 'COLORADO'}, 'city_lead': _co_lead,
        'primaries': N.PRIMARIES, 'abs_prefixes': N.ABS_PREFIXES, 'abs_rule': N.ABS_RULE,
        'abs_gate': N.ABS_GATE['min_share'],
        'person_fields': 'scores', 'party_rollup_check': True, 'port': 8767,
        'score_weights': {'cua': 40, 'part': 35, 'soi': 25}, 'industries': ('cua',),
        'county_names': {'08041': 'El Paso County'}, 'rowfw_root': SIBLING_LOCAL / 'row-framework-20260930',
        'interim_overall': None,
        'candidate': None, 'party': None, 'race_mode': None, 'primary_universe': 'party_only',
        'support': None, 'roi': None,       # no candidate: no support score and, with it, no street plan (L-3)
    },
    'or-05': {
        'id': 'or-05', 'state': 'OR', 'state_name': 'Oregon', 'state_fips': '41', 'cd': '005', 'cd119fp': '05',
        'district_id': '4105', 'label': 'Oregon · District 05', 'short_label': 'District 5',
        'source_csv': DOWNLOADS / 'terrier-sources' / 'statara_or_cd005_20260904.csv',
        'sha256': 'd75d13ccfea891eb252b7609017e338a70263ddee5a27ced5dda17abda91a598',
        'rows': 562563, 'households': 396480,
        'data_dir': 'or-05', 'street_salt': None, 'snapshot_prefix': 'or-cd005-',
        'geography': GEOGRAPHY / 'or-05',
        'road_files': tuple('roads/tl_2025_%s_roads.zip' % c for c in _OR_COUNTIES), 'counties': _OR_COUNTIES,
        'block_regions': False, 'road_clip_m': 2000,
        'bounds': (-124.6, 41.9, -116.4, 46.3), 'utm_epsg': 26910, 'center': (-122.17, 44.64), 'zoom': 8,
        'roads_label': 'Clackamas, Deschutes, Jefferson, Linn, Marion and Multnomah counties',
        'precinct_key': 'county-code', 'precinct_pattern': r'(41\d{3}-)?\d{2,4}',
        'city_field': 'svi_registration_city', 'city_words': {**_CITY_WORDS, 'MT': 'MOUNT', 'ST': 'SAINT'},
        'city_lead': None,
        # third Tuesday in May; each date read from the Oregon Secretary of State's Voters' Pamphlet cover on 2026-10-07:
        # https://records.sos.state.or.us/ORSOSWebDrawer/Record/<id>/File/document with id 6873762 (2014), 6873814
        # (2016), 7325556 (2020), 8782277 (2022), 10173706 (2024); 2018: the Elections Division statistical summary
        # dated 2018-05-15 (https://digitalcollections.library.oregon.gov/nodes/view/215118) and the pamphlet cover.
        'primaries': {'2014p': '2014-05-20', '2016p': '2016-05-17', '2018p': '2018-05-15', '2020p': '2020-05-19',
                      '2022p': '2022-05-17', '2024p': '2024-05-21'},
        # O-3: Oregon votes by mail, so every recorded vote counts (any method letter); no M/A gate.
        'abs_prefixes': None, 'abs_rule': 'Abs = mail ballots (Oregon votes by mail; every recorded vote counts)',
        'abs_gate': None,
        'person_fields': 'csv', 'party_rollup_check': False, 'port': 8775,
        'score_weights': dict(_WITH_CANDIDATE), 'industries': ('cua',),
        'county_names': _OR_NAMES, 'rowfw_root': LOCAL / 'or-05' / 'rowfw', 'interim_overall': dict(_INTERIM),
        # race_mode (Paul, O-2): 'general' counts direction signals only. 'primary' stops tools/support.py with
        # PRIMARY_NOT_APPROVED until PRIMARY_MODE_SPEC.md is approved. primary_universe: 'party_only' or 'party_plus_unaffiliated'.
        'candidate': 'Janelle Bynum', 'party': 'D', 'race_mode': 'general', 'primary_universe': 'party_only',
        'support': copy.deepcopy(_SUPPORT), 'roi': copy.deepcopy(_ROI),
    },
    'ct-01': {
        'id': 'ct-01', 'state': 'CT', 'state_name': 'Connecticut', 'state_fips': '09', 'cd': '001', 'cd119fp': '01',
        'district_id': '0901', 'label': 'Connecticut · District 01', 'short_label': 'District 1',
        'source_csv': DOWNLOADS / 'terrier-sources' / 'statara_ct_cd001_20260904.csv',
        'sha256': '6e6c7822c44bf04712cf67c25721e6a180281a603af41721df32c9ab7074129a',
        'rows': 458372, 'households': 334071,
        'data_dir': 'ct-01', 'street_salt': None, 'snapshot_prefix': 'ct-cd001-',
        'geography': GEOGRAPHY / 'ct-01',
        'road_files': tuple('roads/tl_2025_%s_roads.zip' % c for c in _CT_REGIONS), 'counties': _CT_REGIONS,
        # voter block ids use planning-region county codes; TIGER2025 tabblock20 uses old counties: re-code
        'block_regions': True, 'road_clip_m': 2000,
        'bounds': (-73.8, 40.9, -71.7, 42.1), 'utm_epsg': 26918, 'center': (-72.83, 41.79), 'zoom': 9.5,
        'roads_label': 'Capitol, Lower Connecticut River Valley, Naugatuck Valley and Northwest Hills planning regions',
        'precinct_key': 'code-name', 'precinct_pattern': r'\d{3}-\d{2}(-[0-9A-Z-]{1,57})?',
        'city_field': 'svi_registration_township', 'city_words': {**_CITY_WORDS, 'CTR': 'CENTER', 'CNTR': 'CENTER'},
        'city_lead': _ct_lead,
        # second Tuesday in August; read on 2026-10-07 from the Connecticut Secretary of the State's election calendars
        # (under https://portal.ct.gov/-/media/sots/electionservices/calendars/: 2014_election/2014electioncaledarpdf.pdf,
        # 2018_election/2018-calendar-searchable-pdf.pdf, 2020election/2020-calendar.pdf,
        # 2022-election/2022-election-calendar.pdf, 2024-election/2024-ec_3-27-24--2.pdf) and, for 2016 (image-only
        # calendar), its results page https://portal.ct.gov/sots/election-services/election-results/election-results.
        'primaries': {'2014p': '2014-08-12', '2016p': '2016-08-09', '2018p': '2018-08-14', '2020p': '2020-08-11',
                      '2022p': '2022-08-09', '2024p': '2024-08-13'},
        # O-3: A or M = absentee/mail; E early, P in person, Y voted (method not given); gate off, shares reported.
        'abs_prefixes': ('A', 'M'), 'abs_rule': 'Abs = absentee or mail ballots (A or M)', 'abs_gate': None,
        'person_fields': 'csv', 'party_rollup_check': True, 'port': 8777,
        'score_weights': dict(_WITH_CANDIDATE), 'industries': ('cua',),
        'county_names': _CT_NAMES, 'rowfw_root': LOCAL / 'ct-01' / 'rowfw', 'interim_overall': dict(_INTERIM),
        'candidate': 'Luke Bronin', 'party': 'D', 'race_mode': 'general', 'primary_universe': 'party_only',
        'support': copy.deepcopy(_SUPPORT), 'roi': copy.deepcopy(_ROI),
    },
}


def get(district=None):
    """The entry for an id (None: co-05). A dict is returned as is (tests pass modified copies)."""
    if isinstance(district, dict):
        return district
    try:
        return DISTRICTS[district or DEFAULT]
    except KeyError:
        raise KeyError('Unknown district %r (known: %s)' % (district, ', '.join(DISTRICTS))) from None


def current():
    """The entry this process serves: CU_DISTRICT (default co-05), as server/settings.py reads it."""
    return get(os.environ.get('CU_DISTRICT') or None)


def data_dir(d, local=LOCAL):
    return Path(local) / d['data_dir'] if d['data_dir'] else Path(local)


def street_salt(d, local=LOCAL):
    return Path(d['street_salt']) if d['street_salt'] else data_dir(d, local) / '.street-salt'


def road_paths(d):
    return [Path(d['geography']) / name for name in d['road_files']]


def rowfw_build(d):
    """The frozen rowfw build folder (co-05: the sibling repo's, build_scores_v3.RFB)."""
    return Path(d['rowfw_root']) / 'build' / 'rowfw-1.0.0'


def rowfw_cache(d):
    return Path(d['rowfw_root']) / 'cache' / 'inputs.parquet'


def area_table(d):
    """The table of EVERY area (tools/shown_areas.py writes it; nothing is withheld, Paul 2026-10-08). Local only."""
    return data_dir(d) / 'area_scores_shown.csv'


def block_aliases(d):
    """{old block id: planning-region block id} that prepare_geography writes for CT; {} when the file is missing."""
    path = Path(d['geography']) / 'block_aliases.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


def district_urls(environ=os.environ):
    """{district id: page address} from CU_DISTRICT_URLS, a JSON object or "co-05=https://a.example,or-05=https://b.example";
    {} when unset (the selector then links to this host at the district's port). Hosted, each district is its own container
    behind its own hostname. A bad value is an error, so a typo stops the server instead of sending staff to the wrong place."""
    raw = (environ.get('CU_DISTRICT_URLS') or '').strip()
    if not raw:
        return {}
    if raw.startswith('{'):
        try:
            found = json.loads(raw)
        except ValueError:
            raise ValueError('CU_DISTRICT_URLS is not valid JSON') from None
    else:
        found = {}
        for part in filter(None, (piece.strip() for piece in raw.split(','))):
            name, sep, url = part.partition('=')
            if not sep:
                raise ValueError('CU_DISTRICT_URLS: expected district=address, got %r' % part)
            found[name.strip()] = url.strip()
    if not isinstance(found, dict):
        raise ValueError('CU_DISTRICT_URLS must be a JSON object or a district=address list')
    for name, url in found.items():
        if name not in DISTRICTS:
            raise ValueError('CU_DISTRICT_URLS: unknown district %r (known: %s)' % (name, ', '.join(DISTRICTS)))
        parts = urlsplit(url) if isinstance(url, str) else None
        if (parts is None or parts.scheme not in ('http', 'https') or not parts.hostname or parts.username
                or parts.password or parts.query or parts.fragment):
            raise ValueError('CU_DISTRICT_URLS: %s needs a plain http or https address' % name)
    return dict(found)


def snapshot_pattern(d):
    return re.escape(d['snapshot_prefix']) + '[a-f0-9]{12}'


def window(d):
    """The vote-history window a walk store records (meta 'window'); co-05 gives walk_names.WINDOW exactly."""
    return {'primaries': list(d['primaries']), 'generals': list(N.GENERALS), 'abs_rule': d['abs_rule']}


def election_keys(d):
    """yyyymmdd ints in build_walk's column order: the six primaries, then the six generals."""
    days = {**d['primaries'], **N.GENERALS}
    return [int(days[e].replace('-', '')) for e in [*d['primaries'], *N.GENERALS]]


def is_mail(d, code):
    """True when a recorded vote code counts in Abs: any code (abs_prefixes None), else its first letter."""
    code = str(code or '').strip().upper()
    return bool(code) and (d['abs_prefixes'] is None or code[:1] in d['abs_prefixes'])


def precinct_key(d, code, county='', name=''):
    """The walk/area precinct id: CO the 10-digit code; OR county FIPS + '-' + code (codes repeat across counties);
    CT code + '-' + the name in capitals and hyphens (a code carries several names; a name has one code)."""
    code, county, name = (str(v or '').strip() for v in (code, county, name))
    if not code or d['precinct_key'] == 'code':
        return code
    if d['precinct_key'] == 'county-code':
        return county + '-' + code if county else code
    slug = re.sub(r'[^0-9A-Z]+', '-', name.upper()).strip('-')
    return code + '-' + slug if slug else code


def canonical_city(text, d=None):
    """One spelling per city: upper case, punctuation dropped, the district's lead rule and postal words applied."""
    d = get(d)
    tokens = re.sub(r'[^A-Z0-9]+', ' ', re.sub(r"['\u2018\u2019`\u00b4.]", '', str(text).upper())).split()
    if d['city_lead']:
        tokens = d['city_lead'](tokens)
    return ' '.join(d['city_words'].get(t, t) for t in tokens)


def check_data_dir(d, directory):
    """Raise ValueError when the street store in `directory` was built from another source file than d's."""
    street = Path(directory) / 'street.sqlite3'
    if not street.is_file():
        return
    with closing(sqlite3.connect(street.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        row = connection.execute("SELECT value FROM metadata WHERE key='source_sha256'").fetchone()
    if row is None or json.loads(row[0]) != d['sha256']:
        raise ValueError('%s holds stores built from another source file than %s' % (directory, d['id']))
