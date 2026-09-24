# Changelog

All notable changes to weewx-EcowittGateway are recorded here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and version numbers
follow the scheme described in [VERSIONING.md](VERSIONING.md).

## [Unreleased]

Nothing yet.

## [0.0.1b8] – 24 September 2026 — eighth beta

### Added

- **GW1000 and WH2650 support.** These gateways have no local HTTP API, so the driver reads them
  through Ecowitt's binary TCP API (port 45000).
  - The new `api` option (`auto`, `http` or `tcp`, default `auto`) chooses the API. `auto` asks the
    TCP API for the model and uses it for a GW1000/WH2650, and the HTTP API for everything else,
    with the TCP API as a fallback. If neither API answers, detection is tried again at the next poll.
  - `tcp_port` (default 45000) sets the TCP API port.
  - TCP data uses the same field names and units as HTTP data. The field map, rain and lightning
    handling, `rain_source`, sensor map, `ecwLoop.json`, MQTT and service mode all work unchanged.
  - Decoded: indoor, outdoor and pressure readings; wind; solar (lux converted to W/m²) and UV;
    tipping and piezo rain with gains and reset times; lightning; WN31, WN34 (with battery voltage),
    WN35, WH41, WH45, WH46, WH51 (channels 1–16) and WH55; and sensor IDs, signal and battery. The
    battery voltage is used for sensors whose battery byte is a voltage.
  - Unknown data items are logged once, and the items before them are kept.
  - Catchup from the device is skipped, because there's no SD card. Ecowitt.net catchup works.
  - `--live-data`, `--sensors`, `--list-sensors`, `--firmware`, `--mac-address`, `--test-driver` and
    `--dump-api` (hex output) work. The other commands report that the TCP API doesn't provide
    their data.
  - `--api=auto|http|tcp` on the command line overrides the setting.
- **Installer:** finds a GW1000/WH2650 through the TCP API, reads its paired sensors and rain gauges,
  and offers only `net`, `none` or `either` for catchup.

### Changed

- The GW1000 is now listed as supported by `--discover`.

## [0.0.1b7] – 24 September 2026 — seventh beta

### Added

- **Sensor mapping by hardware ID.** A new `[[sensor_map]]` section (`<sensor ID> = <channel>`)
  reports a multi-channel sensor (WN31, WN34, WN35, WH41, WH51/WH52, WH54, WH55) on a fixed channel,
  whichever gateway channel it is paired on. This keeps its data in the same WeeWX fields after a
  battery change or re-pairing.
  - All of the sensor's fields move with it: readings, battery, signal, RSSI and soil AD values.
  - A sensor already on the chosen channel moves to the channel that was freed, so nothing is
    merged or lost.
  - Applies to loop packets (driver and service), `ecwLoop.json`, MQTT and catchup records.
  - Unpaired IDs, out-of-range channels and duplicate channels are logged and ignored. Channel
    moves are logged when they start or change.
- **`--list-sensors`** (`weectl device` and the module): lists each paired sensor by hardware ID with
  its gateway channel, reported channel, signal, battery, a live reading and the WeeWX fields it
  feeds, then prints a ready-made `[[sensor_map]]`.
- **`--dump-api`**: saves every raw API response, including all sensor pages, as one JSON document
  (`--output=FILE`). Passwords, keys and station IDs are masked unless `--unmask` is given.
- **`--no-sensor-map`**: shows the gateway's own channels in `--list-sensors`, `--live-data` and
  `--test-driver`.
- **Installer prompt** listing the paired multi-channel sensors and offering to lock them to their
  current channels. On an upgrade, only new sensors are offered.

### Changed

- `--live-data` and `--test-driver` apply the sensor map and show the channel moves first.

## [0.0.1b6] – 24 September 2026 — sixth beta

### Added

- **MQTT publishing.** A new `[[mqtt]]` option publishes every loop packet to an MQTT broker, in both
  driver and service mode.
  - Settings: host, port, username/password, topic, format, units, QoS, retain, and TLS (with
    optional CA and client certificates).
  - Formats: `json` sends one message on `<topic>/loop`; `individual` sends one message per field
    on `<topic>/<field>`; `both` sends both.
  - A retained `online`/`offline` status is published on `<topic>/status`, with a last-will message
    so the broker reports `offline` if WeeWX stops unexpectedly.
  - The connection runs in the background with automatic reconnection. Errors are logged once, and
    the reconnection is logged.
  - Needs the `paho-mqtt` package (1.x or 2.x), loaded only when MQTT is enabled. If it's missing,
    the driver logs how to install it and carries on without MQTT.
- **Installer prompts for MQTT**, including a check that the broker can be reached and a password
  prompt that doesn't show what you type.

### Changed

- The JSON file and MQTT output share the same unit conversion and error logging.

## [0.0.1b5] – 24 September 2026 — fifth beta

### Added

