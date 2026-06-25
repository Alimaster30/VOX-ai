from app import app, start_model_loading_once
from src.config import SETTINGS


if SETTINGS.autoload_models:
    start_model_loading_once()


application = app
