import uvicorn
import datetime
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel
from typing import List, Optional

# --- Google API Imports ---
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as AuthRequest
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from settings import settings

app = FastAPI()

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SESSION_SECRET_KEY,
    https_only=False,
)

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CLIENT_CONFIG = {
    "web": {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://127.0.0.1:8000/auth/google/callback"],
    }
}

# --- Helper Function (NEW) ---

def get_google_credentials(request: Request) -> Credentials:
    """
    Gets credentials from session, refreshing them if necessary.
    Also updates the session with the new token if refreshed.
    """
    creds_dict = request.session.get("credentials")
    if not creds_dict:
        raise HTTPException(
            status_code=401, 
            detail="User not authenticated. Please go to /auth/google"
        )

    # Re-create the Credentials object from the session dictionary
    creds = Credentials.from_authorized_user_info(creds_dict, SCOPES)

    # Check if the token is expired and refresh it
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(AuthRequest())
            # Update the session with the new, refreshed credentials
            request.session["credentials"] = {
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": creds.scopes,
            }
        except Exception as e:
            # If refresh fails, the user must re-authenticate
            request.session.pop("credentials", None)
            raise HTTPException(
                status_code=401,
                detail=f"Could not refresh token. Please re-authenticate. Error: {e}"
            )
            
    return creds


# --- OAuth 2.0 Endpoints (Unchanged) ---

@app.get("/auth/google")
async def auth_google(request: Request):
    flow = Flow.from_client_config(
        client_config=CLIENT_CONFIG,
        scopes=SCOPES,
        redirect_uri="http://127.0.0.1:8000/auth/google/callback",
    )
    authorization_url, state = flow.authorization_url(
        access_type="offline", prompt="consent",
    )
    request.session["state"] = state
    return RedirectResponse(authorization_url)


@app.get("/auth/google/callback")
async def auth_google_callback(request: Request, state: str, code: str):
    session_state = request.session.get("state")
    if not session_state or session_state != state:
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    flow = Flow.from_client_config(
        client_config=CLIENT_CONFIG,
        scopes=SCOPES,
        redirect_uri="http://127.0.0.1:8000/auth/google/callback",
    )
    flow.fetch_token(code=code)
    credentials = flow.credentials

    # Store in session (as a serializable dict)
    request.session["credentials"] = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
    }
    return {"message": "Authentication successful! You can now use the API."}

# --- READ (Get Events) ---
class GetEventsInput(BaseModel):
    # Use ISO 8601 format: "2025-10-31T00:00:00Z"
    timeMin: str = "now"
    timeMax: Optional[str] = None
    maxResults: int = 10

@app.post("/mcp/resources/getEvents")
async def mcp_get_events(request: Request, inputs: GetEventsInput):
    """
    MCP Resource: Gets events from the user's primary calendar.
    """
    try:
        creds = get_google_credentials(request)
        service = build("calendar", "v3", credentials=creds)

        # Handle "now" case
        if inputs.timeMin == "now":
            inputs.timeMin = datetime.datetime.utcnow().isoformat() + "Z"

        print(f"Fetching events from {inputs.timeMin} to {inputs.timeMax}")

        events_result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=inputs.timeMin,
                timeMax=inputs.timeMax,
                maxResults=inputs.maxResults,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        
        events = events_result.get("items", [])
        
        # Format for the MCP client
        formatted_events = [
            {
                "id": event["id"],
                "summary": event.get("summary", "No Title"),
                "start": event["start"].get("dateTime", event["start"].get("date")),
                "end": event["end"].get("dateTime", event["end"].get("date")),
            }
            for event in events
        ]
        
        return {"events": formatted_events}

    except HttpError as e:
        raise HTTPException(status_code=e.status_code, detail=f"Google API Error: {e.reason}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- CREATE (Create Event) ---
class CreateEventInput(BaseModel):
    summary: str
    start_time: str  # e.g., "2025-10-31T10:00:00Z"
    end_time: str    # e.g., "2025-10-31T11:00:00Z"
    location: Optional[str] = None
    description: Optional[str] = None
    attendees: Optional[List[str]] = None # List of email addresses

@app.post("/mcp/tools/createEvent")
async def mcp_create_event(request: Request, inputs: CreateEventInput):
    """
    MCP Tool: Creates a new event in the user's primary calendar.
    """
    try:
        creds = get_google_credentials(request)
        service = build("calendar", "v3", credentials=creds)

        # 1. Translate MCP input to Google Calendar API format
        event_body = {
            "summary": inputs.summary,
            "location": inputs.location,
            "description": inputs.description,
            "start": {"dateTime": inputs.start_time, "timeZone": "UTC"},
            "end": {"dateTime": inputs.end_time, "timeZone": "UTC"},
        }
        
        if inputs.attendees:
            event_body["attendees"] = [{"email": email} for email in inputs.attendees]

        # 2. Call the Google Calendar API
        print(f"Creating event: {inputs.summary}")
        created_event = (
            service.events()
            .insert(
                calendarId="primary",
                body=event_body,
                sendNotifications=True,  # Sends email invites to attendees
            )
            .execute()
        )

        # 3. Return a clean MCP-formatted response
        return {
            "status": "success",
            "id": created_event["id"],
            "summary": created_event.get("summary"),
            "htmlLink": created_event.get("htmlLink"),
        }

    except HttpError as e:
        raise HTTPException(status_code=e.status_code, detail=f"Google API Error: {e.reason}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
    if __name__ == "__main__":
        uvicorn.run(app, host="127.0.0.1", port=8000)