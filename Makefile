PY=.venv/Scripts/python.exe

.PHONY: test-geo lint-geo validate-data terrain
validate-data:
	$(PY) scripts/validate_data.py
terrain:
	$(PY) scripts/process_terrain.py --region chennai

test-geo:
	cd geospatial && ../$(PY) -m pytest -q

lint-geo:
	$(PY) -m ruff check geospatial scripts

.PHONY: api-artifacts schemas test-api
api-artifacts:
	$(PY) scripts/build_api_artifacts.py --region chennai
schemas:
	$(PY) scripts/export_schemas.py
test-api:
	cd services/api && ../../$(PY) -m pytest -q

.PHONY: package-lambda test-infra synth
package-lambda:
	$(PY) scripts/package_lambda.py
test-infra:
	cd infrastructure && npm test
synth:
	cd infrastructure && npx cdk synth --quiet
