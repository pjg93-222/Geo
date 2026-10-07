"""Synthetic world for the walk-list builder and API tests. Everything is invented: people, streets, phones (area code
719, numbers 555-01xx), voter ids and coordinates; no real record was read to make it. No Django import.

    road_rows()                       [(id, name, kind, geometry)]: the rows of the `roads` table (geometry: GeoJSON text)
    hand_case()                       Case(rows, expected): 38 people at 27 doors plus a hand-computed oracle
    write_minimal_stores(dir, case)   source.csv and contacts / scores / street / roads .sqlite3 with the table and column
                                      names and types of the real stores: scores.person keeps only the columns the walk
                                      builder reads, contacts has no full-text `search` table; scores=False writes no
                                      scores.sqlite3 (a world for a district whose person fields come from the CSV)
    even_left_for(block, a, b)        1/0: are even numbers on the left walking a -> b along an expected block

The neighbourhood lies near lon -104.80, lat 38.85. Local metres below are x east, y north of that point.

    Cedar Heights Dr  west to east, y = 0 from x = -300 to 100, then rising to y = 24 at x = 330: three blocks, x -300..-100,
                      -100..100 (two road rows joined by a degree-2 node at x = 0), 100..330. Juniper Ln crosses it at
                      x = -100, Pine St at x = 100, Academy Blvd (S1200) ends it. Evens on the north side, odds south.
    Juniper Ln        x = -100, y -210..210, crossed by Cedar Heights Dr and met by Ridgeview Ct at y = 110
    Ridgeview Ct      a dead end 100 m long, y = 110, drawn from its closed end towards Juniper Ln
    Pine St           x = 100, y -190..230, crossed by Cedar Heights Dr (Colorado Springs)
    (unnamed S1400)   a walkable connector joining the south ends of Juniper Ln and Pine St
    I- 625 (S1100)    y = 400, crosses Academy Blvd without a shared vertex; the builder drops it
    Pine St           a second street of that name ~3 km south, in Fountain, connected to nothing

Doors d01..d27 are placed ~15 m off the centreline. What the 38 people exercise: both sides of a block; the cul-de-sac;
an apartment house (1285 Cedar Heights Dr, units A and B, one stop two doors); two households at one door (1132); one
even number geocoded on the odd side (d07); one household with no coordinates between two geocoded neighbours (d03);
a letter after the house number (1350A); the same house number on the two Pine Sts (514); accented, hyphenated,
apostrophe, Mc and suffix names; a precinct boundary inside the middle block (x = +10, which is also a house-district
boundary); vote codes MC PC AP AC MP PP EP YP QP and blanks; the edges of the eligibility rule (see _PEOPLE).
Presidential-primary (pp), odd-year and pre-2014 columns carry votes that must never be counted.
"""
import argparse
import csv
import hashlib
import hmac
import json
import math
import re
import sqlite3
from collections import namedtuple
from contextlib import closing
from datetime import date
from pathlib import Path

from tools import scores_schema as S

SCHEMA = 'walk-testkit-1'
ORIGIN = (-104.80, 38.85)
AS_OF = date(2026, 9, 4)                  # the day `age` is taken (the source file's date)
SALT = hashlib.sha256(b'cu-geography-v3 walk testkit salt').digest()
PRIMARIES = {'2014p': '2014-06-24', '2016p': '2016-06-28', '2018p': '2018-06-26', '2020p': '2020-06-30',
             '2022p': '2022-06-28', '2024p': '2024-06-25'}
GENERALS = {'2014g': '2014-11-04', '2016g': '2016-11-08', '2018g': '2018-11-06', '2020g': '2020-11-03',
            '2022g': '2022-11-08', '2024g': '2024-11-05'}
ABS_PREFIXES = ('M', 'A')
PRESIDENTIAL = (2012, 2016, 2020, 2024)   # years that have a svi_vh_YYYYpp column
VOTE_COLUMNS = ['svi_vh_%d%s' % (year, kind) for year in range(2012, 2026) for kind in ('p', 'pp', 'g')
                if kind != 'pp' or year in PRESIDENTIAL]
# the real column names; the file holds these and no others
CSV_COLUMNS = ['sii_persist_id', 'sii_statara_id', 'sii_household_id', 'sii_first_name', 'sii_middle_name',
               'sii_last_name', 'sii_name_suffix', 'sii_email', 'sii_email_match_level', 'sii_phone',
               'sii_phone_wireless', 'sii_phone_wireless_match_level', 'sii_dob', 'sii_age', 'sii_gender',
               'sii_address', 'svi_voter_id', 'svi_registration_date', 'svi_registration_date_earliest',
               'svi_registration_address', 'svi_registration_addressrange', 'svi_registration_predirection',
               'svi_registration_streetname', 'svi_registration_suffix', 'svi_registration_postdirection',
               'svi_registration_suitename', 'svi_registration_suiterange', 'svi_registration_city',
               'svi_registration_state', 'svi_registration_zip', 'svi_registration_congressional_district',
               'svi_registration_state_house_district', 'svi_registration_state_senate_district',
               'svi_registration_precinct_code', 'svi_registration_precinct_name', 'svi_registration_county',
               'svi_registration_censusid', 'svi_party_registration'] + VOTE_COLUMNS
DATE_COLUMNS = ('sii_dob', 'svi_registration_date', 'svi_registration_date_earliest')

Case = namedtuple('Case', 'rows expected')


class Stores:
    """Paths and ground truth of one synthetic data directory."""
    def __init__(self, **entries):
        self.__dict__.update(entries)


