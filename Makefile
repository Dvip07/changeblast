REPOS := billing-service payments-service notification-service checkout-web shared-contracts
PY := .venv/bin/python

.PHONY: repos setup infra agents stop-agents api web eval

setup: repos
	python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
	npm install --prefix web
	[ -f .env ] || cp .env.example .env

repos:
	./scripts/build_repos.sh

infra:
	docker compose up -d

# Five independent A2A services, one per repository, on ports 9300-9304 (logs in logs/).
agents:
	@mkdir -p logs; port=9300; for r in $(REPOS); do \
	  REPO=$$r nohup .venv/bin/uvicorn changeblast.agent_server:app --port $$port > logs/agent-$$r.log 2>&1 & \
	  echo "$$r agent on :$$port"; port=$$((port+1)); done

stop-agents:
	-pkill -f "changeblast.agent_server:app"

api:
	.venv/bin/uvicorn changeblast.api:app --port 8100

web:
	npm run dev --prefix web

eval:
	$(PY) -m evals.run_eval $(SCENARIOS)
