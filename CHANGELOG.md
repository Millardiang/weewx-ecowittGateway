# Changelog

All notable changes to weewx-ecowittGateway are recorded here. Versions follow
[Semantic Versioning](https://semver.org/).

## [2.0.0] – 12 November 2026

weewx-ecowittGateway is a WeeWX driver and service for Ecowitt gateways and consoles, built on
Gary Roderick's Ecowitt local HTTP API driver, `ecowitt_http.py` 0.1.0a28
([weewx-contrib/weewx-ecowitt_local_http](https://github.com/weewx-contrib/weewx-ecowitt_local_http),
commit `31bdfe5`). This entry lists what it changes and adds to that driver.

### Names, and installing over another driver

- The driver is `EcowittGateway`: file `bin/user/weewx-ecowittGateway.py`, module
  `user.weewx-ecowittGateway`, section `[EcowittGateway]` of `weewx.conf`, extension
  `weewx-ecowittGateway`. Classes keep `ecowitt_http.py`'s names (`EcowittHttpDriver`,
  `EcowittHttpService` …). `[EcowittHttp]` is still read if there is no `[EcowittGateway]` section.
- Installed over `ecowitt_http.py`, the installer moves `[EcowittHttp]` to `[EcowittGateway]`,
  changes `station_type` and the service lists, and removes `ecowitt_http.py` and its extension
  record, keeping its settings and rain configuration.
- Installed over an earlier extension named `weewx-EcowittGateway`, the installer points `driver`
  and the service lists at the new module, removes the old file and extension record (on a
  case-insensitive file system, where they are the same file and record as the new ones, nothing is
  removed), moves the Ecowitt.net keys into `[[catchup]]`, sets WeeWX's rule for `rain` from its
  `rain_source` setting, and offers to record `radiation`.

### Added

- **Fields** from current firmware: `windDir10` (`0x6D`, 10-minute average wind direction),
  `t_rain24` and `p_rain24` (`0x7C`, 24-hour rain of each gauge). Battery and signal fields for every
  sensor the gateway reports (`ecowitt_http.py` had none for the WH40, WH45/WH46, WH68, WS69, WS80,
  WS85 and WN35, nor for WH51 channels 9–16, nor signal for WH54 and WH55 channel 4).
- **Sensor map** (`[[sensor_map]]`): multi-channel sensors (WN31, WN34, WN35, WH41, WH51, WH54,
  WH55) are reported on a fixed channel chosen by sensor ID, so re-pairing a sensor doesn't move its
  data to other WeeWX fields; it applies to loop packets, catchup records, the service and the
  command line (`--no-sensor-map` to see the gateway's channels). `weectl device --sensor-map` lists
  the paired sensors.
- **Rain source**: which gauge WeeWX `rain`/`rainRate` come from (`tipping`, `piezo` or `none`),
  configured the way `ecowitt_http.py` does it (WeeWX derives `rain` from that gauge's yearly total
  in `[StdWXCalculate] [[Delta]]`). The installer asks; `weectl station reconfigure` changes it. The
  driver logs a warning at startup if the rule names a total it doesn't produce (WeeWX's default
  without a rule is `totalRain`), which would leave `rain` empty.
- **ecwLoop.json and MQTT** (`[[loop_json]]`, `[[mqtt]]`): written/published by the
  `EcowittGatewayExport` service, so each packet includes the fields WeeWX calculates, in driver and
  service mode alike. Off by default.
- **GW1000** through the Ecowitt binary TCP API, with the same field names and units as the HTTP
  API (`api = auto|http|tcp`, `tcp_port`; `weectl device --api`). Catchup for a GW1000 uses
  Ecowitt.net.
- **Installer**: asks for the device address (checking it over HTTP or the TCP API), poll interval,
  driver/service/skip, rain source, catchup source and Ecowitt.net keys, sensor map, battery
  display, `radiation`, `loop_on_init`, `ecwLoop.json` and MQTT. Every answer can come from an
  `ECOWITT_*` environment variable for unattended installs; a bad value stops the install before
  anything is changed. Without a terminal or variables it adds its settings and leaves the station
  driver unchanged.
- **Several gateways** (`[[sources]]`, after the design of
  [weewx-ecowitt-multi](https://github.com/mKainzbauer/weewx-ecowitt-multi) by mKainzbauer): each
  polled by its own thread and merged into one stream of loop packets by priority (`source_priority`,
  `[[priority]]`, `max_age`). Running totals (rain, lightning and any other `[[Delta]]` input) are
  merged so WeeWX counts each rise once, keeping the part of a rise that comes after what was
  counted when another gateway takes over. After an outage of every gateway the driver restarts so
  catchup fills it (`gap_mode`, `max_gap_wait`, `min_restart_interval`); catchup combines and merges
  each gateway's history. Loop fields `gateway_online_<name>`; `weectl device --sources` and
  `--source=NAME`; `debug = merge`.
- **`[[limits]]`**: readings outside a minimum and maximum are dropped in loop packets, catchup
  records and the data the service adds.
- **`ignore_no_signal`**: a multi-channel sensor's readings are left out while its signal is 0.
- **`gateway_online`** loop field: `1` while the gateway is answering.
- **`radiation_from_illuminance`**: WeeWX `radiation` (W/m²) worked out from the light sensor's
  `illuminance` (lux) with Ecowitt's factor (126.7 lux per W/m²), so standard skins show it and WeeWX
  can work out `ET`.
- **`pm2_5`** is filled from the WH45/WH46 when there is no WH41/WH43 on channel 1.
- **Catchup**: a warning when the records' interval differs from `archive_interval`, a summary after
  each catchup, `[[catchup]] file_port` for a device behind port forwarding.
- **`[Accumulator]` entries made from the field map**: counts (`t_rain`, `p_rain`,
  `lightning_strike_count`) are totalled; battery and signal states, running rain totals, the
  lightning count, distance and time, `daymaxwind`, `windDir10` and 24-hour AQIs keep their last
  value. As the station driver, the installer and `weectl station reconfigure` remove entries other
  extensions added for names this driver never produces (only those still as they were added, and not
  fields in the field map settings), and correct a `lightningcount` entry of `sum` to `last`.
- **Logging**: long log lines split over numbered lines (syslog cuts longer messages off); the field
  map's size at `debug = 1`, the map at `debug = 2`; the active field map (the entries the gateway is
  filling) logged after the first poll; `log_next_update`.
- **Command line** (`weectl device` and the driver's own): `--active-map`, `--sensor-map`,
  `--dump-api` (every raw API response as JSON, masked), `--sd-files`, `--catchup-dry-run`,
  `--sources`, `--source`, `--registered-only`, `--api`.
- **Documents**: README, INSTALL.md (step-by-step and unattended installs), DEVELOPMENT.md, with
  plain-text copies (`tools/md_to_txt.py`).
- **Tests** (`tests/`, `python3 -m pytest`): the driver, service, installer and every command-line
  option against fake HTTP API and GW1000 gateways and a fake MQTT broker; a snapshot of the API
  parser's and the command-line tool's output (over 1,600 parser cases and 99 command-line runs).
  321 tests in `tests/` and 21 in Gary Roderick's suite pass (1 skipped).

### Changed

- Everything belonging to one gateway (settings, field map, collector thread, catchup, status) is in
  one class, `GatewaySource`; the driver and the service each have one, or one per gateway.
- The API parser is rewritten with shared helpers in place of a block of code per reading (39 KB
  instead of 190 KB), the command-line tool reads the device and reports errors in one place, and
  comments that only restated the code are gone. With everything added, the file is 577 KB against
  `ecowitt_http.py`'s 651 KB.

### Fixed in ecowitt_http.py 0.1.0a28

Driver and catchup:

- WH45/WH46 data (`co2`, `pm2_55`, `pm10_0` …) never reached WeeWX: the parser returned the sensor
  as a list, giving fields `co2.0.<obs>`; PM10 (and WH46 PM1/PM4) values were left as text.
- Catchup from Ecowitt.net stopped WeeWX at startup (`'EcowittHttpDriver' object has no attribute
  'api_key'`): the keys were never read. When it ran, its records had no readings (mapped to names
  the field map doesn't use), and its hourly and daily rain were the wrong way round.
- SD card catchup failed when `ip_address` includes a port, stopped at a file on the card whose name
  isn't a year and month, ended at a connection lost while reading a file, and gave records a
  floating-point `dateTime`. `[[catchup]] retries` (and, with `source = device`, `grace`) were
  ignored; with `source = either`, an SD card failure stopped the catchup instead of falling back to
  Ecowitt.net.
- A response the driver couldn't parse, or an error during the daily firmware check, ended the
  collector thread, and the driver then waited for data for ever. When every attempt to reach the
  gateway timed out, the HTTP API failed with `UnboundLocalError`.
- While a gateway couldn't be reached, every poll logged errors although the service had turned
  failure logging off.
- `t_rain`, `p_rain` (and `t_rainrate`, `p_rainrate`) had no unit group, so WeeWX passed them through
  unconverted.
- The `[Accumulator]` entries named fields the driver never produces (`lightning_distance` for
  `lightningdist` …) and left others averaged (`lightningcount`, `lightningdettime`, the 24-hour
  AQIs, `ws90_sig`).
- The WS69 sensor array, which the API calls `wh69`, wasn't recognised in battery decoding or the
  sensor lists.
- An unreadable WH55 leak sensor voltage stopped all live data being parsed; an unreadable device
  time set `rain_reset_day` instead of `time`.
- Logging: with `debug = collector` every poll gave a logging error; the error for a live-data array
  that couldn't be processed was a logging error; the startup lines for the `sensors`, `catchup` and
  `parser` debug options showed other options' values; `debug = "rain, wind"` (one quoted string)
  turned nothing on; `debug = loop` never showed the packet time; the indoor sensor was called WH26.

Config editor (`weectl station reconfigure`):

- `--no-prompt` (or no terminal) failed with `EOFError`; with no rain gauge paired it failed with
  `IndexError`, and it stopped if the device couldn't be reached.
- Choosing `none` for the rain source left WeeWX calculating `rain` from the old gauge; any text
  containing part of a field name was accepted as the field; a missing `[StdWXCalculate]` or
  `[[Delta]]` raised `KeyError`.
- A new `[EcowittGateway]` section listed options the driver doesn't read.

Command line:

- `weectl device` failed on every command (`--debug` read under the wrong name); commands that
  contact the device failed with no `--max-tries` default; `--sensors` failed at the first field the
  driver didn't know, and with `weectl device` on `registered_only`; `--default-map`, `--driver-map`
  and `--service-map` failed, and the last two left a thread polling the device.
- `--get-rain-data`, `--get-all-rain-data` and `--get-mulch-t-cal` did nothing; `--get-rain-totals`
  failed without a rain gauge list, `--get-rain-data` when a total was missing, `--get-services` when
  a setting was missing.
- `--sensors` listed the WH57 twice and left out the WH51, the WH45/WH46 and any model not in its
  fixed list.
- The direct command line ignored `debug` in `weewx.conf`; `--test-service` ignored the station's
  settings other than the IP address.

Installer and code:

- WeeWX version numbers were compared as text (5.10 would have counted as older than 5.9).
- Regular expressions written as ordinary strings gave "invalid escape sequence" warnings under
  Python 3.12 and later.

## Ecowitt local HTTP API driver (ecowitt_http.py), by Gary Roderick

### v0.1.0

- initial release
