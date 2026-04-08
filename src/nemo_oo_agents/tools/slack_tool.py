"""Slack integration tool for agent006.

Provides async Slack API operations for sending messages, reading threads, and managing channels.
All methods are async and can be awaited in agent code.
"""

import asyncio
import os
from typing import Any


class SlackTool:
    """Async wrapper around Slack SDK for agent use.

    All methods are async and should be awaited.

    Configuration:
        SLACK_BOT_TOKEN: Bot token from environment (required)

    Example:
        slack = SlackTool()
        response = await slack.send_message("C1234567890", "Hello team!")
        members = await slack.get_channel_members("C1234567890")
    """

    def __init__(self, token: str | None = None):
        """Initialize Slack client.

        Args:
            token: Slack bot token. If None, reads from SLACK_BOT_TOKEN env var.

        Note:
            If no token is provided, methods will raise ValueError when called.
            This allows importing the class without requiring a token.
        """
        import slack_sdk

        self.token = token or os.getenv("SLACK_BOT_TOKEN")
        self.client = slack_sdk.WebClient(token=self.token) if self.token else None
        self._user_cache: dict[str, dict[str, Any]] = {}  # Cache user info to avoid N+1 queries

    def _ensure_client(self) -> None:
        """Ensure client is initialized with a token.

        Raises:
            ValueError: If no token was provided during initialization.
        """
        if not self.client:
            raise ValueError(
                "Slack token required. Provide token parameter or set SLACK_BOT_TOKEN env var."
            )

    async def _get_user_info_cached(self, user_id: str) -> dict[str, Any] | None:
        """Get user info with caching to avoid repeated API calls.

        Args:
            user_id: User ID to look up

        Returns:
            User info dict or None if lookup fails (e.g., bot user, deleted user)
        """
        if user_id in self._user_cache:
            return self._user_cache[user_id]

        try:
            user_info = await self.get_user_info(user_id)
            self._user_cache[user_id] = user_info
            return user_info
        except Exception:
            # Cache failures too to avoid repeated failed lookups
            self._user_cache[user_id] = {}
            return None

    async def _enrich_messages_with_user_names(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Enrich messages with user names to avoid N+1 API call pattern.

        For each message with a 'user' field, adds 'user_name' and 'user_real_name'
        fields so agents don't need to call get_user_info separately.

        Args:
            messages: List of message dictionaries from Slack API

        Returns:
            Same messages with 'user_name' and 'user_real_name' fields added
        """
        # Collect unique user IDs
        user_ids: set[str] = {str(msg.get("user")) for msg in messages if msg.get("user")}

        # Fetch all user info (with caching)
        for user_id in user_ids:
            await self._get_user_info_cached(user_id)

        # Enrich messages
        for msg in messages:
            user_id = msg.get("user")
            if user_id and user_id in self._user_cache:
                user_info = self._user_cache[user_id]
                if user_info:
                    msg["user_name"] = user_info.get("name", "")
                    msg["user_real_name"] = user_info.get("real_name", "")

        return messages

    async def send_message(self, channel_id: str, text: str, **kwargs: Any) -> dict[str, Any]:
        """Send a message to a Slack channel.

        Args:
            channel_id: Channel ID (e.g., "C1234567890")
            text: Message text
            **kwargs: Additional arguments for chat.postMessage API

        Returns:
            Response dictionary with 'ts' (timestamp/message ID), 'channel', etc.

        Raises:
            SlackApiError: If API call fails
            ValueError: If no token was provided during initialization

        Example:
            response = await slack.send_message("C1234567890", "Hello!")
            message_ts = response['ts']
        """
        self._ensure_client()
        from slack_sdk.errors import SlackApiError

        def _sync_call() -> dict[str, Any]:
            assert self.client is not None
            try:
                response = self.client.chat_postMessage(channel=channel_id, text=text, **kwargs)
                return response.data  # type: ignore[return-value]
            except SlackApiError as e:
                raise SlackApiError(
                    f"Failed to send message to {channel_id}: {e.response['error']}", e.response
                ) from e

        return await asyncio.to_thread(_sync_call)

    async def send_dm(self, user_id: str, text: str, **kwargs: Any) -> dict[str, Any]:
        """Send a direct message to a user.

        Args:
            user_id: User ID (e.g., "U1234567890")
            text: Message text
            **kwargs: Additional arguments for chat.postMessage API

        Returns:
            Response dictionary with 'ts', 'channel' (DM channel ID), etc.

        Raises:
            SlackApiError: If API call fails

        Example:
            response = await slack.send_dm("U1234567890", "Hi there!")
        """
        # For DMs, we send to the user ID as the channel
        return await self.send_message(channel_id=user_id, text=text, **kwargs)

    async def get_channel_members(self, channel_id: str) -> list[str]:
        """Get list of member user IDs in a channel.

        Args:
            channel_id: Channel ID

        Returns:
            List of user IDs (e.g., ["U1234", "U5678"])

        Raises:
            SlackApiError: If API call fails
            ValueError: If no token was provided during initialization

        Example:
            members = await slack.get_channel_members("C1234567890")
            for user_id in members:
                print(user_id)
        """
        self._ensure_client()
        from slack_sdk.errors import SlackApiError

        def _sync_call() -> list[str]:
            assert self.client is not None
            try:
                response = self.client.conversations_members(channel=channel_id)
                return response["members"]  # type: ignore[return-value]
            except SlackApiError as e:
                raise SlackApiError(
                    f"Failed to get members for {channel_id}: {e.response['error']}", e.response
                ) from e

        return await asyncio.to_thread(_sync_call)

    async def get_thread_messages(
        self, channel_id: str, thread_ts: str, limit: int = 100, enrich_users: bool = True
    ) -> list[dict[str, Any]]:
        """Get all messages in a thread with user names automatically populated.

        Args:
            channel_id: Channel ID
            thread_ts: Thread timestamp (from parent message)
            limit: Maximum messages to retrieve (default 100)
            enrich_users: If True (default), automatically adds 'user_name' and
                'user_real_name' to each message. Set to False to skip enrichment.

        Returns:
            List of message dictionaries with 'user', 'text', 'ts', 'user_name',
            'user_real_name', etc. The user_name and user_real_name fields allow
            agents to identify who said what without separate API calls.

        Raises:
            SlackApiError: If API call fails
            ValueError: If no token was provided during initialization

        Example:
            messages = await slack.get_thread_messages("C1234567890", "1234567890.123456")
            for msg in messages:
                print(f"{msg['user_real_name']}: {msg['text']}")
        """
        self._ensure_client()
        from slack_sdk.errors import SlackApiError

        def _sync_call() -> list[dict[str, Any]]:
            assert self.client is not None
            try:
                response = self.client.conversations_replies(
                    channel=channel_id, ts=thread_ts, limit=limit
                )
                return response["messages"]  # type: ignore[return-value]
            except SlackApiError as e:
                raise SlackApiError(
                    f"Failed to get thread messages for {channel_id}/{thread_ts}: {e.response['error']}",
                    e.response,
                ) from e

        messages = await asyncio.to_thread(_sync_call)

        if enrich_users:
            messages = await self._enrich_messages_with_user_names(messages)

        return messages

    async def get_channel_history(
        self, channel_id: str, limit: int = 50, enrich_users: bool = True
    ) -> list[dict[str, Any]]:
        """Get recent messages in a channel with user names automatically populated.

        Use this to get context about recent channel conversations when a user
        refers to something discussed earlier.

        Args:
            channel_id: Channel ID
            limit: Maximum messages to retrieve (default 50)
            enrich_users: If True (default), automatically adds 'user_name' and
                'user_real_name' to each message. Set to False to skip enrichment.

        Returns:
            List of message dictionaries with 'user', 'text', 'ts', 'user_name',
            'user_real_name', etc. Messages are returned in reverse chronological
            order (newest first). The user_name and user_real_name fields allow
            agents to identify who said what without separate API calls.

        Raises:
            SlackApiError: If API call fails
            ValueError: If no token was provided during initialization

        Example:
            messages = await slack.get_channel_history("C1234567890", limit=20)
            for msg in messages:
                print(f"{msg['user_real_name']}: {msg['text']}")
        """
        self._ensure_client()
        from slack_sdk.errors import SlackApiError

        def _sync_call() -> list[dict[str, Any]]:
            assert self.client is not None
            try:
                response = self.client.conversations_history(channel=channel_id, limit=limit)
                return response["messages"]  # type: ignore[return-value]
            except SlackApiError as e:
                raise SlackApiError(
                    f"Failed to get channel history for {channel_id}: {e.response['error']}",
                    e.response,
                ) from e

        messages = await asyncio.to_thread(_sync_call)

        if enrich_users:
            messages = await self._enrich_messages_with_user_names(messages)

        return messages

    async def reply_to_thread(
        self, channel_id: str, thread_ts: str, text: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Reply to a message thread.

        Args:
            channel_id: Channel ID
            thread_ts: Thread timestamp (from parent message)
            text: Reply text
            **kwargs: Additional arguments for chat.postMessage API

        Returns:
            Response dictionary with 'ts', 'channel', etc.

        Raises:
            SlackApiError: If API call fails

        Example:
            response = await slack.reply_to_thread(
                "C1234567890",
                "1234567890.123456",
                "Thanks for the update!"
            )
        """
        return await self.send_message(
            channel_id=channel_id, text=text, thread_ts=thread_ts, **kwargs
        )

    async def get_user_info(self, user_id: str) -> dict[str, Any]:
        """Get information about a user.

        Args:
            user_id: User ID

        Returns:
            User info dictionary with 'name', 'real_name', 'profile', etc.

        Raises:
            SlackApiError: If API call fails
            ValueError: If no token was provided during initialization

        Example:
            info = await slack.get_user_info("U1234567890")
            print(info['real_name'])  # "John Doe"
            print(info['profile']['email'])  # "john@example.com"
        """
        self._ensure_client()
        from slack_sdk.errors import SlackApiError

        def _sync_call() -> dict[str, Any]:
            assert self.client is not None
            try:
                response = self.client.users_info(user=user_id)
                return response["user"]  # type: ignore[return-value]
            except SlackApiError as e:
                raise SlackApiError(
                    f"Failed to get user info for {user_id}: {e.response['error']}", e.response
                ) from e

        return await asyncio.to_thread(_sync_call)

    async def update_message(
        self, channel_id: str, ts: str, text: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Update (edit) an existing Slack message.

        Args:
            channel_id: Channel ID where the message lives
            ts: Timestamp of the message to edit (from original send_message response)
            text: New message text
            **kwargs: Additional arguments for chat.update API

        Returns:
            Response dictionary with 'ts', 'channel', 'text', etc.

        Raises:
            SlackApiError: If API call fails
            ValueError: If no token was provided during initialization

        Example:
            resp = await slack.send_message("C1234567890", "Initial text")
            ts = resp['ts']
            await slack.update_message("C1234567890", ts, "Updated text")
        """
        self._ensure_client()
        from slack_sdk.errors import SlackApiError

        def _sync_call() -> dict[str, Any]:
            assert self.client is not None
            try:
                response = self.client.chat_update(channel=channel_id, ts=ts, text=text, **kwargs)
                return response.data  # type: ignore[return-value]
            except SlackApiError as e:
                raise SlackApiError(
                    f"Failed to update message {ts} in {channel_id}: {e.response['error']}",
                    e.response,
                ) from e

        return await asyncio.to_thread(_sync_call)

    async def get_channel_info(self, channel_id: str) -> dict[str, Any]:
        """Get information about a channel.

        Args:
            channel_id: Channel ID

        Returns:
            Channel info dictionary with 'name', 'topic', 'purpose', etc.

        Raises:
            SlackApiError: If API call fails
            ValueError: If no token was provided during initialization

        Example:
            info = await slack.get_channel_info("C1234567890")
            print(info['name'])  # "general"
        """
        self._ensure_client()
        from slack_sdk.errors import SlackApiError

        def _sync_call() -> dict[str, Any]:
            assert self.client is not None
            try:
                response = self.client.conversations_info(channel=channel_id)
                return response["channel"]  # type: ignore[return-value]
            except SlackApiError as e:
                raise SlackApiError(
                    f"Failed to get channel info for {channel_id}: {e.response['error']}",
                    e.response,
                ) from e

        return await asyncio.to_thread(_sync_call)
