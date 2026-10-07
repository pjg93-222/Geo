"""Local source-address lookup, deliberately separate from geographic indices.

GET /api/street?bbox=w,s,e,n&basis=registration|residential&zoom>=15
returns households (id, longitude, latitude, count, address), truncated,
total_households and source_sha256. Both bbox dimensions must be <=0.06 degrees.
GET /api/household?id=h_<opaque hash>&basis=registration|residential returns
id, address, people (id/name) and count. At most 100 people are returned; count
is the full household membership count. There are no filter/export parameters.
"""
from contextlib import contextmanager
from functools import wraps
from ipaddress import ip_address
import json
import math
from pathlib import Path
import re
import sqlite3

from django.conf import settings
from django.http import JsonResponse


MAX_HOUSEHOLDS = 300
MAX_PEOPLE = 100
MAX_PRELOAD_BATCH = 5000
MAX_BBOX_WIDTH = 0.06
BASES = {"registration", "residential"}


def response(payload, status=200):
    result = JsonResponse(payload, status=status)
    result["Cache-Control"] = "no-store, private"
    result["Pragma"] = "no-cache"
    result["X-Content-Type-Options"] = "nosniff"
    return result


def local_denied(request):
    """The 403 response unless this is a direct loopback request of the signed-in local administrator, else None."""
    try:
        loopback = ip_address(request.META.get("REMOTE_ADDR", "")).is_loopback
    except ValueError:
        loopback = False
    forwarded = any(request.META.get(key) for key in (
        "HTTP_FORWARDED", "HTTP_X_FORWARDED_FOR", "HTTP_X_FORWARDED_HOST", "HTTP_X_FORWARDED_PROTO"))
    if (getattr(request, "local_admin_access", False) is not True or not loopback
            or forwarded or not getattr(getattr(request, "user", None), "is_authenticated", False)
            or settings.HOSTED or not settings.LOCAL_ADMIN_ACCESS):
        return response({"error": "Direct local administrator access is required."}, 403)
    return None


def local_only(function):
    @wraps(function)
    def wrapped(request, *args, **kwargs):
        denied = local_denied(request)
        if denied is not None:
            return denied
        if request.method != "GET":
            result = response({"error": "GET is required."}, 405)
            result["Allow"] = "GET"
            return result
        try:
            return function(request, *args, **kwargs)
        except (ValueError, TypeError, KeyError, OverflowError):
            return response({"error": "Invalid street lookup parameters."}, 400)
        except (sqlite3.Error, OSError):
            return response({"error": "The local street directory is unavailable."}, 503)
    return wrapped


def local_only_post(function):
    """local_only for POST endpoints: the same loopback gate, POST required. CSRF stays with Django's middleware."""
    @wraps(function)
    def wrapped(request, *args, **kwargs):
        denied = local_denied(request)
        if denied is not None:
            return denied
        if request.method != "POST":
            result = response({"error": "POST is required."}, 405)
            result["Allow"] = "POST"
            return result
        try:
            return function(request, *args, **kwargs)
        except (ValueError, TypeError, KeyError, OverflowError):
            return response({"error": "Invalid request."}, 400)
        except (sqlite3.Error, OSError):
            return response({"error": "The local data is unavailable."}, 503)
    return wrapped


def parameters(request, required):
    if set(request.GET) != set(required) or any(len(request.GET.getlist(key)) != 1 for key in required):
        raise ValueError("Unexpected, missing or duplicate query parameter")
    basis = request.GET["basis"]
    if basis not in BASES:
        raise ValueError("Unknown source basis")
    return basis


@contextmanager
def database():
    path = (Path(settings.DATA_DIR) / "street.sqlite3").resolve()
    # mode=ro must fail for a missing file instead of creating an empty database.
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        row = connection.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        try:
            valid_version = row is not None and json.loads(row[0]) == "1"
        except (ValueError, TypeError):
            valid_version = False
        if not valid_version:
            raise sqlite3.DatabaseError("Unsupported street schema")
        yield connection
    finally:
        connection.close()


