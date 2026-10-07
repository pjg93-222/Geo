"""Approved geographic partitions of a district (d=None: the local Colorado pilot; else a tools.districts entry)."""
import re

from tools import districts as D

BASE_LEVELS = ('block', 'zcta', 'postal_zip')
EXTRA_LEVELS = ('county', 'state_house', 'state_senate', 'congressional', 'region')
LEVELS = BASE_LEVELS + EXTRA_LEVELS
POLYGON_LEVELS = tuple(level for level in LEVELS if level != 'postal_zip')
# cu-summary-v4 adds row-framework area indices on four registration levels (plus residence tract and ZIP) and the
# precinct and tract polygons they need. ZIP areas are drawn on the matching ZCTA polygon.
AREA_INDEX_LEVELS = ('block', 'precinct', 'tract', 'zip')
AREA_INDEX_BASES = {'registration': ('block', 'precinct', 'tract', 'zip'), 'residence': ('tract', 'zip')}
V4_GEOMETRY_LEVELS = ('precinct', 'tract')
V4_SCHEMAS = ('cu-summary-v4',)
def sources(d=None):
    """The public boundary files the hierarchy levels come from (the state's legislative files; counties: all states)."""
    fips = D.get(d)['state_fips']
    return {
        'county': 'https://www2.census.gov/geo/tiger/TIGER2025/COUNTY/tl_2025_us_county.zip',
        'state_house': 'https://www2.census.gov/geo/tiger/TIGER2025/SLDL/tl_2025_%s_sldl.zip' % fips,
        'state_senate': 'https://www2.census.gov/geo/tiger/TIGER2025/SLDU/tl_2025_%s_sldu.zip' % fips,
    }
SOURCES = sources()
def catalog(d=None):
    d = D.get(d)
    return [
        {'id':'region','label':'Regional overview','vintage':'Study region: supplied %s CD-%s extract' % (d['state_name'], d['cd119fp'])},
        {'id':'congressional','label':'Congressional districts','vintage':'119th Congress · TIGER/Line 2025'},
        {'id':'county','label':'Counties','vintage':'TIGER/Line 2025 counties'},
        {'id':'state_senate','label':'State Senate districts','vintage':'2024 legislative plans · TIGER/Line 2025'},
        {'id':'state_house','label':'State House districts','vintage':'2024 legislative plans · TIGER/Line 2025'},
        {'id':'zcta','label':'ZCTAs · ZIP approximation','vintage':'2020 ZCTAs'},
        {'id':'block','label':'Census blocks','vintage':'2020 Census blocks · TIGER/Line 2025'},
        {'id':'postal_zip','label':'Postal ZIPs · table only','vintage':'Not applicable: supplied postal ZIP'},
    ]
CATALOG = catalog()
def available(schema, d=None):
    return catalog(d) if schema in ('cu-summary-v3', 'cu-summary-v4') else [x for x in catalog(d) if x['id'] in BASE_LEVELS]

def pattern(level, d=None):
    d = D.get(d)
    if level == 'precinct': return d['precinct_pattern']
    if level == 'tract': return r'\d{11}'      # the district reaches into neighbouring counties
    if level == 'block': return r'\d{15}'
    if level in ('region','congressional'): return d['district_id']
    if level == 'county': return '|'.join(sorted(d['county_names']))
    if level in ('state_house','state_senate'): return d['state_fips'] + r'\d{3}'
    return r'\d{5}'

def label(level, ident, d=None):
    d = D.get(d)
    if level == 'block': return 'Block '+ident[-4:]+' · '+ident[:11]
    if level == 'zcta': return 'ZCTA '+ident
    if level == 'postal_zip': return 'Postal ZIP '+ident
    if level == 'county': return d['county_names'][ident]+' · '+d['state_name']
    if level == 'state_house': return d['state_name']+' House District '+str(int(ident[2:]))
    if level == 'state_senate': return d['state_name']+' Senate District '+str(int(ident[2:]))
    if level == 'congressional': return d['state_name']+' · Congressional District '+d['cd119fp']
    if level == 'region': return d['state_name']+' CD-'+d['cd119fp']+' study region'
    raise ValueError('Unsupported geography')


# ---- row-framework area labels (FIDELITY_SPEC section 4): derived from the id, never free text --------------------

def tract_short(ident):
    """'08041006400' -> '64', '08041000102' -> '1.02' (the 6-digit tract code: 4 digits, then 2 decimals unless '00')."""
    code = ident[5:11]
    whole, decimals = int(code[:4]), code[4:]
    return str(whole) + ('' if decimals == '00' else '.' + decimals)


def area_label(level, ident, d=None):      # d: accepted like the other district-aware helpers; no label depends on it yet
    """Neutral display label of an area-index area: 'Block 2008', 'Precinct 621', 'Tract 64', 'ZIP 80916'. A precinct id
    that is not Colorado's 10-digit code (Oregon '41005-123', Connecticut '005-00-...') shows whole."""
    if level == 'block': return 'Block ' + ident[-4:]
    if level == 'precinct': return 'Precinct ' + (ident[-3:] if re.fullmatch(r'\d{10}', ident) else ident)
    if level == 'tract': return 'Tract ' + tract_short(ident)
    if level == 'zip': return 'ZIP ' + ident
    raise ValueError('Unsupported area-index level')


def area_pattern(level, d=None):
    return {'block': r'\d{15}', 'precinct': D.get(d)['precinct_pattern'], 'tract': r'\d{11}', 'zip': r'\d{5}'}[level]
