"""No test talks to anything but this machine: a connection to any other address is refused before it is made (a
plugin under test may hold an address and a token that are someone's real ones)."""
import ipaddress
import socket


def local(address):
    try:
        return isinstance(address, tuple) and (address[0] == 'localhost' or ipaddress.ip_address(address[0]).is_loopback)
    except ValueError:
        return False


class Socket(socket.socket):
    def connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not local(address):
            raise OSError(f'the tests stay on this machine: no connection to {address[0]}')
        return super().connect(address)

    def connect_ex(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not local(address):
            return 111
        return super().connect_ex(address)


socket.socket = Socket
