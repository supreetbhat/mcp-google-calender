# main.py
import uvicorn
import datetime
import sys
import argparse
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import List, Optional

# --- MCP Imports ---
from mcp.server.fastmcp import FastMCP # This is the main SDK class
# NO MORE JSON-RPC IMPORTS NEEDED

# --- Database Imports ---
from sqlalchemy.orm import Session
import models
import database

# --- Auth Imports ---
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# --- Google API Imports ---
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as AuthRequest
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from settings import settings

# --- Database Setup ---
models.Base.metadata.create_all(bind=database.engine)

# --- Server Definitions ---
# 1. The MCP Server (for Claude)
mcp = FastMCP("google_calendar")

# 2. The Auth Server (for one-time login)
auth_app = FastAPI()

# --- Dependencies ---
def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()

bearer_scheme = HTTPBearer() # We still need this for the *auth* server

SCOPES = [
    "https.www.googleapis.com/auth/calendar", 
    "https.www.googleapis.com/auth/userinfo.email", 
    "openid"
]
CLIENT_CONFIG = {
    "web": {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://127.0.0.1:8000/auth/google/callback"],
    }
}

# --- Helper Function (The "Bouncer") ---
def get_google_credentials(db: Session = Depends(get_db)) -> Credentials:
    """
    Retrieves the stored refresh_token from the database
    and returns fresh, valid Google Credentials.
    
    This is now a dependency for our MCP tools.
    """
    token_row = db.query(models.TokenStorage).filter(models.TokenStorage.id == 1).first()
    
    if not token_row or not token_row.refresh_token:
        print("ERROR: No refresh token found. Run 'python main.py --auth'", file=sys.stderr)
        # FastMCP will catch HTTPException and convert it to an MCP error
        raise HTTPException(status_code=401, detail="Server not authenticated. Please run 'python main.py --auth' in your terminal.")

    creds = Credentials.from_authorized_user_info({
        "refresh_token": token_row.refresh_token,
        "token_uri": CLIENT_CONFIG["web"]["token_uri"],
        "client_id": CLIENT_CONFIG["web"]["client_id"],
        "client_secret": CLIENT_CONFIG["web"]["client_secret"],
    }, SCOPES)

    try:
        creds.refresh(AuthRequest())
    except Exception as e:
        print(f"ERROR: Could not refresh token. Run 'python main.py --auth'. Error: {e}", file=sys.stderr)
        raise HTTPException(status_code=401, detail=f"Could not refresh token. Please re-authenticate via 'python main.py --auth'.")
            
    return creds


# --- MCP Pydantic Models (Unchanged) ---
class GetEventsInput(BaseModel):
    timeMin: str = "now"
    timeMax: Optional[str] = None
    maxResults: int = 10

class CreateEventInput(BaseModel):
    summary: str
    start_time: str
    end_time: str
    location: Optional[str] = None
    description: Optional[str] = None
    attendees: Optional[List[str]] = None

class UpdateEventInput(BaseModel):
    summary: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None

class PatchEventInput(BaseModel):
    event_id: str
    updates: UpdateEventInput

class DeleteEventInput(BaseModel):
    event_id: str


# --- MCP Endpoints (Registered with FastMCP) ---

@mcp.tool()
async def get_events(
    inputs: GetEventsInput, 
    creds: Credentials = Depends(get_google_credentials) # Auth works the same!
) -> dict:
    """
    Get events from the user's Google Calendar.
    
    Args:
        inputs: The event filter.
    """
    try:
        service = build("calendar", "v3", credentials=creds)
        if inputs.timeMin == "now":
            inputs.timeMin = datetime.datetime.utcnow().isoformat() + "Z"
        
        events_result = service.events().list(
            calendarId="primary", timeMin=inputs.timeMin, timeMax=inputs.timeMax,
            maxResults=inputs.maxResults, singleEvents=True, orderBy="startTime",
        ).execute()
        events = events_result.get("items", [])
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

@mcp.tool()
async def create_event(
    inputs: CreateEventInput,
    creds: Credentials = Depends(get_google_credentials)
) -> dict:
    """
    Creates a new event in the user's primary calendar.
    
    Args:
        inputs: The details for the new event.
    """
    try:
        service = build("calendar", "v3", credentials=creds)
        event_body = {
            "summary": inputs.summary,
            "location": inputs.location,
            "description": inputs.description,
            "start": {"dateTime": inputs.start_time, "timeZone": "UTC"},
            "end": {"dateTime": inputs.end_time, "timeZone": "UTC"},
        }
        if inputs.attendees:
            event_body["attendees"] = [{"email": email} for email in inputs.attendees]

        created_event = service.events().insert(
            calendarId="primary", body=event_body, sendNotifications=True
        ).execute()

        return {
            "status": "success", "id": created_event["id"],
            "summary": created_event.get("summary"),
            "htmlLink": created_event.get("htmlLink"),
        }
    except HttpError as e:
        raise HTTPException(status_code=e.status_code, detail=f"Google API Error: {e.reason}")

