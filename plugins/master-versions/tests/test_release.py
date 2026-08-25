import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch, MagicMock, mock_open

import types

_src_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "release.py")
release_module = types.ModuleType("release")
release_module.__file__ = _src_path
with open(_src_path) as _f:
    exec(compile(_f.read(), _src_path, "exec"), release_module.__dict__)

parse_pr_body                  = release_module.parse_pr_body
_known_commit_types            = release_module._known_commit_types
_expand_locations               = release_module._expand_locations
_bump_subject                   = release_module._bump_subject
_retrieve_message               = release_module._retrieve_message
_retrieve_pull_request_message  = release_module._retrieve_pull_request_message
_retrieve_manual_message        = release_module._retrieve_manual_message
_retrieve_push_message          = release_module._retrieve_push_message
release                         = release_module.release


# ---------------------------------------------------------------------------
# Parsers fixture — mirrors the default cliff.toml commit_parsers.
# Tests use this instead of loading the real file so they stay self-contained.
# ---------------------------------------------------------------------------

PARSERS = [
    {"message": r"^breaking", "group": "🚀 🚀 Breaking Changes", "bump_type": "", "skip": False},
    {"message": r"^feat",     "group": "✨ Features",             "bump_type": "", "skip": False},
    {"message": r"^fix",      "group": "🐛 Bug Fixes",            "bump_type": "", "skip": False},
    {"message": r"^other",    "group": "📦 other",                "bump_type": "", "skip": True},
]


# ---------------------------------------------------------------------------
# Class 1 — TestParsePrBody
#
# parse_pr_body(body, parsers) -> dict[location -> set[commit_str]]
#
# Each line in the body must match one of the cliff.toml commit_parsers
# patterns, followed immediately by [location]. The [location] is routing
# info only — it is stripped from the commit string before the result is
# stored. git-cliff then receives a clean conventional commit.
# ---------------------------------------------------------------------------

class TestParsePrBody(unittest.TestCase):

    def test_1_single_component(self):
        """
        Checks: a standard line with type, location, and description
                produces one entry in the result dict.

        Example:
          Input:  "feat[nati]: add login"
          Parser matches "^feat" at start, finds '[' immediately after.
          Bracket content "nati" becomes the location key.
          Brackets are stripped → commit_str = "feat: add login"
          Result: {"nati": {"feat: add login"}}
        """
        result = parse_pr_body("feat[nati]: add login", PARSERS)
        self.assertEqual(result, {"nati": {"feat: add login"}})

    def test_2_no_scope(self):
        """
        Checks: type without scope — the parser pattern (^feat) matches,
                bracket immediately follows, location and description work normally.

        Example:
          Input:  "feat[nati]: add login"
          "^feat" matches positions 0-4. Next char is '['.
          Brackets stripped → commit_str = "feat: add login"
          Result: {"nati": {"feat: add login"}}
        """
        result = parse_pr_body("feat[nati]: add login", PARSERS)
        self.assertEqual(result, {"nati": {"feat: add login"}})

    def test_3_multiple_locations(self):
        """
        Checks: comma-separated locations in [...] produce one entry per location,
                each carrying the same commit string.

        Example:
          Input:  "feat[nati, check]: msg"
          Locations = ["nati", "check"] (spaces stripped)
          commit_str = "feat: msg"
          Result: {"nati": {"feat: msg"}, "check": {"feat: msg"}}
        """
        result = parse_pr_body("feat[nati, check]: msg", PARSERS)
        self.assertEqual(result, {
            "nati":  {"feat: msg"},
            "check": {"feat: msg"},
        })

    def test_4_root_location(self):
        """
        Checks: empty brackets [] mean the repo root — the location key is "".

        Example:
          Input:  "feat[]: add login"
          Bracket content = "" → location = ""  (root)
          commit_str = "feat: add login"
          Result: {"": {"feat: add login"}}
        """
        result = parse_pr_body("feat[]: add login", PARSERS)
        self.assertEqual(result, {"": {"feat: add login"}})

    def test_5_bang_preserved(self):
        """
        Checks: the ! (breaking-change bang) that comes after [] is kept in the
                commit string — it is NOT part of the location brackets.

        Example:
          Input:  "feat[nati]!: add login"
          Brackets stripped: "[nati]" removed.
          Remaining line: "feat!: add login"  ← bang is still there
          Result: {"nati": {"feat!: add login"}}
        """
        result = parse_pr_body("feat[nati]!: add login", PARSERS)
        self.assertEqual(result, {"nati": {"feat!: add login"}})

    def test_6_uppercase_type_not_matched(self):
        """
        Checks: uppercase type is silently ignored — cliff.toml patterns are
                lowercase anchors (^feat, ^fix, ...) and re.match is case-sensitive.

        Example:
          Input:  "FEAT[nati]: add login"
          "^feat" does NOT match "FEAT..." → no parser matches → line skipped.
          Result: {}
        """
        result = parse_pr_body("FEAT[nati]: add login", PARSERS)
        self.assertEqual(result, {})

    def test_7_leading_space_not_matched(self):
        """
        Checks: a line with leading whitespace is NOT matched — current_line is
                NOT stripped before passing to _match_line, so "  feat..." has a
                space at position 0 and "^feat" does not match it.
                Per README: type must start at the very beginning of the line.

        Example:
          Input: "  feat[nati]: add login"  (two leading spaces)
          re.match("^feat", "  feat...") → None  → line silently ignored.
          Result: {}
        """
        result = parse_pr_body("  feat[nati]: add login", PARSERS)
        self.assertEqual(result, {})

    def test_8_unknown_type_not_matched(self):
        """
        Checks: a type not listed in cliff.toml commit_parsers is silently skipped.
                git-cliff would filter it anyway (filter_unconventional=true).

        Example:
          Input:  "chore[nati]: bump deps"
          "chore" matches no parser pattern → line skipped.
          Result: {}
        """
        result = parse_pr_body("chore[nati]: bump deps", PARSERS)
        self.assertEqual(result, {})

    def test_9_wildcard_location_literal(self):
        """
        Checks: [*] is treated as a literal location "*" by parse_pr_body.
                Wildcard expansion to actual directories happens later in
                _expand_locations — parse_pr_body itself does NOT expand it.

        Example:
          Input:  "feat[*]: upgrade all"
          Location key = "*"  (literal string, not expanded here)
          commit_str = "feat: upgrade all"
          Result: {"*": {"feat: upgrade all"}}
        """
        result = parse_pr_body("feat[*]: upgrade all", PARSERS)
        self.assertEqual(result, {"*": {"feat: upgrade all"}})

    def test_10_empty_body(self):
        """
        Checks: empty string body returns empty dict — nothing to parse.

        Example:
          Input:  ""
          Result: {}
        """
        result = parse_pr_body("", PARSERS)
        self.assertEqual(result, {})

    def test_11_no_parsers_returns_empty_with_error(self):
        """
        Checks: if parsers list is empty, parse_pr_body prints an error and
                returns {} immediately — it cannot determine which lines are commits.

        Example:
          parsers = []
          Input:  "feat[nati]: add login"
          Output: {} (error printed to stdout)
        """
        result = parse_pr_body("feat[nati]: add login", [])
        self.assertEqual(result, {})

    def test_12_multi_line_description(self):
        """
        Checks: continuation lines are ALWAYS collected after a commit line, until
                the next line that matches a parser pattern with '[...}'.
                This includes commits with a non-empty description — everything
                after the commit line (that isn't a new commit header) is appended.

        Example:
          Input:
            "feat[nati]:"
            "  Replace basic auth with OAuth2."
            "  Supports Google and GitHub."
            ""
            "fix[check]: unrelated fix"

          "feat[nati]:" → commit_str = "feat:"
          Continuation lines collected until next commit header: ["  Replace basic auth with OAuth2.", "  Supports Google and GitHub.", ""]
          Final commit_str = "feat:   Replace basic auth with OAuth2.\n  Supports Google and GitHub.\n"
          "fix[check]: unrelated fix" → separate entry.

          Result: {
            "nati":  {commit containing "Replace basic auth with OAuth2."},
            "check": {"fix: unrelated fix"},
          }
        """
        body = (
            "feat[nati]:\n"
            "  Replace basic auth with OAuth2.\n"
            "  Supports Google and GitHub.\n"
            "\n"
            "fix[check]: unrelated fix"
        )
        result = parse_pr_body(body, PARSERS)
        self.assertIn("nati", result)
        self.assertIn("check", result)
        nati_commit = next(iter(result["nati"]))
        # Continuation lines are stored raw (no strip) — leading spaces preserved
        self.assertIn("  Replace basic auth with OAuth2.", nati_commit)
        self.assertIn("  Supports Google and GitHub.", nati_commit)
        self.assertEqual(result["check"], {"fix: unrelated fix"})

    def test_13_multiple_components_multiple_lines(self):
        """
        Checks: a body with several commit lines produces one dict entry per
                unique location, with each location holding its own commit set.

        Example:
          Input:
            "feat[nati]: add login"
            "fix[check]: fix socket"

          Result: {
            "nati":  {"feat: add login"},
            "check": {"fix: fix socket"},
          }
        """
        body = "feat[nati]: add login\nfix[check]: fix socket"
        result = parse_pr_body(body, PARSERS)
        self.assertEqual(result, {
            "nati":  {"feat: add login"},
            "check": {"fix: fix socket"},
        })

    def test_14_no_scope_bang_multiple_locations(self):
        """
        Checks: type + bang + multiple comma-separated locations all
                work together — each location gets the same commit_str (with bang).

        Example:
          Input:  "feat[nati, check]!: big change"
          "^feat" matches, brackets "[nati, check]" found immediately after.
          Brackets stripped → commit_str = "feat!: big change"
          Locations = ["nati", "check"]
          Result: {"nati": {"feat!: big change"}, "check": {"feat!: big change"}}
        """
        result = parse_pr_body("feat[nati, check]!: big change", PARSERS)
        self.assertEqual(result, {
            "nati":  {"feat!: big change"},
            "check": {"feat!: big change"},
        })


