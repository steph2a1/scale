"""Defines the class that manages the database sync background thread"""
from __future__ import unicode_literals

import logging
import math
import time

from django.db import DatabaseError
from django.utils.timezone import now
from mesos.interface import mesos_pb2

from job.execution.running.manager import running_job_mgr
from job.models import JobExecution
from scheduler.cleanup.manager import cleanup_mgr
from scheduler.node.manager import node_mgr
from scheduler.sync.job_type_manager import job_type_mgr
from scheduler.sync.scheduler_manager import scheduler_mgr
from scheduler.sync.workspace_manager import workspace_mgr


logger = logging.getLogger(__name__)


class DatabaseSyncThread(object):
    """This class manages the database sync background thread for the scheduler"""

    THROTTLE = 10  # seconds

    def __init__(self, driver):
        """Constructor

        :param driver: The Mesos scheduler driver
        :type driver: :class:`mesos_api.mesos.SchedulerDriver`
        """

        self._driver = driver
        self._running = True

    @property
    def driver(self):
        """Returns the driver

        :returns: The driver
        :rtype: :class:`mesos_api.mesos.SchedulerDriver`
        """

        return self._driver

    @driver.setter
    def driver(self, value):
        """Sets the driver

        :param value: The driver
        :type value: :class:`mesos_api.mesos.SchedulerDriver`
        """

        self._driver = value

    def run(self):
        """The main run loop of the thread
        """

        logger.info('Database sync thread started')

        while self._running:

            started = now()

            try:
                self._perform_sync()
            except Exception:
                logger.exception('Critical error in database sync thread')

            ended = now()
            secs_passed = (ended - started).total_seconds()

            # If time takes less than a minute, throttle
            if secs_passed < DatabaseSyncThread.THROTTLE:
                # Delay until full throttle time reached
                delay = math.ceil(DatabaseSyncThread.THROTTLE - secs_passed)
                time.sleep(delay)

        logger.info('Database sync thread stopped')

    def shutdown(self):
        """Stops the thread from running and performs any needed clean up
        """

        logger.info('Shutting down database sync thread')
        self._running = False

    def _perform_sync(self):
        """Performs the sync with the database
        """

        scheduler_mgr.sync_with_database()
        job_type_mgr.sync_with_database()
        workspace_mgr.sync_with_database()

        mesos_master = scheduler_mgr.mesos_address
        node_mgr.sync_with_database(mesos_master.hostname, mesos_master.port)

        self._sync_running_job_executions()

    def _sync_running_job_executions(self):
        """Syncs job executions that are currently running by handling any canceled or timed out executions
        """

        running_job_exes = {}
        for job_exe in running_job_mgr.get_all_job_exes():
            running_job_exes[job_exe.id] = job_exe

        right_now = now()

        for job_exe_model in JobExecution.objects.filter(id__in=running_job_exes.keys()).iterator():
            running_job_exe = running_job_exes[job_exe_model.id]
            task_to_kill = None

            if job_exe_model.status == 'CANCELED':
                try:
                    task_to_kill = running_job_exe.execution_canceled()
                except DatabaseError:
                    logger.exception('Error canceling job execution %i', running_job_exe.id)
            elif job_exe_model.is_timed_out(right_now):
                try:
                    task_to_kill = running_job_exe.execution_timed_out(right_now)
                except DatabaseError:
                    logger.exception('Error failing timed out job execution %i', running_job_exe.id)

            if task_to_kill:
                pb_task_to_kill = mesos_pb2.TaskID()
                pb_task_to_kill.value = task_to_kill.id
                logger.info('Killing task %s', task_to_kill.id)
                self._driver.killTask(pb_task_to_kill)

            if running_job_exe.is_finished():
                running_job_mgr.remove_job_exe(running_job_exe.id)
                cleanup_mgr.add_job_execution(running_job_exe)
