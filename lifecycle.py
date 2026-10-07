"""Per-user Windows launch preference and single-instance window reopening."""
import getpass
from pathlib import Path
import re
import subprocess
import sys
import winreg
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from portable_config import instance_suffix

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
VALUE_NAME = 'Voxlet'
SERVER_NAME = 'VoxletDesktop-' + re.sub(r'[^a-zA-Z0-9_-]', '_', getpass.getuser())+'-'+instance_suffix()


def startup_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
        return True
    except OSError:
        return False


def set_startup(enabled, executable=None, script=None):
    command = [str(executable or sys.executable)]
    if script is not None:
        command.append(str(script))
    elif not getattr(sys, 'frozen', False):
        command.append(str(Path(__file__).with_name('app.py')))
    command.append('--background')
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(command))
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass


def notify_running(command='show', server_name=SERVER_NAME):
    socket = QLocalSocket()
    socket.connectToServer(server_name)
    if not socket.waitForConnected(700):
        return False
    written=socket.write((command + '\n').encode('ascii'))
    sent = written>0 and (socket.bytesToWrite()==0 or socket.waitForBytesWritten(700))
    socket.disconnectFromServer()
    return sent


class InstanceServer:
    def __init__(self, parent, show, quit_app, server_name=SERVER_NAME):
        self.server = QLocalServer(parent)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        self.connections = set()
        self.show, self.quit_app = show, quit_app
        QLocalServer.removeServer(server_name)
        if not self.server.listen(server_name):
            raise RuntimeError('Could not create the Voxlet window connection.')
        self.server.newConnection.connect(self.accept)

    def accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            self.connections.add(socket)
            socket.readyRead.connect(lambda socket=socket: self.receive(socket))
            socket.disconnected.connect(lambda socket=socket: self.discard(socket))
            self.receive(socket)

    def receive(self, socket):
        while socket.canReadLine():
            command = bytes(socket.readLine()).decode('ascii', errors='ignore').strip()
            if command == 'show':
                self.show()
            elif command == 'quit':
                self.quit_app()

    def discard(self, socket):
        self.connections.discard(socket)
        try:socket.deleteLater()
        except RuntimeError:pass  # The owning window may already be destroying its children.

    def close(self):
        self.server.close()
        for socket in list(self.connections):
            socket.abort()