# ---------------------------------------------------------------------------
# Class 1b — TestParsePrBodyEdgeCases
# ---------------------------------------------------------------------------

class TestParsePrBodyEdgeCases(unittest.TestCase):

    def test_A_malformed_bracket_in_continuation(self):
        """
        Checks: a line with no closing bracket (e.g. "feat[nati: ...") does NOT
                match _match_line, so inside a continuation block it is collected
                as description text rather than being treated as a new commit.

        Example:
          "feat[nati]:"                               ← empty description → continuation mode
          "feat[nati: this has no closing bracket"    ← _match_line returns None → continuation text
          "fix[check]: stops continuation"            ← valid → stops collection

          Result:
            "nati"  → commit contains "feat[nati: this has no closing bracket"
            "check" → {"fix: stops continuation"}
        """
        body = (
            "feat[nati]:\n"
            "feat[nati: this has no closing bracket\n"
            "fix[check]: stops continuation"
        )
        result = parse_pr_body(body, PARSERS)
        self.assertIn("nati", result)
        self.assertIn("check", result)
        nati_commit = next(iter(result["nati"]))
        self.assertIn("feat[nati: this has no closing bracket", nati_commit)
        self.assertEqual(result["check"], {"fix: stops continuation"})

    def test_B_same_location_two_lines_both_in_set(self):
        """
        Checks: two different commit lines targeting the same location both appear
                in the set — they are NOT deduplicated because they are different strings.

        Example:
          "feat[nati]: add login"
          "fix[nati]: fix crash"

          Result: {"nati": {"feat: add login", "fix: fix crash"}}
        """
        body = "feat[nati]: add login\nfix[nati]: fix crash"
        result = parse_pr_body(body, PARSERS)
        self.assertEqual(result, {"nati": {"feat: add login", "fix: fix crash"}})

    def test_C_other_type_skip_still_routed(self):
        """
        Checks: parse_pr_body does NOT check the skip=True flag in parsers.
                The "other" pattern matches, brackets are stripped, and the commit
                is routed to the location. git-cliff will later skip it — not parse_pr_body.

        Example:
          "other[nati]: some no-op"
          "other" pattern matches → commit_str = "other: some no-op"
          Result: {"nati": {"other: some no-op"}}
        """
        result = parse_pr_body("other[nati]: some no-op", PARSERS)
        self.assertEqual(result, {"nati": {"other: some no-op"}})

    def test_D_duplicate_line_deduplicated_by_set(self):
        """
        Checks: the exact same commit line appearing twice produces only one entry
                in the set — Python sets automatically deduplicate identical strings.

        Example:
          "feat[nati]: add login"   ← first occurrence
          "feat[nati]: add login"   ← identical duplicate

          Result: {"nati": {"feat: add login"}}  (one entry, not two)
        """
        body = "feat[nati]: add login\nfeat[nati]: add login"
        result = parse_pr_body(body, PARSERS)
        self.assertEqual(result, {"nati": {"feat: add login"}})

    def test_E_location_with_slash(self):
        """
        Checks: a location containing a slash is stored as-is in the dict key.
                Slug conversion (slash → hyphen) happens later in release(), not here.

        Example:
          "feat[plugins/docker]: add registry support"
          Location key = "plugins/docker"  (slash preserved)
          Result: {"plugins/docker": {"feat: add registry support"}}
        """
        result = parse_pr_body("feat[plugins/docker]: add registry support", PARSERS)
        self.assertEqual(result, {"plugins/docker": {"feat: add registry support"}})

    def test_F_spaces_around_locations_stripped(self):
        """
        Checks: spaces around each location in a comma-separated list are stripped
                via loc.strip(), producing clean location keys.

        Example:
          "feat[nati,  check  ,  base/argo ]: msg"
          raw_locs = "nati,  check  ,  base/argo "
          After split + strip: ["nati", "check", "base/argo"]
          Result: {
            "nati":     {"feat: msg"},
            "check":    {"feat: msg"},
            "base/argo":{"feat: msg"},
          }
        """
        result = parse_pr_body("feat[nati,  check  ,  base/argo ]: msg", PARSERS)
        self.assertEqual(result, {
            "nati":      {"feat: msg"},
            "check":     {"feat: msg"},
            "base/argo": {"feat: msg"},
        })

    def test_G_parser_with_empty_message_skipped(self):
        """
        Checks: a parser entry with message="" is skipped by _match_line
                (the "if not msg: continue" guard). The next valid parser still matches.

        Example:
          parsers = [{"message": "", "group": "X"}, <standard feat parser>]
          Input: "feat[nati]: add login"
          Empty-message parser skipped → "^feat" parser matches.
          Result: {"nati": {"feat: add login"}}
        """
        parsers_with_empty = [{"message": "", "group": "X", "bump_type": "", "skip": False}] + PARSERS
        result = parse_pr_body("feat[nati]: add login", parsers_with_empty)
        self.assertEqual(result, {"nati": {"feat: add login"}})

    def test_H_bang_empty_description_multiline(self):
        """
        Checks: bang (!) with empty description collects continuation lines.
                The bang is preserved in commit_str. Continuation is collected
                until the next commit header (always, regardless of description).

        Example:
          "feat[nati]!:"                             ← empty description after bracket removal
          "  Big breaking change description."        ← continuation line (raw, spaces preserved)
          "fix[check]: stops it"                     ← stops continuation

          commit_str after join: "feat!:   Big breaking change description."
          Result:
            "nati"  → commit contains "Big breaking change description."
            "check" → {"fix: stops it"}
        """
        body = (
            "feat[nati]!:\n"
            "  Big breaking change description.\n"
            "fix[check]: stops it"
        )
        result = parse_pr_body(body, PARSERS)
        self.assertIn("nati", result)
        nati_commit = next(iter(result["nati"]))
        self.assertIn("  Big breaking change description.", nati_commit)
        self.assertIn("feat!:", nati_commit)
        self.assertEqual(result["check"], {"fix: stops it"})

    def test_I_trailing_comma_adds_root(self):
        """
        Checks: a trailing comma in [location, ] produces an empty string after
                split + strip, which is treated as the repo root location ("").

        Example:
          "feat[nati, ]: msg"
          raw_locs = "nati, "
          split(",") → ["nati", " "]
          strip each → ["nati", ""]   ← "" = root
          Result: {"nati": {"feat: msg"}, "": {"feat: msg"}}
        """
        result = parse_pr_body("feat[nati, ]: msg", PARSERS)
        self.assertEqual(result, {
            "nati": {"feat: msg"},
            "":     {"feat: msg"},
        })

    def test_L_nested_bracket_in_content_becomes_continuation(self):
        """
        Checks: a line with a '[' inside the bracket content (e.g. "feat[na[ti]: msg")
                fails bracket_re because [^[\\]]*  forbids '[' inside brackets.
                _match_line returns None → inside a continuation block the line is
                collected as description text, NOT treated as a new commit.

        Example:
          "feat[nati]:"              ← empty description → continuation mode
          "feat[na[ti]: malformed line"    ← bracket_re fails → becomes continuation text
          "fix[check]: stops it"     ← valid commit → stops continuation

          Result:
            "nati"  → commit contains "feat[na[ti]: malformed line"
            "check" → {"fix: stops it"}
        """
        body = (
            "feat[nati]:\n"
            "feat[na[ti]: malformed line\n"
            "fix[check]: stops it"
        )
        result = parse_pr_body(body, PARSERS)
        self.assertIn("nati", result)
        nati_commit = next(iter(result["nati"]))
        self.assertIn("feat[na[ti]: malformed line", nati_commit)
        self.assertEqual(result["check"], {"fix: stops it"})

    def test_M_breaking_without_scope(self):
        """
        Checks: the "^breaking" parser entry works correctly —
                type immediately followed by brackets, no parentheses needed.

        Example:
          "breaking[nati]: remove v1 api"
          "^breaking" matches at 0-8. stripped[8] = '[' ✓
          Brackets stripped → commit_str = "breaking: remove v1 api"
          Result: {"nati": {"breaking: remove v1 api"}}
        """
        result = parse_pr_body("breaking[nati]: remove v1 api", PARSERS)
        self.assertEqual(result, {"nati": {"breaking: remove v1 api"}})


    def test_N_non_empty_description_collects_continuation(self):
        """
        Checks: continuation is collected even when the commit has a non-empty
                description. Any following lines that don't match a parser pattern
                are appended to the commit string, not silently ignored.

        Example:
          "other[nati]: no-op marker"   ← has description, NOT bare ':'
          "some extra prose"            ← not a commit → continuation
          "fix[check]: stops it"        ← commit header → stops collection

          Result:
            "nati"  → commit contains both "other: no-op marker" and "some extra prose"
            "check" → {"fix: stops it"}
        """
        body = (
            "other[nati]: no-op marker\n"
            "some extra prose\n"
            "fix[check]: stops it\n"
        )
        result = parse_pr_body(body, PARSERS)
        self.assertIn("nati", result)
        nati_commit = next(iter(result["nati"]))
        self.assertIn("other: no-op marker", nati_commit)
        self.assertIn("some extra prose", nati_commit)
        self.assertEqual(result["check"], {"fix: stops it"})

    def test_J_spaces_only_bracket_is_root(self):
        """
        Checks: bracket content consisting only of spaces strips to "" → root location,
                identical in behaviour to empty brackets [].

        Example:
          "feat[   ]: msg"
          raw_locs = "   "
          split(",") → ["   "]
          strip → [""]   ← root
          Result: {"": {"feat: msg"}}
        """
        result = parse_pr_body("feat[   ]: msg", PARSERS)
        self.assertEqual(result, {"": {"feat: msg"}})


