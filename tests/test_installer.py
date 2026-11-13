"""The extension installer, run as 'weectl extension install' runs it, with no terminal."""

import functools
import importlib.util
import io
import os
import types

import configobj
import pytest

from conftest import ROOT, egw_module

# as in weectl: weecfg.extension makes 'from setup import ExtensionInstaller' work
importlib.import_module('weecfg.extension')
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


def env(monkeypatch, **settings):
    for name, value in settings.items():
        monkeypatch.setenv(f'ECOWITT_{name}', value)


def run(config=WEEWX_CONF, dry_run=False, tmp_path=None):
    """Run the installer's configure() on a weewx.conf; returns (config_dict, output lines)."""
    lines = []
    roots = {'USER_DIR': os.path.join(ROOT, 'bin', 'user')}
    if tmp_path is not None:
        roots['EXT_DIR'] = str(tmp_path / 'extensions')
    engine = types.SimpleNamespace(config_dict=configobj.ConfigObj(io.StringIO(config), encoding='utf-8'),
                                   printer=types.SimpleNamespace(out=lambda msg='', level=0: lines.append(msg)),
                                   dry_run=dry_run, root_dict=roots)
    installer = install.EcowittHttpInstaller()
    installer.configure(engine)
    return engine.config_dict, lines


def test_no_terminal_and_no_variables_changes_nothing_important():
    conf, _ = run()
    assert conf['Station']['station_type'] == 'Simulator'
    assert conf['EcowittGateway']['ip_address'] == 'www.xxx.yyy.zzz'
    assert conf.sections.index('EcowittGateway') == conf.sections.index('Station') + 1


def test_variables_make_a_driver_install(monkeypatch, gateway):
    env(monkeypatch, IP_ADDRESS=gateway.address, MODE='Driver', RAIN_SOURCE='piezo', CATCHUP='none',
        POLL_INTERVAL='30', LOOP_JSON='on', LOOP_JSON_PATH='/tmp/x', MQTT='yes', MQTT_HOST='broker',
        MQTT_USERNAME='u', MQTT_PASSWORD='p w', SENSOR_MAP='yes')
    conf, lines = run()
    section = conf['EcowittGateway']
    assert any('Found GW2000' in line for line in lines)
    assert conf['Station']['station_type'] == 'EcowittGateway'
    assert conf['StdArchive']['record_generation'] == 'software'
    assert section['ip_address'] == gateway.address and section['poll_interval'] == '30'
    assert section['catchup']['source'] == 'none'
    assert section['loop_json'] == {'enable': 'True', 'path': '/tmp/x', 'units': 'native'}
    assert section['mqtt']['host'] == 'broker' and section['mqtt']['password'] == 'p w'
    # the rain source, through the driver's ConfEditor
    assert conf['StdWXCalculate']['Delta']['rain']['input'] == 'p_rainyear'
    # the multi-channel sensors found on the gateway, on their current channels
    assert section['sensor_map'] == {'5E': '1', 'C3': '1', 'C0FFEE': '2'}
    assert not any('********' not in line and 'p w' in line for line in lines)     # password not shown


def test_gw1000_found_over_the_tcp_api(monkeypatch, gw1000):
    monkeypatch.setattr(install.EcowittHttpInstaller, '_tcp',
                        staticmethod(functools.partial(install.EcowittHttpInstaller._tcp, port=gw1000.port)))
    env(monkeypatch, IP_ADDRESS='127.0.0.1:9', MODE='driver', CATCHUP='net')
    conf, lines = run()
    assert any('Found GW1000B_V1.7.7 (TCP API)' in line for line in lines)
    assert any('Paired rain gauges: piezo' in line for line in lines)
    assert conf['StdWXCalculate']['Delta']['rain']['input'] == 'p_rainyear'
    assert conf['EcowittGateway']['catchup']['source'] == 'net'


@pytest.mark.parametrize('name,value,message', [
    ('MODE', 'drvier', 'is not one of'), ('POLL_INTERVAL', '0', 'whole number'),
    ('MQTT', 'maybe', 'yes or no'), ('IP_ADDRESS', '', 'is empty')])
def test_bad_values_stop_before_any_change(monkeypatch, name, value, message):
    env(monkeypatch, **{name: value})
    with pytest.raises(install.SettingError, match=message):
        install.EcowittHttpInstaller()


def test_unknown_variable_is_a_warning(monkeypatch):
    env(monkeypatch, IPADDRESS='x')
    assert install.check_env() == ['Ignoring unknown setting ECOWITT_IPADDRESS']


def test_service_mode(monkeypatch, gateway):
    env(monkeypatch, IP_ADDRESS=gateway.address, MODE='service')
    conf, lines = run()
    assert conf['Station']['station_type'] == 'Simulator'
    assert f'{install.SERVICE} is in data_services.' in lines
    # weectl adds the service to data_services after configure()
    assert install.EcowittHttpInstaller()['data_services'] == install.SERVICE