@mcp.tool()
async def update_event(
    inputs: PatchEventInput,
    creds: Credentials = Depends(get_google_credentials)
) -> dict:
    """
    Updates an existing event in the user's primary calendar.
    
    Args:
        inputs: The event ID and the fields to update.
    """
    try:
        service = build("calendar", "v3", credentials=creds)
        update_body = inputs.updates.model_dump(exclude_unset=True)
        
        if "start_time" in update_body:
            update_body["start"] = {"dateTime": update_body.pop("start_time"), "timeZone": "UTC"}
        if "end_time" in update_body:
            update_body["end"] = {"dateTime": update_body.pop("end_time"), "timeZone": "UTC"}

        if not update_body:
            raise HTTPException(status_code=400, detail="No update fields provided.")

        updated_event = service.events().patch(
            calendarId="primary", eventId=inputs.event_id,
            body=update_body, sendNotifications=True
        ).execute()

        return {
            "status": "success", "id": updated_event["id"],
            "summary": updated_event.get("summary"),
            "htmlLink": updated_event.get("htmlLink"),
        }
    except HttpError as e:
        raise HTTPException(status_code=e.status_code, detail=f"Google API Error: {e.reason}")

@mcp.tool()
async def delete_event(
    inputs: DeleteEventInput,
    creds: Credentials = Depends(get_google_credentials)
) -> dict:
    """
    Deletes an event from the user's primary calendar.
    
    Args:
        inputs: The ID of the event to delete.
    """
    try:
        service = build("calendar", "v3", credentials=creds)
        
        service.events().delete(
            calendarId="primary", eventId=inputs.event_id, sendNotifications=True
        ).execute()

        return {"status": "success", "deleted_event_id": inputs.event_id}
    except HttpError as e:
        if e.status_code == 404:
            raise HTTPException(status_code=404, detail="Event not found.")
        raise HTTPException(status_code=e.status_code, detail=f"Google API Error: {e.reason}")


# --- Auth Server Endpoints (for one-time browser login) ---
@auth_app.get("/auth/google")
async def auth_google(request: Request):
    flow = Flow.from_client_config(
        client_config=CLIENT_CONFIG,
        scopes=SCOPES,
        redirect_uri="http://127.0.0.1:8000/auth/google/callback",
    )
    authorization_url, state = flow.authorization_url(
        access_type="offline", prompt="consent",
    )
    return RedirectResponse(authorization_url)

@auth_app.get("/auth/google/callback")
async def auth_google_callback(request: Request, code: str, db: Session = Depends(get_db)):
    flow = Flow.from_client_config(
        client_config=CLIENT_CONFIG,
        scopes=SCOPES,
        redirect_uri="http://127.0.0.1:8000/auth/google/callback",
    )
    flow.fetch_token(code=code)
    credentials = flow.credentials

    if not credentials.refresh_token:
        raise HTTPException(status_code=400, detail="Refresh token not granted. You may need to revoke access and try again.")
    
    token_row = db.query(models.TokenStorage).filter(models.TokenStorage.id == 1).first()
    
    if token_row:
        token_row.refresh_token = credentials.refresh_token
    else:
        token_row = models.TokenStorage(id=1, refresh_token=credentials.refresh_token)
        db.add(token_row)
    
    db.commit()

    return {"message": "Authentication successful! You can now close this browser tab and configure Claude Desktop."}


# --- Main entrypoint to run the server ---
def main():
    parser = argparse.ArgumentParser(description="Google Calendar MCP Server")
    parser.add_argument(
        '--auth', 
        action='store_true', 
        help='Run the one-time web server to authenticate with Google.'
    )
    args = parser.parse_args()

    if args.auth:
        print("Starting one-time auth server on http://127.0.0.1:8000")
        print("Please open http://127.0.0.1:8000/auth/google in your browser.")
        uvicorn.run(auth_app, host="127.0.0.1", port=8000)
    else:
        # This is the default. It runs the MCP server for Claude Desktop.
        # It communicates over stdio, not HTTP.
        # Do not add print() statements here, as they break JSON-RPC.
        mcp.run(transport='stdio')

if __name__ == "__main__":
    main()