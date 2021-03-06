"""Defines the class for a cleanup task"""
from __future__ import unicode_literals

import threading

from job.execution.running.tasks.base_task import Task
from job.resources import NodeResources


CLEANUP_TASK_ID_PREFIX = 'scale_cleanup'


class AtomicCounter(object):
    """Represents an atomic counter
    """

    def __init__(self):
        """Constructor
        """

        self._counter = 0
        self._lock = threading.Lock()

    def get_next(self):
        """Returns the next integer

        :returns: The next integer
        :rtype: int
        """

        with self._lock:
            self._counter += 1
            return self._counter


COUNTER = AtomicCounter()


class CleanupTask(Task):
    """Represents a task that cleans up after job executions. This class is thread-safe.
    """

    def __init__(self, framework_id, agent_id, job_exes):
        """Constructor

        :param framework_id: The framework ID
        :type framework_id: string
        :param agent_id: The agent ID
        :type agent_id: string
        :param job_exes: The list of job executions to clean up
        :type job_exes: [:class:`job.execution.running.job_exe.RunningJobExecution`]
        """

        task_id = '%s_%s_%d' % (CLEANUP_TASK_ID_PREFIX, framework_id, COUNTER.get_next())
        super(CleanupTask, self).__init__(task_id, 'Scale Cleanup', agent_id)

        self._job_exes = job_exes
        self._is_initial_cleanup = not self._job_exes  # This is an initial clean up if job_exes is empty

        self._uses_docker = False
        self._docker_image = None
        self._docker_params = []
        self._is_docker_privileged = False

        # Define basic command pieces
        for_cmd = 'for %s in `%s`; do %s; done'
        nonrunning_filters = '-f status=created -f status=dead -f status=exited'
        all_nonrunning_containers_cmd = 'docker ps %s --format \'{{.Names}}\'' % nonrunning_filters
        all_dangling_volumes_cmd = 'docker volume ls -f dangling=true -q'
        container_delete_cmd = 'docker rm $cont'
        volume_delete_cmd = 'docker volume rm $vol'

        # Create commands that list the containers/volumes to delete
        if self._is_initial_cleanup:
            # Initial clean up deletes all non-running containers
            container_list_cmd = all_nonrunning_containers_cmd

            # TODO: once we upgrade to a later version of Docker (1.12+), we can delete volumes based on their name
            # starting with "scale_" (and also dangling)
            # Initial clean up deletes all dangling Docker volumes
            volume_list_cmd = all_dangling_volumes_cmd
        else:
            # Deletes all containers and volumes for the given job executions
            containers = []
            volumes = []
            for job_exe in self._job_exes:
                containers.extend(job_exe.get_container_names())
                volumes.extend(job_exe.docker_volumes)
            container_list_cmd = '%s | grep -e %s' % (all_nonrunning_containers_cmd, ' -e '.join(containers))
            volume_list_cmd = '%s | grep -e %s' % (all_dangling_volumes_cmd, ' -e '.join(volumes))

        delete_containers_cmd = for_cmd % ('cont', container_list_cmd, container_delete_cmd)
        delete_volumes_cmd = for_cmd % ('vol', volume_list_cmd, volume_delete_cmd)

        # Create overall command that deletes containers and volumes for the job executions
        self._command = '%s; %s' % (delete_containers_cmd, delete_volumes_cmd)

    @property
    def is_initial_cleanup(self):
        """Indicates whether this is an initial clean up job (True) or not (False)

        :returns: Whether this is an initial clean up job
        :rtype: bool
        """

        return self._is_initial_cleanup

    @property
    def job_exes(self):
        """Returns the list of job executions to clean up

        :returns: The list of job executions to clean up
        :rtype: [:class:`job.execution.running.job_exe.RunningJobExecution`]
        """

        return self._job_exes

    def get_resources(self):
        """See :meth:`job.execution.running.tasks.base_task.Task.get_resources`
        """

        return NodeResources(cpus=0.1, mem=32)
