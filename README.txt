WEEWX-ECOWITTGATEWAY
====================

Version: 2.0.0 · WeeWX driver and service for Ecowitt gateways and consoles (local HTTP API, or the
TCP API for the GW1000)

weewx-ecowittGateway is built on Gary Roderick's Ecowitt local HTTP API driver (ecowitt_http.py
0.1.0a28, weewx-contrib/weewx-ecowitt_local_http
(https://github.com/weewx-contrib/weewx-ecowitt_local_http) at commit 31bdfe5). It adds a sensor
map, a choice of rain gauge for WeeWX's rain, ecwLoop.json and MQTT export, the GW1000 (TCP API), an
interactive and unattended installer, logging options, --dump-api, [[limits]], ignore_no_signal,
gateway_online, checked catchup and several gateways. See CHANGELOG.md, and DEVELOPMENT.md for the
tests and the layout of the code.

Compared with ecowitt_http.py the driver is called EcowittGateway, its section of weewx.conf is
[EcowittGateway] and its file is bin/user/weewx-ecowittGateway.py. In the rest of this README, "the
Ecowitt local HTTP driver" means this driver.

Note: General support issues for weewx-ecowittGateway should be raised in the Google Groups
[weewx-user forum](https://groups.google.com/g/weewx-user "Google Groups weewx-user forum"). The
Ecowitt local HTTP driver [Issues Page](https://github.com/gjr80/weewx-ecowitt_local_http/issues
"Ecowitt local HTTP driver Issues") should only be used for specific bugs in the Ecowitt local HTTP
driver code. It is recommended that even if an Ecowitt local HTTP driver bug is suspected, users
first post to the Google Groups [weewx-user forum](https://groups.google.com/g/weewx-user "Google
Groups weewx-user forum").

Description
===========

The Ecowitt local HTTP driver is a WeeWX driver that supports devices compatible with the Ecowitt
Local HTTP API, and the GW1000 through the Ecowitt TCP API.

The Ecowitt local HTTP driver utilises the Ecowitt Local HTTP API thus using a pull methodology for
obtaining data from the device rather than the push methodology. This has the advantage of giving
the user more control over when the data is obtained from the device.

The Ecowitt local HTTP driver can be operated as a traditional WeeWX driver where it is the source
of loop data, or it can be operated as a WeeWX service where it is used to augment loop data
produced by another driver.

Pre-Requisites
==============

The Ecowitt local HTTP driver requires:

- WeeWX v5.0.0 or greater
- Python v3.6 or later

Note: Whilst the Ecowitt local HTTP driver has been written to be compatible with Python v3.6 and
later, the driver utilises a number of features introduced in WeeWX v5.0.0 and hence cannot be
reliably run under WeeWX v4.x and Python v3. The driver extension installer will refuse to install
the driver under WeeWX v4 or earlier. The driver may be installed manually under WeeWX v4.x and
Python v3, and it may work; however, this configuration is not supported. The driver will not run
under Python v2.

Installation Instructions
=========================

INSTALL.md is the step-by-step guide for Debian/apt and pip installs: what the installer asks,
switching from ecowitt_http.py, upgrading, removing, and installing unattended from a script,
Vagrant or Ansible. The summary follows.

Installation as a WeeWX driver
------------------------------

1. If the Ecowitt local HTTP driver is to be installed on a fresh WeeWX installation first install
   WeeWX (http://weewx.com/docs/5.1/usersguide/installing/) and configure WeeWX to use the simulator
   driver.

2. Install the latest version of the Ecowitt local HTTP driver using the weectl utility
   (http://weewx.com/docs/5.1/utilities/weectl-extension/#install-an-extension).

    Note: The exact command syntax to invoke weectl on your system will depend on the installer used
    to install WeeWX. Refer to Installation methods
    (http://weewx.com/docs/5.1/usersguide/installing/#installation-methods) in the WeeWX User's
    Guide (http://weewx.com/docs/5.1/usersguide/introduction/).

    For WeeWX package installs:

        weectl extension install weewx-ecowittGateway-2.0.0.zip

    For WeeWX pip installs the Python virtual environment must be activated before the extension is
    installed:

        source ~/weewx-venv/bin/activate weectl extension install weewx-ecowittGateway-2.0.0.zip

    For WeeWX installs from git the Python virtual environment must be activated before the
    extension is installed:

        source ~/weewx-venv/bin/activate python3 ~/weewx/src/weectl.py extension install
        weewx-ecowittGateway-2.0.0.zip

    The installer asks for the device IP address (and checks it), whether to use the device as the
    station driver or as a service, which rain gauge WeeWX rain comes from, and the optional
    features (see Configuration). Press Enter to accept the value in [brackets]. Without a terminal,
    or with ECOWITT_* environment variables, it runs unattended: see Unattended install.

3. Test the Ecowitt local HTTP driver by running the driver file directly using the --test-driver
   command line option.

    For WeeWX package installs use:

        PYTHONPATH=/usr/share/weewx python3 /etc/weewx/bin/user/weewx-ecowittGateway.py
        --test-driver --ip-address=device_ip_address

    where device_ip_address is the IP address of the gateway device being used.

    For WeeWX pip installs the Python virtual environment must be activated before the driver is
    invoked:

        source ~/weewx-venv/bin/activate python3 ~/weewx-data/bin/user/weewx-ecowittGateway.py
        --test-driver --ip-address=device_ip_address

    where device_ip_address is the IP address of the gateway device being used.

    For WeeWX installs from git the Python virtual environment must be activated before the driver
    is invoked using the path to the local WeeWX git clone:

        source ~/weewx-venv/bin/activate PYTHONPATH=~/weewx/src python3
        ~/weewx-data/bin/user/weewx-ecowittGateway.py --test-driver --ip-address=device_ip_address

    where device_ip_address is the IP address of the gateway device being used.

    You should observe loop packets being emitted on a regular basis. Once finished press ctrl-c to
    exit.

    Note: You will only see loop packets and not archive records when running the driver directly.
    This is because you are seeing output not from WeeWX, but rather directly from the driver.

4. If you chose driver when installing, this step is not needed. Otherwise select and configure the
   driver (this is also how to change the rain source later):

    For WeeWX package installs use:

        weectl station reconfigure --driver=user.weewx-ecowittGateway

    For WeeWX pip installs the Python virtual environment must be activated before weectl is used to
    select and configure the driver:

        source ~/weewx-venv/bin/activate weectl station reconfigure
        --driver=user.weewx-ecowittGateway

    For WeeWX installs from git the Python virtual environment must be activated before weectl.py is
    used to select and configure the driver:

        source ~/weewx-venv/bin/activate python3 ~/weewx/src/weectl.py station reconfigure
        --driver=user.weewx-ecowittGateway

5. You may choose to run WeeWX directly
   (http://weewx.com/docs/5.1/usersguide/running/#running-directly) to observe the loop packets and
   archive records being generated by WeeWX. If WeeWX is already running stop WeeWX before running
   the driver directly.

6. Once satisfied that the Ecowitt local HTTP driver is operating correctly you can restart the
   WeeWX daemon:

        sudo /etc/init.d/weewx restart

    or

        sudo service weewx restart

    or

        sudo systemctl restart weewx

7. You may wish to refer to the wiki for Gary Roderick's ecowitt_http.py
   (https://github.com/gjr80/weewx-ecowitt_local_http/wiki) (written for ecowitt_http.py: use the
   names above) for further guidance on customising the operation of the Ecowitt local HTTP driver
   and integrating device data into WeeWX generated reports.

Installation as a WeeWX service
-------------------------------

1. Install WeeWX (http://weewx.com/docs/5.1/usersguide/installing/) and configure it to use either
   the simulator driver or another driver of your choice.

2. Install the Ecowitt local HTTP driver extension using the weectl utility as per Installation as a
   WeeWX driver step&nbsp;2 above.

3. If you chose service when installing, this step is not needed. Otherwise edit weewx.conf and
   under the [Engine] [[Services]] stanza add an entry user.weewx-ecowittGateway.EcowittHttpService
   to the data_services option. It should look something like:

       [Engine]

        [[Services]] .... data_services = user.weewx-ecowittGateway.EcowittHttpService

4. Test the Ecowitt local HTTP service by running the driver file directly using the --test-service
   command line option.

   For WeeWX package installs use:

        PYTHONPATH=/usr/share/weewx:/etc/weewx/bin python3
        /etc/weewx/bin/user/weewx-ecowittGateway.py --test-service --ip-address=device_ip_address

   where device_ip_address is the IP address of the device being used.

   For WeeWX pip installs the Python virtual environment must be activated before the driver is
   invoked:

        source ~/weewx-venv/bin/activate PYTHONPATH=~/weewx-data/bin python3
        ~/weewx-data/bin/user/weewx-ecowittGateway.py --test-service --ip-address=device_ip_address

   where device_ip_address is the IP address of the device being used.

   For WeeWX installs from git the Python virtual environment must be activated before the driver is
   invoked using the path to the local WeeWX git clone:

        source ~/weewx-venv/bin/activate PYTHONPATH=~/weewx/src:~/weewx-data/bin python3
        ~/weewx-data/bin/user/weewx-ecowittGateway.py --test-service --ip-address=device_ip_address

   where device_ip_address is the IP address of the gateway device being used.

   You should observe loop packets being emitted on a regular basis.

   Note: When the Ecowitt local HTTP driver is run directly with the --test-service command line
   option a series of simulated loop packets are emitted every 10 seconds to simulate a running
   WeeWX instance. The gateway device is polled and the gateway device data added to the loop
   packets when available. As the default gateway device poll interval is 20 seconds not all loop
   packets will be augmented with gateway device data.

   Note: You will only see loop packets and not archive records when running the service directly.
   This is because you are seeing the direct output of the simulated loop packets and the Ecowitt
   local HTTP service and not WeeWX.

   Once finished press ctrl-c to exit.

5. You may choose to run WeeWX directly
   (http://weewx.com/docs/5.1/usersguide/running/#running-directly) to observe the loop packets and
   archive records being generated by WeeWX. Note that depending on the frequency of the loop
   packets emitted by the in-use driver and the polling interval of the Ecowitt local HTTP service,
   not all loop packets may include device data; however, provided the device polling interval is
   less than the frequency of the loop packets emitted by the in-use driver each archive record
   should contain device data.

6. Once satisfied that the Ecowitt local HTTP service is operating correctly you can restart the
   WeeWX daemon:

        sudo /etc/init.d/weewx restart

    or

        sudo service weewx restart

    or

        sudo systemctl restart weewx

7. You may wish to refer to the wiki for Gary Roderick's ecowitt_http.py
   (https://github.com/gjr80/weewx-ecowitt_local_http/wiki) (written for ecowitt_http.py: use the
   names above) for further guidance on customising the operation of the Ecowitt local HTTP driver
   and integrating device data into WeeWX generated reports.

Configuration
=============

The settings are in the [EcowittGateway] section of weewx.conf. The installer writes them from your
answers; edit weewx.conf and restart WeeWX to change them later.

Rain source
-----------

The driver reports each gauge's running totals (t_rainyear for a tipping gauge, p_rainyear for a
piezo gauge) and WeeWX works out the rain in each period from them ([StdWXCalculate] [[Delta]]).
WeeWX rain and rainRate come from the gauge you choose (tipping, piezo or none); the other gauge, if
paired, is recorded in t_rain/t_rainrate or p_rain/p_rainrate. To change it, run weectl station
reconfigure --driver=user.weewx-ecowittGateway.

If [StdWXCalculate] [[Delta]] [[[rain]]] names a total the driver doesn't produce (WeeWX's own
default, when there is no such rule, is totalRain), rain stays empty, and the driver logs a warning
when WeeWX starts.

Sensor map
----------

Multi-channel sensors (WN31, WN34, WN35, WH41, WH51, WH54, WH55) can be fixed to a channel by sensor
ID, so a sensor re-paired onto another channel (eg after a battery change) keeps its WeeWX fields:

    [EcowittGateway] [[sensor_map]] C0FFEE = 3

A sensor already on that channel is moved to the channel freed. weectl device --sensor-map lists the
paired sensors, their IDs and the channels they are reported on.

ecwLoop.json and MQTT
---------------------

Each loop packet, after WeeWX has added the fields it calculates (rain, dewpoint, ...), can be
written to a JSON file and/or published to an MQTT broker (needs the paho-mqtt package):

    [EcowittGateway] [[loop_json]] enable = True path = ecwLoop.json      # relative to the web
    pages folder (HTML_ROOT) units = native           # native, us, metric or metricwx [[mqtt]]
    enable = True host = localhost port = 1883 topic = weewx/ecowitt format = json            # json
    (<topic>/loop), individual (<topic>/<field>) or both units = native

The EcowittGatewayExport service in restful_services does this in both driver and service mode.

GW1000
------

The GW1000 has no local HTTP API; it is read through the Ecowitt TCP API (port 45000). This is
automatic (api = auto); set api = tcp or api = http in [EcowittGateway] to choose, and tcp_port if
needed. The GW1000 has no SD card, so missed data can only come from Ecowitt.net.

Catchup
-------

When WeeWX starts it can fetch missed data from the device's SD card or from Ecowitt.net:

    [EcowittGateway] [[catchup]] source = either          # either, device, net or none api_key =
    ...            # Ecowitt.net API key, for net app_key = ...            # Ecowitt.net application
    key, for net

either tries the SD card first, then Ecowitt.net. The SD card's history files are read from port 81
of the device (file_port in [[catchup]] changes that, for a device behind port forwarding).
Ecowitt.net keeps 5-minute records for 90 days. Catchup records get the same [[sensor_map]] and
[[limits]] as live data. The sensor map uses the sensors paired now, so records from before a sensor
was moved to another channel follow it to where it is now.

After a catchup the log says how many records came from where and the time they cover. If their
interval (the SD card's logging interval, or 5 minutes from Ecowitt.net) differs from WeeWX's
archive_interval, a warning says so: the records are saved with their own interval, so set the SD
card's interval (WS View Plus app) to match.

To see what a catchup would fetch, without saving anything:

    weectl device --sd-files                       # the SD card, its interval and its files weectl
    device --catchup-dry-run --since="2026-11-05 14:00" [--catchup-source=device|net|either]

--since defaults to 24 hours ago.

Limits
------

[[limits]] drops readings outside a minimum and maximum (they become None), in loop packets, catchup
records and the data the service adds, for a sensor that sometimes reports nonsense:

    [EcowittGateway] [[limits]] outTemp = -50, 60                  # in the gateway's units:
    degree_C windGust = , 75                    # no minimum outHumidity = 1, 100 extraTemp1 = -40,
    140, degree_F    # with a unit

Without a unit the limits are in the gateway's units (METRICWX: degree_C, meter_per_second, hPa, mm,
mm_per_hour ...), whatever units your database uses. The first reading dropped for each field is
logged; with debug = 1, every one. Fields WeeWX calculates later (rain, dewpoint ...) are not
checked.

Sensors with no signal
----------------------

With ignore_no_signal = True in [EcowittGateway], the readings of a multi-channel sensor (WN31,
WN34, WN35, WH41, WH51, WH54, WH55) are left out while the gateway reports its signal as 0, until it
is heard again. Its signal, battery and ID are still reported. The log says when a sensor starts and
stops being ignored. The default is False.

gateway_online
--------------

Each loop packet has a gateway_online field: 1 while the gateway is answering. In service mode it is
0 when the gateway hasn't answered within max_age seconds (default 60). (In driver mode WeeWX gets
no loop packets at all while the gateway is unreachable.) To archive it, add the column; its average
over an archive interval is then the fraction of the time the gateway was online:

    weectl database add-column gateway_online --type=REAL

Radiation
---------

Ecowitt's light sensors measure illuminance, which the driver records in illuminance (lux). With

    [EcowittGateway] radiation_from_illuminance = True

it also records WeeWX's radiation (W/m²), worked out with Ecowitt's own factor (126.7 lux per W/m²,
the same conversion the gateway uses when set to show W/m²). Standard skins show radiation, and
WeeWX needs it to work out evapotranspiration (ET). It is an approximation: the sensor is not a
pyranometer. It applies to loop packets and catchup records, and [[limits]] can check it. It is off
if radiation is mapped to a gateway field in your field map settings.

Fields, the database and [Accumulator]
--------------------------------------

Storing fields in the database. Every mapped field is in each loop packet (and in ecwLoop.json and
MQTT), but WeeWX only archives, and so only shows in reports and graphs, the fields the database has
a column for. The standard database (wview_extended) has columns for the common ones, such as
outTemp, inTemp, pressure, rainRate, illuminance, UV, pm2_5, pm10_0, co2, extraTemp1–8, soilMoist1–4
and leafWet1–2 (rain, barometer and dewpoint are calculated by WeeWX). The Ecowitt-specific fields
(t_rainday, wh40_batt, pm2_52, gateway_online_garden …) have no column there.

The additions database. With [[additions]] enable = True (the installer asks, default yes), the
EcowittAdditions service archives the fields of each archive record, live and catchup, that the
station's database has no column for in a database of their own, ecowitt_additions.sdb (next to
weewx.sdb). It needs no setting up: a column, with its daily summary, is added the first time a
field has a value, so a sensor paired later gets its columns then, and sensors that aren't paired
add none. Only numbers are stored. Skins use the fields through its data binding, ecowitt_binding:

    $current($data_binding='ecowitt_binding').t_rainday
    $day($data_binding='ecowitt_binding').wh40_batt.min
    $week($data_binding='ecowitt_binding').pm2_52.avg

The database is set in [DataBindings] [[ecowitt_binding]] and [Databases] [[ecowitt_sqlite]]
(SQLite; point the binding at a MySQL database instead if you use one). Uninstalling leaves the
file.

To store a field in the station's own database instead, add a column there (stop WeeWX first); from
then on the field is archived there, not in the additions database:

    weectl database add-column wh40_batt --type=REAL

weectl device --active-map lists the fields your sensors fill.

Battery and signal fields are named after the sensor, with the channel for multi-channel sensors:
wh40_batt, ws90_sig, wn31_ch2_batt, wh51_ch3_sig, and _volt for a battery voltage (wh51_ch3_volt,
ws90_volt). The WS69 sensor array is wh69 (the name the gateway uses).

PM2.5 and air-quality sensors:

WH41/WH43 (PM2.5), channel 1
    pm2_5, pm25_ch1_real (AQI), pm25_ch1_24h (24-hour AQI), wh41_ch1_batt, wh41_ch1_sig

WH41/WH43, channel n = 2–4
    pm2_5n, pm25_chn_real, pm25_chn_24h, wh41_chn_batt, wh41_chn_sig

WH45/WH46 (CO2 and particulates)
    pm2_55, pm10_0, co2, wh45_batt, wh45_sig

Of these, pm2_5, pm10_0 and co2 have columns in the standard database. Without a WH41/WH43 on
channel 1, pm2_5 is filled from the WH45/WH46, so one PM2.5 sensor shows up in the standard skins
either way (not if pm2_5 or pm2_55 has been mapped to something else). To use the WH45/WH46 for
pm2_5 with a WH41/WH43 on channel 1 too:

    [EcowittGateway] [[field_map_extensions]] pm2_5 = co2.PM25             # the WH45/WH46; pm2_55
    is then empty pm2_51 = ch_pm25.1.PM25      # channel 1, in a field of its own (add the column)

The [Accumulator] section. The installer adds an [Accumulator] entry for each field that shouldn't
be averaged over an archive interval: counts (t_rain, p_rain, lightning_strike_count) are totalled,
and battery and signal states, running rain totals, the lightning count, distance and time,
daymaxwind, windDir10 and 24-hour AQIs keep their last value. Entries for fields you don't have do
no harm. Installing as the station driver, or weectl station reconfigure, removes the entries that
ecowitt_http.py or an earlier weewx-EcowittGateway extension added for names this driver never
produces (only those still as they were added, and not ones named in your field map settings), and
changes a lightningcount entry of sum to last: here lightningcount is the gateway's running count.
As a service nothing is removed, since the station's own driver may use those names.

Several gateways
----------------

The driver can read two or more gateways at once, for example a GW2000 in the house and a WS3900
console in the garden, and merge them into one stream of loop packets. If one gateway stops
answering, the others carry on. The installer asks for other gateways after the first one and writes
[[sources]]; you can also set it up or change it by hand:

    [EcowittGateway] driver = user.weewx-ecowittGateway poll_interval = 20              # settings
    here apply to every gateway max_age = 60                    # how old a gateway's data may be
    and still be used (seconds) source_priority = house, garden # which gateway wins; default: the
    order below

        [[sources]] [[[house]]] ip_address = 192.168.1.100 [[[[sensor_map]]]]          # sensor IDs
        belong to one gateway C0FFEE = 2 [[[garden]]] ip_address = 192.168.1.101 poll_interval = 30
        # any setting can be changed for one gateway [[[[catchup]]]] source = none

        [[priority]]                        # per field; overrides source_priority outTemp = garden,
        house soil* = garden                  # a prefix ending in * covers soilMoist1, soilTemp2
        ...

With no [[sources]] section (or only one gateway in it) the driver uses ip_address as before, and
nothing below applies. A gateway can be left out with enabled = False.

How the data is merged. Each gateway is polled by its own thread, so a gateway that doesn't answer
never holds up the others. Each field comes from the first gateway in its priority order whose
latest data is no older than max_age and has a value for the field. Readings dropped by [[limits]]
or ignore_no_signal count as no value, so the next gateway's reading is used instead. Every gateway
inherits the settings of [EcowittGateway] (subsections such as [[limits]] and [[field_map]] are
merged with its own) and can override any of them, except [[sensor_map]], which has to be given for
each gateway. [[loop_json]] and [[mqtt]] export the merged packets.

Rain and lightning. WeeWX works out rain from a gauge's running total (t_rainyear, p_rainyear) and
lightning strikes from lightningcount (see Rain source). Two gateways that hear the same gauge each
keep their own total, and the totals differ (a gateway paired later, or set by hand, has a lower
year total), so taking the total from one gateway and then another would look to WeeWX like a
downpour or a counter reset. With several gateways these totals are merged: the merged total starts
at the first gateway's total and rises by the rise of one gateway's total at a time, the first in
the field's priority order. If that gateway goes quiet for max_age seconds, the next one takes over
without counting any period twice; the rain it reported while waiting is added when it takes over.
WeeWX's [StdWXCalculate] [[Delta]] settings stay as the installer wrote them. So long as the first
gateway counts, the merged total is that gateway's own total. It follows that gateway's resets (the
new year for rain, midnight for lightning). Every [[Delta]] input is merged this way, as are
t_rainyear, p_rainyear and lightningcount.

When every gateway is out of contact. WeeWX only fills missed archive records at start-up (catchup).
If every gateway is silent for longer than archive_interval, the driver stops sending loop packets
until they're all back (or max_gap_wait has passed). It then makes WeeWX restart the driver, so
catchup fills the outage from the gateways' SD cards or Ecowitt.net. No loop packets are sent while
waiting, because the first archive record written after the outage would stop catchup from going
back any further. An outage of just one gateway can't be filled afterwards: the others keep
producing archive records.

gap_mode
    Default: restart
    restart: restart for catchup after an outage of every gateway. none: don't.

max_gap_wait
    Default: 3600
    Seconds to wait for every gateway to come back before restarting anyway.

min_restart_interval
    Default: 900
    Don't restart again within this many seconds of the last restart.

Catchup. At start-up each gateway's history ([[catchup]], which each gateway can override) is
combined into archive_interval records: averages for most fields, the maximum for gusts and rain
rates, vector averages for wind directions and the latest value for totals. The records of all the
gateways are then merged by the same priorities as loop packets, and the totals are merged as above,
so the rain WeeWX works out is counted once and the loop packets carry on from the last record. If
the database is empty, there's no catchup.

Fields. gateway_online is 1 while any gateway is answering; gateway_online_<name> is 1 while that
gateway has answered within max_age seconds, otherwise 0.

Commands.

    weectl device --sources                        # the gateways, in priority order, and whether
    they answer weectl device --live-data --source=garden      # any other command, for one gateway
    weectl device --catchup-dry-run --since=2026-11-06   # the merged catchup records of all the
    gateways

Without --source, commands about one gateway use the first one. --catchup-dry-run (and, run
directly, --test-driver, --test-service and --weewx-fields) use all of them unless --source is
given. Add merge to debug to log which gateway each field came from.

Service mode. The service merges several gateways in the same way, then adds the fields the main
driver hasn't set.

Moving from weewx-ecowitt-multi. The multi-gateway merge follows the design of weewx-ecowitt-multi
(https://github.com/mKainzbauer/weewx-ecowitt-multi) by mKainzbauer. Its settings mostly carry over:

driver = user.ecowitt_multi, station_type = EcowittMulti
    driver = user.weewx-ecowittGateway, station_type = EcowittGateway

host, port
    ip_address (host and port are also accepted)

history = auto | sd | cloud | none
    [[[[catchup]]]] source = either | device | net | none (history is also accepted)

type = http
    (always the HTTP API, or the TCP API for a GW1000)

source_priority, [[priority]] with prefix*
    the same

cache_max_age
    max_age (default 60 seconds rather than archive_interval)

gap_mode, max_gap_wait, min_restart_interval
    the same

diagnostic_fields
    gateway_online_<name> is always included

Not carried over: MQTT sources (type = mqtt), the sensor catalog and the Ecowitt database schema,
poll_mode = auto (every gateway is polled all the time) and the default plausibility limits for
every unit group ([[limits]] sets limits per field instead).

Unattended install
------------------

Every installer question can be answered with an ECOWITT_* environment variable, so an install can
be scripted (for example in a Vagrant or Ansible provisioner):

    sudo ECOWITT_NO_PROMPT=yes ECOWITT_IP_ADDRESS=192.168.1.100 ECOWITT_MODE=driver \ weectl
    extension install --yes weewx-ecowittGateway-2.0.0.zip

Questions without a variable take their default (the existing setting where there is one), and the
installer prints each answer and where it came from. A variable with a value the installer cannot
use stops it before anything is changed. ECOWITT_NO_PROMPT=yes stops it asking anything, even at a
terminal. INSTALL.md, section C (INSTALL.md#c-unattended-scripted-install) lists every variable with
its default, and has Vagrant and Ansible examples and a troubleshooting table.

Logging
-------

With debug = 1 in weewx.conf the driver logs its settings at startup, the size of the field map,
and, after the first poll, the active field map: the field map entries your gateway is filling (the
sensors it has found), not the whole map of over 200 entries. Fields that appear later (a sensor
added, or back in range) are logged when they appear. weectl device --active-map shows the same list
at any time.

log_next_update
    Default: True
    With debug set, log Next update in N seconds after every poll. Set it to False to keep debug
    logging without these lines.

debug
    Default: none
    Extra logging for particular areas, any of rain, wind, loop, sensors, parser, catchup,
    collector, merge (several gateways: where each field came from), eg debug = rain, wind. loop
    logs every packet.

The whole field map is logged with debug = 2. Long log lines (the field map, and packets with debug
= loop) are split over numbered lines of about 3,000 characters, marked (part 1 of 3) and so on,
because syslog daemons cut longer messages off (rsyslog's default limit is 8 kB).

Upgrade Instructions
====================

From Gary Roderick's ecowitt_http.py
------------------------------------

Install weewx-ecowittGateway as above, without uninstalling ecowitt_http.py first: uninstalling it
deletes its [EcowittHttp] section of weewx.conf and the [StdWXCalculate] and [Accumulator] entries
it added, which this driver uses too. The installer:

- moves [EcowittHttp], with your settings, to [EcowittGateway];
- changes station_type and the service lists to the new names;
- removes ecowitt_http.py and its extension record (Ecowitt_HTTP in weectl extension list).

Your rain configuration is kept: the installer offers the gauge rain already comes from.

The driver also reads [EcowittHttp] if there is no [EcowittGateway] section.

From an earlier weewx-EcowittGateway extension
----------------------------------------------

Install weewx-ecowittGateway over it as above, without uninstalling it first (uninstalling deletes
the [EcowittGateway] section). The installer:

- points driver and the service lists at user.weewx-ecowittGateway and removes the old file
  (bin/user/weewx-EcowittGateway.py) and extension record, so weectl extension list shows only
  weewx-ecowittGateway;
- keeps your settings: the Ecowitt.net API and application keys move into [[catchup]], and WeeWX's
  rule for rain is set from rain_source (both means tipping: the piezo gauge goes to p_rain);
- removes the [Accumulator] entries for fields this driver doesn't produce (see [Fields, the
  database and [Accumulator]](#fields-the-database-and-accumulator));
- offers to record radiation (see Radiation), with y as the default.

The field names this driver records may differ from the columns your database has: weectl device
--active-map lists them. To keep a column filling, map the gateway field to it in
[[field_map_extensions]] (for example batteryStatus1 = wn31.ch1.battery; the field's default name is
then empty). To keep a column's history under a new name, stop WeeWX, back up the database and
rename the column: weectl database rename-column OLD_NAME NEW_NAME. Check the log after restarting.

Later 2.x versions
------------------

Install the new version as per the Installation Instructions above.

Support
=======

General support issues for the Ecowitt local HTTP driver should be raised in the Google Groups
[weewx-user forum](https://groups.google.com/g/weewx-user "Google Groups weewx-user forum"). The
Ecowitt local HTTP driver [Issues Page](https://github.com/gjr80/weewx-ecowitt_local_http/issues
"Ecowitt local HTTP driver Issues") should only be used for specific bugs in the Ecowitt local HTTP
driver code. It is recommended that even if an Ecowitt local HTTP driver bug is suspected users
first post to the Google Groups [weewx-user forum](https://groups.google.com/g/weewx-user "Google
Groups weewx-user forum").

Licensing
=========

weewx-ecowittGateway is licensed under the GNU Public License v3 (LICENSE).

- Copyright (C) 2024-25 Gary Roderick: the Ecowitt local HTTP API driver (ecowitt_http.py), on which
  2.0.0 is built.
- Copyright (C) 2026 Ian Millard: weewx-ecowittGateway 2.0.0 and later.
- Copyright (C) 2026 mKainzbauer: weewx-ecowitt-multi
  (https://github.com/mKainzbauer/weewx-ecowitt-multi), whose design the multi-gateway merge
  (priorities, counting rain across gateways, restarting for catchup after an outage, merged
  catchup) follows.
