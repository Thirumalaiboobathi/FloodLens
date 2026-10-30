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
# ALARM_EMAIL, MAPS_KEY_ARN (the existing console-created Amazon Location key) and FRONTEND_ORIGINS (the Amplify https origin) are required; synth is local-only
synth:
	cd infrastructure && npx cdk synth --quiet -c alarmEmail=$(ALARM_EMAIL) -c mapsApiKeyArn=$(MAPS_KEY_ARN) -c frontendOrigins=$(FRONTEND_ORIGINS)

.PHONY: release-bundle verify-release
release-bundle:
	$(PY) scripts/create_release_bundle.py create --release-id $(RELEASE_ID)
verify-release:
	$(PY) scripts/create_release_bundle.py verify --release-id $(RELEASE_ID)
