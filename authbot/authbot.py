#!/usr/bin/env python3
#
# Copyright (C) 2019  Maurice van der Pot <griffon26@kfk4ever.com>
#
# This file is part of taserver
#
# taserver is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# taserver is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with taserver.  If not, see <http://www.gnu.org/licenses/>.
#

import base64
from functools import wraps
import gevent
import gevent.subprocess as sp
import inspect
import itertools
import json
import logging
import time
import urllib.request as urlreq

from common.datatypes import *
from common.errors import FatalError
from common.connectionhandler import PeerConnectedMessage, PeerDisconnectedMessage
from common.loginprotocol import LoginProtocolMessage
from common.statetracer import statetracer, TracingDict

from .hirezloginserverhandler import HirezLoginServer


class LoginFailedError(FatalError):
    def __init__(self):
        super().__init__('Failed to login with the specified credentials. Check your authbot.ini')


def handles(packet):
    """
    A decorator that defines a function as a handler for a certain packet
    :param packet: the packet being handled by the function
    """

    def real_decorator(func):
        func.handles_packet = packet

        @wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        return wrapper

    return real_decorator


def xor_password_hash(password_hash, salt):
    salt_nibbles = []
    for value in salt:
        salt_nibbles.append(value >> 4)
        salt_nibbles.append(value & 0x0F)

    xor_values = [(value if value <= 9 else 0x47 + value) for value in salt_nibbles]

    xor_pattern = [
        xor_values[6], 0,
        xor_values[7], 0,
        xor_values[4], 0,
        xor_values[5], 0,
        xor_values[2], 0,
        xor_values[3], 0,
        xor_values[0], 0,
        xor_values[1], 0,
        0, 0,
        xor_values[10], 0,
        xor_values[11], 0,
        xor_values[8], 0,
        xor_values[9], 0,
        0, 0,
        xor_values[14], 0,
        xor_values[15], 0,
        xor_values[12], 0,
        xor_values[13], 0,
        0, 0,
        xor_values[16], 0,
        xor_values[17], 0,
        xor_values[18], 0,
        xor_values[19], 0,
        0, 0,
        xor_values[20], 0,
        xor_values[21], 0,
        xor_values[22], 0,
        xor_values[23], 0,
        xor_values[24], 0,
        xor_values[25], 0,
        xor_values[26], 0,
        xor_values[27], 0,
        xor_values[28], 0,
        xor_values[29], 0,
        xor_values[30], 0,
        xor_values[31], 0,
    ]

    xored_password_hash = [
        p ^ x for p, x in itertools.zip_longest(password_hash, xor_pattern, fillvalue = 0)
    ]

    return bytes(xored_password_hash)


