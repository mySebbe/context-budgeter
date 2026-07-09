import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from context_budgeter.budgeter import build_report, rank_files, scan_repository


class ContextBudgeterTest(unittest.TestCase):
    def test_scan_repository_ignores_common_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "src").mkdir()
            (repo / "src" / "auth.py").write_text("def login(): pass\n", encoding="utf-8")
            (repo / ".git").mkdir()
            (repo / ".git" / "config").write_text("secret\n", encoding="utf-8")
            (repo / "node_modules").mkdir()
            (repo / "node_modules" / "lib.js").write_text("ignored\n", encoding="utf-8")
            (repo / "demo.egg-info").mkdir()
            (repo / "demo.egg-info" / "PKG-INFO").write_text("generated metadata\n", encoding="utf-8")

            files = scan_repository(repo)

        paths = {item.relative_path for item in files}
        self.assertEqual(paths, {"src/auth.py"})
        self.assertGreater(files[0].estimated_tokens, 0)

    def test_scan_repository_respects_root_gitignore_patterns(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".gitignore").write_text("local.env\nreports/\n*.tmp\n", encoding="utf-8")
            (repo / "app.py").write_text("print('ok')\n", encoding="utf-8")
            (repo / "local.env").write_text("TOKEN=example\n", encoding="utf-8")
            (repo / "notes.tmp").write_text("scratch\n", encoding="utf-8")
            (repo / "reports").mkdir()
            (repo / "reports" / "large.md").write_text("generated\n", encoding="utf-8")

            scan = scan_repository(repo)

        self.assertEqual({item.relative_path for item in scan}, {".gitignore", "app.py"})
        self.assertEqual(scan.stats.source, "filesystem-fallback")
        self.assertEqual(scan.stats.exclusions["gitignore"], 3)

    def test_scan_repository_uses_git_ls_files_and_reports_gitignore_exclusions(self):
        if shutil.which("git") is None:
            self.skipTest("git is required for repository-aware scan coverage")
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
            (repo / ".gitignore").write_text("ignored.env\n*.tmp\n", encoding="utf-8")
            (repo / "included.py").write_text("print('included')\n", encoding="utf-8")
            (repo / "ignored.env").write_text("TOKEN=private\n", encoding="utf-8")
            (repo / "tracked.tmp").write_text("tracked content\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", ".gitignore", "included.py"], check=True)
            subprocess.run(["git", "-C", str(repo), "add", "--force", "tracked.tmp"], check=True)

            scan = scan_repository(repo)

        self.assertEqual(scan.stats.source, "git-ls-files")
        self.assertEqual(
            {item.relative_path for item in scan},
            {".gitignore", "included.py", "tracked.tmp"},
        )
        self.assertGreaterEqual(scan.stats.exclusions["gitignore"], 1)

    def test_scan_repository_rejects_symlinks_and_root_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "repo"
            outside = base / "outside.txt"
            repo.mkdir()
            outside.write_text("outside secret\n", encoding="utf-8")
            (repo / "inside.txt").write_text("inside text\n", encoding="utf-8")
            try:
                (repo / "inside-link.txt").symlink_to(repo / "inside.txt")
                (repo / "outside-link.txt").symlink_to(outside)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlinks are unavailable: {exc}")

            scan = scan_repository(repo)

        self.assertEqual({item.relative_path for item in scan}, {"inside.txt"})
        self.assertEqual(scan.stats.exclusions["symlink"], 1)
        self.assertEqual(scan.stats.exclusions["outside-root"], 1)

    def test_scan_repository_rejects_malformed_git_paths_outside_root(self):
        if shutil.which("git") is None:
            self.skipTest("git is required for repository-aware scan coverage")
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "--quiet", str(repo)], check=True)

            def fake_git_paths(_repo_root, _scope, *, ignored):
                return [] if ignored else ["../outside.txt"]

            with patch("context_budgeter.budgeter._git_paths", side_effect=fake_git_paths):
                scan = scan_repository(repo)

        self.assertEqual(scan.stats.source, "git-ls-files")
        self.assertEqual(scan.stats.exclusions["outside-root"], 1)
        self.assertEqual(scan, [])

    def test_scan_repository_skips_binary_content_deterministically(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "readme.txt").write_text("plain text\n", encoding="utf-8")
            (repo / "nul.dat").write_bytes(b"text\x00data")
            (repo / "invalid.txt").write_bytes(b"\xff\xfe\x00")
            (repo / "image.png").write_bytes(b"not really an image")

            scan = scan_repository(repo)
            report = build_report(
                repo,
                rank_files(scan, "plain text"),
                "plain text",
                100,
                scan_stats=scan.stats,
            )

        self.assertEqual({item.relative_path for item in scan}, {"readme.txt"})
        self.assertEqual(scan.stats.exclusions["binary"], 3)
        self.assertIn("Counts by reason", report)
        self.assertIn("`binary`: 3", report)

    def test_rank_files_prioritizes_query_terms_in_path_and_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "auth_service.py").write_text("oauth token refresh\n", encoding="utf-8")
            (repo / "billing.py").write_text("invoice total\n", encoding="utf-8")
            files = scan_repository(repo)

            ranked = rank_files(files, "fix oauth login token")

        self.assertEqual(ranked[0].relative_path, "auth_service.py")
        self.assertGreater(ranked[0].rank_score, ranked[1].rank_score)

    def test_scan_repository_skips_files_over_byte_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "small.py").write_text("print('small')\n", encoding="utf-8")
            (repo / "large.log").write_text("x" * 101, encoding="utf-8")

            files = scan_repository(repo, max_file_bytes=100)

        self.assertEqual({item.relative_path for item in files}, {"small.py"})

    def test_build_report_respects_budget_and_recommends_ignores(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "app.py").write_text("important logic\n" * 20, encoding="utf-8")
            (repo / "dist").mkdir()
            (repo / "dist" / "bundle.js").write_text("generated\n", encoding="utf-8")
            ranked = rank_files(scan_repository(repo), "important")

            report = build_report(
                repo,
                ranked,
                task_query="important",
                token_budget=10,
                include_ignore_recommendations=True,
            )

        self.assertIn("# Context Budget Report", report)
        self.assertIn("app.py", report)
        self.assertIn("Ignore Recommendations", report)
        self.assertIn("dist/", report)

    def test_cli_writes_report_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "README.md").write_text("oauth login docs\n", encoding="utf-8")
            output = Path(tmp) / "report.md"
            env = os.environ.copy()
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "context_budgeter",
                    str(repo),
                    "--query",
                    "oauth login",
                    "--budget",
                    "100",
                    "--max-file-bytes",
                    "1000",
                    "--output",
                    str(output),
                    "--recommend-ignore",
                ],
                check=True,
                text=True,
                capture_output=True,
                env=env,
            )

            self.assertIn(str(output), result.stdout)
            self.assertIn("README.md", output.read_text(encoding="utf-8"))

    def test_cli_json_reports_exclusion_counts_and_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / "README.md").write_text("oauth login docs\n", encoding="utf-8")
            (repo / "payload.bin").write_bytes(b"\x00\x01\x02")
            env = os.environ.copy()
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "context_budgeter",
                    str(repo),
                    "--query",
                    "oauth login",
                    "--budget",
                    "100",
                    "--format",
                    "json",
                ],
                check=True,
                text=True,
                capture_output=True,
                env=env,
            )

        report = json.loads(result.stdout)
        self.assertEqual(report["scan"]["source"], "filesystem-fallback")
        self.assertEqual(report["exclusions"]["by_reason"]["binary"], 1)
        self.assertEqual(report["exclusions"]["total"], 1)
        self.assertEqual(report["selected_files"][0]["path"], "README.md")


if __name__ == "__main__":
    unittest.main()