# ---------------------------------------------------------------------------
# Class 2 — TestKnownCommitTypes
#
# _known_commit_types(parsers) -> set[str]
#
# Returns the raw message pattern strings from cliff.toml commit_parsers —
# no extraction, no transformation. These are the regexes used by
# _match_line to identify valid commit headers.
# ---------------------------------------------------------------------------

class TestKnownCommitTypes(unittest.TestCase):

    def test_1_returns_all_raw_patterns(self):
        """
        Checks: all 4 message patterns from PARSERS are returned as-is.

        Example:
          PARSERS has 4 entries. Each has a "message" key.
          Result must contain exactly those 4 strings, unchanged.
          e.g. "^feat" is in the result — not "feat".
        """
        result = _known_commit_types(PARSERS)
        expected = {p["message"] for p in PARSERS}
        self.assertEqual(result, expected)

    def test_2_empty_parsers_returns_empty_set(self):
        """
        Checks: empty parsers list → empty set. No patterns to return.

        Example:
          _known_commit_types([]) == set()
        """
        self.assertEqual(_known_commit_types([]), set())

    def test_3_parsers_without_message_key_skipped(self):
        """
        Checks: parser entries missing the "message" key are silently skipped.

        Example:
          parsers = [{"group": "Features"}]  ← no "message" key
          Result: set()  (nothing to add)
        """
        self.assertEqual(_known_commit_types([{"group": "Features"}]), set())


# ---------------------------------------------------------------------------
# Class 3 — TestExpandLocations
#
# _expand_locations(location_to_commits, root_path, exclude_regex) -> dict
#
# Expands wildcard locations ([*] and [base/*]) to concrete directory paths
# and applies SCOPE_EXCLUDE_REGEX to filter unwanted locations.
# ---------------------------------------------------------------------------

class TestExpandLocations(unittest.TestCase):

    def test_1_no_wildcard_no_exclude_returns_same(self):
        """
        Checks: when there are no wildcards and no exclude regex, the original
                dict is returned unchanged (fast-path — no filesystem access).

        Example:
          Input:  {"nati": {"feat: msg"}}
          No '*' in any location key. exclude_regex = "".
          Result: exactly the same dict object (identity check).
        """
        d = {"nati": {"feat: msg"}}
        result = _expand_locations(d, "/repo", "")
        self.assertIs(result, d)

    def test_2_star_wildcard_expands_to_subdirs(self):
        """
        Checks: location "*" expands to all direct subdirectories of root_path.

        Example:
          Input:  {"*": {"feat: msg"}}
          Filesystem has dirs: ["a", "b"] under /repo
          Result: {"a": {"feat: msg"}, "b": {"feat: msg"}}
          (the "*" key disappears — replaced by concrete dirs)
        """
        with patch("os.listdir", return_value=["a", "b"]), \
             patch("os.path.isdir", return_value=True):
            result = _expand_locations({"*": {"feat: msg"}}, "/repo", "")
        self.assertEqual(result, {"a": {"feat: msg"}, "b": {"feat: msg"}})

    def test_3_prefix_wildcard_expands_subdirs(self):
        """
        Checks: location "plugins/*" expands to all subdirs of root_path/plugins/.
                The prefix is prepended to each discovered dir name.

        Example:
          Input:  {"plugins/*": {"feat: msg"}}
          os.listdir("/repo/plugins") returns ["docker", "git"]
          Result: {"plugins/docker": {"feat: msg"}, "plugins/git": {"feat: msg"}}
        """
        with patch("os.listdir", return_value=["docker", "git"]), \
             patch("os.path.isdir", return_value=True):
            result = _expand_locations({"plugins/*": {"feat: msg"}}, "/repo", "")
        self.assertEqual(result, {
            "plugins/docker": {"feat: msg"},
            "plugins/git":    {"feat: msg"},
        })

    def test_4_exclude_regex_filters_wildcard_results(self):
        """
        Checks: SCOPE_EXCLUDE_REGEX is applied after wildcard expansion.
                Matching locations are silently skipped.

        Example:
          Input:  {"*": {"feat: msg"}}
          Filesystem dirs: ["nati", "docs"]
          exclude_regex = "^docs$"
          "docs" matches → skipped. "nati" does not match → kept.
          Result: {"nati": {"feat: msg"}}
        """
        with patch("os.listdir", return_value=["nati", "docs"]), \
             patch("os.path.isdir", return_value=True):
            result = _expand_locations({"*": {"feat: msg"}}, "/repo", "^docs$")
        self.assertEqual(result, {"nati": {"feat: msg"}})

    def test_5_exclude_regex_filters_explicit_location(self):
        """
        Checks: SCOPE_EXCLUDE_REGEX also applies to explicit (non-wildcard) locations.
                If someone writes [docs] in their message, and docs is excluded, it is
                silently skipped without error.

        Example:
          Input:  {"docs": {"feat: msg"}}
          exclude_regex = "^docs$"
          "docs" matches → skipped.
          Result: {}
        """
        result = _expand_locations({"docs": {"feat: msg"}}, "/repo", "^docs$")
        self.assertEqual(result, {})

    def test_6_explicit_location_with_non_wildcard_star_suffix_skipped_by_exclude(self):
        """
        Checks: an explicit location that looks like a partial wildcard
                (e.g. "plugins/*dsfsf") but does NOT end with exactly "/*"
                is treated as a literal path and passes through the else branch.
                If it matches the exclude regex it is skipped without UnboundLocalError.

        Regression test for bug where the else branch used `subdir` (only defined
        inside wildcard for-loops) instead of `display`, causing:
          UnboundLocalError: cannot access local variable 'subdir'

        Example:
          Input:  {"plugins/*dsfsf": {"feat: msg"}}
          exclude_regex = "plugins"
          Falls into else branch (does not end with "/*").
          "plugins/*dsfsf" matches regex → skipped via display variable (not subdir).
          Result: {}
        """
        result = _expand_locations({"plugins/*dsfsf": {"feat: msg"}}, "/repo", "plugins")
        self.assertEqual(result, {})


# ---------------------------------------------------------------------------
# Class 3b — TestBumpSubject
#
# _bump_subject(commit) decides what text is sent to git-cliff's --bump call.
# Upstream git-cliff bug (conventional_commits=false): custom bump regexes are
# only reliably applied to a single-line commit — see
# https://github.com/orhun/git-cliff/issues/1476. If a commit's first line
# already has real text after the type/colon, any additional lines must be
# dropped (matches the upstream issue exactly: a body attached without a
# blank-line separator silently degrades to a patch bump). But if the first
# line is a BARE "type:" with nothing after it (the real description deferred
# to the next line), dropping everything leaves git-cliff with no description
# at all, which independently also falls back to patch — so in that specific
# case the first non-blank continuation line must be folded in instead.
# ---------------------------------------------------------------------------

class TestBumpSubject(unittest.TestCase):

    def test_1_single_line_commit_unchanged(self):
        self.assertEqual(_bump_subject("feat: add login"), "feat: add login")

    def test_2_subject_with_body_drops_the_body(self):
        """
        Checks: when the first line already has a real description, extra
                lines are dropped (this is the original, still-needed fix —
                passing them through re-triggers the upstream git-cliff bug).
        """
        commit = "feat: real subject\nSecond line of body."
        self.assertEqual(_bump_subject(commit), "feat: real subject")

    def test_3_bare_type_folds_in_next_line(self):
        """
        Checks: 'feat:' with nothing after the colon, and the actual
                description entirely on the next line, folds that line in
                instead of leaving git-cliff with a bare, unrecognizable
                'feat:'.
        """
        commit = "feat:\nnatiii"
        self.assertEqual(_bump_subject(commit), "feat: natiii")

    def test_4_bare_type_with_blank_continuation_lines_skipped(self):
        """
        Checks: blank continuation lines between the bare type line and the
                real description are skipped over, not folded in as-is.
        """
        commit = "breaking:\n\n\nmajor change"
        self.assertEqual(_bump_subject(commit), "breaking: major change")

    def test_5_bare_type_with_no_continuation_at_all(self):
        """
        Checks: a truly empty commit ('feat:' with nothing after it at all,
                anywhere) is returned as-is — nothing to fold in.
        """
        self.assertEqual(_bump_subject("feat:"), "feat:")


