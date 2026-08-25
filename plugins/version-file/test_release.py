"""
Tests for plugins/version-file/release.py — file-based (VERSION.txt) versioning,
used identically by both a "builtin" repo (message-driven, per-component semver)
and a "hardened" repo (images.txt-declared, root-VERSION.txt-seeded, generated
Dockerfiles). Run with: python3 test_release.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import release as rf  # noqa: E402


def run_git(cwd, *args):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout.strip()


def init_repo(root):
    run_git(root, "init", "-q")
    run_git(root, "config", "user.email", "test@example.com")
    run_git(root, "config", "user.name", "test")
    with open(os.path.join(root, ".keep"), "w") as f:
        f.write("x")
    run_git(root, "add", ".")
    run_git(root, "commit", "-q", "-m", "init")


class TestExpandLocationsWildcards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mk(self, rel, with_version=True):
        full = os.path.join(self.tmp, rel)
        os.makedirs(full, exist_ok=True)
        if with_version:
            open(os.path.join(full, "VERSION.txt"), "w").close()

    def test_recursive_double_star_finds_deep_versioned_dirs(self):
        self._mk("dockerhub/woodpeckerci/plugin-git/2.9.3")
        self._mk("redhat/ubi9/ubi/9.4")
        self._mk("dockerhub/woodpeckerci/plugin-git", with_version=False)  # not a leaf
        result = rf._expand_locations({"**": {"feat: rotate"}}, self.tmp)
        self.assertEqual(
            set(result.keys()),
            {"dockerhub/woodpeckerci/plugin-git/2.9.3", "redhat/ubi9/ubi/9.4"},
        )

    def test_prefix_double_star_scopes_to_subtree(self):
        self._mk("dockerhub/woodpeckerci/plugin-git/2.9.3")
        self._mk("redhat/ubi9/ubi/9.4")
        result = rf._expand_locations({"dockerhub/**": {"feat: x"}}, self.tmp)
        self.assertEqual(set(result.keys()), {"dockerhub/woodpeckerci/plugin-git/2.9.3"})

    def test_single_star_still_one_level(self):
        self._mk("base/python3.9", with_version=True)
        self._mk("base/python3.11", with_version=True)
        result = rf._expand_locations({"base/*": {"feat: x"}}, self.tmp)
        self.assertEqual(set(result.keys()), {"base/python3.9", "base/python3.11"})

    def test_double_star_excludes_dirs_without_version_file(self):
        self._mk("a/b", with_version=False)
        result = rf._expand_locations({"**": {"feat: x"}}, self.tmp)
        self.assertEqual(result, {})


class TestScaffoldDeclaredImages(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_noop_without_images_txt(self):
        declared = rf._load_declared_images(self.tmp)
        self.assertEqual(declared, set())
        rf._scaffold_declared_images(self.tmp, declared)
        self.assertEqual(os.listdir(self.tmp), [])

    def test_creates_folder_and_empty_version_file(self):
        with open(os.path.join(self.tmp, "images.txt"), "w") as f:
            f.write("# comment\n\ndockerhub/woodpeckerci/plugin-git/2.9.3\n")
        declared = rf._load_declared_images(self.tmp)
        self.assertEqual(declared, {"dockerhub/woodpeckerci/plugin-git/2.9.3"})
        rf._scaffold_declared_images(self.tmp, declared)
        vf = os.path.join(self.tmp, "dockerhub/woodpeckerci/plugin-git/2.9.3/VERSION.txt")
        self.assertTrue(os.path.exists(vf))
        self.assertEqual(open(vf).read(), "")

    def test_does_not_clobber_existing_version_file(self):
        full = os.path.join(self.tmp, "dockerhub/woodpeckerci/plugin-git/2.9.3")
        os.makedirs(full)
        with open(os.path.join(full, "VERSION.txt"), "w") as f:
            f.write("2.0.0")
        with open(os.path.join(self.tmp, "images.txt"), "w") as f:
            f.write("dockerhub/woodpeckerci/plugin-git/2.9.3\n")
        declared = rf._load_declared_images(self.tmp)
        rf._scaffold_declared_images(self.tmp, declared)
        self.assertEqual(open(os.path.join(full, "VERSION.txt")).read(), "2.0.0")


class TestRejectDirectBumpsOfDeclaredImages(unittest.TestCase):
    def test_noop_when_nothing_declared(self):
        # Should not raise — a builtin repo has no images.txt, so this guard
        # never fires regardless of what locations are named.
        rf._reject_direct_bumps_of_declared_images(
            {"dockerhub/woodpeckerci/plugin-git/2.9.3": {"feat: x"}}, set()
        )

    def test_noop_when_only_wildcard_used(self):
        declared = {"dockerhub/woodpeckerci/plugin-git/2.9.3"}
        # "**" itself is the key pre-expansion — it is never the declared
        # path, so this must not raise.
        rf._reject_direct_bumps_of_declared_images({"**": {"feat: x"}}, declared)

    def test_noop_for_undeclared_explicit_location(self):
        declared = {"dockerhub/woodpeckerci/plugin-git/2.9.3"}
        rf._reject_direct_bumps_of_declared_images({"base/python3.9": {"feat: x"}}, declared)

    def test_raises_when_declared_image_named_directly(self):
        declared = {"dockerhub/woodpeckerci/plugin-git/2.9.3"}
        with self.assertRaises(SystemExit):
            rf._reject_direct_bumps_of_declared_images(
                {"dockerhub/woodpeckerci/plugin-git/2.9.3": {"feat: oops"}}, declared
            )

    def test_raises_even_if_the_same_run_also_uses_the_wildcard(self):
        # Naming it directly is the mistake being guarded against — adding the
        # wildcard too doesn't excuse it, so this must still fail loudly
        # rather than silently accept the direct line alongside it.
        declared = {"dockerhub/woodpeckerci/plugin-git/2.9.3"}
        with self.assertRaises(SystemExit):
            rf._reject_direct_bumps_of_declared_images(
                {
                    "dockerhub/woodpeckerci/plugin-git/2.9.3": {"feat: oops"},
                    "**": {"feat: rotate"},
                },
                declared,
            )


class TestDeriveFromImage(unittest.TestCase):
    def test_valid_shape(self):
        self.assertEqual(
            rf._derive_from_image("dockerhub/woodpeckerci/plugin-git/2.9.3", "harbor.x/outbound_images"),
            "harbor.x/outbound_images/dockerhub/woodpeckerci/plugin-git:2.9.3",
        )

    def test_unknown_alias_returns_none(self):
        self.assertIsNone(rf._derive_from_image("notasource/org/image/1.0", "harbor.x/outbound_images"))

    def test_no_slash_returns_none(self):
        self.assertIsNone(rf._derive_from_image("nati", "harbor.x/outbound_images"))


class TestGenerateDockerfileIfMissing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        with open(os.path.join(self.tmp, "Dockerfile.template"), "w") as f:
            f.write("# GENERATED by scripts/reconcile.sh from a line in images.txt — do not hand-edit.\nFROM {{FROM_IMAGE}}\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_generates_when_missing(self):
        loc = "dockerhub/woodpeckerci/plugin-git/2.9.3"
        full = os.path.join(self.tmp, loc)
        os.makedirs(full)
        rf._generate_dockerfile_if_missing(full, loc, self.tmp, "harbor.x/outbound_images")
        content = open(os.path.join(full, "Dockerfile")).read()
        self.assertIn("FROM harbor.x/outbound_images/dockerhub/woodpeckerci/plugin-git:2.9.3", content)

    def test_noop_without_template(self):
        os.remove(os.path.join(self.tmp, "Dockerfile.template"))
        loc = "dockerhub/woodpeckerci/plugin-git/2.9.3"
        full = os.path.join(self.tmp, loc)
        os.makedirs(full)
        rf._generate_dockerfile_if_missing(full, loc, self.tmp, "harbor.x/outbound_images")
        self.assertFalse(os.path.exists(os.path.join(full, "Dockerfile")))

    def test_leaves_hand_written_dockerfile_alone(self):
        loc = "codeberg/woodpecker-plugins/mastodon-post/1.0.0"
        full = os.path.join(self.tmp, loc)
        os.makedirs(full)
        with open(os.path.join(full, "Dockerfile"), "w") as f:
            f.write("FROM scratch\n")
        rf._generate_dockerfile_if_missing(full, loc, self.tmp, "harbor.x/outbound_images")
        self.assertEqual(open(os.path.join(full, "Dockerfile")).read(), "FROM scratch\n")

    def test_regenerates_when_marker_present(self):
        loc = "dockerhub/woodpeckerci/plugin-git/2.9.3"
        full = os.path.join(self.tmp, loc)
        os.makedirs(full)
        with open(os.path.join(full, "Dockerfile"), "w") as f:
            f.write("# GENERATED by scripts/reconcile.sh from a line in images.txt — do not hand-edit.\nFROM old:stale\n")
        rf._generate_dockerfile_if_missing(full, loc, self.tmp, "harbor.x/outbound_images")
        content = open(os.path.join(full, "Dockerfile")).read()
        self.assertIn("FROM harbor.x/outbound_images/dockerhub/woodpeckerci/plugin-git:2.9.3", content)
        self.assertNotIn("old:stale", content)


class TestUpdateRootChangelog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_creates_file_and_section(self):
        rf._update_root_changelog(self.tmp, "dockerhub/woodpeckerci/plugin-git/2.9.3", "v", "1.0.0", "first release")
        content = open(os.path.join(self.tmp, "CHANGELOG.md")).read()
        self.assertIn("## dockerhub/woodpeckerci/plugin-git/2.9.3", content)
        self.assertIn("**v1.0.0**", content)
        self.assertIn("first release", content)

    def test_newest_bullet_lands_first_under_heading(self):
        rf._update_root_changelog(self.tmp, "x/y", "v", "1.0.0", "first release")
        rf._update_root_changelog(self.tmp, "x/y", "v", "2.0.0", "bumped")
        lines = open(os.path.join(self.tmp, "CHANGELOG.md")).read().splitlines()
        heading_idx = lines.index("## x/y")
        self.assertIn("v2.0.0", lines[heading_idx + 1])
        self.assertIn("v1.0.0", lines[heading_idx + 2])

    def test_separate_locations_get_separate_sections(self):
        rf._update_root_changelog(self.tmp, "a", "v", "1.0.0", "first release")
        rf._update_root_changelog(self.tmp, "b", "v", "1.0.0", "first release")
        content = open(os.path.join(self.tmp, "CHANGELOG.md")).read()
        self.assertIn("## a", content)
        self.assertIn("## b", content)

    def test_a_location_born_late_never_gets_an_earlier_version_line(self):
        # a/b existed since v1.0.0, c/d was only added at v2.0.0.
        rf._update_root_changelog(self.tmp, "a/b", "v", "1.0.0", "first release")
        rf._update_root_changelog(self.tmp, "a/b", "v", "2.0.0", "bumped")
        rf._update_root_changelog(self.tmp, "c/d", "v", "2.0.0", "first release")
        content = open(os.path.join(self.tmp, "CHANGELOG.md")).read()
        section = content.split("## c/d", 1)[1]
        self.assertNotIn("v1.0.0", section)


class TestEndToEndFileVersioning(unittest.TestCase):
    """Runs release() against a real git + git-cliff working tree."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        init_repo(self.tmp)
        self._env_backup = dict(os.environ)
        os.environ["PLUGIN_BASE_PATH"] = self.tmp
        os.environ["PLUGIN_CHANGELOG_LEVEL"] = "0,1,2,3,4"
        os.environ["CI_PIPELINE_EVENT"] = "manual"
        os.environ["PLUGIN_V_PREFIX"] = "true"
        self._orig_cwd = os.getcwd()
        os.chdir(self.tmp)

    def tearDown(self):
        os.chdir(self._orig_cwd)
        os.environ.clear()
        os.environ.update(self._env_backup)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, message):
        os.environ["PLUGIN_MESSAGE"] = message
        rf.release()

    def test_first_release_of_root_uses_initial_tag(self):
        self._run("feat[]: rotate CA")
        self.assertEqual(open(os.path.join(self.tmp, "VERSION.txt")).read(), "1.0.0")

    def test_root_bump_after_first_release(self):
        self._run("feat[]: rotate CA")
        self._run("fix[]: patch bump")
        self.assertEqual(open(os.path.join(self.tmp, "VERSION.txt")).read(), "1.0.1")

    def test_breaking_bump_is_major(self):
        self._run("feat[]: rotate CA")
        self._run("breaking[]!: switch CA vendor")
        self.assertEqual(open(os.path.join(self.tmp, "VERSION.txt")).read(), "2.0.0")

    def test_ca_managed_bump_is_always_major_regardless_of_type(self):
        # images.txt existing at all makes root (and its declared images)
        # CA-managed: "fix" and "feat" both still force a MAJOR bump, not
        # their usual patch/minor.
        with open(os.path.join(self.tmp, "images.txt"), "w") as f:
            f.write("dockerhub/woodpeckerci/plugin-git/2.9.3\n")
        self._run("feat[]: rotate CA")                     # first release, still 1.0.0
        self._run("fix[]: rotate CA again")                # forced major, not patch
        self.assertEqual(open(os.path.join(self.tmp, "VERSION.txt")).read(), "2.0.0")

    def test_bare_double_star_rotates_root_too_when_images_txt_exists(self):
        # One line, no separate [] for root — [**] alone reaches both root and
        # every declared image once images.txt exists.
        img = "dockerhub/woodpeckerci/plugin-git/2.9.3"
        with open(os.path.join(self.tmp, "images.txt"), "w") as f:
            f.write(f"{img}\n")
        self._run("feat[**]: first bake")
        self.assertEqual(open(os.path.join(self.tmp, "VERSION.txt")).read(), "1.0.0")
        self.assertEqual(open(os.path.join(self.tmp, img, "VERSION.txt")).read(), "1.0.0")

        self._run("feat[**]: rotate CA")  # root existing -> forced major, no [] needed
        self.assertEqual(open(os.path.join(self.tmp, "VERSION.txt")).read(), "2.0.0")
        self.assertEqual(open(os.path.join(self.tmp, img, "VERSION.txt")).read(), "2.0.0")

    def test_scoped_double_star_does_not_imply_root(self):
        # [prefix/**] must NOT pull root in — only the bare [**] does.
        img = "dockerhub/woodpeckerci/plugin-git/2.9.3"
        with open(os.path.join(self.tmp, "images.txt"), "w") as f:
            f.write(f"{img}\n")
        self._run("feat[dockerhub/**]: first bake")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "VERSION.txt")))
        self.assertEqual(open(os.path.join(self.tmp, img, "VERSION.txt")).read(), "1.0.0")

    def test_non_ca_managed_component_unaffected_by_forced_major(self):
        # A repo WITH an images.txt can still have an ordinary component
        # elsewhere that isn't declared there — it keeps normal type-driven
        # bumping (patch for "fix"), proving the forced-major rule is scoped
        # to images.txt-declared locations (+ root), not the whole repo.
        with open(os.path.join(self.tmp, "images.txt"), "w") as f:
            f.write("dockerhub/woodpeckerci/plugin-git/2.9.3\n")
        loc = "base/python3.9"
        full = os.path.join(self.tmp, loc)
        os.makedirs(full)
        with open(os.path.join(full, "VERSION.txt"), "w") as f:
            f.write("5.0.0")
        self._run(f"fix[{loc}]: unrelated patch")
        self.assertEqual(open(os.path.join(full, "VERSION.txt")).read(), "5.0.1")

    def test_new_location_added_after_root_is_ahead_seeds_at_root_version_not_bumped_further(self):
        # Root is already at v2.0.0 (two prior rotations).
        self._run("feat[]: rotate CA")               # -> 1.0.0
        self._run("breaking[]!: switch vendor")       # -> 2.0.0

        # Now add a brand-new image, matched by the SAME wildcard as root, in
        # one PR — this is exactly the "new image added during a CA rotation"
        # scenario the whole design exists for. Root's own bump in this same
        # run must be a MAJOR bump too (breaking!), so it lands on a clean,
        # unambiguous 3.0.0 rather than something a minor/patch bump could
        # coincidentally also produce.
        loc = "dockerhub/woodpeckerci/plugin-git/2.9.3"
        with open(os.path.join(self.tmp, "images.txt"), "w") as f:
            f.write(f"{loc}\n")
        self._run("breaking[]!: rotate CA again\nfeat[**]: rotate CA again")

        root_version = open(os.path.join(self.tmp, "VERSION.txt")).read()
        new_version = open(os.path.join(self.tmp, loc, "VERSION.txt")).read()
        self.assertEqual(root_version, "3.0.0")
        # Must equal root's version exactly — NOT root-version-then-bumped-again.
        self.assertEqual(new_version, "3.0.0")

    def test_new_location_changelog_has_no_earlier_entries(self):
        # Root reaches v2.0.0 across two rotations BEFORE images.txt (and
        # therefore CA-managed forced-major bumping) exists — plain type-driven
        # bumps still apply here.
        self._run("feat[]: rotate CA")              # -> 1.0.0
        self._run("breaking[]!: switch vendor")      # -> 2.0.0

        loc = "dockerhub/woodpeckerci/plugin-git/2.9.4"
        with open(os.path.join(self.tmp, "images.txt"), "w") as f:
            f.write(f"{loc}\n")
        # Now that images.txt exists, root is CA-managed: "feat" is forced to a
        # MAJOR bump (2.0.0 -> 3.0.0), not the minor a plain "feat" would give.
        self._run("feat[]: rotate CA again\nfeat[**]: rotate CA again")

        root_changelog = open(os.path.join(self.tmp, "CHANGELOG.md")).read()
        section = root_changelog.split(f"## {loc}", 1)[1].split("## ", 1)[0]
        # The new location was born straight at 3.0.0 — it must have exactly
        # that one entry, never a v1.0.0/v2.0.0/v2.1.0 line it was never built at.
        self.assertEqual(section.count("**v"), 1)
        self.assertIn("v3.0.0", section)
        self.assertNotIn("v1.0.0", section)
        self.assertNotIn("v2.0.0", section)
        self.assertNotIn("v2.1.0", section)

    def test_minor_bump_correct_when_location_path_has_dotted_upstream_tag(self):
        # Regression: a location whose path contains a dotted, semver-SHAPED
        # substring (an upstream tag like "2.9.3") embeds that in the slug used
        # for git-cliff's --tag-pattern/ephemeral tag. git-cliff's own version
        # detection can mistake that embedded substring for part of the real
        # version, corrupting the bump (observed: "feat" on
        # ".../plugin-git-2.9.3-v1.0.0" produced a wrong PATCH bump v1.0.1
        # instead of the correct MINOR v1.1.0 that the identical commit type
        # produces for a location without such a substring, e.g. ".../9.4-v1.0.0"
        # -- a two-part number that doesn't look like a full X.Y.Z version).
        loc_with_dotted_tag = "dockerhub/woodpeckerci/plugin-git/2.9.3"
        loc_plain = "redhat/ubi9/ubi/9.4"
        for loc in (loc_with_dotted_tag, loc_plain):
            full = os.path.join(self.tmp, loc)
            os.makedirs(full)
            with open(os.path.join(full, "VERSION.txt"), "w") as f:
                f.write("1.0.0")
        self._run(f"feat[{loc_with_dotted_tag}]: x\nfeat[{loc_plain}]: x")
        self.assertEqual(open(os.path.join(self.tmp, loc_with_dotted_tag, "VERSION.txt")).read(), "1.1.0")
        self.assertEqual(open(os.path.join(self.tmp, loc_plain, "VERSION.txt")).read(), "1.1.0")

    def test_existing_component_ignores_root_and_bumps_its_own_version(self):
        self._run("feat[]: rotate CA")          # root -> 1.0.0
        loc = "base/python3.9"
        os.makedirs(os.path.join(self.tmp, loc))
        with open(os.path.join(self.tmp, loc, "VERSION.txt"), "w") as f:
            f.write("5.0.0")
        self._run(f"fix[{loc}]: unrelated patch")
        self.assertEqual(open(os.path.join(self.tmp, loc, "VERSION.txt")).read(), "5.0.1")

    def test_dockerfile_generated_for_declared_image(self):
        with open(os.path.join(self.tmp, "Dockerfile.template"), "w") as f:
            f.write("# GENERATED by scripts/reconcile.sh from a line in images.txt — do not hand-edit.\nFROM {{FROM_IMAGE}}\n")
        os.environ["PLUGIN_MIRROR_REGISTRY"] = "harbor.devopstashtiot.page/outbound_images"
        loc = "dockerhub/woodpeckerci/plugin-git/2.9.3"
        with open(os.path.join(self.tmp, "images.txt"), "w") as f:
            f.write(f"{loc}\n")
        self._run("feat[]: seed\nfeat[**]: seed")
        dockerfile = open(os.path.join(self.tmp, loc, "Dockerfile")).read()
        self.assertIn("FROM harbor.devopstashtiot.page/outbound_images/dockerhub/woodpeckerci/plugin-git:2.9.3", dockerfile)

    def test_no_op_when_no_releasable_commits(self):
        # "other" is a real cliff.toml commit type but skip=true — it's parsed
        # (unlike an unrecognized type, which would never even reach the
        # release loop), but must not produce a release.
        self._run("other[]: nothing releasable")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "VERSION.txt")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
