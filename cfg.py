# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
# Copyright 2013 New Dream Network, LLC (DreamHost)
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#
# @author: Mark McClain, DreamHost

import itertools

from oslo.config import cfg

from neutron.agent.linux import utils
from neutron.plugins.common import constants as qconstants
from neutron.services.loadbalancer import constants


PROTOCOL_MAP = {
    constants.PROTOCOL_TCP: 'tcp',
    constants.PROTOCOL_HTTP: 'http',
    constants.PROTOCOL_HTTPS: 'https',
}

BALANCE_MAP = {
    constants.LB_METHOD_ROUND_ROBIN: 'rr',
    constants.LB_METHOD_LEAST_CONNECTIONS: 'least_conn',
    constants.LB_METHOD_SOURCE_IP: 'ip_hash'
}

STATS_MAP = {
    constants.STATS_ACTIVE_CONNECTIONS: 'qcur',
    constants.STATS_MAX_CONNECTIONS: 'qmax',
    constants.STATS_CURRENT_SESSIONS: 'scur',
    constants.STATS_MAX_SESSIONS: 'smax',
    constants.STATS_TOTAL_SESSIONS: 'stot',
    constants.STATS_IN_BYTES: 'bin',
    constants.STATS_OUT_BYTES: 'bout',
    constants.STATS_CONNECTION_ERRORS: 'econ',
    constants.STATS_RESPONSE_ERRORS: 'eresp'
}

ACTIVE = qconstants.ACTIVE
INACTIVE = qconstants.INACTIVE


def save_config(conf_path, logical_config):
    """Convert a logical configuration to the SEnginx version."""
    data = []
    data.extend(_build_global(logical_config))
    data.extend(_build_defaults(logical_config))
    data.extend(_build_backend(logical_config))
    data.extend(_build_frontend(logical_config))
    utils.replace_file(conf_path, '\n'.join(data))


#TODO: reimplement all of the config-builder into more SEnginx native style
# e.g., implement a http builder, an upstream builder, a server builder...


def _build_global(config, socket_path=None):
    opts = [
        'user senginx %s;' % cfg.CONF.user_group,
        'worker_processes 1;',
        'error_log error.log;',
        'pid nginx.pid;',
        'events {',
        'worker_connections 10240;',
        '}',
        '',
    ]

    return opts


def _build_defaults(config):
    protocol = config['pool']['protocol']
    lb_method = config['pool']['lb_method']

    if PROTOCOL_MAP[protocol] == "http" or PROTOCOL_MAP[protocol] == "https":
        opts = [
            'http {',
            'include /usr/local/senginx/conf/mime.types;',
            'default_type /usr/local/senginx/conf/application/octet-stream;',
            'access_log off;',
            'sendfile        on;',
            'keepalive_timeout  65;',
            ' ',
        ]
    else:
        opts = [
            'tcp {',
            ' ',
        ]

    return opts


def _build_backend(config):
    protocol = config['pool']['protocol']
    lb_method = config['pool']['lb_method']

    opts = [
        'upstream %s {' % config['pool']['id'],
    ]

    if lb_method != constants.LB_METHOD_ROUND_ROBIN:
        opts.append('%s' % BALANCE_MAP.get(lb_method))

    opts.append('')

    # add session persistence (if available)
    persist_opts = _get_session_persistence(config)
    #opts.extend(persist_opts)

    # add the members
    for member in config['members']:
        if member['status'] in (ACTIVE, INACTIVE) and member['admin_state_up']:
            server = (('server %(address)s:%(protocol_port)s '
                       'weight=%(weight)s;') % member)
            opts.append(server)

    # add the first health_monitor (if available)
    health_opts = _get_server_health_option(config)
    #opts.extend(health_opts)

    opts.append('}')
    opts.append('')

    return opts


def _build_frontend(config):
    protocol = config['pool']['protocol']

    opts = [
        'server {',
        'listen %s:%d;' % (
            _get_first_ip_from_port(config['vip']['port']),
            config['vip']['protocol_port']
        ),
        '',
        'location / {',
        'proxy_pass %s://%s;' % (PROTOCOL_MAP[protocol], config['pool']['id']),
        '}',
        '}',
        '}',
        ''
    ]

    return opts


def _get_first_ip_from_port(port):
    for fixed_ip in port['fixed_ips']:
        return fixed_ip['ip_address']


def _get_server_health_option(config):
    """return the first active health option."""
    for monitor in config['healthmonitors']:
        # not checking the status of healthmonitor for two reasons:
        # 1) status field is absent in HealthMonitor model
        # 2) only active HealthMonitors are fetched with
        # LoadBalancerCallbacks.get_logical_device
        if monitor['admin_state_up']:
            break
    else:
        return '', []

    server_addon = ' check inter %(delay)ds fall %(max_retries)d' % monitor
    opts = [
        'timeout check %ds' % monitor['timeout']
    ]

    if monitor['type'] in (constants.HEALTH_MONITOR_HTTP,
                           constants.HEALTH_MONITOR_HTTPS):
        opts.append('option httpchk %(http_method)s %(url_path)s' % monitor)
        opts.append(
            'http-check expect rstatus %s' %
            '|'.join(_expand_expected_codes(monitor['expected_codes']))
        )

    if monitor['type'] == constants.HEALTH_MONITOR_HTTPS:
        opts.append('option ssl-hello-chk')

    return server_addon, opts


def _get_session_persistence(config):
    persistence = config['vip'].get('session_persistence')
    if not persistence:
        return []

    opts = []
    if persistence['type'] == constants.SESSION_PERSISTENCE_SOURCE_IP:
        opts.append('stick-table type ip size 10k')
        opts.append('stick on src')
    elif persistence['type'] == constants.SESSION_PERSISTENCE_HTTP_COOKIE:
        opts.append('cookie SRV insert indirect nocache')
    elif (persistence['type'] == constants.SESSION_PERSISTENCE_APP_COOKIE and
          persistence.get('cookie_name')):
        opts.append('appsession %s len 56 timeout 3h' %
                    persistence['cookie_name'])

    return opts


def _has_http_cookie_persistence(config):
    return (config['vip'].get('session_persistence') and
            config['vip']['session_persistence']['type'] ==
            constants.SESSION_PERSISTENCE_HTTP_COOKIE)


def _expand_expected_codes(codes):
    """Expand the expected code string in set of codes.

    200-204 -> 200, 201, 202, 204
    200, 203 -> 200, 203
    """

    retval = set()
    for code in codes.replace(',', ' ').split(' '):
        code = code.strip()

        if not code:
            continue
        elif '-' in code:
            low, hi = code.split('-')[:2]
            retval.update(str(i) for i in xrange(int(low), int(hi) + 1))
        else:
            retval.add(code)
    return retval
