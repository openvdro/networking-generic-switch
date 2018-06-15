# Copyright (c) 2016 Mirantis Inc.
# All Rights Reserved.
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

from keystoneauth1 import loading as ks_loading
from neutron_lib.callbacks import events
from neutron_lib.callbacks import registry
from neutron_lib.callbacks import resources
from neutron_lib import constants
from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import importutils

from neutron._i18n import _
from neutron.notifiers import batch_notifier

ironic_client = None

LOG = logging.getLogger(__name__)

ml2_genericswitch_opts = [
    cfg.StrOpt('region_name',
               help=_('Name of nova region to use. Useful if keystone manages'
                      ' more than one region.')),
    cfg.StrOpt('endpoint_type',
               default='public',
               choices=['public', 'admin', 'internal'],
               help=_('Type of the nova endpoint to use.  This endpoint will'
                      ' be looked up in the keystone catalog and should be'
                      ' one of public, internal or admin.')),
]

ML2_GENERICSWITCH_CONF_SECTION = 'ml2_genericswitch'

ks_loading.register_auth_conf_options(cfg.CONF, ML2_GENERICSWITCH_CONF_SECTION)
ks_loading.register_session_conf_options(cfg.CONF,
                                         ML2_GENERICSWITCH_CONF_SECTION)

cfg.CONF.register_opts(ml2_genericswitch_opts,
                       group=ML2_GENERICSWITCH_CONF_SECTION)


class Notifier(object):

    def __init__(self):
        global ironic_client
        if ironic_client is None:
            ironic_client = importutils.import_module('ironicclient.client')

        auth = ks_loading.load_auth_from_conf_options(
            cfg.CONF, ML2_GENERICSWITCH_CONF_SECTION)

        session = ks_loading.load_session_from_conf_options(
            cfg.CONF,
            ML2_GENERICSWITCH_CONF_SECTION,
            auth=auth)

        self.irclient = ironic_client.get_client(
            1, session=session,
            region_name=cfg.CONF.ml2_genericswitch.region_name,
            endpoint_type=cfg.CONF.ml2_genericswitch.endpoint_type)

        self.batch_notifier = batch_notifier.BatchNotifier(
            cfg.CONF.send_events_interval, self.send_events)

        registry.subscribe(self.process_port_update_event,
                           resources.PORT, events.AFTER_UPDATE)
        registry.subscribe(self.process_port_delete_event,
                           resources.PORT, events.AFTER_DELETE)

    def send_events(self, batched_events):
        LOG.debug("FIND ME: send_events")
        self.irclient.event.create(events=batched_events)

    def process_port_update_event(self, resource, event, trigger,
                                  original_port=None, port=None,
                                  **kwargs):
        # We only want to notify about baremetal ports.
        if not port['binding:vnic_type'] == 'baremetal':
            LOG.debug("FIND ME not a baremetal port")
            return

        original_port_status = original_port['status']
        current_port_status = port['status']
        event_type = None
        if (original_port_status == constants.PORT_STATUS_ACTIVE and
                current_port_status in [constants.PORT_STATUS_DOWN,
                                        constants.PORT_STATUS_ERROR]):
            event_type = 'unbind_port'
        elif (original_port_status == constants.PORT_STATUS_DOWN and
                current_port_status in [constants.PORT_STATUS_ACTIVE,
                                        constants.PORT_STATUS_ERROR]):
            event_type = 'bind_port'

        if event_type:
            event = {
                'interface': 'network',
                'identifier': port['device_id'],
                'payload': {
                    'port_id': port['id'],
                    'mac_address': port['mac_address'],
                    'status': current_port_status,
                }
            }

            self.batch_notifier.queue_event(event)

    def process_port_delete_event(self, resource, event, trigger,
                                  original_port=None, port=None,
                                  **kwargs):
        # We only want to notify about baremetal ports.
        if not port['binding:vnic_type'] == 'baremetal':
            LOG.debug("FIND ME not a baremetal port")
            return

        event = {
            'interface': 'network',
            'identifier': port['device_id'],
            'payload': {
                'port_id': port['id'],
                'mac_address': port['mac_address'],
                'status': 'DELETED',
            }
        }

        self.batch_notifier.queue_event(event)
