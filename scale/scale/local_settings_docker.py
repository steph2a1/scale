# This is the local_settings.py file to be packaged within Scale's Docker image

# Include all the default settings.
from settings import *
import elasticsearch

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get('SCALE_SECRET_KEY', INSECURE_DEFAULT_KEY)

# Use the following lines to enable developer/debug mode.
DEBUG = bool(os.environ.get('SCALE_DEBUG', False))
TEMPLATE_DEBUG = DEBUG

# Set the external URL context here
FORCE_SCRIPT_NAME = os.environ.get('SCALE_API_URL', '/api')
USE_X_FORWARDED_HOST = True

ALLOWED_HOSTS = ['*']
override_hosts = os.environ.get('SCALE_ALLOWED_HOSTS')
if override_hosts:
    ALLOWED_HOSTS = override_hosts.split(',')

STATIC_ROOT = os.environ.get('SCALE_STATIC_ROOT', 'static/')
STATIC_URL = os.environ.get('SCALE_STATIC_URL', '/static/')

LOGGING_ADDRESS = os.environ.get('SCALE_LOGGING_ADDRESS', LOGGING_ADDRESS)
ELASTICSEARCH_URL = os.environ.get('SCALE_ELASTICSEARCH_URL', ELASTICSEARCH_URL)
if ELASTICSEARCH_URL:
    ELASTICSEARCH = elasticsearch.Elasticsearch(
        ELASTICSEARCH_URL.split(','),
        # sniff before doing anything
        sniff_on_start=True,
        # refresh nodes after a node fails to respond
        sniff_on_connection_fail=True,
        # and also every 60 seconds
        sniffer_timeout=60
    )

DB_HOST = os.environ.get('SCALE_DB_HOST', '')
if DB_HOST == '':
        DB_HOST = os.environ.get('DB_PORT_5432_TCP_ADDR', '')
DB_PORT = os.environ.get('SCALE_DB_PORT', '')
if DB_PORT == '':
        DB_PORT = os.environ.get('DB_PORT_5432_TCP_PORT', '5432')

if DB_HOST != '':
    DATABASES = {
        'default': {
            'ENGINE': 'django.contrib.gis.db.backends.postgis',
            'NAME': os.environ.get('SCALE_DB_NAME', 'scale'),
            'USER': os.environ.get('SCALE_DB_USER', 'postgres'),
            'PASSWORD': os.environ.get('SCALE_DB_PASS', 'postgres'),
            'HOST': DB_HOST,
            'PORT': DB_PORT,
        },
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': os.path.join(BASE_DIR, 'db.sqlite3'),
        }
    }

# Mesos connection information. Default for -m
# This can be something like "127.0.0.1:5050"
# or a zookeeper url like 'zk://host1:port1,host2:port2,.../path`
MESOS_MASTER = os.environ.get('MESOS_MASTER_URL', 'zk://localhost:2181/scale')

# Zookeeper URL for scheduler leader election. If this is None, only a single scheduler is used.
SCHEDULER_ZK = os.environ.get('SCALE_ZK_URL', None)

# The full name for the Scale Docker image (without version tag)
SCALE_DOCKER_IMAGE = os.environ.get('SCALE_DOCKER_IMAGE', SCALE_DOCKER_IMAGE)

# Use the :latest tag on docker. Be careful as it's pretty easy for the scheduler and the
# system tasks to get out of sync. This is useful for testing the master branch and for
# the quickstart. Production systems should always set this to false.
if os.environ.get('USE_LATEST', 'false').lower() == 'true':
    DOCKER_VERSION = "latest"

# The location of the config file containing Docker credentials
CONFIG_URI = os.environ.get('CONFIG_URI', CONFIG_URI)