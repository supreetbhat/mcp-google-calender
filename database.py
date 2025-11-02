# database.py
import os
import sys
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker


# Get the user's home directory (e.g., /Users/supreetbhat)
home_dir = os.path.expanduser("~")

# Build a path to the standard Application Support folder
db_dir = os.path.join(
    home_dir, 
    "Library", 
    "Application Support", 
    "mcp-google-calender"
)

# The full, absolute path to our database file
db_path = os.path.join(db_dir, "sql_app.db")

# The new database URL
SQLALCHEMY_DATABASE_URL = f"sqlite:///{db_path}"
# --- END OF NEW PART ---

print(f"Database file will be at: {db_path}", file=sys.stderr) # For debugging

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()