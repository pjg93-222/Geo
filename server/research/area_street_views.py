"""Local public road geography and anonymous residential-point estimates.

Catalogs are explicitly installed local JSON files. This module never opens voter
stores or makes network requests. Point IDs are public GIS feature IDs; counts
describe source records, not occupied homes or verified accessible doors.
"""
from dataclasses import dataclass
from datetime import date
from functools import lru_cache, wraps
import json
import logging
import math
from pathlib import Path
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import SuspiciousOperation
from shapely.geometry import MultiLineString, Point, Polygon, box
from shapely.errors import GEOSException

from .street_views import local_only, local_only_post, response

log = logging.getLogger(__name__)
IDENTIFIER = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}\Z')
MAX_BODY = 64 * 1024
MAX_CATALOG_BYTES = 32 * 1024 * 1024
MAX_CATALOGS, MAX_STREETS = 32, 5000
MAX_POINTS, MAX_LINE_VERTICES, MAX_POLYGON_VERTICES = 200000, 200000, 500
MAX_GIS_ID = 2 ** 53 - 1


class CatalogProblem(ValueError):
    """An unavailable or invalid installed catalog, never a caller input error."""


class RequestProblem(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Catalog:
    metadata: dict
    streets: tuple


def _text(value, limit):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise CatalogProblem('Invalid catalog text.')
    return value


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _coordinate(value, bounds=None):
    if (not isinstance(value, list) or len(value) != 2 or not all(_number(n) for n in value)
            or not -180 <= value[0] <= 180 or not -90 <= value[1] <= 90):
        raise CatalogProblem('Invalid geographic coordinate.')
    if bounds and not (bounds[0] <= value[0] <= bounds[2] and bounds[1] <= value[1] <= bounds[3]):
        raise CatalogProblem('Catalog geometry exceeds its coverage.')
    return value


def validate_catalog(value, expected_id):
    """Validate the complete public schema, rejecting unknown fields and duplicates.

    Used by the reader and available to offline catalog preparation tools. A whole
    catalog either validates or fails; no invalid features are silently omitted.
    """
    fields = {'version', 'id', 'label', 'bbox', 'source_date', 'count_basis', 'sources', 'streets'}
    if not isinstance(value, dict) or set(value) != fields or type(value['version']) is not int or value['version'] != 1:
        raise CatalogProblem('Unsupported public street catalog schema.')
    if value['id'] != expected_id or not isinstance(expected_id, str) or not IDENTIFIER.fullmatch(expected_id):
        raise CatalogProblem('Catalog ID does not match its installed filename.')
    label = _text(value['label'], 120)
    basis = _text(value['count_basis'], 1000)
    bounds = value['bbox']
    if (not isinstance(bounds, list) or len(bounds) != 4 or not all(_number(n) for n in bounds)
            or not -180 <= bounds[0] < bounds[2] <= 180 or not -90 <= bounds[1] < bounds[3] <= 90):
        raise CatalogProblem('Invalid catalog coverage.')
    source_date = value['source_date']
    try:
        if not isinstance(source_date, str) or date.fromisoformat(source_date).isoformat() != source_date:
            raise ValueError
    except ValueError as problem:
        raise CatalogProblem('Invalid catalog source date.') from problem
    sources = value['sources']
    if not isinstance(sources, list) or not 1 <= len(sources) <= 10:
        raise CatalogProblem('Catalog sources are required.')
    for source in sources:
        if not isinstance(source, dict) or set(source) != {'label', 'url'}:
            raise CatalogProblem('Invalid catalog source.')
        _text(source['label'], 120)
        url = urlsplit(_text(source['url'], 2000))
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
            raise CatalogProblem('Invalid catalog source URL.')
    raw_streets = value['streets']
    if not isinstance(raw_streets, list) or not raw_streets or len(raw_streets) > MAX_STREETS:
        raise CatalogProblem('Catalog street limit exceeded or no streets supplied.')
    street_ids, point_ids, streets = set(), set(), []
    vertices = 0
    for street in raw_streets:
        if not isinstance(street, dict) or set(street) != {'id', 'label', 'geometry', 'address_points'}:
            raise CatalogProblem('Invalid public street schema.')
        sid = _text(street['id'], 120)
        if sid in street_ids:
            raise CatalogProblem('Duplicate public street ID.')
        street_ids.add(sid)
        _text(street['label'], 160)
        geometry = street['geometry']
        if (not isinstance(geometry, dict) or set(geometry) != {'type', 'coordinates'}
                or geometry['type'] != 'MultiLineString' or not isinstance(geometry['coordinates'], list)
                or not geometry['coordinates']):
            raise CatalogProblem('Public streets require MultiLineString geometry.')
        for line in geometry['coordinates']:
            if not isinstance(line, list) or len(line) < 2:
                raise CatalogProblem('Invalid street line.')
            vertices += len(line)
            if vertices > MAX_LINE_VERTICES:
                raise CatalogProblem('Catalog line vertex limit exceeded.')
            for coordinate in line:
                _coordinate(coordinate, bounds)
        shape = MultiLineString(geometry['coordinates'])
        if shape.is_empty or not shape.is_valid or shape.length == 0:
            raise CatalogProblem('Invalid street geometry.')
        points = street['address_points']
        if not isinstance(points, list):
            raise CatalogProblem('Invalid public address points.')
        for point in points:
            if not isinstance(point, list) or len(point) != 3:
                raise CatalogProblem('Public points must contain only longitude, latitude and GIS ID.')
            _coordinate(point[:2], bounds)
            if type(point[2]) is not int or not 0 <= point[2] <= MAX_GIS_ID or point[2] in point_ids:
                raise CatalogProblem('Invalid or duplicate public GIS point ID.')
            point_ids.add(point[2])
            if len(point_ids) > MAX_POINTS:
                raise CatalogProblem('Catalog public point limit exceeded.')
        streets.append((street, shape))
    metadata = {'id': expected_id, 'label': label, 'bbox': bounds, 'street_count': len(streets),
                'estimated_doors': len(point_ids), 'source_date': source_date, 'count_basis': basis, 'sources': sources}
    return Catalog(metadata, tuple(streets))


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def _json(data):
    def bad_constant(_):
        raise ValueError('Nonfinite JSON number')
    return json.loads(data, object_pairs_hook=_no_duplicates, parse_constant=bad_constant)


def _directory():
    data = Path(settings.DATA_DIR).resolve()
    directory = data / 'public_streets'
    if directory.resolve() != directory:
        raise CatalogProblem('Public catalog directory must be inside the local data directory.')
    return directory


def _stamp(path):
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino


@lru_cache(maxsize=8)
def _cached_catalog(filename, stamp):
    path = Path(filename)
    if stamp[0] > MAX_CATALOG_BYTES:
        raise CatalogProblem('Public catalog size limit exceeded.')
    try:
        with path.open('rb') as stream:
            raw = stream.read(MAX_CATALOG_BYTES + 1)
        if len(raw) > MAX_CATALOG_BYTES:
            raise CatalogProblem('Public catalog size limit exceeded.')
        result = validate_catalog(_json(raw), path.stem)
    except (ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError, GEOSException) as problem:
        raise CatalogProblem('The installed public street catalog is invalid.') from problem
    if _stamp(path) != stamp:
        raise CatalogProblem('Public street catalog changed while reading; retry the request.')
    return result


def get_catalog(catalog_id):
    if not isinstance(catalog_id, str) or not IDENTIFIER.fullmatch(catalog_id):
        raise RequestProblem('Invalid public street catalog ID.')
    directory = _directory()
    path = directory / (catalog_id + '.json')
    if path.is_symlink() or path.resolve().parent != directory:
        raise CatalogProblem('Public catalogs must be installed local files.')
    if not path.is_file():
        raise RequestProblem('Public street catalog not found.', 404)
    stamp = _stamp(path)
    catalog = _cached_catalog(str(path), stamp)
    if _stamp(path) != stamp:
        raise CatalogProblem('Public street catalog changed; retry the request.')
    return catalog


def polygon_input(value):
    """Validate caller geometry before any catalog file is opened."""
    if not isinstance(value, list) or not 3 <= len(value) <= MAX_POLYGON_VERTICES:
        raise RequestProblem('Draw a polygon with 3 to %d vertices.' % MAX_POLYGON_VERTICES)
    try:
        for coordinate in value:
            _coordinate(coordinate)
        polygon = Polygon(value)
    except (CatalogProblem, ValueError, GEOSException) as problem:
        raise RequestProblem('Invalid polygon coordinates.') from problem
    if polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
        raise RequestProblem('Draw a polygon with nonzero area and no crossing edges.')
    return polygon


def _lines(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == 'LineString':
        return [list(map(list, geometry.coords))] if geometry.length else []
    if geometry.geom_type in ('MultiLineString', 'GeometryCollection'):
        return [line for child in geometry.geoms for line in _lines(child)]
    return []


def query_catalog(catalog, polygon):
    if not box(*catalog.metadata['bbox']).covers(polygon):
        raise RequestProblem('Keep the polygon inside this catalog\'s coverage.')
    streets = []
    for street, geometry in catalog.streets:
        points = [point for point in street['address_points'] if polygon.covers(Point(point[:2]))]
        coordinates = _lines(geometry.intersection(polygon))
        # Points can sit away from their road centerline. Preserve their counts even
        # when the polygon includes an entrance but no portion of its road line.
        if coordinates or points:
            streets.append({'id': street['id'], 'label': street['label'],
                            'geometry': {'type': 'MultiLineString', 'coordinates': coordinates},
                            'estimated_doors': len(points), 'address_points': points})
    streets.sort(key=lambda street: (street['label'], street['id']))
    return {'catalog': catalog.metadata, 'polygon': list(map(list, polygon.exterior.coords)), 'streets': streets,
            'estimated_doors': sum(street['estimated_doors'] for street in streets),
            'count_basis': catalog.metadata['count_basis']}


def _guard(function):
    @wraps(function)
    def wrapped(request):
        if settings.DEMO_MODE:
            return response({'error': 'Public street estimates are unavailable in demo mode.'}, 403)
        try:
            return function(request)
        except RequestProblem as problem:
            return response({'error': str(problem)}, problem.status)
        except (CatalogProblem, OSError):
            log.warning('Public street catalog unavailable.', exc_info=True)
            return response({'error': 'The local public street catalog is unavailable or invalid.'}, 503)
        except (ValueError, TypeError, UnicodeError, SuspiciousOperation):
            return response({'error': 'Invalid public street request.'}, 400)
    return wrapped


@local_only
@_guard
def catalogs(request):
    if request.GET:
        raise RequestProblem('Unexpected query parameters.')
    directory = _directory()
    if not directory.exists():
        return response({'catalogs': []})
    files = sorted(directory.glob('*.json'))
    if len(files) > MAX_CATALOGS or any(not IDENTIFIER.fullmatch(path.stem) for path in files):
        raise CatalogProblem('Invalid installed catalog directory.')
    return response({'catalogs': [get_catalog(path.stem).metadata for path in files]})


def json_request(request):
    """The strict JSON body of a POST (no query string, at most MAX_BODY bytes, no duplicate keys or nonfinite numbers)."""
    if request.GET or request.content_type != 'application/json':
        raise RequestProblem('Send an application/json request without query parameters.')
    declared = request.META.get('CONTENT_LENGTH', '')
    if declared and (not declared.isdecimal() or int(declared) > MAX_BODY):
        raise RequestProblem('Request exceeds the size limit.')
    raw = request.body
    if len(raw) > MAX_BODY:
        raise RequestProblem('Request exceeds the size limit.')
    try:
        return _json(raw)
    except (ValueError, UnicodeError, RecursionError) as problem:
        raise RequestProblem('Invalid JSON request.') from problem


@local_only_post
@_guard
def query(request):
    body = json_request(request)
    if not isinstance(body, dict) or set(body) != {'catalog_id', 'polygon'}:
        raise RequestProblem('Provide only catalog_id and polygon.')
    polygon = polygon_input(body['polygon'])
    return response(query_catalog(get_catalog(body['catalog_id']), polygon))