- **Loop data file (`ecwLoop.json`).** A new `[[loop_json]]` option writes every loop packet to a
  JSON file, for live web pages and scripts. It works in both driver and service mode.
  - `path` sets where the file goes. A relative path means the WeeWX web pages folder (the default
    is `ecwLoop.json` there); an absolute path, or a folder, can also be given.
  - `units` sets the unit system: `native`, `us`, `metric` or `metricwx`.
  - The file is replaced in one step, so readers never see a partial file. Write errors are logged
    once rather than on every packet.
- **Installer prompts for the loop data file:** whether to write it, where (web pages folder, WeeWX
  data folder, `/tmp` or a custom path) and in which units.

## [0.0.1b4] – 24 September 2026 — fourth beta

### Added

- **`both` rain gauge option.** `rain_source = both` records both gauges: the tipping gauge in WeeWX
  `rain`/`rainRate`, and the piezo gauge in `p_rain`, `hail` and `p_rainrate`.
  - The installer now offers `both`, `tipping` or `piezo`, with `both` as the default when both
    gauge types are paired.
  - `weectl station reconfigure` offers the same choice.

### Changed

- The driver logs an error and uses `tipping` if `rain_source` has an unrecognised value.
- `weectl station reconfigure` no longer asks "Select the WeeWX observation to be used to derive
  WeeWX observation 'rain'". The answer was never used.

## [0.0.1b3] – 24 September 2026 — third beta

### Changed

- **The driver name is now `EcowittGateway`** (it was `EcowittHttp`). The `weewx.conf` section is now
  `[EcowittGateway]`, with `station_type = EcowittGateway`.
  - The installer moves the settings from an existing `[EcowittHttp]` section (from earlier betas or
    `ecowitt_http.py`) into the new section, keeping custom settings and comments, and updates
    `station_type`.
  - If there's no `[EcowittGateway]` section, the driver, service and command-line tools still read
    `[EcowittHttp]`.
  - Class names such as `EcowittHttpService` are unchanged, so service entries keep working.
- **WeeWX 5.4.0 or later is now required.** The driver and the installer both stop with a clear
  error on older versions. WeeWX 4 support and the WeeWX 4 instructions have been removed.

## [0.0.1b2] – 24 September 2026 — second beta

Changes from testing the first install.

### Added

- **Interactive installer.** `weectl extension install` now asks for:
  - the gateway IP address, checked by contacting the gateway;
  - the poll interval;
  - driver, service or skip;
  - which rain gauge feeds WeeWX (only asked if both types are paired; the paired gauges are
    detected automatically);
  - the catchup source and Ecowitt.net keys;
  - battery display;
  - `loop_on_init`.

  It writes the answers to `weewx.conf`, so nothing needs editing by hand.
- **Driver mode set-up.** The installer also sets `station_type`, software archive records and the
  rain calculation settings. The separate `weectl station reconfigure` step is no longer needed.
- **Service mode set-up.** The installer adds the service to `data_services`, and uninstalling
  removes it again.
- **Re-running the installer** (for example for an upgrade) offers the current settings as defaults.
  Without a terminal it asks nothing and leaves the station driver unchanged.
- **New `rain_source` option** (`tipping` or `piezo`). It chooses which gauge feeds WeeWX's `rain`
  and `rainRate` fields, in both live data and catchup.
- **New `[[catchup]] source = none`**, to turn off fetching missed data.

### Changed

- The `[EcowittHttp]` section is now placed directly after `[Station]` in `weewx.conf`.
- `weectl station reconfigure` now sets `rain_source` instead of adding a `rainRate` field-map entry.
- Blank `api_key`/`app_key` values are treated as not set.

### Fixed

- Choosing the piezo gauge for WeeWX rain had no effect: `rain` always came from the tipping gauge.
- `weectl station reconfigure` could leave a `[[Delta]] rain` entry, so WeeWX could calculate rain a
  second time.
- With a tipping gauge selected, `weectl station reconfigure` removed the `rain = prefer_hardware`
  setting it had just added.

### Known limitations

- The installer prompts have been tested with WeeWX 5.5. They are written to also work with
  WeeWX 4's `wee_extension`, but that hasn't been tested yet.

## [0.0.1b1] – 24 September 2026 — first beta

First release of weewx-EcowittGateway (0.0.1b1), a compact rewrite based on Gary Roderick's
`ecowitt_http.py` 0.1.0a28.

### Compatibility

- **Changed:** the module is now `weewx-EcowittGateway.py`. Set `driver = user.weewx-EcowittGateway`,
  and use `user.weewx-EcowittGateway.EcowittHttpService` when running as a service.
- **Unchanged:**
  - the `[EcowittHttp]` configuration section and all its options;
  - the default field map and WeeWX field names;
  - the class names;
  - the database schema;
  - the command-line options.

### Changed