def opaque_id(salt, kind, *parts):
    """Same construction as tools.build_street.opaque_id (kept local so this module needs no pandas)."""
    message = json.dumps([kind, *parts], ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return kind + '_' + hmac.new(salt, message, hashlib.sha256).hexdigest()


# ---- roads: local metres east / north of ORIGIN --------------------------------------------------------------------
_PHI = math.radians(ORIGIN[1])
M_LAT = 111132.954 - 559.822 * math.cos(2 * _PHI) + 1.175 * math.cos(4 * _PHI)          # metres per degree at ORIGIN
M_LON = 111412.84 * math.cos(_PHI) - 93.5 * math.cos(3 * _PHI) + 0.118 * math.cos(5 * _PHI)

_ROWS = [         # TIGER-style rows: id, FULLNAME, MTFCC, vertices
    (1, 'Cedar Heights Dr', 'S1400', [(-300, 0), (-200, 0), (-100, 0)]),
    (2, 'Cedar Heights Dr', 'S1400', [(-100, 0), (0, 0)]),
    (3, 'Cedar Heights Dr', 'S1400', [(0, 0), (100, 0)]),
    (4, 'Cedar Heights Dr', 'S1400', [(100, 0), (170, 5), (250, 14), (330, 24)]),
    (5, 'Juniper Ln', 'S1400', [(-100, -210), (-100, -105), (-100, 0)]),
    (6, 'Juniper Ln', 'S1400', [(-100, 0), (-100, 55), (-100, 110)]),
    (7, 'Juniper Ln', 'S1400', [(-100, 110), (-100, 210)]),
    (8, 'Ridgeview Ct', 'S1400', [(0, 110), (-100, 110)]),
    (9, 'Pine St', 'S1400', [(100, 230), (100, 115), (100, 0)]),
    (10, 'Pine St', 'S1400', [(100, 0), (100, -95), (100, -190)]),
    (11, 'Academy Blvd', 'S1200', [(330, -450), (330, 24)]),
    (12, 'Academy Blvd', 'S1200', [(330, 24), (330, 450)]),
    (13, '', 'S1400', [(-100, -210), (-50, -222), (0, -224), (50, -214), (100, -190)]),
    (14, 'I- 625', 'S1100', [(-500, 400), (0, 402), (500, 400)]),
    (15, 'Pine St', 'S1400', [(750, -3200), (600, -3200), (450, -3200)]),
]
DROPPED_ROWS = (14,)                       # MTFCC S1100

# block: (street, road rows end to end, side of the centreline holding even numbers; None: no door, nothing to assert)
_BLOCKS = {
    'chd1': ('chd', (1,), 'N'), 'chd2': ('chd', (2, 3), 'N'), 'chd3': ('chd', (4,), 'N'),
    'jun_s': ('jun', (5,), 'E'), 'jun_n1': ('jun', (6,), 'E'), 'jun_n2': ('jun', (7,), None),
    'rdg': ('rdg', (8,), 'N'),
    'pine_n': ('pine', (9,), 'E'), 'pine_s': ('pine', (10,), None),
    'acad_s': (None, (11,), None), 'acad_n': (None, (12,), None), 'conn': (None, (13,), None),
    'pine_f': ('pinef', (15,), 'N'),
}
_STREETS = {      # key: (label, streetname, suffix, city, zip)
    'chd': ('Cedar Heights Dr', 'CEDAR HEIGHTS', 'DR', 'COLORADO SPRINGS', '80919'),
    'jun': ('Juniper Ln', 'JUNIPER', 'LN', 'COLORADO SPRINGS', '80919'),
    'rdg': ('Ridgeview Ct', 'RIDGEVIEW', 'CT', 'COLORADO SPRINGS', '80919'),
    'pine': ('Pine St', 'PINE', 'ST', 'COLORADO SPRINGS', '80920'),
    'pinef': ('Pine St', 'PINE', 'ST', 'FOUNTAIN', '80817'),
}
_COMPASS = {'N': (0, 1), 'S': (0, -1), 'E': (1, 0), 'W': (-1, 0)}
_CENSUS_ID = {name: '08041%06d%04d' % (1, 1000 + i) for i, name in enumerate(_BLOCKS)}    # 15 digits per road block


def lonlat(x, y):
    return round(ORIGIN[0] + x / M_LON, 6), round(ORIGIN[1] + y / M_LAT, 6)


def road_rows():
    """The `roads` table of roads.sqlite3 as (id, name, kind, geometry): FULLNAME, MTFCC, a GeoJSON LineString as text."""
    return [(ident, name, kind, json.dumps({'type': 'LineString', 'coordinates': [list(lonlat(*p)) for p in points]},
                                          separators=(',', ':'))) for ident, name, kind, points in _ROWS]


def _polyline(rows):
    points = []
    for ident in rows:
        for p in next(pts for i, _, _, pts in _ROWS if i == ident):
            if not points or points[-1] != p:
                points.append(p)
    return points


def _point_at(points, s):
    """(x, y, tx, ty): the point s metres along the polyline and the unit tangent there."""
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        length = math.hypot(x1 - x0, y1 - y0)
        if s <= length:
            return x0 + (x1 - x0) * s / length, y0 + (y1 - y0) * s / length, (x1 - x0) / length, (y1 - y0) / length
        s -= length
    raise ValueError('Beyond the end of the block')


def _beside(block, s, side, offset):
    """lon/lat of the point `offset` metres from the centreline of `block`, `s` metres along it, on a compass side."""
    x, y, tx, ty = _point_at(_polyline(_BLOCKS[block][1]), s)
    nx, ny = -ty, tx
    if nx * _COMPASS[side][0] + ny * _COMPASS[side][1] < 0:
        nx, ny = -nx, -ny
    return lonlat(x + nx * offset, y + ny * offset)


def _left_of(direction, compass):
    """1 when the compass vector lies to the left of `direction`, else 0."""
    return 1 if direction[0] * compass[1] - direction[1] * compass[0] > 0 else 0


# ---- doors and people ---------------------------------------------------------------------------------------------
_AREAS = {'W': ('5200021301', '010', '018'),      # (precinct code, state senate, state house)
          'E': ('5200021302', '010', '019'),
          'F': ('5200021417', '011', '021')}
_DOORS = [        # key, street, block, metres along the block (None: no coordinates), side, metres off, house, unit, area
    ('d01', 'chd', 'chd1', 24, 'N', 15, '1112', '', 'W'),
    ('d02', 'chd', 'chd1', 64, 'N', 16, '1132', '', 'W'),
    ('d03', 'chd', 'chd1', None, None, 0, '1136', '', 'W'),         # no coordinates, between 1132 and 1152
    ('d04', 'chd', 'chd1', 104, 'N', 15, '1152', '', 'W'),
    ('d05', 'chd', 'chd1', 38, 'S', 15, '1119', '', 'W'),
    ('d06', 'chd', 'chd1', 78, 'S', 16, '1139', '', 'W'),
    ('d07', 'chd', 'chd1', 132, 'S', 15, '1166', '', 'W'),         # an even number geocoded on the odd side
    ('d08', 'chd', 'chd2', 24, 'N', 15, '1212', '', 'W'),
    ('d09', 'chd', 'chd2', 64, 'N', 14, '1232', '', 'W'),
    ('d10', 'chd', 'chd2', 38, 'S', 16, '1219', '', 'W'),
    ('d11', 'chd', 'chd2', 144, 'N', 15, '1272', '', 'E'),
    ('d12', 'chd', 'chd2', 170, 'S', 17, '1285', 'A', 'E'),        # one apartment house: one stop, two doors
    ('d13', 'chd', 'chd2', 170, 'S', 17, '1285', 'B', 'E'),
    ('d14', 'chd', 'chd3', 36, 'N', 15, '1316', '', 'E'),
    ('d15', 'chd', 'chd3', 116, 'N', 15, '1350A', '', 'E'),
    ('d16', 'chd', 'chd3', 76, 'S', 16, '1333', '', 'E'),
    ('d17', 'jun', 'jun_s', 60, 'E', 15, '204', '', 'W'),
    ('d18', 'jun', 'jun_s', 95, 'W', 14, '211', '', 'W'),
    ('d19', 'jun', 'jun_n1', 60, 'E', 15, '312', '', 'W'),
    ('d20', 'rdg', 'rdg', 75, 'N', 15, '102', '', 'W'),            # s counts from the closed end of the cul-de-sac
    ('d21', 'rdg', 'rdg', 73, 'S', 15, '105', '', 'W'),
    ('d22', 'rdg', 'rdg', 20, 'N', 16, '110', '', 'W'),
    ('d23', 'rdg', 'rdg', 18, 'S', 15, '111', '', 'W'),
    ('d24', 'pine', 'pine_n', 130, 'E', 15, '514', '', 'E'),       # s counts from the north end
    ('d25', 'pine', 'pine_n', 80, 'W', 16, '521', '', 'E'),
    ('d26', 'pinef', 'pine_f', 230, 'N', 15, '514', '', 'F'),      # s counts from the east end
    ('d27', 'pinef', 'pine_f', 110, 'S', 15, '523', '', 'F'),
]
# a person registered at one address and living at another: key -> (sii_address, x, y)
_RESIDENCE = {'p06': ('4412 OAKMONT PL', -5200, 1800), 'p11': ('880 COLLEGE WAY APT 4C', 6200, 3100)}

# key, door, household, first, middle, last, suffix, displayed name, dob, gender, party, registered, votes.
# votes: p14 = svi_vh_2014p, g20 = svi_vh_2020g, pp20 = svi_vh_2020pp; anything not listed is blank (did not vote).
_PEOPLE = [
    ('p01', 'd01', 1, 'HAROLD', 'T', 'WEATHERBY', '', 'Harold T Weatherby', '1950-02-11', 'M', 'R', '1988-09-20',
     'p14=PC p16=PC p18=PC p20=MC p22=MC p24=MC g14=PC g16=PC g18=MC g20=MC g22=MC g24=MC'),
    ('p02', 'd01', 1, 'MARGARET', 'A', 'WEATHERBY', '', 'Margaret A Weatherby', '1952-07-30', 'F', 'R', '1988-09-20',
     'p16=AP p20=MC p24=MC g14=PC g16=AP g18=AP g20=MC g22=PC g24=MC'),
    ('p03', 'd02', 2, 'JOSÉ', '', 'MUÑOZ', '', 'José Muñoz', '1976-12-05', 'M', 'D', '2004-10-12',
     'p18=MC p22=MC p24=MC g14=EP g16=PC g18=MC g20=MC g22=PC g24=MC'),
    ('p04', 'd02', 2, 'ELENA', '', 'MUÑOZ', '', 'Elena Muñoz', '1979-03-22', 'F', 'D', '2004-10-12',
     'p20=MC p24=MC g16=MC g18=MC g20=MC g22=MC g24=MC'),
    ('p05', 'd02', 3, 'TOBIAS', 'R', 'HALVORSEN', '', 'Tobias R Halvorsen', '1995-04-27', 'M', 'U', '2013-10-09',
     'p24=PC g16=PC g20=MC g24=MC'),
    ('p06', 'd03', 4, 'DONALD', '', 'MCALLISTER', 'JR', 'Donald McAllister JR', '1968-05-09', 'M', 'U', '1990-02-13',
     'p24=PC g18=PC g20=MC g24=MC'),
    ('p07', 'd04', 5, 'GWENDOLYN', 'A', 'LEE-PARK', '', 'Gwendolyn A Lee-Park', '1989-09-17', 'F', 'D', '2008-10-20',
     'p16=MC p18=MC p20=MC p22=MC p24=MC g16=MC g18=MC g20=MC g22=MC g24=MC'),
    ('p08', 'd05', 6, 'ZOË', '', 'BRONTË', '', 'Zoë Brontë', '1991-08-17', 'F', 'U', '2012-03-01',
     'g16=PC g20=MC g22=MC g24=MC g12=PC pp16=PC pp20=MC g19=MC g21=MC g23=MC'),
    ('p09', 'd06', 7, 'PRIYA', 'N', 'RAMASWAMY', '', 'Priya N Ramaswamy', '1985-11-30', 'F', 'D', '2006-09-01',
     'p14=PC p16=PC p18=PC p20=MC p22=MC p24=MC g14=PC g16=PC g18=PC g20=MC g22=MC g24=MC'),
    ('p10', 'd06', 7, 'ARJUN', '', 'RAMASWAMY', '', 'Arjun Ramaswamy', '1983-04-04', 'M', 'D', '2006-09-01',
     'p20=MC p22=MC g14=PC g16=PC g18=PC g20=MC g22=MC g24=MC'),
    # 17 at the 2020 primary, 18 on 2020-10-12: eligible for it only because he voted in it
    ('p11', 'd06', 7, 'ISHAAN', '', 'RAMASWAMY', '', 'Ishaan Ramaswamy', '2002-10-12', 'M', 'U', '2020-02-01',
     'p20=MC g20=MC g22=PC g24=MC'),
    ('p12', 'd07', 8, 'BEATRIZ', '', "O'BRIEN", '', "Beatriz O'Brien", '1944-01-19', 'F', 'R', '1976-05-04',
     'p14=PC p16=PC p18=PC p20=PC p22=PC p24=PC g14=PC g16=PC g18=PC g20=PC g22=PC g24=PC g12=PC'),
    ('p13', 'd08', 9, 'LINH', 'T', 'NGUYEN-PARK', '', 'Linh T Nguyen-Park', '1983-06-14', 'F', 'D', '2010-10-04',
     'p18=MC p20=MC p22=AC p24=MC g16=MC g18=MC g20=MC g22=MC g24=MC pp20=MC'),
    ('p14', 'd08', 9, 'MARCUS', '', 'NGUYEN-PARK', '', 'Marcus Nguyen-Park', '1981-09-03', 'M', 'D', '2010-10-04',
     'g16=MC g20=MC g24=MC'),
    ('p15', 'd09', 10, 'GLORIA', '', 'ESPINOZA', '', 'Gloria Espinoza', '1957-10-08', 'F', 'D', '1999-10-12',
     'p14=PC p16=PC p18=MC p20=MC p22=MC p24=MC g14=PC g16=MC g18=MC g20=MC g22=MC g24=MC pp16=PC pp20=MC'),
    ('p16', 'd10', 11, 'CALVIN', 'R', 'PEMBERTON', '', 'Calvin R Pemberton', '1972-02-27', 'M', 'L', '2001-06-18',
     'g14=YP g16=QP g18=PC g20=MC g22=EP g24=MC'),
    ('p17', 'd11', 12, 'WALTER', 'B', 'STRICKLAND', 'III', 'Walter B Strickland III', '1946-12-21', 'M', 'R',
     '1982-03-30', 'p14=PC p16=PC p18=PC p20=PC p22=PC p24=MC g14=PC g16=PC g18=PC g20=MC g22=MC g24=MC'),
    ('p18', 'd11', 12, 'DOROTHY', 'M', 'STRICKLAND', '', 'Dorothy M Strickland', '1948-04-02', 'F', 'R', '1982-03-30',
     'p22=PC g14=PC g16=PC g18=PC g20=MC g22=MC g24=MC'),
    # registered 2017-09-05: first eligible for the 2018 primary and general
    ('p19', 'd12', 13, 'SAMIRA', 'K', 'HOSSEINI', '', 'Samira K Hosseini', '1999-02-14', 'F', 'U', '2017-09-05',
     'p20=MC g18=MC g20=MC g22=MC g24=MC'),
    ('p20', 'd13', 14, 'DEVON', 'A', 'BAPTISTE', '', 'Devon A Baptiste', '1993-07-25', 'M', 'D', '2014-08-12',
     'p24=MC g16=PC g18=PC g20=MC g22=PC g24=MC'),
    ('p21', 'd14', 15, 'RUTH', 'A', 'GOLDSTEIN', '', 'Ruth A Goldstein', '1962-09-29', 'F', 'D', '1991-11-05',
     'p18=PC p20=MC p22=MC g14=PC g16=PC g18=MC g20=MC g22=MC g24=MC'),
    ('p22', 'd15', 16, 'KEVIN', 'J', 'OKAFOR', '', 'Kevin J Okafor', '1989-01-18', 'M', 'R', '2007-10-11',
     'p14=PC p16=PC g16=PC g20=MC g22=PC g24=MC'),
    # turned 18 and registered on the 2020 general election day, did not vote then: eligible for it on both dates
    ('p23', 'd16', 17, 'NADIA', 'R', 'PETROVA', '', 'Nadia R Petrova', '2002-11-03', 'F', 'U', '2020-11-03',
     'p24=PC g22=MC g24=MC'),
    # registered in 2019 but voted in 2016 and 2018 (moved and re-registered): eligible for those two by voting
    ('p24', 'd17', 18, 'CARMEN', '', 'VELASQUEZ', '', 'Carmen Velasquez', '1971-03-08', 'F', 'D', '2019-03-20',
     'g16=PC g18=MC p20=MC g20=MC p22=MC g22=MC p24=MC g24=MC'),
    # registered in 2025: no election of the window is eligible, tallies "/"; the 2025 vote is outside it
    ('p25', 'd18', 19, 'AVERY', 'J', 'LINDQVIST', '', 'Avery J Lindqvist', '1999-05-20', 'U', 'O', '2025-03-14',
     'g25=MC'),
    ('p26', 'd19', 20, 'ODALYS', '', 'FERNÁNDEZ', '', 'Odalys Fernández', '1978-07-12', 'F', 'D', '2000-10-17',
     'g16=MC g20=MC g22=MC g24=MC pp20=MC pp24=PC'),
    ('p27', 'd20', 21, 'THEODORA', 'K', 'ABERNATHY', '', 'Theodora K Abernathy', '1955-01-31', 'F', 'R', '1984-10-02',
     'p20=MC p22=MC p24=MC g14=PC g16=PC g18=PC g20=MC g22=MC'),
    ('p28', 'd21', 22, 'FELIX', '', 'ADEYEMI', '', 'Felix Adeyemi', '1987-10-22', 'M', 'D', '2016-10-18',
     'p18=MC p20=MC p24=MC g16=MC g18=MC g20=MC g22=MC g24=MC'),
    ('p29', 'd21', 22, 'MEILING', '', 'CHEN', '', 'Meiling Chen', '1990-05-05', 'F', 'U', '2016-10-18',
     'g16=PC g18=PC g20=MC g22=PC g24=MP'),
    ('p30', 'd22', 23, 'RAYMOND', 'E', 'KOWALSKI', 'SR', 'Raymond E Kowalski SR', '1959-06-17', 'M', 'R', '1990-10-09',
     'g14=PC g16=PC g18=PP g20=PP g24=PC'),
    ('p31', 'd23', 24, 'JAMAL', 'D', 'WASHINGTON', '', 'Jamal D Washington', '1996-03-09', 'M', 'D', '2014-04-01',
     'p16=AC p20=MC p24=AP g14=MC g16=AC g18=MC g20=MC g24=MC'),
    ('p32', 'd24', 25, 'HELEN', 'S', 'ROSENBAUM', '', 'Helen S Rosenbaum', '1941-08-25', 'F', 'D', '1972-04-11',
     'p14=PC p16=PC p18=PC p20=AP p22=AP p24=AP g14=PC g16=PC g18=PC g20=AP g22=AP g24=AP'),
    ('p33', 'd24', 25, 'LEONARD', '', 'ROSENBAUM', '', 'Leonard Rosenbaum', '1939-02-02', 'M', 'R', '1972-04-11',
     'g16=AP g20=AP g24=AP'),
    ('p34', 'd25', 26, 'OLUWASEUN', 'O', 'ADEBAYO', '', 'Oluwaseun O Adebayo', '1992-11-11', 'F', 'D', '2018-09-04',
     'p20=MC p22=MC p24=MC g18=MC g20=MC g22=MC g24=MC'),
    ('p35', 'd26', 27, 'IGNACIO', '', 'BELTRÁN', '', 'Ignacio Beltrán', '1980-12-12', 'M', 'G', '2002-10-08',
     'p16=PC p20=PC g14=PC g16=PC g18=PC g20=PC g22=PC g24=PC'),
    ('p36', 'd27', 28, 'ADAEZE', 'N', 'OBI', '', 'Adaeze N Obi', '1998-01-29', 'F', 'D', '2016-02-02',
     'p16=MC p18=MC p20=MC p22=MC p24=MC g16=MC g18=MC g20=MC g22=MC g24=MC'),
    ('p37', 'd27', 28, 'QUENTIN', 'P', 'FAIRCHILD', '', 'Quentin P Fairchild', '1969-07-04', 'M', 'T', '1994-09-27',
     'g20=MC g24=MC'),
    # turns 18 the day after the 2020 general: not eligible for it, though registered
    ('p38', 'd26', 27, 'NOELLE', 'R', 'CASTELLANOS', '', 'Noelle R Castellanos', '2002-11-04', 'F', 'U', '2019-12-20',
     'p22=PC g22=MC g24=MC'),
]
# Hand-computed from the rule: eligible = (registered on or before the election day and 18 on it) or voted in it, over
# svi_vh_YYYYp and svi_vh_YYYYg of 2014..2024 only; abs counts votes whose code starts with M or A, out of pri + gen
# eligible. key: (pri voted, pri eligible, gen voted, gen eligible, abs voted, abs eligible)
_TALLIES = {
    'p01': (6, 6, 6, 6, 7, 12), 'p02': (3, 6, 6, 6, 7, 12), 'p03': (3, 6, 6, 6, 6, 12), 'p04': (2, 6, 5, 6, 7, 12),
    'p05': (1, 6, 3, 6, 2, 12), 'p06': (1, 6, 3, 6, 2, 12), 'p07': (5, 6, 5, 6, 10, 12), 'p08': (0, 6, 4, 6, 3, 12),
    'p09': (6, 6, 6, 6, 6, 12), 'p10': (2, 6, 6, 6, 5, 12), 'p11': (1, 3, 3, 3, 3, 6), 'p12': (6, 6, 6, 6, 0, 12),
    'p13': (4, 6, 5, 6, 9, 12), 'p14': (0, 6, 3, 6, 3, 12), 'p15': (6, 6, 6, 6, 9, 12), 'p16': (0, 6, 6, 6, 2, 12),
    'p17': (6, 6, 6, 6, 4, 12), 'p18': (1, 6, 6, 6, 3, 12), 'p19': (1, 4, 4, 4, 5, 8), 'p20': (1, 5, 5, 6, 3, 11),
    'p21': (3, 6, 6, 6, 6, 12), 'p22': (2, 6, 4, 6, 2, 12), 'p23': (1, 2, 2, 3, 2, 5), 'p24': (3, 3, 5, 5, 7, 8),
    'p25': (0, 0, 0, 0, 0, 0), 'p26': (0, 6, 4, 6, 4, 12), 'p27': (3, 6, 5, 6, 5, 12), 'p28': (3, 4, 5, 5, 8, 9),
    'p29': (0, 4, 5, 5, 2, 9), 'p30': (0, 6, 5, 6, 0, 12), 'p31': (3, 6, 5, 6, 8, 12), 'p32': (6, 6, 6, 6, 6, 12),
    'p33': (0, 6, 3, 6, 3, 12), 'p34': (3, 3, 4, 4, 7, 7), 'p35': (2, 6, 6, 6, 0, 12), 'p36': (5, 5, 5, 5, 10, 10),
    'p37': (0, 6, 2, 6, 2, 12), 'p38': (1, 2, 2, 2, 2, 4),
}


def _votes(spec):
    out = {}
    for token in spec.split():
        label, code = token.split('=')
        kind = 'pp' if label.startswith('pp') else label[0]
        out['svi_vh_%d%s' % (2000 + int(label[len(kind):]), kind)] = code
    return out


def _phones(n):
    """(landline, wireless): both, either or neither by n, all numbers 719-555-01xx."""
    return ('(719) 555-%04d' % (100 + n) if n % 4 in (0, 2) else '',
            '719-555-%04d' % (150 + n) if n % 4 in (1, 2) else '')


def _address(house, name, suffix, unit):
    return ' '.join(p for p in (house, name, suffix, 'APT ' + unit if unit else '') if p)


def _age(on, born):
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


def _place(row, basis):
    """(lon, lat, address) of a row on a street-store basis; lon is None when the store has no such household."""
    c = row['csv']
    if basis == 'registration':
        return row['lon'], row['lat'], c['svi_registration_address']
    return row['res_lon'], row['res_lat'], c['sii_address']


def _household_id(row, basis):
    """The id tools.build_street gives the household of this row on this basis (None: not in the store)."""
    lon, lat, address = _place(row, basis)
    if lon is None:
        return None
    return opaque_id(SALT, 'h', basis, row['csv']['sii_household_id'], address.casefold(), lon, lat)


def _door(entry):
    key, street, block, s, side, offset, house, unit, area = entry
    number = int(re.match(r'\d+', house).group())
    return {'key': key, 'street': street, 'block': block, 's': s, 'side': side, 'house': house, 'house_int': number,
            'unit': unit, 'area': area, 'parity': 'even' if number % 2 == 0 else 'odd',
            'lonlat': None if s is None else _beside(block, s, side, offset)}


def _rows(doors):
    rows = []
    for n, (key, door_key, hh, first, middle, last, suffix, _, dob, gender, party, reg, votes) in enumerate(_PEOPLE, 1):
        door = doors[door_key]
        _, name, road_suffix, city, zip_code = _STREETS[door['street']]
        precinct, ss, sh = _AREAS[door['area']]
        address = _address(door['house'], name, road_suffix, door['unit'])
        landline, wireless = _phones(n)
        residence = _RESIDENCE.get(key)
        lon, lat = door['lonlat'] or (None, None)
        res_lon, res_lat = lonlat(*residence[1:]) if residence else (lon, lat)
        age = _age(AS_OF, date.fromisoformat(dob))
        record = dict.fromkeys(CSV_COLUMNS, '')
        record.update({
            'sii_persist_id': '880%06d' % (n * 7001), 'sii_statara_id': '990%06d' % (n * 7001),
            'sii_household_id': 'SYNHH%03d' % hh, 'sii_first_name': first, 'sii_middle_name': middle,
            'sii_last_name': last, 'sii_name_suffix': suffix,
            'sii_email': 'voter%02d@example.invalid' % n if n % 3 == 0 else '',
            'sii_email_match_level': 'A' if n % 3 == 0 else '', 'sii_phone': landline, 'sii_phone_wireless': wireless,
            'sii_phone_wireless_match_level': 'A' if wireless else '', 'sii_dob': dob, 'sii_age': str(age),
            'sii_gender': gender, 'sii_address': residence[0] if residence else address,
            'svi_voter_id': '91%07d' % (n * 7919), 'svi_registration_date': reg,
            'svi_registration_date_earliest': reg, 'svi_registration_address': address,
            'svi_registration_addressrange': door['house'], 'svi_registration_streetname': name,
            'svi_registration_suffix': road_suffix, 'svi_registration_suitename': 'APT' if door['unit'] else '',
            'svi_registration_suiterange': door['unit'], 'svi_registration_city': city,
            'svi_registration_state': 'CO', 'svi_registration_zip': zip_code,
            'svi_registration_congressional_district': '005', 'svi_registration_state_house_district': sh,
            'svi_registration_state_senate_district': ss, 'svi_registration_precinct_code': precinct,
            'svi_registration_precinct_name': precinct, 'svi_registration_county': '08041',
            'svi_registration_censusid': _CENSUS_ID[door['block']], 'svi_party_registration': party})
        record.update(_votes(votes))
        rows.append({'key': key, 'door': door_key, 'household': hh,
                     'person_id': opaque_id(SALT, 'p', record['sii_persist_id']), 'csv': record,
                     'lon': lon, 'lat': lat, 'res_lon': res_lon, 'res_lat': res_lat, 'party': party, 'age': age})
    return rows


def hand_case():
    """Case(rows, expected). rows: one dict per source-CSV line, in file order (a fixed shuffle): key, door, household,
    person_id, csv {real column name: text, dates ISO}, lon/lat (None: no registration coordinates, so not in
    street.sqlite3), res_lon/res_lat (residential basis), party and age (the scores store). expected: the oracle,
    JSON-serialisable; its parts are described at _expected."""
    doors = {entry[0]: _door(entry) for entry in _DOORS}
    rows = _rows(doors)
    rows.sort(key=lambda r: hashlib.sha256(b'walk-testkit-row:' + r['key'].encode()).hexdigest())
    return Case(rows, _expected(rows, doors))


def _people_out(rows):
    names = {p[0]: p[7] for p in _PEOPLE}
    out = {}
    for r in rows:
        c, tally = r['csv'], _TALLIES[r['key']]
        entry = {'key': r['key'], 'name': names[r['key']], 'door': r['door'], 'household': r['household'],
                 'age': r['age'], 'gender': c['sii_gender'], 'party': r['party'], 'dob': c['sii_dob'],
                 'reg_date': c['svi_registration_date'], 'precinct': c['svi_registration_precinct_code'],
                 'ss': c['svi_registration_state_senate_district'], 'sh': c['svi_registration_state_house_district'],
                 'phone': c['sii_phone'], 'wireless': c['sii_phone_wireless'], 'voter_id': c['svi_voter_id'],
                 'street_household_id': _household_id(r, 'registration')}
        for i, label in enumerate(('pri', 'gen', 'abs')):
            voted, eligible = tally[2 * i], tally[2 * i + 1]
            entry[label] = {'voted': voted, 'eligible': eligible}
            entry[label + '_text'] = '%d/%d' % (voted, eligible) if eligible else '/'     # no eligible election: "/"
        out[r['person_id']] = entry
    return out


def _doors_out(doors, members):
    opposite = {'N': 'S', 'S': 'N', 'E': 'W', 'W': 'E'}
    stops, first, out = {}, {}, {}
    for key, door in doors.items():
        label, name, suffix, city, zip_code = _STREETS[door['street']]
        precinct, ss, sh = _AREAS[door['area']]
        stop = first.setdefault((door['street'], door['house']), key)
        stops.setdefault(stop, []).append(key)
        geocoded = door['s'] is not None
        evens = _BLOCKS[door['block']][2]
        want = (evens if door['parity'] == 'even' else opposite[evens]) if geocoded else None
        out[key] = {
            'street': label, 'streetname': name, 'suffix': suffix, 'city': city, 'zip': zip_code,
            'house': door['house'], 'house_int': door['house_int'], 'unit': door['unit'],
            'suite': 'APT' if door['unit'] else '', 'parity': door['parity'], 'block': door['block'], 'stop': stop,
            'geocoded': geocoded, 'placed_by': 'geocode' if geocoded else 'interpolated',
            'lonlat': list(door['lonlat']) if geocoded else None, 'side': door['side'], 'parity_side': want,
            'side_matches_parity': door['side'] == want if geocoded else None,
            'precinct': precinct, 'ss': ss, 'sh': sh, 'people': [r['person_id'] for r in members[key]],
            'households': len({r['household'] for r in members[key]})}
    return out, stops


def _interpolation(doors):
    """The household without coordinates goes between its geocoded neighbours of the same street and parity,
    in proportion to the house numbers. Returns the expectation and the metres along the block of every door."""
    lost = next(d for d in doors.values() if d['s'] is None)
    same = [d for d in doors.values() if d['s'] is not None and d['street'] == lost['street']
            and d['parity'] == lost['parity']]
    before = max((d for d in same if d['house_int'] < lost['house_int']), key=lambda d: d['house_int'])
    after = min((d for d in same if d['house_int'] > lost['house_int']), key=lambda d: d['house_int'])
    if not before['block'] == after['block'] == lost['block']:
        raise ValueError('The neighbours must share the block')
    fraction = (lost['house_int'] - before['house_int']) / (after['house_int'] - before['house_int'])
    s = before['s'] + fraction * (after['s'] - before['s'])
    x, y, _, _ = _point_at(_polyline(_BLOCKS[lost['block']][1]), s)
    spots = {k: d['s'] for k, d in doors.items() if d['s'] is not None}
    spots[lost['key']] = s
    order = sorted((k for k, d in doors.items() if d['block'] == lost['block'] and d['parity'] == lost['parity']),
                   key=spots.get)
    return {'door': lost['key'], 'block': lost['block'], 'before': before['key'], 'after': after['key'],
            'fraction': round(fraction, 6), 's_m': round(s, 6), 'centreline': list(lonlat(x, y)),
            'order': order, 'placed_by': 'interpolated'}, spots


def _blocks_out(doors, door_out, spots):
    blocks = []
    for name, (street, row_ids, evens) in _BLOCKS.items():
        points = _polyline(row_ids)
        inside = sorted((k for k, d in doors.items() if d['block'] == name), key=spots.get)
        known = [k for k in inside if door_out[k]['geocoded']]
        wrong = [k for k in known if door_out[k]['side_matches_parity'] is False]
        _, road, kind, _ = next(r for r in _ROWS if r[0] == row_ids[0])
        blocks.append({
            'id': name, 'name': road, 'kind': kind, 'street': _STREETS[street][0] if street else None,
            'city': _STREETS[street][3] if street else None, 'rows': list(row_ids),
            'from': list(lonlat(*points[0])), 'to': list(lonlat(*points[-1])),
            'length_m': round(sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:])), 3),
            'even_side': evens if known else None,
            'even_normal': list(_COMPASS[evens]) if known else None,
            'even_left': _left_of((points[-1][0] - points[0][0], points[-1][1] - points[0][1]), _COMPASS[evens])
            if known else None,
            'doors': inside, 'mismatched': wrong, 'consistent': len(known) - len(wrong), 'inconsistent': len(wrong)})
    return blocks


