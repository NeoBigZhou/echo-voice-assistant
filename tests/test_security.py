import os
import tempfile
import unittest

from app.netguard import is_loopback_host, is_loopback_origin
from app.pathutil import safe_under
from app.audio.wake import _syl_match


class PathSafetyTests(unittest.TestCase):
    def test_accepts_child_path(self):
        with tempfile.TemporaryDirectory() as root:
            expected = os.path.join(root, "asset.js")
            self.assertEqual(safe_under(root, "asset.js"), os.path.realpath(expected))

    def test_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory() as parent:
            root = os.path.join(parent, "web")
            os.mkdir(root)
            self.assertIsNone(safe_under(root, "..", "secret.txt"))

    def test_rejects_sibling_with_same_prefix(self):
        with tempfile.TemporaryDirectory() as parent:
            root = os.path.join(parent, "web")
            os.mkdir(root)
            self.assertIsNone(safe_under(root, "..", "web-backup", "secret.txt"))


class LoopbackGuardTests(unittest.TestCase):
    def test_accepts_loopback_hosts(self):
        for host in ("127.0.0.1:8970", "localhost", "[::1]:8970"):
            with self.subTest(host=host):
                self.assertTrue(is_loopback_host(host))

    def test_rejects_non_loopback_hosts(self):
        for host in ("example.com", "192.168.1.10:8970", "", "127.0.0.1.evil.test"):
            with self.subTest(host=host):
                self.assertFalse(is_loopback_host(host))

    def test_origin_rules(self):
        self.assertTrue(is_loopback_origin("http://localhost:8970"))
        self.assertTrue(is_loopback_origin("https://[::1]"))
        self.assertFalse(is_loopback_origin("null"))
        self.assertFalse(is_loopback_origin("https://example.com"))


class WakeMatchingTests(unittest.TestCase):
    def test_single_insert_or_delete_is_tolerated(self):
        self.assertTrue(_syl_match("yuan", "yun"))
        self.assertTrue(_syl_match("abc", "axbc"))
        self.assertTrue(_syl_match("axbc", "abc"))
        self.assertFalse(_syl_match("abc", "axybc"))


if __name__ == "__main__":
    unittest.main()
