# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
# Copyright 2013 New Dream Network, LLC (DreamHost)
# Copyright 2014 Neusoft Corporation (Neusoft)
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
# @author: Paul Yang, Neusoft

import itertools

from oslo.config import cfg

from neutron.agent.linux import utils
from neutron.plugins.common import constants as qconstants
from neutron.services.loadbalancer import constants


PROTOCOL_MAP = {
    constants.PROTOCOL_TCP: 'tcp',
    constants.PROTOCOL_HTTP: 'http',
    constants.PROTOCOL_HTTPS: 'tcp',
}

POOL_PROTOCOL_MAP = {
    constants.PROTOCOL_TCP: 'http',
    constants.PROTOCOL_HTTP: 'http',
    constants.PROTOCOL_HTTPS: 'https',
}

BALANCE_MAP = {
    constants.LB_METHOD_ROUND_ROBIN: 'rr',
    constants.LB_METHOD_LEAST_CONNECTIONS: 'least_conn',
    constants.LB_METHOD_SOURCE_IP: 'ip_hash'
}

ACTIVE = qconstants.ACTIVE
INACTIVE = qconstants.INACTIVE


def save_config(conf_path, logical_config):
    """Convert a logical configuration to the SEnginx version."""
    protocol = logical_config['vip']['protocol']
    if not protocol:
        return

    data = []
    data.extend(_build_global(logical_config))

    # build protocol specified configs
    if PROTOCOL_MAP[protocol] == "http":
        data.extend(_build_http(logical_config))
    else:
        data.extend(_build_tcp(logical_config))

    utils.replace_file(conf_path, '\n'.join(data))


def _build_global(config):
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


def _build_http(config):
    opts = [
            'http {',
            'include /usr/local/senginx/conf/mime.types;',
            'default_type /usr/local/senginx/conf/application/octet-stream;',
            'access_log http.access.log;',
            'sendfile on;',
            'keepalive_timeout 65;',
            ' ',
            ]

    opts.extend(_build_http_upstream(config));
    opts.extend(_build_http_server(config));

    opts.append('}')

    return opts


def _build_http_upstream(config):
    lb_method = config['pool']['lb_method']

    if not config['members']:
        return []

    opts = [
        'upstream %s {' % config['pool']['id'],
    ]

    if lb_method != constants.LB_METHOD_ROUND_ROBIN:
        opts.append('%s;' % BALANCE_MAP.get(lb_method))

    opts.append('')

    # add session persistence (if available)
    persist_opts = _get_session_persistence(config)
    opts.extend(persist_opts)

    # add the members
    for member in config['members']:
        if member['status'] in (ACTIVE, INACTIVE) and member['admin_state_up']:
            server = (('server %(address)s:%(protocol_port)s '
                       'weight=%(weight)s;') % member)
            opts.append(server)

    # add the first health_monitor (if available)
    health_opts = _get_server_health_option(config)
    opts.extend(health_opts)

    opts.append('}')
    opts.append('')

    return opts


def _build_http_server(config):
    pool_protocol = config['pool']['protocol']

    if not config['members']:
        return []

    opts = [
        'server {',
        'listen %s:%d;' % (
            _get_first_ip_from_port(config['vip']['port']),
            config['vip']['protocol_port']
        ),
        '',
        'location / {',
        'proxy_pass %s://%s;' %
        (POOL_PROTOCOL_MAP[pool_protocol], config['pool']['id']),
        '}',
    ]

    if config['healthmonitors']:
        opts.append('location /senginx-check-http-status {');
        opts.append('check_status csv;');
        opts.append('}');


    opts.append('}');
    opts.append('');

    return opts


def _build_tcp(config):
    opts = [
            'tcp {',
            'access_log tcp.access.log;',
            ' ',
            ]

    opts.extend(_build_tcp_upstream(config));
    opts.extend(_build_tcp_server(config));

    opts.append('}')

    return opts


