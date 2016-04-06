# Copyright (C) 2016 Julian Metzler

"""
This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

"""
This file contains the classes needed to operate a server which controls multiple flipdot displays.
The server operates on a simple JSON-based protocol. The full protocol specification can be found
in the SERVER_PROTOCOL.md file.
The server runs as two threads; one to listen for messages and one to control the displays.
"""

import json
import socket
import threading
import traceback

from .controller import *
from .graphics import *
from .utils import *

def receive_message(sock):
    # Receive and parse an incoming message (prefixed with its length)
    try:
        length = int(sock.recv(5))
        raw_data = bytearray()
        l = 0
        while l < length:
            part_data = sock.recv(4096)
            raw_data += part_data
            l += len(part_data)
        message = json.loads(raw_data.decode('utf-8'))
    except:
        raise
    return message

def send_message(sock, data):
    # Build and send a message (prefixed with its length)
    raw_data = json.dumps(data)
    length = len(raw_data)
    message = "{0:05d}{1}".format(length, raw_data)
    sock.sendall(message.encode('utf-8'))

def discard_message(sock):
    sock.setblocking(False)
    try:
        while True:
            sock.recv(1024)
    except socket.error:
        pass
    finally:
        sock.setblocking(True)

class FlipdotServer(object):
    """
    One serial port for all displays, display selection via multiplexing, adress set by DTR and RTS lines.
    The 'display_hwconfig' parameter is a dictionary mapping display IDs to display hardware configurations.

    Example:

    {
        'front': {
            width': 126,
           'height': 16,
           'address': 0
        },
        'side': {
            width': 84,
           'height': 16,
           'address': 1
        }
    }
    """

    CONFIG_FILE = ".server_config"

    def __init__(self, serial_port, display_hwconfig, port = 1810, allowed_ip_match = None, verbose = True):
        self.running = False
        self.port = port
        self.allowed_ip_match = allowed_ip_match
        self.verbose = verbose
        self.ser = get_serial_port(serial_port)
        self.displays = {}
        self.config = {}
        self.update_data = {}
        self.current_message = {}
        self.current_bitmap = {}
        self.display_hwconfig = display_hwconfig
        for id, display in display_hwconfig.items():
            controller = FlipdotController(self.ser, display['width'], display['height'], using_mux = True, mux_port = display['address'])
            self.displays[id] = {
                'address': display['address'],
                'controller': controller,
                'graphics': FlipdotGraphics(controller)
            }
            self.config[id] = {
                'backlight': False,
                'inverting': False,
                'active': True
            }
            self.update_data[id] = {
                'config_keys_changed': [],
                'message_changed': False,
                'sequence_cur_pos': None,
                'sequence_last_switched': None,
                'dynamic_submessages': {}
            }
            self.current_message[id] = None
            self.current_bitmap[id] = None

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Prevent having to wait between reconnects
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener_thread = threading.Thread(target = self.network_listen)

    def output_verbose(self, text):
        if self.verbose:
            print(text)
    
    def run(self):
        self.output_verbose("Starting server...")
        self.load_config()
        self.running = True
        self.listener_thread.start()
        self.control_loop()
    
    def stop(self):
        self.output_verbose("Stopping server...")
        self.save_config()
        self.running = False

    def save_config(self):
        self.output_verbose("Saving configuration to '{0}'...".format(self.CONFIG_FILE))

        config_save = {
            'config': [],
            'messages': []
        }
        
        for display, config in self.config.items():
            config_save['config'].append({
                'display': display,
                'type': 'control',
                'message': config
            })

        for display, message in self.current_message.items():
            config_save['messages'].append({
                'display': display,
                'type': 'data',
                'message': message
            })

        with open(self.CONFIG_FILE, 'w') as f:
            json.dump(config_save, f, indent = 2)

    def load_config(self):
        self.output_verbose("Loading configuration from '{0}'...".format(self.CONFIG_FILE))
        try:
            with open(self.CONFIG_FILE, 'r') as f:
                config_save = json.load(f)

            for message in config_save['config'] + config_save['messages']:
                self.process_message(message)
        except (IOError, OSError, ValueError):
            self.output_verbose("'{0}' not found or invalid.".format(self.CONFIG_FILE))
    
    def set_config(self, display, key, value):
        try:
            func = getattr(self.displays[display]['controller'], "set_{0}".format(key))
            func(value)
        except:
            traceback.print_exc()
            return False
        return True
    
    def network_listen(self):
        self.socket.bind(('', self.port))
        self.socket.settimeout(5.0)
        self.output_verbose("Listening on port {0}".format(self.port))
        self.socket.listen(1)
        
        try:
            while self.running:
                try:
                    # Wait for someone to connect
                    conn, addr = self.socket.accept()
                    ip, port = addr
                    if self.allowed_ip_match is not None and not ip.startswith(self.allowed_ip_match):
                        self.output_verbose("Discarding message from {0} on port {1}".format(*addr))
                        discard_message(conn)
                        continue
                    
                    self.output_verbose("Receiving message from %s on port %i" % addr)
                    # Receive the message(s)
                    messages = receive_message(conn)
                    if messages is None:
                        # We received an invalid message, just discard it
                        continue
                    
                    if type(messages) not in (list, tuple):
                        messages = [messages]
                    
                    reply = {'success': True}
                    for message in messages:
                        reply = self.process_message(message)
                        if not reply.get('success'):
                            break
                    
                    if reply:
                        send_message(conn, reply)
                except socket.timeout: # Nothing special, just renew the socket every few seconds
                    pass
                except KeyboardInterrupt:
                    raise
                except:
                    traceback.print_exc()
        except KeyboardInterrupt:
            self.stop()
        finally:
            self.socket.close()
    
    def process_message(self, message):
        success = True
        error = None
        
        if message['type'] == 'control':
            display = message['display']
            for key, value in message['message'].items():
                if key in self.config[display]:
                    if self.config[display][key] != value:
                        self.config[display][key] = value
                        self.update_data[display]['config_keys_changed'].append(key)
                else:
                    success = False
                    error = "Invalid configuration option: {0}".format(key)
                    break
            if success:
                self.save_config()
            return {'success': success, 'error': error}
        elif message['type'] == 'data':
            display = message['display']
            self.current_message[display] = message['message']
            self.update_data[display]['message_changed'] = True
            if success:
                self.save_config()
            return {'success': success, 'error': error}
        elif message['type'] == 'query-config':
            displays = message.get('displays')
            keys = message.get('keys')
            if displays is None:
                displays = self.displays.keys()
            
            reply = {}
            for display in displays:
                config = self.config[display]
                reply_config = {}
                for key, value in config.items():
                    if keys is None or key in keys:
                        reply_config[key] = value
                reply[display] = reply_config
            return reply
        elif message['type'] == 'query-hwconfig':
            return self.display_hwconfig
        elif message['type'] == 'query-message':
            displays = message.get('displays')
            if displays is None:
                displays = self.displays.keys()
            
            reply = dict(((display, self.current_message[display]) for display in displays))
            return reply
        elif message['type'] == 'query-bitmap':
            displays = message.get('displays')
            if displays is None:
                displays = self.displays.keys()
            
            reply = dict(((display, self.current_bitmap[display]) for display in displays))
            return reply
        else:
            success = False
            error = "Invalid message type: {0}".format(message.get('type'))
            return {'success': success, 'error': error}
        
        # This should never be called
        return {'success': success, 'error': error}
    
    def control_loop(self):
        last_check_dt = datetime.datetime.now()
        while self.running:
            try:
                now_time = time.time()
                now_dt = datetime.datetime.now()
                for display, message in self.current_message.items():
                    update_data = self.update_data[display]
                    try:
                        # Process configuration changes
                        for key in update_data['config_keys_changed']:
                            try:
                                self.set_config(display, key, self.config[display][key])
                            except MatrixError as err:
                                print("Error setting '{0}' to '{1}' on display '{2}': {3}".format(key, self.config[display][key], display, err))
                        update_data['config_keys_changed'] = []
                        
                        # If only config changes were made and no message was sent, commit the changes and we're done
                        if message is None:
                            time.sleep(0.25)
                            continue
                        
                        # A message has been changed
                        if update_data['message_changed']:
                            if message['type'] == 'sequence':
                                update_data['sequence_cur_pos'] = 0
                                update_data['sequence_last_switched'] = now_time
                            elif message['type'] == 'single':
                                update_data['sequence_cur_pos'] = 0
                                update_data['sequence_last_switched'] = None
                        
                        # If we have a sequence message, get the current sub-message and check if it has expired
                        if message['type'] == 'sequence':
                            actual_message = message['messages'][update_data['sequence_cur_pos']]
                            sequence_needs_switching = now_time - update_data['sequence_last_switched'] >= (actual_message.get('duration', message['interval']) or message['interval'])
                        else:
                            actual_message = message
                            sequence_needs_switching = False

                        # If the submessage has expired, switch to the next one
                        if sequence_needs_switching:
                            if update_data['sequence_cur_pos'] == len(message['messages']) - 1:
                                update_data['sequence_cur_pos'] = 0
                            else:
                                update_data['sequence_cur_pos'] += 1
                            actual_message = message['messages'][update_data['sequence_cur_pos']]
                            update_data['sequence_last_switched'] = now_time
                        
                        # Register dynamic submessages
                        if sequence_needs_switching or update_data['message_changed']:
                            for index, submessage in enumerate(actual_message['submessages']):
                                refresh_interval = submessage.get('refresh_interval', 0)
                                if refresh_interval:
                                    update_data['dynamic_submessages'][index] = [refresh_interval, now_time]
                                else:
                                    try:
                                        update_data['dynamic_submessages'].pop(index)
                                    except KeyError:
                                        pass
                        
                        # Check if any dynamic messages need to be updated (the first message to need updating causes all messages to be updated)
                        dynamic_message_changed = False
                        if update_data['dynamic_submessages']:
                            for index, (refresh_interval, last_refresh) in update_data['dynamic_submessages'].items():
                                if refresh_interval == 'minute':
                                    if now_dt.minute != last_check_dt.minute:
                                        dynamic_message_changed = True
                                        break
                                elif now_time - last_refresh >= refresh_interval:
                                    dynamic_message_changed = True
                                    break
                        
                        # Determine whether the bitmap needs to be refreshed (Message changed, dynamic message needs refresh or submessage expired)
                        needs_refresh = update_data['message_changed'] or dynamic_message_changed or sequence_needs_switching
                        #print(update_data['message_changed'], dynamic_message_changed, sequence_needs_switching)

                        # If a refresh is required, determine what to do
                        if needs_refresh:
                            for index, submessage in enumerate(actual_message['submessages']):
                                if submessage['type'] == 'bitmap':
                                    self.displays[display]['graphics'].bitmap(self.displays[display]['graphics'].bitmap_to_image(submessage['bitmap']), x = 0, y = 0)
                                elif submessage['type'] == 'graphics':
                                    func = getattr(self.displays[display]['graphics'], submessage['func'])
                                    try:
                                        func(**submessage['params'])
                                    except:
                                        traceback.print_exc()
                                    if index in update_data['dynamic_submessages']:
                                        update_data['dynamic_submessages'][index][1] = now_time
                            self.current_bitmap[display] = self.displays[display]['graphics'].get_bitmap()
                            try:
                                self.displays[display]['graphics'].commit()
                            except MatrixError as err:
                                print("Error committing changes to display '{0}': {1}".format(display, err))
                        update_data['message_changed'] = False
                    except Exception as err:
                        traceback.print_exc()
                    finally:
                        update_data['message_changed'] = False
                last_check_dt = now_dt
                time.sleep(0.25)
            except KeyboardInterrupt:
                self.stop()
            except:
                traceback.print_exc()



class FlipdotClient(object):
    def __init__(self, host, port = 1810, timeout = 3.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.queue = []
        self.display_submessages = {}

    def __getattr__(self, key):
        """
        This is used to map method calls that are not explicitly defined here to the corresponding graphics calls to ease access to them
        """

        def _graphics_mapper(display, **kwargs):
            self.add_graphics_submessage(display, key, **kwargs)
            self.commit()
        return _graphics_mapper

    def send_raw_message(self, message, expect_reply = True):
        reply = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect((self.host, self.port))
            send_message(sock, message)
            
            if expect_reply:
                reply = receive_message(sock)
        finally:
            sock.close()
        return reply
    
    def clear_queue(self):
        self.queue = []
        self.display_submessages = {}
    
    def commit(self):
        for display, submessages in self.display_submessages.items():
            self.add_single_message(display, submessages)
        if self.queue:
            reply = self.send_raw_message(self.queue)
            if reply.get('success'):
                self.clear_queue()
            return reply
        else:
            return False

    ######################### LEVEL 1 MESSAGES
    
    def build_data_message(self, display, message):
        return {'type': 'data', 'display': display, 'message': message}
    
    def build_control_message(self, display, message):
        return {'type': 'control', 'display': display, 'message': message}
    
    def build_config_query_message(self, displays, keys):
        return {'type': 'query-config', 'displays': displays, 'keys': keys}
    
    def build_hwconfig_query_message(self):
        return {'type': 'query-hwconfig'}
    
    def build_message_query_message(self, displays):
        return {'type': 'query-message', 'displays': displays}
    
    def build_bitmap_query_message(self, displays):
        return {'type': 'query-bitmap', 'displays': displays}

    ######################### LEVEL 2 MESSAGES

    def build_single_message(self, submessages, duration = None):
        return {'type': 'single', 'duration': duration, 'submessages': submessages}

    def build_sequence_message(self, messages, interval = None):
        # The interval parameter is used as the duration for messages in the sequence that don't have their own duration set.
        for message in messages:
            if message['type'] == 'sequence':
                raise ValueError("Nesting of sequence messages is not allowed")
            if not message.get('duration'):
                if not interval:
                    raise ValueError("Sequence contains message with no specified duration, but no default duration was given")
        return {'type': 'sequence', 'interval': interval, 'messages': messages}

    ######################### SUBMESSAGES

    def build_bitmap_submessage(self, bitmap):
        return {'type': 'bitmap', 'bitmap': bitmap}

    def build_graphics_submessage(self, func, refresh_interval = None, **params):
        return {'type': 'graphics', 'func': func, 'refresh_interval': refresh_interval, 'params': params}

    ######################### LEVEL 1 MESSAGES
    
    def add_data_message(self, *args, **kwargs):
        self.queue.append(self.build_data_message(*args, **kwargs))

    ######################### LEVEL 2 MESSAGES
    
    def add_single_message(self, display, submessages, duration = None):
        self.queue.append(self.build_data_message(display, self.build_single_message(submessages, duration)))
    
    def add_sequence_message(self, display, messages, interval = None):
        self.queue.append(self.build_data_message(display, self.build_sequence_message(messages, interval)))

    ######################### SUBMESSAGES

    def add_bitmap_submessage(self, display, bitmap):
        queue = self.display_submessages.get(display, [])
        queue.append(self.build_bitmap_submessage(bitmap))
        self.display_submessages[display] = queue

    def add_graphics_submessage(self, display, func, **kwargs):
        queue = self.display_submessages.get(display, [])
        queue.append(self.build_graphics_submessage(func, **kwargs))
        self.display_submessages[display] = queue

    #########################
    
    def get_config(self, displays = None, keys = None):
        return self.send_raw_message(self.build_config_query_message(displays, keys))
    
    def get_hwconfig(self):
        return self.send_raw_message(self.build_hwconfig_query_message())
    
    def get_message(self, displays = None):
        return self.send_raw_message(self.build_message_query_message(displays))
    
    def get_bitmap(self, displays):
        return self.send_raw_message(self.build_bitmap_query_message(displays))

    #########################
    
    def set_config(self, display, config):
        self.queue.append(self.build_control_message(display, config))
    
    def set_backlight(self, display, state):
        """
        state: True / False
        """
        return self.set_config(display, {'backlight': state})
    
    def set_inverting(self, display, state):
        """
        state: True / False
        """
        return self.set_config(display, {'inverting': state})
    
    def set_active(self, display, state):
        """
        state: True / False
        """
        return self.set_config(display, {'active': state})