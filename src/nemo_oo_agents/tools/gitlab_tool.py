# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""GitLab integration tool for nemo_oo_agents.

Provides async GitLab API operations for querying commits, merge requests, and project information.
All methods are async and can be awaited in agent code.
"""

import asyncio
import os
from datetime import datetime
from typing import Any


class GitLabTool:
    """Async wrapper around python-gitlab for agent use.

    All methods are async and should be awaited.

    Configuration:
        GITLAB_TOKEN: Personal access token from environment (required)
        GITLAB_URL: GitLab instance URL (default: https://gitlab.com)

    Example:
        gl = GitLabTool()
        commits = await gl.get_commits(project_id=123, since="2024-01-01", until="2024-01-07")
        projects = await gl.list_projects()
    """

    def __init__(self, token: str | None = None, url: str | None = None):
        """Initialize GitLab client.

        Args:
            token: GitLab personal access token. If None, reads from GITLAB_TOKEN env var.
            url: GitLab URL. If None, reads from GITLAB_URL env var or uses https://gitlab.com

        Note:
            If no token is provided, methods will raise ValueError when called.
            This allows importing the class without requiring a token.
        """
        self.token = token or os.getenv("GITLAB_TOKEN")
        self.url = url or os.getenv("GITLAB_URL", "https://gitlab.com")
        self.client = None
        self._authenticated = False

    def _ensure_client(self) -> None:
        """Ensure client is initialized and authenticated.

        Raises:
            ValueError: If no token was provided during initialization or authentication fails.
            ImportError: If python-gitlab is not installed.
        """
        # Lazy import: only import gitlab when actually needed
        import gitlab
        from gitlab.exceptions import GitlabError

        if self.client is None:
            if not self.token:
                raise ValueError(
                    "GitLab token required. Provide token parameter or set GITLAB_TOKEN env var."
                )
            self.client = gitlab.Gitlab(self.url, private_token=self.token)

        if not self._authenticated:
            try:
                self.client.auth()
                self._authenticated = True
            except GitlabError as e:
                raise ValueError(f"Failed to authenticate with GitLab: {e}") from e

    async def get_commits(
        self,
        project_id: int | str,
        since: str | datetime | None = None,
        until: str | datetime | None = None,
        ref_name: str = "main",
        author: str | None = None,
        all_commits: bool = False,
    ) -> list[dict[str, Any]]:
        """Get commits for a project within a date range.

        Args:
            project_id: Project ID or path (e.g., 123 or "group/project")
            since: Start date (ISO format "YYYY-MM-DD" or datetime object)
            until: End date (ISO format "YYYY-MM-DD" or datetime object)
            ref_name: Branch/tag name (default: "main")
            author: Filter by author email/name (optional)
            all_commits: Fetch all commits (ignores pagination, slower)

        Returns:
            List of commit dictionaries with 'id', 'title', 'author_name', 'created_at', etc.

        Raises:
            GitlabError: If API call fails

        Example:
            commits = await gl.get_commits(123, since="2024-01-01", until="2024-01-07")
            for commit in commits:
                print(f"{commit['author_name']}: {commit['title']}")
        """
        self._ensure_client()
        from gitlab.exceptions import GitlabError

        def _sync_call() -> list[dict[str, Any]]:
            assert self.client is not None
            try:
                project = self.client.projects.get(project_id)

                # Build query parameters
                params: dict[str, Any] = {"ref_name": ref_name}

                if since:
                    if isinstance(since, datetime):
                        params["since"] = since.isoformat()
                    else:
                        params["since"] = since

                if until:
                    if isinstance(until, datetime):
                        params["until"] = until.isoformat()
                    else:
                        params["until"] = until

                if author:
                    params["author"] = author

                if all_commits:
                    params["all"] = True

                # Get commits
                commits = project.commits.list(**params)

                # Convert to dictionaries (use getattr for potentially missing attrs)
                return [
                    {
                        "id": commit.id,
                        "short_id": getattr(
                            commit, "short_id", commit.id[:8] if commit.id else None
                        ),
                        "title": getattr(commit, "title", None),
                        "message": getattr(commit, "message", None),
                        "author_name": getattr(commit, "author_name", None),
                        "author_email": getattr(commit, "author_email", None),
                        "created_at": getattr(commit, "created_at", None),
                        "web_url": getattr(commit, "web_url", None),
                    }
                    for commit in commits
                ]

            except GitlabError as e:
                raise GitlabError(f"Failed to get commits for project {project_id}: {e}") from e

        return await asyncio.to_thread(_sync_call)

    async def get_merge_requests(
        self,
        project_id: int | str,
        since: str | datetime | None = None,
        until: str | datetime | None = None,
        state: str = "merged",
        author_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Get merge requests for a project within a date range.

        Args:
            project_id: Project ID or path
            since: Start date (ISO format "YYYY-MM-DD" or datetime object)
            until: End date (ISO format "YYYY-MM-DD" or datetime object)
            state: MR state filter: "opened", "closed", "merged", "all" (default: "merged")
            author_id: Filter by author user ID (optional)

        Returns:
            List of MR dictionaries with 'id', 'title', 'author', 'merged_at', etc.

        Raises:
            GitlabError: If API call fails

        Example:
            mrs = await gl.get_merge_requests(123, since="2024-01-01", state="merged")
            for mr in mrs:
                print(f"MR !{mr['iid']}: {mr['title']}")
        """
        self._ensure_client()
        from gitlab.exceptions import GitlabError

        def _sync_call() -> list[dict[str, Any]]:
            assert self.client is not None
            try:
                project = self.client.projects.get(project_id)

                # Build query parameters
                params: dict[str, Any] = {"state": state}

                if since:
                    if isinstance(since, datetime):
                        params["created_after"] = since.isoformat()
                    else:
                        params["created_after"] = since

                if until:
                    if isinstance(until, datetime):
                        params["created_before"] = until.isoformat()
                    else:
                        params["created_before"] = until

                if author_id:
                    params["author_id"] = author_id

                # Get MRs
                mrs = project.mergerequests.list(**params)

                # Convert to dictionaries (use getattr for potentially missing attrs)
                return [
                    {
                        "id": mr.id,
                        "iid": mr.iid,
                        "title": getattr(mr, "title", None),
                        "description": getattr(mr, "description", None),
                        "state": getattr(mr, "state", None),
                        "author": getattr(mr, "author", None),
                        "created_at": getattr(mr, "created_at", None),
                        "updated_at": getattr(mr, "updated_at", None),
                        "merged_at": getattr(mr, "merged_at", None),
                        "merged_by": getattr(mr, "merged_by", None),
                        "web_url": getattr(mr, "web_url", None),
                    }
                    for mr in mrs
                ]

            except GitlabError as e:
                raise GitlabError(
                    f"Failed to get merge requests for project {project_id}: {e}"
                ) from e

        return await asyncio.to_thread(_sync_call)

    async def get_commit_diff(self, project_id: int | str, commit_sha: str) -> list[dict[str, Any]]:
        """Get the diff (changes) for a specific commit.

        Args:
            project_id: Project ID or path
            commit_sha: Commit SHA or short SHA

        Returns:
            List of diff dictionaries with 'old_path', 'new_path', 'diff', etc.

        Raises:
            GitlabError: If API call fails

        Example:
            diffs = await gl.get_commit_diff(123, "abc123")
            for diff in diffs:
                print(f"Changed: {diff['new_path']}")
        """
        self._ensure_client()
        from gitlab.exceptions import GitlabError

        def _sync_call() -> list[dict[str, Any]]:
            assert self.client is not None
            try:
                project = self.client.projects.get(project_id)
                commit = project.commits.get(commit_sha)
                diffs = commit.diff()

                # Convert to dictionaries
                return [
                    {
                        "old_path": d.get("old_path"),
                        "new_path": d.get("new_path"),
                        "a_mode": d.get("a_mode"),
                        "b_mode": d.get("b_mode"),
                        "new_file": d.get("new_file"),
                        "renamed_file": d.get("renamed_file"),
                        "deleted_file": d.get("deleted_file"),
                        "diff": d.get("diff"),
                    }
                    for d in diffs
                ]

            except GitlabError as e:
                raise GitlabError(
                    f"Failed to get diff for commit {commit_sha} in project {project_id}: {e}"
                ) from e

        return await asyncio.to_thread(_sync_call)

    async def list_projects(
        self,
        owned: bool = False,
        membership: bool = True,
        archived: bool = False,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        """List accessible GitLab projects.

        Args:
            owned: Only projects owned by authenticated user
            membership: Only projects where user is a member (default: True)
            archived: Include archived projects (default: False)
            search: Search string to filter projects

        Returns:
            List of project dictionaries with 'id', 'name', 'path_with_namespace', etc.

        Raises:
            GitlabError: If API call fails

        Example:
            projects = await gl.list_projects(search="agent")
            for proj in projects:
                print(f"{proj['id']}: {proj['path_with_namespace']}")
        """
        self._ensure_client()
        from gitlab.exceptions import GitlabError

        def _sync_call() -> list[dict[str, Any]]:
            assert self.client is not None
            try:
                params: dict[str, Any] = {
                    "membership": membership,
                    "archived": archived,
                }

                if owned:
                    params["owned"] = True

                if search:
                    params["search"] = search

                projects = self.client.projects.list(**params)

                # Convert to dictionaries (use getattr for potentially missing attrs)
                return [
                    {
                        "id": proj.id,
                        "name": proj.name,
                        "path": getattr(proj, "path", None),
                        "path_with_namespace": getattr(proj, "path_with_namespace", None),
                        "description": getattr(proj, "description", None),
                        "default_branch": getattr(proj, "default_branch", None),
                        "web_url": getattr(proj, "web_url", None),
                        "created_at": getattr(proj, "created_at", None),
                        "last_activity_at": getattr(proj, "last_activity_at", None),
                    }
                    for proj in projects
                ]

            except GitlabError as e:
                raise GitlabError(f"Failed to list projects: {e}") from e

        return await asyncio.to_thread(_sync_call)

    async def get_project(self, project_id: int | str) -> dict[str, Any]:
        """Get detailed information about a specific project.

        Args:
            project_id: Project ID or path

        Returns:
            Project dictionary with detailed information

        Raises:
            GitlabError: If API call fails

        Example:
            project = await gl.get_project(123)
            print(f"Project: {project['name']}")
            print(f"Stars: {project['star_count']}")
        """
        self._ensure_client()
        from gitlab.exceptions import GitlabError

        def _sync_call() -> dict[str, Any]:
            assert self.client is not None
            try:
                proj = self.client.projects.get(project_id)

                return {
                    "id": proj.id,
                    "name": proj.name,
                    "path": proj.path,
                    "path_with_namespace": proj.path_with_namespace,
                    "description": proj.description,
                    "default_branch": proj.default_branch,
                    "web_url": proj.web_url,
                    "created_at": proj.created_at,
                    "last_activity_at": proj.last_activity_at,
                    "star_count": proj.star_count,
                    "forks_count": proj.forks_count,
                    "open_issues_count": getattr(proj, "open_issues_count", 0),
                }

            except GitlabError as e:
                raise GitlabError(f"Failed to get project {project_id}: {e}") from e

        return await asyncio.to_thread(_sync_call)

    async def create_issue(
        self,
        project_id: int | str,
        title: str,
        description: str | None = None,
        labels: list[str] | None = None,
        assignee_ids: list[int] | None = None,
        milestone_id: int | None = None,
    ) -> dict[str, Any]:
        """Create a new issue in a project.

        Args:
            project_id: Project ID or path
            title: Issue title (required)
            description: Issue description (optional)
            labels: List of label names to add (optional)
            assignee_ids: List of user IDs to assign (optional)
            milestone_id: Milestone ID to assign (optional)

        Returns:
            Created issue dictionary with 'id', 'iid', 'title', 'web_url', etc.

        Raises:
            GitlabError: If API call fails (e.g., insufficient permissions)

        Example:
            issue = await gl.create_issue(
                project_id=123,
                title="Bug: Login fails",
                description="Steps to reproduce...",
                labels=["bug", "priority::high"]
            )
            print(f"Created issue #{issue['iid']}: {issue['web_url']}")
        """
        self._ensure_client()
        from gitlab.exceptions import GitlabError

        def _sync_call() -> dict[str, Any]:
            assert self.client is not None
            try:
                project = self.client.projects.get(project_id)

                # Build issue data
                issue_data: dict[str, Any] = {"title": title}

                if description:
                    issue_data["description"] = description

                if labels:
                    issue_data["labels"] = ",".join(labels)

                if assignee_ids:
                    issue_data["assignee_ids"] = assignee_ids

                if milestone_id:
                    issue_data["milestone_id"] = milestone_id

                # Create the issue
                issue = project.issues.create(issue_data)

                # Return as dictionary
                return {
                    "id": issue.id,
                    "iid": issue.iid,
                    "title": issue.title,
                    "description": getattr(issue, "description", None),
                    "state": getattr(issue, "state", None),
                    "labels": getattr(issue, "labels", []),
                    "author": getattr(issue, "author", None),
                    "assignees": getattr(issue, "assignees", []),
                    "created_at": getattr(issue, "created_at", None),
                    "updated_at": getattr(issue, "updated_at", None),
                    "web_url": getattr(issue, "web_url", None),
                }

            except GitlabError as e:
                raise GitlabError(
                    f"Failed to create issue in project {project_id}: {e}. "
                    "Ensure the GitLab token has 'api' or 'write_repository' scope "
                    "and you have at least Reporter access to the project."
                ) from e

        return await asyncio.to_thread(_sync_call)
