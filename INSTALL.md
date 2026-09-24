# Installing weewx-EcowittGateway

Instructions for WeeWX 5.4.0 or later, installed from the **Debian/apt package** or with **pip**.
Each section covers a new install, switching from `ecowitt_http.py`, upgrading and removal.

The installer stops with an error on older WeeWX versions. Check yours with `weectl --version`, and
upgrade WeeWX first if it's older than 5.4.0.

The installer asks for your settings and writes them to `weewx.conf`, so nothing needs editing by
hand. In the examples, replace `192.168.1.100` with your gateway's IP address. You can find it in
the WSView Plus app, on your router, or with the `--discover` tool described at the end.

---

## Before you start

1. **Check the gateway is reachable.** From the WeeWX machine run:
   ```bash
   curl http://192.168.1.100/get_version
   ```
   You should get a line of JSON such as `{"version":"Version: GW2000A_V3.1.2", ...}`.
2. **Give the gateway a fixed IP address** (a DHCP reservation on your router), so it doesn't change.
3. **Back up `weewx.conf`.** The installer saves a timestamped copy too, but it's good practice.
4. **If you're replacing `ecowitt_http.py`**, don't run both at once. Follow the
   *switching from ecowitt_http.py* steps in your section.

---

## What the installer asks

Press Enter to accept the value in [brackets].

```
Configuring weewx-EcowittGateway
Press Enter to accept the value shown in [brackets].

Gateway IP address, eg 192.168.1.100 [replace_me]: 192.168.1.100
    Found GW2000A_V3.1.2 at 192.168.1.100
Poll interval in seconds [20]:
Use as the station driver, as a service alongside another driver, or skip (driver/service/skip) [driver]:
    Paired rain gauges: piezo and tipping
Which gauge should feed WeeWX 'rain' and 'rainRate' (tipping/piezo) [tipping]:
Fetch missed data at startup from (either/device/net/none) [either]:
    Ecowitt.net keys are only needed to fetch missed data from Ecowitt.net
    (press Enter to leave them blank).
    Ecowitt.net API key []:
    Ecowitt.net application key []:
Show battery state for sensors with no signal? (y/n) [n]:
Keep retrying at startup if the gateway cannot be reached (loop_on_init)? (y/n) [y]:
```

- **IP address:** the installer contacts the gateway to check the address. If there's no answer,
  it asks whether to use the address anyway or try another.
- **Rain gauges:** the installer reads which gauges are paired. It only asks which one feeds WeeWX's
  `rain`/`rainRate` when both types are paired.
- **driver:** makes the gateway your station. The installer sets `station_type`, software archive
  records, `loop_on_init` and the rain calculation settings.
- **service:** adds gateway data to another driver's loop packets. Your `station_type` is left alone.
- **skip:** only saves the gateway settings.

The `[EcowittGateway]` section is written directly after `[Station]`. When re-run, for example for an
upgrade, the installer offers your current settings as the defaults.

Upgrading from 0.0.1b1/b2 or switching from `ecowitt_http.py`: settings in an `[EcowittHttp]` section
are moved to `[EcowittGateway]`, and `station_type = EcowittHttp` becomes `station_type = EcowittGateway`.

If the installer is run without a terminal (for example from a script), it doesn't ask anything.
It saves the default settings with `ip_address = replace_me` and doesn't change the station driver.

---

## A. WeeWX 5 – Debian / Ubuntu / Raspberry Pi OS (apt package)

| Item | Location |
|---|---|
| Configuration | `/etc/weewx/weewx.conf` |
| User extensions | `/etc/weewx/bin/user/` |
| WeeWX program files | `/usr/share/weewx/` |
| Logs | `journalctl -u weewx` (or `/var/log/syslog`) |

### New install

```bash
# 1. Install the extension and answer the prompts
sudo weectl extension install weewx-EcowittGateway-0.0.1b3.zip

# 2. Check it can talk to the gateway
sudo weectl device --live-data

# 3. Restart WeeWX and watch the log
sudo systemctl restart weewx
sudo journalctl -u weewx -f
```

When it's working, the log shows lines like:

```
EcowittHttpDriver: version is 0.0.1b3
     device IP address is 192.168.1.100
EcowittHttpCollector startup
Using 'rain.0x13.val' for rain total
```

### Switching from ecowitt_http.py

```bash
sudo systemctl stop weewx
sudo weectl extension list                       # note the old extension's name
sudo weectl extension uninstall <old-name>       # or delete /etc/weewx/bin/user/ecowitt_http.py
sudo weectl extension install weewx-EcowittGateway-0.0.1b3.zip
sudo systemctl start weewx
```

