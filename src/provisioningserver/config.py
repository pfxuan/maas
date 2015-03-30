# Copyright 2012-2014 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""MAAS Provisioning Configuration.

Configuration for most elements of a Cluster Controller can be obtained
through this module's `Config` validator class. At the time of writing the
exceptions are the `CLUSTER_UUID` and `MAAS_URL` environment variables (see
`provisioningserver.cluster_config`).

It's pretty simple. Typical usage is::

  >>> config = Config.load_from_cache()
  {...}

This reads in a configuration file from `Config.DEFAULT_FILENAME` (see a note
about that later). The configuration file is parsed as YAML, and a plain `dict`
is returned with configuration nested within it. The configuration is validated
at load time using `formencode`. The policy for validation is laid out in this
module; see the various `formencode.Schema` subclasses.

All configuration is optional, and a sensible default is provided in every
instance. When adding or changing settings bear this policy in mind, and also
that the defaults should be geared towards a system in production, and not a
development environment. The defaults can be obtained by calling
`Config.get_defaults()`.

An alternative to `Config.load_from_cache()` is `Config.load()`, which loads
and validates a configuration file while bypassing the cache.  See `Config` for
other useful functions.

`Config.DEFAULT_FILENAME` is a class property, so does not need to be
referenced via an instance of `Config`. It refers to the
``MAAS_PROVISIONING_SETTINGS`` environment variable in the first instance, but
has a sensible default too. You can write to this property and it will update
the environment so that child processes will also use the same configuration
filename. To revert to the default - i.e. erase the environment variable - you
can `del Config.DEFAULT_FILENAME`.

When testing, see `provisioningserver.testing.config.ConfigFixture` to
temporarily use a different configuration.

