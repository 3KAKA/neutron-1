# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
# Copyright 2013 New Dream Network, LLC (DreamHost)
# Copyright 2013 Citrix Systems, Inc.
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
# @author: Youcef Laribi, Citrix

import uuid

from oslo.config import cfg

from neutron.api.v2 import attributes
from neutron.common import constants as q_const
from neutron.common import exceptions as q_exc
from neutron.common import rpc as q_rpc
from neutron.db import agents_db
from neutron.db.loadbalancer import loadbalancer_db
from neutron.extensions import lbaas_agentscheduler
from neutron.openstack.common import importutils
from neutron.openstack.common import log as logging
from neutron.openstack.common import rpc
from neutron.openstack.common.rpc import proxy
from neutron.plugins.common import constants
from neutron.services.loadbalancer.drivers import abstract_driver

LOG = logging.getLogger(__name__)

ACTIVE_PENDING = (
    constants.ACTIVE,
    constants.PENDING_CREATE,
    constants.PENDING_UPDATE
)

AGENT_SCHEDULER_OPTS = [
    cfg.StrOpt('loadbalancer_pool_scheduler_driver',
               default='neutron.services.loadbalancer.agent_scheduler'
                       '.ChanceScheduler',
               help=_('Driver to use for scheduling '
                      'pool to a default loadbalancer agent')),
]

cfg.CONF.register_opts(AGENT_SCHEDULER_OPTS)

# topic name for this particular agent implementation
TOPIC_LOADBALANCER_DEVICE = 'q-lbaas-netscaler'
TOPIC_LOADBALANCER_AGENT = 'lbaas_netscaler_agent'


class LoadBalancerCallbacks(object):

    RPC_API_VERSION = '1.0'

    def __init__(self, plugin):
        self.plugin = plugin

    def create_rpc_dispatcher(self):
        return q_rpc.PluginRpcDispatcher(
            [self, agents_db.AgentExtRpcCallback(self.plugin)])


    def pool_destroyed(self, context, pool_id=None, host=None):
        """Agent confirmation hook that a pool has been destroyed.

        This method exists for subclasses to change the deletion
        behavior.
        """
        pass

    def plug_vip_port(self, context, port_id=None, host=None):
        if not port_id:
            return

        try:
            port = self.plugin._core_plugin.get_port(
                context,
                port_id
            )
        except q_exc.PortNotFound:
            msg = _('Unable to find port %s to plug.')
            LOG.debug(msg, port_id)
            return

        port['admin_state_up'] = True
        port['device_owner'] = 'neutron:' + constants.LOADBALANCER
        port['device_id'] = str(uuid.uuid5(uuid.NAMESPACE_DNS, str(host)))

        self.plugin._core_plugin.update_port(
            context,
            port_id,
            {'port': port}
        )

    def unplug_vip_port(self, context, port_id=None, host=None):
        if not port_id:
            return

        try:
            port = self.plugin._core_plugin.get_port(
                context,
                port_id
            )
        except q_exc.PortNotFound:
            msg = _('Unable to find port %s to unplug.  This can occur when '
                    'the Vip has been deleted first.')
            LOG.debug(msg, port_id)
            return

        port['admin_state_up'] = False
        port['device_owner'] = ''
        port['device_id'] = ''

        try:
            self.plugin._core_plugin.update_port(
                context,
                port_id,
                {'port': port}
            )

        except q_exc.PortNotFound:
            msg = _('Unable to find port %s to unplug.  This can occur when '
                    'the Vip has been deleted first.')
            LOG.debug(msg, port_id)

    def update_pool_stats(self, context, pool_id=None, stats=None, host=None):
        self.plugin.update_pool_stats(context, pool_id, data=stats)


