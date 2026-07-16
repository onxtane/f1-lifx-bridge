"""A missing WebView2 / .NET must explain itself, not crash or render garbled (#72).

pywebview fails badly on both counts: no .NET raises UnboundLocalError while
importing its winforms backend (invisible in a windowed build), and no WebView2
silently downgrades to MSHTML/IE11, which renders our UI as a mess. The gate has
to catch both before pywebview gets control — and, just as importantly, stay out
of the way on a machine that would have worked.
"""
import unittest
import unittest.mock

from tests import harness  # noqa: F401  — sets sys.path for the app modules
import runtime_check  # noqa: E402
from runtime_check import Problem, find_problem  # noqa: E402

# A healthy Windows box, as measured on a real one: .NET 4.8 + WebView2 150.x.
HEALTHY_DOTNET = 533320
HEALTHY_WEBVIEW2 = (150, 0, 4078, 65)


def _machine(dotnet=HEALTHY_DOTNET, clr=True, webview2=HEALTHY_WEBVIEW2):
    """find_problem() probes describing one machine's state."""
    return {
        "dotnet_release": lambda: dotnet,
        "clr_loads": lambda: clr,
        "webview2_version": lambda: webview2,
    }


class HealthyMachineTests(unittest.TestCase):
    def test_fully_equipped_machine_is_not_blocked(self):
        self.assertIsNone(find_problem(**_machine()))

    def test_exact_minimums_are_accepted(self):
        """The thresholds are inclusive — being *at* the minimum is supported."""
        self.assertIsNone(find_problem(**_machine(
            dotnet=394802, webview2=(86, 0, 622, 0))))

    def test_newer_webview2_is_accepted(self):
        self.assertIsNone(find_problem(**_machine(webview2=(999, 0, 0, 0))))


class DotNetTests(unittest.TestCase):
    def test_missing_dotnet_is_reported(self):
        problem = find_problem(**_machine(dotnet=None))
        self.assertIsInstance(problem, Problem)
        self.assertIn(".NET", problem.title)

    def test_too_old_dotnet_is_reported(self):
        problem = find_problem(**_machine(dotnet=394801))  # one below 4.6.2
        self.assertIsInstance(problem, Problem)
        self.assertIn(".NET", problem.title)

    def test_installed_but_unloadable_dotnet_is_reported(self):
        """Registry says yes, pythonnet says no — a damaged install."""
        problem = find_problem(**_machine(clr=False))
        self.assertIsInstance(problem, Problem)
        self.assertIn(".NET", problem.title)

    def test_dotnet_is_reported_before_webview2(self):
        """Both broken: name .NET, since WebView2 needs it anyway."""
        problem = find_problem(**_machine(dotnet=None, webview2=None))
        self.assertIn(".NET", problem.title)

    def test_clr_is_not_probed_when_dotnet_is_already_missing(self):
        """No point asking pythonnet to load a runtime that isn't there."""
        def explode():
            raise AssertionError("clr probe should not run")

        problem = find_problem(dotnet_release=lambda: None, clr_loads=explode,
                               webview2_version=lambda: HEALTHY_WEBVIEW2)
        self.assertIsInstance(problem, Problem)


class WebView2Tests(unittest.TestCase):
    def test_missing_webview2_is_reported(self):
        problem = find_problem(**_machine(webview2=None))
        self.assertIsInstance(problem, Problem)
        self.assertIn("WebView2", problem.title)

    def test_too_old_webview2_is_reported(self):
        problem = find_problem(**_machine(webview2=(85, 0, 0, 0)))
        self.assertIsInstance(problem, Problem)
        self.assertIn("WebView2", problem.title)

    def test_too_old_message_names_both_versions(self):
        """'Too old' is only actionable if it says old compared to what."""
        problem = find_problem(**_machine(webview2=(85, 0, 1, 2)))
        self.assertIn("85.0.1.2", problem.message)
        self.assertIn("86.0.622.0", problem.message)

    def test_uninstall_tombstone_reads_as_missing_not_as_old(self):
        """EdgeUpdate leaves pv='0.0.0.0' behind instead of deleting the key.

        Reporting that as "version 0.0.0.0 is too old" would send the user off to
        update a runtime they simply don't have.
        """
        problem = find_problem(**_machine(webview2=(0, 0, 0, 0)))
        self.assertIn("isn't installed", problem.message)
        self.assertNotIn("0.0.0.0", problem.message)