def _street_tables(doors, members):
    """Per street: counts, the runs (street, parity, precinct; doors in house-number order) and the (ss, sh, precinct)
    counts."""
    streets, runs, combos = [], [], {}
    for key, (label, name, suffix, city, zip_code) in _STREETS.items():
        mine = sorted((d for d in doors.values() if d['street'] == key),
                      key=lambda d: (d['house_int'], d['house'], d['unit']))

        def people(ds):
            return sum(len(members[d['key']]) for d in ds)
        entry = {'street': label, 'streetname': name, 'suffix': suffix, 'city': city, 'zip': zip_code,
                 'doors': len(mine), 'people': people(mine)}
        for parity in ('even', 'odd'):
            side = [d for d in mine if d['parity'] == parity]
            entry['doors_' + parity], entry['people_' + parity] = len(side), people(side)
            for d in side:
                precinct = _AREAS[d['area']][0]
                if runs and (runs[-1]['street'], runs[-1]['city'], runs[-1]['parity'],
                             runs[-1]['precinct']) == (label, city, parity, precinct):
                    runs[-1]['doors'].append(d['key'])
                else:
                    runs.append({'street': label, 'city': city, 'parity': parity, 'precinct': precinct,
                                 'doors': [d['key']]})
        for d in mine:
            precinct, ss, sh = _AREAS[d['area']]
            combo = combos.setdefault((ss, sh, precinct, label, city), {'people': 0, 'doors': 0})
            combo['people'] += len(members[d['key']])
            combo['doors'] += 1
        streets.append(entry)
    return streets, runs, [{'ss': k[0], 'sh': k[1], 'precinct': k[2], 'street': k[3], 'city': k[4], **v}
                           for k, v in sorted(combos.items())]


