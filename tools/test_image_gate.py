"""Tests for tools/image_gate.py (the release workflow's image scan). Synthetic trivy JSON through a fake trivy; no Docker, no network.

    <venv python> -m unittest tools.test_image_gate          (from the V3 folder)
"""
import contextlib
import datetime as dt
import io
import json
import tempfile
import unittest
from pathlib import Path

from tools import image_gate as G

REF = 'ghcr.io/example/app@sha256:' + 'a' * 64
NOW = dt.datetime.now(dt.timezone.utc)


def vuln(cve, pkg, sev, fixed=None):
    return {'VulnerabilityID': cve, 'PkgName': pkg, 'Severity': sev, **({'FixedVersion': fixed} if fixed else {})}


def lic(name, pkg, sev='HIGH'):
    return {'Severity': sev, 'Category': 'restricted', 'PkgName': pkg, 'Name': name}


class FakeTrivy:
    """Answers the three scans and `version` the way trivy does: --ignore-unfixed hides vulnerabilities with no fix."""

    def __init__(self, vulns=(), misconfigs=(), secrets=(), os_lics=(), lang_lics=(), lang_target='Python', db=None,
                 licence_rc=0, unfixed_rc=0):
        self.vulns, self.misconfigs, self.secrets = list(vulns), list(misconfigs), list(secrets)
        self.os_lics, self.lang_lics, self.lang_target = list(os_lics), list(lang_lics), lang_target
        self.db = (NOW - dt.timedelta(hours=1)).isoformat() if db is None else db
        self.licence_rc, self.unfixed_rc = licence_rc, unfixed_rc
        self.calls = []

    def __call__(self, argv):
        self.calls.append(argv)
        if argv[0] == 'version':
            return 0, json.dumps({'VulnerabilityDB': {} if self.db == 'none' else {'UpdatedAt': self.db}})
        arg = lambda flag: argv[argv.index(flag) + 1]    # noqa: E731
        scanners = arg('--scanners')
        results = []
        if scanners == 'license':
            if self.licence_rc:
                return self.licence_rc, ''
            if self.os_lics:
                results.append({'Target': 'OS Packages', 'Class': 'license', 'Licenses': self.os_lics})
            if self.lang_lics:
                results.append({'Target': self.lang_target, 'Class': 'license', 'Licenses': self.lang_lics})
        elif scanners == 'vuln':
            if self.unfixed_rc:
                return self.unfixed_rc, ''
            results.append({'Target': 'debian 13.1', 'Class': 'os-pkgs', 'Type': 'debian', 'Vulnerabilities': self.vulns})
        else:
            vulns = [v for v in self.vulns if v.get('FixedVersion')] if '--ignore-unfixed' in argv else self.vulns
            results.append({'Target': 'debian 13.1', 'Class': 'os-pkgs', 'Type': 'debian', 'Vulnerabilities': vulns,
                            'Misconfigurations': self.misconfigs, 'Secrets': self.secrets})
        Path(arg('--output')).write_text(json.dumps({'Results': results}), encoding='utf-8')
        found = scanners != 'license' and scanners != 'vuln' and any(
            r.get(k) for r in results for k in ('Vulnerabilities', 'Misconfigurations', 'Secrets'))
        return (1 if found and '--exit-code' in argv else 0), ''


