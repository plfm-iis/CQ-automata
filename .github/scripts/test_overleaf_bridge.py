"""Local integration tests: no network, real credentials, or manuscript changes."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("bridge", Path(__file__).with_name("overleaf_bridge.py"))
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.PIPE).decode().strip()


def commit(repo, message):
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", message)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        seed = root / "seed"
        seed.mkdir()
        git(seed, "init", "-b", "main")
        for name in ("main.tex", "macros.tex", "literature.bib"):
            (seed / name).write_text(name + "\n")
        commit(seed, "initial")
        self.ghremote = root / "github.git"
        self.olremote = root / "overleaf.git"
        git(root, "clone", "--bare", str(seed), str(self.ghremote))
        git(root, "clone", "--bare", str(seed), str(self.olremote))
        # Overleaf may use a different default branch from GitHub.
        git(self.olremote, "branch", "-m", "main", "master")
        self.gh = root / "github"
        self.ol = root / "overleaf"
        git(root, "clone", str(self.ghremote), str(self.gh))
        git(root, "clone", str(self.olremote), str(self.ol))
        (self.gh / "AGENTS.md").write_text("GitHub policy\n")
        commit(self.gh, "metadata")
        git(self.gh, "push")
        self.initial_ol = git(self.ol, "rev-parse", "HEAD")

    def run_bridge(self):
        bridge.Bridge(self.gh, str(self.olremote)).run()

    def edit_overleaf(self, path="main.tex", content="updated\n"):
        (self.ol / path).write_text(content)
        commit(self.ol, "author edit")
        git(self.ol, "push")

    def test_initial_and_later_import_preserve_metadata_and_never_write_overleaf(self):
        self.run_bridge()
        self.assertEqual(git(self.olremote, "rev-parse", "HEAD"), self.initial_ol)
        self.edit_overleaf()
        expected = git(self.ol, "rev-parse", "HEAD")
        self.run_bridge()
        self.assertEqual((self.gh / "main.tex").read_text(), "updated\n")
        self.assertEqual((self.gh / "AGENTS.md").read_text(), "GitHub policy\n")
        self.assertEqual(git(self.olremote, "rev-parse", "HEAD"), expected)
        self.assertEqual(bridge.Bridge(self.gh).state()["overleaf_revision"], expected)

    def test_initial_difference_is_refused_without_remote_changes(self):
        self.edit_overleaf()
        before = git(self.ghremote, "rev-parse", "HEAD")
        with self.assertRaisesRegex(bridge.Refused, "Initial"):
            self.run_bridge()
        self.assertEqual(git(self.ghremote, "rev-parse", "HEAD"), before)
        self.assertEqual((self.gh / "main.tex").read_text(), "main.tex\n")

    def test_pending_github_paper_edit_is_preserved(self):
        self.run_bridge()
        (self.gh / "main.tex").write_text("pending approval\n")
        commit(self.gh, "pending edit")
        git(self.gh, "push")
        self.edit_overleaf()
        before = git(self.ghremote, "rev-parse", "HEAD")
        with self.assertRaisesRegex(bridge.Refused, "not confirmed"):
            self.run_bridge()
        self.assertEqual(git(self.ghremote, "rev-parse", "HEAD"), before)
        self.assertEqual((self.gh / "main.tex").read_text(), "pending approval\n")

    def test_stale_github_checkout_is_refused(self):
        other = self.gh.parent / "other"
        git(self.gh.parent, "clone", str(self.ghremote), str(other))
        (other / "AGENTS.md").write_text("new policy\n")
        commit(other, "concurrent edit")
        git(other, "push")
        with self.assertRaisesRegex(bridge.Refused, "GitHub main changed"):
            self.run_bridge()

    def test_symlink_is_refused(self):
        self.run_bridge()
        (self.ol / "unsafe.tex").symlink_to("/etc/passwd")
        commit(self.ol, "unsafe source")
        git(self.ol, "push")
        with self.assertRaisesRegex(bridge.Refused, "Unsupported paper file"):
            self.run_bridge()

    def test_missing_entrypoint_is_refused(self):
        self.run_bridge()
        (self.ol / "main.tex").unlink()
        commit(self.ol, "remove entry point")
        git(self.ol, "push")
        with self.assertRaisesRegex(bridge.Refused, "Required manuscript"):
            self.run_bridge()

    def test_large_deletion_is_refused(self):
        for name in ("a.tex", "b.tex", "c.tex"):
            (self.ol / name).write_text("section\n")
            (self.gh / name).write_text("section\n")
        commit(self.ol, "more files")
        git(self.ol, "push")
        commit(self.gh, "more files")
        git(self.gh, "push")
        self.run_bridge()
        for name in ("a.tex", "b.tex", "c.tex"):
            (self.ol / name).unlink()
        commit(self.ol, "large deletion")
        git(self.ol, "push")
        with self.assertRaisesRegex(bridge.Refused, "Large deletion"):
            self.run_bridge()


if __name__ == "__main__":
    unittest.main()