def _topology():
    kept = [pts for i, _, _, pts in _ROWS if i not in DROPPED_ROWS]
    degree, parent = {}, {}
    for pts in kept:
        for p in (pts[0], pts[-1]):
            degree[p] = degree.get(p, 0) + 1
            parent.setdefault(p, p)

    def find(p):
        while parent[p] != p:
            p = parent[p]
        return p
    for pts in kept:
        parent[find(pts[0])] = find(pts[-1])
    return {'kept_rows': [i for i, _, _, _ in _ROWS if i not in DROPPED_ROWS], 'dropped_rows': list(DROPPED_ROWS),
            'nodes': [{'lonlat': list(lonlat(*p)), 'degree': d} for p, d in sorted(degree.items())],
            'components': len({find(p) for p in degree}), 'spans': len(_BLOCKS)}


def _expected(rows, doors):
    """The oracle, JSON-serialisable. Tallies are the hand-computed literals; sides and positions follow from where
    the doors were put; nothing is read back from a builder.
      people        {person_id: key, name (as displayed), door, age, gender, party, dob, reg_date, precinct, ss, sh,
                     phone, wireless, voter_id, street_household_id (registration basis, None without coordinates),
                     pri/gen/abs {voted, eligible}, pri_text/gen_text/abs_text ("v/e", "/" if e == 0)}
      doors         {door key: street, house, unit, parity, block, stop, placed_by, lonlat, side (compass side of the
                     centreline), parity_side (where the block puts that parity), side_matches_parity, precinct, ss,
                     sh, people, households}
      stops         {stop key: [door keys]}  (a stop is one house number on one street instance)
      blocks        one per junction-to-junction span: road rows, from/to (lon/lat in the drawn direction),
                     even_side / even_normal / even_left (None without a geocoded door), doors by position, mismatched
      interpolated  the door without coordinates: before / after neighbours, fraction, s_m from the block's start
      streets, runs, combos, topology, counts, keys (person key -> id), row_of (person key -> CSV row index)."""
    members = {key: [r for r in rows if r['door'] == key] for key in doors}
    door_out, stops = _doors_out(doors, members)
    interpolated, spots = _interpolation(doors)
    streets, runs, combos = _street_tables(doors, members)
    return {
        'schema': SCHEMA, 'as_of': AS_OF.isoformat(),
        'window': {'primaries': dict(PRIMARIES), 'generals': dict(GENERALS)}, 'abs_prefixes': list(ABS_PREFIXES),
        'counts': {'people': len(rows), 'households': len({r['household'] for r in rows}), 'doors': len(doors),
                   'stops': len(stops), 'geocoded_people': sum(1 for r in rows if r['lon'] is not None),
                   'ungeocoded_people': sum(1 for r in rows if r['lon'] is None), 'streets': len(_STREETS),
                   'spans': len(_BLOCKS), 'precincts': len(_AREAS)},
        'keys': {r['key']: r['person_id'] for r in rows}, 'row_of': {r['key']: i for i, r in enumerate(rows)},
        'people': _people_out(rows), 'doors': door_out, 'stops': stops,
        'blocks': _blocks_out(doors, door_out, spots), 'interpolated': interpolated,
        'streets': streets, 'runs': runs, 'combos': combos, 'topology': _topology()}


