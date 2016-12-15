import logging
import os

from django.conf import settings
from django.core.management import execute_from_command_line
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Command that loads all of the fixtures into the database
    """

    help = 'Loads all of the fixtures into the database'

    def handle(self, **options):
        """See :meth:`django.core.management.base.BaseCommand.handle`.

        This method loads all of the fixtures into the database.
        """
        logger.info(u'Command starting: load_all_data')
        fixture_names = []
        manage_path = os.path.join(settings.BASE_DIR, u'manage.py')

        for app_dir in os.listdir(settings.BASE_DIR):
            app_dir_path = os.path.join(settings.BASE_DIR, app_dir)
            if not os.path.isdir(app_dir_path):
                continue
            sub_dirs = os.listdir(app_dir_path)
            if u'fixtures' in sub_dirs:
                fixture_dir_path = os.path.join(app_dir_path, u'fixtures')
                for entry in os.listdir(fixture_dir_path):
                    if os.path.isfile(os.path.join(fixture_dir_path, entry)):
                        if entry.endswith(u'.json'):
                            logger.info('Discovered: %s -> %s', app_dir, entry)
                            fixture_names.append(entry)

        for name in fixture_names:
            cmd_list = [manage_path, u'loaddata', name]
            logger.info(u'Executing: %s', ' '.join(cmd_list))
            execute_from_command_line(cmd_list)
        logger.info(u'Command completed: load_all_data')
