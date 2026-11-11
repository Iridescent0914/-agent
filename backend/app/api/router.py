from fastapi import APIRouter

from app.api.routes import analytics, assignments, classes, health, papers, profile, question_imports, questions, recognition, students, submissions


api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(questions.router)
api_router.include_router(question_imports.router)
api_router.include_router(students.router)
api_router.include_router(classes.router)
api_router.include_router(profile.router)
api_router.include_router(assignments.router)
api_router.include_router(papers.router)
api_router.include_router(submissions.router)
api_router.include_router(analytics.router)
api_router.include_router(recognition.router)
