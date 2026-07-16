.PHONY: install run migrate seed test openapi docker-up docker-down

install:
	python -m pip install -r requirements.txt

run:
	uvicorn app.main:app --reload

migrate:
	alembic upgrade head

seed:
	python -m scripts.seed

test:
	pytest -q

openapi:
	python -m scripts.export_openapi

postman:
	python -m scripts.generate_postman

docker-up:
	docker compose up --build

docker-down:
	docker compose down
