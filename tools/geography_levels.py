"""Approved geographic partitions for the local Colorado pilot."""
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
SOURCES = {
    'county': 'https://www2.census.gov/geo/tiger/TIGER2025/COUNTY/tl_2025_us_county.zip',
    'state_house': 'https://www2.census.gov/geo/tiger/TIGER2025/SLDL/tl_2025_08_sldl.zip',
    'state_senate': 'https://www2.census.gov/geo/tiger/TIGER2025/SLDU/tl_2025_08_sldu.zip',
}
CATALOG = [
    {'id':'region','label':'Regional overview','vintage':'Study region: supplied Colorado CD-05 extract'},
    {'id':'congressional','label':'Congressional districts','vintage':'119th Congress · TIGER/Line 2025'},
    {'id':'county','label':'Counties','vintage':'TIGER/Line 2025 counties'},
    {'id':'state_senate','label':'State Senate districts','vintage':'2024 legislative plans · TIGER/Line 2025'},
    {'id':'state_house','label':'State House districts','vintage':'2024 legislative plans · TIGER/Line 2025'},
    {'id':'zcta','label':'ZCTAs · ZIP approximation','vintage':'2020 ZCTAs'},
    {'id':'block','label':'Census blocks','vintage':'2020 Census blocks · TIGER/Line 2025'},
    {'id':'postal_zip','label':'Postal ZIPs · table only','vintage':'Not applicable: supplied postal ZIP'},
]
def available(schema):
    return CATALOG if schema in ('cu-summary-v3', 'cu-summary-v4') else [x for x in CATALOG if x['id'] in BASE_LEVELS]

def pattern(level):
    if level == 'precinct': return r'\d{10}'
    if level == 'tract': return r'\d{11}'      # the district reaches into neighbouring counties
    return r'\d{15}' if level == 'block' else r'0805' if level in ('region','congressional') else r'08041' if level == 'county' else r'08\d{3}' if level in ('state_house','state_senate') else r'\d{5}'

def label(level, ident):
    if level == 'block': return 'Block '+ident[-4:]+' · '+ident[:11]
    if level == 'zcta': return 'ZCTA '+ident
    if level == 'postal_zip': return 'Postal ZIP '+ident
    if level == 'county': return 'El Paso County · Colorado'
    if level == 'state_house': return 'Colorado House District '+str(int(ident[2:]))
    if level == 'state_senate': return 'Colorado Senate District '+str(int(ident[2:]))
    if level == 'congressional': return 'Colorado · Congressional District 05'
    if level == 'region': return 'Colorado CD-05 study region'
    raise ValueError('Unsupported geography')


# ---- row-framework area labels (FIDELITY_SPEC section 4): derived from the id, never free text --------------------

def tract_short(ident):
    """'08041006400' -> '64', '08041000102' -> '1.02' (the 6-digit tract code: 4 digits, then 2 decimals unless '00')."""
    code = ident[5:11]
    whole, decimals = int(code[:4]), code[4:]
    return str(whole) + ('' if decimals == '00' else '.' + decimals)


def area_label(level, ident):
    """Neutral display label of an area-index area: 'Block 2008', 'Precinct 621', 'Tract 64', 'ZIP 80916'."""
    if level == 'block': return 'Block ' + ident[-4:]
    if level == 'precinct': return 'Precinct ' + ident[-3:]
    if level == 'tract': return 'Tract ' + tract_short(ident)
    if level == 'zip': return 'ZIP ' + ident
    raise ValueError('Unsupported area-index level')


def area_pattern(level):
    return {'block': r'\d{15}', 'precinct': r'\d{10}', 'tract': r'\d{11}', 'zip': r'\d{5}'}[level]