If `weewx.conf` still has an `[EcowittHttp]` section, the installer moves its settings to
`[EcowittGateway]`, including any custom field map, and updates `station_type` to match. The
database needs no changes.

### Upgrading to a newer version of this driver

```bash
sudo weectl extension install weewx-EcowittGateway-<new-version>.zip
sudo systemctl restart weewx
```

Press Enter at each prompt to keep your current settings.

### Removing

```bash
sudo weectl extension uninstall weewx-EcowittGateway
sudo weectl station reconfigure        # driver mode only: choose another driver, e.g. Simulator
sudo systemctl restart weewx
```

The uninstall removes:

- the driver file;
- the `[EcowittGateway]` section;
- the accumulator entries;
- the service entry, if you used service mode.

Any settings you added yourself, such as `[[field_map_extensions]]`, are left in place.

WeeWX's uninstaller can't change `station_type`. If you used driver mode, run
`weectl station reconfigure` as shown to choose another driver before restarting.

---

## B. WeeWX 5 – pip install (Python virtual environment)

| Item | Location |
|---|---|
| Virtual environment | `~/weewx-venv/` (the default in the WeeWX guide) |
| Configuration | `~/weewx-data/weewx.conf` |
| User extensions | `~/weewx-data/bin/user/` |
| Logs | as configured, usually syslog/journal when run as a daemon |

Always activate the virtual environment first, so that `weectl` and `python3` are WeeWX's own:

```bash
source ~/weewx-venv/bin/activate
```

### New install

```bash
source ~/weewx-venv/bin/activate

# 1. Install the extension and answer the prompts
weectl extension install weewx-EcowittGateway-0.0.1b3.zip

# 2. Check it can talk to the gateway
weectl device --live-data

# 3a. Running WeeWX in a terminal: stop it with Ctrl-C and start it again
weewxd

# 3b. Running WeeWX as a daemon (set up with ~/weewx-data/scripts/setup-daemon.sh)
sudo systemctl restart weewx
```

If your station data isn't in `~/weewx-data`, add `--config=/path/to/weewx.conf` to each `weectl`
command.

### Switching from ecowitt_http.py

```bash
source ~/weewx-venv/bin/activate
sudo systemctl stop weewx                        # or stop weewxd
weectl extension list
weectl extension uninstall <old-name>            # or delete ~/weewx-data/bin/user/ecowitt_http.py
weectl extension install weewx-EcowittGateway-0.0.1b3.zip
sudo systemctl start weewx
```

### Upgrading / removing

These are the same as the Debian instructions above, without `sudo` on the `weectl` commands.

---

## Changing settings later

Run the installer again and change the answers, or edit `[EcowittGateway]` in `weewx.conf` directly.
See the README for every option. Restart WeeWX after either.

`weectl station reconfigure --driver=user.weewx-EcowittGateway` also works. It asks the same rain
gauge questions, as part of WeeWX's full station set-up.

---

## Manual install (without the installer)

1. Copy `bin/user/weewx-EcowittGateway.py` into your user directory (see the tables above).
2. Add this to `weewx.conf`, with `[EcowittGateway]` directly after `[Station]`:
   ```ini
   loop_on_init = 1

   [Station]
       station_type = EcowittGateway

   [EcowittGateway]
       driver = user.weewx-EcowittGateway
       ip_address = 192.168.1.100
       rain_source = tipping

   [StdArchive]
       record_generation = software

   [StdWXCalculate]
       [[Calculations]]
           rain = prefer_hardware
   ```
3. Remove any `[StdWXCalculate] [[Delta]] [[[rain]]]` entry, then restart WeeWX.

For service mode, leave `station_type` alone and add
`user.weewx-EcowittGateway.EcowittHttpService` to `data_services` under `[Engine] [[Services]]`.

---

## Running the command-line tools

`weectl device …` covers most needs.

The extra tools (`--discover`, `--test-driver`, `--test-service`, `--weewx-fields`, `--default-map`,
`--driver-map`, `--service-map`) need the module to be run directly:

**Debian (WeeWX 5):**
```bash
sudo PYTHONPATH=/usr/share/weewx python3 /etc/weewx/bin/user/weewx-EcowittGateway.py \
     --config=/etc/weewx/weewx.conf --test-driver
```

**pip:**
```bash
source ~/weewx-venv/bin/activate
python3 ~/weewx-data/bin/user/weewx-EcowittGateway.py --config=$HOME/weewx-data/weewx.conf --test-driver
```

Stop `--test-driver` and `--test-service` with Ctrl-C. Add `--help` to see every option.

`--discover` listens for gateway broadcasts on UDP port 59387. The firewall must allow this, and the
gateway must be on the same network segment.

**Stop WeeWX before running `--test-driver` or `--test-service`**, so the gateway isn't being polled
twice.