def even_left_for(block, a, b):
    """1 when even numbers are on the left walking from lon/lat `a` to `b` along `block` (an expected['blocks'] entry),
    else 0; None when the block has no door to say. Lets a test judge a span's even_left whichever way it is drawn."""
    if block['even_normal'] is None:
        return None
    return _left_of(((b[0] - a[0]) * M_LON, (b[1] - a[1]) * M_LAT), block['even_normal'])


# ---- the source CSV and the stores -------------------------------------------------------------------------------
_CONTACTS_DDL = '''
    CREATE TABLE contacts (id TEXT PRIMARY KEY, name TEXT, email TEXT, email_match TEXT,
        phone TEXT, wireless TEXT, wireless_match TEXT, registration TEXT, residential TEXT);
    CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
'''
_STREET_DDL = '''
    CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE households (
        id TEXT PRIMARY KEY,
        basis TEXT NOT NULL CHECK(basis IN ('registration','residential')),
        longitude REAL NOT NULL, latitude REAL NOT NULL,
        address TEXT NOT NULL, count INTEGER NOT NULL CHECK(count > 0)
    );
    CREATE TABLE people (
        household_id TEXT NOT NULL REFERENCES households(id),
        id TEXT NOT NULL, name TEXT NOT NULL,
        PRIMARY KEY (household_id, id)
    );
'''
_ROADS_DDL = '''CREATE TABLE roads (id INTEGER PRIMARY KEY,name TEXT,kind TEXT,geometry TEXT);
    CREATE VIRTUAL TABLE extents USING rtree(id,west,east,south,north);'''
