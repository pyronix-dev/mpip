"""Offline unit tests for mpip's core logic.

These exercise version comparison, specifier matching, marker evaluation,
requirement/wheel parsing and tag generation without touching the network, so
they run anywhere. Run with:  python -m unittest discover -s tests
"""

import sys
import unittest

from mpip.markers import evaluate
from mpip.requirements import Requirement, WheelInfo, canonical_name
from mpip.tags import _manylinux_platforms, compatible_tags
from mpip.version import InvalidVersion, SpecifierSet, Version


class TestVersion(unittest.TestCase):
    def test_numeric_ordering(self):
        self.assertLess(Version("1.2.0"), Version("1.10.0"))
        self.assertEqual(Version("1.0"), Version("1.0.0"))
        self.assertGreater(Version("2.0.1"), Version("2.0"))

    def test_prerelease_ordering(self):
        self.assertLess(Version("2.0a1"), Version("2.0"))
        self.assertLess(Version("1.0.dev1"), Version("1.0"))
        self.assertLess(Version("1.0a1"), Version("1.0b1"))
        self.assertLess(Version("1.0b1"), Version("1.0rc1"))
        self.assertLess(Version("1.0rc1"), Version("1.0"))
        self.assertTrue(Version("1.0a1").is_prerelease)
        self.assertFalse(Version("1.0").is_prerelease)

    def test_post_and_epoch(self):
        self.assertGreater(Version("1.0.post1"), Version("1.0"))
        self.assertGreater(Version("1!1.0"), Version("2.0"))

    def test_invalid(self):
        with self.assertRaises(InvalidVersion):
            Version("not-a-version")


class TestSpecifier(unittest.TestCase):
    def test_ranges(self):
        s = SpecifierSet(">=1.0,<2")
        self.assertTrue(s.contains("1.5"))
        self.assertFalse(s.contains("2.0"))
        self.assertFalse(s.contains("0.9"))

    def test_wildcards(self):
        self.assertTrue(SpecifierSet("==1.4.*").contains("1.4.9"))
        self.assertFalse(SpecifierSet("==1.4.*").contains("1.5.0"))
        self.assertTrue(SpecifierSet("!=1.4.*").contains("1.5.0"))

    def test_compatible_release(self):
        self.assertTrue(SpecifierSet("~=1.4.2").contains("1.4.9"))
        self.assertFalse(SpecifierSet("~=1.4.2").contains("1.5.0"))

    def test_prerelease_excluded_by_default(self):
        self.assertFalse(SpecifierSet(">=1.0").contains("2.0a1"))
        self.assertTrue(SpecifierSet(">=1.0").contains("2.0a1", allow_prerelease=True))


class TestMarkers(unittest.TestCase):
    def test_python_version(self):
        self.assertTrue(evaluate('python_version >= "3.0"'))
        self.assertFalse(evaluate('python_version < "3.0"'))

    def test_boolean_and_groups(self):
        self.assertTrue(evaluate(
            'python_version >= "3.6" and '
            '(sys_platform == "win32" or sys_platform == "%s")' % sys.platform
        ))

    def test_extra(self):
        self.assertTrue(evaluate('extra == "socks"', extra="socks"))
        self.assertFalse(evaluate('extra == "socks"', extra="other"))
        self.assertFalse(evaluate('extra == "socks"'))

    def test_in_operator(self):
        self.assertTrue(evaluate('"3" in python_version'))
        self.assertTrue(evaluate('sys_platform not in "plan9"'))


class TestRequirements(unittest.TestCase):
    def test_parse(self):
        r = Requirement('requests[security,socks] >=2.0 ; python_version >= "3.6"')
        self.assertEqual(r.canonical, "requests")
        self.assertEqual(r.extras, frozenset({"security", "socks"}))
        self.assertTrue(r.specifier.contains("2.5"))
        self.assertIsNotNone(r.marker)

    def test_canonical_name(self):
        self.assertEqual(canonical_name("Foo.Bar_Baz"), "foo-bar-baz")


class TestWheel(unittest.TestCase):
    def test_parse_and_match(self):
        w = WheelInfo("idna-3.18-py3-none-any.whl")
        self.assertEqual(w.name, "idna")
        self.assertEqual(w.version, "3.18")
        self.assertIn(("py3", "none", "any"), w.tags)
        self.assertIsNotNone(w.is_compatible(compatible_tags()))

    def test_incompatible_platform(self):
        w = WheelInfo("foo-1.0-cp38-cp38-win_amd64.whl")
        # A Windows-only cp38 wheel should not match this interpreter.
        if sys.platform != "win32":
            self.assertIsNone(w.is_compatible(compatible_tags()))

    def test_compressed_tag_set(self):
        w = WheelInfo("foo-1.0-py2.py3-none-any.whl")
        self.assertIn(("py3", "none", "any"), w.tags)
        self.assertIn(("py2", "none", "any"), w.tags)


class TestManylinux(unittest.TestCase):
    def test_glibc_2_35_x86_64(self):
        plats = _manylinux_platforms(2, 35, "x86_64")
        # A wheel needing an older/equal glibc is compatible with a newer host.
        self.assertIn("manylinux_2_28_x86_64", plats)   # e.g. torch wheels
        self.assertIn("manylinux_2_17_x86_64", plats)
        self.assertIn("manylinux2014_x86_64", plats)     # legacy alias for 2_17
        self.assertIn("manylinux2010_x86_64", plats)     # legacy alias for 2_12
        self.assertIn("manylinux1_x86_64", plats)        # legacy alias for 2_5
        # Newest is preferred (appears before older baselines).
        self.assertLess(plats.index("manylinux_2_35_x86_64"),
                        plats.index("manylinux_2_17_x86_64"))
        # A wheel needing a *newer* glibc than the host must NOT be listed.
        self.assertNotIn("manylinux_2_36_x86_64", plats)

    def test_wheel_matching_on_simulated_linux(self):
        supported = [("cp311", "cp311", p)
                     for p in _manylinux_platforms(2, 35, "aarch64")]
        w = WheelInfo("torch-2.8.0-cp311-cp311-manylinux_2_28_aarch64.whl")
        self.assertIsNotNone(w.is_compatible(supported))
        # An x86_64 wheel is not compatible with an aarch64 tag set.
        w2 = WheelInfo("torch-2.8.0-cp311-cp311-manylinux_2_28_x86_64.whl")
        self.assertIsNone(w2.is_compatible(supported))


class TestTags(unittest.TestCase):
    def test_pure_python_present(self):
        tags = compatible_tags()
        self.assertIn(("py3", "none", "any"), tags)
        # Interpreter-specific tag comes before the generic one (preferred).
        interp = "cp%d%d" % sys.version_info[:2]
        interp_idxs = [i for i, t in enumerate(tags) if t[0] == interp]
        generic_idx = tags.index(("py3", "none", "any"))
        self.assertTrue(all(i < generic_idx for i in interp_idxs))


if __name__ == "__main__":
    unittest.main()
