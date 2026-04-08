# Microsoft Teams Integration Setup

This guide explains how to set up the Microsoft Teams integration for accessing meeting transcripts via the TPM agent.

## Prerequisites

- An Azure subscription with access to Azure Active Directory (now Microsoft Entra ID)
- Admin consent capabilities for your organization (or work with your IT admin)
- Teams meetings with transcription enabled

## Step 1: Register an Azure AD Application

1. Go to the [Azure Portal](https://portal.azure.com)
2. Navigate to **Microsoft Entra ID** (formerly Azure Active Directory)
3. Select **App registrations** → **New registration**
4. Fill in the details:
   - **Name**: `TPM Agent - Teams Integration` (or your preferred name)
   - **Supported account types**: Select based on your organization's needs
     - For single-tenant (recommended): "Accounts in this organizational directory only"
   - **Redirect URI**: Leave blank (not needed for application permissions)
5. Click **Register**

## Step 2: Note Your Application Details

After registration, you'll need these values:

| Value | Where to Find |
|-------|---------------|
| **Client ID** | Application (client) ID on the Overview page |
| **Tenant ID** | Directory (tenant) ID on the Overview page |

## Step 3: Create a Client Secret

1. In your app registration, go to **Certificates & secrets**
2. Click **New client secret**
3. Add a description (e.g., "TPM Agent Secret")
4. Choose an expiration period
5. Click **Add**
6. **Important**: Copy the secret value immediately - you won't be able to see it again!

## Step 4: Configure API Permissions

1. Go to **API permissions** → **Add a permission**
2. Select **Microsoft Graph**
3. Choose **Application permissions** (not delegated)
4. Add these permissions:

| Permission | Purpose |
|------------|---------|
| `OnlineMeetingTranscript.Read.All` | Read meeting transcripts |
| `OnlineMeetings.Read.All` | List user meetings |
| `User.Read.All` | Look up user information |

5. Click **Add permissions**
6. Click **Grant admin consent for [Your Organization]** (requires admin privileges)
7. Confirm all permissions show a green checkmark

## Step 5: Configure Environment Variables

Add these to your `.env` file:

```bash
# Microsoft Teams Integration
MS_TEAMS_CLIENT_ID=your-client-id-here
MS_TEAMS_CLIENT_SECRET=your-client-secret-here
MS_TEAMS_TENANT_ID=your-tenant-id-here
```

## Step 6: Verify the Setup

Run a quick test to verify the integration:

```python
import asyncio
from nemo_oo_agents.tools.teams_tool import TeamsTool

async def test_teams():
    teams = TeamsTool()

    # Try to get meetings for a user (replace with a real user email)
    meetings = await teams.get_user_meetings("user@yourcompany.com")
    print(f"Found {len(meetings)} meetings")

    for meeting in meetings[:5]:
        print(f"  - {meeting['subject']}")

asyncio.run(test_teams())
```

## Enabling Meeting Transcription

For meetings to have transcripts available, transcription must be enabled:

### For Individual Meetings
- Meeting organizers can enable transcription from the meeting options
- During a meeting: Click **More actions (...)** → **Record and transcribe** → **Start transcription**

### Organization-Wide Policy (IT Admin)
1. Go to [Teams Admin Center](https://admin.teams.microsoft.com)
2. Navigate to **Meetings** → **Meeting policies**
3. Enable **Transcription** in your policy

## Webhook Setup (Optional)

For automatic notifications when transcripts are ready, set up a webhook subscription:

1. Your webhook endpoint must be publicly accessible (use ngrok for development)
2. Subscribe to change notifications for:
   ```
   communications/onlineMeetings/{meetingId}/transcripts
   ```
3. See [Microsoft Graph Change Notifications](https://learn.microsoft.com/en-us/graph/teams-changenotifications-callrecording-and-calltranscript) for details

## Troubleshooting

### "Insufficient privileges" Error
- Ensure admin consent was granted for all permissions
- Verify the permissions show green checkmarks in the Azure portal

### "User not found" Error
- The user email must match exactly what's in Azure AD
- Try using the user's Object ID instead of email

### No Transcripts Available
- Verify transcription was enabled during the meeting
- Transcripts are only available after the meeting ends
- Check that the meeting hasn't expired (Teams has retention limits)

### Token Expired
- Client secrets expire - check if yours needs renewal
- The SDK handles token refresh automatically, but the secret itself expires

## API Rate Limits

Microsoft Graph has rate limits. The SDK handles throttling automatically, but be aware:
- Limit concurrent requests to avoid 429 errors
- For bulk operations, consider batching requests

## Security Best Practices

1. **Never commit credentials** - Use environment variables
2. **Rotate secrets regularly** - Set up a renewal schedule
3. **Use minimum permissions** - Only request the permissions you need
4. **Monitor access** - Review Azure AD sign-in logs periodically

## Resources

- [Microsoft Graph API - Meeting Transcripts](https://learn.microsoft.com/en-us/microsoftteams/platform/graph-api/meeting-transcripts/overview-transcripts)
- [Get callTranscript API](https://learn.microsoft.com/en-us/graph/api/calltranscript-get)
- [Microsoft Graph Python SDK](https://github.com/microsoftgraph/msgraph-sdk-python)
- [Azure Identity for Python](https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity)
