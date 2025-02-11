# Copyright 2015-2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for `maasserver.websockets.handlers.user`"""

__all__ = []

from django.contrib.auth.models import User
from maasserver.models.event import Event
from maasserver.models.user import SYSTEM_USERS
from maasserver.permissions import (
    NodePermission,
    PodPermission,
    ResourcePoolPermission,
)
from maasserver.testing.factory import factory
from maasserver.testing.fixtures import RBACForceOffFixture
from maasserver.testing.testcase import MAASServerTestCase
from maasserver.websockets.base import HandlerDoesNotExistError
from maasserver.websockets.handlers.user import UserHandler
from maastesting.djangotestcase import count_queries
from piston3.models import Token
from provisioningserver.events import AUDIT


class TestUserHandler(MAASServerTestCase):

    def dehydrate_user(self, user, sshkeys_count=0, for_self=False):
        data = {
            "id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "is_superuser": user.is_superuser,
            "sshkeys_count": sshkeys_count,
        }
        if for_self:
            permissions = []
            if user.has_perm(NodePermission.admin):
                permissions.append('machine_create')
            if user.has_perm(NodePermission.view):
                permissions.append('device_create')
            if user.has_perm(ResourcePoolPermission.create):
                permissions.append('resource_pool_create')
            if user.has_perm(ResourcePoolPermission.delete):
                permissions.append('resource_pool_delete')
            if user.has_perm(PodPermission.create):
                permissions.append('pod_create')
            data['global_permissions'] = permissions
        return data

    def test_get_for_admin(self):
        user = factory.make_User()
        admin = factory.make_admin()
        handler = UserHandler(admin, {}, None)
        self.assertEqual(
            self.dehydrate_user(user),
            handler.get({"id": user.id}))

    def test_get_for_user_getting_self(self):
        user = factory.make_User()
        handler = UserHandler(user, {}, None)
        self.assertEqual(
            self.dehydrate_user(user, for_self=True),
            handler.get({"id": user.id}))

    def test_get_for_user_not_getting_self(self):
        user = factory.make_User()
        other_user = factory.make_User()
        handler = UserHandler(user, {}, None)
        self.assertRaises(
            HandlerDoesNotExistError, handler.get, {"id": other_user.id})

    def test_list_for_admin(self):
        admin = factory.make_admin()
        handler = UserHandler(admin, {}, None)
        factory.make_User()
        expected_users = [
            self.dehydrate_user(user, for_self=(user == admin))
            for user in User.objects.exclude(username__in=SYSTEM_USERS)
        ]
        self.assertItemsEqual(
            expected_users,
            handler.list({}))

    def test_list_num_queries_is_the_expected_number(self):
        # Prevent RBAC from making a query.
        self.useFixture(RBACForceOffFixture())

        admin = factory.make_admin()
        handler = UserHandler(admin, {}, None)
        for _ in range(3):
            factory.make_User()
        queries_one, _ = count_queries(handler.list, {'limit': 1})
        queries_total, _ = count_queries(handler.list, {})
        # This check is to notify the developer that a change was made that
        # affects the number of queries performed when doing a user listing.
        # It is important to keep this number as low as possible. A larger
        # number means regiond has to do more work slowing down its process
        # and slowing down the client waiting for the response.
        self.assertEqual(
            queries_one, 1,
            "Number of queries has changed; make sure this is expected.")
        self.assertEqual(
            queries_total, 1,
            "Number of queries has changed; make sure this is expected.")

    def test_list_for_standard_user(self):
        user = factory.make_User()
        handler = UserHandler(user, {}, None)
        # Other users
        for _ in range(3):
            factory.make_User()
        self.assertItemsEqual(
            [self.dehydrate_user(user, for_self=True)],
            handler.list({}))

    def test_auth_user(self):
        user = factory.make_User()
        handler = UserHandler(user, {}, None)
        self.assertEqual(
            self.dehydrate_user(user, for_self=True),
            handler.auth_user({}))

    def test_create_authorisation_token(self):
        user = factory.make_User()
        handler = UserHandler(user, {}, None)
        observed = handler.create_authorisation_token({})
        self.assertItemsEqual(['key', 'secret', 'consumer'], observed.keys())
        self.assertItemsEqual(['key', 'name'], observed['consumer'].keys())
        event = Event.objects.get(type__level=AUDIT)
        self.assertIsNotNone(event)
        self.assertEqual(event.description, "Created token.")

    def test_delete_authorisation_token(self):
        user = factory.make_User()
        handler = UserHandler(user, {}, None)
        observed = handler.create_authorisation_token({})
        handler.delete_authorisation_token({'key': observed['key']})
        self.assertIsNone(Token.objects.filter(key=observed['key']).first())
        event = Event.objects.filter(type__level=AUDIT).last()
        self.assertIsNotNone(event)
        self.assertEqual(event.description, "Deleted token.")
