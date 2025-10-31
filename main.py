# main.py
import uvicorn
import datetime
import uuid  # For generating API keys
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import List, Optional

# --- New Database Imports ---
from sqlalchemy.orm import Session
from . import models, database  # Import our new files

# --- New Auth Imports ---
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# --- Google API Imports ---
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as AuthRequest
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from settings import settings

# --- Database Setup ---
# This line creates the 'sql_app.db' file and the 'users' table
models.Base.metadata.create_all(bind=database.engine)
app = FastAPI()

# Dependency to get a DB session for each request
def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- Auth Setup ---
# We MUST add userinfo.email and openid to get the user's email
SCOPES = [
    "https://www.googleapis.com/auth/calendar", 
    "https://www.googleapis.com/auth/userinfo.email", 
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
# This tells FastAPI to look for an 'Authorization: Bearer <token>' header
bearer_scheme = HTTPBearer()

# --- Helper Function (COMPLETELY REWRITTEN) ---
def get_google_credentials(
    # This is our new "Bouncer". It depends on two things:
    # 1. The Bearer token (API key) from the client
    # 2. A connection to the database
    token: HTTPAuthorizationCredentials = Depends(bearer_scheme), 
    db: Session = Depends(get_db)
) -> Credentials:
    """
    Finds a user by their API key (Bearer token),
    retrieves their stored refresh_token,
    and returns fresh, valid Google Credentials.
    """
    api_key = token.credentials
    # 1. Find the user in the DB
    user = db.query(models.User).filter(models.User.api_key == api_key).first()
    
    if not user:
        raise HTTPException(
            status_code=401, 
            detail="Invalid API Key. Please authenticate at /auth/google"
        )

    # 2. Re-create the Credentials object from the stored refresh_token
    creds = Credentials.from_authorized_user_info({
        "refresh_token": user.refresh_token,
        "token_uri": CLIENT_CONFIG["web"]["token_uri"],
        "client_id": CLIENT_CONFIG["web"]["client_id"],
        "client_secret": CLIENT_CONFIG["web"]["client_secret"],
    }, SCOPES)

    # 3. Refresh the access_token (it's always expired when we build from refresh)
    try:
        creds.refresh(AuthRequest())
    except Exception as e:
        raise HTTPException(
            status_code=401,
            detail=f"Could not refresh token. User may have revoked access. Please re-authenticate. Error: {e}"
        )
            
    return creds


# --- OAuth 2.0 Endpoints (HEAVILY MODIFIED) ---
@app.get("/auth/google")
async def auth_google(request: Request):
    """
    Starts the OAuth flow. Redirects user to Google.
    """
    flow = Flow.from_client_config(
        client_config=CLIENT_CONFIG,
        scopes=SCOPES,
        redirect_uri="http://127.0.0.1:8000/auth/google/callback",
    )
    authorization_url, state = flow.authorization_url(
        access_type="offline", prompt="consent",
    )
    
    # We've removed the session, so state validation is more complex.
    # For this project, we'll skip state validation.
    
    return RedirectResponse(authorization_url)


@app.get("/auth/google/callback")
async def auth_google_callback(request: Request, code: str, db: Session = Depends(get_db)):
    """
    Callback from Google. We exchange the code for tokens,
    find or create the user in our DB, and return their new API key.
    """
    # Note: State validation is skipped for simplicity
    
    flow = Flow.from_client_config(
        client_config=CLIENT_CONFIG,
        scopes=SCOPES,
        redirect_uri="http://127.0.0.1:8000/auth/google/callback",
    )
    flow.fetch_token(code=code)
    credentials = flow.credentials

    # Get the user's email to identify them in our DB
    try:
        user_info_service = build('oauth2', 'v2', credentials=credentials)
        user_info = user_info_service.userinfo().get().execute()
        email = user_info['email']
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not get user email from Google: {e}")

    if not email:
        raise HTTPException(status_code=400, detail="No email found in Google token.")

    # Find user in our DB, or create them
    user = db.query(models.User).filter(models.User.email == email).first()
    
    if user:
        # User exists, just update their refresh token
        print(f"User {email} found. Updating refresh token.")
        user.refresh_token = credentials.refresh_token
    else:
        # New user, create them with a new API key
        print(f"New user {email}. Creating database entry.")
        user = models.User(
            email=email,
            refresh_token=credentials.refresh_token,
            api_key=str(uuid.uuid4())  # Generate a new, unique API key
        )
        db.add(user)
    
    db.commit()
    db.refresh(user)

    # THIS IS THE MOST IMPORTANT PART:
    # We return the API key to the client.
    # The client (Claude) MUST save this key for all future requests.
    return {
        "message": "Authentication successful! Save this API key.",
        "api_key": user.api_key,
        "user_email": user.email
    }


# --- CRUD Endpoints (NEW SIGNATURES) ---
# All these endpoints no longer take 'request: Request'.
# They now get credentials by "Depending" on our new helper.

# --- READ (Get Events) ---
class GetEventsInput(BaseModel):
    timeMin: str = "now"
    timeMax: Optional[str] = None
    maxResults: int = 10

@app.post("/mcp/resources/getEvents")
async def mcp_get_events(
    inputs: GetEventsInput, 
    creds: Credentials = Depends(get_google_credentials) # New Auth!
):
    try:
        # 'creds' is now magically provided and valid!
        service = build("calendar", "v3", credentials=creds)
        
        if inputs.timeMin == "now":
            inputs.timeMin = datetime.datetime.utcnow().isoformat() + "Z"

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

# --- CREATE (Create Event) ---
class CreateEventInput(BaseModel):
    summary: str
    start_time: str
    end_time: str
    location: Optional[str] = None
    description: Optional[str] = None
    attendees: Optional[List[str]] = None

@app.post("/mcp/tools/createEvent")
async def mcp_create_event(
    inputs: CreateEventInput,
    creds: Credentials = Depends(get_google_credentials) # New Auth!
):
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
            calendarId="primary",
            body=event_body,
            sendNotifications=True,
        ).execute()

        return {
            "status": "success",
            "id": created_event["id"],
            "summary": created_event.get("summary"),
            "htmlLink": created_event.get("htmlLink"),
        }
    except HttpError as e:
        raise HTTPException(status_code=e.status_code, detail=f"Google API Error: {e.reason}")
    
