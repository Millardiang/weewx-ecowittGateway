# Versioning

weewx-EcowittGateway uses **semantic versioning** (<https://semver.org>) for the public version, and
**PEP 440** for the version string in the code, so that Python tools and `weectl extension list`
sort releases correctly.

## Version format

```
MAJOR.MINOR.PATCH[{a|b|rc}N]
```

| Part | Increase it when… | Example |
|---|---|---|
| **MAJOR** | A change breaks existing installs: a renamed config option, a removed or renamed WeeWX field, or a change that needs a database schema change. | `1.0.0` → `2.0.0` |
| **MINOR** | Something is added and existing installs keep working: a new sensor, new fields, new options or new commands. | `0.1.0` → `0.2.0` |
| **PATCH** | Bug fixes only, with no new fields or options. | `0.1.0` → `0.1.1` |
| **Pre-release** | Testing builds before a release: `a` = alpha (incomplete), `b` = beta (complete, being tested), `rc` = release candidate (expected to be final). `N` counts up from 1. | `0.1.0b1`, `0.1.0b2`, `0.1.0rc1`, `0.1.0` |

Before 1.0.0 (the `0.x` series), MINOR releases may also contain small, clearly documented breaking
changes, as semver allows. Configuration compatibility with `ecowitt_http.py` will still be kept for
the whole 0.x series.

## Where the version is recorded

Each release must update these together:

| File | Item | Format |
|---|---|---|
| `bin/user/weewx-EcowittGateway.py` | `DRIVER_VERSION` | PEP 440, e.g. `0.0.1b1` |
| `bin/user/weewx-EcowittGateway.py` | `Version:` line in the header | human-readable, e.g. `0.0.1 beta` |
| `install.py` | `VERSION` | PEP 440, the same as `DRIVER_VERSION` |
| `CHANGELOG.md` | a new `## [x.y.z]` section | the Unreleased items moved under it, with the date |
| Release package | `weewx-EcowittGateway-<version>.zip` | e.g. `weewx-EcowittGateway-0.0.1b1.zip` |

You can check the installed version with any of these:

```bash
weectl extension list
python3 weewx-EcowittGateway.py --version
```

It also appears in the WeeWX log at start-up (`EcowittHttpDriver: version is 0.0.1b4`).

## Roadmap to 1.0.0

1. **0.0.x betas:** fixes found by testing on real gateways and consoles.
2. **0.1.0:** first stable release, once the beta has run on real hardware (at least one
   GW1100/GW2000 and one console) without problems.
3. **0.x:** new sensors and features.
4. **1.0.0:** from here the configuration and field names are frozen, and breaking changes need a
   MAJOR version.

## Release checklist

1. Move the `[Unreleased]` items in `CHANGELOG.md` into a new version section and add the date,
   written as day month year (e.g. `24 September 2026`).
2. Update `DRIVER_VERSION`, the header `Version:` line and `install.py` `VERSION`.
3. Check the code: `python3 -m pyflakes bin/user/weewx-EcowittGateway.py install.py`.
4. Test on a scratch station: `weectl extension install`, `weectl station reconfigure`,
   `weectl device --live-data`, then `weectl extension uninstall`.
5. Build the package from the directory above `weewx-EcowittGateway/`:
   ```bash
   zip -r weewx-EcowittGateway-<version>.zip weewx-EcowittGateway
   ```
6. Tag the release in version control as `v<version>`, e.g. `v0.0.1b1`.
