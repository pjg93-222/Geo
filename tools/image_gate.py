"""Image scan for the release workflow: the platform's images-stage policy for Terrier's images.

    python tools/image_gate.py REF@sha256:<64 hex> [REF@sha256:<64 hex> ...] [--report FILE]

This is a stand-alone copy of the policy in the platform repo's ops/security_gate.py (stage "images", ops/SECURITY_GATE.md; D-169,
D-181, D-182): that gate needs the platform's exceptions file and brain, and this repo's workflow only needs the trivy runs and the
rules. Change the rules in both places.

Blocks (exit 1):
  * HIGH/CRITICAL vulnerabilities that have a fixed version (D-181: trivy --ignore-unfixed);
  * HIGH/CRITICAL misconfigurations and secrets (rule and severity are printed, never the matched text);
  * licences on language packages (pip, npm) that are forbidden (CRITICAL), restricted (HIGH) or unknown (UNKNOWN) (D-182), and any
    licence result that is not recognisably an OS-package result.
Reported, never blocking: HIGH/CRITICAL vulnerabilities with no fix yet (D-181) and licences on OS packages, Debian's GPL/LGPL and
the like (D-182). They are printed as INFO lines and written to the report.
Fail closed: a missing trivy, a trivy error, an unreadable report, a vulnerability DB older than 24 h or without a timestamp.
Stdlib only.
"""
import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DIGEST_RE = re.compile(r'[^\s@]+@sha256:[0-9a-f]{64}')
DB_MAX_AGE_H = 24
TIMEOUT = 1800                                      # seconds per trivy run
SHOW = 40                                           # findings printed per image; the report carries all of them
UNFIXED_SEVERITIES = ('CRITICAL', 'HIGH')           # D-181: counted and reported, never blocking
LICENSE_SEVERITIES = ('CRITICAL', 'HIGH', 'UNKNOWN')   # forbidden, restricted, unrecognised: trivy's UNKNOWN is the unknown licence
# SPDX-identified permissive licences that trivy's classifier has no category for, so it reports them UNKNOWN (cffi is MIT-0). A
# language-package finding that is UNKNOWN and named here is reported, not blocking. Add a name only after reading the licence.
KNOWN_PERMISSIVE = {'MIT-0'}
# D-182. Trivy's licence scan puts every OS package in one result (Target "OS Packages", Class "license"); its vulnerability results
# say Class "os-pkgs" and Type = the distro. Anything not recognisably an OS-package result is a language package (or unknown): it blocks.
OS_LICENSE_TARGET = 'OS Packages'
OS_PKG_TYPES = {'debian', 'ubuntu', 'alpine', 'redhat', 'centos', 'rocky', 'alma', 'fedora', 'amazon', 'oracle', 'suse',
                'photon', 'wolfi', 'chainguard', 'cbl-mariner', 'azurelinux'}


class GateError(Exception):
    pass


def is_os_package_result(res):
    return (res.get('Class') == 'os-pkgs' or res.get('Target') == OS_LICENSE_TARGET
            or str(res.get('Type') or '').lower() in OS_PKG_TYPES)


