"""Defines the class that represents running job executions"""
from __future__ import unicode_literals

import logging
import threading
from datetime import datetime, timedelta

from django.db import transaction
from django.utils.timezone import now

from error.models import Error
from job.execution.running.tasks.job_task import JobTask
from job.execution.running.tasks.post_task import PostTask
from job.execution.running.tasks.pre_task import PreTask
from job.execution.running.tasks.update import TaskStatusUpdate
from job.models import JobExecution
from util.retry import retry_database_query


logger = logging.getLogger(__name__)


class RunningJobExecution(object):
    """This class represents a currently running job execution. This class is thread-safe."""

    def __init__(self, job_exe):
        """Constructor

        :param job_exe: The job execution, which must be in RUNNING status and have its related node_id, job, job_type
            and job_type_rev models populated
        :type job_exe: :class:`job.models.JobExecution`
        """

        self._id = job_exe.id
        self._job_type_id = job_exe.job.job_type_id
        self._node_id = job_exe.node_id
        if hasattr(job_exe, 'docker_volumes'):
            self._docker_volumes = job_exe.docker_volumes
        else:
            self._docker_volumes = []

        self._lock = threading.Lock()  # Protects _current_task and _remaining_tasks
        self._current_task = None
        self._remaining_tasks = []
        self._all_tasks = []

        # Create tasks
        if not job_exe.is_system:
            self._all_tasks.append(PreTask(job_exe))
        self._all_tasks.append(JobTask(job_exe))
        if not job_exe.is_system:
            self._all_tasks.append(PostTask(job_exe))
        for task in self._all_tasks:
            self._remaining_tasks.append(task)

    @property
    def current_task(self):
        """Returns the currently running task of the job execution, or None if no task is currently running

        :returns: The current task, possibly None
        :rtype: :class:`job.execution.running.tasks.base_task.Task`
        """

        return self._current_task

    @property
    def docker_volumes(self):
        """Returns the names of the Docker volumes used by this job execution

        :returns: The list of Docker volume names
        :rtype: [string]
        """

        return self._docker_volumes

    @property
    def id(self):
        """Returns the ID of this job execution

        :returns: The ID of the job execution
        :rtype: int
        """

        return self._id

    @property
    def job_type_id(self):
        """Returns the job type ID of this job execution

        :returns: The job type ID of the job execution
        :rtype: int
        """

        return self._job_type_id

    @property
    def node_id(self):
        """Returns the ID of this job execution's node

        :returns: The ID of the node
        :rtype: int
        """

        return self._node_id

    @retry_database_query
    def execution_canceled(self):
        """Cancels this job execution and returns the current task

        :returns: The current task, possibly None
        :rtype: :class:`job.execution.running.tasks.base_task.Task`
        """

        # Saves this job execution's task info to the database
        with transaction.atomic():
            job_exe = JobExecution.objects.get_locked_job_exe(self._id)
            for task in self._all_tasks:
                task.populate_job_exe_model(job_exe)
            job_exe.save()

        with self._lock:
            task = self._current_task
            self._current_task = None
            self._remaining_tasks = []
            return task

    @retry_database_query
    def execution_lost(self, when):
        """Fails this job execution for its node becoming lost and returns the current task

        :param when: The time that the node was lost
        :type when: :class:`datetime.datetime`
        :returns: The current task, possibly None
        :rtype: :class:`job.execution.running.tasks.base_task.Task`
        """

        error = Error.objects.get_builtin_error('node-lost')
        from queue.models import Queue
        Queue.objects.handle_job_failure(self._id, when, self._all_tasks, error)

        with self._lock:
            task = self._current_task
            self._current_task = None
            self._remaining_tasks = []
            return task

    @retry_database_query
    def execution_timed_out(self, when):
        """Fails this job execution for timing out and returns the current task

        :param when: The time that the job execution timed out
        :type when: :class:`datetime.datetime`
        :returns: The current task, possibly None
        :rtype: :class:`job.execution.running.tasks.base_task.Task`
        """

        error = Error.objects.get_builtin_error('timeout')
        from queue.models import Queue
        Queue.objects.handle_job_failure(self._id, when, self._all_tasks, error)

        with self._lock:
            task = self._current_task
            self._current_task = None
            self._remaining_tasks = []
            return task

    def get_container_names(self):
        """Returns the list of container names for all tasks in this job execution

        :returns: The list of all container names
        :rtype: [string]
        """

        containers = []
        with self._lock:
            for task in self._all_tasks:
                if task.container_name:
                    containers.append(task.container_name)
            return containers

    def is_finished(self):
        """Indicates whether this job execution is finished with all tasks

        :returns: True if all tasks are finished, False otherwise
        :rtype: bool
        """

        with self._lock:
            return not self._current_task and not self._remaining_tasks

    def is_next_task_ready(self):
        """Indicates whether the next task in this job execution is ready

        :returns: True if the next task is ready, False otherwise
        :rtype: bool
        """

        with self._lock:
            return not self._current_task and self._remaining_tasks

    def next_task_resources(self):
        """Returns the resources that are required by the next task in this job execution. Returns None if there are no
        remaining tasks.

        :returns: The resources required by the next task, possibly None
        :rtype: :class:`job.resources.NodeResources`
        """

        with self._lock:
            if not self._remaining_tasks:
                return None

            next_task = self._remaining_tasks[0]
            return next_task.get_resources()

    def start_next_task(self):
        """Starts the next task in the job execution and returns it. Returns None if the next task is not ready or no
        tasks remain.

        :returns: The new task that was started, possibly None
        :rtype: :class:`job.execution.running.tasks.base_task.Task`
        """

        with self._lock:
            if self._current_task or not self._remaining_tasks:
                return None

            self._current_task = self._remaining_tasks.pop(0)
            return self._current_task

    def task_update(self, task_update):
        """Updates a task for this job execution

        :param task_update: The task update
        :type task_update: :class:`job.execution.running.tasks.update.TaskStatusUpdate`
        """

        if task_update.status == TaskStatusUpdate.RUNNING:
            self._task_start(task_update)
        elif task_update.status == TaskStatusUpdate.FINISHED:
            self._task_complete(task_update)
        elif task_update.status == TaskStatusUpdate.LOST:
            self._task_lost(task_update)
        elif task_update.status in [TaskStatusUpdate.FAILED, TaskStatusUpdate.KILLED]:
            self._task_fail(task_update)

    @retry_database_query
    def _task_complete(self, task_update):
        """Completes a task for this job execution

        :param task_update: The task update
        :type task_update: :class:`job.execution.running.tasks.update.TaskStatusUpdate`
        """

        with self._lock:
            current_task = self._current_task
            remaining_tasks = self._remaining_tasks

        if not current_task or current_task.id != task_update.task_id:
            return

        with transaction.atomic():
            need_refresh = current_task.complete(task_update)
            if need_refresh and remaining_tasks:
                job_exe = JobExecution.objects.get(id=self._id)
                for task in remaining_tasks:
                    task.refresh_cached_values(job_exe)
            if not remaining_tasks:
                from queue.models import Queue
                Queue.objects.handle_job_completion(self._id, now(), self._all_tasks)

        with self._lock:
            if self._current_task and self._current_task.id == task_update.task_id:
                self._current_task = None

    @retry_database_query
    def _task_fail(self, task_update):
        """Fails a task for this job execution

        :param task_update: The task update
        :type task_update: :class:`job.execution.running.tasks.update.TaskStatusUpdate`
        """

        current_task = self._current_task
        if not current_task or current_task.id != task_update.task_id:
            return

        with transaction.atomic():
            current_task.update(task_update)
            error = current_task.determine_error(task_update)
            from queue.models import Queue
            Queue.objects.handle_job_failure(self._id, now(), self._all_tasks, error)

            # TODO: move this somewhere else and refactor it
            from scheduler.models import Scheduler
            job_exe = JobExecution.objects.get_job_exe_with_job_and_job_type(self._id)
            node = job_exe.node
            if not error:
                error = Error.objects.get_unknown_error()
            # Check for a high number of system errors and decide if we should pause the node
            if error.category == 'SYSTEM' and job_exe.job.num_exes >= job_exe.job.max_tries and node is not None and not node.is_paused:
                # search Job.objects. for the number of system failures in the past (configurable) 1 minute
                # if (configurable) 5 or more have occurred, pause the node
                node_error_period = Scheduler.objects.first().node_error_period
                if node_error_period > 0:
                    check_time = datetime.utcnow() - timedelta(minutes=node_error_period)
                    # find out how many jobs have recently failed on this node with a system error
                    num_node_errors = JobExecution.objects.select_related('error', 'node').filter(
                        status='FAILED', error__category='SYSTEM', ended__gte=check_time, node=node).distinct('job').count()
                    max_node_errors = Scheduler.objects.first().max_node_errors
                    if num_node_errors >= max_node_errors:
                        logger.warning('%s failed %d jobs in %d minutes, pausing the host' % (node.hostname, num_node_errors, node_error_period))
                        with transaction.atomic():
                            node.is_paused = True
                            node.is_paused_errors = True
                            node.pause_reason = "System Failure Rate Too High"
                            node.save()

        with self._lock:
            self._current_task = None
            self._remaining_tasks = []

    def _task_lost(self, task_update):
        """Tells this job execution that one of its tasks was lost

        :param task_update: The task update
        :type task_update: :class:`job.execution.running.tasks.update.TaskStatusUpdate`
        """

        with self._lock:
            if not self._current_task or self._current_task.id != task_update.task_id:
                return

            self._current_task.update(task_update)
            self._remaining_tasks.insert(0, self._current_task)
            self._current_task = None

    @retry_database_query
    def _task_start(self, task_update):
        """Tells this job execution that one of its tasks has started running

        :param task_update: The task update
        :type task_update: :class:`job.execution.running.tasks.update.TaskStatusUpdate`
        """

        current_task = self._current_task
        if not current_task or current_task.id != task_update.task_id:
            return

        current_task.update(task_update)