_SCORES_COLUMNS = ('pos', 'person_id', 'hh', 'age', 'party', 'reg_precinct')
NAME_FIELDS = ('sii_first_name', 'sii_middle_name', 'sii_last_name', 'sii_name_suffix')


def _fresh(path):
    for suffix in ('', '-journal', '-wal', '-shm'):
        Path(str(path) + suffix).unlink(missing_ok=True)
    return path


def _name(csv_row):
    return ' '.join(csv_row[f] for f in NAME_FIELDS if csv_row[f])


def _meta(connection, values):
    connection.executemany('INSERT INTO metadata VALUES (?,?)', [(k, json.dumps(v)) for k, v in values.items()])


def write_source_csv(path, rows, date_format='%Y-%m-%d'):
    """The source file (cp1252, header = the real column names); returns its sha256. Dates are written with
    date_format, the real file's format being unknown to the tests."""
    with open(_fresh(path), 'w', newline='', encoding='cp1252') as stream:
        writer = csv.writer(stream)
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            record = dict(row['csv'])
            for column in DATE_COLUMNS:
                record[column] = date.fromisoformat(record[column]).strftime(date_format)
            writer.writerow([record[c] for c in CSV_COLUMNS])
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_contacts(path, rows, sha):
    totals = dict(records=0, with_email=0, with_phone=0, with_wireless=0)
    with closing(sqlite3.connect(_fresh(path))) as connection:
        connection.executescript(_CONTACTS_DDL)
        batch = []
        for i, row in enumerate(rows, start=1):
            c = row['csv']
            values = (row['person_id'], _name(c), c['sii_email'], c['sii_email_match_level'], c['sii_phone'],
                      c['sii_phone_wireless'], c['sii_phone_wireless_match_level'], c['svi_registration_address'],
                      c['sii_address'])
            totals['records'] += 1
            for field, index in (('with_email', 2), ('with_phone', 4), ('with_wireless', 5)):
                totals[field] += bool(values[index])
            batch.append((i, *values))
        connection.executemany('INSERT INTO contacts(rowid,id,name,email,email_match,phone,wireless,wireless_match,'
                               'registration,residential) VALUES (?,?,?,?,?,?,?,?,?,?)', batch)
        connection.execute('CREATE INDEX contacts_name ON contacts(name COLLATE NOCASE,id)')
        _meta(connection, {'schema': 'contacts-v1', 'source_sha256': sha, 'totals': totals})
        connection.commit()
    return Path(path)


