import os, sys, random, threading, time, glob
import warnings
from compat import StringIO

# Loosely based on what unoconv does, Thanks Dag!
searchpaths = glob.glob('/opt/openoffice.org*/basis*/program')

try:
    import uno
except ImportError:
    uno = None
    for path in searchpaths:
        if not os.path.exists(os.path.join(path, 'uno.py')):
            continue
        # We found our directory...
        sys.path.insert(0, path)
        try:
            import uno
            if not sys.executable.startswith(path):
                warnings.warn('You are not running the python interpreter from with OOo. Bad things might happen real soon...')
            os.environ['LD_LIBRARY_PATH'] = path + os.pathsep + os.environ.get('LD_LIBRARY_PATH', '')
            break
        except ImportError:
            sys.path.remove(path)
    if uno is None:
        raise Exception('Document thumbnailing requires OO.o/LibreOffice and python-uno')

from unohelper import Base, systemPathToFileUrl
from com.sun.star.beans import PropertyValue
from com.sun.star.connection import NoConnectException
from com.sun.star.io import IOException, XOutputStream

# Taken from example:
# http://www.openoffice.org/udk/python/samples/ooextract.py
# Also very useful for PDF specifics:
# http://user.services.openoffice.org/en/forum/viewtopic.php?f=44&t=1804

SOFFICE_INSTRUCTIONS = """
# To run in Foreground:
$ soffice --accept="socket,host=localhost,port=2002;urp;"

# To run headless:
$ soffice --accept="socket,host=localhost,port=2002;urp;" --headless --invisible --nocrashreport --nodefault --nofirststartwizard --nologo --norestore

# Then set environment:
$ export UNO_CONNECTION="uno:socket,host=localhost,port=2002;urp;StarOffice.ComponentContext"
"""

def help(message):
    print >> sys.stderr, message
    print >> sys.stderr, SOFFICE_INSTRUCTIONS
    raise SystemExit()


class OutputStream( Base, XOutputStream ):
    """A simple stream that is compatible with UNO XOutputStream and can
    also return a StringIO object containing what was written to it."""
    def __init__(self):
        self.closed = 0
        self.stream = StringIO()

    def closeOutput(self):
        self.closed = 1

    def writeBytes(self, seq):
        self.stream.write(seq.value)

    def getStream(self):
        self.stream.seek(0)
        return self.stream

    def flush(self):
        pass


class Client(object):
    """An UNO client."""
    def __init__(self, connection):
        context = uno.getComponentContext()
        manager = context.ServiceManager
        resolver = manager.createInstanceWithContext(
            'com.sun.star.bridge.UnoUrlResolver',
            context,
        )
        try:
            context = resolver.resolve(connection)
        except NoConnectException:
            help('Could not connect to soffice. Is it running?')
        manager = context.ServiceManager
        self.desktop = manager.createInstanceWithContext(
            'com.sun.star.frame.Desktop',
            context,
        )

    def export_to_pdf(self, path):
        stream = OutputStream()
        props_out = (
            PropertyValue('FilterName', 0, 'writer_pdf_Export', 0),
            PropertyValue('Overwrite', 0, True, 0),
            PropertyValue('OutputStream', 0, stream, 0),
            PropertyValue('PageRange', 0, 1, 0),
        )
        props_in = (
            PropertyValue('Hidden', 0, True, 0),
        )
        document = None
        try:
            pathUrl = systemPathToFileUrl(os.path.abspath(path))
            document = self.desktop.loadComponentFromURL(pathUrl, '_blank', 0, props_in)
            document.storeToURL('private:stream', props_out)
        finally:
            if document:
                document.dispose()
        return stream

    def close(self):
        pass


class PooledClient(Client):
    """An UNO client that is able to track if it is in use or not. It is important that
    any client obtained be closed by the caller. Otherwise, it will not be available for
    reuse."""
    def __init__(self, pool, connection):
        super(PooledClient, self).__init__(connection)
        self.pool = pool
        self.in_use = threading.Event()
        # Track usage, in the future, we may purge old/unused clients.
        self.last_used = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *args):
        self.close()

    def open(self):
        self.pool.lock.acquire()
        try:
            self.last_used = time.time()
            self.in_use.set()
        finally:
            self.pool.lock.release()
        return self

    def close(self):
        self.pool.lock.acquire()
        try:
            self.in_use.clear()
        finally:
            self.pool.lock.release()


class Pool(object):
    """Pools UNO clients, tracking when each is in use. New UNO clients
    will be created as needed to fulfill requests."""
    def __init__(self):
        # Keep a dictionary of clients keyed on the office instance they connect to.
        self.clients = {}
        self.lock = threading.RLock()

    def client(self, connection):
        self.lock.acquire()
        try:
            clients = self.clients.setdefault(connection, [])
            unused = filter(lambda x: not x.in_use.isSet(), clients)
            if unused:
                # Select a random unused client:
                client = random.choice(unused)
            else:
                # Add a new client to the pool
                client = PooledClient(self, connection)
                clients.append(client)
            client.open()
            return client
        finally:
            self.lock.release()


pool = Pool()
"The default UNO client pool."

def client(connection=None):
    "Get an unused UNO client from the default pool."
    global pool
    if connection is None:
        connection = os.environ.get('UNO_CONNECTION', None)
    if connection is None:
        help('No connection information provided.')
    return pool.client(connection)
