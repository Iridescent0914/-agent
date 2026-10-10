from __future__ import annotations

from io import BytesIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.database import SessionLocal, get_db
from app.main import app
from app.models import ExamPoint, PaperInstance, PaperInstanceItem, Question, Student


@pytest.fixture
def db():
    with SessionLocal() as session:
        try:
            yield session
        finally:
            session.rollback()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as value:
            yield value
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_teacher_can_create_review_and_archive_question(client, db):
    exam = db.scalar(select(ExamPoint).order_by(ExamPoint.code))
    qid = f'q.teacher.test.{uuid4().hex[:12]}'
    before = int(db.scalar(
        select(func.count(Question.id)).where(Question.archived_at.is_(None))
    ) or 0)
    created = client.post('/api/v1/questions', json={
        'original_qid': qid,
        'question_type': 'fill_blank',
        'difficulty': 3,
        'section': '教师测试',
        'question_text': '计算：1+1=____。',
        'reference_answer_raw': '2',
        'main_exam_point_code': exam.code,
        'solution': '直接计算。',
        'operator_name': '测试教师',
    })
    assert created.status_code == 201, created.text
    body = created.json()
    question_id = body['id']
    assert body['original_qid'] == qid
    assert body['grading_readiness'] == 'raw_answer_only'
    assert body['review_history'][-1]['type'] == 'created'

    promoted = client.post(f'/api/v1/questions/{question_id}/promote', json={
        'expected_review_revision': body['review_revision'],
        'reviewer_name': '测试教师',
        'items': [{'item_id': '1', 'answer': '2', 'answer_match_type': 'numeric'}],
    })
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()['grading_readiness'] == 'machine_ready'
    assert client.get('/api/v1/questions', params={'keyword': qid}).json()['total'] == 1

    deleted = client.request(
        'DELETE',
        f'/api/v1/questions/{question_id}',
        json={'operator_name': '测试教师'},
    )
    assert deleted.status_code == 200, deleted.text
    assert client.get(f'/api/v1/questions/{question_id}').status_code == 404
    assert client.get('/api/v1/questions', params={'keyword': qid}).json()['total'] == 0
    assert int(db.scalar(
        select(func.count(Question.id)).where(Question.archived_at.is_(None))
    ) or 0) == before
    archived = db.get(Question, body['id'])
    assert archived is not None and archived.archived_by == '测试教师'


def test_teacher_can_upload_images_when_creating_question(client, db, tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, 'teacher_asset_dir', tmp_path)
    monkeypatch.setattr(
        settings,
        'public_teacher_asset_base_url',
        'http://testserver/assets/teacher',
    )

    image_bytes = BytesIO()
    Image.new('RGB', (320, 180), color=(245, 248, 252)).save(image_bytes, format='JPEG')
    uploaded = client.post(
        '/api/v1/questions/assets',
        files={'file': ('geometry.jpg', image_bytes.getvalue(), 'image/jpeg')},
    )
    assert uploaded.status_code == 201, uploaded.text
    asset = uploaded.json()
    assert asset['path'].startswith('assets/teacher/')
    assert asset['path'].endswith('.png')
    assert (tmp_path / asset['path'].split('/')[-1]).is_file()

    exam = db.scalar(select(ExamPoint).order_by(ExamPoint.code))
    created = client.post('/api/v1/questions', json={
        'question_type': 'fill_blank',
        'difficulty': 3,
        'section': '图片题测试',
        'question_text': '观察下图并填空。',
        'reference_answer_raw': '1',
        'main_exam_point_code': exam.code,
        'operator_name': '测试教师',
        'assets': [{'path': asset['path']}],
    })
    assert created.status_code == 201, created.text
    body = created.json()
    assert body['has_image'] is True
    assert body['asset_count'] == 1
    assert f"[ASSET:{asset['path']}]" in body['question_text']
    assert body['assets'][0]['web_url'] == asset['web_url']

    invalid = client.post(
        '/api/v1/questions/assets',
        files={'file': ('fake.png', b'not-an-image', 'image/png')},
    )
    assert invalid.status_code == 422


def test_archiving_referenced_question_preserves_paper_snapshot(client, db):
    item = db.scalar(select(PaperInstanceItem).limit(1))
    assert item is not None
    paper_id = item.paper_instance_id
    original_snapshot = item.question_snapshot
    deleted = client.request(
        'DELETE',
        f'/api/v1/questions/{item.question_id}',
        json={'operator_name': '测试教师'},
    )
    assert deleted.status_code == 200, deleted.text
    paper = client.get(f'/api/v1/paper-instances/{paper_id}')
    assert paper.status_code == 200, paper.text
    matching = next(value for value in paper.json()['items'] if value['id'] == str(item.id))
    assert matching['question_snapshot'] == original_snapshot


def test_teacher_can_create_and_archive_student_with_history_preserved(client, db):
    code = f'T{uuid4().hex[:10]}'
    created = client.post('/api/v1/students', json={
        'student_code': code,
        'display_name': '新增学生',
        'class_name': '六年级测试班',
    })
    assert created.status_code == 201, created.text
    body = created.json()
    student_id = body['id']
    assert body['is_demo'] is False and body['attempt_count'] == 0
    assert client.post('/api/v1/students', json={
        'student_code': code,
        'display_name': '重复学生',
        'class_name': '测试班',
    }).status_code == 409
    assert any(
        item['id'] == body['id']
        for item in client.get('/api/v1/students', params={'page_size': 100}).json()['items']
    )
    deleted = client.request(
        'DELETE',
        f'/api/v1/students/{student_id}',
        json={'operator_name': '测试教师'},
    )
    assert deleted.status_code == 200, deleted.text
    assert client.get(f'/api/v1/students/{student_id}').status_code == 404
    archived = db.get(Student, body['id'])
    assert archived is not None and archived.archived_by == '测试教师'

    paper = db.scalar(select(PaperInstance).limit(1))
    assert paper is not None
    referenced_student = db.get(Student, paper.student_id)
    assert referenced_student is not None
    archived_existing = client.request(
        'DELETE',
        f'/api/v1/students/{referenced_student.id}',
        json={'operator_name': '测试教师'},
    )
    assert archived_existing.status_code == 200, archived_existing.text
    historical_paper = client.get(f'/api/v1/paper-instances/{paper.id}')
    assert historical_paper.status_code == 200, historical_paper.text
    assert historical_paper.json()['student_id'] == str(referenced_student.id)
