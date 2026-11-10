"""The extension installer, run as 'weectl extension install' would run it, with no terminal."""

import importlib.util
import io
import os
import types

import configobj
import pytest

from conftest import ROOT

spec = importlib.util.spec_from_file_location('ecowitt_install', os.path.join(ROOT, 'install.py'))
install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)

WEEWX_CONF = """
WEEWX_ROOT = /tmp/weewx-test
debug = 0
[Station]
    station_type = Simulator
[Simulator]
    driver = weewx.drivers.simulator
[StdReport]
    HTML_ROOT = public_html
[StdArchive]
    record_generation = hardware
[Engine]
    [[Services]]
        data_services = ,
"""


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in list(os.environ):
        if var.startswith('ECOWITT_'):
            monkeypatch.delenv(var)
    monkeypatch.setattr('sys.stdin', io.StringIO())     # no terminal


def run(config=WEEWX_CONF):
    engine = types.SimpleNamespace(config_dict=configobj.ConfigObj(io.StringIO(config), encoding='utf-8'),
                                   printer=types.SimpleNamespace(out=lambda msg='', level=0: None), dry_run=False)
    installer = install.EcowittGatewayInstaller()
    installer.configure(engine)
    return engine.config_dict


def test_no_terminal_no_variables_changes_nothing_important():
    conf = run()
    assert conf['Station']['station_type'] == 'Simulator'
    assert conf['EcowittGateway']['ip_address'] == 'replace_me'
    assert conf.sections.index('EcowittGateway') == conf.sections.index('Station') + 1


def test_variables_make_a_driver_install(monkeypatch):
    for name, value in {'IP_ADDRESS': '192.0.2.1:1', 'MODE': 'Driver', 'RAIN_SOURCE': 'both', 'CATCHUP': 'none',
                        'POLL_INTERVAL': '30', 'LOOP_JSON_PATH': '/tmp/x', 'MQTT': 'yes', 'MQTT_HOST': 'broker',
                        'MQTT_USERNAME': 'u', 'MQTT_PASSWORD': 'p w'}.items():
        monkeypatch.setenv(f'ECOWITT_{name}', value)
    monkeypatch.setenv('ECOWITT_LOOP_JSON', 'on')
    monkeypatch.setattr(install.EcowittGatewayInstaller, '_probe', lambda self, ip: (None, None, None))
    conf = run()
    section = conf['EcowittGateway']
    assert conf['Station']['station_type'] == 'EcowittGateway'
    assert conf['StdArchive']['record_generation'] == 'software'
    assert section['ip_address'] == '192.0.2.1:1' and section['poll_interval'] == '30'
    assert section['rain_source'] == 'both' and section['catchup']['source'] == 'none'
    assert section['loop_json'] == {'enable': 'True', 'path': '/tmp/x', 'units': 'native'}
    assert section['mqtt']['host'] == 'broker' and section['mqtt']['password'] == 'p w'


@pytest.mark.parametrize('name,value,message', [
    ('MODE', 'drvier', 'is not one of'), ('POLL_INTERVAL', '0', 'whole number'),
    ('MQTT', 'maybe', 'yes or no'), ('IP_ADDRESS', '', 'is empty')])
def test_bad_values_stop_before_any_change(monkeypatch, name, value, message):
    monkeypatch.setenv(f'ECOWITT_{name}', value)
    with pytest.raises(install.SettingError, match=message):
        run()


def test_unknown_variable_is_a_warning():
    os.environ['ECOWITT_IPADDRESS'] = 'x'
    try:
        assert install.check_env() == ['Ignoring unknown setting ECOWITT_IPADDRESS']
    finally:
        del os.environ['ECOWITT_IPADDRESS']


def test_service_mode(monkeypatch):
    monkeypatch.setenv('ECOWITT_IP_ADDRESS', '192.0.2.1:1')
    monkeypatch.setenv('ECOWITT_MODE', 'service')
    monkeypatch.setattr(install.EcowittGatewayInstaller, '_probe', lambda self, ip: (None, None, None))
    conf = run()
    assert conf['Station']['station_type'] == 'Simulator'
    assert install.SERVICE in conf['Engine']['Services']['data_services']


