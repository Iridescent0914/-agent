from fastapi import APIRouter

from app.api.routes import assignments, health, papers, profile, questions, students, submissions


api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(questions.router)
api_router.include_router(students.router)
api_router.include_router(profile.router)
api_router.include_router(assignments.router)
api_router.include_router(papers.router)
api_router.include_router(submissions.router)
