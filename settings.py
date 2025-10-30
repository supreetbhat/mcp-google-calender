# settings.py
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str
    SESSION_SECRET_KEY: str

    model_config = SettingsConfigDict(env_file=".env")

# Create one instance to be used by the app
settings = Settings()