def _write_scores(path, rows, sha):
    columns = [c for c in S.PERSON_COLUMNS if c[0] in _SCORES_COLUMNS]
    codes = {}
    for row in rows:
        codes.setdefault(row['household'], len(codes))
    with closing(sqlite3.connect(_fresh(path))) as connection:
        connection.execute(next(t for t in S.TABLES if t.startswith('CREATE TABLE metadata')))
        connection.execute(S.table_sql('person', columns))
        connection.execute('CREATE UNIQUE INDEX person_id ON person(person_id)')
        # `pos` follows the opaque id, as the real store's ties do: nothing aligns it with the source row
        ordered = sorted(rows, key=lambda r: r['person_id'])
        connection.executemany('INSERT INTO person(pos,person_id,hh,age,party,reg_precinct) VALUES (?,?,?,?,?,?)',
                               [(i, r['person_id'], codes[r['household']], r['age'], r['party'],
                                 r['csv']['svi_registration_precinct_code']) for i, r in enumerate(ordered, 1)])
        _meta(connection, {'schema': S.SCHEMA, 'n_people': len(rows), 'contacts_source_sha256': sha,
                           'w1': {'status': 'fixture'}, 'walk_testkit': SCHEMA})
        connection.commit()
    return Path(path)


def _write_street(path, rows, sha):
    bases = {}
    with closing(sqlite3.connect(_fresh(path))) as connection:
        connection.executescript(_STREET_DDL)
        for basis in ('registration', 'residential'):
            households, people = {}, []
            for r in rows:
                ident = _household_id(r, basis)
                if ident is None:
                    continue
                lon, lat, address = _place(r, basis)
                households.setdefault(ident, [ident, basis, lon, lat, address, 0])[-1] += 1
                people.append((ident, r['person_id'], _name(r['csv'])))
            connection.executemany('INSERT INTO households VALUES (?,?,?,?,?,?)', households.values())
            connection.executemany('INSERT INTO people VALUES (?,?,?)', people)
            bases[basis] = {'households': len(households), 'people': len(people)}
        connection.execute('CREATE INDEX household_longitude ON households(basis, longitude, latitude, id)')
        connection.execute('CREATE INDEX household_latitude ON households(basis, latitude, longitude, id)')
        connection.execute('CREATE INDEX household_basis_id ON households(basis, id)')
        connection.execute('CREATE INDEX people_identifier ON people(id,household_id)')
        _meta(connection, {'schema_version': '1', 'source_sha256': sha, 'bases': bases})
        connection.commit()
    return Path(path)


