import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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

    def test_rank_files_prioritizes_query_terms_in_path_and_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "auth_service.py").write_text("oauth token refresh\n", encoding="utf-8")
            (repo / "billing.py").write_text("invoice total\n", encoding="utf-8")
            files = scan_repository(repo)

            ranked = rank_files(files, "fix oauth login token")

        self.assertEqual(ranked[0].relative_path, "auth_service.py")
        self.assertGreater(ranked[0].rank_score, ranked[1].rank_score)

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
                    "--output",
                    str(output),
                    "--recommend-ignore",
                ],
                check=True,
                text=True,
                capture_output=True,
            )

            self.assertIn(str(output), result.stdout)
            self.assertIn("README.md", output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
