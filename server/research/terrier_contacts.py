"""Terrier's local contact sheet, using only the existing read-only source stores."""
from contextlib import contextmanager
import csv
from functools import lru_cache, wraps
import io
import json
from pathlib import Path
import re
import sqlite3

from django.conf import settings

from .street_views import local_only, response
from .v3_http import params

PAGE_SIZE = 50


class SourceStoreError(Exception):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


def source_errors(function):
    @wraps(function)
    def wrapped(request):
        # The real contact registry must never be exposed by a demo instance.
        if settings.DEMO_MODE or request.GET.get('demo') == '1':
            return response({'error': 'Source records are unavailable in demo mode.'}, 403)
        try:
            return function(request)
        except SourceStoreError as problem:
            return response({'error': str(problem)}, problem.status)
        except (sqlite3.Error, OSError):
            return response({'error': 'The local source stores are unavailable.'}, 503)
    return wrapped


def metadata(connection, schema):
    try:
        return {row['key']: json.loads(row['value']) for row in
                connection.execute('SELECT key,value FROM ' + schema + '.metadata')}
    except (ValueError, TypeError) as error:
        raise SourceStoreError('The local source metadata is invalid.') from error


@contextmanager
def verified_stores():
    """Open all source layers read-only and refuse cross-snapshot joins."""
    directory = Path(settings.DATA_DIR).resolve()
    paths = {name: directory / (name + '.sqlite3') for name in ('contacts', 'scores', 'street')}
    connection = sqlite3.connect(paths['contacts'].as_uri() + '?mode=ro', uri=True, timeout=3)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        for name in ('scores', 'street'):
            connection.execute('ATTACH DATABASE ? AS ' + name, (paths[name].as_uri() + '?mode=ro',))
        meta = {name: metadata(connection, 'main' if name == 'contacts' else name) for name in paths}
        if (meta['contacts'].get('schema') != 'contacts-v1'
                or meta['scores'].get('schema') != 'scores-v1'
                or meta['street'].get('schema_version') != '1'):
            raise SourceStoreError('The local source stores use an unsupported schema.')
        checksum = meta['contacts'].get('source_sha256')
        if not isinstance(checksum, str) or not re.fullmatch(r'[a-f0-9]{64}', checksum):
            raise SourceStoreError('The contact source checksum is missing or invalid.')
        if (checksum != meta['street'].get('source_sha256')
                or checksum != meta['scores'].get('contacts_source_sha256')):
            raise SourceStoreError('Contacts, profiles and household locations use different source files.', 409)
        if meta['scores'].get('w1', {}).get('status') != 'verified':
            raise SourceStoreError('The person crosswalk has not been verified against the source file.', 409)
        yield connection, meta
    finally:
        connection.close()


def contact_parameters(request):
    values = params(request, {'q', 'page', 'basis', 'channel'})
    text = values.get('q', '').strip()
    if len(text) > 160:
        raise ValueError('Search is too long')
    basis = values.get('basis', 'registration')
    channel = values.get('channel', 'all')
    raw_page = values.get('page', '1')
    if (basis not in ('registration', 'residential') or channel not in ('all', 'email', 'phone', 'none')
            or not re.fullmatch(r'[0-9]{1,9}', raw_page) or int(raw_page) < 1):
        raise ValueError('Invalid contact parameters')
    return text, basis, channel, int(raw_page)


def contact_clause(text, basis, channel):
    clauses, args = [], []
    present = lambda column: "COALESCE(TRIM(c.%s),'')<>''" % column
    email, phone, wireless = (present(column) for column in ('email', 'phone', 'wireless'))
    if channel == 'email':
        clauses.append(email)
    elif channel == 'phone':
        clauses.append('(' + phone + ' OR ' + wireless + ')')
    elif channel == 'none':
        clauses.append('NOT (' + email + ' OR ' + phone + ' OR ' + wireless + ')')
    if text:
        # Match the selected address basis only; treat wildcard characters as literals.
        pattern = '%' + text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        columns = ['id', 'name', 'email', 'phone', 'wireless', basis]
        terms = ["c.%s LIKE ? ESCAPE '\\'" % column for column in columns]
        args.extend([pattern] * len(columns))
        if re.fullmatch(r'[\d\s()+.\-]+', text):
            digits = ''.join(char for char in text if char.isdigit())
            if digits:
                for column in ('phone', 'wireless'):
                    expression = 'c.' + column
                    for punctuation in (' ', '(', ')', '+', '.', '-'):
                        expression = "REPLACE(%s,'%s','')" % (expression, punctuation)
                    terms.append(expression + ' LIKE ?')
                    args.append('%' + digits + '%')
        clauses.append('(' + ' OR '.join(terms) + ')')
    return (' WHERE ' + ' AND '.join(clauses) if clauses else ''), args