def _build_tcp_upstream(config):
    lb_method = config['pool']['lb_method']

    if not config['members']:
        return []

    opts = [
        'upstream %s {' % config['pool']['id'],
    ]

    if lb_method == constants.LB_METHOD_SOURCE_IP:
        opts.append('%s;' % BALANCE_MAP.get(lb_method))

    opts.append('')

    # add session persistence (if available)
    #persist_opts = _get_session_persistence(config)
    #opts.extend(persist_opts)

    # add the members
    for member in config['members']:
        if member['status'] in (ACTIVE, INACTIVE) and member['admin_state_up']:
            server = (('server %(address)s:%(protocol_port)s') % member)
            if lb_method == constants.LB_METHOD_ROUND_ROBIN:
                server = server + ((' weight=%(weight)s;') % member)
            else:
                server = server + ';'
            opts.append(server)

    # add the first health_monitor (if available)
    health_opts = _get_server_health_option(config)
    opts.extend(health_opts)

    opts.append('}')
    opts.append('')

    return opts


def _build_tcp_server(config):
    if not config['members']:
        return []

    opts = [
        'server {',
        'listen %s:%d;' % (
            _get_first_ip_from_port(config['vip']['port']),
            config['vip']['protocol_port']
        ),
        '',
        'proxy_pass %s;' % config['pool']['id'],
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
        return []

    opts = []

    delay = int(monitor['delay']) * 1000
    timeout = int(monitor['timeout']) * 1000

    if monitor['type'] == constants.HEALTH_MONITOR_HTTP:
        opts.append('check interval=%d fall=%d'
                    ' timeout=%d type=http;' %
                    (delay, monitor['max_retries'], timeout))
        opts.append('check_http_send "%(http_method)s %(url_path)s '
                    'HTTP/1.0\\r\\n\\r\\n";' % monitor)
        opts.append('check_http_expect_alive %s;' %
                    ' '.join(_expand_expected_codes(monitor['expected_codes'])))
    elif monitor['type'] == constants.HEALTH_MONITOR_HTTPS:
        opts.append('check interval=%d fall=%d'
                    ' timeout=%d type=ssl_hello;' %
                    (delay, monitor['max_retries'], timeout))
    elif monitor['type'] == constants.HEALTH_MONITOR_TCP:
        opts.append('check interval=%d fall=%d'
                    ' timeout=%d type=tcp;' %
                    (delay, monitor['max_retries'], timeout))

    return opts


def _get_session_persistence(config):
    persistence = config['vip'].get('session_persistence')
    if not persistence:
        return []

    opts = []
    if persistence['type'] == constants.SESSION_PERSISTENCE_SOURCE_IP:
        # XXX: no source ip persistence is availiable currently
        return opts
    elif persistence['type'] == constants.SESSION_PERSISTENCE_HTTP_COOKIE:
        opts.append('persistence insert_cookie cookie_name=senginx timeout=30;')
    elif (persistence['type'] == constants.SESSION_PERSISTENCE_APP_COOKIE and
          persistence.get('cookie_name')):
        opts.append('persistence insert_cookie cookie_name=senginx ' \
                'monitor_cookie=%s timeout=30;' %
                persistence['cookie_name'])

    return opts


def _expand_expected_codes(codes):
    """SEnginx does not support single response code,
    it only supports 2xx, 3xx, 4xx and 5xx.

    500 -> http_5xx
    200-204 -> http_2xx
    200, 203 -> http_2xx
    300, 304 -> http_3xx
    200-304 -> http_2xx http_3xx
    200, 304 -> http_2xx http_3xx
    ...
    """

    l_codes = []
    retval = []

    if '-' in codes:
        low, hi = codes.split('-')[:2]
        for i in range(int(low), int(hi) + 1):
            l_codes.append(str(i))
    else:
        l_codes = codes.replace(',', ' ').split(' ')

    for code in l_codes:
        code = code.strip()
        i_code = int(code)

        if i_code >= 200 and i_code < 300:
            retval.append('http_2xx')
        elif i_code >= 300 and i_code < 400:
            retval.append('http_3xx')
        elif i_code >= 400 and i_code < 500:
            retval.append('http_4xx')
        elif i_code >= 500 and i_code < 600:
            retval.append('http_5xx')

    return list(set(retval))
