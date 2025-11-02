
# settings.py
import os
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Build the full, absolute path to your .env file
ENV_FILE_PATH = os.path.join(BASE_DIR, ".env")



class Settings(BaseSettings):
    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str

    model_config = SettingsConfigDict(
        # Tell pydantic to load this specific file
        env_file=ENV_FILE_PATH,
        # Set this to 2 to make sure it reads from the file
        # (1=env vars, 2=env file, 3=default values)
        env_file_encoding='utf-8'
    )

# Create one instance to be used by the app
settings = Settings()