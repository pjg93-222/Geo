"""Glass Box event emitter for long v3 processes (stdlib only).

Same event shape as _build/gb.ps1 (schema_version 1): one JSON file per event in ~/.claude/glassbox/events and a mirror
line in V3/_build/glassbox_events.jsonl. Callers pass counts, paths and stage names only; never a name, address,
email, phone or person id.
"""
import datetime
import json
import os
import random
import time
from pathlib import Path

V3 = Path(__file__).resolve().parents[1]
MIRROR = V3 / '_build' / 'glassbox_events.jsonl'
EVENT_DIR = Path.home() / '.claude' / 'glassbox' / 'events'


class GlassBox:
    def __init__(self, run_id, source):
        self.run_id = run_id
        self.source = source
        self.started = time.time()

    def emit(self, kind, status, stage, summary, detail='', bytes_=-1, metadata=None):
        now = datetime.datetime.now(datetime.timezone.utc)
        stamp = now.strftime('%Y%m%dT%H%M%S') + '%06d0' % now.microsecond
        ident = 'evt-%sZ-%s' % (stamp, ''.join(random.choice('0123456789abcdef') for _ in range(10)))
        event = {
            'schema_version': 1, 'id': ident,
            'timestamp_utc': now.strftime('%Y-%m-%dT%H:%M:%S.') + '%06d0Z' % now.microsecond,
            'run_id': self.run_id, 'correlation_id': '', 'parent_id': '', 'lane': 'claude', 'source': self.source,
            'kind': kind, 'status': status, 'stage': stage, 'summary': summary, 'detail': detail,
            'cwd': str(V3 / '_build'), 'duration_ms': int((time.time() - self.started) * 1000), 'bytes': bytes_,
            'metadata': metadata,
        }
        text = json.dumps(event, separators=(',', ':'))
        try:
            EVENT_DIR.mkdir(parents=True, exist_ok=True)
            (EVENT_DIR / (ident + '.json')).write_text(text, encoding='utf-8')
            MIRROR.parent.mkdir(parents=True, exist_ok=True)
            with open(MIRROR, 'a', encoding='utf-8') as stream:
                stream.write(text + '\n')
        except OSError:
            pass     # the dashboard is an observer; a full disk must not change the build's outcome
        return ident

    def run(self, status, stage, summary, **extra):
        return self.emit('run', status, stage, summary, **extra)

    def stage(self, status, stage, summary, **extra):
        return self.emit('stage', status, stage, summary, **extra)

    def failed(self, stage, summary, detail=''):
        return self.emit('failed', 'failed', stage, summary, detail=detail)


def new_run_id(prefix):
    return '%s-%s' % (prefix, datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