# ---------------------------------------------------------------------------
# Class 3c — TestRetrieveMessage
#
# _retrieve_message() dispatches on CI_PIPELINE_EVENT to determine how to get
# the release message:
#   pull_request -> Bitbucket Server REST API (PLUGIN_BITBUCKET_TOKEN,
#                    CI_FORGE_URL, CI_REPO_OWNER, CI_REPO_NAME,
#                    CI_COMMIT_PULL_REQUEST)
#   manual        -> PLUGIN_MESSAGE env var as-is
#   anything else -> git log -1 --pretty=%B, extracting the DESCRIPTION
#                    section (see INCIDENT_PULL_REQUEST_CLOSED_TRAP.md)
# ---------------------------------------------------------------------------

class TestRetrieveMessage(unittest.TestCase):

    def test_1_pull_request_success(self):
        env = {
            "PLUGIN_BITBUCKET_TOKEN": "tok123",
            "CI_FORGE_URL": "https://bitbucket.example.com",
            "CI_REPO_OWNER": "PROJ",
            "CI_REPO_NAME": "myrepo",
            "CI_COMMIT_PULL_REQUEST": "42",
        }
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"id": 42, "title": "My PR", "description": "feat[nati]: add login"}'
        with patch.dict(os.environ, env, clear=False), \
             patch.object(release_module, "urlopen", return_value=mock_response) as mock_urlopen:
            result = _retrieve_pull_request_message()
        self.assertEqual(result, "feat[nati]: add login")
        req = mock_urlopen.call_args[0][0]
        self.assertIn("PROJ", req.full_url)
        self.assertIn("myrepo", req.full_url)
        self.assertIn("42", req.full_url)
        self.assertEqual(req.get_header("Authorization"), "Bearer tok123")

    def test_2_pull_request_missing_env_var_returns_none(self):
        """Checks: a missing required env var is caught and returns None (not a crash)."""
        with patch.dict(os.environ, {}, clear=True):
            result = _retrieve_pull_request_message()
        self.assertIsNone(result)

    def test_3_pull_request_api_failure_returns_none(self):
        env = {
            "PLUGIN_BITBUCKET_TOKEN": "tok",
            "CI_FORGE_URL": "https://bitbucket.example.com",
            "CI_REPO_OWNER": "PROJ",
            "CI_REPO_NAME": "myrepo",
            "CI_COMMIT_PULL_REQUEST": "1",
        }
        with patch.dict(os.environ, env, clear=False), \
             patch.object(release_module, "urlopen", side_effect=Exception("network error")):
            result = _retrieve_pull_request_message()
        self.assertIsNone(result)

    def test_4_manual_message_present(self):
        with patch.dict(os.environ, {"PLUGIN_MESSAGE": "feat[nati]: add login"}, clear=False):
            result = _retrieve_manual_message()
        self.assertEqual(result, "feat[nati]: add login")

    def test_5_manual_message_empty_returns_none(self):
        with patch.dict(os.environ, {"PLUGIN_MESSAGE": ""}, clear=False):
            result = _retrieve_manual_message()
        self.assertIsNone(result)

    def test_6_push_message_extracts_description_section(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = (
            "Merge pull request #12 from feature-branch\n\n"
            "METADATA\n"
            "Title: Add login\n\n"
            "DESCRIPTION\n"
            "feat[nati]: add login\n"
        )
        with patch.object(release_module, "run_command", return_value=mock_result):
            result = _retrieve_push_message()
        self.assertEqual(result, "feat[nati]: add login")

    def test_7_push_message_no_description_marker_uses_full_message(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Direct push, not a merge commit\n"
        with patch.object(release_module, "run_command", return_value=mock_result):
            result = _retrieve_push_message()
        self.assertEqual(result, "Direct push, not a merge commit")

    def test_7a_push_message_git_log_failure_returns_none(self):
        """
        Checks: if 'git log -1 --pretty=%B' itself fails (e.g. corrupted repo,
        not a git checkout at all), _retrieve_push_message() returns None
        instead of silently treating the empty/garbage stdout as "no
        DESCRIPTION marker, use the full message as-is" — that would mask a
        real git failure as an empty-but-valid message.
        """
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "fatal: not a git repository"
        with patch.object(release_module, "run_command", return_value=mock_result):
            result = _retrieve_push_message()
        self.assertIsNone(result)

    def test_8_dispatch_pull_request_event(self):
        with patch.dict(os.environ, {"CI_PIPELINE_EVENT": "pull_request"}, clear=False), \
             patch.object(release_module, "_retrieve_pull_request_message", return_value="from-pr") as mock_fn:
            result = _retrieve_message()
        mock_fn.assert_called_once()
        self.assertEqual(result, "from-pr")

    def test_9_dispatch_manual_event(self):
        with patch.dict(os.environ, {"CI_PIPELINE_EVENT": "manual"}, clear=False), \
             patch.object(release_module, "_retrieve_manual_message", return_value="from-manual") as mock_fn:
            result = _retrieve_message()
        mock_fn.assert_called_once()
        self.assertEqual(result, "from-manual")

    def test_10_dispatch_push_event_default(self):
        with patch.dict(os.environ, {"CI_PIPELINE_EVENT": "push"}, clear=False), \
             patch.object(release_module, "_retrieve_push_message", return_value="from-push") as mock_fn:
            result = _retrieve_message()
        mock_fn.assert_called_once()
        self.assertEqual(result, "from-push")


# ---------------------------------------------------------------------------
# Class 4 — TestRelease
#
# release() — full integration test with env vars and subprocess mocked.
#
# release() reads PLUGIN_* env vars, calls parse_pr_body, _expand_locations,
# then for each location runs git-cliff twice (bump + changelog) via
# run_command(). Tags are appended to PLUGIN_OUTPUT_TAGS_FILE if set.
# ---------------------------------------------------------------------------

class TestRelease(unittest.TestCase):

    def _run(self, message, dirs_exist=(), cliff_stdout="nati-1.1.0",
             cliff_returncode=0, cliff_stderr="", list_dirs=(),
             exclude_regex="", output_tags_file="", changelog_level="1"):
        """
        Helper: patches env vars and all filesystem/subprocess calls, then
        calls release() and returns the mock for run_command.
        changelog_level defaults to "1" (required by release()).
        Pass "" to simulate the variable being unset.

        The message is injected via the "manual" retrieval path
        (CI_PIPELINE_EVENT=manual + PLUGIN_MESSAGE) so no real file or
        subprocess/network access is needed to supply it.
        """
        mock_result = MagicMock()
        mock_result.returncode = cliff_returncode
        mock_result.stdout = cliff_stdout
        mock_result.stderr = cliff_stderr

        env = {
            "PLUGIN_MESSAGE":             message,
            "CI_PIPELINE_EVENT":          "manual",
            "PLUGIN_BASE_PATH":                "/repo",
            "PLUGIN_SCOPE_EXCLUDE_REGEX": exclude_regex,
            "PLUGIN_OUTPUT_TAGS_FILE":    output_tags_file,
            "PLUGIN_VERBOSE":             "0",
            "PLUGIN_CHANGELOG_LEVEL":     changelog_level,
        }

        with patch.dict(os.environ, env, clear=False), \
             patch("os.path.exists", return_value=True), \
             patch("os.path.isdir", side_effect=lambda p: any(p.endswith(d) for d in dirs_exist)), \
             patch("os.listdir", return_value=list(list_dirs)), \
             patch.object(release_module, "load_cliff_parsers", return_value=(PARSERS, {})), \
             patch("builtins.open", mock_open()), \
             patch.object(release_module, "run_command", return_value=mock_result) as mock_cmd:
            release()

        return mock_cmd

    def test_1_single_component_first_release(self):
        """
        Checks: a single component with no existing tag triggers git-cliff
                calls — one for --bump --bumped-version, one to generate the
                changelog — and the tag-pattern flag names the correct component.

        Example:
          PLUGIN_MESSAGE = "feat[nati]: add login"
          PLUGIN_BASE_PATH    = "/repo"
          No existing tags (run_command for git tag returns empty stdout).
          git cliff --bump --bumped-version → "nati-1.0.0"  (first release)
          git cliff --tag 'nati-1.0.0' --output ... → writes CHANGELOG.md
          run_command called: once for tag-list, once for bump, once for changelog = 3 calls.
        """
        mock_cmd = self._run(
            "feat[nati]: add login",
            dirs_exist=["nati"],
            cliff_stdout="nati-1.1.0",
        )
        # tag list + bump + changelog = 3 calls
        self.assertGreaterEqual(mock_cmd.call_count, 2)
        calls_str = " ".join(str(c) for c in mock_cmd.call_args_list)
        self.assertIn("nati", calls_str)

    def test_2_missing_directory_skipped(self):
        """
        Checks: if the component directory does not exist on disk, that location
                is skipped and no git-cliff call is made for it.

        Example:
          PLUGIN_MESSAGE = "feat[ghost]: add login"
          /repo/ghost does NOT exist (isdir returns False for it)
          The cliff command is definitely not called.
          Result: {}
        """
        mock_cmd = self._run(
            "feat[ghost]: add login",
            dirs_exist=[],   # ghost does not exist
        )
        calls_str = " ".join(str(c) for c in mock_cmd.call_args_list)
        self.assertNotIn("git cliff", calls_str)

    def test_3_message_with_no_commit_lines_does_nothing(self):
        """
        Checks: a message with no commit lines → parse_pr_body returns {} →
                release() prints "No release commits detected" and exits
                early. run_command is never called.

        Note: a genuinely *empty* PLUGIN_MESSAGE is rejected earlier, by
        _retrieve_manual_message() itself (see TestRetrieveMessage) — this
        test covers the distinct case of a non-empty message that simply
        contains no releasable commit lines.

        Example:
          PLUGIN_MESSAGE = "just some prose, no commits here"
          Result: run_command.call_count == 0
        """
        mock_cmd = self._run("just some prose, no commits here", dirs_exist=[])
        mock_cmd.assert_not_called()

    def test_4_cliff_changelog_failure_exits_with_error(self):
        """
        Checks: if git-cliff's CHANGELOG-writing call (the --output/--prepend
                step in Phase B) returns a non-zero exit code, release() prints
                an error message and now (unlike the old "silently continue"
                behavior) fails the whole run with SystemExit(1) — a dropped
                CHANGELOG.md must not be reported as a successful run.

        Every OTHER run_command call (tag lookups, shallow-check, fetch, the
        --bump call) succeeds — only the final changelog-write call fails —
        so this isolates exactly the Phase B failure path, not an earlier one.

        Example:
          PLUGIN_MESSAGE = "feat[nati]: add login"
          git cliff changelog (--output/--prepend) returncode = 1
          release() prints ">>> ERROR generating changelog..." then exits(1).
        """
        def fake_run_command(cmd):
            result = MagicMock()
            if "--output" in cmd or "--prepend" in cmd:
                result.returncode = 1
                result.stdout = ""
                result.stderr = "fatal: not a git repo"
            else:
                result.returncode = 0
                result.stdout = ""   # no existing tag -> first-release path, no --bump call needed
                result.stderr = ""
            return result

        env = {
            "PLUGIN_MESSAGE":             "feat[nati]: add login",
            "CI_PIPELINE_EVENT":          "manual",
            "PLUGIN_BASE_PATH":                "/repo",
            "PLUGIN_SCOPE_EXCLUDE_REGEX": "",
            "PLUGIN_OUTPUT_TAGS_FILE":    "",
            "PLUGIN_VERBOSE":             "0",
            "PLUGIN_CHANGELOG_LEVEL":     "1",
        }
        with patch.dict(os.environ, env, clear=False), \
             patch("os.path.exists", return_value=True), \
             patch("os.path.isdir", side_effect=lambda p: p.endswith("nati")), \
             patch("os.listdir", return_value=[]), \
             patch.object(release_module, "load_cliff_parsers", return_value=(PARSERS, {})), \
             patch("builtins.open", mock_open()), \
             patch.object(release_module, "run_command", side_effect=fake_run_command):
            with self.assertRaises(SystemExit) as cm:
                release()
            self.assertEqual(cm.exception.code, 1)

    def test_4a_early_git_failure_exits_with_error(self):
        """
        Checks: if a git command that gates correctness fails BEFORE any
                component is processed (e.g. 'git rev-parse
                --is-shallow-repository', used to decide whether the clone
                needs unshallowing), release() fails fast with SystemExit(1)
                instead of silently guessing (e.g. treating a failed check as
                "not shallow") and possibly computing a wrong version.
        """
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "fatal: not a git repository"

        env = {
            "PLUGIN_MESSAGE":             "feat[nati]: add login",
            "CI_PIPELINE_EVENT":          "manual",
            "PLUGIN_BASE_PATH":                "/repo",
            "PLUGIN_SCOPE_EXCLUDE_REGEX": "",
            "PLUGIN_OUTPUT_TAGS_FILE":    "",
            "PLUGIN_VERBOSE":             "0",
            "PLUGIN_CHANGELOG_LEVEL":     "1",
        }
        with patch.dict(os.environ, env, clear=False), \
             patch("os.path.exists", return_value=True), \
             patch("os.path.isdir", return_value=True), \
             patch("os.listdir", return_value=[]), \
             patch.object(release_module, "load_cliff_parsers", return_value=(PARSERS, {})), \
             patch("builtins.open", mock_open()), \
             patch.object(release_module, "run_command", return_value=mock_result):
            with self.assertRaises(SystemExit) as cm:
                release()
            self.assertEqual(cm.exception.code, 1)


# ---------------------------------------------------------------------------
# Class 4b — TestChangelogLevel
#
# parse_pr_body(body, parsers, changelog_level=N) level-enforcement tests.
#
# PLUGIN_CHANGELOG_LEVEL controls which location depths are accepted.
# The formula is:
#   level 0  → only root (empty string location)
#   level N  → location must have exactly N-1 forward slashes
#              e.g. level 1 → "nati" (0 slashes)
#                   level 2 → "plugins/docker" (1 slash)
#
# Tests 7 and 8 verify that release() itself enforces the variable as required.
# ---------------------------------------------------------------------------

class TestChangelogLevel(unittest.TestCase):

    def test_1_level1_accepts_toplevel_rejects_nested(self):
        """
        Checks: changelog_level=1 accepts locations with 0 slashes (top-level dirs)
                and skips locations with 1+ slashes (nested paths).

        Why this matters: the most common monorepo setup — all components sit
        directly under PLUGIN_BASE_PATH. Setting level=1 enforces that no nested path
        can accidentally trigger a release.

        Body:
          feat[nati]: add dashboard       ← "nati"          has 0 slashes → level 1 → ACCEPT
          fix[plugins/docker]: fix socket ← "plugins/docker" has 1 slash  → level 1 expects 0 → SKIP

        Expected: {"nati": {"feat: add dashboard"}}
                  "plugins/docker" must not appear.
        """
        body = (
            "feat[nati]: add dashboard\n"
            "fix[plugins/docker]: fix socket\n"
        )
        result = parse_pr_body(body, PARSERS, changelog_level=1)
        self.assertIn("nati", result)
        self.assertEqual(result["nati"], {"feat: add dashboard"})
        self.assertNotIn("plugins/docker", result)

    def test_2_level2_accepts_nested_rejects_toplevel(self):
        """
        Checks: changelog_level=2 accepts locations with exactly 1 slash and
                skips locations with 0 slashes.

        Why this matters: a mono-of-monorepo layout where all versioned components
        live one level deep (e.g. plugins/docker, plugins/git). Level=2 ensures
        someone cannot accidentally release the "plugins" parent by writing [plugins].

        Body:
          feat[plugins/docker]: add auth  ← "plugins/docker" has 1 slash → level 2 → ACCEPT
          fix[nati]: fix crash            ← "nati"          has 0 slashes → level 2 expects 1 → SKIP

        Expected: {"plugins/docker": {"feat: add auth"}}
                  "nati" must not appear.
        """
        body = (
            "feat[plugins/docker]: add auth\n"
            "fix[nati]: fix crash\n"
        )
        result = parse_pr_body(body, PARSERS, changelog_level=2)
        self.assertIn("plugins/docker", result)
        self.assertEqual(result["plugins/docker"], {"feat: add auth"})
        self.assertNotIn("nati", result)

    def test_3_level0_accepts_root_only(self):
        """
        Checks: changelog_level=0 accepts ONLY the empty-bracket root location [].
                Any non-empty location is skipped.

        Why this matters: a single-component repo where only the root is ever
        released. Level=0 prevents accidentally triggering a nested release.

        Body:
          feat[]: release root   ← empty string → root → ACCEPT
          feat[nati]: add thing  ← "nati" non-empty → SKIP

        Expected: {"": {"feat: release root"}}
                  "nati" must not appear.
        """
        body = (
            "feat[]: release root\n"
            "feat[nati]: add thing\n"
        )
        result = parse_pr_body(body, PARSERS, changelog_level=0)
        self.assertIn("", result)
        self.assertEqual(result[""], {"feat: release root"})
        self.assertNotIn("nati", result)

    def test_3a_multiple_levels_accepts_several_depths(self):
        """
        Checks: changelog_level="1,2" accepts BOTH top-level (depth 1) and
                one-level-nested (depth 2) locations in the same run.

        Why this matters: a monorepo that versions both flat components (nati)
        and nested ones (plugins/docker) in one release — the whole point of
        allowing an array of depths instead of a single fixed one.

        Body (changelog_level="1,2"):
          feat[nati]: add dashboard       ← depth 1 → in {1,2} → ACCEPT
          fix[plugins/docker]: fix socket ← depth 2 → in {1,2} → ACCEPT

        Expected: both appear.
        """
        body = (
            "feat[nati]: add dashboard\n"
            "fix[plugins/docker]: fix socket\n"
        )
        result = parse_pr_body(body, PARSERS, changelog_level="1,2")
        self.assertIn("nati", result)
        self.assertIn("plugins/docker", result)
        self.assertEqual(result["nati"], {"feat: add dashboard"})
        self.assertEqual(result["plugins/docker"], {"fix: fix socket"})

    def test_3b_multiple_levels_rejects_depth_outside_set(self):
        """
        Checks: changelog_level="1,3" accepts depth 1 and depth 3 but still
                SKIPS a depth-2 location that falls between the allowed values —
                the set is exact-membership, not a min/max range.

        Body (changelog_level="1,3"):
          feat[nati]: a                        ← depth 1 → ACCEPT
          feat[base/uv/0.11.29]: b             ← depth 3 → ACCEPT
          feat[plugins/docker]: c              ← depth 2 → not in {1,3} → SKIP
        """
        body = (
            "feat[nati]: a\n"
            "feat[base/uv/0.11.29]: b\n"
            "feat[plugins/docker]: c\n"
        )
        result = parse_pr_body(body, PARSERS, changelog_level="1,3")
        self.assertIn("nati", result)
        self.assertIn("base/uv/0.11.29", result)
        self.assertNotIn("plugins/docker", result)

    def test_3c_normalize_changelog_levels_forms(self):
        """
        Checks: _normalize_changelog_levels() coerces every supported input form
                (int, plain str, comma-separated str with spaces, iterable) into a
                set of ints, rejects bad input, and returns None for None.
        """
        norm = release_module._normalize_changelog_levels
        self.assertIsNone(norm(None))
        self.assertEqual(norm(2), {2})
        self.assertEqual(norm("2"), {2})
        self.assertEqual(norm("2,3,4"), {2, 3, 4})
        self.assertEqual(norm(" 2 , 3 ,4 "), {2, 3, 4})
        self.assertEqual(norm([1, 2, 2]), {1, 2})
        with self.assertRaises(ValueError):
            norm("abc")
        with self.assertRaises(ValueError):
            norm("-1")
        with self.assertRaises(ValueError):
            norm("")

    def test_4_multilocation_skipped_if_any_location_fails(self):
        """
        Checks: if ANY location in a comma-separated list fails the level check,
                the ENTIRE line is skipped — even locations that would individually pass.

        Why this matters: a line like feat[nati, plugins/docker] targets two
        components at different depths. Partially accepting it would be wrong —
        either the whole line is valid for this repo's layout or none of it is.

        Body (changelog_level=1):
          feat[nati, plugins/docker]: shared change
          ↑ "nati":          0 slashes → ok for level 1
          ↑ "plugins/docker": 1 slash  → FAIL for level 1 → whole line skipped

        Expected: {} — neither nati nor plugins/docker appears.
        """
        body = "feat[nati, plugins/docker]: shared change\n"
        result = parse_pr_body(body, PARSERS, changelog_level=1)
        self.assertEqual(result, {})

    def test_5_failing_line_ends_previous_continuation(self):
        """
        Checks: a level-failing commit line acts as a commit boundary — it stops
                the continuation collection of the preceding commit (because
                _match_line returns truthy for any valid commit structure, regardless
                of level), finalises that commit with its accumulated lines, and is
                then skipped itself.

        Why this matters: without this behaviour, the continuation lines of the
        first commit would "bleed" past the rejected line and potentially merge
        with the next commit's body.

        Body (changelog_level=2):
          feat[plugins/docker]:          ← ACCEPT, starts continuation
            description line 1           ← continuation
            description line 2           ← continuation
          fix[nati]: wrong level         ← _match_line matches → BREAKS inner loop
                                            → first commit finalised with its 2 lines
                                            → level check fails (0 slashes, expected 1) → SKIP
          feat[plugins/auth]: next       ← ACCEPT, starts fresh

        Expected:
          "plugins/docker" commit contains both description lines
          "plugins/auth"   commit = "feat: next"
          "nati" absent
        """
        body = (
            "feat[plugins/docker]:\n"
            "  description line 1\n"
            "  description line 2\n"
            "fix[nati]: wrong level\n"
            "feat[plugins/auth]: next\n"
        )
        result = parse_pr_body(body, PARSERS, changelog_level=2)

        self.assertIn("plugins/docker", result)
        docker_commit = next(iter(result["plugins/docker"]))
        self.assertIn("  description line 1", docker_commit)
        self.assertIn("  description line 2", docker_commit)

        self.assertIn("plugins/auth", result)
        self.assertEqual(result["plugins/auth"], {"feat: next"})

        self.assertNotIn("nati", result)

    def test_6_release_missing_changelog_level_exits_early(self):
        """
        Checks: when PLUGIN_CHANGELOG_LEVEL is not set (empty string is falsy),
                release() prints an error and returns before calling run_command.

        Why this matters: PLUGIN_CHANGELOG_LEVEL is required. Without it the plugin
        has no idea what depth the user expects, so running silently would be wrong.
        The empty-string check mirrors how pipeline tools set "not configured" vars.

        Expected: run_command never called.
        """
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "nati-1.1.0"
        mock_result.stderr = ""

        env = {
            "PLUGIN_BASE_PATH":                "/repo",
            "PLUGIN_SCOPE_EXCLUDE_REGEX": "",
            "PLUGIN_OUTPUT_TAGS_FILE":    "",
            "PLUGIN_VERBOSE":             "0",
            "PLUGIN_CHANGELOG_LEVEL":     "",   # empty → falsy → triggers required-var error
        }
        with patch.dict(os.environ, env, clear=False), \
            patch("os.path.exists", return_value=True), \
            patch("os.path.isdir", return_value=True), \
            patch.object(release_module, "run_command", return_value=mock_result) as mock_cmd:
            with self.assertRaises(SystemExit) as cm:
              release()
            self.assertEqual(cm.exception.code, 1)

        mock_cmd.assert_not_called()

    def test_7_release_invalid_changelog_level_exits_early(self):
        """
        Checks: when PLUGIN_CHANGELOG_LEVEL is set to a non-integer string,
                int() raises ValueError, which is caught, an error is printed,
                and release() exits(1) before calling run_command.

        Why this matters: a typo like PLUGIN_CHANGELOG_LEVEL=one or a copy-paste
        mistake should fail loudly rather than silently continuing or crashing
        with an unhandled exception deep inside the loop.

        Expected: run_command never called; process exits with code 1.
        """
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "nati-1.1.0"
        mock_result.stderr = ""

        env = {
            "PLUGIN_BASE_PATH":                "/repo",
            "PLUGIN_SCOPE_EXCLUDE_REGEX": "",
            "PLUGIN_OUTPUT_TAGS_FILE":    "",
            "PLUGIN_VERBOSE":             "0",
            "PLUGIN_CHANGELOG_LEVEL":     "abc",  # non-integer → int() raises ValueError
        }
        with patch.dict(os.environ, env, clear=False), \
             patch("os.path.exists", return_value=True), \
             patch("os.path.isdir", return_value=True), \
             patch.object(release_module, "run_command", return_value=mock_result) as mock_cmd:
            with self.assertRaises(SystemExit) as cm:
                release()
            self.assertEqual(cm.exception.code, 1)

        mock_cmd.assert_not_called()


# ---------------------------------------------------------------------------
# Class 5 — TestCliffTomlResolution
#
# Verifies the three-tier cliff.toml lookup in release():
#   1. PLUGIN_CLIFF_TOML explicitly set → use that path
#   2. ./cliff.toml exists in the working directory → use it
#   3. Neither → fall back to the bundled cliff.toml next to release.py
# ---------------------------------------------------------------------------

class TestCliffTomlResolution(unittest.TestCase):

    def _run_with_toml_env(self, cliff_toml_env, workspace_has_cliff):
        """
        Calls release() and returns the --config path used in git-cliff commands.
        cliff_toml_env  : value for PLUGIN_CLIFF_TOML ("" means not set)
        workspace_has_cliff : whether ./cliff.toml exists in the working dir
        """
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "nati-1.1.0"
        mock_result.stderr = ""

        bundled = os.path.join(os.path.dirname(_src_path), "cliff.toml")

        def fake_exists(p):
            if p == "./cliff.toml":
                return workspace_has_cliff
            return True  # everything else (dirs, changelog, etc.) exists

        env = {
            "PLUGIN_MESSAGE":             "feat[nati]: add login",
            "CI_PIPELINE_EVENT":          "manual",
            "PLUGIN_BASE_PATH":                "/repo",
            "PLUGIN_SCOPE_EXCLUDE_REGEX": "",
            "PLUGIN_OUTPUT_TAGS_FILE":    "",
            "PLUGIN_VERBOSE":             "0",
            "PLUGIN_CHANGELOG_LEVEL":     "1",
        }
        if cliff_toml_env:
            env["PLUGIN_CLIFF_TOML"] = cliff_toml_env
        else:
            env.pop("PLUGIN_CLIFF_TOML", None)

        with patch.dict(os.environ, env, clear=False), \
             patch("os.path.exists", side_effect=fake_exists), \
             patch("os.path.isdir", side_effect=lambda p: p.endswith("nati")), \
             patch("os.listdir", return_value=[]), \
             patch.object(release_module, "load_cliff_parsers", return_value=(PARSERS, {})), \
             patch("builtins.open", mock_open()), \
             patch.object(release_module, "run_command", return_value=mock_result) as mock_cmd:
            release()

        calls_str = " ".join(str(c) for c in mock_cmd.call_args_list)
        # extract the --config <path> value from the recorded calls
        import re as _re
        m = _re.search(r"--config\s+(\S+)", calls_str)
        return m.group(1) if m else None

    def test_1_explicit_plugin_cliff_toml_used(self):
        """
        Checks: when PLUGIN_CLIFF_TOML is set, that path is passed to git-cliff
                regardless of whether ./cliff.toml exists.
        """
        used = self._run_with_toml_env("/custom/my.toml", workspace_has_cliff=True)
        self.assertEqual(used, "/custom/my.toml")

    def test_2_workspace_cliff_toml_used_when_present(self):
        """
        Checks: when PLUGIN_CLIFF_TOML is not set but ./cliff.toml exists,
                git-cliff is invoked with ./cliff.toml.
        """
        used = self._run_with_toml_env("", workspace_has_cliff=True)
        self.assertEqual(used, "./cliff.toml")

    def test_3_bundled_cliff_toml_used_as_last_resort(self):
        """
        Checks: when PLUGIN_CLIFF_TOML is not set and ./cliff.toml is absent,
                git-cliff is invoked with the bundled cliff.toml (next to release.py).
        """
        bundled = os.path.join(os.path.dirname(_src_path), "cliff.toml")
        used = self._run_with_toml_env("", workspace_has_cliff=False)
        self.assertEqual(used, bundled)


# ---------------------------------------------------------------------------
# Class 6 — TestBranchResolution
#
# release() resolves which branch's tags to trust before processing any
# location: CI_PIPELINE_EVENT=pull_request -> CI_COMMIT_TARGET_BRANCH,
# otherwise -> CI_COMMIT_BRANCH. It resets remote.origin.tagOpt first (so a
# --no-tags clone setting doesn't block later auto-follow). Both PR and
# non-PR cases fetch the resolved branch into an explicit
# refs/remotes/origin/<branch> destination (never passing --tags/--no-tags
# itself) — even for the branch already checked out, since tag auto-follow
# only fires during a real fetch negotiation; a plain re-fetch of an
# already-tracked ref is a no-op that skips it, and just having the commit
# data locally (e.g. via a depth:0 clone) isn't enough on its own.
# ---------------------------------------------------------------------------

class TestBranchResolution(unittest.TestCase):

    def _run(self, extra_env):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "nati-1.1.0"
        mock_result.stderr = ""

        env = {
            "PLUGIN_MESSAGE":             "feat[nati]: add login",
            "PLUGIN_BASE_PATH":                "/repo",
            "PLUGIN_SCOPE_EXCLUDE_REGEX": "",
            "PLUGIN_OUTPUT_TAGS_FILE":    "",
            "PLUGIN_VERBOSE":             "0",
            "PLUGIN_CHANGELOG_LEVEL":     "1",
            "CI_PIPELINE_EVENT":          "manual",
            "CI_COMMIT_BRANCH":           "",
            "CI_COMMIT_TARGET_BRANCH":    "",
        }
        env.update(extra_env)

        # A pull_request event routes _retrieve_message() through the
        # Bitbucket API path, which needs these vars and a mocked urlopen
        # (rather than a real network call) to hand back PLUGIN_MESSAGE.
        if env.get("CI_PIPELINE_EVENT") == "pull_request":
            env.setdefault("PLUGIN_BITBUCKET_TOKEN", "tok")
            env.setdefault("CI_FORGE_URL", "https://bitbucket.example.com")
            env.setdefault("CI_REPO_OWNER", "PROJ")
            env.setdefault("CI_REPO_NAME", "myrepo")
            env.setdefault("CI_COMMIT_PULL_REQUEST", "1")

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {"id": 1, "title": "t", "description": env["PLUGIN_MESSAGE"]}
        ).encode()

        with patch.dict(os.environ, env, clear=False), \
             patch("os.path.exists", return_value=True), \
             patch("os.path.isdir", return_value=True), \
             patch("os.listdir", return_value=[]), \
             patch.object(release_module, "load_cliff_parsers", return_value=(PARSERS, {})), \
             patch("builtins.open", mock_open()), \
             patch.object(release_module, "urlopen", return_value=mock_response), \
             patch.object(release_module, "run_command", return_value=mock_result) as mock_cmd:
            release()

        return mock_cmd

    def test_1_tagopt_reset_always_happens(self):
        """
        Checks: `git config --unset-all remote.origin.tagOpt` is run regardless
                of PR/non-PR, since the clone step's tags: setting can persist
                and block later auto-follow otherwise.
        """
        mock_cmd = self._run({})
        calls_str = " ".join(str(c) for c in mock_cmd.call_args_list)
        self.assertIn("git config --unset-all remote.origin.tagOpt", calls_str)

    def test_2_pr_event_fetches_target_branch_no_tag_flags(self):
        """
        Checks: CI_PIPELINE_EVENT=pull_request + CI_COMMIT_TARGET_BRANCH=main
                fetches 'main' into a fresh remote-tracking ref, with NEITHER
                --tags nor --no-tags (relies on git's default auto-follow, see
                HOTFIX_TAG_RESOLUTION.md), authenticated with a Bearer header.
        """
        mock_cmd = self._run({
            "CI_PIPELINE_EVENT":       "pull_request",
            "CI_COMMIT_TARGET_BRANCH": "main",
        })
        calls_str = " ".join(str(c) for c in mock_cmd.call_args_list)
        # A pull_request run has PLUGIN_BITBUCKET_TOKEN set, so the fetch must be
        # authenticated with a Bearer header (see Bitbucket DC token gotcha).
        self.assertIn('git -c http.extraHeader="Authorization: Bearer tok" fetch origin main:refs/remotes/origin/main', calls_str)
        # Step 1 tag lookup should target the fetched branch, not HEAD.
        self.assertIn("--match 'nati-v[0-9]*' refs/remotes/origin/main", calls_str)

    def test_3_non_pr_also_fetches_its_own_branch_explicitly(self):
        """
        Checks: without a pull_request event, CI_COMMIT_BRANCH still gets
                fetched into refs/remotes/origin/<branch> (even though it's
                already checked out) — required for tag auto-follow to fire —
                and the tag lookup targets that fetched ref, not HEAD directly.
        """
        mock_cmd = self._run({"CI_COMMIT_BRANCH": "hotfix"})
        calls_str = " ".join(str(c) for c in mock_cmd.call_args_list)
        # This manual run has no PLUGIN_BITBUCKET_TOKEN, so no auth header is
        # added and the fetch stays a plain `git fetch`.
        self.assertIn("git fetch origin hotfix:refs/remotes/origin/hotfix", calls_str)
        self.assertNotIn("http.extraHeader", calls_str)
        self.assertIn("--match 'nati-v[0-9]*' refs/remotes/origin/hotfix", calls_str)

    def test_4_neither_var_set_falls_back_to_head(self):
        """
        Checks: outside Woodpecker (no CI_PIPELINE_EVENT/CI_COMMIT_BRANCH set
                at all, e.g. a bare local run), tag lookup falls back to HEAD
                and no branch-resolution fetch happens.
        """
        mock_cmd = self._run({})
        calls_str = " ".join(str(c) for c in mock_cmd.call_args_list)
        self.assertIn("--match 'nati-v[0-9]*' HEAD", calls_str)

    def test_5_tag_lookup_uses_describe_not_tag_list(self):
        """
        Checks: the STEP 1 tag lookup uses `git describe --tags --abbrev=0
                --match` (ancestry-scoped) rather than the old
                `git tag -l --sort=-version:refname` (globally-highest).
        """
        mock_cmd = self._run({})
        calls_str = " ".join(str(c) for c in mock_cmd.call_args_list)
        self.assertIn("git describe --tags --abbrev=0 --match", calls_str)
        self.assertNotIn("--sort=-version:refname", calls_str)


# ---------------------------------------------------------------------------
# Class 7 — TestHotfixBranchTagResolution
#
# Real-git integration test (run_command is NOT mocked here — this exercises
# actual git and git-cliff subprocesses) reproducing the exact scenario that
# motivated this feature: a component has nati-v1.0.0 and nati-v2.0.0 on
# mainline; a hotfix branch cut from nati-v1.0.0 must resolve/bump against
# nati-v1.0.0 (not the globally-higher nati-v2.0.0), and a PR build sourced
# from that same hotfix branch must still resolve against the target
# branch's own latest tag. Requires `git` and `git-cliff` on PATH.
# ---------------------------------------------------------------------------

class TestHotfixBranchTagResolution(unittest.TestCase):

    def _git(self, *args, cwd):
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, f"git {' '.join(args)} failed: {result.stderr}")
        return result.stdout.strip()

    def setUp(self):
        self.src_dir = tempfile.mkdtemp(prefix="release_src_")
        self._git("init", "-q", cwd=self.src_dir)
        self._git("config", "user.email", "test@test.com", cwd=self.src_dir)
        self._git("config", "user.name", "test", cwd=self.src_dir)
        self._git("checkout", "-q", "-b", "master", cwd=self.src_dir)

        os.makedirs(os.path.join(self.src_dir, "nati"))
        with open(os.path.join(self.src_dir, "nati", "f.txt"), "w") as f:
            f.write("v1")
        self._git("add", ".", cwd=self.src_dir)
        self._git("commit", "-q", "-m", "feat: initial release", cwd=self.src_dir)
        self._git("tag", "nati-v1.0.0", cwd=self.src_dir)

        with open(os.path.join(self.src_dir, "nati", "f.txt"), "w") as f:
            f.write("v2")
        self._git("add", ".", cwd=self.src_dir)
        self._git("commit", "-q", "-m", "feat: big new feature", cwd=self.src_dir)
        self._git("tag", "nati-v2.0.0", cwd=self.src_dir)

        self._git("checkout", "-q", "-b", "hotfix", "nati-v1.0.0", cwd=self.src_dir)
        with open(os.path.join(self.src_dir, "nati", "f.txt"), "w") as f:
            f.write("v1-fix")
        self._git("add", ".", cwd=self.src_dir)
        self._git("commit", "-q", "-m", "hotfix commit", cwd=self.src_dir)

    def tearDown(self):
        shutil.rmtree(self.src_dir, ignore_errors=True)

    def _clone_and_run(self, message, extra_env, all_tags=False):
        """
        Reproduces Woodpecker's actual plugin-git clone mechanism -- `git init`
        + `git fetch --no-tags origin <ref>` (matching tags: false), NOT a
        plain `git clone --branch ... --no-tags`, which (without
        --single-branch) fetches every branch regardless of the tags flag and
        would misrepresent what CI actually does.

        When all_tags=True, every tag from every branch is additionally fetched
        into the workspace (simulating a `tags: true` clone) — including
        nati-v2.0.0, which is NOT in hotfix's ancestry — to prove
        --use-branch-tags keeps resolution branch-correct without deleting tags.

        The release message is injected via the "manual" retrieval path
        (CI_PIPELINE_EVENT=manual + PLUGIN_MESSAGE) by default. For a
        pull_request event, urlopen is mocked to hand back `message` as the
        PR description instead, since there's no real Bitbucket server here.
        """
        repo_dir = tempfile.mkdtemp(prefix="release_repo_")
        tags_file = None
        old_cwd = os.getcwd()
        try:
            self._git("init", "-q", cwd=repo_dir)
            self._git("remote", "add", "origin", self.src_dir, cwd=repo_dir)
            self._git("fetch", "-q", "--no-tags", "origin", "hotfix", cwd=repo_dir)
            self._git("checkout", "-q", "FETCH_HEAD", cwd=repo_dir)
            if all_tags:
                # `tags: true`: pull EVERY tag (incl. the non-ancestor v2.0.0)
                # into the workspace, without touching FETCH_HEAD/HEAD.
                self._git("fetch", "-q", "origin", "refs/tags/*:refs/tags/*", cwd=repo_dir)
            cliff_toml = os.path.join(os.path.dirname(_src_path), "cliff.toml")

            tags_file = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False).name

            env = {
                "PLUGIN_MESSAGE":          message,
                "PLUGIN_BASE_PATH":        ".",
                "PLUGIN_CHANGELOG_LEVEL":  "1",
                "PLUGIN_OUTPUT_TAGS_FILE": tags_file,
                "PLUGIN_CLIFF_TOML":       cliff_toml,
                "PLUGIN_VERBOSE":          "0",
                "CI_PIPELINE_EVENT":       "manual",
                "CI_COMMIT_BRANCH":        "",
                "CI_COMMIT_TARGET_BRANCH": "",
            }
            env.update(extra_env)

            is_pr = env.get("CI_PIPELINE_EVENT") == "pull_request"
            if is_pr:
                env.setdefault("PLUGIN_BITBUCKET_TOKEN", "tok")
                env.setdefault("CI_FORGE_URL", "https://bitbucket.example.com")
                env.setdefault("CI_REPO_OWNER", "PROJ")
                env.setdefault("CI_REPO_NAME", "myrepo")
                env.setdefault("CI_COMMIT_PULL_REQUEST", "1")

            os.chdir(repo_dir)
            with patch.dict(os.environ, env, clear=False):
                if is_pr:
                    mock_response = MagicMock()
                    mock_response.read.return_value = json.dumps(
                        {"id": 1, "title": "t", "description": message}
                    ).encode()
                    with patch.object(release_module, "urlopen", return_value=mock_response):
                        release()
                else:
                    release()

            # Snapshot tags still present after the run, so tests can assert the
            # resolution was non-destructive (no `git tag -d`).
            self._repo_tags_after = sorted(
                t for t in self._git("tag", "-l", cwd=repo_dir).splitlines() if t
            )
            with open(tags_file) as f:
                return [line.strip() for line in f if line.strip()]
        finally:
            os.chdir(old_cwd)
            shutil.rmtree(repo_dir, ignore_errors=True)
            if tags_file:
                os.unlink(tags_file)

    def test_1_hotfix_branch_resolves_against_own_ancestor_not_global_latest(self):
        """
        Checks: a fix committed on the hotfix branch (cut from nati-v1.0.0,
                with nati-v2.0.0 existing on mainline) bumps to nati-v1.0.1 —
                not nati-v2.0.1, which is what the old highest-tag-wins logic
                would have produced.
        """
        tags = self._clone_and_run(
            "fix[nati]: patch bug",
            {"CI_COMMIT_BRANCH": "hotfix"},
        )
        self.assertEqual(tags, ["nati-v1.0.1"])

    def test_2_pr_event_resolves_against_target_branch(self):
        """
        Checks: the same hotfix checkout, but flagged as a pull_request event
                targeting master, resolves against master's own latest tag
                (nati-v2.0.0 -> nati-v2.1.0) instead of the hotfix branch's
                ancestry — "what this would look like once merged."
        """
        tags = self._clone_and_run(
            "feat[nati]: normal feature",
            {"CI_PIPELINE_EVENT": "pull_request", "CI_COMMIT_TARGET_BRANCH": "master"},
        )
        self.assertEqual(tags, ["nati-v2.1.0"])

    def test_3_breaking_commit_crosses_major_version_line(self):
        """
        Checks: a breaking commit on the hotfix branch correctly bumps past
                the major-version boundary (nati-v1.0.0 -> nati-v2.0.0) rather
                than failing a tag-pattern mismatch — guards the specific
                failure mode that ruled out the pattern-narrowing approach.
        """
        tags = self._clone_and_run(
            "breaking[nati]: major change",
            {"CI_COMMIT_BRANCH": "hotfix"},
        )
        self.assertEqual(tags, ["nati-v2.0.0"])

    def test_4_bare_type_with_description_on_next_line_still_bumps_minor(self):
        """
        Checks end-to-end: a PR body written as 'feat[nati]:' with the actual
                description entirely on the next line still produces a minor
                bump, not a patch — guards the upstream git-cliff bug
                (https://github.com/orhun/git-cliff/issues/1476) combined with
                release.py's own subject-only bump-call truncation.
        """
        tags = self._clone_and_run(
            "feat[nati]:\nnatiii",
            {"CI_COMMIT_BRANCH": "hotfix"},
        )
        self.assertEqual(tags, ["nati-v1.1.0"])

    def test_5_tags_true_direct_hotfix_still_resolves_ancestor(self):
        """
        Checks the `tags: true` scenario: even with nati-v2.0.0 physically
        present in the workspace, a fix on the hotfix branch still bumps against
        its own ancestor (nati-v1.0.0 -> nati-v1.0.1) — proving git-cliff's
        --use-branch-tags scopes to the branch — and NO tags are deleted.
        """
        tags = self._clone_and_run(
            "fix[nati]: patch bug",
            {"CI_COMMIT_BRANCH": "hotfix"},
            all_tags=True,
        )
        self.assertEqual(tags, ["nati-v1.0.1"])
        self.assertEqual(self._repo_tags_after, ["nati-v1.0.0", "nati-v2.0.0"])

    def test_6_tags_true_breaking_on_hotfix_crosses_own_major(self):
        """
        Checks `tags: true` with a breaking commit: hotfix bumps its OWN line
        (nati-v1.0.0 -> nati-v2.0.0), not master's (which would give 3.0.0),
        and no tags are deleted.
        """
        tags = self._clone_and_run(
            "breaking[nati]: major change",
            {"CI_COMMIT_BRANCH": "hotfix"},
            all_tags=True,
        )
        self.assertEqual(tags, ["nati-v2.0.0"])
        self.assertEqual(self._repo_tags_after, ["nati-v1.0.0", "nati-v2.0.0"])

    def test_7_tags_true_pr_resolves_against_master(self):
        """
        Checks `tags: true` for a pull_request: with all tags present, the PR
        (checked out on hotfix) still resolves against the TARGET branch master
        (nati-v2.0.0 -> nati-v2.1.0) via the target-branch checkout, non-
        destructively.
        """
        tags = self._clone_and_run(
            "feat[nati]: normal feature",
            {"CI_PIPELINE_EVENT": "pull_request", "CI_COMMIT_TARGET_BRANCH": "master"},
            all_tags=True,
        )
        self.assertEqual(tags, ["nati-v2.1.0"])
        self.assertEqual(self._repo_tags_after, ["nati-v1.0.0", "nati-v2.0.0"])


if __name__ == "__main__":
    unittest.main()