def test_driver_mode_removes_the_service(monkeypatch, gateway):
    env(monkeypatch, IP_ADDRESS=gateway.address, MODE='driver')
    conf, _ = run(WEEWX_CONF.replace('data_services = ,', f'data_services = {install.SERVICE}, user.other.Service'))
    assert conf['Engine']['Services']['data_services'] == ['user.other.Service']


def test_unreachable_gateway_still_installs(monkeypatch):
    env(monkeypatch, IP_ADDRESS='127.0.0.1:9', MODE='driver')
    monkeypatch.setattr(install.EcowittHttpInstaller, '_probe', lambda self, ip: (None, None, None))
    conf, lines = run()
    assert any('Using this address anyway' in line for line in lines)
    assert conf['Station']['station_type'] == 'EcowittGateway'
    assert conf['StdWXCalculate']['Delta']['rain']['input'] == 't_rainyear'     # tipping, not none


def test_dry_run_changes_nothing(monkeypatch, gateway):
    env(monkeypatch, IP_ADDRESS=gateway.address, MODE='driver')
    conf, lines = run(dry_run=True)
    assert 'Dry run: weewx.conf not changed.' in lines
    assert conf['Station']['station_type'] == 'Simulator' and 'EcowittGateway' not in conf


def test_legacy_section_is_moved(monkeypatch):
    conf, _ = run(WEEWX_CONF.replace('station_type = Simulator', 'station_type = EcowittHttp')
                  + '[EcowittHttp]\n    driver = user.ecowitt_http\n    ip_address = 192.0.2.5\n'
                    '    poll_interval = 45\n')
    assert 'EcowittHttp' not in conf
    assert conf['Station']['station_type'] == 'EcowittGateway'
    assert conf['EcowittGateway']['ip_address'] == '192.0.2.5' and conf['EcowittGateway']['poll_interval'] == '45'
    assert conf['EcowittGateway']['driver'] == 'user.weewx-ecowittGateway'


def test_legacy_extension_is_removed(tmp_path):
    record = tmp_path / 'extensions' / install.LEGACY_EXTENSION
    record.mkdir(parents=True)
    _, lines = run(tmp_path=tmp_path)
    assert not record.exists()
    assert any('removed the Ecowitt_HTTP extension' in line for line in lines)


def test_reconfigure_without_prompts_keeps_the_settings(monkeypatch, gateway):
    """'weectl station reconfigure --no-prompt' calls modify_config() without prompt_for_settings()."""
    monkeypatch.setattr(egw_module.EcowittHttpDriverConfEditor, 'prompting', False)
    monkeypatch.setattr('weecfg.prompt_with_options',
                        lambda *a, **k: pytest.fail('asked a question with prompting off'))
    conf = configobj.ConfigObj(io.StringIO("loop_on_init = 0\n" + WEEWX_CONF + f"""
[EcowittGateway]
    driver = user.weewx-ecowittGateway
    ip_address = {gateway.address}
    api = http
[StdWXCalculate]
    [[Delta]]
        [[[rain]]]
            input = p_rainyear
"""), encoding='utf-8')
    egw_module.EcowittHttpDriverConfEditor().modify_config(conf)
    assert conf['loop_on_init'] == '0'
    assert conf['StdWXCalculate']['Delta']['rain']['input'] == 'p_rainyear'


def test_versions_compare_as_numbers():
    assert install.version_compare('5.10.0', '5.9.1') == 1
    assert install.version_compare('5.0.0', '5.0') == 0
    assert install.version_compare('4.10.2', '5.0.0') == -1


def test_installer_and_driver_agree():
    assert install.DRIVER_VERSION == egw_module.DRIVER_VERSION
    assert install.SECTION == egw_module.DRIVER_NAME and install.MODULE == egw_module.MODULE
    assert install.EXPORT_SERVICE == egw_module.EXPORT_SERVICE


def test_new_settings_are_added_on_reinstall():
    """ignore_no_signal and [[limits]] reach an existing [EcowittGateway] the way WeeWX merges an
    extension's settings; the user's own settings are kept."""
    import weeutil.config
    conf = configobj.ConfigObj(io.StringIO(WEEWX_CONF + """
[EcowittGateway]
    ip_address = 192.168.1.10
    ignore_no_signal = True
"""), encoding='utf-8')
    weeutil.config.conditional_merge(conf, install.EcowittHttpInstaller()['config'])
    section = conf['EcowittGateway']
    assert section['ip_address'] == '192.168.1.10' and section['ignore_no_signal'] == 'True'
    assert section['limits'] == {} and section['log_next_update'] == 'True'
    fresh = configobj.ConfigObj(io.StringIO(WEEWX_CONF), encoding='utf-8')
    weeutil.config.conditional_merge(fresh, install.EcowittHttpInstaller()['config'])
    assert fresh['EcowittGateway']['ignore_no_signal'] == 'False'


