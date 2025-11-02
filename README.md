# Google Calendar MCP Server

This project is a persistent, Python-based MCP (Model Context Protocol) server that acts as a bridge between an MCP client (like Claude Desktop) and your Google Calendar API.

It exposes your calendar as a set of tools (Create, Read, Update, Delete) that an LLM can use. Thanks to a local SQLite database that securely stores your Google `refresh_token`, you only need to go through the browser-based authentication flow **one time**. After that, the server can run in the background and will always be ready for Claude.

---

## ✨ Features

* **Full CRUD:**
    * `get_events` (Read)
    * `create_event` (Create)
    * `update_event` (Update)
    * `delete_event` (Delete)
* **Persistent Auth:** Uses a local SQLite database (`sql_app.db`) to securely store your Google OAuth `refresh_token`.
* **Simple One-Time Setup:** A built-in `--auth` flag starts a temporary web server to easily get your token from Google.
* **`stdio` Transport:** Designed to be launched and managed automatically by Claude Desktop.
* **Secure:** No API keys are needed in the client. The server handles all token refreshes in the background.

---

## 🔧 Installation & Setup

You will need to get Google credentials, set up the project, and authenticate once.

### Step 1: Get Google Credentials

Before you start, you must get your own API credentials from Google.

1.  Go to the [Google Cloud Console](https://console.cloud.google.com/).
2.  Create a new project.
3.  Go to **"APIs & Services" -> "Library"** and enable the **"Google Calendar API"**.
4.  Go to **"APIs & Services" -> "OAuth consent screen"**.
    * Choose **"External"** and fill in the required fields (app name, user support email).
    * On the "Scopes" page, add the `.../auth/calendar` and `.../auth/userinfo.email` scopes.
    * On the "Test users" page, add the Google email account you plan to use (e.g., `you@gmail.com`).
5.  Go to **"APIs & Services" -> "Credentials"**.
    * Click **"+ Create Credentials" -> "OAuth client ID"**.
    * Select **"Web application"** for the application type.
    * Under "Authorized redirect URIs," click "+ Add URI" and add:
        `http://127.0.0.1:8000/auth/google/callback`
6.  Click "Create". Google will give you a **Client ID** and a **Client Secret**. Copy these.

### Step 2: Clone and Install

1.  Clone the repository:
    ```bash
    git clone [https://github.com/supreetbhat/mcp-google-calender.git](https://github.com/supreetbhat/mcp-google-calender.git)
    cd mcp-google-calender
    ```
2.  Create a virtual environment and activate it:
    ```bash
    # On macOS/Linux
    python3 -m venv .venv
    source .venv/bin/activate

    # On Windows
    python -m venv .venv
    .\.venv\Scripts\activate
    ```
3.  Install the required packages:
    ```bash
    pip install -r requirements.txt
    ```
4.  Create a `.env` file in the root of the project and add the credentials you got from Google:
    ```ini
    # .env
    GOOGLE_CLIENT_ID=YOUR_CLIENT_ID_HERE.apps.googleusercontent.com
    GOOGLE_CLIENT_SECRET=YOUR_CLIENT_SECRET_HERE
    ```

### Step 3: Authorize with Google (One-Time Only)

You must run this step once to create your database and get your `refresh_token`.

1.  Run the auth server:
    ```bash
    python main.py --auth
    ```
2.  Your terminal will show:
    `Starting one-time auth server on http://127.0.0.1:8000`
    `Please open http://127.0.0.1:8000/auth/google in your browser.`
3.  Open that URL in your browser, log in to your Google account, and grant the permissions.
4.  You'll be redirected to a success page. You can now stop the server in your terminal (`Ctrl+C`).
5.  A new database file, `sql_app.db`, will now be in your project's `~/Library/Application Support/mcp-google-calender/` directory. Your token is safely stored.

### Step 4: Connect to Claude Desktop

This is the final step to tell Claude how to run your server.

1.  Find the **absolute path** to your project's Python executable. With your venv active, run:
    ```bash
    # On macOS/Linux
    which python
    # Example output: /Users/yourname/Code/mcp-google-calender/.venv/bin/python
    
    # On Windows
    where python
    # Example output: C:\Users\yourname\Code\mcp-google-calender\.venv\Scripts\python.exe
    ```
2.  Find the **absolute path** to your `main.py` file. In your project folder, run:
    ```bash
    # On macOS/Linux
    pwd
    # Example output: /Users/yourname/Code/mcp-google-calender
    # (So your script path is /Users/yourname/Code/mcp-google-calender/main.py)
    
    # On Windows
    cd
    # Example output: C:\Users\yourname\Code\mcp-google-calender
    # (So your script path is C:\Users\yourname\Code\mcp-google-calender\main.py)
    ```
3.  Open Claude Desktop, go to **Settings** -> **Developer** -> **Edit Config**.
4.  Add the following to the `mcpServers` object, using the **absolute paths** you just found.

    ```json
    {
      "mcpServers": {
        "google_calendar": {
          "command": "/ABSOLUTE/PATH/TO/YOUR/.venv/bin/python",
          "args": [
            "/ABSOLUTE/PATH/TO/YOUR/main.py"
          ],
          "cwd": "/ABSOLUTE/PATH/TO/YOUR/PROJECT/FOLDER"
        }
      }
    }
    ```
    * **`command`**: The full path to your Python executable (from step 4.1).
    * **`args`**: A list containing the full path to your `main.py` script.
    * **`cwd`**: The full path to the *folder* containing `main.py` and `.env` (from step 4.2). This is critical for the server to find your `.env` file.
    * *(Windows users: Remember to use double backslashes `\\` for your paths in the JSON file.)*

---

## 🚀 Usage

1.  **Fully quit and restart Claude Desktop** for the changes to take effect.
2.  Open a new chat. You should see the "Search and tools" (slider) icon in the chat bar.
3.  If you click it, you will see the `google_calendar` server and its four tools:
    * `get_events`
    * `create_event`
    * `update_event`
    * `delete_event`
4.  You can now ask Claude to manage your calendar!

### Example Prompts:

* "What's on my calendar tomorrow?"
* "Create an event called 'Doctor's Appointment' for this Friday at 3 PM."
* "Find the event 'Doctor's Appointment' and change its time to 4 PM."
* "Delete the event 'Doctor's Appointment'."

---

## 📄 License

This project is open-sourced under the MIT License.
