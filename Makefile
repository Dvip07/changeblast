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

# A2A agent host on :9300: one addressable agent per repository or module, created on demand.
agents:
	@mkdir -p logs; nohup .venv/bin/uvicorn changeblast.agent_host:app --port 9300 > logs/agent-host.log 2>&1 &
	@echo "agent host on :9300 (cards at /agents/<repo-or-module>/.well-known/agent-card.json)"

stop-agents:
	-pkill -f "changeblast.agent_host:app"

api:
	.venv/bin/uvicorn changeblast.api:app --port 8100

web:
	npm run dev --prefix web

eval:
	$(PY) -m evals.run_eval $(SCENARIOS)