def _coverage(connection):
    row = connection.execute("""SELECT COUNT(*) AS records,
        COALESCE(SUM(COALESCE(TRIM(email),'')<>''),0) AS email,
        COALESCE(SUM(COALESCE(TRIM(phone),'')<>''),0) AS phone,
        COALESCE(SUM(COALESCE(TRIM(wireless),'')<>''),0) AS wireless,
        COALESCE(SUM(COALESCE(TRIM(email),'')='' AND COALESCE(TRIM(phone),'')=''
            AND COALESCE(TRIM(wireless),'')=''),0) AS withoutChannel FROM contacts""").fetchone()
    return dict(row)


@lru_cache(maxsize=4)
def _cached_coverage(path, stamp):
    connection = sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True, timeout=3)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        return _coverage(connection)
    finally:
        connection.close()


def coverage():
    path = (Path(settings.DATA_DIR) / 'contacts.sqlite3').resolve()
    stat = path.stat()
    return dict(_cached_coverage(str(path), (stat.st_mtime_ns, stat.st_size)))


def contact_rows(connection, clause, args, limit=None, offset=0):
    query = '''SELECT c.id,c.name,c.email,c.phone,c.wireless,
        c.registration AS registrationAddress,c.residential AS residentialAddress,
        p.hh AS household,p.reg_block AS block FROM contacts c
        LEFT JOIN scores.person p ON p.person_id=c.id''' + clause + ' ORDER BY c.name COLLATE NOCASE,c.id'
    if limit is not None:
        query += ' LIMIT ? OFFSET ?'
        args = [*args, limit, offset]
    for record in connection.execute(query, args):
        row = dict(record)
        # A blank remains missing; no invented channel, household or map position.
        for key in ('email', 'phone', 'wireless', 'registrationAddress', 'residentialAddress', 'block'):
            if row[key] is not None and not str(row[key]).strip():
                row[key] = None
        yield row


@local_only
@source_errors
def contacts(request):
    text, basis, channel, requested_page = contact_parameters(request)
    clause, args = contact_clause(text, basis, channel)
    with verified_stores() as (connection, _meta):
        total = connection.execute('SELECT COUNT(*) FROM contacts c' + clause, args).fetchone()[0]
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(requested_page, pages)
        offset = (page - 1) * PAGE_SIZE
        rows = list(contact_rows(connection, clause, args, PAGE_SIZE, offset))
        totals = coverage()
    return response({'rows': rows, 'total': total, 'page': page, 'pages': pages, 'pageSize': PAGE_SIZE,
                     'first': offset + 1 if total else 0, 'last': min(offset + PAGE_SIZE, total), 'coverage': totals})


# Same rule as csvValue in frontend/src/v3/lib/csv.ts. Python's \s, unlike JavaScript's, leaves out U+FEFF.
# Triggers: = + - @ and their full-width forms. Skipped before them: spaces, controls, invisible marks, bidi controls.
CSV_FORMULA = re.compile(r'[\t\r\n]|[\s\x00-\x1f\x7f-\x9f\xad\u061c\u180e\u200b-\u200f\u202a-\u202e\u2060\u2066-\u2069\ufeff]*'
                         r'[=+\-@\uff0b\uff0d\uff1d\uff20]')


def csv_value(value):
    text = '' if value is None else str(value)
    # Keep a stored value from becoming a formula when the local CSV is opened in Excel,
    # including one behind leading spaces, invisible, bidi or control characters, or written full-width.
    return "'" + text if CSV_FORMULA.match(text) else text


@local_only
@source_errors
def export_contacts(request):
    text, basis, channel, _page = contact_parameters(request)
    clause, args = contact_clause(text, basis, channel)
    output = io.StringIO(newline='')
    output.write('\ufeff')
    writer = csv.writer(output, quoting=csv.QUOTE_ALL, lineterminator='\r\n')
    writer.writerow(['Person ID', 'Name', 'Email', 'Phone', 'Wireless phone',
                     'Registration address' if basis == 'registration' else 'Residential address',
                     'Registration census block'])
    count = 0
    with verified_stores() as (connection, _meta):
        for row in contact_rows(connection, clause, args):
            values = [row['id'], row['name'], row['email'], row['phone'], row['wireless'],
                      row[basis + 'Address'], row['block']]
            writer.writerow([csv_value(value) for value in values])
            count += 1
    return response({'csv': output.getvalue(), 'count': count})
