from __future__ import annotations

from collections import defaultdict

from .errors import ConfigurationError
from .models import WorkspaceTask


def task_order(tasks: tuple[WorkspaceTask, ...]) -> tuple[str, ...]:
    by_id = {task.id: task for task in tasks}
    if len(by_id) != len(tasks):
        raise ConfigurationError("task ids must be unique")

    indegree = {task.id: 0 for task in tasks}
    dependents: dict[str, list[str]] = defaultdict(list)
    for task in tasks:
        for dependency in task.depends_on:
            if dependency not in by_id:
                raise ConfigurationError(f"task {task.id} depends on unknown task {dependency}")
            indegree[task.id] += 1
            dependents[dependency].append(task.id)

    ready = sorted(task_id for task_id, degree in indegree.items() if degree == 0)
    ordered: list[str] = []
    while ready:
        task_id = ready.pop(0)
        ordered.append(task_id)
        for dependent in sorted(dependents[task_id]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
                ready.sort()

    if len(ordered) != len(tasks):
        cyclic = sorted(task_id for task_id, degree in indegree.items() if degree > 0)
        raise ConfigurationError(f"task dependency cycle detected: {', '.join(cyclic)}")
    return tuple(ordered)
