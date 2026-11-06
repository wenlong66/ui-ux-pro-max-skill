"""Every script invocation in the shipped skill markdown resolves from the skill directory.

Regression test for #474. The sub-skills ship in two copies (.claude/skills/<skill>/
for the plugin, cli/assets/skills/<skill>/ for CLI installs) and land in layouts where
neither the project root nor ~/.claude/skills/ is a valid anchor: the plugin cache, a
project's .claude/skills/, ~/.claude/skills/ (--global), or a manual copy. The one anchor
that exists in all of them is the skill's own directory, so documented commands use
`scripts/<file>` for the skill's own scripts and `../<skill>/scripts/<file>` for a
sibling sub-skill (the sub-skills are always installed side by side).

This test extracts every `python|python3|node|bash <path>` invocation from every
markdown file under both trees and asserts that the path is skill-relative and names a
file that ships. All skills use their loaded SKILL.md location as the anchor,
including when a plugin host supplies no plugin environment variables.
The grep-based path contract in check-asset-sync.yml is the negative
side (no home-, project- or variable-rooted paths anywhere, code included); this is
the positive side (every documented invocation points at a real file).
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = next(
    parent for parent in Path(__file__).resolve().parents
    if (parent / "scripts" / "generate-catalog-summary.py").is_file()
)
SKILL_TREES = ("cli/assets/skills", ".claude/skills")
INVOCATION = re.compile(r'(?<![\w/.-])(?:python3?|node|bash)\s+"?([^\s"`\']+\.(?:py|cjs|js|mjs|sh))')


def shipped_invocations():
    for tree in SKILL_TREES:
        for skill_dir in sorted((REPO / tree).iterdir()):
            if not skill_dir.is_dir():
                continue
            for md in sorted(skill_dir.rglob("*.md")):
                for lineno, line in enumerate(md.read_text(encoding="utf-8").splitlines(), 1):
                    for match in INVOCATION.finditer(line):
                        yield skill_dir, md, lineno, match.group(1)


def resolve(skill_dir, path):
    """Return (target, None) for a skill-relative path, or (None, reason)."""
    if path.startswith("scripts/"):
        return skill_dir / path, None
    if path.startswith("../"):
        parts = path.split("/")
        if len(parts) > 3 and parts[2] == "scripts" and (skill_dir.parent / parts[1]).is_dir():
            return skill_dir.parent / parts[1] / "/".join(parts[2:]), None
        return None, "a sibling invocation must be ../<skill>/scripts/<file> and the sibling must ship"
    return None, "not skill-relative (expected scripts/<file> or ../<skill>/scripts/<file>)"


class SkillScriptPathsTest(unittest.TestCase):
    def test_skill_markdown_uses_portable_runtime_instructions(self):
        incompatible = ("AskUserQuestion", "$ARGUMENTS", "/slides:create", "/brand:update")
        problems = []
        for md in (REPO / ".claude/skills").rglob("*.md"):
            text = md.read_text(encoding="utf-8")
            for token in incompatible:
                if token in text:
                    problems.append(f"{md.relative_to(REPO)}: {token}")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_plugin_search_from_another_project_without_plugin_environment(self):
        source = REPO / ".claude" / "skills" / "ui-ux-pro-max"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installed = root / "plugin cache" / "ui-ux-pro-max"
            for directory in ("data", "scripts"):
                shutil.copytree(source / directory, installed / directory,
                                ignore=shutil.ignore_patterns("__pycache__", "tests"))
            project = root / "user project"
            project.mkdir()
            env = {key: value for key, value in os.environ.items()
                   if not key.endswith("_PLUGIN_ROOT")}
            documented_path = INVOCATION.search(
                (source / "SKILL.md").read_text(encoding="utf-8")
            ).group(1)
            script = installed / documented_path
            for args in (
                ["keyboard focus modal", "--domain", "ux"],
                ["beauty spa wellness service", "--design-system", "--persist",
                 "-p", "Compatibility Smoke", "--output-dir", str(project)],
            ):
                completed = subprocess.run(
                    [sys.executable, str(script), *args, "--json"],
                    cwd=project, env=env, capture_output=True, text=True,
                    encoding="utf-8", check=True,
                )
                result = json.loads(completed.stdout)
                self.assertTrue(result.get("results") or result.get("design_system"))
            self.assertTrue(list(project.glob("design-system/*/MASTER.md")))
            self.assertFalse((installed / "design-system").exists())

    def test_every_shipped_markdown_invocation_resolves_from_the_skill_directory(self):
        problems, seen = [], 0
        for skill_dir, md, lineno, path in shipped_invocations():
            seen += 1
            target, reason = resolve(skill_dir, path)
            if reason is None and not target.is_file():
                reason = f"no such file: {target}"
            if reason:
                problems.append(f"{md.relative_to(REPO)}:{lineno}: {path} -- {reason}")
        # Guard against a silently broken extractor: the two trees carry well over
        # a hundred documented invocations between them.
        self.assertGreater(seen, 100, f"extractor found only {seen} invocations")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
