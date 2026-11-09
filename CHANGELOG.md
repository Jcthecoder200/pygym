# Changelog

All notable changes to this project are recorded here.
Format follows [Keep a Changelog](https://keepachangelog.com/); versions follow [SemVer](https://semver.org/).

## [Unreleased]
- Grow the pilot to ~10 built-ins with 1-2 exercises each.
- Real-machine testing of the tkinter popup and a Jupyter-friendly UI.
- (Stretch) LLM mode.

## [0.0.0] - 2026-11-09
### Added
- Project kernel: `exercise("name")` entry point and `Session` (submit / hint / solution / skip).
- Sandboxed grader: separate process, timeout, memory limit, restricted imports,
  runtime check that the target built-in was actually called, normalized output comparison.
- Text UI (terminal / Jupyter) and tkinter popup UI.
- Local progress file.
- Pilot dataset: 5 exercises across `zip`, `sum`, `sorted`.
- Exercise validation tool and 11 grader tests.
- GitHub Actions workflow to test, build and publish to PyPI on release.
