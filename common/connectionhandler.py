#!/usr/bin/env python3
#
# Copyright (C) 2018  Maurice van der Pot <griffon26@kfk4ever.com>
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

import gevent.server
import gevent.queue
from gevent import socket

from common.tcpmessage import TcpMessageReader, TcpMessageWriter


class PeerConnectedMessage:
    def __init__(self, peer):
        self.peer = peer


class PeerDisconnectedMessage:
    def __init__(self, peer):
        self.peer = peer


class ConnectionReader:
    def __init__(self, sock):
        self.task_name = None
        self.task_id = None
        self.incoming_queue = None
        self.peer = None
        self.sock = sock

    def run(self):
        self.incoming_queue.put(PeerConnectedMessage(self.peer))

        try:
            while True:
                msg_bytes = self.receive()
                msg = self.decode(msg_bytes)
                msg.peer = self.peer
                self.incoming_queue.put(msg)

        except ConnectionResetError:
            print('%s(%s): disconnected' % (self.task_name, self.task_id))

        self.incoming_queue.put(PeerDisconnectedMessage(self.peer))
        print('%s(%s): signalled launcher; reader exiting' % (self.task_name, self.task_id))

    def decode(self, msg_bytes):
        """ Decode a message from a series of bytes """
        raise NotImplementedError('decode must be implemented in a subclass of ConnectionWriter')

    def receive(self):
        """ Receive a message from a socket and return the bytes that make up the message """
        raise NotImplementedError('receive must be implemented in a subclass of ConnectionWriter')


class TcpMessageConnectionReader(ConnectionReader):
    def __init__(self, sock, max_message_size = 0xFFFF):
        super().__init__(sock)
        self.tcp_reader = TcpMessageReader(sock, max_message_size = max_message_size)

    def receive(self):
        return self.tcp_reader.receive()


class ConnectionWriter:
    def __init__(self, sock):
        self.task_name = None
        self.task_id = None
        self.outgoing_queue = None
        self.sock = sock

    def run(self):
        while True:
            msg = self.outgoing_queue.get()
            if not isinstance(msg, PeerDisconnectedMessage):
                try:
                    msg_bytes = self.encode(msg)
                    self.send(msg_bytes)
                except ConnectionResetError:
                    # Ignore a closed connection here. The reader will notice
                    # it and send us the DisconnectedMessage to tell us that
                    # we can close the socket and terminate
                    pass
            else:
                break

        self.sock.close()
        print('%s(%s): writer exiting gracefully' % (self.task_name, self.task_id))

    def encode(self, msg):
        """ Encode msg into a series of bytes """
        raise NotImplementedError('encode must be implemented in a subclass of ConnectionWriter')

    def send(self, msg_bytes):
        """ Send the bytes that make up a message out over the socket """
        raise NotImplementedError('send must be implemented in a subclass of ConnectionWriter')


class TcpMessageConnectionWriter(ConnectionWriter):
    def __init__(self, sock, max_message_size = 0xFFFF):
        super().__init__(sock)
        self.tcp_writer = TcpMessageWriter(sock, max_message_size = max_message_size)

    def send(self, msg_bytes):
        return self.tcp_writer.send(msg_bytes)


class ClientInstance:
    def __init__(self):
        self.task_name = None
        self.task_id = None
        self.outgoing_queue = None

    def send(self, msg):
        self.outgoing_queue.put(msg)

    def disconnect(self):
        self.outgoing_queue.put(PeerDisconnectedMessage(self))


class ConnectionHandler:
    def __init__(self, task_name, address, port, incoming_queue):
        gevent.getcurrent().name = task_name
        self.task_name = task_name
        self.address = address
        self.port = port
        self.incoming_queue = incoming_queue

    def run(self):
        raise NotImplementedError('ConnectionHandler should not be used directly. '
                                  'Use Incoming/OutgoingConnectionHandler instead.')

    def create_connection_instances(self, sock, address):
        raise NotImplementedError('create_connection_instances must be implemented in a subclass of IncomingConnectionHandler')

    def _handle(self, sock, address):
        task_id = id(gevent.getcurrent())
        print('%s(%s): connected' % (self.task_name, task_id))

        reader, writer, peer = self.create_connection_instances(sock, address)

        if not isinstance(peer, ClientInstance) or \
            not isinstance(reader, ConnectionReader) or \
            not isinstance(writer, ConnectionWriter):
            raise TypeError('create_connection_instances should return a 3-tuple of instances of subclasses '
                            'of ConnectionReader, ConnectionWriter and ClientInstance respectively')

        if type(peer) is ClientInstance:
            raise TypeError('You should create an instance of a specific subclass of ClientInstance in '
                            'create_connection_instances not an instance of ClientInstance itself, '
                            'because the instance will be sent as part of Connected/Disconnected '
                            'messages and the type is the only way to distinguish between messages '
                            'from different IncomingConnectionHandlers.')

        outgoing_queue = gevent.queue.Queue()

        peer.task_id = task_id
        peer.task_name = self.task_name
        peer.outgoing_queue = outgoing_queue

        reader.task_id = task_id
        reader.task_name = self.task_name
        reader.incoming_queue = self.incoming_queue
        reader.peer = peer

        writer.task_id = task_id
        writer.task_name = self.task_name
        writer.outgoing_queue = outgoing_queue

        gevent.spawn(reader.run)
        writer.run()


class IncomingConnectionHandler(ConnectionHandler):
    def run(self):
        server = gevent.server.StreamServer((self.address, self.port), self._handle)
        server.serve_forever()


class OutgoingConnectionHandler(ConnectionHandler):
    def run(self, retry_time = None):
        task_id = id(gevent.getcurrent())

        success = False
        while not success:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.connect((self.address, self.port))
                success = True
            except ConnectionRefusedError:
                sock.close()
                if retry_time is not None:
                    print('%s(%s): remote end is refusing connections. Reconnecting in %d seconds...' %
                          (self.task_name, task_id, retry_time))
                    gevent.sleep(retry_time)
                else:
                    break

        if success:
            self._handle(sock, (str(self.address), self.port))