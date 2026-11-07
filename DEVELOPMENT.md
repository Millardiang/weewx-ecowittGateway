# Development

How to run the tests, add test data from a real gateway, and find your way around the code.

## Running the tests

The tests need Python 3.7 or later, WeeWX 5.4.0 or later and pytest. They don't need a gateway:
they run the driver against a fake gateway on `127.0.0.1`.

```bash
python3 -m venv ~/egw-test
source ~/egw-test/bin/activate
pip install weewx pytest
cd weewx-ecowittGateway
python3 -m pytest tests            # about a minute
```

Also check the code before a release:

```bash
pip install pyflakes
python3 -m pyflakes bin/user/weewx-EcowittGateway.py install.py tests
```

| File | Covers |
|---|---|
| `tests/test_parsing.py` | API responses, units, the field map, SD card CSV files |
| `tests/test_counters_and_sensors.py` | Rain and lightning counters, the hardware ID sensor map, WS68 voltage |
| `tests/test_driver_service.py` | The driver's loop packets and catchup, the service |
| `tests/test_sources.py` | The per-gateway layer (`GatewaySource`), merging, service-mode rain |
| `tests/test_features.py` | `[[limits]]`, `ignore_no_signal`, `gateway_online`, catchup checks, `--sd-files`, `--catchup-dry-run` |
| `tests/test_logging.py` | Long log lines, the active field map, `log_next_update` |
| `tests/test_installer.py` | The installer with `ECOWITT_*` variables, legacy sections, extractors |
| `tests/test_multi.py` | Several gateways: `[[sources]]`, priorities, counters, outages, merged catchup, service, `--sources` |

## Plain-text copies of the documents

Each document (`README.md`, `INSTALL.md`, `CHANGELOG.md`, `VERSIONING.md`, `DEVELOPMENT.md`) has a
plain-text copy beside it (`README.txt` and so on), for reading without a Markdown viewer. Edit the
`.md` files only, then rebuild the copies:

```bash
python3 tools/md_to_txt.py
```

The script needs only the Python standard library.

## Test data from a real gateway

`tests/fixtures/gw2000_ws90.json` is made up, modelled on the Ecowitt local HTTP API. Real responses
are better, and easy to add: the fake gateway (`FakeGateway` in `tests/conftest.py`) serves files in
exactly the format `--dump-api` writes.

1. On the WeeWX machine: `weectl device --dump-api --output=my-gateway.json`. Passwords, keys and
   station IDs are masked.
2. Copy the file into `tests/fixtures/`. Check that every `get_sensors_info?page=N` entry is there
   (the driver reads pages until one is missing), and remove any you would rather not share.
3. Use it in a test with `FakeGateway(load_dump('my-gateway.json'))`, or send it with a bug report.

SD card history files can be added the same way: download one from `http://<gateway>:81/<name>`
(`weectl device --sd-files` lists them) and see the `sd_card` fixture in `tests/test_features.py`.

## How the code is laid out

Everything is in `bin/user/weewx-EcowittGateway.py`, in this order:

| Part | Classes and functions |
|---|---|
| Units and field groups | `define_units`, `_OBS_GROUPS`, `DEFAULT_GROUPS` |
| Field mapping | `FieldMapper`, `HttpMapper` (live data), `SdMapper` (SD card CSV columns) |
| Counters, sensor map, exports | `DeltaTracker`, `SensorMapper`, `LoopJsonWriter`, `MqttPublisher` |
| **One gateway** | `GatewaySource`, `SourceStatus`, `FieldLimits`, `no_signal_keys` |
| **Several gateways** | `source_configs`, `SourcePriority`, `CounterArbiter`, `GatewayMerger`, `GapMonitor`, `aggregate_records`, `merge_history` |
| WeeWX entry points | `EcowittCommon`, `EcowittHttpService`, `EcowittHttpDriver`, `loader` |
| `weectl` support | `EcowittHttpDriverConfEditor`, `EcowittHttpDriverConfigurator` |
| History (catchup) | `EcowittNetCatchup` (Ecowitt.net), `EcowittDeviceCatchup` (SD card) |
| Polling | `EcowittHttpCollector`: a thread that polls one device and queues the data |
| Device access | `EcowittHttpApi`, `EcowittHttpParser`, `EcowittDevice` (HTTP); `EcowittTcpApi`, `EcowittTcpDevice` (GW1000 TCP API); `make_device` |
| Command line | `DirectEcowittDevice`, `main` |

A poll goes like this: the collector's thread calls the device, flattens the responses into one
dict (`common_list.0x02.val`, `ch_aisle.1.temp` …), applies the sensor map and queues the dict.
`GatewaySource.loop_packet()` takes it off the queue and returns a packet of WeeWX fields in the
gateway's unit system (METRICWX), with the rain and lightning counters turned into deltas and
`[[limits]]` applied. The driver yields that packet; the service converts it to the main driver's
units and adds the fields that are missing. Catchup goes through `GatewaySource.history()`, which
gives archive records in the same form.

## Several gateways

With `[[sources]]`, `EcowittCommon` makes one `GatewaySource` per gateway and a `GatewayMerger`.
The design follows [weewx-ecowitt-multi](https://github.com/mKainzbauer/weewx-ecowitt-multi) by
mKainzbauer; the classes are in the "Several gateways" part of the driver:

| Class or function | Does |
|---|---|
| `source_configs` | Reads `[[sources]]`: each gateway inherits the section's settings (except `NOT_INHERITED`) and overrides them; ordered by `source_priority` |
| `SourcePriority` | The gateway order for a field: `[[priority]]` exact names, then the longest `prefix*`, then `source_priority` |
| `CounterArbiter` | Counts each rain and lightning field from one gateway at a time, without overlapping periods; holds a backup gateway's deltas until it takes over |
| `GatewayMerger` | Keeps each gateway's latest packet; `merged()` takes each field from the first fresh gateway with a value, plus the counted deltas |
| `GapMonitor` | Detects an outage of every gateway, holds loop packets until they're back, then asks for an engine restart (`_LAST_GAP_RESTART` survives the restart) |
| `aggregate_records`, `merge_history` | Catchup: each gateway's history into `archive_interval` records (`aggregation()` chooses avg, sum, max, last or vector average), then merged by priority |

The driver's `gen_merged_loop_packets()` drains every gateway's queue (`drain_sources()`), asks the
`GapMonitor` what to do, and yields a merged packet whenever a gateway has delivered data. With one
gateway (no `[[sources]]`) the driver still goes through its original code.

`tests/test_multi.py` runs two fake gateways side by side.

Possible next steps:

- MQTT as a source (weewx-ecowitt-multi has one, for the gateways' own MQTT export and for
  ecowitt2mqtt).
- Polling lower-priority gateways only when the first one is missing something
  (weewx-ecowitt-multi's `poll_mode = auto`); here every gateway is polled all the time.
- Default plausibility limits for every unit group; here `[[limits]]` is per field and off by default.