# ------------------------------------------------------------------ the rename to weewx-ecowittGateway

def test_old_module_name_is_migrated():
    """An earlier extension was installed as weewx-EcowittGateway (module user.weewx-EcowittGateway)."""
    conf = configobj.ConfigObj(io.StringIO(WEEWX_CONF.replace('station_type = Simulator', 'station_type = EcowittGateway')
                                           .replace('data_services = ,', 'data_services = user.weewx-EcowittGateway.EcowittHttpService, user.other.Service\n'
                                                    '        restful_services = weewx.restx.StdStationRegistry, user.weewx-EcowittGateway.EcowittGatewayExport')
                                           + '[EcowittGateway]\n    driver = user.weewx-EcowittGateway\n'), encoding='utf-8')
    changes = install.move_legacy_section(conf)
    assert conf['EcowittGateway']['driver'] == 'user.weewx-ecowittGateway'
    services = conf['Engine']['Services']
    assert services['data_services'] == ['user.weewx-ecowittGateway.EcowittHttpService', 'user.other.Service']
    assert services['restful_services'][1] == 'user.weewx-ecowittGateway.EcowittGatewayExport'
    assert len(changes) == 3
    assert install.move_legacy_section(conf) == []


def test_service_added_by_weewx_is_not_doubled():
    """WeeWX adds the installer's restful_services entry (the new name) before configure() runs."""
    conf = configobj.ConfigObj(io.StringIO(WEEWX_CONF.replace(
        'data_services = ,', 'restful_services = user.weewx-EcowittGateway.EcowittGatewayExport, '
                             'user.weewx-ecowittGateway.EcowittGatewayExport')), encoding='utf-8')
    install.move_legacy_section(conf)
    assert conf['Engine']['Services']['restful_services'] == ['user.weewx-ecowittGateway.EcowittGatewayExport']


def make_roots(tmp_path, old_record, old_file):
    """A user directory with the new driver and extension record as weectl leaves them before configure(),
    and the old ones: real ('copy') or the same file under another case ('same', as on macOS)."""
    user, ext = tmp_path / 'user', tmp_path / 'user' / 'installer'
    (ext / 'weewx-ecowittGateway').mkdir(parents=True)
    (ext / 'weewx-ecowittGateway' / 'install.py').write_text('new')
    (user / 'weewx-ecowittGateway.py').write_text('new driver')
    if old_record == 'copy':
        (ext / 'weewx-EcowittGateway').mkdir()
    else:
        (ext / 'weewx-EcowittGateway').symlink_to(ext / 'weewx-ecowittGateway')
    if old_file == 'copy':
        (user / 'weewx-EcowittGateway.py').write_text('old driver')
    else:
        (user / 'weewx-EcowittGateway.py').symlink_to(user / 'weewx-ecowittGateway.py')
    return types.SimpleNamespace(root_dict={'USER_DIR': str(user), 'EXT_DIR': str(ext)}, dry_run=False), user, ext


def test_old_extension_is_removed(tmp_path):
    engine, user, ext = make_roots(tmp_path, 'copy', 'copy')
    changes = install.EcowittHttpInstaller.remove_legacy_extension(engine)
    assert not (ext / 'weewx-EcowittGateway').exists() and not (user / 'weewx-EcowittGateway.py').exists()
    assert (user / 'weewx-ecowittGateway.py').read_text() == 'new driver'
    assert (ext / 'weewx-ecowittGateway' / 'install.py').exists()
    assert changes == ['removed the weewx-EcowittGateway extension (now weewx-ecowittGateway), which this '
                       'replaces; its settings are kept']


def test_case_insensitive_file_system_keeps_the_new_driver(tmp_path):
    """On macOS the old and new names are one file and one record: nothing is removed."""
    engine, user, ext = make_roots(tmp_path, 'same', 'same')
    assert install.EcowittHttpInstaller.remove_legacy_extension(engine) == []
    assert (user / 'weewx-ecowittGateway.py').read_text() == 'new driver'
    assert (ext / 'weewx-ecowittGateway' / 'install.py').exists()


# ------------------------------------------------------------------ other gateways ([[sources]])

@pytest.fixture
def second_gateway():
    from conftest import FakeGateway, load_dump
    gw = FakeGateway(load_dump('gw2000_ws90.json'))
    yield gw
    gw.close()