class LoadBalancerAgentApi(proxy.RpcProxy):
    """Plugin side of plugin to agent RPC API."""

    BASE_RPC_API_VERSION = '1.0'
    # history
    #   1.0 Initial version
    #   1.1 Support agent_updated call

    def __init__(self, topic):
        super(LoadBalancerAgentApi, self).__init__(
            topic, default_version=self.BASE_RPC_API_VERSION)

    def create_vip(self, context, vip, netinfo, host):
        return self.cast(
            context,
            self.make_msg('create_vip', vip=vip, netinfo=netinfo),
            topic='%s.%s' % (self.topic, host)
        )

    def update_vip(self, context, old_vip, vip, old_netinfo, netinfo, host):
        return self.cast(
            context,
            self.make_msg('update_vip', old_vip=old_vip, vip=vip, old_netinfo=old_netinfo, netinfo=netinfo),
            topic='%s.%s' % (self.topic, host)
        )

    def delete_vip(self, context, vip, netinfo, host):
        return self.cast(
            context,
            self.make_msg('delete_vip', vip=vip, netinfo=netinfo),
            topic='%s.%s' % (self.topic, host)
        )

    def create_pool(self, context, pool, netinfo, host):
        return self.cast(
            context,
            self.make_msg('create_pool', pool=pool, netinfo=netinfo),
            topic='%s.%s' % (self.topic, host)
        )

    def update_pool(self, context, old_pool, pool, old_netinfo, netinfo, host):
        return self.cast(
            context,
            self.make_msg('update_pool', old_pool=old_pool, pool=pool, old_netinfo=old_netinfo, netinfo=netinfo),
            topic='%s.%s' % (self.topic, host)
        )

    def delete_pool(self, context, pool, netinfo, host):
        return self.cast(
            context,
            self.make_msg('delete_pool', pool=pool, netinfo=netinfo),
            topic='%s.%s' % (self.topic, host)
        )

    def create_member(self, context, member, netinfo, host):
        return self.cast(
            context,
            self.make_msg('create_member', member=member, netinfo=netinfo),
            topic='%s.%s' % (self.topic, host)
        )

    def update_member(self, context, old_member, member, old_netinfo, netinfo, host):
        return self.cast(
            context,
            self.make_msg('update_member', old_member=old_member, member=member, 
                           old_netinfo=old_netinfo, netinfo=netinfo),
            topic='%s.%s' % (self.topic, host)
        )

    def delete_member(self, context, member, netinfo, host):
        return self.cast(
            context,
            self.make_msg('delete_member', member=member, netinfo=netinfo),
            topic='%s.%s' % (self.topic, host)
        )

    def create_pool_health_monitor(self, context, health_monitor, pool_id, netinfo, host):
        return self.cast(
            context,
            self.make_msg('create_pool_health_monitor', health_monitor=health_monitor, pool_id=pool_id,
                           netinfo=netinfo),
            topic='%s.%s' % (self.topic, host)
        )

    def update_health_monitor(self, context, old_health_monitor,
                              health_monitor, pool_id, netinfo, host):
        return self.cast(
            context,
            self.make_msg('update_health_monitor', old_health_monitor=old_health_monitor, 
                          health_monitor=health_monitor, pool_id=pool_id, netinfo=netinfo),
            topic='%s.%s' % (self.topic, host)
        )



    def delete_pool_health_monitor(self, context, health_monitor, pool_id,
                                   netinfo, host):
        return self.cast(
            context,
            self.make_msg('delete_pool_health_monitor', health_monitor=health_monitor, 
                           pool_id=pool_id, netinfo=netinfo),
            topic='%s.%s' % (self.topic, host)
        )

    def agent_updated(self, context, admin_state_up, host):
        return self.cast(
            context,
            self.make_msg('agent_updated',
                          payload={'admin_state_up': admin_state_up}),
            topic='%s.%s' % (self.topic, host),
            version='1.1'
        )


