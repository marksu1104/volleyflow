"""Database engine and session setup.

Reads DATABASE_URL from the environment: from .env locally (via
python-dotenv), or a real environment variable once deployed.
"""

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()

engine = create_engine(os.environ["DATABASE_URL"])
SessionLocal = sessionmaker(bind=engine)