# --- UPDATE (Update Event) ---
class UpdateEventInput(BaseModel):
    summary: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None

class PatchEventInput(BaseModel):
    event_id: str
    updates: UpdateEventInput

@app.post("/mcp/tools/updateEvent")
async def mcp_update_event(
    inputs: PatchEventInput,
    creds: Credentials = Depends(get_google_credentials) # New Auth!
):
    try:
        service = build("calendar", "v3", credentials=creds)
        update_body = inputs.updates.model_dump(exclude_unset=True)
        
        if "start_time" in update_body:
            update_body["start"] = {"dateTime": update_body.pop("start_time"), "timeZone": "UTC"}
        if "end_time" in update_body:
            update_body["end"] = {"dateTime": update_body.pop("end_time"), "timeZone": "UTC"}

        if not update_body:
            raise HTTPException(status_code=400, detail="No update fields provided.")

        updated_event = (
            service.events()
            .patch(
                calendarId="primary",
                eventId=inputs.event_id,
                body=update_body,
                sendNotifications=True,
            )
            .execute()
        )

        return {
            "status": "success",
            "id": updated_event["id"],
            "summary": updated_event.get("summary"),
            "htmlLink": updated_event.get("htmlLink"),
        }
    except HttpError as e:
        raise HTTPException(status_code=e.status_code, detail=f"Google API Error: {e.reason}")
    
# --- DELETE (Delete Event) ---
class DeleteEventInput(BaseModel):
    event_id: str

@app.post("/mcp/tools/deleteEvent")
async def mcp_delete_event(
    inputs: DeleteEventInput,
    creds: Credentials = Depends(get_google_credentials) # New Auth!
):
    try:
        service = build("calendar", "v3", credentials=creds)
        
        service.events().delete(
            calendarId="primary",
            eventId=inputs.event_id,
            sendNotifications=True,
        ).execute()

        return {
            "status": "success",
            "deleted_event_id": inputs.event_id
        }
    except HttpError as e:
        if e.status_code == 404:
            raise HTTPException(status_code=404, detail="Event not found.")
        raise HTTPException(status_code=e.status_code, detail=f"Google API Error: {e.reason}")

# --- Entry point ---
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)