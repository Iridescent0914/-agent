"""The ordinary assignment pool supports the same types as current profiles."""
from app.services.profile_evidence import PROFILE_QUESTION_TYPES


def automatic_pool_ready(question) -> bool:
    return (
        question.question_type in PROFILE_QUESTION_TYPES
        and question.grading_readiness == "machine_ready"
        and question.source_review_status == "reviewed"
        and question.archived_at is None
    )