@statetracer()
class AuthBot:
    def __init__(self, config, incoming_queue):
        gevent.getcurrent().name = 'authbot'

        self.logger = logging.getLogger(__name__)
        self.incoming_queue = incoming_queue
        self.login_server = None
        self.login_name = config['login_name']
        self.display_name = None
        self.password_hash = base64.b64decode(config['password_hash'])
        self.last_requests = {}

        self.message_handlers = {
            PeerConnectedMessage: self.handle_peer_connected,
            PeerDisconnectedMessage: self.handle_peer_disconnected,
            LoginProtocolMessage: self.handle_login_protocol_message
        }

    def run(self):
        self.send_and_schedule_keepalive_message()
        while True:
            for message in self.incoming_queue:
                handler = self.message_handlers[type(message)]
                handler(message)

    def send_and_schedule_keepalive_message(self):
        if self.login_server and self.display_name:
            self.login_server.send(
                a0070().set([
                    m009e().set(MESSAGE_PRIVATE),
                    m02e6().set("Ah, ha, ha, ha, stayin' alive, stayin' alive"),
                    m034a().set(self.display_name),
                    m0574()
                ])
            )
        gevent.spawn_later(60, self.send_and_schedule_keepalive_message)

    def handle_peer_connected(self, msg):
        assert isinstance(msg.peer, HirezLoginServer)
        assert self.login_server is None
        self.logger.info('authbot: hirez login server connected')
        self.login_server = msg.peer
        self.login_server.send(
            a01bc().set([
                m049e(),
                m0489()
            ])
        )

    def handle_peer_disconnected(self, msg):
        assert isinstance(msg.peer, HirezLoginServer)
        assert self.login_server is msg.peer
        msg.peer.disconnect()
        self.logger.info('authbot: hirez login server disconnected')
        self.login_server = None

    def handle_login_protocol_message(self, msg):
        msg.peer.last_received_seq = msg.clientseq

        requests = ' '.join(['%04X' % req.ident for req in msg.requests])

        for request in msg.requests:
            methods = [
                func for name, func in inspect.getmembers(self) if
                getattr(func, 'handles_packet', None) == type(request)
            ]
            if not methods:
                self.logger.warning("No handler found for request %s" % request)
                return

            if len(methods) > 1:
                raise ValueError("Duplicate handlers found for request")

            methods[0](request)

    @handles(packet=a01bc)
    def handle_a01bc(self, request):
        pass

    @handles(packet=a0197)
    def handle_a0197(self, request):
        self.login_server.send(a003a())

    @handles(packet=a003a)
    def handle_a003a(self, request):
        salt = request.findbytype(m03e3).value
        self.login_server.send(
            a003a().set([
                m0056().set(xor_password_hash(self.password_hash, salt)),
                m0494().set(self.login_name),
                m0671(),
                m0671(),
                m0672(),
                m0673(),
                m0677(),
                m0676(),
                m0674(),
                m0675(),
                m0434(),
                m049e()
            ])
        )

    @handles(packet=a003d)
    def handle_a003d(self, request):
        display_name_field = request.findbytype(m034a)
        if display_name_field:
            self.display_name = display_name_field.value
        else:
            self.logger.info('authbot: login to HiRez server failed.')
            raise LoginFailedError()

    @handles(packet=a0070)
    def handle_chat(self, request):
        assert self.display_name is not None

        message_type = request.findbytype(m009e).value
        message_text = request.findbytype(m02e6).value
        sender_name = request.findbytype(m02fe).value

        if message_type == MESSAGE_PRIVATE and sender_name != self.display_name:
            reply = self.process_chat_message(sender_name, message_text)
            self.login_server.send(
                a0070().set([
                    m009e().set(MESSAGE_PRIVATE),
                    m02e6().set(reply),
                    m034a().set(sender_name),
                    m0574()
                ])
            )

    def process_chat_message(self, sender_name, message_text):

        self.last_requests = {k: v for k, v in self.last_requests.items() if time.time() - v < 5}

        generic_error_reply = 'Something went wrong. Please contact the administrator of this bot or try again later.'

        if message_text == 'authcode':
            try:
                if sender_name in self.last_requests:
                    return 'Jeez.. I just gave you an authcode five seconds ago! Stop being so pushy!'
                else:
                    output = sp.run('getauthcode.py %s' % sender_name,
                                    shell=True, check=True, capture_output=True, text=True).stdout
                    if output.startswith('Received authcode'):
                        authcode = output.split()[2]
                        self.last_requests[sender_name] = time.time()
                        return 'Your authcode is %s' % authcode
                    else:
                        self.logger.error('authbot: unexpected output from getauthcode.py: %s' % output)
                        return generic_error_reply

            except sp.CalledProcessError as e:
                error_message = e.stderr if e.stderr else str(e)
                self.logger.error('authbot: failed to run getauthcode.py: %s' % error_message)
                return generic_error_reply

        elif message_text == 'status':
            server_info = json.loads(urlreq.urlopen('http://localhost:9080/status').read())

            try:
                return 'There are %s players and %s servers online' % (server_info['online_players'],
                                                                       server_info['online_servers'])
            except KeyError as e:
                self.logger.error('authbot: invalid status received from server: %s' % e)
                return generic_error_reply


        else:
            return 'Hi %s. Valid commands are "authcode" or "status".' % sender_name

def handle_authbot(config, incoming_queue):
    authbot = AuthBot(config, incoming_queue)
    # launcher.trace_as('authbot')
    authbot.run()
