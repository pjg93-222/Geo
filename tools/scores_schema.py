"""Schema of the local score store `scores.sqlite3` (schema "scores-v1"; stdlib only, shared by the offline loader and the app).

The store holds scores, standings and area ids keyed by the opaque person id (`p_` + HMAC). It holds no name, address,
email, phone, coordinate or source-row number: names are joined at request time from contacts.sqlite3, and household
locations are joined from street.sqlite3 by the opaque household id.
"""
SCHEMA = 'scores-v1'
GRADE_LETTERS = 'ABCD'
INDEX_IDS = ('ovr', 'cua', 'part', 'soi')
BLOB_VERSION = 1          # layout of evidence blobs: u16[40] values | u8[40] status | f32[40] p | f32[40] exposure
N_FIELDS = 40
FLAG_BITS = ('flag_no_consumer_match', 'flag_cpi_blank', 'flag_children_rule', 'flag_digital_code50',
             'flag_geo_unaudited', 'flag_rare_class_mass')
AREA_COLUMNS = ('reg_block', 'reg_precinct', 'reg_tract', 'reg_zip', 'res_tract', 'res_zip')
AREA_LEVEL_OF = {
    ('registration', 'block'): 'reg_block', ('registration', 'precinct'): 'reg_precinct',
    ('registration', 'tract'): 'reg_tract', ('registration', 'zip'): 'reg_zip',
    ('residence', 'tract'): 'res_tract', ('residence', 'zip'): 'res_zip',
}


def _index_columns(group):
    return [(group + '_rank', 'INTEGER'), (group + '_tie_n', 'INTEGER'), (group + '_score', 'REAL'),
            (group + '_pct', 'REAL'), (group + '_pct_lo90', 'REAL'), (group + '_pct_hi90', 'REAL'),
            (group + '_measured_pct', 'REAL'), (group + '_pct_within_reg', 'REAL'), (group + '_grade', 'TEXT'),
            (group + '_p_top10', 'REAL')]


PERSON_COLUMNS = ([
    ('pos', 'INTEGER PRIMARY KEY'), ('person_id', 'TEXT NOT NULL'), ('hh', 'INTEGER NOT NULL'), ('hh_size', 'INTEGER'),
    ('age', 'INTEGER'), ('reg_group', 'INTEGER NOT NULL'), ('party', 'TEXT'),
    ('reg_block', 'TEXT'), ('reg_precinct', 'TEXT'), ('reg_tract', 'TEXT'), ('reg_zip', 'TEXT'),
    ('res_tract', 'TEXT'), ('res_zip', 'TEXT'),
    ('flags', 'INTEGER NOT NULL'), ('has_location', 'INTEGER NOT NULL'), ('reg_rank', 'INTEGER NOT NULL'),
] + [column for group in INDEX_IDS for column in _index_columns(group)] + [
    ('lean', 'REAL'), ('lean_lo90', 'REAL'), ('lean_hi90', 'REAL'), ('lean_measured_pct', 'REAL'),
    ('lean_band', 'INTEGER'), ('lean_undet', 'INTEGER'), ('lean_grade', 'TEXT'), ('lean_p_same_band', 'REAL'),
])
PERSON_COLUMN_NAMES = tuple(name for name, _ in PERSON_COLUMNS)

HOUSEHOLD_COLUMNS = (
    ('hh', 'INTEGER PRIMARY KEY'), ('hh_n', 'INTEGER'), ('hh_ovr_mean_pct', 'REAL'), ('hh_ovr_max_pct', 'REAL'),
    ('hh_n_top10', 'REAL'), ('hh_pct', 'REAL'), ('hh_lean_mean', 'REAL'),
    ('hh_n_lean_band_m2', 'INTEGER'), ('hh_n_lean_band_m1', 'INTEGER'), ('hh_n_lean_band_p0', 'INTEGER'),
    ('hh_n_lean_band_p1', 'INTEGER'), ('hh_n_lean_band_p2', 'INTEGER'), ('hh_measured_pct_mean', 'REAL'),
    ('hh_worst_grade', 'TEXT'),
)
HOUSEHOLD_COLUMN_NAMES = tuple(name for name, _ in HOUSEHOLD_COLUMNS)