def test_other_gateways_make_sources(monkeypatch, gateway, second_gateway):
    env(monkeypatch, IP_ADDRESS=gateway.address, MODE='driver', CATCHUP='none', SENSOR_MAP='yes',
        MORE_GATEWAYS=f'garden={second_gateway.address}')
    conf, lines = run()
    section = conf['EcowittGateway']
    sources = section['sources']
    assert list(sources) == ['gateway1', 'garden']
    assert sources['gateway1']['ip_address'] == gateway.address
    assert sources['garden']['ip_address'] == second_gateway.address
    # each gateway's sensor map in its own subsection; the section's isn't read with [[sources]]
    assert sources['gateway1']['sensor_map'] == {'5E': '1', 'C3': '1', 'C0FFEE': '2'}
    assert sources['garden']['sensor_map'] == {'5E': '1', 'C3': '1', 'C0FFEE': '2'}
    assert not section['sensor_map']
    assert any(f'Found GW2000' in line and 'garden' in line for line in lines)
    # the driver reads both
    import conftest
    names = [n for n, _ in conftest.egw_module.source_configs(section)]
    assert names == ['gateway1', 'garden']


def test_unnamed_other_gateways(monkeypatch, gateway, second_gateway):
    env(monkeypatch, IP_ADDRESS=gateway.address, MODE='service', CATCHUP='none',
        MORE_GATEWAYS=f'{second_gateway.address}, 192.0.2.7')
    conf, lines = run()
    sources = conf['EcowittGateway']['sources']
    assert list(sources) == ['gateway1', 'gateway2', 'gateway3']
    assert sources['gateway3']['ip_address'] == '192.0.2.7'      # kept, though it didn't answer
    assert any('No response from a device at 192.0.2.7' in line for line in lines)


def test_no_other_gateways_by_default(monkeypatch, gateway):
    env(monkeypatch, IP_ADDRESS=gateway.address, MODE='driver', CATCHUP='none')
    conf, _ = run()
    assert 'sources' not in conf['EcowittGateway']


@pytest.mark.parametrize('value', ['garden=', 'my garden=192.168.1.5', 'a=1.2.3.4, a=1.2.3.5'])
def test_bad_other_gateways_stop_the_install(monkeypatch, gateway, value):
    env(monkeypatch, IP_ADDRESS=gateway.address, MODE='driver', MORE_GATEWAYS=value)
    with pytest.raises(install.SettingError):
        run()


def test_existing_sources_are_kept(monkeypatch, gateway):
    env(monkeypatch, IP_ADDRESS=gateway.address, MODE='driver', CATCHUP='none', SENSOR_MAP='yes',
        MORE_GATEWAYS='other=192.0.2.9')
    conf, lines = run(WEEWX_CONF + f"""[EcowittGateway]
    ip_address = {gateway.address}
    [[sources]]
        [[[house]]]
            ip_address = {gateway.address}
        [[[shed]]]
            ip_address = 192.0.2.8
""")
    sources = conf['EcowittGateway']['sources']
    assert list(sources) == ['house', 'shed']                  # not changed, nothing added
    assert any('Several gateways are set up in [[sources]]' in line for line in lines)
    # new sensor map entries go to the first gateway, where they are read
    assert sources['house']['sensor_map'] == {'5E': '1', 'C3': '1', 'C0FFEE': '2'}


# ------------------------------------------------------------------ fields with no database column

def test_additions_database_by_default(monkeypatch, gateway):
    env(monkeypatch, IP_ADDRESS=gateway.address, MODE='driver', CATCHUP='none')
    conf, lines = run()
    assert conf['EcowittGateway']['additions'] == {'enable': 'True', 'data_binding': 'ecowitt_binding'}
    assert conf['DataBindings']['ecowitt_binding']['schema'] == 'user.weewx-ecowittGateway.ADDITIONS_SCHEMA'
    assert conf['Databases']['ecowitt_sqlite']['database_name'] == 'ecowitt_additions.sdb'
    assert 'user.weewx-ecowittGateway.EcowittAdditions' in conf['Engine']['Services']['archive_services']
    assert any("no column for, in ecowitt_additions.sdb: y  (default)" in line for line in lines)


def test_additions_database_declined(monkeypatch, gateway):
    env(monkeypatch, IP_ADDRESS=gateway.address, MODE='driver', CATCHUP='none', ADDITIONS='no')
    conf, _ = run()
    assert conf['EcowittGateway']['additions']['enable'] == 'False'


def test_additions_setting_kept_on_reinstall(monkeypatch, gateway):
    env(monkeypatch, IP_ADDRESS=gateway.address, MODE='driver', CATCHUP='none')
    conf, _ = run(WEEWX_CONF + f"[EcowittGateway]\n    ip_address = {gateway.address}\n"
                               "    [[additions]]\n        enable = False\n")
    assert conf['EcowittGateway']['additions']['enable'] == 'False'
