from fastapi import APIRouter

from app.api.routes import health, questions, students


api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(questions.router)
api_router.include_router(students.router)
