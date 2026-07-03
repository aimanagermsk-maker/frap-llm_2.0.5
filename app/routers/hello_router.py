from fastapi import APIRouter

from app.config.app_config import get_app_config
from app.config.database_config import get_database

router = APIRouter(tags=["Test"])


@router.get("/hello")
def hello():
    config = get_app_config()
    db = get_database()
    return {
        "text": "Ура, работает!",
        "contour": config.contour,
        "database": {
            "url": db.url,
            "username": db.username,
            "password": db.password,
        },
    }