"""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
)

str = None

__metaclass__ = type
__all__ = [
    "BOOT_RESOURCES_STORAGE",
    "BootSources",
    "Config",
    "ConfigBase",
    "ConfigMeta",
]

from contextlib import (
    closing,
    contextmanager,
)
from copy import deepcopy
from getpass import getuser
import json
import os
from os import environ
import os.path
import re
from shutil import copyfile
import sqlite3
from threading import RLock

from formencode import (
    ForEach,
    Schema,
)
from formencode.api import NoDefault
from formencode.declarative import DeclarativeMeta
from formencode.validators import (
    Int,
    Invalid,
    is_validator,
    Number,
    RequireIfPresent,
    Set,
    String,
    UnicodeString,
    URL,
)
from lockfile import FileLock
from provisioningserver.path import get_tentative_path
from provisioningserver.utils.fs import (
    atomic_write,
    ensure_dir,
)
import yaml

# Path to the directory on the cluster controller where boot resources are
# stored.  This used to be configurable in bootresources.yaml, and may become
# configurable again in the future.
BOOT_RESOURCES_STORAGE = '/var/lib/maas/boot-resources/'


class Directory(UnicodeString):
    """A validator for a directory on the local filesystem.

    The directory must exist.
    """

    messages = dict(notDir="%(value)r does not exist or is not a directory")

    def validate_python(self, value, state=None):
        if os.path.isdir(value):
            return value
        else:
            raise Invalid(
                self.message("notDir", state, value=value),
                value, state)


class ExtendedURL(URL):
    """A validator URLs.

    This validator extends formencode.validators.URL by adding support
    for the general case of hostnames (i.e. hostnames containing numeric
    digits, hyphens, and hostnames of length 1), and ipv6 addresses with
    or without brackets.
    """

    url_re = re.compile(r'''
        ^(http|https)://
        (?:[%:\w]*@)?                              # authenticator
        (?:                                        # ip or domain
        (?P<ip>(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}
            (?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?))|
        (?P<ipv6>\[?(?:[A-F0-9]{1,4}:){7}[A-F0-9]{1,4}\]?)|
        (?P<domain>[a-z0-9][a-z0-9\-]{,62}\.)*     # subdomain
        (?P<tld>[a-z0-9\-]{1,63})  # tld or hostname
        )
        (?::[0-9]{1,5})?                           # port
        # files/delims/etc
        (?P<path>/[a-z0-9\-\._~:/\?#\[\]@!%\$&\'\(\)\*\+,;=]*)?
        $
    ''', re.I | re.VERBOSE)


class ConfigOops(Schema):
    """Configuration validator for OOPS options.

    Deprecated: MAAS no longer records OOPS reports. This remains here to
    avoid validation failures when using old versions of the cluster's
    configuration file.
    """

    if_key_missing = None

    directory = String(if_missing=b"")
    reporter = String(if_missing=b"")

    chained_validators = (
        RequireIfPresent("reporter", present="directory"),
    )


class ConfigBroker(Schema):
    """Configuration validator for message broker options.

    Deprecated: MAAS no longer uses a message broker. This remains here to
    avoid validation failures when using old versions of the cluster's
    configuration file.
    """

    if_key_missing = None

    host = String(if_missing=b"localhost")
    port = Int(min=1, max=65535, if_missing=5673)
    username = String(if_missing=getuser())
    password = String(if_missing=b"test")
    vhost = String(if_missing="/")


class ConfigTFTP(Schema):
    """Configuration validator for the TFTP service."""

    if_key_missing = None

    # Obsolete: old TFTP root directory.  This is retained for the purpose of
    # deriving new, Simplestreams-based import configuration from previously
    # imported boot images.
    # The last time this is needed is for upgrading an older cluster
    # controller to the Ubuntu 14.04 version of MAAS.  After installation of
    # the 14.04 version, this setting is never used.
    root = String(if_missing="/var/lib/maas/tftp")

    # TFTP root directory, managed by the Simplestreams-based import script.
    # The import script maintains "current" as a symlink pointing to the most
    # recent images.
    # XXX jtv 2014-05-22: Redundant with BOOT_RESOURCES_STORAGE.
    resource_root = String(
        if_missing=os.path.join(BOOT_RESOURCES_STORAGE, 'current/'))

    port = Int(min=1, max=65535, if_missing=69)
    generator = String(if_missing=b"http://localhost/MAAS/api/1.0/pxeconfig/")


class ConfigLegacyEphemeral(Schema):
    """Legacy `ephemeral` section in `pserv.yaml` prior to MAAS 1.5.

    This has been superseded by boot sources.
    It is still accepted in `pserv.yaml`, but not used.
    """
    if_key_missing = None
    images_directory = String(if_missing=None)
    releases = Set(if_missing=None)


class ConfigLegacyBoot(Schema):
    """Legacy `boot` section in `pserv.yaml` prior to MAAS 1.5.

    This has been superseded by boot sources.
    It is still accepted in `pserv.yaml`, but not used.
    """
    if_key_missing = None
    architectures = Set(if_missing=None)
    ephemeral = ConfigLegacyEphemeral


class ConfigRPC(Schema):
    """Configuration validator for the RPC service."""

    if_key_missing = None


class BootSourceSelection(Schema):
    """Configuration validator for boot source selection configuration."""

    if_key_missing = None

    os = String(if_missing="*")
    release = String(if_missing="*")
    arches = Set(if_missing=["*"])
    subarches = Set(if_missing=['*'])
    labels = Set(if_missing=['*'])


class BootSource(Schema):
    """Configuration validator for boot source configuration."""

    if_key_missing = None

    url = String(
        if_missing="http://maas.ubuntu.com/images/ephemeral-v2/releases/")
    keyring = String(
        if_missing="/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg")
    keyring_data = String(if_missing=None)
    selections = ForEach(
        BootSourceSelection,
        if_missing=[BootSourceSelection.to_python({})])


class ConfigBase:
    """Base configuration validator."""

    @classmethod
    def parse(cls, stream):
        """Load a YAML configuration from `stream` and validate."""
        return cls.to_python(yaml.safe_load(stream))

    @classmethod
    def load(cls, filename=None):
        """Load a YAML configuration from `filename` and validate."""
        if filename is None:
            filename = cls.DEFAULT_FILENAME
        with open(filename, "rb") as stream:
            return cls.parse(stream)

    @classmethod
    def _get_backup_name(cls, message, filename=None):
        if filename is None:
            filename = cls.DEFAULT_FILENAME
        return "%s.%s.bak" % (filename, message)

    @classmethod
    def create_backup(cls, message, filename=None):
        """Create a backup of the YAML configuration.

        The given 'message' will be used in the name of the backup file.
        """
        backup_name = cls._get_backup_name(message, filename)
        if filename is None:
            filename = cls.DEFAULT_FILENAME
        copyfile(filename, backup_name)

    @classmethod
    def save(cls, config, filename=None):
        """Save a YAML configuration to `filename`, or to the default file."""
        if filename is None:
            filename = cls.DEFAULT_FILENAME
        dump = yaml.safe_dump(config)
        atomic_write(dump, filename)

    _cache = {}
    _cache_lock = RLock()

    @classmethod
    def load_from_cache(cls, filename=None):
        """Load or return a previously loaded configuration.

        Keeps an internal cache of config files.  If the requested config file
        is not in cache, it is loaded and inserted into the cache first.

        Each call returns a distinct (deep) copy of the requested config from
        the cache, so the caller can modify its own copy without affecting what
        other call sites see.

        This is thread-safe, so is okay to use from Django, for example.
        """
        if filename is None:
            filename = cls.DEFAULT_FILENAME
        filename = os.path.abspath(filename)
        with cls._cache_lock:
            if filename not in cls._cache:
                with open(filename, "rb") as stream:
                    cls._cache[filename] = cls.parse(stream)
            return deepcopy(cls._cache[filename])

    @classmethod
    def flush_cache(cls, filename=None):
        """Evict a config file, or any cached config files, from cache."""
        with cls._cache_lock:
            if filename is None:
                cls._cache.clear()
            else:
                if filename in cls._cache:
                    del cls._cache[filename]

    @classmethod
    def field(target, *steps):
        """Obtain a field by following `steps`."""
        for step in steps:
            target = target.fields[step]
        return target

    @classmethod
    def get_defaults(cls):
        """Return the default configuration."""
        return cls.to_python({})


class ConfigMeta(DeclarativeMeta):
    """Metaclass for the root configuration schema."""

    envvar = None  # Set this in subtypes.
    default = None  # Set this in subtypes.

    def _get_default_filename(cls):
        # Avoid circular imports.
        from provisioningserver.utils import locate_config

        # Get the configuration filename from the environment. Failing that,
        # look for the configuration in its default locations.
        return environ.get(cls.envvar, locate_config(cls.default))

    def _set_default_filename(cls, filename):
        # Set the configuration filename in the environment.
        environ[cls.envvar] = filename

    def _delete_default_filename(cls):
        # Remove any setting of the configuration filename from the
        # environment.
        environ.pop(cls.envvar, None)

    DEFAULT_FILENAME = property(
        _get_default_filename, _set_default_filename,
        _delete_default_filename, doc=(
            "The default config file to load. Refers to "
            "`cls.envvar` in the environment."))


class Config(ConfigBase, Schema):
    """Configuration for the provisioning server."""

    class __metaclass__(ConfigMeta):
        envvar = "MAAS_PROVISIONING_SETTINGS"
        default = "pserv.yaml"

    if_key_missing = None

    logfile = String(if_empty=b"pserv.log", if_missing=b"pserv.log")
    oops = ConfigOops
    broker = ConfigBroker
    tftp = ConfigTFTP
    rpc = ConfigRPC
    boot = ConfigLegacyBoot


class BootSources(ConfigBase, ForEach):
    """Configuration for boot sources."""

    class __metaclass__(ConfigMeta):
        envvar = "MAAS_BOOT_SOURCES_SETTINGS"
        default = "sources.yaml"

    validators = [BootSource]


###############################################################################
# New configuration API follows.
###############################################################################


def touch(path, mode=0600):
    """Ensure that `path` exists."""
    os.close(os.open(path, os.O_CREAT | os.O_APPEND, mode))


class ConfigurationDatabase:
    """Store configuration in an sqlite3 database."""

    def __init__(self, database):
        self.database = database
        with self.cursor() as cursor:
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS configuration "
                "(id INTEGER PRIMARY KEY,"
                " name TEXT NOT NULL UNIQUE,"
                " data BLOB)")

    def cursor(self):
        return closing(self.database.cursor())

    def __iter__(self):
        with self.cursor() as cursor:
            results = cursor.execute(
                "SELECT name FROM configuration").fetchall()
        return (name for (name,) in results)

    def __getitem__(self, name):
        with self.cursor() as cursor:
            data = cursor.execute(
                "SELECT data FROM configuration"
                " WHERE name = ?", (name,)).fetchone()
        if data is None:
            raise KeyError(name)
        else:
            return json.loads(data[0])

    def __setitem__(self, name, data):
        with self.cursor() as cursor:
            cursor.execute(
                "INSERT OR REPLACE INTO configuration (name, data) "
                "VALUES (?, ?)", (name, json.dumps(data)))

    def __delitem__(self, name):
        with self.cursor() as cursor:
            cursor.execute(
                "DELETE FROM configuration"
                " WHERE name = ?", (name,))

    @classmethod
    @contextmanager
    def open(cls, dbpath):
        """Open a configuration database.

        **Note** that this returns a context manager which will close the
        database on exit, saving if the exit is clean.
        """
        # Ensure `dbpath` exists...
        touch(dbpath)
        # before opening it with sqlite.
        database = sqlite3.connect(dbpath)
        try:
            yield cls(database)
        except:
            raise
        else:
            database.commit()
        finally:
            database.close()


class ConfigurationFile:
    """Store configuration as YAML in a file.

    You should almost always prefer the `ConfigurationDatabase` variant above
    this. It provides things like transactions with optimistic write locking,
    synchronisation between processes, and all the goodies that come with a
    mature and battle-tested piece of kit such as SQLite3.

    This, by comparison, will clobber changes made in another thread or
    process without warning. We could add support for locking, even optimistic
    locking, but, you know, that's already been done: `ConfigurationDatabase`
    preceded this. Just use that. Really. Unless, you know, you've absolutely
    got to use this.
    """

    def __init__(self, path):
        super(ConfigurationFile, self).__init__()
        self.config = {}
        self.dirty = False
        self.path = path

    def __iter__(self):
        return iter(self.config)

    def __getitem__(self, name):
        return self.config[name]

    def __setitem__(self, name, data):
        self.config[name] = data
        self.dirty = True

    def __delitem__(self, name):
        if name in self.config:
            del self.config[name]
            self.dirty = True

    def load(self):
        """Load the configuration."""
        with open(self.path, "rb") as fd:
            config = yaml.safe_load(fd)
        if config is None:
            self.config.clear()
            self.dirty = False
        elif isinstance(config, dict):
            self.config = config
            self.dirty = False
        else:
            raise ValueError(
                "Configuration in %s is not a mapping: %r"
                % (self.path, config))

    def save(self):
        """Save the configuration."""
        atomic_write(yaml.safe_dump(self.config), self.path)
        self.dirty = False

    @classmethod
    @contextmanager
    def open(cls, path):
        """Open a configuration file.

        Locks are taken so that there can only be *one* reader or writer for a
        configuration file at a time. Where configuration files can be read by
        multiple concurrent processes it follows that each process should hold
        the file open for the shortest time possible.

        **Note** that this returns a context manager which will save changes
        to the configuration on a clean exit.
        """
        # Only one reader or writer at a time.
        lock = FileLock(path)
        lock.acquire(timeout=5)
        try:
            # Ensure `path` exists...
            touch(path)
            # before loading it in.
            configfile = cls(path)
            configfile.load()
            try:
                yield configfile
            except:
                raise
            else:
                if configfile.dirty:
                    configfile.save()
        finally:
            lock.release()


class ConfigurationMeta(type):
    """Metaclass for configuration objects.

    :cvar envvar: The name of the environment variable which will be used to
        store the filename of the configuration file. This can be passed in
        from the caller's environment. Setting `DEFAULT_FILENAME` updates this
        environment variable so that it's available to sub-processes.
    :cvar default: If the environment variable named by `envvar` is not set,
        this is used as the filename.
    :cvar backend: The class used to load the configuration. This must provide
        an ``open(filename)`` method that returns a context manager. This
        context manager must provide an object with a dict-like interface.
    """

    envvar = None  # Set this in subtypes.
    default = None  # Set this in subtypes.
    backend = None  # Set this in subtypes.

    def _get_default_filename(cls):
        # Get the configuration filename from the environment. Failing that,
        # look for the configuration in its default locations.
        filename = environ.get(cls.envvar)
        if filename is None or len(filename) == 0:
            return get_tentative_path(cls.default)
        else:
            return filename

    def _set_default_filename(cls, filename):
        # Set the configuration filename in the environment.
        environ[cls.envvar] = filename

    def _delete_default_filename(cls):
        # Remove any setting of the configuration filename from the
        # environment.
        environ.pop(cls.envvar, None)

    DEFAULT_FILENAME = property(
        _get_default_filename, _set_default_filename,
        _delete_default_filename, doc=(
            "The default configuration file to load. Refers to "
            "`cls.envvar` in the environment."))


class Configuration:
    """An object that holds configuration options.

    Configuration options should be defined by creating properties using
    `ConfigurationOption`. For example::

        class ApplicationConfiguration(Configuration):

            application_name = ConfigurationOption(
                "application_name", "The name for this app, used in the UI.",
                validator=UnicodeString())

    This can then be used like so::

        config = ApplicationConfiguration(database)  # database is dict-like.
        config.application_name = "Metal On A Plate"
        print(config.application_name)

    """

    # Define this class variable in sub-classes. Using `ConfigurationMeta` as
    # a metaclass is a good way to achieve this.
    DEFAULT_FILENAME = None

    def __init__(self, store):
        """Initialise a new `Configuration` object.

        :param store: A dict-like object.
        """
        super(Configuration, self).__init__()
        # Use the super-class's __setattr__() because it's redefined later on
        # to prevent accidentally setting attributes that are not options.
        super(Configuration, self).__setattr__("store", store)

    def __setattr__(self, name, value):
        """Prevent setting unrecognised options.

        Only options that have been declared on the class, using the
        `ConfigurationOption` descriptor for example, can be set.

        This is as much about preventing typos as anything else.
        """
        if hasattr(self.__class__, name):
            super(Configuration, self).__setattr__(name, value)
        else:
            raise AttributeError(
                "%r object has no attribute %r" % (
                    self.__class__.__name__, name))

    @classmethod
    @contextmanager
    def open(cls, filepath=None):
        if filepath is None:
            filepath = cls.DEFAULT_FILENAME
        ensure_dir(os.path.dirname(filepath))
        with cls.backend.open(filepath) as store:
            yield cls(store)


class ConfigurationOption:
    """Define a configuration option.

    This is for use with `Configuration` and its subclasses.
    """

    def __init__(self, name, doc, validator):
        """Initialise a new `ConfigurationOption`.

        :param name: The name for this option. This is the name as which this
            option will be stored in the underlying `Configuration` object.
        :param doc: A description of the option. This is mandatory.
        :param validator: A `formencode.validators.Validator`.
        """
        super(ConfigurationOption, self).__init__()

        assert isinstance(name, unicode)
        assert isinstance(doc, unicode)
        assert is_validator(validator)
        assert validator.if_missing is not NoDefault

        self.name = name
        self.__doc__ = doc
        self.validator = validator

    def __get__(self, obj, type=None):
        if obj is None:
            return self
        else:
            try:
                value = obj.store[self.name]
            except KeyError:
                return self.validator.if_missing
            else:
                return self.validator.from_python(value)

    def __set__(self, obj, value):
        obj.store[self.name] = self.validator.to_python(value)

    def __delete__(self, obj):
        del obj.store[self.name]


class ClusterConfiguration(Configuration):
    """Local configuration for the MAAS cluster."""

    class __metaclass__(ConfigurationMeta):
        envvar = "MAAS_CLUSTER_CONFIG"
        default = "/etc/maas/cluster.conf"
        backend = ConfigurationFile

    maas_url = ConfigurationOption(
        "maas_url", "The HTTP URL for the MAAS region.",
        ExtendedURL(require_tld=False,
                    if_missing="http://localhost:5240/MAAS"))
    # TFTP options.
    tftp_port = ConfigurationOption(
        "tftp_port", "The UDP port on which to listen for TFTP requests.",
        Number(min=0, max=(2 ** 16) - 1, if_missing=69))
    tftp_root = ConfigurationOption(
        "tftp_root", "The root directory for TFTP resources.",
        Directory(if_missing="/var/lib/maas/boot-resources/current"))