class ImageGateTests(unittest.TestCase):
    def go(self, fake, *extra, refs=(REF,)):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = G.main([*refs, *extra], run=fake)
        return rc, out.getvalue()

    def test_a_clean_image_passes(self):
        rc, out = self.go(FakeTrivy())
        self.assertEqual(rc, 0, out)
        self.assertIn('PASS images/trivy: 1 image(s), no fixable HIGH/CRITICAL findings, no forbidden or unknown language-package licences', out)
        self.assertIn('RESULT PASS: images', out)

    def test_os_package_licences_pass_and_are_counted(self):
        fake = FakeTrivy(os_lics=[lic('GPL-3.0-only', 'bash'), lic('LGPL-2.1-or-later', 'libc6'),
                                  lic('GPL-2.0-only', 'tar', 'CRITICAL'), lic('Foo', 'odd', 'UNKNOWN')])
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / 'r.json'
            rc, out = self.go(fake, '--report', str(report))
            data = json.loads(report.read_text(encoding='utf-8'))
        self.assertEqual(rc, 0, out)
        self.assertIn('INFO images/trivy: ghcr.io/example/app OS-package licences, not blocking (D-182): CRITICAL 1, HIGH 2, UNKNOWN 1', out)
        self.assertNotIn('GPL-3.0-only', out)
        self.assertEqual(data['result'], 'PASS')
        self.assertEqual(data['images'][REF]['os_licenses'], {'CRITICAL': 1, 'HIGH': 2, 'UNKNOWN': 1})

    def test_a_forbidden_restricted_or_unknown_language_licence_fails_beside_os_licences(self):
        os_lics = [lic('GPL-3.0-only', 'bash')]
        for label, target, bad in (('forbidden pip', 'Python', lic('AGPL-3.0-only', 'somelib', 'CRITICAL')),
                                   ('restricted pip', 'Python', lic('GPL-3.0-only', 'otherlib')),
                                   ('unknown pip', 'Python', lic('Foo License', 'thirdlib', 'UNKNOWN')),
                                   ('unknown npm', 'Node.js', lic('Foo License', 'npmthing', 'UNKNOWN'))):
            with self.subTest(label):
                rc, out = self.go(FakeTrivy(os_lics=os_lics, lang_lics=[bad], lang_target=target))
                self.assertEqual(rc, 1, out)
                self.assertIn(f"{target}: license {bad['Name']} {bad['PkgName']} {bad['Severity']}", out)
                self.assertNotIn('bash', out.split('FAIL images/trivy')[1])      # the OS licence is not among the findings

    def test_a_known_permissive_licence_trivy_cannot_categorise_is_reported_not_blocking(self):
        rc, out = self.go(FakeTrivy(lang_lics=[lic('MIT-0', 'cffi', 'UNKNOWN')]))
        self.assertEqual(rc, 0, out)
        self.assertIn('known permissive that trivy cannot categorise, not blocking (D-182): MIT-0 cffi', out)
        for bad in (lic('MIT-0', 'cffi', 'HIGH'), lic('Foo License', 'other', 'UNKNOWN')):      # only the named licence AND UNKNOWN
            with self.subTest(bad['Name']):
                rc, out = self.go(FakeTrivy(lang_lics=[bad]))
                self.assertEqual(rc, 1, out)

    def test_a_licence_result_not_provably_an_os_package_blocks(self):
        for res in ({'Target': 'mystery', 'Class': 'license-file'}, {'Target': 'mystery', 'Class': 'something-new'}, {}):
            with self.subTest(res):
                self.assertFalse(G.is_os_package_result(res))
        for res in ({'Target': 'OS Packages', 'Class': 'license'}, {'Class': 'os-pkgs', 'Type': 'debian'}, {'Type': 'Debian'}):
            with self.subTest(res):
                self.assertTrue(G.is_os_package_result(res))

    def test_a_fixable_vulnerability_fails_and_an_unfixed_one_passes_with_an_info_line(self):
        unfixed = [vuln('CVE-2026-1001', 'libunfixed', 'HIGH'), vuln('CVE-2026-1002', 'libunfixed', 'HIGH'),
                   vuln('CVE-2026-1003', 'libunfixed', 'CRITICAL')]
        rc, out = self.go(FakeTrivy(vulns=unfixed + [vuln('CVE-2026-1004', 'libfixed', 'HIGH', '9.9')]))
        self.assertEqual(rc, 1, out)
        self.assertIn('CVE-2026-1004 libfixed HIGH', out)
        self.assertNotIn('CVE-2026-1001', out)                                    # unfixed ones are not findings
        self.assertIn('INFO images/trivy: ghcr.io/example/app unfixed, not blocking (D-181): CRITICAL 1, HIGH 2', out)
        rc, out = self.go(FakeTrivy(vulns=unfixed))
        self.assertEqual(rc, 0, out)
        self.assertIn('unfixed, not blocking (D-181): CRITICAL 1, HIGH 2', out)

    def test_misconfigurations_and_secrets_block_without_printing_the_match(self):
        rc, out = self.go(FakeTrivy(misconfigs=[{'ID': 'DS002', 'Severity': 'HIGH'}],
                                    secrets=[{'RuleID': 'aws-access-key-id', 'Severity': 'CRITICAL', 'Match': 'AKIAEXAMPLEEXAMPLE'}]))
        self.assertEqual(rc, 1, out)
        self.assertIn('misconfig DS002 HIGH', out)
        self.assertIn('secret rule aws-access-key-id CRITICAL', out)
        self.assertNotIn('AKIAEXAMPLE', out)

    def test_the_runs_use_the_policys_flags(self):
        fake = FakeTrivy()
        self.go(fake)
        runs = {a[a.index('--scanners') + 1]: a for a in fake.calls if '--scanners' in a}
        self.assertEqual(set(runs), {'vuln,misconfig,secret', 'license', 'vuln'})
        blocking = runs['vuln,misconfig,secret']
        self.assertIn('--ignore-unfixed', blocking)
        self.assertEqual(blocking[blocking.index('--severity') + 1], 'HIGH,CRITICAL')
        self.assertEqual(blocking[blocking.index('--exit-code') + 1], '1')
        self.assertEqual(runs['license'][runs['license'].index('--severity') + 1], 'CRITICAL,HIGH,UNKNOWN')
        self.assertNotIn('--ignore-unfixed', runs['vuln'])
        self.assertNotIn('--exit-code', runs['vuln'])

    def test_fail_closed_cases(self):
        rc, out = self.go(FakeTrivy(licence_rc=2))
        self.assertEqual(rc, 1)
        self.assertIn('trivy exited 2 on ghcr.io/example/app (licence scan; tool error)', out)
        rc, out = self.go(FakeTrivy(db=(NOW - dt.timedelta(hours=30)).isoformat()))
        self.assertEqual(rc, 1)
        self.assertRegex(out, r'FAIL images/trivy: trivy vulnerability DB is 30 h old')
        rc, out = self.go(FakeTrivy(db='none'))
        self.assertEqual(rc, 1)
        self.assertIn('vulnerability DB has no timestamp', out)
        rc, out = self.go(FakeTrivy(), refs=('ghcr.io/example/app:latest',))
        self.assertEqual(rc, 1)
        self.assertIn('not pinned by digest', out)

        def missing(argv):
            raise G.GateError('trivy not found on PATH: required tool is missing')
        rc, out = self.go(missing)
        self.assertEqual(rc, 1)
        self.assertIn('trivy not found', out)

    def test_a_failed_unfixed_report_never_fails_the_gate(self):
        rc, out = self.go(FakeTrivy(unfixed_rc=2))
        self.assertEqual(rc, 0, out)
        self.assertIn('unfixed report unavailable', out)

    def test_every_image_is_scanned_and_all_findings_are_reported(self):
        other = 'ghcr.io/example/other@sha256:' + 'b' * 64
        rc, out = self.go(FakeTrivy(vulns=[vuln('CVE-2026-1004', 'libfixed', 'HIGH', '9.9')]), refs=(REF, other))
        self.assertEqual(rc, 1, out)
        self.assertIn('FAIL images/trivy: ghcr.io/example/app: 1 finding(s)', out)
        self.assertIn('FAIL images/trivy: ghcr.io/example/other: 1 finding(s)', out)


if __name__ == '__main__':
    unittest.main()
