# Installing weewx-EcowittGateway

Instructions for WeeWX 5 installed from the **Debian/apt package** or with **pip**, and for
**WeeWX 4**. Each section covers a new install, switching from `ecowitt_http.py`, upgrading and
removal.

In the examples, replace `192.168.1.100` with your gateway's IP address. You can find it in the
WSView Plus app, on your router, or with the `--discover` tool described at the end.

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

## A. WeeWX 5 – Debian / Ubuntu / Raspberry Pi OS (apt package)

| Item | Location |
|---|---|
| Configuration | `/etc/weewx/weewx.conf` |
| User extensions | `/etc/weewx/bin/user/` |
| WeeWX program files | `/usr/share/weewx/` |
| Logs | `journalctl -u weewx` (or `/var/log/syslog`) |

### New install

```bash
# 1. Install the extension
sudo weectl extension install weewx-EcowittGateway.zip

# 2. Select and configure the driver (asks for IP address, poll interval and rain gauges)
sudo weectl station reconfigure --driver=user.weewx-EcowittGateway

# 3. Check it can talk to the gateway
sudo weectl device --live-data

# 4. Restart WeeWX and watch the log
sudo systemctl restart weewx
sudo journalctl -u weewx -f
```

When it's working, the log shows lines like:

```
EcowittHttpDriver: version is 0.0.1b1
     device IP address is 192.168.1.100
EcowittHttpCollector startup
Using 'rain.0x13.val' for rain total
```

### Switching from ecowitt_http.py

```bash
sudo systemctl stop weewx
sudo weectl extension list                       # note the old extension's name
sudo weectl extension uninstall <old-name>       # or delete /etc/weewx/bin/user/ecowitt_http.py
sudo weectl extension install weewx-EcowittGateway.zip
sudo nano /etc/weewx/weewx.conf                  # in [EcowittHttp] change: driver = user.weewx-EcowittGateway
sudo systemctl start weewx
```

Your existing `[EcowittHttp]` settings, field map and database work unchanged.

Uninstalling the old extension may remove its `[EcowittHttp]` section. If it does, run step 2 of the
new install instead of editing by hand.

### Upgrading to a newer version of this driver

```bash
sudo weectl extension install weewx-EcowittGateway-<new-version>.zip
sudo systemctl restart weewx
```

Your settings are kept, because the installer only adds settings that are missing.

### Removing

```bash
sudo weectl extension uninstall weewx-EcowittGateway
sudo weectl station reconfigure        # choose another driver, e.g. Simulator
sudo systemctl restart weewx
```

This also removes the `[EcowittHttp]` section, including any settings you changed there. Back it up
first if you might reinstall.

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

# 1. Install the extension
weectl extension install weewx-EcowittGateway.zip

# 2. Select and configure the driver
weectl station reconfigure --driver=user.weewx-EcowittGateway

# 3. Check it can talk to the gateway
weectl device --live-data

# 4a. Running WeeWX in a terminal: stop it with Ctrl-C and start it again
weewxd

# 4b. Running WeeWX as a daemon (set up with ~/weewx-data/scripts/setup-daemon.sh)
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
weectl extension install weewx-EcowittGateway.zip
nano ~/weewx-data/weewx.conf                     # in [EcowittHttp] change: driver = user.weewx-EcowittGateway
sudo systemctl start weewx
```

### Upgrading / removing

These are the same as the Debian instructions above, without `sudo` on the `weectl` commands.

---

## C. WeeWX 4 (deb package or setup.py install)

WeeWX 4 uses the older `wee_*` utilities.

| Item | deb package | setup.py install |
|---|---|---|
| Configuration | `/etc/weewx/weewx.conf` | `/home/weewx/weewx.conf` |
| User extensions | `/usr/share/weewx/user/` | `/home/weewx/bin/user/` |

```bash
sudo wee_extension --install=weewx-EcowittGateway.zip
sudo wee_config --reconfigure --driver=user.weewx-EcowittGateway
sudo systemctl restart weewx
```

`wee_device` works in the same way as `weectl device`, for example `sudo wee_device --live-data`.

---

## Manual install (without the installer)

1. Copy `bin/user/weewx-EcowittGateway.py` into your user directory (see the tables above).
2. Add at least this to `weewx.conf`:
   ```ini
   loop_on_init = 1

   [Station]
       station_type = EcowittHttp

   [EcowittHttp]
       driver = user.weewx-EcowittGateway
       ip_address = 192.168.1.100

   [StdArchive]
       record_generation = software

   [StdWXCalculate]
       [[Calculations]]
           rain = prefer_hardware
   ```
3. Remove any `[StdWXCalculate] [[Delta]] [[[rain]]]` entry, then restart WeeWX.

Run `weectl station reconfigure --driver=user.weewx-EcowittGateway` if you also want the
`[Accumulator]` entries and the rain gauge set-up.

---

## Running as a service instead of a driver

Install the extension (step 1 only, skip `reconfigure`), then edit `weewx.conf`:

```ini
[EcowittHttp]
    ip_address = 192.168.1.100

[Engine]
    [[Services]]
        data_services = user.weewx-EcowittGateway.EcowittHttpService
```

Leave `station_type` set to your existing driver and restart WeeWX. See the README for the
service options.

---

## Running the command-line tools

`weectl device …` (WeeWX 5) or `wee_device …` (WeeWX 4) covers most needs.

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