- The code is about 80% smaller: 3,473 lines and 177 KB, down from 16,989 lines and 797 KB.
  - The large hand-written lookup tables (unit groups, field maps, SD-card and Ecowitt.net maps) are
    now generated from short per-channel rules. The generated tables were checked entry by entry
    against the originals.
  - About 150 near-identical try/except blocks in the parser are replaced by a few helper functions.
  - The driver, service and archive paths share one set of rain, lightning and derived-field logic.
- Each poll makes two fewer HTTP requests (unit information that was fetched and not used).
- The station model is read once and cached, instead of on every debug log line.
- Debug log messages have been reworded and tidied. Their content is the same.
- Accumulator settings are generated, and the rain-gauge prompts in the config editor now offer a
  real list of choices.

### Fixed

**Data collection and parsing**

- WQT01 water-quality data made live-data parsing crash with a `NameError`, so no data came through
  when a WQT01 was paired.
- An unresponsive gateway (a request timing out on every attempt) caused an `UnboundLocalError`. This
  stopped the collector thread and data stopped until WeeWX was restarted. It now raises a device
  error, so WeeWX's normal retry and restart logic takes over.
- Other network errors (such as a connection reset) also stopped the collector thread; they are now
  handled the same way.
- A failed firmware update check could stop the collector thread.
- The `retry_wait` option was ignored; it is now applied between attempts after a timeout.
- Soil moisture channel 16 was left out of some checks (WH51/WH52 detection and copying WH52
  humidity and battery values).
- An invalid `time` value from `get_device_info` was stored as `rain_reset_day`.
- The device's rain-rate unit was recorded as `mm`/`inch` instead of `mm_per_hour`/`inch_per_hour`.
- A battery voltage of `None` could crash the battery-state description.

**Service mode**

- The estimated WBGT was always written in °F, even into metric loop packets. It now follows the
  packet's unit system.

**Catchup (history)**

- Ecowitt.net catchup: the monthly rain field name had a typo (`ain.0x12.val`), so monthly rain was
  never mapped.
- Ecowitt.net catchup: piezo 24-hour rain was written into the tipping-bucket 24-hour field.
- SD-card catchup: LDS channel 4 depth and channel 1 total height were never unit-converted (two
  field names had been joined together by a missing comma).
- SD-card catchup: the `[[catchup]] retries` option was ignored.
- `[[catchup]] source = device` without an IP address caused a crash; it now just skips catchup.
- Network errors during Ecowitt.net catchup could stop WeeWX starting; they are now logged.
- A missing piezo capacitor-voltage value in Ecowitt.net history caused a `KeyError`.

**Configuration and command-line tools**

- `weectl device` / `wee_device` crashed on start-up (`'Values' object has no attribute
  'driver_debug'`).
- `--get-all-rain-data` and `--get-mulch-t-cal` in `weectl device` were silently ignored.
- `--driver-map` and `--service-map` could never run; the default map was shown instead. Running
  with no action option now prints the help, as intended.
- `--sensors` crashed when the gateway reported an unknown observation ID.
- Nine display commands crashed if the gateway returned a single malformed value (for example
  `--get-rain-totals`, `--get-calibration` and the calibration commands). They now show `---` instead.
- `--get-rain-totals` always used mm as the main unit, even when the gateway was set to inches.
- `--get-calibration` labelled the humidity offsets as temperature offsets.
- The config editor crashed when no rain gauge was paired, or when the gateway couldn't be reached.
  It now warns and carries on.
- The config editor ignored `field_map_extensions` when working out rain sources.
- A `None` retry or timeout value from the command line could make requests hang or fail.

### Removed

- Unused code: the `KNOWN_SENSORS` list, `known_fields`, `sensor_battery_type`, `services`,
  `sensor_address`, the unused `EcowittSensors` properties (`all`, `enabled`, `disabled`, `learning`,
  `connected`, `all_models`), placeholder base classes and never-read internal flags.
- The long in-file revision history.

### Known limitations

- Beta software. Tested against a simulated gateway (all API endpoints), WeeWX 5.5 `weectl`
  install/reconfigure/uninstall and a real WeeWX engine. It still needs testing on real hardware.

---

## History inherited from ecowitt_http.py (summary)

| Version | Date | Notable changes |
|---|---|---|
| 0.1.x | 10 – 25 July 2025 | First releases, based on Gary Roderick's 0.1.0a28 |

[Unreleased]: #unreleased
[0.0.1b8]: #001b8--24-september-2026--eighth-beta
[0.0.1b7]: #001b7--24-september-2026--seventh-beta
[0.0.1b6]: #001b6--24-september-2026--sixth-beta
[0.0.1b5]: #001b5--24-september-2026--fifth-beta
[0.0.1b4]: #001b4--24-september-2026--fourth-beta
[0.0.1b3]: #001b3--24-september-2026--third-beta
[0.0.1b2]: #001b2--24-september-2026--second-beta
[0.0.1b1]: #001b1--24-september-2026--first-beta