class ProblemMessageTests(unittest.TestCase):
    """Every problem has to be legible and actionable, not a stack trace."""

    ALL_BROKEN = (
        _machine(dotnet=None),
        _machine(clr=False),
        _machine(webview2=None),
        _machine(webview2=(85, 0, 0, 0)),
    )

    def test_every_problem_offers_an_official_microsoft_link(self):
        for probes in self.ALL_BROKEN:
            problem = find_problem(**probes)
            with self.subTest(title=problem.title):
                self.assertTrue(
                    problem.url.startswith("https://"), problem.url)
                self.assertIn("microsoft.com", problem.url)

    def test_every_problem_says_what_is_wrong_and_what_to_do(self):
        for probes in self.ALL_BROKEN:
            problem = find_problem(**probes)
            with self.subTest(title=problem.title):
                self.assertIn("GridGlow", problem.title)
                self.assertIn("?", problem.message)          # prompts for consent
                self.assertGreater(len(problem.message), 120)  # explains itself

    def test_no_jargon_leaks_into_the_message(self):
        """The reader is a sim racer, not a pywebview contributor."""
        for probes in self.ALL_BROKEN:
            problem = find_problem(**probes)
            blob = (problem.title + problem.message).lower()
            with self.subTest(title=problem.title):
                for jargon in ("pywebview", "edgechromium", "pythonnet",
                               "traceback", "registry", "hkey"):
                    self.assertNotIn(jargon, blob)


class VersionParsingTests(unittest.TestCase):
    def test_parses_a_real_registry_version(self):
        self.assertEqual(
            runtime_check._parse_version("150.0.4078.65"), (150, 0, 4078, 65))

    def test_uninstalled_runtime_sentinel_is_below_the_minimum(self):
        """EdgeUpdate leaves pv='0.0.0.0' behind when WebView2 is removed."""
        parsed = runtime_check._parse_version("0.0.0.0")
        self.assertEqual(parsed, (0, 0, 0, 0))
        self.assertLess(parsed, runtime_check._MIN_WEBVIEW2_VERSION)

    def test_versions_compare_numerically_not_lexically(self):
        """'9' > '100' as text; the whole check hinges on this being numeric."""
        self.assertGreater(
            runtime_check._parse_version("100.0.0.0"),
            runtime_check._parse_version("99.0.0.0"))

    def test_junk_values_are_not_mistaken_for_a_version(self):
        for junk in (None, "", "abc", "1.2.x", 150, b"150.0", "..."):
            with self.subTest(junk=junk):
                self.assertIsNone(runtime_check._parse_version(junk))


class GuardSafetyTests(unittest.TestCase):
    """The gate must never itself become the reason the app won't start."""

    def setUp(self):
        self._real = runtime_check.find_problem
        self.addCleanup(lambda: setattr(runtime_check, "find_problem", self._real))

    def test_a_crashing_probe_lets_the_app_through(self):
        def explode(*_a, **_k):
            raise RuntimeError("registry exploded")

        runtime_check.find_problem = explode
        self.assertTrue(runtime_check.verify_or_explain())

    def test_escape_hatch_skips_the_check_entirely(self):
        """If the probes are ever wrong, a user must still get their app."""
        def explode(*_a, **_k):
            raise AssertionError("should not probe when skipping")

        runtime_check.find_problem = explode
        with unittest.mock.patch.dict(
                "os.environ", {runtime_check.SKIP_ENV_VAR: "1"}):
            self.assertTrue(runtime_check.verify_or_explain())


if __name__ == "__main__":
    unittest.main()