def test_legacy_section_is_moved(monkeypatch):
    conf = run(WEEWX_CONF.replace('station_type = Simulator', 'station_type = EcowittHttp')
               + '[EcowittHttp]\n    ip_address = 192.0.2.5\n    poll_interval = 45\n')
    assert 'EcowittHttp' not in conf
    assert conf['Station']['station_type'] == 'EcowittGateway'
    assert conf['EcowittGateway']['ip_address'] == '192.0.2.5' and conf['EcowittGateway']['poll_interval'] == '45'


def test_extractors_match_the_driver():
    from conftest import egw_module
    assert {x: tuple(f) for x, f in install.EXTRACTORS.items()} == egw_module._EXTRACTORS
    assert tuple(install.FIRSTLAST) == egw_module._FIRSTLAST
    assert {x: tuple(f) for x, f in install.STALE_EXTRACTORS.items()} == egw_module._STALE_EXTRACTORS


def test_every_accumulator_entry_is_a_field_the_driver_produces():
    """1.3.2 and earlier had 72 entries for names from older drivers (pm2_51_24hav, wh31_ch1_batt ...)."""
    from conftest import egw_module as egw
    produced = set()
    for options in ({}, {'wn32_indoor': True, 'wn32_outdoor': True}):
        produced |= set(egw.HttpMapper(**options).field_map)
    produced |= {'t_rain', 'p_rain', 'hail', 'lightning_num', 'wbgt', 'ws85_batt', 'ws90_batt', 'ws85cap_volt',
                 'ws90cap_volt', 'gateway_online', *(f'soilMoistBatt{n}' for n in range(1, 17))}
    entries = {f for fields in install.EXTRACTORS.values() for f in fields} | set(install.FIRSTLAST)
    assert sorted(entries - produced) == []
    assert 'lightningcount' in install.EXTRACTORS['sum']           # a per-packet count of strikes
    for field in ('pm25_avg_24h_ch1', 'pm25_AQI_24h_ch1', 'batteryStatus1', 'pm25_Batt1'):
        assert field in install.EXTRACTORS['last']


def test_upgrade_tidies_the_accumulator():
    conf = run(WEEWX_CONF + """
[Accumulator]
    [[pm2_51_24hav]]
        extractor = last
    [[wh31_ch1_batt]]
        extractor = last
    [[lightning_noise_count]]
        extractor = sum
    [[model]]
        accumulator = firstlast
        extractor = last
    [[lightningcount]]
        extractor = last
    [[stormRain]]
        extractor = max
    [[myOwnField]]
        extractor = last
""")
    accum = conf['Accumulator']
    for gone in ('pm2_51_24hav', 'wh31_ch1_batt', 'lightning_noise_count', 'model'):
        assert gone not in accum
    assert accum['lightningcount']['extractor'] == 'sum'
    assert accum['stormRain']['extractor'] == 'max'               # changed by the user: kept
    assert accum['myOwnField']['extractor'] == 'last'


def test_reconfigure_without_prompts_keeps_the_settings(monkeypatch, gateway):
    """'weectl station reconfigure --no-prompt' calls modify_config() without prompt_for_settings();
    1.3.0 and earlier still asked about loop_on_init and the rain gauges, and failed with no terminal."""
    from conftest import egw_module
    monkeypatch.setattr(egw_module.EcowittHttpDriverConfEditor, 'prompting', False)
    monkeypatch.setattr('weecfg.prompt_with_options',
                        lambda *a, **k: pytest.fail('asked a question with prompting off'))
    conf = configobj.ConfigObj(io.StringIO("loop_on_init = 0\n" + WEEWX_CONF + f"""
[EcowittGateway]
    driver = user.weewx-EcowittGateway
    ip_address = {gateway.address}
    api = http
    rain_source = piezo
"""), encoding='utf-8')
    egw_module.EcowittHttpDriverConfEditor().modify_config(conf)
    assert conf['loop_on_init'] == '0'
    assert conf['EcowittGateway']['rain_source'] == 'piezo'
    assert conf['StdWXCalculate']['Calculations']['rain'] == 'prefer_hardware'
