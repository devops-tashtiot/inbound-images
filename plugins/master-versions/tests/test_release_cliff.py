"""
cliff.toml integration tests for parse_pr_body.

These tests load the REAL cliff.toml from disk and run parse_pr_body against
realistic PR bodies. They are intentionally coupled to cliff.toml — if you
change the commit_parsers, some tests may fail and you should review whether
the new expectations are correct.

If you are bringing your own cliff.toml, run this file to verify that your
parser configuration handles all the expected scenarios correctly:

    python3 test_release_cliff.py
"""

import os
import unittest
import types

_src_path   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "release.py")
_cliff_toml = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cliff.toml")

release_module = types.ModuleType("release")
with open(_src_path) as _f:
    exec(compile(_f.read(), _src_path, "exec"), release_module.__dict__)

parse_pr_body       = release_module.parse_pr_body
load_cliff_parsers  = release_module.load_cliff_parsers

parsers, bump_cfg = load_cliff_parsers(_cliff_toml)


class TestRealPrBody(unittest.TestCase):
    """
    End-to-end parse tests using the real cliff.toml parsers.
    These tests break if cliff.toml changes — that is intentional: it forces
    you to review whether the expectations are still correct for the new config.
    """

    def test_typical_pr_body_with_prose(self):
        """
        Checks: a realistic PR body that looks like what a developer would
                actually write — markdown prose, a checklist, blank lines,
                and several commit lines scattered through it.
                Only the commit lines (matching cliff.toml parsers + [...])
                should produce entries; all prose is silently ignored.

        PR body:
          ## Summary
          This PR adds OAuth2 login and fixes a race condition in the worker pool.
          Also bumps the base image for all plugins.

          ## Releases
          feat(OAuth2 login)[nati]: add Google and GitHub providers
          fix(worker pool race)[plugins/docker]: lock shared map with mutex
          breaking(REST API)[nati, check]!: remove deprecated /v1 endpoints
          other(scope)[nati]: this is a no-op marker

          ## Checklist
          - [x] Tests pass
          - [x] Changelog reviewed
          feat(base image)[plugins/*]: bump alpine to 3.19

        Expected:
          "nati":         feat commit + breaking commit + other commit (with checklist lines as continuation)
          "check":        breaking commit
          "plugins/docker": fix commit
          "plugins/*":    feat commit (literal wildcard — not expanded here)
          prose lines before any commit: ignored
          checklist lines after "other" commit: absorbed as continuation of that commit
        """
        body = (
            "## Summary\n"
            "This PR adds OAuth2 login and fixes a race condition in the worker pool.\n"
            "Also bumps the base image for all plugins.\n"
            "\n"
            "## Releases\n"
            "feat[nati]: add Google and GitHub providers\n"
            "fix[plugins/docker]: lock shared map with mutex\n"
            "breaking[nati, check]!: remove deprecated /v1 endpoints\n"
            "other[nati]: this is a no-op marker\n"
            "\n"
            "## Checklist\n"
            "- [x] Tests pass\n"
            "- [x] Changelog reviewed\n"
            "feat[plugins/*]: bump alpine to 3.19\n"
        )
        result = parse_pr_body(body, parsers)

        self.assertIn("nati", result)
        self.assertIn("feat: add Google and GitHub providers",        result["nati"])
        self.assertIn("breaking!: remove deprecated /v1 endpoints",  result["nati"])

        # "other" commit absorbs the checklist lines that follow it as continuation
        other_commit = next((c for c in result["nati"] if "other: this is a no-op marker" in c), None)
        self.assertIsNotNone(other_commit, "expected an 'other' commit in nati")
        # checklist lines are NOT separate location keys (they're continuation text, not commit headers)
        self.assertIn("## Checklist",          other_commit)
        self.assertIn("- [x] Tests pass",      other_commit)
        self.assertIn("- [x] Changelog reviewed", other_commit)

        self.assertIn("check", result)
        self.assertIn("breaking!: remove deprecated /v1 endpoints",  result["check"])

        self.assertIn("plugins/docker", result)
        self.assertIn("fix: lock shared map with mutex",              result["plugins/docker"])

        self.assertIn("plugins/*", result)
        self.assertIn("feat: bump alpine to 3.19",                    result["plugins/*"])

        self.assertNotIn("## Summary", result)
        self.assertNotIn("x", result)
        self.assertNotIn(" ", result)

    def test_multiline_commit_in_pr_body(self):
        """
        Checks: a multi-line commit description inside a realistic PR body
                is collected correctly when the description after ':' is empty.
                The next commit header stops the continuation.

        PR body:
          feat(auth flow)[nati]:
            Replace basic auth with OAuth2.
            Supports Google, GitHub, and GitLab providers.
            Adds token refresh logic and session expiry handling.

          fix(null pointer)[check]: unrelated fix — stops the block above

        Expected:
          "nati"  → one commit containing all three description lines
          "check" → {"fix(null pointer): unrelated fix — stops the block above"}
        """
        body = (
            "feat[nati]:\n"
            "  Replace basic auth with OAuth2.\n"
            "  Supports Google, GitHub, and GitLab providers.\n"
            "  Adds token refresh logic and session expiry handling.\n"
            "\n"
            "fix[check]: unrelated fix — stops the block above\n"
        )
        result = parse_pr_body(body, parsers)

        self.assertIn("nati", result)
        nati_commit = next(iter(result["nati"]))
        self.assertIn("  Replace basic auth with OAuth2.",                       nati_commit)
        self.assertIn("  Supports Google, GitHub, and GitLab providers.",        nati_commit)
        self.assertIn("  Adds token refresh logic and session expiry handling.", nati_commit)
        self.assertIn("feat:",                                                   nati_commit)

        self.assertEqual(result["check"],
                         {"fix: unrelated fix — stops the block above"})

    def test_no_commit_lines_in_pr(self):
        """
        Checks: a PR body with only prose and no commit lines returns {}.
                Common for PRs that don't trigger a release.

        PR body:
          ## Refactor
          Cleaned up dead code in the auth module.
          No functional changes — no release needed.

        Expected: {}
        """
        body = (
            "## Refactor\n"
            "Cleaned up dead code in the auth module.\n"
            "No functional changes — no release needed.\n"
        )
        result = parse_pr_body(body, parsers)
        self.assertEqual(result, {})

    def test_checklist_brackets_not_confused_with_locations(self):
        """
        Checks: markdown checklist items like "- [x] Tests pass" do NOT match
                the parser because "- [x]" does not start at position 0 with a
                known type pattern. The '[x]' bracket is NOT mistaken for a location.
                Instead, those lines are absorbed as continuation text of the
                preceding commit.

        PR body:
          feat(auth)[nati]: add login
          - [x] Tests pass
          - [ ] Docs updated

        Expected: only "nati" in result (no "x" or other spurious keys);
                  the "nati" commit contains the checklist lines as continuation.
        """
        body = (
            "feat[nati]: add login\n"
            "- [x] Tests pass\n"
            "- [ ] Docs updated\n"
        )
        result = parse_pr_body(body, parsers)
        self.assertIn("nati", result)
        self.assertEqual(len(result), 1)  # no spurious location keys
        nati_commit = next(iter(result["nati"]))
        self.assertIn("feat: add login",   nati_commit)
        self.assertIn("- [x] Tests pass",  nati_commit)
        self.assertIn("- [ ] Docs updated", nati_commit)

    def test_all_known_types_accepted(self):
        """
        Checks: every parser entry defined in cliff.toml is recognised by
                parse_pr_body when followed by [...].

        Reads cliff.toml directly with tomllib — adding or removing a parser
        entry automatically updates what this test covers.

        For each parser the type name is extracted with plain string ops:
          "^feat\\((.*?)\\)" → lstrip('^') → split on '\\(' → [0] → "feat"
          "^feat"            → lstrip('^') → split on '\\(' → [0] → "feat"
        Each parser gets its own unique location (loc0, loc1, ...) so
        consecutive commit lines don't absorb each other as continuation.

        Expected: every parser entry routes to its dedicated location.
        """
        import tomllib

        with open(_cliff_toml, "rb") as f:
            cliff = tomllib.load(f)

        raw_parsers = cliff.get("git", {}).get("commit_parsers", [])
        self.assertTrue(raw_parsers, "no commit_parsers found in cliff.toml")

        lines = []
        expected = []
        for i, p in enumerate(raw_parsers):
            msg = p.get("message", "")
            if not msg:
                continue
            # plain string extraction — no regex on the regex pattern
            type_name = msg.lstrip('^').split(r'\(')[0]
            loc = f"loc{i}"
            is_scoped = r'\(' in msg
            if is_scoped:
                lines.append(f"{type_name}(scope)[{loc}]: test msg")
                expected.append((loc, f"{type_name}(scope): test msg"))
            else:
                lines.append(f"{type_name}[{loc}]: test msg")
                expected.append((loc, f"{type_name}: test msg"))

        body = "\n".join(lines) + "\n"
        result = parse_pr_body(body, parsers)

        for loc, expected_commit in expected:
            self.assertIn(loc, result, f"parser for '{expected_commit}' not routed to {loc}")
            self.assertIn(expected_commit, result[loc])

    def test_bracket_in_description_not_reparsed_as_location(self):
        """
        Checks: when the description itself looks like a commit line with brackets,
                only the FIRST bracket pair immediately after the type/scope is
                stripped as the location. Any '[...]' inside the description is
                left verbatim and NOT treated as a second location.

        PR body:
          feat(nati)[root]: feat(nati)[root]

        Parsing steps:
          ^feat\\((.*)\\) matches "feat(nati)" at position 0-9.
          Next char is '[' → bracket_re matches "[root]" (positions 9-15).
          commit_str = "feat(nati)" + ": feat(nati)[root]"
                     = "feat(nati): feat(nati)[root]"
          Location = "root"

          The description is never fed back through _match_line — it is
          part of the same line, not a separate line from body.splitlines().

        Expected:
          result == {"root": {"feat(nati): feat(nati)[root]"}}
          Only one location key — the "[root]" inside the description is
          not re-parsed as a location.
        """
        body = "feat[root]: feat[root]\n"
        result = parse_pr_body(body, parsers)

        self.assertEqual(result, {"root": {"feat: feat[root]"}})
        self.assertEqual(len(result), 1)

    def test_unknown_type_ignored(self):
        """
        Checks: a commit type not listed in cliff.toml (e.g. 'chore') is
                silently ignored by parse_pr_body — no entry produced.
                git-cliff would filter it anyway, but parse_pr_body never
                even routes it.

        Example:
          "chore(deps)[nati]: bump dependencies"
          'chore' matches no parser pattern → line skipped.
          Result: {}
        """
        body = "chore(deps)[nati]: bump dependencies\n"
        result = parse_pr_body(body, parsers)
        self.assertEqual(result, {})

    def test_changelog_level_with_real_parsers(self):
        """
        Checks: changelog_level enforcement works correctly when using the real
                cliff.toml parsers — not the inline fixture.

        Why this matters: this test confirms level enforcement works correctly with
        the real cliff.toml bare patterns (e.g. "^feat", "^fix").
        This test confirms level enforcement is independent of which patterns are
        loaded and that the real cliff.toml patterns still produce correct routing
        after the level filter is applied.

        Note: cliff.toml currently has NO scoped parsers — only bare patterns
        (^feat, ^fix, ^breaking, ^other). So commit lines must NOT include a (scope)
        part — the '[' must follow the type directly (e.g. "feat[nati]: msg").

        Body (changelog_level=1):
          ## PR description prose      ← ignored (no parser matches + no bracket)
          feat[nati]: add OAuth2 login ← "nati" has 0 slashes → level 1 → ACCEPT
          fix[plugins/docker]: fix bug ← "plugins/docker" has 1 slash → level 1 expects 0 → SKIP
          breaking[harel]: drop v1 API ← "harel" has 0 slashes → level 1 → ACCEPT

        Expected:
          "nati"  in result with commit "feat: add OAuth2 login"
          "harel" in result with commit "breaking: drop v1 API"
          "plugins/docker" NOT in result
        """
        body = (
            "## PR description prose\n"
            "feat[nati]: add OAuth2 login\n"
            "fix[plugins/docker]: fix bug\n"
            "breaking[harel]: drop v1 API\n"
        )
        result = parse_pr_body(body, parsers, changelog_level=1)

        self.assertIn("nati", result)
        self.assertIn("feat: add OAuth2 login", result["nati"])

        self.assertIn("harel", result)
        self.assertIn("breaking: drop v1 API", result["harel"])

        self.assertNotIn("plugins/docker", result)


if __name__ == "__main__":
    unittest.main()