def _write_roads(path):
    with closing(sqlite3.connect(_fresh(path))) as connection:
        connection.executescript(_ROADS_DDL)
        for (ident, name, kind, geometry), (_, _, _, points) in zip(road_rows(), _ROWS):
            lons, lats = zip(*(lonlat(*p) for p in points))
            connection.execute('INSERT INTO roads VALUES (?,?,?,?)', (ident, name, kind, geometry))
            connection.execute('INSERT INTO extents VALUES (?,?,?,?,?)', (ident, min(lons), max(lons), min(lats), max(lats)))
        connection.commit()
    return Path(path)


def write_minimal_stores(data_dir, case=None, *, date_format='%Y-%m-%d', scores=True):
    """Write source.csv, contacts.sqlite3, scores.sqlite3, street.sqlite3 and roads.sqlite3 into data_dir (replacing
    them); same content for the same case, byte for byte. CSV row i is contacts rowid i+1, and the same person id is
    in scores.person.person_id and street.people.id (registration basis, when the row has coordinates).
    scores=False leaves scores.sqlite3 out (and removes an older one). There is no .street-salt and no walk.sqlite3."""
    case = hand_case() if case is None else case
    directory = Path(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / 'source.csv'
    sha = write_source_csv(source, case.rows, date_format)
    if not scores:
        _fresh(directory / 'scores.sqlite3')
    return Stores(directory=directory, source_csv=source, source_sha256=sha, salt=SALT, case=case,
                  expected=case.expected,
                  contacts_path=_write_contacts(directory / 'contacts.sqlite3', case.rows, sha),
                  scores_path=_write_scores(directory / 'scores.sqlite3', case.rows, sha) if scores else None,
                  street_path=_write_street(directory / 'street.sqlite3', case.rows, sha),
                  roads_path=_write_roads(directory / 'roads.sqlite3'),
                  person_ids=[r['person_id'] for r in case.rows])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('directory')
    stores = write_minimal_stores(parser.parse_args(argv).directory)
    print(json.dumps({'directory': str(stores.directory), 'source_sha256': stores.source_sha256,
                      'people': len(stores.person_ids)}))


if __name__ == '__main__':
    main()