def trivy(argv):
    """-> (returncode, stdout). The raw output of a scan goes to --output files, never to the log."""
    exe = shutil.which('trivy')
    if exe is None:
        raise GateError('trivy not found on PATH: required tool is missing')
    try:
        done = subprocess.run([exe, *argv], capture_output=True, stdin=subprocess.DEVNULL, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        raise GateError(f'trivy timed out after {TIMEOUT}s')
    except OSError as e:
        raise GateError(f'trivy could not start ({type(e).__name__})')
    return done.returncode, done.stdout.decode('utf-8', 'replace')


def scan(ref, run, tmp):
    """-> (blocking findings [str], OS-package licence counts, unfixed vulnerability counts, known-permissive licence names)."""
    name = ref.split('@')[0]
    out = Path(tmp) / 'report.json'

    def report(argv, what, ok=(0,)):
        out.unlink(missing_ok=True)
        rc, _ = run(['image', *argv, '--format', 'json', '--output', str(out), ref])
        if rc not in ok:
            raise GateError(f'trivy exited {rc} on {name} ({what}; tool error)')
        try:
            return rc, json.loads(out.read_text(encoding='utf-8'))
        except (OSError, ValueError) as e:
            raise GateError(f'trivy {what} report for {name} missing or unparseable ({type(e).__name__})')

    # 1. vulnerabilities with a fix, misconfigurations, secrets
    rc, data = report(['--scanners', 'vuln,misconfig,secret', '--severity', 'HIGH,CRITICAL', '--ignore-unfixed', '--exit-code', '1'],
                      'vulnerability scan', ok=(0, 1))
    findings = []
    for res in data.get('Results') or []:
        where = res.get('Target', '?')
        for v in res.get('Vulnerabilities') or []:
            findings.append(f"{where}: {v.get('VulnerabilityID')} {v.get('PkgName')} {v.get('Severity')}")
        for m in res.get('Misconfigurations') or []:
            findings.append(f"{where}: misconfig {m.get('ID')} {m.get('Severity')}")
        for s in res.get('Secrets') or []:
            findings.append(f"{where}: secret rule {s.get('RuleID')} {s.get('Severity')}")
    if rc == 1 and not findings:
        raise GateError(f'trivy exited 1 on {name} with no parseable finding')

    # 2. licences, split by class (D-182); its own run so UNKNOWN is listed for licences without making UNKNOWN vulnerabilities findings
    _, data = report(['--scanners', 'license', '--severity', ','.join(LICENSE_SEVERITIES)], 'licence scan')
    os_counts, known = dict.fromkeys(LICENSE_SEVERITIES, 0), set()
    for res in data.get('Results') or []:
        for lic in res.get('Licenses') or []:
            if is_os_package_result(res):
                os_counts[lic.get('Severity') if lic.get('Severity') in os_counts else 'UNKNOWN'] += 1
            elif lic.get('Severity') == 'UNKNOWN' and lic.get('Name') in KNOWN_PERMISSIVE:
                known.add(f"{lic.get('Name')} {lic.get('PkgName')}")
            else:
                findings.append(f"{res.get('Target', '?')}: license {lic.get('Name')} {lic.get('PkgName')} {lic.get('Severity')}")

    # 3. HIGH/CRITICAL vulnerabilities with no fix: reported only, so any trouble here is recorded and never fails the gate
    unfixed = dict.fromkeys(UNFIXED_SEVERITIES, 0)
    try:
        _, data = report(['--scanners', 'vuln', '--severity', 'HIGH,CRITICAL'], 'unfixed report')
        for res in data.get('Results') or []:
            for v in res.get('Vulnerabilities') or []:
                if not v.get('FixedVersion') and v.get('Severity') in unfixed:
                    unfixed[v['Severity']] += 1
    except GateError as e:
        unfixed = {'error': f'unfixed report unavailable ({e})'}
    return findings, os_counts, unfixed, sorted(known)


def parse_iso(s):
    s = re.sub(r'(\.\d{6})\d+', r'\1', s.strip()).replace('Z', '+00:00')
    d = dt.datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def check_db(run, now=None):
    rc, out = run(['version', '--format', 'json'])
    try:
        db = (json.loads(out) or {}).get('VulnerabilityDB') or {}
    except ValueError:
        raise GateError('trivy version output is not parseable JSON')
    stamp = db.get('UpdatedAt') or db.get('DownloadedAt')
    if not stamp:
        raise GateError('trivy vulnerability DB has no timestamp: its age cannot be checked')
    try:
        age_h = ((now or dt.datetime.now(dt.timezone.utc)) - parse_iso(stamp)).total_seconds() / 3600
    except ValueError:
        raise GateError('trivy vulnerability DB timestamp is unparseable')
    if age_h > DB_MAX_AGE_H:
        raise GateError(f'trivy vulnerability DB is {age_h:.0f} h old (max {DB_MAX_AGE_H} h)')


def main(argv=None, run=trivy):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('image', nargs='+', help='REF@sha256:<64 hex>')
    ap.add_argument('--report', help='write the full result here as JSON')
    args = ap.parse_args(argv)
    doc = {'result': 'FAIL', 'images': {}, 'error': None}
    try:
        bad = [i for i in args.image if not DIGEST_RE.fullmatch(i)]
        if bad:
            raise GateError(f'image reference not pinned by digest (REF@sha256:<64 hex>): {bad[0]}')
        with tempfile.TemporaryDirectory(prefix='imagegate-') as tmp:
            for ref in args.image:
                findings, os_counts, unfixed, known = scan(ref, run, tmp)
                doc['images'][ref] = {'findings': findings, 'os_licenses': os_counts, 'unfixed': unfixed, 'known_licenses': known}
        check_db(run)
    except GateError as e:
        doc['error'] = str(e)
    failing = [ref for ref, got in doc['images'].items() if got['findings']]
    for ref, got in doc['images'].items():
        name = ref.split('@')[0]
        unfixed = got['unfixed']
        print(f'INFO images/trivy: {name} unfixed, not blocking (D-181): '
              + (unfixed['error'] if 'error' in unfixed else ', '.join(f'{s} {unfixed[s]}' for s in UNFIXED_SEVERITIES)))
        print(f'INFO images/trivy: {name} OS-package licences, not blocking (D-182): '
              + ', '.join(f"{s} {got['os_licenses'][s]}" for s in LICENSE_SEVERITIES))
        if got['known_licenses']:
            print(f"INFO images/trivy: {name} language-package licences known permissive that trivy cannot categorise, "
                  f"not blocking (D-182): {', '.join(got['known_licenses'])}")
    for ref in failing:
        found = doc['images'][ref]['findings']
        print(f"FAIL images/trivy: {ref.split('@')[0]}: {len(found)} finding(s)")
        for line in found[:SHOW]:
            print(f'       - {line}')
        if len(found) > SHOW:
            print(f'       ... and {len(found) - SHOW} more (see --report)')
    if doc['error']:
        print(f"FAIL images/trivy: {doc['error']}")
    ok = not failing and not doc['error']
    if ok:
        doc['result'] = 'PASS'
        print(f"PASS images/trivy: {len(args.image)} image(s), no fixable HIGH/CRITICAL findings, "
              'no forbidden or unknown language-package licences')
    print(f"RESULT {doc['result']}: images")
    if args.report:
        Path(args.report).write_text(json.dumps(doc, indent=2), encoding='utf-8')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
