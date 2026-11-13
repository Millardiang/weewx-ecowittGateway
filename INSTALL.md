# Installing weewx-ecowittGateway

Instructions for weewx-ecowittGateway 2.0.0 on WeeWX 5, installed from the **Debian/apt package**
or with **pip**. Each section covers a new install, switching from `ecowitt_http.py`, upgrading and
removal. Section C covers installing with no questions asked, from a script, Vagrant or Ansible.

The installer needs WeeWX 5.0.0 or later and stops with an error on older versions. Check yours with
`weectl --version`.

The installer asks for your settings and writes them to `weewx.conf`, so nothing needs editing by
hand. In the examples, replace `192.168.1.100` with your gateway's IP address. You can find it in
the WSView Plus app, on your router, or with the `--discover` tool described at the end.

---

## Before you start

1. **Check the gateway is reachable.** From the WeeWX machine run:
   ```bash
   curl http://192.168.1.100/get_version
   ```
   You should get a line of JSON such as `{"version":"Version: GW2000A_V3.1.4", ...}`.
   A GW1000 has no HTTP API, so `curl` fails. Check instead that TCP port 45000 can be reached, for
   example with `nc -zv 192.168.1.100 45000`. The installer finds a GW1000 on that port.
2. **Give the gateway a fixed IP address** (a DHCP reservation on your router), so it doesn't change.
3. **Back up `weewx.conf`.** The installer saves a timestamped copy too, but it's good practice.
4. **If you're replacing Gary Roderick's `ecowitt_http.py`, don't uninstall it first.** Install this
   over it; see *Switching from ecowitt_http.py* in your section.
