# Makefile — ai-team project utilities
# ============================================================

.PHONY: setup-hooks help

# Default target
help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ----------------------------------------------------------
# Git hooks
# ----------------------------------------------------------
setup-hooks: ## Configure git to use the project .githooks directory
	@echo "Configuring git hooks path → .githooks/"
	git config core.hooksPath .githooks
	@echo "Verifying hooks are executable..."
	@chmod +x .githooks/*
	@echo "Done. Git will now use hooks from .githooks/"