def viewport(request):
    values = request.GET["bbox"].split(",")
    if len(values) != 4:
        raise ValueError("Four bbox coordinates are required")
    west, south, east, north = (float(value) for value in values)
    zoom = float(request.GET["zoom"])
    if not all(math.isfinite(value) for value in (west, south, east, north, zoom)):
        raise ValueError("Coordinates and zoom must be finite")
    bounds = settings.DISTRICT["bounds"]
    if not (bounds[0] <= west < east <= bounds[2] and bounds[1] <= south < north <= bounds[3]):
        raise ValueError("Viewport is outside the local geographic scope")
    if east - west > MAX_BBOX_WIDTH + 1e-10 or north - south > MAX_BBOX_WIDTH + 1e-10 or zoom < 15:
        raise ValueError("Zoom in for street details")
    return west, south, east, north


@local_only
def street(request):
    basis = parameters(request, {"bbox", "basis", "zoom"})
    west, south, east, north = viewport(request)
    clause = "basis=? AND longitude>=? AND longitude<=? AND latitude>=? AND latitude<=?"
    args = (basis, west, east, south, north)
    with database() as connection:
        checksum_row = connection.execute("SELECT value FROM metadata WHERE key='source_sha256'").fetchone()
        if checksum_row is None:
            raise sqlite3.DatabaseError("Missing source metadata")
        try:
            checksum = json.loads(checksum_row[0])
        except (ValueError, TypeError) as error:
            raise sqlite3.DatabaseError("Invalid source metadata") from error
        if not isinstance(checksum, str) or not re.fullmatch(r"[a-f0-9]{64}", checksum):
            raise sqlite3.DatabaseError("Invalid source metadata")
        total = connection.execute("SELECT COUNT(*) FROM households WHERE " + clause, args).fetchone()[0]
        rows = connection.execute(
            "SELECT id,longitude,latitude,count,address FROM households WHERE " + clause
            + " ORDER BY longitude,latitude,id LIMIT ?", (*args, MAX_HOUSEHOLDS)).fetchall()
    return response({"households": [dict(row) for row in rows], "truncated": total > MAX_HOUSEHOLDS,
                     "total_households": total, "source_sha256": checksum})


@local_only
def household(request):
    basis = parameters(request, {"id", "basis"})
    identifier = request.GET["id"]
    if not re.fullmatch(r"h_[a-f0-9]{64}", identifier):
        raise ValueError("Invalid opaque identifier")
    with database() as connection:
        row = connection.execute("SELECT id,address,count FROM households WHERE id=? AND basis=?",
                                 (identifier, basis)).fetchone()
        if row is None:
            return response({"error": "Household not found for this source basis."}, 404)
        people = connection.execute("SELECT id,name FROM people WHERE household_id=? ORDER BY id LIMIT ?",
                                    (identifier, MAX_PEOPLE)).fetchall()
    return response({**dict(row), "people": [dict(person) for person in people]})


@local_only
def preload(request):
    """Bounded cursor batches for a complete local household-location cache."""
    basis=parameters(request,{'basis','after','source'})
    after=request.GET['after'];expected=request.GET['source']
    if (after and not re.fullmatch(r'h_[a-f0-9]{64}',after)) or not re.fullmatch(r'[a-f0-9]{64}',expected):
        raise ValueError('Invalid preload cursor or source')
    with database() as connection:
        metadata={row['key']:json.loads(row['value']) for row in connection.execute("SELECT key,value FROM metadata WHERE key IN ('source_sha256','bases')")}
        if metadata.get('source_sha256')!=expected:
            return response({'error':'Household locations belong to a different source file. Select the matching snapshot.'},409)
        totals=metadata['bases'][basis]
        rows=connection.execute('SELECT id,longitude,latitude,count,address FROM households WHERE basis=? AND id>? ORDER BY id LIMIT ?',
                                (basis,after,MAX_PRELOAD_BATCH+1)).fetchall()
        batch=rows[:MAX_PRELOAD_BATCH]
    return response({'schema':'household-preload-v1','source_sha256':expected,'basis':basis,
        'total_households':totals['households'],'total_people':totals['people'],
        'columns':['id','longitude','latitude','count','address'],'rows':[list(row) for row in batch],
        'next':batch[-1]['id'] if len(rows)>MAX_PRELOAD_BATCH else None})
