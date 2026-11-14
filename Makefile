.PHONY: install demo-db test api eval-demo eval-bird ablate calibrate

install:
	pip install -r requirements-dev.txt

demo-db:
	python scripts/make_demo_db.py

test:
	pytest -q

api:
	uvicorn api.main:app --reload --port 8000

eval-demo:
	python -m eval.run --dataset data/demo/questions.json --db-root data/demo/databases --name demo

# Download BIRD dev into ./bird first (see README)
eval-bird:
	python -m eval.run --dataset bird/dev.json --db-root bird/dev_databases --limit 300 --name dev300

ablate:
	python -m eval.ablate --dataset bird/dev.json --db-root bird/dev_databases --limit 200 --name ablation

calibrate:
	python -m eval.calibrate runs/dev300 --target-accuracy 0.9