STREET_COLUMNS = (('h_id', 'TEXT PRIMARY KEY'), ('hh', 'INTEGER NOT NULL'), ('n_people', 'INTEGER NOT NULL'),
                  ('ovr_mean_pct', 'REAL NOT NULL'), ('bin', 'INTEGER NOT NULL'), ('reg_block', 'TEXT'),
                  ('measured_mean', 'REAL NOT NULL'))


def table_sql(name, columns, suffix=''):
    return 'CREATE TABLE %s (%s)%s' % (name, ', '.join('%s %s' % pair for pair in columns), suffix)

# Candidate support (Phase 4B), keyed by person.pos. Written only for a candidate district, so it is not in TABLES.
SUPPORT_TABLE = table_sql('support', (('pos', 'INTEGER PRIMARY KEY'), ('sup_score', 'REAL NOT NULL'), ('sup_pct', 'REAL NOT NULL'),
                                      ('sup_rank', 'INTEGER NOT NULL'), ('sup_tie_n', 'INTEGER NOT NULL'),
                                      ('in_universe', 'INTEGER NOT NULL')))


TABLES = [
    'CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)',
    table_sql('person', PERSON_COLUMNS),
    table_sql('household', HOUSEHOLD_COLUMNS),
    table_sql('street_household', STREET_COLUMNS, ' WITHOUT ROWID'),
    'CREATE TABLE evidence (pos INTEGER PRIMARY KEY, blob BLOB NOT NULL)',
    'CREATE TABLE value_dict (fid INTEGER NOT NULL, code INTEGER NOT NULL, literal TEXT NOT NULL, '
    'PRIMARY KEY (fid, code)) WITHOUT ROWID',
    'CREATE TABLE list_column (name TEXT PRIMARY KEY, dtype TEXT NOT NULL, n INTEGER NOT NULL, data BLOB NOT NULL)',
    'CREATE TABLE list_dict (name TEXT PRIMARY KEY, ids TEXT NOT NULL)',
    'CREATE TABLE demo_name (pos INTEGER PRIMARY KEY, name TEXT NOT NULL)',
]

# Covering indexes. The filter indexes cover "filter column(s) + position" for the list shapes; the household index
# covers the household members query without touching the table.
INDEXES = [
    'CREATE UNIQUE INDEX person_id ON person(person_id)',
    'CREATE INDEX person_hh ON person(hh, pos, person_id, age, ovr_pct, ovr_pct_lo90, ovr_pct_hi90, ovr_grade, '
    'lean_band, lean_undet)',
    'CREATE INDEX person_reg ON person(reg_group, pos)',
    'CREATE INDEX person_reg_rank ON person(reg_group, reg_rank, pos)',
    'CREATE INDEX person_grade ON person(ovr_grade, pos)',
    'CREATE INDEX person_lean ON person(lean_band, lean_undet, pos)',
    'CREATE INDEX person_reg_block ON person(reg_block, pos)',
    'CREATE INDEX person_reg_precinct ON person(reg_precinct, pos)',
    'CREATE INDEX person_reg_tract ON person(reg_tract, pos)',
    'CREATE INDEX person_reg_zip ON person(reg_zip, pos)',
    'CREATE INDEX person_res_tract ON person(res_tract, pos)',
    'CREATE INDEX person_res_zip ON person(res_zip, pos)',
    'CREATE INDEX street_household_hh ON street_household(hh)',
]
FTS = ["CREATE VIRTUAL TABLE demo_search USING fts5(name, content='demo_name', content_rowid='pos')"]

# Metadata keys the app requires before it serves person-level endpoints.
REQUIRED_METADATA = ('schema', 'rowfw_version', 'n_people', 'n_households', 'inputs', 'w1', 'field_catalog',
                     'reg_group_counts', 'district', 'balanced', 'demo_key', 'status_codes', 'blob_version',
                     'contacts_source_sha256', 'area_table_sha256', 'built_utc', 'list_columns')
# W1 states the app accepts for serving names: the HMAC census passed on the real source, or a synthetic fixture.
W1_ACCEPTED = ('verified', 'fixture')


def grade_code(letter):
    return GRADE_LETTERS.index(letter)
