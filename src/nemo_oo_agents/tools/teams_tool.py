# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Microsoft Teams integration tool for nemo_oo_agents.

Provides access to Teams meeting transcripts via Microsoft Graph API.
"""

import os
import re
from datetime import datetime, timedelta
from typing import Any


class TeamsTool:
    """Wrapper around Microsoft Graph API for Teams meeting transcripts.

    Configuration:
        MS_TEAMS_CLIENT_ID: Azure AD app client ID (required)
        MS_TEAMS_CLIENT_SECRET: Azure AD app client secret (required)
        MS_TEAMS_TENANT_ID: Azure AD tenant ID (required)

    Example:
        teams = TeamsTool()
        meetings = await teams.get_user_meetings("user@company.com")
        transcripts = await teams.list_transcripts(meeting_id)
        content = await teams.get_transcript_content(meeting_id, transcript_id)
    """

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        tenant_id: str | None = None,
    ):
        """Initialize Teams client with Azure AD credentials.

        Args:
            client_id: Azure AD app client ID. If None, reads from MS_TEAMS_CLIENT_ID env var.
            client_secret: Azure AD app client secret. If None, reads from MS_TEAMS_CLIENT_SECRET env var.
            tenant_id: Azure AD tenant ID. If None, reads from MS_TEAMS_TENANT_ID env var.

        Raises:
            ValueError: If any required credential is missing.
        """
        from azure.identity import ClientSecretCredential
        from msgraph import GraphServiceClient  # type: ignore[attr-defined]

        self.client_id = client_id or os.getenv("MS_TEAMS_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("MS_TEAMS_CLIENT_SECRET")
        self.tenant_id = tenant_id or os.getenv("MS_TEAMS_TENANT_ID")

        if not self.client_id:
            raise ValueError(
                "Azure AD client ID required. Provide client_id parameter or set MS_TEAMS_CLIENT_ID env var."
            )
        if not self.client_secret:
            raise ValueError(
                "Azure AD client secret required. Provide client_secret parameter or set MS_TEAMS_CLIENT_SECRET env var."
            )
        if not self.tenant_id:
            raise ValueError(
                "Azure AD tenant ID required. Provide tenant_id parameter or set MS_TEAMS_TENANT_ID env var."
            )

        # Create credential and client
        self._credential = ClientSecretCredential(
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            client_secret=self.client_secret,
        )

        # Scopes for Microsoft Graph API
        scopes = ["https://graph.microsoft.com/.default"]
        self._client = GraphServiceClient(credentials=self._credential, scopes=scopes)

    async def get_user_meetings(
        self,
        user_id: str,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get online meetings for a user.

        Args:
            user_id: User ID or email (e.g., "user@company.com")
            since: Only return meetings after this datetime (default: last 7 days)
            limit: Maximum number of meetings to return (default: 50)

        Returns:
            List of meeting dictionaries with id, subject, start_time, end_time, organizer, etc.

        Raises:
            ODataError: If API call fails

        Example:
            meetings = await teams.get_user_meetings("user@company.com", since=datetime.now() - timedelta(days=1))
            for meeting in meetings:
                print(f"{meeting['subject']} - {meeting['start_time']}")
        """
        from msgraph.generated.models.o_data_errors.o_data_error import ODataError

        if since is None:
            since = datetime.now() - timedelta(days=7)

        try:
            # Get user's online meetings
            # Note: This requires OnlineMeetings.Read.All permission
            result = await self._client.users.by_user_id(user_id).online_meetings.get()

            meetings = []
            if result and result.value:
                for meeting in result.value[:limit]:
                    meeting_dict = {
                        "id": meeting.id,
                        "subject": meeting.subject,
                        "start_time": meeting.start_date_time.isoformat()
                        if meeting.start_date_time
                        else None,
                        "end_time": meeting.end_date_time.isoformat()
                        if meeting.end_date_time
                        else None,
                        "join_url": meeting.join_web_url,
                        "video_teleconference_id": meeting.video_teleconference_id,
                    }

                    # Filter by date if specified
                    if meeting.start_date_time and meeting.start_date_time >= since:
                        meetings.append(meeting_dict)

            return meetings

        except ODataError as e:
            raise ODataError(
                f"Failed to get meetings for user {user_id}: {e.error.message if e.error else str(e)}"  # type: ignore[arg-type]
            ) from e

    async def list_transcripts(
        self,
        user_id: str,
        meeting_id: str,
    ) -> list[dict[str, Any]]:
        """List all transcripts for a meeting.

        Args:
            user_id: User ID or email of the meeting organizer
            meeting_id: Online meeting ID

        Returns:
            List of transcript dictionaries with id, created_time, content_url, etc.

        Raises:
            ODataError: If API call fails

        Example:
            transcripts = await teams.list_transcripts("user@company.com", "meeting-id")
            for t in transcripts:
                print(f"Transcript {t['id']} created at {t['created_time']}")
        """
        from msgraph.generated.models.o_data_errors.o_data_error import ODataError

        try:
            result = await (
                self._client.users.by_user_id(user_id)
                .online_meetings.by_online_meeting_id(meeting_id)
                .transcripts.get()
            )

            transcripts = []
            if result and result.value:
                for transcript in result.value:
                    transcripts.append(
                        {
                            "id": transcript.id,
                            "meeting_id": meeting_id,
                            "created_time": transcript.created_date_time.isoformat()
                            if transcript.created_date_time
                            else None,
                            "content_url": transcript.transcript_content_url,
                        }
                    )

            return transcripts

        except ODataError as e:
            raise ODataError(
                f"Failed to list transcripts for meeting {meeting_id}: {e.error.message if e.error else str(e)}"  # type: ignore[arg-type]
            ) from e

    async def get_transcript_content(
        self,
        user_id: str,
        meeting_id: str,
        transcript_id: str,
        format: str = "text/vtt",
    ) -> str:
        """Get the content of a transcript.

        Args:
            user_id: User ID or email of the meeting organizer
            meeting_id: Online meeting ID
            transcript_id: Transcript ID
            format: Content format (default: "text/vtt")

        Returns:
            Transcript content as string (VTT format by default)

        Raises:
            ODataError: If API call fails

        Example:
            content = await teams.get_transcript_content("user@company.com", "meeting-id", "transcript-id")
            print(content)  # VTT formatted transcript
        """
        from msgraph.generated.models.o_data_errors.o_data_error import ODataError

        try:
            # Get transcript content
            result = await (
                self._client.users.by_user_id(user_id)
                .online_meetings.by_online_meeting_id(meeting_id)
                .transcripts.by_call_transcript_id(transcript_id)
                .content.get()
            )

            if result:
                return result.decode("utf-8") if isinstance(result, bytes) else str(result)
            return ""

        except ODataError as e:
            raise ODataError(
                f"Failed to get transcript content for {transcript_id}: {e.error.message if e.error else str(e)}"  # type: ignore[arg-type]
            ) from e

    async def get_meeting_info(
        self,
        user_id: str,
        meeting_id: str,
    ) -> dict[str, Any]:
        """Get detailed information about a meeting.

        Args:
            user_id: User ID or email of the meeting organizer
            meeting_id: Online meeting ID

        Returns:
            Dictionary with meeting details (subject, participants, etc.)

        Raises:
            ODataError: If API call fails

        Example:
            info = await teams.get_meeting_info("user@company.com", "meeting-id")
            print(f"Meeting: {info['subject']}")
        """
        from msgraph.generated.models.o_data_errors.o_data_error import ODataError

        try:
            meeting = await (
                self._client.users.by_user_id(user_id)
                .online_meetings.by_online_meeting_id(meeting_id)
                .get()
            )

            if meeting:
                return {
                    "id": meeting.id,
                    "subject": meeting.subject,
                    "start_time": meeting.start_date_time.isoformat()
                    if meeting.start_date_time
                    else None,
                    "end_time": meeting.end_date_time.isoformat()
                    if meeting.end_date_time
                    else None,
                    "join_url": meeting.join_web_url,
                    "video_teleconference_id": meeting.video_teleconference_id,
                    "is_broadcast": meeting.is_broadcast,
                    "recording_status": getattr(meeting, "recording_status", None),
                }
            return {}

        except ODataError as e:
            raise ODataError(
                f"Failed to get meeting info for {meeting_id}: {e.error.message if e.error else str(e)}"  # type: ignore[arg-type]
            ) from e

    def parse_vtt_transcript(self, vtt_content: str) -> list[dict[str, Any]]:
        """Parse VTT transcript content into structured format.

        Args:
            vtt_content: Raw VTT transcript content

        Returns:
            List of dictionaries with speaker, timestamp, and text

        Example:
            entries = teams.parse_vtt_transcript(vtt_content)
            for entry in entries:
                print(f"[{entry['timestamp']}] {entry['speaker']}: {entry['text']}")
        """
        entries = []

        # VTT format: timestamp line followed by speaker: text
        # Example:
        # 00:00:05.000 --> 00:00:10.000
        # <v John Doe>Hello everyone, welcome to the meeting.

        lines = vtt_content.split("\n")
        current_timestamp = ""
        current_speaker = ""
        current_text = []

        timestamp_pattern = re.compile(r"(\d{2}:\d{2}:\d{2}\.\d{3}) --> (\d{2}:\d{2}:\d{2}\.\d{3})")
        speaker_pattern = re.compile(r"<v ([^>]+)>(.+)")

        for line in lines:
            line = line.strip()

            # Skip WEBVTT header and empty lines
            if not line or line == "WEBVTT" or line.startswith("NOTE"):
                continue

            # Check for timestamp
            timestamp_match = timestamp_pattern.match(line)
            if timestamp_match:
                # Save previous entry if exists
                if current_text:
                    entries.append(
                        {
                            "timestamp": current_timestamp,
                            "speaker": current_speaker,
                            "text": " ".join(current_text),
                        }
                    )
                    current_text = []

                current_timestamp = f"{timestamp_match.group(1)} - {timestamp_match.group(2)}"
                continue

            # Check for speaker and text
            speaker_match = speaker_pattern.match(line)
            if speaker_match:
                current_speaker = speaker_match.group(1)
                current_text.append(speaker_match.group(2))
            elif line and not line.isdigit():  # Skip cue identifiers (numbers)
                # Continuation of previous text
                current_text.append(line)

        # Don't forget the last entry
        if current_text:
            entries.append(
                {
                    "timestamp": current_timestamp,
                    "speaker": current_speaker,
                    "text": " ".join(current_text),
                }
            )

        return entries

    def format_transcript_as_text(self, vtt_content: str) -> str:
        """Convert VTT transcript to readable text format.

        Args:
            vtt_content: Raw VTT transcript content

        Returns:
            Human-readable transcript with speakers and text

        Example:
            readable = teams.format_transcript_as_text(vtt_content)
            print(readable)
            # John Doe: Hello everyone, welcome to the meeting.
            # Jane Smith: Thanks John, let's get started.
        """
        entries = self.parse_vtt_transcript(vtt_content)

        # Group consecutive entries by same speaker
        grouped = []
        current_speaker = None
        current_texts = []

        for entry in entries:
            if entry["speaker"] != current_speaker:
                if current_texts:
                    grouped.append({"speaker": current_speaker, "text": " ".join(current_texts)})
                current_speaker = entry["speaker"]
                current_texts = [entry["text"]]
            else:
                current_texts.append(entry["text"])

        # Add last group
        if current_texts:
            grouped.append({"speaker": current_speaker, "text": " ".join(current_texts)})

        # Format as readable text
        lines = []
        for entry in grouped:
            speaker = entry["speaker"] or "Unknown"
            lines.append(f"{speaker}: {entry['text']}")

        return "\n\n".join(lines)
