# Copyright 2019 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for ScriptResult status transition event."""

__all__ = []


import json
import random

from maasserver.models import Event
from maasserver.preseed import CURTIN_INSTALL_LOG
from maasserver.testing.factory import factory
from maasserver.testing.testcase import MAASServerTestCase
from metadataserver.enum import (
    RESULT_TYPE,
    SCRIPT_STATUS,
    SCRIPT_STATUS_CHOICES,
    SCRIPT_STATUS_FAILED,
    SCRIPT_STATUS_RUNNING,
)
from provisioningserver.events import (
    EVENT_DETAILS,
    EVENT_TYPES,
)


class TestStatusTransitionEvent(MAASServerTestCase):

    def test__running_or_installing_emits_event_empty_storage_parameters(self):

        script_result = factory.make_ScriptResult(
            status=SCRIPT_STATUS.PENDING, script_set=factory.make_ScriptSet(
                result_type=RESULT_TYPE.TESTING), script=factory.make_Script())
        script_result.status = random.choice(list(SCRIPT_STATUS_RUNNING))
        script_result.parameters = json.dumps({})
        script_result.save()

        latest_event = Event.objects.last()
        self.assertEqual(
            (
                EVENT_TYPES.RUNNING_TEST,
                EVENT_DETAILS[
                    EVENT_TYPES.RUNNING_TEST].description,
                script_result.name,
            ),
            (
                latest_event.type.name,
                latest_event.type.description,
                latest_event.description,
            ))

    def test__running_or_installing_emits_event_with_storage_parameters(self):

        script_result = factory.make_ScriptResult(
            status=SCRIPT_STATUS.PENDING, script_set=factory.make_ScriptSet(
                result_type=RESULT_TYPE.TESTING), script=factory.make_Script())
        script_result.status = random.choice(list(SCRIPT_STATUS_RUNNING))
        script_result.parameters = json.dumps({
            'storage': {
                'value': {
                    'name': factory.make_name('name')
                    }
                }
            })
        script_result.save()

        latest_event = Event.objects.last()
        self.assertEqual(
            (
                EVENT_TYPES.RUNNING_TEST,
                EVENT_DETAILS[
                    EVENT_TYPES.RUNNING_TEST].description,
                "%s on %s" % (script_result.name, script_result.parameters.get(
                    'storage').get('value').get('name')),
            ),
            (
                latest_event.type.name,
                latest_event.type.description,
                latest_event.description,
            ))

    def test__script_did_not_complete_emits_event(self):

        script_result = factory.make_ScriptResult(
            status=SCRIPT_STATUS.RUNNING, script_set=factory.make_ScriptSet(
                result_type=RESULT_TYPE.TESTING), script=factory.make_Script())
        script_result.status = random.choice(list(
            SCRIPT_STATUS_FAILED.union({SCRIPT_STATUS.ABORTED})))
        script_result.save()

        latest_event = Event.objects.last()
        self.assertEqual(
            (
                EVENT_TYPES.SCRIPT_DID_NOT_COMPLETE,
                EVENT_DETAILS[
                    EVENT_TYPES.SCRIPT_DID_NOT_COMPLETE].description,
                "%s %s" % (script_result.name, SCRIPT_STATUS_CHOICES[
                    script_result.status][1].lower()),
            ),
            (
                latest_event.type.name,
                latest_event.type.description,
                latest_event.description,
            ))

    def test__script_changed_status_emits_event(self):

        old_status = SCRIPT_STATUS.RUNNING
        script_result = factory.make_ScriptResult(
            status=old_status, script_set=factory.make_ScriptSet(
                result_type=RESULT_TYPE.COMMISSIONING),
            script=factory.make_Script())
        new_status = SCRIPT_STATUS.PASSED
        script_result.status = new_status
        script_result.save()

        latest_event = Event.objects.last()
        self.assertEqual(
            (
                EVENT_TYPES.SCRIPT_RESULT_CHANGED_STATUS,
                EVENT_DETAILS[
                    EVENT_TYPES.SCRIPT_RESULT_CHANGED_STATUS].description,
                "%s changed status from '%s' to '%s'" % (
                    script_result.name, SCRIPT_STATUS_CHOICES[old_status][1],
                    SCRIPT_STATUS_CHOICES[new_status][1]),
            ),
            (
                latest_event.type.name,
                latest_event.type.description,
                latest_event.description,
            ))

    def test__install_log_emits_event(self):

        old_status = SCRIPT_STATUS.RUNNING
        script_result = factory.make_ScriptResult(
            status=old_status, script_set=factory.make_ScriptSet(
                result_type=RESULT_TYPE.COMMISSIONING),
            script=factory.make_Script(name=CURTIN_INSTALL_LOG))
        script_result.status = SCRIPT_STATUS.PASSED
        script_result.script_set.node.netboot = False
        script_result.save()

        latest_event = Event.objects.last()
        self.assertEqual(
            (
                EVENT_TYPES.REBOOTING,
                EVENT_DETAILS[EVENT_TYPES.REBOOTING].description,
                "",
            ),
            (
                latest_event.type.name,
                latest_event.type.description,
                latest_event.description,
            ))
