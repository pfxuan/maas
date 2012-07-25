# Copyright 2012 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Test DNS module."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

__metaclass__ = type
__all__ = []


from django.core.management import call_command
from maasserver.dns import (
    add_zone,
    change_dns_zone,
    next_zone_serial,
    write_full_dns_config,
    zone_serial,
    )
from maasserver.testing.factory import factory
from maasserver.testing.testcase import TestCase
from maastesting.bindfixture import BINDServer
from maastesting.celery import CeleryFixture
from maastesting.tests.test_bindfixture import dig_call
from netaddr import IPNetwork
from provisioningserver.dns.config import conf
from provisioningserver.dns.utils import generated_hostname
from testresources import FixtureResource
from testtools.matchers import MatchesStructure


class TestDNSUtilities(TestCase):

    def test_zone_serial_parameters(self):
        self.assertThat(
            zone_serial,
            MatchesStructure.byEquality(
                maxvalue=2 ** 32 - 1,
                minvalue=1,
                incr=1,
                )
            )

    def test_next_zone_serial_returns_sequence(self):
        initial = int(next_zone_serial())
        self.assertSequenceEqual(
            ['%0.10d' % i for i in range(initial + 1, initial + 11)],
            [next_zone_serial() for i in range(initial, initial + 10)])


class TestDNSConfigModifications(TestCase):

    resources = (
        ("celery", FixtureResource(CeleryFixture())),
        )

    def setUp(self):
        super(TestDNSConfigModifications, self).setUp()
        self.bind = self.useFixture(BINDServer())
        self.patch(conf, 'DNS_CONFIG_DIR', self.bind.config.homedir)

        # This simulates what should happen when the package is
        # installed:
        # Create MAAS-specific DNS configuration files.
        call_command('set_up_dns')
        # Register MAAS-specific DNS configuration files with the
        # system's BIND instance.
        call_command(
            'get_named_conf', edit=True,
            config_path=self.bind.config.conf_file)
        # Reload BIND.
        self.bind.runner.rndc('reload')

    def create_nodegroup_with_lease(self, lease_number=1, nodegroup=None):
        if nodegroup is None:
            nodegroup = factory.make_node_group(
                network=IPNetwork('192.168.0.1/24'))
        node = factory.make_node(
            nodegroup=nodegroup, set_hostname=True)
        mac = factory.make_mac_address(node=node)
        lease = factory.make_dhcp_lease(
            nodegroup=nodegroup, mac=mac.mac_address,
            ip='192.168.0.%d' % lease_number)
        return nodegroup, node, lease

    def dig_resolve(self, fqdn):
        """Resolve `fqdn` using dig.  Returns a list of results."""
        return dig_call(
            port=self.bind.config.port,
            commands=[fqdn, '+short']).split('\n')

    def dig_reverse_resolve(self, ip):
        """Reverse resolve `ip` using dig.  Returns a list of results."""
        return dig_call(
            port=self.bind.config.port,
            commands=['-x', ip, '+short']).split('\n')

    def assertDNSMatches(self, hostname, domain, ip):
        fqdn = "%s.%s" % (hostname, domain)
        autogenerated_hostname = '%s.' % generated_hostname(ip, domain)
        # The fqdn resolves to the autogenerated hostname (CNAME record) and
        # the IP address (A record).
        self.assertItemsEqual(
            [autogenerated_hostname, ip],
            self.dig_resolve(fqdn))
        # A reverse lookup on the IP returns the autogenerated
        # hostname.
        self.assertEqual(
            [autogenerated_hostname], self.dig_reverse_resolve(ip))

    def test_add_zone_loads_dns_zone(self):
        nodegroup, node, lease = self.create_nodegroup_with_lease()
        add_zone(nodegroup)
        self.assertDNSMatches(node.hostname, nodegroup.name, lease.ip)

    def test_change_zone_changes_dns_zone(self):
        nodegroup, _, _ = self.create_nodegroup_with_lease()
        write_full_dns_config()
        nodegroup, new_node, new_lease = (
            self.create_nodegroup_with_lease(
                nodegroup=nodegroup, lease_number=2))
        change_dns_zone(nodegroup)
        self.assertDNSMatches(new_node.hostname, nodegroup.name, new_lease.ip)

    def test_write_full_dns_loads_full_dns_config(self):
        nodegroup, node, lease = self.create_nodegroup_with_lease()
        write_full_dns_config()
        self.assertDNSMatches(node.hostname, nodegroup.name, lease.ip)
