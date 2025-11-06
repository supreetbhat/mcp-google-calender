# main.py
import uvicorn
import datetime
import sys
import argparse
import datetime
from fastapi import FastAPI, Request, HTTPException, Depends # We still need HTTPException for the auth server
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import List, Optional

# --- MCP Imports ---
from mcp.server.fastmcp import FastMCP # This is the main SDK class


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
mcp = FastMCP("google_calendar")
auth_app = FastAPI()

# --- Dependencies (FOR AUTH APP ONLY) ---
def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()

SCOPES = [
    "https://www.googleapis.com/auth/calendar", 
    "https://www.googleapis.com/auth/userinfo.email", 
    "openid",
    "https://www.googleapis.com/auth/gmail.readonly"
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
def get_google_credentials() -> Credentials:
    """
    Manually creates a DB session, retrieves the stored refresh_token,
    and returns fresh, valid Google Credentials.
    """
    db = database.SessionLocal()
    try:
        token_row = db.query(models.TokenStorage).filter(models.TokenStorage.id == 1).first()
        
        if not token_row or not token_row.refresh_token:
            print("ERROR: No refresh token found. Run 'python main.py --auth'", file=sys.stderr)
            # Raise a standard Exception for FastMCP to catch
            raise Exception("Server not authenticated. Please run 'python main.py --auth' in your terminal.")

        creds = Credentials.from_authorized_user_info({
            "refresh_token": token_row.refresh_token,
            "token_uri": CLIENT_CONFIG["web"]["token_uri"],
            "client_id": CLIENT_CONFIG["web"]["client_id"],
            "client_secret": CLIENT_CONFIG["web"]["client_secret"],
        }, SCOPES)

        creds.refresh(AuthRequest())
        return creds
    
    except Exception as e:
        print(f"ERROR: Could not get credentials. Run 'python main.py --auth'. Error: {e}", file=sys.stderr)
        raise Exception(f"Could not get credentials. Please re-authenticate via 'python main.py --auth'. Original error: {e}")
    finally:
        db.close()



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

class PotentialEvent(BaseModel):
    source: str = "gmail"
    subject: str
    snippet: str
    message_id: str

def scan_gmail_for_potential_events(
    creds: Credentials,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> List[PotentialEvent]:
    """
    Scans Gmail for potential events and returns them as a list.
    """
    try:
        service = build("gmail", "v1", credentials=creds)
        
        # 1. Search for emails with keywords
        # We'll combine multiple queries with { }
        # This looks for unread emails in the inbox with event-related keywords
        # Limit scan to past 30 days by default
# --- Build Gmail search query ---
        if start_date and end_date:
            query = f"in:inbox after:{start_date} before:{end_date} {{flight confirmation booking hotel reservation zoom.us/j meet.google.com/}}"
        else:
            # Default: last 30 days
            thirty_days_ago = (datetime.datetime.utcnow() - datetime.timedelta(days=30)).strftime("%Y/%m/%d")
            query = f"in:inbox after:{thirty_days_ago} {{flight confirmation booking hotel reservation zoom.us/j meet.google.com/}}"


        response = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=10  # Limit to 10 potential events
        ).execute()
        
        messages = response.get("messages", [])
        potential_events = []
        
        if not messages:
            return [] # No potential events found

        # 2. Get details for each message
        for msg in messages:
            msg_data = service.users().messages().get(
                userId="me", 
                id=msg["id"], 
                format="metadata" # We only need headers and snippet, not the full body
            ).execute()
            
            payload = msg_data.get("payload", {})
            headers = payload.get("headers", [])
            
            subject = ""
            for h in headers:
                if h["name"].lower() == "subject":
                    subject = h["value"]
                    break
            
            snippet = msg_data.get("snippet", "No snippet available.")
            
            potential_events.append(
                PotentialEvent(
                    subject=subject,
                    snippet=snippet,
                    message_id=msg["id"]
                )
            )
            
        return potential_events
        
    except HttpError as e:
        # If Gmail API fails, just log it and return an empty list
        print(f"ERROR: Could not scan Gmail. {e}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"ERROR: Unexpected error in Gmail scan. {e}", file=sys.stderr)
        return []
# --- MCP Endpoints (UPDATED with correct Error Handling) ---

@mcp.tool()
async def get_events(
    inputs: GetEventsInput
) -> dict: # The return type is a generic dict
    """
    Get events from the user's Google Calendar AND scan Gmail for potential events.
    
    Args:
        inputs: The event filter for the calendar.
    """
    calendar_events_list = []
    potential_events_list = []
    
    try:
        # --- 1. Get Credentials ---
        # We get the credentials once, which now have both calendar and gmail scopes
        creds = get_google_credentials()

        # --- 2. Get Calendar Events (Original Logic) ---
        try:
            service_cal = build("calendar", "v3", credentials=creds)
            if inputs.timeMin == "now":
                inputs.timeMin = datetime.datetime.utcnow().isoformat() + "Z"
            
            events_result = service_cal.events().list(
                calendarId="primary", timeMin=inputs.timeMin, timeMax=inputs.timeMax,
                maxResults=inputs.maxResults, singleEvents=True, orderBy="startTime",
            ).execute()
            events = events_result.get("items", [])
            calendar_events_list = [
                {
                    "id": event["id"],
                    "summary": event.get("summary", "No Title"),
                    "start": event["start"].get("dateTime", event["start"].get("date")),
                    "end": event["end"].get("dateTime", event["end"].get("date")),
                }
                for event in events
            ]
        except HttpError as e:
            # If calendar fails, we can still try Gmail
            print(f"ERROR: Could not get calendar events. {e}", file=sys.stderr)
        
        # --- 3. Get Potential Gmail Events (New Logic) ---
        # We call our new sync helper function using the same credentials
        potential_events_list = scan_gmail_for_potential_events(
            creds,
            start_date=inputs.timeMin,
            end_date=inputs.timeMax
        )


        # --- 4. Return Combined Results ---
        return {
            "calendar_events": calendar_events_list,
            "potential_events_from_gmail": [event.model_dump() for event in potential_events_list]
        }
        
    except Exception as e:
        # This catches any errors from get_google_credentials() or other unexpected issues
        print(f"ERROR in get_events: {e}", file=sys.stderr)
        raise Exception(f"Failed to get events: {e}")

@mcp.tool()
async def create_event(inputs: CreateEventInput) -> dict:
    """
    Creates a new event in the user's primary calendar.
    
    Args:
        inputs: The details for the new event.
    """
    try:
        creds = get_google_credentials()
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
        raise Exception(f"Google API Error: {e.reason}")
    except Exception as e:
        raise e

@mcp.tool()
async def update_event(inputs: PatchEventInput) -> dict:
    """
    Updates an existing event in the user's primary calendar.
    
    Args:
        inputs: The event ID and the fields to update.
    """
    try:
        creds = get_google_credentials()
        service = build("calendar", "v3", credentials=creds)
        update_body = inputs.updates.model_dump(exclude_unset=True)
        
        if "start_time" in update_body:
            update_body["start"] = {"dateTime": update_body.pop("start_time"), "timeZone": "UTC"}
        if "end_time" in update_body:
            update_body["end"] = {"dateTime": update_body.pop("end_time"), "timeZone": "UTC"}

        if not update_body:
            raise Exception("No update fields provided.")

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
        raise Exception(f"Google API Error: {e.reason}")
    except Exception as e:
        raise e

@mcp.tool()
async def delete_event(inputs: DeleteEventInput) -> dict:
    """
    Deletes an event from the user's primary calendar.
    
    Args:
        inputs: The ID of the event to delete.
    """
    try:
        creds = get_google_credentials()
        service = build("calendar", "v3", credentials=creds)
        
        service.events().delete(
            calendarId="primary", eventId=inputs.event_id, sendNotifications=True
        ).execute()

        return {"status": "success", "deleted_event_id": inputs.event_id}
    except HttpError as e:
        if e.status_code == 404:
            raise Exception("Event not found.")
        raise Exception(f"Google API Error: {e.reason}")
    except Exception as e:
        raise e


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
        mcp.run(transport='stdio')

if __name__ == "__main__":
    main()