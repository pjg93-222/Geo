from contextlib import contextmanager
import json
import re
import sqlite3
from django.conf import settings
from .street_views import local_only, parameters, database, response


@contextmanager
def contacts_db():
    connection = sqlite3.connect((settings.DATA_DIR/'contacts.sqlite3').resolve().as_uri()+'?mode=ro', uri=True)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        version = connection.execute("SELECT value FROM metadata WHERE key='schema'").fetchone()
        if not version or json.loads(version[0]) != 'contacts-v1':
            raise sqlite3.DatabaseError('Invalid contact registry')
        yield connection
    finally:
        connection.close()


def search_query(value):
    if len(value) > 160:
        raise ValueError('Search is too long')
    terms = re.findall(r'\w+', value, re.UNICODE)
    if value.strip() and not terms:
        raise ValueError('Enter a name, contact detail or address')
    # Normalize a formatted phone query to the same digits stored in the index.
    if value.strip() and re.fullmatch(r'[\d\s()+.\-]+', value):
        terms = [''.join(c for c in value if c.isdigit())]
    return ' AND '.join('"'+term+'"*' for term in terms[:8])


@local_only
def contacts(request):
    basis = parameters(request, {'q','page','basis'})
    page = int(request.GET['page'])
    if not 0 <= page <= 20000:
        raise ValueError('Invalid page')
    query = search_query(request.GET['q'])
    with contacts_db() as connection:
        totals = json.loads(connection.execute("SELECT value FROM metadata WHERE key='totals'").fetchone()[0])
        clause = ' WHERE rowid IN (SELECT rowid FROM search WHERE search MATCH ?)' if query else ''
        values = (query,) if query else ()
        count = connection.execute('SELECT COUNT(*) FROM contacts'+clause, values).fetchone()[0]
        rows = connection.execute('SELECT id,name,email,phone,wireless,'+basis+' AS address FROM contacts'+clause+
                                  ' ORDER BY name COLLATE NOCASE,id LIMIT 50 OFFSET ?', (*values,page*50)).fetchall()
    return response({'people':[dict(row) for row in rows], 'total':count, 'page':page, 'page_size':50,
                     'source_totals':totals, 'basis':basis})


@local_only
def person(request):
    basis = parameters(request, {'id','basis'})
    ident = request.GET['id']
    if not re.fullmatch(r'p_[a-f0-9]{64}', ident):
        raise ValueError('Invalid person identifier')
    with contacts_db() as connection:
        row = connection.execute('SELECT * FROM contacts WHERE id=?', (ident,)).fetchone()
        if row is None:
            return response({'error':'This person is not in the supplied registry.'},404)
        checksum = json.loads(connection.execute("SELECT value FROM metadata WHERE key='source_sha256'").fetchone()[0])
    location = None
    with database() as street:
        street_hash = json.loads(street.execute("SELECT value FROM metadata WHERE key='source_sha256'").fetchone()[0])
        if checksum != street_hash:
            return response({'error':'The registry and map use different source snapshots.'},409)
        found = street.execute('SELECT h.id,h.address,h.longitude,h.latitude FROM people p JOIN households h ON p.household_id=h.id WHERE p.id=? AND h.basis=? LIMIT 1', (ident,basis)).fetchone()
        if found:
            location = dict(found)
    return response({**dict(row),'location':location,'basis':basis})