5. **If an earlier weewx-EcowittGateway extension is installed**, read *From an earlier
   weewx-EcowittGateway extension* in
   [README.md](README.md#from-an-earlier-weewx-ecowittgateway-extension) first. Install over it;
   don't uninstall it first.

---

## What the installer asks

Press Enter to accept the value in [brackets].

```
Configuring weewx-ecowittGateway
Press Enter to accept the value shown in [brackets].

Gateway IP address, eg 192.168.1.100 [www.xxx.yyy.zzz]: 192.168.1.100
    Found GW2000A_V3.1.4 at 192.168.1.100
Poll interval in seconds [20]:
Use as the station driver, as a service alongside another driver, or skip (driver/service/skip) [driver]:
    Paired rain gauges: piezo and tipping
    tipping = WeeWX 'rain'/'rainRate' from the tipping gauge
    piezo   = WeeWX 'rain'/'rainRate' from the piezo gauge
    none    = leave 'rain'/'rainRate' empty
    Each paired gauge is also recorded in 't_rain'/'t_rainrate' or 'p_rain'/'p_rainrate', unless it is the one used for 'rain'.
Source of WeeWX 'rain' (tipping/piezo/none) [tipping]:
Fetch missed data at startup from (either/device/net/none) [either]:
    Ecowitt.net keys are only needed to fetch missed data from Ecowitt.net
    (press Enter to leave them blank).
    Ecowitt.net API key []:
    Ecowitt.net application key []:
    Multi-channel sensors found:
        WN31  CH1   ID 5E
        WH41  CH1   ID C3
        WH51  CH2   ID C0FFEE
    Fixing a sensor to its channel keeps its data in the same WeeWX fields
    if it is re-paired onto a different channel later.
Fix these sensors to their current channels (sensor_map)? (y/n) [y]:
Show battery state for sensors with no signal? (y/n) [n]:
Record WeeWX 'radiation' (W/m2), worked out from the light sensor (illuminance, lux)? (y/n) [n]:
Keep retrying at startup if the gateway cannot be reached (loop_on_init)? (y/n) [y]:
Write each loop packet to ecwLoop.json (for web pages and scripts)? (y/n) [n]: y
    web    = WeeWX web pages folder: /home/pi/weewx-data/public_html/ecwLoop.json
    data   = WeeWX data folder: /home/pi/weewx-data/ecwLoop.json
    tmp    = /tmp/ecwLoop.json (often held in memory, which saves SD card writes)
    custom = a folder or file path of your choice
Where should ecwLoop.json be written (web/data/tmp/custom) [web]:
Units for ecwLoop.json (native/us/metric/metricwx) [native]:
Publish each loop packet to an MQTT broker? (y/n) [n]: y
    Broker host name or IP address [localhost]: 192.168.1.20
    Use an encrypted (TLS) connection? (y/n) [n]:
    Broker port [1883]:
    Broker 192.168.1.20:1883 is reachable
    Username (Enter for none) []: weewx
    Password (Enter for none):
    Topic [weewx/ecowitt]:
    json       = one message with all fields on weewx/ecowitt/loop
    individual = one message per field, e.g. weewx/ecowitt/outTemp
    both       = both of the above
    Message format (json/individual/both) [json]:
    Units (native/us/metric/metricwx) [native]:
    Retain the latest messages on the broker? (y/n) [n]:

Station driver set to EcowittGateway (user.weewx-ecowittGateway); archive records generated in software.
Restart WeeWX to start using the new settings.
```

- **IP address:** the installer contacts the gateway to check the address. A GW1000 is found through
  its TCP API and shown as, for example, `Found GW1000B_V1.7.7 (TCP API)`. If nothing answers, it
  asks whether to use the address anyway or try another.
- **driver:** makes the gateway your station. The installer sets `station_type`, software archive
  records, `loop_on_init` and the rain calculation.
- **service:** adds gateway data to another driver's loop packets. Your `station_type` is left alone.
- **skip:** only saves the gateway settings.
- **Rain source** (driver mode): WeeWX works out the rain in each archive period from the chosen
  gauge's yearly total (`[StdWXCalculate] [[Delta]]`). The question lists the gauges the gateway has
  paired; with only one paired, only that one (or `none`) is offered. If the gateway couldn't be
  checked, the default is `tipping`. See the README's *Rain source* section.
- **Missed data (catchup):** `either` uses the gateway's SD card if it has one, else Ecowitt.net.
  Ecowitt.net needs the API and application keys of the account the gateway uploads to. A GW1000
  has no SD card, so it's offered only `net`, `none` or `either`.
- **Sensor map:** if multi-channel sensors (WN31, WN34, WN35, WH41, WH51, WH54, WH55) are paired,
  the installer lists them with their IDs and offers to fix each to its current channel, so its
  data stays in the same WeeWX fields if it's re-paired later. On a re-install, only sensors not
  already in the map are offered. See the README's *Sensor map* section.
- **radiation:** the light sensor measures illuminance (lux), recorded in `illuminance`. Answer **y**
  to also record WeeWX's `radiation` (W/m²), worked out with Ecowitt's factor (126.7 lux per W/m²),
  Standard skins show `radiation`, and WeeWX needs it to work out evapotranspiration (`ET`). The
  default is **y** when installing over an earlier weewx-EcowittGateway extension, else **n**.
- **ecwLoop.json:** optional. Writes every loop packet, including the fields WeeWX calculates, to a
  JSON file for live web pages or scripts, in the location and units you choose.

  > **Note:** if you use a skin or extension that writes its own live JSON data, such as
  > weewx-DivumWX or weewx-loopdata, answer **n** here. Those already provide the live data their
  > pages need, so a second file only adds disk writes. To turn it off later, set `enable = False`
  > under `[EcowittGateway] [[loop_json]]` in `weewx.conf` and restart WeeWX.
- **MQTT:** optional. Publishes every loop packet to an MQTT broker. The installer checks the broker
  can be reached, and the password isn't shown as you type it. This needs the `paho-mqtt` package:
  `sudo apt install python3-paho-mqtt` (Debian) or `pip install paho-mqtt` (pip, with the WeeWX
  virtual environment active). The installer tells you if it's missing.

The `[EcowittGateway]` section is written directly after `[Station]`. When re-run, for example for an
upgrade, the installer offers your current settings as the defaults.

If the installer is run without a terminal (for example from a script), it doesn't ask anything.
Unless you give the answers in environment variables (section C), it saves the default settings with
`ip_address = www.xxx.yyy.zzz` and doesn't change the station driver.

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
sudo weectl extension install weewx-ecowittGateway-2.0.0.zip

# 2. Check it can talk to the gateway
sudo weectl device --live-data

# 3. Restart WeeWX and watch the log
sudo systemctl restart weewx
sudo journalctl -u weewx -f
```

When it's working, the log shows lines like:

```
EcowittHttpDriver: version is 2.0.0
     device IP address is 192.168.1.100
     poll interval is 20 seconds
```

### Switching from ecowitt_http.py

```bash
sudo systemctl stop weewx
sudo weectl extension install weewx-ecowittGateway-2.0.0.zip
sudo systemctl start weewx
```

**Don't uninstall `ecowitt_http.py` first.** Uninstalling it deletes its `[EcowittHttp]` section and
the `[StdWXCalculate]` and `[Accumulator]` entries it added, which this driver uses too. The
installer instead:

- moves `[EcowittHttp]`, with your settings and any custom field map, to `[EcowittGateway]`;
- changes `station_type` and the service lists to the new names;
- removes `ecowitt_http.py` and its extension record (`Ecowitt_HTTP` in `weectl extension list`).

Your rain configuration is kept: the installer offers the gauge `rain` already comes from. The
field names are the same as `ecowitt_http.py`'s, so the database needs no changes.

### Upgrading to a newer version of this driver

```bash
sudo weectl extension install weewx-ecowittGateway-<new-version>.zip
sudo systemctl restart weewx
```

Press Enter at each prompt to keep your current settings.

### Removing

```bash
sudo weectl extension uninstall weewx-ecowittGateway
sudo weectl station reconfigure        # driver mode only: choose another driver, e.g. Simulator
sudo systemctl restart weewx
```

The uninstall removes the driver file, the service entries, and the settings the installer added to
`[EcowittGateway]`, `[StdWXCalculate]` and `[Accumulator]`. **Your `[[sensor_map]]` and `[[limits]]`
entries go with them**, so note them down first if you might install again.

WeeWX's uninstaller can't undo the changes made from your answers. Afterwards:

- in driver mode, `station_type` still names `EcowittGateway`: run `weectl station reconfigure`, as
  shown, to choose another driver before restarting;
- the rain calculation stays: remove `[[[rain]]]` (with `input = t_rainyear` or `p_rainyear`) from
  `[StdWXCalculate] [[Delta]]`, unless your new driver needs WeeWX to work out `rain` the same way;
- `[EcowittGateway]` is left with only `[[field_map_extensions]]` in it (the `rainRate` mapping),
  plus any settings you added by hand, such as `[[sources]]` and `[[priority]]` for several
  gateways: delete the section.

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
weectl extension install weewx-ecowittGateway-2.0.0.zip

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
weectl extension install weewx-ecowittGateway-2.0.0.zip
sudo systemctl start weewx
```

As in the Debian section: don't uninstall `ecowitt_http.py` first.

### Upgrading / removing

These are the same as the Debian instructions above, without `sudo` on the `weectl` commands.

---

## C. Unattended (scripted) install

Use this when the install has to run with nobody at the keyboard: a `vagrant up`, an Ansible play,
cloud-init, or your own build script. You give the installer its answers in environment variables
instead of typing them.

### How it works

The installer asks the same questions as an ordinary install (see
[What the installer asks](#what-the-installer-asks)), and takes each answer like this:

1. **If the question's `ECOWITT_*` variable is set,** it uses that value and doesn't ask. It prints
   the question with the answer and `(from ECOWITT_...)`, so the install output is a record of what
   was set. Passwords and keys are printed as `********`.
2. **Otherwise, if there's no terminal** (as in a provisioning script) **or `ECOWITT_NO_PROMPT=yes`
   is set,** it uses the default, and prints it with `(default)`. On an upgrade or re-install the
   default is your current setting.
3. **Otherwise** it asks you at the terminal as usual.

So you only need variables for the answers that differ from the defaults. On a new install that's
usually just the gateway address and how to use it:

```bash
ECOWITT_IP_ADDRESS=192.168.1.100 ECOWITT_MODE=driver
```

Two more things are needed for a run with no questions at all:

- **`--yes` on `weectl extension install`.** Without it `weectl` asks you to confirm the install.
- **`ECOWITT_NO_PROMPT=yes` if the script runs at a terminal.** Without a terminal it isn't needed,
  but it does no harm, so it's simplest to always set it.

### Step 1: choose your answers

| Variable | Values | Default if not set (new install) |
|---|---|---|
| `ECOWITT_IP_ADDRESS` | IP address, or `host:port` | none: `ip_address = www.xxx.yyy.zzz` and the station driver isn't changed. **Set this.** |
| `ECOWITT_MODE` | `driver`, `service` or `skip` | `driver` when `ECOWITT_IP_ADDRESS` is set, otherwise `skip` |
| `ECOWITT_POLL_INTERVAL` | seconds | `20` |
| `ECOWITT_RAIN_SOURCE` | `tipping`, `piezo` or `none` (`both` means `tipping`) | `tipping`, or `piezo` if that is the only gauge paired (driver mode only) |
| `ECOWITT_CATCHUP` | `either`, `device`, `net` or `none` | `either` |
| `ECOWITT_API_KEY`, `ECOWITT_APP_KEY` | text | blank. Only used for catchup from Ecowitt.net (`either` or `net`). |
| `ECOWITT_SENSOR_MAP` | yes/no | yes: fix any multi-channel sensors found to their channels |
| `ECOWITT_SHOW_ALL_BATT` | yes/no | no |
| `ECOWITT_RADIATION` | yes/no | yes over an earlier weewx-EcowittGateway extension, else no |
| `ECOWITT_LOOP_ON_INIT` | yes/no | yes (driver mode only) |
| `ECOWITT_LOOP_JSON` | yes/no | no |
| `ECOWITT_LOOP_JSON_LOCATION` | `web`, `data`, `tmp` or `custom` | `web` |
| `ECOWITT_LOOP_JSON_PATH` | folder or file path | none. Setting it on its own means `custom`. |
| `ECOWITT_LOOP_JSON_UNITS` | `native`, `us`, `metric` or `metricwx` | `native` |
| `ECOWITT_MQTT` | yes/no | no |
| `ECOWITT_MQTT_HOST` | host name or IP address | `localhost` |
| `ECOWITT_MQTT_PORT` | port | `1883`, or `8883` with TLS |
| `ECOWITT_MQTT_TLS` | yes/no | no |
| `ECOWITT_MQTT_USERNAME`, `ECOWITT_MQTT_PASSWORD` | text | blank. The password is only used with a username. |
| `ECOWITT_MQTT_TOPIC` | text | `weewx/ecowitt` |
| `ECOWITT_MQTT_FORMAT` | `json`, `individual` or `both` | `json` |
| `ECOWITT_MQTT_UNITS` | `native`, `us`, `metric` or `metricwx` | `native` |
| `ECOWITT_MQTT_RETAIN` | yes/no | no |
| `ECOWITT_MQTT_CA_CERTS` | file path | blank (the system certificates). Only used with TLS. |
| `ECOWITT_NO_PROMPT` | yes/no | no |

- Yes/no values can be written `yes`/`no`, `y`/`n`, `true`/`false`, `on`/`off` or `1`/`0`, in any
  case. Choice values (`driver`, `tipping`, …) are not case sensitive either.
- The MQTT and `ecwLoop.json` variables are only used when `ECOWITT_MQTT` or `ECOWITT_LOOP_JSON` is
  yes (or those features are already turned on).
- `ECOWITT_RAIN_SOURCE` and `ECOWITT_LOOP_ON_INIT` only apply in driver mode.
- Quote values that contain spaces or shell characters: `ECOWITT_MQTT_PASSWORD='p@ss word!'`.

### Step 2: put the answers somewhere

For one or two settings, put them on the command line (Step 3). For more, a settings file is easier
to keep, and keeps passwords out of your shell history and out of a `Vagrantfile` you might share.
Write one `NAME=value` per line:

```bash
# ecowitt.env
ECOWITT_NO_PROMPT=yes
ECOWITT_IP_ADDRESS=192.168.1.100
ECOWITT_MODE=driver
ECOWITT_RAIN_SOURCE=piezo
ECOWITT_CATCHUP=device
ECOWITT_LOOP_JSON=yes
ECOWITT_LOOP_JSON_LOCATION=tmp
ECOWITT_MQTT=yes
ECOWITT_MQTT_HOST=192.168.1.20
ECOWITT_MQTT_USERNAME=weewx
ECOWITT_MQTT_PASSWORD='change-me'
```

If it holds a password, make it readable only by you: `chmod 600 ecowitt.env`.

### Step 3: run the install

**Debian / apt install (WeeWX runs as a service, `weectl` needs `sudo`)**

On the command line:

```bash
sudo ECOWITT_NO_PROMPT=yes ECOWITT_IP_ADDRESS=192.168.1.100 ECOWITT_MODE=driver \
     weectl extension install --yes weewx-ecowittGateway-2.0.0.zip
sudo systemctl restart weewx
```

With a settings file:

```bash
sudo bash -c 'set -a; . ./ecowitt.env; set +a; weectl extension install --yes weewx-ecowittGateway-2.0.0.zip'
sudo systemctl restart weewx
```

`set -a` exports every variable the file sets, so `weectl` can see them.

> **`sudo` and variables:** `sudo` starts a clean environment, so
> `export ECOWITT_IP_ADDRESS=…` followed by `sudo weectl …` **does not work**: the installer never
> sees the variable and quietly uses the defaults. Put the variables after `sudo` on the same line,
> or load them inside `sudo bash -c '…'`, as above. A script that already runs as root (a Vagrant
> shell provisioner, cloud-init, Ansible with `become`) can use `export` as normal.

**pip install (virtual environment, no `sudo`)**

Call the virtual environment's own `weectl`; you don't need to activate it first:

```bash
ECOWITT_NO_PROMPT=yes ECOWITT_IP_ADDRESS=192.168.1.100 ECOWITT_MODE=driver \
    ~/weewx-venv/bin/weectl extension install --yes weewx-ecowittGateway-2.0.0.zip

# or with a settings file
set -a; . ./ecowitt.env; set +a
~/weewx-venv/bin/weectl extension install --yes weewx-ecowittGateway-2.0.0.zip
```

Add `--config=/path/to/weewx.conf` if your station data isn't in `~/weewx-data`. Then restart WeeWX.

### Step 4: check the result

The install output lists every answer and where it came from:

```
Configuring weewx-ecowittGateway

Gateway IP address, eg 192.168.1.100: 192.168.1.100  (from ECOWITT_IP_ADDRESS)
    Found GW2000A_V3.1.4 at 192.168.1.100
Poll interval in seconds: 20  (default)
Use as the station driver, as a service alongside another driver, or skip: driver  (from ECOWITT_MODE)
    Paired rain gauges: piezo and tipping
Source of WeeWX 'rain': piezo  (from ECOWITT_RAIN_SOURCE)
Fetch missed data at startup from: device  (from ECOWITT_CATCHUP)
...
Station driver set to EcowittGateway (user.weewx-ecowittGateway); archive records generated in software.
Restart WeeWX to start using the new settings.
```

Then confirm it can reach the gateway, and look at the settings it wrote:

```bash
sudo weectl device --live-data                         # pip: ~/weewx-venv/bin/weectl device --live-data
sudo weectl device --active-map                        # the WeeWX fields your sensors fill
sudo sed -n '/^\[EcowittGateway\]/,/^\[[A-Za-z]/p' /etc/weewx/weewx.conf
```

In a script, test the exit status: `weectl` stops with a non-zero status if a variable is wrong (see
*Troubleshooting* below), so `set -e` or `|| exit 1` stops the build there.

### Complete examples

**Vagrant: a new Debian VM with WeeWX and the driver, from `vagrant up`.** Put the zip (and
`ecowitt.env`, if you use one) next to the `Vagrantfile`; Vagrant shares that folder as `/vagrant`.
The WeeWX lines follow the [WeeWX Debian guide](https://weewx.com/docs/5.1/quickstarts/debian/).
WeeWX's own package installs with its default answers (the Simulator driver), which the extension
then replaces.

```ruby
Vagrant.configure("2") do |config|
  config.vm.box = "debian/bookworm64"
  config.vm.network "public_network"          # the VM must be able to reach the gateway

  config.vm.provision "shell", inline: <<-SHELL
    set -e
    export DEBIAN_FRONTEND=noninteractive

    # WeeWX from weewx.com's apt repository
    apt-get update
    apt-get install -y wget gnupg
    wget -qO - https://weewx.com/keys.html | gpg --dearmor --yes --output /etc/apt/trusted.gpg.d/weewx.gpg
    echo "deb [arch=all] https://weewx.com/apt/python3 buster main" > /etc/apt/sources.list.d/weewx.list
    apt-get update
    apt-get install -y weewx

    # weewx-ecowittGateway, with no questions
    export ECOWITT_NO_PROMPT=yes
    export ECOWITT_IP_ADDRESS=192.168.1.100
    export ECOWITT_MODE=driver
    export ECOWITT_CATCHUP=device
    # or keep the answers in a file:  set -a; . /vagrant/ecowitt.env; set +a
    weectl extension install --yes /vagrant/weewx-ecowittGateway-2.0.0.zip

    systemctl restart weewx
  SHELL
end
```

**Ansible**

```yaml
- name: Install weewx-ecowittGateway
  become: true
  ansible.builtin.command:
    cmd: weectl extension install --yes /tmp/weewx-ecowittGateway-2.0.0.zip
  environment:
    ECOWITT_NO_PROMPT: "yes"
    ECOWITT_IP_ADDRESS: "192.168.1.100"
    ECOWITT_MODE: "driver"
    ECOWITT_MQTT: "yes"
    ECOWITT_MQTT_HOST: "192.168.1.20"
    ECOWITT_MQTT_USERNAME: "weewx"
    ECOWITT_MQTT_PASSWORD: "{{ vault_mqtt_password }}"
  notify: Restart weewx
```

**Upgrading unattended.** Run the same command with the new zip. Every setting you don't give a
variable for keeps its current value, so an upgrade normally needs no variables at all, only
`ECOWITT_NO_PROMPT=yes` if it's run at a terminal:

```bash
sudo ECOWITT_NO_PROMPT=yes weectl extension install --yes weewx-ecowittGateway-<new version>.zip
sudo systemctl restart weewx
```

To change one setting the same way, set only that variable, for example `ECOWITT_POLL_INTERVAL=30`.

**Replacing `ecowitt_http.py` unattended** works the same way: run the install over it, without
uninstalling it first. Its settings become the defaults.

### Settings the installer doesn't ask about

Options such as `log_next_update`, `debug`, `max_tries`, `url_timeout`, `api` or a
`[[field_map_extensions]]` entry aren't installer questions (see the README for each). Set them
after the install, before restarting WeeWX. WeeWX already includes the `configobj` module that reads
and writes `weewx.conf` and keeps its comments, so a few lines of Python do it:

```bash
sudo python3 - /etc/weewx/weewx.conf <<'EOF'
import sys, configobj
conf = configobj.ConfigObj(sys.argv[1], encoding='utf-8')
conf['EcowittGateway']['log_next_update'] = 'False'
conf['EcowittGateway']['max_tries'] = '5'
conf['debug'] = '1'
conf.write()
EOF
```

For a pip install, use `~/weewx-venv/bin/python3` and `~/weewx-data/weewx.conf` instead.

### Troubleshooting

| What you see | Why, and what to do |
|---|---|
| It still asks questions | It's running at a terminal. Set `ECOWITT_NO_PROMPT=yes`. If `weectl` asks to confirm the install, add `--yes`. |
| It finishes, but `ip_address = www.xxx.yyy.zzz` and the station driver hasn't changed | The installer didn't see your variables. Usually `export` followed by `sudo`: see the note in Step 3. Check for `(from ECOWITT_...)` in the output. |
| `install.SettingError: ECOWITT_MODE='drvier' is not one of: driver, service, skip` | A value isn't valid. Every variable is checked before anything is copied or changed. Correct it and run the install again. |
| `Ignoring unknown setting ECOWITT_IPADDRESS` | A misspelt variable name. It's ignored and the install carries on, so correct it and run the install again. |
| `No response from a device at …` then `Using this address anyway; paired sensors and rain gauges could not be checked.` | The gateway couldn't be reached during the install, for example because the VM isn't on its network yet. The address is saved, but the paired rain gauges and multi-channel sensors couldn't be read: set `ECOWITT_RAIN_SOURCE` yourself (the default is `tipping`), and once the gateway can be reached, `weectl device --sensor-map` lists the sensor IDs for `[[sensor_map]]`. |
| `install.SettingError: ECOWITT_CATCHUP='device' can't be used here: use one of net, none, either` | A GW1000, which has no SD card. `weewx.conf` hasn't been changed; run the install again with `net`, `none` or `either`. |
| `install.SettingError: ECOWITT_LOOP_JSON_PATH is needed for a custom ecwLoop.json location` | `ECOWITT_LOOP_JSON_LOCATION=custom` needs a path. Add `ECOWITT_LOOP_JSON_PATH`. |
| A GW1000 on a TCP port other than 45000 shows `No response` | The installer only checks port 45000. Install anyway (the address is kept), then set `tcp_port` in `[EcowittGateway]`. |

---

## Changing settings later

Run the installer again and change the answers, or edit `[EcowittGateway]` in `weewx.conf` directly.
See the README for every option. Restart WeeWX after either.

`weectl station reconfigure --driver=user.weewx-ecowittGateway` changes the rain source as part of
WeeWX's full station set-up. With `--no-prompt` it keeps the current settings.

### A second gateway

The installer sets up one gateway. To read two or more, install for the first one, then add a
`[[sources]]` section to `[EcowittGateway]` by hand, with a subsection for each gateway (see the
README's *Several gateways* section), and restart WeeWX. The installer's rain settings
(`[StdWXCalculate] [[Delta]]`) stay as they are. Then check them all with:

```bash
weectl device --sources
```

Running the installer again keeps `[[sources]]`; the `ip_address` it asks about is the one used
without `[[sources]]`.

---

## Manual install (without the installer)

1. Copy `bin/user/weewx-ecowittGateway.py` into your user directory (see the tables above).
2. Add this to `weewx.conf`, with `[EcowittGateway]` directly after `[Station]`. It takes WeeWX
   `rain` and `rainRate` from the tipping gauge; for the piezo gauge use `p_rainyear` and
   `piezoRain.0x0E.val`.
   ```ini
   loop_on_init = 1

   [Station]
       station_type = EcowittGateway

   [EcowittGateway]
       driver = user.weewx-ecowittGateway
       ip_address = 192.168.1.100
       [[field_map_extensions]]
           rainRate = rain.0x0E.val

   [StdArchive]
       record_generation = software

   [StdWXCalculate]
       [[Calculations]]
           rain = prefer_hardware
           p_rain = prefer_hardware
           lightning_strike_count = prefer_hardware
       [[Delta]]
           [[[rain]]]
               input = t_rainyear
           [[[p_rain]]]
               input = p_rainyear
           [[[lightning_strike_count]]]
               input = lightningcount
   ```
3. Copy the `[Accumulator]` entries from `install.py` (between `# Start Ecowitt local HTTP API
   driver extractors` and `# End …`) into `weewx.conf`, then restart WeeWX.

For service mode, leave `station_type` and the rain settings alone, and add
`user.weewx-ecowittGateway.EcowittHttpService` to `data_services` under `[Engine] [[Services]]`.

---

## Running the command-line tools

`weectl device …` covers most needs:

| Command | Shows |
|---|---|
| `weectl device --live-data` | the gateway's current readings |
| `weectl device --sensors` | the sensors, their IDs, signal and battery |
| `weectl device --sensor-map` | the multi-channel sensors and the channels they are reported on |
| `weectl device --active-map` | the field map entries your gateway is filling now: the WeeWX field each sensor reading goes to |
| `weectl device --firmware`, `--mac-address`, `--system-params` | gateway details |
| `weectl device --get-rain-data`, `--get-calibration`, `--get-services` … | gateway settings |
| `weectl device --dump-api --output=FILE` | saves every raw API response to FILE, as JSON (passwords and keys masked; `--unmask` to keep them), for a fault report or as test data |
| `weectl device --sd-files` | the SD card, its logging interval and the history files catchup reads |
| `weectl device --catchup-dry-run [--since=DATE] [--catchup-source=either\|device\|net]` | the records a catchup would save after DATE (default 24 hours ago), without saving them |

Add `--help` for every option, `--no-sensor-map` to see the gateway's own channels, and
`--api=tcp` to read a GW1000 when the address alone doesn't find it.

The other tools (`--discover`, `--test-driver`, `--test-service`, `--weewx-fields`, `--default-map`,
`--driver-map`, `--service-map`) need the module to be run directly:

**Debian:**
```bash
sudo PYTHONPATH=/usr/share/weewx:/etc/weewx/bin python3 /etc/weewx/bin/user/weewx-ecowittGateway.py \
     --config=/etc/weewx/weewx.conf --test-driver
```

**pip:**
```bash
source ~/weewx-venv/bin/activate
PYTHONPATH=~/weewx-data/bin python3 ~/weewx-data/bin/user/weewx-ecowittGateway.py \
     --config=$HOME/weewx-data/weewx.conf --test-driver
```

Stop `--test-driver` and `--test-service` with Ctrl-C. Add `--help` to see every option.

`--discover` listens for gateway broadcasts on UDP port 59387. The firewall must allow this, and the
gateway must be on the same network segment.

**Stop WeeWX before running `--test-driver` or `--test-service`**, so the gateway isn't being polled
twice.
