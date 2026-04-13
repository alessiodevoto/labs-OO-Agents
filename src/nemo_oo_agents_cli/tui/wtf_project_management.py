# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""WTF Project Management skill - programmatic access to WTF issues."""

from __future__ import annotations

from typing import TYPE_CHECKING

from wtf_issues import WTF  # type: ignore[import-untyped]
from wtf_issues.types import Filter, Issue, Priority  # type: ignore[import-untyped]

from nemo_oo_agents.skill import Skill

if TYPE_CHECKING:
    pass


class WtfProjectManagement(Skill):
    """Project management using WTF issues - direct Python API.

    Methods:
    - status(label_filter=None) -> dict: Get current project status with issues grouped by state
    - create(title, description="", priority=MEDIUM) -> Issue: Create a new issue
    - get(id) -> Issue: Get issue by ID
    - list_issues(filter=None) -> list[Issue]: List/search issues
    - close(id, reason="") -> Issue: Close an issue
    - update(id, **fields) -> Issue: Update issue fields
    - add_dependency(from_id, to_id, dep_type) -> None: Add issue dependency
    - comment(id, body, author="") -> Comment: Add a comment to an issue
    """

    def __init__(self):
        """Initialize WTF client from environment."""
        self._client = WTF.from_env()
        super().__init__(content=self.__class__.__doc__)

    def status(self, label_filter: str | None = None) -> dict:
        """Get project status with issues grouped by state.

        Args:
            label_filter: Optional label to filter issues (e.g., "urgent", "agent006")

        Returns:
            Dict with keys:
            - backend: Backend type name (SqliteBackend/GitLabBackend)
            - issues: All open issues (as dicts)
            - ready: List of unblocked issue IDs
            - blockers: Map of issue_id -> list of blocker IDs
            - epics: Issues with "epic" label
        """
        issues = self._client.list_issues()
        ready = self._client.get_ready()
        blockers = self._client.get_blockers()

        # Filter by label if requested
        if label_filter:
            issues = [i for i in issues if label_filter in i.labels]

        # Separate epics
        epics = [i for i in issues if "epic" in i.labels]
        tasks = [i for i in issues if "epic" not in i.labels]

        return {
            "backend": type(self._client._backend).__name__,
            "issues": [i.to_dict() for i in tasks],
            "epics": [i.to_dict() for i in epics],
            "ready": ready,
            "blockers": blockers,
        }

    def create(
        self,
        title: str,
        description: str = "",
        priority: Priority = Priority.MEDIUM,
        assignee: str | None = None,
        labels: list[str] | None = None,
    ) -> Issue:
        """Create a new issue.

        Args:
            title: Short summary
            description: Full details
            priority: Priority level (LOWEST, LOW, MEDIUM, HIGH, CRITICAL)
            assignee: Username to assign to
            labels: List of labels

        Returns:
            Created Issue object
        """
        return self._client.create(
            title=title,
            description=description,
            priority=priority,
            assignee=assignee,
            labels=labels,
        )

    def get(self, id: str) -> Issue:
        """Get issue by ID.

        Args:
            id: Issue ID (e.g., "wtf-abc123" or "gl-42")

        Returns:
            Issue object
        """
        return self._client.get(id)

    def list_issues(self, filter: Filter | None = None) -> list[Issue]:
        """List/search issues with optional filtering.

        Args:
            filter: Optional Filter object with status, priority, assignee, labels, query

        Returns:
            List of Issue objects
        """
        return self._client.list_issues(filter)

    def close(self, id: str, reason: str = "") -> Issue:
        """Close an issue.

        Args:
            id: Issue ID
            reason: Human-readable reason for closing

        Returns:
            Updated Issue object
        """
        return self._client.close(id, reason=reason)

    def update(self, id: str, **fields) -> Issue:
        """Update issue fields.

        Args:
            id: Issue ID
            **fields: Fields to update (title, description, priority, assignee, status)

        Returns:
            Updated Issue object
        """
        return self._client.update(id, **fields)

    def add_dependency(self, from_id: str, to_id: str, dep_type: str = "depends_on") -> None:
        """Add a dependency between issues.

        Args:
            from_id: Source issue ID
            to_id: Target issue ID
            dep_type: Dependency type (depends_on, blocks, parent, child, related)
        """
        from wtf_issues.types import DepType  # type: ignore[import-untyped]

        self._client.add_dependency(from_id, to_id, DepType(dep_type))

    def comment(self, id: str, body: str, author: str = "") -> None:
        """Add a comment to an issue.

        Args:
            id: Issue ID
            body: Comment text
            author: Username (empty = anonymous)
        """
        self._client.add_comment(id, body, author=author)
