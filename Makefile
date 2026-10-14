.DEFAULT_GOAL := help

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

up:  ## Build and start the full stack
	docker compose up -d --build

down:  ## Stop all services
	docker compose down

reset:  ## Stop all services and delete volumes
	docker compose down -v

logs:  ## Tail logs from all services
	docker compose logs -f --tail=100

ps:  ## Show service status
	docker compose ps

test:  ## Run backend tests (requires Postgres + Redis on localhost)
	cd backend && python -m pytest tests/ -v

lint:  ## Lint the backend with ruff
	ruff check backend/

migrate:  ## Apply database migrations
	cd backend && alembic upgrade head

revision:  ## Create a migration: make revision NAME="add column"
	cd backend && alembic revision --autogenerate -m "$(NAME)"

psql:  ## Open psql on the primary
	docker compose exec db psql -U postgres -d urlshortener

psql-replica:  ## Open psql on the replica
	docker compose exec db-replica psql -U postgres -d urlshortener

redis-cli:  ## Open redis-cli
	docker compose exec redis redis-cli

frontend-dev:  ## Run the React dev server (port 3000)
	cd frontend && npm install && npm run dev

helm-install:  ## Install/upgrade the Helm chart on the current kube context
	kubectl create namespace url-shortener --dry-run=client -o yaml | kubectl apply -f -
	helm upgrade --install url-shortener ./helm/url-shortener --namespace url-shortener

helm-uninstall:  ## Remove the Helm release
	helm uninstall url-shortener -n url-shortener
