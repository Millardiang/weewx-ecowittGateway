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
| **Pre-release** | Testing builds before a release: `a` = alpha (incomplete), `b` = beta (complete, being tested), `rc` = release candidate (expected to be final). `N` counts up from 1. | `1.1.0b1`, `1.1.0b2`, `1.1.0rc1`, `1.1.0` |

The betas before 1.0.0 (`0.0.1b1` to `0.0.1b8`) could contain small, clearly documented breaking
changes, as semver allows. From 1.0.0 onwards the configuration options and WeeWX field names are
frozen, and a breaking change needs a new MAJOR version. Configuration compatibility with
`ecowitt_http.py` is kept for the whole 1.x series.

## Where the version is recorded

Each release must update these together:

| File | Item | Format |
|---|---|---|
| `bin/user/weewx-EcowittGateway.py` | `DRIVER_VERSION` | PEP 440, e.g. `1.0.0` or `1.1.0b1` |
| `bin/user/weewx-EcowittGateway.py` | `Version:` line in the header | human-readable, e.g. `1.0.0` or `1.1.0 beta 1` |
| `install.py` | `VERSION` | PEP 440, the same as `DRIVER_VERSION` |
| `CHANGELOG.md` | a new `## [x.y.z]` section | the Unreleased items moved under it, with the date |
| Release package | `weewx-EcowittGateway-<version>.zip` | e.g. `weewx-EcowittGateway-1.0.0.zip` |

You can check the installed version with any of these:

```bash
weectl extension list
python3 weewx-EcowittGateway.py --version
```

It also appears in the WeeWX log at start-up (`EcowittHttpDriver: version is 1.0.0`).

## Release history and roadmap

1. **0.0.1b1 – 0.0.1b8** (24 September 2026): betas, with fixes and features found by testing.
2. **1.0.0** (25 September 2026): first full release, identical in code to 0.0.1b8. From here the
   configuration and field names are frozen, and breaking changes need a MAJOR version.
3. **1.x:** new sensors and features (MINOR) and bug fixes (PATCH), with `b`/`rc` pre-releases for
   testing where useful.

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
6. Tag the release in version control as `v<version>`, e.g. `v1.0.0`.