class NetScalerPluginDriver(abstract_driver.LoadBalancerAbstractDriver):

    def __init__(self, plugin):
        self.agent_rpc = LoadBalancerAgentApi(TOPIC_LOADBALANCER_AGENT)
        self.callbacks = LoadBalancerCallbacks(plugin)
        self.conn = rpc.create_connection(new=True)
        self.conn.create_consumer(
            TOPIC_LOADBALANCER_DEVICE,
            self.callbacks.create_rpc_dispatcher(),
            fanout=False)
        self.conn.consume_in_thread()
        self.plugin = plugin
        self.plugin.agent_notifiers.update(
            {q_const.AGENT_TYPE_LOADBALANCER: self.agent_rpc})

        self.pool_scheduler = importutils.import_object(
            cfg.CONF.loadbalancer_pool_scheduler_driver)

    def _get_vip_network_info(self, context, vip):
        network_info = {}

        subnet_id = vip['subnet_id']
        subnet = self.plugin._core_plugin.get_subnet(context, subnet_id)

        network_id = subnet['network_id']
        network = self.plugin._core_plugin.get_network(context, network_id)


        network_info['port_id'] = vip['port_id']
        network_info['network_id'] = subnet['network_id']
        network_info['subnet_id'] = subnet_id

        if 'provider:network_type' in network:
	        network_info['network_type'] = network['provider:network_type']


        if 'provider:segmentation_id' in network:
	        network_info['segmentation_id'] = network['provider:segmentation_id']


        return network_info
   
    def _get_pool_network_info(self, context, pool):
        network_info = {}

        subnet_id = pool['subnet_id']
        subnet = self.plugin._core_plugin.get_subnet(context, subnet_id)

        network_id = subnet['network_id']
        network = self.plugin._core_plugin.get_network(context, network_id)

        network_info['network_id'] = network_id
        network_info['subnet_id'] = subnet_id

        if 'provider:network_type' in network:
	        network_info['network_type'] = network['provider:network_type']

        if 'provider:segmentation_id' in network:
	        network_info['segmentation_id'] = network['provider:segmentation_id']

        return network_info


    def _get_pools_on_subnet(self, context, tenant_id, subnet_id):

        filter = {'subnet_id': [subnet_id], 'tenant_id': [tenant_id]}

        pools = self.plugin.get_pools(context, filters=filter)

        return pools


    def _get_snatport_for_subnet(self, context, tenant_id, subnet_id):

        name = '_lb-snatport-' + subnet_id
        
        subnet = self.plugin._core_plugin.get_subnet(context, subnet_id)
        network_id=subnet['network_id']

        LOG.info(_("Filtering ports based on network_id=%s, tenant_id=%s, name=%s" %
                  (network_id, tenant_id, name)))

        filter = {'network_id': [network_id], 'tenant_id': [tenant_id], 'name': [name]}
        ports = self.plugin._core_plugin.get_ports(context, filters=filter)

        if ports:
           LOG.info(_("Found an existing SNAT port for subnet %s" % subnet_id))
           return ports[0]
        
        LOG.info(_("Found no SNAT ports for subnet %s" % subnet_id))
        return None


    def _create_snatport_for_subnet(self, context, tenant_id, subnet_id, ip_address):
            subnet = self.plugin._core_plugin.get_subnet(context, subnet_id)
            fixed_ip = {'subnet_id': subnet['id']}
            if ip_address and ip_address != attributes.ATTR_NOT_SPECIFIED:
                fixed_ip['ip_address'] = ip_address

            port_data = {
                'tenant_id': tenant_id,
                'name': '_lb-snatport-' + subnet_id,
                'network_id': subnet['network_id'],
                'mac_address': attributes.ATTR_NOT_SPECIFIED,
                'admin_state_up': False,
                'device_id': '',
                'device_owner': '',
                'fixed_ips': [fixed_ip]
            }

            port = self.plugin._core_plugin.create_port(context, {'port': port_data})

            return port


    def _remove_snatport_for_subnet(self, context, tenant_id, subnet_id):
        port = self._get_snatport_for_subnet(context,tenant_id, subnet_id)

        if port:
            self.plugin._core_plugin.delete_port(context, port['id'])


    def _create_snatport_for_subnet_if_not_exists(self, context, tenant_id, subnet_id, network_info):
        port = self._get_snatport_for_subnet(context, tenant_id, subnet_id)

        if not port:
            LOG.info(_("No SNAT port exists yet for subnet %s. Creating one..." % subnet_id))
            port = self._create_snatport_for_subnet(context, tenant_id, subnet_id, ip_address=None)

        network_info['port_id'] = port['id']
        network_info['snat_ip'] = port['fixed_ips'][0]['ip_address']

        LOG.info(_("SNAT port: %s" % repr(port)))

    def _remove_snatport_for_subnet_if_not_used(self, context, tenant_id, subnet_id):
        pools = self._get_pools_on_subnet(context, tenant_id, subnet_id)
 
        if not pools:
            #No pools left on the old subnet. We can remove the SNAT port/ipaddress
            self._remove_snatport_for_subnet(context, tenant_id, subnet_id)
            LOG.info(_("Removing SNAT port for subnet %s as it is the last pool using it..." % subnet_id))

    def get_pool_agent(self, context, pool_id):
        agent = self.plugin.get_lbaas_agent_hosting_pool(context, pool_id)
        if not agent:
            raise lbaas_agentscheduler.NoActiveLbaasAgent(pool_id=pool_id)
        return agent['agent']

    def create_vip(self, context, vip):
        agent = self.get_pool_agent(context, vip['pool_id'])
        network_info = self._get_vip_network_info(context, vip)
        self.agent_rpc.create_vip(context, vip, network_info, agent['host'])
        LOG.info(_('create_vip rpc sent to loadbalancer agent...'))

    def update_vip(self, context, old_vip, vip):
        agent = self.get_pool_agent(context, vip['pool_id'])
        old_network_info = self._get_vip_network_info(context, old_vip)
        network_info = self._get_vip_network_info(context, vip)
        self.agent_rpc.update_vip(context, old_vip, vip, old_network_info, network_info, agent['host'])
        LOG.info(_('update_vip rpc sent to loadbalancer agent...'))

    def delete_vip(self, context, vip):
        agent = self.get_pool_agent(context, vip['pool_id'])
        network_info = self._get_vip_network_info(context, vip)
        self.agent_rpc.delete_vip(context, vip, network_info, agent['host'])
        LOG.info(_('delete_vip rpc sent to loadbalancer agent...'))
        self.plugin._delete_db_vip(context, vip['id'])

    def create_pool(self, context, pool):
        LOG.info(_("Pool to be created: %s" % repr(pool)))
        #This is where we pick an agent for this pool (and related resources)
        agent = self.pool_scheduler.schedule(self.plugin, context, pool)

        if not agent:
            raise lbaas_agentscheduler.NoEligibleLbaasAgent(pool_id=pool['id'])

        network_info = self._get_pool_network_info(context, pool)

        #allocate a snat port/ipaddress on the subnet if one doesn't exist
        port = self._create_snatport_for_subnet_if_not_exists(context, pool['tenant_id'], pool['subnet_id'], network_info)

        self.agent_rpc.create_pool(context, pool, network_info, agent['host'])
        LOG.info(_('create_pool rpc sent to loadbalancer agent...'))

    def update_pool(self, context, old_pool, pool):
        agent = self.get_pool_agent(context, pool['id'])
        old_network_info = self._get_pool_network_info(context, old_pool)
        network_info = self._get_pool_network_info(context, pool)

        if pool['subnet_id'] != old_pool['subnet_id']:
           # if this is the first pool using the new subnet, then add a snat port/ipaddress to it.
           self._create_snatport_for_subnet_if_not_exists(context, pool['tenant_id'], pool['subnet_id'], network_info)
           #remove the old snat port/ipaddress from old subnet if this was the last pool using it
           self._remove_snatport_for_subnet_if_not_used(context, old_pool['tenant_id'], old_pool['subnet_id'])

        self.agent_rpc.update_pool(context, old_pool, pool, old_network_info, network_info, agent['host'])
        LOG.info(_('update_pool rpc sent to loadbalancer agent...'))

    def delete_pool(self, context, pool):
        LOG.info(_("Pool to be deleted: %s" % repr(pool)))
        agent = self.get_pool_agent(context, pool['id'])
        network_info = self._get_pool_network_info(context, pool)
        self.agent_rpc.delete_pool(context, pool, network_info, agent['host'])
        LOG.info(_('delete_pool rpc sent to loadbalancer agent...'))
        self.plugin._delete_db_pool(context, pool['id'])

        self._remove_snatport_for_subnet_if_not_used(context, pool['tenant_id'], pool['subnet_id'])

    def create_member(self, context, member):
        agent = self.get_pool_agent(context, member['pool_id'])
        pool = self.plugin.get_pool(context, member['pool_id'])
        network_info = self._get_pool_network_info(context, pool)
        self.agent_rpc.create_member(context, member, network_info, agent['host'])
        LOG.info(_('create_member rpc sent to loadbalancer agent...'))

    def update_member(self, context, old_member, member):
        agent = self.get_pool_agent(context, member['pool_id'])
        old_pool = self.plugin.get_pool(context, old_member['pool_id'])
        pool = self.plugin.get_pool(context, member['pool_id'])
        old_network_info = self._get_pool_network_info(context, old_pool)
        network_info = self._get_pool_network_info(context, pool)
        self.agent_rpc.update_member(context, old_member, member, old_network_info, network_info, agent['host'])
        LOG.info(_('update_member rpc sent to loadbalancer agent...'))

    def delete_member(self, context, member):
        agent = self.get_pool_agent(context, member['pool_id'])
        pool = self.plugin.get_pool(context, member['pool_id'])
        network_info = self._get_pool_network_info(context, pool)
        self.agent_rpc.delete_member(context, member, network_info, agent['host'])
        LOG.info(_('delete_member rpc sent to loadbalancer agent...'))
        self.plugin._delete_db_member(context, member['id'])

    def create_pool_health_monitor(self, context, health_monitor, pool_id):
        LOG.info(_("about to create health monitor..."))
        agent = self.get_pool_agent(context, pool_id)
        pool = self.plugin.get_pool(context, pool_id)
        network_info = self._get_pool_network_info(context, pool)
        self.agent_rpc.create_pool_health_monitor(context, health_monitor, pool_id, network_info, agent['host'])
        LOG.info(_('create_pool_health_monitor rpc sent to loadbalancer agent...'))

    def update_health_monitor(self, context, old_health_monitor,
                              health_monitor, pool_id):
        agent = self.get_pool_agent(context, pool_id)
        pool = self.plugin.get_pool(context, pool_id)
        network_info = self._get_pool_network_info(context, pool)
        self.agent_rpc.update_health_monitor(context, old_health_monitor,
                                             health_monitor, pool_id, network_info, agent['host'])
        LOG.info(_('update_health_monitor rpc sent to loadbalancer agent...'))

    def delete_pool_health_monitor(self, context, health_monitor, pool_id):
        agent = self.get_pool_agent(context, pool_id)
        pool = self.plugin.get_pool(context, pool_id)
        network_info = self._get_pool_network_info(context, pool)
        self.agent_rpc.delete_pool_health_monitor(context, health_monitor, pool_id, 
                                                  network_info, agent['host'])
        LOG.info(_('delete_health_monitor rpc sent to loadbalancer agent...'))
        self.plugin._delete_db_pool_health_monitor(context, health_monitor['id'], pool_id)

    def stats(self, context, pool_id):
        pass
